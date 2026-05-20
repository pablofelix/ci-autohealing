# Summary: AI Analyzer Fixes - 2026-05-18

## Problems Found & Fixed

### 1. Wrong Error Messages for Timeouts

**Problem:** Build failures with `PipelineRunTimeout` were showing wrong error messages extracted from script source code instead of timeout-specific messages.

**Example:**
- **Before:** `error_message = "ERROR: The CONTEXT parameter ('$CONTEXT') is invalid..."`  
- **After:** `error_message = "Build timed out"`

**Root Cause:** The error extraction logic (`build_failure_collector.py:316-317`) always parsed logs with regex patterns, matching script definitions like `echo "ERROR: ..."` instead of actual execution output.

**Fix Applied:**
1. Modified `get_last_failed_pipelinerun()` to return the raw `reason` from PipelineRun conditions
2. Added logic to check `failure_reason` before parsing logs
3. For `PipelineRunTimeout` and `TaskRunTimeout`: use timeout-specific message
4. For `PipelineRunCancelled`: use cancellation message
5. Only parse logs for actual `Failed` status

**Files Modified:**
- `PROJECT_DIR/src/collectors/build_failure_collector.py`

**Impact:**
- AI analysis now correctly identifies timeout issues
- Category changed from `config_error` (wrong) to `resource_limit` (correct)
- Root cause is accurate instead of misleading

### 2. AI Analysis Improvement

**Before Fix:**
```
Component: odh-trustyai-nemo-guardrails-server-v3-4-ea-1
Category: config_error
Confidence: 0.52
Root cause: The failed step is init-task with error "$CONTEXT parameter invalid", 
            but the build logs show the actual build completed successfully
```

**After Fix:**
```
Component: odh-trustyai-nemo-guardrails-server-v3-4-ea-1
Category: resource_limit
Confidence: 0.55
Root cause: The pipeline timed out, but the build logs show both the x86-64 and 
            aarch64 builds appear to have completed successfully — suggesting the 
            timeout occurred in a later pipeline task or the overall Pipeline
```

**Test Results:**
```bash
# Re-collect the failure
python3 src/collectors/build_failure_collector.py

# Verify DB
SELECT error_message, error_type FROM build_failures 
WHERE pipelinerun_name = 'odh-trustyai-nemo-guardrails-server-v3-4-ea-1-on-push-nkwtw';

# Result:
# error_message: "Build timed out"
# error_type: "Timeout"

# Re-run AI analysis
./ic ai analyze 1 --force

# Result: Category changed to resource_limit, accurate root cause
```

## Documentation Created

1. **Investigation:** `PROJECT_DIR/docs/investigation-ai-analyzer-wrong-error.md`  
   - Detailed analysis of the wrong error message problem
   - Example of actual vs expected behavior
   - Database state verification

2. **Fix Guide:** `PROJECT_DIR/docs/fix-error-extraction-timeout.md`  
   - Step-by-step fix instructions
   - Code examples before/after
   - Testing procedure

3. **Summary:** This document

4. **Memory:** 
   - `project_rhoai-ea1-fips-failures.md` — FIPS compliance issues context
   - `reference_rhoai-release-repos.md` — GitHub repos overview
   - `reference_fips-compliance-guide.md` — FIPS requirements reference

## Remaining Tasks

### Task #3: Verify KubeArchive Build Failure Collection

Status: In progress

Need to verify:
- Are all Konflux UI failures being collected via KubeArchive?
- The user sees 6 failures in Konflux UI but only 1-2 in `ic get components`
- Reason: The other 4 have newer successful builds (collector only tracks latest per component)

**Next Steps:**
- Document this "latest-only" design decision
- Consider if historical failure tracking is needed
- Add explanation to `ic get components` output about why some UI failures aren't shown

### Task #4: Review Conforma Errors

Status: In progress

Need to check:
- Are conforma failures being properly collected?
- `ic get alerts` shows 34 conforma failures — verify they're accurate
- Review error extraction logic for conforma (might have similar issues)

**Next Steps:**
- Run `./ic get conforma --refresh`
- Sample a few conforma failures and verify error messages are correct
- Check if FIPS-related conforma failures are properly categorized

### Task #5: Check Konflux API Alternative

Status: In progress

Need to investigate:
- Is there a Konflux REST API that can supplement KubeArchive?
- Current system uses KubeArchive exclusively
- Konflux UI must be getting data from somewhere — find that API

**Next Steps:**
- Check Konflux documentation for API endpoints
- Look for `konflux_client.py` implementations
- Compare data quality: KubeArchive vs Konflux API

## Recommendations

1. **Deploy the fix to production** after testing with a few more components
2. **Add integration test** for timeout error extraction
3. **Audit other error extraction patterns** — the regex-based approach might have similar issues elsewhere
4. **Consider adding retry logic** for builds that timeout due to transient issues
5. **Monitor AI analysis confidence** — track if it improves after this fix

## Related Issues

- FIPS compliance failures are being handled by component teams (Rishab tracking)
- Disk space issue resolved by switching to D160 architecture in konflux-central
- Commit context is correctly captured — no issues found there
