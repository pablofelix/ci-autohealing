"""Tests for repository layer: factory, connection, and representative repos."""

import importlib
import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Guard against sys.modules pollution from other test files.
# Some tests (e.g. test_enrichment_orchestrator_coverage, test_poll_jira_coverage)
# replace 'repositories.connection' (and even 'repositories') with a MagicMock
# at module level, which breaks @patch('repositories.connection.psycopg2') for
# subsequent tests.  Force-reload the real modules so patches work correctly.
# ---------------------------------------------------------------------------
def _ensure_real_module(name):
    """If *name* was replaced by a MagicMock in sys.modules, reload the real one."""
    # Also restore parent packages (e.g. 'repositories' for 'repositories.connection')
    parts = name.split('.')
    for i in range(len(parts)):
        partial = '.'.join(parts[:i + 1])
        mod = sys.modules.get(partial)
        if mod is not None and isinstance(mod, MagicMock):
            sys.modules.pop(partial, None)
    # Now import (or re-import) the module
    mod = sys.modules.get(name)
    if mod is None or isinstance(mod, MagicMock):
        sys.modules.pop(name, None)
        importlib.import_module(name)


_ensure_real_module('repositories.connection')
_ensure_real_module('repositories.repository_factory')


def _mock_db():
    """Create a mock DB connection with cursor.

    Handles both `cursor = conn.cursor()` and `with conn.cursor() as cur:` patterns.
    """
    db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor

    @contextmanager
    def _connection():
        yield conn

    db.connection = _connection
    return db, conn, cursor


# ═══════════════════════════════════════════════════════════════════════
# Repository factory tests
# ═══════════════════════════════════════════════════════════════════════

class TestRepositoryFactory:
    def setup_method(self):
        import repositories.repository_factory as rf
        rf._db_pool = None
        rf._repo_cache = {}

    def teardown_method(self):
        import repositories.repository_factory as rf
        rf._db_pool = None
        rf._repo_cache = {}

    def test_get_repository_before_init(self):
        from repositories.repository_factory import get_repository
        with pytest.raises(RuntimeError, match="Call init_pool"):
            get_repository(MagicMock)

    def test_get_pool_before_init(self):
        from repositories.repository_factory import get_pool
        with pytest.raises(RuntimeError, match="Call init_pool"):
            get_pool()

    @patch('repositories.repository_factory.PooledDatabaseConnection')
    def test_init_pool(self, mock_pooled):
        from repositories.repository_factory import get_pool, init_pool
        config = MagicMock()
        init_pool(config, pool_size=5)
        mock_pooled.assert_called_once_with(config, maxconn=5)
        assert get_pool() is not None

    @patch('repositories.repository_factory.PooledDatabaseConnection')
    def test_init_pool_idempotent(self, mock_pooled):
        from repositories.repository_factory import init_pool
        config = MagicMock()
        init_pool(config)
        init_pool(config)  # second call is no-op
        assert mock_pooled.call_count == 1

    @patch('repositories.repository_factory.PooledDatabaseConnection')
    def test_get_repository_caches(self, mock_pooled):
        from repositories.repository_factory import get_repository, init_pool
        init_pool(MagicMock())

        class FakeRepo:
            def __init__(self, db):
                self.db = db

        repo1 = get_repository(FakeRepo)
        repo2 = get_repository(FakeRepo)
        assert repo1 is repo2

    @patch('repositories.repository_factory.PooledDatabaseConnection')
    def test_close_pool(self, mock_pooled):
        from repositories.repository_factory import close_pool, get_pool, init_pool
        init_pool(MagicMock())
        close_pool()
        with pytest.raises(RuntimeError):
            get_pool()


# ═══════════════════════════════════════════════════════════════════════
# DatabaseConnection tests
# ═══════════════════════════════════════════════════════════════════════

class TestDatabaseConnection:
    @patch('repositories.connection.psycopg2')
    def test_connection_commits(self, mock_pg):
        from repositories.connection import DatabaseConnection
        config = MagicMock()
        config.connection_string = 'postgresql://test'
        mock_conn = MagicMock()
        mock_pg.connect.return_value = mock_conn

        db = DatabaseConnection(config)
        with db.connection() as conn:
            conn.cursor().execute("SELECT 1")

        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('repositories.connection.psycopg2')
    def test_connection_rollback_on_error(self, mock_pg):
        from repositories.connection import DatabaseConnection
        config = MagicMock()
        config.connection_string = 'postgresql://test'
        mock_conn = MagicMock()
        mock_pg.connect.return_value = mock_conn

        db = DatabaseConnection(config)
        with pytest.raises(ValueError), db.connection():
            raise ValueError("test error")

        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('repositories.connection.psycopg2')
    def test_create_scan(self, mock_pg):
        from repositories.connection import DatabaseConnection
        config = MagicMock()
        config.connection_string = 'postgresql://test'
        mock_conn = MagicMock()
        mock_pg.connect.return_value = mock_conn

        db = DatabaseConnection(config)
        scan_id = db.create_scan('python', 'full')
        assert isinstance(scan_id, str)
        assert len(scan_id) == 36  # UUID

    @patch('repositories.connection.psycopg2')
    def test_complete_scan(self, mock_pg):
        from repositories.connection import DatabaseConnection
        config = MagicMock()
        config.connection_string = 'postgresql://test'
        mock_conn = MagicMock()
        mock_pg.connect.return_value = mock_conn

        db = DatabaseConnection(config)
        result = MagicMock()
        result.duration_seconds = 10
        result.components_scanned = 5
        result.failures_found = 2
        result.new_failures = 1
        result.logs_fetched = True
        db.complete_scan('test-id', result)
        mock_conn.cursor().execute.assert_called()


# ═══════════════════════════════════════════════════════════════════════
# BuildFailureRepository tests
# ═══════════════════════════════════════════════════════════════════════

class TestBuildFailureRepository:
    def _make_repo(self):
        from repositories.build_failure_repository import BuildFailureRepository
        db, conn, cursor = _mock_db()
        return BuildFailureRepository(db), cursor

    def test_pipelinerun_exists_true(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = (1,)
        assert repo.pipelinerun_exists('pr-1') is True

    def test_pipelinerun_exists_false(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = None
        assert repo.pipelinerun_exists('pr-1') is False

    def test_find_unresolved_component_names(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = [('comp-a',), ('comp-b',)]
        result = repo.find_unresolved_component_names('test-app')
        assert result == {'comp-a', 'comp-b'}

    def test_find_unresolved_component_names_empty(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = []
        result = repo.find_unresolved_component_names('test-app')
        assert result == set()

    def test_find_unresolved_component_names_error(self):
        from repositories.build_failure_repository import BuildFailureRepository
        db = MagicMock()
        db.connection.side_effect = Exception("db down")
        repo = BuildFailureRepository(db)
        result = repo.find_unresolved_component_names('test-app')
        assert result == set()

    def test_find_failing_component_names(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = [('comp-failing',)]
        result = repo.find_failing_component_names('test-app')
        assert result == {'comp-failing'}

    def test_find_failing_component_names_error(self):
        from repositories.build_failure_repository import BuildFailureRepository
        db = MagicMock()
        db.connection.side_effect = Exception("db down")
        repo = BuildFailureRepository(db)
        assert repo.find_failing_component_names('test-app') is None

    def test_count_unresolved(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = (3,)
        assert repo.count_unresolved('comp-a', 'test-app') == 3

    def test_is_pipelinerun_complete_true(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = (True, True, True)
        assert repo.is_pipelinerun_complete('pr-1') is True

    def test_is_pipelinerun_complete_false(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = (True, False, True)
        assert repo.is_pipelinerun_complete('pr-1') is False

    def test_is_pipelinerun_complete_not_found(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = None
        assert repo.is_pipelinerun_complete('pr-1') is False


# ═══════════════════════════════════════════════════════════════════════
# ConformaRepository tests
# ═══════════════════════════════════════════════════════════════════════

class TestConformaRepository:
    def _make_repo(self):
        from repositories.conforma_repository import ConformaRepository
        db, conn, cursor = _mock_db()
        return ConformaRepository(db), cursor

    def test_get_violation_summaries(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = []
        result = repo.get_violation_summaries('test-app')
        assert isinstance(result, list)

    def test_find_unresolved_component_names(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = [('comp-x',), ('comp-y',)]
        result = repo.find_unresolved_component_names('test-app')
        assert 'comp-x' in result
        assert 'comp-y' in result

    def test_find_unresolved_component_names_error(self):
        from repositories.conforma_repository import ConformaRepository
        db = MagicMock()
        db.connection.side_effect = Exception("db down")
        repo = ConformaRepository(db)
        result = repo.find_unresolved_component_names('test-app')
        assert result == set()


# ═══════════════════════════════════════════════════════════════════════
# TriageRepository tests
# ═══════════════════════════════════════════════════════════════════════

class TestTriageRepository:
    def _make_repo(self):
        from repositories.triage_repository import TriageRepository
        db, conn, cursor = _mock_db()
        return TriageRepository(db), cursor

    def test_get_active(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = []
        result = repo.get_active('test-app')
        assert isinstance(result, list)

    def test_get_by_id_found(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = (
            1, 'group', ['comp-a'], 'root cause', 'step-build',
            'new', [], 'JIRA-1', 'notes', None, None, None,
            '2026-01-01', '2026-01-01', 'test-app',
        )
        result = repo.get_by_id(1)
        assert result is not None
        assert result['id'] == 1
        assert result['status'] == 'new'

    def test_get_by_id_not_found(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = None
        assert repo.get_by_id(999) is None

    def test_get_summary(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = (10, 2, 3, 4, 1)
        result = repo.get_summary('test-app')
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════
# AIAnalysisRepository tests
# ═══════════════════════════════════════════════════════════════════════

class TestAIAnalysisRepository:
    def _make_repo(self):
        from repositories.ai_analysis_repository import AIAnalysisRepository
        db, conn, cursor = _mock_db()
        return AIAnalysisRepository(db), cursor

    def test_get_calibration_data_empty(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = []
        result = repo.get_calibration_data()
        assert result == []

    def test_get_verdict_stats_by_category_empty(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = []
        result = repo.get_verdict_stats_by_category()
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# ErrorPatternRepository tests
# ═══════════════════════════════════════════════════════════════════════

class TestErrorPatternRepository:
    def _make_repo(self):
        from repositories.error_pattern_repository import ErrorPatternRepository
        db, conn, cursor = _mock_db()
        return ErrorPatternRepository(db), cursor

    def test_get_all_empty(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = []
        result = repo.get_all()
        assert result == []

    def test_get_by_category(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = []
        result = repo.get_by_category('build', 'dependency_issue')
        assert isinstance(result, (list, dict))
        assert len(result) == 0

    def test_record_occurrence(self):
        repo, cursor = self._make_repo()
        repo.record_occurrence(1, 0.85)
        cursor.execute.assert_called()


# ═══════════════════════════════════════════════════════════════════════
# SyncStatusRepository tests
# ═══════════════════════════════════════════════════════════════════════

class TestSyncStatusRepository:
    def _make_repo(self):
        from repositories.sync_status_repository import SyncStatusRepository
        db, conn, cursor = _mock_db()
        return SyncStatusRepository(db), cursor

    def test_save_build_sync_status(self):
        repo, cursor = self._make_repo()
        status = {
            'running_builds': [],
            'in_sync': True,
            'cluster_connected': True,
            'cluster_components': 10,
            'db_components': 10,
            'missing_in_db': [],
            'extra_in_db': [],
            'retriggered_components': [],
            'error': None,
        }
        repo.save_build_sync_status('test-app', status, 10.5)
        cursor.execute.assert_called()

    def test_save_conforma_sync_status(self):
        repo, cursor = self._make_repo()
        repo.save_conforma_sync_status('test-app', ['comp-a', 'comp-b'])
        cursor.execute.assert_called()


# ═══════════════════════════════════════════════════════════════════════
# JiraCommentDraftRepository tests
# ═══════════════════════════════════════════════════════════════════════

class TestJiraCommentDraftRepository:
    def _make_repo(self):
        from repositories.jira_comment_draft_repository import JiraCommentDraftRepository
        db, conn, cursor = _mock_db()
        return JiraCommentDraftRepository(db), cursor

    def test_insert_draft(self):
        repo, cursor = self._make_repo()
        repo.insert_draft('JIRA-123', '10001', 'Draft response body')
        cursor.execute.assert_called()

    def test_get_existing_comment_ids(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = [('10001',), ('10002',)]
        result = repo.get_existing_comment_ids('JIRA-123')
        assert len(result) >= 0

    def test_count_unreviewed(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = (5,)
        result = repo.count_unreviewed()
        assert result == 5


# ═══════════════════════════════════════════════════════════════════════
# ConfigRepository tests
# ═══════════════════════════════════════════════════════════════════════

class TestConfigRepository:
    def _make_repo(self):
        from repositories.config_repository import ConfigRepository
        db, conn, cursor = _mock_db()
        return ConfigRepository(db), cursor

    def test_get_not_found(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = None
        result = repo.get('missing-key')
        assert result is None

    def test_get_with_default(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = None
        result = repo.get('missing-key', default='fallback')
        assert result == 'fallback'

    def test_set(self):
        repo, cursor = self._make_repo()
        repo.set('test-key', 'test-value')
        cursor.execute.assert_called()

    def test_get_found(self):
        repo, cursor = self._make_repo()
        cursor.fetchone.return_value = ('stored-value',)
        result = repo.get('test-key')
        assert result == 'stored-value'

    def test_get_all(self):
        repo, cursor = self._make_repo()
        cursor.fetchall.return_value = [('key1', 'val1'), ('key2', 'val2')]
        result = repo.get_all()
        assert isinstance(result, (list, dict))
