"""Comprehensive tests for remaining repositories:
- context_enrichment_repository.py
- resolution_attempt_repository.py
- conforma_rule_catalog_repository.py
"""

import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
from datetime import datetime
from unittest.mock import MagicMock, patch


def _ensure_real_module(name):
    parts = name.split('.')
    for i in range(len(parts)):
        partial = '.'.join(parts[:i + 1])
        mod = sys.modules.get(partial)
        if mod is not None and isinstance(mod, MagicMock):
            sys.modules.pop(partial, None)
    mod = sys.modules.get(name)
    if mod is None or isinstance(mod, MagicMock):
        sys.modules.pop(name, None)
        importlib.import_module(name)


_ensure_real_module('repositories.context_enrichment_repository')
_ensure_real_module('repositories.resolution_attempt_repository')
_ensure_real_module('repositories.conforma_rule_catalog_repository')

from repositories.conforma_rule_catalog_repository import ConformaRuleCatalogRepository
from repositories.context_enrichment_repository import ContextEnrichmentRepository
from repositories.resolution_attempt_repository import ResolutionAttemptRepository

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


# ===================================================================
# ContextEnrichmentRepository
# ===================================================================

class TestContextEnrichmentGetPending:
    """Tests for get_pending_enrichments."""

    @patch('repositories.context_enrichment_repository.resolve_blob_fields')
    def test_returns_list_of_dicts(self, mock_resolve):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            (1, 'comp-a', 'pr-1', 'sha123', 'https://github.com/org/repo',
             'build_error', 'some error', '{"diff": "x"}', 'rhoai-v3-5', None),
        ]
        repo = ContextEnrichmentRepository(db)
        result = repo.get_pending_enrichments('rhoai-v3-5')
        assert len(result) == 1
        assert result[0]['id'] == 1
        assert result[0]['component_name'] == 'comp-a'
        mock_resolve.assert_called_once()

    @patch('repositories.context_enrichment_repository.resolve_blob_fields')
    def test_with_component_filter(self, mock_resolve):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = ContextEnrichmentRepository(db)
        result = repo.get_pending_enrichments('rhoai-v3-5', component_filter='comp-a')
        assert result == []
        # Check that the SQL has the component_name filter
        sql = cursor.execute.call_args[0][0]
        assert 'component_name = %s' in sql
        params = cursor.execute.call_args[0][1]
        assert 'comp-a' in params

    @patch('repositories.context_enrichment_repository.resolve_blob_fields')
    def test_no_component_filter(self, mock_resolve):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = ContextEnrichmentRepository(db)
        result = repo.get_pending_enrichments('rhoai-v3-5', limit=5)
        assert result == []
        sql = cursor.execute.call_args[0][0]
        assert 'component_name = %s' not in sql

    @patch('repositories.context_enrichment_repository.resolve_blob_fields')
    def test_empty_results(self, mock_resolve):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = ContextEnrichmentRepository(db)
        result = repo.get_pending_enrichments('rhoai-v3-5')
        assert result == []
        mock_resolve.assert_not_called()


class TestContextEnrichmentUpdate:
    """Tests for update_enriched_context."""

    def test_stores_json_and_commits(self):
        db, conn, cursor = _make_db()
        repo = ContextEnrichmentRepository(db)
        context = {'tekton_files': ['a.yaml'], 'dockerfile': 'FROM ubi8'}
        repo.update_enriched_context(42, context)

        cursor.execute.assert_called_once()
        args = cursor.execute.call_args[0]
        assert 'UPDATE build_failures' in args[0]
        assert json.dumps(context) == args[1][0]
        assert args[1][1] == 42
        conn.commit.assert_called_once()


class TestContextEnrichmentIncrementAttempts:
    """Tests for increment_enrichment_attempts."""

    def test_returns_new_count(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (3,)
        repo = ContextEnrichmentRepository(db)
        result = repo.increment_enrichment_attempts(42)
        assert result == 3
        conn.commit.assert_called_once()

    def test_returns_1_when_no_row(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        repo = ContextEnrichmentRepository(db)
        result = repo.increment_enrichment_attempts(42)
        assert result == 1


class TestContextEnrichmentMarkFailed:
    """Tests for mark_enrichment_failed."""

    def test_stores_error_message(self):
        db, conn, cursor = _make_db()
        repo = ContextEnrichmentRepository(db)
        repo.mark_enrichment_failed(42, 'Rate limit exceeded')

        cursor.execute.assert_called_once()
        args = cursor.execute.call_args[0]
        assert 'enrichment_error' in args[0]
        assert args[1] == ('Rate limit exceeded', 42)
        conn.commit.assert_called_once()


class TestContextEnrichmentCoverage:
    """Tests for get_enrichment_coverage."""

    def test_returns_stats_with_data(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (10, 7, 2, 1)
        repo = ContextEnrichmentRepository(db)
        result = repo.get_enrichment_coverage('rhoai-v3-5')

        assert result['total_with_commit_sha'] == 10
        assert result['enriched'] == 7
        assert result['pending'] == 2
        assert result['failed'] == 1
        assert result['coverage_pct'] == 70.0

    def test_zero_total_returns_zero_pct(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (0, 0, 0, 0)
        repo = ContextEnrichmentRepository(db)
        result = repo.get_enrichment_coverage('rhoai-v3-5')

        assert result['total_with_commit_sha'] == 0
        assert result['coverage_pct'] == 0.0


# ===================================================================
# ResolutionAttemptRepository
# ===================================================================

class TestRecordPrCreated:
    """Tests for record_pr_created."""

    def test_inserts_and_returns_id(self):
        db, conn, cursor = _make_db()
        # First fetchone: attempt_number query
        # Second fetchone: INSERT RETURNING id
        cursor.fetchone.side_effect = [(0,), (42,)]
        repo = ResolutionAttemptRepository(db)

        result = repo.record_pr_created(
            build_failure_id=10,
            pr_url='https://github.com/org/repo/pull/5',
            pr_number=5,
            pr_branch='ci-autohealing/fix',
            files_modified=['a.go', 'b.go'],
            changes_description='Fixed dep',
        )
        # attempt_number is MAX + 1, so (0) + 1 = 1
        assert result == 42
        assert cursor.execute.call_count == 3  # SELECT MAX, INSERT, UPDATE
        conn.commit.assert_called_once()

    def test_custom_attempted_by(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.side_effect = [(2,), (99,)]
        repo = ResolutionAttemptRepository(db)
        result = repo.record_pr_created(
            build_failure_id=10,
            pr_url='url', pr_number=1, pr_branch='br',
            files_modified=[], changes_description='desc',
            attempted_by='autonomous',
        )
        assert result == 99
        # Check that the INSERT includes 'autonomous'
        insert_call = cursor.execute.call_args_list[1]
        assert 'autonomous' in insert_call[0][1]


class TestRecordConformaPrCreated:
    """Tests for record_conforma_pr_created."""

    def test_inserts_and_returns_id(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.side_effect = [(0,), (55,)]
        repo = ResolutionAttemptRepository(db)

        result = repo.record_conforma_pr_created(
            conforma_result_id=20,
            pr_url='https://github.com/org/repo/pull/6',
            pr_number=6,
            pr_branch='ci-autohealing/fix-conf',
            files_modified=['Dockerfile'],
            changes_description='Fixed hermetic',
        )
        assert result == 55
        conn.commit.assert_called_once()
        # Should update conforma_results, not build_failures
        update_call = cursor.execute.call_args_list[2]
        assert 'conforma_results' in update_call[0][0]


class TestGetPendingVerification:
    """Tests for get_pending_verification."""

    def test_returns_list_of_dicts(self):
        db, conn, cursor = _make_db()
        cursor.description = [
            ('id',), ('build_failure_id',), ('conforma_result_id',),
            ('pr_url',), ('pr_number',), ('pr_branch',),
            ('attempted_at',), ('component_name',), ('application',),
        ]
        cursor.fetchall.return_value = [
            (1, 10, None, 'url', 5, 'branch', datetime(2026, 6, 1), 'comp', 'app'),
        ]
        repo = ResolutionAttemptRepository(db)
        result = repo.get_pending_verification()
        assert len(result) == 1
        assert result[0]['id'] == 1
        assert result[0]['component_name'] == 'comp'

    def test_empty_when_none_pending(self):
        db, conn, cursor = _make_db()
        cursor.description = [
            ('id',), ('build_failure_id',), ('conforma_result_id',),
            ('pr_url',), ('pr_number',), ('pr_branch',),
            ('attempted_at',), ('component_name',), ('application',),
        ]
        cursor.fetchall.return_value = []
        repo = ResolutionAttemptRepository(db)
        result = repo.get_pending_verification()
        assert result == []


class TestUpdateVerification:
    """Tests for update_verification."""

    def test_success_updates_parent_tables(self):
        db, conn, cursor = _make_db()
        repo = ResolutionAttemptRepository(db)
        repo.update_verification(
            attempt_id=1, pr_merged=True,
            pr_merged_at=datetime(2026, 6, 15),
            result_pipelinerun_name='pr-123',
            result_build_status='Succeeded',
            was_successful=True,
            verification_notes='All good',
        )
        # UPDATE resolution_attempts + UPDATE build_failures + UPDATE conforma_results
        assert cursor.execute.call_count >= 3
        conn.commit.assert_called_once()

    def test_abandoned_status(self):
        db, conn, cursor = _make_db()
        repo = ResolutionAttemptRepository(db)
        repo.update_verification(
            attempt_id=1, pr_merged=False,
            pr_merged_at=None,
            result_pipelinerun_name=None,
            result_build_status=None,
            was_successful=False,
            verification_notes='Closed without merge',
        )
        # Check new_status logic: pr_merged=False => 'abandoned'
        update_call = cursor.execute.call_args_list[0]
        params = update_call[0][1]
        assert 'abandoned' in params

    def test_failed_status(self):
        db, conn, cursor = _make_db()
        repo = ResolutionAttemptRepository(db)
        repo.update_verification(
            attempt_id=1, pr_merged=True,
            pr_merged_at=datetime(2026, 6, 15),
            result_pipelinerun_name='pr-123',
            result_build_status='Failed',
            was_successful=False,
            verification_notes='Build failed after merge',
        )
        update_call = cursor.execute.call_args_list[0]
        params = update_call[0][1]
        assert 'failed' in params

    def test_pattern_learning_on_success(self):
        db, conn, cursor = _make_db()
        pattern_repo = MagicMock()
        # After conn.commit(), the next execute is the pattern learning query
        cursor.fetchone.return_value = (10, None)
        pattern_repo.get_pattern_for_failure.return_value = 99
        repo = ResolutionAttemptRepository(db, pattern_repo=pattern_repo)
        repo.update_verification(
            attempt_id=1, pr_merged=True,
            pr_merged_at=datetime(2026, 6, 15),
            result_pipelinerun_name='pr-123',
            result_build_status='Succeeded',
            was_successful=True,
            verification_notes='All good',
        )
        pattern_repo.get_pattern_for_failure.assert_called_once()
        pattern_repo.record_fix_outcome.assert_called_once_with(99, True)

    def test_pattern_learning_skipped_when_no_pattern_repo(self):
        db, conn, cursor = _make_db()
        repo = ResolutionAttemptRepository(db, pattern_repo=None)
        # Should not raise
        repo.update_verification(
            attempt_id=1, pr_merged=True,
            pr_merged_at=datetime(2026, 6, 15),
            result_pipelinerun_name='pr-123',
            result_build_status='Succeeded',
            was_successful=True,
            verification_notes='All good',
        )

    def test_pattern_learning_no_pattern_found(self):
        db, conn, cursor = _make_db()
        pattern_repo = MagicMock()
        cursor.fetchone.return_value = (10, None)
        pattern_repo.get_pattern_for_failure.return_value = None
        repo = ResolutionAttemptRepository(db, pattern_repo=pattern_repo)
        repo.update_verification(
            attempt_id=1, pr_merged=True,
            pr_merged_at=datetime(2026, 6, 15),
            result_pipelinerun_name='pr-123',
            result_build_status='Succeeded',
            was_successful=True,
            verification_notes='All good',
        )
        pattern_repo.record_fix_outcome.assert_not_called()


class TestGetAll:
    """Tests for get_all."""

    def test_returns_list_of_dicts(self):
        db, conn, cursor = _make_db()
        cursor.description = [
            ('id',), ('component_name',), ('failure_type',),
            ('status',), ('pr_url',), ('pr_number',),
            ('was_successful',), ('attempted_at',),
            ('verified_at',), ('verification_notes',),
        ]
        cursor.fetchall.return_value = [
            (1, 'comp', 'build', 'success', 'url', 5, True,
             datetime(2026, 6, 1), datetime(2026, 6, 2), 'ok'),
        ]
        repo = ResolutionAttemptRepository(db)
        result = repo.get_all(days=7)
        assert len(result) == 1
        assert result[0]['component_name'] == 'comp'

    def test_empty_results(self):
        db, conn, cursor = _make_db()
        cursor.description = [
            ('id',), ('component_name',), ('failure_type',),
            ('status',), ('pr_url',), ('pr_number',),
            ('was_successful',), ('attempted_at',),
            ('verified_at',), ('verification_notes',),
        ]
        cursor.fetchall.return_value = []
        repo = ResolutionAttemptRepository(db)
        result = repo.get_all()
        assert result == []


class TestGetOutcomeSummary:
    """Tests for get_outcome_summary."""

    def test_returns_correct_stats(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (10, 6, 2, 2)
        repo = ResolutionAttemptRepository(db)
        result = repo.get_outcome_summary(days=30)
        assert result['total'] == 10
        assert result['successful'] == 6
        assert result['failed'] == 2
        assert result['pending'] == 2
        assert result['rate'] == 75  # 6 / (6+2) * 100

    def test_zero_resolved_no_division_error(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (0, 0, 0, 0)
        repo = ResolutionAttemptRepository(db)
        result = repo.get_outcome_summary()
        assert result['rate'] == 0


# ===================================================================
# ConformaRuleCatalogRepository
# ===================================================================

class TestGetByRuleIds:
    """Tests for get_by_rule_ids."""

    def test_returns_list_of_dicts(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            ('hermetic_task__hermetic', 'hermetic_task', 'hermetic',
             'Must be hermetic', 'release', ['slsa3'], 'Enable hermetic',
             'Set hermetic=true', 'https://docs.example.com'),
        ]
        repo = ConformaRuleCatalogRepository(db)
        result = repo.get_by_rule_ids(['hermetic_task.hermetic'])
        assert len(result) == 1
        assert result[0]['rule_id'] == 'hermetic_task__hermetic'
        assert result[0]['rule_package'] == 'hermetic_task'
        # Verify normalization: dot replaced with __
        params = cursor.execute.call_args[0][1]
        assert params[0] == ['hermetic_task__hermetic']

    def test_empty_rule_ids_returns_empty(self):
        db, conn, cursor = _make_db()
        repo = ConformaRuleCatalogRepository(db)
        result = repo.get_by_rule_ids([])
        assert result == []
        cursor.execute.assert_not_called()

    def test_mixed_formats_normalized(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = ConformaRuleCatalogRepository(db)
        repo.get_by_rule_ids(['cve.cve_results_found', 'hermetic_task__hermetic'])
        params = cursor.execute.call_args[0][1]
        assert 'cve__cve_results_found' in params[0]
        assert 'hermetic_task__hermetic' in params[0]


class TestGetByPackage:
    """Tests for get_by_package."""

    def test_returns_rules_for_package(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            ('cve__cve_results_found', 'cve', 'cve_results_found',
             'CVE results', 'release', None, None, None, None),
        ]
        repo = ConformaRuleCatalogRepository(db)
        result = repo.get_by_package('cve')
        assert len(result) == 1
        assert result[0]['rule_package'] == 'cve'

    def test_empty_results(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = ConformaRuleCatalogRepository(db)
        result = repo.get_by_package('nonexistent')
        assert result == []


class TestSearch:
    """Tests for search."""

    def test_search_finds_matches(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            ('hermetic_task__hermetic', 'hermetic_task', 'hermetic',
             'Must be hermetic', 'release', None, 'Enable hermetic', None, None),
        ]
        repo = ConformaRuleCatalogRepository(db)
        result = repo.search('hermetic')
        assert len(result) == 1
        # Check ILIKE pattern
        params = cursor.execute.call_args[0][1]
        assert params[0] == '%hermetic%'
        assert params[1] == '%hermetic%'

    def test_search_empty(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = ConformaRuleCatalogRepository(db)
        result = repo.search('nonexistent')
        assert result == []


class TestGetAll:
    """Tests for get_all."""

    def test_returns_all_rules(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            ('r1', 'p1', 'n1', 'd1', 't1', None, None, None, None),
            ('r2', 'p2', 'n2', 'd2', 't2', None, None, None, None),
        ]
        repo = ConformaRuleCatalogRepository(db)
        result = repo.get_all()
        assert len(result) == 2


class TestUpsert:
    """Tests for upsert."""

    def test_upsert_calls_execute(self):
        db, conn, cursor = _make_db()
        repo = ConformaRuleCatalogRepository(db)
        repo.upsert(
            rule_id='test__rule',
            rule_package='test',
            rule_name='rule',
            description='A test rule',
            policy_type='release',
            collections=['slsa3'],
            typical_fix='Fix it',
            reporter_solution='Solution',
            doc_url='https://docs.example.com',
        )
        cursor.execute.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert 'INSERT INTO conforma_rule_catalog' in sql
        assert 'ON CONFLICT (rule_id) DO UPDATE' in sql

    def test_upsert_with_minimal_fields(self):
        db, conn, cursor = _make_db()
        repo = ConformaRuleCatalogRepository(db)
        repo.upsert(
            rule_id='test__rule',
            rule_package='test',
            rule_name='rule',
        )
        cursor.execute.assert_called_once()
        params = cursor.execute.call_args[0][1]
        # description, policy_type, etc. should be None
        assert params[3] is None  # description


class TestUpdateReporterSolution:
    """Tests for update_reporter_solution."""

    def test_updates_and_normalizes(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 1
        repo = ConformaRuleCatalogRepository(db)
        result = repo.update_reporter_solution('cve.cve_results', 'New solution')
        assert result is True
        params = cursor.execute.call_args[0][1]
        assert params[0] == 'New solution'
        assert params[1] == 'cve__cve_results'  # normalized

    def test_returns_false_when_no_rows_affected(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 0
        repo = ConformaRuleCatalogRepository(db)
        result = repo.update_reporter_solution('nonexistent__rule', 'solution')
        assert result is False


class TestGetCatalogStats:
    """Tests for get_catalog_stats."""

    def test_returns_stats_dict(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (50, 30, 40, 10, 3)
        repo = ConformaRuleCatalogRepository(db)
        result = repo.get_catalog_stats()
        assert result['total'] == 50
        assert result['with_reporter_solution'] == 30
        assert result['with_typical_fix'] == 40
        assert result['packages'] == 10
        assert result['policy_types'] == 3


class TestRowToDict:
    """Tests for _row_to_dict static method."""

    def test_converts_tuple_to_dict(self):
        row = ('r1', 'pkg', 'name', 'desc', 'release', ['c1'], 'fix', 'sol', 'url')
        result = ConformaRuleCatalogRepository._row_to_dict(row)
        assert result == {
            'rule_id': 'r1',
            'rule_package': 'pkg',
            'rule_name': 'name',
            'description': 'desc',
            'policy_type': 'release',
            'collections': ['c1'],
            'typical_fix': 'fix',
            'reporter_solution': 'sol',
            'doc_url': 'url',
        }
