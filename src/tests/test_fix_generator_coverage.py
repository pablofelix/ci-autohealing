"""Comprehensive tests for fix_generator.py — covers ~844 uncovered lines.

Covers: load/data functions, prompt building, response parsing, PR mode,
conforma fix modes (hermetic, vendor label, RPM repo, deprecated task,
unpinned task, untrusted image), Jira mode, push_pr_and_record,
deterministic fixers, edge cases, error paths.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest

from fixers.fix_generator import (
    _format_enrichment_section,
    _format_pattern_section,
    _format_previous_attempts,
    _push_pr_and_record,
    build_fix_prompt,
    conforma_branch_name,
    determine_target_repo,
    fetch_files_for_fix,
    generate_unified_diff,
    load_conforma_and_analysis,
    load_failure_and_analysis,
    run_conforma_hermetic_mode,
    run_conforma_pr_mode,
    run_conforma_rpm_repo_id_mode,
    run_conforma_sbom_vendor_label_mode,
    run_conforma_unpinned_task_mode,
    run_conforma_untrusted_image_mode,
    run_jira_mode,
    run_pr_mode,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(llm=True, github_token='gh-tok', jira=None, db=None):
    """Build a mock CollectorConfig."""
    cfg = MagicMock()
    cfg.llm = MagicMock() if llm else None
    cfg.github_token = github_token
    cfg.jira = jira
    cfg.db = db or MagicMock()
    return cfg


def _make_failure(**overrides):
    """Build a sample failure dict."""
    base = {
        'id': 42,
        'component_name': 'my-component',
        'repository_url': 'https://github.com/org/repo',
        'branch': 'rhoai-2.16',
        'commit_sha': 'abc123',
        'error_type': 'build_error',
        'error_message': 'go: module not found',
        'failed_step_name': 'build',
        'build_logs': 'some logs here',
        'application': 'rhoai-v2-16',
        'enriched_context': None,
        'previous_attempts': [],
    }
    base.update(overrides)
    return base


def _make_analysis(**overrides):
    """Build a sample analysis dict."""
    base = {
        'failure_category': 'dependency_error',
        'confidence_score': 0.92,
        'root_cause': 'Missing go module',
        'recommended_fix': 'Update go.mod',
        'recommended_files': ['go.mod', 'go.sum'],
        'can_auto_fix': True,
        'requires_human_review': False,
        'pattern_name': None,
        'typical_fix': None,
        'doc_context': None,
        'occurrence_count': 0,
        'pattern_confidence': None,
    }
    base.update(overrides)
    return base


def _make_conforma(**overrides):
    """Build a sample conforma_results dict."""
    base = {
        'id': 99,
        'component_name': 'my-component',
        'repository_url': 'https://github.com/org/repo',
        'scenario': 'release',
        'violations_count': 1,
        'warnings_count': 0,
        'violation_summary': 'policy_deprecated_task: 1 violation',
        'violation_details': None,
        'commit_sha': 'def456',
        'application': 'rhoai-v2-16',
    }
    base.update(overrides)
    return base


def _make_llm_response(content='', tool_calls=None):
    """Build a mock LLMResponse."""
    resp = MagicMock()
    resp.content = content
    resp.tool_calls = tool_calls or []
    resp.input_tokens = 1000
    resp.output_tokens = 500
    resp.model = 'test-model'
    resp.stop_reason = 'end_turn'
    return resp


def _mock_cursor_with_rows(rows, columns):
    """Create a mock cursor that returns the given rows."""
    cur = MagicMock()
    cur.description = [(col,) for col in columns]
    cur.fetchone.return_value = rows[0] if rows else None
    cur.fetchall.return_value = rows[1:] if len(rows) > 1 else []
    return cur


# ---------------------------------------------------------------------------
# conforma_branch_name
# ---------------------------------------------------------------------------

class TestConformaBranchName:
    def test_basic(self):
        assert conforma_branch_name('comp', 42) == 'ci-autohealing/conforma/comp/42'

    def test_with_dashes(self):
        result = conforma_branch_name('odh-dashboard-v3-4', 100)
        assert result == 'ci-autohealing/conforma/odh-dashboard-v3-4/100'


# ---------------------------------------------------------------------------
# generate_unified_diff
# ---------------------------------------------------------------------------

class TestGenerateUnifiedDiff:
    def test_identical_content(self):
        assert generate_unified_diff('file.txt', 'hello', 'hello') == ''

    def test_content_changed(self):
        diff = generate_unified_diff('file.txt', 'old line', 'new line')
        assert 'a/file.txt' in diff
        assert 'b/file.txt' in diff
        assert '-old line' in diff
        assert '+new line' in diff

    def test_none_old_content(self):
        diff = generate_unified_diff('file.txt', None, 'new line')
        assert '+new line' in diff

    def test_none_new_content(self):
        diff = generate_unified_diff('file.txt', 'old line', None)
        assert '-old line' in diff

    def test_both_none(self):
        assert generate_unified_diff('file.txt', None, None) == ''

    def test_empty_strings(self):
        assert generate_unified_diff('file.txt', '', '') == ''

    def test_multiline(self):
        old = 'line1\nline2\nline3'
        new = 'line1\nchanged\nline3'
        diff = generate_unified_diff('f.txt', old, new)
        assert '-line2' in diff
        assert '+changed' in diff


# ---------------------------------------------------------------------------
# determine_target_repo
# ---------------------------------------------------------------------------

class TestDetermineTargetRepo:
    def test_default_uses_failure_repo(self):
        failure = {'repository_url': 'https://github.com/org/repo'}
        result = determine_target_repo(failure, None, {})
        assert result == 'https://github.com/org/repo'

    def test_konflux_central_override(self):
        failure = {'repository_url': 'https://github.com/org/repo'}
        fix_resp = {'target_repo': 'konflux-central'}
        with patch('fixers.fix_generator.KONFLUX_CENTRAL_REPO', 'https://github.com/org/central'):
            result = determine_target_repo(failure, None, fix_resp)
        assert result == 'https://github.com/org/central'

    def test_non_central_target_uses_failure(self):
        failure = {'repository_url': 'https://github.com/org/repo'}
        fix_resp = {'target_repo': 'component'}
        result = determine_target_repo(failure, None, fix_resp)
        assert result == 'https://github.com/org/repo'


# ---------------------------------------------------------------------------
# fetch_files_for_fix
# ---------------------------------------------------------------------------

class TestFetchFilesForFix:
    def test_no_recommended_files(self):
        gh = MagicMock()
        result = fetch_files_for_fix(gh, 'https://github.com/org/repo', 'main', [])
        assert result == {}

    def test_none_recommended_files(self):
        gh = MagicMock()
        result = fetch_files_for_fix(gh, 'https://github.com/org/repo', 'main', None)
        assert result == {}

    @patch('fixers.fix_generator.parse_github_repo', return_value=None)
    def test_invalid_repo_url(self, _):
        gh = MagicMock()
        result = fetch_files_for_fix(gh, 'invalid-url', 'main', ['go.mod'])
        assert result == {}

    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_fetches_files(self, _):
        gh = MagicMock()
        gh.get_file_content.return_value = 'module example'
        result = fetch_files_for_fix(gh, 'https://github.com/org/repo', 'main', ['go.mod'])
        assert result == {'go.mod': 'module example'}
        gh.get_file_content.assert_called_once_with('org', 'repo', 'go.mod', ref='main')

    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_file_not_found_returns_none_value(self, _):
        gh = MagicMock()
        gh.get_file_content.return_value = None
        result = fetch_files_for_fix(gh, 'https://github.com/org/repo', 'main', ['missing.txt'])
        assert result == {'missing.txt': None}

    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_uses_branch_or_defaults_to_main(self, _):
        gh = MagicMock()
        gh.get_file_content.return_value = 'content'
        fetch_files_for_fix(gh, 'https://github.com/org/repo', None, ['f.txt'])
        gh.get_file_content.assert_called_with('org', 'repo', 'f.txt', ref='main')


# ---------------------------------------------------------------------------
# _format_pattern_section
# ---------------------------------------------------------------------------

class TestFormatPatternSection:
    def test_none_analysis(self):
        assert _format_pattern_section(None) == []

    def test_no_pattern_name(self):
        assert _format_pattern_section({'pattern_name': None}) == []

    def test_with_pattern(self):
        analysis = {
            'pattern_name': 'go_module_missing',
            'occurrence_count': 5,
            'pattern_confidence': 0.85,
            'typical_fix': 'Run go mod tidy',
            'doc_context': 'See docs about go modules',
        }
        result = _format_pattern_section(analysis)
        text = '\n'.join(result)
        assert 'go_module_missing' in text
        assert '5 occurrences' in text
        assert '85%' in text
        assert 'go mod tidy' in text
        assert 'docs about go modules' in text

    def test_without_typical_fix(self):
        analysis = {
            'pattern_name': 'some_pattern',
            'occurrence_count': 1,
            'pattern_confidence': 0.5,
            'typical_fix': None,
            'doc_context': None,
        }
        result = _format_pattern_section(analysis)
        text = '\n'.join(result)
        assert 'some_pattern' in text
        assert 'successful fix' not in text

    def test_none_pattern_confidence(self):
        analysis = {
            'pattern_name': 'test',
            'occurrence_count': 0,
            'pattern_confidence': None,
        }
        result = _format_pattern_section(analysis)
        text = '\n'.join(result)
        assert '0%' in text


# ---------------------------------------------------------------------------
# _format_enrichment_section
# ---------------------------------------------------------------------------

class TestFormatEnrichmentSection:
    def test_none_context(self):
        assert _format_enrichment_section(None) == []

    def test_non_dict_context(self):
        assert _format_enrichment_section("not a dict") == []

    def test_empty_dict(self):
        # Empty dict is falsy in Python, so treated same as None
        result = _format_enrichment_section({})
        assert result == []

    def test_dict_with_header_only(self):
        # Non-empty dict with no recognized keys still gets the header
        result = _format_enrichment_section({'unrecognized_key': 'value'})
        assert 'Enrichment Context' in '\n'.join(result)

    def test_dependency_changes(self):
        ctx = {
            'dependency_changes': {
                'go.mod': {'diff': '+ github.com/foo/bar v1.2.3'},
            },
        }
        result = _format_enrichment_section(ctx)
        text = '\n'.join(result)
        assert 'Dependency Changes' in text
        assert 'go.mod' in text

    def test_dependency_changes_string_value(self):
        ctx = {
            'dependency_changes': {
                'go.mod': 'some string diff',
            },
        }
        result = _format_enrichment_section(ctx)
        text = '\n'.join(result)
        assert 'some string diff' in text

    def test_related_failures(self):
        ctx = {
            'related_failures': [
                {
                    'component_name': 'odh-dashboard',
                    'failure_category': 'build_error',
                    'root_cause': 'Missing module',
                },
            ],
        }
        result = _format_enrichment_section(ctx)
        text = '\n'.join(result)
        assert 'Related Failures' in text
        assert 'odh-dashboard' in text

    def test_related_failures_cross_app(self):
        ctx = {
            'related_failures': [
                {
                    'component_name': 'comp-a',
                    'failure_category': 'dep_err',
                    'root_cause': 'Missing',
                    'cross_app': True,
                    'source_application': 'other-app',
                },
            ],
        }
        result = _format_enrichment_section(ctx)
        text = '\n'.join(result)
        assert 'cross-app: other-app' in text

    def test_related_failures_truncated_to_three(self):
        ctx = {
            'related_failures': [
                {'component_name': f'comp-{i}', 'error_type': 'err', 'error_message': 'msg'}
                for i in range(5)
            ],
        }
        result = _format_enrichment_section(ctx)
        text = '\n'.join(result)
        assert 'comp-0' in text
        assert 'comp-2' in text
        assert 'comp-3' not in text  # Only first 3


# ---------------------------------------------------------------------------
# _format_previous_attempts
# ---------------------------------------------------------------------------

class TestFormatPreviousAttempts:
    def test_empty(self):
        assert _format_previous_attempts([]) == []

    def test_none(self):
        assert _format_previous_attempts(None) == []

    def test_single_attempt(self):
        attempts = [
            {
                'attempt_number': 1,
                'changes_description': 'Updated go.mod',
                'files_modified': ['go.mod'],
                'was_successful': False,
                'verification_notes': 'Build still fails',
                'status': 'failed',
                'pr_url': 'https://github.com/org/repo/pull/1',
            },
        ]
        result = _format_previous_attempts(attempts)
        text = '\n'.join(result)
        assert 'Previous Fix Attempts' in text
        assert 'Attempt #1' in text
        assert 'Updated go.mod' in text
        assert 'failed' in text
        assert 'Build still fails' in text
        assert 'DIFFERENT approach' in text

    def test_attempt_no_files(self):
        attempts = [
            {
                'attempt_number': 1,
                'changes_description': 'Tried something',
                'files_modified': None,
                'status': 'pending',
            },
        ]
        result = _format_previous_attempts(attempts)
        text = '\n'.join(result)
        assert 'no files recorded' in text


# ---------------------------------------------------------------------------
# build_fix_prompt
# ---------------------------------------------------------------------------

class TestBuildFixPrompt:
    def test_basic_prompt(self):
        failure = _make_failure()
        prompt = build_fix_prompt(failure, None, {})
        assert 'my-component' in prompt
        assert 'build_error' in prompt
        assert 'go: module not found' in prompt
        assert 'Generate a specific fix' in prompt

    def test_with_analysis(self):
        failure = _make_failure()
        analysis = _make_analysis()
        prompt = build_fix_prompt(failure, analysis, {})
        assert 'AI Analysis' in prompt
        assert 'dependency_error' in prompt
        assert 'Missing go module' in prompt

    def test_with_file_contents(self):
        failure = _make_failure()
        files = {'go.mod': 'module example\ngo 1.21'}
        prompt = build_fix_prompt(failure, None, files)
        assert 'Current file contents' in prompt
        assert 'go.mod' in prompt
        assert 'module example' in prompt

    def test_file_not_fetchable(self):
        failure = _make_failure()
        files = {'missing.go': None}
        prompt = build_fix_prompt(failure, None, files)
        assert 'could not fetch' in prompt

    def test_with_build_logs(self):
        failure = _make_failure(build_logs='ERROR: build failed at step 3')
        prompt = build_fix_prompt(failure, None, {})
        assert 'Build logs' in prompt
        assert 'build failed at step 3' in prompt

    def test_no_build_logs(self):
        failure = _make_failure(build_logs=None)
        prompt = build_fix_prompt(failure, None, {})
        assert 'Build logs' not in prompt

    def test_with_enriched_context(self):
        failure = _make_failure(enriched_context={'dependency_changes': {'go.mod': {'diff': '+new dep'}}})
        prompt = build_fix_prompt(failure, None, {})
        assert 'Enrichment Context' in prompt

    def test_with_previous_attempts(self):
        failure = _make_failure(previous_attempts=[
            {'attempt_number': 1, 'changes_description': 'Try 1', 'files_modified': ['f.txt'], 'status': 'failed'},
        ])
        prompt = build_fix_prompt(failure, None, {})
        assert 'Previous Fix Attempts' in prompt

    def test_missing_optional_fields(self):
        failure = _make_failure(error_type=None, failed_step_name=None, error_message=None, application=None)
        prompt = build_fix_prompt(failure, None, {})
        assert 'my-component' in prompt


# ---------------------------------------------------------------------------
# load_failure_and_analysis
# ---------------------------------------------------------------------------

class TestLoadFailureAndAnalysis:
    def _setup_db(self, failure_rows=None, analysis_row=None, attempt_rows=None):
        """Build a mock db_conn with cursor responses."""
        db_conn = MagicMock()
        conn = MagicMock()
        cur = MagicMock()

        failure_cols = [
            'id', 'component_name', 'repository_url', 'branch', 'commit_sha',
            'error_type', 'error_message', 'failed_step_name', 'build_logs',
            'application', 'enriched_context',
        ]
        analysis_cols = [
            'failure_category', 'confidence_score', 'root_cause',
            'recommended_fix', 'recommended_files', 'can_auto_fix',
            'requires_human_review', 'pattern_name', 'typical_fix',
            'doc_context', 'occurrence_count', 'pattern_confidence',
        ]
        attempt_cols = [
            'attempt_number', 'changes_description', 'files_modified',
            'was_successful', 'verification_notes', 'status', 'pr_url',
        ]

        call_count = [0]

        def execute_side_effect(query, params=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # failure query
                cur.description = [(c,) for c in failure_cols]
                cur.fetchone.return_value = failure_rows
            elif call_count[0] == 2:
                # analysis query
                cur.description = [(c,) for c in analysis_cols]
                cur.fetchone.return_value = analysis_row
            elif call_count[0] == 3:
                # attempts query
                cur.description = [(c,) for c in attempt_cols]
                cur.fetchall.return_value = attempt_rows or []

        cur.execute.side_effect = execute_side_effect

        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        db_conn.connection.return_value.__enter__ = lambda s: conn
        db_conn.connection.return_value.__exit__ = MagicMock(return_value=False)

        return db_conn

    def test_no_failure_found(self):
        db = self._setup_db(failure_rows=None)
        failure, analysis = load_failure_and_analysis(db, failure_id=999)
        assert failure is None
        assert analysis is None

    def test_failure_by_id(self):
        row = (42, 'comp', 'https://github.com/org/repo', 'main', 'abc',
               'build_err', 'msg', 'step', 'logs', 'app', None)
        db = self._setup_db(failure_rows=row, analysis_row=None)
        failure, analysis = load_failure_and_analysis(db, failure_id=42)
        assert failure is not None
        assert failure['id'] == 42
        assert failure['component_name'] == 'comp'
        assert analysis is None

    def test_failure_with_analysis(self):
        f_row = (42, 'comp', 'url', 'main', 'sha', 'err', 'msg', 'step', 'logs', 'app', None)
        a_row = ('dep_err', 0.9, 'root', 'fix', ['f.txt'], True, False,
                 'pattern_x', 'typical', 'docs', 3, 0.85)
        db = self._setup_db(failure_rows=f_row, analysis_row=a_row)
        failure, analysis = load_failure_and_analysis(db, failure_id=42)
        assert failure is not None
        assert analysis is not None
        assert analysis['failure_category'] == 'dep_err'
        assert analysis['pattern_name'] == 'pattern_x'

    def test_failure_by_component(self):
        f_row = (42, 'comp', 'url', 'main', 'sha', 'err', 'msg', 'step', 'logs', 'app', None)
        db = self._setup_db(failure_rows=f_row)
        failure, analysis = load_failure_and_analysis(db, component='comp', application='app')
        assert failure is not None
        assert failure['component_name'] == 'comp'


# ---------------------------------------------------------------------------
# load_conforma_and_analysis
# ---------------------------------------------------------------------------

class TestLoadConformaAndAnalysis:
    def _setup_db(self, conforma_row=None, analysis_row=None):
        db_conn = MagicMock()
        conn = MagicMock()
        cur = MagicMock()

        conforma_cols = [
            'id', 'component_name', 'repository_url', 'scenario',
            'violations_count', 'warnings_count', 'violation_summary',
            'violation_details', 'commit_sha', 'application',
        ]
        analysis_cols = [
            'failure_category', 'confidence_score', 'root_cause',
            'recommended_fix', 'can_auto_fix', 'requires_human_review',
        ]

        call_count = [0]

        def execute_side_effect(query, params=None):
            call_count[0] += 1
            if call_count[0] == 1:
                cur.description = [(c,) for c in conforma_cols]
                cur.fetchone.return_value = conforma_row
            elif call_count[0] == 2:
                cur.description = [(c,) for c in analysis_cols]
                cur.fetchone.return_value = analysis_row

        cur.execute.side_effect = execute_side_effect

        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        db_conn.connection.return_value.__enter__ = lambda s: conn
        db_conn.connection.return_value.__exit__ = MagicMock(return_value=False)

        return db_conn

    def test_no_conforma_found(self):
        db = self._setup_db(conforma_row=None)
        conforma, analysis = load_conforma_and_analysis(db, conforma_id=999)
        assert conforma is None
        assert analysis is None

    def test_by_id(self):
        c_row = (99, 'comp', 'url', 'release', 1, 0, 'summary', None, 'sha', 'app')
        db = self._setup_db(conforma_row=c_row)
        conforma, analysis = load_conforma_and_analysis(db, conforma_id=99)
        assert conforma is not None
        assert conforma['id'] == 99
        assert analysis is None

    def test_with_analysis(self):
        c_row = (99, 'comp', 'url', 'release', 1, 0, 'sum', None, 'sha', 'app')
        a_row = ('policy_deprecated_task', 0.95, 'root', 'fix', True, False)
        db = self._setup_db(conforma_row=c_row, analysis_row=a_row)
        conforma, analysis = load_conforma_and_analysis(db, conforma_id=99)
        assert conforma is not None
        assert analysis is not None
        assert analysis['failure_category'] == 'policy_deprecated_task'

    def test_by_component_and_application(self):
        c_row = (99, 'comp', 'url', 'release', 1, 0, 'sum', None, 'sha', 'app')
        db = self._setup_db(conforma_row=c_row)
        conforma, analysis = load_conforma_and_analysis(db, component='comp', application='app')
        assert conforma is not None


# ---------------------------------------------------------------------------
# run_pr_mode
# ---------------------------------------------------------------------------

class TestRunPrMode:
    def test_no_llm(self):
        cfg = _make_config(llm=False)
        result = run_pr_mode(cfg, 'comp', 42, 'app')
        assert result == 1

    def test_no_github_token(self):
        cfg = _make_config(github_token=None)
        result = run_pr_mode(cfg, 'comp', 42, 'app')
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis', return_value=(None, None))
    def test_no_failure_found(self, mock_load, mock_db):
        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app')
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    def test_no_repository_url(self, mock_load, mock_db):
        failure = _make_failure(repository_url=None)
        mock_load.return_value = (failure, None)
        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app')
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={'go.mod': 'module x'})
    def test_dry_run_valid_json(self, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        failure = _make_failure()
        analysis = _make_analysis()
        mock_load.return_value = (failure, analysis)

        fix_json = json.dumps({
            'pr_title': 'fix: update go.mod',
            'confidence': 0.95,
            'files': [
                {'path': 'go.mod', 'new_content': 'module x\ngo 1.22', 'change_summary': 'bump go', 'change_reason': 'compat'},
            ],
        })
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=False)
        assert result == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    def test_invalid_json_response(self, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        failure = _make_failure()
        mock_load.return_value = (failure, None)

        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content='not valid json')
        mock_llm_factory.return_value = mock_llm

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app')
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    def test_json_wrapped_in_markdown_fences(self, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        failure = _make_failure()
        mock_load.return_value = (failure, None)

        fix_json = json.dumps({'pr_title': 'fix', 'confidence': 0.9, 'files': []})
        wrapped = '```json\n{}\n```'.format(fix_json)
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=wrapped)
        mock_llm_factory.return_value = mock_llm

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=False)
        assert result == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={'go.mod': 'module x'})
    def test_recommended_files_as_string(self, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        """recommended_files can be a comma-separated string."""
        failure = _make_failure()
        analysis = _make_analysis(recommended_files='go.mod, go.sum')
        mock_load.return_value = (failure, analysis)

        fix_json = json.dumps({'pr_title': 'fix', 'confidence': 0.9, 'files': []})
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=False)
        assert result == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={'go.mod': 'module x'})
    def test_recommended_files_as_json_string(self, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        """recommended_files can be a JSON string."""
        failure = _make_failure()
        analysis = _make_analysis(recommended_files='["go.mod", "go.sum"]')
        mock_load.return_value = (failure, analysis)

        fix_json = json.dumps({'pr_title': 'fix', 'confidence': 0.9, 'files': []})
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=False)
        assert result == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    def test_tekton_files_fetched_from_central(self, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        """When recommended_files include .tekton paths, also fetch from central."""
        failure = _make_failure()
        analysis = _make_analysis(recommended_files=['.tekton/pipeline.yaml'])
        mock_load.return_value = (failure, analysis)

        # fetch_files_for_fix is called twice: component repo + central
        mock_fetch.side_effect = [
            {'.tekton/pipeline.yaml': 'content'},
            {'.tekton/pipeline.yaml': 'central-content'},
        ]

        fix_json = json.dumps({'pr_title': 'fix', 'confidence': 0.9, 'files': []})
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=False)
        assert result == 0
        assert mock_fetch.call_count == 2


# ---------------------------------------------------------------------------
# run_pr_mode — execute mode
# ---------------------------------------------------------------------------

class TestRunPrModeExecute:
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={'go.mod': 'old'})
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_execute_creates_pr(self, mock_parse, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        failure = _make_failure()
        mock_load.return_value = (failure, None)

        fix_json = json.dumps({
            'pr_title': 'fix: go.mod',
            'confidence': 0.9,
            'pr_body': 'Fix body',
            'files': [{'path': 'go.mod', 'new_content': 'new', 'change_summary': 'upd'}],
        })
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        mock_gh = MagicMock()
        mock_gh.get_ref_sha.return_value = 'sha123'
        mock_gh.create_branch.return_value = True
        mock_gh.get_file_sha.return_value = None
        mock_gh.put_file.return_value = True
        mock_gh.create_pull_request.return_value = {'url': 'https://github.com/org/repo/pull/1', 'number': 1}
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=True)
        assert result == 0
        mock_gh.create_branch.assert_called_once()
        mock_gh.create_pull_request.assert_called_once()

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    @patch('fixers.fix_generator.parse_github_repo', return_value=None)
    def test_execute_invalid_target_repo(self, mock_parse, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        failure = _make_failure()
        mock_load.return_value = (failure, None)

        fix_json = json.dumps({'pr_title': 'fix', 'confidence': 0.9, 'files': []})
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=True)
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_execute_base_sha_fails(self, mock_parse, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        failure = _make_failure()
        mock_load.return_value = (failure, None)

        fix_json = json.dumps({'pr_title': 'fix', 'confidence': 0.9, 'files': []})
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        mock_gh = MagicMock()
        mock_gh.get_ref_sha.return_value = None
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=True)
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_execute_create_branch_fails(self, mock_parse, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        failure = _make_failure()
        mock_load.return_value = (failure, None)

        fix_json = json.dumps({'pr_title': 'fix', 'confidence': 0.9, 'files': [{'path': 'f.txt', 'new_content': 'x'}]})
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        mock_gh = MagicMock()
        mock_gh.get_ref_sha.return_value = 'sha123'
        mock_gh.create_branch.return_value = False
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=True)
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_execute_put_file_fails(self, mock_parse, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        failure = _make_failure()
        mock_load.return_value = (failure, None)

        fix_json = json.dumps({
            'pr_title': 'fix',
            'confidence': 0.9,
            'files': [{'path': 'f.txt', 'new_content': 'x', 'change_summary': 's'}],
        })
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        mock_gh = MagicMock()
        mock_gh.get_ref_sha.return_value = 'sha123'
        mock_gh.create_branch.return_value = True
        mock_gh.get_file_sha.return_value = None
        mock_gh.put_file.return_value = False
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=True)
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_execute_pr_creation_fails(self, mock_parse, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        failure = _make_failure()
        mock_load.return_value = (failure, None)

        fix_json = json.dumps({
            'pr_title': 'fix',
            'confidence': 0.9,
            'files': [{'path': 'f.txt', 'new_content': 'x', 'change_summary': 's'}],
        })
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        mock_gh = MagicMock()
        mock_gh.get_ref_sha.return_value = 'sha123'
        mock_gh.create_branch.return_value = True
        mock_gh.get_file_sha.return_value = None
        mock_gh.put_file.return_value = True
        mock_gh.create_pull_request.return_value = None
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=True)
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_execute_resolution_attempt_failure_is_non_fatal(self, mock_parse, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        """If recording the resolution attempt fails, the function still returns 0."""
        failure = _make_failure()
        mock_load.return_value = (failure, None)

        fix_json = json.dumps({
            'pr_title': 'fix',
            'confidence': 0.9,
            'pr_body': 'body',
            'files': [{'path': 'f.txt', 'new_content': 'x', 'change_summary': 's'}],
        })
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        mock_gh = MagicMock()
        mock_gh.get_ref_sha.return_value = 'sha123'
        mock_gh.create_branch.return_value = True
        mock_gh.get_file_sha.return_value = None
        mock_gh.put_file.return_value = True
        mock_gh.create_pull_request.return_value = {'url': 'https://pr', 'number': 1}
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        with patch('repositories.resolution_attempt_repository.ResolutionAttemptRepository', side_effect=Exception('db error')):
            result = run_pr_mode(cfg, 'comp', 42, 'app', execute=True)
        assert result == 0


# ---------------------------------------------------------------------------
# _push_pr_and_record
# ---------------------------------------------------------------------------

class TestPushPrAndRecord:
    def test_base_sha_fails(self):
        gh = MagicMock()
        gh.get_ref_sha.return_value = None
        db = MagicMock()
        conforma = _make_conforma()
        result = _push_pr_and_record(
            gh, db, conforma, 'org', 'repo', 'branch',
            {'f.txt': ('old', 'new')},
            lambda p: 'msg', 'title', 'body', 'desc',
        )
        assert result == 1

    def test_create_branch_fails(self):
        gh = MagicMock()
        gh.get_ref_sha.return_value = 'sha123'
        gh.create_branch.return_value = False
        db = MagicMock()
        conforma = _make_conforma()
        result = _push_pr_and_record(
            gh, db, conforma, 'org', 'repo', 'branch',
            {'f.txt': ('old', 'new')},
            lambda p: 'msg', 'title', 'body', 'desc',
        )
        assert result == 1

    def test_put_file_fails(self):
        gh = MagicMock()
        gh.get_ref_sha.return_value = 'sha123'
        gh.create_branch.return_value = True
        gh.get_file_sha.return_value = None
        gh.put_file.return_value = False
        db = MagicMock()
        conforma = _make_conforma()
        result = _push_pr_and_record(
            gh, db, conforma, 'org', 'repo', 'branch',
            {'f.txt': ('old', 'new')},
            lambda p: 'msg', 'title', 'body', 'desc',
        )
        assert result == 1

    def test_pr_creation_fails(self):
        gh = MagicMock()
        gh.get_ref_sha.return_value = 'sha123'
        gh.create_branch.return_value = True
        gh.get_file_sha.return_value = None
        gh.put_file.return_value = True
        gh.create_pull_request.return_value = None
        db = MagicMock()
        conforma = _make_conforma()
        result = _push_pr_and_record(
            gh, db, conforma, 'org', 'repo', 'branch',
            {'f.txt': ('old', 'new')},
            lambda p: 'msg', 'title', 'body', 'desc',
        )
        assert result == 1

    def test_success(self):
        gh = MagicMock()
        gh.get_ref_sha.return_value = 'sha123'
        gh.create_branch.return_value = True
        gh.get_file_sha.return_value = 'existing-sha'
        gh.put_file.return_value = True
        gh.create_pull_request.return_value = {'url': 'https://pr', 'number': 1}
        db = MagicMock()
        conforma = _make_conforma()

        with patch('repositories.resolution_attempt_repository.ResolutionAttemptRepository') as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.record_conforma_pr_created.return_value = 1
            mock_repo_cls.return_value = mock_repo

            result = _push_pr_and_record(
                gh, db, conforma, 'org', 'repo', 'branch',
                {'f.txt': ('old', 'new')},
                lambda p: 'msg', 'title', 'body', 'desc',
            )
        assert result == 0
        mock_repo.record_conforma_pr_created.assert_called_once()

    def test_resolution_recording_failure_is_non_fatal(self):
        gh = MagicMock()
        gh.get_ref_sha.return_value = 'sha123'
        gh.create_branch.return_value = True
        gh.get_file_sha.return_value = None
        gh.put_file.return_value = True
        gh.create_pull_request.return_value = {'url': 'https://pr', 'number': 1}
        db = MagicMock()
        conforma = _make_conforma()

        with patch('repositories.resolution_attempt_repository.ResolutionAttemptRepository', side_effect=Exception('fail')):
            result = _push_pr_and_record(
                gh, db, conforma, 'org', 'repo', 'branch',
                {'f.txt': ('old', 'new')},
                lambda p: 'msg', 'title', 'body', 'desc',
            )
        assert result == 0


# ---------------------------------------------------------------------------
# run_conforma_hermetic_mode
# ---------------------------------------------------------------------------

class TestRunConformaHermeticMode:
    def test_no_github_token(self):
        cfg = _make_config(github_token=None)
        result = run_conforma_hermetic_mode(cfg, 'comp', 99, 'app')
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis', return_value=(None, None))
    def test_no_conforma_found(self, mock_load, mock_db):
        cfg = _make_config()
        result = run_conforma_hermetic_mode(cfg, 'comp', 99, 'app')
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo')
    def test_no_hermetic_false_found(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.return_value = '- name: hermetic\n  value: "true"\n'
        mock_gh_cls.return_value = mock_gh

        # Make parse_github_repo return valid data for both central + component
        mock_parse.side_effect = [('central-org', 'central-repo'), ('org', 'repo')]

        cfg = _make_config()
        with patch('fixers.fix_generator.KONFLUX_CENTRAL_REPO', 'https://github.com/central-org/central-repo'):
            result = run_conforma_hermetic_mode(cfg, 'comp', 99, 'app')
        assert result == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_dry_run_with_hermetic_false(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['my-component-push.yaml']
        mock_gh.get_file_content.return_value = (
            '- name: hermetic\n  value: false\n'
            '- name: prefetch-dependencies\n  value: something\n'
        )
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        with patch('fixers.fix_generator.KONFLUX_CENTRAL_REPO', ''):
            result = run_conforma_hermetic_mode(cfg, 'comp', 99, 'app', execute=False)
        assert result == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_dry_run_no_prefetch_warning(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        """Without prefetch-dependencies, a warning is shown."""
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['my-component-push.yaml']
        mock_gh.get_file_content.return_value = '- name: hermetic\n  value: false\n'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        with patch('fixers.fix_generator.KONFLUX_CENTRAL_REPO', ''):
            result = run_conforma_hermetic_mode(cfg, 'comp', 99, 'app', execute=False)
        assert result == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator._push_pr_and_record', return_value=0)
    def test_execute_mode(self, mock_push, mock_parse, mock_gh_cls, mock_load, mock_db):
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['my-component-push.yaml']
        mock_gh.get_file_content.return_value = '- name: hermetic\n  value: false\n'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        with patch('fixers.fix_generator.KONFLUX_CENTRAL_REPO', ''):
            result = run_conforma_hermetic_mode(cfg, 'comp', 99, 'app', execute=True)
        assert result == 0
        mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# run_conforma_sbom_vendor_label_mode
# ---------------------------------------------------------------------------

class TestRunConformaSbomVendorLabelMode:
    def test_no_github_token(self):
        cfg = _make_config(github_token=None)
        assert run_conforma_sbom_vendor_label_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis', return_value=(None, None))
    def test_no_conforma_found(self, mock_load, mock_db):
        cfg = _make_config()
        assert run_conforma_sbom_vendor_label_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    def test_no_repository_url(self, mock_load, mock_db):
        conforma = _make_conforma(repository_url=None)
        mock_load.return_value = (conforma, None)
        cfg = _make_config()
        assert run_conforma_sbom_vendor_label_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=None)
    def test_invalid_repo_url(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)
        cfg = _make_config()
        assert run_conforma_sbom_vendor_label_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_no_containerfile_found(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)

        mock_gh = MagicMock()
        mock_gh.get_file_content.return_value = None
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_sbom_vendor_label_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_vendor_label_already_present(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)

        mock_gh = MagicMock()
        mock_gh.get_file_content.return_value = 'FROM ubi9\nLABEL vendor="Red Hat, Inc."\nCMD ["bash"]\n'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_sbom_vendor_label_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_dry_run_success(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)

        mock_gh = MagicMock()
        mock_gh.get_file_content.return_value = 'FROM ubi9\nCMD ["bash"]\n'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_sbom_vendor_label_mode(cfg, 'comp', 99, 'app', execute=False) == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator._push_pr_and_record', return_value=0)
    def test_execute_mode(self, mock_push, mock_parse, mock_gh_cls, mock_load, mock_db):
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)

        mock_gh = MagicMock()
        mock_gh.get_file_content.return_value = 'FROM ubi9\nCMD ["bash"]\n'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_sbom_vendor_label_mode(cfg, 'comp', 99, 'app', execute=True) == 0
        mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# run_conforma_rpm_repo_id_mode
# ---------------------------------------------------------------------------

class TestRunConformaRpmRepoIdMode:
    def test_no_github_token(self):
        cfg = _make_config(github_token=None)
        assert run_conforma_rpm_repo_id_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis', return_value=(None, None))
    def test_no_conforma(self, mock_load, mock_db):
        cfg = _make_config()
        assert run_conforma_rpm_repo_id_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    def test_no_repo_url(self, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(repository_url=None), None)
        cfg = _make_config()
        assert run_conforma_rpm_repo_id_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=None)
    def test_unparseable_repo(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(), None)
        cfg = _make_config()
        assert run_conforma_rpm_repo_id_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_no_matching_files(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(), None)
        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = []
        mock_gh.get_file_content.return_value = None
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_rpm_repo_id_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_dry_run_success(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(), None)
        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['base.repo']
        mock_gh.get_file_content.side_effect = [
            '[ubi-9-baseos-rpms]\nbaseurl=http://...\n',  # rpms.in.yaml
            '[ubi-9-appstream-rpms]\nbaseurl=http://...\n',  # base.repo
        ]
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_rpm_repo_id_mode(cfg, 'comp', 99, 'app', execute=False) == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator._push_pr_and_record', return_value=0)
    def test_execute_mode(self, mock_push, mock_parse, mock_gh_cls, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(), None)
        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = []
        mock_gh.get_file_content.return_value = '[ubi-9-baseos-rpms]\nbaseurl=...\n'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_rpm_repo_id_mode(cfg, 'comp', 99, 'app', execute=True) == 0
        mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# run_conforma_pr_mode (deprecated task)
# ---------------------------------------------------------------------------

class TestRunConformaPrMode:
    def test_no_github_token(self):
        cfg = _make_config(github_token=None)
        assert run_conforma_pr_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis', return_value=(None, None))
    def test_no_conforma(self, mock_load, mock_db):
        cfg = _make_config()
        assert run_conforma_pr_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    def test_no_repo_url(self, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(repository_url=None), None)
        cfg = _make_config()
        assert run_conforma_pr_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    def test_no_parseable_fixes(self, mock_load, mock_db):
        conforma = _make_conforma(violation_details={'violations': [{'rule': 'other', 'solution': 'x'}]})
        mock_load.return_value = (conforma, None)
        cfg = _make_config()
        assert run_conforma_pr_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=None)
    def test_unparseable_repo_url(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        old_ref = 'oci://quay.io/org/task:0.1@sha256:aaa111'
        new_ref = 'oci://quay.io/org/task:0.1@sha256:bbb222'
        details = {'violations': [{'rule': 'policy_deprecated_task', 'solution': f'Replace `{old_ref}` with `{new_ref}`'}]}
        mock_load.return_value = (_make_conforma(violation_details=details), None)
        cfg = _make_config()
        assert run_conforma_pr_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_no_tekton_yaml_files(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        old_ref = 'oci://quay.io/org/task:0.1@sha256:aaa111'
        new_ref = 'oci://quay.io/org/task:0.1@sha256:bbb222'
        details = {'violations': [{'rule': 'policy_deprecated_task', 'solution': f'Replace `{old_ref}` with `{new_ref}`'}]}
        mock_load.return_value = (_make_conforma(violation_details=details), None)

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = []
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_pr_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_no_files_needed_updating(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        old_ref = 'oci://quay.io/org/task:0.1@sha256:aaa111'
        new_ref = 'oci://quay.io/org/task:0.1@sha256:bbb222'
        details = {'violations': [{'rule': 'policy_deprecated_task', 'solution': f'Replace `{old_ref}` with `{new_ref}`'}]}
        mock_load.return_value = (_make_conforma(violation_details=details), None)

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.return_value = 'no matching ref here'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_pr_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_dry_run_success(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        old_ref = 'oci://quay.io/org/task:0.1@sha256:aaa111'
        new_ref = 'oci://quay.io/org/task:0.1@sha256:bbb222'
        details = {'violations': [{'rule': 'policy_deprecated_task', 'solution': f'Replace `{old_ref}` with `{new_ref}`'}]}
        mock_load.return_value = (_make_conforma(violation_details=details), None)

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.return_value = f'bundle: {old_ref}\n'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_pr_mode(cfg, 'comp', 99, 'app', execute=False) == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator._push_pr_and_record', return_value=0)
    def test_execute_mode(self, mock_push, mock_parse, mock_gh_cls, mock_load, mock_db):
        old_ref = 'oci://quay.io/org/task:0.1@sha256:aaa111'
        new_ref = 'oci://quay.io/org/task:0.1@sha256:bbb222'
        details = {'violations': [{'rule': 'policy_deprecated_task', 'solution': f'Replace `{old_ref}` with `{new_ref}`'}]}
        mock_load.return_value = (_make_conforma(violation_details=details), None)

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.return_value = f'bundle: {old_ref}\n'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_pr_mode(cfg, 'comp', 99, 'app', execute=True) == 0
        mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# run_conforma_unpinned_task_mode
# ---------------------------------------------------------------------------

class TestRunConformaUnpinnedTaskMode:
    def test_no_github_token(self):
        cfg = _make_config(github_token=None)
        assert run_conforma_unpinned_task_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis', return_value=(None, None))
    def test_no_conforma(self, mock_load, mock_db):
        cfg = _make_config()
        assert run_conforma_unpinned_task_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    def test_no_repo_url(self, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(repository_url=None), None)
        cfg = _make_config()
        assert run_conforma_unpinned_task_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=None)
    def test_unparseable_repo(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(), None)
        cfg = _make_config()
        assert run_conforma_unpinned_task_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_no_tekton_yaml(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(), None)
        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = []
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_unpinned_task_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    def test_no_floating_refs(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(), None)
        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.return_value = 'bundle: quay.io/org/task:0.1@sha256:abc123\n'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_unpinned_task_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator.resolve_quay_digest')
    def test_all_refs_unresolvable(self, mock_resolve, mock_parse, mock_gh_cls, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(), None)
        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.return_value = 'bundle: quay.io/org/task:0.1\n'
        mock_gh_cls.return_value = mock_gh
        mock_resolve.return_value = ''

        cfg = _make_config()
        assert run_conforma_unpinned_task_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator.resolve_quay_digest', return_value='sha256:newdigest1234')
    def test_dry_run_success(self, mock_resolve, mock_parse, mock_gh_cls, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(), None)
        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.return_value = 'bundle: quay.io/org/task:0.1\n'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_unpinned_task_mode(cfg, 'comp', 99, 'app', execute=False) == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator.resolve_quay_digest')
    @patch('fixers.fix_generator._push_pr_and_record', return_value=0)
    def test_execute_with_partial_resolution(self, mock_push, mock_resolve, mock_parse, mock_gh_cls, mock_load, mock_db):
        """Some refs resolved, some not — PR still created for the resolved ones."""
        mock_load.return_value = (_make_conforma(), None)
        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.return_value = (
            'bundle: quay.io/org/task-a:0.1\n'
            'bundle: quay.io/org/task-b:0.2\n'
        )
        mock_gh_cls.return_value = mock_gh

        # First ref resolves, second does not
        mock_resolve.side_effect = ['sha256:resolved123', '']

        cfg = _make_config()
        assert run_conforma_unpinned_task_mode(cfg, 'comp', 99, 'app', execute=True) == 0
        mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# run_conforma_untrusted_image_mode
# ---------------------------------------------------------------------------

class TestRunConformaUntrustedImageMode:
    def test_no_github_token(self):
        cfg = _make_config(github_token=None)
        assert run_conforma_untrusted_image_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis', return_value=(None, None))
    def test_no_conforma(self, mock_load, mock_db):
        cfg = _make_config()
        assert run_conforma_untrusted_image_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    def test_no_repo_url(self, mock_load, mock_db):
        mock_load.return_value = (_make_conforma(repository_url=None), None)
        cfg = _make_config()
        assert run_conforma_untrusted_image_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    def test_no_extractable_refs(self, mock_load, mock_db):
        conforma = _make_conforma(violation_details={'violations': [{'msg': 'no refs', 'solution': ''}]})
        mock_load.return_value = (conforma, None)
        cfg = _make_config()
        assert run_conforma_untrusted_image_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=None)
    def test_unparseable_repo(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        details = {'violations': [{'msg': 'quay.io/org/task:0.1@sha256:aaa111bbb222ccc333', 'solution': ''}]}
        mock_load.return_value = (_make_conforma(violation_details=details), None)
        cfg = _make_config()
        assert run_conforma_untrusted_image_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator._refresh_pinned_ref')
    def test_all_refs_already_current(self, mock_refresh, mock_parse, mock_gh_cls, mock_load, mock_db):
        old_ref = 'quay.io/org/task:0.1@sha256:aaa111bbb222ccc333'
        details = {'violations': [{'msg': old_ref, 'solution': ''}]}
        mock_load.return_value = (_make_conforma(violation_details=details), None)
        mock_refresh.return_value = old_ref  # same = already current

        cfg = _make_config()
        assert run_conforma_untrusted_image_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator._refresh_pinned_ref')
    def test_all_refs_unresolvable(self, mock_refresh, mock_parse, mock_gh_cls, mock_load, mock_db):
        old_ref = 'quay.io/org/task:0.1@sha256:aaa111bbb222ccc333'
        details = {'violations': [{'msg': old_ref, 'solution': ''}]}
        mock_load.return_value = (_make_conforma(violation_details=details), None)
        mock_refresh.return_value = None

        cfg = _make_config()
        assert run_conforma_untrusted_image_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator._refresh_pinned_ref')
    def test_no_files_contain_old_refs(self, mock_refresh, mock_parse, mock_gh_cls, mock_load, mock_db):
        old_ref = 'quay.io/org/task:0.1@sha256:aaa111bbb222ccc333'
        new_ref = 'quay.io/org/task:0.1@sha256:ddd444eee555fff666'
        details = {'violations': [{'msg': old_ref, 'solution': ''}]}
        mock_load.return_value = (_make_conforma(violation_details=details), None)
        mock_refresh.return_value = new_ref

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.return_value = 'no matching refs in this file'
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_untrusted_image_mode(cfg, 'comp', 99, 'app') == 1

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator._refresh_pinned_ref')
    def test_dry_run_success(self, mock_refresh, mock_parse, mock_gh_cls, mock_load, mock_db):
        old_ref = 'quay.io/org/task:0.1@sha256:aaa111bbb222ccc333'
        new_ref = 'quay.io/org/task:0.1@sha256:ddd444eee555fff666'
        details = {'violations': [{'msg': f'Untrusted: {old_ref}', 'solution': ''}]}
        conforma = _make_conforma(violation_details=details)
        mock_load.return_value = (conforma, None)
        mock_refresh.return_value = new_ref

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.side_effect = [
            f'bundle: {old_ref}\n',  # .tekton/pipeline.yaml
            None, None, None, None, None,  # Containerfile candidates
        ]
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_untrusted_image_mode(cfg, 'comp', 99, 'app', execute=False) == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo', return_value=('org', 'repo'))
    @patch('fixers.fix_generator._refresh_pinned_ref')
    @patch('fixers.fix_generator._push_pr_and_record', return_value=0)
    def test_execute_mode(self, mock_push, mock_refresh, mock_parse, mock_gh_cls, mock_load, mock_db):
        old_ref = 'quay.io/org/task:0.1@sha256:aaa111bbb222ccc333'
        new_ref = 'quay.io/org/task:0.1@sha256:ddd444eee555fff666'
        details = {'violations': [{'msg': f'Untrusted: {old_ref}', 'solution': ''}]}
        conforma = _make_conforma(violation_details=details)
        mock_load.return_value = (conforma, None)
        mock_refresh.return_value = new_ref

        mock_gh = MagicMock()
        mock_gh.get_directory_listing.return_value = ['pipeline.yaml']
        mock_gh.get_file_content.side_effect = [
            f'bundle: {old_ref}\n',
            None, None, None, None, None,
        ]
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        assert run_conforma_untrusted_image_mode(cfg, 'comp', 99, 'app', execute=True) == 0
        mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# run_jira_mode
# ---------------------------------------------------------------------------

class TestRunJiraMode:
    def test_no_llm(self):
        cfg = _make_config(llm=False)
        assert run_jira_mode(cfg, 'comp') == 1

    @patch('sys.stdin')
    def test_empty_stdin(self, mock_stdin):
        mock_stdin.read.return_value = ''
        cfg = _make_config()
        assert run_jira_mode(cfg, 'comp') == 1

    @patch('sys.stdin')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator._open_tty')
    def test_post_immediately_no_jira(self, mock_tty, mock_llm_factory, mock_stdin):
        mock_stdin.read.return_value = 'Ticket text here'
        mock_stdin.isatty.return_value = False

        tty = MagicMock()
        tty.readline.return_value = ''  # Enter = post immediately
        mock_tty.return_value = tty

        cfg = _make_config(jira=None)
        result = run_jira_mode(cfg, 'comp')
        assert result == 0

    @patch('sys.stdin')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator._open_tty')
    def test_edit_then_post_no_jira(self, mock_tty, mock_llm_factory, mock_stdin):
        mock_stdin.read.return_value = 'Initial ticket'

        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content='Edited ticket')
        mock_llm_factory.return_value = mock_llm

        tty = MagicMock()
        # First call: edit request; second call: enter = done
        tty.readline.side_effect = ['make it shorter\n', '\n']
        mock_tty.return_value = tty

        cfg = _make_config(jira=None)
        result = run_jira_mode(cfg, 'comp')
        assert result == 0
        mock_llm.create_message.assert_called_once()

    @patch('sys.stdin')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator._open_tty')
    def test_keyboard_interrupt_during_edit(self, mock_tty, mock_llm_factory, mock_stdin):
        mock_stdin.read.return_value = 'Ticket text'

        tty = MagicMock()
        tty.readline.side_effect = KeyboardInterrupt()
        mock_tty.return_value = tty

        cfg = _make_config(jira=None)
        result = run_jira_mode(cfg, 'comp')
        assert result == 0

    @patch('sys.stdin')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator._open_tty')
    def test_post_to_jira_declined(self, mock_tty, mock_llm_factory, mock_stdin):
        mock_stdin.read.return_value = 'Ticket text'

        tty = MagicMock()
        # First readline: empty (skip edit); second: decline post
        tty.readline.side_effect = ['\n', 'n\n']
        mock_tty.return_value = tty

        jira_cfg = MagicMock()
        jira_cfg.base_url = 'https://jira.example.com'
        jira_cfg.email = 'user@example.com'
        jira_cfg.token = 'jira-token'
        jira_cfg.project = 'PROJ'

        cfg = _make_config(jira=jira_cfg)
        result = run_jira_mode(cfg, 'comp')
        assert result == 0

    @patch('sys.stdin')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator._open_tty')
    @patch('fixers.fix_generator.JiraClient')
    def test_post_to_jira_success(self, mock_jira_cls, mock_tty, mock_llm_factory, mock_stdin):
        mock_stdin.read.return_value = 'Ticket text'

        tty = MagicMock()
        tty.readline.side_effect = ['\n', 'y\n']
        mock_tty.return_value = tty

        mock_jira = MagicMock()
        mock_jira.create_issue.return_value = {'url': 'https://jira/PROJ-123'}
        mock_jira_cls.return_value = mock_jira

        jira_cfg = MagicMock()
        jira_cfg.base_url = 'https://jira.example.com'
        jira_cfg.email = 'user@example.com'
        jira_cfg.token = 'jira-token'
        jira_cfg.project = 'PROJ'

        cfg = _make_config(jira=jira_cfg)
        result = run_jira_mode(cfg, 'comp')
        assert result == 0
        mock_jira.create_issue.assert_called_once()

    @patch('sys.stdin')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator._open_tty')
    @patch('fixers.fix_generator.JiraClient')
    def test_post_to_jira_failure(self, mock_jira_cls, mock_tty, mock_llm_factory, mock_stdin):
        mock_stdin.read.return_value = 'Ticket text'

        tty = MagicMock()
        tty.readline.side_effect = ['\n', 'y\n']
        mock_tty.return_value = tty

        mock_jira = MagicMock()
        mock_jira.create_issue.return_value = None
        mock_jira_cls.return_value = mock_jira

        jira_cfg = MagicMock()
        jira_cfg.base_url = 'https://jira.example.com'
        jira_cfg.email = 'user@example.com'
        jira_cfg.token = 'jira-token'
        jira_cfg.project = 'PROJ'

        cfg = _make_config(jira=jira_cfg)
        result = run_jira_mode(cfg, 'comp')
        assert result == 1

    @patch('sys.stdin')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator._open_tty')
    def test_summary_provided(self, mock_tty, mock_llm_factory, mock_stdin):
        mock_stdin.read.return_value = 'Ticket text'

        tty = MagicMock()
        tty.readline.side_effect = ['\n']
        mock_tty.return_value = tty

        cfg = _make_config(jira=None)
        result = run_jira_mode(cfg, 'comp', summary='Custom summary')
        assert result == 0

    @patch('sys.stdin')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator._open_tty')
    def test_keyboard_interrupt_at_confirm(self, mock_tty, mock_llm_factory, mock_stdin):
        mock_stdin.read.return_value = 'Ticket text'

        tty = MagicMock()
        # First readline: empty (skip edit); second: KeyboardInterrupt during confirm
        tty.readline.side_effect = ['\n', KeyboardInterrupt()]
        mock_tty.return_value = tty

        jira_cfg = MagicMock()
        jira_cfg.base_url = 'https://jira.example.com'
        jira_cfg.email = 'user@example.com'
        jira_cfg.token = 'jira-token'
        jira_cfg.project = 'PROJ'

        cfg = _make_config(jira=jira_cfg)
        result = run_jira_mode(cfg, 'comp')
        assert result == 0


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------

class TestMain:
    @patch('fixers.fix_generator.CollectorConfig')
    @patch('fixers.fix_generator.run_pr_mode', return_value=0)
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis', return_value=(None, None))
    def test_pr_mode_build_failure(self, mock_load, mock_db, mock_run, mock_cfg):
        from fixers.fix_generator import main
        mock_cfg.from_env.return_value = MagicMock()
        with patch('sys.argv', ['fix_generator.py', '--mode', 'pr', '--failure-id', '42']):
            main()

    @patch('fixers.fix_generator.CollectorConfig')
    @patch('fixers.fix_generator.run_jira_mode', return_value=0)
    def test_jira_mode(self, mock_run, mock_cfg):
        from fixers.fix_generator import main
        mock_cfg.from_env.return_value = MagicMock()
        with patch('sys.argv', ['fix_generator.py', '--mode', 'jira', '--component', 'comp']):
            main()
        mock_run.assert_called_once()

    @patch('fixers.fix_generator.CollectorConfig')
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.run_conforma_hermetic_mode', return_value=0)
    def test_conforma_hermetic_routing(self, mock_hermetic, mock_load, mock_db, mock_cfg):
        from fixers.fix_generator import main
        mock_cfg.from_env.return_value = MagicMock()
        conforma = _make_conforma()
        analysis = _make_analysis(failure_category='policy_hermetic_build')
        mock_load.return_value = (conforma, analysis)

        with patch('sys.argv', ['fix_generator.py', '--mode', 'pr', '--conforma-id', '99']):
            main()
        mock_hermetic.assert_called_once()

    @patch('fixers.fix_generator.CollectorConfig')
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.run_conforma_sbom_vendor_label_mode', return_value=0)
    def test_conforma_sbom_routing(self, mock_sbom, mock_load, mock_db, mock_cfg):
        from fixers.fix_generator import main
        mock_cfg.from_env.return_value = MagicMock()
        mock_load.return_value = (_make_conforma(), _make_analysis(failure_category='policy_sbom_vendor_label'))

        with patch('sys.argv', ['fix_generator.py', '--mode', 'pr', '--conforma-id', '99']):
            main()
        mock_sbom.assert_called_once()

    @patch('fixers.fix_generator.CollectorConfig')
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.run_conforma_rpm_repo_id_mode', return_value=0)
    def test_conforma_rpm_routing(self, mock_rpm, mock_load, mock_db, mock_cfg):
        from fixers.fix_generator import main
        mock_cfg.from_env.return_value = MagicMock()
        mock_load.return_value = (_make_conforma(), _make_analysis(failure_category='policy_rpm_repository'))

        with patch('sys.argv', ['fix_generator.py', '--mode', 'pr', '--conforma-id', '99']):
            main()
        mock_rpm.assert_called_once()

    @patch('fixers.fix_generator.CollectorConfig')
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.run_conforma_unpinned_task_mode', return_value=0)
    def test_conforma_unpinned_routing(self, mock_unpinned, mock_load, mock_db, mock_cfg):
        from fixers.fix_generator import main
        mock_cfg.from_env.return_value = MagicMock()
        mock_load.return_value = (_make_conforma(), _make_analysis(failure_category='policy_unpinned_task'))

        with patch('sys.argv', ['fix_generator.py', '--mode', 'pr', '--conforma-id', '99']):
            main()
        mock_unpinned.assert_called_once()

    @patch('fixers.fix_generator.CollectorConfig')
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.run_conforma_untrusted_image_mode', return_value=0)
    def test_conforma_untrusted_routing(self, mock_untrusted, mock_load, mock_db, mock_cfg):
        from fixers.fix_generator import main
        mock_cfg.from_env.return_value = MagicMock()
        mock_load.return_value = (_make_conforma(), _make_analysis(failure_category='policy_untrusted_image'))

        with patch('sys.argv', ['fix_generator.py', '--mode', 'pr', '--conforma-id', '99']):
            main()
        mock_untrusted.assert_called_once()

    @patch('fixers.fix_generator.CollectorConfig')
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.run_conforma_pr_mode', return_value=0)
    def test_conforma_default_routing(self, mock_default, mock_load, mock_db, mock_cfg):
        """Unknown category falls through to run_conforma_pr_mode."""
        from fixers.fix_generator import main
        mock_cfg.from_env.return_value = MagicMock()
        mock_load.return_value = (_make_conforma(), _make_analysis(failure_category='unknown_category'))

        with patch('sys.argv', ['fix_generator.py', '--mode', 'pr', '--conforma-id', '99']):
            main()
        mock_default.assert_called_once()


# ---------------------------------------------------------------------------
# _open_tty and _prompt
# ---------------------------------------------------------------------------

class TestTtyHelpers:
    def test_open_tty_when_stdin_is_tty(self):
        from fixers.fix_generator import _open_tty
        with patch('sys.stdin') as mock_stdin:
            mock_stdin.isatty.return_value = True
            result = _open_tty()
            assert result is mock_stdin

    def test_open_tty_when_stdin_is_pipe(self):
        from fixers.fix_generator import _open_tty
        with patch('sys.stdin') as mock_stdin:
            mock_stdin.isatty.return_value = False
            with patch('builtins.open', side_effect=OSError('no tty')):
                result = _open_tty()
                assert result is mock_stdin

    def test_prompt_reads_from_tty(self):
        from fixers.fix_generator import _prompt
        tty = MagicMock()
        tty.readline.return_value = 'user input\n'
        result = _prompt(tty, 'Enter: ')
        assert result == 'user input'

    def test_prompt_eof_raises_keyboard_interrupt(self):
        from fixers.fix_generator import _prompt
        tty = MagicMock()
        tty.readline.side_effect = EOFError()
        with pytest.raises(KeyboardInterrupt):
            _prompt(tty, 'Enter: ')


# ---------------------------------------------------------------------------
# Edge cases for PR mode with target_repo=konflux-central
# ---------------------------------------------------------------------------

class TestPrModeKonfluxCentral:
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    def test_konflux_central_target_uses_main_base(self, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        failure = _make_failure(branch='rhoai-2.16')
        mock_load.return_value = (failure, None)

        fix_json = json.dumps({
            'pr_title': 'fix',
            'confidence': 0.9,
            'target_repo': 'konflux-central',
            'target_repo_reason': 'pipeline config',
            'caveat': 'check manually',
            'files': [],
        })
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=False)
        assert result == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={'go.mod': 'content'})
    def test_file_from_konflux_central_key(self, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        """When fix references a file fetched from konflux-central, old_content lookup uses the key."""
        failure = _make_failure()
        mock_load.return_value = (failure, None)

        fix_json = json.dumps({
            'pr_title': 'fix',
            'confidence': 0.9,
            'files': [
                {'path': '.tekton/pipeline.yaml', 'new_content': 'new', 'change_summary': 's'},
            ],
        })
        # Simulate .tekton file was fetched from central
        mock_fetch.return_value = {'konflux-central:.tekton/pipeline.yaml': 'old content'}
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=False)
        assert result == 0


# ---------------------------------------------------------------------------
# Edge: hermetic mode searches central first, then component
# ---------------------------------------------------------------------------

class TestHermeticModeRepoSearch:
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo')
    def test_central_searched_first(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)

        # Central parses OK, component parses OK
        mock_parse.side_effect = [('central-org', 'central-repo'), ('org', 'repo')]

        mock_gh = MagicMock()
        # Central has the file with hermetic: false
        mock_gh.get_directory_listing.side_effect = [
            ['my-component-push.yaml'],  # central
        ]
        mock_gh.get_file_content.side_effect = [
            '- name: hermetic\n  value: false\n',  # central file
        ]
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        with patch('fixers.fix_generator.KONFLUX_CENTRAL_REPO', 'https://github.com/central-org/central-repo'):
            result = run_conforma_hermetic_mode(cfg, 'comp', 99, 'app', execute=False)
        assert result == 0

    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_conforma_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.parse_github_repo')
    def test_falls_back_to_component_repo(self, mock_parse, mock_gh_cls, mock_load, mock_db):
        conforma = _make_conforma()
        mock_load.return_value = (conforma, None)

        # Central parses OK, component parses OK
        mock_parse.side_effect = [('central-org', 'central-repo'), ('org', 'repo')]

        mock_gh = MagicMock()
        # Central has no matching files, component has them
        mock_gh.get_directory_listing.side_effect = [
            [],  # central - empty
            ['my-component-push.yaml'],  # component
        ]
        mock_gh.get_file_content.side_effect = [
            '- name: hermetic\n  value: false\n',  # component file
        ]
        mock_gh_cls.return_value = mock_gh

        cfg = _make_config()
        with patch('fixers.fix_generator.KONFLUX_CENTRAL_REPO', 'https://github.com/central-org/central-repo'):
            result = run_conforma_hermetic_mode(cfg, 'comp', 99, 'app', execute=False)
        assert result == 0


# ---------------------------------------------------------------------------
# Edge: recommended_files parsing edge cases
# ---------------------------------------------------------------------------

class TestRecommendedFilesParsing:
    @patch('fixers.fix_generator.DatabaseConnection')
    @patch('fixers.fix_generator.load_failure_and_analysis')
    @patch('fixers.fix_generator.GitHubClient')
    @patch('fixers.fix_generator.create_llm_provider')
    @patch('fixers.fix_generator.fetch_files_for_fix', return_value={})
    def test_recommended_files_unparseable_json_string(self, mock_fetch, mock_llm_factory, mock_gh_cls, mock_load, mock_db):
        """When recommended_files is a string that's not valid JSON, split by comma."""
        failure = _make_failure()
        analysis = _make_analysis(recommended_files='not-valid-json')
        mock_load.return_value = (failure, analysis)

        fix_json = json.dumps({'pr_title': 'fix', 'confidence': 0.9, 'files': []})
        mock_llm = MagicMock()
        mock_llm.create_message.return_value = _make_llm_response(content=fix_json)
        mock_llm_factory.return_value = mock_llm

        cfg = _make_config()
        result = run_pr_mode(cfg, 'comp', 42, 'app', execute=False)
        assert result == 0
