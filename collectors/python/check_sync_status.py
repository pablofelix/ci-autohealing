#!/usr/bin/env python3
"""
Check synchronization status between Konflux and local database.
Returns JSON with sync status and missing/extra components.

Uses KubeArchive API to get archived PipelineRuns (live cluster only
retains a few recent ones, while KubeArchive has the full history).
"""

import os
import sys
import json
import subprocess
from collections import defaultdict
from typing import Set, Dict, Any, Optional

import requests

from openshift_auth import is_logged_in, get_openshift_token, discover_kubearchive_api_url, create_authenticated_session

APPLICATION_NAME = os.getenv('APPLICATION_NAME', 'acme-v2-0')
NAMESPACE = os.getenv('NAMESPACE', 'NAMESPACE_PLACEHOLDER')


def get_failing_components_from_cluster() -> Dict[str, Any]:
    """Get failing components from KubeArchive + live cluster (push builds only).

    Queries KubeArchive for archived PipelineRuns and live cluster.
    Groups by component, tracks latest push build and latest incoming build
    separately. Returns components whose latest push build failed, and
    identifies which ones have a successful incoming re-trigger.

    Returns:
        Dict with 'failing' (set of component names with failed push builds)
        and 'retriggered' (set of components with successful incoming after push failure).
    """
    token = get_openshift_token()
    if not token:
        return {'failing': set(), 'retriggered': set()}

    # Track latest push and incoming builds per component
    push_latest = {}      # component -> (timestamp, status, reason)
    incoming_latest = {}   # component -> (timestamp, status, reason)
    running_builds = {}    # component -> (timestamp, pr_name) for currently running builds

    def process_pipelinerun(pr):
        """Process a single PipelineRun into push/incoming tracking."""
        labels = pr.get('metadata', {}).get('labels', {})
        comp = labels.get('appstudio.openshift.io/component')
        if not comp:
            return
        event_type = labels.get('pipelinesascode.tekton.dev/event-type', '')
        ts = pr.get('metadata', {}).get('creationTimestamp', '')
        pr_name = pr.get('metadata', {}).get('name', '')
        conditions = pr.get('status', {}).get('conditions', [])

        if not conditions:
            # No conditions = still initializing, track as running
            if event_type in ('push', 'incoming'):
                if comp not in running_builds or ts > running_builds[comp][0]:
                    running_builds[comp] = (ts, pr_name)
            return

        c = conditions[-1]
        status = c.get('status')
        reason = c.get('reason')

        # status 'Unknown' = still running in Tekton
        if status == 'Unknown':
            if event_type in ('push', 'incoming'):
                if comp not in running_builds or ts > running_builds[comp][0]:
                    running_builds[comp] = (ts, pr_name)
            return

        if event_type == 'push':
            if comp not in push_latest or ts > push_latest[comp][0]:
                push_latest[comp] = (ts, status, reason)
        elif event_type == 'incoming':
            if comp not in incoming_latest or ts > incoming_latest[comp][0]:
                incoming_latest[comp] = (ts, status, reason)

    # Source 1: KubeArchive (has archived PipelineRuns)
    try:
        api_url = discover_kubearchive_api_url()
        session = create_authenticated_session(token)

        url = f"{api_url}/apis/tekton.dev/v1/namespaces/{NAMESPACE}/pipelineruns"
        params = {
            'labelSelector': f'appstudio.openshift.io/application={APPLICATION_NAME},pipelines.appstudio.openshift.io/type=build',
            'limit': 500
        }

        # Paginate through results (max 3 pages = 1500 PipelineRuns)
        for _ in range(3):
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            for pr in data.get('items', []):
                process_pipelinerun(pr)
            cont = data.get('metadata', {}).get('continue')
            if cont:
                params['continue'] = cont
            else:
                break
    except Exception:
        pass

    # Source 2: Live cluster PipelineRuns (may have very recent ones not yet in KubeArchive)
    try:
        result = subprocess.run(
            ['oc', 'get', 'pipelinerun',
             '-n', NAMESPACE,
             '-l', f'appstudio.openshift.io/application={APPLICATION_NAME},pipelines.appstudio.openshift.io/type=build',
             '-o', 'json'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=15
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            for pr in data.get('items', []):
                process_pipelinerun(pr)
    except Exception:
        pass

    # Determine failing components (latest push build failed)
    failing = set()
    retriggered = set()
    for comp, (push_ts, push_status, push_reason) in push_latest.items():
        if push_status == 'False':
            failing.add(comp)
            # Check if a successful incoming build exists AFTER the push failure
            if comp in incoming_latest:
                inc_ts, inc_status, inc_reason = incoming_latest[comp]
                if inc_status == 'True' and inc_ts > push_ts:
                    retriggered.add(comp)

    # Don't report running builds for components that already have a completed build
    # (only track truly in-progress ones where no final result exists yet, or the
    # running build is newer than the latest completed one)
    active_running = {}
    for comp, (run_ts, run_pr) in running_builds.items():
        is_newer = True
        if comp in push_latest and run_ts <= push_latest[comp][0]:
            is_newer = False
        if comp in incoming_latest and run_ts <= incoming_latest[comp][0]:
            is_newer = False
        if is_newer:
            active_running[comp] = {'timestamp': run_ts, 'pipelinerun_name': run_pr, 'type': 'build'}

    return {'failing': failing, 'retriggered': retriggered, 'running': active_running}


def get_components_from_db() -> Set[str]:
    """Get list of currently failing components from database."""
    try:
        # Use docker exec instead of direct psql
        result = subprocess.run(
            ['docker', 'exec', 'ci-autohealing-db',
             'psql', '-U', 'postgres', '-d', 'konflux_monitoring', '-tAc',
             f"""
             WITH latest_builds AS (
                 SELECT DISTINCT ON (component_name)
                     component_name,
                     status,
                     is_resolved
                 FROM build_failures
                 WHERE application = '{APPLICATION_NAME}'
                 ORDER BY component_name, first_detected_at DESC
             )
             SELECT component_name FROM latest_builds
             WHERE status = 'Failed' AND is_resolved = FALSE;
             """],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=10
        )

        if result.returncode != 0:
            return None  # Signal error instead of empty set

        components = {line.strip() for line in result.stdout.strip().split('\n') if line.strip()}
        return components if components else set()  # Empty set is valid (no failures)

    except Exception:
        return None  # Signal error


def save_sync_status(status: Dict[str, Any], duration: float) -> None:
    """Save sync status to database for caching."""
    try:
        # Escape single quotes in error message
        error_val = "NULL"
        if status.get('error'):
            escaped = status['error'].replace("'", "''")
            error_val = f"'{escaped}'"

        running_builds_json = json.dumps(status.get('running_builds', []))

        sql = f"""
        INSERT INTO sync_status (application, last_checked_at, in_sync, cluster_connected,
            cluster_components, db_components, missing_in_db, extra_in_db,
            retriggered_components, running_builds, error, check_duration_seconds)
        VALUES (
            '{APPLICATION_NAME}', NOW(),
            {'TRUE' if status['in_sync'] else 'FALSE'},
            {'TRUE' if status['cluster_connected'] else 'FALSE'},
            '{json.dumps(status['cluster_components'])}',
            '{json.dumps(status['db_components'])}',
            '{json.dumps(status['missing_in_db'])}',
            '{json.dumps(status['extra_in_db'])}',
            '{json.dumps(status.get('retriggered_components', []))}',
            '{running_builds_json}',
            {error_val}, {duration:.2f}
        )
        ON CONFLICT (application) DO UPDATE SET
            last_checked_at = NOW(),
            in_sync = EXCLUDED.in_sync,
            cluster_connected = EXCLUDED.cluster_connected,
            cluster_components = EXCLUDED.cluster_components,
            db_components = EXCLUDED.db_components,
            missing_in_db = EXCLUDED.missing_in_db,
            extra_in_db = EXCLUDED.extra_in_db,
            retriggered_components = EXCLUDED.retriggered_components,
            running_builds = EXCLUDED.running_builds,
            error = EXCLUDED.error,
            check_duration_seconds = EXCLUDED.check_duration_seconds;
        """

        subprocess.run(
            ['docker', 'exec', 'ci-autohealing-db',
             'psql', '-U', 'postgres', '-d', 'konflux_monitoring', '-c', sql],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5
        )
    except Exception:
        pass


def main():
    """Check sync status and return JSON."""
    import time
    start_time = time.time()

    status = {
        'cluster_connected': False,
        'in_sync': False,
        'cluster_components': [],
        'db_components': [],
        'missing_in_db': [],
        'extra_in_db': [],
        'retriggered_components': [],
        'running_builds': [],
        'error': None
    }

    # Check cluster connection
    if not is_logged_in():
        status['error'] = 'Not logged into OpenShift cluster'
        save_sync_status(status, time.time() - start_time)
        print(json.dumps(status))
        sys.exit(0)

    status['cluster_connected'] = True

    # Get components from database (always should work if DB is running)
    db_components = get_components_from_db()
    if db_components is None:
        status['error'] = 'Database connection failed'
        save_sync_status(status, time.time() - start_time)
        print(json.dumps(status))
        sys.exit(0)

    # Get components from cluster + KubeArchive (push builds only)
    cluster_result = get_failing_components_from_cluster()
    cluster_components = cluster_result['failing']
    retriggered = cluster_result['retriggered']
    running = cluster_result.get('running', {})

    # Calculate differences (exclude retriggered from "missing" — they'll be collected)
    missing_in_db = cluster_components - db_components
    extra_in_db = db_components - cluster_components

    # Update status
    status['cluster_components'] = sorted(cluster_components)
    status['db_components'] = sorted(db_components)
    status['missing_in_db'] = sorted(missing_in_db)
    status['extra_in_db'] = sorted(extra_in_db)
    status['retriggered_components'] = sorted(retriggered)
    status['running_builds'] = [
        {'component': comp, **info} for comp, info in sorted(running.items())
    ]
    status['in_sync'] = len(missing_in_db) == 0 and len(extra_in_db) == 0

    # Save to DB cache
    save_sync_status(status, time.time() - start_time)

    print(json.dumps(status))


if __name__ == '__main__':
    main()
