# Changelog

All notable changes to the CI Auto-Healing system.

## [Unreleased] - 2026-04-17

### Fixed

#### Critical Bug Fix: Container Name Prefix in KubeArchive
**File**: `collectors/python/kubearchive_client.py:287`

- **Issue**: Logs were not being retrieved from KubeArchive due to incorrect container names
- **Root Cause**: Tekton prefixes step names with `"step-"` when creating containers in Pods
  - Step name in TaskRun: `build`
  - Actual container name in Pod: `step-build`
- **Solution**: Added automatic `step-` prefix when querying pod logs
  ```python
  # Before (incorrect):
  logs = self.get_pod_logs(pod_name, container=step, namespace=ns)
  
  # After (correct):
  container_name = f"step-{step}"
  logs = self.get_pod_logs(pod_name, container=container_name, namespace=ns)
  ```
- **Impact**: 
  - Before: All log queries returned `{"message":"resource not found"}`
  - After: Successfully retrieving logs from KubeArchive (18 failures with logs vs 12 before)
  - Components with updated logs: 6 additional components now have logs

### Added

#### Improved Log Display in `ic` Command
**File**: `ic-current`

- **Smart Error Context Extraction**: Instead of showing full logs, `ic 1` now shows:
  - Last 3 TaskRun sections (most relevant for debugging)
  - For large sections: first 5 lines + last 33 lines (where errors typically are)
  - Total TaskRun count (e.g., "Showing last 3 of 16 TaskRun section(s)")
  
- **New `--log` Flag**: View complete build logs when needed
  - `ic 1` → Shows error context (condensed, easier to scan)
  - `ic 1 --log` → Shows complete logs (all TaskRuns, all lines)
  - `ic describe component <name> --log` → Shows complete logs
  
- **Benefits**:
  - Faster triage: See relevant errors without scrolling through 200K lines
  - Complete access: Full logs available with `--log` flag
  - Better UX: Clear indicators and helpful tips

### Changed

- **Log Display Logic**: Restructured to show TaskRun sections instead of keyword-based error search
  - More reliable: Doesn't depend on error message formats
  - More context: Shows actual build progress and failures
  - Less noise: Avoids false positives from code comments or validation scripts

## Statistics

### Before Fix (2026-04-17 morning)
```
Total build records: 155
Unique components:   9
With logs:           12 (7.7%)
Without logs:        143 (92.3%)
```

### After Fix (2026-04-17 evening)
```
Total build records: 155
Unique components:   9
With logs:           18 (11.6%)
Without logs:        137 (88.4%)
```

### Improvement
- +50% increase in logs captured (12 → 18)
- +6 failures now have logs thanks to the container name fix

## Technical Details

### Why Logs Were Missing

The KubeArchive API **does** store pod logs, but we were querying with incorrect container names:

1. **TaskRun Definition**: Steps are named without prefix (e.g., `build`, `push`)
2. **Pod Implementation**: Tekton creates containers with `step-` prefix (e.g., `step-build`, `step-push`)
3. **Our Query**: Was using step names directly → no match → "resource not found"
4. **Fix**: Prefix step names with `step-` before querying

### Why Some Logs Still Missing

- **Pod Retention**: Kubernetes deletes pods after 24-48h
  - Most failures in DB are 2+ days old (April 15-16)
  - Pods already deleted, logs gone
- **Solution**: Cron job runs every 15 minutes to capture fresh failures
  - Catches new failures before pod deletion
  - Stores logs permanently in database

### Next Steps

1. **Reduce cron interval** to 5 minutes (better coverage)
2. **Implement log archival** to S3/MinIO for permanent storage
3. **Request Tekton Results API access** (stores logs persistently)
4. **Monitor log collection rate** to ensure we're capturing more failures

## Commands Reference

### New Usage Patterns

```bash
# Quick triage (condensed logs)
ic 1
ic 2
ic describe component odh-spark-operator-v3-4

# Full investigation (complete logs)
ic 1 --log
ic describe component odh-spark-operator-v3-4 --log

# Statistics
ic stats                    # Overall statistics
ic get components           # Currently failing components
ic triage                   # Triage dashboard
```

## Files Modified

- `collectors/python/kubearchive_client.py` - Fixed container name prefix
- `ic-current` - Added error context extraction and --log flag
- `ic-current` - Updated help text with new options

## Files Created

- `CHANGELOG.md` - This file
