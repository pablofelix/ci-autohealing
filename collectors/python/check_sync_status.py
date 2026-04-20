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
    """Get list of failing components from Kubernetes cluster.

    Discovers components automatically using 'oc get components' and checks
    the latest PipelineRun status for each component.
    """
    try:
        # Step 1: Discover all components in the application
        result = subprocess.run(
            ['oc', 'get', 'component',
             '-n', 'NAMESPACE_PLACEHOLDER',
             '-o', 'json'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=15
        )

        if result.returncode != 0:
            return set()

        data = json.loads(result.stdout)
        all_components = data.get('items', [])

        # Filter components by application name in spec
        components = [
            comp for comp in all_components
            if comp.get('spec', {}).get('application') == 'acme-v2-0'
        ]

        if not components:
            return set()

        # Step 2: For each component, get latest PipelineRun status
        failing_components = set()

        for comp in components:
            component_name = comp.get('metadata', {}).get('name')
            if not component_name:
                continue

            # Get latest PipelineRun for this component
            pr_result = subprocess.run(
                ['oc', 'get', 'pipelinerun',
                 '-n', 'NAMESPACE_PLACEHOLDER',
                 '-l', f'appstudio.openshift.io/component={component_name}',
                 '--sort-by', '.metadata.creationTimestamp',
                 '-o', 'json'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=10
            )

            if pr_result.returncode != 0:
                continue

            pr_data = json.loads(pr_result.stdout)
            pipelineruns = pr_data.get('items', [])

            if not pipelineruns:
                continue

            # Get status of the LATEST PipelineRun (last in sorted list)
            latest_pr = pipelineruns[-1]
            conditions = latest_pr.get('status', {}).get('conditions', [])

            if conditions:
                last_condition = conditions[-1]
                # Check if Failed (status=False and reason=Failed)
                if (last_condition.get('status') == 'False' and
                    last_condition.get('reason') == 'Failed'):
                    failing_components.add(component_name)

        return failing_components

    except Exception as e:
        return set()


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

    # Get components from database (always should work if DB is running)
    db_components = get_components_from_db()
    if db_components is None:
        status['error'] = 'Database connection failed'
        print(json.dumps(status))
        sys.exit(0)

    # Get components from cluster
    cluster_components = get_failing_components_from_cluster()

    # If cluster query returns empty but we have DB components, something is wrong
    if len(cluster_components) == 0 and len(db_components) > 0:
        # Could be: 1) All resolved (unlikely), 2) Query failed, 3) No access
        # Check if we can query at all
        status['error'] = 'Cannot query cluster components (may lack permissions)'
        status['db_components'] = sorted(db_components)
        status['cluster_components'] = []
        status['in_sync'] = False
        print(json.dumps(status))
        sys.exit(0)

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
