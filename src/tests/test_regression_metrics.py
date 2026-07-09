"""Tests for Phase 17b: improved regression tester metrics.

Covers:
- compute_null_model_accuracy()
- build_confusion_matrix()
- compute_per_category_f1()
- compute_macro_f1()
- BuildRegressionTester.compute_labeled_metrics()
- RegressionMetrics / CategoryMetrics new fields
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# --- Pure function tests ---

class TestComputeNullModelAccuracy:
    def _fn(self, labels):
        from analyzers.build_regression import compute_null_model_accuracy
        return compute_null_model_accuracy(labels)

    def test_empty_returns_zero(self):
        assert self._fn([]) == 0.0

    def test_single_label_returns_1(self):
        assert self._fn(['dependency_issue']) == 1.0

    def test_majority_class_wins(self):
        labels = ['a', 'a', 'a', 'b', 'b']
        assert self._fn(labels) == 0.6

    def test_tie_returns_0_5(self):
        labels = ['a', 'a', 'b', 'b']
        assert self._fn(labels) == 0.5

    def test_all_same_class_returns_1(self):
        labels = ['build_error'] * 10
        assert self._fn(labels) == 1.0

    def test_uniform_distribution_returns_1_over_n(self):
        labels = ['a', 'b', 'c', 'd']
        assert self._fn(labels) == 0.25


class TestBuildConfusionMatrix:
    def _fn(self, rows):
        from analyzers.build_regression import build_confusion_matrix
        return build_confusion_matrix(rows)

    def _row(self, true_label, ai_predicted):
        return {'ml_label': true_label, 'ai_predicted': ai_predicted}

    def test_empty_returns_empty_dict(self):
        assert self._fn([]) == {}

    def test_perfect_predictions(self):
        rows = [
            self._row('dep', 'dep'),
            self._row('dep', 'dep'),
            self._row('cfg', 'cfg'),
        ]
        matrix = self._fn(rows)
        assert matrix['dep']['dep'] == 2
        assert matrix['cfg']['cfg'] == 1

    def test_misprediction_recorded(self):
        rows = [self._row('dep', 'build'), self._row('dep', 'dep')]
        matrix = self._fn(rows)
        assert matrix['dep']['build'] == 1
        assert matrix['dep']['dep'] == 1

    def test_multiple_classes(self):
        rows = [
            self._row('a', 'a'),
            self._row('a', 'b'),
            self._row('b', 'b'),
            self._row('b', 'a'),
            self._row('c', 'c'),
        ]
        matrix = self._fn(rows)
        assert matrix['a']['a'] == 1
        assert matrix['a']['b'] == 1
        assert matrix['b']['b'] == 1
        assert matrix['b']['a'] == 1
        assert matrix['c']['c'] == 1

    def test_missing_ml_label_uses_unknown(self):
        rows = [{'ai_predicted': 'dep'}]
        matrix = self._fn(rows)
        assert 'unknown' in matrix

    def test_missing_ai_predicted_uses_unknown(self):
        rows = [{'ml_label': 'dep'}]
        matrix = self._fn(rows)
        assert matrix['dep']['unknown'] == 1


class TestComputePerCategoryF1:
    def _fn(self, matrix):
        from analyzers.build_regression import compute_per_category_f1
        return compute_per_category_f1(matrix)

    def test_empty_matrix_returns_empty(self):
        assert self._fn({}) == {}

    def test_perfect_classifier_all_1(self):
        matrix = {'dep': {'dep': 10}, 'build': {'build': 5}}
        result = self._fn(matrix)
        assert result['dep']['precision'] == 1.0
        assert result['dep']['recall'] == 1.0
        assert result['dep']['f1'] == 1.0
        assert result['build']['f1'] == 1.0

    def test_all_wrong_precision_0(self):
        matrix = {'dep': {'build': 5}}
        result = self._fn(matrix)
        assert result['dep']['recall'] == 0.0
        assert result['dep']['f1'] == 0.0
        assert result['build']['precision'] == 0.0

    def test_partial_overlap(self):
        matrix = {
            'a': {'a': 3, 'b': 1},
            'b': {'b': 2, 'a': 1},
        }
        result = self._fn(matrix)
        assert result['a']['precision'] == pytest_approx(3 / 4, abs=0.01)
        assert result['a']['recall'] == pytest_approx(3 / 4, abs=0.01)

    def test_f1_is_harmonic_mean(self):
        matrix = {'a': {'a': 2, 'b': 1}, 'b': {'b': 3, 'a': 0}}
        result = self._fn(matrix)
        p = result['a']['precision']
        r = result['a']['recall']
        f1 = result['a']['f1']
        expected_f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        assert abs(f1 - expected_f1) < 0.001

    def test_zero_tp_zero_fp_precision_is_zero(self):
        matrix = {'a': {'b': 3}, 'b': {'b': 2}}
        result = self._fn(matrix)
        assert result['a']['precision'] == 0.0


class TestComputeMacroF1:
    def _fn(self, per_cat):
        from analyzers.build_regression import compute_macro_f1
        return compute_macro_f1(per_cat)

    def test_empty_returns_zero(self):
        assert self._fn({}) == 0.0

    def test_single_category(self):
        assert self._fn({'dep': {'f1': 0.8}}) == 0.8

    def test_macro_average(self):
        per_cat = {'a': {'f1': 0.6}, 'b': {'f1': 0.4}}
        result = self._fn(per_cat)
        assert abs(result - 0.5) < 0.001

    def test_all_perfect_returns_1(self):
        per_cat = {'a': {'f1': 1.0}, 'b': {'f1': 1.0}, 'c': {'f1': 1.0}}
        assert self._fn(per_cat) == 1.0

    def test_zero_f1_pulls_down_average(self):
        per_cat = {'a': {'f1': 1.0}, 'b': {'f1': 0.0}}
        result = self._fn(per_cat)
        assert abs(result - 0.5) < 0.001


# --- BuildRegressionTester.compute_labeled_metrics() ---

def _make_tester():
    from unittest.mock import MagicMock

    from analyzers.build_regression import BuildRegressionTester
    config = MagicMock()
    config.db = MagicMock()
    db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    db.connection.return_value = conn
    return BuildRegressionTester(config, db=db)


class TestComputeLabeledMetrics:
    def test_empty_input_returns_zeros(self):
        tester = _make_tester()
        result = tester.compute_labeled_metrics([])
        assert result['labeled_count'] == 0
        assert result['macro_f1'] == 0.0
        assert result['confusion_matrix'] == {}

    def test_labeled_count_matches_input(self):
        tester = _make_tester()
        rows = [
            {'ml_label': 'dep', 'ai_predicted': 'dep'},
            {'ml_label': 'dep', 'ai_predicted': 'dep'},
            {'ml_label': 'build', 'ai_predicted': 'dep'},
        ]
        result = tester.compute_labeled_metrics(rows)
        assert result['labeled_count'] == 3

    def test_perfect_predictions_macro_f1_1(self):
        tester = _make_tester()
        rows = [
            {'ml_label': 'dep', 'ai_predicted': 'dep'},
            {'ml_label': 'build', 'ai_predicted': 'build'},
        ]
        result = tester.compute_labeled_metrics(rows)
        assert result['macro_f1'] == 1.0

    def test_confusion_matrix_populated(self):
        tester = _make_tester()
        rows = [
            {'ml_label': 'dep', 'ai_predicted': 'dep'},
            {'ml_label': 'dep', 'ai_predicted': 'build'},
        ]
        result = tester.compute_labeled_metrics(rows)
        matrix = result['confusion_matrix']
        assert matrix['dep']['dep'] == 1
        assert matrix['dep']['build'] == 1

    def test_per_category_f1_present(self):
        tester = _make_tester()
        rows = [{'ml_label': 'dep', 'ai_predicted': 'dep'} for _ in range(5)]
        result = tester.compute_labeled_metrics(rows)
        assert 'dep' in result['per_category']
        assert result['per_category']['dep']['f1'] == 1.0


# --- Models: CategoryMetrics and RegressionMetrics new fields ---

class TestCategoryMetricsNewFields:
    def test_has_precision_recall_f1_defaults(self):
        from analyzers.models import CategoryMetrics
        cm = CategoryMetrics()
        assert cm.precision == 0.0
        assert cm.recall == 0.0
        assert cm.f1 == 0.0

    def test_can_set_precision_recall_f1(self):
        from analyzers.models import CategoryMetrics
        cm = CategoryMetrics(correct=8, total=10, precision=0.8, recall=0.75, f1=0.77)
        assert cm.precision == 0.8
        assert cm.recall == 0.75
        assert cm.f1 == 0.77

    def test_model_dump_includes_new_fields(self):
        from analyzers.models import CategoryMetrics
        data = CategoryMetrics(precision=0.9, recall=0.85, f1=0.87).model_dump()
        assert 'precision' in data
        assert 'recall' in data
        assert 'f1' in data


class TestRegressionMetricsNewFields:
    def _make(self, **kwargs):
        from analyzers.models import RegressionMetrics
        defaults = {'total_resolved': 10, 'with_ai_analysis': 8, 'ai_coverage_pct': 80.0}
        defaults.update(kwargs)
        return RegressionMetrics(**defaults)

    def test_null_model_accuracy_defaults_zero(self):
        m = self._make()
        assert m.null_model_accuracy == 0.0

    def test_macro_f1_defaults_zero(self):
        m = self._make()
        assert m.macro_f1 == 0.0

    def test_labeled_count_defaults_zero(self):
        m = self._make()
        assert m.labeled_count == 0

    def test_confusion_matrix_defaults_empty(self):
        m = self._make()
        assert m.confusion_matrix == {}

    def test_can_set_new_fields(self):
        m = self._make(
            null_model_accuracy=0.55,
            macro_f1=0.72,
            labeled_count=42,
            confusion_matrix={'dep': {'dep': 10, 'build': 2}},
        )
        assert m.null_model_accuracy == 0.55
        assert m.macro_f1 == 0.72
        assert m.labeled_count == 42
        assert m.confusion_matrix['dep']['dep'] == 10

    def test_model_dump_includes_new_fields(self):
        m = self._make(null_model_accuracy=0.6, macro_f1=0.7, labeled_count=5)
        data = m.model_dump()
        assert 'null_model_accuracy' in data
        assert 'macro_f1' in data
        assert 'labeled_count' in data
        assert 'confusion_matrix' in data

    def test_null_accuracy_validated_range(self):
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            self._make(null_model_accuracy=1.5)


def pytest_approx(value, abs=0.01):
    import pytest
    return pytest.approx(value, abs=abs)
