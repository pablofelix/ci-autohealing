"""Comprehensive tests for BuildFailureRepository."""

import os
import sys
from contextlib import contextmanager
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from repositories.build_failure_repository import BuildFailureRepository

# ═══════════════════════════════════════════════════════════════════════
# Shared fixtures
# ═══════════════════════════════════════════════════════════════════════

def _mock_db():
    """Create a mock DB with context-managed connection and cursor."""
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
# pipelinerun_exists
# ═══════════════════════════════════════════════════════════════════════

class TestPipelinerunExists:
    def test_returns_true_when_found(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (1,)
        assert repo.pipelinerun_exists('pr-abc') is True
        cursor.execute.assert_called_once()
        assert 'pr-abc' in cursor.execute.call_args[0][1]

    def test_returns_false_when_not_found(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        assert repo.pipelinerun_exists('pr-missing') is False

    def test_returns_false_for_empty_string(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        assert repo.pipelinerun_exists('') is False


# ═══════════════════════════════════════════════════════════════════════
# is_pipelinerun_complete
# ═══════════════════════════════════════════════════════════════════════

class TestIsPipelinerunComplete:
    def test_complete_when_all_fields_true(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (True, True, True)
        assert repo.is_pipelinerun_complete('pr-1') is True

    def test_incomplete_when_logs_missing(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (False, True, True)
        assert repo.is_pipelinerun_complete('pr-1') is False

    def test_incomplete_when_commit_missing(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (True, None, True)
        assert repo.is_pipelinerun_complete('pr-1') is False

    def test_incomplete_when_url_missing(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (True, True, None)
        assert repo.is_pipelinerun_complete('pr-1') is False

    def test_not_found_returns_false(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        assert repo.is_pipelinerun_complete('pr-nope') is False


# ═══════════════════════════════════════════════════════════════════════
# find_unresolved_component_names
# ═══════════════════════════════════════════════════════════════════════

class TestFindUnresolvedComponentNames:
    def test_returns_set_of_names(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = [('comp-a',), ('comp-b',)]
        result = repo.find_unresolved_component_names('my-app')
        assert result == {'comp-a', 'comp-b'}

    def test_returns_empty_set_when_none(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        result = repo.find_unresolved_component_names('my-app')
        assert result == set()

    def test_returns_empty_set_on_exception(self):
        db = MagicMock()
        db.connection.side_effect = Exception("DB down")
        repo = BuildFailureRepository(db)
        result = repo.find_unresolved_component_names('my-app')
        assert result == set()


# ═══════════════════════════════════════════════════════════════════════
# find_failing_component_names
# ═══════════════════════════════════════════════════════════════════════

class TestFindFailingComponentNames:
    def test_returns_set_of_failing(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = [('comp-x',), ('comp-y',)]
        result = repo.find_failing_component_names('my-app')
        assert result == {'comp-x', 'comp-y'}

    def test_returns_empty_set_when_no_failures(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        result = repo.find_failing_component_names('my-app')
        assert result == set()

    def test_returns_none_on_exception(self):
        db = MagicMock()
        db.connection.side_effect = Exception("DB fail")
        repo = BuildFailureRepository(db)
        result = repo.find_failing_component_names('my-app')
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# count_unresolved
# ═══════════════════════════════════════════════════════════════════════

class TestCountUnresolved:
    def test_returns_count(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (5,)
        assert repo.count_unresolved('comp-a', 'app1') == 5

    def test_returns_zero_when_none(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (0,)
        assert repo.count_unresolved('comp-a', 'app1') == 0

    def test_returns_zero_on_exception(self):
        db = MagicMock()
        db.connection.side_effect = Exception("nope")
        repo = BuildFailureRepository(db)
        assert repo.count_unresolved('comp-a', 'app1') == 0


# ═══════════════════════════════════════════════════════════════════════
# get_last_status
# ═══════════════════════════════════════════════════════════════════════

class TestGetLastStatus:
    def test_returns_status_string(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = ('Failed',)
        assert repo.get_last_status('comp-a', 'app1') == 'Failed'

    def test_returns_none_when_no_rows(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        assert repo.get_last_status('comp-a', 'app1') is None

    def test_returns_none_on_exception(self):
        db = MagicMock()
        db.connection.side_effect = Exception("err")
        repo = BuildFailureRepository(db)
        assert repo.get_last_status('comp-a', 'app1') is None


# ═══════════════════════════════════════════════════════════════════════
# mark_resolved
# ═══════════════════════════════════════════════════════════════════════

class TestMarkResolved:
    @patch('repositories.build_failure_repository.shared_config', create=True)
    def test_returns_true_when_rows_updated(self, mock_sc, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.rowcount = 2
        with patch.dict('sys.modules', {'shared_config': MagicMock(KONFLUX_UI_BASE='https://k.ui')}):
            result = repo.mark_resolved('comp-a', 'app1', 'ns1', 'pr-good', 'sha123')
        assert result is True

    @patch('repositories.build_failure_repository.shared_config', create=True)
    def test_returns_false_when_no_rows(self, mock_sc, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.rowcount = 0
        with patch.dict('sys.modules', {'shared_config': MagicMock(KONFLUX_UI_BASE='https://k.ui')}):
            result = repo.mark_resolved('comp-a', 'app1', 'ns1', 'pr-x')
        assert result is False

    def test_returns_false_on_exception(self):
        db = MagicMock()
        db.connection.side_effect = Exception("boom")
        repo = BuildFailureRepository(db)
        assert repo.mark_resolved('c', 'a', 'n', 'p') is False

    def test_builds_resolution_url(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.rowcount = 1
        with patch.dict('sys.modules', {'shared_config': MagicMock(KONFLUX_UI_BASE='https://k.example')}):
            repo.mark_resolved('comp-a', 'app1', 'myns', 'pr-123', 'abc')
        call_args = cursor.execute.call_args[0][1]
        assert 'https://k.example/ns/myns/pipelinerun/pr-123' == call_args[0]
        assert call_args[1] == 'abc'


# ═══════════════════════════════════════════════════════════════════════
# mark_resolved_deleted
# ═══════════════════════════════════════════════════════════════════════

class TestMarkResolvedDeleted:
    def test_returns_true_when_rows_updated(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.rowcount = 3
        assert repo.mark_resolved_deleted('comp-a', 'app1') is True

    def test_returns_false_when_no_rows(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.rowcount = 0
        assert repo.mark_resolved_deleted('comp-a', 'app1') is False

    def test_returns_false_on_exception(self):
        db = MagicMock()
        db.connection.side_effect = Exception("err")
        repo = BuildFailureRepository(db)
        assert repo.mark_resolved_deleted('c', 'a') is False

    def test_sql_sets_component_deleted_type(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.rowcount = 1
        repo.mark_resolved_deleted('comp-x', 'app2')
        sql = cursor.execute.call_args[0][0]
        assert 'component-deleted' in sql


# ═══════════════════════════════════════════════════════════════════════
# record_successful_build
# ═══════════════════════════════════════════════════════════════════════

class TestRecordSuccessfulBuild:
    def test_inserts_new_record(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None  # not exists
        result = repo.record_successful_build(
            'comp-a', 'pr-1', 'uid-1', 'app1', 'ns1',
            'https://github.com/org/repo.git', 'main'
        )
        assert result is True
        assert cursor.execute.call_count == 2  # SELECT + INSERT

    def test_returns_false_if_already_exists(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (1,)  # already exists
        result = repo.record_successful_build(
            'comp-a', 'pr-1', 'uid-1', 'app1', 'ns1',
            'https://github.com/org/repo.git', 'main'
        )
        assert result is False
        assert cursor.execute.call_count == 1  # only SELECT

    def test_strips_github_prefix_and_git_suffix(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        repo.record_successful_build(
            'comp-a', 'pr-1', 'uid-1', 'app1', 'ns1',
            'https://github.com/myorg/myrepo.git', 'main'
        )
        insert_args = cursor.execute.call_args[0][1]
        assert 'myorg/myrepo' in insert_args

    def test_returns_false_on_exception(self):
        db = MagicMock()
        db.connection.side_effect = Exception("err")
        repo = BuildFailureRepository(db)
        result = repo.record_successful_build('c', 'p', 'u', 'a', 'n', 'https://github.com/x/y', 'b')
        assert result is False


# ═══════════════════════════════════════════════════════════════════════
# upsert_failure
# ═══════════════════════════════════════════════════════════════════════

class TestUpsertFailure:
    """Test insert (new) and update (existing) paths, blob offloading, unresolve clause."""

    @patch('repositories.build_failure_repository.should_offload', return_value=False)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key')
    def test_insert_new_returns_true(self, mock_key, mock_store, mock_offload, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None  # not exists
        with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=lambda x: x)}):
            result = repo.upsert_failure(
                'pr-new', 'uid-1', 'comp-a', 'app1', 'ns1',
                'https://github.com/org/repo.git', 'main', 'Failed',
                logs='some logs', error_message='oops'
            )
        assert result is True
        # SELECT check + INSERT
        assert cursor.execute.call_count == 2

    @patch('repositories.build_failure_repository.should_offload', return_value=False)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key')
    def test_update_existing_returns_false(self, mock_key, mock_store, mock_offload, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (42,)  # exists
        with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=lambda x: x)}):
            result = repo.upsert_failure(
                'pr-existing', 'uid-1', 'comp-a', 'app1', 'ns1',
                'https://github.com/org/repo.git', 'main', 'Failed',
                logs='new logs'
            )
        assert result is False
        # SELECT check + UPDATE (with unresolve clause for Failed)
        assert cursor.execute.call_count == 2

    @patch('repositories.build_failure_repository.should_offload', return_value=False)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key')
    def test_update_with_failed_status_includes_unresolve(self, mock_key, mock_store, mock_offload, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (42,)  # exists
        with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=lambda x: x)}):
            repo.upsert_failure(
                'pr-existing', 'uid-1', 'comp-a', 'app1', 'ns1',
                'https://github.com/org/repo.git', 'main', 'Failed'
            )
        update_sql = cursor.execute.call_args_list[1][0][0]
        assert 'is_resolved = FALSE' in update_sql

    @patch('repositories.build_failure_repository.should_offload', return_value=False)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key')
    def test_update_with_non_failed_status_no_unresolve(self, mock_key, mock_store, mock_offload, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (42,)  # exists
        with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=lambda x: x)}):
            repo.upsert_failure(
                'pr-existing', 'uid-1', 'comp-a', 'app1', 'ns1',
                'https://github.com/org/repo.git', 'main', 'Succeeded'
            )
        update_sql = cursor.execute.call_args_list[1][0][0]
        assert 'is_resolved = FALSE' not in update_sql

    @patch('repositories.build_failure_repository.should_offload', return_value=True)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key', return_value='blob/key/logs')
    def test_blob_offload_on_insert(self, mock_key, mock_store, mock_offload, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None  # new record
        blob_store = MagicMock()
        mock_store.return_value = blob_store
        with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=lambda x: x)}):
            repo.upsert_failure(
                'pr-blob', 'uid-1', 'comp-a', 'app1', 'ns1',
                'https://github.com/org/repo.git', 'main', 'Failed',
                logs='very large logs'
            )
        blob_store.put.assert_called_once_with('blob/key/logs', 'very large logs')
        # logs should be None in the INSERT (offloaded to blob)
        insert_args = cursor.execute.call_args[0][1]
        # logs is at index 22 in the INSERT values
        assert insert_args[22] is None

    @patch('repositories.build_failure_repository.should_offload', return_value=True)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key', return_value='blob/key/logs')
    def test_blob_offload_on_update(self, mock_key, mock_store, mock_offload, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (42,)  # exists
        blob_store = MagicMock()
        mock_store.return_value = blob_store
        with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=lambda x: x)}):
            repo.upsert_failure(
                'pr-blob-upd', 'uid-1', 'comp-a', 'app1', 'ns1',
                'https://github.com/org/repo.git', 'main', 'Failed',
                logs='large update logs'
            )
        blob_store.put.assert_called_once()
        # SELECT + UPDATE (main) + UPDATE (blob_refs)
        assert cursor.execute.call_count == 3

    @patch('repositories.build_failure_repository.should_offload', return_value=False)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key')
    def test_sanitize_called_for_logs_and_error(self, mock_key, mock_store, mock_offload, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        mock_sanitize = MagicMock(side_effect=lambda x: f"sanitized:{x}")
        with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=mock_sanitize)}):
            repo.upsert_failure(
                'pr-san', 'uid-1', 'comp-a', 'app1', 'ns1',
                'https://github.com/org/repo.git', 'main', 'Failed',
                logs='raw logs', error_message='raw error'
            )
        assert mock_sanitize.call_count == 2

    @patch('repositories.build_failure_repository.should_offload', return_value=False)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key')
    def test_no_sanitize_when_logs_none(self, mock_key, mock_store, mock_offload, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        mock_sanitize = MagicMock(side_effect=lambda x: x)
        with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=mock_sanitize)}):
            repo.upsert_failure(
                'pr-nolog', 'uid-1', 'comp-a', 'app1', 'ns1',
                'https://github.com/org/repo.git', 'main', 'Failed',
                logs=None, error_message=None
            )
        mock_sanitize.assert_not_called()

    @patch('repositories.build_failure_repository.should_offload', return_value=False)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key')
    def test_details_dict_fields_forwarded(self, mock_key, mock_store, mock_offload, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        details = {
            'commit_sha': 'abc123',
            'commit_short_sha': 'abc',
            'commit_url': 'https://github.com/org/repo/commit/abc123',
            'commit_message': 'fix bug',
            'commit_author': 'dev',
            'konflux_url': 'https://k.ui/ns/ns1/pipelinerun/pr-det',
            'pipeline_url': 'https://logs.example/pr-det',
            'pr_number': '42',
            'pr_url': 'https://github.com/org/repo/pull/42',
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'output_image': 'quay.io/org/img:tag',
            'image_digest': 'sha256:deadbeef',
            'task_summary': 'build-task failed',
            'chains_git_url': 'https://github.com/org/repo',
            'chains_git_commit': 'abc123',
            'trigger_type': 'push',
        }
        with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=lambda x: x)}):
            repo.upsert_failure(
                'pr-det', 'uid-1', 'comp-a', 'app1', 'ns1',
                'https://github.com/org/repo.git', 'main', 'Failed',
                details=details
            )
        insert_args = cursor.execute.call_args[0][1]
        assert 'abc123' in insert_args
        assert 'quay.io/org/img:tag' in insert_args

    @patch('repositories.build_failure_repository.should_offload', return_value=False)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key')
    def test_empty_details_uses_defaults(self, mock_key, mock_store, mock_offload, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=lambda x: x)}):
            repo.upsert_failure(
                'pr-nodet', 'uid-1', 'comp-a', 'app1', 'ns1',
                'https://github.com/org/repo.git', 'main', 'Failed',
                details=None
            )
        insert_args = cursor.execute.call_args[0][1]
        # trigger_type defaults to 'push' when details is None
        assert insert_args[-1] == 'push'

    @patch('repositories.build_failure_repository.should_offload', return_value=False)
    @patch('repositories.build_failure_repository.get_blob_store')
    @patch('repositories.build_failure_repository.make_blob_key')
    def test_exception_propagates(self, mock_key, mock_store, mock_offload):
        db = MagicMock()
        db.connection.side_effect = Exception("DB error")
        repo = BuildFailureRepository(db)
        with pytest.raises(Exception, match="DB error"):
            with patch.dict('sys.modules', {'security.redact': MagicMock(sanitize_for_storage=lambda x: x)}):
                repo.upsert_failure(
                    'pr-err', 'uid-1', 'comp-a', 'app1', 'ns1',
                    'https://github.com/org/repo.git', 'main', 'Failed'
                )


# ═══════════════════════════════════════════════════════════════════════
# update_component_health
# ═══════════════════════════════════════════════════════════════════════

class TestUpdateComponentHealth:
    def test_calls_db_function(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        repo.update_component_health('comp-a')
        cursor.execute.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert 'update_component_health' in sql
        assert cursor.execute.call_args[0][1] == ('comp-a',)


# ═══════════════════════════════════════════════════════════════════════
# get_component_summary
# ═══════════════════════════════════════════════════════════════════════

class TestGetComponentSummary:
    def test_returns_summary_dict(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (10, 8, datetime(2024, 1, 1), datetime(2024, 6, 1))
        result = repo.get_component_summary('comp-a')
        assert result == {
            'total': 10, 'with_logs': 8,
            'first_seen': datetime(2024, 1, 1), 'last_seen': datetime(2024, 6, 1),
        }

    def test_returns_none_when_no_rows(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        assert repo.get_component_summary('comp-missing') is None

    def test_returns_none_when_count_zero(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (0, 0, None, None)
        assert repo.get_component_summary('comp-empty') is None


# ═══════════════════════════════════════════════════════════════════════
# get_applications
# ═══════════════════════════════════════════════════════════════════════

class TestGetApplications:
    def test_returns_list_of_dicts(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = [('app1', 5), ('app2', 3)]
        result = repo.get_applications()
        assert result == [
            {'application': 'app1', 'count': 5},
            {'application': 'app2', 'count': 3},
        ]

    def test_returns_empty_list(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        assert repo.get_applications() == []


# ═══════════════════════════════════════════════════════════════════════
# get_overview_stats
# ═══════════════════════════════════════════════════════════════════════

class TestGetOverviewStats:
    def test_with_application(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (100, 15, 80)
        result = repo.get_overview_stats(application='app1')
        assert result == {'total': 100, 'components': 15, 'with_logs': 80}
        sql = cursor.execute.call_args[0][0]
        assert 'application = %s' in sql

    def test_without_application(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (200, 30, 150)
        result = repo.get_overview_stats()
        assert result == {'total': 200, 'components': 30, 'with_logs': 150}
        sql = cursor.execute.call_args[0][0]
        assert 'application = %s' not in sql

    def test_with_none_application_uses_global_query(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (0, 0, 0)
        result = repo.get_overview_stats(application=None)
        assert result == {'total': 0, 'components': 0, 'with_logs': 0}


# ═══════════════════════════════════════════════════════════════════════
# get_daily_stats
# ═══════════════════════════════════════════════════════════════════════

class TestGetDailyStats:
    def test_returns_daily_list(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = [
            (date(2024, 6, 1), 5),
            (date(2024, 5, 31), 3),
        ]
        result = repo.get_daily_stats('app1', days=7)
        assert result == [
            {'date': date(2024, 6, 1), 'count': 5},
            {'date': date(2024, 5, 31), 'count': 3},
        ]

    def test_empty_results(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        assert repo.get_daily_stats('app1') == []

    def test_uses_days_parameter(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        repo.get_daily_stats('app1', days=30)
        call_args = cursor.execute.call_args[0][1]
        assert call_args == ('app1', 30)


# ═══════════════════════════════════════════════════════════════════════
# get_resolved_stats
# ═══════════════════════════════════════════════════════════════════════

class TestGetResolvedStats:
    def test_returns_stats(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (50, 40, 10, 35)
        result = repo.get_resolved_stats('app1')
        assert result == {
            'total': 50, 'resolved': 40,
            'unresolved': 10, 'successes': 35,
        }


# ═══════════════════════════════════════════════════════════════════════
# get_triage_summary
# ═══════════════════════════════════════════════════════════════════════

class TestGetTriageSummary:
    def test_returns_full_summary(self, repo_and_mocks):
        from datetime import datetime
        repo, _, _, cursor = repo_and_mocks
        # First query: overview stats
        cursor.fetchone.return_value = (100, 5, 80)
        # Second query: failing components (14 cols: component, first_detected_at,
        # last_updated_at, error_type, jira_key, failure_count, has_logs, has_context,
        # ai_analyzed, trigger_type, pipelinerun_name, konflux_url, failed_step_name,
        # previous_error_type)
        cursor.fetchall.return_value = [
            ('comp-a', datetime(2024, 6, 1), datetime(2024, 6, 2), 'build_error', None, 3, True, False, True, 'push', 'pr-abc', '', 'build-images', ''),
            ('comp-b', datetime(2024, 5, 30), datetime(2024, 5, 31), None, 'RHOAI-1', 1, False, True, False, 'nightly', 'pr-def', 'https://konflux/pr-def', 'fips-check', 'Build Error'),
        ]
        result = repo.get_triage_summary('app1')
        assert result['total'] == 100
        assert result['failing'] == 5
        assert result['working'] == 80
        assert len(result['failing_components']) == 2
        fc = result['failing_components']
        assert fc[0]['component'] == 'comp-a'
        assert fc[0]['error_type'] == 'build_error'
        assert fc[0]['has_logs'] is True
        assert fc[0]['failure_count'] == 3
        assert fc[1]['has_context'] is True
        assert fc[1]['jira_key'] == 'RHOAI-1'
        assert fc[1]['is_nightly'] is True
        assert fc[0]['is_nightly'] is False
        assert fc[0]['pipelinerun_name'] == 'pr-abc'
        assert fc[1]['konflux_url'] == 'https://konflux/pr-def'
        assert fc[0]['failed_step'] == 'build-images'
        assert fc[1]['failed_step'] == 'fips-check'

    def test_no_failing_components(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (50, 0, 50)
        cursor.fetchall.return_value = []
        result = repo.get_triage_summary('app1')
        assert result['failing_components'] == []


# ═══════════════════════════════════════════════════════════════════════
# get_resolved_components
# ═══════════════════════════════════════════════════════════════════════

class TestGetResolvedComponents:
    def test_basic_query(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = [
            ('comp-a', datetime(2024, 6, 1), 2, 'https://pr.url', 'sha1'),
        ]
        result = repo.get_resolved_components('app1')
        assert len(result) == 1
        assert result[0]['component'] == 'comp-a'
        assert result[0]['count'] == 2
        assert result[0]['commit_sha'] == 'sha1'

    def test_with_days_param(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        repo.get_resolved_components('app1', days=14)
        sql = cursor.execute.call_args[0][0]
        assert "INTERVAL '1 day'" in sql
        params = cursor.execute.call_args[0][1]
        assert params == ['app1', 14]

    def test_with_since_param(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        repo.get_resolved_components('app1', since='2024-06-01')
        sql = cursor.execute.call_args[0][0]
        assert 'resolved_at >= %s' in sql
        params = cursor.execute.call_args[0][1]
        assert params == ['app1', '2024-06-01']

    def test_with_to_param(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        repo.get_resolved_components('app1', since='2024-06-01', to='2024-06-30')
        sql = cursor.execute.call_args[0][0]
        assert 'resolved_at >= %s' in sql
        assert 'resolved_at < %s' in sql
        params = cursor.execute.call_args[0][1]
        assert params == ['app1', '2024-06-01', '2024-06-30']

    def test_since_takes_precedence_over_days(self, repo_and_mocks):
        """When both since and days are provided, since wins (elif logic)."""
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        repo.get_resolved_components('app1', days=7, since='2024-01-01')
        sql = cursor.execute.call_args[0][0]
        assert 'resolved_at >= %s' in sql
        assert "INTERVAL '1 day'" not in sql

    def test_empty_results(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        assert repo.get_resolved_components('app1') == []

    def test_to_without_since_or_days(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        repo.get_resolved_components('app1', to='2024-07-01')
        sql = cursor.execute.call_args[0][0]
        assert 'resolved_at < %s' in sql
        params = cursor.execute.call_args[0][1]
        assert params == ['app1', '2024-07-01']


# ═══════════════════════════════════════════════════════════════════════
# get_working_components
# ═══════════════════════════════════════════════════════════════════════

class TestGetWorkingComponents:
    def test_returns_working_components(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = [
            ('comp-a', 'abc12345', 'fix: something important that is long en', date(2024, 6, 1)),
            ('comp-b', 'def67890', 'feat: new thing', date(2024, 5, 31)),
        ]
        result = repo.get_working_components('app1')
        assert len(result) == 2
        assert result[0] == {
            'component': 'comp-a', 'commit': 'abc12345',
            'message': 'fix: something important that is long en', 'date': date(2024, 6, 1),
        }

    def test_empty(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchall.return_value = []
        assert repo.get_working_components('app1') == []


# ═══════════════════════════════════════════════════════════════════════
# find_by_image
# ═══════════════════════════════════════════════════════════════════════

class TestFindByImage:
    def test_returns_matching_records(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.description = [
            ('component_name',), ('application',), ('pipelinerun_name',),
            ('status',), ('output_image',), ('image_digest',),
            ('first_detected_at',), ('is_resolved',),
            ('repository_url',), ('branch',), ('commit_sha',), ('commit_author',),
        ]
        cursor.fetchall.return_value = [
            ('comp-a', 'app1', 'pr-1', 'Failed', 'quay.io/org/img:tag',
             'sha256:abc', datetime(2024, 6, 1), False,
             'https://github.com/org/repo', 'main', 'sha1', 'dev'),
        ]
        result = repo.find_by_image('quay.io/org/img')
        assert len(result) == 1
        assert result[0]['component_name'] == 'comp-a'
        assert result[0]['output_image'] == 'quay.io/org/img:tag'

    def test_short_ref_returns_empty(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        assert repo.find_by_image('abc') == []
        cursor.execute.assert_not_called()

    def test_strips_whitespace(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.description = [('x',)]
        cursor.fetchall.return_value = []
        repo.find_by_image('  sha256:abc123  ')
        call_args = cursor.execute.call_args[0][1]
        assert call_args[0] == '%sha256:abc123%'

    def test_empty_string_returns_empty(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        assert repo.find_by_image('') == []

    def test_exactly_6_chars_allowed(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.description = [('x',)]
        cursor.fetchall.return_value = []
        result = repo.find_by_image('abcdef')
        assert result == []
        cursor.execute.assert_called_once()

    def test_5_chars_too_short(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        result = repo.find_by_image('abcde')
        assert result == []
        cursor.execute.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# get_component_history
# ═══════════════════════════════════════════════════════════════════════

class TestGetComponentHistory:
    def test_full_history(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        # Summary query
        cursor.fetchone.side_effect = [
            (10, 6, 4, 3),   # summary counts
            ('Failed',),      # last_status
        ]
        # Builds query
        cursor.fetchall.return_value = [
            ('pr-1', 'Failed', False, 'abc1', datetime(2024, 6, 1)),
            ('pr-2', 'Succeeded', True, 'abc2', datetime(2024, 5, 30)),
        ]
        result = repo.get_component_history('comp-a', 'app1')
        assert result['summary'] == {
            'total': 10, 'failures': 6, 'successes': 4, 'resolved': 3,
        }
        assert len(result['builds']) == 2
        assert result['builds'][0]['pipelinerun'] == 'pr-1'
        assert result['last_status'] == 'Failed'

    def test_uses_limit_param(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.side_effect = [(0, 0, 0, 0), (None,)]
        cursor.fetchall.return_value = []
        repo.get_component_history('comp-a', 'app1', limit=5)
        # The second execute call (builds query) should have limit=5
        build_query_args = cursor.execute.call_args_list[1][0][1]
        assert build_query_args[2] == 5

    def test_no_last_status(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.side_effect = [(0, 0, 0, 0), None]
        cursor.fetchall.return_value = []
        result = repo.get_component_history('comp-a', 'app1')
        assert result['last_status'] is None


# ═══════════════════════════════════════════════════════════════════════
# get_enrichment_coverage
# ═══════════════════════════════════════════════════════════════════════

class TestGetEnrichmentCoverage:
    def test_returns_coverage_stats(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (20, 15, 2)
        result = repo.get_enrichment_coverage('app1')
        assert result == {
            'total': 20, 'enriched': 15, 'failed': 2,
            'pct': 75,
        }

    def test_zero_total_no_division_error(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (0, 0, 0)
        result = repo.get_enrichment_coverage('app1')
        assert result['pct'] == 0

    def test_pct_integer_division(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (3, 1, 0)
        result = repo.get_enrichment_coverage('app1')
        assert result['pct'] == 33  # 1 * 100 // 3 = 33


# ═══════════════════════════════════════════════════════════════════════
# get_failure_details
# ═══════════════════════════════════════════════════════════════════════

class TestGetFailureDetails:
    @patch('repositories.build_failure_repository.resolve_blob_fields')
    def test_returns_resolved_dict(self, mock_resolve, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        row_data = (
            1, 'comp-a', 'pr-1', 'error msg', 'BuildError',
            'task-1', 'step-1', 'logs here',
            'sha123', 'fix bug', 'dev', 'https://commit.url',
            'https://repo.url', 'main', '{"files": []}',
            'https://k.url', datetime(2024, 6, 1), datetime(2024, 6, 1),
            'Failed', False, 'JIRA-123', None,
        )
        cursor.fetchone.return_value = row_data
        expected_dict = {
            'id': 1, 'component_name': 'comp-a', 'pipelinerun_name': 'pr-1',
            'error_message': 'error msg', 'error_type': 'BuildError',
            'failed_task_name': 'task-1', 'failed_step_name': 'step-1',
            'build_logs': 'logs here', 'commit_sha': 'sha123',
            'commit_message': 'fix bug', 'commit_author': 'dev',
            'commit_url': 'https://commit.url', 'repository_url': 'https://repo.url',
            'branch': 'main', 'commit_context': '{"files": []}',
            'konflux_url': 'https://k.url',
            'first_detected_at': datetime(2024, 6, 1),
            'last_updated_at': datetime(2024, 6, 1),
            'status': 'Failed', 'ai_analyzed': False,
            'jira_key': 'JIRA-123', 'blob_refs': None,
        }
        mock_resolve.return_value = expected_dict
        result = repo.get_failure_details('comp-a', 'app1')
        mock_resolve.assert_called_once()
        assert result == expected_dict

    @patch('repositories.build_failure_repository.resolve_blob_fields')
    def test_returns_none_when_not_found(self, mock_resolve, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = None
        result = repo.get_failure_details('comp-missing', 'app1')
        assert result is None
        mock_resolve.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# get_analysis_queue
# ═══════════════════════════════════════════════════════════════════════

class TestGetAnalysisQueue:
    def test_returns_queue_stats(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (12, 45)
        result = repo.get_analysis_queue('app1')
        assert result == {'pending': 12, 'analyzed': 45}

    def test_zero_counts(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.fetchone.return_value = (0, 0)
        result = repo.get_analysis_queue('app1')
        assert result == {'pending': 0, 'analyzed': 0}


# ═══════════════════════════════════════════════════════════════════════
# get_nightly_history
# ═══════════════════════════════════════════════════════════════════════

class TestGetNightlyHistory:
    def test_returns_nightly_and_fbc(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        # First query: nightly builds
        cursor.description = [
            ('build_date',), ('pipelinerun_name',), ('component_name',),
            ('status',), ('build_completion_time',), ('commit_sha',),
        ]
        nightly_rows = [
            (date(2024, 6, 1), 'pr-nightly-1', 'comp-a', 'Failed',
             datetime(2024, 6, 1, 3, 0), 'sha1'),
        ]
        # Second query: FBC fragments
        fbc_rows = [
            ('comp-fbc-fragment-a', datetime(2024, 5, 30), 'healthy'),
        ]
        cursor.fetchall.side_effect = [nightly_rows, fbc_rows]

        result = repo.get_nightly_history('app1', days=7)
        assert result['application'] == 'app1'
        assert result['days'] == 7
        assert len(result['nightly_builds']) == 1
        assert result['nightly_builds'][0]['component_name'] == 'comp-a'
        assert len(result['fbc_fragments']) == 1

    def test_default_days(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.description = [('x',)]
        cursor.fetchall.side_effect = [[], []]
        result = repo.get_nightly_history('app1')
        assert result['days'] == 14
        # Check the days param was passed as string
        first_call_args = cursor.execute.call_args_list[0][0][1]
        assert first_call_args[1] == '14'

    def test_empty_results(self, repo_and_mocks):
        repo, _, _, cursor = repo_and_mocks
        cursor.description = [('x',)]
        cursor.fetchall.side_effect = [[], []]
        result = repo.get_nightly_history('app1', days=7)
        assert result['nightly_builds'] == []
        assert result['fbc_fragments'] == []


# ═══════════════════════════════════════════════════════════════════════
# Edge case: constructor
# ═══════════════════════════════════════════════════════════════════════

class TestConstructor:
    def test_stores_db_reference(self):
        db = MagicMock()
        repo = BuildFailureRepository(db)
        assert repo.db is db
