# Auto-Sync Feature

## Overview

The `ic get components` command now **automatically detects and syncs** with the Konflux cluster.

---

## How It Works

Every time you run `ic get components`, it:

1. **Checks cluster connection** (via `oc whoami`)
2. **Queries Konflux** for currently failing components
3. **Compares with database**
4. **Auto-syncs if needed** (launches collector in background)
5. **Shows sync status** prominently

---

## Sync Status Indicators

### ✅ Scenario 1: In Sync

```
✓ Sync Status: IN SYNC

Summary:
  Currently failing: 8
  Total build records: 155

 # |                 Component                | Failures | Logs
---+------------------------------------------+----------+------
 1 | odh-spark-operator-v3-4                  |       30 |    2
 2 | acme-api-registry-sync-v3-4 |       43 |    5
 ...
```

**What it means**: Database matches Konflux - everything is synchronized.

---

### ⚠️ Scenario 2: Out of Sync (Auto-fixing)

```
⚠ Sync Status: OUT OF SYNC
  Missing in DB: 2 component(s)
    - rhai-on-openshift-chart-v3-4
    - acme-fbc-fragment-v3-4
  Extra in DB (resolved): 3 component(s)
    - odh-pipelines-components-v3-4
    - odh-feature-server-v3-4
    - odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312-v3-4
  → Auto-syncing in background...

  ✓ Sync started (PID: 12345)
  Run 'ic get components' again in ~2 minutes to see updated data

Summary:
  Currently failing: 9
  Total build records: 155
  ...
```

**What it means**:
- **Missing in DB**: New failures in Konflux not yet in database
- **Extra in DB**: Components that were failing but now resolved
- **Auto-sync**: Collector is running in background to update database

**What to do**: Wait ~2 minutes, then run `ic get components` again.

---

### 🔴 Scenario 3: Disconnected from Cluster

```
⚠ Cluster Status: DISCONNECTED
  Not logged into OpenShift cluster
  Database is OUT OF SYNC with Konflux
  → Login to cluster: oc login <cluster-url>

Summary:
  Currently failing: 9
  Total build records: 155
  ...
```

**What it means**: Not logged into OpenShift - cannot verify sync status.

**What to do**:
```bash
# Login to cluster
oc login https://api.CLUSTER_DOMAIN

# Then run again
ic get components
# Will now auto-sync if needed
```

---

## Manual Sync

If you want to force a sync without running `ic get components`:

```bash
# Full sync
./cron/collect-comprehensive.sh

# Or just check status
python3 src/check_sync_status.py | jq
```

---

## How Auto-Sync Works

### Detection
1. Queries Kubernetes for PipelineRuns with `Failed` status
2. Groups by component to get latest status
3. Compares with database (`DISTINCT ON` latest build per component)

### Auto-Fix
When out of sync, automatically runs:
```bash
./cron/collect-comprehensive.sh &
```

This:
- Collects new failures
- Updates logs
- Marks resolved components
- Syncs all metadata

### Background Execution
- Runs in background (non-blocking)
- Shows PID so you can monitor
- Takes ~1-2 minutes to complete
- Safe to run multiple times (idempotent)

---

## Advantages

### Before (Manual Sync)
```bash
# 1. Check Konflux UI manually
# 2. Notice missing component
# 3. Add to components.txt
# 4. Run ./cron/collect-comprehensive.sh
# 5. Wait
# 6. Run ic get components
```

### After (Auto-Sync)
```bash
# 1. Run ic get components
# → Automatically detects, syncs, and tells you to wait
# 2. Run ic get components again in 2 min
# → Now synchronized
```

---

## Technical Details

### Files Involved
- `src/check_sync_status.py` - Sync detection script
- `ic` (cmd_get_components) - Modified to call sync check
- `cron/collect-comprehensive.sh` - Auto-launched when out of sync

### Sync Detection Algorithm
```python
# Get failing components from cluster
cluster_components = get_failing_components_from_cluster()

# Get failing components from DB
db_components = get_components_from_db()

# Calculate diff
missing_in_db = cluster_components - db_components
extra_in_db = db_components - cluster_components

# Sync if there are differences
if missing_in_db or extra_in_db:
    launch_sync()
```

### Performance
- Sync check: < 1 second
- Auto-sync (background): ~1-2 minutes
- No blocking - can continue working

---

## Troubleshooting

### "Cluster Status: DISCONNECTED"

**Cause**: Not logged into OpenShift

**Fix**:
```bash
oc login https://api.CLUSTER_DOMAIN
```

### "Out of sync" persists after 5 minutes

**Cause**: Collector might have failed

**Fix**:
```bash
# Check if collector is still running
ps aux | grep collect-comprehensive

# Check logs
tail -50 logs/cron/$(ls -t logs/cron/ | head -1)

# Run manual sync
./cron/collect-comprehensive.sh
```

### False "missing in DB"

**Cause**: Collector hasn't finished yet

**Wait**: Give it 2-3 minutes, then check again

---

## Example Workflow

### Day 1: Initial Setup
```bash
$ ic get components

⚠ Cluster Status: DISCONNECTED
  Not logged into OpenShift cluster
  ...

$ oc login https://api.CLUSTER_DOMAIN
Login successful.

$ ic get components

⚠ Sync Status: OUT OF SYNC
  Missing in DB: 8 component(s)
    - odh-spark-operator-v3-4
    - acme-api-registry-sync-v3-4
    ...
  → Auto-syncing in background...
  ✓ Sync started (PID: 45231)

[wait 2 minutes]

$ ic get components

✓ Sync Status: IN SYNC
  Currently failing: 8
  ...
```

### Day 2: New Failure Appears
```bash
$ ic get components

⚠ Sync Status: OUT OF SYNC
  Missing in DB: 1 component(s)
    - acme-fbc-fragment-v3-4
  → Auto-syncing in background...
  ✓ Sync started (PID: 67890)

[wait 2 minutes]

$ ic get components

✓ Sync Status: IN SYNC
  Currently failing: 9
  ...
```

### Day 3: Component Gets Fixed
```bash
$ ic get components

⚠ Sync Status: OUT OF SYNC
  Extra in DB (resolved): 1 component(s)
    - odh-spark-operator-v3-4
  → Auto-syncing in background...

[wait 2 minutes]

$ ic get components

✓ Sync Status: IN SYNC
  Currently failing: 8
  ...
```

---

## Configuration

### Disable Auto-Sync

If you don't want automatic syncing, comment out this line in `ic`:

```bash
# ic file, line ~265
# nohup "$SCRIPT_DIR/cron/collect-comprehensive.sh" > /dev/null 2>&1 &
```

Then it will only show the sync status without auto-fixing.

### Change Sync Behavior

Edit `src/check_sync_status.py` to customize:
- Which components to include
- Sync criteria
- Error handling

---

## Benefits

✅ **Always up-to-date**: Database stays in sync automatically  
✅ **Zero manual work**: No need to manually update components.txt  
✅ **Clear visibility**: Always know if you're in sync or not  
✅ **Non-blocking**: Sync happens in background  
✅ **Self-healing**: Detects and fixes drift automatically  

---

**Created**: 2026-04-20  
**Last Updated**: 2026-04-20
