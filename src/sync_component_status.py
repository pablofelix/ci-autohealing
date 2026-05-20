#!/usr/bin/env python3
"""Component status synchronization entry point.

Use collectors.status_synchronizer.StatusSynchronizer for the implementation.
"""

import sys
from pathlib import Path
from dataclasses import replace

from config import CollectorConfig
from collectors.status_synchronizer import StatusSynchronizer


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Synchronize component status - mark resolved and track successes'
    )
    parser.add_argument(
        '--components-file', type=Path,
        help='File with component names (one per line)'
    )
    args = parser.parse_args()

    config = CollectorConfig.from_env()
    if args.components_file:
        config = replace(config, components_file=args.components_file)

    synchronizer = StatusSynchronizer(config)
    synchronizer.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
