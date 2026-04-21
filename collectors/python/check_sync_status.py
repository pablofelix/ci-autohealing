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

APPLICATION_NAME = os.getenv('APPLICATION_NAME', 'acme-v2-0')
NAMESPACE = os.getenv('NAMESPACE', 'NAMESPACE_PLACEHOLDER')


def check_oc_login() -> bool:
    """Check if user is logged into OpenShift."""
    try:
        result = subprocess.run(
            ['oc', 'whoami'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def get_kubearchive_url() -> str:
    """Get KubeArchive API URL."""
    try:
        result = subprocess.run(
            ['oc', 'get', 'cm', '-n', 'product-kubearchive', 'kubearchive-api-url',
             '-o', 'jsonpath={.data.URL}'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN"


def get_oc_token() -> Optional[str]:
    """Get OpenShift authentication token."""
    try:
        result = subprocess.run(
            ['oc', 'whoami', '-t'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


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
    token = get_oc_token()
    if not token:
        return {'failing': set(), 'retriggered': set()}

    # Track latest push and incoming builds per component
    push_latest = {}      # component -> (timestamp, status, reason)
    incoming_latest = {}   # component -> (timestamp, status, reason)

    def process_pipelinerun(pr):
        """Process a single PipelineRun into push/incoming tracking."""
        labels = pr.get('metadata', {}).get('labels', {})
        comp = labels.get('appstudio.openshift.io/component')
        if not comp:
            return
        event_type = labels.get('pipelinesascode.tekton.dev/event-type', '')
        ts = pr.get('metadata', {}).get('creationTimestamp', '')
        conditions = pr.get('status', {}).get('conditions', [])
        if not conditions:
            return
        c = conditions[-1]
        status = c.get('status')
        reason = c.get('reason')

        if event_type == 'push':
            if comp not in push_latest or ts > push_latest[comp][0]:
                push_latest[comp] = (ts, status, reason)
        elif event_type == 'incoming':
            if comp not in incoming_latest or ts > incoming_latest[comp][0]:
                incoming_latest[comp] = (ts, status, reason)

    # Source 1: KubeArchive (has archived PipelineRuns)
    try:
        api_url = get_kubearchive_url()
        session = requests.Session()
        session.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        })

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

    return {'failing': failing, 'retriggered': retriggered}


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

        sql = f"""
        UPDATE sync_status SET
            last_checked_at = NOW(),
            in_sync = {'TRUE' if status['in_sync'] else 'FALSE'},
            cluster_connected = {'TRUE' if status['cluster_connected'] else 'FALSE'},
            cluster_components = '{json.dumps(status['cluster_components'])}',
            db_components = '{json.dumps(status['db_components'])}',
            missing_in_db = '{json.dumps(status['missing_in_db'])}',
            extra_in_db = '{json.dumps(status['extra_in_db'])}',
            retriggered_components = '{json.dumps(status.get('retriggered_components', []))}',
            error = {error_val},
            check_duration_seconds = {duration:.2f}
        WHERE id = 1;
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
        'error': None
    }

    # Check cluster connection
    if not check_oc_login():
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

    # Calculate differences (exclude retriggered from "missing" — they'll be collected)
    missing_in_db = cluster_components - db_components
    extra_in_db = db_components - cluster_components

    # Update status
    status['cluster_components'] = sorted(cluster_components)
    status['db_components'] = sorted(db_components)
    status['missing_in_db'] = sorted(missing_in_db)
    status['extra_in_db'] = sorted(extra_in_db)
    status['retriggered_components'] = sorted(retriggered)
    status['in_sync'] = len(missing_in_db) == 0 and len(extra_in_db) == 0

    # Save to DB cache
    save_sync_status(status, time.time() - start_time)

    print(json.dumps(status))


if __name__ == '__main__':
    main()
