"""Pre-fix analysis reviewer — deterministic validation of AI analyses.

Runs 5 pattern-based checks on a completed analysis before the triager acts on it.
Pure functions only — no DB, no API, no LLM calls.
"""

import inspect
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ReviewFlag:
    check: str
    severity: str
    message: str


@dataclass
class ReviewResult:
    verdict: str
    flags: List[ReviewFlag] = field(default_factory=list)
    summary: str = ''


STOPWORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'to', 'of', 'in', 'for',
    'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through', 'during',
    'before', 'after', 'above', 'below', 'and', 'but', 'or', 'not', 'no',
    'this', 'that', 'these', 'those', 'it', 'its', 'due',
})

CATEGORY_STEP_MAP = {
    'prefetch-dependencies': {'dependency_issue', 'build_configuration'},
    'build-container': {'build_error', 'source_code_error', 'dependency_issue'},
    'build-image-index': {'infrastructure', 'build_configuration'},
    'clair-scan': {'security_vulnerability'},
    'deprecated-image-check': {'build_configuration'},
    'build-source-image': {'infrastructure', 'build_configuration'},
    'inspect-image': {'infrastructure', 'build_configuration'},
}


def review_analysis(analysis: dict, failure: dict) -> ReviewResult:
    checks = [
        check_evidence_log_coherence,
        check_category_step_coherence,
        check_confidence_gate,
        check_staleness,
        check_known_misclassifications,
    ]
    flags = []
    for check_fn in checks:
        params = inspect.signature(check_fn).parameters
        flag = check_fn(analysis, failure) if len(params) > 1 else check_fn(analysis)
        if flag:
            flags.append(flag)

    verdict = _compute_verdict(flags)
    summary = _build_summary(verdict, flags)
    return ReviewResult(verdict=verdict, flags=flags, summary=summary)


def _compute_verdict(flags: List[ReviewFlag]) -> str:
    severities = {f.severity for f in flags}
    if 'error' in severities:
        return 'fail'
    if 'warning' in severities:
        return 'warn'
    return 'pass'


def _build_summary(verdict: str, flags: List[ReviewFlag]) -> str:
    if not flags:
        return 'All checks passed'
    flag_names = ', '.join(f.check for f in flags)
    return f'{verdict.upper()}: {flag_names}'


def _extract_key_terms(text: str) -> set:
    words = re.findall(r'[a-z0-9_.-]{3,}', text.lower())
    return {w for w in words if w not in STOPWORDS}


def check_evidence_log_coherence(analysis: dict, failure: dict) -> Optional[ReviewFlag]:
    root_cause = analysis.get('root_cause', '')
    if not root_cause:
        return None

    logs = str(failure.get('build_logs', '') or '')
    error_msg = str(failure.get('error_message', '') or '')
    combined = (logs[:5000] + ' ' + error_msg).lower()

    if not combined.strip():
        return ReviewFlag(
            check='root_cause_not_in_logs',
            severity='error',
            message='No build logs or error message available to verify root cause',
        )

    terms = _extract_key_terms(root_cause)
    if not terms:
        return None

    matched = sum(1 for t in terms if t in combined)
    ratio = matched / len(terms)

    if ratio < 0.3:
        return ReviewFlag(
            check='root_cause_not_in_logs',
            severity='error',
            message=f'Only {matched}/{len(terms)} root cause terms found in logs ({ratio:.0%})',
        )
    return None


def check_category_step_coherence(analysis: dict, failure: dict) -> Optional[ReviewFlag]:
    category = analysis.get('failure_category', '')
    step = failure.get('failed_step_name', '')
    if not category or not step:
        return None

    plausible = CATEGORY_STEP_MAP.get(step)
    if plausible is None:
        return None

    if category not in plausible:
        return ReviewFlag(
            check='category_step_mismatch',
            severity='warning',
            message=f'Category "{category}" is unusual for step "{step}" (expected: {", ".join(sorted(plausible))})',
        )
    return None


def check_confidence_gate(analysis: dict) -> Optional[ReviewFlag]:
    score = analysis.get('confidence_score', 0)
    if not isinstance(score, (int, float)):
        score = 0

    if score < 0.3:
        return ReviewFlag(
            check='very_low_confidence',
            severity='error',
            message=f'Confidence {score:.2f} is below minimum threshold (0.3)',
        )
    if score < 0.5:
        return ReviewFlag(
            check='low_confidence',
            severity='warning',
            message=f'Confidence {score:.2f} is below recommended threshold (0.5)',
        )
    return None


def check_staleness(analysis: dict, failure: dict) -> Optional[ReviewFlag]:
    analyzed_at = analysis.get('analyzed_at')
    last_updated = failure.get('last_updated_at')

    if not analyzed_at or not last_updated:
        return None

    if analyzed_at >= last_updated:
        return None

    delta = last_updated - analyzed_at
    hours = delta.total_seconds() / 3600

    if hours > 24:
        return ReviewFlag(
            check='stale_analysis',
            severity='error',
            message=f'Analysis is {hours:.0f}h older than the latest failure update',
        )
    return ReviewFlag(
        check='stale_analysis',
        severity='warning',
        message=f'Analysis is {hours:.0f}h older than the latest failure update',
    )


def check_known_misclassifications(analysis: dict) -> Optional[ReviewFlag]:
    root_cause = (analysis.get('root_cause', '') or '').lower()
    category = analysis.get('failure_category', '')
    rec_fix = (analysis.get('recommended_fix', '') or '').lower()

    if 'cpu-ubi9-test' in root_cause and category == 'dependency_issue':
        return ReviewFlag(
            check='likely_index_misconfiguration',
            severity='warning',
            message='Root cause mentions cpu-ubi9-test index but category is dependency_issue — likely build_configuration',
        )

    if 'hermeto' in root_cause and 'checksum' in root_cause:
        combined = root_cause + ' ' + rec_fix
        if not any(kw in combined for kw in ('lock file', 'requirements', 'index', 'lock_file')):
            return ReviewFlag(
                check='incomplete_hermeto_diagnosis',
                severity='warning',
                message='Hermeto checksum failure diagnosed without mentioning lock file or index — diagnosis may be incomplete',
            )

    return None
