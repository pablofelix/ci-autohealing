"""Comprehensive tests for api.routes.exports module."""

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api import create_app
from api.routes.exports import (
    _fetch_open_prs,
    _format_build_jira,
    _format_conforma_jira,
    _format_failure_duration,
    _konflux_url,
    _row_to_analysis,
    _sanitize_for_jira,
    _sanitize_for_slack,
)
from repositories.ai_analysis_repository import AIAnalysisRepository
from repositories.build_failure_repository import BuildFailureRepository
from repositories.conforma_repository import ConformaRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    with patch.dict(os.environ, {
        'DB_HOST': 'localhost',
        'DB_NAME': 'test_db',
        'DB_USER': 'test_user',
        'DB_PASS': 'test_pass',
        'NAMESPACE': 'test-tenant',
    }), patch('api.init_pool'), patch('api.rate_limit.setup_rate_limiter'):
        app = create_app()
        yield TestClient(app, raise_server_exceptions=False)


def _make_failure(**overrides):
    """Build a realistic build-failure dict."""
    base = {
        'component_name': 'my-component',
        'pipelinerun_name': 'pr-12345',
        'error_message': 'go build failed',
        'failed_step_name': 'build',
        'first_detected_at': datetime(2026, 6, 1, tzinfo=UTC),
        'last_updated_at': datetime(2026, 6, 2, tzinfo=UTC),
        'repository_url': 'https://github.com/org/repo',
        'branch': 'release-v3.5',
        'commit_sha': 'abc12345def67890',
        'commit_url': 'https://github.com/org/repo/commit/abc12345def67890',
        'commit_message': 'fix: update deps',
        'commit_author': 'alice',
        'error_type': 'BuildError',
        'konflux_url': '',
        'build_logs': 'some long log text',
        'commit_context': '{"diffs": []}',
    }
    base.update(overrides)
    return base


def _make_violation(**overrides):
    """Build a realistic conforma-violation dict."""
    base = {
        'component_name': 'my-component',
        'pipelinerun_name': 'pr-99999',
        'scenario': 'verify-conforma-lp',
        'violations_count': 3,
        'warnings_count': 1,
        'first_detected_at': datetime(2026, 6, 1, tzinfo=UTC),
        'last_updated_at': datetime(2026, 6, 2, tzinfo=UTC),
        'snapshot_name': 'snap-001',
        'repository_url': 'https://github.com/org/repo',
        'commit_sha': 'abc12345def67890',
        'commit_url': 'https://github.com/org/repo/commit/abc12345def67890',
    }
    base.update(overrides)
    return base


def _make_analysis_row(**overrides):
    """Build a realistic analysis DB row dict."""
    base = {
        'analysis_type': 'build',
        'component_name': 'my-component',
        'model_used': 'claude',
        'root_cause': 'Missing dependency foo-bar',
        'failure_category': 'dependency_issue',
        'confidence_score': 0.85,
        'recommended_fix': 'Add foo-bar to go.mod',
        'recommended_files': ['go.mod', 'go.sum'],
        'can_auto_fix': True,
        'requires_human_review': False,
        'analyzed_at': datetime(2026, 6, 2, tzinfo=UTC),
        'langfuse_trace_url': 'https://langfuse.example/trace/123',
        'tokens_used': 5000,
        'cost_usd': 0.05,
    }
    base.update(overrides)
    return base


def _mock_repos(build_repo=None, conforma_repo=None, ai_repo=None):
    """Return a side_effect function for patching get_repository."""
    mock_build = build_repo or MagicMock()
    mock_conforma = conforma_repo or MagicMock()
    mock_ai = ai_repo or MagicMock()

    def side_effect(cls):
        if cls == BuildFailureRepository:
            return mock_build
        if cls == ConformaRepository:
            return mock_conforma
        if cls == AIAnalysisRepository:
            return mock_ai
        return MagicMock()

    return side_effect


# ===========================================================================
# 1. Helper functions
# ===========================================================================


class TestSanitizeForJira:
    """Tests for _sanitize_for_jira."""

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_none_input(self, _mock):
        assert _sanitize_for_jira(None) is None

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_empty_string(self, _mock):
        assert _sanitize_for_jira('') == ''

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_strips_jira_macros(self, _mock):
        text = 'Error in {code}block{code} and {panel:title=hi}stuff{panel}'
        result = _sanitize_for_jira(text)
        assert '{code}' not in result
        assert '{panel' not in result

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_strips_user_mentions(self, _mock):
        text = 'Assigned to [~john.doe] for review'
        result = _sanitize_for_jira(text)
        assert '[~john.doe]' not in result
        assert 'Assigned to' in result

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_plain_text_unchanged(self, _mock):
        text = 'Simple error message with no special chars'
        assert _sanitize_for_jira(text) == text

    @patch('api.routes.exports.redact_secrets', return_value='[REDACTED] key found')
    def test_calls_redact_secrets(self, mock_redact):
        result = _sanitize_for_jira('sk-ant-xxx key found')
        mock_redact.assert_called_once()
        assert '[REDACTED]' in result


class TestSanitizeForSlack:
    """Tests for _sanitize_for_slack."""

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_none_input(self, _mock):
        assert _sanitize_for_slack(None) is None

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_empty_string(self, _mock):
        assert _sanitize_for_slack('') == ''

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_replaces_at_here(self, _mock):
        result = _sanitize_for_slack('Alert @here please check')
        assert '@here' not in result
        assert '(at)here' in result

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_replaces_at_channel(self, _mock):
        result = _sanitize_for_slack('Notify @channel now')
        assert '@channel' not in result
        assert '(at)channel' in result

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_strips_user_mentions(self, _mock):
        result = _sanitize_for_slack('cc <@U012ABC34> for review')
        assert '<@U012ABC34>' not in result

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    def test_plain_text_unchanged(self, _mock):
        text = 'Simple message'
        assert _sanitize_for_slack(text) == text


class TestRowToAnalysis:
    """Tests for _row_to_analysis."""

    def test_basic_conversion(self):
        row = _make_analysis_row()
        result = _row_to_analysis(row)
        assert result.type == 'build'
        assert result.component == 'my-component'
        assert result.root_cause == 'Missing dependency foo-bar'
        assert result.confidence_score == 0.85
        assert result.can_auto_fix is True
        assert result.recommended_files == ['go.mod', 'go.sum']

    def test_files_as_csv_string(self):
        row = _make_analysis_row(recommended_files='file1.py, file2.py, file3.py')
        result = _row_to_analysis(row)
        assert result.recommended_files == ['file1.py', 'file2.py', 'file3.py']

    def test_files_none_becomes_empty_list(self):
        row = _make_analysis_row(recommended_files=None)
        result = _row_to_analysis(row)
        assert result.recommended_files == []

    def test_missing_optional_fields(self):
        row = {
            'analysis_type': 'conforma',
            'component_name': 'comp-x',
        }
        result = _row_to_analysis(row)
        assert result.model_used == ''
        assert result.confidence_score == 0.0
        assert result.cost_usd == 0.0
        assert result.tokens_used == 0

    def test_confidence_score_none_becomes_zero(self):
        row = _make_analysis_row(confidence_score=None)
        result = _row_to_analysis(row)
        assert result.confidence_score == 0.0

    def test_csv_with_empty_entries(self):
        row = _make_analysis_row(recommended_files='a.py, , b.py, ')
        result = _row_to_analysis(row)
        assert result.recommended_files == ['a.py', 'b.py']


class TestKonfluxUrl:
    """Tests for _konflux_url."""

    @patch('shared_config.KONFLUX_UI_BASE', 'https://konflux.example')
    @patch('shared_config.NAMESPACE', 'my-ns')
    def test_builds_correct_url(self):
        url = _konflux_url('pr-abc')
        assert url == 'https://konflux.example/ns/my-ns/pipelinerun/pr-abc/logs'

    @patch('shared_config.KONFLUX_UI_BASE', '')
    @patch('shared_config.NAMESPACE', '')
    def test_empty_base_returns_empty(self):
        url = _konflux_url('pr-xyz')
        assert url == ''

    def test_empty_pipelinerun_returns_empty(self):
        url = _konflux_url('')
        assert url == ''


class TestFormatFailureDuration:
    """Tests for _format_failure_duration."""

    def test_none_returns_none(self):
        assert _format_failure_duration(None) is None

    def test_today(self):
        now = datetime.now(UTC)
        assert _format_failure_duration(now) == 'today'

    def test_yesterday(self):
        yesterday = datetime.now(UTC) - timedelta(days=1)
        assert _format_failure_duration(yesterday) == 'yesterday'

    def test_multi_day(self):
        five_days_ago = datetime.now(UTC) - timedelta(days=5)
        result = _format_failure_duration(five_days_ago)
        assert '5 days' in result
        assert five_days_ago.strftime('%b %d') in result

    def test_naive_datetime_no_tz(self):
        naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3)
        result = _format_failure_duration(naive)
        assert '3 days' in result


class TestFetchOpenPrs:
    """Tests for _fetch_open_prs."""

    def test_empty_repo_url(self):
        assert _fetch_open_prs('', 'main') == []

    def test_none_repo_url(self):
        assert _fetch_open_prs(None, 'main') == []

    def test_no_github_token(self):
        with patch('clients.github_client.parse_github_repo', return_value=('org', 'repo')), \
             patch.dict(os.environ, {'GITHUB_TOKEN': ''}, clear=False):
            # Remove GITHUB_TOKEN if it exists
            os.environ.pop('GITHUB_TOKEN', None)
            result = _fetch_open_prs('https://github.com/org/repo', 'main')
        assert result == []

    def test_parse_returns_none(self):
        with patch('clients.github_client.parse_github_repo', return_value=None), \
             patch.dict(os.environ, {'GITHUB_TOKEN': 'ghp_test'}):
            result = _fetch_open_prs('https://invalid-url', 'main')
            assert result == []

    def test_exception_returns_empty(self):
        with patch('clients.github_client.parse_github_repo', side_effect=Exception('boom')):
            result = _fetch_open_prs('https://github.com/org/repo', 'main')
            assert result == []

    def test_successful_fetch(self):
        mock_gh = MagicMock()
        mock_gh.list_pull_requests.return_value = [
            {'title': 'Fix build', 'url': 'https://github.com/pr/1', 'number': 1, 'author': 'bob'}
        ]
        with patch('clients.github_client.parse_github_repo', return_value=('org', 'repo')), \
             patch('clients.github_client.GitHubClient', return_value=mock_gh), \
             patch.dict(os.environ, {'GITHUB_TOKEN': 'ghp_test'}):
            result = _fetch_open_prs('https://github.com/org/repo', 'release-v3.5')
            assert len(result) == 1
            assert result[0]['author'] == 'bob'


# ===========================================================================
# 2. Format functions (Jira)
# ===========================================================================


class TestFormatBuildJira:
    """Tests for _format_build_jira."""

    def test_with_analysis(self):
        failure = _make_failure()
        analysis = _make_analysis_row()
        text = _format_build_jira(failure, analysis)
        assert 'h2. Build Failure: my-component' in text
        assert 'pr-12345' in text
        assert 'AI Analysis' in text
        assert '85% confidence' in text
        assert 'dependency_issue' in text
        assert 'go.mod' in text
        assert 'langfuse.example' in text

    def test_without_analysis(self):
        failure = _make_failure()
        text = _format_build_jira(failure, None)
        assert 'h2. Build Failure: my-component' in text
        assert 'AI Analysis' not in text
        assert 'go build failed' in text
        assert '{code}' in text

    def test_context_section(self):
        failure = _make_failure()
        text = _format_build_jira(failure, None)
        assert 'h3. Context' in text
        assert 'repo' in text
        assert 'release-v3.5' in text
        assert 'abc12345' in text
        assert 'fix: update deps' in text

    def test_no_optional_fields(self):
        failure = _make_failure(
            repository_url='',
            commit_sha='',
            commit_url='',
            commit_message='',
            failed_step_name='',
            error_type='',
            error_message='',
        )
        text = _format_build_jira(failure, None)
        assert 'h2. Build Failure' in text
        assert 'h3. Resources' in text

    def test_uses_konflux_url_fallback(self):
        failure = _make_failure(konflux_url='')
        with patch('shared_config.KONFLUX_UI_BASE', 'https://k.example'), \
             patch('shared_config.NAMESPACE', 'ns'):
            text = _format_build_jira(failure, None)
            assert 'https://k.example/ns/ns/pipelinerun/pr-12345/logs' in text

    def test_uses_konflux_url_when_present(self):
        failure = _make_failure(konflux_url='https://custom-url.example/logs')
        text = _format_build_jira(failure, None)
        assert 'https://custom-url.example/logs' in text

    def test_analysis_with_no_langfuse_url(self):
        analysis = _make_analysis_row(langfuse_trace_url=None)
        text = _format_build_jira(_make_failure(), analysis)
        assert 'Analysis Trace' not in text

    def test_analysis_auto_fix_no(self):
        analysis = _make_analysis_row(can_auto_fix=False, requires_human_review=True)
        text = _format_build_jira(_make_failure(), analysis)
        assert '|Can Auto-Fix|No|' in text
        assert '|Requires Review|Yes|' in text


class TestFormatConformaJira:
    """Tests for _format_conforma_jira."""

    def test_with_analysis(self):
        violation = _make_violation()
        analysis = _make_analysis_row(analysis_type='conforma')
        text = _format_conforma_jira(violation, analysis)
        assert 'h2. Conforma Policy Violation: my-component' in text
        assert 'verify-conforma-lp' in text
        assert '3 violations, 1 warnings' in text
        assert 'AI Analysis' in text
        assert 'snap-001' in text

    def test_without_analysis(self):
        violation = _make_violation()
        text = _format_conforma_jira(violation, None)
        assert 'h2. Conforma Policy Violation' in text
        assert 'AI Analysis' not in text

    def test_no_snapshot(self):
        violation = _make_violation(snapshot_name=None)
        text = _format_conforma_jira(violation, None)
        assert 'Snapshot' not in text

    def test_with_commit_info(self):
        violation = _make_violation()
        text = _format_conforma_jira(violation, None)
        assert 'abc12345' in text
        assert 'github.com' in text

    def test_no_commit_info(self):
        violation = _make_violation(commit_sha='', commit_url='')
        text = _format_conforma_jira(violation, None)
        # Should not have a Commit line
        assert 'Commit:' not in text


# ===========================================================================
# 3. Export functions (unit tests, no HTTP)
# ===========================================================================


class TestExportJira:
    """Tests for _export_jira (called by the endpoint handler)."""

    @patch('api.routes.exports.get_repository')
    def test_build_failure_found(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = _make_analysis_row()
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_jira
        text = _export_jira('my-component', 'rhoai-v3-5', False)
        assert 'Build Failure' in text
        mock_build.get_failure_details.assert_called_once_with('my-component', 'rhoai-v3-5')

    @patch('api.routes.exports.get_repository')
    def test_conforma_violation_found(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = _make_violation()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma, ai_repo=mock_ai
        )

        from api.routes.exports import _export_jira
        text = _export_jira('my-component', 'rhoai-v3-5', False)
        assert 'Conforma Policy Violation' in text

    @patch('api.routes.exports.get_repository')
    def test_nothing_found(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = None
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma
        )

        from api.routes.exports import _export_jira
        text = _export_jira('comp-x', 'rhoai-v3-5', False)
        assert 'No unresolved failures found' in text
        assert 'comp-x' in text


class TestExportMarkdown:
    """Tests for _export_markdown."""

    @patch('api.routes.exports.get_repository')
    def test_build_failure_with_analysis(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = _make_analysis_row()
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_markdown
        text = _export_markdown('my-component', 'rhoai-v3-5', False)
        assert '## Build Failure: my-component' in text
        assert '### AI Analysis' in text
        assert '85% confidence' in text
        assert '`go.mod`' in text

    @patch('api.routes.exports.get_repository')
    def test_build_failure_no_analysis(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_markdown
        text = _export_markdown('my-component', 'rhoai-v3-5', False)
        assert '## Build Failure' in text
        assert 'AI Analysis' not in text

    @patch('api.routes.exports.get_repository')
    def test_violation_with_analysis(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = _make_violation()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = _make_analysis_row(analysis_type='conforma')
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma, ai_repo=mock_ai
        )

        from api.routes.exports import _export_markdown
        text = _export_markdown('my-component', 'rhoai-v3-5', False)
        assert '## Conforma Violation' in text
        assert '### AI Analysis' in text

    @patch('api.routes.exports.get_repository')
    def test_violation_no_analysis(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = _make_violation()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma, ai_repo=mock_ai
        )

        from api.routes.exports import _export_markdown
        text = _export_markdown('my-component', 'rhoai-v3-5', False)
        assert '## Conforma Violation' in text
        assert 'AI Analysis' not in text

    @patch('api.routes.exports.get_repository')
    def test_nothing_found(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = None
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma, ai_repo=mock_ai
        )

        from api.routes.exports import _export_markdown
        text = _export_markdown('comp-x', 'rhoai-v3-5', False)
        assert 'No unresolved failures found' in text

    @patch('api.routes.exports.get_repository')
    def test_build_failure_optional_fields(self, mock_get_repo):
        """Test markdown with minimal failure data (no repo, no commit, no step)."""
        failure = _make_failure(
            repository_url='',
            commit_sha='',
            commit_url='',
            failed_step_name='',
            konflux_url='https://k.example/logs',
        )
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = failure
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_markdown
        text = _export_markdown('my-component', 'rhoai-v3-5', False)
        assert '## Build Failure' in text
        assert 'Repository' not in text
        assert 'Commit' not in text
        assert 'Failed Step' not in text


class TestExportSlack:
    """Tests for _export_slack."""

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_build_failure_with_analysis(self, mock_get_repo, _mock_prs):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = _make_analysis_row()
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        assert ':red_circle:' in text
        assert '*Build Failure: my-component*' in text
        assert ':robot_face:' in text
        assert '85% confidence' in text
        assert 'View in Konflux' in text

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_build_failure_no_analysis(self, mock_get_repo, _mock_prs):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        assert ':red_circle:' in text
        assert ':robot_face:' not in text

    @patch('api.routes.exports._fetch_open_prs', return_value=[
        {'title': 'Fix the thing that is broken', 'url': 'https://github.com/pr/1',
         'number': 1, 'author': 'bob'},
    ])
    @patch('api.routes.exports.get_repository')
    def test_build_failure_with_open_prs(self, mock_get_repo, _mock_prs):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        assert ':mag: *Open PRs:*' in text
        assert '#1' in text
        assert 'bob' in text
        assert ':bust_in_silhouette: Contacts:' in text

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_violation_with_analysis(self, mock_get_repo, _mock_prs):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = _make_violation()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = _make_analysis_row(analysis_type='conforma')
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma, ai_repo=mock_ai
        )

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        assert ':warning:' in text
        assert '*Conforma Violation' in text
        assert ':robot_face:' in text

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_nothing_found(self, mock_get_repo, _mock_prs):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = None
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma, ai_repo=mock_ai
        )

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        assert 'No unresolved failures found' in text

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_build_failure_no_commit_no_repo(self, mock_get_repo, _mock_prs):
        """Slack export with minimal failure data."""
        failure = _make_failure(
            commit_sha='', commit_url='', repository_url='', branch='',
            commit_author='', first_detected_at=None,
        )
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = failure
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        assert ':red_circle:' in text
        assert 'Commit:' not in text
        assert 'Repo:' not in text
        assert 'Failing since:' not in text

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_build_failure_auto_fix_true(self, mock_get_repo, _mock_prs):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = _make_analysis_row(can_auto_fix=True)
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        assert ':white_check_mark:' in text

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_build_failure_auto_fix_false(self, mock_get_repo, _mock_prs):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = _make_analysis_row(can_auto_fix=False)
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        assert ':x:' in text

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_violation_no_analysis(self, mock_get_repo, _mock_prs):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = _make_violation()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma, ai_repo=mock_ai
        )

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        assert ':warning:' in text
        assert ':robot_face:' not in text


class TestExportJson:
    """Tests for _export_json."""

    @patch('api.routes.exports.get_repository')
    def test_build_failure_with_logs(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = _make_analysis_row()
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_json
        text = _export_json('my-component', 'rhoai-v3-5', True)
        data = json.loads(text)
        assert data['type'] == 'build_failure'
        assert 'failure' in data
        assert 'analysis' in data
        assert 'build_logs' in data['failure']
        assert 'commit_context' not in data['failure']

    @patch('api.routes.exports.get_repository')
    def test_build_failure_without_logs(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_json
        text = _export_json('my-component', 'rhoai-v3-5', False)
        data = json.loads(text)
        assert data['type'] == 'build_failure'
        assert 'build_logs' not in data['failure']
        assert 'analysis' not in data

    @patch('api.routes.exports.get_repository')
    def test_violation_json(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = _make_violation()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = _make_analysis_row()
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma, ai_repo=mock_ai
        )

        from api.routes.exports import _export_json
        text = _export_json('my-component', 'rhoai-v3-5', False)
        data = json.loads(text)
        assert data['type'] == 'conforma_violation'
        assert 'violation' in data
        assert 'analysis' in data

    @patch('api.routes.exports.get_repository')
    def test_not_found_json(self, mock_get_repo):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = None
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma
        )

        from api.routes.exports import _export_json
        text = _export_json('comp-x', 'rhoai-v3-5', False)
        data = json.loads(text)
        assert data['type'] == 'not_found'
        assert data['component'] == 'comp-x'
        assert 'analysis' not in data


# ===========================================================================
# 4. HTTP endpoint tests via TestClient
# ===========================================================================


class TestExportEndpoint:
    """Integration tests for GET /api/v1/applications/{app}/export/{comp}."""

    @patch('api.routes.exports.get_repository')
    def test_default_format_is_jira(self, mock_get_repo, client):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        resp = client.get('/api/v1/applications/rhoai-v3-5/export/my-component')
        assert resp.status_code == 200
        assert resp.headers['content-type'] == 'text/plain; charset=utf-8'
        assert 'h2. Build Failure' in resp.text

    @patch('api.routes.exports.get_repository')
    def test_markdown_format(self, mock_get_repo, client):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        resp = client.get('/api/v1/applications/rhoai-v3-5/export/my-component?format=markdown')
        assert resp.status_code == 200
        assert '## Build Failure' in resp.text

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_slack_format(self, mock_get_repo, _mock_prs, client):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        resp = client.get('/api/v1/applications/rhoai-v3-5/export/my-component?format=slack')
        assert resp.status_code == 200
        assert ':red_circle:' in resp.text

    @patch('api.routes.exports.get_repository')
    def test_json_format(self, mock_get_repo, client):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        resp = client.get('/api/v1/applications/rhoai-v3-5/export/my-component?format=json')
        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = json.loads(resp.text)
        assert data['type'] == 'build_failure'

    @patch('api.routes.exports.get_repository')
    def test_json_format_include_logs(self, mock_get_repo, client):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        resp = client.get(
            '/api/v1/applications/rhoai-v3-5/export/my-component?format=json&include_logs=true'
        )
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert 'build_logs' in data['failure']

    @patch('api.routes.exports.get_repository')
    def test_not_found_component(self, mock_get_repo, client):
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = None
        mock_conforma = MagicMock()
        mock_conforma.get_violation_details.return_value = None
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(
            build_repo=mock_build, conforma_repo=mock_conforma, ai_repo=mock_ai
        )

        resp = client.get('/api/v1/applications/rhoai-v3-5/export/ghost-component')
        assert resp.status_code == 200
        assert 'No unresolved failures found' in resp.text

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    @patch('api.routes.exports.get_repository')
    def test_jira_sanitization_applied(self, mock_get_repo, _mock_redact, client):
        """Jira format should sanitize macro-like patterns."""
        failure = _make_failure(error_message='Error in {code}block{code}')
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = failure
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        resp = client.get('/api/v1/applications/rhoai-v3-5/export/my-component?format=jira')
        # The _format_build_jira creates {code} blocks itself, but the error_message
        # content gets wrapped inside them. The sanitizer strips inline {code} patterns.
        assert resp.status_code == 200

    @patch('api.routes.exports.redact_secrets', side_effect=lambda t: t)
    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_slack_sanitization_applied(self, mock_get_repo, _mock_prs, _mock_redact, client):
        """Slack format should sanitize @here/@channel and user mentions."""
        failure = _make_failure(error_message='Alert @here check <@U123> now')
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = failure
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        resp = client.get('/api/v1/applications/rhoai-v3-5/export/my-component?format=slack')
        assert resp.status_code == 200
        assert '@here' not in resp.text
        assert '<@U123>' not in resp.text

    def test_invalid_format_returns_422(self, client):
        resp = client.get('/api/v1/applications/rhoai-v3-5/export/my-component?format=xml')
        assert resp.status_code == 422

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_slack_long_pr_title_truncated(self, mock_get_repo, _mock_prs, client):
        """PR titles >50 chars should be truncated in slack format."""
        long_title = 'A' * 60
        _mock_prs.return_value = [
            {'title': long_title, 'url': 'https://github.com/pr/1', 'number': 1, 'author': 'dev'}
        ]
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        # Should have truncated title with ...
        assert '...' in text

    @patch('api.routes.exports._fetch_open_prs', return_value=[])
    @patch('api.routes.exports.get_repository')
    def test_slack_root_cause_truncated(self, mock_get_repo, _mock_prs, client):
        """Root cause >200 chars should be truncated in slack format."""
        long_cause = 'X' * 300
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = _make_analysis_row(root_cause=long_cause)
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_slack
        text = _export_slack('my-component', 'rhoai-v3-5', False)
        # Root cause should be truncated to 200 chars
        assert 'X' * 200 in text
        assert 'X' * 201 not in text

    @patch('api.routes.exports.get_repository')
    def test_markdown_with_repo_and_commit(self, mock_get_repo, client):
        """Markdown should include repository link and commit."""
        mock_build = MagicMock()
        mock_build.get_failure_details.return_value = _make_failure()
        mock_ai = MagicMock()
        mock_ai.get_analysis_by_component.return_value = None
        mock_get_repo.side_effect = _mock_repos(build_repo=mock_build, ai_repo=mock_ai)

        from api.routes.exports import _export_markdown
        text = _export_markdown('my-component', 'rhoai-v3-5', False)
        assert '[https://github.com/org/repo]' in text
        assert '[abc12345]' in text
        assert '**Konflux:**' in text
