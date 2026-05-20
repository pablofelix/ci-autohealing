# Investigation: AI Analyzer Showing Wrong Error Message

**Date:** 2026-05-18  
**Component:** odh-trustyai-nemo-guardrails-server-v3-4-ea-1  
**PipelineRun:** odh-trustyai-nemo-guardrails-server-v3-4-ea-1-on-push-nkwtw

## Problem

The AI analyzer reports:
- **Error message:** `"ERROR: The CONTEXT parameter ('$CONTEXT') is invalid because it escapes the source directory."`
- **Root cause analysis:** "The failed step is init-task with error about $CONTEXT, but the build logs show the actual build completed successfully"
- **Confidence:** 0.52 (low)

But this is **incorrect**.

## Actual Failure

Querying KubeArchive shows the real failure:

```
PipelineRun: odh-trustyai-nemo-guardrails-server-v3-4-ea-1-on-push-nkwtw
Date: 2026-04-22T11:04:32Z
Status: False
Reason: PipelineRunTimeout
```

The build **timed out after 2 hours**. It did NOT fail with a $CONTEXT error.

## Root Cause of Wrong Error Message

### Database State

```sql
SELECT failed_task_name, failed_step_name, error_message
FROM build_failures 
WHERE pipelinerun_name = 'odh-trustyai-nemo-guardrails-server-v3-4-ea-1-on-push-nkwtw';

-- Result:
-- failed_task_name: NULL
-- failed_step_name: init-task
-- error_message: ERROR: The CONTEXT parameter ('$CONTEXT') is invalid because it escapes the source directory." >&2
```

### What Happened

1. The `build_logs` contain the **script code** from the `init-task` step that validates the CONTEXT parameter
2. The error extraction logic grabbed the `echo "ERROR: ..."` line from the script **source code**, not from actual execution output
3. The `init-task` step **passed** — it was just validating parameters
4. The actual failure was a **timeout** in a later build step (multi-arch build of python packages)

### Logs Show

```
===== TaskRun: odh-trustyai-nemo-gubf89fc0fa9c32fa84f89b36902be258d-init-task / Step: init-task =====
----- DEBUG INFORMATION -----
...build metadata...
----- PARSING IMAGE OUTPUT -----
...

===== TaskRun: ... / Step: init =====
Fetching cluster-config from konflux-info namespace...

===== TaskRun: ... / Step: clone =====
{"level":"info",...,"msg":"Successfully cloned https://github.com/acme-org/NeMo-Guardrails..."}

[1/8] STEP 1/9: FROM registry.access.redhat.com/ubi9/python-312@sha256:4a6f6abc...
[1/8] STEP 2/9: USER root
...
[2/8] STEP 1/6: FROM 39e3bb7f3a5cc52f67c71069030bd9b2dbcc1437f93ae63882831860315c7be2 AS openblas-builder
...
```

The logs show successful build steps progressing. The build ran for 2 hours building OpenBLAS and Python packages on multi-arch (x86_64, arm64, ppc64le) and then **timed out**.

## What the AI Sees

The AI prompt includes:
- `error_message`: wrong — shows script validation code
- `failed_step_name`: init-task — misleading, this step passed
- `build_logs`: correct — shows successful build steps
- `commit_context`: correct — shows only python-312 base image update (`09d1b54` → `4a6f6ab`)

The AI correctly concludes "the logs show successful build" but incorrectly tries to reconcile that with the wrong error message.

## Fix Required

The build failure collector needs to:

1. **Extract the actual failure reason** from `PipelineRun.status.conditions[-1].reason` (e.g., `PipelineRunTimeout`, `Failed`)
2. **Not parse `error_message` from logs when reason is `PipelineRunTimeout`** — there is no error message for timeouts
3. **If reason is `Failed`, find the actual failed TaskRun** and extract its error from the failed container's logs

### Current Behavior (Incorrect)

```python
# Somewhere in build_failure_collector.py or log extraction
# It's grabbing "ERROR: ..." text from logs without checking:
# - Is this from script source code or actual execution?
# - What is the PipelineRun.status.conditions[-1].reason?
```

### Correct Behavior

```python
conditions = pr.get('status', {}).get('conditions', [])
if conditions:
    last_condition = conditions[-1]
    reason = last_condition.get('reason')
    
    if reason == 'PipelineRunTimeout':
        error_message = f"Build timed out after {pipeline_timeout} (multi-arch builds in progress)"
        failed_step = "build (timeout)"
    elif reason == 'Failed':
        # Find which TaskRun failed
        # Extract error from that TaskRun's failed container
        error_message = extract_from_failed_taskrun(pr)
    else:
        # Succeeded or other
        pass
```

## Commit Context (Correctly Captured)

The commit that triggered this build:

- **PR #217:** chore(deps): update registry.access.redhat.com/ubi9/python-312 docker digest to 4a6f6ab
- **Change:** Updated python-312 base image from `09d1b54` to `4a6f6ab` in `Dockerfile.konflux`
- **Author:** konflux-internal-p02[bot] (automated dependency update)

This is correct and available to the AI. The problem is purely the wrong error_message.

## Impact

- AI analysis has low confidence (0.52) because evidence doesn't match
- AI conclusion is misleading ("$CONTEXT error but build succeeded")
- Human reviewers waste time investigating wrong root cause
- Automated fixes can't be generated for the real issue (timeout due to slow multi-arch build)

## Recommendation

1. Fix error extraction in `build_failure_collector.py` to check `PipelineRun.status.conditions[-1].reason`
2. Add special handling for `PipelineRunTimeout` (no error message, just timeout metadata)
3. For `Failed` reason, parse the actual failed TaskRun logs, not script source code
4. Add test: parse `odh-trustyai-nemo-guardrails-server-v3-4-ea-1-on-push-nkwtw` and verify it extracts `PipelineRunTimeout`
