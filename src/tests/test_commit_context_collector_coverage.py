"""Comprehensive tests for collectors/commit_context_collector.py."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
from unittest.mock import MagicMock, patch

from collectors.commit_context_collector import CommitContextCollector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db():
    """Create a mock DatabaseConnection with connection -> cursor chain."""
    db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    db.connection.return_value.__enter__ = MagicMock(return_value=conn)
    db.connection.return_value.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    return db, conn, cursor


def _make_config(app_name='rhoai-v3-5', github_token='ghp_test'):
    """Build a mock CollectorConfig."""
    config = MagicMock()
    config.k8s.application_name = app_name
    config.github_token = github_token
    return config


def _make_failure(failure_id=1, component='comp-a', sha='abc12345',
                  repo_url='https://github.com/org/repo', branch='main'):
    return {
        'id': failure_id,
        'component_name': component,
        'pipelinerun_name': f'pipelinerun-{failure_id}',
        'commit_sha': sha,
        'repository_url': repo_url,
        'branch': branch,
    }


# ===================================================================
# __init__
# ===================================================================

class TestInit:
    """Tests for CommitContextCollector initialization."""

    def test_uses_provided_db_and_github(self):
        config = _make_config()
        db = MagicMock()
        gh = MagicMock()
        collector = CommitContextCollector(config, db=db, github=gh)
        assert collector.db is db
        assert collector.github is gh

    @patch('collectors.commit_context_collector.GitHubClient')
    @patch('collectors.commit_context_collector.DatabaseConnection')
    def test_creates_defaults_when_not_provided(self, MockDB, MockGH):
        config = _make_config()
        collector = CommitContextCollector(config)
        MockDB.assert_called_once_with(config.db)
        MockGH.assert_called_once_with(token='ghp_test')


# ===================================================================
# get_pending_failures
# ===================================================================

class TestGetPendingFailures:
    """Tests for get_pending_failures."""

    def test_returns_list_of_dicts(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        cursor.fetchall.return_value = [
            (1, 'comp-a', 'pr-1', 'sha123', 'https://github.com/org/repo', 'main'),
            (2, 'comp-b', 'pr-2', 'sha456', 'https://github.com/org/repo2', 'release'),
        ]
        collector = CommitContextCollector(config, db=db, github=MagicMock())
        result = collector.get_pending_failures(limit=10)
        assert len(result) == 2
        assert result[0]['id'] == 1
        assert result[0]['component_name'] == 'comp-a'
        assert result[1]['branch'] == 'release'

    def test_empty_when_no_pending(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        cursor.fetchall.return_value = []
        collector = CommitContextCollector(config, db=db, github=MagicMock())
        result = collector.get_pending_failures()
        assert result == []


# ===================================================================
# store_context
# ===================================================================

class TestStoreContext:
    """Tests for store_context."""

    def test_stores_json_in_db(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        collector = CommitContextCollector(config, db=db, github=MagicMock())

        context = {'diff': 'some diff', 'files': ['a.go']}
        collector.store_context(42, context)

        cursor.execute.assert_called_once()
        args = cursor.execute.call_args[0]
        assert 'UPDATE build_failures' in args[0]
        assert json.dumps(context) == args[1][0]
        assert args[1][1] == 42
        conn.commit.assert_called_once()


# ===================================================================
# run
# ===================================================================

class TestRun:
    """Tests for the run() method."""

    def test_rate_limit_too_low_skips(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        github = MagicMock()
        github.check_rate_limit.return_value = {'remaining': 10, 'limit': 5000}
        collector = CommitContextCollector(config, db=db, github=github)

        result = collector.run()
        assert result['fetched'] == 0
        assert result['reason'] == 'rate_limit'

    def test_rate_limit_none_continues(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        github = MagicMock()
        github.check_rate_limit.return_value = None
        cursor.fetchall.return_value = []  # no pending
        collector = CommitContextCollector(config, db=db, github=github)

        result = collector.run()
        assert result['fetched'] == 0
        assert 'reason' not in result

    def test_no_pending_returns_zero_counts(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        github = MagicMock()
        github.check_rate_limit.return_value = {'remaining': 5000, 'limit': 5000}
        cursor.fetchall.return_value = []
        collector = CommitContextCollector(config, db=db, github=github)

        result = collector.run()
        assert result['fetched'] == 0
        assert result['skipped'] == 0

    def test_fetches_and_stores_context(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        github = MagicMock()
        github.check_rate_limit.return_value = {'remaining': 5000, 'limit': 5000}

        failures = [_make_failure(1, 'comp-a', 'sha111')]
        cursor.fetchall.return_value = [
            (1, 'comp-a', 'pr-1', 'sha111', 'https://github.com/org/repo', 'main'),
        ]
        github.get_commit_context.return_value = {'diff': '+line'}

        collector = CommitContextCollector(config, db=db, github=github)
        result = collector.run(limit=5)

        assert result['fetched'] == 1
        assert result['skipped'] == 0

    def test_reuses_context_for_duplicate_sha(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        github = MagicMock()
        github.check_rate_limit.return_value = {'remaining': 5000, 'limit': 5000}

        # Two failures with the same SHA
        cursor.fetchall.return_value = [
            (1, 'comp-a', 'pr-1', 'sha111', 'https://github.com/org/repo', 'main'),
            (2, 'comp-b', 'pr-2', 'sha111', 'https://github.com/org/repo', 'main'),
        ]
        github.get_commit_context.return_value = {'diff': '+line'}

        collector = CommitContextCollector(config, db=db, github=github)
        result = collector.run(limit=5)

        assert result['fetched'] == 2
        # get_commit_context should only be called once (reused for second)
        assert github.get_commit_context.call_count == 1

    def test_skips_on_no_context_returned(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        github = MagicMock()
        github.check_rate_limit.return_value = {'remaining': 5000, 'limit': 5000}

        cursor.fetchall.return_value = [
            (1, 'comp-a', 'pr-1', 'sha111', 'https://github.com/org/repo', 'main'),
        ]
        github.get_commit_context.return_value = None

        collector = CommitContextCollector(config, db=db, github=github)
        result = collector.run(limit=5)

        assert result['fetched'] == 0
        assert result['skipped'] == 1

    def test_skips_on_exception(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        github = MagicMock()
        github.check_rate_limit.return_value = {'remaining': 5000, 'limit': 5000}

        cursor.fetchall.return_value = [
            (1, 'comp-a', 'pr-1', 'sha111', 'https://github.com/org/repo', 'main'),
        ]
        github.get_commit_context.side_effect = RuntimeError("API error")

        collector = CommitContextCollector(config, db=db, github=github)
        result = collector.run(limit=5)

        assert result['fetched'] == 0
        assert result['skipped'] == 1

    def test_duration_is_positive(self):
        db, conn, cursor = _make_db()
        config = _make_config()
        github = MagicMock()
        github.check_rate_limit.return_value = {'remaining': 5000, 'limit': 5000}
        cursor.fetchall.return_value = []
        collector = CommitContextCollector(config, db=db, github=github)

        result = collector.run()
        assert result['duration'] >= 0
