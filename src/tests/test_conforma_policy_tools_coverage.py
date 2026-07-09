"""Comprehensive tests for conforma/policy_tools.py.

Covers pure functions (strip_version_suffix, extract_policy_from_scenario,
policy_url, categorize_policy, extract_violation_rules, count_unique_violations,
_extract_detail, compute_violation_coverage, _counterpart_policy, policy_env,
compute_blocks, _resolve_tenant_namespace, detect_component_policy_type,
expected_policy_prefix, _is_rule_excluded, lookup_exceptions, enrich_with_coverage,
apply_policy_correction) and I/O-dependent functions (fetch_exceptions_by_policy,
_read_file_cache, _write_file_cache) with all external deps mocked.
"""

import os
import sys
import time
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from conforma.policy_tools import (  # noqa: E402
    _counterpart_policy,
    _extract_detail,
    _get_permanent_excludes,
    _is_rule_excluded,
    _read_file_cache,
    _resolve_tenant_namespace,
    _write_file_cache,
    apply_policy_correction,
    categorize_policy,
    compute_blocks,
    compute_violation_coverage,
    count_unique_violations,
    detect_component_policy_type,
    enrich_with_coverage,
    expected_policy_prefix,
    extract_policy_from_scenario,
    extract_violation_rules,
    fetch_exceptions_by_policy,
    lookup_exceptions,
    policy_env,
    policy_url,
    strip_version_suffix,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _make_exception(value, permanent=True, effective_until=None, days_left=None):
    """Build an exception dict matching KonfluxClient.extract_exceptions output."""
    return {
        'value': value,
        'permanent': permanent,
        'effectiveUntil': effective_until,
        'days_left': days_left,
    }


def _violation_text(*rules_and_terms):
    """Build a violation_summary string from (rule, term) pairs."""
    lines = []
    for rule, term in rules_and_terms:
        lines.append(f'✕ [Violation] {rule}')
        if term:
            lines.append(f'  Term: {term}')
    return '\n'.join(lines) + '\n'


# ═══════════════════════════════════════════════════════════════════════
# 1. strip_version_suffix
# ═══════════════════════════════════════════════════════════════════════

class TestStripVersionSuffix:
    def test_empty_string(self):
        assert strip_version_suffix('') == ''

    def test_no_suffix(self):
        assert strip_version_suffix('odh-cli') == 'odh-cli'

    def test_strip_vN_M(self):
        assert strip_version_suffix('odh-trustyai-service-v3-5') == 'odh-trustyai-service'

    def test_strip_vN_M_ea_D(self):
        assert strip_version_suffix('odh-trustyai-service-v3-5-ea-2') == 'odh-trustyai-service'

    def test_strip_vN_M_rc_D(self):
        assert strip_version_suffix('rhoai-fbc-fragment-v3-5-rc-2') == 'rhoai-fbc-fragment'

    def test_preserves_inner_v(self):
        """Component names with 'v' in the middle should not be stripped."""
        assert strip_version_suffix('odh-vino-operator') == 'odh-vino-operator'


# ═══════════════════════════════════════════════════════════════════════
# 2. extract_policy_from_scenario
# ═══════════════════════════════════════════════════════════════════════

class TestExtractPolicyFromScenario:
    def test_empty(self):
        assert extract_policy_from_scenario('') == ''

    def test_no_match(self):
        assert extract_policy_from_scenario('some-random-string') == ''

    def test_versioned_scenario(self):
        assert (
            extract_policy_from_scenario(
                'conforma-registry-rhoai-prod-v3-5-ea-2-single-component'
            )
            == 'registry-rhoai-prod'
        )

    def test_unversioned_scenario(self):
        assert (
            extract_policy_from_scenario(
                'conforma-fbc-rhoai-prod-single-component'
            )
            == 'fbc-rhoai-prod'
        )

    def test_chart_scenario(self):
        assert (
            extract_policy_from_scenario(
                'conforma-registry-rhoai-chart-prod-v3-5-single-component'
            )
            == 'registry-rhoai-chart-prod'
        )

    def test_rc_suffix(self):
        assert (
            extract_policy_from_scenario(
                'conforma-registry-rhoai-prod-v3-5-rc-2-single-component'
            )
            == 'registry-rhoai-prod'
        )


# ═══════════════════════════════════════════════════════════════════════
# 3. policy_url
# ═══════════════════════════════════════════════════════════════════════

class TestPolicyUrl:
    def test_no_base_returns_none(self):
        with patch('conforma.policy_tools.GITLAB_EC_BASE', ''):
            assert policy_url('conforma-fbc-rhoai-prod-single-component') is None

    def test_empty_scenario_returns_none(self):
        with patch('conforma.policy_tools.GITLAB_EC_BASE', 'https://gitlab.example.com'):
            assert policy_url('') is None

    def test_valid_url(self):
        base = 'https://gitlab.example.com/-/tree/main/policies'
        with patch('conforma.policy_tools.GITLAB_EC_BASE', base):
            result = policy_url('conforma-fbc-rhoai-prod-single-component')
            assert result == f'{base}/fbc-rhoai-prod.yaml'


# ═══════════════════════════════════════════════════════════════════════
# 4. categorize_policy
# ═══════════════════════════════════════════════════════════════════════

class TestCategorizePolicy:
    def test_fbc(self):
        assert categorize_policy('conforma-fbc-rhoai-prod-v3-5-ea-2-single-component') == 'FBC'

    def test_components(self):
        assert categorize_policy('conforma-registry-rhoai-prod-v3-5-ea-2-single-component') == 'Components'

    def test_charts(self):
        assert categorize_policy('conforma-registry-rhoai-chart-prod-v3-4-single-component') == 'Charts'

    def test_other_for_unknown(self):
        assert categorize_policy('conforma-unknown-policy-single-component') == 'Other'

    def test_empty(self):
        assert categorize_policy('') == 'Other'


# ═══════════════════════════════════════════════════════════════════════
# 5. extract_violation_rules
# ═══════════════════════════════════════════════════════════════════════

class TestExtractViolationRules:
    def test_empty(self):
        assert extract_violation_rules('') == set()

    def test_none(self):
        assert extract_violation_rules(None) == set()

    def test_single_rule(self):
        text = '✕ [Violation] hermetic_task.hermetic\n  Reason: not hermetic\n'
        assert extract_violation_rules(text) == {'hermetic_task.hermetic'}

    def test_multiple_rules(self):
        text = (
            '✕ [Violation] hermetic_task.hermetic\n'
            '  Reason: not hermetic\n'
            '✕ [Violation] labels.required_labels\n'
            '  Reason: missing labels\n'
        )
        assert extract_violation_rules(text) == {
            'hermetic_task.hermetic', 'labels.required_labels'
        }

    def test_duplicates_collapsed(self):
        text = (
            '✕ [Violation] hermetic_task.hermetic\n'
            '✕ [Violation] hermetic_task.hermetic\n'
        )
        assert extract_violation_rules(text) == {'hermetic_task.hermetic'}


# ═══════════════════════════════════════════════════════════════════════
# 6. count_unique_violations
# ═══════════════════════════════════════════════════════════════════════

class TestCountUniqueViolations:
    def test_none(self):
        assert count_unique_violations(None) == (0, [])

    def test_empty(self):
        assert count_unique_violations('') == (0, [])

    def test_collapsed_rule(self):
        text = _violation_text(
            ('hermetic_task.hermetic', 'CVE-2026-001'),
            ('hermetic_task.hermetic', 'buildah-remote-oci-ta'),
        )
        count, rules = count_unique_violations(text)
        assert count == 1
        assert rules == [{'rule': 'hermetic_task.hermetic', 'detail': ''}]

    def test_non_collapsed_with_different_terms(self):
        text = _violation_text(
            ('rpm_packages.unique_version', 'annobin'),
            ('rpm_packages.unique_version', 'openssl'),
            ('rpm_packages.unique_version', 'annobin'),  # duplicate
        )
        count, rules = count_unique_violations(text)
        assert count == 2
        details = sorted([r['detail'] for r in rules])
        assert details == ['annobin', 'openssl']

    def test_mixed_collapsed_and_non_collapsed(self):
        text = _violation_text(
            ('hermetic_task.hermetic', 'some-term'),
            ('rpm_packages.unique_version', 'annobin'),
        )
        count, rules = count_unique_violations(text)
        assert count == 2
        rule_names = sorted([r['rule'] for r in rules])
        assert rule_names == ['hermetic_task.hermetic', 'rpm_packages.unique_version']

    def test_sbom_collapsed(self):
        text = _violation_text(
            ('sbom_spdx.disallowed_package_attributes', 'pkg:pypi/absl-py@2.4.0'),
            ('sbom_spdx.disallowed_package_attributes', 'pkg:pypi/aiohttp@3.14.0'),
        )
        count, rules = count_unique_violations(text)
        assert count == 1
        assert rules[0]['detail'] == ''


# ═══════════════════════════════════════════════════════════════════════
# 7. _extract_detail
# ═══════════════════════════════════════════════════════════════════════

class TestExtractDetail:
    def test_collapsed_rule_returns_empty(self):
        assert _extract_detail('hermetic_task.hermetic', '  Term: anything\n') == ''

    def test_non_collapsed_with_term(self):
        body = '  ImageRef: quay.io/img\n  Term: annobin\n'
        assert _extract_detail('rpm_packages.unique_version', body) == 'annobin'

    def test_pkg_prefix_strips_query(self):
        body = '  Term: pkg:pypi/requests@2.31.0?vcs_url=https://github.com/...\n'
        result = _extract_detail('some_rule.check', body)
        assert result == 'pkg:pypi/requests@2.31.0'
        assert '?' not in result

    def test_no_term_returns_empty(self):
        body = '  ImageRef: quay.io/img\n  Reason: failed\n'
        assert _extract_detail('rpm_packages.unique_version', body) == ''


# ═══════════════════════════════════════════════════════════════════════
# 8. compute_violation_coverage
# ═══════════════════════════════════════════════════════════════════════

class TestComputeViolationCoverage:
    def test_empty_rules_returns_none(self):
        assert compute_violation_coverage(set(), []) is None

    def test_fully_covered(self):
        rules = {'hermetic_task.hermetic'}
        exc = [_make_exception('hermetic_task.hermetic')]
        result = compute_violation_coverage(rules, exc)
        assert result['coverage'] == 'fully_covered'
        assert result['covered_rules'] == ['hermetic_task.hermetic']
        assert result['uncovered_rules'] == []

    def test_partially_covered(self):
        rules = {'hermetic_task.hermetic', 'labels.required_labels'}
        exc = [_make_exception('hermetic_task.hermetic')]
        result = compute_violation_coverage(rules, exc)
        assert result['coverage'] == 'partially_covered'
        assert 'hermetic_task.hermetic' in result['covered_rules']
        assert 'labels.required_labels' in result['uncovered_rules']

    def test_not_covered(self):
        rules = {'hermetic_task.hermetic'}
        exc = [_make_exception('labels.required_labels')]
        result = compute_violation_coverage(rules, exc)
        assert result['coverage'] == 'not_covered'
        assert result['covered_rules'] == []
        assert result['uncovered_rules'] == ['hermetic_task.hermetic']

    def test_expired_exception_excluded(self):
        """Exception with negative days_left and not permanent should be excluded."""
        rules = {'hermetic_task.hermetic'}
        exc = [_make_exception('hermetic_task.hermetic', permanent=False, days_left=-5)]
        result = compute_violation_coverage(rules, exc)
        assert result['coverage'] == 'not_covered'


# ═══════════════════════════════════════════════════════════════════════
# 9. _counterpart_policy
# ═══════════════════════════════════════════════════════════════════════

class TestCounterpartPolicy:
    def test_prod_to_stage(self):
        assert _counterpart_policy('registry-rhoai-prod') == 'registry-rhoai-stage'

    def test_stage_to_prod(self):
        assert _counterpart_policy('registry-rhoai-stage') == 'registry-rhoai-prod'

    def test_chart_prod_to_stage(self):
        assert _counterpart_policy('registry-rhoai-chart-prod') == 'registry-rhoai-chart-stage'

    def test_unknown_returns_empty(self):
        assert _counterpart_policy('unknown-policy') == ''


# ═══════════════════════════════════════════════════════════════════════
# 10. policy_env
# ═══════════════════════════════════════════════════════════════════════

class TestPolicyEnv:
    def test_prod(self):
        assert policy_env('registry-rhoai-prod') == 'prod'

    def test_stage(self):
        assert policy_env('fbc-rhoai-stage') == 'stage'

    def test_unknown(self):
        assert policy_env('unknown') == ''


# ═══════════════════════════════════════════════════════════════════════
# 11. compute_blocks
# ═══════════════════════════════════════════════════════════════════════

class TestComputeBlocks:
    def test_prod_scenario_no_coverage(self):
        result = compute_blocks('conforma-registry-rhoai-prod-v3-5-ea-2-single-component')
        assert result == 'prod'

    def test_stage_scenario_no_coverage(self):
        result = compute_blocks('conforma-registry-rhoai-stage-v3-5-single-component')
        assert result == 'stage'

    def test_prod_fully_covered(self):
        result = compute_blocks(
            'conforma-registry-rhoai-prod-v3-5-single-component',
            exception_coverage_prod='fully_covered',
        )
        assert result == 'none'

    def test_prod_partially_covered(self):
        result = compute_blocks(
            'conforma-registry-rhoai-prod-v3-5-single-component',
            exception_coverage_stage='not_covered',
            exception_coverage_prod='partially_covered',
        )
        assert result == 'prod'

    def test_empty_scenario(self):
        assert compute_blocks('') == ''

    def test_no_match_scenario(self):
        assert compute_blocks('no-match') == ''


# ═══════════════════════════════════════════════════════════════════════
# 12. _resolve_tenant_namespace
# ═══════════════════════════════════════════════════════════════════════

class TestResolveTenantNamespace:
    def test_already_tenant(self):
        assert _resolve_tenant_namespace('rhoai-tenant') == 'rhoai-tenant'

    @patch.dict(os.environ, {'TENANT_NAMESPACE': 'custom-tenant'}, clear=False)
    def test_uses_tenant_env(self):
        result = _resolve_tenant_namespace('some-ns')
        assert result == 'custom-tenant'

    @patch.dict(os.environ, {}, clear=True)
    def test_appends_tenant_suffix(self):
        assert _resolve_tenant_namespace('rhoai') == 'rhoai-tenant'

    @patch.dict(os.environ, {}, clear=True)
    def test_default_namespace(self):
        assert _resolve_tenant_namespace(None) == 'rhoai-tenant'


# ═══════════════════════════════════════════════════════════════════════
# 13. detect_component_policy_type
# ═══════════════════════════════════════════════════════════════════════

class TestDetectComponentPolicyType:
    def test_chart(self):
        assert detect_component_policy_type('rhai-on-openshift-chart-v3-5') == 'chart'

    def test_chart_ea(self):
        assert detect_component_policy_type('rhai-on-xks-chart-v3-5-ea-2') == 'chart'

    def test_fbc(self):
        assert detect_component_policy_type('rhoai-fbc-fragment-v3-5') == 'fbc'

    def test_registry(self):
        assert detect_component_policy_type('odh-dashboard-v3-5') == 'registry'

    def test_empty(self):
        assert detect_component_policy_type('') == 'registry'


# ═══════════════════════════════════════════════════════════════════════
# 14. expected_policy_prefix
# ═══════════════════════════════════════════════════════════════════════

class TestExpectedPolicyPrefix:
    def test_chart(self):
        assert expected_policy_prefix('rhai-on-openshift-chart-v3-5') == 'registry-rhoai-chart'

    def test_fbc(self):
        assert expected_policy_prefix('rhoai-fbc-fragment-v3-5') == 'fbc-rhoai'

    def test_registry(self):
        assert expected_policy_prefix('odh-dashboard-v3-5') == 'registry-rhoai'


# ═══════════════════════════════════════════════════════════════════════
# 15. _is_rule_excluded
# ═══════════════════════════════════════════════════════════════════════

class TestIsRuleExcluded:
    def test_exact_match(self):
        assert _is_rule_excluded('labels.required_labels', {'labels.required_labels'}) is True

    def test_prefix_match(self):
        assert _is_rule_excluded('cve.cve_results_found', {'cve'}) is True

    def test_no_match(self):
        assert _is_rule_excluded('hermetic_task.hermetic', {'cve'}) is False

    def test_prefix_requires_dot_boundary(self):
        """Prefix 'lab' should NOT match 'labels.required_labels'."""
        assert _is_rule_excluded('labels.required_labels', {'lab'}) is False


# ═══════════════════════════════════════════════════════════════════════
# 16. lookup_exceptions
# ═══════════════════════════════════════════════════════════════════════

class TestLookupExceptions:
    def test_basic_lookup(self):
        exc_by_policy = {
            'registry-rhoai-prod': [_make_exception('hermetic_task.hermetic')],
        }
        result = lookup_exceptions('registry-rhoai-prod', '', exc_by_policy)
        assert len(result) == 1
        assert result[0]['value'] == 'hermetic_task.hermetic'

    def test_includes_future_variant(self):
        exc_by_policy = {
            'registry-rhoai-prod': [_make_exception('rule_a')],
            'registry-rhoai-prod-v3-5-ea-2-future': [_make_exception('rule_b')],
        }
        scenario = 'conforma-registry-rhoai-prod-v3-5-ea-2-single-component'
        result = lookup_exceptions('registry-rhoai-prod', scenario, exc_by_policy)
        values = {e['value'] for e in result}
        assert values == {'rule_a', 'rule_b'}

    def test_no_match(self):
        result = lookup_exceptions('missing-policy', '', {})
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# 17. enrich_with_coverage
# ═══════════════════════════════════════════════════════════════════════

class TestEnrichWithCoverage:
    def test_empty_exceptions_is_noop(self):
        violations = [{'policy_name': 'x', 'violation_summary': 'y'}]
        enrich_with_coverage(violations, {})
        assert 'exception_coverage' not in violations[0]

    def test_enriches_violation(self):
        violations = [{
            'policy_name': 'registry-rhoai-prod',
            'scenario': 'conforma-registry-rhoai-prod-v3-5-single-component',
            'violation_summary': '✕ [Violation] hermetic_task.hermetic\n',
        }]
        exc_by_policy = {
            'registry-rhoai-prod': [_make_exception('hermetic_task.hermetic')],
        }
        enrich_with_coverage(violations, exc_by_policy)
        assert violations[0]['exception_coverage'] == 'fully_covered'
        assert violations[0]['covered_rules'] == ['hermetic_task.hermetic']
        assert violations[0]['uncovered_rules'] == []

    def test_skips_missing_policy_name(self):
        violations = [{'violation_summary': '✕ [Violation] rule\n'}]
        enrich_with_coverage(violations, {'some-policy': [_make_exception('rule')]})
        assert 'exception_coverage' not in violations[0]


# ═══════════════════════════════════════════════════════════════════════
# 18. apply_policy_correction
# ═══════════════════════════════════════════════════════════════════════

class TestApplyPolicyCorrection:
    def test_no_exceptions_returns_zeros(self):
        result = apply_policy_correction([], {})
        assert result == {
            'corrected': 0, 'removed': 0,
            'filtered_violations': 0, 'details': [],
        }

    def test_marks_noise_violation_as_wrong_policy(self):
        """Chart component evaluated against registry-rhoai-prod: all rules are
        permanently excluded in the correct chart policy -> marked as wrong policy,
        NOT removed (kept visible in alerts with warning indicator)."""
        violations = [{
            'component_name': 'rhai-on-openshift-chart-v3-5',
            'scenario': 'conforma-registry-rhoai-prod-v3-5-single-component',
            'violation_summary': '✕ [Violation] cve.cve_results_found\n',
            'violations_count': 3,
        }]
        exc_by_policy = {
            'registry-rhoai-chart-prod': [_make_exception('cve', permanent=True)],
        }
        result = apply_policy_correction(violations, exc_by_policy)
        assert result['removed'] == 0
        assert result['corrected'] == 1
        assert len(violations) == 1  # still in list — marked, not removed
        assert violations[0].get('is_wrong_policy') is True

    def test_partial_filter(self):
        """Some rules are noise, some are real -> partial filter."""
        violations = [{
            'component_name': 'rhai-on-openshift-chart-v3-5',
            'scenario': 'conforma-registry-rhoai-prod-v3-5-single-component',
            'violation_summary': (
                '✕ [Violation] cve.cve_results_found\n'
                '  Term: CVE-2026-001\n'
                '✕ [Violation] hermetic_task.hermetic\n'
                '  Term: some-task\n'
            ),
            'violations_count': 5,
        }]
        exc_by_policy = {
            'registry-rhoai-chart-prod': [_make_exception('cve', permanent=True)],
        }
        result = apply_policy_correction(violations, exc_by_policy)
        assert result['corrected'] == 1
        assert result['removed'] == 0
        assert len(violations) == 1  # kept but filtered
        assert violations[0].get('policy_noise_filtered', 0) > 0

    def test_matching_policy_skipped(self):
        """Component already evaluated against the correct policy -> no correction."""
        violations = [{
            'component_name': 'odh-dashboard-v3-5',
            'scenario': 'conforma-registry-rhoai-prod-v3-5-single-component',
            'violation_summary': '✕ [Violation] hermetic_task.hermetic\n',
            'violations_count': 1,
        }]
        exc_by_policy = {
            'registry-rhoai-prod': [_make_exception('cve', permanent=True)],
        }
        result = apply_policy_correction(violations, exc_by_policy)
        assert result['corrected'] == 0
        assert len(violations) == 1


# ═══════════════════════════════════════════════════════════════════════
# 19. fetch_exceptions_by_policy — caching and fallback
# ═══════════════════════════════════════════════════════════════════════

class TestFetchExceptionsByPolicy:
    """Tests the three-tier caching: memory -> file -> cluster -> gitlab."""

    def setup_method(self):
        """Reset the module-level memory cache before each test."""
        import conforma.policy_tools as mod
        mod._exceptions_cache['data'] = None
        mod._exceptions_cache['ts'] = 0

    def test_memory_cache_hit(self):
        """If memory cache is fresh, return it without touching file or cluster."""
        import conforma.policy_tools as mod
        cached_data = {'registry-rhoai-prod': [{'value': 'cached_rule'}]}
        mod._exceptions_cache['data'] = cached_data
        mod._exceptions_cache['ts'] = time.time()

        result = fetch_exceptions_by_policy()
        assert result is cached_data

    @patch('conforma.policy_tools._read_file_cache')
    def test_file_cache_hit_with_sufficient_policies(self, mock_read):
        """Stale memory cache + fresh file cache with >= 5 policies -> use file cache."""
        file_data = {
            'policy-a': [{'value': 'rule1'}],
            'policy-b': [{'value': 'rule2'}],
            'policy-c': [{'value': 'rule3'}],
            'policy-d': [{'value': 'rule4'}],
            'policy-e': [{'value': 'rule5'}],
        }
        mock_read.return_value = file_data

        result = fetch_exceptions_by_policy()
        assert result == file_data
        mock_read.assert_called_once()

    @patch('conforma.policy_tools._read_file_cache')
    @patch('conforma.policy_tools.fetch_exceptions_from_gitlab',
           return_value={'registry-rhoai-prod': [{'value': 'gitlab_rule'}]})
    def test_file_cache_sparse_falls_through_to_gitlab(self, mock_gitlab, mock_read):
        """Sparse file cache (< 5 policies) must be ignored and fall through to GitLab."""
        import conforma.policy_tools as pt
        pt._exceptions_cache['data'] = None
        pt._exceptions_cache['ts'] = 0
        mock_read.return_value = {'only-one-policy': [{'value': 'rule'}]}

        with patch('clients.konflux_client.KonfluxClient') as mock_kc:
            mock_kc.return_value.get_ec_policies.side_effect = Exception('cluster down')
            result = fetch_exceptions_by_policy()

        # Must have fallen through to GitLab since file cache had < 5 policies
        assert 'registry-rhoai-prod' in result, \
            'Sparse file cache was used instead of falling through to GitLab'
        mock_gitlab.assert_called_once()

    @patch('conforma.policy_tools._write_file_cache')
    @patch('conforma.policy_tools._read_file_cache', return_value=None)
    @patch('conforma.policy_tools._resolve_tenant_namespace', return_value='rhoai-tenant')
    def test_cluster_fetch(self, mock_ns, mock_read, mock_write):
        """No cache -> fetch from cluster via KonfluxClient."""
        import conforma.policy_tools as pt
        mock_client = MagicMock()
        # Return enough policies to exceed the caching threshold (>=5)
        mock_client.get_ec_policies.return_value = [
            {'metadata': {'name': 'registry-rhoai-prod'}, 'spec': {}},
            {'metadata': {'name': 'fbc-rhoai-prod'}, 'spec': {}},
            {'metadata': {'name': 'registry-rhoai-chart-prod'}, 'spec': {}},
            {'metadata': {'name': 'registry-rhoai-stage'}, 'spec': {}},
            {'metadata': {'name': 'fbc-rhoai-stage'}, 'spec': {}},
        ]
        mock_client.extract_exceptions.return_value = [
            {'value': 'hermetic_task.hermetic', 'permanent': True},
        ]
        # Reset memory cache so the function reaches the cluster call
        pt._exceptions_cache['data'] = None
        pt._exceptions_cache['ts'] = 0

        # KonfluxClient is imported inside fetch_exceptions_by_policy, so patch
        # the class in the source module rather than the policy_tools namespace.
        with patch('clients.konflux_client.KonfluxClient', return_value=mock_client):
            result = fetch_exceptions_by_policy(namespace='rhoai-tenant')

        assert 'registry-rhoai-prod' in result
        mock_write.assert_called_once()

    @patch('conforma.policy_tools.fetch_exceptions_from_gitlab')
    @patch('conforma.policy_tools._read_file_cache', return_value=None)
    def test_gitlab_fallback(self, mock_read, mock_gitlab):
        """Cluster fetch fails -> fall back to GitLab."""
        gitlab_data = {'fbc-rhoai-prod': [{'value': 'gitlab_rule'}]}
        mock_gitlab.return_value = gitlab_data

        with patch('clients.konflux_client.KonfluxClient', side_effect=Exception('no cluster')):
            result = fetch_exceptions_by_policy()

        assert result == gitlab_data

    @patch('conforma.policy_tools.fetch_exceptions_from_gitlab', side_effect=Exception('fail'))
    @patch('conforma.policy_tools._read_file_cache', return_value=None)
    def test_all_fail_returns_empty(self, mock_read, mock_gitlab):
        """All sources fail -> return empty dict, never raise."""
        with patch('clients.konflux_client.KonfluxClient', side_effect=Exception('nope')):
            result = fetch_exceptions_by_policy()
        assert result == {}


# ═══════════════════════════════════════════════════════════════════════
# 20. _read_file_cache / _write_file_cache
# ═══════════════════════════════════════════════════════════════════════

class TestFileCache:
    @patch('os.path.exists', return_value=False)
    def test_read_no_file(self, mock_exists):
        assert _read_file_cache() is None

    @patch('os.path.getmtime', return_value=0)
    @patch('os.path.exists', return_value=True)
    def test_read_stale_cache(self, mock_exists, mock_mtime):
        """File exists but is older than _CACHE_TTL -> return None."""
        assert _read_file_cache() is None

    @patch('builtins.open', mock_open(read_data='{"key": "value"}'))
    @patch('os.path.getmtime')
    @patch('os.path.exists', return_value=True)
    def test_read_fresh_cache(self, mock_exists, mock_mtime):
        mock_mtime.return_value = time.time()  # fresh
        result = _read_file_cache()
        assert result == {'key': 'value'}

    @patch('builtins.open', side_effect=OSError('disk error'))
    @patch('os.path.getmtime', return_value=time.time())
    @patch('os.path.exists', return_value=True)
    def test_read_handles_error(self, mock_exists, mock_mtime, mock_file):
        assert _read_file_cache() is None

    @patch('os.makedirs')
    def test_write_creates_dirs(self, mock_makedirs):
        with patch('builtins.open', mock_open()):
            _write_file_cache({'a': 1})
        mock_makedirs.assert_called_once()

    @patch('os.makedirs', side_effect=OSError('permission denied'))
    def test_write_handles_error(self, mock_makedirs):
        """Write errors are silently swallowed (never raises)."""
        _write_file_cache({'a': 1})  # should not raise


# ═══════════════════════════════════════════════════════════════════════
# Additional coverage: _get_permanent_excludes
# ═══════════════════════════════════════════════════════════════════════

class TestGetPermanentExcludes:
    def test_finds_prod_excludes(self):
        exc_by_policy = {
            'registry-rhoai-chart-prod': [
                _make_exception('cve', permanent=True),
                _make_exception('sbom', permanent=False),
            ],
        }
        result = _get_permanent_excludes('registry-rhoai-chart', exc_by_policy)
        assert result == {'cve'}

    def test_no_matching_policy(self):
        result = _get_permanent_excludes('nonexistent', {})
        assert result == set()
