# Multi-Failure TaskRun Collection

## Problem

IC stops at the first failed TaskRun in a PipelineRun. When multiple TaskRuns fail (e.g., coverity-availability-check AND fips-check-0), IC captures whichever appears first in `childReferences`, which may not be the actual root cause.

Real-world case: `odh-vllm-cpu-v3-5` failed fips-check on s390x (patchelf statically linked), but IC reported "Coverity license expired" because that TaskRun appeared first.

## Affected Files

| File | Function | Gap |
|------|----------|-----|
| `collectors/build_failure_collector.py` | `_find_failed_taskrun` | Returns on first failed TaskRun (line 350) |
| `clients/tekton_results.py` | `find_failed_taskrun` | Same early-return pattern (line 198) |
| `analyzers/build_failure_analyzer.py` | `_fetch_failed_taskrun_logs` | `if not failed_logs:` guard skips subsequent failures (line 557) |
| `tekton_parsers.py` | `extract_error_from_logs` | Returns first regex match (line 129) |
| `tekton_parsers.py` | `extract_pipelinerun_metadata` | `failed_tasks` lists ALL tasks, not just failed ones (line 233-238) |
| `watcher/handlers.py` | `_handle_failure` | Takes `failed_tasks[0]` as failed_step (line 101) |

## Design

### 1. Collect all failed TaskRuns, pick the primary one

**`_find_failed_taskrun`** (build_failure_collector.py):
- Iterate ALL `childReferences`, collect every TaskRun with `status == 'False'` into a list of `(task_name, error_msg, logs)`
- Pick the primary failure using `_pick_primary_failure(failures, pr_data)`
- Return the primary one (same signature, no breaking change)

**`find_failed_taskrun`** (tekton_results.py):
- Same pattern: collect all failed, pick primary
- Return signature unchanged: `(task_name, logs, record_name)`

### 2. Primary failure selection heuristic

New function `_pick_primary_failure(failures, pr_data)`:

1. Parse PipelineRun `conditions[-1].message` for the task name that caused the pipeline to fail (Tekton often embeds it)
2. If no match, prefer failures from build-critical tasks over side-effect tasks. Priority order:
   - Build tasks: `build-container`, `build-images`, `build-image-index`
   - Compliance tasks: `fips-check`, `verify-conforma`
   - Test tasks: `sast-snyk-check`, `clamav-scan`
   - Side-effect tasks: `coverity-availability-check`, `sast-coverity-check`
3. If still tied, prefer the TaskRun with actual log content over one with only condition messages
4. Final tiebreaker: latest failure timestamp

This heuristic lives in `tekton_parsers.py` as a pure function for easy testing.

### 3. Analyzer sends all failed TaskRun logs to LLM

**`_fetch_failed_taskrun_logs`** (build_failure_analyzer.py):
- Remove the `if not failed_logs:` guard on line 557
- Collect logs from ALL failed TaskRuns into a list
- Concatenate with clear markers: `=== Failed TaskRun: {task} [{platform}] ===`
- The LLM prompt already includes per-platform status summary; now it also gets all relevant logs

### 4. Fix `extract_error_from_logs` to collect all errors

**`extract_error_from_logs`** (tekton_parsers.py):
- New function `extract_all_errors_from_logs(logs)` returns a list of `(error_message, error_type)`
- Original `extract_error_from_logs` calls it and returns the first match (backward compatible)
- The collector uses `extract_all_errors_from_logs` when multiple failures exist to pick the most relevant error

### 5. Fix `failed_tasks` to filter by actual failure status

**`extract_pipelinerun_metadata`** (tekton_parsers.py):
- `failed_tasks` currently appends every childReference TaskRun regardless of status
- Change: only include tasks whose `whenExpressions` were met AND whose condition is failed
- Problem: `childReferences` doesn't include status — it's just a reference list
- Solution: rename to `all_tasks` for clarity, or cross-reference with PipelineRun conditions message to extract actual failed task names

Pragmatic fix: parse the PipelineRun `conditions[-1].message` which lists failed tasks, and use that for `failed_tasks`. Fall back to all tasks if parsing fails.

### 6. Watcher uses primary failure for failed_step

**`_handle_failure`** (watcher/handlers.py):
- Currently: `failed_step = details.get('failed_tasks', [None])[0]`
- Change: use `failed_step` from the primary failure selection (the collector already picks the right one)
- The watcher calls `pipeline_client.get_pipelinerun_logs()` which collects all TaskRun logs — `extract_error_from_logs` on this combined output should pick the primary error, not just the first

## Testing

- Unit tests for `_pick_primary_failure` with matrix task fixtures (coverity + fips-check scenario)
- Unit tests for `extract_all_errors_from_logs` with multi-error logs
- Update existing `test_failed_tr_found_via_kubearchive` to cover multiple failed TaskRuns
- Integration test with the real vllm pipeline run data as a fixture (Coverity + fips-check-0 s390x)

## Not in scope

- Changing the AI prompt structure (the current prompt with per-platform summary is adequate)
- Changing the database schema (error_message remains a single string field)
- Retry/rebuild logic
