# RHOAI CI/CD Infrastructure — Complete System Reference

> Last updated: 2026-07-09
> Purpose: Comprehensive reference for RHOAI CI/CD infrastructure configuration, architecture, and operations.
> Audience: Release engineers, IC analyzer, onboarding automation.

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Repository Map](#2-repository-map)
3. [Upstream/Downstream Flow](#3-upstreamdownstream-flow)
4. [Konflux Platform Concepts](#4-konflux-platform-concepts)
5. [Build Pipelines](#5-build-pipelines)
6. [Operator Bundle and FBC Fragment Lifecycle](#6-operator-bundle-and-fbc-fragment-lifecycle)
7. [Dependency Management (MintMaker)](#7-dependency-management-mintmaker)
8. [Component Nudges](#8-component-nudges)
9. [Release Process](#9-release-process)
10. [Nightly Builds](#10-nightly-builds)
11. [Conforma (Enterprise Contract) Compliance](#11-conforma-enterprise-contract-compliance)
12. [Cluster Configuration (CRDs)](#12-cluster-configuration-crds)
13. [Detailed GitHub Actions & Automation Reference](#13-detailed-github-actions--automation-reference)
    - 13.1 [konflux-central](#131-konflux-central--pipeline-management-hub)
    - 13.2 [rhods-operator](#132-rhods-operator--rhoai-operator)
    - 13.3 [rhods-devops-infra](#133-rhods-devops-infra--release-automation-center)
    - 13.4 [RHOAI-Build-Config](#134-rhoai-build-config--bundle--fbc-build-configuration)
    - 13.5 [odh-konflux-central](#135-odh-konflux-central--odh-pipeline-management)
    - 13.6 [conforma-reporter](#136-conforma-reporter--daily-compliance-reports)
    - 13.7 [rhoai-konflux-tasks (Tekton)](#137-rhoai-konflux-tasks--custom-tekton-tasks)
    - 13.8 [aiops-infra (Skills)](#138-aiops-infra--onboarding-automation-skills)
    - 13.9 [Key Config Files Map](#139-key-config-files--where-to-find-things)
    - 13.10 [Scheduled Automation Summary](#1310-scheduled-automation-summary)
14. [Branch Strategy](#14-branch-strategy)
15. [Onboarding a New Component](#15-onboarding-a-new-component)
16. [Testing Infrastructure](#16-testing-infrastructure)
17. [Automation Skills (aiops-infra)](#17-automation-skills-aiops-infra)
18. [Renovate Configuration](#18-renovate-configuration)
19. [Infrastructure Monitoring (IC)](#19-infrastructure-monitoring-ic)

---

## 1. System Overview

RHOAI (Red Hat OpenShift AI) is built from ~100+ container images flowing through a Konflux-based CI/CD system. Code originates **upstream** in Open Data Hub (ODH, `opendatahub-io`) and is productized **downstream** in RHOAI (`red-hat-data-services`).

### High-Level Architecture

```
   UPSTREAM (ODH)                    DOWNSTREAM (RHOAI)                    DELIVERY
   opendatahub-io/                   red-hat-data-services/
  ┌─────────────────┐              ┌──────────────────────┐            ┌─────────────────┐
  │ Component repos  │──auto-merge──▶ rhoai-* branches     │            │                 │
  │ (kserve, dash-   │  every 4h    │ in component repos   │            │ registry.redhat │
  │  board, etc.)    │              │                      │            │   .io/rhoai/     │
  └────────┬─────────┘              └──────────┬───────────┘            └────────▲────────┘
           │                                   │                                │
           │ odh-konflux-central               │ konflux-central               │
           │ (pipeline templates)              │ (pipeline templates)          │
           ▼                                   ▼                               │
  ┌─────────────────┐              ┌──────────────────────┐           ┌────────┴────────┐
  │ Konflux Builds   │              │ Konflux Builds       │           │ Release Pipeline│
  │ (ODH tenant)     │              │ (RHOAI tenant)       │──────────▶│ (stage → prod)  │
  └─────────────────┘              └──────────┬───────────┘           └─────────────────┘
                                              │ nudge PRs
                                              ▼
                                   ┌──────────────────────┐
                                   │ RHOAI-Build-Config    │
                                   │ (bundle + FBC builds) │
                                   └──────────┬───────────┘
                                              │
                                              ▼
                                   ┌──────────────────────┐
                                   │ rhods-devops-infra    │
                                   │ (stage promotion,     │
                                   │  nightly orchestration│
                                   │  auto-merge crons)    │
                                   └──────────────────────┘
```

### Key Principles

- **SLSA Build Level 3**: All builds produce SLSA provenance attestations.
- **Hermetic builds**: No network access during builds; all dependencies pre-fetched.
- **Multi-arch**: Builds target amd64, arm64, ppc64le, s390x (varies by component).
- **Upstream-first**: Fixes go into ODH upstream repos first, then auto-merge downstream.
- **No EC exceptions by default**: Fix root causes, not policy exceptions.

---

## 2. Repository Map

### Core Infrastructure Repos

| Repository | Org | Purpose | Key Contents |
|-----------|-----|---------|-------------|
| **konflux-central** | red-hat-data-services | Central pipeline management for RHOAI | 65 component PipelineRun YAMLs, 3 Pipeline definitions, 12 GHA workflows, Renovate configs |
| **rhods-operator** | red-hat-data-services | RHOAI operator (Go, multi-arch) | Operator code, Dockerfiles, nudge auto-merge, branch sync workflows, RPM lockfiles |
| **rhods-devops-infra** | red-hat-data-services | Release automation center | Stage promoter (~1000 lines), nightly triggers, auto-merge crons (60+ repos), upstream sync (80+ repos) |
| **RHOAI-Build-Config** | red-hat-data-services | Operator bundle + FBC configuration | Bundle CSV patching, FBC catalog generation, PCC cache, stage promotion, per-OCP Dockerfiles |
| **rhoai-konflux-tasks** | red-hat-data-services | Custom RHOAI Tekton tasks | rhoai-init, trigger-operator-build, trigger-bundle-build, Sealights injection, PR comments |
| **konflux-release-data** | gitlab.cee.redhat.com/releng | Cluster configuration GitOps | ReleasePlans, ReleasePlanAdmissions, EC policies, tenant configs, constraints |
| **conforma-reporter** | red-hat-data-services | Conforma compliance reports | Snapshot generation, daily violation reports (nightly + latest), Slack notifications |
| **odh-konflux-central** | opendatahub-io | ODH pipeline management | ODH PipelineRuns, early-gate system, integration tests, component-repo map |
| **aiops-infra** | opendatahub-io | Onboarding automation skills | Claude Code skills for component onboarding, Jira creation/validation |
| **RHOAI-Konflux-Automation** | rhoai-rhtap | Python utilities for GHA workflows | bundle-processor.py, fbc-processor.py, stage_promoter.py, catalog_validator.py |

### Additional Referenced Repos

| Repository | Org | Purpose |
|-----------|-----|---------|
| **rhoai-ops** | red-hat-data-services | Operational configs (`config/release.yaml` for supported versions) |
| **ODH-Build-Config** | opendatahub-io | ODH equivalent of RHOAI-Build-Config |
| **rhoai-component-infra** | red-hat-data-services | Component infrastructure configs |
| **odh-release-manager** | opendatahub-io | ODH release management |

### Component Repos (~100+)

Component source code lives across both orgs. Examples:

| Component | Upstream (ODH) | Downstream (RHOAI) |
|----------|---------------|-------------------|
| Dashboard | opendatahub-io/odh-dashboard | red-hat-data-services/odh-dashboard |
| KServe | opendatahub-io/kserve | red-hat-data-services/kserve |
| Notebooks | opendatahub-io/notebooks | red-hat-data-services/notebooks |
| vLLM | opendatahub-io/vllm | red-hat-data-services/vllm |
| Model Registry | opendatahub-io/model-registry | red-hat-data-services/model-registry |

---

## 3. Upstream/Downstream Flow

### The ODH → RHOAI Pipeline

Code flows from upstream Open Data Hub to downstream RHOAI through an automated multi-stage sync process:

```
  opendatahub-io/<component>       red-hat-data-services/<component>
  ┌──────────┐                    ┌──────────┐    ┌──────────┐    ┌──────────┐
  │  main    │──upstream-merge───▶│  main    │───▶│  stable  │───▶│  rhoai   │
  │          │    every 4h        │          │ 2h │          │ 2h │          │
  └──────────┘                    └──────────┘    └──────────┘    └──────────┘
                                                                       │
                                                         release cut   │
                                                                       ▼
                                                                  ┌──────────┐
                                                                  │ rhoai-X.Y│
                                                                  │ (frozen) │
                                                                  └──────────┘
```

### Sync Mechanisms

1. **Upstream Auto-Merge** (`rhods-devops-infra`): `upstream-auto-merge.yaml` runs every 4 hours, syncs 80+ repos from `opendatahub-io` → `red-hat-data-services` using `src/config/upstream-source-map.yaml`.

2. **Main-to-Release Auto-Merge** (`rhods-devops-infra`): `main-release-auto-merge.yaml` runs every 4 hours, merges `main` → release branches for 60+ repos using `src/config/main-release-source-map.yaml`.

3. **Branch Sync in rhods-operator**:
   - `sync-main-to-stable.yaml`: every 2 hours, `main` → `stable`
   - `sync-stable-to-rhoai.yaml`: every 2 hours, `stable` → `rhoai`

### Why This Flow Exists

- **main**: Development branch, receives upstream sync
- **stable**: Stabilization buffer; allows holding back unstable changes
- **rhoai**: RHOAI-specific customizations on top of stable upstream code
- **rhoai-X.Y**: Release branch, frozen at release time; only receives cherry-picks and nudge PRs

This multi-stage flow ensures:
- Upstream changes propagate automatically (no manual cherry-picks for routine updates)
- Each stage can be individually gated if issues arise
- Release branches are isolated from ongoing development

---

## 4. Konflux Platform Concepts

### Core CRDs (Custom Resource Definitions)

| CRD | Purpose | Where Configured |
|-----|---------|-----------------|
| **Application** | Groups related components (e.g., `rhoai-v3-5`) | `konflux-release-data` (Kustomize) |
| **Component** | Single buildable unit (repo + branch + Dockerfile) | `konflux-release-data` (Kustomize) |
| **Snapshot** | Immutable set of component image digests for testing/releasing | Auto-created by Konflux after successful builds |
| **IntegrationTestScenario (ITS)** | Defines tests to run on snapshots | Cluster CRDs (not in git repos) |
| **EnterpriseContractPolicy (ECP)** | Policy rules for compliance validation | `konflux-release-data` (GitOps) |
| **ReleasePlan** | Tenant-side: declares intent to release an application | `konflux-release-data` (tenant configs) |
| **ReleasePlanAdmission (RPA)** | Releng-side: defines HOW releases are processed | `konflux-release-data` (product configs) |
| **ImageRepository** | Quay.io image repo for each component | `konflux-release-data` (Kustomize) |
| **ProjectDevelopmentStream** | Version stream definitions (e.g., `rhoai-stream-v3-5`) | `konflux-release-data` (Kustomize) |

### Pipelines-as-Code (PaC)

Konflux uses PaC to trigger Tekton pipeline runs from git events. `.tekton/` directories in component repos contain PipelineRun YAMLs with:
- **CEL expressions** for trigger filtering (e.g., `on-cel-expression: event == "push" && target_branch == "rhoai-3.5"`)
- **Git resolver** references to pipeline definitions in `konflux-central` or `rhoai-konflux-tasks`
- **Parameters** for build type, architecture, image output locations

### Trusted Artifacts (OCI-TA)

Modern tasks use Trusted Artifacts — instead of passing data through PVC volumes, they push/pull artifacts to OCI registries. This provides better isolation, auditability, and scales across multi-arch builds.

---

## 5. Build Pipelines

### Pipeline Types

| Pipeline | Source Repo | Purpose | Architectures |
|----------|-----------|---------|--------------|
| `container-build` | rhoai-konflux-tasks/pipelines | Single-arch container image | 1 arch |
| `container-build-remote` | rhoai-konflux-tasks/pipelines | Multi-arch container image (remote VMs) | amd64, arm64, ppc64le, s390x |
| `multi-arch-container-build` | konflux-central/pipelines | Multi-arch build | amd64, arm64, ppc64le, s390x |
| `fbc-fragment-build` | konflux-central/pipelines | FBC fragment image | amd64, arm64, ppc64le, s390x |

### RHOAI Pipeline Extensions

RHOAI builds extend standard Konflux pipelines with custom tasks from `rhoai-konflux-tasks`:

1. **`rhoai-init`** (v0.3): Every RHOAI build starts here
   - Computes CPE ID from RHOAI version (for Red Hat product metadata)
   - Generates Slack failure notification templates
   - Determines mandatory image tags (`odh-pr-{PR}` for PRs, version tags for pushes)
   - Detects cluster mismatch for notification routing

2. **`trigger-operator-build`**: After a component builds successfully, triggers operator rebuild via `/test` comment on the operator's latest commit

3. **`trigger-bundle-build`**: Triggers bundle-processor GHA workflow in `ODH-Build-Config` via GitHub API dispatch

4. **`trigger-group-testing`**: Monitors all PR check runs; when all complete, posts `/group-test` to trigger integration tests

5. **`pull-request-comment`**: Posts build status comments on PRs with log links, ORAS artifact instructions, and test results

6. **`rhoai-inject-sealights-oci-ta`**: Injects Sealights instrumentation for test coverage tracking

7. **`prefetch-operand-manifests-oci-ta`**: Prefetches operator operand manifests for hermetic builds

### Build Event Types

| Event | Trigger | What Happens |
|-------|---------|-------------|
| **pull_request** | PR opened/updated on any branch | PR pipeline runs, builds image, runs build-time tests |
| **push** | Merge to release branch | Push pipeline runs, builds production image, creates nudge PRs |
| **incoming** | Manual trigger via annotation | Same as push; used for rebuilds (`trigger-pac-build`) |

### How PipelineRuns Reference Pipelines

```yaml
# In .tekton/<component>-push.yaml
pipelineRef:
  resolver: git
  params:
  - name: url
    value: https://github.com/red-hat-data-services/konflux-central.git
  - name: revision
    value: "$(params.fbc-pipeline-branch)"  # allows version pinning
  - name: pathInRepo
    value: pipelines/multi-arch-container-build.yaml
```

---

## 6. Operator Bundle and FBC Fragment Lifecycle

### What Are Bundles and FBC Fragments?

- **Operator Bundle**: A container image containing the ClusterServiceVersion (CSV), CRDs, and metadata for deploying a specific version of the RHOAI operator via OLM.
- **FBC Fragment**: A File-Based Catalog container image that tells OLM how to discover and upgrade between operator versions. Built per-OCP-version (4.19, 4.20, 4.21, 4.22).

### The Build Chain

```
Component Image Build (e.g., kserve)
    │
    │ Nudge PR (Konflux updates image digest)
    ▼
rhods-operator (receives nudge PR → auto-merged by operator-insta-merge)
    │
    │ operator-processor.yaml processes nudge commits
    │ Updates RELATED_IMAGE entries in build/operator-nudging.yaml
    ▼
Operator Image Build (push to rhoai-3.5 branch)
    │
    │ Nudge PR to RHOAI-Build-Config
    ▼
RHOAI-Build-Config (release branch)
    │
    │ bundle-insta-merge auto-merges nudge PR
    │ process-operator-bundle GHA patches CSV
    ▼
Bundle Image Build (Tekton PipelineRun)
    │
    │ Nudge PR updates catalog-patch.yaml
    ▼
FBC Fragment Build (per OCP version)
    │
    │ process-fbc-fragment GHA generates catalog.yaml
    │ using opm alpha render-template semver
    ▼
FBC Fragment Images (one per OCP version: 4.19, 4.20, 4.21, 4.22)
```

### Key Files in RHOAI-Build-Config

| File | Branch | Purpose |
|------|--------|---------|
| `config/config.yaml` | main | Global: supported OCP versions, `discontinued-from` rules, `skip-bundles` |
| `config/build-config.yaml` | release | Per-release: component image mappings, supported OCP base images |
| `bundle/bundle-patch.yaml` | release | CSV patches (target of Konflux nudge PRs) |
| `catalog/catalog-patch.yaml` | release | FBC catalog patches (target of FBC nudge PRs) |
| `catalog/catalog_build_args.map` | release | Git commit provenance for all 100+ components |
| `pcc/shipped_rhoai_versions.txt` | main | Registry-synced list of shipped versions |
| `pcc/catalog-v4.{xx}.yaml` | main | Pre-Computed Catalog cache per OCP version |

### PCC (Pre-Computed Catalog) Cache

The PCC cache stores the current state of all shipped RHOAI versions from `registry.redhat.io`. It serves as the base for stage promotion: when a new release is pushed to stage, the release branch's FBC fragment is merged INTO the PCC to create the complete catalog index.

**Why PCC goes stale**: When new RHOAI versions ship to production, the PCC must be regenerated to include them. The `regen-pcc-cache.yaml` GHA does this using `opm migrate` against the production registry. If PCC is stale, stage promotion may fail or produce incorrect catalogs.

### Stage Promotion Flow

1. `push-to-stage.yaml` (manual dispatch) takes a release branch + FBC image URI
2. Validates FBC image signature via `skopeo inspect`
3. Checks PCC freshness against `registry.redhat.io` shipped versions
4. If stale, regenerates PCC using `opm migrate`
5. Merges release branch catalogs into PCC using `stage_promoter.py`
6. Commits merged catalogs to main branch under `catalog/{release}/`
7. Main branch Tekton pipelines fire (CEL `pathChanged()` expressions)
8. Sends Slack notification

---

## 7. Dependency Management (MintMaker)

MintMaker is Konflux's hosted dependency update service, built on Renovate. (Note: sometimes called "MindMaker" colloquially — the correct name is **MintMaker**.)

### How It Works

- Runs Renovate every 4 hours starting 00:00 UTC
- Automatically processes all Konflux-onboarded components
- Creates PRs to update dependencies (base image digests, Tekton tasks, Go modules, RPM lockfiles, Python packages)
- On GitHub, PRs come from `red-hat-konflux` app, GPG-signed with key `B5690EEEBB952194`

### Supported Managers (Key Ones for RHOAI)

| Manager | What It Updates |
|---------|----------------|
| `dockerfile` | Base image digests in Dockerfiles/Containerfiles |
| `gomod` | Go module dependencies |
| `tekton` | Tekton task bundle references (Saturdays only) |
| `rpm-lockfile` | RPM lockfile refresh |
| `pip_requirements` | Python requirements.txt |
| `custom.regex` | Custom patterns (git SHAs, etc.) |

### RHOAI-Specific Renovate Configuration

Centrally managed in `konflux-central/renovate/`:

| Config File | Purpose | Applied To |
|------------|---------|-----------|
| `default-renovate.json5` | Standard RHOAI config: digest-only updates, RPM tracking, auto-merge for Dockerfiles | ~50 repos |
| `pipelines-renovate.json5` | Tekton task bundle + custom git SHA updates | konflux-central itself |
| `rpm-refresh-renovate.json5` | RPM lockfile refresh preset | 2 repos (model-registry, fms-guardrails) |

### Sync Mechanism

The `sync-renovate-configs.yml` workflow pushes compiled `.json` configs to target repos at `.github/renovate.json`. The mapping is defined in `config.yaml` in konflux-central.

### Key Config Details

```json5
// default-renovate.json5 (simplified)
{
  "baseBranches": ["main", "rhoai-2.25", "rhoai-3.3", "rhoai-3.4", "rhoai-3.5-ea.2"],
  "dockerfile": {
    "fileMatch": [".*Dockerfile\\.konflux.*"],
    "packageRules": [{
      "matchUpdateTypes": ["digest"],
      "automerge": true  // Auto-merge digest-only updates
    }]
  },
  "rpm-lockfile": {
    "packageRules": [{
      "groupName": "RPM updates"
    }]
  }
}
```

### Disabling MintMaker

To disable for a specific component:
```bash
oc -n <namespace> annotate component/<name> mintmaker.appstudio.redhat.com/disabled=true
```

---

## 8. Component Nudges

Nudges are automated PRs that update image digest references when a dependency image rebuilds.

### How Nudges Work

1. Component A has `spec.build-nudges-ref: [ComponentB]` in its Component CR
2. When Component A's push build completes, Konflux creates a PR in Component B's repo
3. The PR updates all `sha256:` digest references to Component A's new image
4. Nudge PRs are created by `red-hat-konflux` app via Renovate

### File Patterns

By default, Konflux looks for digests in:
- Dockerfiles, Containerfiles
- YAML files

Customizable via annotation:
```yaml
metadata:
  annotations:
    build.appstudio.openshift.io/build-nudge-files: ".*Dockerfile.*, .*.yaml, .*Containerfile.*"
```

### Auto-Merge of Nudge PRs

Several repos auto-merge Konflux nudge PRs:

| Repo | Workflow | What It Merges |
|------|---------|---------------|
| rhods-operator | `operator-insta-merge.yaml` | Nudge PRs from `konflux-internal-p02[bot]` |
| RHOAI-Build-Config | `bundle-insta-merge.yaml` | Nudge PRs touching `bundle/bundle-patch.yaml` |
| RHOAI-Build-Config | `fbc-insta-merge.yaml` | Nudge PRs touching `catalog/catalog-patch.yaml` |

### Customizing Nudge PRs

Nudge behavior can be customized via a ConfigMap:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: namespace-wide-nudging-renovate-config
data:
  automerge: "true"
  ignoreTests: "true"
  platformAutomerge: "true"
```

---

## 9. Release Process

### Release Types

| Type | Example | Description |
|------|---------|------------|
| GA (General Availability) | rhoai-3.5 | Full production release |
| EA (Early Access) | rhoai-3.5-ea.1, rhoai-3.5-ea.2 | Preview releases |
| Z-stream | rhoai-3.5.1 | Patch releases (hotfixes) |

### Release Pipeline Flow

```
Developer/Scheduler triggers release
    │
    ▼
ReleasePlan CR (tenant namespace: rhoai-tenant)
    │ spec.application: rhoai-v3-5
    │ spec.target: rhtap-releng-tenant
    │ auto-release: "false" (manual trigger)
    ▼
ReleasePlanAdmission CR (releng namespace: rhtap-releng-tenant)
    │ spec.policy: registry-rhoai-stage (or -prod)
    │ spec.data.mapping.components: [{name, repository}]
    ▼
Conforma (Enterprise Contract) Validation
    │ Checks image against EC policy
    ▼
Release Pipeline (from release-service-catalog.git)
    │ Pushes images to registry.stage.redhat.io/rhoai/
    │ or registry.redhat.io/rhoai/
    ▼
Images available in target registry
```

### RHOAI Release Plan Admissions (v3.5)

There are 4 types of RPAs for each environment (stage + prod = 8 total):

| RPA Type | What It Releases | Target Registry |
|----------|-----------------|----------------|
| **components** | 90+ container images | `registry.{stage.}redhat.io/rhoai/` |
| **fbc** | FBC fragment index (per OCP 4.19-4.22) | `registry.{stage.}redhat.io/rhoai/` |
| **charts** | Helm charts (rhai-on-openshift, rhai-on-xks) | `registry.{stage.}redhat.io/rhai/` |
| **ea** variants | Same as above but for EA milestones | Same registries |

### Stage Promotion (Master Workflow)

`stage-promoter.yaml` in `rhods-devops-infra` is the ~1000+ line master release workflow that orchestrates the entire stage promotion:

1. Validates all component images pass Conforma
2. Generates the release Snapshot
3. Triggers the release pipeline via ReleasePlan
4. Monitors pipeline completion
5. Validates post-release image availability
6. Sends Slack notifications

### Release Schedule

Release milestones are tracked in Product Pages (`rhai-release-data.yaml` in rhods-devops-infra):
- Planning freeze
- Feature freeze
- Code freeze
- Initial RC
- Release window
- GA date

---

## 10. Nightly Builds

### Purpose

Nightly builds ensure the FBC fragment is always up-to-date with the latest component images. They catch integration issues early by building the complete operator + bundle + FBC chain daily.

### Trigger Chain

```
Grafana scheduler / cron
    │
    │ Touches schedule/bundle-github-trigger.txt
    ▼
trigger-nightly-bundle-build.yaml (RHOAI-Build-Config)
    │ Patches bundle CSV with latest images
    │ Disables Tekton push pipeline
    │ Writes schedule/bundle-tekton-trigger.txt
    ▼
Tekton PipelineRun (builds bundle image)
    │
    │ On success, touches schedule/catalog-github-trigger.txt
    ▼
trigger-nightly-fbc-build.yaml (RHOAI-Build-Config)
    │ Regenerates FBC catalogs per OCP version
    │ Writes schedule/catalog-tekton-trigger.txt
    ▼
Tekton PipelineRuns (one per OCP version)
    │ Builds FBC fragment images
    ▼
FBC fragment images tagged as nightly
```

### External Trigger

The nightly trigger originates from `rhods-devops-infra`:
- `trigger-nightlies.yaml`: daily at 00:00 UTC
- Uses `rhoai-sync-agent.yaml`: every 2 hours syncs shared component images

### Monitoring

- Grafana dashboard tracks nightly build status
- IC tool surfaces nightly failures via `ic get nightly-status` and `ic get alerts`

---

## 11. Conforma (Enterprise Contract) Compliance

### What Conforma Checks

Conforma (formerly Enterprise Contract) validates container images against policy rules before release. Key rule categories:

| Category | What It Checks |
|----------|---------------|
| `slsa_source_correlated` | SLSA provenance matches source commit |
| `source_image.exists` | Source image uploaded alongside container |
| `cve.cve_results_found` | CVE scan results available |
| `sbom_spdx.found` | SBOM (Software Bill of Materials) present |
| `fips_check` | FIPS compliance (for FBC fragments) |
| `rpm_signature` | RPM packages are properly signed |
| `labels` | Required container labels present |
| `deprecated_image_check` | Base images not deprecated |

### Policy Structure (from konflux-release-data)

| Policy | Purpose | Strictness |
|--------|---------|-----------|
| `registry-rhoai-stage` | Stage releases | Lenient (more exclusions) |
| `registry-rhoai-prod` | Production releases | Strict (minimal exclusions) |
| `fbc-rhoai-stage` | FBC stage | Lenient |
| `fbc-rhoai-prod` | FBC production | Strict |
| `registry-rhoai-chart-stage` | Helm chart stage | Lenient |
| `registry-rhoai-chart-prod` | Helm chart production | Strict |

### Exception Types

1. **Permanent exclusions** (`config.exclude`): Rules excluded indefinitely
   - `hermetic_task`, `required_tasks`, `skipped_tests`
   - RPM signature keys for CUDA, ROCm (vendor packages)

2. **Volatile exclusions** (`volatileConfig.exclude`): Temporary exclusions with expiry dates and Jira references
   - `sbom_spdx.disallowed_package_attributes` until 2026-09-30
   - `source_image.exists` for specific components until 2026-08-01

### Conforma Reporter

Daily automated Conforma checks run via `conforma-reporter`:
- **Nightly runs**: 01:00 UTC, validates against last nightly FBC build
- **Latest runs**: 02:00 UTC, validates against latest CI build
- Reports saved to version-specific branches with violations/warnings in CSV, Markdown, YAML
- Results consumed by IC tool for monitoring

### Constraints

The `constraints/` directory in `konflux-release-data` restricts what RPAs can configure:
- RHOAI can only push to `registry.redhat.io/rhoai` or `registry.stage.redhat.io/rhoai` (or `rhai`)
- Must use official `release-service-catalog` pipelines
- Limited to approved service accounts

---

## 12. Cluster Configuration (CRDs)

### Where CRDs Live

CRDs are managed through GitOps in `konflux-release-data` on GitLab. When MRs merge, a GitLab CI pipeline runs `oc apply` to push config directly into clusters.

### Cluster: stone-prod-p02.hjvn.p1

This is the primary RHOAI Konflux cluster.

**Tenants:**
- `rhoai-tenant`: Main production tenant
- `rhoai-private-tenant`: Private/internal use

**Tenant config path:**
```
tenants-config/auto-generated/cluster/stone-prod-p02/tenants/rhoai-tenant/
  ├── Applications/           # rhoai-v3-5, rhoai-fbc-v4-13, etc.
  ├── Components/             # Pull-request pipeline components
  ├── ReleasePlans/           # ~100+ plans (v2.13 through v3.5)
  ├── ImageRepositories/      # Quay.io image repos
  └── ProjectDevelopmentStreams/  # Version streams
```

**Product config path:**
```
config/stone-prod-p02.hjvn.p1/product/
  ├── ReleasePlanAdmission/rhoai/  # 8 RPAs (components/fbc/charts × stage/prod)
  └── EnterpriseContractPolicy/    # 6 EC policies
```

### Active Applications (from IC)

| Application | Components | Description |
|------------|-----------|------------|
| rhoai-v3-4 | 43 | RHOAI 3.4 GA |
| rhoai-v3-4-ea-1 | 2 | RHOAI 3.4 EA1 |
| rhoai-v3-5 | 28 | RHOAI 3.5 GA |
| rhoai-v3-5-ea-1 | 79 | RHOAI 3.5 EA1 |
| rhoai-v3-5-ea-2 | 76 | RHOAI 3.5 EA2 |

---

## 13. Detailed GitHub Actions & Automation Reference

This section documents every significant GitHub Actions workflow, what it does, where it lives, and what to check if it fails.

---

### 13.1 konflux-central — Pipeline Management Hub

**Repo**: `https://github.com/red-hat-data-services/konflux-central`
**Path**: `.github/workflows/`

#### `sync-pipelineruns.yml`
- **Location**: `.github/workflows/sync-pipelineruns.yml`
- **Trigger**: Push to `main` branch, manual dispatch
- **What it does**: The CORE sync mechanism. Takes PipelineRun YAMLs from `pipelineruns/<component>/.tekton/` and pushes them to the corresponding component repo's `.tekton/` directory. This is how pipeline changes propagate to 65+ component repos.
- **Config**: Target repos are derived from the directory structure in `pipelineruns/`
- **If it fails**: Component repos keep their old PipelineRun configs. No immediate build failures, but new pipeline improvements won't reach component repos. Check GHA logs for auth errors (needs `KONFLUX_SYNC_TOKEN` secret).
- **Runners**: Self-hosted (`konflux` label)

#### `pipelinerun-replicator.yml`
- **Location**: `.github/workflows/pipelinerun-replicator.yml`
- **Trigger**: Manual dispatch (inputs: source branch, target branch, component list)
- **What it does**: When creating a new release branch (e.g., `rhoai-3.6`), this replicates PipelineRun YAMLs from an existing branch, updates branch references, and creates PRs in component repos with the new `.tekton/` configs for the release branch.
- **If it fails**: New release branch won't have push pipelines. Components on that branch won't build until manually fixed.

#### `validate-pipelineruns.yml`
- **Location**: `.github/workflows/validate-pipelineruns.yml`
- **Trigger**: Pull requests modifying `pipelineruns/` directory
- **What it does**: PR gate that validates PipelineRun YAML syntax, checks for required fields, and ensures consistency across components.
- **If it fails**: PR can't merge. Fix the YAML syntax errors shown in the GHA log.

#### `validate-release-branches.yml`
- **Location**: `.github/workflows/validate-release-branches.yml`
- **Trigger**: Pull requests
- **What it does**: Validates that release branch configurations are consistent (correct branch references, pipeline versions, etc.).

#### `apply-z-stream-changes.yml`
- **Location**: `.github/workflows/apply-z-stream-changes.yml`
- **Trigger**: Manual dispatch
- **What it does**: Applies z-stream (patch release) changes to PipelineRun YAMLs. Used for hotfix releases like `rhoai-3.5.1`.

#### `sync-renovate-configs.yml`
- **Location**: `.github/workflows/sync-renovate-configs.yml`
- **Trigger**: Push to `renovate/` directory, manual dispatch
- **What it does**: Compiles Renovate config files from `renovate/default-renovate.json5` (and other source configs) into `.json` distribution files and pushes them to ~50 target repos as `.github/renovate.json`.
- **Config**: Target repos defined in `config.yaml` root file.
- **If it fails**: Target repos keep old Renovate configs. MintMaker still runs with stale configs, but new base branches or dependency rules won't take effect.

#### `run-renovate.yml` / `renovate-dry-run.yml`
- **Location**: `.github/workflows/`
- **Trigger**: Manual dispatch
- **What they do**: On-demand Renovate runs using the MintMaker container image (`quay.io/konflux-ci/mintmaker-renovate-image:latest`). The dry-run variant previews what PRs would be created without actually creating them.
- **Config**: Uses `script/run-renovate.sh` which layers MintMaker global config + source config + force overrides.
- **When to use**: When you need dependency updates outside MintMaker's 4-hour schedule, or to debug Renovate behavior.

---

### 13.2 rhods-operator — RHOAI Operator

**Repo**: `https://github.com/red-hat-data-services/rhods-operator`
**Path**: `.github/workflows/`

#### `sync-main-to-stable.yaml`
- **Location**: `.github/workflows/sync-main-to-stable.yaml`
- **Trigger**: Cron every 2 hours
- **What it does**: Merges `main` → `stable` branch. Part of the 3-stage sync pipeline (`main` → `stable` → `rhoai`).
- **If it fails**: Merge conflict. Check the PR that conflicted. Usually requires manual resolution. Downstream `stable → rhoai` sync will also stall.

#### `sync-stable-to-rhoai.yaml`
- **Location**: `.github/workflows/sync-stable-to-rhoai.yaml`
- **Trigger**: Cron every 2 hours
- **What it does**: Merges `stable` → `rhoai` branch. Final stage before release branch content stabilizes.
- **If it fails**: Same as above — merge conflict. The `rhoai` branch won't receive the latest stable changes.

#### `operator-insta-merge.yaml`
- **Location**: `.github/workflows/operator-insta-merge.yaml`
- **Trigger**: PR opened by `konflux-internal-p02[bot]`
- **What it does**: Auto-merges Konflux nudge PRs that update image digests in the operator. These PRs update `build/operator-nudging.yaml` with new SHA digests for downstream component images.
- **If it fails**: Nudge PRs accumulate without merging. The operator image becomes stale (missing latest component images). Check if the bot PR has merge conflicts.

#### `operator-processor.yaml`
- **Location**: `.github/workflows/operator-processor.yaml`
- **Trigger**: Push to release branches when nudge-related files change
- **What it does**: Processes nudge commits by updating operator manifests and bundles. Reads `build/operator-nudging.yaml` (which maps RELATED_IMAGE entries to their current SHA digests) and updates deployment manifests.
- **Key file**: `build/operator-nudging.yaml` — this is WHERE all component image digests are pinned in the operator. When a component image rebuilds, its digest is updated here via nudge PR.
- **If it fails**: Operator builds will use stale image references. Check the processor logs for parsing errors.

#### Testing Workflows
- `unit-tests.yaml`, `integration-tests.yaml`, `e2e-tests.yaml` — Standard CI testing
- **Location**: `.github/workflows/`
- **Runners**: Mix of GitHub-hosted and self-hosted

---

### 13.3 rhods-devops-infra — Release Automation Center

**Repo**: `https://github.com/red-hat-data-services/rhods-devops-infra`
**Path**: `.github/workflows/`

#### `stage-promoter.yaml` (THE Master Release Workflow)
- **Location**: `.github/workflows/stage-promoter.yaml`
- **Trigger**: Manual dispatch
- **What it does**: Orchestrates the ENTIRE stage promotion process (~1000+ lines). This is the most critical workflow in the RHOAI CI/CD system.
  1. Validates all component images pass Conforma
  2. Generates the release Snapshot
  3. Triggers the Konflux release pipeline via ReleasePlan
  4. Monitors pipeline completion
  5. Validates post-release image availability in `registry.stage.redhat.io`
  6. Sends Slack notifications
- **Config**: Uses `src/config/releases.yaml` (active releases), `src/config/rhai-release-data.yaml` (milestones)
- **If it fails**: Release is blocked. Check which step failed in the workflow log. Common causes: Conforma violations, PCC staleness, pipeline timeout.
- **Runners**: Self-hosted (`konflux` label)

#### `trigger-nightlies.yaml`
- **Location**: `.github/workflows/trigger-nightlies.yaml`
- **Trigger**: Cron daily at 00:00 UTC
- **What it does**: Triggers nightly builds for all active releases. Touches `schedule/bundle-github-trigger.txt` in `RHOAI-Build-Config` to kick off the nightly build chain.
- **Config**: Active releases from `src/config/releases.yaml`
- **If it fails**: Nightly builds won't start. FBC fragments become stale. Check if the cron trigger fired (sometimes GitHub disables crons on inactive repos).

#### `main-release-auto-merge.yaml`
- **Location**: `.github/workflows/main-release-auto-merge.yaml`
- **Trigger**: Cron every 4 hours
- **What it does**: Merges `main` → release branches for 60+ repos. Ensures release branches stay up to date with development.
- **Config**: `src/config/main-release-source-map.yaml` — maps each repo to its source (main) and target (release) branches.
- **If it fails**: Release branches fall behind `main`. Check for merge conflicts. Individual repo failures are logged but don't block other repos.

#### `upstream-auto-merge.yaml`
- **Location**: `.github/workflows/upstream-auto-merge.yaml`
- **Trigger**: Cron every 4 hours
- **What it does**: Syncs 80+ repos from upstream `opendatahub-io` → downstream `red-hat-data-services`. The primary mechanism for getting upstream changes into RHOAI.
- **Config**: `src/config/upstream-source-map.yaml` — maps each upstream repo/branch to its downstream counterpart.
  ```yaml
  # Example entry in upstream-source-map.yaml
  - upstream: opendatahub-io/kserve:main
    downstream: red-hat-data-services/kserve:main
  ```
- **If it fails**: Downstream repos diverge from upstream. Upstream fixes don't flow downstream. Check for merge conflicts or authentication errors.

#### `rhoai-sync-agent.yaml`
- **Location**: `.github/workflows/rhoai-sync-agent.yaml`
- **Trigger**: Cron every 2 hours
- **What it does**: Syncs shared component images between applications. Ensures all RHOAI applications use consistent image versions.
- **If it fails**: Different RHOAI applications may reference different versions of the same component image.

---

### 13.4 RHOAI-Build-Config — Bundle & FBC Build Configuration

**Repo**: `https://github.com/red-hat-data-services/RHOAI-Build-Config`
**Path**: `.github/workflows/`

#### `process-operator-bundle.yaml`
- **Location**: `.github/workflows/process-operator-bundle.yaml`
- **Trigger**: Push to `bundle/` or `to-be-processed/bundle/` on release branches
- **What it does**: Patches the operator bundle CSV (`bundle/manifests/rhods-operator.clusterserviceversion.yaml`) with the latest component image references from `config/build-config.yaml`. Enables the Tekton push pipeline to build the bundle image.
- **Key tool**: Uses `bundle-processor.py` from `rhoai-rhtap/RHOAI-Konflux-Automation`
- **If it fails**: Bundle image won't be rebuilt. The operator will ship with stale component image references. Check `build-config.yaml` for missing or incorrect image entries.

#### `process-fbc-fragment.yaml`
- **Location**: `.github/workflows/process-fbc-fragment.yaml`
- **Trigger**: Push to `catalog/catalog-patch.yaml` on release branches
- **What it does**: Regenerates FBC catalogs for every supported OCP version using `opm alpha render-template semver`. Creates per-OCP `catalog.yaml` files in `catalog/{version}/v4.{xx}/`.
- **Key tool**: Uses `fbc-processor.py` from `rhoai-rhtap/RHOAI-Konflux-Automation` and `opm` (Operator Package Manager)
- **If it fails**: FBC fragment images won't be rebuilt. OLM won't discover the new operator version. Check `opm` errors in the log — usually caused by malformed bundle references or schema validation failures.

#### `trigger-nightly-bundle-build.yaml`
- **Location**: `.github/workflows/trigger-nightly-bundle-build.yaml`
- **Trigger**: Push to `schedule/bundle-github-trigger.txt` on release branches
- **What it does**: Nightly version of `process-operator-bundle.yaml`. Patches the bundle CSV AND writes `schedule/bundle-tekton-trigger.txt` to trigger the Tekton PipelineRun. Importantly, it DISABLES the Tekton push pipeline (so the nightly uses a specific trigger path, not the normal push path).
- **If it fails**: Nightly bundle build won't start. Check if `schedule/bundle-github-trigger.txt` was modified correctly.

#### `trigger-nightly-fbc-build.yaml`
- **Location**: `.github/workflows/trigger-nightly-fbc-build.yaml`
- **Trigger**: Push to `schedule/catalog-github-trigger.txt` on release branches
- **What it does**: Nightly version of `process-fbc-fragment.yaml`. Regenerates FBC catalogs and writes `schedule/catalog-tekton-trigger.txt` for each OCP version.
- **If it fails**: Nightly FBC fragment won't build. The nightly operator catalog will be stale.

#### `push-to-stage.yaml` (Stage Promotion)
- **Location**: `.github/workflows/push-to-stage.yaml`
- **Trigger**: Manual dispatch (inputs: release branch, FBC image URI)
- **What it does**: THE stage promotion workflow for FBC catalogs:
  1. Validates FBC image signature via `skopeo inspect`
  2. Checks PCC cache against `registry.redhat.io` shipped versions using `pcc/shipped_rhoai_versions.txt`
  3. If PCC is stale, regenerates using `opm migrate`
  4. Merges release branch catalogs into PCC using `stage_promoter.py`
  5. Commits merged catalogs to `main` branch under `catalog/{release}/`
  6. Main branch Tekton pipelines fire (CEL `pathChanged()` expression detects new catalog files)
  7. Sends Slack notification
- **Key tool**: `stage_promoter.py` from `rhoai-rhtap/RHOAI-Konflux-Automation`
- **If it fails**: Stage promotion is blocked. Common causes: PCC stale (run `regen-pcc-cache.yaml` first), FBC image not signed (rebuild FBC), catalog validation errors.

#### `multi-push-to-stage.yaml`
- **Location**: `.github/workflows/multi-push-to-stage.yaml`
- **Trigger**: Manual dispatch
- **What it does**: Batch version of `push-to-stage.yaml` for promoting multiple release branches simultaneously.

#### `regen-pcc-cache.yaml`
- **Location**: `.github/workflows/regen-pcc-cache.yaml`
- **Trigger**: Manual dispatch
- **What it does**: Regenerates the Pre-Computed Catalog (PCC) cache from `registry.redhat.io` production index using `opm migrate`. Updates `pcc/catalog-v4.{xx}.yaml` and `pcc/shipped_rhoai_versions.txt` on `main` branch.
- **When to run**: After any RHOAI GA release ships to production. If PCC is stale, stage promotion will fail or produce incorrect catalogs.
- **If it fails**: Check `opm migrate` errors (registry auth, network issues). The PCC files on `main` branch will be stale.

#### `bundle-insta-merge.yaml`
- **Location**: `.github/workflows/bundle-insta-merge.yaml`
- **Trigger**: PR opened by `konflux-internal-p02[bot]` touching `bundle/bundle-patch.yaml`
- **What it does**: Auto-merges Konflux nudge PRs that update bundle patches. Part of the automated build chain: when a new operator image builds, Konflux nudges `bundle-patch.yaml`, and this workflow merges it immediately.
- **If it fails**: Bundle nudge PRs accumulate. The bundle won't include the latest operator image. Check for merge conflicts on the PR.

#### `fbc-insta-merge.yaml`
- **Location**: `.github/workflows/fbc-insta-merge.yaml`
- **Trigger**: PR opened by `konflux-internal-p02[bot]` touching `catalog/catalog-patch.yaml`
- **What it does**: Same as `bundle-insta-merge` but for FBC catalog patches.

---

### 13.5 odh-konflux-central — ODH Pipeline Management

**Repo**: `https://github.com/opendatahub-io/odh-konflux-central`
**Path**: `.github/workflows/`

#### `odh-konflux-onboarder.yml`
- **Location**: `.github/workflows/odh-konflux-onboarder.yml`
- **Trigger**: Manual dispatch (inputs: component name, build type CI/Release, version, branch)
- **What it does**: The primary ODH onboarding tool. Takes PipelineRun templates from `pipelineruns/`, substitutes placeholders (`$$OUTPUT_IMAGE_TAG$$`, `$$TARGET_BRANCH$$`), and creates a PR in the component repo with the resulting `.tekton/` files.
- **Build types**: CI builds get `odh-pr-{PR}` / `odh-stable` tags; Release builds get version tags.
- **If it fails**: Component won't have `.tekton/` files. It won't build in Konflux until the files are manually added.

#### `odh-early-gate-onboarder.yml`
- **Location**: `.github/workflows/odh-early-gate-onboarder.yml`
- **Trigger**: Manual dispatch
- **What it does**: Onboards a component to the early-gate system. Pushes early-gate pipeline files to the component repo and updates `config/early-gate-config.yaml`.
- **Early-gate**: A unique ODH feature — developers can comment `/early-gate` on a PR to trigger a full operator+bundle+FBC build from the PR branch. Provides pre-merge integration validation.

#### `generate-component-map.yml`
- **Location**: `.github/workflows/generate-component-map.yml`
- **Trigger**: Daily cron + push to `pipelineruns/`
- **What it does**: Parses all PipelineRun YAMLs to auto-generate `config/component_repo_map.json` — a mapping of every ODH component to its Quay.io image repository.
- **Output**: `config/component_repo_map.json`

#### `build-integration-images.yml`
- **Location**: `.github/workflows/build-integration-images.yml`
- **Trigger**: Push to `integration-tests/` when Dockerfiles change
- **What it does**: Builds integration test Docker images and pushes to `quay.io/rhoai/rhoai-task-toolset`.

---

### 13.6 conforma-reporter — Daily Compliance Reports

**Repo**: `https://github.com/red-hat-data-services/conforma-reporter`
**Path**: `.github/workflows/`

#### `trigger-conforma-nightly-supported-versions.yaml`
- **Location**: `.github/workflows/trigger-conforma-nightly-supported-versions.yaml`
- **Trigger**: Cron daily at 01:00 UTC
- **What it does**: Discovers all supported RHOAI versions from `rhoai-ops/config/release.yaml`, then triggers `conforma-reporter.yaml` for each version with `build_type=nightly`.
- **If it fails**: No nightly Conforma report generated. Violations may go undetected until release time.

#### `trigger-conforma-latest-supported-versions.yaml`
- **Location**: `.github/workflows/trigger-conforma-latest-supported-versions.yaml`
- **Trigger**: Cron daily at 02:00 UTC
- **What it does**: Same as above but with `build_type=latest` (uses latest CI build instead of nightly).

#### `conforma-reporter.yaml` (Main Conforma Workflow)
- **Location**: `.github/workflows/conforma-reporter.yaml`
- **Trigger**: Workflow dispatch (called by the trigger workflows above)
- **What it does**: The full Conforma validation pipeline:
  1. Runs on self-hosted Konflux runners in `quay.io/rhoai/conforma-runner:latest` container
  2. Looks up release dates from `rhai-release-data.yaml` for future-date policy evaluation
  3. Creates backdated EnterpriseContractPolicy CRDs for prod evaluation (`backdate_ec_policy.py`)
  4. Runs `conforma-reporter.sh` from `rhods-devops-infra/tools/snapshot-generator/` to create a custom Snapshot
  5. Filters JSON results into violations/warnings per artifact type (FBC, Components, Charts)
  6. Generates CSV reports, Markdown reports, exception YAML files, and summary YAML
  7. Pushes results to version-specific branch (e.g., `rhoai-3.5`) under `prod/future/build_type_nightly/`
- **Output location**: Branch `rhoai-{version}` in the conforma-reporter repo, directories like `prod/future/build_type_nightly/`
- **If it fails**: Check runner availability (self-hosted `konflux` runners), cluster auth (`KUBECONFIG`), or `skopeo` image inspection failures.

---

### 13.7 rhoai-konflux-tasks — Custom Tekton Tasks

**Repo**: `https://github.com/red-hat-data-services/rhoai-konflux-tasks`
**Path**: `konflux-tekton-tasks/`, `pipelines/`, `stepactions/`

This repo doesn't have GHA workflows — it contains **Tekton Tasks** and **Pipelines** referenced by PipelineRuns in component repos via git resolver.

#### Task: `rhoai-init` (v0.3)
- **Location**: `konflux-tekton-tasks/rhoai-init/0.3/rhoai-init.yaml`
- **What it does**: Every RHOAI build starts here. Computes CPE ID from RHOAI version (e.g., `cpe:/a:redhat:rhoai:3.5`), generates Slack failure notification templates, determines mandatory image tags, and detects cluster mismatch for routing notifications to the correct Slack channel.
- **If it fails**: Build will fail at the first task. Check CPE computation logic or Slack template generation.

#### Task: `trigger-operator-build` (v0.1)
- **Location**: `konflux-tekton-tasks/trigger-operator-build/0.1/trigger-operator-build.yaml`
- **What it does**: After a component builds successfully on a push pipeline, this task triggers an operator rebuild by posting a `/test` comment on the operator's latest commit. Skips if the current component IS the operator/bundle/FBC.
- **Why**: Ensures the operator image always includes the latest component images.
- **If it fails**: Operator won't rebuild after component updates. The operator image becomes stale.

#### Task: `trigger-bundle-build` (v0.1)
- **Location**: `konflux-tekton-tasks/trigger-bundle-build/0.1/trigger-bundle-build.yaml`
- **What it does**: Triggers the bundle-processor GHA workflow in `opendatahub-io/ODH-Build-Config` via GitHub API dispatch. Chains operator → bundle builds.
- **If it fails**: Bundle won't rebuild after operator updates.

#### Task: `trigger-group-testing` (v0.1)
- **Location**: `konflux-tekton-tasks/trigger-group-testing/0.1/trigger-group-testing.yaml`
- **What it does**: Monitors all "Red Hat Konflux" check runs on a PR. When all builds complete (except group-test itself), posts `/group-test` comment to trigger group integration testing.
- **Why**: Ensures integration tests only run when ALL component builds pass.

#### Task: `rhoai-inject-sealights-oci-ta` (v0.1)
- **Location**: `konflux-tekton-tasks/rhoai-inject-sealights-oci-ta/0.1/rhoai-inject-sealights-oci-ta.yaml`
- **What it does**: Injects Sealights test coverage instrumentation into operator bundles and FBC catalogs. Downloads attestation from built images, finds Sealights-instrumented image URIs, and replaces standard images.
- **When active**: Only when Sealights is configured for the build.

#### Task: `prefetch-operand-manifests-oci-ta` (v0.1)
- **Location**: `konflux-tekton-tasks/prefetch-operand-manifests-oci-ta/0.1/prefetch-operand-manifests-oci-ta.yaml`
- **What it does**: Prefetches all deployment manifests for ODH/RHODS operator operands. Clones `RHOAI-Konflux-Automation` and `ODH-Build-Config` repos to process operand manifests. Uses Trusted Artifacts (OCI-TA).
- **Why**: Operator builds need all operand manifests embedded in the image for disconnected deployments. This task is the Tekton equivalent of the `operator-processor` GHA workflow.

#### Task: `pull-request-comment` (v0.2)
- **Location**: `konflux-tekton-tasks/pull-request-comment/0.2/pull-request-comment.yaml`
- **What it does**: Posts build status comments on PRs with pipeline run status, build log links, ORAS artifact download instructions, and optional JUnit/E2E test result analysis.

#### Pipeline: `container-build-remote.yaml`
- **Location**: `pipelines/container-build-remote.yaml`
- **What it does**: Multi-arch container build pipeline with RHOAI extensions. Uses remote VMs for cross-architecture builds. Includes `rhoai-init`, `show-sbom`, and `send-slack-notification` tasks.
- **Referenced by**: All RHOAI component PipelineRuns on release branches.

---

### 13.8 aiops-infra — Onboarding Automation Skills

**Repo**: `https://github.com/opendatahub-io/aiops-infra`
**Path**: `skill/`, `.github/workflows/`

#### GHA: `lint.yml`
- **Location**: `.github/workflows/lint.yml`
- **What it does**: Runs skillsaw linter on skill definitions.

#### Skill: `onboard-konflux-components-for-odh-and-rhoai`
- **Location**: `skill/onboard-konflux-components-for-odh-and-rhoai/`
- **What it does**: Full onboarding automation across ~10 repos. Orchestrated via GitLab CI (every 2h via Vertex AI).
- **Sub-skills**: ~15 documented sub-skills for different onboarding steps.

#### Skill: `create-component-onboarding-jira`
- **Location**: `skill/create-component-onboarding-jira/`
- **What it does**: Creates Jira tickets with onboarding checklists for new components.

#### Skill: `validate-component-onboarding-jira`
- **Location**: `skill/validate-component-onboarding-jira/`
- **What it does**: Validates that onboarding Jira tickets are progressing correctly.

---

### 13.9 Key Config Files — WHERE to Find Things

| What You Need to Change | Exact File Location | Repo |
|------------------------|-------------------|------|
| Add a new component to RHOAI builds | `pipelineruns/<name>/.tekton/<name>-pull-request.yaml` | konflux-central |
| Change which repos receive Renovate config | `config.yaml` (root) | konflux-central |
| Modify default Renovate rules | `renovate/default-renovate.json5` | konflux-central |
| Add repo to upstream auto-merge | `src/config/upstream-source-map.yaml` | rhods-devops-infra |
| Add repo to main→release auto-merge | `src/config/main-release-source-map.yaml` | rhods-devops-infra |
| Define active releases | `src/config/releases.yaml` | rhods-devops-infra |
| Check release milestones | `src/config/rhai-release-data.yaml` | rhods-devops-infra |
| Add component to operator bundle | `bundle/bundle-patch.yaml` (release branch) | RHOAI-Build-Config |
| Change supported OCP versions for FBC | `config/config.yaml` (main) | RHOAI-Build-Config |
| Update component image references | `config/build-config.yaml` (release branch) | RHOAI-Build-Config |
| Add/modify EC policy exclusions | `config/stone-prod-p02.hjvn.p1/product/EnterpriseContractPolicy/` | konflux-release-data (GitLab) |
| Create a new ReleasePlan | `tenants-config/auto-generated/cluster/stone-prod-p02/tenants/rhoai-tenant/` | konflux-release-data (GitLab) |
| Create a new ReleasePlanAdmission | `config/stone-prod-p02.hjvn.p1/product/ReleasePlanAdmission/rhoai/` | konflux-release-data (GitLab) |
| Check which versions conforma-reporter checks | `rhoai-ops/config/release.yaml` | rhoai-ops |
| Pin component image digests in operator | `build/operator-nudging.yaml` | rhods-operator |
| Modify custom RHOAI Tekton tasks | `konflux-tekton-tasks/<task>/<version>/<task>.yaml` | rhoai-konflux-tasks |
| Check ODH component→quay mapping | `config/component_repo_map.json` | odh-konflux-central |
| Onboard new ODH component | Run `odh-konflux-onboarder.yml` workflow | odh-konflux-central |

---

### 13.10 Scheduled Automation Summary

| Schedule | What Runs | Where |
|---------|----------|-------|
| **Every 2h** | `main→stable` sync, `stable→rhoai` sync | rhods-operator |
| **Every 2h** | `rhoai-sync-agent` (shared image sync) | rhods-devops-infra |
| **Every 4h** | Upstream ODH → downstream RHOAI (80+ repos) | rhods-devops-infra |
| **Every 4h** | `main → release branch` merge (60+ repos) | rhods-devops-infra |
| **Every 4h** | MintMaker Renovate runs (all onboarded repos) | Konflux platform |
| **Daily 00:00 UTC** | Nightly build trigger | rhods-devops-infra |
| **Daily 01:00 UTC** | Conforma nightly report | conforma-reporter |
| **Daily 02:00 UTC** | Conforma latest report | conforma-reporter |
| **Saturday 05:00 UTC** | Tekton task updates (MintMaker) | Konflux platform |
| **On nudge PR** | Auto-merge (operator, bundle, FBC) | rhods-operator, RHOAI-Build-Config |
| **On component push build** | Trigger operator rebuild | rhoai-konflux-tasks |

---

## 14. Branch Strategy

### Standard Component Branch Flow

```
main ──▶ stable ──▶ rhoai ──▶ rhoai-X.Y (release branch)
  │         │          │
  2h sync   2h sync    release cut (frozen)
```

### RHOAI-Build-Config Branches

| Branch Pattern | Purpose |
|---------------|---------|
| `main` | Stage/prod catalogs, PCC cache, global config |
| `rhoai-{version}` | Per-release bundle/FBC config (e.g., `rhoai-3.5`) |
| `rhoai-{version}-ea.{N}` | Early Access milestones |
| `main-zstream-*` | Z-stream hotfix branches |
| `hotfix-*` | Ad hoc hotfixes |

### PipelineRun Branch Rules

| Branch Type | PR Pipeline | Push Pipeline | Nudge PRs |
|------------|------------|--------------|----------|
| `main` | Yes | No (development only) | No |
| `rhoai-X.Y` (release) | Yes | Yes | Yes (auto-merged) |

---

## 15. Onboarding a New Component

### ODH (Upstream) Onboarding

1. **Component repo setup**: Create repo in `opendatahub-io/` with Dockerfile
2. **Onboard via GHA**: Run `odh-konflux-onboarder.yml` in `odh-konflux-central` with:
   - Component name, build type (CI/Release), version, branch
   - Creates PR in component repo with templated `.tekton/` files
3. **Create Component CR**: In Konflux cluster (via `konflux-release-data`)
4. **Set up nudge relationships**: Define `build-nudges-ref` in Component CR

### RHOAI (Downstream) Onboarding

1. **Fork/mirror upstream repo** to `red-hat-data-services/`
2. **Add to konflux-central**: Create PipelineRun YAMLs in `pipelineruns/<component>/.tekton/`
3. **Sync PipelineRuns**: `sync-pipelineruns.yml` pushes `.tekton/` to component repo
4. **Register in Konflux**: Create Application, Component, ReleasePlan CRDs via `konflux-release-data`
5. **Add to Renovate**: Add repo to `config.yaml` in `konflux-central`
6. **Add to auto-merge**: Add to `upstream-source-map.yaml` and `main-release-source-map.yaml` in `rhods-devops-infra`
7. **Add to bundle**: Add image reference to `bundle-patch.yaml` in `RHOAI-Build-Config`
8. **Set up nudge chain**: Configure `build-nudges-ref` to nudge bundle/operator

### Automated Onboarding (aiops-infra)

The `aiops-infra` repo provides Claude Code skills for automating parts of this:
- `create-component-onboarding-jira`: Creates Jira tickets for onboarding tasks
- `validate-component-onboarding-jira`: Validates onboarding progress
- `onboard-konflux-components-for-odh-and-rhoai`: Full onboarding automation

---

## 16. Testing Infrastructure

### Build-Time Tests

Run as part of the Tekton pipeline during image builds:
- Dockerfile linting
- SBOM generation validation
- CVE scanning (Snyk optional)
- SLSA provenance attestation

### Integration Tests (ITS)

Defined as IntegrationTestScenario CRDs in the cluster. Run against Snapshots after successful builds.

**Note**: As of 2026-07-09, IC reports 0 ITS scenarios for `rhoai-v3-5`. Integration tests may be configured differently (possibly through external systems like Jenkins or Testing Farm).

### Early-Gate System (ODH Only)

Unique to `odh-konflux-central`: a pre-merge CI system triggered by `/early-gate` PR comments that builds operator + bundle + FBC in a single pipeline run. Provides faster validation on feature branches.

### Group Testing

The `trigger-group-testing` task in `rhoai-konflux-tasks` orchestrates group testing:
1. Monitors all PR check runs
2. When all builds pass, posts `/group-test` comment
3. Triggers group integration testing pipeline

### Smoke Tests (Jenkins)

`rhods-devops-infra` integrates with Jenkins for smoke tests via `trigger-jenkins-smoke-test.yaml`. Self-hosted runners labeled `konflux` and `pnc`.

---

## 17. Automation Skills (aiops-infra)

### Available Skills

| Skill | Purpose | Risk Level |
|-------|---------|-----------|
| `create-component-onboarding-jira` | Creates Jira tickets for onboarding | Medium |
| `validate-component-onboarding-jira` | Validates onboarding progress | Low (read-only) |
| `onboard-konflux-components-for-odh-and-rhoai` | Full onboarding automation (~10 repos) | High |

### Orchestration

Skills run via:
- GitLab CI (every 2h via Vertex AI)
- IC tool skill registry (`src/skills/known_sources.py`)
- Direct Claude Code invocation

### ADLC Vision

The `aiops-infra` repo documents an Agentic SDLC+Productization vision where AI agents handle routine productization tasks (onboarding, triage, fix generation) with human oversight.

---

## 18. Renovate Configuration

### Config Layering (Order of Application)

1. Renovate built-in defaults
2. MintMaker global config (from `konflux-ci/mintmaker/config/renovate/renovate.json`)
3. Inherited config (optional: `<org>/renovate-config/org-inherited-config.json`)
4. Repo-level config (`.github/renovate.json` synced from konflux-central)

Later configs override earlier ones.

### MintMaker Global Defaults

- Presets: `config:recommended`, `:gitSignOff`, `:disableDependencyDashboard`
- Branch prefix: `konflux/mintmaker/{{ baseBranch }}/`
- `pruneStaleBranches: false`
- Tekton updates: Saturdays after 05:00 UTC
- `minimumReleaseAge: 3 days`
- Post-upgrade: `pipeline-migration-tool` for Tekton task migrations

### On-Demand Renovate (konflux-central)

The `run-renovate.sh` script in `konflux-central` runs Renovate locally using the MintMaker container image for testing or one-off updates outside the 4-hour schedule.

---

## 19. Infrastructure Monitoring (IC)

### What IC Monitors

The IC (ci-autohealing) tool provides real-time monitoring of the RHOAI CI/CD infrastructure:

| Command | What It Shows |
|---------|-------------|
| `ic get alerts` | All unresolved build failures + Conforma violations |
| `ic get triage` | Failing vs working components overview |
| `ic get health` | Component health scores and failure rates |
| `ic get conforma` | Conforma violation details |
| `ic get nightly-status` | FBC fragment health + blocking failures |
| `ic release-readiness` | Pre-release readiness checklist |
| `ic get stale-components` | Components with unbuilt commits |
| `ic get snapshot-freshness` | Snapshot images vs latest builds |

### Data Sources

IC gathers data from:
- Konflux K8s API (live cluster CRDs)
- KubeArchive / Tekton Results (build logs)
- GitHub API (PRs, commits)
- GitLab API (konflux-release-data files)
- Product Pages API (release milestones)
- Quay.io API (image manifests)

### Current Config Analysis Findings (as of 2026-07-09)

**13 configuration findings** from IC `get_config_analysis`:
1. rhai-on-openshift/xks-chart missing SBOM, CVE scan, 14 required labels
2. rhoai-fbc-fragment missing fbc-fips-check on all 5 architectures
3. FBC using disallowed `brew.registry.redhat.io` base image
4. Volatile exclusions expiring 2026-08-01 (22 days remaining)
5. Binary Python wheel violations in latency predictor components (111 packages)
6. Zero ITS scenarios and EC policies found for rhoai-v3-5 (cluster access issue)
7. Deprecated pipeline tasks approaching EOL (git-clone-oci-ta 2026-08-20, build-image-index 2026-08-30)
8. 4 auto-rebuild candidates (transient slsa_source_correlated violations)

**5 build config findings**:
1. 5 odh-mod-arch-* components with recurring Build Error (3 each in 7 days)
2. odh-kserve-autogluon-server with 5 Python Error failures
3. odh-vllm-cpu 104min avg build duration
4. All 6 transient-failure components are auto-rebuild candidates

---

## Appendix A: Glossary

| Term | Definition |
|------|-----------|
| **CEL** | Common Expression Language; used in PaC for trigger conditions |
| **CPE** | Common Platform Enumeration; Red Hat product identifier |
| **CSV** | ClusterServiceVersion; OLM operator deployment manifest |
| **EA** | Early Access; preview release milestone |
| **ECP** | EnterpriseContractPolicy; Conforma policy CRD |
| **FBC** | File-Based Catalog; OLM operator discovery mechanism |
| **GA** | General Availability; production release |
| **ITS** | IntegrationTestScenario; Konflux test configuration CRD |
| **MintMaker** | Konflux's Renovate-based dependency update service |
| **Nudge PR** | Automated PR updating image digest references |
| **OCI-TA** | OCI Trusted Artifacts; secure artifact passing between tasks |
| **OLM** | Operator Lifecycle Manager |
| **PaC** | Pipelines-as-Code; Tekton trigger from git events |
| **PCC** | Pre-Computed Catalog; cached registry state for stage promotion |
| **RPA** | ReleasePlanAdmission; defines how releases are processed |
| **SLSA** | Supply-chain Levels for Software Artifacts |
| **Z-stream** | Patch/hotfix release (e.g., 3.5.1) |

## Appendix B: Key Config Files Quick Reference

| What You Need | Where To Find It |
|--------------|-----------------|
| Supported RHOAI versions | `rhods-devops-infra/src/config/releases.yaml` |
| Upstream→downstream repo mapping | `rhods-devops-infra/src/config/upstream-source-map.yaml` |
| Main→release branch mapping | `rhods-devops-infra/src/config/main-release-source-map.yaml` |
| Component PipelineRun YAMLs | `konflux-central/pipelineruns/<component>/.tekton/` |
| Release milestones | `rhods-devops-infra/src/config/rhai-release-data.yaml` |
| EC policies (source of truth) | `konflux-release-data/config/stone-prod-p02.hjvn.p1/product/EnterpriseContractPolicy/` |
| Release plans (tenant-side) | `konflux-release-data/tenants-config/auto-generated/cluster/stone-prod-p02/tenants/rhoai-tenant/` |
| Release plan admissions | `konflux-release-data/config/stone-prod-p02.hjvn.p1/product/ReleasePlanAdmission/rhoai/` |
| Bundle patches | `RHOAI-Build-Config/<release-branch>/bundle/bundle-patch.yaml` |
| FBC catalog patches | `RHOAI-Build-Config/<release-branch>/catalog/catalog-patch.yaml` |
| OCP version matrix | `RHOAI-Build-Config/config/config.yaml` |
| Renovate configs (source) | `konflux-central/renovate/` |
| Renovate target repos | `konflux-central/config.yaml` |
| Conforma reports | `conforma-reporter` version-specific branches |
| Nightly trigger files | `RHOAI-Build-Config/<release-branch>/schedule/` |
| Component-to-quay mapping (ODH) | `odh-konflux-central/config/component_repo_map.json` |
| Custom RHOAI Tekton tasks | `rhoai-konflux-tasks/konflux-tekton-tasks/` |
| RHOAI pipeline definitions | `rhoai-konflux-tasks/pipelines/` |
| Operational release config | `rhoai-ops/config/release.yaml` |
