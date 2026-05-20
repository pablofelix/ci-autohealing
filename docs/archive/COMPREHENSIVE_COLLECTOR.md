# Comprehensive CI Failure Collector

## Overview

The comprehensive collector (`collect_comprehensive.py`) is optimized for **problem solving** - collecting ALL information needed for both humans and AI to understand and fix CI failures.

## What It Collects

### For Each Component:
- ✅ **Last (most recent) failure** - Current build status
- ✅ **Comprehensive logs** from all TaskRuns and steps
- ✅ **Commit details**:
  - SHA (full and short)
  - GitHub commit URL (clickable link)
  - Commit message
  - Commit author
- ✅ **Failed task/step identification**:
  - Which TaskRun failed
  - Which step failed
  - Exit code
- ✅ **Error extraction**:
  - Error messages from logs
  - Error type (Build Error, Test Failure, Timeout, etc.)
- ✅ **Build timing**:
  - Start time
  - Completion time
  - Duration in seconds
- ✅ **UI Links**:
  - Konflux UI logs URL (full visualization)
  - Pipeline configuration URL
- ✅ **Pull Request info** (if applicable):
  - PR number
  - PR URL

## Why Comprehensive?

### For Humans:
- **Easy triage**: All info in one place
- **Quick debugging**: Error messages extracted
- **Context**: Commit details show what changed
- **Links**: One-click access to Konflux UI

### For AI:
- **Complete context**: Full logs + metadata
- **Structured data**: Error types, failed steps
- **Traceable**: Commit SHA links to code
- **Automated fixes**: All data needed for analysis

## Usage

### Manual Run

```bash
cd src

# Collect all components
python3 collect_comprehensive.py

# Collect specific components
python3 collect_comprehensive.py --components-file my_components.txt

# Collect first N components (for testing)
python3 collect_comprehensive.py --limit 5
```

### Automatic (Cron)

The cron job runs every 15 minutes:

```bash
# Check crontab
crontab -l

# Logs stored in:
ls -lh logs/cron/collect-comprehensive-*.log
```

## Example Output

```
======================================================================
Comprehensive CI Failure Collection
======================================================================

[1/8] odh-trustyai-nemo-guardrails-server-v3-4
  Latest failure: odh-trustyai-nemo-guardrails-server-v3-4-on-push-q8zb5
  → Fetching comprehensive logs...
  → Fetching PipelineRun metadata...
  ✓ Updated with 33656 chars of logs

[2/8] odh-spark-operator-v3-4
  Latest failure: odh-spark-operator-v3-4-on-pull-request-w2qrs
  → Fetching comprehensive logs...
  → Fetching PipelineRun metadata...
  ✓ Inserted with 0 chars of logs

...

======================================================================
Collection Complete
======================================================================
Components scanned: 8
Failures found: 8
New failures inserted: 2
Logs collected: 8
Duration: 245.3s
```

## Database Schema

All data is stored in `build_failures` table:

| Field | Purpose | Example |
|-------|---------|---------|
| `pipelinerun_name` | Unique identifier | `odh-spark-operator-v3-4-on-push-abc123` |
| `component_name` | Component | `odh-spark-operator-v3-4` |
| `commit_sha` | Full commit SHA | `5df4295783ebecf44696d8179bca6b907bdeffd4` |
| `commit_short_sha` | Short SHA | `5df42957` |
| `commit_url` | GitHub URL | `https://github.com/.../commit/5df42957...` |
| `commit_message` | Commit message | `Merge remote-tracking branch...` |
| `commit_author` | Who committed | `incoming` |
| `konflux_url` | Konflux UI logs | `https://konflux-ui.apps.../pipelineruns/.../logs` |
| `logs_full_url` | Pipeline config | URL to pipeline definition |
| `pr_number` | PR number | `123` |
| `pr_url` | PR URL | `https://github.com/.../pull/123` |
| `error_message` | Extracted error | `ERROR: python.extension_module...` |
| `error_type` | Error category | `Build Error` |
| `failed_step_name` | Failed step | `build-container` |
| `build_duration_seconds` | How long | `450` |
| `build_logs` | Full logs | `===== TaskRun: ... / Step: ... =====\n...` |

## Querying the Data

### View comprehensive data for a component

```bash
./ic describe component odh-spark-operator-v3-4
```

### SQL queries

```bash
# Get all data for a failure
./ic db query "
SELECT 
    pipelinerun_name,
    commit_short_sha,
    commit_message,
    error_type,
    failed_step_name,
    build_duration_seconds,
    konflux_url,
    commit_url
FROM build_failures
WHERE component_name = 'odh-spark-operator-v3-4'
ORDER BY first_detected_at DESC
LIMIT 1
"

# Components with errors extracted
./ic db query "
SELECT 
    component_name,
    error_type,
    COUNT(*) as count
FROM build_failures
WHERE error_message IS NOT NULL
GROUP BY component_name, error_type
ORDER BY count DESC
"
```

## Log Collection Strategy

### Multi-Method Approach

1. **Active pods first** (fastest):
   - Gets logs from running/recent pods
   - Tail last 5000 lines per container
   - Up to 10 pods per PipelineRun
   
2. **KubeArchive fallback** (for archived):
   - Queries KubeArchive API
   - Gets archived PipelineRuns
   - Extracts TaskRuns and pod logs

### Log Size Limits

- **200KB per PipelineRun** (increased from 100KB)
- Focuses on failed steps
- Tail-based to get most recent output

## Error Detection

### Automatic Error Extraction

The collector automatically detects:

| Pattern | Error Type |
|---------|-----------|
| `ERROR: ...` | Build Error |
| `FAIL: ...` / `FAILED` | Test Failure |
| `exit code N` | Exit Code |
| `timeout` / `timed out` | Timeout |
| `resource not found` / `404` | Resource Not Found |
| `FATAL: ...` | Fatal Error |

### Example

From logs:
```
ERROR: python.extension_module keyword argument 'dependencies' 
       was of type array[str] but should have been array[Dependency]
```

Extracted:
- **error_message**: `ERROR: python.extension_module keyword argument 'dependencies'...`
- **error_type**: `Build Error`

## Integration with Triage Workflow

```bash
# 1. See what's failing
./ic triage

# 2. Understand why (uses comprehensive data)
./ic why odh-spark-operator-v3-4

# 3. Deep analysis
./ic analyze odh-spark-operator-v3-4

# 4. View full details with all comprehensive data
./ic describe component odh-spark-operator-v3-4
```

## Comparison with Simple Collector

| Feature | Simple (`collect_failures.py`) | Comprehensive (`collect_comprehensive.py`) |
|---------|-------------------------------|------------------------------------------|
| **Focus** | All failures (up to 10 per component) | Last failure per component |
| **Commit details** | ❌ No | ✅ Yes (SHA, URL, message, author) |
| **Error extraction** | ❌ No | ✅ Yes (automatic pattern detection) |
| **Failed step** | ❌ No | ✅ Yes (from logs) |
| **Build duration** | ❌ No | ✅ Yes (calculated from times) |
| **PR info** | ❌ No | ✅ Yes (number, URL) |
| **Konflux URL** | Partial | ✅ Complete with app name |
| **Pipeline config URL** | ❌ No | ✅ Yes |
| **Log size** | 100KB | 200KB |
| **Update existing** | ❌ Skip | ✅ Updates with new data |

## When to Use

### Use Comprehensive Collector When:
- ✅ You want complete troubleshooting data
- ✅ AI analysis is planned
- ✅ You need commit traceability
- ✅ Error patterns are important
- ✅ Building a knowledge base

### Use Simple Collector When:
- ✅ You want historical data
- ✅ Tracking all failures over time
- ✅ Basic log collection is enough
- ✅ Running initial database population

## Performance

- **~40-60s per component** (depending on log availability)
- **Parallel potential**: Can be enhanced with async/await
- **Network-bound**: Most time spent on API calls
- **Database efficient**: Uses COALESCE for updates

## Configuration

### Environment Variables

```bash
# .env file
NAMESPACE=NAMESPACE_PLACEHOLDER
APPLICATION_NAME=acme-v2-0
DB_HOST=localhost
DB_PORT=5433
KUBEARCHIVE_API_URL=https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN
```

### Components File

```bash
# components.txt
odh-spark-operator-v3-4
odh-model-registry-v3-4
odh-trustyai-service-v3-4
```

## Troubleshooting

### No logs collected?

**Reason**: Logs deleted from both active pods and KubeArchive

**Solution**: Run collector more frequently (15-min cron should help)

### No commit details?

**Reason**: PipelineRun not in KubeArchive

**Solution**: Data only available while PipelineRun is in KubeArchive (~24-48h)

### Slow collection?

**Reason**: Network timeouts, API rate limits

**Solution**: 
- Reduce number of components
- Use `--limit` flag for testing
- Run during off-peak hours

## Future Enhancements

### Planned:
- [ ] Async/await for parallel component processing
- [ ] Retry logic with exponential backoff
- [ ] Prometheus metrics export
- [ ] Detailed TaskRun breakdown storage
- [ ] Image digest and vulnerability data

### AI Integration:
- [ ] Automatic error classification
- [ ] Similar failure detection
- [ ] Fix suggestion generation
- [ ] Root cause analysis

## Summary

The comprehensive collector provides **everything needed to solve CI failures**:

1. ✅ **What failed**: Component, PipelineRun name
2. ✅ **When**: Timestamps, duration
3. ✅ **Where**: Failed task, failed step
4. ✅ **Why**: Error messages, error types
5. ✅ **How**: Full logs, Konflux UI link
6. ✅ **What changed**: Commit SHA, message, author, GitHub link
7. ✅ **Context**: PR info, pipeline config URL

**Perfect for both human triage and AI analysis!** 🎯
