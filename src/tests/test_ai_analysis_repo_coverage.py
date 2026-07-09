"""Comprehensive tests for AIAnalysisRepository."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from repositories.ai_analysis_repository import AIAnalysisRepository

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


# ===================================================================
# insert_analysis
# ===================================================================

class TestInsertAnalysis:
    """Tests for insert_analysis covering build, conforma, validation, and legacy paths."""

    BASE_KWARGS = dict(
        model_used='claude-sonnet-4-6',
        root_cause='Missing dependency',
        failure_category='dependency_issue',
        confidence_score=0.85,
        recommended_fix='Add the dep',
        recommended_files=['go.mod'],
        can_auto_fix=True,
        requires_human_review=False,
        tokens_used=1200,
        cost_usd=0.01,
        analysis_duration=3.5,
        analysis_json={'steps': [1, 2]},
    )

    def test_build_failure_path(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (42,)
        repo = AIAnalysisRepository(db)
        result = repo.insert_analysis(
            **self.BASE_KWARGS,
            build_failure_id=10,
        )
        assert result == 42
        conn.commit.assert_called_once()
        # Should UPDATE build_failures
        update_call = cursor.execute.call_args_list[1]
        assert 'UPDATE build_failures' in update_call[0][0]

    def test_conforma_path(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (99,)
        repo = AIAnalysisRepository(db)
        result = repo.insert_analysis(
            **self.BASE_KWARGS,
            conforma_result_id=20,
        )
        assert result == 99
        update_call = cursor.execute.call_args_list[1]
        assert 'UPDATE conforma_results' in update_call[0][0]

    def test_neither_id_raises(self):
        db, _, _ = _make_db()
        repo = AIAnalysisRepository(db)
        with pytest.raises(ValueError, match='Exactly one'):
            repo.insert_analysis(**self.BASE_KWARGS)

    def test_both_ids_raises(self):
        db, _, _ = _make_db()
        repo = AIAnalysisRepository(db)
        with pytest.raises(ValueError, match='Exactly one'):
            repo.insert_analysis(
                **self.BASE_KWARGS,
                build_failure_id=1,
                conforma_result_id=2,
            )

    def test_backward_compat_int_first_arg(self):
        """Legacy callers pass build_failure_id as first positional arg."""
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (77,)
        repo = AIAnalysisRepository(db)
        result = repo.insert_analysis(
            10,                            # build_failure_id (old style)
            'claude-sonnet-4-6',           # model_used
            'root cause',                  # root_cause
            'dependency_issue',            # failure_category
            0.9,                           # confidence_score
            'fix it',                      # recommended_fix
            ['file.py'],                   # recommended_files
            False,                         # can_auto_fix
            True,                          # requires_human_review
            500,                           # tokens_used
            0.005,                         # cost_usd
            2.0,                           # analysis_duration
            {'key': 'val'},                # analysis_json
        )
        assert result == 77
        # build_failure_id should be set
        insert_params = cursor.execute.call_args_list[0][0][1]
        assert insert_params[0] == 10  # build_failure_id
        assert insert_params[1] is None  # conforma_result_id

    def test_analysis_json_serialized(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (1,)
        repo = AIAnalysisRepository(db)
        repo.insert_analysis(**self.BASE_KWARGS, build_failure_id=5)
        insert_params = cursor.execute.call_args_list[0][0][1]
        # analysis_json should be JSON-serialized
        assert insert_params[16] == json.dumps({'steps': [1, 2]})

    def test_analysis_json_none(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (1,)
        repo = AIAnalysisRepository(db)
        kwargs = dict(self.BASE_KWARGS)
        kwargs['analysis_json'] = None
        repo.insert_analysis(**kwargs, build_failure_id=5)
        insert_params = cursor.execute.call_args_list[0][0][1]
        assert insert_params[16] is None

    def test_langfuse_fields(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (1,)
        repo = AIAnalysisRepository(db)
        repo.insert_analysis(
            **self.BASE_KWARGS,
            build_failure_id=5,
            langfuse_trace_id='trace-1',
            langfuse_trace_url='http://lf/trace-1',
            langfuse_observation_id='obs-1',
        )
        insert_params = cursor.execute.call_args_list[0][0][1]
        assert insert_params[10] == 'trace-1'
        assert insert_params[11] == 'http://lf/trace-1'
        assert insert_params[12] == 'obs-1'

    def test_error_pattern_id(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (1,)
        repo = AIAnalysisRepository(db)
        repo.insert_analysis(
            **self.BASE_KWARGS,
            build_failure_id=5,
            error_pattern_id=33,
        )
        insert_params = cursor.execute.call_args_list[0][0][1]
        assert insert_params[17] == 33

    def test_db_exception_propagates(self):
        db, conn, cursor = _make_db()
        cursor.execute.side_effect = Exception('constraint violation')
        repo = AIAnalysisRepository(db)
        with pytest.raises(Exception, match='constraint violation'):
            repo.insert_analysis(**self.BASE_KWARGS, build_failure_id=1)


# ===================================================================
# get_analysis_for_failure
# ===================================================================

class TestGetAnalysisForFailure:
    def test_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (
            1, 'claude-sonnet-4-6', 'OOM', 'resource_issue', 0.9,
            'increase mem', ['k8s.yaml'], True, False,
            datetime(2025, 6, 1), 'trace-1', 800, 0.008,
        )
        repo = AIAnalysisRepository(db)
        result = repo.get_analysis_for_failure(10)
        assert result['id'] == 1
        assert result['model_used'] == 'claude-sonnet-4-6'
        assert result['confidence_score'] == 0.9
        assert result['cost_usd'] == 0.008

    def test_not_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        repo = AIAnalysisRepository(db)
        assert repo.get_analysis_for_failure(999) is None

    def test_db_exception(self):
        db, conn, cursor = _make_db()
        cursor.execute.side_effect = Exception('timeout')
        repo = AIAnalysisRepository(db)
        with pytest.raises(Exception, match='timeout'):
            repo.get_analysis_for_failure(1)


# ===================================================================
# increment_attempts
# ===================================================================

class TestIncrementAttempts:
    def test_build_failure(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (3,)
        repo = AIAnalysisRepository(db)
        assert repo.increment_attempts(build_failure_id=10) == 3
        conn.commit.assert_called_once()

    def test_conforma_result(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (2,)
        repo = AIAnalysisRepository(db)
        assert repo.increment_attempts(conforma_result_id=20) == 2

    def test_no_row_returns_1(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        repo = AIAnalysisRepository(db)
        assert repo.increment_attempts(build_failure_id=999) == 1


# ===================================================================
# mark_skipped
# ===================================================================

class TestMarkSkipped:
    def test_build_failure(self):
        db, conn, cursor = _make_db()
        repo = AIAnalysisRepository(db)
        repo.mark_skipped('no_logs', build_failure_id=5)
        sql = cursor.execute.call_args[0][0]
        assert 'UPDATE build_failures' in sql
        conn.commit.assert_called_once()

    def test_conforma_result(self):
        db, conn, cursor = _make_db()
        repo = AIAnalysisRepository(db)
        repo.mark_skipped('max_retries', conforma_result_id=5)
        sql = cursor.execute.call_args[0][0]
        assert 'UPDATE conforma_results' in sql


# ===================================================================
# skip_no_logs_timeouts
# ===================================================================

class TestSkipNoLogsTimeouts:
    def test_returns_count(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 3
        repo = AIAnalysisRepository(db)
        result = repo.skip_no_logs_timeouts('app-1', timeout_days=14)
        assert result == 3
        conn.commit.assert_called_once()

    def test_zero_affected(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 0
        repo = AIAnalysisRepository(db)
        assert repo.skip_no_logs_timeouts('app-1') == 0


# ===================================================================
# get_status_counts
# ===================================================================

class TestGetStatusCounts:
    def test_returns_build_and_conforma_sections(self):
        db, conn, cursor = _make_db()
        # Three queries: build counts, conforma counts, low-confidence
        cursor.fetchone.side_effect = [
            (5, 2, 1, 10),       # build: pending, no_logs, skipped, analyzed
            (3, 0, 8),           # conforma: pending, skipped, analyzed
            (2, 1),              # low-confidence: build_low, conforma_low
        ]
        repo = AIAnalysisRepository(db)
        result = repo.get_status_counts('app-1')
        assert result['build']['pending'] == 5
        assert result['build']['no_logs'] == 2
        assert result['build']['low_confidence'] == 2
        assert result['conforma']['pending'] == 3
        assert result['conforma']['low_confidence'] == 1

    def test_db_exception(self):
        db, conn, cursor = _make_db()
        cursor.execute.side_effect = Exception('DB error')
        repo = AIAnalysisRepository(db)
        with pytest.raises(Exception, match='DB error'):
            repo.get_status_counts('app-1')


# ===================================================================
# get_pending_failures
# ===================================================================

class TestGetPendingFailures:
    @patch('repositories.ai_analysis_repository.resolve_blob_fields')
    def test_happy_path(self, mock_resolve):
        mock_resolve.side_effect = lambda e, **kw: e
        db, conn, cursor = _make_db()
        # 22-element row (matches SELECT column count)
        row = (
            1, 'comp-a', 'pr-123', 'error msg', 'BuildError',
            'build-container', 'step-build', 'logs here',
            'abc123', 'commit msg', 'author', 'repo-name',
            'main', {'diff': 'data'}, 'https://github.com/repo',
            {'extra': 'ctx'}, 'app-1',
            'typical fix text', 'doc context text', 'pattern-name', 42,
            None,  # blob_refs
        )
        cursor.fetchall.return_value = [row]
        repo = AIAnalysisRepository(db)
        result = repo.get_pending_failures(application='app-1', limit=10)
        assert len(result) == 1
        assert result[0]['component_name'] == 'comp-a'
        assert result[0]['pattern_name'] == 'pattern-name'
        mock_resolve.assert_called_once()

    @patch('repositories.ai_analysis_repository.resolve_blob_fields')
    def test_with_component_filter(self, mock_resolve):
        mock_resolve.side_effect = lambda e, **kw: e
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        repo.get_pending_failures(application='app-1', component_filter='comp-x')
        sql = cursor.execute.call_args[0][0]
        assert 'bf.component_name = %s' in sql

    @patch('repositories.ai_analysis_repository.resolve_blob_fields')
    def test_force_includes_analyzed(self, mock_resolve):
        mock_resolve.side_effect = lambda e, **kw: e
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        repo.get_pending_failures(application='app-1', force=True)
        sql = cursor.execute.call_args[0][0]
        assert 'bf.ai_analyzed = FALSE' not in sql

    @patch('repositories.ai_analysis_repository.resolve_blob_fields')
    def test_no_application(self, mock_resolve):
        mock_resolve.side_effect = lambda e, **kw: e
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        repo.get_pending_failures()  # application=None
        sql = cursor.execute.call_args[0][0]
        assert 'bf.application = %s' not in sql

    @patch('repositories.ai_analysis_repository.resolve_blob_fields')
    def test_empty_result(self, mock_resolve):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        assert repo.get_pending_failures('app-1') == []


# ===================================================================
# get_pending_count
# ===================================================================

class TestGetPendingCount:
    def test_with_application(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (7,)
        repo = AIAnalysisRepository(db)
        assert repo.get_pending_count('app-1') == 7

    def test_without_application(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (15,)
        repo = AIAnalysisRepository(db)
        assert repo.get_pending_count() == 15
        sql = cursor.execute.call_args[0][0]
        assert 'application' not in sql

    def test_zero(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (0,)
        repo = AIAnalysisRepository(db)
        assert repo.get_pending_count('app-1') == 0


# ===================================================================
# get_pending_conforma_violations
# ===================================================================

class TestGetPendingConformaViolations:
    @patch('repositories.ai_analysis_repository.resolve_blob_fields')
    def test_happy_path(self, mock_resolve):
        mock_resolve.side_effect = lambda e, **kw: e
        db, conn, cursor = _make_db()
        # 18-element row
        row = (
            1, 'comp-a', 'pr-123', 'scenario-1',
            3, 1, 10,
            'summary text', 'details text',
            'sha123', 'https://commit.url',
            'https://repo.url', 'snapshot-1',
            'typical fix', 'doc ctx', 'pattern-1', 55,
            None,  # blob_refs
        )
        cursor.fetchall.return_value = [row]
        repo = AIAnalysisRepository(db)
        result = repo.get_pending_conforma_violations(application='app-1')
        assert len(result) == 1
        assert result[0]['scenario'] == 'scenario-1'
        assert result[0]['violations_count'] == 3
        assert result[0]['repository'] == 'https://repo.url'
        assert result[0]['commit_message'] is None
        assert result[0]['commit_author'] is None
        mock_resolve.assert_called_once()

    @patch('repositories.ai_analysis_repository.resolve_blob_fields')
    def test_force_flag(self, mock_resolve):
        mock_resolve.side_effect = lambda e, **kw: e
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        repo.get_pending_conforma_violations(application='app-1', force=True)
        sql = cursor.execute.call_args[0][0]
        assert 'cr.ai_analyzed = FALSE' not in sql

    @patch('repositories.ai_analysis_repository.resolve_blob_fields')
    def test_component_filter(self, mock_resolve):
        mock_resolve.side_effect = lambda e, **kw: e
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        repo.get_pending_conforma_violations(component_filter='comp-x')
        sql = cursor.execute.call_args[0][0]
        assert 'cr.component_name = %s' in sql

    @patch('repositories.ai_analysis_repository.resolve_blob_fields')
    def test_empty(self, mock_resolve):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        assert repo.get_pending_conforma_violations() == []


# ===================================================================
# get_pending_conforma_count
# ===================================================================

class TestGetPendingConformaCount:
    def test_with_application(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (4,)
        repo = AIAnalysisRepository(db)
        assert repo.get_pending_conforma_count('app-1') == 4

    def test_without_application(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (12,)
        repo = AIAnalysisRepository(db)
        assert repo.get_pending_conforma_count() == 12

    def test_zero(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (0,)
        repo = AIAnalysisRepository(db)
        assert repo.get_pending_conforma_count('app-1') == 0


# ===================================================================
# insert_release_analysis
# ===================================================================

class TestInsertReleaseAnalysis:
    def test_happy_path(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (88,)
        repo = AIAnalysisRepository(db)
        result = repo.insert_release_analysis(
            release_name='rhoai-v3.5',
            model_used='claude-sonnet-4-6',
            root_cause='validation failed',
            failure_category='release_validation',
            confidence_score=0.75,
            recommended_fix='retry release',
            recommended_files=[],
            can_auto_fix=False,
            requires_human_review=True,
            tokens_used=2000,
            cost_usd=0.02,
            analysis_duration=5.0,
            analysis_json={'release': 'data'},
        )
        assert result == 88
        conn.commit.assert_called_once()

    def test_none_analysis_json(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (1,)
        repo = AIAnalysisRepository(db)
        repo.insert_release_analysis(
            release_name='rhoai-v3.5',
            model_used='model', root_cause='rc',
            failure_category='cat', confidence_score=0.5,
            recommended_fix='fix', recommended_files=[],
            can_auto_fix=False, requires_human_review=False,
            tokens_used=100, cost_usd=0.001, analysis_duration=1.0,
            analysis_json=None,
        )
        insert_params = cursor.execute.call_args[0][1]
        assert insert_params[-1] is None  # analysis_json should be None


# ===================================================================
# get_analysis_for_release
# ===================================================================

class TestGetAnalysisForRelease:
    def test_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (
            1, 'model', 'root cause', 'category', 0.8,
            'fix', ['f.py'], True, False, datetime(2025, 6, 1),
            'trace-1', 1000, 0.01, {'json': True},
        )
        repo = AIAnalysisRepository(db)
        result = repo.get_analysis_for_release('rhoai-v3.5')
        assert result['id'] == 1
        assert result['analysis_json'] == {'json': True}

    def test_not_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        repo = AIAnalysisRepository(db)
        assert repo.get_analysis_for_release('nonexistent') is None


# ===================================================================
# get_extended_status
# ===================================================================

class TestGetExtendedStatus:
    def test_happy_path(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.side_effect = [
            (5, 2, 1),          # build: pending, no_logs, skipped
            (10, 3, 2),         # build analysis: analyzed, low_conf, auto_fixable
            (4, 0),             # conforma: pending, skipped
            (8, 1, 3),          # conforma analysis: analyzed, low_conf, auto_fixable
            (Decimal('1.50'),), # total_cost
        ]
        repo = AIAnalysisRepository(db)
        result = repo.get_extended_status('app-1')
        assert result['build']['pending'] == 5
        assert result['build']['auto_fixable'] == 2
        assert result['conforma']['analyzed'] == 8
        assert result['total_cost'] == Decimal('1.50')

    def test_db_exception(self):
        db, conn, cursor = _make_db()
        cursor.execute.side_effect = Exception('conn error')
        repo = AIAnalysisRepository(db)
        with pytest.raises(Exception, match='conn error'):
            repo.get_extended_status('app-1')


# ===================================================================
# get_recent_analyses
# ===================================================================

class TestGetRecentAnalyses:
    def test_returns_list(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            ('comp-a', 'Build', 'dependency_issue', 85, True),
            ('comp-b', 'Conforma', 'policy_violation', 70, False),
        ]
        repo = AIAnalysisRepository(db)
        result = repo.get_recent_analyses('app-1', days=7, limit=10)
        assert len(result) == 2
        assert result[0]['component'] == 'comp-a'
        assert result[0]['type'] == 'Build'
        assert result[1]['auto_fixable'] is False

    def test_empty(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        assert repo.get_recent_analyses('app-1') == []


# ===================================================================
# get_category_stats
# ===================================================================

class TestGetCategoryStats:
    def test_returns_all_sections(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.side_effect = [
            # by_category
            [('Build', 'dependency_issue', 5, 80, 3)],
            # by_date
            [(date(2025, 6, 1), 10, 4, Decimal('0.10'))],
        ]
        cursor.fetchone.return_value = (5000, 500)  # total_tokens, avg_tokens
        repo = AIAnalysisRepository(db)
        result = repo.get_category_stats('app-1', days=30)
        assert len(result['by_category']) == 1
        assert result['by_category'][0]['category'] == 'dependency_issue'
        assert len(result['by_date']) == 1
        assert result['total_tokens'] == 5000
        assert result['avg_tokens'] == 500

    def test_empty(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.side_effect = [[], []]
        cursor.fetchone.return_value = (0, 0)
        repo = AIAnalysisRepository(db)
        result = repo.get_category_stats('app-1')
        assert result['by_category'] == []
        assert result['by_date'] == []
        assert result['total_tokens'] == 0


# ===================================================================
# get_cost_summary
# ===================================================================

class TestGetCostSummary:
    def test_happy_path(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (50, 25000, Decimal('2.50'))
        repo = AIAnalysisRepository(db)
        result = repo.get_cost_summary(days=30)
        assert result == {'analyses': 50, 'tokens': 25000, 'cost_usd': Decimal('2.50')}

    def test_zero_cost(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (0, 0, Decimal('0.00'))
        repo = AIAnalysisRepository(db)
        result = repo.get_cost_summary()
        assert result['analyses'] == 0
        assert result['cost_usd'] == Decimal('0.00')

    def test_custom_days(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (10, 5000, Decimal('0.50'))
        repo = AIAnalysisRepository(db)
        repo.get_cost_summary(days=7)
        params = cursor.execute.call_args[0][1]
        assert params == (7,)


# ===================================================================
# get_analysis_by_component
# ===================================================================

class TestGetAnalysisByComponent:
    def test_build_type(self):
        db, conn, cursor = _make_db()
        row = (
            'build', 'comp-a', 'model', 'root cause', 'cat', 0.9,
            'fix', ['f.py'], True, False,
            datetime(2025, 6, 1), 'http://lf/url', 1000, 0.01,
        )
        cursor.fetchone.return_value = row
        repo = AIAnalysisRepository(db)
        result = repo.get_analysis_by_component('comp-a', 'app-1', analysis_type='build')
        assert result['analysis_type'] == 'build'
        assert result['component_name'] == 'comp-a'

    def test_conforma_type(self):
        db, conn, cursor = _make_db()
        row = (
            'conforma', 'comp-a', 'model', 'root cause', 'cat', 0.9,
            'fix', ['f.py'], True, False,
            datetime(2025, 6, 1), 'http://lf/url', 1000, 0.01,
        )
        cursor.fetchone.return_value = row
        repo = AIAnalysisRepository(db)
        result = repo.get_analysis_by_component('comp-a', 'app-1', analysis_type='conforma')
        assert result['analysis_type'] == 'conforma'

    def test_auto_tries_build_then_conforma(self):
        db, conn, cursor = _make_db()
        # First call (build) returns None, second (conforma) returns a row
        row = (
            'conforma', 'comp-a', 'model', 'root cause', 'cat', 0.8,
            'fix', [], False, True,
            datetime(2025, 6, 1), None, 500, 0.005,
        )
        cursor.fetchone.side_effect = [None, row]
        repo = AIAnalysisRepository(db)
        result = repo.get_analysis_by_component('comp-a', 'app-1')
        assert result['analysis_type'] == 'conforma'

    def test_not_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        repo = AIAnalysisRepository(db)
        assert repo.get_analysis_by_component('comp-a', 'app-1') is None

    def test_auto_returns_build_if_found(self):
        db, conn, cursor = _make_db()
        row = (
            'build', 'comp-a', 'model', 'root', 'cat', 0.9,
            'fix', [], True, False,
            datetime(2025, 6, 1), None, 800, 0.01,
        )
        cursor.fetchone.return_value = row
        repo = AIAnalysisRepository(db)
        result = repo.get_analysis_by_component('comp-a', 'app-1', analysis_type='auto')
        assert result['analysis_type'] == 'build'


# ===================================================================
# get_full_analysis
# ===================================================================

class TestGetFullAnalysis:
    def _full_row(self, atype='build'):
        return (
            atype, 'comp-a', 'model', 'root cause', 'category', 0.85,
            'recommended fix', datetime(2025, 6, 1),
            {'analysis': 'json'}, 'correct', 'actual root cause',
        )

    def test_build_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = self._full_row('build')
        repo = AIAnalysisRepository(db)
        result = repo.get_full_analysis('comp-a', 'app-1')
        assert result['analysis_type'] == 'build'
        assert result['human_verdict'] == 'correct'

    def test_falls_through_to_conforma(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.side_effect = [None, self._full_row('conforma')]
        repo = AIAnalysisRepository(db)
        result = repo.get_full_analysis('comp-a', 'app-1')
        assert result['analysis_type'] == 'conforma'

    def test_falls_through_to_release(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.side_effect = [None, None, self._full_row('release')]
        repo = AIAnalysisRepository(db)
        result = repo.get_full_analysis('comp-a', 'app-1')
        assert result['analysis_type'] == 'release'

    def test_nothing_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        repo = AIAnalysisRepository(db)
        assert repo.get_full_analysis('comp-a', 'app-1') is None


# ===================================================================
# record_verdict
# ===================================================================

class TestRecordVerdict:
    def test_valid_verdict_correct(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 1
        repo = AIAnalysisRepository(db)
        # Patch _update_confidence_models to avoid side effects
        with patch.object(repo, '_update_confidence_models'):
            result = repo.record_verdict('comp-a', 'app-1', 'correct')
        assert result is True
        conn.commit.assert_called_once()

    def test_valid_verdict_incorrect(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 1
        repo = AIAnalysisRepository(db)
        with patch.object(repo, '_update_confidence_models'):
            result = repo.record_verdict('comp-a', 'app-1', 'incorrect',
                                         actual_root_cause='real cause')
        assert result is True

    def test_valid_verdict_partial(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 1
        repo = AIAnalysisRepository(db)
        with patch.object(repo, '_update_confidence_models'):
            result = repo.record_verdict('comp-a', 'app-1', 'partial')
        assert result is True

    def test_valid_verdict_unknown(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 1
        repo = AIAnalysisRepository(db)
        result = repo.record_verdict('comp-a', 'app-1', 'unknown')
        # 'unknown' does not trigger _update_confidence_models
        assert result is True

    def test_invalid_verdict_raises(self):
        db, _, _ = _make_db()
        repo = AIAnalysisRepository(db)
        with pytest.raises(ValueError, match='verdict must be one of'):
            repo.record_verdict('comp-a', 'app-1', 'maybe')

    def test_no_matching_row(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 0
        repo = AIAnalysisRepository(db)
        result = repo.record_verdict('comp-a', 'app-1', 'correct')
        assert result is False

    def test_verdict_by_parameter(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 1
        repo = AIAnalysisRepository(db)
        with patch.object(repo, '_update_confidence_models'):
            repo.record_verdict('comp-a', 'app-1', 'correct', verdict_by='triage')
        params = cursor.execute.call_args[0][1]
        assert 'triage' in params


# ===================================================================
# _update_confidence_models
# ===================================================================

class TestUpdateConfidenceModels:
    @patch('repositories.ai_analysis_repository.AIAnalysisRepository._update_confidence_models')
    def test_called_on_correct_verdict(self, mock_update):
        db, conn, cursor = _make_db()
        cursor.rowcount = 1
        repo = AIAnalysisRepository(db)
        repo.record_verdict('comp-a', 'app-1', 'correct')
        mock_update.assert_called_once_with('comp-a', 'app-1', 'correct')

    @patch('repositories.ai_analysis_repository.AIAnalysisRepository._update_confidence_models')
    def test_not_called_on_unknown_verdict(self, mock_update):
        db, conn, cursor = _make_db()
        cursor.rowcount = 1
        repo = AIAnalysisRepository(db)
        repo.record_verdict('comp-a', 'app-1', 'unknown')
        mock_update.assert_not_called()

    @patch('repositories.ai_analysis_repository.AIAnalysisRepository._update_confidence_models')
    def test_not_called_when_no_row_updated(self, mock_update):
        db, conn, cursor = _make_db()
        cursor.rowcount = 0
        repo = AIAnalysisRepository(db)
        repo.record_verdict('comp-a', 'app-1', 'correct')
        mock_update.assert_not_called()


# ===================================================================
# get_quality_metrics
# ===================================================================

class TestGetQualityMetrics:
    def test_with_verdicts(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (
            10,    # total_with_verdict
            6,     # correct
            2,     # partial
            2,     # incorrect
            0,     # unknown
            0.92,  # avg_conf_correct
            0.55,  # avg_conf_incorrect
            20,    # total_analyses
        )
        cursor.fetchall.return_value = [
            ('dependency_issue', 4, 1, 1, 6),
        ]
        repo = AIAnalysisRepository(db)
        result = repo.get_quality_metrics('app-1', days=30)
        assert result['total_with_verdict'] == 10
        assert result['correct'] == 6
        # accuracy = (6 + 2*0.5) / 10 = 0.7
        assert result['accuracy'] == 0.7
        assert result['avg_confidence_correct'] == 0.92
        assert 'dependency_issue' in result['by_category']

    def test_no_verdicts(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (0, 0, 0, 0, 0, None, None, 5)
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        result = repo.get_quality_metrics('app-1')
        assert result['accuracy'] is None
        assert result['avg_confidence_correct'] is None
        assert result['by_category'] == {}

    def test_without_application(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (0, 0, 0, 0, 0, None, None, 0)
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        result = repo.get_quality_metrics(application=None)
        # SQL should not include application filter
        sql = cursor.execute.call_args_list[0][0][0]
        assert 'b.application' not in sql
        assert result is not None


# ===================================================================
# get_verdict_stats_by_category
# ===================================================================

class TestGetVerdictStatsByCategory:
    def test_build_type(self):
        db, conn, cursor = _make_db()
        cursor.description = [
            ('category',), ('correct',), ('partial',), ('incorrect',), ('total',),
        ]
        cursor.fetchall.return_value = [
            ('dependency_issue', 5, 2, 1, 8),
        ]
        repo = AIAnalysisRepository(db)
        result = repo.get_verdict_stats_by_category('build', days=90)
        assert len(result) == 1
        assert result[0]['category'] == 'dependency_issue'
        assert result[0]['correct'] == 5

    def test_conforma_type(self):
        db, conn, cursor = _make_db()
        cursor.description = [
            ('category',), ('correct',), ('partial',), ('incorrect',), ('total',),
        ]
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        result = repo.get_verdict_stats_by_category('conforma')
        sql = cursor.execute.call_args[0][0]
        assert 'conforma_results' in sql
        assert result == []

    def test_all_type(self):
        db, conn, cursor = _make_db()
        cursor.description = [
            ('category',), ('correct',), ('partial',), ('incorrect',), ('total',),
        ]
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        result = repo.get_verdict_stats_by_category('all')
        sql = cursor.execute.call_args[0][0]
        assert 'LEFT JOIN build_failures' in sql
        assert 'LEFT JOIN conforma_results' in sql


# ===================================================================
# get_calibration_data
# ===================================================================

class TestGetCalibrationData:
    def test_maps_verdicts_to_accuracy(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            (0.9, 'correct', 'dep_issue'),
            (0.7, 'partial', 'build_error'),
            (0.3, 'incorrect', 'infra_issue'),
        ]
        repo = AIAnalysisRepository(db)
        result = repo.get_calibration_data()
        assert len(result) == 3
        assert result[0]['accuracy'] == 1.0
        assert result[1]['accuracy'] == 0.5
        assert result[2]['accuracy'] == 0.0

    def test_unknown_verdict_skipped(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            (0.5, 'unknown', 'cat'),
        ]
        repo = AIAnalysisRepository(db)
        result = repo.get_calibration_data()
        assert result == []

    def test_none_confidence_defaults_to_half(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            (None, 'correct', 'cat'),
        ]
        repo = AIAnalysisRepository(db)
        result = repo.get_calibration_data()
        assert result[0]['confidence'] == 0.5

    def test_filter_by_type_build(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        repo.get_calibration_data(analyzer_type='build')
        sql = cursor.execute.call_args[0][0]
        assert 'a.build_failure_id IS NOT NULL' in sql

    def test_filter_by_type_conforma(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        repo.get_calibration_data(analyzer_type='conforma')
        sql = cursor.execute.call_args[0][0]
        assert 'a.conforma_result_id IS NOT NULL' in sql

    def test_filter_by_category(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        repo.get_calibration_data(category='dependency_issue')
        sql = cursor.execute.call_args[0][0]
        assert 'a.failure_category = %s' in sql

    def test_empty(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        assert repo.get_calibration_data() == []


# ===================================================================
# get_resolved_conforma_with_analysis
# ===================================================================

class TestGetResolvedConformaWithAnalysis:
    def test_with_application(self):
        db, conn, cursor = _make_db()
        row = (
            1, 'comp-a', 'scenario-1', 'app-1',
            3, 'summary', datetime(2025, 5, 1), datetime(2025, 6, 1), 'RHOAI-1',
            10, 'policy_violation', 0.8,
            'root cause', 'fix', True, 'correct', 'actual cause', 'model',
        )
        cursor.fetchall.return_value = [row]
        repo = AIAnalysisRepository(db)
        result = repo.get_resolved_conforma_with_analysis('app-1', limit=10)
        assert len(result) == 1
        assert result[0]['component_name'] == 'comp-a'
        assert result[0]['human_verdict'] == 'correct'

    def test_without_application(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        result = repo.get_resolved_conforma_with_analysis()
        sql = cursor.execute.call_args[0][0]
        # When no application provided, the WHERE clause should not filter by app
        assert 'AND cr.application = %s' not in sql
        assert result == []


# ===================================================================
# get_resolved_builds_with_analysis
# ===================================================================

class TestGetResolvedBuildsWithAnalysis:
    def test_with_application(self):
        db, conn, cursor = _make_db()
        row = (
            1, 'comp-a', 'app-1',
            'build-container', 'BuildError', 'error msg',
            'failed', 'manual',
            datetime(2025, 5, 1), datetime(2025, 6, 1), 'RHOAI-1',
            'sha123',
            10, 'dependency_issue', 0.9,
            'root cause', 'fix', ['file.py'],
            True, 'correct', 'actual cause', 'model',
        )
        cursor.fetchall.return_value = [row]
        repo = AIAnalysisRepository(db)
        result = repo.get_resolved_builds_with_analysis('app-1')
        assert len(result) == 1
        assert result[0]['component_name'] == 'comp-a'
        assert result[0]['resolution_commit_sha'] == 'sha123'

    def test_without_application(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        result = repo.get_resolved_builds_with_analysis()
        sql = cursor.execute.call_args[0][0]
        # When no application provided, the WHERE clause should not filter by app
        assert 'AND bf.application = %s' not in sql
        assert result == []


# ===================================================================
# get_release_analyses
# ===================================================================

class TestGetReleaseAnalyses:
    def test_returns_list(self):
        db, conn, cursor = _make_db()
        row = (
            1, 'rhoai-v3.5', 'release_validation', 0.75,
            'root cause', 'fix', [], False,
            'correct', 'actual cause', 'model',
            datetime(2025, 6, 1), {'json': True},
        )
        cursor.fetchall.return_value = [row]
        repo = AIAnalysisRepository(db)
        result = repo.get_release_analyses(limit=10)
        assert len(result) == 1
        assert result[0]['release_name'] == 'rhoai-v3.5'

    def test_empty(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        repo = AIAnalysisRepository(db)
        assert repo.get_release_analyses() == []


# ===================================================================
# get_conforma_queue
# ===================================================================

class TestGetConformaQueue:
    def test_returns_counts(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (5, 12)
        repo = AIAnalysisRepository(db)
        result = repo.get_conforma_queue('app-1')
        assert result == {'pending': 5, 'analyzed': 12}

    def test_all_zero(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (0, 0)
        repo = AIAnalysisRepository(db)
        result = repo.get_conforma_queue('app-1')
        assert result == {'pending': 0, 'analyzed': 0}

    def test_db_exception(self):
        db, conn, cursor = _make_db()
        cursor.execute.side_effect = Exception('conn refused')
        repo = AIAnalysisRepository(db)
        with pytest.raises(Exception, match='conn refused'):
            repo.get_conforma_queue('app-1')


# ===================================================================
# _full_analysis_dict static method
# ===================================================================

class TestFullAnalysisDict:
    def test_all_fields(self):
        row = (
            'build', 'comp-a', 'model', 'root cause', 'category', 0.85,
            'fix', datetime(2025, 6, 1), {'json': True}, 'correct', 'actual',
        )
        result = AIAnalysisRepository._full_analysis_dict(row)
        assert result['analysis_type'] == 'build'
        assert result['component_name'] == 'comp-a'
        assert result['human_verdict'] == 'correct'
        assert result['actual_root_cause'] == 'actual'
        assert result['analysis_json'] == {'json': True}
