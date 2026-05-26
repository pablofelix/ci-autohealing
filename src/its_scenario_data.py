#!/usr/bin/env python3
"""Query IntegrationTestScenario CRDs from the K8s API.

Outputs JSON to stdout for consumption by the ic shell script.
"""

import json
import logging
import sys
import argparse
from collections import Counter

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


def cmd_list(client, namespace, app_filter=None):
    # type: (KonfluxClient, str, str) -> dict
    scenarios = client.get_integration_test_scenarios(
        namespace=namespace, app_filter=app_filter,
    )
    extracted = [KonfluxClient.extract_its_metadata(s) for s in scenarios]
    extracted.sort(key=lambda s: (s['application'], s['is_disabled'], s['name']))

    active = [s for s in extracted if not s['is_disabled']]
    disabled = [s for s in extracted if s['is_disabled']]
    conforma = [s for s in extracted if s['is_conforma']]

    apps = sorted(set(s['application'] for s in extracted))

    return {
        'source': 'k8s-api',
        'namespace': namespace,
        'application_filter': app_filter,
        'total': len(extracted),
        'active': len(active),
        'disabled': len(disabled),
        'conforma': len(conforma),
        'applications': apps,
        'scenarios': extracted,
    }


def _normalize_scenario_type(name):
    # type: (str) -> str
    """Strip app-specific suffixes to get a canonical scenario type."""
    import re
    cleaned = re.sub(r'-v\d+-\d+(-ea-?\d+)?(-single-component)?(-future)?$', '', name)
    cleaned = re.sub(r'-v\d+-\d+$', '', cleaned)
    return cleaned


def cmd_gaps(client, namespace, app_filter=None):
    # type: (KonfluxClient, str, str) -> dict
    scenarios = client.get_integration_test_scenarios(namespace=namespace)
    extracted = [KonfluxClient.extract_its_metadata(s) for s in scenarios]

    apps_scenarios = {}  # type: dict
    for s in extracted:
        if s['is_disabled'] or s['is_future']:
            continue
        app = s['application']
        if app_filter and app_filter not in app:
            continue
        apps_scenarios.setdefault(app, set()).add(_normalize_scenario_type(s['name']))

    if not apps_scenarios:
        return {'source': 'k8s-api', 'gaps': [], 'apps_checked': 0}

    type_counts = Counter()  # type: Counter
    for types in apps_scenarios.values():
        type_counts.update(types)

    threshold = max(1, int(len(apps_scenarios) * 0.6))
    common_types = {t for t, c in type_counts.items() if c >= threshold}

    gaps = []
    for app in sorted(apps_scenarios):
        missing = common_types - apps_scenarios[app]
        if missing:
            gaps.append({
                'application': app,
                'has': sorted(apps_scenarios[app]),
                'missing': sorted(missing),
            })

    return {
        'source': 'k8s-api',
        'apps_checked': len(apps_scenarios),
        'common_types': sorted(common_types),
        'threshold_pct': 60,
        'gaps': gaps,
    }


def cmd_summary(client, namespace, app_filter=None):
    # type: (KonfluxClient, str, str) -> dict
    """Compact per-app summary for bash consumption."""
    scenarios = client.get_integration_test_scenarios(namespace=namespace)
    extracted = [KonfluxClient.extract_its_metadata(s) for s in scenarios]

    if app_filter:
        extracted = [s for s in extracted if s['application'] == app_filter]

    per_app = {}  # type: dict
    for s in extracted:
        app = s['application']
        entry = per_app.setdefault(app, {
            'total': 0, 'active': 0, 'disabled': 0,
            'conforma': 0, 'future': 0, 'policies': [],
        })
        entry['total'] += 1
        if s['is_disabled']:
            entry['disabled'] += 1
        else:
            entry['active'] += 1
        if s['is_conforma']:
            entry['conforma'] += 1
        if s['is_future']:
            entry['future'] += 1
        if s['policy_ref'] and s['policy_ref'] not in entry['policies']:
            entry['policies'].append(s['policy_ref'])

    return {
        'source': 'k8s-api',
        'namespace': namespace,
        'application_filter': app_filter,
        'apps': per_app,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Query IntegrationTestScenario CRDs from K8s API')
    parser.add_argument('--action', required=True,
                        choices=['list', 'gaps', 'summary'],
                        help='What data to fetch')
    parser.add_argument('--namespace', default='',
                        help='Namespace for ITS CRDs')
    parser.add_argument('--app-filter', default=None,
                        help='Filter by application name')
    args = parser.parse_args()

    try:
        client = KonfluxClient()

        if args.action == 'list':
            output = cmd_list(client, args.namespace, args.app_filter)
        elif args.action == 'gaps':
            output = cmd_gaps(client, args.namespace, args.app_filter)
        elif args.action == 'summary':
            output = cmd_summary(client, args.namespace, args.app_filter)

        print(json.dumps(output, indent=2, default=str))
        sys.exit(0)

    except Exception as e:
        logger.error("ITS query failed: %s", e, exc_info=True)
        error_result = {
            'source': 'error',
            'error': str(e),
        }
        print(json.dumps(error_result, indent=2))
        sys.exit(1)


if __name__ == '__main__':
    main()
