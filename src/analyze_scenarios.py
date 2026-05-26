#!/usr/bin/env python3
"""Entry point for AI analysis of IntegrationTestScenario configurations.

Usage:
    python3 analyze_scenarios.py [--app <name>] [--namespace <ns>]

Examples:
    python3 analyze_scenarios.py --app acme-v2-0
    python3 analyze_scenarios.py --app acme-v2-0-ea-1
    python3 analyze_scenarios.py  # all apps in namespace
"""

import argparse
import json
import logging
import sys
from config import CollectorConfig
from analyzers.scenarios_analyzer import ScenariosAnalyzer
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
        description='Analyze ITS scenario configurations using AI'
    )
    parser.add_argument(
        '--app',
        help='Analyze scenarios for this application (e.g., acme-v2-0)'
    )
    parser.add_argument(
        '--namespace',
        default='',
        help='Namespace for ITS CRDs'
    )

    args = parser.parse_args()

    config = CollectorConfig.from_env()
    if not config.llm:
        logger.error("LLM not configured. Set LLM_PROVIDER and related env vars.")
        sys.exit(1)

    analyzer = ScenariosAnalyzer(config)

    result = analyzer.run(
        namespace=args.namespace,
        app_filter=args.app,
    )

    # Output JSON result to stdout for bash consumption
    print(json.dumps(result, indent=2, default=str))

    return 0 if result.get('analyzed') else 1


if __name__ == '__main__':
    sys.exit(main())
