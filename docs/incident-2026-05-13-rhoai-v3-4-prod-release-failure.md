# Incident Report: RHOAI v3.4 Production Release Failure

**Date:** 2026-05-13
**Release:** `acme-v2-0-prod-1778657574` (and retry `-1`)
**Snapshot:** `acme-v2-0-1778062796`
**Author:** mshikalw (Moulali)
**Namespace:** `NAMESPACE_PLACEHOLDER`
**Status:** FAILED — `verify-conforma` step-assert exit code 1

---

## Timeline

| Time (UTC) | Event |
|---|---|
| 2026-05-06 10:25 | Snapshot `acme-v2-0-1778062796` created (83 components) |
| 2026-05-13 07:48:34 | Release `acme-v2-0-prod-1778657574` created by mshikalw |
| 2026-05-13 07:48:44 | Managed pipeline `managed-qmgwd` starts in `releng-tenant` |
| 2026-05-13 08:04:13 | `verify-conforma` validate step begins fetching images — first error logged |
| 2026-05-13 08:10:07 | Pipeline completes: **FAILED** |
| 2026-05-13 08:01:41 | Retry release `acme-v2-0-prod-1778657574-1` created |
| 2026-05-13 08:24:18 | Retry pipeline `managed-zxk95` completes: **FAILED** (same error) |

---

## Root Cause

The `verify-conforma` task in the managed release pipeline (`rh-advisories.yaml`) failed with **6 violations**, all on the **`acme-operator-bundle-v3-4`** component. The violation rule was `olm.unmapped_references` — images referenced in the operator's CSV (ClusterServiceVersion) that are not present in the snapshot and not accessible in the production registry (`registry.redhat.io`).

### Conforma Results Summary

- **Successes:** 53,391
- **Failures:** 6
- **Warnings:** 5,400
- **Result:** FAILURE

All 6 violations were on the operator bundle image:
`quay.io/acme/acme-operator-bundle@sha256:4dc4cb613198b295d210600fdff2c866a2cbf381617a1bec6c4136857f3d43fa`

---

## The 6 Violations

### Violation 1: Component name typo in RPA (1 violation)

The ReleasePlanAdmission (RPA) at `konflux-release-data` had a typo in the component mapping:

- **Wrong name:** `odh-llm-d-batch-gateway-gc-rhel9-v3-4`
- **Correct name:** `odh-llm-d-batch-gateway-gc-v3-4` (no `rhel9` in the name)

This caused the image to be mapped to the wrong `registry.redhat.io` path, making it inaccessible:

```
registry.redhat.io/rhoai/odh-llm-d-batch-gateway-gc-rhel9@sha256:3989a63578337a83e244063d593fb4e5c01eb304f19823294cb20243549e4d76
```

**Error from KubeArchive logs:**
```
level=error msg="failed to fetch image" error="GET https://registry.redhat.io/v2/rhoai/odh-llm-d-batch-gateway-gc-rhel9/manifests/sha256:3989a63...: UNAUTHORIZED"
```

### Violations 2-6: RHAII vLLM images not released to production (5 violations)

The operator bundle CSV references vLLM serving runtime images from the **RHAII** (Red Hat AI Infrastructure) product. These images existed in staging but had **never been pushed to the production registry** — the `rhaii/` namespace didn't exist yet in `registry.redhat.io`.

| # | Image | Error |
|---|---|---|
| 2 | `registry.redhat.io/rhaii/vllm-cpu-rhel9@sha256:562ae...` | UNAUTHORIZED |
| 3 | `registry.redhat.io/rhaii/vllm-cuda-rhel9@sha256:d3e57...` | UNAUTHORIZED |
| 4 | `registry.redhat.io/rhaii/vllm-gaudi-rhel9@sha256:1adc5...` | UNAUTHORIZED |
| 5 | `registry.redhat.io/rhaii/vllm-rocm-rhel9@sha256:cfeac...` | UNAUTHORIZED |
| 6 | `registry.redhat.io/rhaii/vllm-spyre-rhel9@sha256:5c0ed...` | UNAUTHORIZED |

**Why this passed staging:** The RHAII team had released these images to `registry.stage.redhat.io`, so the stage release worked. But they hadn't promoted them to production yet.

**Why this is the first time:** RHAII rebranded from `rhaiis` to `rhaii`, making this the first release under the new namespace. The `rhaii/` repositories don't yet exist in the prod container catalog.

---

## How This Was Diagnosed

### Step 1: Confirm snapshot exists

```bash
oc get snapshot acme-v2-0-1778062796 -n NAMESPACE_PLACEHOLDER
```

Confirmed: exists, age 6d22h, 83 components.

### Step 2: Find and inspect the Release CR

```bash
oc get release acme-v2-0-prod-1778657574 -n NAMESPACE_PLACEHOLDER -o yaml
```

Key findings from the Release CR:
- `status.conditions[ManagedPipelineProcessed]` → Failed: `task verify-conforma failed: "step-assert" exited with code 1`
- `status.managedProcessing.pipelineRun` → `releng-tenant/managed-qmgwd`

### Step 3: Attempt live pipeline run access (dead end)

```bash
oc get pipelinerun managed-qmgwd -n releng-tenant    # NotFound
oc get taskrun -n releng-tenant -l tekton.dev/pipelineRun=managed-qmgwd    # No resources
```

Both the PipelineRun and TaskRuns had been garbage-collected by Tekton's pruner.

### Step 4: Check ic conforma monitoring tool

```bash
~/claude/ci-autohealing/ic conforma --refresh
~/claude/ci-autohealing/ic conforma report 3.4
```

No failures reported. The `ic` tool monitors nightly conforma checks (integration test scenarios), not the release pipeline's `verify-conforma` task. Different pipeline, different context.

### Step 5: Trace the policy configuration chain

```
Release CR → ReleasePlan → ReleasePlanAdmission → EnterpriseContractPolicy
```

```bash
oc get releaseplan rhoai-onprem-v3-4-components-prod -n NAMESPACE_PLACEHOLDER -o yaml
oc get releaseplanadmission rhoai-onperm-v3-4-components-prod -n releng-tenant -o yaml
oc get enterprisecontractpolicy registry-acme-prod -n releng-tenant -o yaml
```

This revealed:
- Pipeline: `rh-advisories.yaml` from `release-service-catalog`
- EC Policy: `registry-acme-prod` with `@redhat` rules including `olm.unmapped_references`
- 50+ volatile exceptions already configured for known issues

### Step 6: Retrieve GC'd data from KubeArchive

Since pipeline runs were garbage-collected, we queried KubeArchive — the archive store for deleted Kubernetes resources:

```bash
KUBEARCHIVE_URL="https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN"
TOKEN=$(oc whoami -t)

# Get the archived PipelineRun
curl -sk -H "Authorization: Bearer $TOKEN" \
  "$KUBEARCHIVE_URL/apis/tekton.dev/v1/namespaces/releng-tenant/pipelineruns/managed-qmgwd"

# Get the archived TaskRun
curl -sk -H "Authorization: Bearer $TOKEN" \
  "$KUBEARCHIVE_URL/apis/tekton.dev/v1/namespaces/releng-tenant/taskruns/managed-qmgwd-verify-conforma"

# Get step logs (via Loki backend)
curl -sk -H "Authorization: Bearer $TOKEN" \
  "$KUBEARCHIVE_URL/api/v1/namespaces/releng-tenant/pods/managed-qmgwd-verify-conforma-pod/log?container=step-validate"
```

**From the TaskRun metadata:**
- `TEST_OUTPUT`: `{"successes":53391,"failures":6,"warnings":5400,"result":"FAILURE"}`
- `step-validate`: completed successfully (exit 0) — it ran the EC check and found violations
- `step-assert`: failed (exit 1) — it asserted the result and correctly blocked the release

**From the step-validate logs** (the actual error messages):
```
level=error msg="failed to fetch image" error="GET https://registry.redhat.io/v2/rhoai/odh-llm-d-batch-gateway-gc-rhel9/manifests/sha256:3989a...: UNAUTHORIZED"
level=error msg="failed to fetch image" error="GET https://registry.redhat.io/v2/rhaii/vllm-gaudi-rhel9/manifests/sha256:1adc5...: UNAUTHORIZED"
level=error msg="failed to fetch image" error="GET https://registry.redhat.io/v2/rhaii/vllm-cuda-rhel9/manifests/sha256:d3e57...: UNAUTHORIZED"
level=error msg="failed to fetch image" error="GET https://registry.redhat.io/v2/rhaii/vllm-rocm-rhel9/manifests/sha256:cfeac...: UNAUTHORIZED"
level=error msg="failed to fetch image" error="GET https://registry.redhat.io/v2/rhaii/vllm-spyre-rhel9/manifests/sha256:5c0ed...: UNAUTHORIZED"
level=error msg="failed to fetch image" error="GET https://registry.redhat.io/v2/rhaii/vllm-cpu-rhel9/manifests/sha256:562ae...: UNAUTHORIZED"
```

**From the step-detailed-report logs:**
Confirmed all 6 violations belonged to `acme-operator-bundle-v3-4`.

---

## Additional Issue: Helm Charts Snapshot Ambiguity

During the same release window, the push-to-prod script for Helm charts also failed:

```
Found multiple charts snapshots with the given condition, please cleanup manually
and ensure only one eligible snapshot exists ...exiting
```

The correct chart snapshot was `acme-v2-0-charts-1778065476`. Manual cleanup was required and was performed by Moulali. Helm charts were a big rock feature for 3.4.

---

## Resolution Actions

| Action | Owner | Status |
|---|---|---|
| Fix RPA typo: `odh-llm-d-batch-gateway-gc-rhel9-v3-4` → `odh-llm-d-batch-gateway-gc-v3-4` | Moulali | In progress — fixing in prod RPA, stage RPA, and subsequent versions |
| Get RHAII vLLM images released to prod (`registry.redhat.io/rhaii/*`) | RHAII team (Selbi/Giulia) | Blocked — Jakub and Klara OOO. Giulia can push if Selbi approves |
| Chart snapshot cleanup | Moulali | Done |
| Retry production release after blockers resolved | Team | Pending |

---

## Lessons Learned

1. **Cross-product dependency not validated earlier:** The RHOAI operator bundle references images from the RHAII product (`rhaii/vllm-*`). This dependency passed staging because RHAII had images in staging, but nobody verified they were also in production before triggering the prod release.

2. **Component onboarding errors propagate silently:** The `odh-llm-d-batch-gateway-gc` typo in the RPA (`rhel9` in the wrong position) wasn't caught during staging because the image might have been mapped differently or wasn't present in the stage snapshot at the time.

3. **Pipeline run GC makes post-mortem harder:** The managed pipeline runs in `releng-tenant` were garbage-collected within hours. Without KubeArchive, the actual violation details would have been lost. The team had to rely on Moulali having the output locally.

4. **ic conforma monitoring gap:** The `ic conforma` tool monitors nightly integration test conforma checks but does not monitor the release pipeline's `verify-conforma` results. This is a different pipeline running in a different namespace (`releng-tenant` vs `NAMESPACE_PLACEHOLDER`).

5. **Helm charts release process fragility:** The charts release script couldn't handle multiple eligible snapshots and required manual cleanup. For a big rock feature, the chart release workflow needs hardening.

---

## How the Release Process Works (Reference)

```
1. BUILD: Components built individually in Konflux → quay.io/acme/* images
      ↓
2. SNAPSHOT: All component images bundled into a Snapshot CR (AppStudio)
      ↓
3. STAGE RELEASE: Snapshot released to registry.stage.redhat.io
   - Uses stage ReleasePlan + stage EC policy
   - QE validates the RC
      ↓
4. PRODUCTION RELEASE: Same snapshot released to registry.redhat.io
   - Release CR created pointing to snapshot + prod ReleasePlan
   - Triggers managed pipeline in releng-tenant:
     a. verify-access-to-resources (permissions check)
     b. collect-data (gather release metadata)
     c. reduce-snapshot (filter components)
     d. apply-mapping (map quay.io → registry.redhat.io paths)
     e. verify-conforma ← THIS IS WHERE IT FAILED
        - Runs Enterprise Contract policy checks
        - Validates all CSV image refs are accessible
        - Checks hermetic builds, SBOM, signatures, etc.
     f. publish-pyxis-repository (push to prod registry)
     g. create advisories (RHBA/RHSA)
      ↓
5. ADVISORY: Errata advisory published, images live on registry.redhat.io
```

The `olm.unmapped_references` rule specifically checks that every image pullspec found in the operator bundle's CSV exists either:
- In the snapshot being released (will be pushed as part of this release), OR
- Already accessible in the target registry (previously released)

If an image is neither, it means the released operator would reference an image that customers can't pull — which is a hard block.
