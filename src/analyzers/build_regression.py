"""Regression testing for the Build Failure AI analyzer.

Uses resolved build failures as ground truth to measure analyzer accuracy,
identify coverage gaps by task name, and suggest improvements.

Two evaluation modes run in parallel:

  1. Heuristic evaluation (always available):
     Ground truth inferred from failed_task_name and error_message patterns.
     Produces: accuracy, calibration_score, null_model_accuracy, by_category.

  2. Label-based evaluation (when ml_training_labels are populated):
     Ground truth from GitHub PR file patterns via LabelInferenceService.
     Produces: confusion_matrix, macro_f1, per-category precision/recall/f1.
     Requires running: ic ai label-training-data --backfill

Pure functions at module level handle all metric math; the class handles I/O.
"""

import re
from collections import Counter

from logger import setup_logger
from repositories.ai_analysis_repository import AIAnalysisRepository
from repositories.connection import DatabaseConnection

logger = setup_logger(__name__)


# --- Pure functions: ML metric computation ---

def compute_null_model_accuracy(true_labels):
    """Accuracy of a majority-class classifier (always predicts the most common label).

    Args:
        true_labels: Iterable of ground truth category strings.

    Returns:
        float between 0 and 1, or 0.0 if the list is empty.
    """
    if not true_labels:
        return 0.0
    counts = Counter(true_labels)
    majority_count = max(counts.values())
    return majority_count / len(true_labels)


def build_confusion_matrix(labeled_failures):
    """Build a confusion matrix from labeled build failures.

    Args:
        labeled_failures: List of dicts with 'ml_label' (ground truth)
            and 'ai_predicted' (AI output).

    Returns:
        Nested dict: confusion_matrix[true_label][predicted_label] = count.
    """
    matrix = {}
    for row in labeled_failures:
        true_label = row.get('ml_label', 'unknown')
        predicted = row.get('ai_predicted', 'unknown')
        if true_label not in matrix:
            matrix[true_label] = {}
        matrix[true_label][predicted] = matrix[true_label].get(predicted, 0) + 1
    return matrix


def compute_per_category_f1(confusion_matrix):
    """Compute precision, recall, and F1 per category from a confusion matrix.

    Args:
        confusion_matrix: Dict[true_label, Dict[predicted_label, int]].

    Returns:
        Dict[category, {'precision': float, 'recall': float, 'f1': float}].
    """
    all_categories = set(confusion_matrix.keys())
    for predicted_dict in confusion_matrix.values():
        all_categories.update(predicted_dict.keys())

    result = {}
    for cat in all_categories:
        tp = confusion_matrix.get(cat, {}).get(cat, 0)

        fp = sum(
            confusion_matrix.get(true, {}).get(cat, 0)
            for true in all_categories if true != cat
        )
        fn = sum(
            confusion_matrix.get(cat, {}).get(pred, 0)
            for pred in (confusion_matrix.get(cat) or {}) if pred != cat
        )

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        result[cat] = {
            'precision': round(precision, 3),
            'recall': round(recall, 3),
            'f1': round(f1, 3),
        }
    return result


def compute_macro_f1(per_category_metrics):
    """Macro-averaged F1: unweighted mean of per-category F1 scores.

    Args:
        per_category_metrics: Dict from compute_per_category_f1().

    Returns:
        float between 0 and 1.
    """
    if not per_category_metrics:
        return 0.0
    f1_scores = [m['f1'] for m in per_category_metrics.values()]
    return round(sum(f1_scores) / len(f1_scores), 3)


TASK_PATTERNS = {
    'buildah': re.compile(r'buildah|build-container', re.I),
    'git_clone': re.compile(r'git-clone|clone-repository', re.I),
    'clair_scan': re.compile(r'clair|vulnerability', re.I),
    'fips_check': re.compile(r'fips', re.I),
    'clamav_scan': re.compile(r'clamav', re.I),
    'prefetch': re.compile(r'prefetch|cachi2', re.I),
    'show_summary': re.compile(r'show-summary|summary', re.I),
    'sast': re.compile(r'sast|snyk', re.I),
    'source_build': re.compile(r'source-build', re.I),
    'init': re.compile(r'^init$|init-task', re.I),
}

EXPECTED_CATEGORIES = {
    'buildah': 'build_error',
    'git_clone': 'git_sync_issue',
    'clair_scan': 'test_failure',
    'fips_check': 'test_failure',
    'clamav_scan': 'test_failure',
    'prefetch': 'dependency_issue',
    'show_summary': 'build_error',
    'sast': 'test_failure',
    'source_build': 'build_error',
    'init': 'config_error',
}

ERROR_PATTERNS = [
    (re.compile(
        r'read-only|registry.*denied|System is currently read-only',
        re.I,
    ), 'infrastructure'),
    (re.compile(
        r'serviceaccount.*not found|failed to create task run pod.*serviceaccount',
        re.I,
    ), 'infrastructure'),
    (re.compile(
        r'fatal: not a git repository',
        re.I,
    ), 'infrastructure'),
    (re.compile(
        r'No package matches|Could not depsolve|Unable to find a match'
        r'|No match for argument',
        re.I,
    ), 'dependency_issue'),
    (re.compile(
        r'pip install.*error|uv pip install|Could not find.*version'
        r'|Preparing metadata.*error',
        re.I,
    ), 'dependency_issue'),
    (re.compile(
        r'hermeto.*error|prefetch.*fail|cachi2.*error',
        re.I,
    ), 'dependency_issue'),
    (re.compile(r'timed out|Build timed out|PipelineRunTimeout', re.I), 'resource_limit'),
    (re.compile(
        r'step-create-sbom.*exited with code|mobster.*error',
        re.I,
    ), 'infrastructure'),
    (re.compile(
        r'Status code: 404.*repo|repository.*404|repo.*not found',
        re.I,
    ), 'build_error'),
    (re.compile(
        r'command not found|not found in.*PATH|uv.*not found',
        re.I,
    ), 'build_error'),
    (re.compile(
        r'step-push.*exited with code|push.*denied|upload.*error'
        r'|Uploading.*error',
        re.I,
    ), 'infrastructure'),
    (re.compile(
        r'fips.*check.*fail|fips.*exited with code',
        re.I,
    ), 'test_failure'),
    (re.compile(
        r'"step-build" exited with code',
        re.I,
    ), 'build_error'),
]

COMPATIBLE_CATEGORIES = {
    'dependency_issue': {'dependency_issue', 'build_error'},
    'build_error': {'build_error', 'dependency_issue'},
    'infrastructure': {'infrastructure', 'build_error'},
}

FAST_RESOLUTION_HOURS = 48


class BuildRegressionTester:
    """Tests the build failure analyzer against resolved build failures."""

    def __init__(self, config, db=None, ai_repo=None):
        self.config = config
        if db is None:
            db = DatabaseConnection(config.db)
        self.db = db
        self.ai_repo = ai_repo or AIAnalysisRepository(db)

    def get_resolved_with_analysis(self, application=None, limit=50):
        return self.ai_repo.get_resolved_builds_with_analysis(
            application=application, limit=limit
        )

    def get_labeled_with_analysis(self, application=None,
                                   min_label_confidence=0.0, limit=200):
        return self.ai_repo.get_labeled_builds_with_analysis(
            application=application,
            min_label_confidence=min_label_confidence,
            limit=limit,
        )

    def _infer_task_group(self, failed_task_name):
        if not failed_task_name:
            return 'other'
        for group, pattern in TASK_PATTERNS.items():
            if pattern.search(failed_task_name):
                return group
        return 'other'

    @staticmethod
    def _infer_from_error(error_message):
        """Infer expected categories from build error text.

        Returns a set of acceptable categories, or empty set if no pattern
        matches.  Multiple patterns may fire on the same error message
        (e.g. a dependency download failure also looks like a build error),
        so all matching categories are returned.
        """
        if not error_message:
            return set()
        cats = set()
        for pattern, category in ERROR_PATTERNS:
            if pattern.search(error_message):
                cats.add(category)
        return cats

    def evaluate_accuracy(self, resolved_failures):
        evaluations = []
        for f in resolved_failures:
            task_group = self._infer_task_group(f.get('failed_task_name', ''))
            expected_cat = EXPECTED_CATEGORIES.get(task_group)
            actual_cat = f.get('failure_category', '')
            ground_truth_source = 'task_name' if expected_cat else None
            acceptable_cats = {expected_cat} if expected_cat else set()

            if not expected_cat:
                error_cats = self._infer_from_error(f.get('error_message', ''))
                if error_cats:
                    acceptable_cats = set(error_cats)
                    for ec in error_cats:
                        acceptable_cats.update(
                            COMPATIBLE_CATEGORIES.get(ec, set())
                        )
                    expected_cat = sorted(error_cats)[0]
                    ground_truth_source = 'error_pattern'

            verifiable = bool(acceptable_cats)
            if verifiable:
                category_correct = actual_cat in acceptable_cats
            else:
                category_correct = None

            hours_to_resolve = None
            if f.get('build_start_time') and f.get('resolved_at'):
                delta = f['resolved_at'] - f['build_start_time']
                hours_to_resolve = delta.total_seconds() / 3600

            was_quick_fix = (
                hours_to_resolve is not None
                and hours_to_resolve < FAST_RESOLUTION_HOURS
            )
            auto_fix_correct = f.get('can_auto_fix', False) == was_quick_fix

            fix_file_match = False
            recommended = f.get('recommended_files') or []
            if f.get('resolution_commit_sha') and recommended:
                fix_file_match = True

            evaluations.append({
                'component': f.get('component_name', ''),
                'task_group': task_group,
                'failed_task_name': f.get('failed_task_name', ''),
                'expected_category': expected_cat,
                'actual_category': actual_cat,
                'category_correct': category_correct,
                'verifiable': verifiable,
                'ground_truth_source': ground_truth_source,
                'confidence': float(f.get('confidence_score', 0) or 0),
                'can_auto_fix': f.get('can_auto_fix', False),
                'was_quick_fix': was_quick_fix,
                'auto_fix_correct': auto_fix_correct,
                'hours_to_resolve': hours_to_resolve,
                'human_verdict': f.get('human_verdict'),
                'resolution_type': f.get('resolution_type', ''),
                'has_fix_files': fix_file_match,
                'jira_key': f.get('jira_key'),
            })

        return evaluations

    def identify_coverage_gaps(self, application=None):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            app_filter = ""
            params = []
            if application:
                app_filter = "AND application = %s"
                params.append(application)

            cursor.execute("""
                SELECT
                    COALESCE(failed_task_name, 'unknown') as task_name,
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE ai_analyzed) as with_ai
                FROM build_failures
                WHERE is_resolved = TRUE {app_filter}
                GROUP BY task_name
                ORDER BY total DESC
            """.format(app_filter=app_filter), params)

            gaps = []
            for row in cursor.fetchall():
                coverage = row[2] * 100 / max(row[1], 1)
                gaps.append({
                    'task_name': row[0],
                    'total_resolved': row[1],
                    'with_ai': row[2],
                    'coverage_pct': round(coverage, 1),
                })
            return gaps

    def compute_metrics(self, evaluations, coverage_gaps, labeled_metrics=None):
        """Compute aggregate regression metrics.

        Args:
            evaluations: Output of evaluate_accuracy().
            coverage_gaps: Output of identify_coverage_gaps().
            labeled_metrics: Optional output of compute_labeled_metrics().
                When provided, confusion_matrix/macro_f1/F1 per category are
                included in the result without post-construction mutation.
        """
        from analyzers.models import CategoryMetrics, RegressionMetrics

        total = len(evaluations)
        if total == 0:
            return RegressionMetrics(
                total_resolved=0, with_ai_analysis=0, ai_coverage_pct=0.0
            )

        verifiable = [e for e in evaluations if e['verifiable']]
        unverifiable = [e for e in evaluations if not e['verifiable']]
        correct = sum(1 for e in verifiable if e['category_correct'])
        accuracy = correct / len(verifiable) if verifiable else 0

        correct_confs = [e['confidence'] for e in verifiable if e['category_correct']]
        incorrect_confs = [e['confidence'] for e in verifiable if not e['category_correct']]
        avg_correct = sum(correct_confs) / len(correct_confs) if correct_confs else 0
        avg_incorrect = sum(incorrect_confs) / len(incorrect_confs) if incorrect_confs else 0
        calibration = min(1.0, max(0.0, avg_correct - avg_incorrect)) if incorrect_confs else avg_correct

        by_category = {}
        for e in verifiable:
            cat = e['actual_category']
            if cat not in by_category:
                by_category[cat] = CategoryMetrics()
            m = by_category[cat]
            m.total += 1
            if e['category_correct']:
                m.correct += 1
            elif e.get('human_verdict') == 'partial':
                m.partial += 1
            else:
                m.incorrect += 1
            m.avg_confidence = round(
                (m.avg_confidence * (m.total - 1) + e['confidence']) / m.total, 2
            )

        if labeled_metrics:
            for cat, pm in labeled_metrics['per_category'].items():
                if cat not in by_category:
                    by_category[cat] = CategoryMetrics()
                cm = by_category[cat]
                cm.precision = pm['precision']
                cm.recall = pm['recall']
                cm.f1 = pm['f1']

        auto_fix_evals = [e for e in evaluations if e['hours_to_resolve'] is not None]
        auto_fix_correct = sum(1 for e in auto_fix_evals if e['auto_fix_correct'])
        auto_fix_accuracy = auto_fix_correct / len(auto_fix_evals) if auto_fix_evals else 0

        total_resolved_all = sum(g['total_resolved'] for g in coverage_gaps)
        total_with_ai = sum(g['with_ai'] for g in coverage_gaps)
        gap_groups = [g['task_name'] for g in coverage_gaps if g['coverage_pct'] < 5]

        true_labels = [
            e['expected_category'] for e in verifiable if e.get('expected_category')
        ]
        null_accuracy = compute_null_model_accuracy(true_labels)

        improvements = self._suggest_improvements(
            verifiable, unverifiable, coverage_gaps, by_category,
        )

        return RegressionMetrics(
            total_resolved=total_resolved_all,
            with_ai_analysis=total_with_ai,
            ai_coverage_pct=round(total_with_ai * 100 / max(total_resolved_all, 1), 1),
            accuracy=round(accuracy, 2),
            calibration_score=round(calibration, 2),
            by_category=by_category,
            auto_fix_accuracy=round(auto_fix_accuracy, 2),
            coverage_gaps=gap_groups,
            improvements=improvements,
            verifiable_count=len(verifiable),
            unverifiable_count=len(unverifiable),
            null_model_accuracy=round(null_accuracy, 2),
            confusion_matrix=labeled_metrics['confusion_matrix'] if labeled_metrics else {},
            macro_f1=labeled_metrics['macro_f1'] if labeled_metrics else 0.0,
            labeled_count=labeled_metrics['labeled_count'] if labeled_metrics else 0,
        )

    def _suggest_improvements(self, verifiable, unverifiable, coverage_gaps, by_category):
        improvements = []

        if unverifiable:
            improvements.append(
                '{} evaluations excluded (no ground truth) — populate '
                'failed_task_name in build records to improve coverage'.format(
                    len(unverifiable)
                )
            )

        for cat, m in sorted(by_category.items(), key=lambda x: x[1].avg_confidence):
            if m.avg_confidence < 0.70 and m.total >= 3:
                improvements.append(
                    'Low confidence for {}: {:.0f}% avg on {} failures '
                    '— improve prompt patterns for this category'.format(
                        cat, m.avg_confidence * 100, m.total
                    )
                )

        catch_all_count = sum(
            m.total for cat, m in by_category.items()
            if cat in ('config_error', 'infrastructure')
        )
        if catch_all_count > 5:
            improvements.append(
                '{} failures use catch-all categories (config_error/infrastructure) '
                '— many should be more specific'.format(catch_all_count)
            )

        auto_fix_marked = sum(1 for e in verifiable if e.get('can_auto_fix'))
        quick_fixes = sum(1 for e in verifiable if e.get('was_quick_fix'))
        if quick_fixes > auto_fix_marked * 2 and quick_fixes > 5:
            improvements.append(
                'Auto-fix under-marked: {} failures resolved quickly but only {} marked '
                'auto-fixable — update can_auto_fix heuristics'.format(
                    quick_fixes, auto_fix_marked
                )
            )

        for g in coverage_gaps:
            if g['coverage_pct'] == 0 and g['total_resolved'] > 10:
                improvements.append(
                    'Zero AI coverage for task {} ({} resolved failures) '
                    '— analyzer never ran on this task type'.format(
                        g['task_name'], g['total_resolved']
                    )
                )

        return improvements

    def compute_labeled_metrics(self, labeled_failures):
        """Compute confusion matrix, F1, precision, recall from ML-labeled data.

        Uses ml_training_labels as ground truth — more reliable than heuristic
        task/error pattern matching, but requires labels to have been generated
        via `ic ai label-training-data --backfill`.

        Args:
            labeled_failures: Output of get_labeled_with_analysis().

        Returns:
            Dict with keys: confusion_matrix, per_category, macro_f1, labeled_count.
        """
        if not labeled_failures:
            return {
                'confusion_matrix': {},
                'per_category': {},
                'macro_f1': 0.0,
                'labeled_count': 0,
            }

        matrix = build_confusion_matrix(labeled_failures)
        per_category = compute_per_category_f1(matrix)
        macro = compute_macro_f1(per_category)

        return {
            'confusion_matrix': matrix,
            'per_category': per_category,
            'macro_f1': macro,
            'labeled_count': len(labeled_failures),
        }

    def run(self, application=None, limit=50, verbose=False,
            min_label_confidence=0.7):
        logger.info("=" * 70)
        logger.info("Build Failure AI Regression Testing")
        logger.info("=" * 70)

        resolved = self.get_resolved_with_analysis(application=application, limit=limit)
        logger.info("Found %d resolved build failures with AI analysis", len(resolved))

        if not resolved:
            return {'analyzed': False, 'reason': 'no_resolved_with_analysis'}

        evaluations = self.evaluate_accuracy(resolved)
        coverage_gaps = self.identify_coverage_gaps(application=application)

        labeled = self.get_labeled_with_analysis(
            application=application,
            min_label_confidence=min_label_confidence,
            limit=limit * 4,
        )
        labeled_metrics = None
        if labeled:
            logger.info("Found %d ML-labeled failures (min_conf=%.2f)",
                        len(labeled), min_label_confidence)
            labeled_metrics = self.compute_labeled_metrics(labeled)
        else:
            logger.debug("No ML-labeled failures found — skipping labeled evaluation")

        metrics = self.compute_metrics(evaluations, coverage_gaps,
                                       labeled_metrics=labeled_metrics)

        if verbose:
            for e in evaluations:
                if not e['verifiable']:
                    status = 'SKIP'
                elif e['category_correct']:
                    status = 'OK'
                else:
                    status = 'WRONG'
                src = e.get('ground_truth_source', '?')
                logger.info(
                    "  [%s] %s: expected %s, got %s (conf %.2f, src=%s)",
                    status, e['component'],
                    e['expected_category'], e['actual_category'],
                    e['confidence'], src,
                )

        return {
            'analyzed': True,
            'metrics': metrics.model_dump(),
            'evaluations_count': len(evaluations),
            'coverage_gaps': coverage_gaps,
        }
