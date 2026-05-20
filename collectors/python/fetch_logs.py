#!/usr/bin/env python3
"""Fetch build logs on-demand for a PipelineRun and store in DB.

Used by `ic describe component` when logs are not yet in the database.
Uses Tekton Results (most reliable) with KubeArchive as fallback.
"""

import sys
import json
import time

from config import CollectorConfig
from logger import setup_logger
from repositories import DatabaseConnection

logger = setup_logger(__name__)


def fetch_and_store_logs(pr_name, config=None):
    if not config:
        config = CollectorConfig.from_env()

    db = DatabaseConnection(config.db)
    ns = config.k8s.namespace
    t0 = time.time()
    logs = None
    source = 'none'

    # Try Tekton Results first (most reliable), only failed TaskRun logs
    try:
        from clients.tekton_results import TektonResultsClient
        tr = TektonResultsClient(namespace=ns)
        logs = tr.get_pipelinerun_logs(pr_name, failed_only=True)
        if logs:
            source = 'TektonResults'
    except Exception as e:
        logger.debug("Tekton Results failed: %s", e)

    # Fallback to KubeArchive
    if not logs:
        try:
            from clients.kubearchive import KubeArchiveClient
            ka = KubeArchiveClient(namespace=ns)
            logs = ka.get_pipelinerun_logs(pr_name)
            if logs:
                source = 'KubeArchive'
        except Exception as e:
            logger.debug("KubeArchive failed: %s", e)

    elapsed = time.time() - t0
    result = {
        'pipelinerun': pr_name,
        'source': source,
        'elapsed': round(elapsed, 1),
        'logs_length': len(logs) if logs else 0,
        'stored': False,
    }

    if not logs:
        return result

    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE build_failures SET build_logs = %s "
            "WHERE pipelinerun_name = %s AND build_logs IS NULL",
            (logs, pr_name)
        )
        result['stored'] = cursor.rowcount > 0
        conn.commit()

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: fetch_logs.py <pipelinerun-name>", file=sys.stderr)
        sys.exit(1)

    pr_name = sys.argv[1]
    result = fetch_and_store_logs(pr_name)
    print(json.dumps(result))
    if result['logs_length'] == 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
