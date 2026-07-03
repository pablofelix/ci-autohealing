"""Evidence hierarchy for AI analysis confidence scoring.

Defines 4-tier evidence classification and confidence adjustment formulas.
Pure module — no I/O, no state.
"""

EVIDENCE_TIERS = {
    1: {
        'name': 'Direct Artifact',
        'weight': 1.0,
        'max_confidence': 0.95,
        'examples': [
            'Commit diff showing exact error line',
            'Build logs with full stack trace',
            'Test failure output with assertion details',
            'step-detailed-report violation lines',
        ],
    },
    2: {
        'name': 'Configuration Source',
        'weight': 0.85,
        'max_confidence': 0.85,
        'examples': [
            '.tekton pipeline YAML configs',
            'Dockerfile/Containerfile contents',
            'Dependency manifests (go.mod, requirements.txt)',
            'EC policy YAML',
            'operator-nudging.yaml',
        ],
    },
    3: {
        'name': 'Historical Pattern',
        'weight': 0.65,
        'max_confidence': 0.70,
        'examples': [
            'Build history showing repeated failures',
            'Error message keyword matching',
            'Related failures across components',
            'Known pattern from error_patterns table',
        ],
    },
    4: {
        'name': 'Inference',
        'weight': 0.40,
        'max_confidence': 0.55,
        'examples': [
            'Default assumptions',
            'General best practices without component-specific confirmation',
            'Speculation based on error type alone',
        ],
    },
}

TIER_NAMES = {v['name']: k for k, v in EVIDENCE_TIERS.items()}


def classify_evidence(evidence_type):
    """Classify an evidence type string into a tier (1-4).

    Args:
        evidence_type: String like 'commit_diff', 'build_logs', 'pattern_match', etc.

    Returns:
        Tier number (1-4), defaults to 3 if unrecognized.
    """
    tier1 = {'commit_diff', 'build_logs', 'stack_trace', 'test_output',
             'error_line', 'step_detailed_report', 'violation_output'}
    tier2 = {'tekton_config', 'dockerfile', 'containerfile', 'dependency_manifest',
             'ec_policy', 'nudging_yaml', 'rpa_mapping', 'pipeline_yaml',
             'go_mod', 'requirements_txt'}
    tier3 = {'build_history', 'error_pattern', 'keyword_match', 'related_failures',
             'known_pattern', 'nightly_trend'}
    tier4 = {'assumption', 'inference', 'general_practice', 'speculation'}

    normalized = evidence_type.lower().replace(' ', '_').replace('-', '_')
    if normalized in tier1:
        return 1
    if normalized in tier2:
        return 2
    if normalized in tier3:
        return 3
    if normalized in tier4:
        return 4
    return 3


def adjust_confidence(base_confidence, evidence_tiers):
    """Adjust confidence based on evidence tier distribution.

    If the highest-quality evidence is only Tier 3, the confidence
    should be capped below what Tier 1 evidence would support.

    Args:
        base_confidence: Raw confidence score (0.0-1.0)
        evidence_tiers: List of tier numbers used in the analysis

    Returns:
        Adjusted confidence score (0.0-1.0)
    """
    if not evidence_tiers:
        return min(base_confidence, 0.55)

    best_tier = min(evidence_tiers)
    tier_info = EVIDENCE_TIERS.get(best_tier, EVIDENCE_TIERS[3])
    max_conf = tier_info['max_confidence']

    avg_weight = sum(EVIDENCE_TIERS.get(t, EVIDENCE_TIERS[3])['weight']
                     for t in evidence_tiers) / len(evidence_tiers)

    adjusted = base_confidence * avg_weight
    return min(adjusted, max_conf)


def validate_hypothesis(hypothesis):
    """Validate and normalize a differential diagnosis hypothesis dict.

    Returns cleaned hypothesis or None if invalid.
    """
    if not isinstance(hypothesis, dict):
        return None

    required = ('hypothesis', 'category', 'confidence')
    if not all(hypothesis.get(k) for k in required):
        return None

    confidence = hypothesis.get('confidence', 0.5)
    if not isinstance(confidence, (int, float)):
        confidence = 0.5
    confidence = max(0.0, min(1.0, float(confidence)))

    return {
        'hypothesis': str(hypothesis['hypothesis']),
        'category': str(hypothesis['category']),
        'confidence': confidence,
        'supporting_evidence': hypothesis.get('supporting_evidence', []),
        'contradicting_evidence': hypothesis.get('contradicting_evidence', []),
    }
