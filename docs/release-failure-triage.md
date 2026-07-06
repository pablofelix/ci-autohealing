# Release Failure Triage with `ic`

How to diagnose a failed release push using `ic` commands. This documents the diagnostic process that a release engineer follows when a stage or prod push fails.

## Quick Reference

```bash
# 1. See all recent releases
ic get releases

# 2. Inspect the failed release
ic describe release <release-name> --logs

# 3. If verify-conforma failed — trace a failing image to its build
ic lookup-image <sha256-digest>

# 4. Check the component's build status
ic describe component <component-name>
```

## Step-by-Step Diagnosis

### 1. Identify the failing release

```bash
ic get releases --stage
```

Look for releases where `Released = False` and `ManagedPipelineProcessed = False`. These are the ones stuck or failed.

### 2. Get release details

```bash
ic describe release rhoai-v3-5-ea-2-stage-1782975804-1-2-3
```

This shows:
- **Snapshot** name and component count
- **ReleasePlan** (which determines stage vs prod, components vs FBC vs charts)
- **Conditions** — which pipeline step failed
- **Pipeline Steps** — checkmark list showing progress

Key things to look for:
- `verify-conforma` with `◌` (pending/failed) — EC policy violations
- `Released = False` with `Reason = Progressing` — still running
- `Released = False` with `Reason = Failed` — terminal failure

### 3. Get the logs

```bash
ic describe release <name> --logs
```

With `--logs`, when `verify-conforma` fails, ic parses the EC output and shows:
- **Failing components** with their violation rules
- **Violation messages** explaining why each rule failed
- **Failing images** — the image SHAs that triggered the violations
- **Summary** — total counts of violations and warnings

For non-conforma failures, it shows the relevant error lines from the failed step's logs.

### 4. Trace a failing image to its build

When verify-conforma shows a failing image like:
```
odh-vllm-cpu-v3-5-ea-2@sha256:abc123...
```

Look up which build produced it:

```bash
ic lookup-image sha256:abc123
```

This searches both the build failure database and the live cluster. It tells you:
- Which **component** produced the image
- The **PipelineRun** name
- Whether the build **succeeded or failed**
- The **source repo and branch**

### 5. Determine root cause

The violation tells you what's wrong. Common patterns:

| Violation Rule | Meaning | Root Cause |
|---|---|---|
| `source_image.exists` | Source image not found in registry | Build failed — image was never published |
| `builtin.attestation.signature_check` | Image attestation missing/invalid | Build failed or signing step failed |
| `labels.required_labels` | Required labels missing from image | Dockerfile missing required LABELs |
| `hermetic_task.hermetic` | Task wasn't hermetic | `.tekton/` pipeline config needs `hermetic: true` |
| `base_image_registries.base_image_info_found` | Base image not from allowed registry | Dockerfile FROM uses an untrusted base |
| `cve.cve_results_found` | CVE scan results missing | Clair scan didn't run or failed |
| `tasks.required_tasks_found` | Required Tekton tasks missing | Pipeline definition incomplete |

**Key insight**: `source_image.exists` and `builtin.attestation.signature_check` almost always mean the build itself failed — the image was never properly built and signed. The violation is a symptom, not the root cause. Check the component's build status:

```bash
ic describe component <component-name>
```

### 6. Check exception coverage

Some violations have policy exceptions (allowed exceptions in the EC policy). To see if a violation is covered:

```bash
ic get alerts
```

Look for the exception tags:
- `[EXC:S+P]` — fully covered in both stage and prod → will not block
- `[EXC:S]` — covered in stage only → won't block stage push, will block prod
- `[EXC:P]` — covered in prod only
- `[PARTIAL:S]` — partially covered in stage (some rules excepted, others not)
- No tag — not covered → will block the release

For more detail on exceptions:
```bash
ic get exceptions
ic describe conforma <component>
```

### 7. Take action

Depending on root cause:

**Build failure** → Fix the build
```bash
ic describe component <component-name> --log
# Check error, fix code, rebuild
```

**Missing exception** → Request exception or fix violation
```bash
# See what rules are missing from the exception
ic get alerts   # Look at missing(S), missing(P) fields

# Export for Jira ticket
ic export <component> jira
```

**Stale snapshot** → The snapshot includes an old image from a build that has since failed
```bash
# Compare what's in the snapshot vs latest builds
ic release diff <epoch1> <epoch2>

# Check if the component has newer successful builds
ic history <component>
```

## Example: Diagnosing a verify-conforma Failure

Real example from Slack thread (2026-07-02) — `rhoai-v3-5-ea-2-stage-1782975804`:

```bash
# 1. See the failed release — verify-conforma failed with 10 violations
$ ic describe release rhoai-v3-5-ea-2-stage-1782975804
  Released       False   Failed   Release processing failed on managed pipelineRun ←
  ManagedPipeline  False   Failed   task verify-conforma failed: "step-assert" exited with code 1 ←
  Result: FAILURE (10 failures)

# 2. Get --logs — shows ONLY the failing images (not all 560)
$ ic describe release rhoai-v3-5-ea-2-stage-1782975804 --logs
  Failing images:
    5x odh-workbench-jupyter-minimal-cpu-py312-rhel9  (Violations: 1 each — source_image.exists)
    5x odh-mod-arch-agent-ops-rhel9                   (Violations: 1 each — attestation.signature_check)

# 3. Trace a failing image to its component
$ ic lookup-image quay.io/rhoai/odh-workbench-jupyter-minimal-cpu-py312-rhel9@sha256:e2a0bb1e...
  odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2 | rhoai-v3-5-ea-2

$ ic lookup-image quay.io/rhoai/odh-mod-arch-agent-ops-rhel9@sha256:fcee8fc0...
  odh-mod-arch-agent-ops-v3-5-ea-2 | rhoai-v3-5-ea-2

# 4. Check snapshot freshness — is the snapshot using the right builds?
$ ic snapshot check
  STALE odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2
    Newer build available: 2026-06-30
  → Snapshot has a stale image from a failed build; a green build exists but wasn't picked up

# 5. Root cause:
#    - odh-workbench-jupyter-minimal-cpu-py312: build timed out but image was already
#      pushed to quay → nightly FBC picked up the broken image → source_image.exists
#    - odh-mod-arch-agent-ops: build failed → no attestation → signature_check
#    Both are build issues surfaced by conforma, not conforma issues themselves.
```

## How the Release Pipeline Works

Understanding the full chain helps diagnose where things break.

### Architecture

```
rhods-operator repo                    RHOAI-Build-Config repo
  build/operator-nudging.yaml            .github/workflows/
  (maps component → image SHA)             trigger-nightly-fbc-build.yaml
         ↓ nudge PRs                       push-to-stage.yaml
         ↓ (Renovate/automated)            ↓
         ↓                            Konflux (rhoai-tenant namespace)
         ↓                              Component builds (per-push)
         ↓                              FBC fragment build (nightly)
         ↓                              Snapshot CR (latest good images)
         ↓                                   ↓
         ↓                            konflux-release-data (GitLab CEE)
         ↓                              MR → creates Release CR
         ↓                                   ↓
         ↓                            Release pipeline (rhtap-releng-tenant)
         ↓                              verify-conforma → stage push
         ↓                                   ↓
     quay.io/rhoai/                    quay.io/rhoai/ (promoted)
       (build output)                    (stage registry)
```

### Step by step

1. **Component builds**: Each push to a component's source branch (e.g., `rhoai-3.5-ea.2` on `red-hat-data-services/odh-dashboard`) triggers a Konflux PipelineRun that builds the image and pushes to `quay.io/redhat-user-workloads/rhoai-tenant/...`

2. **Operator nudging**: `red-hat-data-services/rhods-operator` has `build/operator-nudging.yaml` that maps each component name to its promoted image SHA. Renovate/automated PRs update these SHAs when new builds succeed. The `build/operands-map.yaml` has the same mapping used by the nightly.

3. **Nightly FBC build**: Triggered by `RHOAI-Build-Config/.github/workflows/trigger-nightly-fbc-build.yaml` (on schedule or `workflow_dispatch`). It reads the operands-map to determine which image SHAs to include in the FBC fragment.

4. **Snapshot creation**: Konflux's Integration Service creates a Snapshot CR from the latest successful build of each component. The snapshot lists every component with its `containerImage` (promoted URL + digest).

5. **Push to stage**: `RHOAI-Build-Config/.github/workflows/push-to-stage.yaml` is triggered manually (`workflow_dispatch`). It validates the FBC image, patches the catalog, commits to main, and the pipeline-watcher creates a Release CR.

6. **Release pipeline**: Runs in `rhtap-releng-tenant` namespace. The `verify-conforma` step checks all snapshot images against EC policy. If all pass → images are promoted to stage.

### Key repos

| Repo | What it does | Branch |
|---|---|---|
| `red-hat-data-services/rhods-operator` | Operator source; `build/operator-nudging.yaml` maps components to image SHAs | `rhoai-3.5-ea.2` |
| `red-hat-data-services/RHOAI-Build-Config` | Nightly triggers, push-to-stage GHA, catalog config | `rhoai-3.5-ea.2` |
| `gitlab.cee.redhat.com/releng/konflux-release-data` | Release CRs, ReleasePlans, policy configs (MRs trigger releases) | — |
| Component repos (e.g., `odh-dashboard`) | Source code for each component image | `rhoai-3.5-ea.2` |

### How the nightly picks images

The nightly FBC fragment build reads `quay.io/rhoai/<component>-rhel9:rhoai-3.5-ea.2` (the floating tag) to get the latest promoted image. **Critical**: if a build times out AFTER pushing to quay but BEFORE completing successfully, the floating tag points to the broken image. The nightly then bakes this broken SHA into the FBC fragment, and the stage push fails with `source_image.exists`.

## Case Study: rhoai-v3-5-ea-2-stage-1782975804 (2026-07-02)

### What happened

Push to stage failed with 10 conforma violations:
- 5x `source_image.exists` for `odh-workbench-jupyter-minimal-cpu-py312-rhel9` — stale image from a timed-out build
- 5x `builtin.attestation.signature_check` for `odh-mod-arch-agent-ops-rhel9` — build had failed, no attestation

### Diagnosis with ic

```bash
# 1. See the failure
$ ic describe release rhoai-v3-5-ea-2-stage-1782975804
  Released       False   Failed
  verify-conforma: FAILURE (10 failures)

# 2. See which images fail (--logs filters to only violating images)
$ ic describe release rhoai-v3-5-ea-2-stage-1782975804 --logs
  5x odh-workbench-jupyter-minimal-cpu-py312-rhel9  (Violations: 1 — source_image.exists)
  5x odh-mod-arch-agent-ops-rhel9                   (Violations: 1 — attestation.signature_check)

# 3. Trace images to components
$ ic lookup-image quay.io/rhoai/odh-workbench-jupyter-minimal-cpu-py312-rhel9@sha256:e2a0bb1e...
  → odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2

$ ic lookup-image quay.io/rhoai/odh-mod-arch-agent-ops-rhel9@sha256:fcee8fc0...
  → odh-mod-arch-agent-ops-v3-5-ea-2

# 4. Check if snapshot is stale
$ ic snapshot check
  STALE odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2
    Newer build available: 2026-06-30

# 5. Check if retrying would help (auto-shown on failed releases)
$ ic describe release rhoai-v3-5-ea-2-stage-1782975804
  Snapshot Stale: 6 components have newer images in the latest snapshot:
    ↑ odh-mod-arch-agent-ops-v3-5-ea-2    ← fixed, new image available
    ↑ odh-kuberay-operator-controller-v3-5-ea-2
    ...
  → A new release with the latest snapshot may resolve some violations
```

### Diagnosis without ic (manual steps)

If you don't have `ic`, here's how to do the same investigation:

**1. Find the failed release:**
```bash
oc get releases.appstudio.redhat.com -n rhoai-tenant | grep stage | grep False
oc get release <name> -n rhoai-tenant -o yaml
# Look at: .status.conditions (Released=False, ManagedPipelineProcessed=Failed)
# Note: .spec.snapshot (the snapshot name) and .status.managedProcessing.pipelineRun
```

**2. Get violation details:**
```bash
# Find the verify-conforma pod
PIPELINE_NS=rhtap-releng-tenant
PR_NAME=managed-fqnhj  # from .status.managedProcessing.pipelineRun
oc get pod -n $PIPELINE_NS -l tekton.dev/pipelineRun=$PR_NAME | grep verify-conforma
# Get the detailed report
oc logs <pod> -n $PIPELINE_NS -c step-detailed-report | grep -B1 'Violations: [1-9]'
# Shows ImageRef + Violation count for each failing image
```

**3. Trace image to component:**
```bash
# Extract the image name from the URL (e.g., odh-workbench-jupyter-minimal-cpu-py312-rhel9)
# Search for matching components in the cluster:
oc get component -n rhoai-tenant -o json | jq -r '.items[] | select(.spec.containerImage | contains("minimal-cpu-py312")) | "\(.metadata.name) \(.metadata.labels["appstudio.openshift.io/application"])"'
```

**4. Check if snapshot is stale:**
```bash
# Get the snapshot used by the release
SNAPSHOT=$(oc get release <name> -n rhoai-tenant -o jsonpath='{.spec.snapshot}')
# Get image SHA from the release snapshot
oc get snapshot $SNAPSHOT -n rhoai-tenant -o json | jq -r '.spec.components[] | select(.name | contains("minimal-cpu")) | .containerImage'
# Get image SHA from the LATEST snapshot
LATEST=$(oc get snapshot -n rhoai-tenant -l appstudio.openshift.io/application=rhoai-v3-5-ea-2 --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
oc get snapshot $LATEST -n rhoai-tenant -o json | jq -r '.spec.components[] | select(.name | contains("minimal-cpu")) | .containerImage'
# If the SHAs differ → the component has been rebuilt since the release snapshot was created
```

**5. Check the operator nudging file:**
```bash
# See what SHA the operator is using for this component
# This is what the nightly FBC fragment picks up
gh api "repos/red-hat-data-services/rhods-operator/contents/build/operator-nudging.yaml?ref=rhoai-3.5-ea.2" \
  --jq '.content' | base64 -d | grep -A1 'MINIMAL_CPU_PY312'
# If this SHA matches the failing image → the operator nudging hasn't been updated
```

**6. Check the component's build history:**
```bash
# See recent builds on the source repo
REPO=$(oc get component <comp-name> -n rhoai-tenant -o jsonpath='{.spec.source.git.url}')
BRANCH=$(oc get component <comp-name> -n rhoai-tenant -o jsonpath='{.spec.source.git.revision}')
gh api "repos/${REPO#https://github.com/}/commits?sha=$BRANCH&per_page=5" \
  --jq '.[] | "\(.sha[0:12]) \(.commit.author.date[0:10]) \(.commit.message | split("\n")[0][0:60])"'
```

### How to fix

**For `odh-mod-arch-agent-ops` (build failure → attestation_check):**
The build failed and was later rebuilt successfully by automated Dockerfile digest updates on `red-hat-data-services/odh-dashboard` branch `rhoai-3.5-ea.2`:

```
# Source repo commits (odh-dashboard):
528e00512cb6  2026-07-01  Update Dockerfile Digest Updates (#2145)   ← lastBuiltCommit (green)
179915c0b4fc  2026-06-30  chore(deps): update dockerfile digest updates (#2141)
31c6893c8d6c  2026-06-29  chore(deps): update dockerfile digest updates (#2133)
5dbef7fbb016  2026-06-25  Merge pull request #2102 (revert-agent-ops-manifests-ea2)  ← likely the broken one

# Operator nudging commit (rhods-operator):
a7c3b4be2103  2026-07-02  chore(deps): update odh-mod-arch-agent-ops-v3-5-ea-2 to 630ddad
                           ↑ automated nudge PR updated the SHA in operator-nudging.yaml
                             from broken image → sha256:630dda... (the green build)
```

The fix was automatic: a new push to the source branch → Konflux built a new image → Renovate updated the nudging file → latest snapshot got the new SHA.

**For `odh-workbench-jupyter-minimal-cpu-py312` (stale image → source_image.exists):**
The build timed out AFTER pushing its image to quay. Source repo is `red-hat-data-services/notebooks` branch `rhoai-3.5-ea.2`:

```
# Source repo commits (notebooks):
a15f5837562b  2026-06-20  Merge remote-tracking branch 'upstream/main' into rhoai-3.5-ea.2
                           ↑ lastBuiltCommit — no new pushes since June 20

# Operator nudging: NO nudge commit for minimal-cpu-py312 since the failure
# The old broken SHA (sha256:6e6e80d5...) is still baked in operator-nudging.yaml
```

Even though a green build existed (June 30), the nightly still picks the broken image. **No automated nudge happened** because the notebooks repo has had no new pushes. The fix requires either:

1. **Trigger a new nightly** that picks up the latest green image:
   - Go to `RHOAI-Build-Config` repo → Actions → "Trigger Nightly FBC Build" → Run workflow on `rhoai-3.5-ea.2`
   - This rebuilds the FBC fragment with fresh images from quay

2. **Or manually update the operator nudging** to point to the correct SHA:
   - In `red-hat-data-services/rhods-operator`, branch `rhoai-3.5-ea.2`
   - Edit `build/operator-nudging.yaml` → update `RELATED_IMAGE_ODH_WORKBENCH_JUPYTER_MINIMAL_CPU_PY312_IMAGE` to the SHA from the latest green build
   - This triggers a rebuild of the operator, which then triggers a new FBC fragment

3. **Then trigger a new push to stage**:
   - Go to `RHOAI-Build-Config` repo → Actions → "Push To Stage" → Run workflow
   - Input: `release_branch=rhoai-3.5-ea.2`, `rhoai_version=v3.5.0-ea.2`

**Key lesson**: Retrying the SAME release (creating `-1`, `-1-2`, `-1-2-3` suffixed releases) does NOT help because they all use the same snapshot with the same stale images. You need a new snapshot with updated images first.

### Outcome

As of 2026-07-02: **NOT RESOLVED**. 8 retry attempts (4 per epoch), all failed. Last successful stage push was June 23. `odh-mod-arch-agent-ops` was fixed (new image in latest snapshot) but `odh-workbench-jupyter-minimal-cpu-py312` remained stale.

### Retrospective takeaways

From the ea.2 retrospective thread (2026-07-02):

- **Root cause** (ckodama): *"we should update operator processor to somehow pick the latest green build instead of the latest build"* — the nightly FBC fragment picks up the latest image from quay (floating tag), which may be from a build that timed out or failed after pushing. It should only pick images from successful builds.

- **Renovate freeze** (Wiktor): Renovate digest updates close to release time can trigger builds that fail for "silly reasons" (timeouts, transient infra). Suggestion: freeze Renovate 3-7 days before push to prod. Tradeoff: may miss CVE fixes (Moulali).

- **Build guardian before release** (Wiktor): whoever does the release should also act as build guardian before the push — double check and prevent stale images from entering the FBC.

- **Prevention check** (Wiktor): *"the check (or prevention) to not include unfinished builds in the fbc should be done way earlier — at the operator level"* — this is what `ic snapshot check` now does. It compares snapshot images against latest builds and flags STALE components before the push even starts.

### What ic provides for prevention

| Retrospective concern | ic command | What it catches |
|---|---|---|
| "pick latest green build, not latest build" | `ic snapshot check` | Flags components where snapshot has stale/failed image |
| "double check before push" | `ic describe release --logs` | Shows exactly which images violate, auto-detects stale snapshot |
| "is conforma using the correct build?" | `ic snapshot check` + `ic lookup-image` | Traces violation images back to components, shows if newer build exists |
| Build guardian pre-flight | `ic release readiness` | Checks builds + conforma status before attempting release |

## Related Commands

| Command | Purpose |
|---|---|
| `ic get releases` | List recent release CRs by epoch |
| `ic describe release <name> --logs` | Release details + failure logs (auto-shows stale snapshot) |
| `ic lookup-image <url>` | Find component from promoted image URL (fuzzy matching) |
| `ic snapshot check` | Check snapshot freshness — detect stale/failed images |
| `ic release status [epoch]` | Process checklist (stage+prod) |
| `ic release verify <name>` | Check image availability in Pyxis |
| `ic release diff <e1> <e2>` | Compare snapshots between releases |
| `ic release readiness` | Can we release? (builds + conforma) |
| `ic get alerts` | All current failures with exception status |
| `ic describe component <name>` | Full component details |
| `ic history <name>` | Build history for a component |
