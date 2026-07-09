"""Tests for analyzer Pydantic models and pure-function modules."""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ═══════════════════════════════════════════════════════════════════════
# Pydantic model validation tests
# ═══════════════════════════════════════════════════════════════════════

class TestAnalysisResult:
    def _valid_data(self, **overrides):
        base = {
            'root_cause': 'Missing dependency golang.org/x/net in go.mod',
            'failure_category': 'dependency_issue',
            'confidence_score': 0.85,
            'recommended_fix': 'Add golang.org/x/net v0.24.0 to go.mod',
        }
        base.update(overrides)
        return base

    def test_valid_model(self):
        from analyzers.models import AnalysisResult
        m = AnalysisResult(**self._valid_data())
        assert m.failure_category == 'dependency_issue'
        assert m.confidence_score == 0.85

    def test_confidence_rounding(self):
        from analyzers.models import AnalysisResult
        m = AnalysisResult(**self._valid_data(confidence_score=0.8567))
        assert m.confidence_score == 0.86

    def test_confidence_clamped_low(self):
        from analyzers.models import AnalysisResult
        with pytest.raises((ValueError, TypeError, AttributeError)):
            AnalysisResult(**self._valid_data(confidence_score=-0.1))

    def test_confidence_clamped_high(self):
        from analyzers.models import AnalysisResult
        with pytest.raises((ValueError, TypeError, AttributeError)):
            AnalysisResult(**self._valid_data(confidence_score=1.1))

    def test_root_cause_too_short(self):
        from analyzers.models import AnalysisResult
        with pytest.raises((ValueError, TypeError, AttributeError)):
            AnalysisResult(**self._valid_data(root_cause='short'))

    def test_placeholder_root_cause(self):
        from analyzers.models import AnalysisResult
        with pytest.raises((ValueError, TypeError, AttributeError)):
            AnalysisResult(**self._valid_data(root_cause='N/A'))

    def test_placeholder_fix(self):
        from analyzers.models import AnalysisResult
        with pytest.raises((ValueError, TypeError, AttributeError)):
            AnalysisResult(**self._valid_data(recommended_fix='unknown'))

    def test_invalid_category(self):
        from analyzers.models import AnalysisResult
        with pytest.raises((ValueError, TypeError, AttributeError)):
            AnalysisResult(**self._valid_data(failure_category='not_a_category'))

    def test_files_cleaned(self):
        from analyzers.models import AnalysisResult
        m = AnalysisResult(**self._valid_data(
            recommended_files=['  file.go ', '', '  ', 'Dockerfile']
        ))
        assert m.recommended_files == ['file.go', 'Dockerfile']

    def test_defaults(self):
        from analyzers.models import AnalysisResult
        m = AnalysisResult(**self._valid_data())
        assert m.can_auto_fix is False
        assert m.requires_human_review is True
        assert m.recommended_files == []
        assert m.evidence_references == []

    def test_evidence_reference(self):
        from analyzers.models import AnalysisResult, EvidenceReference
        ref = EvidenceReference(type='doc', url='https://example.com', description='Go module docs')
        m = AnalysisResult(**self._valid_data(evidence_references=[ref]))
        assert len(m.evidence_references) == 1

    def test_evidence_reference_too_short(self):
        from analyzers.models import EvidenceReference
        with pytest.raises((ValueError, TypeError, AttributeError)):
            EvidenceReference(type='doc', description='ab')

    def test_source_transparency(self):
        from analyzers.models import AnalysisResult, SourceTransparency
        st = SourceTransparency(
            sources_consulted=['build logs', 'go.mod'],
            sources_unavailable=['commit diff'],
            limitations=['No access to upstream repo'],
        )
        m = AnalysisResult(**self._valid_data(source_transparency=st))
        assert len(m.source_transparency.sources_consulted) == 2


class TestConformaAnalysisResult:
    def _valid_data(self, **overrides):
        base = {
            'root_cause': 'Image uses unpinned base image from quay.io',
            'failure_category': 'policy_untrusted_image',
            'confidence_score': 0.75,
            'recommended_fix': 'Pin the base image to a specific SHA digest',
        }
        base.update(overrides)
        return base

    def test_valid_model(self):
        from analyzers.models import ConformaAnalysisResult
        m = ConformaAnalysisResult(**self._valid_data())
        assert m.failure_category == 'policy_untrusted_image'

    def test_all_conforma_categories(self):
        from analyzers.models import ConformaAnalysisResult
        categories = [
            'policy_hermetic_build', 'policy_unpinned_task', 'policy_untrusted_image',
            'policy_signing_key', 'policy_package_source', 'policy_rpm_repository',
            'policy_version_label', 'policy_fips_check', 'policy_deprecated_task',
            'policy_deprecated_image', 'policy_slsa_provenance', 'policy_snyk_error',
            'policy_labels', 'policy_sbom_vendor_label', 'policy_cpe_label',
            'policy_source_image', 'config_error', 'infrastructure',
        ]
        for cat in categories:
            m = ConformaAnalysisResult(**self._valid_data(failure_category=cat))
            assert m.failure_category == cat


class TestReleaseAnalysisResult:
    def _valid_data(self, **overrides):
        base = {
            'root_cause': 'Image quay.io/rhoai/foo not mapped in release config',
            'failure_category': 'unmapped_image',
            'confidence_score': 0.9,
            'recommended_fix': 'Add image mapping to release config YAML',
        }
        base.update(overrides)
        return base

    def test_valid_model(self):
        from analyzers.models import ReleaseAnalysisResult
        m = ReleaseAnalysisResult(**self._valid_data())
        assert m.failure_category == 'unmapped_image'
        assert m.fix_action_type == 'investigation_needed'

    def test_with_affected_images(self):
        from analyzers.models import ReleaseAnalysisResult
        m = ReleaseAnalysisResult(**self._valid_data(
            affected_images=['quay.io/img:sha', '  ', 'quay.io/img2:sha']
        ))
        assert len(m.affected_images) == 2

    def test_release_categories(self):
        from analyzers.models import ReleaseAnalysisResult
        for cat in ['unmapped_image', 'rpa_mapping_typo', 'cross_product_dependency',
                     'missing_ec_exception', 'build_artifact_missing', 'validation_error',
                     'publish_failure', 'access_denied', 'infrastructure']:
            m = ReleaseAnalysisResult(**self._valid_data(failure_category=cat))
            assert m.failure_category == cat


class TestOnboardingAnalysisResult:
    def test_valid_model(self):
        from analyzers.models import OnboardingAnalysisResult
        m = OnboardingAnalysisResult(
            root_cause='Automation bot PR is stuck waiting for review',
            failure_category='pr_review_needed',
            confidence_score=0.7,
            recommended_fix='Merge the bot-generated PR to unblock automation',
            blocked_step='component-nudge-pr',
        )
        assert m.failure_category == 'pr_review_needed'
        assert m.blocked_step == 'component-nudge-pr'


class TestScenariosAnalysisResult:
    def test_valid_model(self):
        from analyzers.models import ScenariosAnalysisResult
        m = ScenariosAnalysisResult(
            findings='Disabled ITS scenario my-test not linked to any Jira',
            severity='warning',
            confidence_score=0.8,
            recommendations='Create Jira ticket to track re-enablement',
            issues_count=3,
        )
        assert m.severity == 'warning'
        assert m.issues_count == 3


class TestConfigAnalysisResult:
    def test_valid_model(self):
        from analyzers.models import ConfigAnalysisResult, ConfigFinding
        finding = ConfigFinding(
            title='Expired policy exception',
            severity='critical',
            category='expired_exceptions',
            description='Exception for rule X expired 30 days ago',
            recommendation='Remove expired exception or renew it',
        )
        m = ConfigAnalysisResult(
            findings=[finding],
            overall_severity='critical',
            confidence_score=0.95,
            summary='Configuration audit found 1 critical issue',
        )
        assert len(m.findings) == 1
        assert m.overall_severity == 'critical'

    def test_config_finding_categories(self):
        from analyzers.models import ConfigFinding
        for cat in ['expired_exceptions', 'policy_gap', 'scenario_coverage',
                     'pipeline_config', 'auto_rebuild_candidate', 'rule_catalog_gap']:
            f = ConfigFinding(
                title='Test finding for category',
                severity='info', category=cat,
                description='Testing all category values',
                recommendation='No action needed for test',
            )
            assert f.category == cat


class TestBuildConfigFinding:
    def test_valid_finding(self):
        from analyzers.models import BuildConfigFinding
        f = BuildConfigFinding(
            title='Stale nudge for odh-dashboard',
            severity='warning',
            category='stale_component',
            description='Component building from commits 5 days old',
            recommendation='Fix nudge propagation for this component',
        )
        assert f.category == 'stale_component'
        assert f.fix_action == 'investigation'

    def test_all_categories(self):
        from analyzers.models import BuildConfigFinding
        for cat in ['stale_component', 'recurring_transient', 'quota_bottleneck',
                     'missing_webhook', 'build_chain_broken']:
            f = BuildConfigFinding(
                title='Test finding for category',
                severity='info', category=cat,
                description='Testing all category values',
                recommendation='No action needed for test',
            )
            assert f.category == cat

    def test_invalid_category(self):
        from analyzers.models import BuildConfigFinding
        with pytest.raises((ValueError, TypeError)):
            BuildConfigFinding(
                title='Bad category finding',
                severity='warning', category='not_a_category',
                description='Should fail validation',
                recommendation='This should not be created',
            )

    def test_fix_actions(self):
        from analyzers.models import BuildConfigFinding
        for action in ['rebuild', 'nudge_fix', 'webhook_setup',
                        'quota_request', 'investigation']:
            f = BuildConfigFinding(
                title='Test fix action value',
                severity='info', category='stale_component',
                description='Testing all fix action values',
                recommendation='No action needed for test',
                fix_action=action,
            )
            assert f.fix_action == action

    def test_defaults(self):
        from analyzers.models import BuildConfigFinding
        f = BuildConfigFinding(
            title='Defaults test finding',
            severity='info', category='stale_component',
            description='Testing default values work',
            recommendation='No action needed for test',
        )
        assert f.can_auto_fix is False
        assert f.fix_action == 'investigation'
        assert f.affected_components == []


class TestBuildConfigAnalysisResult:
    def test_valid_result(self):
        from analyzers.models import BuildConfigAnalysisResult, BuildConfigFinding
        finding = BuildConfigFinding(
            title='Stale nudge for component',
            severity='warning', category='stale_component',
            description='Component building from old commits',
            recommendation='Fix nudge propagation',
        )
        m = BuildConfigAnalysisResult(
            findings=[finding],
            overall_severity='warning',
            confidence_score=0.85,
            summary='Build config audit found 1 warning issue',
        )
        assert len(m.findings) == 1
        assert m.overall_severity == 'warning'
        assert m.auto_rebuild_candidates == []

    def test_confidence_rounding(self):
        from analyzers.models import BuildConfigAnalysisResult
        m = BuildConfigAnalysisResult(
            findings=[], overall_severity='info',
            confidence_score=0.8567,
            summary='Build config looks healthy overall',
        )
        assert m.confidence_score == 0.86

    def test_confidence_rejects_above_one(self):
        from analyzers.models import BuildConfigAnalysisResult
        with pytest.raises((ValueError, TypeError)):
            BuildConfigAnalysisResult(
                findings=[], overall_severity='info',
                confidence_score=1.5,
                summary='Should fail validation check',
            )

    def test_confidence_rejects_negative(self):
        from analyzers.models import BuildConfigAnalysisResult
        with pytest.raises((ValueError, TypeError)):
            BuildConfigAnalysisResult(
                findings=[], overall_severity='info',
                confidence_score=-0.1,
                summary='Should fail validation check',
            )

    def test_placeholder_summary_rejected(self):
        from analyzers.models import BuildConfigAnalysisResult
        with pytest.raises((ValueError, TypeError)):
            BuildConfigAnalysisResult(
                findings=[], overall_severity='info',
                confidence_score=0.5,
                summary='N/A',
            )

    def test_empty_findings_valid(self):
        from analyzers.models import BuildConfigAnalysisResult
        m = BuildConfigAnalysisResult(
            findings=[], overall_severity='info',
            confidence_score=0.9,
            summary='No issues found in build config',
        )
        assert m.findings == []

    def test_auto_rebuild_candidates(self):
        from analyzers.models import BuildConfigAnalysisResult
        m = BuildConfigAnalysisResult(
            findings=[], overall_severity='info',
            confidence_score=0.9,
            summary='Build config audit completed successfully',
            auto_rebuild_candidates=['comp-a', 'comp-b'],
        )
        assert len(m.auto_rebuild_candidates) == 2


class TestReleaseConfigFinding:
    def test_valid_finding(self):
        from analyzers.models import ReleaseConfigFinding
        f = ReleaseConfigFinding(
            title='Unresolved conforma violation blocks release',
            severity='critical',
            category='conforma_blocker',
            description='Component has unresolved violations with no exception',
            recommendation='File exception or fix the violation',
        )
        assert f.category == 'conforma_blocker'
        assert f.blocks_release is False
        assert f.fix_action == 'investigation'

    def test_all_categories(self):
        from analyzers.models import ReleaseConfigFinding
        for cat in ['conforma_blocker', 'pcc_cache_stale', 'missing_exception',
                     'sha_drift', 'nightly_broken']:
            f = ReleaseConfigFinding(
                title='Test finding for category',
                severity='info', category=cat,
                description='Testing all category values',
                recommendation='No action needed for test',
            )
            assert f.category == cat

    def test_invalid_category(self):
        from analyzers.models import ReleaseConfigFinding
        with pytest.raises((ValueError, TypeError)):
            ReleaseConfigFinding(
                title='Bad category finding',
                severity='warning', category='invalid_cat',
                description='Should fail validation',
                recommendation='This should not be created',
            )

    def test_blocks_release_flag(self):
        from analyzers.models import ReleaseConfigFinding
        f = ReleaseConfigFinding(
            title='Critical release blocker finding',
            severity='critical', category='conforma_blocker',
            description='This violation will block the release',
            recommendation='Fix it before the release window',
            blocks_release=True,
        )
        assert f.blocks_release is True

    def test_fix_actions(self):
        from analyzers.models import ReleaseConfigFinding
        for action in ['rebuild', 'exception_request', 'pcc_regen',
                        'config_change', 'investigation']:
            f = ReleaseConfigFinding(
                title='Test fix action value',
                severity='info', category='pcc_cache_stale',
                description='Testing all fix action values',
                recommendation='No action needed for test',
                fix_action=action,
            )
            assert f.fix_action == action


class TestReleaseConfigAnalysisResult:
    def test_valid_result(self):
        from analyzers.models import ReleaseConfigAnalysisResult, ReleaseConfigFinding
        finding = ReleaseConfigFinding(
            title='PCC cache is stale',
            severity='warning', category='pcc_cache_stale',
            description='PCC not regenerated after last release',
            recommendation='Trigger PCC regeneration workflow',
        )
        m = ReleaseConfigAnalysisResult(
            findings=[finding],
            overall_severity='warning',
            confidence_score=0.8,
            summary='Release config audit found 1 warning issue',
        )
        assert len(m.findings) == 1
        assert m.release_blockers == []

    def test_confidence_rounding(self):
        from analyzers.models import ReleaseConfigAnalysisResult
        m = ReleaseConfigAnalysisResult(
            findings=[], overall_severity='info',
            confidence_score=0.7777,
            summary='Release config looks healthy overall',
        )
        assert m.confidence_score == 0.78

    def test_confidence_rejects_above_one(self):
        from analyzers.models import ReleaseConfigAnalysisResult
        with pytest.raises((ValueError, TypeError)):
            ReleaseConfigAnalysisResult(
                findings=[], overall_severity='info',
                confidence_score=1.5,
                summary='Should fail validation check',
            )

    def test_placeholder_summary_rejected(self):
        from analyzers.models import ReleaseConfigAnalysisResult
        with pytest.raises((ValueError, TypeError)):
            ReleaseConfigAnalysisResult(
                findings=[], overall_severity='info',
                confidence_score=0.5,
                summary='unknown',
            )

    def test_release_blockers(self):
        from analyzers.models import ReleaseConfigAnalysisResult
        m = ReleaseConfigAnalysisResult(
            findings=[], overall_severity='critical',
            confidence_score=0.9,
            summary='Release has critical blockers identified',
            release_blockers=['comp-a: conforma violation', 'comp-b: SHA drift'],
        )
        assert len(m.release_blockers) == 2

    def test_empty_findings_valid(self):
        from analyzers.models import ReleaseConfigAnalysisResult
        m = ReleaseConfigAnalysisResult(
            findings=[], overall_severity='info',
            confidence_score=0.9,
            summary='No issues found in release config',
        )
        assert m.findings == []


class TestRegressionMetrics:
    def test_valid_model(self):
        from analyzers.models import CategoryMetrics, RegressionMetrics
        m = RegressionMetrics(
            total_resolved=100,
            with_ai_analysis=80,
            ai_coverage_pct=80.0,
            accuracy=0.75,
            by_category={
                'dependency_issue': CategoryMetrics(correct=10, partial=2, total=15),
            },
        )
        assert m.total_resolved == 100
        assert m.by_category['dependency_issue'].correct == 10


# ═══════════════════════════════════════════════════════════════════════
# Evidence hierarchy tests (pure functions)
# ═══════════════════════════════════════════════════════════════════════

class TestEvidenceHierarchy:
    def test_classify_tier1(self):
        from analyzers.evidence_hierarchy import classify_evidence
        for ev in ['commit_diff', 'build_logs', 'stack_trace', 'test_output', 'violation_output']:
            assert classify_evidence(ev) == 1

    def test_classify_tier2(self):
        from analyzers.evidence_hierarchy import classify_evidence
        for ev in ['tekton_config', 'dockerfile', 'ec_policy', 'nudging_yaml', 'pipeline_yaml']:
            assert classify_evidence(ev) == 2

    def test_classify_tier3(self):
        from analyzers.evidence_hierarchy import classify_evidence
        for ev in ['build_history', 'error_pattern', 'keyword_match', 'nightly_trend']:
            assert classify_evidence(ev) == 3

    def test_classify_tier4(self):
        from analyzers.evidence_hierarchy import classify_evidence
        for ev in ['assumption', 'inference', 'speculation']:
            assert classify_evidence(ev) == 4

    def test_classify_unknown_defaults_tier3(self):
        from analyzers.evidence_hierarchy import classify_evidence
        assert classify_evidence('some_random_thing') == 3

    def test_classify_normalizes_input(self):
        from analyzers.evidence_hierarchy import classify_evidence
        assert classify_evidence('Build-Logs') == 1
        assert classify_evidence('COMMIT DIFF') == 1
        assert classify_evidence('ec-policy') == 2

    def test_adjust_confidence_no_evidence(self):
        from analyzers.evidence_hierarchy import adjust_confidence
        assert adjust_confidence(0.9, []) == 0.55

    def test_adjust_confidence_tier1(self):
        from analyzers.evidence_hierarchy import adjust_confidence
        result = adjust_confidence(0.9, [1])
        assert result <= 0.95
        assert result == 0.9 * 1.0

    def test_adjust_confidence_tier4_caps(self):
        from analyzers.evidence_hierarchy import adjust_confidence
        result = adjust_confidence(0.95, [4])
        assert result <= 0.55

    def test_adjust_confidence_mixed_tiers(self):
        from analyzers.evidence_hierarchy import adjust_confidence
        result = adjust_confidence(0.9, [1, 3])
        avg_w = (1.0 + 0.65) / 2
        assert result == min(0.9 * avg_w, 0.95)

    def test_adjust_confidence_multiple_tier2(self):
        from analyzers.evidence_hierarchy import adjust_confidence
        result = adjust_confidence(1.0, [2, 2, 2])
        assert result == 0.85

    def test_validate_hypothesis_valid(self):
        from analyzers.evidence_hierarchy import validate_hypothesis
        h = validate_hypothesis({
            'hypothesis': 'Missing go dep',
            'category': 'dependency_issue',
            'confidence': 0.8,
            'supporting_evidence': ['go.mod missing entry'],
        })
        assert h is not None
        assert h['confidence'] == 0.8
        assert h['category'] == 'dependency_issue'

    def test_validate_hypothesis_clamps_confidence(self):
        from analyzers.evidence_hierarchy import validate_hypothesis
        h = validate_hypothesis({
            'hypothesis': 'test',
            'category': 'test',
            'confidence': 1.5,
        })
        assert h['confidence'] == 1.0

    def test_validate_hypothesis_negative_confidence(self):
        from analyzers.evidence_hierarchy import validate_hypothesis
        h = validate_hypothesis({
            'hypothesis': 'test',
            'category': 'test',
            'confidence': -0.5,
        })
        assert h['confidence'] == 0.0

    def test_validate_hypothesis_non_numeric_confidence(self):
        from analyzers.evidence_hierarchy import validate_hypothesis
        h = validate_hypothesis({
            'hypothesis': 'test',
            'category': 'test',
            'confidence': 'high',
        })
        assert h['confidence'] == 0.5

    def test_validate_hypothesis_missing_required(self):
        from analyzers.evidence_hierarchy import validate_hypothesis
        assert validate_hypothesis({'hypothesis': 'test'}) is None
        assert validate_hypothesis({'category': 'test'}) is None
        assert validate_hypothesis({}) is None

    def test_validate_hypothesis_not_dict(self):
        from analyzers.evidence_hierarchy import validate_hypothesis
        assert validate_hypothesis("string") is None
        assert validate_hypothesis(None) is None
        assert validate_hypothesis(42) is None

    def test_tier_names_reverse_mapping(self):
        from analyzers.evidence_hierarchy import TIER_NAMES
        assert TIER_NAMES['Direct Artifact'] == 1
        assert TIER_NAMES['Configuration Source'] == 2
        assert TIER_NAMES['Historical Pattern'] == 3
        assert TIER_NAMES['Inference'] == 4


# ═══════════════════════════════════════════════════════════════════════
# Confidence features tests (pure functions)
# ═══════════════════════════════════════════════════════════════════════

class TestFeatureVector:
    def test_default_all_false(self):
        from analyzers.confidence_features import FeatureVector
        fv = FeatureVector()
        assert fv.active_count() == 0
        assert fv.total_count() == 10

    def test_active_features(self):
        from analyzers.confidence_features import FeatureVector
        fv = FeatureVector(has_build_history=True, has_pipeline_logs=True)
        assert fv.active_count() == 2
        assert 'has_build_history' in fv.active_features()
        assert 'has_pipeline_logs' in fv.active_features()

    def test_to_dict(self):
        from analyzers.confidence_features import FeatureVector
        fv = FeatureVector(has_build_history=True)
        d = fv.to_dict()
        assert d['has_build_history'] is True
        assert d['has_pipeline_logs'] is False

    def test_frozen(self):
        from analyzers.confidence_features import FeatureVector
        fv = FeatureVector()
        with pytest.raises((ValueError, TypeError, AttributeError)):
            fv.has_build_history = True


class TestExtractFeatures:
    def test_empty_context(self):
        from analyzers.confidence_features import extract_features
        fv = extract_features(None)
        assert fv.active_count() == 0

    def test_non_dict_context(self):
        from analyzers.confidence_features import extract_features
        fv = extract_features("string")
        assert fv.active_count() == 0

    def test_unknown_analyzer(self):
        from analyzers.confidence_features import extract_features
        fv = extract_features({'some': 'data'}, analyzer_type='unknown')
        assert fv.active_count() == 0

    def test_build_with_history(self):
        from analyzers.confidence_features import extract_features
        ctx = {
            'build_history': [{'status': 'Failed'}, {'status': 'Succeeded'}],
            'build_logs': 'x' * 200,
            'commit_context': {'diff': 'some diff'},
        }
        fv = extract_features(ctx, 'build')
        assert fv.has_build_history is True
        assert fv.has_pipeline_logs is True
        assert fv.has_commit_diff is True
        assert fv.error_in_recent_builds is True

    def test_build_short_logs_ignored(self):
        from analyzers.confidence_features import extract_features
        ctx = {'build_logs': 'short'}
        fv = extract_features(ctx, 'build')
        assert fv.has_pipeline_logs is False

    def test_build_sha_mismatch(self):
        from analyzers.confidence_features import extract_features
        ctx = {'sha_mismatch': True, 'pattern_match': {'rule': 'test'}}
        fv = extract_features(ctx, 'build')
        assert fv.sha_mismatch_confirmed is True
        assert fv.pattern_matched is True

    def test_conforma_features(self):
        from analyzers.confidence_features import extract_features
        ctx = {
            'violations': {'rule1': ['v1', 'v2'], 'rule2': ['v3']},
            'tekton_config': 'some yaml',
            'open_prs': [{'url': 'pr1'}],
        }
        fv = extract_features(ctx, 'conforma')
        assert fv.multiple_violations is True
        assert fv.has_pipeline_logs is True
        assert fv.has_open_prs is True

    def test_conforma_single_violation(self):
        from analyzers.confidence_features import extract_features
        ctx = {'violations': {'rule1': ['v1']}}
        fv = extract_features(ctx, 'conforma')
        assert fv.multiple_violations is False

    def test_conforma_violations_as_list(self):
        from analyzers.confidence_features import extract_features
        ctx = {'violation_details': [{'rule': 'a'}, {'rule': 'b'}]}
        fv = extract_features(ctx, 'conforma')
        assert fv.multiple_violations is True

    def test_release_features(self):
        from analyzers.confidence_features import extract_features
        ctx = {
            'component_build_history': [{'status': 'ok'}],
            'step_report': 'report content',
            'nudge_prs': [{'url': 'pr1'}],
            'nudging_yaml': 'yaml content',
            'triage_items': [{'id': 1}],
        }
        fv = extract_features(ctx, 'release')
        assert fv.has_build_history is True
        assert fv.has_pipeline_logs is True
        assert fv.has_commit_diff is True
        assert fv.nudging_checked is True
        assert fv.has_open_prs is True
        assert fv.has_triage_context is True


# ═══════════════════════════════════════════════════════════════════════
# Calibration tests
# ═══════════════════════════════════════════════════════════════════════

class TestBinPredictions:
    def test_empty_data(self):
        from analyzers.calibration import _bin_predictions
        assert _bin_predictions([]) == []

    def test_single_prediction(self):
        from analyzers.calibration import _bin_predictions
        data = [{'confidence': 0.75, 'accuracy': 1.0}]
        result = _bin_predictions(data)
        assert len(result) == 1
        assert result[0].bucket_min == 0.7
        assert result[0].bucket_max == 0.8
        assert result[0].actual_accuracy == 1.0
        assert result[0].sample_size == 1

    def test_multiple_buckets(self):
        from analyzers.calibration import _bin_predictions
        data = [
            {'confidence': 0.55, 'accuracy': 1.0},
            {'confidence': 0.55, 'accuracy': 0.0},
            {'confidence': 0.85, 'accuracy': 1.0},
        ]
        result = _bin_predictions(data)
        assert len(result) == 2
        bucket_50_60 = [b for b in result if b.bucket_min == 0.5][0]
        assert bucket_50_60.actual_accuracy == 0.5
        assert bucket_50_60.sample_size == 2

    def test_low_confidence_below_buckets(self):
        from analyzers.calibration import _bin_predictions
        data = [{'confidence': 0.1, 'accuracy': 1.0}]
        result = _bin_predictions(data)
        assert len(result) == 0

    def test_predicted_confidence_is_midpoint(self):
        from analyzers.calibration import _bin_predictions
        data = [{'confidence': 0.75, 'accuracy': 0.5}]
        result = _bin_predictions(data)
        assert result[0].predicted_confidence == 0.75


class TestCalibrationService:
    def test_compute_curve_empty(self):
        from analyzers.calibration import CalibrationService
        repo = MagicMock()
        repo.get_calibration_data.return_value = []
        svc = CalibrationService(repo)
        assert svc.compute_calibration_curve() == []

    def test_compute_curve_with_data(self):
        from analyzers.calibration import CalibrationService
        repo = MagicMock()
        repo.get_calibration_data.return_value = [
            {'confidence': 0.75, 'accuracy': 1.0},
            {'confidence': 0.75, 'accuracy': 0.5},
        ]
        svc = CalibrationService(repo)
        result = svc.compute_calibration_curve('build')
        assert len(result) == 1
        assert result[0].sample_size == 2

    def test_per_category_calibration(self):
        from analyzers.calibration import CalibrationService
        repo = MagicMock()
        repo.get_calibration_data.return_value = [
            {'confidence': 0.75, 'accuracy': 1.0, 'category': 'build_error'},
            {'confidence': 0.75, 'accuracy': 1.0, 'category': 'build_error'},
            {'confidence': 0.75, 'accuracy': 0.0, 'category': 'build_error'},
            {'confidence': 0.75, 'accuracy': 1.0, 'category': 'build_error'},
            {'confidence': 0.75, 'accuracy': 0.5, 'category': 'build_error'},
            {'confidence': 0.55, 'accuracy': 1.0, 'category': 'dep'},
        ]
        svc = CalibrationService(repo)
        result = svc.get_per_category_calibration('build')
        assert 'build_error' in result
        assert 'dep' not in result  # < 5 samples

    def test_calibration_score_perfect(self):
        from analyzers.calibration import CalibrationService
        repo = MagicMock()
        repo.get_calibration_data.return_value = [
            {'confidence': 0.75, 'accuracy': 0.75},
        ]
        svc = CalibrationService(repo)
        score = svc.compute_calibration_score()
        assert score == 1.0

    def test_calibration_score_empty(self):
        from analyzers.calibration import CalibrationService
        repo = MagicMock()
        repo.get_calibration_data.return_value = []
        svc = CalibrationService(repo)
        assert svc.compute_calibration_score() is None


# ═══════════════════════════════════════════════════════════════════════
# Bayesian priors tests
# ═══════════════════════════════════════════════════════════════════════

class TestBayesianPriorService:
    def _make_service(self, stats=None):
        from analyzers.bayesian_priors import BayesianPriorService
        repo = MagicMock()
        repo.get_verdict_stats_by_category.return_value = stats or []
        svc = BayesianPriorService(repo)
        return svc

    def test_get_prior_no_data(self):
        svc = self._make_service()
        prior = svc.get_prior('build_error')
        assert prior.is_bootstrapped is True
        assert prior.sample_size == 0
        assert prior.prior_correct == 0.5

    def test_get_prior_with_enough_data(self):
        stats = [
            {'category': 'build_error', 'correct': 8, 'partial': 0, 'total': 10},
        ]
        svc = self._make_service(stats)
        prior = svc.get_prior('build_error')
        assert prior.prior_correct == 0.8
        assert prior.is_bootstrapped is False
        assert prior.sample_size == 10

    def test_get_prior_bootstrapped(self):
        stats = [
            {'category': 'rare', 'correct': 2, 'partial': 1, 'total': 5},
            {'category': 'common', 'correct': 8, 'partial': 0, 'total': 20},
        ]
        svc = self._make_service(stats)
        prior = svc.get_prior('rare')
        assert prior.is_bootstrapped is True
        assert prior.sample_size == 5

    def test_adjust_confidence_no_data(self):
        svc = self._make_service()
        result = svc.adjust_confidence(0.8, 'build_error')
        assert result == 0.8

    def test_adjust_confidence_with_data(self):
        stats = [
            {'category': 'build_error', 'correct': 9, 'partial': 0, 'total': 10},
            {'category': 'other', 'correct': 5, 'partial': 0, 'total': 10},
        ]
        svc = self._make_service(stats)
        result = svc.adjust_confidence(0.8, 'build_error')
        assert 0.1 <= result <= 0.95

    def test_adjust_confidence_clamped(self):
        stats = [
            {'category': 'test', 'correct': 10, 'partial': 0, 'total': 10},
        ]
        svc = self._make_service(stats)
        result = svc.adjust_confidence(0.99, 'test')
        assert result <= 0.95

    def test_cache_ttl(self):
        from analyzers.bayesian_priors import BayesianPriorService
        repo = MagicMock()
        repo.get_verdict_stats_by_category.return_value = [
            {'category': 'test', 'correct': 5, 'partial': 0, 'total': 10},
        ]
        svc = BayesianPriorService(repo)
        svc.get_prior('test')
        svc.get_prior('test')
        assert repo.get_verdict_stats_by_category.call_count == 1

    def test_category_prior_frozen(self):
        from analyzers.bayesian_priors import CategoryPrior
        cp = CategoryPrior('test', 0.8, 0.7, 10, False)
        with pytest.raises((ValueError, TypeError, AttributeError)):
            cp.category = 'other'


# ═══════════════════════════════════════════════════════════════════════
# Feature weights tests
# ═══════════════════════════════════════════════════════════════════════

class TestFeatureWeightService:
    def _make_service(self, rows=None):
        from analyzers.feature_weights import FeatureWeightService
        db = MagicMock()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = rows or []
        conn.cursor.return_value = cursor
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        db.connection.return_value = conn
        return FeatureWeightService(db), cursor

    def test_compute_score_no_weights(self):
        from analyzers.confidence_features import FeatureVector
        svc, _ = self._make_service()
        fv = FeatureVector(has_build_history=True, has_pipeline_logs=True)
        score = svc.compute_score(fv)
        assert 0 <= score <= 1.0
        assert score == 2 / 10  # 2 active out of 10 total, all weight=1.0

    def test_compute_score_all_active(self):
        from analyzers.confidence_features import FeatureVector
        svc, _ = self._make_service()
        fv = FeatureVector(
            has_build_history=True, has_pipeline_logs=True, has_commit_diff=True,
            sha_mismatch_confirmed=True, pattern_matched=True, nudging_checked=True,
            has_open_prs=True, error_in_recent_builds=True, multiple_violations=True,
            has_triage_context=True,
        )
        assert svc.compute_score(fv) == 1.0

    def test_compute_score_with_db_weights(self):
        from analyzers.confidence_features import FeatureVector
        svc, _ = self._make_service(rows=[
            ('has_build_history', 2.0, 100),
            ('has_pipeline_logs', 0.5, 50),
        ])
        fv = FeatureVector(has_build_history=True)
        score = svc.compute_score(fv)
        assert score > 0

    def test_compute_score_none_active(self):
        from analyzers.confidence_features import FeatureVector
        svc, _ = self._make_service()
        fv = FeatureVector()
        assert svc.compute_score(fv) == 0.0

    def test_update_from_verdict_correct(self):
        from analyzers.confidence_features import FeatureVector
        svc, cursor = self._make_service()
        fv = FeatureVector(has_build_history=True)
        svc.update_from_verdict(fv, was_correct=True)
        assert cursor.execute.called

    def test_update_from_verdict_incorrect(self):
        from analyzers.confidence_features import FeatureVector
        svc, cursor = self._make_service()
        fv = FeatureVector(has_build_history=True)
        svc.update_from_verdict(fv, was_correct=False)
        assert cursor.execute.called

    def test_get_feature_importance_empty(self):
        svc, _ = self._make_service()
        svc._ensure_loaded()
        assert svc.get_feature_importance() == []

    def test_get_feature_importance_sorted(self):
        svc, _ = self._make_service(rows=[
            ('feature_a', 1.5, 10),
            ('feature_b', 0.8, 5),
            ('feature_c', 2.0, 20),
        ])
        result = svc.get_feature_importance()
        assert result[0]['name'] == 'feature_c'
        assert result[-1]['name'] == 'feature_b'

    def test_feature_weight_frozen(self):
        from analyzers.feature_weights import FeatureWeight
        fw = FeatureWeight('test', 1.0, 10)
        with pytest.raises((ValueError, TypeError, AttributeError)):
            fw.weight = 2.0

    def test_db_error_graceful(self):
        from analyzers.feature_weights import FeatureWeightService
        db = MagicMock()
        db.connection.side_effect = Exception("db down")
        svc = FeatureWeightService(db)
        svc._ensure_loaded()
        assert svc._loaded is True
