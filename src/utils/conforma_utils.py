"""Conforma (Enterprise Contract) utility functions."""

import os
import re
import time

from logger import setup_logger

logger = setup_logger(__name__)

_exceptions_cache = {'data': None, 'ts': 0}
_CACHE_TTL = 300  # 5 minutes
_CACHE_FILE = os.path.join(os.path.expanduser('~'), '.ic', 'cache', 'ec_exceptions.json')

GITLAB_EC_BASE = os.environ.get('GITLAB_EC_POLICY_URL', '')

_SCENARIO_RE = re.compile(r'^conforma-(.+)-single-component$')
_VERSION_SUFFIX_RE = re.compile(r'-v\d+-\d+(?:-(?:ea|rc)-\d+)?$')

def extract_policy_from_scenario(scenario):
    """Extract EC policy name from a conforma scenario string.

    Handles both versioned and unversioned scenario names:
    >>> extract_policy_from_scenario('conforma-registry-rhoai-prod-v3-5-ea-2-single-component')
    'registry-rhoai-prod'
    >>> extract_policy_from_scenario('conforma-fbc-rhoai-prod-single-component')
    'fbc-rhoai-prod'
    >>> extract_policy_from_scenario('conforma-registry-rhoai-chart-prod-v3-5-single-component')
    'registry-rhoai-chart-prod'
    >>> extract_policy_from_scenario('conforma-registry-rhoai-prod-v3-5-rc-2-single-component')
    'registry-rhoai-prod'
    >>> extract_policy_from_scenario('')
    ''
    """
    if not scenario:
        return ''
    m = _SCENARIO_RE.match(scenario)
    if not m:
        return ''
    raw = m.group(1)
    return _VERSION_SUFFIX_RE.sub('', raw)


def policy_url(scenario):
    """Build a GitLab URL for the policy YAML from a scenario string."""
    name = extract_policy_from_scenario(scenario)
    if not name or not GITLAB_EC_BASE:
        return None
    return '{}/{}.yaml'.format(GITLAB_EC_BASE, name)


def policy_display_name(scenario):
    """Short display label for a policy, derived from the scenario.

    >>> policy_display_name('conforma-registry-rhoai-prod-v3-5-ea-2-single-component')
    'registry-rhoai-prod'
    >>> policy_display_name('conforma-fbc-rhoai-prod-single-component')
    'fbc-rhoai-prod'
    """
    return extract_policy_from_scenario(scenario)


_POLICY_CATEGORY_MAP = {
    'fbc-rhoai-prod': 'FBC',
    'fbc-rhoai-stage': 'FBC',
    'registry-rhoai-chart-prod': 'Charts',
    'registry-rhoai-chart-stage': 'Charts',
    'registry-rhoai-prod': 'Components',
    'registry-rhoai-stage': 'Components',
}


def categorize_policy(scenario):
    """Map a scenario to its high-level category: FBC, Components, or Charts.

    >>> categorize_policy('conforma-registry-rhoai-prod-v3-5-ea-2-single-component')
    'Components'
    >>> categorize_policy('conforma-fbc-rhoai-prod-v3-5-ea-2-single-component')
    'FBC'
    >>> categorize_policy('conforma-registry-rhoai-chart-prod-v3-4-single-component')
    'Charts'
    >>> categorize_policy('')
    'Other'
    """
    policy = extract_policy_from_scenario(scenario)
    return _POLICY_CATEGORY_MAP.get(policy, 'Other')


# ---------------------------------------------------------------------------
# Step 2: Exception cross-referencing
# ---------------------------------------------------------------------------

_VIOLATION_RULE_RE = re.compile(r'\[Violation\]\s+([\w.]+)')


def extract_violation_rules(violation_summary):
    """Extract unique failing rule names from a violation_summary text.

    The detailed-report step produces lines like:
        ✕ [Violation] hermetic_task.hermetic

    >>> sorted(extract_violation_rules(
    ...     '✕ [Violation] hermetic_task.hermetic\\n'
    ...     '  Reason: not hermetic\\n'
    ...     '✕ [Violation] labels.required_labels\\n'
    ...     '  Reason: missing labels\\n'
    ...     '✕ [Violation] hermetic_task.hermetic\\n'
    ... ))
    ['hermetic_task.hermetic', 'labels.required_labels']
    >>> extract_violation_rules('')
    set()
    >>> extract_violation_rules(None)
    set()
    """
    if not violation_summary:
        return set()
    return set(_VIOLATION_RULE_RE.findall(violation_summary))


_VIOLATION_BLOCK_RE = re.compile(
    r'✕ \[Violation\]\s+([\w.]+)(.*?)(?=✕ \[(?:Violation|Warning)\]|$)',
    re.DOTALL,
)
_TERM_RE = re.compile(r'^\s*Term:\s*(.+)', re.MULTILINE)
_DISALLOWED_ATTR_RE = re.compile(r'attributes\s+([\w:=]+)\s+which are not allowed')

# Rules where all violations per component share the same root cause,
# so Term detail is ignored — count as 1 per component regardless of term.
_COLLAPSE_RULES = frozenset({
    'hermetic_task.hermetic',
    'sbom_spdx.disallowed_package_attributes',
})


def _extract_detail(rule, block_body):
    """Extract the semantic detail for a violation block.

    For most rules, the Term field is used (e.g., RPM name, test name).
    For collapsed rules (same root cause per component), detail is empty
    or uses a group-level key from the Reason field.
    """
    if rule in _COLLAPSE_RULES:
        return ''
    term_match = _TERM_RE.search(block_body)
    detail = term_match.group(1).strip() if term_match else ''
    if detail.startswith('pkg:'):
        detail = detail.split('?')[0]
    return detail


def count_unique_violations(violation_summary):
    """Count unique (rule, detail) pairs from violation_summary text.

    Each violation block in the detailed-report has a rule name and a Term field
    that provides the semantic detail (e.g., package name for rpm_packages).
    Multiple image digests produce duplicate blocks for the same rule+term pair.

    For rules like hermetic_task and sbom_spdx.disallowed_package_attributes,
    all violations per component share the same root cause, so individual
    Terms are collapsed into a single violation.

    Returns (count, rule_list) where rule_list is a sorted list of
    {'rule': str, 'detail': str} dicts.

    >>> count_unique_violations(
    ...     '✕ [Violation] hermetic_task.hermetic\\n'
    ...     '  ImageRef: quay.io/img@sha256:aaa\\n'
    ...     '  Term: CVE-2026-22020\\n'
    ...     '✕ [Violation] hermetic_task.hermetic\\n'
    ...     '  ImageRef: quay.io/img@sha256:bbb\\n'
    ...     '  Term: buildah-remote-oci-ta\\n'
    ... )
    (1, [{'rule': 'hermetic_task.hermetic', 'detail': ''}])
    >>> count_unique_violations(
    ...     '✕ [Violation] rpm_packages.unique_version\\n'
    ...     '  Term: annobin\\n'
    ...     '✕ [Violation] rpm_packages.unique_version\\n'
    ...     '  Term: openssl\\n'
    ...     '✕ [Violation] rpm_packages.unique_version\\n'
    ...     '  Term: annobin\\n'
    ... )
    (2, [{'rule': 'rpm_packages.unique_version', 'detail': 'annobin'}, {'rule': 'rpm_packages.unique_version', 'detail': 'openssl'}])
    >>> count_unique_violations(
    ...     '✕ [Violation] sbom_spdx.disallowed_package_attributes\\n'
    ...     '  Term: pkg:pypi/absl-py@2.4.0\\n'
    ...     '✕ [Violation] sbom_spdx.disallowed_package_attributes\\n'
    ...     '  Term: pkg:pypi/aiohttp@3.14.0\\n'
    ... )
    (1, [{'rule': 'sbom_spdx.disallowed_package_attributes', 'detail': ''}])
    >>> count_unique_violations('')
    (0, [])
    >>> count_unique_violations(None)
    (0, [])
    """
    if not violation_summary:
        return 0, []
    seen = set()
    results = []
    for match in _VIOLATION_BLOCK_RE.finditer(violation_summary):
        rule = match.group(1)
        block_body = match.group(2)
        detail = _extract_detail(rule, block_body)
        key = (rule, detail)
        if key not in seen:
            seen.add(key)
            results.append({'rule': rule, 'detail': detail})
    results.sort(key=lambda r: (r['rule'], r['detail']))
    return len(results), results


def compute_violation_coverage(rules, exceptions):
    """Classify a violation's exception coverage given its rules and the policy's exceptions.

    Args:
        rules: set of rule name strings from extract_violation_rules()
        exceptions: list of exception dicts (from KonfluxClient.extract_exceptions())
                    already filtered to the correct policy

    Returns dict with coverage classification, or None if rules is empty.

    >>> compute_violation_coverage(
    ...     {'hermetic_task.hermetic', 'labels.required_labels'},
    ...     [{'value': 'hermetic_task.hermetic', 'permanent': True,
    ...       'effectiveUntil': None, 'days_left': None}]
    ... )['coverage']
    'partially_covered'
    >>> compute_violation_coverage(
    ...     {'hermetic_task.hermetic'},
    ...     [{'value': 'hermetic_task.hermetic', 'permanent': True,
    ...       'effectiveUntil': None, 'days_left': None}]
    ... )['coverage']
    'fully_covered'
    >>> compute_violation_coverage(
    ...     {'hermetic_task.hermetic'},
    ...     [{'value': 'labels.required_labels', 'permanent': True,
    ...       'effectiveUntil': None, 'days_left': None}]
    ... )['coverage']
    'not_covered'
    >>> compute_violation_coverage(set(), []) is None
    True
    """
    if not rules:
        return None

    active = [exc for exc in exceptions
              if exc.get('permanent') or exc.get('days_left') is None
              or exc.get('days_left', -1) >= 0]
    exception_values = {exc.get('value', '') for exc in active}
    covered = rules & exception_values
    uncovered = rules - exception_values

    matching = []
    for exc in active:
        if exc.get('value', '') in covered:
            matching.append({
                'value': exc['value'],
                'permanent': exc.get('permanent', False),
                'effectiveUntil': exc.get('effectiveUntil'),
                'days_left': exc.get('days_left'),
            })

    if not uncovered:
        coverage = 'fully_covered'
    elif covered:
        coverage = 'partially_covered'
    else:
        coverage = 'not_covered'

    return {
        'coverage': coverage,
        'covered_rules': sorted(covered),
        'uncovered_rules': sorted(uncovered),
        'matching_exceptions': matching,
    }


def _resolve_tenant_namespace(namespace):
    """Resolve the tenant namespace where EC policies live.

    EC policies are in the tenant namespace (e.g., 'rhoai-tenant'),
    not the workspace namespace.
    """
    ns = namespace or os.environ.get('NAMESPACE', '')
    if ns and ns.endswith('-tenant'):
        return ns
    tenant = os.environ.get('TENANT_NAMESPACE', '')
    if tenant:
        return tenant
    if ns:
        return ns + '-tenant'
    return 'rhoai-tenant'


def _read_file_cache():
    """Read exceptions from file cache if fresh enough."""
    try:
        import json
        if not os.path.exists(_CACHE_FILE):
            return None
        age = time.time() - os.path.getmtime(_CACHE_FILE)
        if age > _CACHE_TTL:
            return None
        with open(_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _write_file_cache(data):
    """Write exceptions to file cache."""
    try:
        import json
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


def fetch_exceptions_by_policy(namespace=None):
    """Fetch all EC policy exceptions grouped by policy name.

    Returns {policy_name: [exception_dicts]} or {} on failure.
    Results are cached (memory + file) for 5 minutes.
    Gracefully degrades — never raises.
    """
    now = time.time()
    if _exceptions_cache['data'] is not None and (now - _exceptions_cache['ts']) < _CACHE_TTL:
        return _exceptions_cache['data']
    cached = _read_file_cache()
    if cached is not None:
        _exceptions_cache['data'] = cached
        _exceptions_cache['ts'] = now
        return cached
    try:
        from clients.konflux_client import KonfluxClient
        tenant_ns = _resolve_tenant_namespace(namespace)
        client = KonfluxClient(namespace=tenant_ns)
        policies = client.get_ec_policies()
        result = {}
        for policy in policies:
            policy_name = policy.get('metadata', {}).get('name', '')
            if not policy_name:
                continue
            exceptions = client.extract_exceptions(policy)
            if exceptions:
                result[policy_name] = exceptions
        if result:
            _exceptions_cache['data'] = result
            _exceptions_cache['ts'] = now
            _write_file_cache(result)
            return result
    except Exception as e:
        logger.debug("Cannot fetch EC policy exceptions from cluster: %s", e)

    try:
        result = fetch_exceptions_from_gitlab()
        if result:
            _exceptions_cache['data'] = result
            _exceptions_cache['ts'] = now
            _write_file_cache(result)
            return result
    except Exception as e:
        logger.debug("GitLab fallback also failed: %s", e)

    return {}


def lookup_exceptions(policy_name, scenario, exceptions_by_policy):
    """Find exceptions for a policy, including the -future variant.

    Scenarios like 'conforma-registry-rhoai-prod-v3-5-ea-2-single-component'
    extract to 'registry-rhoai-prod', but active exceptions live in
    'registry-rhoai-prod-v3-5-ea-2-future'. This function merges both.
    """
    exc_list = list(exceptions_by_policy.get(policy_name, []))
    version_match = re.search(r'-(v\d+-\d+(?:-ea-?\d+)?)-', scenario or '')
    if version_match:
        future_name = '{}-{}-future'.format(policy_name, version_match.group(1))
        exc_list.extend(exceptions_by_policy.get(future_name, []))
    return exc_list


def enrich_with_coverage(violations, exceptions_by_policy):
    """Add exception coverage fields to a list of violation dicts in-place.

    Each violation dict must have 'policy_name' and 'violation_summary'.
    If exceptions_by_policy is empty, coverage fields are set to None.
    """
    if not exceptions_by_policy:
        return
    for v in violations:
        policy_name = v.get('policy_name', '')
        summary = v.get('violation_summary', '')
        if not policy_name or not summary:
            continue
        rules = extract_violation_rules(summary)
        exc_list = lookup_exceptions(
            policy_name, v.get('scenario', ''), exceptions_by_policy)
        cov = compute_violation_coverage(rules, exc_list)
        if cov:
            v['exception_coverage'] = cov['coverage']
            v['covered_rules'] = cov['covered_rules']
            v['uncovered_rules'] = cov['uncovered_rules']
            v['matching_exceptions'] = cov['matching_exceptions']


def _counterpart_policy(policy_name):
    """Swap '-stage' <-> '-prod' in a policy name.

    >>> _counterpart_policy('registry-rhoai-prod')
    'registry-rhoai-stage'
    >>> _counterpart_policy('registry-rhoai-stage')
    'registry-rhoai-prod'
    >>> _counterpart_policy('fbc-rhoai-stage')
    'fbc-rhoai-prod'
    >>> _counterpart_policy('registry-rhoai-chart-prod')
    'registry-rhoai-chart-stage'
    >>> _counterpart_policy('unknown-policy')
    ''
    """
    if '-prod' in policy_name:
        return policy_name.replace('-prod', '-stage')
    if '-stage' in policy_name:
        return policy_name.replace('-stage', '-prod')
    return ''


def policy_env(policy_name):
    """Extract environment from a policy name.

    >>> policy_env('registry-rhoai-prod')
    'prod'
    >>> policy_env('fbc-rhoai-stage')
    'stage'
    >>> policy_env('unknown')
    ''
    """
    if '-prod' in policy_name:
        return 'prod'
    if '-stage' in policy_name:
        return 'stage'
    return ''


def compute_coverage_by_env(rules, scenario, exceptions_by_policy):
    """Check exception coverage in both stage and prod policies.

    Returns dict with per-env coverage and a combined tag string:
        'S' (stage only), 'P' (prod only), 'S+P' (both), or None.
    """
    policy_name = extract_policy_from_scenario(scenario)
    if not policy_name or not rules:
        return {'stage': None, 'prod': None, 'combined_tag': None}

    counterpart = _counterpart_policy(policy_name)
    own_env = policy_env(policy_name)

    result = {'stage': None, 'prod': None, 'combined_tag': None}

    for env, pname in [('stage', None), ('prod', None)]:
        if own_env == env:
            pname = policy_name
        elif counterpart and policy_env(counterpart) == env:
            pname = counterpart
        else:
            continue
        exc_list = lookup_exceptions(pname, scenario, exceptions_by_policy)
        cov = compute_violation_coverage(rules, exc_list)
        result[env] = cov

    stage_cov = (result.get('stage') or {}).get('coverage')
    prod_cov = (result.get('prod') or {}).get('coverage')

    has_stage = stage_cov in ('fully_covered', 'partially_covered')
    has_prod = prod_cov in ('fully_covered', 'partially_covered')

    if has_stage and has_prod:
        result['combined_tag'] = 'S+P'
    elif has_stage:
        result['combined_tag'] = 'S'
    elif has_prod:
        result['combined_tag'] = 'P'

    return result


def compute_exception_coverage_details(rules, scenario, exceptions_by_policy):
    """Compute full exception coverage details for a violation.

    Consolidates the repeated pattern of calling compute_coverage_by_env,
    extracting per-env coverage, computing policy URLs, and uncovered rules.

    Returns dict with keys: coverage, stage, prod, env_tag,
    policy_url_stage, policy_url_prod, covered_rules_stage, covered_rules_prod,
    uncovered_rules_stage, uncovered_rules_prod.
    """
    empty = {
        'coverage': None, 'stage': None, 'prod': None, 'env_tag': None,
        'policy_url_stage': None, 'policy_url_prod': None,
        'covered_rules_stage': [], 'covered_rules_prod': [],
        'uncovered_rules_stage': [], 'uncovered_rules_prod': [],
    }
    if not exceptions_by_policy:
        return empty

    env_cov = compute_coverage_by_env(rules, scenario, exceptions_by_policy)
    stage_data = env_cov.get('stage') or {}
    prod_data = env_cov.get('prod') or {}

    stage = stage_data.get('coverage')
    prod = prod_data.get('coverage')
    covered_stage = stage_data.get('covered_rules', [])
    covered_prod = prod_data.get('covered_rules', [])

    all_rules = set(rules) if rules else set()
    uncovered_stage = sorted(all_rules - set(covered_stage)) if stage else []
    uncovered_prod = sorted(all_rules - set(covered_prod)) if prod else []

    p_url_stage = None
    p_url_prod = None
    policy_name = extract_policy_from_scenario(scenario)
    counterpart = _counterpart_policy(policy_name)
    if stage in ('fully_covered', 'partially_covered'):
        sp = policy_name if policy_env(policy_name) == 'stage' else counterpart
        p_url_stage = policy_url_with_line(sp, covered_stage)
    if prod in ('fully_covered', 'partially_covered'):
        pp = policy_name if policy_env(policy_name) == 'prod' else counterpart
        p_url_prod = policy_url_with_line(pp, covered_prod)

    return {
        'coverage': stage or prod,
        'stage': stage,
        'prod': prod,
        'env_tag': env_cov.get('combined_tag'),
        'policy_url_stage': p_url_stage,
        'policy_url_prod': p_url_prod,
        'covered_rules_stage': covered_stage,
        'covered_rules_prod': covered_prod,
        'uncovered_rules_stage': uncovered_stage,
        'uncovered_rules_prod': uncovered_prod,
    }


_yaml_content_cache = {}


def _fetch_policy_yaml_raw(policy_name):
    """Fetch raw YAML content for a policy from GitLab (cached in memory)."""
    if policy_name in _yaml_content_cache:
        return _yaml_content_cache[policy_name]

    if not GITLAB_EC_BASE:
        return ''

    gitlab_token = os.environ.get('GITLAB_TOKEN', '')
    if not gitlab_token:
        return ''

    import requests
    raw_url = GITLAB_EC_BASE.replace('/-/tree/', '/-/raw/') + '/{}.yaml'.format(policy_name)
    try:
        resp = requests.get(raw_url, headers={'PRIVATE-TOKEN': gitlab_token}, timeout=10)
        if resp.status_code == 200:
            _yaml_content_cache[policy_name] = resp.text
            return resp.text
    except Exception:
        pass
    _yaml_content_cache[policy_name] = ''
    return ''


def find_rule_line(policy_name, rule):
    """Find the line number where a rule appears in a policy YAML.

    Returns the line number (1-based) or None if not found.
    """
    content = _fetch_policy_yaml_raw(policy_name)
    if not content:
        return None
    for i, line in enumerate(content.splitlines(), 1):
        if rule in line:
            return i
    return None


def policy_url_with_line(policy_name, rules=None):
    """Build a GitLab URL for a policy, linking to the first matching rule line.

    If rules are provided, links to the first rule found in the YAML.
    Uses -/blob/ instead of -/tree/ for line anchors.
    """
    if not policy_name or not GITLAB_EC_BASE:
        return None
    base = GITLAB_EC_BASE.replace('/-/tree/', '/-/blob/')
    url = '{}/{}.yaml'.format(base, policy_name)
    if rules:
        for rule in sorted(rules):
            line = find_rule_line(policy_name, rule)
            if line:
                return '{}#L{}'.format(url, line)
    return url


def fetch_exceptions_from_gitlab():
    """Fallback: fetch EC policy exceptions from GitLab YAML files.

    Parses the same config.exclude/volatileConfig.exclude structure
    as the K8s EnterpriseContractPolicy CRs.
    """
    import yaml

    if not GITLAB_EC_BASE:
        return {}

    gitlab_token = os.environ.get('GITLAB_TOKEN', '')
    if not gitlab_token:
        return {}

    policy_names = [
        'registry-rhoai-prod', 'registry-rhoai-stage',
        'registry-rhoai-chart-prod', 'registry-rhoai-chart-stage',
        'fbc-rhoai-prod', 'fbc-rhoai-stage',
    ]

    import requests
    result = {}
    for pname in policy_names:
        url = '{}/{}.yaml'.format(GITLAB_EC_BASE, pname)
        try:
            headers = {'PRIVATE-TOKEN': gitlab_token}
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            doc = yaml.safe_load(resp.text)
            if not doc or not isinstance(doc, dict):
                continue
            fake_policy = {
                'metadata': {'name': pname},
                'spec': doc.get('spec', {}),
            }
            from clients.konflux_client import KonfluxClient
            exceptions = KonfluxClient.extract_exceptions(fake_policy)
            if exceptions:
                result[pname] = exceptions
        except Exception as e:
            logger.debug("GitLab fallback for %s failed: %s", pname, e)
    return result
