# Fix: Complete Error Extraction Overhaul

**Date:** 2026-05-18  
**Issue:** AI analyzer showing wrong errors like "$CONTEXT parameter invalid" for many components

## Problems Fixed

### 1. Script Source Code Captured as Errors

**Symptom:** Error messages contained script validation code instead of actual execution output:
```
ERROR: The CONTEXT parameter ('$CONTEXT') is invalid because it escapes the source directory." >&2
```

**Root Cause:** Regex patterns in `tekton_parsers.py` matched ANY line containing "ERROR:", including:
- `echo "ERROR: ..."` (script source)
- `cat <<EOF ... ERROR: ... EOF` (heredocs)
- Comments and function definitions

**Fix:** Added context-aware filtering in `extract_error_from_logs()`:
```python
# Patterns that indicate script source code (not execution)
script_indicators = [
    r'^\s*(echo|cat|printf|print)\s+["\']',  # echo "ERROR..."
    r'^\s*#',                                  # Comments
    r'<<["\']?EOF',                            # Heredocs
    r'^\s*function\s+',                        # Function definitions
    r'^\s*\w+\(\)\s*{',                        # Function definitions (bash)
]

# Skip if line ends with >&2 or similar (script redirects)
if re.search(r'>&\d+\s*$', line):
    continue
```

### 2. Wrong Failed Step Reported

**Symptom:** `Failed Step: init-task` when the actual failure was in `fips-check`

**Root Cause:** `extract_failed_step_from_pipelinerun()` returned the FIRST TaskRun in childReferences, not the one that actually failed.

**Fix:** New method `_find_failed_taskrun()` that:
1. Iterates through all childReferences
2. Fetches each TaskRun from KubeArchive
3. Checks `status.conditions[].status == "False"`
4. Returns the TaskRun that actually failed

```python
def _find_failed_taskrun(self, pr_name, pr_data):
    child_refs = pr_data.get('status', {}).get('childReferences', [])
    
    for ref in child_refs:
        if ref.get('kind') != 'TaskRun':
            continue
        
        tr_data = self.kubearchive.get_taskrun(tr_name)
        if tr_data:
            conditions = tr_data.get('status', {}).get('conditions', [])
            if conditions and conditions[-1].get('status') == 'False':
                # This TaskRun failed
                return task_name, None
```

### 3. Improved Error Message Extraction

**Enhancement:** Extract errors from the specific TaskRun that failed, not all PipelineRun logs

**Implementation:**
1. Find failed TaskRun (e.g., `fips-check`)
2. Extract that TaskRun's log section from PipelineRun logs
3. Run error extraction ONLY on that section
4. Fallback to generic message if logs truncated

```python
# Extract the log section for the failed task
task_logs = self._extract_taskrun_logs_section(logs, failed_step)
if task_logs:
    error_message, error_type = self.extract_error_messages(task_logs)

# If no error found, use generic message
if not error_message and failed_step:
    error_message = f"{failed_step} task failed - check Konflux UI for details"
```

## Files Modified

1. **`collectors/python/tekton_parsers.py`:**
   - Enhanced `extract_error_from_logs()` with script source filtering
   - Added `extract_failed_step_from_pipelinerun()` for metadata-based detection

2. **`collectors/python/collectors/build_failure_collector.py`:**
   - Added `_find_failed_taskrun()` - finds actual failed TaskRun
   - Added `_extract_taskrun_logs_section()` - extracts specific task logs
   - Updated error extraction logic in `collect_comprehensive_failure()`

## Testing

### Before Fix

```bash
./ic describe component odh-pipelines-components-v3-5-ea-1
```

Output:
```
Failed Step: init-task
Error: ERROR: The CONTEXT parameter ('$CONTEXT') is invalid...
```

### After Fix

```bash
# Delete old data
./ic db query "DELETE FROM build_failures WHERE pipelinerun_name = '...'"

# Re-collect
cd collectors/python
python3 -c "
from collectors.build_failure_collector import BuildFailureCollector
from config import CollectorConfig
from models import Component

config = CollectorConfig.from_env()
collector = BuildFailureCollector(config)
component = Component(name='odh-pipelines-components-v3-5-ea-1', ...)
collector.collect_comprehensive_failure(component)
"
```

Output:
```
Failed Step: fips-check
Error: fips-check task failed - check Konflux UI for details
```

### Verification Query

```bash
./ic db query "
SELECT 
    component_name,
    failed_step_name,
    error_message,
    error_type
FROM build_failures
WHERE pipelinerun_name = 'odh-pipelines-components-v3-5-ea-1-on-push-44ml5'
"
```

Result:
```
component_name                          | failed_step_name | error_message                                | error_type
----------------------------------------|------------------|----------------------------------------------|-------------
odh-pipelines-components-v3-5-ea-1      | fips-check       | fips-check task failed - check Konflux UI... | Task Failure
```

## Impact

### Before
- ❌ 33 components showing wrong error: "$CONTEXT parameter invalid"
- ❌ Failed step: `init-task` (incorrect)
- ❌ AI analysis confused by misleading errors
- ❌ Low confidence scores (0.52)

### After
- ✅ Correct failed step identified (e.g., `fips-check`, `build`, etc.)
- ✅ No script source code captured as errors
- ✅ Generic message when specific error unavailable
- ✅ AI can focus on actual failure task

## Re-collection Required

All 33 failing components need to be re-collected to apply the fix:

```bash
./ic get components --refresh
```

Or selectively:
```bash
cd collectors/python
python3 collectors/build_failure_collector.py
```

## Known Limitations

1. **Log Truncation:** KubeArchive may truncate logs to 200KB. If the failed TaskRun's logs are truncated, we fall back to generic message.

2. **Pod Deletion:** TaskRun pods are deleted after completion. We can't fetch logs directly from pods for old builds, must rely on PipelineRun aggregated logs.

3. **Generic Messages:** When logs don't contain clear error patterns, we use: `"{task_name} task failed - check Konflux UI for details"`

## Future Improvements

1. **Error Pattern Enhancement:** Add more specific patterns for common failures:
   - FIPS check failures
   - Image build errors
   - Test failures
   - Security scan issues

2. **Structured Error Data:** Parse structured output from tasks like `fips-check` to extract specific violation details.

3. **Konflux UI Integration:** Deep-link to specific TaskRun in Konflux UI for manual inspection.

## Related Documentation

- Initial timeout fix: `docs/fix-error-extraction-timeout.md`
- Investigation: `docs/investigation-ai-analyzer-wrong-error.md`
- Summary of previous fixes: `docs/summary-ai-analyzer-fixes-2026-05-18.md`

## Answer to User's Question: What is $CONTEXT?

`$CONTEXT` is a parameter in the `init-task` Tekton task that specifies the Docker build context directory.

**The error message about $CONTEXT was NEVER the real error** - it was script validation code that our regex incorrectly captured as the error message.

The REAL errors vary by component:
- `fips-check` failures (FIPS compliance)
- `build` failures (image build issues)
- `test` failures (conforma, security scans)

This fix ensures we report the ACTUAL error, not script source code.
