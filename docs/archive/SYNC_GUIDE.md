# Component Synchronization Guide

## ⚠️ Prerequisites

**You must be logged into OpenShift** for the collector to work:

```bash
# Check if you're logged in
oc whoami

# If not logged in, you'll see:
# error: You must be logged in to the server (Unauthorized)

# Login to OpenShift
oc login <your-cluster-url>
```

---

## 🔄 Automatic Sync (Recommended)

### Option 1: Quick Add Components

```bash
# Add one or more components
./sync-components.sh rhai-on-openshift-chart-v3-4 acme-fbc-fragment-v3-4

# Then collect
./cron/collect-comprehensive.sh
```

### Option 2: Interactive Mode

```bash
# Run interactive sync
./sync-components.sh

# Paste component names from Konflux UI
# Press Ctrl+D when done

# Then collect
./cron/collect-comprehensive.sh
```

---

## 📋 Manual Sync Process

### Step 1: Check Konflux UI

Open: https://konflux-ui.apps.CLUSTER_DOMAIN/

Go to: **Applications → acme-v2-0 → Components**

Look for components with **"Build failed"** status.

### Step 2: Update components.txt

```bash
# Edit the file
vi collectors/python/components.txt

# Add missing components (one per line)
rhai-on-openshift-chart-v3-4
acme-fbc-fragment-v3-4
```

### Step 3: Run Collection

```bash
# Full sync (recommended)
./cron/collect-comprehensive.sh

# Or just collect failures
cd collectors/python
python3 collect_comprehensive.py
```

### Step 4: Verify

```bash
# Check database
./ic get components

# Should match Konflux UI
```

---

## 🔍 Troubleshooting

### "Component not found in cluster"

**Cause**: Not logged into OpenShift

**Solution**:
```bash
oc login <cluster-url>
./cron/collect-comprehensive.sh
```

### "No components discovered"

**Cause**: Missing Kubernetes credentials

**Solution**: Check `oc login` status and re-authenticate

### Database out of sync with UI

**Cause**: components.txt is missing new failing components

**Solution**:
```bash
# Quick add
./sync-components.sh <missing-component-name>

# Or edit manually
vi collectors/python/components.txt

# Then sync
./cron/collect-comprehensive.sh
```

---

## 📊 Current State (Example)

### In Konflux UI (failing):
- odh-mod-arch-mlflow-v3-4
- acme-api-registry-sync-v3-4
- odh-spark-operator-v3-4
- odh-ta-lmes-job-v3-4
- odh-trustyai-nemo-guardrails-server-v3-4
- odh-vllm-cpu-v3-4
- rhai-on-openshift-chart-v3-4
- acme-fbc-fragment-v3-4

### In Database (from `./ic get components`):
- acme-api-registry-sync-v3-4
- odh-spark-operator-v3-4
- odh-ta-lmes-job-v3-4
- odh-trustyai-nemo-guardrails-server-v3-4
- odh-pipelines-components-v3-4 (resolved in UI)
- odh-feature-server-v3-4 (resolved in UI)
- odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312-v3-4 (resolved in UI)
- odh-mod-arch-mlflow-v3-4
- odh-vllm-cpu-v3-4

### Missing from Database:
- rhai-on-openshift-chart-v3-4 ⚠️
- acme-fbc-fragment-v3-4 ⚠️

### To fix:
```bash
# 1. Make sure you're logged in
oc login

# 2. Components are already in components.txt, just collect
./cron/collect-comprehensive.sh

# 3. Verify
./ic get components
```

---

## 🔄 Automation

To fully automate this, you can:

1. **Set up token-based auth** (so you don't need to log in manually)
2. **Run cron every 15 minutes** (already configured)
3. **Monitor** for new components in Konflux UI

For now, the sync script helps you quickly add new components when you see them.
