"""Comprehensive tests for BuildFailureAnalyzer.

Covers constructor, every public/private method, and the full orchestration
flow. All external dependencies are mocked to avoid real DB/LLM/GitHub calls.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_config(llm=True):
    """Build a mock config object matching the real AppConfig shape."""
    config = MagicMock()
    config.db = MagicMock()
    config.k8s.application_name = 'test-app'
    config.k8s.namespace = 'test-ns'
    config.github_token = 'gh-tok'
    if llm:
        config.llm = MagicMock()
        config.llm.provider = 'anthropic'
    else:
        config.llm = None
    return config


def _make_pattern_service():
    """Build a mock PatternMatchingService."""
    svc = MagicMock()
    enhancement = MagicMock()
    enhancement.boost_applied = False
    enhancement.original_confidence = 0.8
    enhancement.boosted_confidence = 0.8
    enhancement.boost_amount = 0.0
    enhancement.matched_patterns = []
    svc.enhance_analysis.return_value = enhancement
    svc.get_matches_for_prompt.return_value = ''
    return svc


def _make_llm_response(tool_input=None, tool_name='record_analysis',
                        input_tokens=1000, output_tokens=500):
    """Build a mock LLMResponse matching the real dataclass."""
    if tool_input is None:
        tool_input = {
            'root_cause': 'Dependency resolution failed because Go module proxy returned 404',
            'failure_category': 'dependency_issue',
            'confidence_score': 0.85,
            'recommended_fix': 'Update go.sum by running go mod tidy to refresh checksums',
            'recommended_files': ['go.sum', 'go.mod'],
            'can_auto_fix': False,
            'requires_human_review': True,
        }
    resp = MagicMock()
    resp.tool_calls = [{'name': tool_name, 'input': tool_input}]
    resp.input_tokens = input_tokens
    resp.output_tokens = output_tokens
    resp.content = 'some text'
    resp.content_text = 'some text'
    return resp


def _make_failure(**overrides):
    """Build a failure dict with sensible defaults."""
    failure = {
        'id': 42,
        'component_name': 'odh-dashboard',
        'pipelinerun_name': 'odh-dashboard-pr-123',
        'repository_url': 'https://github.com/opendatahub-io/odh-dashboard',
        'repository': 'https://github.com/opendatahub-io/odh-dashboard',
        'commit_sha': 'abc1234567890def',
        'commit_author': 'dev@example.com',
        'commit_message': 'Update deps',
        'branch': 'rhoai-2.19',
        'failed_task_name': 'build-container',
        'failed_step_name': 'build',
        'error_type': 'build_error',
        'error_message': 'exit code 1',
        'build_logs': 'Step 5: ERROR: cannot find module\nSome more logs',
        'commit_context': None,
        'enriched_context': None,
        'application': 'rhoai-v2.19',
        'triage_items': [],
        'blob_refs': None,
    }
    failure.update(overrides)
    return failure


def _build_analyzer(**kw):
    """Construct a BuildFailureAnalyzer with all deps mocked."""
    defaults = dict(
        config=_make_config(),
        db=MagicMock(),
        build_repo=MagicMock(),
        ai_repo=MagicMock(),
        llm=MagicMock(),
        langfuse=MagicMock(),
        pattern_service=_make_pattern_service(),
        github_client=MagicMock(),
    )
    defaults.update(kw)
    with patch('analyzers.build_failure_analyzer.load_prompt', return_value='system prompt'):
        from analyzers.build_failure_analyzer import BuildFailureAnalyzer
        return BuildFailureAnalyzer(**defaults)


# ---------------------------------------------------------------------------
# TestBuildFailureAnalyzerConstructor
# ---------------------------------------------------------------------------
class TestBuildFailureAnalyzerConstructor:
    """Tests for __init__ method."""

    def test_constructor_with_all_deps(self):
        """All mocked deps are stored, no real constructors called."""
        llm = MagicMock()
        db = MagicMock()
        build_repo = MagicMock()
        ai_repo = MagicMock()
        langfuse = MagicMock()
        ps = _make_pattern_service()
        gh = MagicMock()
        config = _make_config()

        with patch('analyzers.build_failure_analyzer.load_prompt', return_value='sp'):
            from analyzers.build_failure_analyzer import BuildFailureAnalyzer
            a = BuildFailureAnalyzer(
                config=config, db=db, build_repo=build_repo,
                ai_repo=ai_repo, llm=llm, langfuse=langfuse,
                pattern_service=ps, github_client=gh,
            )
        assert a.llm is llm
        assert a.db is db
        assert a.build_repo is build_repo
        assert a.ai_repo is ai_repo
        assert a.langfuse is langfuse
        assert a.pattern_service is ps
        assert a._github_client is gh

    @patch('analyzers.build_failure_analyzer.load_prompt', return_value='sp')
    @patch('analyzers.build_failure_analyzer.LangfuseTracker')
    @patch('analyzers.build_failure_analyzer.create_llm_provider')
    @patch('analyzers.build_failure_analyzer.ErrorPatternRepository')
    @patch('analyzers.build_failure_analyzer.AIAnalysisRepository')
    @patch('analyzers.build_failure_analyzer.BuildFailureRepository')
    @patch('analyzers.build_failure_analyzer.DatabaseConnection')
    def test_constructor_creates_defaults(
        self, mock_db_cls, mock_build_cls, mock_ai_cls,
        mock_err_cls, mock_llm_factory, mock_lf_cls, mock_lp
    ):
        """When deps are None, real constructors are called."""
        config = _make_config()
        mock_db_instance = MagicMock()
        mock_db_cls.return_value = mock_db_instance

        with patch('analyzers.build_failure_analyzer.load_prompt', return_value='sp'):
            from analyzers.build_failure_analyzer import BuildFailureAnalyzer
            a = BuildFailureAnalyzer(config=config)

        mock_db_cls.assert_called_once_with(config.db)
        mock_build_cls.assert_called_once_with(mock_db_instance)
        mock_ai_cls.assert_called_once_with(mock_db_instance)
        mock_llm_factory.assert_called_once_with(config.llm)

    def test_constructor_raises_without_llm_config(self):
        """ValueError when config.llm is falsy and no llm passed."""
        config = _make_config(llm=False)
        with patch('analyzers.build_failure_analyzer.load_prompt', return_value='sp'):
            from analyzers.build_failure_analyzer import BuildFailureAnalyzer
            with pytest.raises(ValueError, match="LLM not configured"):
                BuildFailureAnalyzer(
                    config=config, db=MagicMock(),
                    build_repo=MagicMock(), ai_repo=MagicMock(),
                    pattern_service=_make_pattern_service(),
                )


# ---------------------------------------------------------------------------
# TestGetPendingFailures
# ---------------------------------------------------------------------------
class TestGetPendingFailures:

    def test_delegates_to_ai_repo(self):
        a = _build_analyzer()
        a.ai_repo.get_pending_failures.return_value = [{'id': 1}]
        result = a.get_pending_failures()
        a.ai_repo.get_pending_failures.assert_called_once_with(
            'test-app', limit=5, component_filter=None, force=False
        )
        assert result == [{'id': 1}]

    def test_with_component_filter(self):
        a = _build_analyzer()
        a.ai_repo.get_pending_failures.return_value = []
        a.get_pending_failures(component_filter='odh-dashboard')
        a.ai_repo.get_pending_failures.assert_called_once_with(
            'test-app', limit=5, component_filter='odh-dashboard', force=False
        )

    def test_with_force_flag(self):
        a = _build_analyzer()
        a.ai_repo.get_pending_failures.return_value = []
        a.get_pending_failures(force=True, limit=10)
        a.ai_repo.get_pending_failures.assert_called_once_with(
            'test-app', limit=10, component_filter=None, force=True
        )

    def test_with_application_override(self):
        a = _build_analyzer()
        a.ai_repo.get_pending_failures.return_value = []
        a.get_pending_failures(application='custom-app')
        a.ai_repo.get_pending_failures.assert_called_once_with(
            'custom-app', limit=5, component_filter=None, force=False
        )


# ---------------------------------------------------------------------------
# TestEnsureContext
# ---------------------------------------------------------------------------
class TestEnsureContext:

    def test_skips_when_commit_context_has_commit(self):
        a = _build_analyzer()
        failure = _make_failure(commit_context={'commit': {'sha': 'abc'}})
        a._ensure_context(failure)
        a._github_client.get_commit_context.assert_not_called()

    def test_fetches_from_github_when_missing(self):
        a = _build_analyzer()
        a._github_client.get_commit_context.return_value = {'commit': {'sha': 'abc'}}
        failure = _make_failure(commit_context=None)
        a._ensure_context(failure)
        a._github_client.get_commit_context.assert_called_once_with(
            failure['repository_url'], failure['commit_sha'], failure['branch']
        )
        assert failure['commit_context'] == {'commit': {'sha': 'abc'}}

    def test_skips_when_no_repo_url(self):
        a = _build_analyzer()
        failure = _make_failure(repository_url=None, commit_sha='abc123')
        a._ensure_context(failure)
        a._github_client.get_commit_context.assert_not_called()

    def test_skips_when_no_sha(self):
        a = _build_analyzer()
        failure = _make_failure(commit_sha=None)
        a._ensure_context(failure)
        a._github_client.get_commit_context.assert_not_called()

    def test_handles_string_commit_context(self):
        a = _build_analyzer()
        ctx = json.dumps({'commit': {'sha': 'def456'}})
        failure = _make_failure(commit_context=ctx)
        a._ensure_context(failure)
        # Already has commit -> skip
        a._github_client.get_commit_context.assert_not_called()

    def test_handles_invalid_json_string(self):
        a = _build_analyzer()
        failure = _make_failure(commit_context='not-json')
        a._github_client.get_commit_context.return_value = {'commit': {'sha': 'x'}}
        a._ensure_context(failure)
        # Invalid JSON -> treated as no context -> fetches from GitHub
        a._github_client.get_commit_context.assert_called_once()

    def test_github_returns_none(self):
        a = _build_analyzer()
        a._github_client.get_commit_context.return_value = None
        failure = _make_failure(commit_context=None)
        a._ensure_context(failure)
        assert failure['commit_context'] is None

    def test_creates_github_client_if_none(self):
        a = _build_analyzer(github_client=None)
        a.config.github_token = None
        failure = _make_failure(commit_context=None)
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'tok'}, clear=False):
            with patch('clients.github_client.GitHubClient') as mock_gh:
                mock_gh.return_value.get_commit_context.return_value = {'commit': {'sha': 'x'}}
                a._ensure_context(failure)
                mock_gh.assert_called_once_with('tok')

    def test_no_github_token_warns(self):
        a = _build_analyzer(github_client=None)
        a.config.github_token = None
        failure = _make_failure(commit_context=None)
        with patch.dict(os.environ, {}, clear=False):
            # Remove GITHUB_TOKEN if present
            os.environ.pop('GITHUB_TOKEN', None)
            a._ensure_context(failure)
            # Should not have created a client
            assert a._github_client is None


# ---------------------------------------------------------------------------
# TestEnsureLogs
# ---------------------------------------------------------------------------
class TestEnsureLogs:

    def test_skips_when_no_logs_and_no_taskrun(self):
        a = _build_analyzer()
        failure = _make_failure(build_logs='', pipelinerun_name=None)
        with patch.object(a, '_fetch_failed_taskrun_logs', return_value=None):
            a._ensure_logs(failure)
        assert failure['build_logs'] == ''

    def test_extracts_failed_section(self):
        a = _build_analyzer()
        logs = (
            '===== TaskRun: ns/build-container-abc =====\n'
            'ERROR: something went wrong\n'
            '===== TaskRun: ns/other-task-def =====\n'
            'OK\n'
        )
        failure = _make_failure(
            build_logs=logs,
            failed_task_name='build-container',
        )
        with patch.object(a, '_fetch_failed_taskrun_logs', return_value=None):
            a._ensure_logs(failure)
        assert 'ERROR: something went wrong' in failure['build_logs']

    def test_truncated_logs_refetch(self):
        """Logs >= 199000 chars trigger re-fetch."""
        a = _build_analyzer()
        long_logs = 'x' * 199001
        failure = _make_failure(build_logs=long_logs, failed_task_name='')
        with patch.object(a, '_fetch_failed_taskrun_logs', return_value=None):
            with patch.object(a, '_refetch_logs', return_value='refetched-logs') as mock_refetch:
                a._ensure_logs(failure)
                mock_refetch.assert_called_once()

    def test_error_filtering_applied_when_large(self):
        """Logs > 100000 chars apply keyword filtering."""
        a = _build_analyzer()
        large_logs = 'normal line\n' * 10000 + 'ERROR: real problem\n'
        failure = _make_failure(
            build_logs=large_logs,
            failed_task_name='',
        )
        with patch.object(a, '_fetch_failed_taskrun_logs', return_value=None):
            with patch.object(a, '_filter_error_lines', return_value='ERROR: real problem') as mock_filter:
                a._ensure_logs(failure)
                mock_filter.assert_called_once()

    def test_taskrun_fetch_for_specific_failures(self):
        """Task-specific errors like fips-check trigger taskrun fetch."""
        a = _build_analyzer()
        failure = _make_failure(
            build_logs='some logs',
            error_message='fips-check failed',
        )
        with patch.object(a, '_fetch_failed_taskrun_logs', return_value='taskrun logs') as mock_fetch:
            a._ensure_logs(failure)
            mock_fetch.assert_called_once()
            assert failure['build_logs'] == 'taskrun logs'


# ---------------------------------------------------------------------------
# TestExtractFailedSection
# ---------------------------------------------------------------------------
class TestExtractFailedSection:

    def test_extracts_matching_section(self):
        a = _build_analyzer()
        logs = (
            '===== TaskRun: build-container-abc /step =====\n'
            'line1\nline2\n'
            '===== TaskRun: other-task-def /step =====\n'
            'line3\n'
        )
        result = a._extract_failed_section(logs, 'build-container')
        assert result is not None
        assert 'line1' in result
        assert 'line3' not in result

    def test_returns_none_when_no_match(self):
        a = _build_analyzer()
        logs = 'plain text logs with no TaskRun markers'
        result = a._extract_failed_section(logs, 'nonexistent-task')
        assert result is None


# ---------------------------------------------------------------------------
# TestFormatCommitContext
# ---------------------------------------------------------------------------
class TestFormatCommitContext:

    def test_with_full_context(self):
        a = _build_analyzer()
        ctx = {
            'commit': {
                'sha': 'abc123',
                'files': [
                    {'filename': 'main.go', 'status': 'modified',
                     'additions': 10, 'deletions': 5, 'patch': '+added\n-removed'}
                ],
                'stats': {'additions': 10, 'deletions': 5},
            },
            'pr': {'number': 42, 'title': 'Update deps', 'body': 'PR body text'},
            'dockerfile': {'path': 'Dockerfile', 'content': 'FROM ubi9'},
            'tekton_configs': {
                'push.yaml': 'apiVersion: tekton.dev/v1\nkind: PipelineRun'
            },
        }
        result = a._format_commit_context(ctx)
        assert '## Commit Context' in result
        assert 'main.go' in result
        assert '+added' in result
        assert 'Pull Request #42' in result
        assert 'Dockerfile' in result
        assert 'push.yaml' in result

    def test_with_no_context(self):
        a = _build_analyzer()
        result = a._format_commit_context(None)
        assert 'Not available' in result

    def test_with_string_context(self):
        a = _build_analyzer()
        ctx = json.dumps({
            'commit': {
                'sha': 'x',
                'files': [],
                'stats': {'additions': 0, 'deletions': 0},
            }
        })
        result = a._format_commit_context(ctx)
        assert '## Commit Context' in result
        assert 'Commit Diff' in result

    def test_with_invalid_string_context(self):
        a = _build_analyzer()
        result = a._format_commit_context('not-valid-json')
        assert 'Not available' in result

    def test_with_enriched_context_dependency_changes(self):
        a = _build_analyzer()
        ctx = {'commit': {'sha': 'x', 'files': [], 'stats': {'additions': 0, 'deletions': 0}}}
        enriched = {
            'dependency_changes': {
                'go.mod': {'status': 'modified', 'additions': 5, 'deletions': 2,
                           'patch': '+new dep\n-old dep'}
            }
        }
        result = a._format_commit_context(ctx, enriched)
        assert 'Dependency File Changes' in result
        assert 'go.mod' in result

    def test_with_enriched_context_related_failures(self):
        a = _build_analyzer()
        ctx = {'commit': {'sha': 'x', 'files': [], 'stats': {'additions': 0, 'deletions': 0}}}
        enriched = {
            'related_failures': [
                {
                    'component_name': 'other-comp',
                    'error_type': 'build_error',
                    'error_message': 'exit code 1',
                    'ai_analyzed': True,
                    'failure_category': 'dependency_issue',
                    'root_cause': 'Go module proxy failure',
                }
            ]
        }
        result = a._format_commit_context(ctx, enriched)
        assert 'Related Recent Failures' in result
        assert 'other-comp' in result
        assert 'Previous root cause' in result

    def test_with_enriched_context_open_prs(self):
        a = _build_analyzer()
        ctx = {'commit': {'sha': 'x', 'files': [], 'stats': {'additions': 0, 'deletions': 0}}}
        enriched = {
            'open_prs': [
                {'number': 99, 'title': 'Fix build', 'author': 'dev',
                 'url': 'https://github.com/org/repo/pull/99', 'merged': False}
            ]
        }
        result = a._format_commit_context(ctx, enriched)
        assert 'Open PRs' in result
        assert 'PR #99' in result

    def test_with_enriched_string_context(self):
        a = _build_analyzer()
        ctx = {'commit': {'sha': 'x', 'files': [], 'stats': {'additions': 0, 'deletions': 0}}}
        enriched = json.dumps({
            'dependency_changes': {
                'package.json': {'status': 'modified', 'additions': 1, 'deletions': 1, 'patch': ''}
            }
        })
        result = a._format_commit_context(ctx, enriched)
        assert 'Dependency File Changes' in result

    def test_with_resolved_examples(self):
        a = _build_analyzer()
        ctx = {'commit': {'sha': 'x', 'files': [], 'stats': {'additions': 0, 'deletions': 0}}}
        enriched = {
            'resolved_examples': [
                {
                    'component_name': 'comp-a',
                    'error_type': 'build_error',
                    'error_message': 'npm install failed',
                    'root_cause': 'Lock file out of sync',
                    'recommended_fix': 'Run npm install',
                    'commit_url': 'https://github.com/org/repo/commit/abc',
                }
            ]
        }
        result = a._format_commit_context(ctx, enriched)
        assert 'Previously Resolved' in result
        assert 'comp-a' in result
        assert 'Resolution commit' in result


# ---------------------------------------------------------------------------
# TestBuildAnalysisPrompt
# ---------------------------------------------------------------------------
class TestBuildAnalysisPrompt:

    def test_returns_system_and_user_tuple(self):
        a = _build_analyzer()
        failure = _make_failure()
        with patch.object(a, '_get_dependency_updates', return_value=''):
            with patch.object(a, '_get_release_context', return_value=''):
                with patch('knowledge.graph_context.build_context', side_effect=Exception):
                    sys_prompt, user_prompt = a.build_analysis_prompt(failure)
        assert isinstance(sys_prompt, str)
        assert isinstance(user_prompt, str)

    def test_includes_component_info(self):
        a = _build_analyzer()
        failure = _make_failure()
        with patch.object(a, '_get_dependency_updates', return_value=''):
            with patch.object(a, '_get_release_context', return_value=''):
                with patch('knowledge.graph_context.build_context', side_effect=Exception):
                    _, user_prompt = a.build_analysis_prompt(failure)
        assert 'odh-dashboard' in user_prompt

    def test_includes_log_section(self):
        a = _build_analyzer()
        failure = _make_failure(build_logs='UNIQUE_ERROR_42')
        with patch.object(a, '_get_dependency_updates', return_value=''):
            with patch.object(a, '_get_release_context', return_value=''):
                with patch('knowledge.graph_context.build_context', side_effect=Exception):
                    _, user_prompt = a.build_analysis_prompt(failure)
        assert 'UNIQUE_ERROR_42' in user_prompt

    def test_includes_commit_context(self):
        a = _build_analyzer()
        failure = _make_failure(commit_context={
            'commit': {'sha': 'abc', 'files': [], 'stats': {'additions': 0, 'deletions': 0}}
        })
        with patch.object(a, '_get_dependency_updates', return_value=''):
            with patch.object(a, '_get_release_context', return_value=''):
                with patch('knowledge.graph_context.build_context', side_effect=Exception):
                    _, user_prompt = a.build_analysis_prompt(failure)
        assert 'Commit Context' in user_prompt

    def test_includes_dynamic_urls_with_repo_and_sha(self):
        a = _build_analyzer()
        failure = _make_failure(
            repository_url='https://github.com/org/repo',
            commit_sha='abc1234567890def',
        )
        with patch.object(a, '_get_dependency_updates', return_value=''):
            with patch.object(a, '_get_release_context', return_value=''):
                with patch('knowledge.graph_context.build_context', side_effect=Exception):
                    _, user_prompt = a.build_analysis_prompt(failure)
        assert '.tekton/' in user_prompt
        assert 'Containerfile' in user_prompt
        assert 'commit/abc' in user_prompt


# ---------------------------------------------------------------------------
# TestParseAnalysisResponse
# ---------------------------------------------------------------------------
class TestParseAnalysisResponse:

    def test_valid_record_analysis_call(self):
        a = _build_analyzer()
        resp = _make_llm_response()
        result = a.parse_analysis_response(resp)
        assert result['failure_category'] == 'dependency_issue'
        assert result['confidence_score'] == 0.85
        assert result['can_auto_fix'] is False

    def test_no_tool_calls_raises_value_error(self):
        a = _build_analyzer()
        resp = MagicMock()
        resp.tool_calls = []
        with pytest.raises(ValueError, match="did not return tool_use"):
            a.parse_analysis_response(resp)

    def test_none_tool_calls_raises_value_error(self):
        a = _build_analyzer()
        resp = MagicMock()
        resp.tool_calls = None
        with pytest.raises(ValueError, match="did not return tool_use"):
            a.parse_analysis_response(resp)

    def test_wrong_tool_name_raises_value_error(self):
        a = _build_analyzer()
        resp = MagicMock()
        resp.tool_calls = [{'name': 'wrong_tool', 'input': {}}]
        with pytest.raises(ValueError, match="did not call record_analysis"):
            a.parse_analysis_response(resp)

    def test_validation_error_returns_fallback(self):
        """Invalid data (e.g. too short root_cause) returns fallback with confidence 0.0."""
        a = _build_analyzer()
        resp = _make_llm_response(tool_input={
            'root_cause': 'short',  # < 10 chars
            'failure_category': 'build_error',
            'confidence_score': 0.9,
            'recommended_fix': 'do something about it now',
            'can_auto_fix': True,
        })
        result = a.parse_analysis_response(resp)
        assert result['confidence_score'] == 0.0
        assert result['requires_human_review'] is True
        assert result['can_auto_fix'] is False

    def test_multiple_tool_calls_picks_record_analysis(self):
        a = _build_analyzer()
        resp = MagicMock()
        resp.tool_calls = [
            {'name': 'other_tool', 'input': {'data': 'x'}},
            {'name': 'record_analysis', 'input': {
                'root_cause': 'Missing base image in registry causes build to fail',
                'failure_category': 'infrastructure',
                'confidence_score': 0.7,
                'recommended_fix': 'Push the base image to the internal registry first',
                'can_auto_fix': False,
            }},
        ]
        result = a.parse_analysis_response(resp)
        assert result['failure_category'] == 'infrastructure'


# ---------------------------------------------------------------------------
# TestAnnotateFixStatus
# ---------------------------------------------------------------------------
class TestAnnotateFixStatus:

    def test_adds_fix_note_when_commit_mentions_fix(self):
        a = _build_analyzer()
        analysis = {'recommended_fix': 'Update the go.sum file to fix checksums'}
        failure = _make_failure(commit_context={
            'commit': {'sha': 'abc12345', 'message': 'Fix build error in deps'}
        })
        result = a._annotate_fix_status(analysis, failure)
        assert 'Fix Status' in result['recommended_fix']
        assert 'recent commit' in result['recommended_fix']

    def test_adds_triage_note_when_active_triage(self):
        a = _build_analyzer()
        analysis = {'recommended_fix': 'Rebuild the container image from scratch'}
        failure = _make_failure(
            commit_context=None,
            triage_items=[{'id': 7, 'status': 'active'}],
        )
        result = a._annotate_fix_status(analysis, failure)
        assert 'Triage item #7' in result['recommended_fix']

    def test_no_annotation_when_no_signals(self):
        a = _build_analyzer()
        original_fix = 'Run go mod tidy and commit the updated checksums'
        analysis = {'recommended_fix': original_fix}
        failure = _make_failure(commit_context=None, triage_items=[])
        result = a._annotate_fix_status(analysis, failure)
        assert result['recommended_fix'] == original_fix

    def test_handles_string_commit_context(self):
        a = _build_analyzer()
        analysis = {'recommended_fix': 'Rebuild the container image from scratch'}
        ctx = json.dumps({'commit': {'sha': 'abc', 'message': 'hotfix: revert broken change'}})
        failure = _make_failure(commit_context=ctx)
        result = a._annotate_fix_status(analysis, failure)
        assert 'Fix Status' in result['recommended_fix']

    def test_handles_invalid_json_commit_context(self):
        a = _build_analyzer()
        analysis = {'recommended_fix': 'Rebuild the container image from scratch'}
        failure = _make_failure(commit_context='not-json')
        result = a._annotate_fix_status(analysis, failure)
        # No crash, no annotation added
        assert 'Fix Status' not in result['recommended_fix']


# ---------------------------------------------------------------------------
# TestBuildDynamicUrls
# ---------------------------------------------------------------------------
class TestBuildDynamicUrls:

    def test_with_repo_and_sha(self):
        a = _build_analyzer()
        failure = _make_failure(
            repository_url='https://github.com/org/repo',
            commit_sha='abc1234567890def',
        )
        result = a._build_dynamic_urls(failure)
        assert '.tekton/' in result
        assert 'Containerfile' in result
        assert 'commit/abc1234567890def' in result

    def test_with_repo_only(self):
        a = _build_analyzer()
        failure = _make_failure(repository_url='https://github.com/org/repo', commit_sha='')
        result = a._build_dynamic_urls(failure)
        assert 'Component repo' in result

    def test_with_no_repo(self):
        a = _build_analyzer()
        failure = _make_failure(repository_url='', commit_sha='')
        # Also clear the fallback 'repository' field
        failure['repository'] = ''
        result = a._build_dynamic_urls(failure)
        assert result == ''

    def test_strips_git_suffix(self):
        a = _build_analyzer()
        failure = _make_failure(
            repository_url='https://github.com/org/repo.git',
            commit_sha='abc1234567890def',
        )
        result = a._build_dynamic_urls(failure)
        assert '.git' not in result
        assert 'org/repo/blob/' in result

    def test_short_sha_excluded(self):
        """SHA < 7 chars means no commit URLs."""
        a = _build_analyzer()
        failure = _make_failure(
            repository_url='https://github.com/org/repo',
            commit_sha='abc',
        )
        result = a._build_dynamic_urls(failure)
        # Short SHA -> treated as repo-only
        assert 'Component repo' in result
        assert '.tekton/' not in result


# ---------------------------------------------------------------------------
# TestAnalyzeFailure
# ---------------------------------------------------------------------------
class TestAnalyzeFailure:

    def test_full_flow(self):
        """Full orchestration: ensure_context -> ensure_logs -> prompt -> LLM -> parse -> save."""
        a = _build_analyzer()
        resp = _make_llm_response()
        a.llm.create_message.return_value = resp
        a.llm.model_name.return_value = 'test-model'
        a.langfuse.create_trace.return_value = MagicMock(id='trace-1')
        a.ai_repo.insert_analysis.return_value = 100
        a.pattern_repo = MagicMock()
        a.pattern_repo.find_or_create.return_value = {'id': 5}

        failure = _make_failure()

        with patch.object(a, '_ensure_context'):
            with patch.object(a, '_ensure_enrichment'):
                with patch.object(a, '_ensure_logs'):
                    with patch.object(a, '_get_dependency_updates', return_value=''):
                        with patch.object(a, '_get_release_context', return_value=''):
                            with patch('knowledge.graph_context.build_context', side_effect=Exception):
                                result = a.analyze_failure(failure)

        assert result['failure_category'] == 'dependency_issue'
        a.llm.create_message.assert_called_once()
        a.ai_repo.insert_analysis.assert_called_once()
        a.langfuse.create_trace.assert_called_once()
        a.langfuse.end_trace.assert_called_once()
        a.pattern_repo.find_or_create.assert_called_once()
        a.pattern_repo.record_occurrence.assert_called_once()

    def test_handles_llm_error(self):
        """LLM raising an exception propagates up."""
        a = _build_analyzer()
        a.llm.create_message.side_effect = RuntimeError("API down")
        a.langfuse.create_trace.return_value = MagicMock()

        failure = _make_failure()
        with patch.object(a, '_ensure_context'):
            with patch.object(a, '_ensure_enrichment'):
                with patch.object(a, '_ensure_logs'):
                    with patch.object(a, '_get_dependency_updates', return_value=''):
                        with patch.object(a, '_get_release_context', return_value=''):
                            with patch('knowledge.graph_context.build_context', side_effect=Exception):
                                with pytest.raises(RuntimeError, match="API down"):
                                    a.analyze_failure(failure)

    def test_pattern_boost_applied(self):
        """When pattern boost is applied, confidence is updated."""
        a = _build_analyzer()
        resp = _make_llm_response()
        a.llm.create_message.return_value = resp
        a.llm.model_name.return_value = 'test-model'
        a.langfuse.create_trace.return_value = MagicMock(id='t')
        a.ai_repo.insert_analysis.return_value = 100
        a.pattern_repo = MagicMock()
        a.pattern_repo.find_or_create.return_value = {'id': 5}

        # Configure pattern boost
        enhancement = MagicMock()
        enhancement.boost_applied = True
        enhancement.original_confidence = 0.85
        enhancement.boosted_confidence = 0.95
        enhancement.boost_amount = 0.10
        matched = MagicMock()
        matched.pattern_id = 1
        matched.pattern_name = 'go-proxy-404'
        enhancement.matched_patterns = [matched]
        a.pattern_service.enhance_analysis.return_value = enhancement

        failure = _make_failure()
        with patch.object(a, '_ensure_context'):
            with patch.object(a, '_ensure_enrichment'):
                with patch.object(a, '_ensure_logs'):
                    with patch.object(a, '_get_dependency_updates', return_value=''):
                        with patch.object(a, '_get_release_context', return_value=''):
                            with patch('knowledge.graph_context.build_context', side_effect=Exception):
                                result = a.analyze_failure(failure)

        assert result['confidence_score'] == 0.95


# ---------------------------------------------------------------------------
# TestRun
# ---------------------------------------------------------------------------
class TestRun:

    def test_no_pending_failures(self):
        a = _build_analyzer()
        a.ai_repo.get_pending_failures.return_value = []
        a.ai_repo.skip_no_logs_timeouts.return_value = 0
        result = a.run()
        assert result['analyzed'] == 0

    def test_analyzes_pending_failures(self):
        a = _build_analyzer()
        failures = [_make_failure(id=i, component_name=f'comp-{i}') for i in range(3)]
        a.ai_repo.get_pending_failures.return_value = failures
        a.ai_repo.skip_no_logs_timeouts.return_value = 0
        a.ai_repo.increment_attempts.return_value = 1

        with patch.object(a, 'analyze_failure', return_value={
            'failure_category': 'build_error',
            'confidence_score': 0.8,
            'can_auto_fix': False,
            'root_cause': 'test root cause that is long enough',
        }):
            result = a.run()

        assert result['analyzed'] == 3
        a.langfuse.flush.assert_called_once()

    def test_handles_analysis_error(self):
        """Errors in analyze_failure don't stop processing the rest."""
        a = _build_analyzer()
        failures = [_make_failure(id=1), _make_failure(id=2)]
        a.ai_repo.get_pending_failures.return_value = failures
        a.ai_repo.skip_no_logs_timeouts.return_value = 0
        a.ai_repo.increment_attempts.return_value = 1

        call_count = [0]
        def analyze_side_effect(failure):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("LLM timeout")
            return {
                'failure_category': 'build_error',
                'confidence_score': 0.7,
                'can_auto_fix': False,
                'root_cause': 'second one worked fine and produced results',
            }

        with patch.object(a, 'analyze_failure', side_effect=analyze_side_effect):
            result = a.run()

        assert result['analyzed'] == 1

    def test_marks_skipped_after_max_retries(self):
        """After MAX_RETRIES attempts, failure is marked as skipped."""
        a = _build_analyzer()
        failures = [_make_failure(id=1)]
        a.ai_repo.get_pending_failures.return_value = failures
        a.ai_repo.skip_no_logs_timeouts.return_value = 0
        a.ai_repo.increment_attempts.return_value = 3  # == MAX_RETRIES

        with patch.object(a, 'analyze_failure', side_effect=RuntimeError("fail")):
            a.run()

        a.ai_repo.mark_skipped.assert_called_once_with(
            'max_retries', build_failure_id=1
        )

    def test_does_not_skip_before_max_retries(self):
        """Before MAX_RETRIES, failure is NOT marked as skipped."""
        a = _build_analyzer()
        failures = [_make_failure(id=1)]
        a.ai_repo.get_pending_failures.return_value = failures
        a.ai_repo.skip_no_logs_timeouts.return_value = 0
        a.ai_repo.increment_attempts.return_value = 2  # < MAX_RETRIES

        with patch.object(a, 'analyze_failure', side_effect=RuntimeError("fail")):
            a.run()

        a.ai_repo.mark_skipped.assert_not_called()

    def test_skipped_new_returned(self):
        """skip_no_logs_timeouts count is included in stats."""
        a = _build_analyzer()
        a.ai_repo.get_pending_failures.return_value = []
        a.ai_repo.skip_no_logs_timeouts.return_value = 5
        result = a.run()
        assert result['skipped_new'] == 5

    def test_application_override(self):
        a = _build_analyzer()
        a.ai_repo.get_pending_failures.return_value = []
        a.ai_repo.skip_no_logs_timeouts.return_value = 0
        a.run(application='custom-app')
        a.ai_repo.skip_no_logs_timeouts.assert_called_once_with('custom-app')


# ---------------------------------------------------------------------------
# TestGetDependencyUpdates
# ---------------------------------------------------------------------------
class TestGetDependencyUpdates:

    def test_returns_formatted_updates(self):
        a = _build_analyzer()
        failure = _make_failure()

        mock_updates = [
            {'created': '2024-01-15T10:00:00', 'package': 'lodash',
             'from_version': '4.17.20', 'to_version': '4.17.21',
             'merged': True, 'pr_url': 'https://github.com/org/repo/pull/1'},
        ]

        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_dependency_updates.return_value = mock_updates
            result = a._get_dependency_updates(failure)

        assert 'MintMaker' in result
        assert 'lodash' in result
        assert '4.17.20' in result
        assert '(merged)' in result

    def test_returns_empty_on_error(self):
        a = _build_analyzer()
        failure = _make_failure()
        with patch('clients.konflux_client.KonfluxClient', side_effect=Exception("fail")):
            result = a._get_dependency_updates(failure)
        assert result == ''

    def test_returns_empty_when_no_component(self):
        a = _build_analyzer()
        failure = _make_failure(component_name=None)
        # component_name is falsy -> bail out immediately
        # Need to also set it to empty string since _make_failure would set it
        failure['component_name'] = ''
        result = a._get_dependency_updates(failure)
        assert result == ''

    def test_returns_empty_when_no_updates(self):
        a = _build_analyzer()
        failure = _make_failure()
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_dependency_updates.return_value = []
            result = a._get_dependency_updates(failure)
        assert result == ''


# ---------------------------------------------------------------------------
# TestGetReleaseContext
# ---------------------------------------------------------------------------
class TestGetReleaseContext:

    def test_returns_empty_when_no_application(self):
        a = _build_analyzer()
        failure = _make_failure(application='')
        result = a._get_release_context(failure)
        assert result == ''

    def test_returns_release_context_with_countdown(self):
        a = _build_analyzer()
        failure = _make_failure(application='rhoai-v2.19')

        mock_countdown = MagicMock()
        mock_countdown.phase = 'Development'
        mock_countdown.urgency = 'high'
        mock_countdown.message = '5 days to code freeze'

        with patch('api.routes.releases.get_schedule', return_value={}):
            with patch('api.routes.failures._compute_freeze_countdown',
                       return_value=mock_countdown):
                with patch('api.routes.failures.list_blockers',
                           side_effect=Exception("skip")):
                    result = a._get_release_context(failure)

        assert 'Release Context' in result
        assert 'Development' in result
        assert 'high' in result

    def test_handles_all_exceptions_gracefully(self):
        a = _build_analyzer()
        failure = _make_failure(application='rhoai-v2.19')

        with patch('api.routes.releases.get_schedule', side_effect=Exception("no")):
            result = a._get_release_context(failure)
        # Should not raise, returns empty string
        assert result == ''


# ---------------------------------------------------------------------------
# TestFilterErrorLines
# ---------------------------------------------------------------------------
class TestFilterErrorLines:

    def test_delegates_to_utils(self):
        a = _build_analyzer()
        with patch('log_filter.filter_error_lines',
                   return_value='ERROR: found') as mock_filter:
            result = a._filter_error_lines('big log data')
            mock_filter.assert_called_once_with('big log data', 20)
            assert result == 'ERROR: found'

    def test_returns_none_when_no_change(self):
        a = _build_analyzer()
        original = 'same log'
        with patch('log_filter.filter_error_lines',
                   return_value=original):
            result = a._filter_error_lines(original)
            assert result is None


# ---------------------------------------------------------------------------
# TestStoreField
# ---------------------------------------------------------------------------
class TestStoreField:

    def test_rejects_invalid_field(self):
        a = _build_analyzer()
        with pytest.raises(ValueError, match="invalid field"):
            a._store_field({'id': 1, 'component_name': 'x', 'pipelinerun_name': 'y'},
                           'bad_field', 'data')

    def test_stores_inline_when_small(self):
        a = _build_analyzer()
        failure = {'id': 1, 'component_name': 'x', 'pipelinerun_name': 'y'}
        mock_cursor = MagicMock()
        a.db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
        with patch('analyzers.build_failure_analyzer.should_offload', return_value=False):
            a._store_field(failure, 'build_logs', 'small data')
        mock_cursor.execute.assert_called_once()


# ---------------------------------------------------------------------------
# TestEnsureEnrichment
# ---------------------------------------------------------------------------
class TestEnsureEnrichment:

    def test_skips_when_already_enriched(self):
        a = _build_analyzer()
        failure = _make_failure(enriched_context={'some': 'data'})
        a._ensure_enrichment(failure)
        # No imports or orchestrator calls should happen

    def test_handles_enrichment_exception(self):
        a = _build_analyzer()
        failure = _make_failure(enriched_context=None)
        with patch('enrichment.enrichment_orchestrator.EnrichmentOrchestrator',
                   side_effect=Exception("module missing")):
            # Should not raise
            a._ensure_enrichment(failure)
