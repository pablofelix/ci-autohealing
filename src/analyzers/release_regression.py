"""Regression testing for the Release Failure AI analyzer.

Uses release analyses to measure category distribution, confidence
calibration, and identify patterns in release failure diagnosis.
"""

from logger import setup_logger
from repositories.ai_analysis_repository import AIAnalysisRepository
from repositories.connection import DatabaseConnection

logger = setup_logger(__name__)

RELEASE_CATEGORIES = {
    'unmapped_image', 'rpa_mapping_typo', 'cross_product_dependency',
    'missing_ec_exception', 'build_artifact_missing', 'validation_error',
    'publish_failure', 'access_denied', 'infrastructure',
}

TRANSIENT_CATEGORIES = {'infrastructure', 'build_artifact_missing'}

ACTIONABLE_CATEGORIES = {
    'unmapped_image', 'rpa_mapping_typo', 'cross_product_dependency',
    'missing_ec_exception',
}


class ReleaseRegressionTester:
    """Tests the release analyzer against historical release analyses."""

    def __init__(self, config, db=None, ai_repo=None):
        self.config = config
        if db is None:
            db = DatabaseConnection(config.db)
        self.db = db
        self.ai_repo = ai_repo or AIAnalysisRepository(db)

    def get_release_analyses(self, limit=50):
        return self.ai_repo.get_release_analyses(limit=limit)

    def evaluate_quality(self, analyses):
        evaluations = []
        for a in analyses:
            category = a.get('failure_category', '')
            valid_category = category in RELEASE_CATEGORIES
            confidence = float(a.get('confidence_score', 0) or 0)

            has_root_cause = bool(a.get('root_cause', '').strip())
            has_fix = bool(a.get('recommended_fix', '').strip())
            has_files = bool(a.get('recommended_files'))

            raw_json = a.get('analysis_json') or {}
            if isinstance(raw_json, list) and raw_json:
                analysis_json = raw_json[0].get('input', {}) if isinstance(raw_json[0], dict) else {}
            elif isinstance(raw_json, dict):
                analysis_json = raw_json
            else:
                analysis_json = {}
            has_affected_images = bool(analysis_json.get('affected_images'))
            has_owner_team = bool(analysis_json.get('owner_team'))
            has_fix_action = bool(analysis_json.get('fix_action_type'))

            completeness_score = sum([
                has_root_cause,
                has_fix,
                has_files,
                has_affected_images,
                has_owner_team,
                has_fix_action,
            ]) / 6.0

            evaluations.append({
                'release_name': a.get('release_name', ''),
                'category': category,
                'valid_category': valid_category,
                'confidence': confidence,
                'has_root_cause': has_root_cause,
                'has_fix': has_fix,
                'has_files': has_files,
                'has_affected_images': has_affected_images,
                'has_owner_team': has_owner_team,
                'has_fix_action': has_fix_action,
                'completeness_score': round(completeness_score, 2),
                'human_verdict': a.get('human_verdict'),
                'is_actionable': category in ACTIONABLE_CATEGORIES,
                'is_transient': category in TRANSIENT_CATEGORIES,
            })

        return evaluations

    def compute_metrics(self, evaluations):
        from analyzers.models import CategoryMetrics, RegressionMetrics

        total = len(evaluations)
        if total == 0:
            return RegressionMetrics(
                total_resolved=0, with_ai_analysis=0, ai_coverage_pct=0.0
            )

        valid_cats = sum(1 for e in evaluations if e['valid_category'])
        accuracy = valid_cats / total

        confidences = [e['confidence'] for e in evaluations]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0

        by_category = {}
        for e in evaluations:
            cat = e['category']
            if cat not in by_category:
                by_category[cat] = CategoryMetrics()
            m = by_category[cat]
            m.total += 1
            if e['valid_category']:
                m.correct += 1
            else:
                m.incorrect += 1
            m.avg_confidence = round(
                (m.avg_confidence * (m.total - 1) + e['confidence']) / m.total, 2
            )

        avg_completeness = sum(e['completeness_score'] for e in evaluations) / total

        improvements = self._suggest_improvements(evaluations, by_category)

        return RegressionMetrics(
            total_resolved=total,
            with_ai_analysis=total,
            ai_coverage_pct=100.0,
            accuracy=round(accuracy, 2),
            calibration_score=round(avg_confidence, 2),
            by_category=by_category,
            auto_fix_accuracy=round(avg_completeness, 2),
            coverage_gaps=[],
            improvements=improvements,
        )

    def _suggest_improvements(self, evaluations, by_category):
        improvements = []

        for cat, m in sorted(by_category.items(), key=lambda x: x[1].avg_confidence):
            if m.avg_confidence < 0.70 and m.total >= 2:
                improvements.append(
                    'Low confidence for {}: {:.0f}% avg on {} analyses '
                    '— improve release prompt patterns'.format(
                        cat, m.avg_confidence * 100, m.total
                    )
                )

        incomplete = [e for e in evaluations if e['completeness_score'] < 0.5]
        if len(incomplete) > total_third(evaluations):
            improvements.append(
                '{}/{} analyses are incomplete (missing affected_images, '
                'owner_team, or fix_action) — enrich release analysis tool schema'.format(
                    len(incomplete), len(evaluations)
                )
            )

        no_files = sum(1 for e in evaluations if not e['has_files'] and e['is_actionable'])
        if no_files > 2:
            improvements.append(
                '{} actionable analyses lack recommended_files — '
                'improve file recommendation for release failures'.format(no_files)
            )

        return improvements

    def run(self, limit=50, verbose=False):
        logger.info("=" * 70)
        logger.info("Release Failure AI Regression Testing")
        logger.info("=" * 70)

        analyses = self.get_release_analyses(limit=limit)
        logger.info("Found %d release analyses", len(analyses))

        if not analyses:
            return {'analyzed': False, 'reason': 'no_release_analyses'}

        evaluations = self.evaluate_quality(analyses)
        metrics = self.compute_metrics(evaluations)

        if verbose:
            for e in evaluations:
                logger.info(
                    "  [%.0f%%] %s: %s (conf %.2f, completeness %.0f%%)",
                    e['completeness_score'] * 100, e['release_name'],
                    e['category'], e['confidence'], e['completeness_score'] * 100
                )

        return {
            'analyzed': True,
            'metrics': metrics.model_dump(),
            'evaluations_count': len(evaluations),
        }


def total_third(items):
    return max(1, len(items) // 3)
