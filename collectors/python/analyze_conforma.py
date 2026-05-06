#!/usr/bin/env python3
"""Entry point for AI analysis of Conforma compliance violations.

Usage:
    python3 analyze_conforma.py [--component <name>] [--force] [--limit N]

Examples:
    python3 analyze_conforma.py
    python3 analyze_conforma.py --component acme-autorag-v3-4
    python3 analyze_conforma.py --limit 10
    python3 analyze_conforma.py --component acme-autorag-v3-4 --force
"""

import argparse
import sys
from config import CollectorConfig
from analyzers.conforma_analyzer import ConformaAnalyzer
from logger import setup_logger

logger = setup_logger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='Analyze Conforma compliance violations using AI'
    )
    parser.add_argument(
        '--component',
        help='Analyze only this component (e.g., acme-autorag-v3-4)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Maximum number of violations to analyze (default from config)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Re-analyze even if already analyzed'
    )

    args = parser.parse_args()

    # Load config
    config = CollectorConfig.from_env()
    if not config.llm:
        logger.error("LLM not configured. Set LLM_PROVIDER and related env vars.")
        logger.error("Example: export LLM_PROVIDER=anthropic")
        logger.error("         export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    # Create analyzer
    analyzer = ConformaAnalyzer(config)

    # Determine limit
    limit = args.limit if args.limit else config.llm.max_analysis_per_run

    # Run analysis
    result = analyzer.run(
        limit=limit,
        component_filter=args.component,
        force=args.force
    )

    logger.info("Analysis complete: %d violations analyzed", result['analyzed'])
    return 0 if result['analyzed'] > 0 else 1


if __name__ == '__main__':
    sys.exit(main())
