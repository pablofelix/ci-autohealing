"""Regression testing for the Conforma AI analyzer.

Uses resolved violations as ground truth to measure analyzer accuracy,
identify coverage gaps, and suggest improvements to prompts and catalog.
"""

import re

from logger import setup_logger
from repositories.ai_analysis_repository import AIAnalysisRepository
from repositories.connection import DatabaseConnection

logger = setup_logger(__name__)

RULE_PATTERNS = {
    'hermetic_task': re.compile(r'hermetic_task', re.I),
    'trusted_task': re.compile(r'trusted_task', re.I),
    'source_image': re.compile(r'source_image', re.I),
    'labels': re.compile(r'labels\.|required_labels', re.I),
    'fips': re.compile(r'fips', re.I),
    'slsa': re.compile(r'slsa', re.I),
    'sbom': re.compile(r'sbom|disallowed_package', re.I),
    'rpm': re.compile(r'rpm_', re.I),
    'deprecated': re.compile(r'deprecated', re.I),
    'signature': re.compile(r'signature|attestation', re.I),
    'snyk': re.compile(r'snyk', re.I),
}

EXPECTED_CATEGORIES = {
    'hermetic_task': 'policy_hermetic_build',
    'trusted_task': 'policy_untrusted_image',
    'source_image': 'policy_source_image',
    'labels': 'policy_labels',
    'fips': 'policy_fips_check',
    'slsa': 'policy_slsa_provenance',
    'sbom': 'policy_package_source',
    'rpm': 'policy_rpm_repository',
    'deprecated': 'policy_deprecated_task',
    'signature': 'policy_signing_key',
    'snyk': 'policy_snyk_error',
}

TRANSIENT_RULES = {'trusted_task', 'slsa', 'signature', 'snyk', 'fips'}

FAST_RESOLUTION_HOURS = 48


class ConformaRegressionTester:
    """Tests the conforma analyzer against resolved violations."""

    def __init__(self, config, db=None, ai_repo=None):
        self.config = config
        if db is None:
            db = DatabaseConnection(config.db)
        self.db = db
        self.ai_repo = ai_repo or AIAnalysisRepository(db)

    def get_resolved_with_analysis(self, application=None, limit=50):
        return self.ai_repo.get_resolved_conforma_with_analysis(
            application=application, limit=limit
        )

    def _infer_rule_group(self, violation_summary):
        if not violation_summary:
            return 'other'
        for group, pattern in RULE_PATTERNS.items():
            if pattern.search(violation_summary):
                return group
        return 'other'

    def evaluate_accuracy(self, resolved_violations):
        evaluations = []
        for v in resolved_violations:
            rule_group = self._infer_rule_group(v.get('violation_summary', ''))
            expected_cat = EXPECTED_CATEGORIES.get(rule_group)
            actual_cat = v.get('failure_category', '')

            category_correct = (
                actual_cat == expected_cat if expected_cat
                else actual_cat not in ('config_error', 'infrastructure')
            )

            hours_to_resolve = None
            if v.get('first_detected_at') and v.get('resolved_at'):
                delta = v['resolved_at'] - v['first_detected_at']
                hours_to_resolve = delta.total_seconds() / 3600

            was_quick_fix = (
                hours_to_resolve is not None
                and hours_to_resolve < FAST_RESOLUTION_HOURS
            )
            auto_fix_correct = v.get('can_auto_fix', False) == was_quick_fix

            evaluations.append({
                'component': v.get('component_name', ''),
                'rule_group': rule_group,
                'expected_category': expected_cat,
                'actual_category': actual_cat,
                'category_correct': category_correct,
                'confidence': float(v.get('confidence_score', 0) or 0),
                'can_auto_fix': v.get('can_auto_fix', False),
                'was_quick_fix': was_quick_fix,
                'auto_fix_correct': auto_fix_correct,
                'hours_to_resolve': hours_to_resolve,
                'human_verdict': v.get('human_verdict'),
                'jira_key': v.get('jira_key'),
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
                    CASE
                        WHEN violation_summary LIKE '%%hermetic_task%%' THEN 'hermetic_task'
                        WHEN violation_summary LIKE '%%trusted_task%%' THEN 'trusted_task'
                        WHEN violation_summary LIKE '%%source_image%%' THEN 'source_image'
                        WHEN violation_summary LIKE '%%labels%%' THEN 'labels'
                        WHEN violation_summary LIKE '%%fips%%' THEN 'fips'
                        WHEN violation_summary LIKE '%%slsa%%' THEN 'slsa'
                        WHEN violation_summary LIKE '%%sbom%%' THEN 'sbom'
                        WHEN violation_summary LIKE '%%rpm%%' THEN 'rpm'
                        WHEN violation_summary LIKE '%%deprecated%%' THEN 'deprecated'
                        WHEN violation_summary LIKE '%%signature%%' THEN 'signature'
                        WHEN violation_summary LIKE '%%snyk%%' THEN 'snyk'
                        ELSE 'other'
                    END as rule_group,
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE ai_analyzed) as with_ai
                FROM conforma_results
                WHERE is_resolved = TRUE {app_filter}
                GROUP BY rule_group
                ORDER BY total DESC
            """.format(app_filter=app_filter), params)

            gaps = []
            for row in cursor.fetchall():
                coverage = row[2] * 100 / max(row[1], 1)
                gaps.append({
                    'rule_group': row[0],
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
        gap_groups = [g['rule_group'] for g in coverage_gaps if g['coverage_pct'] < 5]

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
            if m.avg_confidence < 0.80 and m.total >= 3:
                improvements.append(
                    'Low confidence for {}: {:.0f}% avg on {} violations '
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
                '{} violations use catch-all categories (config_error/infrastructure) '
                '— many should be more specific'.format(catch_all_count)
            )

        auto_fix_marked = sum(1 for e in evaluations if e.get('can_auto_fix'))
        quick_fixes = sum(1 for e in evaluations if e.get('was_quick_fix'))
        if quick_fixes > auto_fix_marked * 2 and quick_fixes > 5:
            improvements.append(
                'Auto-fix under-marked: {} violations resolved quickly but only {} marked '
                'auto-fixable — update can_auto_fix heuristics'.format(
                    quick_fixes, auto_fix_marked
                )
            )

        for g in coverage_gaps:
            if g['coverage_pct'] == 0 and g['total_resolved'] > 10:
                improvements.append(
                    'Zero AI coverage for {} ({} resolved violations) '
                    '— analyzer never ran on this rule group'.format(
                        g['rule_group'], g['total_resolved']
                    )
                )

        return improvements

    def run(self, application=None, limit=50, verbose=False):
        logger.info("=" * 70)
        logger.info("Conforma AI Regression Testing")
        logger.info("=" * 70)

        resolved = self.get_resolved_with_analysis(application=application, limit=limit)
        logger.info("Found %d resolved violations with AI analysis", len(resolved))

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
                    status, e['component'], e['rule_group'],
                    e['expected_category'], e['actual_category'], e['confidence']
                )

        return {
            'analyzed': True,
            'metrics': metrics.model_dump(),
            'evaluations_count': len(evaluations),
            'coverage_gaps': coverage_gaps,
        }
