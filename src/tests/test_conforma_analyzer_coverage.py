"""Comprehensive tests for ConformaAnalyzer.

Covers constructor, prompt building, context enrichment, response parsing,
annotation, single-violation analysis, and batch run orchestration.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Mock load_prompt BEFORE importing the module under test so the module-level
# CONFORMA_SYSTEM_PROMPT = load_prompt('conforma_analyzer') doesn't hit disk.
# ---------------------------------------------------------------------------
_FAKE_SYSTEM_PROMPT = "You are a Conforma policy analysis assistant."

with patch('prompt_loader.load_prompt', return_value=_FAKE_SYSTEM_PROMPT):
    from analyzers.conforma_analyzer import CONFORMA_SYSTEM_PROMPT, ConformaAnalyzer


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_config(llm=True):
    config = MagicMock()
    config.db = MagicMock()
    config.k8s.application_name = 'rhoai-v3-5-ea-2'
    config.k8s.namespace = 'rhoai-tenant'
    config.llm = MagicMock() if llm else None
    return config


def _make_violation(**overrides):
    violation = {
        'id': 1,
        'component_name': 'odh-dashboard',
        'repository': 'https://github.com/opendatahub-io/odh-dashboard',
        'snapshot_name': 'rhoai-v3-5-ea-2-snapshot-abc',
        'pipelinerun_name': 'pr-123',
        'scenario': 'rhoai-v3-5-ea-2-conforma-prod',
        'violations_count': 3,
        'warnings_count': 1,
        'successes_count': 50,
        'violation_summary': 'rule: hermetic_build_task check failed',
        'commit_sha': 'abc123',
        'commit_author': 'dev@example.com',
        'commit_message': 'fix stuff',
        'application': 'rhoai-v3-5-ea-2',
        'branch': 'rhoai-2.19',
        'pattern_typical_fix': None,
        'pattern_doc_context': None,
        'pattern_name': None,
        'has_exception': False,
    }
    violation.update(overrides)
    return violation


def _make_llm_response(tool_input=None):
    if tool_input is None:
        tool_input = {
            'root_cause': 'The hermetic build task parameter is not set in the pipeline',
            'failure_category': 'policy_hermetic_build',
            'confidence_score': 0.85,
            'recommended_fix': 'Add hermetic: true to the pipeline YAML params section',
            'can_auto_fix': False,
            'differential_diagnosis': [
                {
                    'hypothesis': 'Hermetic build parameter missing from pipeline config',
                    'category': 'policy_hermetic_build',
                    'confidence': 0.85,
                    'supporting_evidence': ['hermetic_task rule failed'],
                    'contradicting_evidence': [],
                },
                {
                    'hypothesis': 'Pipeline task bundle is outdated',
                    'category': 'policy_untrusted_image',
                    'confidence': 0.15,
                    'supporting_evidence': [],
                    'contradicting_evidence': ['Only hermetic_task rule failed'],
                },
            ],
        }
    resp = MagicMock()
    resp.tool_calls = [{'name': 'record_conforma_analysis', 'input': tool_input}]
    resp.input_tokens = 1000
    resp.output_tokens = 500
    return resp


def _make_analyzer(config=None, llm=None, ai_repo=None, langfuse=None, db=None):
    """Create a ConformaAnalyzer with fully mocked deps."""
    config = config or _make_config()
    llm = llm or MagicMock()
    ai_repo = ai_repo or MagicMock()
    langfuse = langfuse or MagicMock()
    db = db or MagicMock()
    with patch('analyzers.conforma_analyzer.ErrorPatternRepository'):
        with patch('analyzers.conforma_analyzer.ConformaRuleCatalogRepository'):
            return ConformaAnalyzer(
                config, db=db, ai_repo=ai_repo, llm=llm, langfuse=langfuse
            )


# ── 1. Constructor tests ─────────────────────────────────────────────────


class TestConstructor:
    def test_all_deps_injected(self):
        analyzer = _make_analyzer()
        assert analyzer.llm is not None
        assert analyzer.ai_repo is not None
        assert analyzer.langfuse is not None
        assert analyzer._konflux_client is None

    def test_db_none_creates_database_connection(self):
        config = _make_config()
        llm = MagicMock()
        with patch('analyzers.conforma_analyzer.DatabaseConnection') as mock_db_cls, \
             patch('analyzers.conforma_analyzer.ErrorPatternRepository'), \
             patch('analyzers.conforma_analyzer.ConformaRuleCatalogRepository'):
            ConformaAnalyzer(config, db=None, llm=llm, langfuse=MagicMock())
            mock_db_cls.assert_called_once_with(config.db)

    def test_llm_none_and_config_llm_none_raises(self):
        config = _make_config(llm=False)
        with patch('analyzers.conforma_analyzer.ErrorPatternRepository'), \
             patch('analyzers.conforma_analyzer.ConformaRuleCatalogRepository'), \
             patch('analyzers.conforma_analyzer.DatabaseConnection'):
            with pytest.raises(ValueError, match="LLM not configured"):
                ConformaAnalyzer(config, llm=None, langfuse=MagicMock())

    def test_llm_none_creates_from_config(self):
        config = _make_config()
        with patch('analyzers.conforma_analyzer.create_llm_provider', return_value=MagicMock()) as mock_create, \
             patch('analyzers.conforma_analyzer.ErrorPatternRepository'), \
             patch('analyzers.conforma_analyzer.ConformaRuleCatalogRepository'), \
             patch('analyzers.conforma_analyzer.DatabaseConnection'):
            analyzer = ConformaAnalyzer(config, llm=None, langfuse=MagicMock())
            mock_create.assert_called_once_with(config.llm)
            assert analyzer.llm is not None


# ── 2. get_pending_violations ─────────────────────────────────────────────


class TestGetPendingViolations:
    def test_returns_violations_from_ai_repo(self):
        ai_repo = MagicMock()
        ai_repo.get_pending_conforma_violations.return_value = [_make_violation()]
        analyzer = _make_analyzer(ai_repo=ai_repo)

        result = analyzer.get_pending_violations(limit=10)

        ai_repo.get_pending_conforma_violations.assert_called_once_with(
            'rhoai-v3-5-ea-2', limit=10, component_filter=None, force=False
        )
        assert len(result) == 1

    def test_with_component_filter(self):
        ai_repo = MagicMock()
        ai_repo.get_pending_conforma_violations.return_value = []
        analyzer = _make_analyzer(ai_repo=ai_repo)

        analyzer.get_pending_violations(component_filter='odh-dashboard')

        ai_repo.get_pending_conforma_violations.assert_called_once_with(
            'rhoai-v3-5-ea-2', limit=5, component_filter='odh-dashboard', force=False
        )

    def test_with_application_override(self):
        ai_repo = MagicMock()
        ai_repo.get_pending_conforma_violations.return_value = []
        analyzer = _make_analyzer(ai_repo=ai_repo)

        analyzer.get_pending_violations(application='rhoai-v3-5-rc-1')

        ai_repo.get_pending_conforma_violations.assert_called_once_with(
            'rhoai-v3-5-rc-1', limit=5, component_filter=None, force=False
        )


# ── 3. build_analysis_prompt ──────────────────────────────────────────────


class TestBuildAnalysisPrompt:
    def test_returns_tuple(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        with patch.object(analyzer, '_get_doc_refs', return_value=''), \
             patch.object(analyzer, '_get_release_context', return_value=''), \
             patch.object(analyzer, '_get_policy_exclusions', return_value=''), \
             patch.object(analyzer, '_get_sarif_context', return_value=''), \
             patch.object(analyzer, '_get_rule_catalog_context', return_value=''), \
             patch.object(analyzer, '_get_tekton_files', return_value=''), \
             patch('analyzers.conforma_analyzer.conforma_context', create=True, side_effect=ImportError):
            system, user = analyzer.build_analysis_prompt(violation)
            assert system == CONFORMA_SYSTEM_PROMPT
            assert isinstance(user, str)

    def test_with_commit_info(self):
        analyzer = _make_analyzer()
        violation = _make_violation(
            commit_sha='deadbeef', commit_author='alice@rh.com',
            commit_message='bump version'
        )
        with patch.object(analyzer, '_get_doc_refs', return_value=''), \
             patch.object(analyzer, '_get_release_context', return_value=''), \
             patch.object(analyzer, '_get_policy_exclusions', return_value=''), \
             patch.object(analyzer, '_get_sarif_context', return_value=''), \
             patch.object(analyzer, '_get_rule_catalog_context', return_value=''), \
             patch.object(analyzer, '_get_tekton_files', return_value=''), \
             patch('analyzers.conforma_analyzer.conforma_context', create=True, side_effect=ImportError):
            _, user = analyzer.build_analysis_prompt(violation)
            assert 'deadbeef' in user
            assert 'alice@rh.com' in user
            assert 'bump version' in user

    def test_without_commit_info(self):
        analyzer = _make_analyzer()
        violation = _make_violation(
            commit_sha=None, commit_author=None, commit_message=None
        )
        with patch.object(analyzer, '_get_doc_refs', return_value=''), \
             patch.object(analyzer, '_get_release_context', return_value=''), \
             patch.object(analyzer, '_get_policy_exclusions', return_value=''), \
             patch.object(analyzer, '_get_sarif_context', return_value=''), \
             patch.object(analyzer, '_get_rule_catalog_context', return_value=''), \
             patch.object(analyzer, '_get_tekton_files', return_value=''), \
             patch('analyzers.conforma_analyzer.conforma_context', create=True, side_effect=ImportError):
            _, user = analyzer.build_analysis_prompt(violation)
            assert '(not available)' in user

    def test_long_summary_truncated(self):
        analyzer = _make_analyzer()
        long_summary = 'x' * 90000
        violation = _make_violation(violation_summary=long_summary)
        with patch.object(analyzer, '_get_doc_refs', return_value=''), \
             patch.object(analyzer, '_get_release_context', return_value=''), \
             patch.object(analyzer, '_get_policy_exclusions', return_value=''), \
             patch.object(analyzer, '_get_sarif_context', return_value=''), \
             patch.object(analyzer, '_get_rule_catalog_context', return_value=''), \
             patch.object(analyzer, '_get_tekton_files', return_value=''), \
             patch('analyzers.conforma_analyzer.conforma_context', create=True, side_effect=ImportError):
            _, user = analyzer.build_analysis_prompt(violation)
            assert '(truncated - summary too long)' in user

    def test_minimal_violation(self):
        analyzer = _make_analyzer()
        violation = _make_violation(
            commit_sha=None, commit_author=None, commit_message=None,
            violation_summary='', repository='', scenario='',
        )
        with patch.object(analyzer, '_get_doc_refs', return_value=''), \
             patch.object(analyzer, '_get_release_context', return_value=''), \
             patch.object(analyzer, '_get_policy_exclusions', return_value=''), \
             patch.object(analyzer, '_get_sarif_context', return_value=''), \
             patch.object(analyzer, '_get_rule_catalog_context', return_value=''), \
             patch.object(analyzer, '_get_tekton_files', return_value=''), \
             patch('analyzers.conforma_analyzer.conforma_context', create=True, side_effect=ImportError):
            system, user = analyzer.build_analysis_prompt(violation)
            assert 'Component: odh-dashboard' in user
            assert system == CONFORMA_SYSTEM_PROMPT


# ── 4. _get_release_context ──────────────────────────────────────────────


class TestGetReleaseContext:
    def test_no_application_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation(application='')
        result = analyzer._get_release_context(violation)
        assert result == ''

    def test_with_release_context_data(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        mock_countdown = MagicMock()
        mock_countdown.phase = 'code-freeze'
        mock_countdown.urgency = 'high'
        mock_countdown.message = '3 days until GA'
        with patch('analyzers.conforma_analyzer.get_schedule', create=True) as mock_sched, \
             patch('analyzers.conforma_analyzer._compute_freeze_countdown',
                   create=True, return_value=mock_countdown) as mock_freeze:
            # Patch the imports inside the method
            with patch.dict('sys.modules', {
                'api.routes.failures': MagicMock(
                    _compute_freeze_countdown=MagicMock(return_value=mock_countdown)
                ),
                'api.routes.releases': MagicMock(
                    get_schedule=MagicMock(return_value={})
                ),
            }):
                result = analyzer._get_release_context(violation)
                assert 'Release Context' in result
                assert 'code-freeze' in result
                assert 'high' in result

    def test_import_failure_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        with patch.dict('sys.modules', {
            'api.routes.failures': None,
            'api.routes.releases': None,
        }):
            result = analyzer._get_release_context(violation)
            assert result == ''


# ── 5. _get_doc_refs ─────────────────────────────────────────────────────


class TestGetDocRefs:
    def test_with_scenario_and_summary(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        with patch('analyzers.conforma_analyzer.extract_policy_from_scenario',
                   create=True, return_value='prod-policy'), \
             patch('analyzers.conforma_analyzer.extract_violation_rules',
                   create=True, return_value=['hermetic_build_task']), \
             patch('analyzers.conforma_analyzer.policy_url_with_line',
                   create=True, return_value='https://example.com/policy#L10'):
            with patch.dict('sys.modules', {
                'conforma.policy_tools': MagicMock(
                    extract_policy_from_scenario=MagicMock(return_value='prod-policy'),
                    extract_violation_rules=MagicMock(return_value=['hermetic_build_task']),
                    policy_url_with_line=MagicMock(return_value='https://example.com/policy#L10'),
                ),
            }):
                result = analyzer._get_doc_refs(violation)
                assert 'EC policy file' in result
                assert 'Component repo' in result

    def test_with_repository(self):
        analyzer = _make_analyzer()
        violation = _make_violation(scenario='', violation_summary='')
        with patch.dict('sys.modules', {
            'conforma.policy_tools': MagicMock(
                extract_policy_from_scenario=MagicMock(return_value=None),
                extract_violation_rules=MagicMock(return_value=[]),
                policy_url_with_line=MagicMock(return_value=None),
            ),
        }):
            result = analyzer._get_doc_refs(violation)
            assert 'Component repo' in result

    def test_empty_scenario_and_no_repo(self):
        analyzer = _make_analyzer()
        violation = _make_violation(scenario='', repository='', violation_summary='')
        with patch.dict('sys.modules', {
            'conforma.policy_tools': MagicMock(
                extract_policy_from_scenario=MagicMock(return_value=None),
                extract_violation_rules=MagicMock(return_value=[]),
                policy_url_with_line=MagicMock(return_value=None),
            ),
        }):
            result = analyzer._get_doc_refs(violation)
            assert result == ''


# ── 6. _get_tekton_files ─────────────────────────────────────────────────


class TestGetTektonFiles:
    def test_no_repo_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation(repository='')
        result = analyzer._get_tekton_files(violation)
        assert result == ''

    def test_no_component_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation(component_name='')
        result = analyzer._get_tekton_files(violation)
        assert result == ''

    def test_github_listing_found(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = [
            'odh-dashboard-push.yaml', 'odh-dashboard-pull-request.yaml'
        ]
        mock_gh.get_file_content.return_value = None
        with patch.dict('sys.modules', {
            'clients.github_client': MagicMock(
                parse_github_repo=MagicMock(return_value=('opendatahub-io', 'odh-dashboard')),
                GitHubClient=MagicMock(return_value=mock_gh),
            ),
        }):
            result = analyzer._get_tekton_files(violation)
            assert 'Tekton Pipeline Files' in result
            assert 'odh-dashboard-push.yaml' in result

    def test_github_listing_not_found_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = None
        with patch.dict('sys.modules', {
            'clients.github_client': MagicMock(
                parse_github_repo=MagicMock(return_value=('opendatahub-io', 'odh-dashboard')),
                GitHubClient=MagicMock(return_value=mock_gh),
            ),
        }):
            result = analyzer._get_tekton_files(violation)
            assert result == ''

    def test_exception_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        with patch.dict('sys.modules', {
            'clients.github_client': MagicMock(
                parse_github_repo=MagicMock(side_effect=RuntimeError("GitHub down")),
            ),
        }):
            result = analyzer._get_tekton_files(violation)
            assert result == ''


# ── 7. _match_tekton_files ───────────────────────────────────────────────


class TestMatchTektonFiles:
    def test_exact_match(self):
        listing = ['odh-dashboard-push.yaml', 'odh-dashboard-pull-request.yaml', 'other-push.yaml']
        result = ConformaAnalyzer._match_tekton_files('odh-dashboard', listing)
        assert 'odh-dashboard-push.yaml' in result
        assert 'odh-dashboard-pull-request.yaml' in result
        assert 'other-push.yaml' not in result

    def test_fuzzy_match_via_keywords(self):
        listing = ['dashboard-component-push.yaml', 'notebook-push.yaml']
        result = ConformaAnalyzer._match_tekton_files('odh-dashboard-v3-ea-2', listing)
        assert 'dashboard-component-push.yaml' in result

    def test_no_match_returns_empty(self):
        listing = ['notebook-push.yaml', 'model-registry-push.yaml']
        result = ConformaAnalyzer._match_tekton_files('odh-dashboard', listing)
        assert result == []

    def test_noise_words_filtered(self):
        listing = ['dashboard-component-push.yaml']
        result = ConformaAnalyzer._match_tekton_files('odh-workbench-cpu-ubi9-v3-ea1', listing)
        # 'odh', 'workbench', 'cpu', 'ubi9', 'v3', 'ea1' are all noise
        # No keywords left -> empty
        assert result == []


# ── 8. _format_pattern_section ───────────────────────────────────────────


class TestFormatPatternSection:
    def test_no_pattern_data_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation(
            pattern_typical_fix=None, pattern_doc_context=None
        )
        result = analyzer._format_pattern_section(violation)
        assert result == ''

    def test_with_fix_and_doc(self):
        analyzer = _make_analyzer()
        violation = _make_violation(
            pattern_typical_fix='Set hermetic: true',
            pattern_doc_context='See Konflux docs on hermetic builds',
            pattern_name='hermetic_build_missing',
        )
        result = analyzer._format_pattern_section(violation)
        assert 'Known Pattern: hermetic_build_missing' in result
        assert 'Previous Solution' in result
        assert 'Set hermetic: true' in result
        assert 'Relevant Documentation' in result

    def test_with_fix_only(self):
        analyzer = _make_analyzer()
        violation = _make_violation(
            pattern_typical_fix='Rebuild the pipeline',
            pattern_doc_context=None,
            pattern_name='rebuild_needed',
        )
        result = analyzer._format_pattern_section(violation)
        assert 'Previous Solution' in result
        assert 'Relevant Documentation' not in result


# ── 9. _get_rule_catalog_context ─────────────────────────────────────────


class TestGetRuleCatalogContext:
    def test_no_rules_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation(violation_summary='')
        with patch.dict('sys.modules', {
            'conforma.policy_tools': MagicMock(
                extract_violation_rules=MagicMock(return_value=[]),
            ),
        }):
            result = analyzer._get_rule_catalog_context(violation)
            assert result == ''

    def test_matching_rules_with_reporter_solution(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        catalog_entry = {
            'rule_name': 'Hermetic Build Task',
            'rule_id': 'hermetic__build__task',
            'rule_package': 'release_policy',
            'policy_type': 'prod',
            'description': 'Checks that build tasks are hermetic',
            'reporter_solution': 'Set hermetic=true in pipeline YAML',
            'typical_fix': None,
            'doc_url': 'https://konflux-ci.dev/docs/building/hermetic-builds/',
        }
        analyzer.catalog_repo.get_by_rule_ids.return_value = [catalog_entry]
        with patch.dict('sys.modules', {
            'conforma.policy_tools': MagicMock(
                extract_violation_rules=MagicMock(return_value=['hermetic.build.task']),
            ),
        }):
            result = analyzer._get_rule_catalog_context(violation)
            assert 'Conforma Rule Catalog' in result
            assert 'High-confidence evidence' in result
            assert 'RHOAI-specific fix (VERIFIED)' in result
            assert 'Set hermetic=true' in result

    def test_exception_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        with patch.dict('sys.modules', {
            'conforma.policy_tools': MagicMock(
                extract_violation_rules=MagicMock(side_effect=RuntimeError("DB down")),
            ),
        }):
            result = analyzer._get_rule_catalog_context(violation)
            assert result == ''


# ── 10. _get_policy_exclusions ───────────────────────────────────────────


class TestGetPolicyExclusions:
    def test_no_scenario_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation(scenario='')
        result = analyzer._get_policy_exclusions(violation)
        assert result == ''

    def test_with_permanent_and_volatile_exclusions(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        mock_kc = MagicMock()
        meta = {'name': 'rhoai-v3-5-ea-2-conforma-prod', 'policy_ref': 'prod-policy'}
        mock_kc.get_integration_test_scenarios.return_value = [MagicMock()]
        mock_kc.extract_its_metadata.return_value = meta
        mock_kc.get_ec_policy.return_value = MagicMock()
        mock_kc.extract_exceptions.return_value = [
            {'permanent': True, 'value': 'deprecated_image_check'},
            {
                'permanent': False, 'value': 'sbom_spdx',
                'effectiveUntil': '2026-08-01', 'days_left': 30,
                'reference': 'RHOAIENG-1234',
            },
        ]
        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(
                KonfluxClient=MagicMock(return_value=mock_kc),
            ),
        }):
            analyzer._konflux_client = mock_kc
            result = analyzer._get_policy_exclusions(violation)
            assert 'Active Policy Exclusions' in result
            assert 'PERMANENT' in result
            assert 'VOLATILE' in result
            assert 'RHOAIENG-1234' in result

    def test_exception_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation()
        mock_kc = MagicMock()
        mock_kc.get_integration_test_scenarios.side_effect = RuntimeError("k8s down")
        analyzer._konflux_client = mock_kc
        result = analyzer._get_policy_exclusions(violation)
        assert result == ''


# ── 11. _get_sarif_context ───────────────────────────────────────────────


class TestGetSarifContext:
    def test_not_scan_related_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation(
            violation_summary='rule: hermetic_build_task check failed'
        )
        result = analyzer._get_sarif_context(violation)
        assert result == ''

    def test_scan_related_with_results(self):
        analyzer = _make_analyzer()
        violation = _make_violation(
            violation_summary='CVE-2024-1234 vulnerability found in clair scan'
        )
        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'container_image': 'quay.io/rhoai/dashboard@sha256:abcdef123456'
        }
        mock_rc = MagicMock()
        mock_rc.fetch_sarif_results.return_value = {'runs': []}
        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(
                KubernetesClient=MagicMock(return_value=mock_kc),
            ),
            'clients.registry_client': MagicMock(
                RegistryClient=MagicMock(
                    parse_image_ref=MagicMock(
                        return_value=('quay.io', 'rhoai/dashboard', 'sha256:abcdef123456')
                    ),
                    format_sarif_summary=MagicMock(return_value='## SARIF Summary\n2 CVEs found'),
                    return_value=mock_rc,
                ),
            ),
        }):
            result = analyzer._get_sarif_context(violation)
            assert 'SARIF Summary' in result

    def test_exception_returns_empty(self):
        analyzer = _make_analyzer()
        violation = _make_violation(
            violation_summary='CVE vulnerability scan failure'
        )
        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(
                KubernetesClient=MagicMock(side_effect=RuntimeError("k8s down")),
            ),
        }):
            result = analyzer._get_sarif_context(violation)
            assert result == ''


# ── 12. parse_analysis_response ──────────────────────────────────────────


class TestParseAnalysisResponse:
    def test_valid_response(self):
        analyzer = _make_analyzer()
        resp = _make_llm_response()
        result = analyzer.parse_analysis_response(resp)
        assert result['failure_category'] == 'policy_hermetic_build'
        assert result['confidence_score'] == 0.85
        assert result['root_cause'] == 'The hermetic build task parameter is not set in the pipeline'

    def test_empty_tool_calls_raises(self):
        analyzer = _make_analyzer()
        resp = MagicMock()
        resp.tool_calls = []
        with pytest.raises(ValueError, match="LLM did not return tool_use response"):
            analyzer.parse_analysis_response(resp)

    def test_none_tool_calls_raises(self):
        analyzer = _make_analyzer()
        resp = MagicMock()
        resp.tool_calls = None
        with pytest.raises(ValueError, match="LLM did not return tool_use response"):
            analyzer.parse_analysis_response(resp)

    def test_wrong_tool_name_raises(self):
        analyzer = _make_analyzer()
        resp = MagicMock()
        resp.tool_calls = [{'name': 'wrong_tool', 'input': {}}]
        with pytest.raises(ValueError, match="LLM did not call record_conforma_analysis"):
            analyzer.parse_analysis_response(resp)

    def test_invalid_data_falls_back_to_permissive_dict(self):
        analyzer = _make_analyzer()
        resp = _make_llm_response(tool_input={
            'root_cause': 'short',  # Too short for Pydantic min_length=10
            'failure_category': 'INVALID_CATEGORY',
            'confidence_score': 0.5,
            'recommended_fix': 'short',
            'can_auto_fix': False,
        })
        result = analyzer.parse_analysis_response(resp)
        # Falls back to permissive dict
        assert result['failure_category'] == 'infrastructure'
        assert result['confidence_score'] == 0.0
        assert result['requires_human_review'] is True

    def test_multiple_tool_calls_picks_correct(self):
        analyzer = _make_analyzer()
        resp = MagicMock()
        resp.tool_calls = [
            {'name': 'wrong_tool', 'input': {}},
            {'name': 'record_conforma_analysis', 'input': {
                'root_cause': 'The hermetic build task parameter is not set in the pipeline',
                'failure_category': 'policy_hermetic_build',
                'confidence_score': 0.9,
                'recommended_fix': 'Add hermetic: true to the pipeline YAML params section',
                'can_auto_fix': False,
            }},
        ]
        result = analyzer.parse_analysis_response(resp)
        assert result['failure_category'] == 'policy_hermetic_build'
        assert result['confidence_score'] == 0.9


# ── 13. _annotate_fix_status ─────────────────────────────────────────────


class TestAnnotateFixStatus:
    def test_with_has_exception(self):
        analyzer = _make_analyzer()
        analysis = {
            'failure_category': 'policy_hermetic_build',
            'recommended_fix': 'Set hermetic: true',
        }
        violation = _make_violation(has_exception=True)
        result = analyzer._annotate_fix_status(analysis, violation)
        assert 'policy exception already covers' in result['recommended_fix']

    def test_with_version_mismatch_category(self):
        analyzer = _make_analyzer()
        analysis = {
            'failure_category': 'policy_version_mismatch',
            'recommended_fix': 'Update the label',
        }
        violation = _make_violation()
        result = analyzer._annotate_fix_status(analysis, violation)
        assert 'typically resolved by a rebuild' in result['recommended_fix']

    def test_no_annotations(self):
        analyzer = _make_analyzer()
        original_fix = 'Set hermetic: true'
        analysis = {
            'failure_category': 'policy_hermetic_build',
            'recommended_fix': original_fix,
        }
        violation = _make_violation(has_exception=False)
        result = analyzer._annotate_fix_status(analysis, violation)
        assert result['recommended_fix'] == original_fix


# ── 14. analyze_violation ────────────────────────────────────────────────


class TestAnalyzeViolation:
    def test_happy_path(self):
        llm = MagicMock()
        llm.create_message.return_value = _make_llm_response()
        llm.model_name.return_value = 'claude-sonnet-4-20250514'
        ai_repo = MagicMock()
        ai_repo.insert_analysis.return_value = 42
        langfuse = MagicMock()
        langfuse.create_trace.return_value = MagicMock(id='trace-123')
        analyzer = _make_analyzer(llm=llm, ai_repo=ai_repo, langfuse=langfuse)
        analyzer.pattern_repo = MagicMock()
        analyzer.pattern_repo.find_or_create.return_value = {'id': 10}

        violation = _make_violation()
        with patch.object(analyzer, 'build_analysis_prompt',
                          return_value=(_FAKE_SYSTEM_PROMPT, 'user prompt')):
            result = analyzer.analyze_violation(violation)

        assert result['failure_category'] == 'policy_hermetic_build'
        llm.create_message.assert_called_once()
        ai_repo.insert_analysis.assert_called_once()
        langfuse.create_trace.assert_called_once()
        langfuse.record_generation.assert_called_once()
        langfuse.end_trace.assert_called_once()
        analyzer.pattern_repo.find_or_create.assert_called_once_with(
            'conforma', 'policy_hermetic_build'
        )
        analyzer.pattern_repo.record_occurrence.assert_called_once_with(10, 0.85)
        analyzer.pattern_repo.link_analysis.assert_called_once_with(42, 10)

    def test_llm_error_propagates(self):
        llm = MagicMock()
        llm.create_message.side_effect = RuntimeError("LLM API error")
        langfuse = MagicMock()
        langfuse.create_trace.return_value = MagicMock()
        analyzer = _make_analyzer(llm=llm, langfuse=langfuse)

        violation = _make_violation()
        with patch.object(analyzer, 'build_analysis_prompt',
                          return_value=(_FAKE_SYSTEM_PROMPT, 'user prompt')):
            with pytest.raises(RuntimeError, match="LLM API error"):
                analyzer.analyze_violation(violation)


# ── 15. run ──────────────────────────────────────────────────────────────


class TestRun:
    def test_no_pending_violations(self):
        ai_repo = MagicMock()
        ai_repo.get_pending_conforma_violations.return_value = []
        langfuse = MagicMock()
        analyzer = _make_analyzer(ai_repo=ai_repo, langfuse=langfuse)

        result = analyzer.run()

        assert result['analyzed'] == 0
        assert result['duration'] == 0

    def test_successful_batch_analysis(self):
        violations = [_make_violation(id=1), _make_violation(id=2)]
        ai_repo = MagicMock()
        ai_repo.get_pending_conforma_violations.return_value = violations
        ai_repo.increment_attempts.return_value = 1
        langfuse = MagicMock()
        analyzer = _make_analyzer(ai_repo=ai_repo, langfuse=langfuse)

        mock_analysis = {
            'failure_category': 'policy_hermetic_build',
            'confidence_score': 0.85,
            'can_auto_fix': False,
            'root_cause': 'Missing hermetic param in the pipeline configuration YAML',
        }
        with patch.object(analyzer, 'analyze_violation', return_value=mock_analysis):
            result = analyzer.run(limit=2)

        assert result['analyzed'] == 2
        assert result['duration'] > 0
        langfuse.flush.assert_called_once()

    def test_error_handling_with_retry(self):
        violations = [_make_violation(id=1)]
        ai_repo = MagicMock()
        ai_repo.get_pending_conforma_violations.return_value = violations
        ai_repo.increment_attempts.return_value = 1  # First attempt
        langfuse = MagicMock()
        analyzer = _make_analyzer(ai_repo=ai_repo, langfuse=langfuse)

        with patch.object(analyzer, 'analyze_violation',
                          side_effect=RuntimeError("temporary error")):
            result = analyzer.run()

        assert result['analyzed'] == 0
        ai_repo.mark_skipped.assert_not_called()  # attempts < MAX_RETRIES

    def test_max_retries_reached_marks_skipped(self):
        violations = [_make_violation(id=1, component_name='broken-component')]
        ai_repo = MagicMock()
        ai_repo.get_pending_conforma_violations.return_value = violations
        ai_repo.increment_attempts.return_value = 3  # == MAX_RETRIES
        langfuse = MagicMock()
        analyzer = _make_analyzer(ai_repo=ai_repo, langfuse=langfuse)

        with patch.object(analyzer, 'analyze_violation',
                          side_effect=RuntimeError("persistent error")):
            result = analyzer.run()

        assert result['analyzed'] == 0
        ai_repo.mark_skipped.assert_called_once_with(
            'max_retries', conforma_result_id=1
        )

    def test_with_component_filter(self):
        violations = [_make_violation()]
        ai_repo = MagicMock()
        ai_repo.get_pending_conforma_violations.return_value = violations
        ai_repo.increment_attempts.return_value = 1
        langfuse = MagicMock()
        analyzer = _make_analyzer(ai_repo=ai_repo, langfuse=langfuse)

        mock_analysis = {
            'failure_category': 'policy_hermetic_build',
            'confidence_score': 0.85,
            'can_auto_fix': False,
            'root_cause': 'Missing hermetic param in the pipeline configuration YAML',
        }
        with patch.object(analyzer, 'analyze_violation', return_value=mock_analysis):
            result = analyzer.run(component_filter='odh-dashboard')

        ai_repo.get_pending_conforma_violations.assert_called_once_with(
            'rhoai-v3-5-ea-2', limit=5, component_filter='odh-dashboard', force=False
        )
        assert result['analyzed'] == 1


# ── 16. _extract_prefetch_context ────────────────────────────────────────


class TestExtractPrefetchContext:
    def test_no_content_returns_empty(self):
        analyzer = _make_analyzer()
        gh = MagicMock()
        gh.get_file_content.return_value = None
        result = analyzer._extract_prefetch_context(
            gh, 'owner', 'repo', 'push.yaml', 'main'
        )
        assert result == []

    def test_with_prefetch_input(self):
        analyzer = _make_analyzer()
        gh = MagicMock()
        content = """
spec:
  params:
    - name: prefetch-input
      value: '{"type": "pip", "path": ".", "binary": "allow"}'
    - name: other
status:
  conditions: []
"""
        gh.get_file_content.return_value = content
        result = analyzer._extract_prefetch_context(
            gh, 'owner', 'repo', 'push.yaml', 'main'
        )
        assert len(result) > 0
        assert any('Prefetch Configuration' in line for line in result)

    def test_with_binary_flag(self):
        analyzer = _make_analyzer()
        gh = MagicMock()
        content = """
spec:
  params:
    - name: prefetch-input
      value: '[{"type": "pip", "path": "subdir", "binary": "allow", "requirements_files": ["requirements.txt"]}]'
    - name: end
status:
  done: true
"""
        gh.get_file_content.return_value = content
        gh.get_directory_listing.return_value = None
        result = analyzer._extract_prefetch_context(
            gh, 'owner', 'repo', 'push.yaml', 'main'
        )
        assert any('BINARY WHEELS ENABLED' in line for line in result)


# ── 17. _check_repo_build_files ──────────────────────────────────────────


class TestCheckRepoBuildFiles:
    def test_finds_unused_build_files(self):
        gh = MagicMock()
        gh.get_directory_listing.return_value = [
            'requirements.txt', 'requirements-build.txt', 'README.md'
        ]
        lines = []
        ConformaAnalyzer._check_repo_build_files(
            gh, 'owner', 'repo', 'subdir',
            ['subdir/requirements.txt'], 'main', lines
        )
        assert any('Unused Build Requirements Files Found' in l for l in lines)

    def test_no_build_files_found(self):
        gh = MagicMock()
        gh.get_directory_listing.return_value = ['requirements.txt', 'README.md']
        lines = []
        ConformaAnalyzer._check_repo_build_files(
            gh, 'owner', 'repo', 'subdir',
            ['subdir/requirements.txt'], 'main', lines
        )
        assert len(lines) == 0

    def test_exception_is_swallowed(self):
        gh = MagicMock()
        gh.get_directory_listing.side_effect = RuntimeError("API error")
        lines = []
        ConformaAnalyzer._check_repo_build_files(
            gh, 'owner', 'repo', 'subdir',
            ['subdir/requirements.txt'], 'main', lines
        )
        assert len(lines) == 0


# ── 18. Edge cases and integration ───────────────────────────────────────


class TestEdgeCases:
    def test_run_with_application_override(self):
        ai_repo = MagicMock()
        ai_repo.get_pending_conforma_violations.return_value = []
        langfuse = MagicMock()
        analyzer = _make_analyzer(ai_repo=ai_repo, langfuse=langfuse)

        result = analyzer.run(application='rhoai-v3-5-rc-1')

        ai_repo.get_pending_conforma_violations.assert_called_once_with(
            'rhoai-v3-5-rc-1', limit=5, component_filter=None, force=False
        )
        assert result['analyzed'] == 0

    def test_run_with_force_flag(self):
        ai_repo = MagicMock()
        ai_repo.get_pending_conforma_violations.return_value = []
        langfuse = MagicMock()
        analyzer = _make_analyzer(ai_repo=ai_repo, langfuse=langfuse)

        result = analyzer.run(force=True)

        ai_repo.get_pending_conforma_violations.assert_called_once_with(
            'rhoai-v3-5-ea-2', limit=5, component_filter=None, force=True
        )

    def test_annotate_with_outdated_label_category(self):
        analyzer = _make_analyzer()
        analysis = {
            'failure_category': 'policy_outdated_label',
            'recommended_fix': 'Update the label value',
        }
        violation = _make_violation()
        result = analyzer._annotate_fix_status(analysis, violation)
        assert 'typically resolved by a rebuild' in result['recommended_fix']

    def test_annotate_both_exception_and_version_mismatch(self):
        analyzer = _make_analyzer()
        analysis = {
            'failure_category': 'policy_version_mismatch',
            'recommended_fix': 'Fix the version',
        }
        violation = _make_violation(has_exception=True)
        result = analyzer._annotate_fix_status(analysis, violation)
        assert 'typically resolved by a rebuild' in result['recommended_fix']
        assert 'policy exception already covers' in result['recommended_fix']

    def test_parse_response_extracts_all_fields(self):
        analyzer = _make_analyzer()
        resp = _make_llm_response(tool_input={
            'root_cause': 'The build pipeline is missing the hermetic parameter setting',
            'failure_category': 'policy_hermetic_build',
            'confidence_score': 0.95,
            'recommended_fix': 'Add hermetic: true to the push pipeline YAML params section',
            'recommended_files': ['.tekton/odh-dashboard-push.yaml'],
            'can_auto_fix': False,
            'requires_human_review': True,
            'evidence_references': [
                {'type': 'doc', 'description': 'Hermetic builds docs'}
            ],
        })
        result = analyzer.parse_analysis_response(resp)
        assert result['recommended_files'] == ['.tekton/odh-dashboard-push.yaml']
        assert result['requires_human_review'] is True
        assert len(result.get('evidence_references', [])) == 1


# ── _validate_category ─────────────────────────────────────────────────


class TestValidateCategory:

    def test_non_catchall_category_unchanged(self):
        """Categories other than config_error/infrastructure pass through."""
        analyzer = _make_analyzer()
        analysis = {'failure_category': 'policy_hermetic_build', 'root_cause': 'test'}
        result = analyzer._validate_category(analysis, _make_violation())
        assert result['failure_category'] == 'policy_hermetic_build'

    @patch('conforma.policy_tools.detect_component_policy_type', return_value='chart')
    @patch('conforma.policy_tools.extract_violation_rules', return_value=['sbom.found'])
    def test_chart_config_error_kept(self, _mock_rules, _mock_type):
        """Charts with config_error keep their category — it's legitimate."""
        analyzer = _make_analyzer()
        analysis = {'failure_category': 'config_error', 'root_cause': 'chart issue'}
        result = analyzer._validate_category(analysis, _make_violation())
        assert result['failure_category'] == 'config_error'

    @patch('conforma.policy_tools.detect_component_policy_type', return_value='registry')
    @patch('conforma.policy_tools.extract_violation_rules',
           return_value=['hermetic_task.required', 'hermetic_task.build'])
    def test_config_error_corrected_to_hermetic(self, _mock_rules, _mock_type):
        """Non-chart with config_error and hermetic rules gets corrected."""
        analyzer = _make_analyzer()
        analysis = {'failure_category': 'config_error', 'root_cause': 'some issue'}
        result = analyzer._validate_category(analysis, _make_violation())
        assert result['failure_category'] == 'policy_hermetic_build'
        assert '[Category auto-corrected from config_error]' in result['root_cause']

    @patch('conforma.policy_tools.detect_component_policy_type', return_value='registry')
    @patch('conforma.policy_tools.extract_violation_rules', return_value=[])
    def test_no_rules_keeps_original(self, _mock_rules, _mock_type):
        """When no rules can be extracted, keep original category."""
        analyzer = _make_analyzer()
        analysis = {'failure_category': 'infrastructure', 'root_cause': 'infra'}
        result = analyzer._validate_category(analysis, _make_violation())
        assert result['failure_category'] == 'infrastructure'

    @patch('conforma.policy_tools.detect_component_policy_type', return_value='registry')
    @patch('conforma.policy_tools.extract_violation_rules',
           return_value=['hermetic_task.x', 'unknown_rule.y', 'mystery.z', 'random.w'])
    def test_below_threshold_keeps_original(self, _mock_rules, _mock_type):
        """When <50% of rules match a category, keep original."""
        analyzer = _make_analyzer()
        analysis = {'failure_category': 'config_error', 'root_cause': 'test'}
        result = analyzer._validate_category(analysis, _make_violation())
        assert result['failure_category'] == 'config_error'

    @patch('conforma.policy_tools.detect_component_policy_type', return_value='registry')
    @patch('conforma.policy_tools.extract_violation_rules',
           return_value=['trusted_task.ref', 'trusted_task.bundle'])
    def test_infrastructure_corrected_to_untrusted(self, _mock_rules, _mock_type):
        """Infrastructure category with trusted_task rules gets corrected."""
        analyzer = _make_analyzer()
        analysis = {'failure_category': 'infrastructure', 'root_cause': 'test'}
        result = analyzer._validate_category(analysis, _make_violation())
        assert result['failure_category'] == 'policy_untrusted_image'


# ── _validate_differential ──────────────────────────────────────────────


class TestValidateDifferential:

    def test_missing_differential_penalizes_confidence(self):
        analyzer = _make_analyzer()
        analysis = {'confidence_score': 0.90}
        result = analyzer._validate_differential(analysis)
        assert result['confidence_score'] == 0.80

    def test_empty_differential_penalizes(self):
        analyzer = _make_analyzer()
        analysis = {'confidence_score': 0.70, 'differential_diagnosis': []}
        result = analyzer._validate_differential(analysis)
        assert result['confidence_score'] == 0.60

    def test_single_hypothesis_penalizes(self):
        analyzer = _make_analyzer()
        analysis = {
            'confidence_score': 0.85,
            'differential_diagnosis': [{'hypothesis': 'only one'}],
        }
        result = analyzer._validate_differential(analysis)
        assert result['confidence_score'] == 0.75

    def test_two_hypotheses_no_penalty(self):
        analyzer = _make_analyzer()
        analysis = {
            'confidence_score': 0.85,
            'differential_diagnosis': [
                {'hypothesis': 'first'}, {'hypothesis': 'second'},
            ],
        }
        result = analyzer._validate_differential(analysis)
        assert result['confidence_score'] == 0.85

    def test_low_confidence_floors_at_zero(self):
        analyzer = _make_analyzer()
        analysis = {'confidence_score': 0.05}
        result = analyzer._validate_differential(analysis)
        assert result['confidence_score'] == 0.0


# ── _inject_component_type_context ──────────────────────────────────────


class TestInjectComponentTypeContext:

    @patch('conforma.policy_tools.detect_component_policy_type', return_value='chart')
    def test_chart_context_appended(self, _mock):
        analyzer = _make_analyzer()
        result = analyzer._inject_component_type_context('base prompt', _make_violation())
        assert 'HELM CHART' in result
        assert result.startswith('base prompt')

    @patch('conforma.policy_tools.detect_component_policy_type', return_value='fbc')
    def test_fbc_context_appended(self, _mock):
        analyzer = _make_analyzer()
        result = analyzer._inject_component_type_context('base prompt', _make_violation())
        assert 'FBC FRAGMENT' in result

    @patch('conforma.policy_tools.detect_component_policy_type', return_value='registry')
    def test_standard_component_unchanged(self, _mock):
        analyzer = _make_analyzer()
        result = analyzer._inject_component_type_context('base prompt', _make_violation())
        assert result == 'base prompt'
