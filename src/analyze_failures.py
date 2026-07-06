#!/usr/bin/env python3
"""Entry point for AI analysis of build failures.

Thin shim that loads config and runs BuildFailureAnalyzer.
Follows the same pattern as collect_comprehensive.py and sync_component_status.py.
"""

import argparse
import sys

from analyzers.build_failure_analyzer import BuildFailureAnalyzer
from config import CollectorConfig
from logger import setup_logger

logger = setup_logger(__name__)


def main():
    """Run AI analysis on pending build failures."""
    parser = argparse.ArgumentParser(description='Analyze build failures with AI')
    parser.add_argument('--component', help='Analyze specific component')
    parser.add_argument('--limit', type=int, help='Maximum number of failures to analyze')
    parser.add_argument('--force', action='store_true', help='Re-analyze even if already analyzed')
    args = parser.parse_args()

    try:
        config = CollectorConfig.from_env()

        # Check LLM configuration
        if not config.llm:
            logger.error("LLM not configured. Set LLM_PROVIDER and related env vars in .env")
            logger.error("See .env.example for configuration options")
            sys.exit(1)

        # Run analyzer
        analyzer = BuildFailureAnalyzer(config)
        limit = args.limit or config.llm.max_analysis_per_run

        if args.component:
            # Targeted analysis for a specific component
            logger.info("Analyzing component: %s", args.component)
            result = analyzer.run(limit=1, component_filter=args.component, force=args.force)
        else:
            # Batch analysis (existing behavior)
            result = analyzer.run(limit=limit)

        logger.info("Analysis complete: %d failures analyzed in %.1fs",
                   result['analyzed'], result['duration'])

        sys.exit(0)

    except Exception as e:
        logger.error("Analysis failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
