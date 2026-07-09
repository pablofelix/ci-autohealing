"""Comprehensive tests for ReleaseFailureAnalyzer.

Covers constructor, every public/private method, and the full orchestration
flow. All external dependencies are mocked to avoid real DB/LLM/HTTP calls.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_config(llm=True):
    """Build a mock config object matching the real AppConfig shape."""
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
    """Build a mock LLMResponse matching the real dataclass."""
    if tool_input is None:
        tool_input = {
            'root_cause': 'Source image missing for odh-dashboard because build timed out before source-build task',
            'failure_category': 'build_artifact_missing',
            'confidence_score': 0.85,
            'recommended_fix': 'Trigger a rebuild of odh-dashboard to regenerate source container image',
            'recommended_files': ['bundle/additional-images-patch.yaml'],
            'fix_action_type': 'rebuild',
            'can_auto_fix': False,
            'requires_human_review': True,
            'affected_images': ['quay.io/rhoai/odh-dashboard-rhel9@sha256:abc123'],
            'owner_team': 'RHOAI',
            'evidence_references': [
                {'type': 'log', 'description': 'step-detailed-report shows source_image violation'}
            ],
            'source_transparency': {
                'sources_consulted': ['release PipelineRun logs'],
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
    """Construct a ReleaseFailureAnalyzer with all deps mocked."""
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
    # Replace real repo objects with mocks for test control
    analyzer.pattern_repo = MagicMock()
    analyzer.build_repo = MagicMock()
    analyzer.conforma_repo = MagicMock()
    analyzer.triage_repo = MagicMock()
    return analyzer


# ---------------------------------------------------------------------------
# TestReleaseFailureAnalyzerConstructor
# ---------------------------------------------------------------------------
class TestReleaseFailureAnalyzerConstructor:
    """Tests for __init__ method."""

    def test_constructor_with_all_deps(self):
        """All mocked deps are stored, no real constructors called."""
        llm = MagicMock()
        db = MagicMock()
        ai_repo = MagicMock()
        langfuse = MagicMock()
        config = _make_config()

        with patch('analyzers.release_failure_analyzer.load_prompt', return_value='sp'):
            from analyzers.release_failure_analyzer import ReleaseFailureAnalyzer
            a = ReleaseFailureAnalyzer(
                config=config, db=db, ai_repo=ai_repo,
                llm=llm, langfuse=langfuse,
            )
        assert a.llm is llm
        assert a.db is db
        assert a.ai_repo is ai_repo
        assert a.langfuse is langfuse
        assert a.kubearchive is None
        assert a.github is None
        assert a.gitlab is None

    @patch('analyzers.release_failure_analyzer.load_prompt', return_value='sp')
    @patch('analyzers.release_failure_analyzer.LangfuseTracker')
    @patch('analyzers.release_failure_analyzer.create_llm_provider')
    @patch('analyzers.release_failure_analyzer.ErrorPatternRepository')
    @patch('analyzers.release_failure_analyzer.AIAnalysisRepository')
    @patch('analyzers.release_failure_analyzer.BuildFailureRepository')
    @patch('analyzers.release_failure_analyzer.ConformaRepository')
    @patch('analyzers.release_failure_analyzer.TriageRepository')
    @patch('analyzers.release_failure_analyzer.DatabaseConnection')
    def test_constructor_creates_defaults(
        self, mock_db_cls, mock_triage_cls, mock_conforma_cls,
        mock_build_cls, mock_ai_cls, mock_err_cls,
        mock_llm_factory, mock_lf_cls, mock_lp
    ):
        """When deps are None, real constructors are called."""
        config = _make_config()
        mock_db_instance = MagicMock()
        mock_db_cls.return_value = mock_db_instance

        with patch('analyzers.release_failure_analyzer.load_prompt', return_value='sp'):
            from analyzers.release_failure_analyzer import ReleaseFailureAnalyzer
            a = ReleaseFailureAnalyzer(config=config)

        mock_db_cls.assert_called_once_with(config.db)
        mock_ai_cls.assert_called_once_with(mock_db_instance)
        mock_llm_factory.assert_called_once_with(config.llm)

    def test_constructor_raises_without_llm_config(self):
        """ValueError when config.llm is falsy and no llm passed."""
        config = _make_config(llm=False)
        with patch('analyzers.release_failure_analyzer.load_prompt', return_value='sp'):
            from analyzers.release_failure_analyzer import ReleaseFailureAnalyzer
            with pytest.raises(ValueError, match="LLM not configured"):
                ReleaseFailureAnalyzer(
                    config=config, db=MagicMock(), ai_repo=MagicMock(),
                )

    @patch('analyzers.release_failure_analyzer.load_prompt', return_value='sp')
    @patch('analyzers.release_failure_analyzer.create_llm_provider')
    def test_constructor_creates_llm_from_config(self, mock_llm_factory, mock_lp):
        """When llm=None but config.llm exists, LLM provider is created."""
        config = _make_config()
        mock_llm_factory.return_value = MagicMock()

        from analyzers.release_failure_analyzer import ReleaseFailureAnalyzer
        a = ReleaseFailureAnalyzer(
            config=config, db=MagicMock(), ai_repo=MagicMock(),
            langfuse=MagicMock(),
        )
        mock_llm_factory.assert_called_once_with(config.llm)
        assert a.llm is mock_llm_factory.return_value

    @patch('analyzers.release_failure_analyzer.load_prompt', return_value='sp')
    @patch('analyzers.release_failure_analyzer.LangfuseTracker')
    def test_constructor_creates_langfuse_when_none(self, mock_lf_cls, mock_lp):
        """When langfuse=None, LangfuseTracker is created."""
        config = _make_config()
        from analyzers.release_failure_analyzer import ReleaseFailureAnalyzer
        with patch.dict(os.environ, {'LANGFUSE_PUBLIC_KEY': 'pk-test'}, clear=False):
            a = ReleaseFailureAnalyzer(
                config=config, db=MagicMock(), ai_repo=MagicMock(),
                llm=MagicMock(),
            )
        mock_lf_cls.assert_called_once_with(enabled=True)


# ---------------------------------------------------------------------------
# TestExtractViolations
# ---------------------------------------------------------------------------
class TestExtractViolations:
    """Tests for _extract_violations static method."""

    def test_no_violations(self):
        a = _build_analyzer()
        result = a._extract_violations("All checks passed\nNo issues found")
        assert result == []

    def test_single_violation(self):
        a = _build_analyzer()
        report = (
            "ImageRef: quay.io/rhoai/odh-dashboard-rhel9@sha256:abc123\n"
            "[Violation] source_image.exists\n"
            "Reason: Source image not found for digest sha256:abc123\n"
        )
        result = a._extract_violations(report)
        assert len(result) == 1
        assert result[0]['image_ref'] == 'quay.io/rhoai/odh-dashboard-rhel9@sha256:abc123'
        assert result[0]['rule'] == 'source_image.exists'
        assert 'Source image not found' in result[0]['reason']

    def test_multiple_violations(self):
        a = _build_analyzer()
        report = (
            "ImageRef: quay.io/rhoai/img-a@sha256:aaa\n"
            "[Violation] source_image.exists\n"
            "Reason: Missing source\n"
            "ImageRef: quay.io/rhoai/img-b@sha256:bbb\n"
            "[Violation] deprecated_image_check\n"
            "Reason: Image uses deprecated base\n"
        )
        result = a._extract_violations(report)
        assert len(result) == 2
        assert result[0]['image_ref'] == 'quay.io/rhoai/img-a@sha256:aaa'
        assert result[1]['rule'] == 'deprecated_image_check'

    def test_violations_with_reason_lines(self):
        a = _build_analyzer()
        report = (
            "ImageRef: quay.io/rhoai/test@sha256:def\n"
            "[Violation] rpm_sig_check\n"
            "Reason: RPM signature not valid\n"
        )
        result = a._extract_violations(report)
        assert len(result) == 1
        assert result[0]['reason'] == 'RPM signature not valid'

    def test_missing_image_ref_uses_empty_string(self):
        a = _build_analyzer()
        report = "[Violation] some.rule.check\n"
        result = a._extract_violations(report)
        assert len(result) == 1
        assert result[0]['image_ref'] == ''
        assert result[0]['rule'] == 'some.rule.check'

    def test_reason_attaches_to_last_violation(self):
        """Reason lines attach to the most recent violation only."""
        a = _build_analyzer()
        report = (
            "ImageRef: quay.io/test@sha256:aaa\n"
            "[Violation] rule_one\n"
            "[Violation] rule_two\n"
            "Reason: This reason goes to rule_two\n"
        )
        result = a._extract_violations(report)
        assert len(result) == 2
        assert 'reason' not in result[0]
        assert result[1]['reason'] == 'This reason goes to rule_two'

    def test_reason_before_any_violation_is_ignored(self):
        """Reason line without a preceding violation is ignored."""
        a = _build_analyzer()
        report = "Reason: orphan reason\nsome text\n"
        result = a._extract_violations(report)
        assert result == []


# ---------------------------------------------------------------------------
# TestImageRefToComponent
# ---------------------------------------------------------------------------
class TestImageRefToComponent:
    """Tests for _image_ref_to_component static method."""

    def test_normal_mapping(self):
        a = _build_analyzer()
        ref = 'quay.io/rhoai/odh-dashboard-rhel9@sha256:abc123'
        result = a._image_ref_to_component(ref, 'v3-5-ea-2')
        assert result == 'odh-dashboard-v3-5-ea-2'

    def test_empty_ref(self):
        a = _build_analyzer()
        assert a._image_ref_to_component('') == ''

    def test_no_suffix(self):
        a = _build_analyzer()
        ref = 'quay.io/rhoai/odh-dashboard-rhel9@sha256:abc'
        result = a._image_ref_to_component(ref)
        assert result == 'odh-dashboard'

    def test_strips_rhel_suffix(self):
        a = _build_analyzer()
        ref = 'quay.io/rhoai/component-rhel9@sha256:abc'
        result = a._image_ref_to_component(ref, 'v3-5')
        assert result == 'component-v3-5'

    def test_strips_rhel8_suffix(self):
        a = _build_analyzer()
        ref = 'quay.io/rhoai/component-rhel8@sha256:abc'
        result = a._image_ref_to_component(ref)
        assert result == 'component'


# ---------------------------------------------------------------------------
# TestApplicationToSuffix
# ---------------------------------------------------------------------------
class TestApplicationToSuffix:
    """Tests for _application_to_suffix static method."""

    def test_normal_application(self):
        a = _build_analyzer()
        assert a._application_to_suffix('rhoai-v3-5-ea-2') == 'v3-5-ea-2'

    def test_no_match(self):
        a = _build_analyzer()
        assert a._application_to_suffix('nohyphen') == ''


# ---------------------------------------------------------------------------
# TestLazyClientInit
# ---------------------------------------------------------------------------
class TestLazyClientInit:
    """Tests for _get_kubearchive, _get_github, _get_gitlab, _get_tekton_results."""

    @patch('analyzers.release_failure_analyzer.KubeArchiveClient')
    def test_get_kubearchive_creates_on_first_call(self, mock_ka_cls):
        a = _build_analyzer()
        assert a.kubearchive is None
        ka = a._get_kubearchive()
        mock_ka_cls.assert_called_once()
        assert ka is mock_ka_cls.return_value

    @patch('analyzers.release_failure_analyzer.KubeArchiveClient')
    def test_get_kubearchive_caches(self, mock_ka_cls):
        a = _build_analyzer()
        ka1 = a._get_kubearchive()
        ka2 = a._get_kubearchive()
        assert ka1 is ka2
        mock_ka_cls.assert_called_once()

    @patch('analyzers.release_failure_analyzer.GitHubClient')
    def test_get_github_creates_on_first_call(self, mock_gh_cls):
        a = _build_analyzer()
        assert a.github is None
        gh = a._get_github()
        mock_gh_cls.assert_called_once_with(token='gh-tok')
        assert gh is mock_gh_cls.return_value

    @patch('analyzers.release_failure_analyzer.GitLabClient')
    def test_get_gitlab_creates_on_first_call(self, mock_gl_cls):
        a = _build_analyzer()
        assert a.gitlab is None
        gl = a._get_gitlab()
        mock_gl_cls.assert_called_once()
        assert gl is mock_gl_cls.return_value

    @patch('analyzers.release_failure_analyzer.TektonResultsClient')
    def test_get_tekton_results_creates_on_first_call(self, mock_tr_cls):
        a = _build_analyzer()
        assert a._tekton_results is None
        tr = a._get_tekton_results('my-namespace')
        mock_tr_cls.assert_called_once_with(namespace='my-namespace')
        assert tr is mock_tr_cls.return_value


# ---------------------------------------------------------------------------
# TestK8sGetJson
# ---------------------------------------------------------------------------
class TestK8sGetJson:
    """Tests for _k8s_get_json."""

    @patch('analyzers.release_failure_analyzer.client')
    @patch('analyzers.release_failure_analyzer._ensure_k8s_config')
    def test_success(self, mock_ensure, mock_k8s_client):
        a = _build_analyzer()
        mock_api = MagicMock()
        mock_k8s_client.CustomObjectsApi.return_value = mock_api
        mock_api.get_namespaced_custom_object.return_value = {'metadata': {'name': 'test'}}

        result = a._k8s_get_json('release', 'release-abc')
        assert result == {'metadata': {'name': 'test'}}
        mock_api.get_namespaced_custom_object.assert_called_once_with(
            group='appstudio.redhat.com', version='v1alpha1',
            namespace='rhoai-tenant', plural='releases', name='release-abc',
            _request_timeout=15,
        )

    @patch('analyzers.release_failure_analyzer.client')
    @patch('analyzers.release_failure_analyzer._ensure_k8s_config')
    def test_exception_returns_none(self, mock_ensure, mock_k8s_client):
        a = _build_analyzer()
        mock_api = MagicMock()
        mock_k8s_client.CustomObjectsApi.return_value = mock_api
        mock_api.get_namespaced_custom_object.side_effect = Exception("not found")

        result = a._k8s_get_json('release', 'nonexistent')
        assert result is None

    def test_unknown_resource_returns_none(self):
        a = _build_analyzer()
        result = a._k8s_get_json('unknown_resource', 'some-name')
        assert result is None


# ---------------------------------------------------------------------------
# TestDeriveBranch
# ---------------------------------------------------------------------------
class TestDeriveBranch:
    """Tests for _derive_branch."""

    def test_standard_release(self):
        a = _build_analyzer()
        assert a._derive_branch('rhoai-v3-4') == 'rhoai-3.4'

    def test_ea_release(self):
        a = _build_analyzer()
        assert a._derive_branch('rhoai-v3-5-ea-2') == 'rhoai-3.5-ea.2'

    def test_no_match_returns_main(self):
        a = _build_analyzer()
        assert a._derive_branch('some-other-app') == 'main'

    def test_different_product_name(self):
        a = _build_analyzer()
        assert a._derive_branch('rhaii-v1-2') == 'rhaii-1.2'


# ---------------------------------------------------------------------------
# TestDeriveRpaFilename
# ---------------------------------------------------------------------------
class TestDeriveRpaFilename:
    """Tests for _derive_rpa_filename."""

    def test_normal(self):
        a = _build_analyzer()
        result = a._derive_rpa_filename('rhoai-onprem-v3-4-components-prod')
        assert result == 'rhoai-onprem-v3-4-components-prod.yaml'


# ---------------------------------------------------------------------------
# TestCollectContext
# ---------------------------------------------------------------------------
class TestCollectContext:
    """Tests for collect_context."""

    def test_release_cr_not_found(self):
        a = _build_analyzer()
        with patch.object(a, '_k8s_get_json', return_value=None):
            ctx = a.collect_context('release-missing')
        assert ctx['release_name'] == 'release-missing'
        assert 'conditions' not in ctx

    def test_minimal_context_release_found(self):
        """Release CR found, but no snapshot, no pipeline, no logs."""
        a = _build_analyzer()
        release_cr = {
            'metadata': {'creationTimestamp': '2025-06-01T00:00:00Z'},
            'spec': {
                'snapshot': '',
                'releasePlan': 'rhoai-onprem-v3-5-ea-2-components-prod',
            },
            'status': {
                'conditions': [
                    {'type': 'Released', 'status': 'False',
                     'reason': 'Failed', 'message': 'EC policy violation'}
                ],
                'managedProcessing': {},
            },
        }

        def mock_k8s(resource, name, namespace=None):
            if resource == 'release':
                return release_cr
            return None

        with patch.object(a, '_k8s_get_json', side_effect=mock_k8s):
            with patch.object(a, '_enrich_violation_context'):
                with patch.object(a, '_enrich_application_context'):
                    with patch.object(a, '_enrich_with_component_prs'):
                        with patch.object(a, '_enrich_with_readiness_checks'):
                            with patch.object(a, '_get_gitlab', side_effect=Exception("skip")):
                                ctx = a.collect_context('release-test')

        assert len(ctx['conditions']) == 1
        assert ctx['target'] == 'prod'
        assert ctx['type'] == 'components'

    def test_full_context_with_logs_and_snapshot(self):
        """Full pipeline: Release CR, pipeline logs, snapshot, violations."""
        a = _build_analyzer()
        release_cr = {
            'metadata': {'creationTimestamp': '2025-06-01T00:00:00Z'},
            'spec': {
                'snapshot': 'rhoai-v3-5-ea-2-snap-abc',
                'releasePlan': 'rhoai-fbc-v3-5-ea-2-prod',
            },
            'status': {
                'conditions': [
                    {'type': 'Released', 'status': 'False',
                     'reason': 'Failed',
                     'message': 'task verify-enterprise-contract failed'}
                ],
                'managedProcessing': {
                    'pipelineRun': 'ns/pipeline-run-123',
                },
            },
        }
        snapshot_cr = {
            'spec': {
                'components': [
                    {'name': 'comp-a', 'containerImage': 'quay.io/rhoai/comp-a@sha256:aaa'},
                ]
            }
        }

        def mock_k8s(resource, name, namespace=None):
            if resource == 'release':
                return release_cr
            if resource == 'snapshot':
                return snapshot_cr
            return None

        mock_ka = MagicMock()
        mock_ka.get_taskrun.return_value = None
        mock_ka.get_pod_logs.return_value = None

        with patch.object(a, '_k8s_get_json', side_effect=mock_k8s):
            with patch.object(a, '_get_kubearchive', return_value=mock_ka):
                with patch.object(a, '_enrich_violation_context'):
                    with patch.object(a, '_enrich_application_context'):
                        with patch.object(a, '_enrich_with_component_prs'):
                            with patch.object(a, '_enrich_with_readiness_checks'):
                                with patch.object(a, '_get_gitlab', side_effect=Exception("skip")):
                                    with patch.object(a, '_get_github', side_effect=Exception("skip")):
                                        with patch('clients.registry_client.RegistryClient', side_effect=Exception("skip")):
                                            ctx = a.collect_context('release-full')

        assert ctx['snapshot'] == 'rhoai-v3-5-ea-2-snap-abc'
        assert ctx['type'] == 'fbc'
        assert ctx['pipeline']['ref'] == 'ns/pipeline-run-123'
        assert len(ctx['snapshot_components']) == 1

    def test_kubearchive_unavailable_logs_warning(self):
        """When KubeArchive raises, we log a warning and continue."""
        a = _build_analyzer()
        release_cr = {
            'metadata': {'creationTimestamp': '2025-06-01T00:00:00Z'},
            'spec': {'snapshot': '', 'releasePlan': 'rp-prod'},
            'status': {
                'conditions': [
                    {'type': 'Released', 'status': 'False',
                     'reason': 'Failed', 'message': 'task ec failed'}
                ],
                'managedProcessing': {'pipelineRun': 'ns/pr-1'},
            },
        }

        def mock_k8s(resource, name, namespace=None):
            if resource == 'release':
                return release_cr
            return None

        with patch.object(a, '_k8s_get_json', side_effect=mock_k8s):
            with patch.object(a, '_get_kubearchive', side_effect=Exception("KA down")):
                with patch.object(a, '_enrich_violation_context'):
                    with patch.object(a, '_enrich_application_context'):
                        with patch.object(a, '_enrich_with_component_prs'):
                            with patch.object(a, '_enrich_with_readiness_checks'):
                                with patch.object(a, '_get_gitlab', side_effect=Exception("skip")):
                                    ctx = a.collect_context('release-ka-fail')

        # Should still have conditions (did not crash)
        assert len(ctx['conditions']) == 1
        assert 'logs' not in ctx

    def test_gitlab_unavailable(self):
        """When GitLab is unavailable, context is still collected without RPA."""
        a = _build_analyzer()
        release_cr = {
            'metadata': {'creationTimestamp': '2025-06-01T00:00:00Z'},
            'spec': {'snapshot': '', 'releasePlan': 'rp-stage'},
            'status': {
                'conditions': [
                    {'type': 'Released', 'status': 'False',
                     'reason': 'Failed', 'message': 'error'}
                ],
                'managedProcessing': {},
            },
        }

        def mock_k8s(resource, name, namespace=None):
            if resource == 'release':
                return release_cr
            return None

        with patch.object(a, '_k8s_get_json', side_effect=mock_k8s):
            with patch.object(a, '_get_gitlab', side_effect=Exception("GitLab down")):
                with patch.object(a, '_enrich_violation_context'):
                    with patch.object(a, '_enrich_application_context'):
                        with patch.object(a, '_enrich_with_component_prs'):
                            with patch.object(a, '_enrich_with_readiness_checks'):
                                ctx = a.collect_context('release-gl-fail')

        assert 'rpa_content' not in ctx
        assert ctx['target'] == 'stage'


# ---------------------------------------------------------------------------
# TestBuildAnalysisPrompt
# ---------------------------------------------------------------------------
class TestBuildAnalysisPrompt:
    """Tests for build_analysis_prompt."""

    def test_returns_tuple_of_prompts(self):
        a = _build_analyzer()
        a.pattern_repo.get_all.return_value = []
        ctx = {
            'release_name': 'r-1', 'application': 'rhoai-v3-5',
            'snapshot': 's-1', 'release_plan': 'rp-1',
            'target': 'prod', 'type': 'components',
            'created_at': '2025-06-01',
        }
        with patch('analyzers.release_failure_analyzer.SYSTEM_PROMPT', 'system prompt'):
            with patch.dict('sys.modules', {'knowledge.graph_context': MagicMock(release_context=MagicMock(return_value=''))}):
                result = a.build_analysis_prompt(ctx)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result[0] == 'system prompt'

    def test_minimal_context(self):
        a = _build_analyzer()
        a.pattern_repo.get_all.return_value = []
        ctx = {
            'release_name': 'r-minimal', 'application': 'app',
            'snapshot': '', 'release_plan': '',
            'target': 'unknown', 'type': 'unknown',
            'created_at': '',
        }
        with patch('analyzers.release_failure_analyzer.SYSTEM_PROMPT', 'sp'):
            with patch.dict('sys.modules', {'knowledge.graph_context': MagicMock(release_context=MagicMock(return_value=''))}):
                _, user_prompt = a.build_analysis_prompt(ctx)
        assert 'r-minimal' in user_prompt
        assert 'record_release_analysis' in user_prompt

    def test_full_context_with_violations_and_logs(self):
        a = _build_analyzer()
        a.pattern_repo.get_all.return_value = []
        ctx = {
            'release_name': 'r-full', 'application': 'rhoai-v3-5-ea-2',
            'snapshot': 'snap-1', 'release_plan': 'rp-prod',
            'target': 'prod', 'type': 'components',
            'created_at': '2025-06-01',
            'conditions': [
                {'type': 'Released', 'status': 'False',
                 'reason': 'Failed', 'message': 'EC violation detected'}
            ],
            'pipeline': {'ref': 'ns/pr-1', 'failed_task': 'verify-enterprise-contract'},
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/img@sha256:abc', 'rule': 'source_image.exists',
                 'reason': 'Missing source'}
            ],
            'logs': {'step-detailed-report': 'violation log content'},
            'snapshot_components': [
                {'name': 'comp-a', 'image': 'quay.io/rhoai/comp-a@sha256:aaa'}
            ],
            'rpa_content': 'rpa yaml content',
            'rpa_source': 'gitlab/path',
            'ec_policies': [{'path': 'policy.yaml', 'content': 'ec yaml'}],
            'bundle_images_content': 'bundle yaml',
            'bundle_images_source': 'GitHub',
            'violation_enrichment': {
                'comp-a-v3-5-ea-2': {
                    'component': 'comp-a-v3-5-ea-2',
                    'violation_sha': 'sha256:abc',
                    'builds_with_sha': [
                        {'created': '2025-01', 'status': 'Succeeded',
                         'image_sha': 'sha256:def', 'name': 'build-1'}
                    ],
                }
            },
            'health_summary': {'working': 50, 'failing': 2},
            'active_build_failures': ['comp-x'],
            'active_conforma_violations': ['comp-y'],
            'nightly_history': [
                {'created_at': '2025-06-01', 'status': 'Failed', 'pr_name': 'nightly-1'}
            ],
            'readiness_checks': [
                {'phase': 'pre-release', 'status': 'FAIL', 'name': 'check-1',
                 'detail': 'stale snapshot', 'fix': 'refresh snapshot'}
            ],
            'triage_items': [
                {'id': 42, 'group_label': 'source-image', 'root_cause': 'timeout build',
                 'jira_key': 'RHOAI-123', 'components': ['comp-a'], 'status': 'active'}
            ],
        }
        with patch('analyzers.release_failure_analyzer.SYSTEM_PROMPT', 'sp'):
            with patch.dict('sys.modules', {'knowledge.graph_context': MagicMock(release_context=MagicMock(return_value=''))}):
                _, user_prompt = a.build_analysis_prompt(ctx)

        assert 'VIOLATIONS FOUND' in user_prompt
        assert 'source_image.exists' in user_prompt
        assert 'Pipeline Logs' in user_prompt
        assert 'Snapshot Components' in user_prompt
        assert 'RPA Mappings' in user_prompt
        assert 'EC Policy' in user_prompt
        assert 'Application Health' in user_prompt
        assert 'Readiness Checks' in user_prompt
        assert 'Triage' in user_prompt
        assert 'Nightly' in user_prompt

    def test_with_nudge_prs(self):
        a = _build_analyzer()
        a.pattern_repo.get_all.return_value = []
        ctx = {
            'release_name': 'r-prs', 'application': 'rhoai-v3-5-ea-2',
            'snapshot': '', 'release_plan': '',
            'target': 'prod', 'type': 'components',
            'created_at': '',
            'violation_enrichment': {
                'comp-a-v3-5-ea-2': {
                    'component': 'comp-a-v3-5-ea-2',
                    'nudge_prs': [
                        {'number': 99, 'title': 'Nudge comp-a', 'state': 'open',
                         'html_url': 'https://github.com/org/repo/pull/99'}
                    ],
                }
            },
        }
        with patch('analyzers.release_failure_analyzer.SYSTEM_PROMPT', 'sp'):
            with patch.dict('sys.modules', {'knowledge.graph_context': MagicMock(release_context=MagicMock(return_value=''))}):
                _, user_prompt = a.build_analysis_prompt(ctx)
        assert 'Nudge PR' in user_prompt
        assert '#99' in user_prompt

    def test_with_pattern_section(self):
        a = _build_analyzer()
        a.pattern_repo.get_all.return_value = [
            {'pattern_name': 'source_image_missing', 'description': 'Build timed out',
             'typical_fix': 'Trigger rebuild'}
        ]
        ctx = {
            'release_name': 'r-pat', 'application': 'app',
            'snapshot': '', 'release_plan': '',
            'target': 'prod', 'type': 'components',
            'created_at': '',
        }
        with patch('analyzers.release_failure_analyzer.SYSTEM_PROMPT', 'sp'):
            with patch.dict('sys.modules', {'knowledge.graph_context': MagicMock(release_context=MagicMock(return_value=''))}):
                _, user_prompt = a.build_analysis_prompt(ctx)
        assert 'Known Release Failure Patterns' in user_prompt
        assert 'source_image_missing' in user_prompt


# ---------------------------------------------------------------------------
# TestFormatPatternSection
# ---------------------------------------------------------------------------
class TestFormatPatternSection:

    def test_no_patterns(self):
        a = _build_analyzer()
        a.pattern_repo.get_all.return_value = []
        result = a._format_pattern_section({})
        assert result == ""

    def test_with_patterns(self):
        a = _build_analyzer()
        a.pattern_repo.get_all.return_value = [
            {'pattern_name': 'build_timeout', 'description': 'Build exceeded limit',
             'typical_fix': 'Increase timeout or rebuild'},
        ]
        result = a._format_pattern_section({})
        assert 'build_timeout' in result
        assert 'Typical fix' in result


# ---------------------------------------------------------------------------
# TestParseAnalysisResponse
# ---------------------------------------------------------------------------
class TestParseAnalysisResponse:
    """Tests for parse_analysis_response."""

    def test_valid_response(self):
        a = _build_analyzer()
        resp = _make_llm_response()
        result = a.parse_analysis_response(resp)
        assert result['failure_category'] == 'build_artifact_missing'
        assert result['confidence_score'] == 0.85
        assert result['can_auto_fix'] is False
        assert result['fix_action_type'] == 'rebuild'

    def test_empty_tool_calls_raises(self):
        a = _build_analyzer()
        resp = MagicMock()
        resp.tool_calls = []
        with pytest.raises(ValueError, match="did not return tool_use"):
            a.parse_analysis_response(resp)

    def test_none_tool_calls_raises(self):
        a = _build_analyzer()
        resp = MagicMock()
        resp.tool_calls = None
        with pytest.raises(ValueError, match="did not return tool_use"):
            a.parse_analysis_response(resp)

    def test_wrong_tool_name_raises(self):
        a = _build_analyzer()
        resp = MagicMock()
        resp.tool_calls = [{'name': 'wrong_tool', 'input': {}}]
        with pytest.raises(ValueError, match="did not call record_release_analysis"):
            a.parse_analysis_response(resp)

    def test_invalid_data_falls_back_to_permissive_dict(self):
        """Invalid data (e.g., too short root_cause) returns fallback."""
        a = _build_analyzer()
        resp = _make_llm_response(tool_input={
            'root_cause': 'short',  # < 10 chars, triggers ValidationError
            'failure_category': 'build_artifact_missing',
            'confidence_score': 0.9,
            'recommended_fix': 'do something about this issue now',
            'can_auto_fix': True,
        })
        result = a.parse_analysis_response(resp)
        assert result['confidence_score'] == 0.0
        assert result['requires_human_review'] is True
        assert result['can_auto_fix'] is False
        assert result['failure_category'] == 'infrastructure'

    def test_multiple_tool_calls_picks_correct_one(self):
        a = _build_analyzer()
        resp = MagicMock()
        resp.tool_calls = [
            {'name': 'other_tool', 'input': {'data': 'x'}},
            {'name': 'record_release_analysis', 'input': {
                'root_cause': 'Source image missing for component because build timed out',
                'failure_category': 'unmapped_image',
                'confidence_score': 0.7,
                'recommended_fix': 'Add the image to the RPA mapping file and retry',
                'can_auto_fix': False,
            }},
        ]
        result = a.parse_analysis_response(resp)
        assert result['failure_category'] == 'unmapped_image'


# ---------------------------------------------------------------------------
# TestApplyConfidenceCap
# ---------------------------------------------------------------------------
class TestApplyConfidenceCap:
    """Tests for _apply_confidence_cap."""

    def test_no_penalties(self):
        a = _build_analyzer()
        analysis = {'confidence_score': 0.95}
        context = {
            'violation_enrichment': {'comp': {}},
            'operator_nudging_content': 'yaml',
            'health_summary': {'working': 10},
            'triage_items': [{'id': 1}],
            'violations_summary': [{'rule': 'x'}],
        }
        result = a._apply_confidence_cap(analysis, context)
        assert result['confidence_score'] == 0.95

    def test_missing_enrichment_caps_confidence(self):
        a = _build_analyzer()
        analysis = {'confidence_score': 0.95,
                     'source_transparency': {'limitations': []}}
        context = {}  # Missing everything
        result = a._apply_confidence_cap(analysis, context)
        # Total penalty: 0.10 + 0.10 + 0.05 + 0.05 + 0.15 = 0.45
        # max_allowed = max(0.55, 1.0 - 0.45) = 0.55
        assert result['confidence_score'] == 0.55

    def test_already_below_cap_stays_unchanged(self):
        a = _build_analyzer()
        analysis = {'confidence_score': 0.40}
        context = {}  # Missing everything
        result = a._apply_confidence_cap(analysis, context)
        assert result['confidence_score'] == 0.40

    def test_source_transparency_limitations_updated(self):
        a = _build_analyzer()
        analysis = {
            'confidence_score': 0.95,
            'source_transparency': {
                'limitations': ['existing limitation'],
            },
        }
        context = {}  # Missing everything -> caps confidence
        result = a._apply_confidence_cap(analysis, context)
        limitations = result['source_transparency']['limitations']
        assert len(limitations) == 2
        assert 'Confidence capped' in limitations[1]

    def test_partial_data_smaller_cap(self):
        """Only some data missing results in smaller penalty."""
        a = _build_analyzer()
        analysis = {'confidence_score': 0.95}
        context = {
            'violation_enrichment': {'comp': {}},  # present
            'operator_nudging_content': 'yaml',    # present
            # health_summary missing: 0.05
            # triage_items missing: 0.05
            'violations_summary': [{'rule': 'x'}], # present
        }
        result = a._apply_confidence_cap(analysis, context)
        # max_allowed = max(0.55, 1.0 - 0.10) = 0.90
        assert result['confidence_score'] == 0.90


# ---------------------------------------------------------------------------
# TestValidateFixActionType
# ---------------------------------------------------------------------------
class TestValidateFixActionType:
    """Tests for _validate_fix_action_type."""

    def test_valid_fix_action_passes_through(self):
        a = _build_analyzer()
        analysis = {'fix_action_type': 'file_change', 'failure_category': 'unmapped_image'}
        context = {}
        result = a._validate_fix_action_type(analysis, context)
        assert result['fix_action_type'] == 'file_change'

    def test_missing_fix_action_inferred_from_category(self):
        a = _build_analyzer()
        analysis = {'fix_action_type': '', 'failure_category': 'build_artifact_missing'}
        context = {}
        result = a._validate_fix_action_type(analysis, context)
        assert result['fix_action_type'] == 'rebuild'

    def test_invalid_fix_action_inferred_from_category(self):
        a = _build_analyzer()
        analysis = {'fix_action_type': 'bogus_action', 'failure_category': 'unmapped_image'}
        context = {}
        result = a._validate_fix_action_type(analysis, context)
        assert result['fix_action_type'] == 'file_change'

    def test_source_image_correction_to_rebuild(self):
        """When violation SHA lacks source but green build has it, set rebuild."""
        a = _build_analyzer()
        analysis = {'fix_action_type': 'file_change', 'failure_category': 'build_artifact_missing'}
        context = {
            'violation_enrichment': {
                'comp-a': {
                    'source_image_status': {
                        'violation_sha': {'exists': False},
                        'green_build_sha': {'exists': True},
                    }
                }
            }
        }
        result = a._validate_fix_action_type(analysis, context)
        assert result['fix_action_type'] == 'rebuild'

    def test_both_shas_missing_source_investigation_needed(self):
        """When both violation and green build lack source images."""
        a = _build_analyzer()
        analysis = {'fix_action_type': 'rebuild', 'failure_category': 'build_artifact_missing'}
        context = {
            'violation_enrichment': {
                'comp-a': {
                    'source_image_status': {
                        'violation_sha': {'exists': False},
                        'green_build_sha': {'exists': False},
                    }
                }
            }
        }
        result = a._validate_fix_action_type(analysis, context)
        assert result['fix_action_type'] == 'investigation_needed'


# ---------------------------------------------------------------------------
# TestAnnotateFixStatus
# ---------------------------------------------------------------------------
class TestAnnotateFixStatus:
    """Tests for _annotate_fix_status."""

    def test_merged_nudge_pr_adds_note(self):
        a = _build_analyzer()
        analysis = {'recommended_fix': 'Rebuild the component to fix source image'}
        context = {
            'violation_enrichment': {
                'comp-a': {
                    'nudge_prs': [
                        {'number': 42, 'title': 'Nudge comp-a', 'state': 'closed'}
                    ],
                }
            },
        }
        result = a._annotate_fix_status(analysis, context)
        assert 'Nudge PR #42' in result['recommended_fix']
        assert 'merged' in result['recommended_fix']

    def test_open_nudge_pr_adds_note(self):
        a = _build_analyzer()
        analysis = {'recommended_fix': 'Rebuild the component to fix source image'}
        context = {
            'violation_enrichment': {
                'comp-a': {
                    'nudge_prs': [
                        {'number': 55, 'title': 'Nudge comp-a', 'state': 'open'}
                    ],
                }
            },
        }
        result = a._annotate_fix_status(analysis, context)
        assert 'Nudge PR #55' in result['recommended_fix']
        assert 'in progress' in result['recommended_fix']

    def test_triage_item_adds_note(self):
        a = _build_analyzer()
        analysis = {'recommended_fix': 'Investigate the build timeout in detail'}
        context = {
            'violation_enrichment': {},
            'triage_items': [
                {'id': 7, 'status': 'active', 'components': ['comp-a']},
            ],
        }
        result = a._annotate_fix_status(analysis, context)
        assert 'Triage item #7' in result['recommended_fix']

    def test_no_annotation_when_no_signals(self):
        a = _build_analyzer()
        original_fix = 'Add the missing image to the RPA mapping file'
        analysis = {'recommended_fix': original_fix}
        context = {'violation_enrichment': {}, 'triage_items': []}
        result = a._annotate_fix_status(analysis, context)
        assert result['recommended_fix'] == original_fix

    def test_latest_green_build_note(self):
        a = _build_analyzer()
        analysis = {'recommended_fix': 'Rebuild the component to regenerate source'}
        context = {
            'violation_enrichment': {
                'comp-a': {
                    'builds_with_sha': [
                        {'status': 'Succeeded', 'image_sha': 'sha256:greensha1234567890',
                         'name': 'build-green-1', 'created': '2025-06-01'},
                    ],
                }
            },
        }
        result = a._annotate_fix_status(analysis, context)
        assert 'latest build' in result['recommended_fix']
        assert 'green' in result['recommended_fix']


# ---------------------------------------------------------------------------
# TestBuildVerificationSnippets
# ---------------------------------------------------------------------------
class TestBuildVerificationSnippets:
    """Tests for _build_verification_snippets."""

    def test_generates_rebuild_snippet(self):
        a = _build_analyzer()
        snippets = []
        a._build_verification_snippets(
            snippets, 'comp-a-v3-5-ea-2', {}, {}
        )
        assert any('ic rebuild' in s for s in snippets)

    def test_generates_skopeo_snippet_for_violation_sha(self):
        a = _build_analyzer()
        snippets = []
        data = {'violation_sha': 'sha256:abc123def456'}
        # Component name (minus dashes) must be a substring of image_ref (minus dashes)
        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/comp-a-v3-5-ea-2-rhel9@sha256:abc123def456'}
            ]
        }
        a._build_verification_snippets(
            snippets, 'comp-a-v3-5-ea-2', data, context
        )
        assert any('skopeo inspect' in s for s in snippets)

    def test_generates_green_sha_snippet(self):
        a = _build_analyzer()
        snippets = []
        data = {
            'latest_green_build': {
                'image_sha': 'sha256:greensha1234',
                'name': 'green-build-1',
            }
        }
        a._build_verification_snippets(
            snippets, 'comp-a-v3-5-ea-2', data, {}
        )
        assert any('green build SHA' in s.lower() or 'Latest green' in s for s in snippets)


# ---------------------------------------------------------------------------
# TestIsReleaseFailed
# ---------------------------------------------------------------------------
class TestIsReleaseFailed:
    """Tests for _is_release_failed."""

    def test_failed_release(self):
        a = _build_analyzer()
        release_cr = {
            'status': {
                'conditions': [
                    {'type': 'Released', 'status': 'False', 'reason': 'Failed'}
                ]
            }
        }
        with patch.object(a, '_k8s_get_json', return_value=release_cr):
            result = a._is_release_failed('release-bad')
        assert result is True

    def test_not_failed_release(self):
        a = _build_analyzer()
        release_cr = {
            'status': {
                'conditions': [
                    {'type': 'Released', 'status': 'True', 'reason': 'Succeeded'}
                ]
            }
        }
        with patch.object(a, '_k8s_get_json', return_value=release_cr):
            result = a._is_release_failed('release-ok')
        assert result is False

    def test_release_not_found_returns_none(self):
        a = _build_analyzer()
        with patch.object(a, '_k8s_get_json', return_value=None):
            result = a._is_release_failed('nonexistent')
        assert result is None

    def test_managed_pipeline_processed_false(self):
        a = _build_analyzer()
        release_cr = {
            'status': {
                'conditions': [
                    {'type': 'ManagedPipelineProcessed', 'status': 'False',
                     'reason': 'Error'}
                ]
            }
        }
        with patch.object(a, '_k8s_get_json', return_value=release_cr):
            result = a._is_release_failed('release-pipe-fail')
        assert result is True


# ---------------------------------------------------------------------------
# TestGetExistingAnalysis
# ---------------------------------------------------------------------------
class TestGetExistingAnalysis:

    def test_delegates_to_ai_repo(self):
        a = _build_analyzer()
        a.ai_repo.get_analysis_for_release.return_value = {'id': 99}
        result = a._get_existing_analysis('release-1')
        a.ai_repo.get_analysis_for_release.assert_called_once_with('release-1')
        assert result == {'id': 99}

    def test_returns_none_when_no_existing(self):
        a = _build_analyzer()
        a.ai_repo.get_analysis_for_release.return_value = None
        result = a._get_existing_analysis('release-new')
        assert result is None


# ---------------------------------------------------------------------------
# TestAnalyzeRelease
# ---------------------------------------------------------------------------
class TestAnalyzeRelease:
    """Tests for analyze_release (full pipeline)."""

    def test_returns_existing_analysis_when_not_forced(self):
        a = _build_analyzer()
        existing = {'id': 1, 'root_cause': 'known issue', 'failure_category': 'unmapped_image'}
        a.ai_repo.get_analysis_for_release.return_value = existing
        result = a.analyze_release('release-existing')
        assert result == existing
        a.llm.create_message.assert_not_called()

    def test_release_not_found_raises(self):
        a = _build_analyzer()
        a.ai_repo.get_analysis_for_release.return_value = None
        with patch.object(a, '_is_release_failed', return_value=None):
            with pytest.raises(ValueError, match="Release CR not found"):
                a.analyze_release('nonexistent')

    def test_release_not_failed_returns_status(self):
        a = _build_analyzer()
        a.ai_repo.get_analysis_for_release.return_value = None
        with patch.object(a, '_is_release_failed', return_value=False):
            result = a.analyze_release('release-ok')
        assert result['status'] == 'not_failed'
        assert result['release_name'] == 'release-ok'

    def test_full_happy_path(self):
        """Full orchestration: context -> LLM -> parse -> save."""
        a = _build_analyzer()
        a.ai_repo.get_analysis_for_release.return_value = None
        resp = _make_llm_response()
        a.llm.create_message.return_value = resp
        a.llm.model_name.return_value = 'test-model'
        a.langfuse.create_trace.return_value = MagicMock(id='trace-1')
        a.ai_repo.insert_release_analysis.return_value = 100
        a.pattern_repo = MagicMock()
        a.pattern_repo.find_or_create.return_value = {'id': 5}

        context = {
            'release_name': 'r-happy',
            'conditions': [{'type': 'Released', 'status': 'False',
                            'reason': 'Failed', 'message': 'error'}],
            'application': 'rhoai-v3-5-ea-2',
            'snapshot': 'snap-1',
            'target': 'prod',
            'pipeline': {'failed_task': 'ec'},
            'violation_enrichment': {},
        }

        with patch.object(a, '_is_release_failed', return_value=True):
            with patch.object(a, 'collect_context', return_value=context):
                with patch.object(a, 'build_analysis_prompt',
                                  return_value=('system', 'user')):
                    with patch.object(a, '_apply_confidence_cap',
                                      side_effect=lambda analysis, ctx: analysis):
                        with patch.object(a, '_validate_fix_action_type',
                                          side_effect=lambda analysis, ctx: analysis):
                            with patch.object(a, '_annotate_fix_status',
                                              side_effect=lambda analysis, ctx: analysis):
                                result = a.analyze_release('r-happy')

        assert result['failure_category'] == 'build_artifact_missing'
        a.llm.create_message.assert_called_once()
        a.ai_repo.insert_release_analysis.assert_called_once()
        a.langfuse.create_trace.assert_called_once()
        a.langfuse.end_trace.assert_called_once()
        a.langfuse.flush.assert_called_once()
        a.pattern_repo.find_or_create.assert_called_once_with('release', 'build_artifact_missing')
        a.pattern_repo.record_occurrence.assert_called_once()

    def test_force_re_analyze(self):
        """With force=True, existing analysis is bypassed."""
        a = _build_analyzer()
        a.ai_repo.get_analysis_for_release.return_value = {'id': 1, 'old': True}
        resp = _make_llm_response()
        a.llm.create_message.return_value = resp
        a.llm.model_name.return_value = 'test-model'
        a.langfuse.create_trace.return_value = MagicMock(id='trace-2')
        a.ai_repo.insert_release_analysis.return_value = 200
        a.pattern_repo = MagicMock()
        a.pattern_repo.find_or_create.return_value = {'id': 5}

        context = {
            'release_name': 'r-force',
            'conditions': [{'type': 'Released', 'status': 'False',
                            'reason': 'Failed', 'message': 'error'}],
            'violation_enrichment': {},
        }

        with patch.object(a, '_is_release_failed', return_value=True):
            with patch.object(a, 'collect_context', return_value=context):
                with patch.object(a, 'build_analysis_prompt',
                                  return_value=('system', 'user')):
                    with patch.object(a, '_apply_confidence_cap',
                                      side_effect=lambda analysis, ctx: analysis):
                        with patch.object(a, '_validate_fix_action_type',
                                          side_effect=lambda analysis, ctx: analysis):
                            with patch.object(a, '_annotate_fix_status',
                                              side_effect=lambda analysis, ctx: analysis):
                                result = a.analyze_release('r-force', force=True)

        # Should have called LLM despite existing analysis
        a.llm.create_message.assert_called_once()
        assert result['failure_category'] == 'build_artifact_missing'

    def test_no_conditions_raises(self):
        """When collect_context returns no conditions, raise ValueError."""
        a = _build_analyzer()
        a.ai_repo.get_analysis_for_release.return_value = None
        context = {'release_name': 'r-no-cond'}  # No 'conditions' key

        with patch.object(a, '_is_release_failed', return_value=True):
            with patch.object(a, 'collect_context', return_value=context):
                with pytest.raises(ValueError, match="no conditions found"):
                    a.analyze_release('r-no-cond')


# ---------------------------------------------------------------------------
# TestEnrichViolationContext
# ---------------------------------------------------------------------------
class TestEnrichViolationContext:
    """Tests for _enrich_violation_context."""

    def test_no_violations_returns_early(self):
        a = _build_analyzer()
        context = {}
        a._enrich_violation_context(context)
        assert 'violation_enrichment' not in context

    def test_enriches_with_build_history(self):
        a = _build_analyzer()
        a.build_repo.get_component_history.return_value = [
            {'pr_name': 'build-1', 'status': 'Failed',
             'created_at': '2025-06-01', 'error_message': 'timeout'}
        ]
        mock_tr = MagicMock()
        mock_tr.namespace = 'rhoai-tenant'
        mock_tr.query_component_build_history.return_value = []
        a._tekton_results = mock_tr

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/comp-a-rhel9@sha256:abc', 'rule': 'source_image.exists'}
            ],
            'application': 'rhoai-v3-5-ea-2',
        }

        with patch.object(a, '_enrich_artifact_health'):
            a._enrich_violation_context(context)

        assert 'violation_enrichment' in context
        enriched = context['violation_enrichment']
        # comp name: comp-a-v3-5-ea-2 (rhel9 stripped, suffix appended)
        assert any('comp-a' in key for key in enriched)

    def test_handles_db_error_gracefully(self):
        a = _build_analyzer()
        a.build_repo.get_component_history.side_effect = Exception("DB down")
        mock_tr = MagicMock()
        mock_tr.namespace = 'rhoai-tenant'
        mock_tr.query_component_build_history.return_value = []
        a._tekton_results = mock_tr

        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/comp-a@sha256:abc', 'rule': 'x'}
            ],
            'application': 'rhoai-v3-5-ea-2',
        }

        with patch.object(a, '_enrich_artifact_health'):
            # Should not raise
            a._enrich_violation_context(context)

        assert 'violation_enrichment' in context


# ---------------------------------------------------------------------------
# TestEnrichApplicationContext
# ---------------------------------------------------------------------------
class TestEnrichApplicationContext:
    """Tests for _enrich_application_context."""

    def test_no_application_returns_early(self):
        a = _build_analyzer()
        context = {}
        a._enrich_application_context(context)
        assert 'active_build_failures' not in context

    def test_enriches_with_health_data(self):
        a = _build_analyzer()
        a.build_repo.find_failing_component_names.return_value = ['comp-x']
        a.conforma_repo.find_unresolved_component_names.return_value = ['comp-y']
        a.triage_repo.get_active.return_value = [
            {'id': 1, 'components': ['comp-x'], 'group_label': 'build-fail',
             'root_cause': 'timeout', 'jira_key': 'X-1', 'status': 'active'}
        ]
        a.build_repo.get_nightly_history.return_value = []
        a.build_repo.get_working_components.return_value = ['c1', 'c2']
        a.build_repo.find_unresolved_component_names.return_value = ['comp-x']

        context = {'application': 'rhoai-v3-5-ea-2'}
        a._enrich_application_context(context)

        assert context['active_build_failures'] == ['comp-x']
        assert context['active_conforma_violations'] == ['comp-y']
        assert len(context['triage_items']) == 1
        assert context['health_summary']['working'] == 2

    def test_handles_all_errors_gracefully(self):
        a = _build_analyzer()
        a.build_repo.find_failing_component_names.side_effect = Exception("err")
        a.conforma_repo.find_unresolved_component_names.side_effect = Exception("err")
        a.triage_repo.get_active.side_effect = Exception("err")
        a.build_repo.get_nightly_history.side_effect = Exception("err")
        a.build_repo.get_working_components.side_effect = Exception("err")

        context = {'application': 'app'}
        # Should not raise
        a._enrich_application_context(context)


# ---------------------------------------------------------------------------
# TestEnrichWithComponentPrs
# ---------------------------------------------------------------------------
class TestEnrichWithComponentPrs:
    """Tests for _enrich_with_component_prs."""

    def test_no_enrichment_returns_early(self):
        a = _build_analyzer()
        context = {}
        a._enrich_with_component_prs(context)
        # No crash, no PR lookups

    @patch('analyzers.release_failure_analyzer.GITHUB_OPERATOR_OWNER', 'org')
    @patch('analyzers.release_failure_analyzer.GITHUB_OPERATOR_REPO', 'repo')
    @patch('analyzers.release_failure_analyzer.OPERATOR_NUDGING_PATH', 'build/nudging.yaml')
    def test_fetches_nudge_prs(self):
        a = _build_analyzer()
        mock_gh = MagicMock()
        mock_gh.get_file_content.return_value = 'nudging content'
        mock_gh.list_pull_requests.return_value = [
            {'number': 10, 'title': 'Nudge comp-a SHA', 'state': 'open',
             'html_url': 'https://github.com/org/repo/pull/10'}
        ]

        context = {
            'application': 'rhoai-v3-5-ea-2',
            'violation_enrichment': {
                'comp-a-v3-5-ea-2': {'component': 'comp-a-v3-5-ea-2'},
            },
        }

        with patch.object(a, '_get_github', return_value=mock_gh):
            a._enrich_with_component_prs(context)

        assert 'operator_nudging_content' in context
        assert context['violation_enrichment']['comp-a-v3-5-ea-2'].get('nudge_prs')

    def test_github_exception_handled(self):
        a = _build_analyzer()
        context = {
            'application': 'rhoai-v3-5-ea-2',
            'violation_enrichment': {'comp': {}},
        }
        with patch.object(a, '_get_github', side_effect=Exception("no GH")):
            # Should not raise
            a._enrich_with_component_prs(context)


# ---------------------------------------------------------------------------
# TestEnrichWithReadinessChecks
# ---------------------------------------------------------------------------
class TestEnrichWithReadinessChecks:
    """Tests for _enrich_with_readiness_checks."""

    def test_no_application_returns_early(self):
        a = _build_analyzer()
        context = {}
        a._enrich_with_readiness_checks(context)
        assert 'readiness_checks' not in context

    def test_adds_readiness_checks(self):
        a = _build_analyzer()
        context = {'application': 'rhoai-v3-5-ea-2'}
        checks = [
            {'phase': 'pre-release', 'status': 'PASS', 'name': 'check-1'},
            {'phase': 'pre-release', 'status': 'FAIL', 'name': 'check-2'},
        ]
        mock_module = MagicMock()
        mock_module._run_readiness_checks.return_value = checks
        with patch.dict('sys.modules', {'api.routes.releases': mock_module}):
            a._enrich_with_readiness_checks(context)
        assert context['readiness_checks'] == checks

    def test_handles_import_error(self):
        a = _build_analyzer()
        context = {'application': 'app'}
        # Remove the module so import fails
        with patch.dict('sys.modules', {'api.routes.releases': None}):
            # Should not raise — method catches Exception
            a._enrich_with_readiness_checks(context)
        assert 'readiness_checks' not in context


# ---------------------------------------------------------------------------
# TestEnrichArtifactHealth
# ---------------------------------------------------------------------------
class TestEnrichArtifactHealth:
    """Tests for _enrich_artifact_health."""

    def test_no_registry_client_returns_early(self):
        a = _build_analyzer()
        comp_data = {'component': 'comp-a'}
        with patch.dict('sys.modules', {'clients.registry_client': None}):
            with patch('builtins.__import__', side_effect=ImportError("no module")):
                a._enrich_artifact_health(comp_data, 'sha256:abc', {})
        assert 'artifact_health' not in comp_data

    def test_no_matching_image_ref_returns_early(self):
        a = _build_analyzer()
        comp_data = {'component': 'unrelated-comp'}
        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/totally-different@sha256:xyz'}
            ]
        }
        mock_rc = MagicMock()
        with patch('clients.registry_client.RegistryClient', return_value=mock_rc):
            a._enrich_artifact_health(comp_data, 'sha256:abc', context)
        # No image ref matched the component name
        assert 'artifact_health' not in comp_data

    def test_enriches_with_artifact_checks(self):
        a = _build_analyzer()
        comp_data = {
            'component': 'comp-a-v3-5-ea-2',
            'latest_green_build': {'image_sha': 'sha256:greensha123', 'name': 'b-1'},
        }
        context = {
            'violations_summary': [
                {'image_ref': 'quay.io/rhoai/comp-a-v3-5-ea-2-rhel9@sha256:badsha'}
            ]
        }
        mock_rc = MagicMock()
        mock_rc.parse_image_ref.return_value = ('quay.io', 'rhoai/comp-a-v3-5-ea-2-rhel9', 'sha256:badsha')
        mock_rc.check_artifact_health.return_value = {
            'healthy': False,
            'missing': ['src'],
            'artifacts': {
                'sig': {'exists': True, 'method': 'cosign'},
                'src': {'exists': False, 'method': 'tag', 'details': 'not found'},
                'att': {'exists': True, 'method': 'referrer'},
                'sbom': {'exists': True, 'method': 'referrer'},
            },
        }

        with patch('clients.registry_client.RegistryClient', return_value=mock_rc):
            a._enrich_artifact_health(comp_data, 'sha256:badsha', context)

        assert 'artifact_health' in comp_data
        assert 'source_image_status' in comp_data
