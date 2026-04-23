#!/usr/bin/env python3
"""Check Conforma test status entry point.

Use collectors.status_synchronizer.get_failing_conforma_components for the implementation.
"""

import sys
import json
import time

from config import CollectorConfig
from logger import setup_logger
from repositories import DatabaseConnection, ConformaRepository, SyncStatusRepository
from collectors.status_synchronizer import get_failing_conforma_components

logger = setup_logger(__name__)


def main():
    start_time = time.time()

    config = CollectorConfig.from_env()
    db = DatabaseConnection(config.db)
    conforma_repo = ConformaRepository(db)
    sync_repo = SyncStatusRepository(db)
    application_name = config.k8s.application_name

    logger.info("Checking Conforma test status for %s", application_name)

    cluster_result = get_failing_conforma_components(config)
    failing = cluster_result['failing']
    details = cluster_result['details']
    running = cluster_result.get('running', {})

    db_components = conforma_repo.find_unresolved_component_names(application_name)

    missing_in_db = failing - db_components
    extra_in_db = db_components - failing

    sync_repo.save_conforma_sync_status(application_name, failing, running)

    duration = time.time() - start_time

    logger.info("Conforma failures in cluster: %d", len(failing))
    logger.info("Conforma failures in DB: %d", len(db_components))
    if running:
        logger.info("Conforma tests running: %d (%s)", len(running), sorted(running.keys()))
    if missing_in_db:
        logger.warning("Missing in DB: %s", sorted(missing_in_db))
    if extra_in_db:
        logger.info("Extra in DB (resolved?): %s", sorted(extra_in_db))
    logger.info("Duration: %.1fs", duration)

    status = {
        'failing_components': sorted(failing),
        'db_components': sorted(db_components),
        'missing_in_db': sorted(missing_in_db),
        'extra_in_db': sorted(extra_in_db),
        'running_conforma': [{'component': c, **i} for c, i in sorted(running.items())],
        'details': {k: v for k, v in details.items()},
        'duration': round(duration, 2)
    }

    print(json.dumps(status))


if __name__ == '__main__':
    main()
