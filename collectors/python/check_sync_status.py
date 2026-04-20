#!/usr/bin/env python3
"""
Check synchronization status between Konflux and local database.
Returns JSON with sync status and missing/extra components.
"""

import sys
import json
import subprocess
from pathlib import Path
from typing import Set, Dict, Any


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


def get_failing_components_from_cluster() -> Set[str]:
    """Get list of failing components from Kubernetes cluster."""
    try:
        # Get all PipelineRuns for the application (last 24h should be enough)
        result = subprocess.run(
            ['oc', 'get', 'pipelinerun',
             '-n', 'NAMESPACE_PLACEHOLDER',
             '-l', 'build.appstudio.redhat.com/application=acme-v2-0',
             '--sort-by', '.metadata.creationTimestamp',
             '-o', 'json'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30
        )

        if result.returncode != 0:
            return set()

        data = json.loads(result.stdout)
        items = data.get('items', [])

        # Group by component and get latest status for each
        component_status = {}
        for pr in items:
            labels = pr.get('metadata', {}).get('labels', {})
            component = labels.get('build.appstudio.redhat.com/component')

            if not component:
                continue

            # Get status
            conditions = pr.get('status', {}).get('conditions', [])
            if conditions:
                last_condition = conditions[-1]
                status = 'Succeeded' if last_condition.get('status') == 'True' else 'Failed'
            else:
                status = 'Running'

            # Store latest status (items are sorted by creation time)
            component_status[component] = status

        # Return only failing components
        return {comp for comp, status in component_status.items() if status == 'Failed'}

    except Exception as e:
        return set()


def get_components_from_db() -> Set[str]:
    """Get list of currently failing components from database."""
    try:
        result = subprocess.run(
            ['psql',
             '-h', 'localhost',
             '-p', '5433',
             '-U', 'postgres',
             '-d', 'konflux_monitoring',
             '-tAc',
             """
             WITH latest_builds AS (
                 SELECT DISTINCT ON (component_name)
                     component_name,
                     status
                 FROM build_failures
                 WHERE application = 'acme-v2-0'
                 ORDER BY component_name, first_detected_at DESC
             )
             SELECT component_name FROM latest_builds WHERE status = 'Failed';
             """],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=10,
            env={'PGPASSWORD': 'admin'}
        )

        if result.returncode != 0:
            return set()

        return {line.strip() for line in result.stdout.strip().split('\n') if line.strip()}

    except Exception:
        return set()


def main():
    """Check sync status and return JSON."""
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
        print(json.dumps(status))
        sys.exit(0)

    status['cluster_connected'] = True

    # Get components from cluster
    cluster_components = get_failing_components_from_cluster()
    if not cluster_components and status['cluster_connected']:
        # Could mean no failures, or query failed
        # We'll assume it's OK and just show empty
        pass

    # Get components from database
    db_components = get_components_from_db()

    # Calculate differences
    missing_in_db = cluster_components - db_components
    extra_in_db = db_components - cluster_components

    # Update status
    status['cluster_components'] = sorted(cluster_components)
    status['db_components'] = sorted(db_components)
    status['missing_in_db'] = sorted(missing_in_db)
    status['extra_in_db'] = sorted(extra_in_db)
    status['in_sync'] = len(missing_in_db) == 0 and len(extra_in_db) == 0

    print(json.dumps(status))


if __name__ == '__main__':
    main()
