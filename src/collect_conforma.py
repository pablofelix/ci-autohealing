#!/usr/bin/env python3
"""Conforma test failure collector entry point.

Use collectors.conforma_violation_collector.ConformaViolationCollector for the implementation.
"""

import sys

from config import CollectorConfig
from collectors.conforma_violation_collector import ConformaViolationCollector


def main():
    config = CollectorConfig.from_env()
    collector = ConformaViolationCollector(config)
    collector.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
