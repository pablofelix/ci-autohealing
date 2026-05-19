#!/usr/bin/env python3.11
"""Batch analysis CLI - entry point for automated cron jobs.

Usage:
    ./analyze_batch.py              # Analyze one batch (default limits)
    ./analyze_batch.py --limit 50   # Custom batch size
    ./analyze_batch.py --estimate   # Show queue depth without analyzing
"""

import argparse
import sys
from pathlib import Path

from config import CollectorConfig
from services.batch_analysis_service import BatchAnalysisService
from logger import setup_logger

logger = setup_logger(__name__)


def main():
    """Run batch analysis or estimate queue depth."""
    parser = argparse.ArgumentParser(
        description='Batch AI analysis of build failures and conforma violations'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Override max_per_run from config'
    )
    parser.add_argument(
        '--estimate',
        action='store_true',
        help='Estimate queue depth without analyzing'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Analyze failures across all applications (default: current app only)'
    )
    args = parser.parse_args()

    # Load config
    try:
        config = CollectorConfig.from_env()
    except Exception as e:
        logger.error("Failed to load configuration: %s", e)
        return 1

    # Verify LLM config present
    if not config.llm:
        logger.error("LLM configuration required for AI analysis")
        logger.error("Set ANTHROPIC_VERTEX_PROJECT_ID or ANTHROPIC_API_KEY")
        return 1

    # Create service
    service = BatchAnalysisService(config, all_apps=args.all)

    # Override limit if provided
    if args.limit:
        service.max_per_run = args.limit
        service.max_build = int(args.limit * 0.75)
        service.max_conforma = args.limit - service.max_build

    try:
        if args.estimate:
            estimate = service.estimate_queue_depth()
            mode = "All Apps" if args.all else config.k8s.application_name
            logger.info("Queue Depth Estimate (%s)", mode)
            logger.info("Build pending: %d", estimate['build_pending'])
            logger.info("Conforma pending: %d", estimate['conforma_pending'])
            logger.info("Total pending: %d", estimate['total_pending'])
            logger.info("ETA to clear: %.1f hours", estimate['eta_hours'])
            if estimate.get('apps'):
                logger.info("Apps with pending: %s", ', '.join(estimate['apps']))
            return 0
        else:
            # Run batch analysis
            result = service.run_batch()

            # Exit code based on queue depth
            total_pending = result.build_pending + result.conforma_pending
            if total_pending > 100:
                logger.warning("Queue depth high: %d pending", total_pending)
                return 2  # Warning status
            elif total_pending == 0:
                logger.info("Queue cleared - no pending failures")
                return 0
            else:
                logger.info("Batch complete - %d pending", total_pending)
                return 0

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as e:
        logger.error("Batch analysis failed: %s", e, exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
