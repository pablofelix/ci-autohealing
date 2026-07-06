#!/usr/bin/env python3
"""Query advisory status from Pyxis.

Fetches advisory lifecycle data (QE, REL_PREP, SHIPPED_LIVE) from Pyxis
for release tracking. Outputs JSON to stdout.
"""

import argparse
import json
import logging
import sys

from clients.pyxis_client import PyxisClient
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
    parser = argparse.ArgumentParser(description='Query advisory status from Pyxis')
    parser.add_argument('--target', default='prod', choices=['stage', 'prod'],
                        help='Pyxis environment (default: prod)')
    parser.add_argument('--filter', dest='filter_str',
                        help='RSQL filter (e.g., errata_id==RHSA-2024:1234)')
    parser.add_argument('--page-size', type=int, default=10,
                        help='Number of results (default: 10)')
    args = parser.parse_args()

    try:
        logger.info("Querying advisories from Pyxis (%s)...", args.target)
        client = PyxisClient(target=args.target)
        advisories = client.get_advisories(
            filter_str=args.filter_str,
            page_size=args.page_size,
        )

        output = {
            'target': args.target,
            'count': len(advisories),
            'advisories': advisories,
        }

        print(json.dumps(output, indent=2, default=str))
        sys.exit(0)

    except Exception as e:
        logger.error("Advisory query failed: %s", e, exc_info=True)
        error_result = {
            'status': 'error',
            'error': str(e),
        }
        print(json.dumps(error_result, indent=2))
        sys.exit(1)


if __name__ == '__main__':
    main()
