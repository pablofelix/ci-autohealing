#!/usr/bin/env python3
"""Entry point for Konflux configuration analysis.

Usage:
    python3 analyze_config.py [--app <name>]

Examples:
    python3 analyze_config.py --app rhoai-v3-5
    python3 analyze_config.py
"""

import argparse
import json
import logging
import sys

from analyzers.config_analyzer import ConfigAnalyzer
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
        description='Analyze Konflux configuration using AI'
    )
    parser.add_argument(
        '--app',
        help='Application name (e.g., rhoai-v3-5)'
    )

    args = parser.parse_args()

    config = CollectorConfig.from_env()
    if not config.llm:
        logger.error("LLM not configured. Set LLM_PROVIDER and related env vars.")
        sys.exit(1)

    analyzer = ConfigAnalyzer(config)
    result = analyzer.run(application=args.app)

    print(json.dumps(result, indent=2, default=str))

    return 0 if result.get('analyzed') else 1


if __name__ == '__main__':
    sys.exit(main())
