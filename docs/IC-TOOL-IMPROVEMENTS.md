# IC Tool Improvements — Based on RHOAI Infrastructure Analysis

> Proposed improvements to the IC (ci-autohealing) tool based on comprehensive analysis
> of the RHOAI CI/CD infrastructure (2026-07-09).

---

## 1. Build Chain Awareness

### 1.1 Understand the Component → Operator → Bundle → FBC Build Chain

**Current gap**: IC treats each component failure independently. It doesn't know that a `kserve` failure will cascade to operator, bundle, and FBC builds.

**Proposed improvement**:
- Model the build chain: component → operator → bundle → FBC fragment
- When a component fails, predict downstream impact (e.g., "kserve failure will block nightly FBC build")
- When the FBC fragment fails, trace back to identify which component triggered the chain
- Data source: `RHOAI-Build-Config/catalog/catalog_build_args.map` maps every component to its commit/URL

### 1.2 Map Components to Their Source Repos and Branches

**Current gap**: IC knows component names but doesn't always map them to the correct source repo/branch without cluster access.

**Proposed improvement**:
- Import `konflux-central/pipelineruns/` structure to build a component→repo map
- Import ODH's `component_repo_map.json` for upstream components
- Use `rhods-devops-infra/src/config/upstream-source-map.yaml` to map upstream→downstream repos
- Cache this mapping locally for offline analysis

---

## 2. Enhanced Conforma Analysis

### 2.1 Policy-Aware Violation Triage

**Current gap**: IC can detect Conforma violations but doesn't always know which EC policy file applies to a given component.

**Proposed improvement**:
- Map each application to its EC policy via RPA bindings:
  - `rhoai-v3-5` components → `registry-rhoai-stage` / `registry-rhoai-prod`
  - `rhoai-v3-5` FBC → `fbc-rhoai-stage` / `fbc-rhoai-prod`
  - `rhoai-v3-5` charts → `registry-rhoai-chart-stage` / `registry-rhoai-chart-prod`
- When analyzing a violation, check if it's covered by the relevant policy's exclusions
- Already partially implemented via `compute_blocks()` — extend to cover all application types

### 2.2 Conforma Reporter Integration

**Current gap**: The `conforma-reporter` generates daily violation reports saved to version-specific branches, but IC doesn't consume these directly.

**Proposed improvement**:
- Fetch conforma-reporter results from GitHub (version branches under `conforma-reporter` repo)
- Compare IC's cluster-based findings with reporter findings to detect discrepancies
- Surface reporter's exception YAML files for automated exception coverage analysis
- Use reporter's `conforma_overall_status.yaml` for trend tracking

### 2.3 Policy Gap Alerting

**Current gap**: IC has `get_ec_policy_summary` but doesn't proactively alert when stage exclusions exist that prod doesn't have.

**Proposed improvement**:
- Add a "policy gap" alert: when a component passes stage Conforma but would fail prod
- Run prod-policy simulation before release cuts
- Track policy gap trends over time

---

## 3. Nightly Build Monitoring

### 3.1 Full Nightly Trigger Chain Monitoring

**Current gap**: IC monitors nightly build results but doesn't monitor the trigger chain itself (Grafana → `schedule/` files → GHA → Tekton).

**Proposed improvement**:
- Monitor that `schedule/bundle-github-trigger.txt` was touched at the expected time
- Monitor that the GHA workflow ran and completed
- Monitor that the Tekton PipelineRun was created and completed
- Alert if any step in the chain fails silently (e.g., GHA rate limiting, Tekton timeout)

### 3.2 Nightly → Stage Promotion Readiness

**Current gap**: `ic release-readiness` checks current state but doesn't specifically validate the nightly build chain is healthy.

**Proposed improvement**:
- Add a readiness check: "Last nightly completed successfully within 24h"
- Add a readiness check: "FBC fragment images are <24h old"
- Add a readiness check: "PCC cache matches registry.redhat.io shipped versions"

---

## 4. Auto-Merge Monitoring

### 4.1 Track Upstream Sync Health

**Current gap**: IC doesn't monitor whether the upstream auto-merge (80+ repos every 4h) is working correctly.

**Proposed improvement**:
- Monitor the `upstream-auto-merge.yaml` GHA workflow runs
- Alert if a repo fails to sync for >12h
- Track merge conflicts that block auto-merge
- Surface repos where upstream is ahead of downstream

### 4.2 Track Branch Sync Health

**Current gap**: IC doesn't monitor the `main → stable → rhoai` sync chain in rhods-operator.

**Proposed improvement**:
- Compare HEAD commits across `main`, `stable`, `rhoai` branches
- Alert if any pair diverges by >24h or >N commits
- This helps catch sync failures that could delay release branches

---

## 5. Onboarding Improvements

### 5.1 Onboarding Completeness Verification

**Current gap**: IC has onboarding status checks but doesn't verify all required configurations across repos.

**Proposed improvement**:
Verify each component has:
- [ ] PipelineRun YAMLs in `konflux-central/pipelineruns/<name>/.tekton/`
- [ ] `.tekton/` directory in the component repo (synced by `sync-pipelineruns.yml`)
- [ ] Entry in `konflux-central/config.yaml` for Renovate sync
- [ ] Entry in `upstream-source-map.yaml` (for upstream components)
- [ ] Entry in `main-release-source-map.yaml` (for release branch merging)
- [ ] Image reference in `RHOAI-Build-Config/bundle/bundle-patch.yaml`
- [ ] Nudge relationship configured (`build-nudges-ref`)
- [ ] Quay.io image repository created

### 5.2 Cross-Repo Consistency Checks

**Current gap**: Components can be partially onboarded (present in Konflux cluster but missing from Renovate config, or in auto-merge but not in konflux-central).

**Proposed improvement**:
- Cross-reference components across:
  - Konflux cluster (Component CRDs)
  - `konflux-central/pipelineruns/` (pipeline configs)
  - `konflux-central/config.yaml` (Renovate targets)
  - `rhods-devops-infra/src/config/upstream-source-map.yaml`
  - `RHOAI-Build-Config/catalog/catalog_build_args.map`
- Flag components that are missing from any of these systems

---

## 6. Release Process Improvements

### 6.1 Stage Promotion Pre-Check

**Current gap**: Stage promotion (`push-to-stage.yaml`) can fail mid-way if prerequisites aren't met.

**Proposed improvement**:
- Add an `ic release pre-check` command that validates:
  - All component images pass Conforma (stage policy)
  - PCC cache is fresh
  - No expired EC policy exceptions
  - FBC fragment images are available and signed
  - All nudge PRs in RHOAI-Build-Config are merged
  - No pending auto-merge failures in critical repos

### 6.2 Release Timeline Tracking

**Current gap**: IC has `get_release_schedule` but doesn't proactively warn about upcoming milestones.

**Proposed improvement**:
- Add countdown alerts: "Feature freeze in 3 days", "Code freeze tomorrow"
- Cross-reference with current build/Conforma health
- Generate a "release health dashboard" showing: builds OK, Conforma OK, nightly OK, sync OK

---

## 7. Failure Analysis Improvements

### 7.1 Infrastructure Knowledge for AI Analyzer

**Current gap**: The AI analyzer diagnoses failures without deep knowledge of the RHOAI infrastructure relationships.

**Proposed improvement**:
- Feed the infrastructure documentation into the AI analyzer context
- When analyzing a build failure, provide relevant context:
  - Which repo, branch, and pipeline type
  - Whether this component is upstream ODH or downstream RHOAI
  - What the expected build chain is (component → operator → bundle → FBC)
  - What EC policy applies to this component
  - Whether there are related nightly build failures

### 7.2 Pattern Recognition Across Repos

**Current gap**: IC analyzes failures per-component but doesn't detect patterns across the 80+ repo fleet.

**Proposed improvement**:
- Detect systemic issues: "15 repos failed with Go 1.26 error → suggest Renovate base image update"
- Track failure clusters by root cause (dependency issue, infra issue, code issue)
- Correlate failures with auto-merge events (did an upstream sync introduce a breaking change?)

### 7.3 GHA Workflow Failure Tracking

**Current gap**: IC monitors Tekton builds but not GitHub Actions workflows.

**Proposed improvement**:
- Monitor key GHA workflows: `sync-pipelineruns`, `process-operator-bundle`, `trigger-nightlies`, `upstream-auto-merge`
- Alert on GHA failures that affect the build pipeline (e.g., sync failure means component repos have stale pipeline configs)
- Track GHA run duration trends to catch degradation

---

## 8. Data Source Improvements

### 8.1 konflux-release-data Direct Access

**Current gap**: IC uses both K8s API and GitLab API to access EC policies and release plans, but the connection can be flaky.

**Proposed improvement**:
- Clone `konflux-release-data` periodically for offline analysis
- Parse YAML CRDs directly from the repo instead of requiring cluster access
- This gives access to ALL configuration including constraints, exceptions, and RPAs
- Fall back to cluster API only for live state (current Snapshots, running PipelineRuns)

### 8.2 RHOAI-Build-Config Data

**Current gap**: IC doesn't consume data from `RHOAI-Build-Config` (bundle patches, FBC catalogs, PCC cache).

**Proposed improvement**:
- Parse `catalog_build_args.map` to get component→commit mappings
- Monitor `config/build-config.yaml` for supported OCP version changes
- Track `pcc/shipped_rhoai_versions.txt` for PCC freshness
- This provides richer context for FBC-related failure analysis

### 8.3 conforma-reporter Data

**Current gap**: IC generates its own Conforma data via cluster queries. The `conforma-reporter` generates independent daily reports.

**Proposed improvement**:
- Ingest conforma-reporter results as a secondary data source
- Cross-validate: IC cluster data vs reporter data should agree
- Reporter runs with future-date EC policy (prod readiness) — IC can surface these results proactively

---

## 9. New Commands

### 9.1 `ic get chain <component>`
Show the full build chain for a component:
```
kserve → odh-rhel9-operator → rhoai-operator-bundle → rhoai-fbc-fragment
  ✓ built 2h ago    ✓ built 1h ago       ✓ built 45m ago      ✗ FAILED (fips-check)
```

### 9.2 `ic get sync-health`
Show upstream→downstream sync status:
```
opendatahub-io/kserve → red-hat-data-services/kserve
  upstream: abc1234 (2h ago)
  downstream main: abc1234 (synced ✓)
  downstream rhoai-3.5: def5678 (3 commits behind)
```

### 9.3 `ic get renovate-status <app>`
Show MintMaker/Renovate PR status:
```
rhoai-v3-5: 12 open Renovate PRs
  ✓ 8 auto-merged (digest updates)
  ⏳ 3 pending review (minor version bumps)
  ✗ 1 failing CI (rpm-lockfile conflict)
```

### 9.4 `ic get pcc-health`
Show PCC cache freshness:
```
PCC last regenerated: 2026-07-05 (4 days ago)
Registry shipped versions: 12
PCC cached versions: 11
STALE: Missing rhoai-3.5-ea.1 (shipped 2026-07-07)
```

### 9.5 `ic get pipeline-health`
Show Tekton task/pipeline deprecation status:
```
⚠ git-clone-oci-ta: EOL 2026-08-20 (42 days)
  Used by: 65 components in konflux-central
⚠ build-image-index: EOL 2026-08-30 (52 days)
  Used by: 12 FBC fragment builds
```

---

## 10. Proactive Konflux Configuration Advisor

Shift IC from reactive failure detection to proactive configuration auditing. Based on Konflux features available in our installed version that RHOAI doesn't fully leverage.

### 10.1 `ic advisor` — Unified Configuration Audit

Run all sub-audits and produce a unified report with severity scoring:

```
$ ic advisor rhoai-v3-5

RHOAI v3.5 Configuration Advisor — 2026-07-10
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔴 CRITICAL (3):
  • 3 components with deprecated git-clone-oci-ta (EOL Aug 20)
  • 2 EC volatile exclusions expire Aug 1

🟡 WARNING (17):
  • 12 components without caching proxy (avg build 35min vs 18min)
  • 5 security update PRs open >7 days

🔵 INFO (8):
  • 0 periodic integration tests configured
  • 8 components could enable hermetic builds

Total: 3 critical, 17 warnings, 8 info (3 new, 2 resolved)
```

### 10.2 `ic advisor builds` — Build Optimization Audit

Checks per component:
- Missing `hermetic: "true"` in `.tekton/` PipelineRun YAML
- Missing `CACHI2_*` caching proxy env vars
- `on-cel-expression` too broad (redundant rebuilds) or too narrow (stale components)
- Deprecated Tekton task bundles (MintMaker migration PRs not merged)
- Missing `use-trusted-artifacts` pipeline parameter
- Recurring OOM (same component OOMKilled ≥3 times in 7 days)
- Build time regression (>50% above 30-day rolling average)

**Data sources**: `.tekton/` YAMLs via GitHub API, IC build history DB, MintMaker PR tracking

### 10.3 `ic advisor tests` — Testing Gap Detection

Checks per application:
- No CronJob triggering periodic ITS via snapshot labeling
- ITS configured for wrong context (`push` but should also run on `group`)
- ITS resolver bundle unchanged >90 days (stale test pipeline)
- No group snapshot support (no matching branch naming convention across repos)
- Disabled ITS scenarios without annotation explaining why

**Data sources**: ITS CRDs, CronJob CRDs (cluster), GitHub API (branch names)

### 10.4 `ic advisor compliance` — Supply Chain Compliance Audit

Checks per component image:
- Missing SLSA Build L3 provenance attestation in Quay.io OCI referrers
- Incomplete SBOM (declared deps vs build log imports mismatch)
- Stale SARIF scan results (>7 days old)
- Missing signature verification chain

**Data sources**: Quay.io OCI referrers API

### 10.5 `ic advisor deps` — Dependency Management Health

Checks per component repo:
- No `renovate.json5` (MintMaker not configured)
- MintMaker security PRs open >7 days without review
- Missing RPM lockfile for RPM-consuming components
- Auto-merge nudge PRs failing CI (digest update chain broken)

**Data sources**: GitHub API (file presence, PR age, workflow runs), DependencyUpdateCheck CRDs

### 10.6 `ic advisor drift` — Configuration Drift Detection

Cross-reference cluster state vs declared configuration:
- `kustomize build` output differs from cluster Component CRD
- Component CR `spec.source.git.revision` mismatches `.tekton/` push YAML `target_branch`
- ImageRepository credential rotation >90 days ago
- ReleasePlan/ReleasePlanAdmission stale >90 days
- EC policy exclusion gap: exists in stage but missing in prod

**Data sources**: `konflux-release-data` repo (GitLab), cluster CRDs, GitHub API

### 10.7 MCP Tools

| MCP tool | Description |
|----------|------------|
| `get_build_optimization_audit()` | Build config audit findings |
| `get_testing_gap_audit()` | ITS and periodic test gaps |
| `get_supply_chain_audit()` | SLSA, SBOM, scan freshness |
| `get_dependency_health()` | MintMaker, RPM lockfile, security PRs |
| `get_config_drift()` | CaC vs cluster drift |
| `get_advisor_report()` | Unified report (all sub-audits) |

### 10.8 Implementation Notes

- Advisor runs as part of the worker's periodic scan cycle
- Findings stored in IC database for trend tracking (`3 new, 2 resolved` since last run)
- Each finding links to relevant Konflux docs and provides a concrete fix suggestion
- Roadmap Phase 20 breaks this into 6 implementation sub-phases (20a–20f)
- Map Phase 10e (Guided Actions) will consume advisor findings for the UI

---

## Priority Matrix

| Improvement | Impact | Effort | Priority |
|------------|--------|--------|----------|
| Build chain awareness (1.1) | High | Medium | P1 |
| Policy-aware violation triage (2.1) | High | Low (partial existing) | P1 |
| Nightly trigger chain monitoring (3.1) | High | Medium | P1 |
| Infrastructure context for AI analyzer (7.1) | High | Low (docs exist) | P1 |
| Stage promotion pre-check (6.1) | Medium | Medium | P2 |
| Cross-repo consistency checks (5.2) | Medium | Medium | P2 |
| GHA workflow failure tracking (7.3) | Medium | Medium | P2 |
| konfl-release-data direct access (8.1) | Medium | Low | P2 |
| Upstream sync health (4.1) | Low | Low | P3 |
| New commands (9.*) | Low-Medium | Medium per command | P3 |
| Proactive advisor (10.*) | High | High (phased) | P2 |
