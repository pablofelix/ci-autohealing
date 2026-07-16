"""Tests for the pre-fix analysis reviewer."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from analyzers.analysis_reviewer import (
    check_category_step_coherence,
    check_confidence_gate,
    check_evidence_log_coherence,
    check_known_misclassifications,
    check_staleness,
    review_analysis,
)


def _sample_analysis(**overrides):
    base = {
        'root_cause': 'hermeto checksum mismatch on yarl package due to stale lock file',
        'failure_category': 'dependency_issue',
        'confidence_score': 0.82,
        'recommended_fix': 'Regenerate requirements.txt against the correct PyPI index',
        'analyzed_at': datetime(2026, 7, 14, 10, 0),
    }
    base.update(overrides)
    return base


def _sample_failure(**overrides):
    base = {
        'error_message': 'hermeto checksum mismatch for yarl-1.9.4',
        'build_logs': 'Step prefetch-dependencies failed: hermeto checksum verification failed for yarl-1.9.4. Expected sha256:abc123, got sha256:def456. Lock file references cpu-ubi9-test index.',
        'failed_step_name': 'prefetch-dependencies',
        'last_updated_at': datetime(2026, 7, 14, 8, 0),
    }
    base.update(overrides)
    return base


class TestReviewResult:

    def test_no_flags_gives_pass(self):
        result = review_analysis(_sample_analysis(), _sample_failure())
        assert result.verdict == 'pass'

    def test_warning_flag_gives_warn(self):
        analysis = _sample_analysis(confidence_score=0.45)
        result = review_analysis(analysis, _sample_failure())
        assert result.verdict == 'warn'
        assert any(f.check == 'low_confidence' for f in result.flags)

    def test_error_flag_gives_fail(self):
        analysis = _sample_analysis(confidence_score=0.2)
        result = review_analysis(analysis, _sample_failure())
        assert result.verdict == 'fail'
        assert any(f.check == 'very_low_confidence' for f in result.flags)

    def test_summary_is_nonempty_string(self):
        result = review_analysis(_sample_analysis(), _sample_failure())
        assert isinstance(result.summary, str)
        assert len(result.summary) > 0


class TestEvidenceLogCoherence:

    def test_root_cause_terms_in_logs_passes(self):
        analysis = _sample_analysis()
        failure = _sample_failure()
        flag = check_evidence_log_coherence(analysis, failure)
        assert flag is None

    def test_root_cause_not_in_logs_flags(self):
        analysis = _sample_analysis(root_cause='golang compiler segfault in cgo module')
        failure = _sample_failure()
        flag = check_evidence_log_coherence(analysis, failure)
        assert flag is not None
        assert flag.check == 'root_cause_not_in_logs'
        assert flag.severity == 'error'

    def test_empty_logs_flags(self):
        analysis = _sample_analysis()
        failure = _sample_failure(build_logs='', error_message='')
        flag = check_evidence_log_coherence(analysis, failure)
        assert flag is not None


class TestCategoryStepCoherence:

    def test_matching_category_step_passes(self):
        analysis = _sample_analysis(failure_category='dependency_issue')
        failure = _sample_failure(failed_step_name='prefetch-dependencies')
        flag = check_category_step_coherence(analysis, failure)
        assert flag is None

    def test_mismatched_category_step_flags(self):
        analysis = _sample_analysis(failure_category='security_vulnerability')
        failure = _sample_failure(failed_step_name='prefetch-dependencies')
        flag = check_category_step_coherence(analysis, failure)
        assert flag is not None
        assert flag.check == 'category_step_mismatch'
        assert flag.severity == 'warning'

    def test_unknown_step_does_not_flag(self):
        analysis = _sample_analysis(failure_category='dependency_issue')
        failure = _sample_failure(failed_step_name='some-custom-step')
        flag = check_category_step_coherence(analysis, failure)
        assert flag is None


class TestConfidenceGate:

    def test_high_confidence_passes(self):
        flag = check_confidence_gate(_sample_analysis(confidence_score=0.82))
        assert flag is None

    def test_low_confidence_warns(self):
        flag = check_confidence_gate(_sample_analysis(confidence_score=0.45))
        assert flag is not None
        assert flag.check == 'low_confidence'
        assert flag.severity == 'warning'

    def test_very_low_confidence_errors(self):
        flag = check_confidence_gate(_sample_analysis(confidence_score=0.2))
        assert flag is not None
        assert flag.check == 'very_low_confidence'
        assert flag.severity == 'error'


class TestStaleness:

    def test_fresh_analysis_passes(self):
        analysis = _sample_analysis(analyzed_at=datetime(2026, 7, 14, 10, 0))
        failure = _sample_failure(last_updated_at=datetime(2026, 7, 14, 8, 0))
        flag = check_staleness(analysis, failure)
        assert flag is None

    def test_stale_analysis_warns(self):
        analysis = _sample_analysis(analyzed_at=datetime(2026, 7, 13, 10, 0))
        failure = _sample_failure(last_updated_at=datetime(2026, 7, 14, 8, 0))
        flag = check_staleness(analysis, failure)
        assert flag is not None
        assert flag.check == 'stale_analysis'
        assert flag.severity == 'warning'

    def test_very_stale_analysis_errors(self):
        now = datetime(2026, 7, 15, 10, 0)
        analysis = _sample_analysis(analyzed_at=datetime(2026, 7, 13, 6, 0))
        failure = _sample_failure(last_updated_at=now)
        flag = check_staleness(analysis, failure)
        assert flag is not None
        assert flag.severity == 'error'

    def test_missing_timestamps_passes(self):
        analysis = _sample_analysis(analyzed_at=None)
        failure = _sample_failure(last_updated_at=None)
        flag = check_staleness(analysis, failure)
        assert flag is None


class TestKnownMisclassifications:

    def test_index_misconfiguration_detected(self):
        analysis = _sample_analysis(
            root_cause='hermeto checksum mismatch due to cpu-ubi9-test index',
            failure_category='dependency_issue',
        )
        flag = check_known_misclassifications(analysis)
        assert flag is not None
        assert flag.check == 'likely_index_misconfiguration'

    def test_incomplete_hermeto_diagnosis_detected(self):
        analysis = _sample_analysis(
            root_cause='hermeto checksum verification failed',
            recommended_fix='Retry the build',
        )
        flag = check_known_misclassifications(analysis)
        assert flag is not None
        assert flag.check == 'incomplete_hermeto_diagnosis'

    def test_correct_analysis_passes(self):
        analysis = _sample_analysis()
        flag = check_known_misclassifications(analysis)
        assert flag is None
