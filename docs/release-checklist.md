# Release Checklist

Automated and manual checks for RHOAI Konflux releases. Covers pre-release (before stage push) and post-stage (after stage, before prod).

## Quick Start

```bash
# Fast checks (~15s) — 15 automated checks
ic release readiness

# Full checks (~60s) — adds artifact health for all components + FBC prune detection
ic release readiness --full
```

---

## Release Workflow

The release process follows five phases. Each phase has gates (automated checks + manual verification) that must pass before proceeding.

```
┌─────────────┐    ┌──────────────┐    ┌───────────────┐    ┌──────────────┐    ┌────────────┐
│ Pre-Release │───▶│  Stage Push   │───▶│  Post-Stage   │───▶│  Prod Push   │───▶│ Post-Prod  │
│  Checks     │    │              │    │  Verification │    │              │    │ Follow-up  │
└─────────────┘    └──────────────┘    └───────────────┘    └──────────────┘    └────────────┘
```

### Phase 1: Pre-Release Verification

Before triggering a stage release, all pre-release checks must pass.

1. **Run automated checks**: `ic release readiness --full`
2. **Fix any FAIL items** — these are release blockers (failed builds, signing failures, missing artifacts)
3. **Review WARN items** — assess whether warnings are acceptable (stale components, policy differences)
4. **Manual spot-checks** — verify image signatures with cosign, inspect OCI artifacts with skopeo
5. **Confirm FBC fragment** — the FBC (File-Based Catalog) fragment build is healthy and not pruning shipped versions
6. **Verify OLM channels** — expected operator upgrade channels (beta, stable, fast) exist in the FBC fragment

### Phase 2: Stage Push

Trigger the stage release in Konflux:

1. **Create a Release CR** targeting the stage ReleasePlan (e.g., `<app>-components-stage`)
2. The release pipeline runs: signs artifacts, pushes to stage registry, runs post-validation EC checks
3. Monitor progress: `ic describe release <name>` or Konflux UI

### Phase 3: Post-Stage Verification

After the stage release completes, verify before proceeding to prod:

1. **Run automated checks**: `ic release readiness` — the post-stage section now shows results
2. **Stage release health** — confirm `Released=True` and `failedPostValidation` is absent/false
3. **Snapshot drift** — the snapshot released to stage should match the current latest snapshot
4. **Pipeline completeness** — all release conditions are `True` (no partial releases)
5. **Prod RPA exists** — a ReleasePlanAdmission (RPA) for prod is configured with the correct EC policy
6. **Conforma violations** — check that any violations flagged in stage are not prod-blocking

### Phase 4: Prod Push

Trigger the production release:

1. **Create a Release CR** targeting the prod ReleasePlan (e.g., `<app>-components-prod`)
2. The release pipeline runs with the prod EC policy (may be stricter than stage)
3. Monitor progress: `ic describe release <name>` or Konflux UI
4. If the release fails `verify-conforma`, run `ic analyze release <name>` for AI-assisted root cause analysis

### Phase 5: Post-Prod Follow-up

After the production release completes:

1. **Verify artifacts in prod registry** — `skopeo inspect` on released images
2. **Regenerate PCC cache** — run the `regen-pcc-cache` workflow in RHOAI-Build-Config so the next FBC build includes the newly released versions
3. **Close nudge PRs** — merge or close any remaining dependency update PRs
4. **Update release schedule** — if applicable, update milestone dates

---

## Pre-Release Checks (before pushing to stage)

### Automated (via `ic release readiness`) — 11 checks

| # | Check | What it verifies | FAIL means | Fix |
|---|-------|-----------------|------------|-----|
| 1 | FBC fragment health | Nightly FBC (File-Based Catalog) build status | Last FBC build failed | `ic describe <fbc-component>` to see error |
| 2 | PCC cache | PCC (Pre-Computed Catalog) freshness vs registry | Registry has versions not in cache | Run `regen-pcc-cache` workflow in RHOAI-Build-Config |
| 3 | GHA nightly trigger | GitHub Actions nightly trigger workflow | Nightly cron workflow failed | Check workflow URL in output |
| 4 | Stale components | Components with untriggered commits | Components building from stale code | `ic get stale` to see details; trigger builds |
| 5 | Snapshot freshness | Snapshot contains latest successful builds | Snapshot has outdated builds | Wait for next build or nudge |
| 6 | FBC artifact health | OCI artifacts (.sig/.src/.att/.sbom) for FBC fragment | FBC missing artifacts (e.g. .src) | Rebuild FBC; missing .src = timeout build |
| 7 | Tekton Chains signing | Build signing annotation (`chains.tekton.dev/signed`) on latest PipelineRuns | Signing failed | Rebuild affected components |
| 8 | Snapshot completeness | Snapshot has all application components | Components missing from snapshot | Missing components need a successful build |
| 9 | EC policy consistency | Stage and prod RPAs (ReleasePlanAdmission) use same EC (Enterprise Contract) policy | Policies differ between stage/prod | Verify intentional or align policies |
| 10 | Integration tests | Snapshot status conditions for test results | Tests not passing | Check test logs; blocks release |
| 11 | Nudge PR propagation | Open nudge PRs in operator repo | PRs open >48h | Merge or close stale PRs |

### Automated (`--full` only) — 2 additional checks

| # | Check | What it verifies | Performance |
|---|-------|-----------------|-------------|
| 16 | Full artifact health | OCI artifacts for ALL snapshot components | ~60s |
| 17 | FBC prune detection | FBC build logs for pruned versions/channels | ~10-30s |

### Manual Verification

These commands provide additional confidence beyond automated checks:

```bash
# Verify image signatures with cosign
cosign verify --key <key> <image>@<digest>

# Check snapshot component count
oc get snapshot -l appstudio.openshift.io/application=<app> -n <ns> \
  -o json | jq '.items[0].spec.components | length'

# Inspect Tekton Chains signing annotation on a component's latest build
oc get pipelinerun -l appstudio.openshift.io/component=<comp> -n <ns> \
  -o jsonpath='{.items[0].metadata.annotations.chains\.tekton\.dev/signed}'

# Check OCI artifact presence with skopeo
skopeo inspect --raw docker://<image>@<digest> | jq .

# Compare stage vs prod EC policy configuration
oc get releaseplanadmission -n <ns> \
  -o custom-columns=NAME:.metadata.name,POLICY:.spec.policy

# Check integration test results on snapshot
oc get snapshot <name> -n <ns> -o jsonpath='{.status.conditions}'

# Check FBC fragment build logs for prune indicators
oc logs -l appstudio.openshift.io/component=<fbc-comp> -n <ns> \
  --tail=500 | grep -i 'prun\|removed.*version\|removed.*channel'

# Check OLM upgrade channels in FBC
# The FBC fragment defines operator upgrade channels (beta, stable, fast).
# Verify expected channels exist and connect the upgrade graph correctly.
oc get snapshot <name> -n <ns> -o json | jq '.spec.components[] | select(.name | contains("fbc"))'
```

---

## Post-Stage Checks (after stage release, before prod) — Phase 3

### Automated (via `ic release readiness`) — 4 checks

| # | Check | What it verifies | FAIL means | Fix |
|---|-------|-----------------|------------|-----|
| 12 | Stage release health | Latest stage release completed, post-validation passed | Post-validation failed = artifacts don't pass EC in stage | Fix EC violations before prod |
| 13 | Prod RPA exists | ReleasePlanAdmission for prod is configured | No prod RPA = release to prod cannot start | Create prod RPA in managed namespace |
| 14 | Snapshot drift | Snapshot released to stage matches current snapshot | Stage has older snapshot | Re-release or verify changes are acceptable |
| 15 | Release pipeline completeness | All stage release conditions are True | Partial release (some steps stuck) | `ic describe release <name>` for details |

### Manual Verification

```bash
# Verify stage release completed successfully
oc get release -l appstudio.openshift.io/application=<app> -n <ns> \
  -o jsonpath='{.items[0].status.conditions}'

# Check post-validation (failedPostValidation should be false or absent)
oc get release <name> -n <ns> \
  -o jsonpath='{.status.validation.failedPostValidation}'

# Compare snapshot used in stage release vs current
oc get release <name> -n <ns> -o jsonpath='{.spec.snapshot}'
# Then compare with:
oc get snapshot -l appstudio.openshift.io/application=<app> -n <ns> \
  --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}'

# Verify prod RPA exists and has correct policy
oc get releaseplanadmission -n <ns> | grep prod

# Check release pipeline managed processing details
oc get release <name> -n <ns> -o json | jq '.status.managedProcessing'
```

### Konflux UI Links

- **Releases**: `https://console.redhat.com/application-pipeline/workspaces/<ns>/applications/<app>/releases`
- **Snapshots**: `https://console.redhat.com/application-pipeline/workspaces/<ns>/applications/<app>/snapshots`
- **Integration tests**: `https://console.redhat.com/application-pipeline/workspaces/<ns>/applications/<app>/integrationtests`

---

## Glossary

| Acronym | Full Name | Description |
|---------|-----------|-------------|
| EC | Enterprise Contract | Policy evaluation system that validates build artifacts meet security/compliance rules |
| RPA | ReleasePlanAdmission | Konflux CR that defines the release gate configuration (which EC policy, which pipeline) |
| PCC | Pre-Computed Catalog | Cached operator catalog versions in RHOAI-Build-Config; must be regenerated after new releases |
| FBC | File-Based Catalog | OLM operator catalog fragment; container image with RHOAI's slice of the operator catalog |
| Chains | Tekton Chains | Build artifact signing mechanism; annotates PipelineRuns with signing status |
| OCI | Open Container Initiative | Standard for container images and artifacts (.sig, .src, .att, .sbom) |
| OLM | Operator Lifecycle Manager | OpenShift system for operator installation and upgrades via channels |
| GHA | GitHub Actions | CI system; runs nightly trigger workflows |

---

## AI-Assisted Release Analysis

When a release fails, `ic analyze release <name>` runs AI-assisted root cause analysis. The analyzer automatically includes readiness check results as context, so the LLM knows what was healthy/unhealthy pre-release when diagnosing the failure.

```bash
# Analyze a failed release
ic analyze release <release-name>

# Force re-analysis
ic analyze release <release-name> --force
```

The analysis collects context from:
1. Release CR conditions and pipeline logs
2. KubeArchive TaskRun results and pod logs
3. Snapshot components and artifact health
4. RPA (ReleasePlanAdmission) mappings from GitLab
5. EC (Enterprise Contract) policy files
6. Bundle images from RHOAI-Build-Config
7. Build context for violated components
8. Application-level health data
9. Nudge PR status for violated components
10. **Readiness checks** — automated pre-release and post-stage check results

The readiness context helps the LLM correlate failures. For example:
- If EC policy consistency was WARN (stage ≠ prod policies), a `verify-conforma` failure in prod is likely caused by the policy mismatch
- If Snapshot freshness was WARN, the release may contain outdated builds
- If Chains signing was FAIL, EC policy will reject unsigned artifacts
