"""Regression testing for the Onboarding AI analyzer.

Uses completed onboardings as ground truth to measure analyzer accuracy,
identify coverage gaps, and suggest improvements to the analyzer and
onboarding infrastructure.
"""

from logger import setup_logger

logger = setup_logger(__name__)

CATEGORY_MAPPING = {
    'retry_storm': 'automation_stuck',
    'branch_exists': 'branch_conflict',
    'cluster_connectivity': 'infrastructure_issue',
    'ci_environment': 'infrastructure_issue',
    'component_not_found': 'missing_prerequisite',
    'workflow_dispatch': 'automation_stuck',
    'dockerfile_missing': 'missing_prerequisite',
    'vendor_inconsistency': 'configuration_error',
    'edit_yaml_error': 'configuration_error',
    'attachment_missing': 'missing_prerequisite',
    'http_422': 'infrastructure_issue',
    'pr_merge_conflict': 'branch_conflict',
    'build_failure_post_onboarding': 'first_build_failing',
    'krd_version_missing': 'configuration_error',
}

SLOW_RESOLUTION_STEPS = {'operator_pr', 'bundle_integration', 'krd_mr', 'tekton_pr'}

FAST_RESOLUTION_HOURS = 72


class OnboardingRegressionTester:
    """Tests the onboarding analyzer against completed onboardings."""

    def __init__(self, config):
        self.config = config

    def get_completed_onboardings(self, application=None):
        from api.routes.onboarding import get_onboarding_status
        app = application or self.config.application_name
        all_data = get_onboarding_status(app)
        if not all_data or all_data.get('error'):
            return []

        completed = []
        for comp in all_data.get('components', []):
            progress = comp.get('progress', 0)
            if progress >= 90:
                completed.append(comp)
        return completed

    def get_component_details(self, component, application=None):
        from api.routes.onboarding import get_component_onboarding
        app = application or self.config.application_name
        return get_component_onboarding(app, component)

    def evaluate_component(self, component_data, analyzer=None):
        """Evaluate analyzer accuracy for a single completed onboarding."""
        component = component_data.get('component', '?')
        evaluation = {
            'component': component,
            'progress': component_data.get('progress', 0),
            'had_errors': False,
            'error_categories_actual': [],
            'stuck_steps_actual': [],
            'analysis_available': False,
            'analysis_category': None,
            'analysis_confidence': 0,
            'category_correct': None,
            'fix_useful': None,
        }

        bot_analysis = component_data.get('bot_error_analysis', {})
        if bot_analysis and bot_analysis.get('has_errors'):
            evaluation['had_errors'] = True
            for cat in bot_analysis.get('error_categories', []):
                evaluation['error_categories_actual'].append(cat['category'])
            stuck = bot_analysis.get('stuck_steps', {})
            evaluation['stuck_steps_actual'] = list(stuck.keys())

        if analyzer:
            try:
                result = analyzer.analyze(component_data)
                evaluation['analysis_available'] = True
                evaluation['analysis_category'] = result.get('failure_category')
                evaluation['analysis_confidence'] = result.get('confidence_score', 0)
                evaluation['analysis_fix'] = result.get('recommended_fix', '')
                evaluation['analysis_can_auto_fix'] = result.get('can_auto_fix', False)

                expected = self._infer_expected_category(evaluation)
                if expected and evaluation['analysis_category']:
                    evaluation['category_correct'] = (
                        evaluation['analysis_category'] == expected
                    )
                evaluation['expected_category'] = expected
            except Exception as e:
                logger.warning("Analysis failed for %s: %s", component, e)
                evaluation['analysis_error'] = str(e)

        return evaluation

    def _infer_expected_category(self, evaluation):
        """Infer the expected category from actual error data."""
        error_cats = evaluation.get('error_categories_actual', [])
        if not error_cats:
            return None

        votes = {}
        for ec in error_cats:
            expected = CATEGORY_MAPPING.get(ec)
            if expected:
                votes[expected] = votes.get(expected, 0) + 1

        if votes:
            return max(votes, key=votes.get)
        return None

    def compute_metrics(self, evaluations):
        total = len(evaluations)
        if total == 0:
            return {
                'total_completed': 0,
                'with_errors': 0,
                'with_analysis': 0,
                'accuracy': 0,
                'coverage_pct': 0,
            }

        with_errors = sum(1 for e in evaluations if e['had_errors'])
        with_analysis = sum(1 for e in evaluations if e['analysis_available'])
        evaluated = [e for e in evaluations if e.get('category_correct') is not None]
        correct = sum(1 for e in evaluated if e['category_correct'])
        accuracy = correct / len(evaluated) if evaluated else 0

        confidences_correct = [
            e['analysis_confidence'] for e in evaluated if e['category_correct']
        ]
        confidences_wrong = [
            e['analysis_confidence'] for e in evaluated if not e['category_correct']
        ]
        avg_correct = (
            sum(confidences_correct) / len(confidences_correct)
            if confidences_correct else 0
        )
        avg_wrong = (
            sum(confidences_wrong) / len(confidences_wrong)
            if confidences_wrong else 0
        )
        calibration = (
            min(1.0, max(0.0, avg_correct - avg_wrong))
            if confidences_wrong else avg_correct
        )

        error_cat_counts = {}
        for e in evaluations:
            for cat in e.get('error_categories_actual', []):
                error_cat_counts[cat] = error_cat_counts.get(cat, 0) + 1

        step_counts = {}
        for e in evaluations:
            for step in e.get('stuck_steps_actual', []):
                step_counts[step] = step_counts.get(step, 0) + 1

        return {
            'total_completed': total,
            'with_errors': with_errors,
            'with_analysis': with_analysis,
            'evaluated': len(evaluated),
            'correct': correct,
            'accuracy': round(accuracy, 2),
            'calibration_score': round(calibration, 2),
            'coverage_pct': round(with_analysis * 100 / max(total, 1), 1),
            'error_categories': error_cat_counts,
            'stuck_steps': step_counts,
        }

    def suggest_improvements(self, evaluations, metrics):
        improvements = []

        wrong = [e for e in evaluations
                 if e.get('category_correct') is False]
        if wrong:
            wrong_cats = {}
            for e in wrong:
                cat = e.get('analysis_category', 'unknown')
                wrong_cats[cat] = wrong_cats.get(cat, 0) + 1
            for cat, count in sorted(wrong_cats.items(), key=lambda x: -x[1]):
                improvements.append(
                    'Category "{}" misclassified {} times — review prompt '
                    'patterns for this category'.format(cat, count)
                )

        error_cats = metrics.get('error_categories', {})
        for ec, count in sorted(error_cats.items(), key=lambda x: -x[1]):
            if ec not in CATEGORY_MAPPING and count >= 3:
                improvements.append(
                    'Error pattern "{}" seen {} times has no category mapping '
                    '— add to CATEGORY_MAPPING in onboarding_regression.py'.format(
                        ec, count
                    )
                )

        stuck_steps = metrics.get('stuck_steps', {})
        for step, count in sorted(stuck_steps.items(), key=lambda x: -x[1]):
            if count >= 3:
                improvements.append(
                    'Step "{}" stuck {} times — consider adding auto-retry '
                    'or better error handling in the automation bot'.format(
                        step, count
                    )
                )

        no_error_comps = [e for e in evaluations if not e['had_errors']]
        if len(no_error_comps) > len(evaluations) * 0.7:
            improvements.append(
                '{:.0f}% of completed onboardings had no bot errors — '
                'analyzer may be unnecessary for healthy components; '
                'focus on the {} that did have errors'.format(
                    len(no_error_comps) * 100 / max(len(evaluations), 1),
                    metrics.get('with_errors', 0)
                )
            )

        auto_fix_pct = sum(
            1 for e in evaluations if e.get('analysis_can_auto_fix')
        ) * 100 / max(len(evaluations), 1)
        if auto_fix_pct < 20 and metrics.get('with_errors', 0) > 5:
            improvements.append(
                'Only {:.0f}% marked auto-fixable but many errors are retriable '
                '— review can_auto_fix criteria for automation_stuck category'.format(
                    auto_fix_pct
                )
            )

        return improvements

    def run(self, application=None, limit=20, verbose=False, use_llm=False):
        logger.info("=" * 70)
        logger.info("Onboarding AI Regression Testing")
        logger.info("=" * 70)

        completed = self.get_completed_onboardings(application=application)
        logger.info("Found %d completed/near-complete onboardings", len(completed))

        if not completed:
            return {
                'analyzed': False,
                'reason': 'no_completed_onboardings',
            }

        completed = completed[:limit]

        analyzer = None
        if use_llm:
            try:
                from analyzers.onboarding_analyzer import OnboardingAnalyzer
                from clients.llm_provider import create_llm_provider
                llm = create_llm_provider(self.config.llm)
                analyzer = OnboardingAnalyzer(llm)
            except Exception as e:
                logger.warning("Could not create analyzer: %s", e)

        evaluations = []
        for comp in completed:
            comp_name = comp.get('name', '')
            if not comp_name:
                continue
            detail = self.get_component_details(comp_name, application)
            if detail and not detail.get('error'):
                ev = self.evaluate_component(detail, analyzer=analyzer)
                evaluations.append(ev)
                if verbose:
                    status = ''
                    if ev.get('category_correct') is True:
                        status = 'OK'
                    elif ev.get('category_correct') is False:
                        status = 'WRONG'
                    elif ev['had_errors']:
                        status = 'NO_ANALYSIS'
                    else:
                        status = 'CLEAN'
                    logger.info(
                        "  [%s] %s: errors=%s stuck=%s",
                        status, comp_name,
                        ','.join(ev.get('error_categories_actual', [])) or 'none',
                        ','.join(ev.get('stuck_steps_actual', [])) or 'none',
                    )

        metrics = self.compute_metrics(evaluations)
        improvements = self.suggest_improvements(evaluations, metrics)

        return {
            'analyzed': True,
            'metrics': metrics,
            'evaluations_count': len(evaluations),
            'improvements': improvements,
        }
