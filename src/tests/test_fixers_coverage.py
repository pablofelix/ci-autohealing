"""Comprehensive tests for fixers/verify_fixes.py and fixers/auto_fix.py."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import datetime
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db():
    """Create a mock DatabaseConnection with connection -> cursor chain.

    Supports both `conn.cursor()` used directly and as a context manager
    (``with conn.cursor() as cur``).
    """
    db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    db.connection.return_value.__enter__ = MagicMock(return_value=conn)
    db.connection.return_value.__exit__ = MagicMock(return_value=False)
    # Support `cursor = conn.cursor()` (direct use)
    conn.cursor.return_value = cursor
    # Support `with conn.cursor() as cur:` (context manager use)
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    return db, conn, cursor


def _make_attempt(**overrides):
    """Build a resolution_attempts row dict."""
    base = {
        'id': 1,
        'build_failure_id': 10,
        'conforma_result_id': None,
        'pr_url': 'https://github.com/org/repo/pull/42',
        'pr_number': 42,
        'pr_branch': 'ci-autohealing/fix-component',
        'attempted_at': datetime(2026, 6, 1),
        'component_name': 'my-component',
        'application': 'rhoai-v3-5',
    }
    base.update(overrides)
    return base


# ===================================================================
# verify_fixes.check_build_resolved_after
# ===================================================================

class TestCheckBuildResolvedAfter:
    """Tests for check_build_resolved_after."""

    def test_returns_pipelinerun_name_when_resolved(self):
        from fixers.verify_fixes import check_build_resolved_after
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = ('pipeline-run-123',)

        result = check_build_resolved_after(db, 'comp', 'app', datetime(2026, 6, 1))
        assert result == 'pipeline-run-123'
        cursor.execute.assert_called_once()

    def test_returns_none_when_not_resolved(self):
        from fixers.verify_fixes import check_build_resolved_after
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None

        result = check_build_resolved_after(db, 'comp', 'app', datetime(2026, 6, 1))
        assert result is None


# ===================================================================
# verify_fixes.verify_one
# ===================================================================

class TestVerifyOne:
    """Tests for verify_one."""

    def test_unparseable_pr_url_returns_skip(self):
        from fixers.verify_fixes import verify_one
        attempt = _make_attempt(pr_url='not-a-url')
        result = verify_one(attempt, MagicMock(), MagicMock(), MagicMock())
        assert result == 'skip'

    def test_pr_fetch_failure_returns_skip(self):
        from fixers.verify_fixes import verify_one
        github = MagicMock()
        github.get_pull_request.return_value = None
        attempt = _make_attempt()
        result = verify_one(attempt, github, MagicMock(), MagicMock())
        assert result == 'skip'

    def test_pr_open_returns_open(self):
        from fixers.verify_fixes import verify_one
        github = MagicMock()
        github.get_pull_request.return_value = {'state': 'open', 'merged': False}
        attempt = _make_attempt()
        result = verify_one(attempt, github, MagicMock(), MagicMock())
        assert result == 'open'

    def test_pr_closed_not_merged_returns_abandoned(self):
        from fixers.verify_fixes import verify_one
        github = MagicMock()
        github.get_pull_request.return_value = {
            'state': 'closed', 'merged': False
        }
        repo_obj = MagicMock()
        attempt = _make_attempt()
        result = verify_one(attempt, github, MagicMock(), repo_obj)
        assert result == 'abandoned'
        repo_obj.update_verification.assert_called_once()

    def test_pr_closed_not_merged_dry_run(self):
        from fixers.verify_fixes import verify_one
        github = MagicMock()
        github.get_pull_request.return_value = {
            'state': 'closed', 'merged': False
        }
        repo_obj = MagicMock()
        attempt = _make_attempt()
        result = verify_one(attempt, github, MagicMock(), repo_obj, dry_run=True)
        assert result == 'abandoned'
        repo_obj.update_verification.assert_not_called()

    @patch('fixers.verify_fixes.check_build_resolved_after')
    def test_pr_merged_build_succeeded(self, mock_check):
        from fixers.verify_fixes import verify_one
        github = MagicMock()
        github.get_pull_request.return_value = {
            'state': 'closed', 'merged': True,
            'merged_at': '2026-06-15T12:00:00Z',
        }
        mock_check.return_value = 'pipeline-run-abc'
        repo_obj = MagicMock()
        attempt = _make_attempt()
        result = verify_one(attempt, github, MagicMock(), repo_obj)
        assert result == 'success'
        repo_obj.update_verification.assert_called_once()
        call_kwargs = repo_obj.update_verification.call_args
        assert call_kwargs[1]['was_successful'] is True or call_kwargs[0][5] is True

    @patch('fixers.verify_fixes.check_build_resolved_after')
    def test_pr_merged_build_not_yet_resolved(self, mock_check):
        from fixers.verify_fixes import verify_one
        github = MagicMock()
        github.get_pull_request.return_value = {
            'state': 'closed', 'merged': True,
            'merged_at': '2026-06-15T12:00:00Z',
        }
        mock_check.return_value = None
        repo_obj = MagicMock()
        attempt = _make_attempt()
        result = verify_one(attempt, github, MagicMock(), repo_obj)
        assert result == 'merged-pending'
        # Should not call update_verification when build is not yet resolved
        repo_obj.update_verification.assert_not_called()

    @patch('fixers.verify_fixes.check_build_resolved_after')
    def test_pr_merged_invalid_timestamp_uses_utcnow(self, mock_check):
        from fixers.verify_fixes import verify_one
        github = MagicMock()
        github.get_pull_request.return_value = {
            'state': 'closed', 'merged': True,
            'merged_at': None,  # triggers except branch
        }
        mock_check.return_value = None
        attempt = _make_attempt()
        result = verify_one(attempt, github, MagicMock(), MagicMock())
        assert result == 'merged-pending'

    @patch('fixers.verify_fixes.check_build_resolved_after')
    def test_dry_run_skips_db_write_on_success(self, mock_check):
        from fixers.verify_fixes import verify_one
        github = MagicMock()
        github.get_pull_request.return_value = {
            'state': 'closed', 'merged': True,
            'merged_at': '2026-06-15T12:00:00Z',
        }
        mock_check.return_value = 'pipeline-run-abc'
        repo_obj = MagicMock()
        attempt = _make_attempt()
        result = verify_one(attempt, github, MagicMock(), repo_obj, dry_run=True)
        assert result == 'success'
        repo_obj.update_verification.assert_not_called()


# ===================================================================
# verify_fixes.main
# ===================================================================

class TestVerifyFixesMain:
    """Tests for verify_fixes main()."""

    @patch('fixers.verify_fixes.ResolutionAttemptRepository')
    @patch('fixers.verify_fixes.GitHubClient')
    @patch('fixers.verify_fixes.DatabaseConnection')
    @patch('fixers.verify_fixes.CollectorConfig')
    def test_no_github_token(self, MockConfig, MockDB, MockGH, MockRepo):
        from fixers.verify_fixes import main

        config = MagicMock()
        config.github_token = None
        MockConfig.from_env.return_value = config

        with patch('sys.argv', ['prog']):
            result = main()
        assert result == 1

    @patch('fixers.verify_fixes.ErrorPatternRepository', create=True)
    @patch('fixers.verify_fixes.ResolutionAttemptRepository')
    @patch('fixers.verify_fixes.GitHubClient')
    @patch('fixers.verify_fixes.DatabaseConnection')
    @patch('fixers.verify_fixes.CollectorConfig')
    def test_no_pending_returns_zero(self, MockConfig, MockDB, MockGH,
                                     MockRepo, MockEPR):
        from fixers.verify_fixes import main

        config = MagicMock()
        config.github_token = 'ghp_test'
        MockConfig.from_env.return_value = config
        MockRepo.return_value.get_pending_verification.return_value = []

        with patch('sys.argv', ['prog']):
            result = main()
        assert result == 0

    @patch('fixers.verify_fixes.verify_one')
    @patch('fixers.verify_fixes.ErrorPatternRepository', create=True)
    @patch('fixers.verify_fixes.ResolutionAttemptRepository')
    @patch('fixers.verify_fixes.GitHubClient')
    @patch('fixers.verify_fixes.DatabaseConnection')
    @patch('fixers.verify_fixes.CollectorConfig')
    def test_verifies_pending_attempts(self, MockConfig, MockDB, MockGH,
                                       MockRepo, MockEPR, mock_verify_one):
        from fixers.verify_fixes import main

        config = MagicMock()
        config.github_token = 'ghp_test'
        MockConfig.from_env.return_value = config

        pending = [_make_attempt(), _make_attempt(id=2)]
        MockRepo.return_value.get_pending_verification.return_value = pending
        mock_verify_one.return_value = 'success'

        with patch('sys.argv', ['prog']):
            result = main()
        assert result == 0
        assert mock_verify_one.call_count == 2


# ===================================================================
# auto_fix._get_candidates
# ===================================================================

class TestGetCandidates:
    """Tests for auto_fix._get_candidates."""

    def test_returns_list_of_dicts(self):
        from fixers.auto_fix import _get_candidates
        db, conn, cursor = _make_db()
        cursor.description = [
            ('id',), ('component_name',), ('application',),
            ('repository_url',), ('failure_category',), ('confidence_score',),
        ]
        cursor.fetchall.return_value = [
            (1, 'comp-a', 'app', 'https://github.com/org/repo', 'policy_hermetic_build', 0.98),
        ]
        result = _get_candidates(db, 0.95, 3)
        assert len(result) == 1
        assert result[0]['component_name'] == 'comp-a'
        assert result[0]['confidence_score'] == 0.98

    def test_empty_when_no_candidates(self):
        from fixers.auto_fix import _get_candidates
        db, conn, cursor = _make_db()
        cursor.description = [
            ('id',), ('component_name',), ('application',),
            ('repository_url',), ('failure_category',), ('confidence_score',),
        ]
        cursor.fetchall.return_value = []
        result = _get_candidates(db, 0.95, 3)
        assert result == []


# ===================================================================
# auto_fix._branch_exists
# ===================================================================

class TestBranchExists:
    """Tests for auto_fix._branch_exists."""

    @patch('fixers.auto_fix.parse_github_repo')
    def test_true_when_sha_found(self, mock_parse):
        from fixers.auto_fix import _branch_exists
        mock_parse.return_value = ('org', 'repo')
        github = MagicMock()
        github.get_ref_sha.return_value = 'abc123'
        assert _branch_exists(github, 'https://github.com/org/repo', 'branch') is True

    @patch('fixers.auto_fix.parse_github_repo')
    def test_false_when_no_sha(self, mock_parse):
        from fixers.auto_fix import _branch_exists
        mock_parse.return_value = ('org', 'repo')
        github = MagicMock()
        github.get_ref_sha.return_value = None
        assert _branch_exists(github, 'https://github.com/org/repo', 'branch') is False

    @patch('fixers.auto_fix.parse_github_repo')
    def test_false_when_parse_fails(self, mock_parse):
        from fixers.auto_fix import _branch_exists
        mock_parse.return_value = None
        github = MagicMock()
        assert _branch_exists(github, 'bad-url', 'branch') is False

    @patch('fixers.auto_fix.parse_github_repo')
    def test_false_on_exception(self, mock_parse):
        from fixers.auto_fix import _branch_exists
        mock_parse.return_value = ('org', 'repo')
        github = MagicMock()
        github.get_ref_sha.side_effect = RuntimeError("network error")
        assert _branch_exists(github, 'https://github.com/org/repo', 'branch') is False


# ===================================================================
# auto_fix.main
# ===================================================================

class TestAutoFixMain:
    """Tests for auto_fix main()."""

    def test_refuses_without_autonomous_mode(self):
        from fixers.auto_fix import main
        with patch.dict(os.environ, {'AUTONOMOUS_MODE': 'false'}, clear=False):
            with patch('sys.argv', ['prog']):
                result = main()
        assert result == 1

    @patch('fixers.auto_fix.DatabaseConnection')
    @patch('fixers.auto_fix.CollectorConfig')
    def test_refuses_without_github_token(self, MockConfig, MockDB):
        from fixers.auto_fix import main
        config = MagicMock()
        config.github_token = None
        MockConfig.from_env.return_value = config

        with patch.dict(os.environ, {'AUTONOMOUS_MODE': 'true'}, clear=False):
            with patch('sys.argv', ['prog']):
                result = main()
        assert result == 1

    @patch('fixers.auto_fix._get_candidates')
    @patch('fixers.auto_fix.GitHubClient')
    @patch('fixers.auto_fix.DatabaseConnection')
    @patch('fixers.auto_fix.CollectorConfig')
    def test_no_candidates_returns_zero(self, MockConfig, MockDB, MockGH,
                                        mock_get_candidates):
        from fixers.auto_fix import main
        config = MagicMock()
        config.github_token = 'ghp_test'
        config.auto_fix_min_confidence = 0.95
        config.auto_fix_max_per_run = 3
        MockConfig.from_env.return_value = config
        mock_get_candidates.return_value = []

        with patch.dict(os.environ, {'AUTONOMOUS_MODE': 'true'}, clear=False):
            with patch('sys.argv', ['prog']):
                result = main()
        assert result == 0

    @patch('fixers.auto_fix._branch_exists')
    @patch('fixers.auto_fix.conforma_branch_name')
    @patch('fixers.auto_fix._get_candidates')
    @patch('fixers.auto_fix.GitHubClient')
    @patch('fixers.auto_fix.DatabaseConnection')
    @patch('fixers.auto_fix.CollectorConfig')
    def test_skips_existing_branch(self, MockConfig, MockDB, MockGH,
                                    mock_get_candidates, mock_branch_name,
                                    mock_branch_exists):
        from fixers.auto_fix import main
        config = MagicMock()
        config.github_token = 'ghp_test'
        config.auto_fix_min_confidence = 0.95
        config.auto_fix_max_per_run = 3
        MockConfig.from_env.return_value = config
        mock_get_candidates.return_value = [{
            'id': 1, 'component_name': 'comp', 'application': 'app',
            'repository_url': 'https://github.com/org/repo',
            'failure_category': 'policy_hermetic_build',
            'confidence_score': 0.99,
        }]
        mock_branch_name.return_value = 'ci-autohealing/fix-comp-1'
        mock_branch_exists.return_value = True

        with patch.dict(os.environ, {'AUTONOMOUS_MODE': 'true'}, clear=False):
            with patch('sys.argv', ['prog']):
                result = main()
        assert result == 0

    @patch('fixers.auto_fix._branch_exists')
    @patch('fixers.auto_fix.conforma_branch_name')
    @patch('fixers.auto_fix._get_candidates')
    @patch('fixers.auto_fix.GitHubClient')
    @patch('fixers.auto_fix.DatabaseConnection')
    @patch('fixers.auto_fix.CollectorConfig')
    def test_runs_fixer_successfully(self, MockConfig, MockDB, MockGH,
                                      mock_get_candidates, mock_branch_name,
                                      mock_branch_exists):
        from fixers.auto_fix import _FIXER_BY_CATEGORY, main
        config = MagicMock()
        config.github_token = 'ghp_test'
        config.auto_fix_min_confidence = 0.95
        config.auto_fix_max_per_run = 3
        MockConfig.from_env.return_value = config
        mock_get_candidates.return_value = [{
            'id': 1, 'component_name': 'comp', 'application': 'app',
            'repository_url': 'https://github.com/org/repo',
            'failure_category': 'policy_hermetic_build',
            'confidence_score': 0.99,
        }]
        mock_branch_name.return_value = 'ci-autohealing/fix-comp-1'
        mock_branch_exists.return_value = False

        mock_fixer = MagicMock(return_value=0)
        with patch.dict(_FIXER_BY_CATEGORY, {'policy_hermetic_build': mock_fixer}):
            with patch.dict(os.environ, {'AUTONOMOUS_MODE': 'true'}, clear=False):
                with patch('sys.argv', ['prog']):
                    result = main()
        assert result == 0
        mock_fixer.assert_called_once()

    @patch('fixers.auto_fix._branch_exists')
    @patch('fixers.auto_fix.conforma_branch_name')
    @patch('fixers.auto_fix._get_candidates')
    @patch('fixers.auto_fix.GitHubClient')
    @patch('fixers.auto_fix.DatabaseConnection')
    @patch('fixers.auto_fix.CollectorConfig')
    def test_handles_fixer_exception(self, MockConfig, MockDB, MockGH,
                                      mock_get_candidates, mock_branch_name,
                                      mock_branch_exists):
        from fixers.auto_fix import _FIXER_BY_CATEGORY, main
        config = MagicMock()
        config.github_token = 'ghp_test'
        config.auto_fix_min_confidence = 0.95
        config.auto_fix_max_per_run = 3
        MockConfig.from_env.return_value = config
        mock_get_candidates.return_value = [{
            'id': 1, 'component_name': 'comp', 'application': 'app',
            'repository_url': 'https://github.com/org/repo',
            'failure_category': 'policy_hermetic_build',
            'confidence_score': 0.99,
        }]
        mock_branch_name.return_value = 'ci-autohealing/fix-comp-1'
        mock_branch_exists.return_value = False

        mock_fixer = MagicMock(side_effect=RuntimeError("fixer crash"))
        with patch.dict(_FIXER_BY_CATEGORY, {'policy_hermetic_build': mock_fixer}):
            with patch.dict(os.environ, {'AUTONOMOUS_MODE': 'true'}, clear=False):
                with patch('sys.argv', ['prog']):
                    result = main()
        assert result == 0  # main still returns 0

    @patch('fixers.auto_fix._branch_exists')
    @patch('fixers.auto_fix.conforma_branch_name')
    @patch('fixers.auto_fix._get_candidates')
    @patch('fixers.auto_fix.GitHubClient')
    @patch('fixers.auto_fix.DatabaseConnection')
    @patch('fixers.auto_fix.CollectorConfig')
    def test_skips_unknown_category(self, MockConfig, MockDB, MockGH,
                                     mock_get_candidates, mock_branch_name,
                                     mock_branch_exists):
        from fixers.auto_fix import main
        config = MagicMock()
        config.github_token = 'ghp_test'
        config.auto_fix_min_confidence = 0.95
        config.auto_fix_max_per_run = 3
        MockConfig.from_env.return_value = config
        mock_get_candidates.return_value = [{
            'id': 1, 'component_name': 'comp', 'application': 'app',
            'repository_url': 'https://github.com/org/repo',
            'failure_category': 'unknown_category',
            'confidence_score': 0.99,
        }]
        mock_branch_name.return_value = 'ci-autohealing/fix-comp-1'
        mock_branch_exists.return_value = False

        with patch.dict(os.environ, {'AUTONOMOUS_MODE': 'true'}, clear=False):
            with patch('sys.argv', ['prog']):
                result = main()
        assert result == 0

    @patch('fixers.auto_fix._branch_exists')
    @patch('fixers.auto_fix.conforma_branch_name')
    @patch('fixers.auto_fix._get_candidates')
    @patch('fixers.auto_fix.GitHubClient')
    @patch('fixers.auto_fix.DatabaseConnection')
    @patch('fixers.auto_fix.CollectorConfig')
    def test_fixer_returns_nonzero(self, MockConfig, MockDB, MockGH,
                                    mock_get_candidates, mock_branch_name,
                                    mock_branch_exists):
        from fixers.auto_fix import _FIXER_BY_CATEGORY, main
        config = MagicMock()
        config.github_token = 'ghp_test'
        config.auto_fix_min_confidence = 0.95
        config.auto_fix_max_per_run = 3
        MockConfig.from_env.return_value = config
        mock_get_candidates.return_value = [{
            'id': 1, 'component_name': 'comp', 'application': 'app',
            'repository_url': 'https://github.com/org/repo',
            'failure_category': 'policy_hermetic_build',
            'confidence_score': 0.99,
        }]
        mock_branch_name.return_value = 'ci-autohealing/fix-comp-1'
        mock_branch_exists.return_value = False

        mock_fixer = MagicMock(return_value=1)  # non-zero
        with patch.dict(_FIXER_BY_CATEGORY, {'policy_hermetic_build': mock_fixer}):
            with patch.dict(os.environ, {'AUTONOMOUS_MODE': 'true'}, clear=False):
                with patch('sys.argv', ['prog']):
                    result = main()
        assert result == 0  # main still returns 0, logs warning
