#!/usr/bin/env python3
"""Check build synchronization status entry point.

Use collectors.status_synchronizer.get_failing_build_components for the implementation.
"""

import sys
import json
import time

from config import CollectorConfig
from repositories import DatabaseConnection, BuildFailureRepository, SyncStatusRepository
from openshift_auth import is_logged_in
from collectors.status_synchronizer import get_failing_build_components


def main():
    start_time = time.time()

    config = CollectorConfig.from_env()
    db = DatabaseConnection(config.db)
    build_repo = BuildFailureRepository(db)
    sync_repo = SyncStatusRepository(db)
    application_name = config.k8s.application_name

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

    if not is_logged_in():
        status['error'] = 'Not logged into OpenShift cluster'
        sync_repo.save_build_sync_status(application_name, status, time.time() - start_time)
        print(json.dumps(status))
        sys.exit(0)

    status['cluster_connected'] = True

    db_components = build_repo.find_failing_component_names(application_name)
    if db_components is None:
        status['error'] = 'Database connection failed'
        sync_repo.save_build_sync_status(application_name, status, time.time() - start_time)
        print(json.dumps(status))
        sys.exit(0)

    cluster_result = get_failing_build_components(config)
    cluster_components = cluster_result['failing']
    retriggered = cluster_result['retriggered']
    running = cluster_result.get('running', {})

    missing_in_db = cluster_components - db_components
    extra_in_db = db_components - cluster_components

    status['cluster_components'] = sorted(cluster_components)
    status['db_components'] = sorted(db_components)
    status['missing_in_db'] = sorted(missing_in_db)
    status['extra_in_db'] = sorted(extra_in_db)
    status['retriggered_components'] = sorted(retriggered)
    status['running_builds'] = [
        {'component': comp, **info} for comp, info in sorted(running.items())
    ]
    status['in_sync'] = len(missing_in_db) == 0 and len(extra_in_db) == 0

    sync_repo.save_build_sync_status(application_name, status, time.time() - start_time)

    print(json.dumps(status))


if __name__ == '__main__':
    main()
