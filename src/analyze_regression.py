#!/usr/bin/env python3
"""Entry point for AI regression testing on resolved conforma violations.

Usage:
    python3 analyze_regression.py [--app <name>] [--limit N] [--verbose]

Examples:
    python3 analyze_regression.py --app rhoai-v3-5 --verbose
    python3 analyze_regression.py --limit 100
"""

import argparse
import json
import logging
import sys

from analyzers.conforma_regression import ConformaRegressionTester
from config import CollectorConfig
from logger import setup_logger

for name in list(logging.Logger.manager.loggerDict) + ['root']:
    lg = logging.getLogger(name) if name != 'root' else logging.getLogger()
    for h in lg.handlers:
        if hasattr(h, 'stream') and h.stream is sys.stdout:
            h.stream = sys.stderr

logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

logger = setup_logger(__name__)
for h in logger.handlers:
    if hasattr(h, 'stream'):
        h.stream = sys.stderr


def main():
    parser = argparse.ArgumentParser(
        description='Regression test the conforma AI analyzer against resolved violations'
    )
    parser.add_argument(
        '--app',
        help='Filter by application (e.g., rhoai-v3-5)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=50,
        help='Max resolved violations to evaluate (default: 50)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show per-violation evaluation details'
    )

    args = parser.parse_args()

    config = CollectorConfig.from_env()

    tester = ConformaRegressionTester(config)
    result = tester.run(
        application=args.app,
        limit=args.limit,
        verbose=args.verbose,
    )

    print(json.dumps(result, indent=2, default=str))

    return 0 if result.get('analyzed') else 1


if __name__ == '__main__':
    sys.exit(main())
