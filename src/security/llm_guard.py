"""LLM prompt injection detection and output validation.

Protects the AI analyzer from manipulated build logs that try to
influence the diagnosis via prompt injection.
"""

import re
import secrets

_VALID_CATEGORIES = {
    'dependency_issue', 'build_error', 'config_error', 'test_failure',
    'timeout', 'resource_limit', 'permission_error', 'network_error',
    'syntax_error', 'missing_file', 'incompatible_version', 'infra_issue',
    'hermetic_build', 'image_build', 'unknown',
}

_INJECTION_PATTERNS = [
    re.compile(r'ignore\s+(previous|all|above)\s+instructions', re.IGNORECASE),
    re.compile(r'system:\s*(you are|act as|pretend)', re.IGNORECASE),
    re.compile(r'<\|?(system|user|assistant)\|?>', re.IGNORECASE),
    re.compile(r'IMPORTANT:\s*override', re.IGNORECASE),
    re.compile(r'root.?cause.*everything.*(fine|ok|good)', re.IGNORECASE),
    re.compile(r'confidence.*(:|\s)\s*(100|99|0\.99)', re.IGNORECASE),
]


def generate_canary():
    """Generate a unique canary token to embed in the system prompt.

    If the canary appears in the AI output, the output was likely
    influenced by prompt injection (the AI echoed the system prompt).
    """
    return 'CANARY-{}'.format(secrets.token_hex(8))


def check_canary(output, canary):
    """Check if the canary token leaked into the AI output."""
    return canary in output if output and canary else False


def detect_injection(text):
    """Scan text for known prompt injection patterns.

    Returns list of detected patterns (empty if clean).
    """
    if not text:
        return []
    findings = []
    for pattern in _INJECTION_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            findings.append(pattern.pattern[:60])
    return findings


def validate_analysis_output(result):
    """Validate that AI analysis output is structurally sound.

    Returns (is_valid, issues) tuple.
    """
    issues = []

    if not isinstance(result, dict):
        return False, ['Output is not a dict']

    confidence = result.get('confidence_score')
    if confidence is not None:
        if not isinstance(confidence, (int, float)):
            issues.append('confidence_score is not numeric: {}'.format(type(confidence)))
        elif confidence < 0 or confidence > 1:
            issues.append('confidence_score out of range: {}'.format(confidence))

    category = result.get('failure_category', '')
    if category and category not in _VALID_CATEGORIES:
        issues.append('Unknown failure_category: {} (expected one of: {})'.format(
            category, ', '.join(sorted(_VALID_CATEGORIES))))

    root_cause = result.get('root_cause', '')
    if root_cause:
        injection = detect_injection(root_cause)
        if injection:
            issues.append('Possible injection in root_cause: {}'.format(injection))

    fix = result.get('recommended_fix', '')
    if fix:
        injection = detect_injection(fix)
        if injection:
            issues.append('Possible injection in recommended_fix: {}'.format(injection))

    return len(issues) == 0, issues
