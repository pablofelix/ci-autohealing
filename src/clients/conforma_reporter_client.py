"""Conforma Reporter client — fetches violation/warning CSVs from GitHub.

The conforma-reporter repo (red-hat-data-services/conforma-reporter) publishes
daily/weekly EC violation reports as CSV files on version-specific branches.
This client fetches and caches those CSVs, normalizing them to the same dict
format that ic uses internally for cluster-sourced violations.

Report layout on each branch:
    stage/future/build_type_latest/conforma-violations-report.csv
    stage/future/build_type_nightly/conforma-violations-report.csv
    prod/future/build_type_latest/conforma-violations-report.csv
    prod/release_day/conforma-violations-report.csv
"""

import base64
import csv
import io
import json
import os
import time

import requests

from logger import setup_logger

logger = setup_logger(__name__)

REPO = 'red-hat-data-services/conforma-reporter'
API_BASE = 'https://api.github.com'
CACHE_DIR = os.path.join(os.path.expanduser('~'), '.ic', 'cache', 'reporter')
CACHE_TTL = 3600


def _report_path(env, build_type):
    """Resolve the CSV path within the reporter repo.

    >>> _report_path('stage', 'latest')
    'stage/future/build_type_latest/conforma-violations-report.csv'
    >>> _report_path('prod', 'nightly')
    'prod/future/build_type_nightly/conforma-violations-report.csv'
    >>> _report_path('prod', 'release_day')
    'prod/release_day/conforma-violations-report.csv'
    """
    if build_type == 'release_day':
        return '{}/release_day/conforma-violations-report.csv'.format(env)
    return '{}/future/build_type_{}/conforma-violations-report.csv'.format(env, build_type)


def _warnings_path(env, build_type):
    if build_type == 'release_day':
        return '{}/release_day/conforma-warnings-report.csv'.format(env)
    return '{}/future/build_type_{}/conforma-warnings-report.csv'.format(env, build_type)


def _cache_key(branch, path):
    safe = path.replace('/', '_').replace('.', '_')
    return os.path.join(CACHE_DIR, branch, safe)


def _read_cache(cache_path):
    try:
        if not os.path.exists(cache_path):
            return None
        age = time.time() - os.path.getmtime(cache_path)
        if age > CACHE_TTL:
            return None
        with open(cache_path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(cache_path, data):
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


def _fetch_file(branch, path, token=None):
    """Fetch a file from the reporter repo via GitHub API."""
    url = '{}/repos/{}/contents/{}?ref={}'.format(API_BASE, REPO, path, branch)
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'ci-autohealing/1.0',
    }
    if token:
        headers['Authorization'] = 'token {}'.format(token)
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 404:
            logger.debug("Reporter file not found: %s on branch %s", path, branch)
            return None
        resp.raise_for_status()
        data = resp.json()
        content = data.get('content', '')
        return base64.b64decode(content).decode('utf-8')
    except Exception as e:
        logger.warning("Failed to fetch reporter file %s: %s", path, e)
        return None


def _parse_csv(content):
    """Parse CSV content into list of row dicts."""
    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def _infer_scenario(component_name, env):
    """Synthesize a scenario string so categorize_policy() works.

    The reporter CSV doesn't carry a policy column — the policy is implicit
    in the file path. We infer it from the component name pattern:
      fbc-* → fbc-rhoai-{env}
      *-chart-* → registry-rhoai-chart-{env}
      everything else → registry-rhoai-{env}
    """
    name_lower = component_name.lower()
    if 'fbc' in name_lower:
        policy = 'fbc-rhoai-{}'.format(env)
    elif 'chart' in name_lower:
        policy = 'registry-rhoai-chart-{}'.format(env)
    else:
        policy = 'registry-rhoai-{}'.format(env)
    return 'conforma-{}-single-component'.format(policy)


def _group_by_component(rows, env='prod'):
    """Group CSV rows by component_name and aggregate counts.

    The reporter CSV has one row per (component, image, rule). We aggregate
    to one entry per component matching ic's violation dict format.
    """
    components = {}
    for row in rows:
        comp = row.get('component_name', '')
        if not comp:
            continue
        row_type = row.get('type', 'violation')
        if comp not in components:
            components[comp] = {
                'component_name': comp,
                'component': comp,
                'violations_count': 0,
                'warnings_count': 0,
                'successes_count': 0,
                'scenario': _infer_scenario(comp, env),
                'violation_summary': '',
                'rules': set(),
                'images': set(),
            }
        entry = components[comp]
        if row_type == 'violation':
            entry['violations_count'] += 1
        elif row_type == 'warning':
            entry['warnings_count'] += 1
        code = row.get('code', '')
        if code:
            entry['rules'].add(code)
        image = row.get('image', '')
        if image:
            entry['images'].add(image)

    result = []
    for _comp, entry in sorted(components.items()):
        rules = sorted(entry.pop('rules'))
        images = entry.pop('images')
        summary_lines = []
        for rule in rules:
            summary_lines.append('✕ [Violation] {}'.format(rule))
        entry['violation_summary'] = '\n'.join(summary_lines)
        entry['unique_violations'] = len(rules)
        entry['image_count'] = len(images)
        result.append(entry)
    return result


def _group_by_rule(rows):
    """Group CSV rows by rule code, collecting affected components and solutions.

    Returns list sorted by component count (descending).
    """
    rules = {}
    for row in rows:
        code = row.get('code', '')
        if not code:
            continue
        comp = row.get('component_name', '')
        if code not in rules:
            rules[code] = {
                'rule': code,
                'title': row.get('title', ''),
                'solution': row.get('solution', ''),
                'components': set(),
                'violation_rows': 0,
            }
        entry = rules[code]
        entry['violation_rows'] += 1
        if comp:
            entry['components'].add(comp)

    result = []
    for entry in rules.values():
        entry['components'] = sorted(entry['components'])
        entry['count'] = len(entry['components'])
        result.append(entry)
    result.sort(key=lambda r: (-r['count'], r['rule']))
    return result


def fetch_reporter_rules(branch, env='stage', build_type='latest'):
    """Fetch violations grouped by rule code from the conforma-reporter.

    Returns list of rule dicts sorted by component count (desc):
    [{'rule', 'title', 'solution', 'components': [str], 'count': int, 'violation_rows': int}]
    """
    path = _report_path(env, build_type)
    cache_path = _cache_key(branch, path + '.rules')
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    token = os.environ.get('GITHUB_TOKEN', '')
    content = _fetch_file(branch, path, token)
    if content is None:
        return []

    rows = _parse_csv(content)
    rules = _group_by_rule(rows)
    _write_cache(cache_path, rules)
    return rules


def fetch_reporter_violations(branch, env='stage', build_type='latest'):
    """Fetch and parse violations from the conforma-reporter.

    Args:
        branch: Reporter branch (e.g., 'rhoai-3.5-ea.2')
        env: 'stage' or 'prod'
        build_type: 'latest', 'nightly', or 'release_day'

    Returns list of violation dicts in ic's internal format, or empty list on error.
    """
    path = _report_path(env, build_type)
    cache_path = _cache_key(branch, path)
    cached = _read_cache(cache_path)
    if cached is not None:
        logger.debug("Reporter cache hit: %s/%s", branch, path)
        return cached

    token = os.environ.get('GITHUB_TOKEN', '')
    content = _fetch_file(branch, path, token)
    if content is None:
        return []

    rows = _parse_csv(content)
    violations = _group_by_component(rows, env=env)
    _write_cache(cache_path, violations)
    logger.info("Fetched %d components from reporter %s/%s", len(violations), branch, path)
    return violations


def fetch_reporter_warnings(branch, env='stage', build_type='latest'):
    """Same as fetch_reporter_violations but for warnings CSV."""
    path = _warnings_path(env, build_type)
    cache_path = _cache_key(branch, path)
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    token = os.environ.get('GITHUB_TOKEN', '')
    content = _fetch_file(branch, path, token)
    if content is None:
        return []

    rows = _parse_csv(content)
    warnings = _group_by_component(rows, env=env)
    _write_cache(cache_path, warnings)
    return warnings
