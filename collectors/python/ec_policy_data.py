#!/usr/bin/env python3.11
"""Query EC policy data from the K8s API.

Replaces the GitLab→YAML pipeline with direct K8s REST API calls.
Outputs JSON to stdout for consumption by the ic shell script.
"""

import json
import logging
import sys
import argparse

from clients.konflux_client import KonfluxClient
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


def cmd_exceptions(client, name_filter=None):
    # type: (KonfluxClient, str) -> dict
    policies = client.get_ec_policies(name_filter=name_filter)
    all_exceptions = []
    for p in policies:
        all_exceptions.extend(client.extract_exceptions(p))
    active = [e for e in all_exceptions
              if e['days_left'] is None or e['days_left'] >= 0]
    return {
        'source': 'k8s-api',
        'policies_count': len(policies),
        'total_exceptions': len(all_exceptions),
        'active_exceptions': len(active),
        'exceptions': active,
    }


def cmd_expiring(client, name_filter=None, days=30):
    # type: (KonfluxClient, str, int) -> dict
    policies = client.get_ec_policies(name_filter=name_filter)
    all_exceptions = []
    for p in policies:
        all_exceptions.extend(client.extract_exceptions(p))
    expiring = [e for e in all_exceptions
                if e['days_left'] is not None and -7 <= e['days_left'] <= days]
    expiring.sort(key=lambda e: e['days_left'])
    return {
        'source': 'k8s-api',
        'policies_count': len(policies),
        'days_window': days,
        'exceptions': expiring,
    }


def cmd_bindings(client, name_filter=None):
    # type: (KonfluxClient, str) -> dict
    policies = client.get_ec_policies(name_filter=name_filter)
    policy_names = {p.get('metadata', {}).get('name') for p in policies} - {None}
    rpas = client.get_release_plan_admissions(name_filter=name_filter)
    bindings = KonfluxClient.extract_rpa_bindings(rpas, policy_names)
    bindings.sort(key=lambda b: (b['application'], b['rpa_name']))
    return {
        'source': 'k8s-api',
        'policies_count': len(policies),
        'rpas_total': len(rpas),
        'bindings': bindings,
    }


def main():
    parser = argparse.ArgumentParser(description='Query EC policy data from K8s API')
    parser.add_argument('--action', required=True,
                        choices=['exceptions', 'expiring', 'bindings'],
                        help='What data to fetch')
    parser.add_argument('--name-filter', default=None,
                        help='Filter policies/RPAs by name substring (e.g., rhoai)')
    parser.add_argument('--days', type=int, default=30,
                        help='Days window for expiring action (default: 30)')
    args = parser.parse_args()

    try:
        client = KonfluxClient()

        if args.action == 'exceptions':
            output = cmd_exceptions(client, args.name_filter)
        elif args.action == 'expiring':
            output = cmd_expiring(client, args.name_filter, args.days)
        elif args.action == 'bindings':
            output = cmd_bindings(client, args.name_filter)

        print(json.dumps(output, indent=2, default=str))
        sys.exit(0)

    except Exception as e:
        logger.error("EC policy query failed: %s", e, exc_info=True)
        error_result = {
            'source': 'error',
            'error': str(e),
        }
        print(json.dumps(error_result, indent=2))
        sys.exit(1)


if __name__ == '__main__':
    main()
