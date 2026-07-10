# RHOAI CI/CD Infrastructure — Potential Improvements

> Based on analysis of 8 repositories, Konflux documentation, and IC MCP data (2026-07-09).
> These are observations and suggestions — not bugs or urgent issues.

---

## 1. Build Pipeline Improvements

### 1.1 Deprecated Tekton Tasks Approaching EOL

**Finding**: Two Tekton tasks used across pipelines are approaching end-of-life:
- `git-clone-oci-ta`: EOL 2026-08-20
- `build-image-index`: EOL 2026-08-30

**Impact**: After EOL, builds may fail if the task images are removed from the bundle registry.

**Recommendation**: Update all PipelineRun YAMLs in `konflux-central` and `rhoai-konflux-tasks` to use the replacement tasks. The `sync-pipelineruns.yml` workflow will propagate changes to component repos.

### 1.2 Recurring Build Errors in odh-mod-arch Components

**Finding**: 5 `odh-mod-arch-*` components each had 3 Build Error failures in the last 7 days. Pattern suggests a systematic issue (likely Go 1.26 upgrade or architecture-specific build failures).

**Recommendation**: Investigate the common root cause across these components rather than fixing them individually. These are auto-rebuild candidates — a single pipeline fix could resolve all 5.

### 1.3 Extreme Build Duration (odh-vllm-cpu)

**Finding**: `odh-vllm-cpu` averages 104 minutes per build. This is well above the typical 15-30 minute range.

**Recommendation**: 
- Enable caching proxy (`hermetic-cachi2`) if not already in use
- Consider splitting the build into stages (base image + application layer)
- Investigate if compilation of CPU-specific kernels can be parallelized across build steps
- Set appropriate `PipelineRunTimeout` to avoid blocking the pipeline queue

### 1.4 Missing FIPS Check on FBC Fragment

**Finding**: `rhoai-fbc-fragment` is missing `fbc-fips-check` on all 5 architectures.

**Recommendation**: Add FIPS check tasks to the FBC fragment pipeline in `konflux-central/pipelines/fbc-fragment-build.yaml`. FIPS compliance is a requirement for production releases.

### 1.5 FBC Using Disallowed Base Image

**Finding**: FBC fragments use `brew.registry.redhat.io` base images, which are not allowed for production releases.

**Recommendation**: Switch to `registry.redhat.io` or `registry.access.redhat.com` base images in FBC Dockerfiles. Update `RHOAI-Build-Config/catalog/{version}/v4.{xx}/Dockerfile` on all release branches.

---

## 2. Conforma & Policy Improvements

### 2.1 Volatile Exclusions Expiring Soon

**Finding**: Several volatile EC policy exclusions expire 2026-08-01 (22 days from analysis date). Components covered by these exclusions will start failing Conforma checks.

**Recommendation**: 
- Track these in Jira (many already have references)
- Prioritize fixing the root cause before expiry
- Set up IC alerts for exceptions expiring within 30 days (already partially implemented)

### 2.2 Helm Charts Missing Required Metadata

**Finding**: `rhai-on-openshift` and `rhai-on-xks` charts are missing SBOM, CVE scan results, and 14 required container labels.

**Recommendation**: Add SBOM generation and CVE scanning to the Helm chart build pipeline. The chart Dockerfiles should include all required Red Hat container labels.

### 2.3 Binary Python Wheel Violations

**Finding**: Latency predictor components contain 111 binary Python wheel packages flagged by `sbom_spdx.disallowed_package_attributes`.

**Recommendation**: 
- Pre-compile wheels from source in a dedicated build step
- Or request a targeted EC policy exclusion with a clear remediation plan and timeline
- This is a known issue across the ML/AI ecosystem where vendor-provided wheels are binary-only

### 2.4 Stage vs Prod Policy Gap

**Finding**: Stage EC policy has substantially more exclusions than prod. Components that pass stage validation may fail prod validation.

**Recommendation**: 
- Maintain a visible "policy gap" report (IC already has `get_ec_policy_summary`)
- Before each release, run Conforma with prod policy in dry-run mode
- The `conforma-reporter` already supports this (prod + stage runs), but the results need to be more prominently surfaced

---

## 3. Automation & Process Improvements

### 3.1 Zero ITS Scenarios for rhoai-v3-5

**Finding**: IC reports 0 IntegrationTestScenario CRDs for `rhoai-v3-5`. This means no cluster-native integration tests run on Snapshots.

**Recommendation**: 
- Investigate if ITS scenarios are managed through a different mechanism (possibly Jenkins, Testing Farm)
- If integration testing is external, document the connection in `konflux-release-data`
- Consider migrating at least smoke tests to native Konflux ITS for faster feedback

### 3.2 PCC Cache Staleness

**Finding**: PCC (Pre-Computed Catalog) cache is not auto-regenerated after production releases. Stale PCC can block stage promotion.

**Recommendation**: 
- Add a post-release hook that triggers `regen-pcc-cache.yaml` automatically
- Or schedule PCC regeneration to run weekly/daily
- Add a PCC freshness check to `ic release-readiness`

### 3.3 Auto-Rebuild for Transient Failures

**Finding**: 4 components have transient `slsa_source_correlated` violations that would resolve with a rebuild. 6 build-failure components are auto-rebuild candidates.

**Recommendation**: 
- Implement automated rebuild for components with known-transient failure patterns
- IC already has `trigger_rebuild` capability — integrate it with pattern detection
- Add a configurable auto-rebuild policy per application (max retries, cooldown period)

### 3.4 Nightly Build Failure Visibility

**Finding**: Nightly build failures are now surfaced in `ic get alerts`, but the connection between nightly triggers (Grafana/cron → `rhods-devops-infra` → `RHOAI-Build-Config`) is fragile.

**Recommendation**: 
- Add health checks to the nightly trigger chain (if schedule/ files weren't touched, alert)
- Consider moving nightly scheduling to Konflux's native `CronJob` feature (scheduled builds)
- Monitor the Grafana dashboard for trigger failures

### 3.5 ODH Early-Gate System for RHOAI

**Finding**: ODH has an early-gate system (`/early-gate` PR comments trigger full operator+bundle+FBC builds) that RHOAI doesn't have.

**Recommendation**: Consider adopting early-gate for RHOAI release branches. This would catch integration issues earlier by validating the full build chain before merge, rather than discovering them in nightly builds.

---

## 4. Configuration Consistency Improvements

### 4.1 Renovate Config Base Branch Drift

**Finding**: `default-renovate.json5` lists specific base branches (`main`, `rhoai-2.25`, `rhoai-3.3`, `rhoai-3.4`, `rhoai-3.5-ea.2`). These need to be updated for every new release.

**Recommendation**: 
- Automate base branch discovery from `releases.yaml` or the Konflux Application CRDs
- Or use branch patterns (`rhoai-*`) with MintMaker's `baseBranchPatterns` (noting the known caveats documented in Konflux docs)
- Add a validation step to `sync-renovate-configs.yml` that warns if a release branch is missing

### 4.2 Inconsistent Pipeline References

**Finding**: ODH uses inline pipeline definitions in `odh-konflux-central/pipeline/`, while RHOAI uses git resolver references to `rhoai-konflux-tasks/pipelines/`. Different repos reference different versions.

**Recommendation**: This is an intentional architectural difference (ODH is self-contained, RHOAI separates tasks from pipelines). But ensure version pinning is consistent: use tagged refs rather than `main` branch for pipeline references in production builds.

### 4.3 Auto-Merge Scope

**Finding**: Three repos auto-merge nudge PRs from Konflux bots (`operator-insta-merge`, `bundle-insta-merge`, `fbc-insta-merge`). These merge without human review.

**Recommendation**: 
- Ensure auto-merge workflows verify the PR author is the expected bot
- Add basic validation (e.g., only YAML changes, no code changes)
- Monitor for unexpected merge patterns via IC

---

## 5. Documentation & Observability Improvements

### 5.1 Missing Component-to-Repo Map for RHOAI

**Finding**: ODH has an auto-generated `component_repo_map.json` mapping every component to its quay image repo. RHOAI doesn't have an equivalent.

**Recommendation**: Generate a similar map for RHOAI from `konflux-central/pipelineruns/`. This would help IC and other tools resolve component names to repos without cluster access.

### 5.2 Release Data Freshness

**Finding**: `rhai-release-data.yaml` in `rhods-devops-infra` is auto-generated from Product Pages. But it may lag behind schedule changes.

**Recommendation**: 
- Add a freshness indicator (last-sync timestamp) to the file
- IC already consumes this via `get_release_schedule` — add a staleness alert if the file is >24h old

### 5.3 Sealights Integration Documentation

**Finding**: `rhoai-inject-sealights-oci-ta` task injects test coverage instrumentation but its usage and configuration are undocumented outside the task YAML.

**Recommendation**: Document when Sealights injection is enabled, which components use it, and how to interpret results. This is important for build troubleshooting.

---

## 6. Security Improvements

### 6.1 RPM Signature Key Management

**Finding**: EC policies permanently exclude RPM signature checks for CUDA and ROCm vendor packages. This is necessary but creates a security gap.

**Recommendation**: 
- Document which specific packages come from each vendor
- Set up periodic review of the excluded packages
- Consider adding a verification step that checks vendor-specific GPG keys instead of skipping entirely

### 6.2 Secret Scanning in Build Artifacts

**Finding**: `rhoai-konflux-tasks` includes `secure-push-oci` and `secure-git-push` StepActions that scan for secrets with `leaktk-scanner` before pushing artifacts.

**Recommendation**: Ensure all artifact push paths use these secure StepActions. Verify the scanner configuration covers common secret patterns (API keys, tokens, credentials).

---

## Priority Matrix

| Improvement | Impact | Effort | Priority |
|------------|--------|--------|----------|
| Deprecated Tekton tasks (1.1) | High (builds will break) | Low | P0 |
| Volatile exclusions expiring (2.1) | High (releases blocked) | Medium | P0 |
| FBC disallowed base image (1.5) | High (prod release blocked) | Low | P1 |
| FIPS check missing (1.4) | High (compliance) | Medium | P1 |
| PCC cache staleness (3.2) | Medium (stage promotion delayed) | Low | P1 |
| Auto-rebuild transient failures (3.3) | Medium (toil reduction) | Medium | P2 |
| Helm chart metadata (2.2) | Medium (compliance) | Medium | P2 |
| Renovate base branch drift (4.1) | Low (manual update needed per release) | Low | P2 |
| Component-to-repo map (5.1) | Low (tooling improvement) | Low | P3 |
| Early-gate for RHOAI (3.5) | Medium (earlier feedback) | High | P3 |

---

## 7. What's Left To Do (Pending Work)

### 7.1 Short-Term (Next 2 Weeks)

| Task | Urgency | Who/Where |
|------|---------|-----------|
| Update deprecated Tekton tasks (`git-clone-oci-ta`, `build-image-index`) before EOL | **Urgent** (EOL 2026-08-20/30) | `konflux-central/pipelineruns/` + `rhoai-konflux-tasks/pipelines/` |
| Fix volatile EC exclusions before expiry 2026-08-01 | **Urgent** | Component teams + `konflux-release-data` |
| Fix FBC `brew.registry.redhat.io` base image | **High** | `RHOAI-Build-Config/catalog/*/Dockerfile` |
| Add `fbc-fips-check` to FBC fragment pipeline | **High** | `konflux-central/pipelines/fbc-fragment-build.yaml` |
| Regenerate PCC cache after latest GA ship | **Medium** | Run `regen-pcc-cache.yaml` in `RHOAI-Build-Config` |
| Resolve `odh-mod-arch-*` recurring Build Errors (5 components) | **Medium** | Investigate common root cause across components |
| Fix `odh-kserve-autogluon-server` Python Error (5 failures) | **Medium** | Component team |

### 7.2 Medium-Term (Next 1-2 Months)

| Task | Impact | Where |
|------|--------|-------|
| Automate PCC regeneration (post-release hook) | Prevents stage promotion delays | `RHOAI-Build-Config` or `rhods-devops-infra` |
| Implement auto-rebuild for transient `slsa_source_correlated` violations | Reduces toil (4+ components) | IC tool (`trigger_rebuild` + pattern detection) |
| Add Helm chart SBOM/CVE scanning | Compliance requirement | Chart build pipeline |
| Automate Renovate base branch discovery | Removes manual step per release | `konflux-central/renovate/` |
| Build component-to-repo map for RHOAI (like ODH's `component_repo_map.json`) | Improves tooling | `konflux-central` |
| Add IC build chain awareness (component → operator → bundle → FBC tracking) | Better failure analysis | IC tool |
| Monitor GHA workflow health for critical workflows | Catch silent failures | IC tool |
| Implement nightly trigger chain monitoring | End-to-end nightly visibility | IC tool |

### 7.3 Long-Term / Aspirational

| Task | Impact | Notes |
|------|--------|-------|
| Adopt early-gate system for RHOAI (like ODH has) | Pre-merge integration validation | High effort, would catch integration issues earlier |
| Migrate ITS scenarios to native Konflux | Faster feedback loop | Currently external (Jenkins/Testing Farm?) |
| Native Konflux CronJob for nightly builds | Replace GHA→file-touch chain | Simplifies nightly trigger chain |
| Full policy gap simulation before release | No surprise prod failures | Use conforma-reporter prod results proactively |
| Track binary Python wheel vendors | Audit and reduce SBOM violations | Long-running compliance item |
| Reduce `odh-vllm-cpu` build time (104 min average) | Queue/resource efficiency | Caching, build splitting |

### 7.4 Tracking Konflux New Features

Monitor `https://github.com/konflux-ci` for new features to adopt:

| Area | What to Watch | Why |
|------|-------------|-----|
| `konflux-ci/build-definitions` | New Tekton task versions, EOL announcements | Must update before tasks are removed |
| `konflux-ci/mintmaker` | Renovate manager updates, new config options | Better dependency management |
| `konflux-ci/release-service` | Release pipeline improvements | More reliable releases |
| `konflux-ci/integration-service` | ITS improvements, new test frameworks | Better test coverage |
| `konflux-ci/enterprise-contract` | New policy rules, Conforma improvements | Compliance changes may affect components |
| `konflux-ci/image-controller` | Image repository management changes | Quay.io integration |
| `konflux-ci/build-trusted-artifacts` | OCI-TA improvements | Security and build isolation |

**How to track**: Set up GitHub notifications or RSS for releases in key repos. Consider adding an `ic get konflux-updates` command that checks latest releases in these repos

### 7.5 Konflux New Features Evaluation (updated 2026-07-10)

Features discovered from Konflux repo commits and docs. Evaluated for RHOAI adoption.

#### Adopt (high value, reasonable effort)

| Feature | Source | Impact for RHOAI | Effort |
|---------|--------|-----------------|--------|
| **Scheduled Builds (CronJobs)** | [Konflux docs](https://konflux-ci.dev/docs/building/scheduled-builds/) | **Replaces fragile GHA→file-touch nightly trigger chain** (improvement 3.4). Native K8s CronJob annotates Component CR to trigger PaC build. Simpler, more reliable, no GitHub dependency. | Low — one CronJob manifest per component |
| **Task Migrations (pipeline-migration-tool)** | [build-definitions](https://github.com/konflux-ci/build-definitions) + [docs](https://konflux-ci.dev/docs/building/apply-task-migrations/) | MintMaker auto-applies task migrations in Renovate PRs. Partially solves deprecated tasks (1.1) — if Renovate PRs are merged promptly, tasks migrate automatically via `pmt`. | Low — already built into MintMaker |
| **Periodic Integration Tests** | [Konflux docs](https://konflux-ci.dev/docs/testing/integration/periodic-integration-tests/) | Solves improvement 3.1 (zero ITS scenarios). CronJob labels latest snapshot to trigger ITS on schedule. Native Konflux, no external CI needed. | Medium — requires RBAC + CronJob + ITS definition |
| **Managed Pipeline Retry** | release-service commit `RELEASE-2120` (2026-07-08) | Release pipelines now retry with mitigations. Reduces manual intervention on transient release failures (network timeouts, registry flakes). | Low — update release-service version |
| **Group Snapshots** | [Konflux docs](https://konflux-ci.dev/docs/testing/integration/snapshots/group-snapshots/) | Coordinate linked PR branches across components into single snapshot. Useful for cross-component changes (Go version upgrades, shared dep bumps). | Medium — requires PR branch naming convention |

#### Monitor (not urgent, watch for maturity)

| Feature | Source | Notes |
|---------|--------|-------|
| **Skip Draft PR Builds** | image-controller (2026-06-29) | Saves build resources. RHOAI may already have this via PaC on-cel-expression. |
| **Dual-group ImageRepository API** | image-controller (2026-06-23) | API migration support. Watch but don't adopt yet. |
| **Preventing Redundant Rebuilds** | [Konflux docs](https://konflux-ci.dev/docs/building/redundant-rebuilds/) | PaC on-cel-expression to rebuild only affected components in monorepo. Already partially used. |

#### Not relevant

| Feature | Why skip |
|---------|---------|
| Forgejo onboarding | RHOAI uses GitHub |
| Pulp storage | RHOAI ships OCI artifacts |
| Configuration as Code (Kustomize) | Already using `.tekton/` + `konflux-central` pattern |

### 7.6 IC Tool Bugs to Fix (identified 2026-07-09)

| Bug | Impact | Fix |
|-----|--------|-----|
| `ic get conforma rhoai-v3-5-ea-2` ignores positional arg | **High** — always queries default app, wrong data in triage | Rename `filter` → `application` in CLI, wire into `app_name` resolution |
| Release-readiness counts ALL conforma violations as blockers | **High** — reports 41 blockers when only 5 are real (rest covered by exceptions) | Add exception-aware logic from `compute_blocks()` to readiness endpoint |

### 7.7 Proactive Konflux Configuration Advisor

**Goal**: Shift IC from reactive (detect failures after they happen) to proactive (audit configuration and suggest improvements before problems occur). Based on Konflux features available in our installed version that RHOAI is not fully leveraging.

**Why this matters**: Most build failures, Conforma violations, and release delays are caused by configuration gaps — not bugs. A misconfigured `on-cel-expression` causes redundant rebuilds (or missed builds). A missing periodic integration test means regressions hide until nightly. An expired EC exception surprises the team at release time. These are all preventable.

#### 7.7a — Build Optimization Audit

| Finding type | What IC checks | Why it matters |
|-------------|---------------|----------------|
| Missing hermetic builds | Component `.tekton/` YAML lacks `hermetic: "true"` | Required for SLSA Build L3. Builds without hermetic mode can access the network, breaking reproducibility guarantees |
| Missing caching proxy | No `CACHI2_*` env vars in PipelineRun | Builds download dependencies every run. Enabling caching proxy reduces build time 20-50% |
| Misconfigured CEL expression | `on-cel-expression` too broad or too restrictive | Too broad → redundant rebuilds waste pipeline queue. Too restrictive → pushes don't trigger builds (shows as "stale component") |
| Deprecated Tekton tasks | Task bundle SHA matches known deprecated version, MintMaker migration PR not merged | After EOL, builds fail when task images are removed from registry |
| Missing trusted artifacts | Pipeline doesn't use `use-trusted-artifacts` | Tasks share data via workspace volume — less secure than OCI-mediated trusted artifacts |
| Recurring OOM | Same component OOMKilled ≥3 times in 7 days | Should increase `overriding-compute-resources` for the build task |
| Build time regression | Build duration >50% above 30-day rolling average | Possible dependency bloat, missing cache, or infrastructure degradation |

**Data sources**: Component CR `.tekton/` YAMLs (via GitHub API), build history (IC DB), MintMaker PR tracking

#### 7.7b — Testing Gap Detection

| Finding type | What IC checks | Why it matters |
|-------------|---------------|----------------|
| No periodic integration tests | Application has no CronJob triggering ITS via snapshot labeling | Regressions hide until manual testing or nightly builds. [Konflux docs: Periodic Integration Tests](https://konflux-ci.dev/docs/testing/integration/periodic-integration-tests/) |
| ITS wrong context | Scenario configured for `push` but should also run on `group`, or disabled entirely | Tests run at wrong time — either too late (miss PR feedback) or never |
| Stale ITS Git ref | ITS resolver bundle unchanged >90 days | Test pipeline may reference deprecated tasks or outdated test logic |
| No group snapshot support | No components with matching branch naming convention | Cross-component changes (Go upgrades, shared dep bumps) can't be tested together. [Konflux docs: Group Snapshots](https://konflux-ci.dev/docs/testing/integration/snapshots/group-snapshots/) |

**Data sources**: ITS CRDs (cluster), CronJob CRDs (cluster), GitHub API (branch names)

#### 7.7c — Supply Chain Compliance Audit

| Finding type | What IC checks | Why it matters |
|-------------|---------------|----------------|
| Missing SLSA provenance | Image in Quay.io has no attestation referrer | SLSA Build L3 requires provenance attestation for every artifact |
| Incomplete SBOM | SBOM document missing declared dependencies vs build log imports | Compliance gap — SBOM should reflect actual build inputs |
| Stale scan results | SARIF scan >7 days old | CVE landscape changes daily; old scans create false confidence |

**Data sources**: Quay.io OCI referrers API (attestations, SBOMs, SARIF), build history (timestamps)

#### 7.7d — Dependency Management Health

| Finding type | What IC checks | Why it matters |
|-------------|---------------|----------------|
| No MintMaker/Renovate | Component repo has no `renovate.json5` | Dependency updates require manual PR creation — no auto-bump |
| Ignored security updates | MintMaker security PR open >7 days without review | CVE exposure window grows; compliance risk |
| Missing RPM lockfile | Component uses RPMs but no lockfile refresh configured | RPM versions drift between builds — non-reproducible |
| Auto-merge failures | Nudge PRs that should auto-merge but CI fails | Digest update chain is broken — downstream images reference stale SHAs |

**Data sources**: GitHub API (renovate.json5 presence, PR age, auto-merge workflow runs), MintMaker DependencyUpdateCheck CRDs

#### 7.7e — Configuration Drift Detection

| Finding type | What IC checks | Why it matters |
|-------------|---------------|----------------|
| CaC drift | `kustomize build` output differs from cluster Component CRD state | Cluster state has diverged from declared configuration — manual edits bypassed CaC |
| .tekton/ mismatch | Component CR `spec.source.git.revision` doesn't match `.tekton/` push YAML `target_branch` | Builds target wrong branch — images built from unexpected commits |
| Credentials approaching expiry | ImageRepository `credentials-last-rotated` annotation >90 days ago | Quay push credentials will expire, breaking builds silently |
| Stale release config | ReleasePlan or ReleasePlanAdmission `metadata.creationTimestamp` >90 days, no updates | Release pipeline may reference outdated policy or target |
| Policy gap (stage ≠ prod) | EC policy exclusion exists in stage but not prod | Component passes stage Conforma but will fail at prod release gate |

**Data sources**: `konflux-release-data` repo (GitLab), cluster CRDs, GitHub API (`.tekton/` files)

#### Implementation approach

This should be a **single `ic advisor` command** that runs all sub-audits and produces a unified report:

```
$ ic advisor rhoai-v3-5

RHOAI v3.5 Configuration Advisor — 2026-07-10
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔴 CRITICAL (will block release):
  • 3 components with deprecated git-clone-oci-ta (EOL Aug 20)
  • 2 EC volatile exclusions expire Aug 1

🟡 WARNING (risk/toil):
  • 12 components without caching proxy (avg build 35min vs 18min with cache)
  • 5 security update PRs open >7 days (highest: CVE-2026-1234, Critical)
  • odh-vllm-cpu build time increased 40% in last 2 weeks

🔵 INFO (improvement opportunity):
  • 0 periodic integration tests configured (Konflux supports CronJob-based)
  • 8 components could enable hermetic builds for SLSA L3
  • No group snapshot support configured for coordinated PRs

Total: 3 critical, 17 warnings, 8 informational
(3 new since last run, 2 resolved)
```

Each finding links to the relevant Konflux documentation and provides a concrete fix suggestion. The advisor runs as part of the worker's periodic scan cycle, with findings stored in the IC database for trend tracking.

**MCP tools**: `get_build_optimization_audit()`, `get_testing_gap_audit()`, `get_supply_chain_audit()`, `get_dependency_health()`, `get_config_drift()`, and a unified `get_advisor_report()`

**Relationship to existing features**:
- Phase 16c Build/Release Config Analyzers are *reactive* (find problems in current failures) — the Advisor is *proactive* (audit config regardless of current failures)
- Phase 20 in the ROADMAP consolidates this into a phased implementation plan
- Map Phase 10e (Guided Actions) consumes advisor findings to suggest improvements in the UI

### 7.8 IC Tool New Commands for Advisor

| Command | What it does |
|---------|-------------|
| `ic advisor [app]` | Run all audits, unified report |
| `ic advisor builds [app]` | Build optimization audit only |
| `ic advisor tests [app]` | Testing gap detection only |
| `ic advisor compliance [app]` | Supply chain compliance audit only |
| `ic advisor deps [app]` | Dependency management health only |
| `ic advisor drift [app]` | Configuration drift detection only |

---

### 7.5 Interactive System Map (Visual Infrastructure Navigator)

**Goal**: Build a live, interactive digital twin of the RHOAI CI/CD infrastructure. Not a static diagram — a system that discovers, visualizes, and monitors the real infrastructure, detects drift, and shows resolution workflows in real time.

**Problem it solves**: Understanding the system requires knowing which of 8+ repos to look in, which GitHub Action does what, where policies live, how the build chain flows. New team members (and even the AI) have to memorize this or search blindly. Documentation goes stale the moment it's written. A visual map that maintains itself makes the entire system discoverable at a glance and always accurate.

#### Architecture

**Standalone system** in `map/` subdirectory — desacoplado de IC:

```
map/
├── backend/                    # FastAPI + Neo4j (standalone)
│   ├── main.py                 # Uvicorn entrypoint
│   ├── graph.py                # Neo4j connection + Cypher queries
│   ├── routes.py               # REST API endpoints
│   ├── ic_client.py            # Optional HTTP client to IC API
│   ├── discovery.py            # Auto-discovery: GitHub/GitLab/cluster polling
│   └── events.py               # SSE/WebSocket event stream
├── frontend/                   # React + React Flow
│   ├── src/
│   │   ├── App.tsx             # Main layout
│   │   ├── MapCanvas.tsx       # React Flow graph renderer
│   │   ├── NodeDetail.tsx      # Click-to-detail panel
│   │   ├── SearchBar.tsx       # Full-text search
│   │   ├── ResolutionTimeline.tsx  # IC resolution step viz (Phase 3)
│   │   └── nodes/              # Custom node components per type
│   └── package.json
├── seed.py                     # Populate graph from live sources
├── pyproject.toml              # Own deps: neo4j, fastapi, uvicorn, httpx
├── Dockerfile
└── README.md
```

**Key design principles**:
- **Desacoplado**: Funciona sin IC. IC es una fuente de datos opcional vía HTTP API.
- **Live data first**: La topología se descubre de fuentes reales (GitHub API, GitLab API, cluster), no de documentación estática. Los docs son fallback, no fuente de verdad.
- **Graceful degradation**: Sin IC → muestra topología estática. Sin GitHub token → usa datos cacheados. Sin Neo4j → error claro.

```
┌─────────────────────────────────────────────────────────┐
│                 Frontend (React + React Flow)             │
│  Interactive graph · Custom nodes per type · Zoom levels  │
│  Click → detail panel · Search · Path tracing · Deep links│
│  Resolution timeline (Phase 3) · Guided tours (Phase 6)  │
└────────────────────────┬────────────────────────────────┘
                         │ REST API + SSE
┌────────────────────────▼────────────────────────────────┐
│              Map Backend (FastAPI + Neo4j)                │
│  /api/map/nodes       → all nodes with positions          │
│  /api/map/edges       → all relationships                 │
│  /api/map/node/{id}   → node detail + live status         │
│  /api/map/search      → full-text search across graph     │
│  /api/map/path/{a}/{b}→ shortest path between nodes       │
│  /api/map/explain/{id}→ AI explanation of any node        │
│  /api/map/events      → SSE stream of changes (Phase 4+)  │
│  /api/map/resolution/{id} → resolution steps (Phase 3)    │
└────────────┬──────────────────────┬─────────────────────┘
             │ direct queries       │ optional HTTP
┌────────────▼──────────┐  ┌───────▼─────────────────────┐
│   Data Sources         │  │    IC API (optional)         │
│  · GitHub API          │  │  GET /api/v1/health          │
│  · GitLab API          │  │  GET /api/v1/alerts          │
│  · Konflux cluster     │  │  GET /api/v1/conforma        │
│  · Neo4j (topology)    │  │  WebSocket /ws/events        │
└────────────────────────┘  └─────────────────────────────┘
```

#### Node types

| Type | Examples | Click action |
|------|----------|-------------|
| **Repository** | konflux-central, RHOAI-Build-Config | Purpose, key files, recent GHA runs |
| **Workflow** | sync-pipelineruns, trigger-nightlies, conforma-reporter | Trigger type, schedule, last run status |
| **Pipeline** | docker-build-remote, fbc-builder | Tasks, parameters, which components use it |
| **TektonTask** | buildah-remote-oci-ta, fbc-fips-check | Version, bundle SHA, EOL status |
| **Component** | odh-dashboard-v3-5, kserve-v3-5 | Build status, image, repo, branch, health |
| **Application** | rhoai-v3-5, rhoai-v3-5-ea-2 | Components list, overall health, release status |
| **ECPolicy** | registry-rhoai-stage, fbc-rhoai-prod | Rules, exceptions, affected components |
| **ReleasePlan** | rhoai-v3-5-stage, rhoai-v3-5-prod | Target registry, policy binding, last release |
| **ConfigFile** | build-config.yaml, catalog-patch.yaml | What it controls, who reads it, last change |
| **Automation** | MintMaker, Renovate, Nudge Chain, PaC | Schedule, health, what it manages |

#### Relationships

| From | Relationship | To | Example |
|------|-------------|-----|---------|
| Application | CONTAINS | Component | rhoai-v3-5 → odh-dashboard-v3-5 |
| Component | BUILDS_WITH | Pipeline | kserve-v3-5 → docker-build-remote |
| Component | NUDGES | Component | operator → bundle → FBC fragment |
| Pipeline | USES_TASK | TektonTask | docker-build → buildah-remote-oci-ta |
| Repository | CONTAINS | Workflow | rhoai-build-config → trigger-nightlies |
| Repository | CONTAINS_CONFIG | ECPolicy | konflux-release-data → registry-rhoai-prod |
| ECPolicy | VALIDATES | Application | fbc-rhoai-prod → rhoai-v3-5 |
| Automation | TRIGGERS | Pipeline | PaC → docker-build-remote |
| Automation | UPDATES | TektonTask | Renovate → buildah-remote-oci-ta |
| Automation | CREATES_PR_FOR | Repository | MintMaker → rhods-operator |

#### Phased Roadmap

##### Phase 1: Static Map + Limitations Analysis (Week 1 — START: 2026-07-10)

**Deliverable**: Navigable interactive map of RHOAI CI/CD infrastructure with click-to-detail AND a clear view of what's missing, broken, or misconfigured.

**What**:
- Neo4j schema: 10 node types, 10 relationship types, constraints + indexes
- Seeding script: populates graph from GitHub API (repos, workflows) + cluster DB (components, applications) + static fallback (pipelines, tasks, automations, policies)
- FastAPI backend: 6 endpoints (`/nodes`, `/edges`, `/node/{id}`, `/search`, `/path/{a}/{b}`, `/explain/{id}`)
- React Flow frontend: interactive graph with custom node shapes per type, zoom, pan, minimap, click-to-detail panel
- Deep links: every node has a shareable URL (`/map/node/comp-odh-dashboard-v3-5`)
- **Limitations layer**: each node can have `gaps` (things missing) and `issues` (things broken), surfaced visually as warning badges

**Limitations the map must surface**:
- Components without PaC webhooks (builds won't auto-trigger)
- Components with no successful build in N days (stale)
- EC policies without stage+prod pair (incomplete coverage)
- Workflows with no recent runs (dead automation)
- Missing nudge chain links (component exists but no operator→bundle→FBC path)
- Tekton tasks approaching EOL (unmerged Renovate PRs)
- Repos with no branch protection or missing `.tekton/` directory
- Components not covered by any IntegrationTestScenario
- Orphan nodes: components in cluster but not in any Application

**Architecture decisions**:
- Standalone in `map/` — own `pyproject.toml`, own `Dockerfile`, no IC imports
- Neo4j for topology (graph traversal), no Postgres dependency
- Seeding pulls from live data first (GitHub API, cluster DB), documentation as fallback
- Frontend served by the same FastAPI app (static files) for zero-config deployment

**Tech stack**: Python 3.11+, FastAPI, neo4j driver, React 18, React Flow, TypeScript

##### Phase 2: Live Status Overlay (Week 2-3)

**Deliverable**: Map shows real-time build/conforma/nightly status on each node.

**What**:
- `ic_client.py`: HTTP client that calls IC API endpoints for health data
- Status overlay: green/yellow/red indicators on Component and Application nodes
- Alert badges: number of active alerts per node
- Conforma violations: per-component violation count with severity
- Nightly build status: FBC fragment freshness indicator
- Auto-refresh: polling IC API every 60s (configurable)
- Graceful degradation: if IC is down, show last-known status with "stale" indicator

**IC endpoints consumed** (read-only, no coupling):
- `GET /api/v1/applications/{app}/health` → component health scores
- `GET /api/v1/applications/{app}/alerts` → active build failures + conforma violations
- `GET /api/v1/applications/{app}/conforma` → violation details
- `GET /api/v1/applications/{app}/nightly` → FBC fragment + nightly status

##### Phase 3: Resolution Visualization (Week 3-4)

**Deliverable**: When IC resolves an alert, the map shows the resolution workflow step by step in real time.

**What**:
- Resolution timeline component: shows each step of IC's fix workflow (diagnose → identify fix → create PR → rebuild → verify)
- IC event integration: IC emits events per resolution step (requires adding event emitting to IC skills/analyzers)
- WebSocket/SSE stream: map subscribes to IC resolution events
- Step states: pending → in_progress → success/failed, with duration and details
- Path highlighting: when viewing a resolution, the map highlights all affected nodes (component → pipeline → policy)

**IC changes required**:
- Add event bus to IC skill execution (each step emits `{step, status, detail, timestamp}`)
- New IC endpoint: `GET /api/v1/resolutions/{id}/events` (SSE stream)
- New IC endpoint: `GET /api/v1/resolutions/active` (list in-progress resolutions)

**Example flow** (conforma violation fix):
```
1. [diagnose]     AI analyzes violation → identifies root cause          ⏱ 12s
2. [classify]     Maps to known pattern: "untrusted task bundle"         ⏱ 1s
3. [find_fix]     Checks konflux-central for unmerged Renovate PR        ⏱ 5s
4. [create_pr]    Merges Renovate PR #847 in konflux-central             ⏱ 3s
5. [trigger]      Triggers rebuild of affected component                 ⏱ 2s
6. [wait_build]   Waiting for PipelineRun to complete...                 ⏱ 45m
7. [verify]       Re-runs conforma check → violation resolved ✓          ⏱ 8s
```

##### Phase 4: Auto-Discovery (Week 5-6)

**Deliverable**: Map automatically discovers infrastructure changes and updates itself.

**What**:
- GitHub poller: periodically scans repos for new/changed workflows, config files
- GitLab poller: checks EC policy files, ReleasePlanAdmissions for changes
- Cluster poller: detects new/removed Components, Applications, IntegrationTestScenarios
- Diff engine: compares discovered state with graph state, generates change events
- Change log: every auto-discovered change is recorded with timestamp and source
- Notifications: new node types get a "NEW" badge until acknowledged

**Polling strategy**:
| Source | Interval | What changes |
|--------|----------|-------------|
| GitHub repos (GHA workflows) | 1h | New/modified/deleted workflows |
| GitHub repos (config files) | 1h | build-config.yaml, catalog-patch.yaml |
| GitLab EC policies | 6h | Exception additions/removals, rule changes |
| Cluster Components | 30m | New components, deleted components, branch changes |
| Cluster ITS | 6h | Test scenario coverage changes |

**Reconciliation rules**:
- Auto-discovered nodes are marked `source: "discovered"` vs `source: "seed"`
- Discovered data takes precedence over seed data (live > static)
- Deleted nodes are soft-deleted (marked `_deleted: true`) with retention for 7 days
- Conflicting properties are flagged for human review

##### Phase 5: Change Detection & Drift Alerts (Week 7)

**Deliverable**: Map detects when reality diverges from expected state and raises alerts.

**What**:
- Expected state definitions: declare what the infrastructure SHOULD look like (e.g., "every component must have a PaC webhook", "every EC policy must have a stage and prod variant")
- Drift detector: compares expected vs discovered state
- Drift alerts: surface as notifications in the map UI
- Integration with IC: optionally create Jira tickets for infrastructure drift

**Example drift detections**:
- "Component `odh-new-thing-v3-5` has no PaC webhook configured" (missing automation)
- "Workflow `trigger-nightlies` was deleted from `rhoai-build-config`" (removed infra)
- "EC policy `fbc-rhoai-prod` has 3 new exceptions added this week" (policy change)
- "Component `odh-dashboard-v3-5` hasn't built in 14 days" (stale component)
- "Renovate PR #912 in konflux-central has been open for 5 days without merge" (blocked automation)

**Drift rules** (configurable YAML):
```yaml
rules:
  - name: pac-webhook-required
    match: {type: Component}
    expect: {has_relationship: "TRIGGERED_BY -> Automation[name='PaC']"}
    severity: warning

  - name: ec-policy-pair
    match: {type: ECPolicy, scope: registry}
    expect: {count_by: [scope, environment], min: 2}  # stage + prod
    severity: error

  - name: component-freshness
    match: {type: Component}
    expect: {property: last_build_age, max_days: 14}
    severity: warning
```

##### Phase 6: Tutorial Integration (Week 8)

**Deliverable**: `/learn` tutorial topics link to map regions. Interactive guided tours.

**What**:
- Map regions: each `/learn` topic maps to a subgraph (e.g., "Topic 4: Builds" → build pipeline subgraph)
- Guided tours: step-by-step walk-through that zooms/pans to relevant nodes with explanations
- Contextual "Learn more": every node detail panel has a "Learn about this" link to the relevant tutorial topic
- Interactive exercises: "Click on the FBC fragment and trace its build chain back to the component"

**Tour definitions** (JSON):
```json
{
  "tour": "build-chain",
  "title": "Understanding the RHOAI Build Chain",
  "steps": [
    {"focus": "comp-kserve-v3-5", "text": "A component like kserve starts here..."},
    {"focus": "pipeline-docker-build", "text": "It builds using this Tekton pipeline..."},
    {"focus": "automation-nudge", "text": "After build, MintMaker creates a nudge PR..."},
    {"focus": "comp-rhods-operator-v3-5", "text": "The operator picks up the new image..."},
    {"focus": "comp-rhoai-fbc-fragment-v3-5", "text": "Finally, the FBC fragment rebuilds with everything"}
  ]
}
```

#### Technology Choices

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Backend language** | Python (FastAPI) | Team expertise, Neo4j driver mature, iteration speed > performance |
| **Graph database** | Neo4j | Topology IS a graph. Cypher traversals are 1 line vs recursive SQL CTEs |
| **Frontend** | React + React Flow | Interactive graph rendering, custom nodes, zoom/pan, minimap, mature ecosystem |
| **Deployment** | Subdirectory (`map/`) | Easy to start; can move to separate repo later if needed |
| **IC integration** | HTTP API (desacoplado) | Map works without IC. IC is optional data source for live status |
| **Real-time** | SSE for events, HTTP polling for status | SSE for resolution streams (Phase 3); polling for health (Phase 2) |
| **Data seeding** | Live APIs first, static fallback | GitHub/GitLab/cluster APIs are source of truth, not documentation |

#### Why API-first matters

The same graph data that renders the visual map is available to the AI. When IC diagnoses a build failure, it can query the map API: "What workflow triggers this build? What config file controls it? What's upstream/downstream?" — without hardcoding relationships. The map becomes the shared brain for both humans and AI.
