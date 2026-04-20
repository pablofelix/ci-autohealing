#!/usr/bin/env python3
"""
Check synchronization status between Konflux and local database.
Returns JSON with sync status and missing/extra components.

Uses KubeArchive API to get archived PipelineRuns (live cluster only
retains a few recent ones, while KubeArchive has the full history).
"""

import sys
import json
import subprocess
from collections import defaultdict
from typing import Set, Dict, Any, Optional

import requests


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


def get_failing_components_from_cluster() -> Set[str]:
    """Get list of failing components from KubeArchive + live cluster.

    Queries KubeArchive for archived PipelineRuns (which has the full history)
    and also checks live cluster PipelineRuns. Groups by component, sorts by
    timestamp, and returns components whose latest build failed.
    """
    token = get_oc_token()
    if not token:
        return set()

    # Track latest status per component across all sources
    component_latest = {}  # component -> (timestamp, status, reason)

    # Source 1: KubeArchive (has archived PipelineRuns)
    try:
        api_url = get_kubearchive_url()
        session = requests.Session()
        session.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        })

        url = f"{api_url}/apis/tekton.dev/v1/namespaces/NAMESPACE_PLACEHOLDER/pipelineruns"
        params = {
            'labelSelector': 'appstudio.openshift.io/application=acme-v2-0,pipelines.appstudio.openshift.io/type=build',
            'limit': 500
        }

        # Paginate through results (max 3 pages = 1500 PipelineRuns)
        all_prs = []
        for _ in range(3):
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            all_prs.extend(data.get('items', []))
            cont = data.get('metadata', {}).get('continue')
            if cont:
                params['continue'] = cont
            else:
                break

        if all_prs:
            for pr in all_prs:
                comp = pr.get('metadata', {}).get('labels', {}).get('appstudio.openshift.io/component')
                if not comp:
                    continue
                ts = pr.get('metadata', {}).get('creationTimestamp', '')
                conditions = pr.get('status', {}).get('conditions', [])
                if conditions:
                    c = conditions[-1]
                    status = c.get('status')
                    reason = c.get('reason')
                    # Keep the most recent per component
                    if comp not in component_latest or ts > component_latest[comp][0]:
                        component_latest[comp] = (ts, status, reason)
    except Exception:
        pass

    # Source 2: Live cluster PipelineRuns (may have very recent ones not yet in KubeArchive)
    try:
        result = subprocess.run(
            ['oc', 'get', 'pipelinerun',
             '-n', 'NAMESPACE_PLACEHOLDER',
             '-l', 'appstudio.openshift.io/application=acme-v2-0,pipelines.appstudio.openshift.io/type=build',
             '-o', 'json'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=15
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            for pr in data.get('items', []):
                comp = pr.get('metadata', {}).get('labels', {}).get('appstudio.openshift.io/component')
                if not comp:
                    continue
                ts = pr.get('metadata', {}).get('creationTimestamp', '')
                conditions = pr.get('status', {}).get('conditions', [])
                if conditions:
                    c = conditions[-1]
                    status = c.get('status')
                    reason = c.get('reason')
                    if comp not in component_latest or ts > component_latest[comp][0]:
                        component_latest[comp] = (ts, status, reason)
    except Exception:
        pass

    # Return components whose latest build failed
    failing = set()
    for comp, (ts, status, reason) in component_latest.items():
        if status == 'False':
            failing.add(comp)

    return failing


def get_components_from_db() -> Set[str]:
    """Get list of currently failing components from database."""
    try:
        # Use docker exec instead of direct psql
        result = subprocess.run(
            ['docker', 'exec', 'ci-autohealing-db',
             'psql', '-U', 'postgres', '-d', 'konflux_monitoring', '-tAc',
             """
             WITH latest_builds AS (
                 SELECT DISTINCT ON (component_name)
                     component_name,
                     status,
                     is_resolved
                 FROM build_failures
                 WHERE application = 'acme-v2-0'
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

    # Get components from cluster + KubeArchive
    cluster_components = get_failing_components_from_cluster()

    # Calculate differences
    missing_in_db = cluster_components - db_components
    extra_in_db = db_components - cluster_components

    # Update status
    status['cluster_components'] = sorted(cluster_components)
    status['db_components'] = sorted(db_components)
    status['missing_in_db'] = sorted(missing_in_db)
    status['extra_in_db'] = sorted(extra_in_db)
    status['in_sync'] = len(missing_in_db) == 0 and len(extra_in_db) == 0

    # Save to DB cache
    save_sync_status(status, time.time() - start_time)

    print(json.dumps(status))


if __name__ == '__main__':
    main()
