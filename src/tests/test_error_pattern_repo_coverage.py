"""Comprehensive tests for ErrorPatternRepository.

Covers all public methods: CRUD, matching, statistics, discovery, staleness.
"""

import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from repositories.error_pattern_repository import ErrorPatternRepository

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


def _make_repo():
    db, conn, cursor = _mock_db()
    return ErrorPatternRepository(db), db, conn, cursor


def _sample_row(pattern_id=1, failure_type='build', failure_category='dependency_issue'):
    return (
        pattern_id, failure_type, failure_category, failure_category,
        'A dependency build error', 'Update dependency version', 'https://docs/dep',
        'Doc excerpt about deps', '2026-01-01',
        10, 0.85, '2026-01-01', '2026-06-15', 'auto',
    )


# ═══════════════════════════════════════════════════════════════════════
# _row_to_dict (static method)
# ═══════════════════════════════════════════════════════════════════════

class TestRowToDict:
    def test_valid_row(self):
        result = ErrorPatternRepository._row_to_dict(_sample_row())
        assert result['id'] == 1
        assert result['failure_type'] == 'build'
        assert result['avg_confidence'] == 0.85
        assert result['created_by'] == 'auto'

    def test_none_row(self):
        assert ErrorPatternRepository._row_to_dict(None) == {}


# ═══════════════════════════════════════════════════════════════════════
# find_or_create
# ═══════════════════════════════════════════════════════════════════════

class TestFindOrCreate:
    def test_creates_and_returns(self):
        repo, _, conn, cursor = _make_repo()
        cursor.fetchone.return_value = _sample_row()
        result = repo.find_or_create('build', 'dependency_issue')
        assert result['failure_type'] == 'build'
        assert result['failure_category'] == 'dependency_issue'
        conn.commit.assert_called_once()
        # Two execute calls: INSERT ON CONFLICT + SELECT
        assert cursor.execute.call_count == 2

    def test_custom_pattern_name(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = _sample_row()
        repo.find_or_create('build', 'dep_issue', pattern_name='Dependency Failure')
        insert_call = cursor.execute.call_args_list[0]
        params = insert_call[0][1]
        assert params[2] == 'Dependency Failure'

    def test_default_pattern_name_is_category(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = _sample_row()
        repo.find_or_create('build', 'my_category')
        insert_call = cursor.execute.call_args_list[0]
        params = insert_call[0][1]
        assert params[2] == 'my_category'


# ═══════════════════════════════════════════════════════════════════════
# record_occurrence
# ═══════════════════════════════════════════════════════════════════════

class TestRecordOccurrence:
    def test_calls_update(self):
        repo, _, conn, cursor = _make_repo()
        repo.record_occurrence(42, 0.9)
        cursor.execute.assert_called_once()
        params = cursor.execute.call_args[0][1]
        assert params[0] == 0.9  # confidence_score
        assert params[2] == 42   # pattern_id
        conn.commit.assert_called_once()

    def test_zero_confidence(self):
        repo, _, conn, cursor = _make_repo()
        repo.record_occurrence(1, 0.0)
        params = cursor.execute.call_args[0][1]
        assert params[0] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# update_typical_fix
# ═══════════════════════════════════════════════════════════════════════

class TestUpdateTypicalFix:
    def test_updates_fix(self):
        repo, _, conn, cursor = _make_repo()
        repo.update_typical_fix(5, 'Run `npm install` again')
        cursor.execute.assert_called_once()
        params = cursor.execute.call_args[0][1]
        assert params[0] == 'Run `npm install` again'
        assert params[1] == 5
        conn.commit.assert_called_once()

    def test_empty_fix(self):
        repo, _, _, cursor = _make_repo()
        repo.update_typical_fix(5, '')
        params = cursor.execute.call_args[0][1]
        assert params[0] == ''


# ═══════════════════════════════════════════════════════════════════════
# update_doc_context
# ═══════════════════════════════════════════════════════════════════════

class TestUpdateDocContext:
    def test_stores_doc_context(self):
        repo, _, conn, cursor = _make_repo()
        repo.update_doc_context(7, 'Fetched documentation excerpt')
        cursor.execute.assert_called_once()
        params = cursor.execute.call_args[0][1]
        assert params[0] == 'Fetched documentation excerpt'
        assert params[1] == 7
        conn.commit.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert 'doc_fetched_at = NOW()' in sql


# ═══════════════════════════════════════════════════════════════════════
# get_needing_doc_fetch
# ═══════════════════════════════════════════════════════════════════════

class TestGetNeedingDocFetch:
    def test_returns_stale_patterns(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [_sample_row(1), _sample_row(2)]
        result = repo.get_needing_doc_fetch(stale_days=7)
        assert len(result) == 2
        assert result[0]['id'] == 1

    def test_custom_stale_days(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        repo.get_needing_doc_fetch(stale_days=30)
        params = cursor.execute.call_args[0][1]
        assert params[0] == 30

    def test_empty_results(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        assert repo.get_needing_doc_fetch() == []


# ═══════════════════════════════════════════════════════════════════════
# get_all
# ═══════════════════════════════════════════════════════════════════════

class TestGetAll:
    def test_all_patterns(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [_sample_row(1), _sample_row(2)]
        result = repo.get_all()
        assert len(result) == 2

    def test_filtered_by_type(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [_sample_row()]
        result = repo.get_all(failure_type='build')
        assert len(result) == 1
        params = cursor.execute.call_args[0][1]
        assert params[0] == 'build'

    def test_no_filter(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        repo.get_all()
        sql = cursor.execute.call_args[0][0]
        assert 'WHERE' not in sql

    def test_empty(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        assert repo.get_all() == []


# ═══════════════════════════════════════════════════════════════════════
# get_by_category
# ═══════════════════════════════════════════════════════════════════════

class TestGetByCategory:
    def test_found(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = _sample_row()
        result = repo.get_by_category('build', 'dependency_issue')
        assert result['failure_category'] == 'dependency_issue'

    def test_not_found(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = None
        assert repo.get_by_category('build', 'nonexistent') is None


# ═══════════════════════════════════════════════════════════════════════
# link_analysis
# ═══════════════════════════════════════════════════════════════════════

class TestLinkAnalysis:
    def test_updates_analysis_table(self):
        repo, _, conn, cursor = _make_repo()
        repo.link_analysis(analysis_id=100, pattern_id=5)
        cursor.execute.assert_called_once()
        params = cursor.execute.call_args[0][1]
        assert params == (5, 100)
        conn.commit.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert 'ai_analysis' in sql


# ═══════════════════════════════════════════════════════════════════════
# record_fix_outcome
# ═══════════════════════════════════════════════════════════════════════

class TestRecordFixOutcome:
    def test_successful_fix(self):
        repo, _, conn, cursor = _make_repo()
        repo.record_fix_outcome(pattern_id=3, was_successful=True)
        params = cursor.execute.call_args[0][1]
        # outcome_score=1.0, LEARNING_RATE=0.2
        assert params[0] == 1.0  # outcome_score for NULL case
        assert params[1] == 0.2  # LEARNING_RATE
        assert params[2] == 0.2  # LEARNING_RATE
        assert params[3] == 1.0  # outcome_score for EMA
        assert params[4] == 3    # pattern_id
        conn.commit.assert_called_once()

    def test_failed_fix(self):
        repo, _, conn, cursor = _make_repo()
        repo.record_fix_outcome(pattern_id=3, was_successful=False)
        params = cursor.execute.call_args[0][1]
        assert params[0] == 0.0  # outcome_score
        assert params[3] == 0.0
        conn.commit.assert_called_once()

    def test_sql_updates_match_count(self):
        repo, _, _, cursor = _make_repo()
        repo.record_fix_outcome(1, True)
        sql = cursor.execute.call_args[0][0]
        assert 'match_count = match_count + 1' in sql
        assert 'last_used_at = NOW()' in sql


# ═══════════════════════════════════════════════════════════════════════
# get_pattern_for_failure
# ═══════════════════════════════════════════════════════════════════════

class TestGetPatternForFailure:
    def test_by_build_failure_id(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = (7,)
        result = repo.get_pattern_for_failure(build_failure_id=42)
        assert result == 7
        sql = cursor.execute.call_args[0][0]
        assert 'build_failure_id' in sql

    def test_by_conforma_result_id(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = (12,)
        result = repo.get_pattern_for_failure(conforma_result_id=99)
        assert result == 12
        sql = cursor.execute.call_args[0][0]
        assert 'conforma_result_id' in sql

    def test_neither_id_returns_none(self):
        repo, _, _, cursor = _make_repo()
        result = repo.get_pattern_for_failure()
        assert result is None
        cursor.execute.assert_not_called()

    def test_no_pattern_found(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = None
        result = repo.get_pattern_for_failure(build_failure_id=1)
        assert result is None

    def test_build_id_takes_precedence(self):
        """When both IDs are given, build_failure_id branch runs."""
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = (5,)
        result = repo.get_pattern_for_failure(build_failure_id=1, conforma_result_id=2)
        assert result == 5
        sql = cursor.execute.call_args[0][0]
        assert 'build_failure_id' in sql


# ═══════════════════════════════════════════════════════════════════════
# discover_new_patterns
# ═══════════════════════════════════════════════════════════════════════

class TestDiscoverNewPatterns:
    def test_discovers_and_creates(self):
        repo, db, conn, cursor = _make_repo()
        # First call: discover candidates
        cursor.fetchall.return_value = [
            ('fips_check_failure', 5, 0.8),
            ('image_pull_error', 3, 0.65),
        ]

        # Patch find_or_create and _set_initial_confidence to avoid nested DB calls
        created_patterns = [
            {'id': 10, 'failure_type': 'build', 'failure_category': 'fips_check_failure'},
            {'id': 11, 'failure_type': 'build', 'failure_category': 'image_pull_error'},
        ]
        with patch.object(repo, 'find_or_create', side_effect=created_patterns) as mock_foc, \
             patch.object(repo, '_set_initial_confidence') as mock_sic:
            result = repo.discover_new_patterns()
            assert len(result) == 2
            assert mock_foc.call_count == 2
            # Both have avg_conf, so _set_initial_confidence called for both
            assert mock_sic.call_count == 2
            mock_sic.assert_any_call(10, 0.8, 5)
            mock_sic.assert_any_call(11, 0.65, 3)

    def test_no_candidates(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        result = repo.discover_new_patterns()
        assert result == []

    def test_skips_confidence_when_none(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [('some_cat', 4, None)]
        pattern = {'id': 20, 'failure_type': 'build', 'failure_category': 'some_cat'}
        with patch.object(repo, 'find_or_create', return_value=pattern), \
             patch.object(repo, '_set_initial_confidence') as mock_sic:
            result = repo.discover_new_patterns()
            assert len(result) == 1
            mock_sic.assert_not_called()

    def test_min_occurrences_constant(self):
        assert ErrorPatternRepository.MIN_OCCURRENCES_FOR_PATTERN == 3


# ═══════════════════════════════════════════════════════════════════════
# _set_initial_confidence
# ═══════════════════════════════════════════════════════════════════════

class TestSetInitialConfidence:
    def test_sets_values(self):
        repo, _, conn, cursor = _make_repo()
        repo._set_initial_confidence(pattern_id=5, avg_confidence=0.75,
                                     occurrence_count=10)
        params = cursor.execute.call_args[0][1]
        assert params == (0.75, 10, 5)
        conn.commit.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert 'avg_confidence IS NULL' in sql


# ═══════════════════════════════════════════════════════════════════════
# get_stale_patterns
# ═══════════════════════════════════════════════════════════════════════

class TestGetStalePatterns:
    def test_returns_stale(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [_sample_row(99)]
        result = repo.get_stale_patterns()
        assert len(result) == 1
        assert result[0]['id'] == 99

    def test_custom_thresholds(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        repo.get_stale_patterns(inactive_days=180, min_accuracy=0.5)
        params = cursor.execute.call_args[0][1]
        assert params[0] == 180
        assert params[1] == 0.5

    def test_default_thresholds(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        repo.get_stale_patterns()
        params = cursor.execute.call_args[0][1]
        assert params[0] == 90
        assert params[1] == 0.3

    def test_empty(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        assert repo.get_stale_patterns() == []


# ═══════════════════════════════════════════════════════════════════════
# get_cross_app_patterns
# ═══════════════════════════════════════════════════════════════════════

class TestGetCrossAppPatterns:
    def test_returns_cross_app(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [
            (1, 'fips_check', 'build', 'fips_check_failure', 0.9,
             'Fix FIPS', 3, ['app1', 'app2', 'app3'], 15),
        ]
        result = repo.get_cross_app_patterns()
        assert len(result) == 1
        assert result[0]['app_count'] == 3
        assert result[0]['applications'] == ['app1', 'app2', 'app3']
        assert result[0]['total_occurrences'] == 15

    def test_empty(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        assert repo.get_cross_app_patterns() == []

    def test_correct_columns(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [
            (1, 'name', 'build', 'cat', 0.5, 'fix', 2, ['a', 'b'], 5),
        ]
        result = repo.get_cross_app_patterns()
        expected_keys = {'id', 'pattern_name', 'failure_type', 'failure_category',
                         'avg_confidence', 'typical_fix', 'app_count',
                         'applications', 'total_occurrences'}
        assert set(result[0].keys()) == expected_keys


# ═══════════════════════════════════════════════════════════════════════
# get_patterns_for_app
# ═══════════════════════════════════════════════════════════════════════

class TestGetPatternsForApp:
    def test_returns_app_patterns(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [
            (1, 'fips_check', 'build', 'fips_check_failure', 0.9,
             'Fix FIPS', 5, 20, ['app2', 'app3']),
        ]
        result = repo.get_patterns_for_app('app1')
        assert len(result) == 1
        assert result[0]['app_occurrences'] == 5
        assert result[0]['also_seen_in'] == ['app2', 'app3']

    def test_application_param_passed(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        repo.get_patterns_for_app('my-app')
        params = cursor.execute.call_args[0][1]
        assert params == ('my-app', 'my-app')

    def test_empty(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        assert repo.get_patterns_for_app('app1') == []

    def test_correct_columns(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [
            (1, 'name', 'build', 'cat', 0.5, 'fix', 3, 10, None),
        ]
        result = repo.get_patterns_for_app('app1')
        expected_keys = {'id', 'pattern_name', 'failure_type', 'failure_category',
                         'avg_confidence', 'typical_fix', 'app_occurrences',
                         'total_occurrences', 'also_seen_in'}
        assert set(result[0].keys()) == expected_keys


# ═══════════════════════════════════════════════════════════════════════
# get_by_name_or_category
# ═══════════════════════════════════════════════════════════════════════

class TestGetByNameOrCategory:
    def test_found(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = _sample_row()
        result = repo.get_by_name_or_category('dependency_issue')
        assert result['failure_category'] == 'dependency_issue'

    def test_not_found(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = None
        assert repo.get_by_name_or_category('nonexistent') is None

    def test_searches_both_name_and_category(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = None
        repo.get_by_name_or_category('fips_check')
        params = cursor.execute.call_args[0][1]
        assert params == ('fips_check', 'fips_check')
        sql = cursor.execute.call_args[0][0]
        assert 'pattern_name = %s OR failure_category = %s' in sql


# ═══════════════════════════════════════════════════════════════════════
# get_library_summary
# ═══════════════════════════════════════════════════════════════════════

class TestGetLibrarySummary:
    def test_returns_summary(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = (25, 20, 78, 150)
        result = repo.get_library_summary()
        assert result == {
            'total': 25,
            'with_confidence': 20,
            'avg_confidence': 78,
            'total_occurrences': 150,
        }

    def test_empty_table(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchone.return_value = (0, 0, 0, 0)
        result = repo.get_library_summary()
        assert result['total'] == 0
        assert result['total_occurrences'] == 0


# ═══════════════════════════════════════════════════════════════════════
# get_patterns_with_skills
# ═══════════════════════════════════════════════════════════════════════

class TestGetPatternsWithSkills:
    def test_returns_patterns(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [
            (1, 'build', 'fips_check_failure', 'FIPS Check', 'fix_fips'),
            (2, 'conforma', 'sbom_missing', 'SBOM Missing', 'fix_sbom'),
        ]
        result = repo.get_patterns_with_skills()
        assert len(result) == 2
        assert result[0]['skill_name'] == 'fix_fips'
        assert result[1]['pattern_name'] == 'SBOM Missing'

    def test_empty(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        assert repo.get_patterns_with_skills() == []

    def test_correct_columns(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = [
            (1, 'build', 'cat', 'name', 'skill'),
        ]
        result = repo.get_patterns_with_skills()
        expected_keys = {'id', 'failure_type', 'failure_category',
                         'pattern_name', 'skill_name'}
        assert set(result[0].keys()) == expected_keys

    def test_sql_filters_empty_skill(self):
        repo, _, _, cursor = _make_repo()
        cursor.fetchall.return_value = []
        repo.get_patterns_with_skills()
        sql = cursor.execute.call_args[0][0]
        assert "skill_name IS NOT NULL" in sql
        assert "skill_name != ''" in sql
