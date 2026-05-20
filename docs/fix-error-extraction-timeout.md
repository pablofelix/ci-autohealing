# Fix: Correct Error Extraction for Timeout Failures

## Problem

`build_failure_collector.py` line 316-317 always extracts error messages from logs using regex patterns, even for PipelineRun timeouts. This causes:

- Wrong error messages (e.g., script validation code instead of "timeout")
- Misleading AI analysis
- Low confidence in automated fixes

## Root Cause

```python
# src/collectors/build_failure_collector.py:316-317
error_message, error_type = self.extract_error_messages(logs) if logs else (None, None)
failed_step = self.extract_failed_step_from_logs(logs) if logs else None
```

This code runs regardless of `pr_info['status']`, which already knows if it's a Timeout, Failed, etc.

## Solution

Check the PipelineRun status **before** parsing logs:

```python
# In collect_comprehensive_failure(), replace lines 316-317 with:

from models import BuildStatus

# Check status reason first
if pr_info['status'] == BuildStatus.Timeout:
    # Timeout: no error message in logs, use timeout-specific message
    error_message = f"Build timed out after {duration}s" if duration else "Build timed out"
    error_type = "Timeout"
    failed_step = None  # Timeouts don't have a specific failed step
elif pr_info['status'] == BuildStatus.Cancelled:
    error_message = "Build was cancelled"
    error_type = "Cancelled"
    failed_step = None
else:
    # Failed or other: extract from logs
    error_message, error_type = self.extract_error_messages(logs) if logs else (None, None)
    failed_step = self.extract_failed_step_from_logs(logs) if logs else None
```

## Additional Fix: Improve Log-Based Error Extraction

The regex patterns in `tekton_parsers.py` match script source code. Add context awareness:

```python
# tekton_parsers.py:76

def extract_error_from_logs(logs):
    """Extract error message from build logs, avoiding script source code."""
    if not logs:
        return None, None
    
    # Skip lines that are clearly script definitions (echo, cat, etc.)
    lines = logs.split('\n')
    error_lines = []
    
    for i, line in enumerate(lines):
        # Skip if this looks like a script definition
        if re.match(r'\s*(echo|cat|printf)\s+"?ERROR:', line):
            continue
        
        # Check for actual error execution
        for pattern, error_type in _ERROR_PATTERNS:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                # Get some context (previous and next lines)
                start = max(0, i-2)
                end = min(len(lines), i+3)
                context = '\n'.join(lines[start:end])
                return context[:500], error_type
    
    return None, None
```

## Testing

After applying the fix, re-collect the failing build:

```bash
cd PROJECT_DIR/src
python3 -c "
from collectors.build_failure_collector import BuildFailureCollector
from config import CollectorConfig
from models import Component

config = CollectorConfig.from_env()
collector = BuildFailureCollector(config)

# Re-collect odh-trustyai-nemo-guardrails-server
component = Component(
    name='odh-trustyai-nemo-guardrails-server-v3-4-ea-1',
    repository_url='https://github.com/acme-org/NeMo-Guardrails',
    branch='acme-3.4-ea.1',
    namespace='NAMESPACE_PLACEHOLDER'
)

found, inserted, logs = collector.collect_comprehensive_failure(component)
print(f'Found: {found}, Inserted: {inserted}, Logs: {logs} chars')
"
```

Then verify in DB:

```sql
SELECT 
    pipelinerun_name,
    error_message,
    error_type,
    failed_step_name
FROM build_failures
WHERE pipelinerun_name = 'odh-trustyai-nemo-guardrails-server-v3-4-ea-1-on-push-nkwtw';

-- Expected:
-- error_message: "Build timed out after 7202s"
-- error_type: "Timeout"
-- failed_step_name: NULL
```

Finally, re-run AI analysis:

```bash
./ic ai analyze 1 --force
```

Expected result:
- Category: timeout or build_timeout
- Root cause: "The build timed out after 2 hours while building multi-arch packages (x86_64, arm64, ppc64le). The commit updated python-312 base image which may have introduced heavier dependencies."
- Recommended fix: "Increase pipeline timeout OR split multi-arch builds into parallel tasks OR investigate if the new python-312 image has performance issues"

## Files to Modify

1. `PROJECT_DIR/src/collectors/build_failure_collector.py` (line 316-317)
2. `PROJECT_DIR/src/tekton_parsers.py` (line 76-91) — optional improvement

## Related

- Investigation doc: `PROJECT_DIR/docs/investigation-ai-analyzer-wrong-error.md`
- Build status enum: `PROJECT_DIR/src/models.py` (BuildStatus)
