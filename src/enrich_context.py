#!/usr/bin/env python3.11
"""Context enrichment CLI entry point.

Enriches build failures with additional context from multiple sources:
- Dependency changes (extracted from commit diffs)
- Related failures (similar failures from same component)

Usage:
    ./enrich_context.py                    # Enrich up to 20 failures
    ./enrich_context.py --limit 50         # Process 50 failures
    ./enrich_context.py --component name   # Specific component only

Designed for:
- Manual runs for testing
- Cron jobs (hourly/daily)
- Part of comprehensive collection pipeline
"""

import sys
import argparse

from config import CollectorConfig
from enrichment.enrichment_orchestrator import EnrichmentOrchestrator
from enrichment.sources.dependency_context import DependencyContextSource
from enrichment.sources.related_failures import RelatedFailuresSource
from logger import setup_logger

logger = setup_logger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='Enrich build failures with additional context'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=20,
        help='Maximum number of failures to enrich (default: 20)'
    )
    parser.add_argument(
        '--component',
        type=str,
        help='Enrich specific component only'
    )

    args = parser.parse_args()

    try:
        config = CollectorConfig.from_env()

        # Initialize orchestrator
        orchestrator = EnrichmentOrchestrator(config)

        # Register context sources
        orchestrator.register_source(DependencyContextSource(config))
        orchestrator.register_source(RelatedFailuresSource(config))

        # Run batch enrichment
        result = orchestrator.enrich_batch(limit=args.limit)

        # Log results
        logger.info("Enrichment batch complete:")
        logger.info("  Enriched: %d", result['enriched'])
        logger.info("  Failed: %d", result['failed'])
        logger.info("  Coverage: %.1f%%", result['coverage_pct'])
        logger.info("  Duration: %.1fs", result['duration'])

        # Exit code
        if result['failed'] > 0 and result['enriched'] == 0:
            # All failed
            sys.exit(1)
        else:
            # Some or all succeeded
            sys.exit(0)

    except Exception as e:
        logger.error("Enrichment failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
