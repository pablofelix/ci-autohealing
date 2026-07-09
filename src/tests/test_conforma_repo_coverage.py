"""Comprehensive tests for ConformaRepository.

Covers all public methods: happy path, DB exceptions, edge cases.
"""

import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from repositories.conforma_repository import ConformaRepository

# ── Helpers ──────────────────────────────────────────────────────────────

def _mock_db():
    db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    db.connection.return_value.__enter__ = MagicMock(return_value=conn)
    conn.cursor.return_value = cursor

    @contextmanager
    def _connection():
        yield conn

    db.connection = _connection
    return db, conn, cursor


def _broken_db():
    """Return a db mock whose .connection() raises."""
    db = MagicMock()
    db.connection.side_effect = Exception("connection lost")
    return db


def _make_repo():
    db, conn, cursor = _mock_db()
    return ConformaRepository(db), db, conn, cursor


# ═══════════════════════════════════════════════════════════════════════
# find_unresolved_component_names
# ═══════════════════════════════════════════════════════════════════════

class TestFindUnresolvedComponentNames:
    def test_returns_set_with_future(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [('comp-a',), ('comp-b',)]
        result = repo.find_unresolved_component_names('app1', include_future=True)
        assert result == {'comp-a', 'comp-b'}

    def test_returns_set_without_future(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [('comp-c',)]
        result = repo.find_unresolved_component_names('app1', include_future=False)
        assert result == {'comp-c'}
        # Verify the SQL included is_future = FALSE
        sql = cursor.execute.call_args[0][0]
        assert 'is_future = FALSE' in sql

    def test_default_includes_future(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [('comp-d',)]
        result = repo.find_unresolved_component_names('app1')
        assert result == {'comp-d'}
        sql = cursor.execute.call_args[0][0]
        assert 'is_future = FALSE' not in sql

    def test_empty_results(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        assert repo.find_unresolved_component_names('app1') == set()

    def test_db_exception_returns_empty_set(self):
        repo = ConformaRepository(_broken_db())
        assert repo.find_unresolved_component_names('app1') == set()


# ═══════════════════════════════════════════════════════════════════════
# upsert_violation — UPDATE path (record exists)
# ═══════════════════════════════════════════════════════════════════════

class TestUpsertViolationUpdate:
    @patch('repositories.conforma_repository.should_offload', return_value=False)
    def test_update_existing_no_blob(self, _mock_offload):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = (42,)  # exists
        violations = {
            'violation_details': [{'rule': 'r1'}],
            'violations_count': 3,
            'warnings_count': 1,
            'successes_count': 5,
            'violation_summary': 'summary text',
        }
        comp_info = {'repository_url': 'https://git', 'commit_url': 'https://commit'}
        result = repo.upsert_violation('app1', 'comp-a', 'scenario-1',
                                       'pr-1', 'uid-1', violations, comp_info)
        assert result is True

    @patch('repositories.conforma_repository.get_blob_store')
    @patch('repositories.conforma_repository.make_blob_key', return_value='blob/key')
    @patch('repositories.conforma_repository.should_offload', return_value=True)
    def test_update_existing_with_blob(self, _offload, _key, mock_store):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = (42,)  # exists
        violations = {
            'violation_details': [{'rule': 'r1'}],
            'violations_count': 1,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': 'big',
        }
        result = repo.upsert_violation('app1', 'comp-a', 'scn', 'pr-1', 'uid-1',
                                       violations, {})
        assert result is True
        mock_store().put.assert_called_once()
        # Blob refs UPDATE should have been called (3 execute calls total:
        # SELECT id, UPDATE main, UPDATE blob_refs)
        assert cursor.execute.call_count == 3


# ═══════════════════════════════════════════════════════════════════════
# upsert_violation — INSERT path (new record)
# ═══════════════════════════════════════════════════════════════════════

class TestUpsertViolationInsert:
    @patch('repositories.conforma_repository.should_offload', return_value=False)
    def test_insert_new_no_previous_jira(self, _offload):
        repo, _, _, cursor = _make_repo()
        # First fetchone: SELECT id -> None (not exists)
        # Second fetchone: SELECT jira_key -> None (no previous jira)
        cursor.fetchone.side_effect = [None, None]
        violations = {
            'violation_details': None,
            'violations_count': 2,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': 'new violation',
        }
        comp_info = {
            'snapshot_name': 'snap-1',
            'container_image': 'quay.io/img',
            'repository_url': 'https://repo',
            'commit_sha': 'abc123',
            'commit_url': 'https://commit',
        }
        result = repo.upsert_violation('app1', 'comp-b', 'scenario-2',
                                       'pr-2', 'uid-2', violations, comp_info)
        assert result is True

    @patch('repositories.conforma_repository.should_offload', return_value=False)
    def test_insert_inherits_jira_key(self, _offload):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.side_effect = [None, ('JIRA-999',)]
        violations = {
            'violation_details': None,
            'violations_count': 1,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': 'inherited jira',
        }
        result = repo.upsert_violation('app1', 'comp-c', 'scn', 'pr-3', 'uid-3',
                                       violations, {})
        assert result is True
        # The INSERT call should include 'JIRA-999'
        insert_call = cursor.execute.call_args_list[-1]
        insert_params = insert_call[0][1]
        assert 'JIRA-999' in insert_params

    @patch('repositories.conforma_repository.should_offload', return_value=False)
    def test_insert_with_is_future_true(self, _offload):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.side_effect = [None, None]
        violations = {
            'violation_details': None,
            'violations_count': 1,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': 'future',
        }
        result = repo.upsert_violation('app1', 'comp-d', 'scn', 'pr-4', 'uid-4',
                                       violations, {}, is_future=True)
        assert result is True
        insert_params = cursor.execute.call_args_list[-1][0][1]
        assert True in insert_params

    @patch('repositories.conforma_repository.get_blob_store')
    @patch('repositories.conforma_repository.make_blob_key', return_value='blob/key')
    @patch('repositories.conforma_repository.should_offload', return_value=True)
    def test_insert_with_blob_offload(self, _offload, _key, mock_store):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.side_effect = [None, None]
        violations = {
            'violation_details': [{'big': 'data'}],
            'violations_count': 1,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': 'big payload',
        }
        result = repo.upsert_violation('app1', 'comp-e', 'scn', 'pr-5', 'uid-5',
                                       violations, {})
        assert result is True
        mock_store().put.assert_called_once()

    def test_upsert_violation_db_exception(self):
        repo = ConformaRepository(_broken_db())
        violations = {
            'violation_details': None,
            'violations_count': 0,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': '',
        }
        assert repo.upsert_violation('app', 'c', 's', 'p', 'u', violations, {}) is False

    @patch('repositories.conforma_repository.should_offload', return_value=False)
    def test_no_violation_details_yields_none_json(self, _offload):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = (42,)  # exists
        violations = {
            'violation_details': None,
            'violations_count': 0,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': '',
        }
        repo.upsert_violation('app', 'c', 's', 'p', 'u', violations, {})
        update_call = cursor.execute.call_args_list[1]
        params = update_call[0][1]
        # violation_details_json should be None when violation_details is None
        assert params[4] is None


# ═══════════════════════════════════════════════════════════════════════
# get_violation_summaries
# ═══════════════════════════════════════════════════════════════════════

class TestGetViolationSummaries:
    def _sample_row(self):
        return (
            'comp-a', 'scenario-1', 3, 1, 5, 'summary',
            'https://repo', 'abc123',
            '2026-01-01', '2026-01-02', True, 'JIRA-1', False, 'push',
            'conforma-pr-abc',
        )

    def test_current_only_default(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [self._sample_row()]
        result = repo.get_violation_summaries('app1')
        assert len(result) == 1
        assert result[0]['component_name'] == 'comp-a'
        assert result[0]['is_future'] is False
        sql = cursor.execute.call_args[0][0]
        assert 'is_future = FALSE' in sql

    def test_future_only(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        repo.get_violation_summaries('app1', include_future=True)
        sql = cursor.execute.call_args[0][0]
        assert 'is_future = TRUE' in sql

    def test_all_violations(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        repo.get_violation_summaries('app1', include_future='all')
        sql = cursor.execute.call_args[0][0]
        assert 'is_future' not in sql.split('WHERE')[1].split('ORDER')[0]

    def test_empty_results(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        assert repo.get_violation_summaries('app1') == []

    def test_multiple_rows(self):
        repo, _, _, cursor = _make_repo()
        # Two rows with different component names -> both returned
        row_b = list(self._sample_row())
        row_b[0] = 'comp-b'
        cursor.fetchall.return_value = [self._sample_row(), tuple(row_b)]
        result = repo.get_violation_summaries('app1')
        assert len(result) == 2

    def test_deduplicates_wrong_policy_scenario(self):
        # Same component with two scenarios: correct policy preferred over wrong policy
        repo, _, _, cursor = _make_repo()
        correct_row = list(self._sample_row())
        correct_row[1] = 'conforma-fbc-rhoai-prod-v3-5-single-component'
        correct_row[0] = 'rhoai-fbc-fragment-v3-5'
        wrong_row = list(self._sample_row())
        wrong_row[1] = 'conforma-registry-rhoai-prod-v3-5-single-component'
        wrong_row[0] = 'rhoai-fbc-fragment-v3-5'
        cursor.fetchall.return_value = [tuple(correct_row), tuple(wrong_row)]
        result = repo.get_violation_summaries('app1')
        assert len(result) == 1
        assert result[0]['scenario'] == 'conforma-fbc-rhoai-prod-v3-5-single-component'


# ═══════════════════════════════════════════════════════════════════════
# get_violation_details
# ═══════════════════════════════════════════════════════════════════════

class TestGetViolationDetails:
    def _sample_row(self):
        return (
            'comp-a', 'scenario-1', 3, 1, 5,
            'summary', '{"rules": []}',
            'https://repo', 'abc123', 'https://commit',
            'snap-1', 'pr-1',
            '2026-01-01', '2026-01-02',
            True, 'JIRA-1', None,
        )

    @patch('repositories.conforma_repository.resolve_blob_fields')
    def test_found(self, mock_resolve):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [self._sample_row()]
        mock_resolve.side_effect = lambda row, **kw: row
        result = repo.get_violation_details('comp-a', 'app1')
        assert result is not None
        assert result['component_name'] == 'comp-a'
        assert result['pipelinerun_name'] == 'pr-1'
        mock_resolve.assert_called_once()

    @patch('repositories.conforma_repository.resolve_blob_fields')
    def test_prefers_correct_policy(self, mock_resolve):
        # FBC with two scenarios: correct policy returned over wrong policy
        repo, _, _, cursor = _make_repo()
        wrong_row = list(self._sample_row())
        wrong_row[0] = 'rhoai-fbc-fragment-v3-5'
        wrong_row[1] = 'conforma-registry-rhoai-prod-v3-5-single-component'
        correct_row = list(self._sample_row())
        correct_row[0] = 'rhoai-fbc-fragment-v3-5'
        correct_row[1] = 'conforma-fbc-rhoai-prod-v3-5-single-component'
        # Wrong-policy row is more recent (first) — correct policy should still win
        cursor.fetchall.return_value = [tuple(wrong_row), tuple(correct_row)]
        mock_resolve.side_effect = lambda row, **kw: row
        result = repo.get_violation_details('rhoai-fbc-fragment-v3-5', 'app1')
        assert result['scenario'] == 'conforma-fbc-rhoai-prod-v3-5-single-component'

    def test_not_found(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        result = repo.get_violation_details('missing', 'app1')
        assert result is None

    @patch('repositories.conforma_repository.resolve_blob_fields')
    def test_blob_refs_passed_to_resolve(self, mock_resolve):
        repo, _, _, cursor = _make_repo()
        row = list(self._sample_row())
        row[-1] = '{"violation_details": "blob/key"}'  # blob_refs
        cursor.fetchall.return_value = [tuple(row)]
        mock_resolve.side_effect = lambda row, **kw: row
        repo.get_violation_details('comp-a', 'app1')
        call_kwargs = mock_resolve.call_args
        assert call_kwargs[1]['fields'] == ('violation_details',)


# ═══════════════════════════════════════════════════════════════════════
# get_evaluated_images
# ═══════════════════════════════════════════════════════════════════════

class TestGetEvaluatedImages:
    def test_returns_dict(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [
            ('comp-a', 'quay.io/img-a:latest'),
            ('comp-b', 'quay.io/img-b:latest'),
        ]
        result = repo.get_evaluated_images('app1')
        assert result == {
            'comp-a': 'quay.io/img-a:latest',
            'comp-b': 'quay.io/img-b:latest',
        }

    def test_empty(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        assert repo.get_evaluated_images('app1') == {}


# ═══════════════════════════════════════════════════════════════════════
# get_latest_future_timestamp
# ═══════════════════════════════════════════════════════════════════════

class TestGetLatestFutureTimestamp:
    def test_has_timestamp(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = ('2026-06-15 12:00:00',)
        result = repo.get_latest_future_timestamp('app1')
        assert result == '2026-06-15 12:00:00'

    def test_no_results(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = None
        assert repo.get_latest_future_timestamp('app1') is None

    def test_max_returns_none(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = (None,)
        assert repo.get_latest_future_timestamp('app1') is None


# ═══════════════════════════════════════════════════════════════════════
# resolve_fixed_components
# ═══════════════════════════════════════════════════════════════════════

class TestResolveFixedComponents:
    def test_resolves_passing_components(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [
            ('comp-a', 'scn-1'),
            ('comp-b', 'scn-2'),
            ('comp-c', 'scn-3'),
        ]
        cursor.rowcount = 1

        currently_failing = {('comp-a', 'scn-1')}
        all_seen = {('comp-a', 'scn-1'), ('comp-b', 'scn-2'), ('comp-c', 'scn-3')}

        result = repo.resolve_fixed_components('app1', currently_failing, all_seen)
        # comp-b/scn-2 and comp-c/scn-3 should be resolved (2 iterations, rowcount=1 each)
        assert result == 2

    def test_nothing_to_resolve(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [('comp-a', 'scn-1')]
        currently_failing = {('comp-a', 'scn-1')}
        all_seen = {('comp-a', 'scn-1')}
        result = repo.resolve_fixed_components('app1', currently_failing, all_seen)
        assert result == 0

    def test_unseen_pairs_left_alone(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [('comp-a', 'scn-1'), ('comp-old', 'scn-old')]
        cursor.rowcount = 1
        currently_failing = set()
        all_seen = {('comp-a', 'scn-1')}  # comp-old not seen
        result = repo.resolve_fixed_components('app1', currently_failing, all_seen)
        # Only comp-a/scn-1 resolved (seen + not failing), comp-old skipped
        assert result == 1

    def test_empty_db_pairs(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        result = repo.resolve_fixed_components('app1', set(), {('comp-a', 'scn-1')})
        assert result == 0

    def test_db_exception_returns_zero(self):
        repo = ConformaRepository(_broken_db())
        assert repo.resolve_fixed_components('app1', set(), set()) == 0


# ═══════════════════════════════════════════════════════════════════════
# resolve_deleted_component
# ═══════════════════════════════════════════════════════════════════════

class TestResolveDeletedComponent:
    def test_resolves_rows(self):
        repo, _, _, cursor = _make_repo()
        cursor.rowcount = 3
        result = repo.resolve_deleted_component('comp-a', 'app1')
        assert result == 3
        sql = cursor.execute.call_args[0][0]
        assert 'is_resolved = TRUE' in sql

    def test_no_rows_to_resolve(self):
        repo, _, _, cursor = _make_repo()
        cursor.rowcount = 0
        assert repo.resolve_deleted_component('comp-none', 'app1') == 0

    def test_db_exception_returns_zero(self):
        repo = ConformaRepository(_broken_db())
        assert repo.resolve_deleted_component('comp-a', 'app1') == 0


# ═══════════════════════════════════════════════════════════════════════
# Integration-style: upsert with empty violation_details dict
# ═══════════════════════════════════════════════════════════════════════

class TestUpsertEdgeCases:
    @patch('repositories.conforma_repository.should_offload', return_value=False)
    def test_violation_details_empty_dict(self, _offload):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = (1,)
        violations = {
            'violation_details': {},
            'violations_count': 0,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': '',
        }
        result = repo.upsert_violation('app', 'c', 's', 'p', 'u', violations, {})
        assert result is True

    @patch('repositories.conforma_repository.should_offload', return_value=False)
    def test_comp_info_missing_keys(self, _offload):
        """comp_info with missing keys should use .get() defaults."""
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.side_effect = [None, None]
        violations = {
            'violation_details': None,
            'violations_count': 0,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': '',
        }
        result = repo.upsert_violation('app', 'c', 's', 'p', 'u', violations, {})
        assert result is True
