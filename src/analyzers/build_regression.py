"""Regression testing for the Build Failure AI analyzer.

Uses resolved build failures as ground truth to measure analyzer accuracy,
identify coverage gaps by task name, and suggest improvements.
"""

import re

from logger import setup_logger
from repositories.ai_analysis_repository import AIAnalysisRepository
from repositories.connection import DatabaseConnection

logger = setup_logger(__name__)

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

    def _infer_task_group(self, failed_task_name):
        if not failed_task_name:
            return 'other'
        for group, pattern in TASK_PATTERNS.items():
            if pattern.search(failed_task_name):
                return group
        return 'other'

    def evaluate_accuracy(self, resolved_failures):
        evaluations = []
        for f in resolved_failures:
            task_group = self._infer_task_group(f.get('failed_task_name', ''))
            expected_cat = EXPECTED_CATEGORIES.get(task_group)
            actual_cat = f.get('failure_category', '')

            category_correct = (
                actual_cat == expected_cat if expected_cat
                else actual_cat not in ('config_error', 'infrastructure')
            )

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

    def compute_metrics(self, evaluations, coverage_gaps):
        from analyzers.models import CategoryMetrics, RegressionMetrics

        total = len(evaluations)
        if total == 0:
            return RegressionMetrics(
                total_resolved=0, with_ai_analysis=0, ai_coverage_pct=0.0
            )

        correct = sum(1 for e in evaluations if e['category_correct'])
        accuracy = correct / total

        correct_confs = [e['confidence'] for e in evaluations if e['category_correct']]
        incorrect_confs = [e['confidence'] for e in evaluations if not e['category_correct']]
        avg_correct = sum(correct_confs) / len(correct_confs) if correct_confs else 0
        avg_incorrect = sum(incorrect_confs) / len(incorrect_confs) if incorrect_confs else 0
        calibration = min(1.0, max(0.0, avg_correct - avg_incorrect)) if incorrect_confs else avg_correct

        by_category = {}
        for e in evaluations:
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

        auto_fix_evals = [e for e in evaluations if e['hours_to_resolve'] is not None]
        auto_fix_correct = sum(1 for e in auto_fix_evals if e['auto_fix_correct'])
        auto_fix_accuracy = auto_fix_correct / len(auto_fix_evals) if auto_fix_evals else 0

        total_resolved_all = sum(g['total_resolved'] for g in coverage_gaps)
        total_with_ai = sum(g['with_ai'] for g in coverage_gaps)
        gap_groups = [g['task_name'] for g in coverage_gaps if g['coverage_pct'] < 5]

        improvements = self._suggest_improvements(evaluations, coverage_gaps, by_category)

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
        )

    def _suggest_improvements(self, evaluations, coverage_gaps, by_category):
        improvements = []

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

        auto_fix_marked = sum(1 for e in evaluations if e.get('can_auto_fix'))
        quick_fixes = sum(1 for e in evaluations if e.get('was_quick_fix'))
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

    def run(self, application=None, limit=50, verbose=False):
        logger.info("=" * 70)
        logger.info("Build Failure AI Regression Testing")
        logger.info("=" * 70)

        resolved = self.get_resolved_with_analysis(application=application, limit=limit)
        logger.info("Found %d resolved build failures with AI analysis", len(resolved))

        if not resolved:
            return {'analyzed': False, 'reason': 'no_resolved_with_analysis'}

        evaluations = self.evaluate_accuracy(resolved)
        coverage_gaps = self.identify_coverage_gaps(application=application)
        metrics = self.compute_metrics(evaluations, coverage_gaps)

        if verbose:
            for e in evaluations:
                status = 'OK' if e['category_correct'] else 'WRONG'
                logger.info(
                    "  [%s] %s: %s (expected %s, got %s, conf %.2f)",
                    status, e['component'], e['task_group'],
                    e['expected_category'], e['actual_category'], e['confidence']
                )

        return {
            'analyzed': True,
            'metrics': metrics.model_dump(),
            'evaluations_count': len(evaluations),
            'coverage_gaps': coverage_gaps,
        }
