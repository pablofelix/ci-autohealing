# Smarter Auto-Resolution — Design Spec

## Problem

The watcher's `_handle_success` unconditionally resolves all unresolved build failures for a component when any build succeeds. This causes premature resolution:

1. **Structural failures** (component has never built successfully) get resolved by unrelated successful builds — e.g., trustyai-service was resolved despite the underlying issue (deleted .tekton pipelines, broken Dockerfile) being unfixed.

2. **Step mismatch** — a push build that doesn't run fips-check resolves a nightly failure that failed at fips-check. The root cause was never tested.

## Solution

Add a validation layer in `_handle_success` before calling `mark_resolved()`. Two guards:

### Guard 1: Structural failures do not auto-resolve

- Persist `failure_nature` as a new column in `build_failures` (VARCHAR(20), values: `structural`, `unknown`, NULL).
- `classify_failure_nature()` already exists at `src/analyzers/build_failure_analyzer.py:136`. Save its result to the DB column when AI analysis runs.
- In `_handle_success`: query the pending failure. If `failure_nature = 'structural'`, log a warning and skip auto-resolution.

### Guard 2: Step mismatch does not resolve

- The pending failure has `failed_step_name` (already in the DB schema).
- In `_handle_success`: if the pending failure has a `failed_step_name`, check whether the successful PipelineRun ran that step (inspect `status.childReferences` or `status.taskRuns` on the PipelineRun object).
- If the failed step did not run in the successful build, skip auto-resolution.

## Changes

### New migration

Add column `failure_nature VARCHAR(20) DEFAULT NULL` to `build_failures`.

### `src/analyzers/build_failure_analyzer.py`

In `analyze_failure()`, after calling `classify_failure_nature(component_health)`, save the result to the failure record via `build_repo.update_failure_nature(failure_id, nature)`.

### `src/repositories/build_failure_repository.py`

- New method: `get_unresolved_failure(component, application)` — returns the most recent unresolved failure row with `failure_nature` and `failed_step_name`.
- New method: `update_failure_nature(failure_id, nature)` — sets the `failure_nature` column.
- `mark_resolved()` unchanged internally — the guards live in the caller.

### `src/watcher/handlers.py`

`_handle_success` becomes:

```
1. Get unresolved failure for this component
2. If no unresolved failure → nothing to resolve, return
3. If failure.failure_nature == 'structural' → log warning, return
4. If failure.failed_step_name exists:
   a. Extract completed tasks from successful PipelineRun status
   b. If failed_step_name not in completed tasks → log warning, return
5. Call mark_resolved() as before
```

## What does NOT change

- Failures with `failure_nature = 'unknown'` or NULL where the step matches → auto-resolve as before.
- `mark_resolved()` internals unchanged.
- Triage items remain independent from build_failures resolution.
- `classify_failure_nature()` logic unchanged (has_ever_succeeded=False → structural).

## Testing

- Unit test: structural failure is NOT auto-resolved on success
- Unit test: step-mismatch failure is NOT auto-resolved
- Unit test: normal transient failure IS auto-resolved (regression)
- Unit test: failure with no failed_step_name IS auto-resolved (backwards compat)
- Unit test: update_failure_nature persists correctly
- Integration test: end-to-end with mock PipelineRun events
