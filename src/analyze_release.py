#!/usr/bin/env python3.11
"""Entry point for AI analysis of release pipeline failures.

Thin shim that loads config and runs ReleaseFailureAnalyzer.
Outputs JSON to stdout for consumption by the ic shell script.
Logs go to stderr to keep stdout clean for JSON parsing.
"""

import json
import logging
import sys
import argparse
from config import CollectorConfig
from analyzers.release_failure_analyzer import ReleaseFailureAnalyzer
from logger import setup_logger

# Redirect ALL logger handlers to stderr so stdout is clean for JSON output.
# This must run after imports since setup_logger() is called at module level.
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
    """Analyze a failed release and output results as JSON."""
    parser = argparse.ArgumentParser(description='Analyze release failures with AI')
    parser.add_argument('--release', required=True, help='Release CR name to analyze')
    parser.add_argument('--namespace', help='Namespace (default: from config)')
    parser.add_argument('--force', action='store_true', help='Re-analyze even if already analyzed')
    args = parser.parse_args()

    try:
        config = CollectorConfig.from_env()

        if not config.llm:
            logger.error("LLM not configured. Set LLM_PROVIDER and related env vars in .env")
            sys.exit(1)

        analyzer = ReleaseFailureAnalyzer(config)
        result = analyzer.analyze_release(
            release_name=args.release,
            namespace=args.namespace,
            force=args.force,
        )

        # Output JSON to stdout for ic to parse
        print(json.dumps(result, indent=2, default=str))

        sys.exit(0)

    except Exception as e:
        logger.error("Release analysis failed: %s", e, exc_info=True)
        error_result = {
            'status': 'error',
            'error': str(e),
            'release_name': args.release,
        }
        print(json.dumps(error_result, indent=2))
        sys.exit(1)


if __name__ == '__main__':
    main()
