"""Additional coverage tests for ReleaseFailureAnalyzer.

Covers uncovered lines: 355-392, 396-405, 441, 533-534, 577-578, 593-594,
677, 716-721, 731-741, 758, 766-779, 790-812, 820-834, 849-851, 902,
984-1015, 1029-1030, 1033-1037, 1043-1087, 1101, 1103, 1108-1113, 1122,
1175-1181, 1209, 1225, 1241-1242, 1451-1456, 1462-1465, 1545
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_config(llm=True):
    config = MagicMock()
    config.db = MagicMock()
    config.k8s.application_name = 'rhoai-v3-5-ea-2'
    config.k8s.namespace = 'rhoai-tenant'
    config.k8s.kubearchive_api_url = 'http://kubearchive'
    config.github_token = 'gh-tok'
    if llm:
        config.llm = MagicMock()
        config.llm.provider = 'anthropic'
    else:
        config.llm = None
    return config


def _make_llm_response(tool_input=None, tool_name='record_release_analysis',
                        input_tokens=1000, output_tokens=500):
    if tool_input is None:
        tool_input = {
            'root_cause': 'Source image missing',
            'failure_category': 'build_artifact_missing',
            'confidence_score': 0.85,
            'recommended_fix': 'Trigger rebuild',
            'recommended_files': ['bundle/additional-images-patch.yaml'],
            'fix_action_type': 'rebuild',
            'can_auto_fix': False,
            'requires_human_review': True,
            'affected_images': ['quay.io/rhoai/odh-dashboard-rhel9@sha256:abc123'],
            'owner_team': 'RHOAI',
            'evidence_references': [{'type': 'log', 'description': 'violation'}],
            'source_transparency': {
                'sources_consulted': ['logs'],
                'sources_unavailable': [],
                'limitations': [],
            },
        }
    resp = MagicMock()
    resp.tool_calls = [{'name': tool_name, 'input': tool_input}]
    resp.input_tokens = input_tokens
    resp.output_tokens = output_tokens
    resp.content = 'some text'
    resp.content_text = 'some text'
    return resp


def _build_analyzer(**kw):
    defaults = dict(
        config=_make_config(),
        db=MagicMock(),
        ai_repo=MagicMock(),
        llm=MagicMock(),
        langfuse=MagicMock(),
    )
    defaults.update(kw)
    with patch('analyzers.release_failure_analyzer.load_prompt', return_value='system prompt'):
        from analyzers.release_failure_analyzer import ReleaseFailureAnalyzer
        analyzer = ReleaseFailureAnalyzer(**defaults)
    analyzer.pattern_repo = MagicMock()
    analyzer.build_repo = MagicMock()
    analyzer.conforma_repo = MagicMock()
    analyzer.triage_repo = MagicMock()
    return analyzer


# ---------------------------------------------------------------------------
# Lines 355-392: _enrich_violation_context - Tekton Results branch
# ---------------------------------------------------------------------------
class TestEnrichViolationContextTektonResults:
    """Tests for the Tekton Results enrichment path in _enrich_violation_context."""

    def test_builds_with_sha_from_tekton_results(self):
        """Lines 355-371: Builds list is created from Tekton Results records."""
        analyzer = _build_analyzer()
        tr_mock = MagicMock()
        records = [
            {
                'metadata': {'name': 'build-1', 'creationTimestamp': '2026-01-01T00:00:00Z'},
                'status': {
                    'conditions': [{'reason': 'Completed'}],
                    'results': [{'name': 'IMAGE_DIGEST', 'value': 'sha256:aaa111'}],
                },
            },
            {
                'metadata': {'name': 'build-2', 'creationTimestamp': '2026-01-02T00:00:00Z'},
                'status': {
                    'conditions': [{'reason': 'Failed'}],
                    'results': [],
                },
            },
        ]
        tr_mock.query_component_build_history.return_value = records
        analyzer._get_tekton_results = MagicMock(return_value=tr_mock)
        analyzer.build_repo.get_component_history.return_value = None

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/odh-dashboard-rhel9@sha256:xyz999', 'rule': 'test_rule'},
            ],
            'application': 'rhoai-v3-5-ea-2',
        }
        analyzer._enrich_violation_context(context)

        enrichment = context['violation_enrichment']
        assert len(enrichment) == 1
        comp_name = list(enrichment.keys())[0]
        comp_data = enrichment[comp_name]
        assert 'builds_with_sha' in comp_data
        assert len(comp_data['builds_with_sha']) == 2
        assert comp_data['builds_with_sha'][0]['name'] == 'build-1'
        assert comp_data['builds_with_sha'][0]['image_sha'] == 'sha256:aaa111'
        assert comp_data['builds_with_sha'][1]['image_sha'] == ''

    def test_sha_tracing_finds_violation_build(self):
        """Lines 374-381: SHA tracing matches violation SHA to a build."""
        analyzer = _build_analyzer()
        tr_mock = MagicMock()
        violation_sha = 'sha256:abc123deadbeef'
        records = [
            {
                'metadata': {'name': 'build-matching', 'creationTimestamp': '2026-01-01'},
                'status': {
                    'conditions': [{'reason': 'PipelineRunTimeout'}],
                    'results': [{'name': 'IMAGE_DIGEST', 'value': violation_sha}],
                },
            },
        ]
        tr_mock.query_component_build_history.return_value = records
        analyzer._get_tekton_results = MagicMock(return_value=tr_mock)
        analyzer.build_repo.get_component_history.return_value = None

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/odh-dashboard-rhel9@{}'.format(violation_sha), 'rule': 'r'},
            ],
            'application': 'rhoai-v3-5-ea-2',
        }
        analyzer._enrich_violation_context(context)

        comp_data = list(context['violation_enrichment'].values())[0]
        assert comp_data.get('violation_build') is not None
        assert comp_data['violation_build']['name'] == 'build-matching'
        assert comp_data['violation_build']['status'] == 'PipelineRunTimeout'

    def test_latest_green_build_found(self):
        """Lines 382-390: Finds the latest green build with different SHA."""
        analyzer = _build_analyzer()
        tr_mock = MagicMock()
        violation_sha = 'sha256:badbadbad'
        records = [
            {
                'metadata': {'name': 'build-bad', 'creationTimestamp': '2026-01-02'},
                'status': {
                    'conditions': [{'reason': 'Failed'}],
                    'results': [{'name': 'IMAGE_DIGEST', 'value': violation_sha}],
                },
            },
            {
                'metadata': {'name': 'build-green', 'creationTimestamp': '2026-01-01'},
                'status': {
                    'conditions': [{'reason': 'Succeeded'}],
                    'results': [{'name': 'IMAGE_DIGEST', 'value': 'sha256:good123'}],
                },
            },
        ]
        tr_mock.query_component_build_history.return_value = records
        analyzer._get_tekton_results = MagicMock(return_value=tr_mock)
        analyzer.build_repo.get_component_history.return_value = None

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/odh-dashboard-rhel9@{}'.format(violation_sha), 'rule': 'r'},
            ],
            'application': 'rhoai-v3-5-ea-2',
        }
        analyzer._enrich_violation_context(context)

        comp_data = list(context['violation_enrichment'].values())[0]
        assert comp_data.get('latest_green_build') is not None
        assert comp_data['latest_green_build']['name'] == 'build-green'
        assert comp_data['latest_green_build']['image_sha'] == 'sha256:good123'

    def test_tekton_results_exception_handled(self):
        """Lines 391-392: Exception in Tekton Results is swallowed."""
        analyzer = _build_analyzer()
        analyzer._get_tekton_results = MagicMock(side_effect=RuntimeError("TR down"))
        analyzer.build_repo.get_component_history.return_value = None

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/some-comp-rhel9@sha256:abc', 'rule': 'r'},
            ],
            'application': 'rhoai-v3-5-ea-2',
        }
        # Should not raise
        analyzer._enrich_violation_context(context)
        enrichment = context.get('violation_enrichment', {})
        # builds_with_sha should not be present
        if enrichment:
            comp_data = list(enrichment.values())[0]
            assert 'builds_with_sha' not in comp_data

    def test_no_conditions_defaults_unknown_status(self):
        """Line 360: Build record with no conditions gets 'Unknown' status."""
        analyzer = _build_analyzer()
        tr_mock = MagicMock()
        records = [
            {
                'metadata': {'name': 'build-nocond', 'creationTimestamp': '2026-01-01'},
                'status': {
                    'conditions': [],
                    'results': [{'name': 'IMAGE_DIGEST', 'value': 'sha256:bbb'}],
                },
            },
        ]
        tr_mock.query_component_build_history.return_value = records
        analyzer._get_tekton_results = MagicMock(return_value=tr_mock)
        analyzer.build_repo.get_component_history.return_value = None

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/odh-dashboard-rhel9@sha256:xxx', 'rule': 'r'},
            ],
            'application': 'rhoai-v3-5-ea-2',
        }
        analyzer._enrich_violation_context(context)
        comp_data = list(context['violation_enrichment'].values())[0]
        assert comp_data['builds_with_sha'][0]['status'] == 'Unknown'


# ---------------------------------------------------------------------------
# Lines 396-405: _enrich_violation_context - build failure details
# ---------------------------------------------------------------------------
class TestEnrichViolationContextBuildFailureDetails:
    """Tests for the failed TaskRun lookup when latest build failed."""

    def test_failed_taskrun_lookup(self):
        """Lines 396-403: When latest build is Failed, look up failed TaskRun."""
        analyzer = _build_analyzer()
        tr_mock = MagicMock()

        records = [
            {
                'metadata': {'name': 'build-fail', 'creationTimestamp': '2026-01-01'},
                'status': {
                    'conditions': [{'reason': 'Failed'}],
                    'results': [{'name': 'IMAGE_DIGEST', 'value': 'sha256:fff'}],
                },
            },
        ]
        tr_mock.query_component_build_history.return_value = records
        tr_mock.find_failed_taskrun.return_value = ('source-build', 'error: timeout occurred in source-build task' * 20, None)
        analyzer._get_tekton_results = MagicMock(return_value=tr_mock)
        analyzer.build_repo.get_component_history.return_value = None

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/odh-dashboard-rhel9@sha256:fff', 'rule': 'r'},
            ],
            'application': 'rhoai-v3-5-ea-2',
        }
        analyzer._enrich_violation_context(context)

        comp_data = list(context['violation_enrichment'].values())[0]
        assert comp_data.get('failed_task') == 'source-build'
        assert comp_data.get('failed_task_logs') is not None
        # Should be truncated to 500 chars
        assert len(comp_data['failed_task_logs']) <= 500

    def test_pipeline_run_timeout_triggers_lookup(self):
        """Lines 397: PipelineRunTimeout status also triggers lookup."""
        analyzer = _build_analyzer()
        tr_mock = MagicMock()

        records = [
            {
                'metadata': {'name': 'build-timeout', 'creationTimestamp': '2026-01-01'},
                'status': {
                    'conditions': [{'reason': 'PipelineRunTimeout'}],
                    'results': [],
                },
            },
        ]
        tr_mock.query_component_build_history.return_value = records
        tr_mock.find_failed_taskrun.return_value = ('source-build', 'timed out', None)
        analyzer._get_tekton_results = MagicMock(return_value=tr_mock)
        analyzer.build_repo.get_component_history.return_value = None

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/odh-dashboard-rhel9@sha256:abc', 'rule': 'r'},
            ],
            'application': 'rhoai-v3-5-ea-2',
        }
        analyzer._enrich_violation_context(context)
        comp_data = list(context['violation_enrichment'].values())[0]
        assert comp_data.get('failed_task') == 'source-build'

    def test_failed_taskrun_lookup_exception(self):
        """Lines 404-405: Exception in failed TaskRun lookup is swallowed."""
        analyzer = _build_analyzer()
        tr_mock = MagicMock()
        records = [
            {
                'metadata': {'name': 'build-fail', 'creationTimestamp': '2026-01-01'},
                'status': {
                    'conditions': [{'reason': 'Failed'}],
                    'results': [{'name': 'IMAGE_DIGEST', 'value': 'sha256:fff'}],
                },
            },
        ]
        tr_mock.query_component_build_history.return_value = records
        tr_mock.find_failed_taskrun.side_effect = RuntimeError("TaskRun lookup failed")
        analyzer._get_tekton_results = MagicMock(return_value=tr_mock)
        analyzer.build_repo.get_component_history.return_value = None

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/odh-dashboard-rhel9@sha256:fff', 'rule': 'r'},
            ],
            'application': 'rhoai-v3-5-ea-2',
        }
        # Should not raise
        analyzer._enrich_violation_context(context)
        comp_data = list(context['violation_enrichment'].values())[0]
        assert 'failed_task' not in comp_data

    def test_failed_taskrun_not_found_returns_none(self):
        """Line 401: When failed_task is None/empty, don't set it."""
        analyzer = _build_analyzer()
        tr_mock = MagicMock()
        records = [
            {
                'metadata': {'name': 'build-fail', 'creationTimestamp': '2026-01-01'},
                'status': {
                    'conditions': [{'reason': 'Failed'}],
                    'results': [{'name': 'IMAGE_DIGEST', 'value': 'sha256:fff'}],
                },
            },
        ]
        tr_mock.query_component_build_history.return_value = records
        tr_mock.find_failed_taskrun.return_value = (None, None, None)
        analyzer._get_tekton_results = MagicMock(return_value=tr_mock)
        analyzer.build_repo.get_component_history.return_value = None

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/odh-dashboard-rhel9@sha256:fff', 'rule': 'r'},
            ],
            'application': 'rhoai-v3-5-ea-2',
        }
        analyzer._enrich_violation_context(context)
        comp_data = list(context['violation_enrichment'].values())[0]
        assert 'failed_task' not in comp_data


# ---------------------------------------------------------------------------
# Line 441: _enrich_artifact_health - RegistryClient creation failure
# ---------------------------------------------------------------------------
class TestEnrichArtifactHealthRegistryError:
    """Test RegistryClient import/creation failure in _enrich_artifact_health."""

    def test_registry_client_creation_fails_returns_early(self):
        """Line 441: parse_image_ref returns empty -> returns early."""
        analyzer = _build_analyzer()
        mock_rc = MagicMock()
        mock_rc.parse_image_ref.return_value = ('', '', '')

        comp_data = {'component': 'test-comp'}
        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/test-comp-rhel9@sha256:abc', 'rule': 'r'},
            ],
        }
        with patch('analyzers.release_failure_analyzer.RegistryClient', return_value=mock_rc, create=True):
            # Need to import RegistryClient inside the method; mock the import
            with patch.dict('sys.modules', {'clients.registry_client': MagicMock(RegistryClient=MagicMock(return_value=mock_rc))}):
                analyzer._enrich_artifact_health(comp_data, 'sha256:abc', context)
        assert 'artifact_health' not in comp_data

    def test_registry_client_import_exception(self):
        """Line 424: RegistryClient import/init exception returns early."""
        analyzer = _build_analyzer()
        comp_data = {'component': 'test-comp'}
        context = {'violations_summary': []}

        # Make the import of RegistryClient raise
        with patch.dict('sys.modules', {'clients.registry_client': None}):
            # Should not raise; returns early
            analyzer._enrich_artifact_health(comp_data, 'sha256:abc', context)
        assert 'artifact_health' not in comp_data


# ---------------------------------------------------------------------------
# Lines 533-534: _enrich_application_context - nightly history
# ---------------------------------------------------------------------------
class TestEnrichApplicationContextNightly:
    """Test nightly build history enrichment."""

    def test_nightly_history_enriched(self):
        """Lines 533-534: Nightly history is added to context, limited to 5."""
        analyzer = _build_analyzer()
        nightly_records = [
            {'created_at': '2026-01-0{}T00:00:00Z'.format(i), 'status': 'Completed', 'pr_name': 'nightly-{}'.format(i)}
            for i in range(1, 8)
        ]
        analyzer.build_repo.get_nightly_history.return_value = nightly_records
        analyzer.build_repo.find_failing_component_names.return_value = None
        analyzer.conforma_repo.find_unresolved_component_names.return_value = None
        analyzer.triage_repo.get_active.return_value = None
        analyzer.build_repo.get_working_components.return_value = []
        analyzer.build_repo.find_unresolved_component_names.return_value = []

        context = {'application': 'rhoai-v3-5-ea-2'}
        analyzer._enrich_application_context(context)
        assert 'nightly_history' in context
        assert len(context['nightly_history']) == 5  # limited to 5


# ---------------------------------------------------------------------------
# Lines 501-506: _enrich_application_context - conforma violations
# ---------------------------------------------------------------------------
class TestEnrichApplicationContextConforma:
    """Test active conforma violations enrichment."""

    def test_active_conforma_violations_enriched(self):
        """Lines 533-534: Conforma violations list added to context."""
        analyzer = _build_analyzer()
        analyzer.conforma_repo.find_unresolved_component_names.return_value = ['comp-a', 'comp-b']
        analyzer.build_repo.find_failing_component_names.return_value = None
        analyzer.triage_repo.get_active.return_value = None
        analyzer.build_repo.get_nightly_history.return_value = None
        analyzer.build_repo.get_working_components.return_value = []
        analyzer.build_repo.find_unresolved_component_names.return_value = []

        context = {'application': 'rhoai-v3-5-ea-2'}
        analyzer._enrich_application_context(context)
        assert 'active_conforma_violations' in context
        assert context['active_conforma_violations'] == ['comp-a', 'comp-b']


# ---------------------------------------------------------------------------
# Lines 577-578, 593-594: _enrich_with_component_prs
# ---------------------------------------------------------------------------
class TestEnrichWithComponentPRs:
    """Tests for nudge PR enrichment."""

    def test_operator_nudging_yaml_fetched(self):
        """Lines 577-578: operator-nudging.yaml is fetched and stored."""
        analyzer = _build_analyzer()
        gh_mock = MagicMock()
        gh_mock.get_file_content.return_value = 'nudging: content here\nimage: sha256:abc'
        gh_mock.list_pull_requests.return_value = []
        analyzer._get_github = MagicMock(return_value=gh_mock)

        context = {
            'application': 'rhoai-v3-5-ea-2',
            'violation_enrichment': {
                'odh-dashboard-v3-5-ea-2': {'component': 'odh-dashboard-v3-5-ea-2'},
            },
        }
        analyzer._enrich_with_component_prs(context)
        assert 'operator_nudging_content' in context
        assert 'operator_nudging_source' in context

    def test_operator_nudging_fetch_exception(self):
        """Lines 577-578: Exception fetching nudging yaml is swallowed."""
        analyzer = _build_analyzer()
        gh_mock = MagicMock()
        gh_mock.get_file_content.side_effect = RuntimeError("API error")
        gh_mock.list_pull_requests.return_value = []
        analyzer._get_github = MagicMock(return_value=gh_mock)

        context = {
            'application': 'rhoai-v3-5-ea-2',
            'violation_enrichment': {
                'odh-dashboard-v3-5-ea-2': {'component': 'odh-dashboard-v3-5-ea-2'},
            },
        }
        # Should not raise
        analyzer._enrich_with_component_prs(context)
        assert 'operator_nudging_content' not in context

    def test_nudge_prs_matched_by_component_short_name(self):
        """Lines 582-591: PRs are matched by shortened component name."""
        analyzer = _build_analyzer()
        gh_mock = MagicMock()
        gh_mock.get_file_content.return_value = None
        gh_mock.list_pull_requests.return_value = [
            {'number': 42, 'title': 'Nudge odh-dashboard SHA', 'state': 'open', 'html_url': 'https://github.com/test/42'},
            {'number': 43, 'title': 'Unrelated PR', 'state': 'open', 'html_url': 'https://github.com/test/43'},
        ]
        analyzer._get_github = MagicMock(return_value=gh_mock)

        context = {
            'application': 'rhoai-v3-5-ea-2',
            'violation_enrichment': {
                'odh-dashboard-v3-5-ea-2': {'component': 'odh-dashboard-v3-5-ea-2'},
            },
        }
        analyzer._enrich_with_component_prs(context)
        data = context['violation_enrichment']['odh-dashboard-v3-5-ea-2']
        assert 'nudge_prs' in data
        assert len(data['nudge_prs']) == 1
        assert data['nudge_prs'][0]['number'] == 42

    def test_nudge_prs_matched_by_nudge_keyword(self):
        """Lines 588: PRs with 'nudge' in title are matched."""
        analyzer = _build_analyzer()
        gh_mock = MagicMock()
        gh_mock.get_file_content.return_value = None
        gh_mock.list_pull_requests.return_value = [
            {'number': 50, 'title': 'Auto-nudge update for all components', 'state': 'closed', 'html_url': ''},
        ]
        analyzer._get_github = MagicMock(return_value=gh_mock)

        context = {
            'application': 'rhoai-v3-5-ea-2',
            'violation_enrichment': {
                'odh-vllm-v3-5-ea-2': {'component': 'odh-vllm-v3-5-ea-2'},
            },
        }
        analyzer._enrich_with_component_prs(context)
        data = context['violation_enrichment']['odh-vllm-v3-5-ea-2']
        assert 'nudge_prs' in data
        assert data['nudge_prs'][0]['number'] == 50

    def test_pr_lookup_exception_swallowed(self):
        """Lines 593-594: Exception in PR lookup is swallowed per-component."""
        analyzer = _build_analyzer()
        gh_mock = MagicMock()
        gh_mock.get_file_content.return_value = None
        gh_mock.list_pull_requests.side_effect = RuntimeError("API error")
        analyzer._get_github = MagicMock(return_value=gh_mock)

        context = {
            'application': 'rhoai-v3-5-ea-2',
            'violation_enrichment': {
                'comp-v3-5-ea-2': {'component': 'comp-v3-5-ea-2'},
            },
        }
        # Should not raise
        analyzer._enrich_with_component_prs(context)
        data = context['violation_enrichment']['comp-v3-5-ea-2']
        assert 'nudge_prs' not in data


# ---------------------------------------------------------------------------
# Line 677: collect_context - charts type
# ---------------------------------------------------------------------------
class TestCollectContextCharts:
    """Test chart type detection in collect_context."""

    def test_chart_release_plan_sets_charts_type(self):
        """Line 677: When release_plan contains 'chart', type = 'charts'."""
        analyzer = _build_analyzer()
        analyzer._k8s_get_json = MagicMock(return_value={
            'spec': {
                'snapshot': 'rhoai-v3-5-ea-2-1234',
                'releasePlan': 'rhoai-charts-stage',
            },
            'status': {
                'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
                'managedProcessing': {},
            },
            'metadata': {'creationTimestamp': '2026-01-01'},
        })
        # Skip all the external enrichments
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        context = analyzer.collect_context('test-release')
        assert context['type'] == 'charts'


# ---------------------------------------------------------------------------
# Lines 716-721: collect_context - TEST_OUTPUT extraction
# ---------------------------------------------------------------------------
class TestCollectContextTestOutput:
    """Test TEST_OUTPUT extraction from TaskRun results."""

    def test_test_output_extracted_from_taskrun(self):
        """Lines 716-721: TEST_OUTPUT JSON result is parsed and stored."""
        analyzer = _build_analyzer()
        release_cr = {
            'spec': {'snapshot': 'snap-1', 'releasePlan': 'rhoai-onprem-v3-5-components-prod'},
            'status': {
                'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed',
                                'message': 'task verify-conforma failed'}],
                'managedProcessing': {'pipelineRun': 'ns/pipeline-run-1'},
            },
            'metadata': {'creationTimestamp': '2026-01-01'},
        }
        analyzer._k8s_get_json = MagicMock(side_effect=lambda kind, name, ns: release_cr if kind == 'release' else None)

        ka_mock = MagicMock()
        test_output_json = '{"successes": 10, "failures": 2, "warnings": 1, "result": "FAILURE"}'
        ka_mock.get_taskrun.return_value = {
            'status': {
                'results': [{'name': 'TEST_OUTPUT', 'value': test_output_json}],
            },
        }
        ka_mock.get_pod_logs.return_value = None
        analyzer._get_kubearchive = MagicMock(return_value=ka_mock)
        analyzer._get_gitlab = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None), list_directory=MagicMock(return_value=None)))
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        context = analyzer.collect_context('test-release')
        assert context['pipeline']['test_output'] == {
            'successes': 10, 'failures': 2, 'warnings': 1, 'result': 'FAILURE'
        }

    def test_test_output_invalid_json_ignored(self):
        """Lines 721: Invalid JSON in TEST_OUTPUT is silently ignored."""
        analyzer = _build_analyzer()
        release_cr = {
            'spec': {'snapshot': 'snap-1', 'releasePlan': 'rhoai-prod-plan'},
            'status': {
                'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed',
                                'message': 'task verify-conforma failed'}],
                'managedProcessing': {'pipelineRun': 'ns/pipeline-run-2'},
            },
            'metadata': {'creationTimestamp': '2026-01-01'},
        }
        analyzer._k8s_get_json = MagicMock(side_effect=lambda kind, name, ns: release_cr if kind == 'release' else None)

        ka_mock = MagicMock()
        ka_mock.get_taskrun.return_value = {
            'status': {
                'results': [{'name': 'TEST_OUTPUT', 'value': 'not valid json'}],
            },
        }
        ka_mock.get_pod_logs.return_value = None
        analyzer._get_kubearchive = MagicMock(return_value=ka_mock)
        analyzer._get_gitlab = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None), list_directory=MagicMock(return_value=None)))
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        context = analyzer.collect_context('test-release')
        assert 'test_output' not in context.get('pipeline', {})


# ---------------------------------------------------------------------------
# Lines 731-741: collect_context - violations from step-detailed-report
# ---------------------------------------------------------------------------
class TestCollectContextViolationsExtraction:
    """Test violation extraction from step-detailed-report logs."""

    def test_violations_extracted_from_detailed_report(self):
        """Lines 731-741: Violations are extracted before log truncation."""
        analyzer = _build_analyzer()
        release_cr = {
            'spec': {'snapshot': 'snap-1', 'releasePlan': 'rhoai-prod-plan'},
            'status': {
                'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed',
                                'message': 'task verify-conforma failed'}],
                'managedProcessing': {'pipelineRun': 'ns/pipeline-run-3'},
            },
            'metadata': {'creationTimestamp': '2026-01-01'},
        }
        analyzer._k8s_get_json = MagicMock(side_effect=lambda kind, name, ns: release_cr if kind == 'release' else None)

        ka_mock = MagicMock()
        ka_mock.get_taskrun.return_value = {
            'status': {'results': []},
        }
        detailed_report_logs = (
            'ImageRef: quay.io/rhoai/odh-dashboard-rhel9@sha256:abc123\n'
            '[Violation] source_image.exists\n'
            'Reason: Source image not found\n'
        )

        def mock_get_pod_logs(pod_name, container='', namespace=''):
            if container == 'step-detailed-report':
                return detailed_report_logs
            return None

        ka_mock.get_pod_logs = mock_get_pod_logs
        analyzer._get_kubearchive = MagicMock(return_value=ka_mock)
        analyzer._get_gitlab = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None), list_directory=MagicMock(return_value=None)))
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        context = analyzer.collect_context('test-release')
        assert 'violations_summary' in context
        assert len(context['violations_summary']) == 1
        assert context['violations_summary'][0]['rule'] == 'source_image.exists'
        assert context['violations_summary'][0]['reason'] == 'Source image not found'
        # Logs should also be stored
        assert 'step-detailed-report' in context.get('logs', {})

    def test_large_logs_truncated(self):
        """Lines 737-740: Logs > 50000 chars are truncated from end."""
        analyzer = _build_analyzer()
        release_cr = {
            'spec': {'snapshot': 'snap-1', 'releasePlan': 'rhoai-prod-plan'},
            'status': {
                'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed',
                                'message': 'task some-task failed'}],
                'managedProcessing': {'pipelineRun': 'ns/pipeline-run-4'},
            },
            'metadata': {'creationTimestamp': '2026-01-01'},
        }
        analyzer._k8s_get_json = MagicMock(side_effect=lambda kind, name, ns: release_cr if kind == 'release' else None)

        ka_mock = MagicMock()
        ka_mock.get_taskrun.return_value = {'status': {'results': []}}
        # Generate very large logs
        large_logs = 'x' * 100000

        def mock_get_pod_logs(pod_name, container='', namespace=''):
            if container == 'step-validate':
                return large_logs
            return None

        ka_mock.get_pod_logs = mock_get_pod_logs
        analyzer._get_kubearchive = MagicMock(return_value=ka_mock)
        analyzer._get_gitlab = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None), list_directory=MagicMock(return_value=None)))
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        context = analyzer.collect_context('test-release')
        assert len(context['logs']['step-validate']) == 50000


# ---------------------------------------------------------------------------
# Line 758: collect_context - snapshot not found warning
# ---------------------------------------------------------------------------
class TestCollectContextSnapshotNotFound:
    """Test snapshot not found case."""

    def test_snapshot_not_found_logs_warning(self):
        """Line 758: When snapshot CR is not found, warn and continue."""
        analyzer = _build_analyzer()

        def mock_k8s(kind, name, ns):
            if kind == 'release':
                return {
                    'spec': {'snapshot': 'snap-missing', 'releasePlan': 'rhoai-prod-plan'},
                    'status': {
                        'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
                        'managedProcessing': {},
                    },
                    'metadata': {'creationTimestamp': '2026-01-01'},
                }
            # snapshot not found
            return None

        analyzer._k8s_get_json = MagicMock(side_effect=mock_k8s)
        analyzer._get_gitlab = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None), list_directory=MagicMock(return_value=None)))
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        context = analyzer.collect_context('test-release')
        assert 'snapshot_components' not in context


# ---------------------------------------------------------------------------
# Lines 766-779: collect_context - artifact health batch check
# ---------------------------------------------------------------------------
class TestCollectContextArtifactHealthBatch:
    """Test artifact health batch check for snapshot components."""

    def test_artifact_health_batch_all_healthy(self):
        """Lines 766-779: All components healthy."""
        analyzer = _build_analyzer()

        def mock_k8s(kind, name, ns):
            if kind == 'release':
                return {
                    'spec': {'snapshot': 'snap-1', 'releasePlan': 'rhoai-prod-plan'},
                    'status': {
                        'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
                        'managedProcessing': {},
                    },
                    'metadata': {'creationTimestamp': '2026-01-01'},
                }
            if kind == 'snapshot':
                return {
                    'spec': {
                        'components': [
                            {'name': 'comp-a', 'containerImage': 'quay.io/rhoai/comp-a@sha256:aaa'},
                            {'name': 'comp-b', 'containerImage': 'quay.io/rhoai/comp-b@sha256:bbb'},
                        ],
                    },
                }
            return None

        analyzer._k8s_get_json = MagicMock(side_effect=mock_k8s)

        mock_rc = MagicMock()
        mock_rc.check_artifact_health_batch.return_value = {
            'comp-a': {'healthy': True},
            'comp-b': {'healthy': True},
        }

        analyzer._get_gitlab = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None), list_directory=MagicMock(return_value=None)))
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        with patch.dict('sys.modules', {'clients.registry_client': MagicMock(RegistryClient=MagicMock(return_value=mock_rc))}):
            context = analyzer.collect_context('test-release')
        assert 'artifact_health' in context
        assert all(h['healthy'] for h in context['artifact_health'].values())

    def test_artifact_health_batch_some_unhealthy(self):
        """Lines 772-777: Unhealthy components are logged."""
        analyzer = _build_analyzer()

        def mock_k8s(kind, name, ns):
            if kind == 'release':
                return {
                    'spec': {'snapshot': 'snap-1', 'releasePlan': 'rhoai-prod-plan'},
                    'status': {
                        'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
                        'managedProcessing': {},
                    },
                    'metadata': {'creationTimestamp': '2026-01-01'},
                }
            if kind == 'snapshot':
                return {
                    'spec': {
                        'components': [
                            {'name': 'comp-a', 'containerImage': 'quay.io/rhoai/comp-a@sha256:aaa'},
                        ],
                    },
                }
            return None

        analyzer._k8s_get_json = MagicMock(side_effect=mock_k8s)

        mock_rc = MagicMock()
        mock_rc.check_artifact_health_batch.return_value = {
            'comp-a': {'healthy': False, 'missing': ['src']},
        }

        analyzer._get_gitlab = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None), list_directory=MagicMock(return_value=None)))
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        with patch.dict('sys.modules', {'clients.registry_client': MagicMock(RegistryClient=MagicMock(return_value=mock_rc))}):
            context = analyzer.collect_context('test-release')
        assert 'artifact_health' in context
        assert not context['artifact_health']['comp-a']['healthy']

    def test_artifact_health_batch_exception(self):
        """Line 782: Exception in batch health check is caught."""
        analyzer = _build_analyzer()

        def mock_k8s(kind, name, ns):
            if kind == 'release':
                return {
                    'spec': {'snapshot': 'snap-1', 'releasePlan': 'rhoai-prod-plan'},
                    'status': {
                        'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
                        'managedProcessing': {},
                    },
                    'metadata': {'creationTimestamp': '2026-01-01'},
                }
            if kind == 'snapshot':
                return {
                    'spec': {
                        'components': [{'name': 'comp-a', 'containerImage': 'img'}],
                    },
                }
            return None

        analyzer._k8s_get_json = MagicMock(side_effect=mock_k8s)
        analyzer._get_gitlab = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None), list_directory=MagicMock(return_value=None)))
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        # Make RegistryClient raise
        with patch.dict('sys.modules', {'clients.registry_client': MagicMock(RegistryClient=MagicMock(side_effect=RuntimeError("reg err")))}):
            context = analyzer.collect_context('test-release')
        assert 'artifact_health' not in context


# ---------------------------------------------------------------------------
# Lines 790-812: collect_context - RPA mapping fallback
# ---------------------------------------------------------------------------
class TestCollectContextRPAFallback:
    """Test RPA mapping fallback when initial file not found."""

    def test_rpa_directory_listing_and_pattern_match(self):
        """Lines 790-812: When RPA file not found, list directory and match."""
        analyzer = _build_analyzer()

        # Use a release plan with 'onprem' to exercise the onperm replacement
        rp_name = 'rhoai-onprem-v3-5-components-prod'
        # The file in the directory uses the 'onperm' typo variant
        rpa_file_name = 'rhoai-onperm-v3-5-components-prod.yaml'

        def mock_k8s(kind, name, ns):
            if kind == 'release':
                return {
                    'spec': {'snapshot': 'snap-1', 'releasePlan': rp_name},
                    'status': {
                        'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
                        'managedProcessing': {},
                    },
                    'metadata': {'creationTimestamp': '2026-01-01'},
                }
            return None

        analyzer._k8s_get_json = MagicMock(side_effect=mock_k8s)

        gl_mock = MagicMock()

        # Track calls to distinguish them
        get_file_calls = []
        def mock_get_file(project, path):
            get_file_calls.append(path)
            # Initial RPA exact lookup returns None (file not found)
            if rp_name in path and 'onperm' not in path:
                return None
            # Pattern match with 'onperm' returns content
            if rpa_file_name in path:
                return 'rpa_content: matched'
            return None

        gl_mock.get_file_content = mock_get_file

        list_dir_calls = []
        def mock_list_dir(project, path):
            list_dir_calls.append(path)
            # RPA directory listing returns files
            return [
                {'name': rpa_file_name, 'path': 'rpa/{}'.format(rpa_file_name)},
                {'name': 'other-file.txt', 'path': 'rpa/other-file.txt'},
            ]

        gl_mock.list_directory = mock_list_dir

        analyzer._get_gitlab = MagicMock(return_value=gl_mock)
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        with patch('analyzers.release_failure_analyzer.GITLAB_EC_PATHS', []):
            context = analyzer.collect_context('test-release')
        assert 'rpa_content' in context
        assert context['rpa_content'] == 'rpa_content: matched'


    def test_rpa_found_on_first_try(self):
        """Lines 794-796: RPA file found on first exact lookup."""
        analyzer = _build_analyzer()

        def mock_k8s(kind, name, ns):
            if kind == 'release':
                return {
                    'spec': {'snapshot': 'snap-1', 'releasePlan': 'rhoai-onprem-v3-5-components-prod'},
                    'status': {
                        'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
                        'managedProcessing': {},
                    },
                    'metadata': {'creationTimestamp': '2026-01-01'},
                }
            return None

        analyzer._k8s_get_json = MagicMock(side_effect=mock_k8s)

        gl_mock = MagicMock()
        gl_mock.get_file_content.return_value = 'rpa_yaml_content: found'
        gl_mock.list_directory.return_value = []
        analyzer._get_gitlab = MagicMock(return_value=gl_mock)
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        with patch('analyzers.release_failure_analyzer.GITLAB_EC_PATHS', []):
            context = analyzer.collect_context('test-release')
        assert context['rpa_content'] == 'rpa_yaml_content: found'
        assert 'rpa_source' in context


# ---------------------------------------------------------------------------
# Lines 820-834: collect_context - EC policy files
# ---------------------------------------------------------------------------
class TestCollectContextECPolicies:
    """Test EC policy file fetching from GitLab."""

    @patch('analyzers.release_failure_analyzer.GITLAB_EC_PATHS', ['ec/policies'])
    def test_ec_policy_files_fetched(self):
        """Lines 820-834: EC policy files are listed and fetched."""
        analyzer = _build_analyzer()

        def mock_k8s(kind, name, ns):
            if kind == 'release':
                return {
                    'spec': {'snapshot': 'snap-1', 'releasePlan': 'plan'},
                    'status': {
                        'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
                        'managedProcessing': {},
                    },
                    'metadata': {'creationTimestamp': '2026-01-01'},
                }
            return None

        analyzer._k8s_get_json = MagicMock(side_effect=mock_k8s)

        gl_mock = MagicMock()
        gl_mock.get_file_content.side_effect = [
            None,  # RPA file
            'ec-policy-content',  # EC policy file
        ]
        gl_mock.list_directory.side_effect = [
            None,  # RPA directory
            [{'name': 'stage-policy.yaml', 'path': 'ec/policies/stage-policy.yaml'}],  # EC files
        ]
        analyzer._get_gitlab = MagicMock(return_value=gl_mock)
        analyzer._get_github = MagicMock(return_value=MagicMock(get_file_content=MagicMock(return_value=None)))
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        context = analyzer.collect_context('test-release')
        assert 'ec_policies' in context
        assert len(context['ec_policies']) == 1
        assert context['ec_policies'][0]['path'] == 'ec/policies/stage-policy.yaml'
        assert context['ec_policies'][0]['content'] == 'ec-policy-content'


# ---------------------------------------------------------------------------
# Lines 849-851: collect_context - bundle images from GitHub
# ---------------------------------------------------------------------------
class TestCollectContextBundleImages:
    """Test bundle images fetching from GitHub."""

    def test_bundle_images_fetched(self):
        """Lines 849-851: Bundle images content and source are stored."""
        analyzer = _build_analyzer()

        def mock_k8s(kind, name, ns):
            if kind == 'release':
                return {
                    'spec': {'snapshot': 'snap-1', 'releasePlan': 'plan'},
                    'status': {
                        'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
                        'managedProcessing': {},
                    },
                    'metadata': {'creationTimestamp': '2026-01-01'},
                }
            return None

        analyzer._k8s_get_json = MagicMock(side_effect=mock_k8s)
        analyzer._get_gitlab = MagicMock(return_value=MagicMock(
            get_file_content=MagicMock(return_value=None),
            list_directory=MagicMock(return_value=None)))

        gh_mock = MagicMock()
        gh_mock.get_file_content.return_value = 'bundle:\n  images:\n    - quay.io/rhoai/test@sha256:abc'
        analyzer._get_github = MagicMock(return_value=gh_mock)
        analyzer._enrich_violation_context = MagicMock()
        analyzer._enrich_application_context = MagicMock()
        analyzer._enrich_with_component_prs = MagicMock()
        analyzer._enrich_with_readiness_checks = MagicMock()

        context = analyzer.collect_context('test-release')
        assert 'bundle_images_content' in context
        assert 'bundle_images_source' in context
        assert 'additional-images-patch.yaml' in context['bundle_images_source']


# ---------------------------------------------------------------------------
# build_analysis_prompt coverage tests
# ---------------------------------------------------------------------------
class TestBuildAnalysisPromptCoverage:
    """Tests for uncovered lines in build_analysis_prompt."""

    def _base_context(self):
        return {
            'release_name': 'test-release',
            'application': 'rhoai-v3-5-ea-2',
            'snapshot': 'snap-1',
            'release_plan': 'rhoai-prod-plan',
            'target': 'prod',
            'type': 'components',
            'created_at': '2026-01-01',
            'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
            'pipeline': {'ref': 'ns/pipeline-run-1', 'failed_task': 'verify-conforma'},
        }

    def test_test_output_section(self):
        """Line 902: Test output is formatted in pipeline section."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['pipeline']['test_output'] = {
            'successes': 10, 'failures': 2, 'warnings': 1, 'result': 'FAILURE'
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'successes=10' in user_prompt
        assert 'failures=2' in user_prompt
        assert 'warnings=1' in user_prompt
        assert 'result=FAILURE' in user_prompt

    def test_operator_nudging_relevant_lines(self):
        """Lines 984-1015: Operator nudging shows relevant lines when content is large."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()

        # Build large nudging content (> 5000 chars) with a matching component name
        lines = ['line-{}: some-padding-content-to-make-lines-longer-{}'.format(i, 'x' * 20) for i in range(300)]
        lines[50] = 'odh-dashboard: sha256:abc123'
        context['operator_nudging_content'] = '\n'.join(lines)
        context['operator_nudging_source'] = 'owner/repo branch build/operator-nudging.yaml'
        context['violation_enrichment'] = {
            'odh-dashboard-v3-5-ea-2': {'component': 'odh-dashboard-v3-5-ea-2'},
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'relevant lines only' in user_prompt
        assert 'odh-dashboard' in user_prompt

    def test_operator_nudging_no_matching_lines_falls_back(self):
        """Lines 1008-1011: When no relevant lines found, show truncated content."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()

        lines = ['unrelated-content-{}-{}'.format(i, 'x' * 30) for i in range(300)]
        context['operator_nudging_content'] = '\n'.join(lines)
        context['operator_nudging_source'] = 'source'
        context['violation_enrichment'] = {
            'zzz-unique-component-v3-5-ea-2': {'component': 'zzz-unique-component-v3-5-ea-2'},
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        # Should fall back to full content (no 'relevant lines only')
        assert '```yaml' in user_prompt
        assert 'unrelated-content-0' in user_prompt

    def test_operator_nudging_small_content_shows_all(self):
        """Lines 1012-1015: Small nudging content is shown in full."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()

        context['operator_nudging_content'] = 'small: content\nonly: two lines'
        context['operator_nudging_source'] = 'source'
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {'component': 'comp-v3-5-ea-2'},
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'small: content' in user_prompt

    def test_violation_build_display(self):
        """Lines 1029-1030: Violation build info is displayed."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'violation_build': {
                    'name': 'build-bad-123', 'status': 'PipelineRunTimeout', 'created': '2026-01-01'
                },
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'VIOLATION SHA belongs to build: build-bad-123' in user_prompt
        assert 'PipelineRunTimeout' in user_prompt

    def test_sha_mismatch_display(self):
        """Lines 1033-1038: SHA mismatch is flagged when both violation and green build exist."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'violation_build': {
                    'name': 'build-bad', 'status': 'Failed', 'created': '2026-01-01'
                },
                'latest_green_build': {
                    'name': 'build-good', 'image_sha': 'sha256:green123', 'created': '2026-01-02', 'status': 'Completed',
                },
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'LATEST GREEN BUILD' in user_prompt
        assert 'SHA MISMATCH' in user_prompt
        assert 'Failed' in user_prompt

    def test_artifact_health_verification_section(self):
        """Lines 1043-1062: Artifact health verification is displayed with status."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'artifact_health': {
                    'violation_sha': {
                        'digest': 'sha256:abc12345...',
                        'healthy': False,
                        'missing': ['src'],
                        'artifacts': {
                            'sig': {'exists': True},
                            'src': {'exists': False},
                            'att': {'exists': True},
                            'sbom': {'exists': True},
                        },
                    },
                    'green_build_sha': {
                        'digest': 'sha256:green1234...',
                        'healthy': True,
                        'missing': [],
                        'artifacts': {
                            'sig': {'exists': True},
                            'src': {'exists': True},
                            'att': {'exists': True},
                            'sbom': {'exists': True},
                        },
                    },
                },
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'ARTIFACT HEALTH VERIFICATION' in user_prompt
        assert '.sig=PRESENT' in user_prompt
        assert '.src=MISSING' in user_prompt
        assert 'INCOMPLETE' in user_prompt
        assert 'HEALTHY' in user_prompt

    def test_artifact_health_timeout_build_pattern(self):
        """Lines 1074-1079: Diagnostic for .sig present but .src missing."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'artifact_health': {
                    'violation_sha': {
                        'digest': 'sha256:bad...',
                        'healthy': False,
                        'missing': ['src'],
                        'artifacts': {
                            'sig': {'exists': True},
                            'src': {'exists': False},
                            'att': {'exists': True},
                            'sbom': {'exists': True},
                        },
                    },
                    'green_build_sha': {
                        'digest': 'sha256:good...',
                        'healthy': True,
                        'missing': [],
                        'artifacts': {
                            'sig': {'exists': True},
                            'src': {'exists': True},
                            'att': {'exists': True},
                            'sbom': {'exists': True},
                        },
                    },
                },
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'TIMEOUT BUILD pattern' in user_prompt
        assert 'Green build has .src' in user_prompt
        assert 'REBUILD will fix this' in user_prompt

    def test_artifact_health_green_also_lacks_src(self):
        """Lines 1080-1081: Warning when green build also lacks .src."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'artifact_health': {
                    'violation_sha': {
                        'digest': 'sha256:bad...',
                        'healthy': False,
                        'missing': ['src'],
                        'artifacts': {
                            'sig': {'exists': True},
                            'src': {'exists': False},
                            'att': {'exists': True},
                            'sbom': {'exists': True},
                        },
                    },
                    'green_build_sha': {
                        'digest': 'sha256:green...',
                        'healthy': False,
                        'missing': ['src'],
                        'artifacts': {
                            'sig': {'exists': True},
                            'src': {'exists': False},
                            'att': {'exists': True},
                            'sbom': {'exists': True},
                        },
                    },
                },
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'Green build also lacks .src' in user_prompt
        assert 'rebuild may NOT fix this' in user_prompt

    def test_artifact_health_violation_src_missing_green_present(self):
        """Lines 1082-1084: Diagnostic for violation lacks src but green has it."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'artifact_health': {
                    'violation_sha': {
                        'digest': 'sha256:bad...',
                        'healthy': False,
                        'missing': ['src'],
                        'artifacts': {
                            'sig': {'exists': False},  # sig NOT present (different from timeout pattern)
                            'src': {'exists': False},
                            'att': {'exists': True},
                            'sbom': {'exists': True},
                        },
                    },
                    'green_build_sha': {
                        'digest': 'sha256:good...',
                        'healthy': True,
                        'missing': [],
                        'artifacts': {
                            'sig': {'exists': True},
                            'src': {'exists': True},
                            'att': {'exists': True},
                            'sbom': {'exists': True},
                        },
                    },
                },
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'Violation SHA has no source image but green build does' in user_prompt
        assert 'fix is a REBUILD' in user_prompt

    def test_artifact_health_both_lack_src(self):
        """Lines 1085-1087: Both SHAs lack source images -> may not fix."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'artifact_health': {
                    'violation_sha': {
                        'digest': 'sha256:bad...',
                        'healthy': False,
                        'missing': ['src'],
                        'artifacts': {
                            'sig': {'exists': False},
                            'src': {'exists': False},
                            'att': {'exists': True},
                            'sbom': {'exists': True},
                        },
                    },
                    'green_build_sha': {
                        'digest': 'sha256:green...',
                        'healthy': False,
                        'missing': ['src'],
                        'artifacts': {
                            'sig': {'exists': True},
                            'src': {'exists': False},
                            'att': {'exists': True},
                            'sbom': {'exists': True},
                        },
                    },
                },
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'BOTH SHAs lack source images' in user_prompt
        assert 'source-build task may be misconfigured' in user_prompt

    def test_artifact_health_unknown_status(self):
        """Line 1056: Unknown artifact existence renders as UNKNOWN."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'artifact_health': {
                    'violation_sha': {
                        'digest': 'sha256:bad...',
                        'healthy': False,
                        'missing': [],
                        'artifacts': {
                            'sig': {'exists': None},
                            'src': {'exists': None},
                            'att': {'exists': None},
                            'sbom': {'exists': None},
                        },
                    },
                },
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert '.sig=UNKNOWN' in user_prompt
        assert '.src=UNKNOWN' in user_prompt

    def test_failed_task_in_prompt(self):
        """Lines 1101, 1103: Failed task name and logs are displayed."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'failed_task': 'source-build',
                'failed_task_logs': 'Error: timeout in source-build task',
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'Failed task: source-build' in user_prompt
        assert 'Error: timeout in source-build task' in user_prompt

    def test_db_history_displayed_when_no_builds_with_sha(self):
        """Lines 1108-1113: DB build history shown when no builds_with_sha."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'build_history': [
                    {'pipelinerun': 'pr-1', 'status': 'Failed', 'created': '2026-01-01', 'error': 'build failed'},
                    {'pipelinerun': 'pr-2', 'status': 'Completed', 'created': '2026-01-02', 'error': ''},
                ],
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'Build history (from DB)' in user_prompt
        assert 'pr-1' in user_prompt
        assert 'build failed' in user_prompt

    def test_nudge_pr_url_construction(self):
        """Line 1122: PR URL is constructed when html_url is empty."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'nudge_prs': [
                    {'number': 99, 'title': 'Nudge comp SHA', 'state': 'open', 'html_url': ''},
                ],
            },
        }

        with patch('analyzers.release_failure_analyzer.GITHUB_OPERATOR_OWNER', 'test-owner'), \
             patch('analyzers.release_failure_analyzer.GITHUB_OPERATOR_REPO', 'test-repo'):
            _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'https://github.com/test-owner/test-repo/pull/99' in user_prompt

    def test_post_stage_readiness_checks(self):
        """Lines 1175-1181: Post-stage readiness checks are displayed."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['readiness_checks'] = [
            {'phase': 'pre-release', 'status': 'PASS', 'name': 'Build Failures', 'detail': '0 failures', 'fix': ''},
            {'phase': 'post-stage', 'status': 'FAIL', 'name': 'Conforma Violations', 'detail': '3 violations', 'fix': 'Fix violations'},
            {'phase': 'post-stage', 'status': 'WARN', 'name': 'Stale Components', 'detail': '2 stale', 'fix': 'Rebuild stale components'},
        ]

        _, user_prompt = analyzer.build_analysis_prompt(context)
        assert '### Post-Stage' in user_prompt
        assert '[FAIL] Conforma Violations' in user_prompt
        assert 'Fix: Fix violations' in user_prompt
        assert '[WARN] Stale Components' in user_prompt
        assert 'Fix: Rebuild stale components' in user_prompt

    def test_build_config_repo_url(self):
        """Line 1209: Build-Config repo URL is included when env vars set."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()

        with patch('analyzers.release_failure_analyzer.GITHUB_BUILD_CONFIG_OWNER', 'config-owner'), \
             patch('analyzers.release_failure_analyzer.GITHUB_BUILD_CONFIG_REPO', 'config-repo'):
            _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'https://github.com/config-owner/config-repo' in user_prompt

    def test_component_specific_nudge_pr_urls(self):
        """Line 1225: Component-specific nudge PR URLs in reference section."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()
        context['violation_enrichment'] = {
            'comp-v3-5-ea-2': {
                'nudge_prs': [
                    {'number': 77, 'title': 'Nudge', 'state': 'open', 'html_url': ''},
                ],
            },
        }

        with patch('analyzers.release_failure_analyzer.GITHUB_OPERATOR_OWNER', 'owner'), \
             patch('analyzers.release_failure_analyzer.GITHUB_OPERATOR_REPO', 'repo'):
            _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'Nudge PR #77 (comp-v3-5-ea-2)' in user_prompt
        assert 'https://github.com/owner/repo/pull/77' in user_prompt

    def test_knowledge_graph_context_included(self):
        """Lines 1237-1240: Knowledge graph context is included when available."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()

        mock_provider = MagicMock()
        mock_provider.release_context.return_value = '\n## Knowledge Graph\nRelevant patterns found.'

        with patch('knowledge.graph_provider.get_provider', return_value=mock_provider):
            _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'Knowledge Graph' in user_prompt
        assert 'Relevant patterns found' in user_prompt

    def test_knowledge_graph_exception_swallowed(self):
        """Line 1237-1243: Exception from knowledge graph is silently handled."""
        analyzer = _build_analyzer()
        analyzer.pattern_repo.get_all.return_value = []
        context = self._base_context()

        with patch('knowledge.graph_provider.get_provider', side_effect=RuntimeError('graph unavailable')):
            _, user_prompt = analyzer.build_analysis_prompt(context)
        assert 'record_release_analysis' in user_prompt


# ---------------------------------------------------------------------------
# Lines 1451-1456, 1462-1465: analyze_release - artifact health embedding
# ---------------------------------------------------------------------------
class TestAnalyzeReleaseArtifactHealth:
    """Test artifact health embedding in analysis JSON."""

    def test_artifact_health_embedded_in_tool_calls(self):
        """Lines 1451-1465: Artifact health summaries are embedded in tool_calls."""
        analyzer = _build_analyzer()
        analyzer._get_existing_analysis = MagicMock(return_value=None)
        analyzer._is_release_failed = MagicMock(return_value=True)

        context = {
            'release_name': 'test-release',
            'application': 'rhoai-v3-5-ea-2',
            'snapshot': 'snap-1',
            'release_plan': 'plan',
            'target': 'prod',
            'type': 'components',
            'created_at': '2026-01-01',
            'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
            'pipeline': {'ref': '', 'failed_task': ''},
            'violation_enrichment': {
                'comp-a-v3-5-ea-2': {
                    'artifact_health': {
                        'violation_sha': {
                            'healthy': False,
                            'missing': ['src'],
                            'digest': 'sha256:abc...',
                        },
                        'green_build_sha': {
                            'healthy': True,
                            'missing': [],
                            'digest': 'sha256:def...',
                        },
                    },
                },
            },
        }
        analyzer.collect_context = MagicMock(return_value=context)

        response = _make_llm_response()
        analyzer.llm.create_message.return_value = response
        analyzer.llm.model_name.return_value = 'claude-sonnet-4-5-20250929'
        analyzer.pattern_repo.find_or_create.return_value = {'id': 1}
        analyzer.pattern_repo.get_all.return_value = []
        analyzer.ai_repo.insert_release_analysis.return_value = 42

        result = analyzer.analyze_release('test-release')

        # Verify that tool_calls were modified to include artifact_health
        call_args = analyzer.ai_repo.insert_release_analysis.call_args
        analysis_json = call_args.kwargs.get('analysis_json') or call_args[1].get('analysis_json')
        # analysis_json should be a dict with tool_calls list
        assert isinstance(analysis_json, dict), "analysis_json should be a dict"
        tool_calls = analysis_json.get('tool_calls', [])
        # The tool_calls list should have artifact_health embedded
        found_artifact_health = False
        for tc in tool_calls:
            if isinstance(tc, dict) and tc.get('name') == 'record_release_analysis':
                if 'artifact_health' in tc.get('input', {}):
                    found_artifact_health = True
                    ah = tc['input']['artifact_health']
                    assert 'comp-a-v3-5-ea-2' in ah
                    assert ah['comp-a-v3-5-ea-2']['violation_sha']['missing'] == ['src']
        assert found_artifact_health

    def test_no_artifact_health_when_empty(self):
        """Lines 1461: artifact_summaries not embedded when empty."""
        analyzer = _build_analyzer()
        analyzer._get_existing_analysis = MagicMock(return_value=None)
        analyzer._is_release_failed = MagicMock(return_value=True)

        context = {
            'release_name': 'test-release',
            'application': 'rhoai-v3-5-ea-2',
            'snapshot': 'snap-1',
            'release_plan': 'plan',
            'target': 'prod',
            'type': 'components',
            'created_at': '2026-01-01',
            'conditions': [{'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'err'}],
            'pipeline': {'ref': ''},
            'violation_enrichment': {
                'comp-a': {},  # No artifact_health
            },
        }
        analyzer.collect_context = MagicMock(return_value=context)

        response = _make_llm_response()
        analyzer.llm.create_message.return_value = response
        analyzer.llm.model_name.return_value = 'claude-sonnet-4-5-20250929'
        analyzer.pattern_repo.find_or_create.return_value = {'id': 1}
        analyzer.pattern_repo.get_all.return_value = []
        analyzer.ai_repo.insert_release_analysis.return_value = 42

        result = analyzer.analyze_release('test-release')

        call_args = analyzer.ai_repo.insert_release_analysis.call_args
        analysis_json = call_args.kwargs.get('analysis_json') or call_args[1].get('analysis_json')
        assert isinstance(analysis_json, dict), "analysis_json should be a dict"
        tool_calls = analysis_json.get('tool_calls', [])
        for tc in tool_calls:
            if isinstance(tc, dict) and tc.get('name') == 'record_release_analysis':
                assert 'artifact_health' not in tc.get('input', {})


# ---------------------------------------------------------------------------
# Line 1545: _annotate_fix_status - nightly overwrite warning
# ---------------------------------------------------------------------------
class TestAnnotateFixStatusNightlyWarning:
    """Test the nightly overwrite warning in _annotate_fix_status."""

    def test_merged_nudge_pr_with_bad_source_image_warns(self):
        """Line 1545: Warning appended when merged PR + bad source image."""
        analyzer = _build_analyzer()
        analysis = {
            'recommended_fix': 'Original fix',
            'failure_category': 'build_artifact_missing',
        }
        context = {
            'violation_enrichment': {
                'comp-a': {
                    'nudge_prs': [
                        {'number': 10, 'title': 'Nudge comp-a', 'state': 'closed', 'html_url': ''},
                    ],
                    'source_image_status': {
                        'violation_sha': {'exists': False},
                    },
                },
            },
        }

        result = analyzer._annotate_fix_status(analysis, context)
        assert 'Nudge PR #10' in result['recommended_fix']
        assert 'already be propagating' in result['recommended_fix']
        assert 'WARNING' in result['recommended_fix']
        assert 'nightly operator-processor may overwrite' in result['recommended_fix']

    def test_merged_nudge_pr_without_bad_source_no_warning(self):
        """Line 1544: No warning when source image status is not False."""
        analyzer = _build_analyzer()
        analysis = {
            'recommended_fix': 'Original fix',
            'failure_category': 'build_artifact_missing',
        }
        context = {
            'violation_enrichment': {
                'comp-a': {
                    'nudge_prs': [
                        {'number': 10, 'title': 'Nudge comp-a', 'state': 'closed', 'html_url': ''},
                    ],
                    'source_image_status': {
                        'violation_sha': {'exists': True},
                    },
                },
            },
        }

        result = analyzer._annotate_fix_status(analysis, context)
        assert 'Nudge PR #10' in result['recommended_fix']
        assert 'WARNING' not in result['recommended_fix']

    def test_open_nudge_pr_note(self):
        """Line 1551: Open nudge PR gets 'fix is in progress' note."""
        analyzer = _build_analyzer()
        analysis = {
            'recommended_fix': 'Original',
            'failure_category': 'build_artifact_missing',
        }
        context = {
            'violation_enrichment': {
                'comp-a': {
                    'nudge_prs': [
                        {'number': 20, 'title': 'Nudge', 'state': 'open', 'html_url': ''},
                    ],
                },
            },
        }

        result = analyzer._annotate_fix_status(analysis, context)
        assert 'fix is in progress' in result['recommended_fix']

    def test_latest_build_succeeded_note(self):
        """Lines 1556-1561: Latest green build is noted."""
        analyzer = _build_analyzer()
        analysis = {
            'recommended_fix': 'Original',
            'failure_category': 'build_artifact_missing',
        }
        context = {
            'violation_enrichment': {
                'comp-a': {
                    'builds_with_sha': [
                        {'name': 'build-green', 'status': 'Completed', 'image_sha': 'sha256:goodgoodgoodgoodgoodgoodgood'},
                    ],
                },
            },
        }

        result = analyzer._annotate_fix_status(analysis, context)
        assert 'latest build' in result['recommended_fix']
        assert 'green' in result['recommended_fix']
        assert 'automated nudge should pick it up' in result['recommended_fix']
