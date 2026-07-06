# Troubleshooting `source_image.exists` violations

Real-world case study from July 2026: odh-workbench-jupyter-minimal-cpu-py312
failed `source_image.exists` in the stage release, blocking the EA2 push to stage.

This guide shows the full troubleshooting process side by side: manual investigation
(as performed by ckodama in ~45 minutes) and the IC + AI analyzer approach (~2 minutes).

---

## Background

### What is `source_image.exists`?

The Conforma (Enterprise Contract) policy `source_image.exists` verifies that every
container image in a release snapshot has a corresponding **source container image**.
Source images are generated during the Konflux build pipeline — if the build times out,
fails, or the source-build task is skipped, the source image won't exist and this
violation fires.

### Why does this happen?

The most common cause is a **stale SHA** in the release snapshot. The propagation chain is:

```
Component build (Konflux)
  → operator-nudging.yaml (nightly operator-processor, GitHub Actions)
    → bundle CSV clusterserviceversion.yaml (bundle-processor, GitHub Actions)
      → catalog.yaml (RHOAI-Build-Config)
        → FBC fragment build (Konflux)
          → Stage promoter → Release
```

If any step in this chain uses an outdated or broken SHA, the release will include
an image whose source container was never generated.

---

## Step 0: Detect the problem

### Manual

1. Someone reports in Slack that the release failed
2. Open Konflux UI, find the Release PipelineRun, read the logs

### With IC

```bash
# See all alerts — failed releases appear immediately
ic get releases
```

Output:
```
Epoch 1783018561  (2026-07-02)
  rhoai-v3-5-ea-2-stage-1783018561  Components  Stage  ✗ Failed (verify-conforma)
```

```bash
# Get full details: error, policy, failed step, Konflux UI link
ic describe release rhoai-v3-5-ea-2-stage-1783018561
```

Output shows:
```
Failed task: verify-conforma
Message:    "step-assert" exited with code 1: Error
Result:     FAILURE (5 failures)
EC Policy:  registry-rhoai-stage (98 exclusions)
```

---

## Step 1: Identify the violations

### Manual

1. Open `verify-conforma` logs in Konflux UI
2. Search for `[Violation]` in the log text
3. Copy image refs and SHAs by hand
4. Count how many there are and which components they belong to

### With IC

```bash
ic describe release rhoai-v3-5-ea-2-stage-1783018561 --logs
```

The tool downloads and parses the logs automatically. Shows:
- 5 violations of `source_image.exists`
- 4 on `odh-workbench-jupyter-minimal-cpu-py312-rhel9` (1 manifest list + 3 arch-specific)
- 1 on `odh-ai-gateway-operator-rhel9`
- Exact ImageRefs with SHAs

---

## Step 2: Check the latest build of the component

### Manual (what ckodama did)

1. Go to Konflux UI → Application → Component → PipelineRuns
2. Find the latest green build
3. Copy the SHA from the build results section
4. Compare manually:
   - Green build SHA: `sha256:8ad6545b82014b75b799ff301274f0d21f72c778...`
   - Violation SHA: `sha256:6e6e80d5d6849a95f53b471bcff60469107e1013...`
   - **They don't match** — the snapshot has a stale SHA
5. Conclusion: "the nightly is not picking up the latest green build"

### With IC

```bash
# View build history for the component
ic describe conforma odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2 --history
```

---

## Step 3: Run AI analysis

### Manual

This step does not exist — ckodama did all the investigation mentally over ~45 minutes,
navigating between GitHub Actions, Konflux UI, quay.io, and multiple GitHub repos.

### With IC

```bash
ic ai analyze release rhoai-v3-5-ea-2-stage-1783018561
```

This single command automatically performs what ckodama did in steps 2 through 5:

1. **Downloads release logs** and extracts violation details
2. **Queries build history** for each affected component
3. **Compares SHAs**: violation SHA vs latest green SHA vs nudging.yaml SHA
4. **Reads operator-nudging.yaml** from the GitHub repo and verifies the SHA
5. **Searches nudge PRs** in GitHub for each component
6. **Generates diagnosis** with root cause, evidence references, and fix steps

The AI analysis output:

```
Root Cause:
  PRIMARY: odh-workbench-jupyter-minimal-cpu-py312-rhel9 — stale SHA from a timed-out build.
  Build qjhfc (sha256:6e6e80d5...) has status PipelineRunTimeout — source image was never
  generated. Latest green build j76bz (sha256:8ad6545b...) completed successfully.
  operator-nudging.yaml L212-L213 shows SHA sha256:0c11f8ed... — does NOT match either SHA.
  The nudging mechanism has not picked up the latest green build.

  SECONDARY: odh-ai-gateway-operator-rhel9 — snapshot uses a build whose source image is
  missing despite the build completing.

Recommended Fix:
  - Update operator-nudging.yaml L212-L213 to point to sha256:8ad6545b...
  - Retrigger the nightly build cycle for full propagation
  - Do NOT retrigger the release until both issues are resolved
```

---

## Step 4: Investigate the nightly operator processor

### Manual (ckodama)

1. Open GitHub Actions → `red-hat-data-services/rhods-operator` → nightly operator processor
2. Find the latest run: `actions/runs/28587599451`
3. See it created commit `66cc90bb`
4. Open `operator-nudging.yaml` at that commit
5. Verify it uses the "bad" SHA
6. Filter PRs by `workbench-minimal` in rhods-operator
7. Confirm: the nudge for `sha256:8ad6545` **was never created**

### With IC

The AI analyzer already did this in Step 3. The evidence reference shows:

```
[C] operator-nudging.yaml (red-hat-data-services/rhods-operator, branch rhoai-3.5)
    L212-L213: RELATED_IMAGE_ODH_WORKBENCH_JUPYTER_MINIMAL_CPU_PY312_IMAGE = sha256:0c11f8ed...
    — does not match violation SHA (6e6e80d5) or latest green SHA (8ad6545b)
```

---

## Step 5: Investigate the bundle processor

### Manual (ckodama)

1. Open GitHub Actions → `RHOAI-Build-Config` → bundle trigger
2. Find the run: `actions/runs/28588655238`
3. See it created commit `52b20c7`
4. Open `rhods-operator.clusterserviceversion.yaml` at that commit
5. Verify it also uses the "bad" SHA `6e6e80...`
6. Confirm: bundle-processor copies the SHA from operator-nudging.yaml, not from quay

### With IC

The analyzer already identified the full propagation chain:

```
After fixing nudging.yaml: retrigger the nightly build cycle so the full
propagation chain executes:
  operator-nudging.yaml → bundle CSV → catalog.yaml → FBC fragment build
  → stage promoter picks up the correct SHA
```

---

## Step 6: Apply the fix

### Manual (ckodama)

1. Create PR #33945 in `red-hat-data-services/rhods-operator`
2. Update `operator-nudging.yaml` L212 with the correct SHA `8ad6545b...`
3. Get review and merge PR
4. Retrigger nightly build manually via GitHub Actions
5. Wait for the chain to complete: nightly → FBC build → stage promoter

### With IC

```bash
# The analyzer already told you exactly what to do and which lines to change.
# The fix is still manual (create a PR in GitHub), but IC can generate
# the Jira ticket or Slack message with the full context:
ic export release rhoai-v3-5-ea-2-stage-1783018561 jira
```

### Important: nightly overwrite risk

The AI analyzer warns about a critical pitfall that ckodama's team hit:

> **WARNING**: The nightly operator-processor will overwrite manual changes to
> operator-nudging.yaml on its next run. A manual nudge PR is a temporary fix.
>
> - **Preferred**: Retrigger a fresh component build. The new build generates a new SHA
>   that the operator-processor will pick up automatically.
> - **Faster-but-fragile**: Manual nudge + immediate nightly retrigger. You must retrigger
>   *before* the next scheduled nightly run, or your fix gets overwritten.

This is exactly what happened: ckodama's manual nudge PR was merged, but the next
nightly build overwrote it with the same stale SHA. The team had to rebuild the
workbench component entirely (the "preferred" approach).

---

## Step 7: Verify the fix

### Manual

1. Check the new nightly build completed in GitHub Actions
2. Open Konflux UI to verify the new FBC fragment build has the correct SHAs
3. Check the new release PipelineRun — does `verify-conforma` pass?
4. Multiple Slack threads, calls between team members to coordinate

### With IC

```bash
# Check the new release status
ic describe release rhoai-v3-5-ea-2-stage-1783073621
```

Output:
```
  ✓ verify-conforma          ← PASSED!
  ○ push-snapshot             ← running
```

```bash
# Verify the violations are gone
ic describe conforma odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2
# → "No active Conforma violations"

# Verify FBC fragment no longer has source_image violations
ic describe conforma rhoai-fbc-fragment-v3-5-ea-2
# → source_image.exists violations are gone
```

---

## Comparison summary

| Aspect | Manual (ckodama) | IC + AI analyzer |
|--------|-----------------|------------------|
| **Investigation time** | ~45 min | ~2 min |
| **Browser tabs needed** | GitHub Actions (x3), Konflux UI, quay.io, operator-nudging.yaml, catalog.yaml, PR filter | Terminal only |
| **SHA comparisons** | 5 manual copy-paste comparisons | Automatic — AI compares violation vs green vs nudging SHAs |
| **Identified timeout build** | No — inferred "nightly not picking up" | Yes — `PipelineRunTimeout` on build qjhfc |
| **Second component found** | No — only investigated workbench | Yes — also found ai-gateway-operator |
| **Future risk identified** | No | Yes — 5 RHAII vLLM images returning 404 |
| **Nightly overwrite warning** | No — team hit this problem | Yes — warns about overwrite risk |
| **Correct fix** | Yes — PR #33945 | Yes — same recommendation |
| **Verification** | Manual Konflux UI checks | `ic describe release` + `ic describe conforma` |

---

## Quick reference: commands for this scenario

```bash
# 1. Detect failed release
ic get releases

# 2. Investigate
ic describe release <release-name>
ic describe release <release-name> --logs

# 3. AI analysis (does steps 2-5 automatically)
ic ai analyze release <release-name>

# 4. Check component build history
ic describe conforma <component> --history

# 5. View the full analysis with evidence
ic describe release <release-name>
# → Shows: Root Cause, Recommended Fix, Evidence References,
#   Fix Status, Verification Commands

# 6. After fix: verify
ic describe release <new-release-name>
ic describe conforma <component>

# 7. Export for Jira/Slack
ic export release <release-name> jira
ic export release <release-name> slack
```
