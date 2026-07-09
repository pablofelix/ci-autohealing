"""Comprehensive tests for verdict_correlator module.

Covers all pure functions (calculate_file_overlap, classify_fix_type,
compare_recommendations, detect_actual_fix) and the VerdictCorrelator
class with all its methods.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from collectors.verdict_correlator import (
    CORRECT_OVERLAP_THRESHOLD,
    PARTIAL_OVERLAP_THRESHOLD,
    PR_MERGE_WINDOW,
    VerdictCorrelator,
    calculate_file_overlap,
    classify_fix_type,
    compare_recommendations,
    detect_actual_fix,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db():
    db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    db.connection.return_value.__enter__ = MagicMock(return_value=conn)
    db.connection.return_value.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    return db, conn, cursor


def _make_config(github_token=None):
    config = MagicMock()
    config.github_token = github_token
    return config


# ===========================================================================
# calculate_file_overlap
# ===========================================================================

class TestCalculateFileOverlap:

    def test_empty_recommended(self):
        assert calculate_file_overlap([], ['a.py']) == 0.0

    def test_empty_actual(self):
        assert calculate_file_overlap(['a.py'], []) == 0.0

    def test_both_empty(self):
        assert calculate_file_overlap([], []) == 0.0

    def test_none_recommended(self):
        assert calculate_file_overlap(None, ['a.py']) == 0.0

    def test_none_actual(self):
        assert calculate_file_overlap(['a.py'], None) == 0.0

    def test_no_overlap(self):
        result = calculate_file_overlap(['a.py', 'b.py'], ['c.py', 'd.py'])
        assert result == 0.0

    def test_full_overlap_same_files(self):
        result = calculate_file_overlap(['a.py', 'b.py'], ['a.py', 'b.py'])
        assert result == 1.0

    def test_partial_overlap(self):
        # intersection = {a.py}, union = {a.py, b.py, c.py} => 1/3
        result = calculate_file_overlap(['a.py', 'b.py'], ['a.py', 'c.py'])
        assert abs(result - 1 / 3) < 1e-9

    def test_basename_matching(self):
        """Full paths differ but basenames match."""
        result = calculate_file_overlap(
            ['src/config/Dockerfile'],
            ['build/Dockerfile'],
        )
        assert result == 1.0

    def test_files_with_empty_strings_filtered(self):
        """Empty strings in the list should be ignored by basenames()."""
        result = calculate_file_overlap(['', 'a.py'], ['a.py'])
        # basenames: rec={a.py}, act={a.py} => 1.0
        assert result == 1.0

    def test_all_empty_strings(self):
        result = calculate_file_overlap(['', ''], ['', ''])
        assert result == 0.0


# ===========================================================================
# classify_fix_type
# ===========================================================================

class TestClassifyFixType:

    def test_empty_files(self):
        assert classify_fix_type([]) == 'unknown'

    def test_none_files(self):
        assert classify_fix_type(None) == 'unknown'

    def test_all_config_files(self):
        files = ['values.yaml', 'config.json', 'Dockerfile']
        assert classify_fix_type(files) == 'config_change'

    def test_all_code_files(self):
        files = ['main.py', 'utils.go', 'handler.rs']
        assert classify_fix_type(files) == 'code_change'

    def test_mixed_files(self):
        files = ['main.py', 'values.yaml']
        assert classify_fix_type(files) == 'mixed'

    def test_single_config_file(self):
        assert classify_fix_type(['app.toml']) == 'config_change'

    def test_single_code_file(self):
        assert classify_fix_type(['app.py']) == 'code_change'

    def test_containerfile_is_config(self):
        assert classify_fix_type(['Containerfile']) == 'config_change'

    def test_conf_extension_is_config(self):
        assert classify_fix_type(['nginx.conf']) == 'config_change'


# ===========================================================================
# compare_recommendations
# ===========================================================================

class TestCompareRecommendations:

    def test_unknown_actual_type(self):
        verdict, evidence = compare_recommendations(
            'file_change', ['a.py'], {'type': 'unknown'}
        )
        assert verdict == 'unknown'
        assert 'Could not determine' in evidence['reason']

    def test_ai_deferred_investigation_needed(self):
        verdict, evidence = compare_recommendations(
            'investigation_needed', [], {'type': 'pr_merged'}
        )
        assert verdict == 'unknown'
        assert 'deferred' in evidence['reason']

    def test_ai_deferred_other(self):
        verdict, _ = compare_recommendations(
            'other', [], {'type': 'pr_merged'}
        )
        assert verdict == 'unknown'

    def test_ai_deferred_none(self):
        verdict, _ = compare_recommendations(
            None, [], {'type': 'pr_merged'}
        )
        assert verdict == 'unknown'

    def test_rebuild_correct(self):
        verdict, evidence = compare_recommendations(
            'rebuild', [], {'type': 'rebuild'}
        )
        assert verdict == 'correct'
        assert 'rebuild' in evidence['reason'].lower()

    def test_rebuild_partial_when_pr_merged(self):
        verdict, evidence = compare_recommendations(
            'rebuild', [], {'type': 'pr_merged', 'pr_url': 'http://pr/1'}
        )
        assert verdict == 'partial'
        assert evidence['pr_url'] == 'http://pr/1'

    def test_file_change_incorrect_when_rebuild(self):
        verdict, evidence = compare_recommendations(
            'file_change', ['a.py'], {'type': 'rebuild'}
        )
        assert verdict == 'incorrect'

    def test_config_change_incorrect_when_rebuild(self):
        verdict, _ = compare_recommendations(
            'config_change', ['a.yaml'], {'type': 'rebuild'}
        )
        assert verdict == 'incorrect'

    def test_file_change_high_overlap_correct(self):
        """Overlap > 0.5 => correct."""
        actual = {
            'type': 'pr_merged',
            'pr_url': 'http://pr/2',
            'files_changed': ['a.py', 'b.py'],
        }
        verdict, evidence = compare_recommendations(
            'file_change', ['a.py', 'b.py'], actual
        )
        assert verdict == 'correct'
        assert evidence['overlap'] == 1.0

    def test_file_change_partial_overlap(self):
        """Overlap between 0.2 and 0.5 => partial."""
        actual = {
            'type': 'pr_merged',
            'pr_url': 'http://pr/3',
            'files_changed': ['a.py', 'b.py', 'c.py', 'd.py'],
        }
        # intersection = {a.py}, union = {a.py, b.py, c.py, d.py, x.py} => 1/5 = 0.2
        # Need overlap > 0.2, so use fewer actual files
        actual['files_changed'] = ['a.py', 'b.py', 'c.py']
        # intersection = {a.py}, union = {a.py, b.py, c.py, x.py} => 1/4 = 0.25
        verdict, evidence = compare_recommendations(
            'file_change', ['a.py', 'x.py'], actual
        )
        assert verdict == 'partial'
        assert 0.2 < evidence['overlap'] <= 0.5

    def test_file_change_low_overlap_incorrect(self):
        """Overlap <= 0.2 => incorrect."""
        actual = {
            'type': 'pr_merged',
            'pr_url': 'http://pr/4',
            'files_changed': ['x.py', 'y.py', 'z.py', 'w.py', 'v.py'],
        }
        verdict, evidence = compare_recommendations(
            'file_change', ['a.py'], actual
        )
        assert verdict == 'incorrect'
        assert 'wrong files' in evidence['reason']

    def test_config_change_no_files_correct_type_match(self):
        """AI predicted config_change with no specific files, actual is config_change."""
        actual = {
            'type': 'pr_merged',
            'pr_url': 'http://pr/5',
            'files_changed': ['values.yaml'],
        }
        verdict, evidence = compare_recommendations(
            'config_change', [], actual
        )
        assert verdict == 'correct'
        assert 'config change type' in evidence['reason']

    def test_file_change_no_files_code_match(self):
        """AI predicted file_change with no specific files, actual is code_change."""
        actual = {
            'type': 'pr_merged',
            'pr_url': 'http://pr/6',
            'files_changed': ['main.go'],
        }
        verdict, evidence = compare_recommendations(
            'file_change', [], actual
        )
        assert verdict == 'partial'
        assert 'file change type' in evidence['reason']

    def test_file_change_no_files_type_mismatch(self):
        """AI predicted file_change with no files, actual is config_change."""
        actual = {
            'type': 'pr_merged',
            'pr_url': 'http://pr/7',
            'files_changed': ['values.yaml'],
        }
        verdict, evidence = compare_recommendations(
            'file_change', [], actual
        )
        # file_change != config_change/mixed in second branch, falls through
        assert verdict == 'partial'
        assert 'predicted' in evidence['reason']

    def test_config_change_low_overlap_but_type_match(self):
        """Config type correct but wrong files => partial."""
        actual = {
            'type': 'pr_merged',
            'pr_url': 'http://pr/8',
            'files_changed': ['other.yaml', 'deploy.yml', 'main.py'],
        }
        verdict, evidence = compare_recommendations(
            'config_change', ['wrong.yaml'], actual
        )
        assert verdict == 'partial'
        assert 'config change type, wrong files' in evidence['reason']

    def test_multi_step_with_pr(self):
        actual = {
            'type': 'pr_merged',
            'pr_url': 'http://pr/9',
            'files_changed': ['a.py'],
        }
        verdict, evidence = compare_recommendations(
            'multi_step', ['a.py'], actual
        )
        assert verdict == 'partial'
        assert 'multi-step' in evidence['reason'].lower()
        assert evidence['pr_url'] == 'http://pr/9'

    def test_no_matching_rule(self):
        """Unrecognized fix type with unmatched actual type."""
        verdict, evidence = compare_recommendations(
            'rebuild', [], {'type': 'some_other_type'}
        )
        assert verdict == 'unknown'
        assert 'No matching verdict rule' in evidence['reason']


# ===========================================================================
# detect_actual_fix
# ===========================================================================

class TestDetectActualFix:

    def test_no_merged_prs(self):
        result = detect_actual_fix([], 'sha123', datetime(2024, 6, 1))
        assert result['type'] == 'rebuild'
        assert result['commit_sha'] == 'sha123'
        assert result['files_changed'] == []

    def test_pr_within_window(self):
        resolution_ts = datetime(2024, 6, 1, 12, 0, 0)
        pr = {
            'merged_at_dt': datetime(2024, 6, 1, 11, 0, 0),  # 1h before
            'url': 'http://pr/1',
            'number': 42,
            'title': 'Fix thing',
            'files_changed': ['a.py'],
            'merge_commit_sha': 'sha_pr',
            'merged_at': '2024-06-01T11:00:00Z',
        }
        result = detect_actual_fix([pr], 'sha123', resolution_ts)
        assert result['type'] == 'pr_merged'
        assert result['pr_url'] == 'http://pr/1'
        assert result['pr_number'] == 42
        assert result['files_changed'] == ['a.py']

    def test_pr_outside_window(self):
        resolution_ts = datetime(2024, 6, 1, 12, 0, 0)
        pr = {
            'merged_at_dt': datetime(2024, 5, 30, 0, 0, 0),  # 2 days before
            'url': 'http://pr/2',
            'number': 43,
            'title': 'Old PR',
            'files_changed': ['b.py'],
            'merged_at': '2024-05-30T00:00:00Z',
        }
        result = detect_actual_fix([pr], 'sha123', resolution_ts)
        assert result['type'] == 'rebuild'

    def test_no_resolution_timestamp_picks_latest(self):
        """Without resolution_timestamp, picks the most recent merged PR."""
        pr_old = {
            'merged_at_dt': datetime(2024, 5, 1),
            'url': 'http://pr/old',
            'number': 10,
            'title': 'Old',
            'files_changed': [],
        }
        pr_new = {
            'merged_at_dt': datetime(2024, 6, 1),
            'url': 'http://pr/new',
            'number': 20,
            'title': 'New',
            'files_changed': ['c.py'],
        }
        result = detect_actual_fix([pr_old, pr_new], 'sha123', None)
        assert result['type'] == 'pr_merged'
        assert result['pr_url'] == 'http://pr/new'
        assert result['pr_number'] == 20

    def test_pr_without_merged_at_dt_skipped(self):
        pr = {
            'merged_at_dt': None,
            'url': 'http://pr/x',
            'number': 99,
        }
        result = detect_actual_fix([pr], 'sha123', datetime(2024, 6, 1))
        assert result['type'] == 'rebuild'

    def test_best_pr_closest_to_resolution(self):
        """When multiple PRs are within the window, picks closest."""
        resolution_ts = datetime(2024, 6, 1, 12, 0, 0)
        pr_far = {
            'merged_at_dt': datetime(2024, 6, 1, 2, 0, 0),  # 10h away
            'url': 'http://pr/far',
            'number': 1,
            'title': 'Far',
            'files_changed': ['far.py'],
            'merge_commit_sha': 'sha_far',
            'merged_at': '2024-06-01T02:00:00Z',
        }
        pr_close = {
            'merged_at_dt': datetime(2024, 6, 1, 11, 30, 0),  # 30m away
            'url': 'http://pr/close',
            'number': 2,
            'title': 'Close',
            'files_changed': ['close.py'],
            'merge_commit_sha': 'sha_close',
            'merged_at': '2024-06-01T11:30:00Z',
        }
        result = detect_actual_fix([pr_far, pr_close], 'sha123', resolution_ts)
        assert result['pr_url'] == 'http://pr/close'
        assert result['commit_sha'] == 'sha_close'


# ===========================================================================
# VerdictCorrelator.__init__
# ===========================================================================

class TestVerdictCorrelatorInit:

    @patch('collectors.verdict_correlator.GitHubClient')
    def test_init_with_github_client_passed(self, mock_gh_cls):
        db, _, _ = _make_db()
        gh = MagicMock()
        vc = VerdictCorrelator(_make_config(), db, github_client=gh)
        assert vc.github is gh
        mock_gh_cls.assert_not_called()

    @patch('collectors.verdict_correlator.GitHubClient')
    def test_init_creates_client_when_token_present(self, mock_gh_cls):
        db, _, _ = _make_db()
        vc = VerdictCorrelator(_make_config(github_token='tok123'), db)
        mock_gh_cls.assert_called_once_with(token='tok123')
        assert vc.github == mock_gh_cls.return_value

    @patch('collectors.verdict_correlator.GitHubClient')
    def test_init_no_client_when_no_token(self, mock_gh_cls):
        db, _, _ = _make_db()
        config = MagicMock(spec=[])  # no github_token attribute
        vc = VerdictCorrelator(config, db)
        assert vc.github is None
        mock_gh_cls.assert_not_called()


# ===========================================================================
# VerdictCorrelator._extract_fix_action_type
# ===========================================================================

class TestExtractFixActionType:

    def test_from_fix_action_type_field(self):
        db, _, _ = _make_db()
        vc = VerdictCorrelator(_make_config(), db)
        analysis = {'fix_action_type': 'rebuild'}
        assert vc._extract_fix_action_type(analysis) == 'rebuild'

    def test_from_analysis_json_list(self):
        db, _, _ = _make_db()
        vc = VerdictCorrelator(_make_config(), db)
        analysis = {
            'fix_action_type': None,
            'analysis_json': [
                {'input': {'fix_action_type': 'config_change'}, 'output': {}},
            ],
        }
        assert vc._extract_fix_action_type(analysis) == 'config_change'

    def test_returns_none_when_nothing(self):
        db, _, _ = _make_db()
        vc = VerdictCorrelator(_make_config(), db)
        analysis = {'fix_action_type': None, 'analysis_json': None}
        assert vc._extract_fix_action_type(analysis) is None

    def test_returns_none_when_analysis_json_empty_list(self):
        db, _, _ = _make_db()
        vc = VerdictCorrelator(_make_config(), db)
        analysis = {'fix_action_type': None, 'analysis_json': []}
        assert vc._extract_fix_action_type(analysis) is None

    def test_skips_non_dict_entries_in_analysis_json(self):
        db, _, _ = _make_db()
        vc = VerdictCorrelator(_make_config(), db)
        analysis = {
            'fix_action_type': None,
            'analysis_json': ['not_a_dict', 42],
        }
        assert vc._extract_fix_action_type(analysis) is None

    def test_skips_entries_without_input_key(self):
        db, _, _ = _make_db()
        vc = VerdictCorrelator(_make_config(), db)
        analysis = {
            'fix_action_type': None,
            'analysis_json': [{'output': {}}],
        }
        assert vc._extract_fix_action_type(analysis) is None


# ===========================================================================
# VerdictCorrelator._get_repo_url / _get_component_branch
# ===========================================================================

class TestGetRepoUrlAndBranch:

    def test_get_repo_url_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = ('https://github.com/org/repo',)
        vc = VerdictCorrelator(_make_config(), db)
        assert vc._get_repo_url('comp', 'app') == 'https://github.com/org/repo'

    def test_get_repo_url_not_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        vc = VerdictCorrelator(_make_config(), db)
        assert vc._get_repo_url('comp', 'app') is None

    def test_get_component_branch_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = ('rhoai-2.14',)
        vc = VerdictCorrelator(_make_config(), db)
        assert vc._get_component_branch('comp', 'app') == 'rhoai-2.14'

    def test_get_component_branch_not_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        vc = VerdictCorrelator(_make_config(), db)
        assert vc._get_component_branch('comp', 'app') is None


# ===========================================================================
# VerdictCorrelator._fetch_actual_changes
# ===========================================================================

class TestFetchActualChanges:

    def test_enriches_merged_prs(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = ('main',)  # branch
        gh = MagicMock()
        gh.list_pull_requests.return_value = [
            {
                'merged': True,
                'merged_at': '2024-06-01T10:00:00Z',
                'number': 5,
                'url': 'http://pr/5',
            },
        ]
        vc = VerdictCorrelator(_make_config(), db, github_client=gh)

        resp_mock = MagicMock()
        resp_mock.json.return_value = [{'filename': 'a.py'}, {'filename': 'b.py'}]
        gh._get.return_value = resp_mock

        result = vc._fetch_actual_changes(
            'org', 'repo', 'comp', 'app', 'sha1',
            datetime(2024, 6, 1, 12, 0, 0)
        )
        assert result['type'] == 'pr_merged'
        assert result['files_changed'] == ['a.py', 'b.py']

    def test_skips_unmerged_prs(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = ('main',)
        gh = MagicMock()
        gh.list_pull_requests.return_value = [
            {'merged': False, 'number': 1},
        ]
        vc = VerdictCorrelator(_make_config(), db, github_client=gh)
        result = vc._fetch_actual_changes(
            'org', 'repo', 'comp', 'app', 'sha1',
            datetime(2024, 6, 1, 12, 0, 0)
        )
        assert result['type'] == 'rebuild'

    def test_file_fetch_error_gracefully_handled(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = ('main',)
        gh = MagicMock()
        gh.list_pull_requests.return_value = [
            {
                'merged': True,
                'merged_at': '2024-06-01T10:00:00Z',
                'number': 7,
                'url': 'http://pr/7',
            },
        ]
        vc = VerdictCorrelator(_make_config(), db, github_client=gh)
        gh._get.side_effect = RuntimeError("API error")

        result = vc._fetch_actual_changes(
            'org', 'repo', 'comp', 'app', 'sha1',
            datetime(2024, 6, 1, 12, 0, 0)
        )
        # Should still produce a result (files_changed=[])
        assert result['type'] in ('pr_merged', 'rebuild')

    def test_pr_without_merged_at_still_enriched(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = ('main',)
        gh = MagicMock()
        gh.list_pull_requests.return_value = [
            {
                'merged': True,
                'merged_at': None,
                'number': 8,
                'url': 'http://pr/8',
            },
        ]
        vc = VerdictCorrelator(_make_config(), db, github_client=gh)
        gh._get.return_value = MagicMock(json=MagicMock(return_value=[]))

        result = vc._fetch_actual_changes(
            'org', 'repo', 'comp', 'app', 'sha1', None
        )
        # merged_at is None so merged_at_dt won't be set
        assert result['type'] == 'rebuild'


# ===========================================================================
# VerdictCorrelator._record_resolution_evidence
# ===========================================================================

class TestRecordResolutionEvidence:

    def test_happy_path(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (42,)  # failure_id
        vc = VerdictCorrelator(_make_config(), db)

        actual = {'pr_url': 'http://pr/1', 'pr_number': 10,
                  'files_changed': ['a.py'], 'merged_at': '2024-01-01'}
        vc._record_resolution_evidence(
            'comp', 'app', 100, actual, 'correct', {'reason': 'matched'}
        )
        cursor.execute.assert_called()
        conn.commit.assert_called_once()

    def test_no_failure_row_returns_early(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None  # no failure found
        vc = VerdictCorrelator(_make_config(), db)

        actual = {'files_changed': []}
        vc._record_resolution_evidence(
            'comp', 'app', 100, actual, 'unknown', {'reason': 'x'}
        )
        # Should only have the SELECT call, not the INSERT
        assert cursor.execute.call_count == 1
        conn.commit.assert_not_called()

    def test_exception_handling(self):
        db, conn, cursor = _make_db()
        db.connection.return_value.__enter__.side_effect = RuntimeError("DB down")
        vc = VerdictCorrelator(_make_config(), db)

        actual = {'files_changed': []}
        # Should not raise — logs warning instead
        vc._record_resolution_evidence(
            'comp', 'app', 100, actual, 'unknown', {'reason': 'x'}
        )


# ===========================================================================
# VerdictCorrelator.correlate_build_resolution
# ===========================================================================

class TestCorrelateBuildResolution:

    @patch('collectors.verdict_correlator.AIAnalysisRepository')
    def test_no_analysis_found(self, mock_ai_repo_cls):
        db, _, _ = _make_db()
        mock_ai_repo_cls.return_value.get_analysis_by_component.return_value = None
        vc = VerdictCorrelator(_make_config(), db)

        verdict = vc.correlate_build_resolution(
            'comp', 'app', 'sha1', datetime(2024, 6, 1)
        )
        assert verdict == 'unknown'

    @patch('collectors.verdict_correlator.AIAnalysisRepository')
    def test_no_repo_url_returns_correct(self, mock_ai_repo_cls):
        db, conn, cursor = _make_db()
        mock_ai_repo_cls.return_value.get_analysis_by_component.return_value = {
            'id': 1, 'fix_action_type': 'rebuild', 'recommended_files': None,
        }
        cursor.fetchone.return_value = None  # no repo URL
        vc = VerdictCorrelator(_make_config(github_token='tok'), db)

        verdict = vc.correlate_build_resolution(
            'comp', 'app', 'sha1', datetime(2024, 6, 1)
        )
        assert verdict == 'correct'

    @patch('collectors.verdict_correlator.AIAnalysisRepository')
    def test_no_github_client_returns_correct(self, mock_ai_repo_cls):
        db, conn, cursor = _make_db()
        mock_ai_repo_cls.return_value.get_analysis_by_component.return_value = {
            'id': 1, 'fix_action_type': 'rebuild', 'recommended_files': None,
        }
        cursor.fetchone.return_value = ('https://github.com/org/repo',)
        config = MagicMock(spec=[])  # no github_token
        vc = VerdictCorrelator(config, db)
        assert vc.github is None

        verdict = vc.correlate_build_resolution(
            'comp', 'app', 'sha1', datetime(2024, 6, 1)
        )
        assert verdict == 'correct'

    @patch('collectors.verdict_correlator.parse_github_repo')
    @patch('collectors.verdict_correlator.AIAnalysisRepository')
    def test_full_happy_path(self, mock_ai_repo_cls, mock_parse):
        db, conn, cursor = _make_db()

        mock_ai_repo_cls.return_value.get_analysis_by_component.return_value = {
            'id': 1,
            'fix_action_type': 'file_change',
            'recommended_files': 'a.py, b.py',
        }
        # First fetchone: _get_repo_url => repo URL
        # Second fetchone: _get_component_branch => branch
        # Third fetchone: _record_resolution_evidence => failure_id
        cursor.fetchone.side_effect = [
            ('https://github.com/org/repo',),  # _get_repo_url
            ('main',),                          # _get_component_branch
            (99,),                              # _record_resolution_evidence
        ]
        mock_parse.return_value = ('org', 'repo')

        gh = MagicMock()
        resolution_ts = datetime(2024, 6, 1, 12, 0, 0)
        gh.list_pull_requests.return_value = [
            {
                'merged': True,
                'merged_at': '2024-06-01T11:00:00Z',
                'number': 5,
                'url': 'http://pr/5',
                'title': 'Fix a.py',
                'merge_commit_sha': 'sha_pr',
            },
        ]
        resp_mock = MagicMock()
        resp_mock.json.return_value = [{'filename': 'a.py'}, {'filename': 'b.py'}]
        gh._get.return_value = resp_mock

        vc = VerdictCorrelator(_make_config(github_token='tok'), db, github_client=gh)
        verdict = vc.correlate_build_resolution(
            'comp', 'app', 'sha1', resolution_ts
        )
        assert verdict == 'correct'

    @patch('collectors.verdict_correlator.parse_github_repo')
    @patch('collectors.verdict_correlator.AIAnalysisRepository')
    def test_parse_returns_none(self, mock_ai_repo_cls, mock_parse):
        db, conn, cursor = _make_db()
        mock_ai_repo_cls.return_value.get_analysis_by_component.return_value = {
            'id': 1, 'fix_action_type': 'rebuild', 'recommended_files': None,
        }
        cursor.fetchone.return_value = ('https://github.com/org/repo',)
        mock_parse.return_value = None

        gh = MagicMock()
        vc = VerdictCorrelator(_make_config(github_token='tok'), db, github_client=gh)
        verdict = vc.correlate_build_resolution(
            'comp', 'app', 'sha1', datetime(2024, 6, 1)
        )
        assert verdict == 'correct'

    @patch('collectors.verdict_correlator.AIAnalysisRepository')
    def test_recommended_files_as_string(self, mock_ai_repo_cls):
        """When recommended_files is a comma-separated string, it gets split."""
        db, conn, cursor = _make_db()
        mock_ai_repo_cls.return_value.get_analysis_by_component.return_value = {
            'id': 1,
            'fix_action_type': 'file_change',
            'recommended_files': 'a.py, b.py, ,',
        }
        cursor.fetchone.return_value = None  # no repo URL
        vc = VerdictCorrelator(_make_config(github_token='tok'), db)

        verdict = vc.correlate_build_resolution(
            'comp', 'app', 'sha1', datetime(2024, 6, 1)
        )
        # No repo URL => returns 'correct' (simple verdict path)
        assert verdict == 'correct'


# ===========================================================================
# _get_pr_files
# ===========================================================================

class TestGetPrFiles:

    def test_returns_filenames(self):
        db, _, _ = _make_db()
        gh = MagicMock()
        resp = MagicMock()
        resp.json.return_value = [
            {'filename': 'a.py'},
            {'filename': 'b.go'},
        ]
        gh._get.return_value = resp
        vc = VerdictCorrelator(_make_config(), db, github_client=gh)
        files = vc._get_pr_files('org', 'repo', 42)
        assert files == ['a.py', 'b.go']

    def test_returns_empty_when_no_response(self):
        db, _, _ = _make_db()
        gh = MagicMock()
        gh._get.return_value = None
        vc = VerdictCorrelator(_make_config(), db, github_client=gh)
        files = vc._get_pr_files('org', 'repo', 42)
        assert files == []


# ===========================================================================
# Constants sanity
# ===========================================================================

class TestConstants:

    def test_threshold_values(self):
        assert CORRECT_OVERLAP_THRESHOLD == 0.5
        assert PARTIAL_OVERLAP_THRESHOLD == 0.2

    def test_pr_merge_window(self):
        assert PR_MERGE_WINDOW == timedelta(hours=12)
