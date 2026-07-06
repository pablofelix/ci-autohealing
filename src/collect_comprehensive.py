#!/usr/bin/env python3
"""Comprehensive CI failure collector entry point.

Use collectors.build_failure_collector.BuildFailureCollector for the implementation.
"""

import sys
from dataclasses import replace
from pathlib import Path

from collectors.build_failure_collector import BuildFailureCollector
from config import CollectorConfig
from logger import setup_logger

logger = setup_logger(__name__)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Comprehensive CI failure collector - optimized for troubleshooting'
    )
    parser.add_argument(
        '--components-file', type=Path,
        help='File with component names (one per line)'
    )
    parser.add_argument(
        '--limit', type=int,
        help='Limit number of components to process'
    )
    args = parser.parse_args()

    config = CollectorConfig.from_env()
    if args.components_file:
        config = replace(config, components_file=args.components_file)

    collector = BuildFailureCollector(config)
    result = collector.run(limit=args.limit)

    logger.info("=" * 70)
    logger.info("Collection Complete")
    logger.info("=" * 70)
    logger.info("Components scanned: %d", result.components_scanned)
    logger.info("Failures found: %d", result.failures_found)
    logger.info("New failures inserted: %d", result.new_failures)
    logger.info("Logs collected: %d", result.logs_fetched)
    logger.info("Duration: %.1fs", result.duration_seconds)

    return 0


if __name__ == '__main__':
    sys.exit(main())
