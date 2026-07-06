#!/usr/bin/env python3
"""Fetch build history for a component from Tekton Results.

Usage: python3 get_build_history.py <component_name> [--limit N] [--app APPLICATION]
Output: One line per build: timestamp|status|commit_sha|failed_task|image_url|pr_name
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collectors.build_failure_collector import BuildFailureCollector
from config import CollectorConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('component', help='Component name')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--app', default=None)
    parser.add_argument('--type', choices=['build', 'conforma'], default='build')
    args = parser.parse_args()

    config = CollectorConfig.from_env()
    if args.app:
        config.k8s.application_name = args.app

    if args.type == 'build':
        collector = BuildFailureCollector(config)
        history = collector.get_component_build_history(args.component, limit=args.limit)
        for entry in history:
            print("{}|{}|{}|{}|{}|{}".format(
                entry.get('timestamp', ''),
                entry.get('status', ''),
                entry.get('commit_sha', '') or '',
                entry.get('failed_task', '') or '',
                entry.get('image_url', '') or '',
                entry.get('name', ''),
            ))
    else:
        from collectors.conforma_violation_collector import ConformaViolationCollector
        collector = ConformaViolationCollector(config)
        history = collector.get_conforma_history(args.component, limit=args.limit)
        for entry in history:
            print("{}|{}|{}|{}".format(
                entry.get('timestamp', ''),
                entry.get('status', ''),
                entry.get('scenario', ''),
                entry.get('name', ''),
            ))


if __name__ == '__main__':
    main()
