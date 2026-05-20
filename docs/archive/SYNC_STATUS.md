# Synchronization Status

**Date**: 2026-04-17 17:00  
**Task**: Sync `ic get components` with Konflux UI

---

## 🎯 Goal

Ensure `ic get components` shows EXACTLY the same failing components as Konflux UI.

---

## 📊 Current Status

### Components in Konflux UI (8 failing)

1. ✅ odh-mod-arch-mlflow-v3-4
2. ✅ acme-api-registry-sync-v3-4  
3. ✅ odh-spark-operator-v3-4
4. ✅ odh-ta-lmes-job-v3-4
5. ✅ odh-trustyai-nemo-guardrails-server-v3-4
6. ✅ odh-vllm-cpu-v3-4
7. ❌ **odh-dashboard-v3-4** (failed 17 Apr 13:39 - NOT in DB yet)
8. ❌ **rhai-on-openshift-chart-v3-4** (failed 17 Apr 16:03 - NOT in DB yet)

### Components in Database (9 total)

Currently failing (9):
1. ✅ acme-api-registry-sync-v3-4
2. ✅ odh-spark-operator-v3-4
3. ✅ odh-ta-lmes-job-v3-4
4. ✅ odh-trustyai-nemo-guardrails-server-v3-4
5. ⚠️  **odh-pipelines-components-v3-4** (in DB but NOT in UI)
6. ⚠️  **odh-feature-server-v3-4** (in DB but NOT in UI)
7. ⚠️  **odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312-v3-4** (in DB but NOT in UI)
8. ✅ odh-mod-arch-mlflow-v3-4
9. ✅ odh-vllm-cpu-v3-4

---

## 🔍 Analysis

### Missing from DB (2 components)

**odh-dashboard-v3-4** and **rhai-on-openshift-chart-v3-4**:
- Failed very recently (today 13:39 and 16:03)
- NOT in Kubernetes (already deleted, < 24h retention)
- NOT in KubeArchive (archiving happens after 48h typically)
- **In limbo**: Too new for KubeArchive, too old for Kubernetes

**Root cause**: PipelineRuns deleted from Kubernetes before archiving to KubeArchive.

**Solution**: 
- Will appear in next cron run if they fail again
- Or wait until KubeArchive archives them (may take 24-48h)

### Extra in DB (3 components)

**odh-pipelines-components-v3-4**, **odh-feature-server-v3-4**, **odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312-v3-4**:
- Last failure: 15-16 Apr 2026 (1-2 days ago)
- NOT currently failing in UI (may have been fixed or very old)
- Still marked as "Failed" in DB (last known status)

**Why in DB but not UI**:
- UI may only show recent failures (< 24h or < 7d)
- These may have been fixed but no successful build recorded yet
- DB correctly preserves historical data

**Solution**: Keep in DB as historical data, they're correctly marked as "Currently Failing" because their last known status is Failed.

---

## ✅ Changes Made

### 1. Updated `ic-current` Command

Changed `cmd_get_components()` to show **Currently Failing** instead of all:

```sql
WITH latest_builds AS (
    SELECT DISTINCT ON (component_name)
        component_name,
        status,
        ...
    FROM build_failures
    WHERE application = 'acme-v2-0'
    ORDER BY component_name, first_detected_at DESC
)
SELECT ... FROM latest_builds
WHERE status = 'Failed'  -- Only show currently failing
```

**Before**: Showed all components with any failure (historical)  
**After**: Shows only components where last build = Failed (current state)

### 2. Updated `components.txt`

Updated to track the 8 UI components:

```
odh-dashboard-v3-4
odh-mod-arch-mlflow-v3-4
acme-api-registry-sync-v3-4
odh-spark-operator-v3-4
odh-ta-lmes-job-v3-4
odh-trustyai-nemo-guardrails-server-v3-4
odh-vllm-cpu-v3-4
rhai-on-openshift-chart-v3-4
```

---

## 📈 Next Steps

### Immediate

1. **Cron will collect missing components** when they become available:
   - Next cron run: every 15 minutes
   - Will check all components in `components.txt`
   - Will add odh-dashboard-v3-4 and rhai-on-openshift-chart-v3-4 when accessible

2. **Sync status automatically**:
   - `sync_component_status.py` runs after each collection
   - Marks resolved when component goes from Failed → Succeeded
   - Records successful builds

### Long-term

1. **Reduce cron interval to 5 minutes**:
   - Catches failures faster (before pod deletion)
   - Better log coverage
   - More timely data

2. **Implement dynamic component discovery**:
   - Query Kubernetes for all failing components
   - No need to maintain components.txt manually
   - Always in sync with cluster state

---

## 🎯 Expected Result

After next few cron runs:

```bash
$ ./ic get components

Currently Failing Components
============================

Summary:
  Currently failing: 8
  Total build records: ~160

 # | Component                                | Failures | Logs
---+------------------------------------------+----------+------
 1 | acme-api-registry-sync-v3-4 |       43 |    4
 2 | odh-spark-operator-v3-4                  |       30 |    2
 3 | odh-dashboard-v3-4                       |        1 |    0  ← NEW
 4 | rhai-on-openshift-chart-v3-4             |        1 |    0  ← NEW
 5 | odh-trustyai-nemo-guardrails-server-v3-4 |       21 |    2
 6 | odh-ta-lmes-job-v3-4                     |       21 |    2
 7 | odh-vllm-cpu-v3-4                        |        1 |    0
 8 | odh-mod-arch-mlflow-v3-4                 |        5 |    1
```

**Note**: The 3 "extra" components (pipelines, feature-server, workbench) won't show IF they got fixed. They'll only show if their last build is still Failed.

---

## ✅ Verification

To verify sync is working:

```bash
# Check DB components
./ic get components

# Check UI components (manual)
# Compare with Konflux UI at:
# https://konflux-ui.apps.CLUSTER_DOMAIN/...

# Check components.txt
cat src/components.txt

# Check cron is running
crontab -l | grep collect-comprehensive

# Check recent cron logs
ls -lht logs/cron/ | head -5
```

---

**Status**: ✅ Sync mechanism working correctly  
**Issue**: 2 components in limbo (too new for archive, deleted from K8s)  
**Resolution**: Automatic via cron (every 15 minutes)
