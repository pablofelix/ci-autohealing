"""Feature extraction for evidence-based confidence scoring.

Pure functions that extract boolean features from analysis context.
Used by the confidence pipeline to compute feature-weighted scores.
"""

from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class FeatureVector:
    has_build_history: bool = False
    has_pipeline_logs: bool = False
    has_commit_diff: bool = False
    sha_mismatch_confirmed: bool = False
    pattern_matched: bool = False
    nudging_checked: bool = False
    has_open_prs: bool = False
    error_in_recent_builds: bool = False
    multiple_violations: bool = False
    has_triage_context: bool = False

    def to_dict(self):
        return asdict(self)

    def active_features(self):
        return [f.name for f in fields(self) if getattr(self, f.name)]

    def active_count(self):
        return sum(1 for f in fields(self) if getattr(self, f.name))

    def total_count(self):
        return len(fields(self))


def extract_features(context, analyzer_type='build'):
    """Extract boolean features from enrichment context.

    Args:
        context: Dict with build_history, commit_context, logs, etc.
        analyzer_type: 'build', 'conforma', or 'release'

    Returns:
        FeatureVector with boolean flags.
    """
    if not context or not isinstance(context, dict):
        return FeatureVector()

    if analyzer_type == 'build':
        return _extract_build_features(context)
    if analyzer_type == 'conforma':
        return _extract_conforma_features(context)
    if analyzer_type == 'release':
        return _extract_release_features(context)
    return FeatureVector()


def _extract_build_features(ctx):
    build_history = ctx.get('build_history') or []
    commit_context = ctx.get('commit_context') or {}
    logs = ctx.get('build_logs') or ctx.get('logs') or ''

    has_errors_in_recent = False
    if build_history:
        recent_statuses = [b.get('status', '') for b in build_history[:5]]
        has_errors_in_recent = any(s in ('Failed', 'failed') for s in recent_statuses)

    return FeatureVector(
        has_build_history=bool(build_history),
        has_pipeline_logs=bool(logs and len(str(logs)) > 100),
        has_commit_diff=bool(commit_context.get('diff') or commit_context.get('files')),
        sha_mismatch_confirmed=bool(ctx.get('sha_mismatch')),
        pattern_matched=bool(ctx.get('pattern_match') or ctx.get('matched_pattern')),
        nudging_checked=bool(ctx.get('nudging_checked')),
        has_open_prs=bool(ctx.get('open_prs')),
        error_in_recent_builds=has_errors_in_recent,
    )


def _extract_conforma_features(ctx):
    violations = ctx.get('violations') or ctx.get('violation_details') or {}
    total_violations = 0
    if isinstance(violations, dict):
        total_violations = sum(len(v) if isinstance(v, list) else 1
                               for v in violations.values())
    elif isinstance(violations, list):
        total_violations = len(violations)

    return FeatureVector(
        has_build_history=bool(ctx.get('build_history')),
        has_pipeline_logs=bool(ctx.get('tekton_config') or ctx.get('pipeline_yaml')),
        has_commit_diff=bool(ctx.get('commit_context', {}).get('diff')),
        pattern_matched=bool(ctx.get('pattern_match') or ctx.get('matched_pattern')),
        multiple_violations=total_violations > 1,
        has_open_prs=bool(ctx.get('open_prs')),
    )


def _extract_release_features(ctx):
    return FeatureVector(
        has_build_history=bool(ctx.get('component_build_history')),
        has_pipeline_logs=bool(ctx.get('step_report') or ctx.get('detailed_report')),
        has_commit_diff=bool(ctx.get('nudge_prs')),
        nudging_checked=bool(ctx.get('nudging_yaml') or ctx.get('nudging_checked')),
        has_open_prs=bool(ctx.get('nudge_prs')),
        has_triage_context=bool(ctx.get('triage_items')),
        pattern_matched=bool(ctx.get('pattern_match')),
        multiple_violations=bool(ctx.get('violations') and len(ctx.get('violations', [])) > 1),
    )
