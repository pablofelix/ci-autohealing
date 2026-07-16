"""Tests for triage gaps #16, #17, #18.

Gap #16: Alerts and resolved lists must not contain the same component.
Gap #17: error_type changes between syncs should be tracked.
Gap #18: Batch rebuild endpoint accepts multiple components.
"""

import inspect
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from repositories.build_failure_repository import BuildFailureRepository


def _mock_db():
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


@pytest.fixture
def repo_and_mocks():
    db, conn, cursor = _mock_db()
    repo = BuildFailureRepository(db)
    return repo, db, conn, cursor


# ═══════════════════════════════════════════════════════════════════════
# Gap #16 — Alerts exclude components resolved more recently
# ═══════════════════════════════════════════════════════════════════════

class TestGap16AlertsResolvedDedup:

    def test_failing_query_excludes_recently_resolved(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (10, 2, 5)
        cursor.fetchall.return_value = []

        repo.get_triage_summary('rhoai-v3-5')

        failing_query = cursor.execute.call_args_list[1][0][0]
        assert 'NOT EXISTS' in failing_query, \
            "Failing components query must exclude components with newer resolved records"

    def test_count_query_excludes_stale_unresolved(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (10, 2, 5)
        cursor.fetchall.return_value = []

        repo.get_triage_summary('rhoai-v3-5')

        count_query = cursor.execute.call_args_list[0][0][0]
        assert 'not_stale_resolved' in count_query.lower(), \
            "Count query must use not_stale_resolved CTE to exclude phantom entries"

    def test_count_query_passes_application_for_dedup(self, repo_and_mocks):
        """NOT EXISTS subquery needs the application param to scope correctly."""
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (10, 2, 5)
        cursor.fetchall.return_value = []

        repo.get_triage_summary('rhoai-v3-5')

        count_params = cursor.execute.call_args_list[0][0][1]
        assert count_params.count('rhoai-v3-5') >= 2, \
            "Count query must pass application at least twice (once for CTE, once for dedup)"


# ═══════════════════════════════════════════════════════════════════════
# Gap #17 — error_type change tracking
# ═══════════════════════════════════════════════════════════════════════

class TestGap17ErrorTypeTracking:

    def test_upsert_tracks_previous_error_type(self):
        """The UPDATE branch of upsert_failure must set previous_error_type."""
        source = inspect.getsource(BuildFailureRepository.upsert_failure)
        assert 'previous_error_type' in source, \
            "upsert_failure must track previous_error_type on UPDATE"

    def test_triage_summary_includes_previous_error_type(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (10, 2, 5)
        cursor.fetchall.return_value = [
            ('comp-a', datetime(2024, 6, 1), datetime(2024, 6, 2),
             'Task Failure', None, 3, True, False, True, 'push',
             'pr-abc', '', 'build-images', 'Build Error'),
        ]
        result = repo.get_triage_summary('rhoai-v3-5')
        fc = result['failing_components']
        assert len(fc) == 1
        assert fc[0].get('previous_error_type') == 'Build Error'

    def test_triage_summary_previous_error_type_none_when_unchanged(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (10, 1, 5)
        cursor.fetchall.return_value = [
            ('comp-a', datetime(2024, 6, 1), datetime(2024, 6, 2),
             'Build Error', None, 1, True, False, False, 'push',
             'pr-abc', '', 'build-images', ''),
        ]
        result = repo.get_triage_summary('rhoai-v3-5')
        fc = result['failing_components']
        assert fc[0].get('previous_error_type') is None

    def test_failure_summary_model_has_previous_error_type(self):
        from mcp_server.models import FailureSummary
        fields = FailureSummary.model_fields
        assert 'previous_error_type' in fields, \
            "FailureSummary model must have previous_error_type field"


# ═══════════════════════════════════════════════════════════════════════
# Gap #18 — Batch rebuild
# ═══════════════════════════════════════════════════════════════════════

class TestGap18BatchRebuild:

    def test_batch_rebuild_route_exists(self):
        from api.routes import rebuilds
        assert hasattr(rebuilds, 'batch_rebuild'), \
            "rebuilds module must have batch_rebuild function"

    @patch('api.routes.rebuilds.NAMESPACE', 'rhoai-tenant')
    def test_batch_rebuild_returns_per_component_results(self):
        from api.routes.rebuilds import BatchRebuildRequest, batch_rebuild
        with patch('api.routes.rebuilds._k8s_client') as mock_k8s:
            mock_k8s.return_value.trigger_rebuild.return_value = None
            result = batch_rebuild(
                'rhoai-v3-5',
                BatchRebuildRequest(components=['comp-a', 'comp-b']))
            assert len(result['results']) == 2
            assert result['triggered'] == 2
            assert result['failed'] == 0

    @patch('api.routes.rebuilds.NAMESPACE', 'rhoai-tenant')
    def test_batch_rebuild_partial_failure(self):
        from api.routes.rebuilds import BatchRebuildRequest, batch_rebuild
        with patch('api.routes.rebuilds._k8s_client') as mock_k8s:
            client = mock_k8s.return_value
            client.trigger_rebuild.side_effect = [None, Exception("not found")]
            result = batch_rebuild(
                'rhoai-v3-5',
                BatchRebuildRequest(components=['comp-a', 'comp-b']))
            assert result['results'][0]['status'] == 'triggered'
            assert result['results'][1]['status'] == 'failed'
            assert result['triggered'] == 1
            assert result['failed'] == 1

    @patch('api.routes.rebuilds.NAMESPACE', '')
    def test_batch_rebuild_empty_list_raises(self):
        from fastapi import HTTPException

        from api.routes.rebuilds import BatchRebuildRequest, batch_rebuild
        with pytest.raises(HTTPException) as exc_info:
            batch_rebuild('rhoai-v3-5',
                          BatchRebuildRequest(components=[], namespace='test-ns'))
        assert exc_info.value.status_code == 400

    def test_batch_rebuild_mcp_tool_exists(self):
        from mcp_server.tools import batch_rebuild as mcp_batch_rebuild
        assert callable(mcp_batch_rebuild)
