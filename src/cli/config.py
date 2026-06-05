"""Configuration defaults for IC CLI (mirrors ic-config.sh)."""

import os
from pathlib import Path

from shared_config import APPLICATION_NAME, KONFLUX_UI_BASE, KUBEARCHIVE_URL, NAMESPACE  # noqa: F401

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
PYTHON_DIR = PROJECT_DIR / 'src'

KNOWN_APPLICATIONS = os.environ.get('KNOWN_APPLICATIONS', '').split()
AUTONOMOUS_MODE = os.environ.get('AUTONOMOUS_MODE', 'false')

DB_CONTAINER = os.environ.get('DB_CONTAINER', 'ci-autohealing-db')
DB_NAME = os.environ.get('DB_NAME', 'konflux_monitoring')
DB_USER = os.environ.get('DB_USER', 'postgres')

JIRA_URL = os.environ.get('JIRA_URL', '')
JIRA_EMAIL = os.environ.get('JIRA_EMAIL', '')
JIRA_PROJECT = os.environ.get('JIRA_PROJECT', '')
JIRA_COMPONENT = os.environ.get('JIRA_COMPONENT', '')

LANGFUSE_HOST = os.environ.get('LANGFUSE_HOST', 'http://localhost:3000')

COMPONENT_DATA_URL = os.environ.get('COMPONENT_DATA_URL', '')
COMPONENT_DATA_CACHE = os.environ.get('COMPONENT_DATA_CACHE', '/tmp/component-data.yaml')
COMPONENT_DATA_TTL = int(os.environ.get('COMPONENT_DATA_TTL', '86400'))

SYNC_STALENESS_MINUTES = int(os.environ.get('SYNC_STALENESS_MINUTES', '30'))
NIGHTLY_STALENESS_HOURS = int(os.environ.get('NIGHTLY_STALENESS_HOURS', '24'))

BLOB_STORE = os.environ.get('BLOB_STORE', 'local')
BLOB_THRESHOLD = int(os.environ.get('BLOB_THRESHOLD', '51200'))  # 50 KB

REF_CACHE_DIR = os.environ.get('IC_CACHE_DIR', os.path.expanduser('~/.ic/cache'))
REF_CACHE_TTL = int(os.environ.get('REF_CACHE_TTL', '300'))


def app_to_reporter_branch(app_name):
    """Convert APPLICATION_NAME to conforma-reporter branch name.

    product-v3-4 -> product-3.4, product-v3-4-ea-1 -> product-3.4-ea.1
    """
    import re
    m = re.match(r'(.+)-v(\d+)-(\d+)(.*)', app_name)
    if not m:
        return app_name
    product, major, minor, rest = m.group(1), m.group(2), m.group(3), m.group(4)
    version = '{}.{}'.format(major, minor)
    if rest:
        suffix = rest[1:] if rest.startswith('-') else rest
        version += '-' + suffix.replace('-', '.') if suffix else ''
    return '{}-{}'.format(product, version)


def conforma_scenario_label(scenario):
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
