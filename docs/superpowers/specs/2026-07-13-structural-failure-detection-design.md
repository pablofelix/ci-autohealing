# Structural Failure Detection

## Problem

IC misdiagnosed `odh-trustyai-service-v3-5` as a transient DNS failure and recommended rebuild. The real issue was structural: the component uses UBI 10 base images whose CDN (`cdn-ubi.redhat.com`) is unreachable from Konflux build nodes. The component has **never built successfully** — a signal IC didn't surface to the analyzer.

This is phase 1 of a broader improvement. Phase 2 (onboarding status cross-referencing and smarter auto-resolution) will follow once we validate this fixes the immediate misdiagnosis pattern.

## Root Cause of the Misdiagnosis

Two gaps in the current analyzer pipeline:

1. **No build success history signal.** The `BuildHistorySource` enrichment source looks for the *last successful build* and returns `None` when there isn't one — but `None` is indistinguishable from "Tekton Results unavailable" or "source timed out." The LLM never learns that the component has zero successful builds in its entire history.

2. **No transient vs structural guidance.** The LLM prompt has no framework for distinguishing infrastructure failures that are retry-able (timeouts, rate limits, temporary outages) from those that are structural (wrong base image, unreachable CDN, missing entitlements). It defaults to "infrastructure" + "rebuild" for any network error.

## Design

### 1. New enrichment source: `ComponentHealthSource`

**File:** `enrichment/sources/component_health.py`

A new `ContextSource` implementation that surfaces build success history. Follows the existing enrichment pattern (Strategy + Template Method via `ContextSource` base class).

**Data sources (in order of authority):**
- Tekton Results (`TektonResultsClient.query_component_build_history`) — authoritative record of all builds
- IC's `build_failures` table (`get_component_history`) — faster, but only tracks what IC has seen

**Returns:**
```python
{
    'component_health': {
        'has_ever_succeeded': bool,       # THE key signal
        'total_builds': int,              # from Tekton Results
        'successful_builds': int,
        'failed_builds': int,
        'consecutive_failures': int,      # current streak
        'last_success_date': str | None,  # ISO timestamp or None
        'last_success_sha': str | None,
    }
}
```

**Properties:**
- `source_name`: `'component_health'`
- `requires_external_api`: `True` (Tekton Results)
- `timeout_seconds`: `15`

**Registration:** Added to `_ensure_enrichment` in `build_failure_analyzer.py` alongside the existing 4 sources.

### 2. Mechanical structural failure classification

**File:** `analyzers/build_failure_analyzer.py` (new function)

A pure function that classifies failure nature based on component health data. Runs **before** the LLM call. No LLM involvement — deterministic logic only.

```python
def classify_failure_nature(component_health):
    """Classify failure as structural, or unknown.

    Only Rule 1 for now: component has never built successfully = structural.
    Additional rules will be added with evidence from real misdiagnoses.

    Returns: 'structural' or 'unknown'
    """
    if not component_health:
        return 'unknown'
    if not component_health.get('has_ever_succeeded', True):
        return 'structural'
    return 'unknown'
```

Single rule, zero false-positive risk. Additional rules (consecutive same-error, same-commit-succeeded-before) deferred until we have evidence they're needed.

**Call site:** Inside `build_analysis_prompt` (line ~697), after `_ensure_enrichment` has run. Extract `component_health` from `failure.get('enriched_context', {})` and pass to `classify_failure_nature`. The classification result determines what gets injected into the prompt.

### 3. Prompt injection based on classification

**File:** `analyzers/build_failure_analyzer.py` (in `build_analysis_prompt`)

When `classify_failure_nature` returns `'structural'`, inject into the user prompt:

```
## Component Build Health
- Has ever built successfully: No
- Total builds observed: {N}
- Consecutive failures: {N}

STRUCTURAL FAILURE: This component has NEVER built successfully.
This is NOT a transient issue — do not recommend rebuild or retry.
Identify the structural root cause: wrong base image, unreachable registry,
missing pipeline config, incomplete onboarding, or misconfigured Dockerfile.
```

When classification is `'unknown'`, still show the health stats (they're useful context) but without the structural warning:

```
## Component Build Health
- Has ever built successfully: Yes
- Last success: {date} ({sha})
- Consecutive failures since: {N}
```

### 4. Format in `_format_commit_context`

The `component_health` enrichment data gets formatted and injected in `_format_commit_context` (line ~1008), alongside existing enrichment sections (dependency changes, related failures, resolved examples, open PRs).

## Affected Files

| File | Change |
|------|--------|
| `enrichment/sources/component_health.py` | **New.** ComponentHealthSource implementation |
| `enrichment/sources/__init__.py` | Register new source in `__all__` |
| `analyzers/build_failure_analyzer.py` | Add `classify_failure_nature()`, register source in `_ensure_enrichment`, inject health section in prompt, format in `_format_commit_context` |
| `tests/test_component_health_source.py` | **New.** Tests for ComponentHealthSource |
| `tests/test_structural_classification.py` | **New.** Tests for `classify_failure_nature` |

## Testing

### ComponentHealthSource tests
- Component with successful builds → `has_ever_succeeded: True`, correct counts
- Component with zero successes → `has_ever_succeeded: False`
- Tekton Results unavailable → returns `None` (graceful degradation)
- Empty build history → `has_ever_succeeded: False`, `total_builds: 0`

### classify_failure_nature tests
- `has_ever_succeeded: False` → `'structural'`
- `has_ever_succeeded: True` → `'unknown'`
- `None` input → `'unknown'`
- Empty dict → `'unknown'`

### Integration test
- Build prompt for a component with `has_ever_succeeded: False` → prompt contains "STRUCTURAL FAILURE" section
- Build prompt for a component with `has_ever_succeeded: True` → prompt shows health stats without structural warning
- `_ensure_enrichment` registers ComponentHealthSource alongside existing sources

## Validation

After implementation, re-run the `odh-trustyai-service-v3-5` failure through the analyzer. Expected outcome:
- `ComponentHealthSource` returns `has_ever_succeeded: False` (trustyai-service has 0 successful builds)
- `classify_failure_nature` returns `'structural'`
- Prompt includes structural warning
- LLM diagnosis should identify UBI 10 CDN as root cause and NOT recommend rebuild

## Not in Scope (Phase 2)

- Onboarding status cross-referencing (gap 3)
- Smarter auto-resolution that verifies root cause was addressed (gap 4)
- Additional classification rules beyond Rule 1
- Changes to the failure_category enum (the LLM still picks from existing categories)
