"""Configuration defaults for IC CLI (mirrors ic-config.sh)."""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
PYTHON_DIR = PROJECT_DIR / 'src'

NAMESPACE = os.environ.get('NAMESPACE', 'NAMESPACE_PLACEHOLDER')
APPLICATION_NAME = os.environ.get('APPLICATION_NAME', 'acme-v2-0')
KNOWN_APPLICATIONS = os.environ.get('KNOWN_APPLICATIONS', 'acme-v2-0 acme-v2-1-ea-1').split()
AUTONOMOUS_MODE = os.environ.get('AUTONOMOUS_MODE', 'false')

DB_CONTAINER = os.environ.get('DB_CONTAINER', 'ci-autohealing-db')
DB_NAME = os.environ.get('DB_NAME', 'konflux_monitoring')
DB_USER = os.environ.get('DB_USER', 'postgres')

KUBEARCHIVE_URL = os.environ.get(
    'KUBEARCHIVE_URL',
    'https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN',
)
KONFLUX_UI_BASE = os.environ.get(
    'KONFLUX_UI_BASE',
    'https://konflux-ui.apps.CLUSTER_DOMAIN',
)

JIRA_URL = os.environ.get('JIRA_URL', 'https://JIRA_HOST')
JIRA_EMAIL = os.environ.get('JIRA_EMAIL', 'user@example.com')
JIRA_PROJECT = os.environ.get('JIRA_PROJECT', 'RHOAIENG')
JIRA_COMPONENT = os.environ.get('JIRA_COMPONENT', 'DevOps')

LANGFUSE_HOST = os.environ.get('LANGFUSE_HOST', 'http://localhost:3000')

COMPONENT_DATA_URL = os.environ.get(
    'COMPONENT_DATA_URL',
    'https://GITLAB_INTERNAL_HOST/wznoinsk/rhoai-monitoring/-/raw/main/data/rhoai-component-data.yaml',
)
COMPONENT_DATA_CACHE = os.environ.get('COMPONENT_DATA_CACHE', '/tmp/rhoai-component-data.yaml')
COMPONENT_DATA_TTL = int(os.environ.get('COMPONENT_DATA_TTL', '86400'))

SYNC_STALENESS_MINUTES = int(os.environ.get('SYNC_STALENESS_MINUTES', '30'))


def app_to_reporter_branch(app_name):
    # type: (str) -> str
    """Convert APPLICATION_NAME to conforma-reporter branch name.

    acme-v2-0 -> acme-3.4, acme-v2-0-ea-1 -> acme-3.4-ea.1
    """
    version = app_name.replace('rhoai-v', '', 1)
    parts = version.split('-', 2)
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        version = '{}.{}'.format(parts[0], parts[1])
        if len(parts) > 2:
            rest = parts[2].replace('-', '.')
            version = '{}-{}'.format(version, rest)
    return 'rhoai-{}'.format(version)


def conforma_scenario_label(scenario):
    # type: (str) -> str
    if scenario.endswith('-single-component'):
        return 'single'
    if scenario.startswith('conforma-fbc-'):
        return 'fbc'
    if scenario.startswith('conforma-custom-'):
        return 'custom'
    if '-chart-' in scenario:
        return 'chart'
    if scenario == 'csv-import':
        return 'csv'
    return '-'
