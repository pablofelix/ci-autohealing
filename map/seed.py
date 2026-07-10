#!/usr/bin/env python3
"""Seed the Neo4j system map graph with RHOAI CI/CD infrastructure topology.

Pulls from live data sources (GitHub, cluster) where possible, with static
topology data as fallback. Run after Neo4j is up:

    python scripts/seed_system_map.py                  # full seed
    python scripts/seed_system_map.py --schema-only    # just create constraints/indexes
    python scripts/seed_system_map.py --verify         # check graph integrity
    python scripts/seed_system_map.py --stats          # node/edge counts

Requires: SLK_NEO4J_URI, SLK_NEO4J_USER, SLK_NEO4J_PASSWORD env vars.
Optional: GITHUB_TOKEN for live repo discovery.
"""

import argparse
import json
import logging
import os
import sys
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_driver():
    from neo4j import GraphDatabase
    uri = os.environ.get("SLK_NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("SLK_NEO4J_USER", "neo4j")
    password = os.environ.get("SLK_NEO4J_PASSWORD", "")
    if not password:
        logger.error("SLK_NEO4J_PASSWORD not set")
        sys.exit(1)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    logger.info("Connected to Neo4j at %s", uri)
    return driver


# ─── Schema ────────────────────────────────────────────────────────────────────

CONSTRAINTS = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Repository) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Workflow) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Pipeline) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Component) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:ECPolicy) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:ReleasePlan) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:ConfigFile) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Automation) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Application) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:TektonTask) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:PolicyRule) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:FailurePattern) REQUIRE n.category IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Concept) REQUIRE n.name IS UNIQUE",
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS FOR (n:Component) ON (n.name)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Repository) ON (n.org, n.repo)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Workflow) ON (n.name)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Application) ON (n.name)",
]


def create_schema(driver):
    """Create constraints and indexes."""
    with driver.session() as s:
        for stmt in CONSTRAINTS + INDEXES:
            s.run(stmt)
    logger.info("Schema created: %d constraints, %d indexes", len(CONSTRAINTS), len(INDEXES))


# ─── Static topology data ──────────────────────────────────────────────────────
# This defines the RHOAI CI/CD infrastructure structure.
# Source of truth: actual repos and cluster CRDs.
# This static data is a fallback — the script tries live GitHub API first.

REPOSITORIES = [
    {
        "id": "konflux-central",
        "org": "red-hat-data-services",
        "repo": "konflux-central",
        "description": "Single source of truth for RHOAI build definitions — without it, each component repo drifts to different pipeline versions. Contains shared build pipelines (container-build, fbc-fragment-build, multi-arch), PipelineRun templates synced to component .tekton/ dirs, and Renovate configs for automated task bundle SHA updates.",
        "category": "config",
        "team": "RHOAI DevOps",
        "url": "https://github.com/red-hat-data-services/konflux-central",
    },
    {
        "id": "rhoai-build-config",
        "org": "red-hat-data-services",
        "repo": "RHOAI-Build-Config",
        "description": "RHOAI release orchestration — coordinates the nightly-to-GA pipeline so releases ship on schedule. Contains OLM catalog patches, component version lists, nightly build triggers (bundle + FBC), stage push workflows, and PCC cache regeneration.",
        "category": "config",
        "team": "RHOAI DevOps",
        "url": "https://github.com/red-hat-data-services/RHOAI-Build-Config",
    },
    {
        "id": "konflux-release-data",
        "org": "releng",
        "repo": "konflux-release-data",
        "platform": "gitlab",
        "description": "Release engineering configuration (internal GitLab) — defines what is allowed to ship and under what conditions. Contains ReleasePlanAdmissions, EnterpriseContractPolicy CRDs (stage/prod), policy exceptions, and release pipeline definitions. Managed by RelEng; changes here gate or unblock every release.",
        "category": "release",
        "team": "Release Engineering",
        "url": "https://gitlab.cee.redhat.com/releng/konflux-release-data",
    },
    {
        "id": "rhods-operator",
        "org": "red-hat-data-services",
        "repo": "rhods-operator",
        "description": "RHOAI operator (downstream fork of opendatahub-operator) — the central control plane that customers install. Manages RHOAI component lifecycle, dashboard, model serving, and data science pipelines on OpenShift. All component images ultimately feed into this operator's bundle and FBC catalog.",
        "category": "operator",
        "team": "RHOAI Platform",
        "url": "https://github.com/red-hat-data-services/rhods-operator",
    },
    {
        "id": "rhods-devops-infra",
        "org": "red-hat-data-services",
        "repo": "rhods-devops-infra",
        "description": "DevOps automation workflows — keeps the RHOAI CI/CD pipeline running without manual intervention. Nightly build triggers, upstream-to-downstream sync agents, release branch onboarding, nudge verification, stage promotion, and Jira/CVE tooling. If these workflows stop, nightly builds and release promotions stall.",
        "category": "automation",
        "team": "RHOAI DevOps",
        "url": "https://github.com/red-hat-data-services/rhods-devops-infra",
    },
    {
        "id": "conforma-reporter",
        "org": "red-hat-data-services",
        "repo": "conforma-reporter",
        "description": "Conforma validation automation — catches policy violations before they block a release. Runs policy checks (latest, nightly, release-day) across all RHOAI components and publishes results to Slack/GitHub. Triggers nightly stage pushes. Without this, violations are only discovered at release gate time.",
        "category": "automation",
        "team": "RHOAI DevOps",
        "url": "https://github.com/red-hat-data-services/conforma-reporter",
    },
    {
        "id": "aiops-infra",
        "org": "opendatahub-io",
        "repo": "aiops-infra",
        "description": "AIOps infrastructure toolkit — reduces manual toil by automating common CI/CD fixes. Contains IC skills for automated fixes (hermetic builds, FIPS compliance, onboarding), AI-powered build failure analysis, and CI/CD helper utilities. Enables self-service troubleshooting without escalating to DevOps.",
        "category": "automation",
        "team": "RHOAI AIOps",
        "url": "https://github.com/opendatahub-io/aiops-infra",
    },
    {
        "id": "opendatahub-operator",
        "org": "opendatahub-io",
        "repo": "opendatahub-operator",
        "description": "Upstream Open Data Hub operator — the community project that RHOAI is built from. Community-driven ML platform operator that RHOAI (rhods-operator) forks and extends for enterprise use. Upstream fixes must land here first before syncing downstream; breaking changes here ripple through RHOAI.",
        "category": "operator",
        "team": "ODH Community",
        "url": "https://github.com/opendatahub-io/opendatahub-operator",
    },
    {
        "id": "build-definitions",
        "org": "konflux-ci",
        "repo": "build-definitions",
        "description": "Konflux shared Tekton task definitions — the building blocks every pipeline depends on. Contains buildah, source-build, prefetch-dependencies, SAST scanners, and all reusable build tasks referenced by pipeline bundles. When a task here is updated, Renovate propagates the new SHA to all consuming pipelines.",
        "category": "platform",
        "team": "Konflux Build",
        "url": "https://github.com/konflux-ci/build-definitions",
        "doc_url": "https://konflux-ci.dev/docs/building/",
    },
]

WORKFLOWS = [
    {
        "id": "gha-sync-pipelineruns",
        "name": "Sync Pipelineruns",
        "repo_id": "konflux-central",
        "description": "Replicates PipelineRun YAML definitions from konflux-central/pipelineruns/ to each component repository's .tekton/ directory. Without this sync, pipeline updates in konflux-central would never reach component repos, causing version drift and inconsistent build behavior.",
        "trigger": "manual / scheduled",
        "url": "https://github.com/red-hat-data-services/konflux-central/actions/workflows/sync-pipelineruns.yml",
    },
    {
        "id": "gha-trigger-nightlies",
        "name": "Trigger Nightly Build",
        "repo_id": "rhods-devops-infra",
        "description": "Triggers nightly operator builds by annotating Component CRs with build.appstudio.openshift.io/request=trigger-pac-build. Without nightly builds, the release pipeline has no fresh images to validate — Conforma checks would run against stale artifacts.",
        "trigger": "cron (nightly)",
        "url": "https://github.com/red-hat-data-services/rhods-devops-infra/actions/workflows/trigger-nightlies.yaml",
    },
    {
        "id": "gha-trigger-nightly-bundle",
        "name": "Trigger Nightly Bundle Build",
        "repo_id": "rhoai-build-config",
        "description": "Triggers nightly operator bundle builds in RHOAI-Build-Config. Bundles package operator metadata (CSV, CRDs, RBAC) for OLM deployment. Without a fresh bundle, operator upgrades on customer clusters would reference stale component images.",
        "trigger": "cron (nightly)",
        "url": "https://github.com/red-hat-data-services/RHOAI-Build-Config/actions/workflows/trigger-nightly-bundle-build.yaml",
    },
    {
        "id": "gha-trigger-nightly-fbc",
        "name": "Trigger Nightly FBC Build",
        "repo_id": "rhoai-build-config",
        "description": "Triggers nightly FBC fragment builds. FBC fragments are the OLM catalog entries that make operators discoverable and upgradeable. Without a current FBC fragment, the operator cannot be installed or upgraded via OLM on OpenShift clusters.",
        "trigger": "cron (nightly)",
        "url": "https://github.com/red-hat-data-services/RHOAI-Build-Config/actions/workflows/trigger-nightly-fbc-build.yaml",
    },
    {
        "id": "gha-conforma-reporter",
        "name": "Conforma Reporter",
        "repo_id": "conforma-reporter",
        "description": "Runs full Conforma (Enterprise Contract) validation across all RHOAI component images. Supports latest, nightly, and release-day build types against stage or prod policies. Provides early warning of policy violations days before a release attempt, when fixes are still cheap.",
        "trigger": "cron (nightly) / manual",
        "url": "https://github.com/red-hat-data-services/conforma-reporter/actions/workflows/conforma-reporter.yaml",
    },
    {
        "id": "gha-run-renovate",
        "name": "Run Renovate",
        "repo_id": "konflux-central",
        "description": "Runs Renovate to detect and update Tekton task bundle SHAs in PipelineRun definitions. Keeps pipelines on latest trusted task versions — without this, builds would use outdated tasks that may lack security fixes or fail Conforma trusted-task checks.",
        "trigger": "scheduled / manual",
        "url": "https://github.com/red-hat-data-services/konflux-central/actions/workflows/run-renovate.yml",
    },
    {
        "id": "gha-push-to-stage",
        "name": "Push To Stage",
        "repo_id": "rhoai-build-config",
        "description": "Triggers a stage release by creating a Release CR in Konflux. Stage is the validation gate before production — images must pass Conforma checks here before they can be promoted to the customer-facing registry.",
        "trigger": "manual",
        "url": "https://github.com/red-hat-data-services/RHOAI-Build-Config/actions/workflows/push-to-stage.yaml",
    },
    {
        "id": "gha-regen-pcc-cache",
        "name": "Regenerate PCC Cache",
        "repo_id": "rhoai-build-config",
        "description": "Regenerates the Pre-built Container Cache (PCC). Required after GA releases to update the base image cache that FBC fragments validate against.",
        "trigger": "manual",
        "url": "https://github.com/red-hat-data-services/RHOAI-Build-Config/actions/workflows/regen-pcc-cache.yaml",
    },
    # ── Release lifecycle workflows ────────────────────────────────────────
    {
        "id": "workflow-nightly-build",
        "name": "Nightly Build Flow",
        "repo_id": "rhoai-build-config",
        "description": "End-to-end nightly build orchestration — ensures fresh operator images are built and validated every night. Flow: GHA triggers → Component CRs annotated → PaC builds → Nudge chain propagates digests → Bundle built → FBC fragment built → Conforma reporter validates. Without nightly builds, releases would use stale images and compliance issues would go undetected until release gate.",
        "trigger": "cron (nightly)",
        "category": "release-lifecycle",
        "url": "https://github.com/red-hat-data-services/RHOAI-Build-Config/actions",
    },
    {
        "id": "workflow-stage-release",
        "name": "Stage Release Flow",
        "repo_id": "rhoai-build-config",
        "description": "Stage release pipeline — the validation gate between development and production. Flow: Snapshot captured → Push-to-stage GHA creates Release CR → Konflux release pipeline runs → Conforma validates against stage EC policy → Images pushed to stage registry → QE testing begins. Stage catches compliance and integration issues in a lower-risk environment before production.",
        "trigger": "manual",
        "category": "release-lifecycle",
        "url": "https://github.com/red-hat-data-services/RHOAI-Build-Config/actions/workflows/push-to-stage.yaml",
    },
    {
        "id": "workflow-prod-release",
        "name": "Production Release Flow",
        "repo_id": "konflux-release-data",
        "description": "Production release pipeline — the final gate before artifacts reach customers. Flow: Stage-validated images → Prod EC policy validation (full SLSA, RPM signatures, FIPS) → Release pipeline copies images to prod registry → Errata advisory created → OperatorHub updated. Any violation here blocks the entire release.",
        "trigger": "manual (TPM-approved)",
        "category": "release-lifecycle",
        "url": "https://gitlab.cee.redhat.com/releng/konflux-release-data",
    },
    {
        "id": "workflow-code-freeze",
        "name": "Code Freeze Gate",
        "repo_id": "rhoai-build-config",
        "description": "Code freeze milestone — no more feature merges after this date, only bug fixes. Enforced via branch protection + merge freeze in IC. Signals the transition from development to stabilization. After code freeze: only approved fixes can merge, nightlies must pass cleanly, and any new failure needs explicit TPM approval to unblock.",
        "trigger": "milestone (scheduled)",
        "category": "release-lifecycle",
        "url": "https://pp.engineering.redhat.com/pp/product/rhai/overview",
    },
    {
        "id": "workflow-rc-cut",
        "name": "RC (Release Candidate) Cut",
        "repo_id": "rhoai-build-config",
        "description": "Release Candidate milestone — a stage release promoted as the candidate for GA. Flow: All builds green → Conforma clean (or exceptions approved) → Snapshot frozen → Stage release → RC tag applied → QE regression testing (Israel team). Multiple RCs are common (RC1, RC2, RC3) as issues are found and fixed. RC is the last checkpoint before production release.",
        "trigger": "manual (TPM-approved)",
        "category": "release-lifecycle",
        "url": "https://github.com/red-hat-data-services/RHOAI-Build-Config",
    },
    {
        "id": "gha-rhoai-sync-agent",
        "name": "RHOAI Sync Agent",
        "repo_id": "rhods-devops-infra",
        "description": "Synchronizes upstream ODH component changes to downstream RHOAI repositories. Without this sync, RHOAI falls behind upstream bug fixes and features, and manual cherry-picks become unsustainable at scale. Handles branch mapping, conflict resolution, and automated PR creation.",
        "trigger": "scheduled / manual",
        "url": "https://github.com/red-hat-data-services/rhods-devops-infra/actions/workflows/rhoai-sync-agent.yaml",
    },
    {
        "id": "gha-verify-nudges",
        "name": "Verify Nudges",
        "repo_id": "rhods-devops-infra",
        "description": "Checks that nudge chain PRs are being created and merged correctly. A broken nudge chain means a rebuilt component image never propagates to the operator bundle or FBC catalog — the release ships stale images without anyone noticing.",
        "trigger": "scheduled",
        "url": "https://github.com/red-hat-data-services/rhods-devops-infra/actions/workflows/verify-nudges.yaml",
    },
    {
        "id": "gha-stage-promoter",
        "name": "Stage Promoter",
        "repo_id": "rhods-devops-infra",
        "description": "Automates promotion of images from stage to production registry after Conforma validation passes. Without this, every GA release would require manual image copying across registries — error-prone and slow at 100+ component scale.",
        "trigger": "manual / scheduled",
        "url": "https://github.com/red-hat-data-services/rhods-devops-infra/actions/workflows/stage-promoter.yaml",
    },
    {
        "id": "gha-retrigger-builds",
        "name": "Retrigger Konflux Builds",
        "repo_id": "konflux-central",
        "description": "Bulk retrigger of failed Konflux builds across components by annotating Component CRs. Needed because transient failures (timeouts, infra flakes) are common at 100+ component scale — without bulk retrigger, each would need a manual dummy commit or individual annotation.",
        "trigger": "manual",
        "url": "https://github.com/red-hat-data-services/konflux-central/actions/workflows/retrigger-builds.yml",
    },
]

PIPELINES = [
    {
        "id": "pipeline-container-build",
        "name": "container-build",
        "description": "Standard container build pipeline for RHOAI components — the pipeline most component images go through. Uses buildah-oci-ta for image builds with trusted artifacts, plus source-build, SAST scanners (snyk, shell, unicode), Clair/ClamAV scans, RPM signature checks, and SBOM generation. Output is signed by Tekton Chains and validated by Conforma before release.",
        "type": "build",
        "url": "https://github.com/red-hat-data-services/konflux-central/blob/main/pipelines/container-build.yaml",
        "doc_url": "https://konflux-ci.dev/docs/building/",
    },
    {
        "id": "pipeline-fbc-builder",
        "name": "fbc-fragment-build",
        "description": "FBC (File-Based Catalog) fragment build pipeline — produces the catalog entries that OLM uses to discover, install, and upgrade the operator. Builds on remote RHEL nodes via buildah-remote-oci-ta. Includes FIPS check, FBC validation (opm validate), and pruning check. A broken FBC blocks operator upgrades on customer clusters.",
        "type": "build",
        "url": "https://github.com/red-hat-data-services/konflux-central/blob/main/pipelines/fbc-fragment-build.yaml",
        "doc_url": "https://konflux-ci.dev/docs/end-to-end/building-olm/",
    },
    {
        "id": "pipeline-multi-arch",
        "name": "multi-arch-container-build",
        "description": "Multi-architecture container build pipeline — required because RHOAI runs on x86, ARM, IBM Z, and Power. Builds images for amd64, arm64, s390x, ppc64le in parallel using remote build nodes, then creates a multi-arch manifest list. Without this, customers on non-x86 hardware cannot deploy RHOAI.",
        "type": "build",
        "url": "https://github.com/red-hat-data-services/konflux-central/blob/main/pipelines/multi-arch-container-build.yaml",
        "doc_url": "https://konflux-ci.dev/docs/building/",
    },
]

TEKTON_TASKS = [
    {
        "id": "task-buildah-remote-oci-ta",
        "name": "buildah-remote-oci-ta",
        "description": "Builds container images using buildah on remote RHEL build nodes with trusted artifact data exchange. Used for FBC fragments and multi-arch builds because these require RHEL-specific tooling (opm, FIPS) or non-x86 architectures unavailable in standard Konflux build pods.",
        "url": "https://github.com/konflux-ci/build-definitions/tree/main/task/buildah-remote-oci-ta",
        "doc_url": "https://konflux-ci.dev/docs/building/using-trusted-artifacts/",
    },
    {
        "id": "task-buildah-oci-ta",
        "name": "buildah-oci-ta",
        "description": "Builds container images using buildah with trusted artifacts (OCI-based data exchange instead of shared PVCs) for secure task-to-task data sharing. Primary build task for standard component images. Trusted artifacts prevent one task from tampering with another task's output.",
        "url": "https://github.com/konflux-ci/build-definitions/tree/main/task/buildah-oci-ta",
        "doc_url": "https://konflux-ci.dev/docs/building/using-trusted-artifacts/",
    },
    {
        "id": "task-source-build-oci-ta",
        "name": "source-build-oci-ta",
        "description": "Creates source container images containing the original source code used to build the binary image. Required for enterprise compliance and SLSA provenance.",
        "url": "https://github.com/konflux-ci/build-definitions/tree/main/task/source-build-oci-ta",
    },
    {
        "id": "task-prefetch-dependencies",
        "name": "prefetch-dependencies-oci-ta",
        "description": "Pre-fetches all declared dependencies using Hermeto (the dependency prefetcher) before the network-isolated hermetic build step. Supports gomod, pip, npm, pnpm, yarn, bundler, cargo, rpm (via rpm-lockfile-prototype), and generic fetcher. Generates accurate SBOMs with pinned versions. Ensures no arbitrary code execution during prefetch.",
        "url": "https://github.com/konflux-ci/build-definitions/tree/main/task/prefetch-dependencies-oci-ta",
        "doc_url": "https://konflux-ci.dev/docs/building/prefetching-dependencies/",
    },
    {
        "id": "task-git-clone-oci-ta",
        "name": "git-clone-oci-ta",
        "description": "Clones the source repository at the specified revision into a trusted artifact. First task in every pipeline — without it, no subsequent task has source code to build, scan, or test. The trusted artifact output ensures downstream tasks receive exactly the cloned content, not a tampered version.",
        "url": "https://github.com/konflux-ci/build-definitions/tree/main/task/git-clone-oci-ta",
    },
    {
        "id": "task-build-image-index",
        "name": "build-image-index",
        "description": "Creates an OCI image index (manifest list) from per-architecture build results. Enables multi-arch image support (amd64, arm64, s390x, ppc64le).",
        "url": "https://github.com/konflux-ci/build-definitions/tree/main/task/build-image-index",
    },
    {
        "id": "task-deprecated-image-check",
        "name": "deprecated-image-check",
        "description": "Checks if the built image uses deprecated or EOL base images. Prevents shipping images built on unsupported RHEL versions or retired UBI images.",
        "url": "https://github.com/konflux-ci/build-definitions/tree/main/task/deprecated-image-check",
    },
    {
        "id": "task-sast-snyk-check",
        "name": "sast-snyk-check-oci-ta",
        "description": "Runs Snyk SAST (Static Application Security Testing) scan on the source code. Catches known CVEs in dependencies and insecure code patterns before they ship. Snyk failures surface as Conforma violations (snyk-check) and can block releases if not excepted.",
        "url": "https://github.com/konflux-ci/build-definitions/tree/main/task/sast-snyk-check-oci-ta",
    },
    {
        "id": "task-rpms-signature-scan",
        "name": "rpms-signature-scan",
        "description": "Validates that all RPM packages in the container image have valid Red Hat GPG signatures. Ensures no unsigned or tampered packages in production images.",
        "url": "https://github.com/konflux-ci/build-definitions/tree/main/task/rpms-signature-scan",
    },
    {
        "id": "task-clair-scan",
        "name": "clair-scan",
        "description": "Runs Clair vulnerability scanner against the built container image. Detects CVEs in OS packages and application dependencies at the image layer — complementing Snyk's source-level scan. Critical/High CVEs found here may block release or require base image updates.",
        "url": "https://github.com/konflux-ci/build-definitions/tree/main/task/clair-scan",
    },
]

AUTOMATIONS = [
    {
        "id": "automation-mintmaker",
        "name": "MintMaker",
        "description": "Automated dependency update service built on Renovate — keeps every component's dependencies current so security fixes and upstream improvements flow in continuously. Scans repos every 4h (Tekton tasks on Saturdays 5AM UTC). Supports Go, Python, npm, RPM, container images, Cargo, Bundler. PRs via red-hat-konflux GitHub app with GPG-signed commits. Without MintMaker, dependency updates would be manual and inconsistent across 100+ repos.",
        "type": "dependency-management",
        "url": "https://github.com/konflux-ci/mintmaker",
        "doc_url": "https://konflux-ci.dev/docs/mintmaker/user/",
    },
    {
        "id": "automation-renovate",
        "name": "Renovate",
        "description": "Tekton task bundle SHA updater for konflux-central — keeps pipeline tasks on latest trusted versions. Detects when upstream build-definitions publishes new task versions and creates PRs to update the pinned SHA digests. Without this, pipelines would use outdated tasks that may fail Conforma's trusted-task checks or lack security fixes.",
        "type": "dependency-management",
        "url": "https://github.com/red-hat-data-services/konflux-central/tree/main/renovate",
    },
    {
        "id": "automation-nudge",
        "name": "Nudge Chain",
        "description": "Automatic cross-component digest update mechanism — propagates every successful build to all consumers so operators always ship the latest tested images. When a parent component builds, Konflux generates Renovate PRs to update sha256 digest references downstream. Chain: component image → operator → bundle → FBC fragment. Without nudging, operators would reference stale images, causing snapshot SHA drift and Conforma violations.",
        "type": "build-chain",
        "url": "https://konflux-ci.dev/docs/building/component-nudges/",
        "doc_url": "https://konflux-ci.dev/docs/building/component-nudges/",
    },
    {
        "id": "automation-pac",
        "name": "Pipelines-as-Code",
        "description": "Webhook-driven build trigger system (built on Tekton) — ensures every code change is automatically built and validated without manual intervention. Runs pre-merge (pull_request) and post-merge (push) PipelineRuns from .tekton/ directory definitions. Retrigger via /retest comment. Without PaC, builds would need manual triggering, and unreviewed code could reach production.",
        "type": "ci-trigger",
        "url": "https://pipelinesascode.com/",
        "doc_url": "https://konflux-ci.dev/docs/building/running/",
    },
    {
        "id": "automation-tekton-chains",
        "name": "Tekton Chains",
        "description": "Supply chain security attestation — provides cryptographic proof that each artifact was built from the claimed source by a trusted pipeline, which Conforma verifies at release time. Automatically signs build results and generates in-toto SLSA provenance attestations stored alongside container images. Without Chains, there would be no verifiable link between source code and shipped binaries.",
        "type": "security",
        "url": "https://tekton.dev/docs/chains/",
        "doc_url": "https://konflux-ci.dev/docs/compliance/",
    },
    {
        "id": "automation-conforma",
        "name": "Conforma (Enterprise Contract)",
        "description": "Policy-based artifact verification framework — the final gate that prevents non-compliant artifacts from reaching customers. Validates container images against supply chain security policies by checking SLSA provenance attestations, build task trust, and compliance rules defined in EnterpriseContractPolicy CRDs. Without Conforma, there would be no automated enforcement of security and compliance requirements at release time.",
        "type": "compliance",
        "url": "https://conforma.dev/",
        "doc_url": "https://konflux-ci.dev/docs/compliance/",
    },
]

# Relationships between nodes
STATIC_RELATIONSHIPS = [
    # Repos contain workflows
    ("Repository", "konflux-central", "CONTAINS", "Automation", "automation-renovate"),
    ("Repository", "konflux-central", "CONTAINS", "Workflow", "gha-sync-pipelineruns"),
    ("Repository", "konflux-central", "CONTAINS", "Workflow", "gha-run-renovate"),
    ("Repository", "konflux-central", "CONTAINS", "Workflow", "gha-retrigger-builds"),
    ("Repository", "rhoai-build-config", "CONTAINS", "Workflow", "gha-trigger-nightly-bundle"),
    ("Repository", "rhoai-build-config", "CONTAINS", "Workflow", "gha-trigger-nightly-fbc"),
    ("Repository", "rhoai-build-config", "CONTAINS", "Workflow", "gha-push-to-stage"),
    ("Repository", "rhoai-build-config", "CONTAINS", "Workflow", "gha-regen-pcc-cache"),
    ("Repository", "rhods-devops-infra", "CONTAINS", "Workflow", "gha-trigger-nightlies"),
    ("Repository", "rhods-devops-infra", "CONTAINS", "Workflow", "gha-rhoai-sync-agent"),
    ("Repository", "rhods-devops-infra", "CONTAINS", "Workflow", "gha-verify-nudges"),
    ("Repository", "rhods-devops-infra", "CONTAINS", "Workflow", "gha-stage-promoter"),
    ("Repository", "conforma-reporter", "CONTAINS", "Workflow", "gha-conforma-reporter"),
    # Release lifecycle workflows belong to their orchestrating repos
    ("Repository", "rhoai-build-config", "CONTAINS", "Workflow", "workflow-nightly-build"),
    ("Repository", "rhoai-build-config", "CONTAINS", "Workflow", "workflow-stage-release"),
    ("Repository", "rhoai-build-config", "CONTAINS", "Workflow", "workflow-code-freeze"),
    ("Repository", "rhoai-build-config", "CONTAINS", "Workflow", "workflow-rc-cut"),
    ("Repository", "konflux-release-data", "CONTAINS", "Workflow", "workflow-prod-release"),
    # Repos contain pipelines
    ("Repository", "konflux-central", "CONTAINS", "Pipeline", "pipeline-container-build"),
    ("Repository", "konflux-central", "CONTAINS", "Pipeline", "pipeline-fbc-builder"),
    ("Repository", "konflux-central", "CONTAINS", "Pipeline", "pipeline-multi-arch"),
    # Repos contain config
    ("Repository", "konflux-release-data", "CONTAINS_CONFIG", "ECPolicy", "ec-registry-rhoai-stage"),
    ("Repository", "konflux-release-data", "CONTAINS_CONFIG", "ECPolicy", "ec-registry-rhoai-prod"),
    ("Repository", "konflux-release-data", "CONTAINS_CONFIG", "ECPolicy", "ec-fbc-rhoai-stage"),
    ("Repository", "konflux-release-data", "CONTAINS_CONFIG", "ECPolicy", "ec-fbc-rhoai-prod"),
    # build-definitions contains tasks
    ("Repository", "build-definitions", "CONTAINS", "TektonTask", "task-buildah-remote-oci-ta"),
    ("Repository", "build-definitions", "CONTAINS", "TektonTask", "task-buildah-oci-ta"),
    ("Repository", "build-definitions", "CONTAINS", "TektonTask", "task-source-build-oci-ta"),
    ("Repository", "build-definitions", "CONTAINS", "TektonTask", "task-prefetch-dependencies"),
    ("Repository", "build-definitions", "CONTAINS", "TektonTask", "task-git-clone-oci-ta"),
    ("Repository", "build-definitions", "CONTAINS", "TektonTask", "task-build-image-index"),
    ("Repository", "build-definitions", "CONTAINS", "TektonTask", "task-deprecated-image-check"),
    ("Repository", "build-definitions", "CONTAINS", "TektonTask", "task-sast-snyk-check"),
    ("Repository", "build-definitions", "CONTAINS", "TektonTask", "task-rpms-signature-scan"),
    ("Repository", "build-definitions", "CONTAINS", "TektonTask", "task-clair-scan"),
    # Container-build pipeline uses tasks
    ("Pipeline", "pipeline-container-build", "USES_TASK", "TektonTask", "task-git-clone-oci-ta"),
    ("Pipeline", "pipeline-container-build", "USES_TASK", "TektonTask", "task-prefetch-dependencies"),
    ("Pipeline", "pipeline-container-build", "USES_TASK", "TektonTask", "task-buildah-oci-ta"),
    ("Pipeline", "pipeline-container-build", "USES_TASK", "TektonTask", "task-build-image-index"),
    ("Pipeline", "pipeline-container-build", "USES_TASK", "TektonTask", "task-source-build-oci-ta"),
    ("Pipeline", "pipeline-container-build", "USES_TASK", "TektonTask", "task-sast-snyk-check"),
    ("Pipeline", "pipeline-container-build", "USES_TASK", "TektonTask", "task-deprecated-image-check"),
    ("Pipeline", "pipeline-container-build", "USES_TASK", "TektonTask", "task-clair-scan"),
    ("Pipeline", "pipeline-container-build", "USES_TASK", "TektonTask", "task-rpms-signature-scan"),
    # FBC pipeline uses tasks
    ("Pipeline", "pipeline-fbc-builder", "USES_TASK", "TektonTask", "task-git-clone-oci-ta"),
    ("Pipeline", "pipeline-fbc-builder", "USES_TASK", "TektonTask", "task-prefetch-dependencies"),
    ("Pipeline", "pipeline-fbc-builder", "USES_TASK", "TektonTask", "task-buildah-remote-oci-ta"),
    ("Pipeline", "pipeline-fbc-builder", "USES_TASK", "TektonTask", "task-build-image-index"),
    ("Pipeline", "pipeline-fbc-builder", "USES_TASK", "TektonTask", "task-deprecated-image-check"),
    # Multi-arch uses same tasks as container-build
    ("Pipeline", "pipeline-multi-arch", "USES_TASK", "TektonTask", "task-git-clone-oci-ta"),
    ("Pipeline", "pipeline-multi-arch", "USES_TASK", "TektonTask", "task-prefetch-dependencies"),
    ("Pipeline", "pipeline-multi-arch", "USES_TASK", "TektonTask", "task-buildah-remote-oci-ta"),
    ("Pipeline", "pipeline-multi-arch", "USES_TASK", "TektonTask", "task-build-image-index"),
    # ── Automation → Pipeline triggers ──────────────────────────────────────
    # Nudge creates PRs that trigger PaC builds
    ("Automation", "automation-nudge", "TRIGGERS", "Pipeline", "pipeline-container-build"),
    # PaC triggers all build pipelines from .tekton/ definitions
    ("Automation", "automation-pac", "TRIGGERS", "Pipeline", "pipeline-container-build"),
    ("Automation", "automation-pac", "TRIGGERS", "Pipeline", "pipeline-fbc-builder"),
    ("Automation", "automation-pac", "TRIGGERS", "Pipeline", "pipeline-multi-arch"),
    # ── Dependency management ────────────────────────────────────────────
    # Renovate updates task bundle SHAs in pipeline definitions
    ("Automation", "automation-renovate", "UPDATES", "Pipeline", "pipeline-container-build"),
    ("Automation", "automation-renovate", "UPDATES", "Pipeline", "pipeline-fbc-builder"),
    ("Automation", "automation-renovate", "UPDATES", "Pipeline", "pipeline-multi-arch"),
    # MintMaker is built on Renovate with RPM lockfile extension + Konflux integration
    ("Automation", "automation-mintmaker", "BUILT_ON", "Automation", "automation-renovate"),
    # MintMaker creates dependency update PRs for component repos
    ("Automation", "automation-mintmaker", "CREATES_PR_FOR", "Repository", "rhods-operator"),
    ("Automation", "automation-mintmaker", "CREATES_PR_FOR", "Repository", "rhoai-build-config"),
    ("Automation", "automation-mintmaker", "CREATES_PR_FOR", "Repository", "opendatahub-operator"),
    # Nudge uses Renovate internally to create digest-update PRs
    ("Automation", "automation-nudge", "USES", "Automation", "automation-renovate"),
    # ── Supply chain security (trust model) ──────────────────────────────
    # Tekton Chains observes builds and generates SLSA provenance attestations
    ("Automation", "automation-tekton-chains", "SIGNS_OUTPUT_OF", "Pipeline", "pipeline-container-build"),
    ("Automation", "automation-tekton-chains", "SIGNS_OUTPUT_OF", "Pipeline", "pipeline-fbc-builder"),
    ("Automation", "automation-tekton-chains", "SIGNS_OUTPUT_OF", "Pipeline", "pipeline-multi-arch"),
    # Conforma validates attestations created by Tekton Chains
    ("Automation", "automation-conforma", "VALIDATES_ATTESTATION_FROM", "Automation", "automation-tekton-chains"),
    # Conforma enforces EC policies
    ("Automation", "automation-conforma", "ENFORCES", "ECPolicy", "ec-registry-rhoai-stage"),
    ("Automation", "automation-conforma", "ENFORCES", "ECPolicy", "ec-registry-rhoai-prod"),
    ("Automation", "automation-conforma", "ENFORCES", "ECPolicy", "ec-fbc-rhoai-stage"),
    ("Automation", "automation-conforma", "ENFORCES", "ECPolicy", "ec-fbc-rhoai-prod"),
    # ── Workflow → Automation links ──────────────────────────────────────
    # Nightly triggers annotate Component CRs to trigger PaC builds
    ("Workflow", "gha-trigger-nightlies", "TRIGGERS", "Automation", "automation-pac"),
    ("Workflow", "gha-trigger-nightly-bundle", "TRIGGERS", "Automation", "automation-pac"),
    ("Workflow", "gha-trigger-nightly-fbc", "TRIGGERS", "Automation", "automation-pac"),
    # Retrigger builds re-runs PaC for failed components
    ("Workflow", "gha-retrigger-builds", "TRIGGERS", "Automation", "automation-pac"),
    # Conforma reporter runs Conforma validation across all images
    ("Workflow", "gha-conforma-reporter", "RUNS", "Automation", "automation-conforma"),
    # Push-to-stage creates a Release CR, which triggers Conforma validation
    ("Workflow", "gha-push-to-stage", "TRIGGERS", "Automation", "automation-conforma"),
    # Verify-nudges monitors the nudge chain for broken propagation
    ("Workflow", "gha-verify-nudges", "MONITORS", "Automation", "automation-nudge"),
    # ── Workflow → infrastructure ────────────────────────────────────────
    # Sync pipelineruns distributes pipeline definitions to component repos
    ("Workflow", "gha-sync-pipelineruns", "DISTRIBUTES", "Pipeline", "pipeline-container-build"),
    ("Workflow", "gha-sync-pipelineruns", "DISTRIBUTES", "Pipeline", "pipeline-fbc-builder"),
    ("Workflow", "gha-sync-pipelineruns", "DISTRIBUTES", "Pipeline", "pipeline-multi-arch"),
    # RHOAI sync agent synchronizes upstream ODH changes to downstream
    ("Workflow", "gha-rhoai-sync-agent", "SYNCS", "Repository", "rhods-operator"),
    # Renovate workflow runs the Renovate automation
    ("Workflow", "gha-run-renovate", "RUNS", "Automation", "automation-renovate"),
    # ── Release / promotion workflows ────────────────────────────────────
    # PCC cache is required by FBC fragment builds for validation
    ("Workflow", "gha-regen-pcc-cache", "UPDATES_CACHE_FOR", "Pipeline", "pipeline-fbc-builder"),
    # Stage promoter promotes images validated by stage Conforma
    ("Workflow", "gha-stage-promoter", "PROMOTES_VIA", "ECPolicy", "ec-registry-rhoai-stage"),
    # ── Release lifecycle workflows ──────────────────────────────────────
    # Nightly build flow orchestrates the full nightly chain
    ("Workflow", "workflow-nightly-build", "ORCHESTRATES", "Workflow", "gha-trigger-nightlies"),
    ("Workflow", "workflow-nightly-build", "ORCHESTRATES", "Workflow", "gha-trigger-nightly-bundle"),
    ("Workflow", "workflow-nightly-build", "ORCHESTRATES", "Workflow", "gha-trigger-nightly-fbc"),
    ("Workflow", "workflow-nightly-build", "VALIDATED_BY", "Workflow", "gha-conforma-reporter"),
    # Stage release flow
    ("Workflow", "workflow-stage-release", "TRIGGERS", "Workflow", "gha-push-to-stage"),
    ("Workflow", "workflow-stage-release", "VALIDATED_BY", "ECPolicy", "ec-registry-rhoai-stage"),
    ("Workflow", "workflow-stage-release", "VALIDATED_BY", "ECPolicy", "ec-fbc-rhoai-stage"),
    # Prod release flow
    ("Workflow", "workflow-prod-release", "PROMOTED_FROM", "Workflow", "workflow-stage-release"),
    ("Workflow", "workflow-prod-release", "VALIDATED_BY", "ECPolicy", "ec-registry-rhoai-prod"),
    ("Workflow", "workflow-prod-release", "VALIDATED_BY", "ECPolicy", "ec-fbc-rhoai-prod"),
    # RC is a stage release promoted as candidate
    ("Workflow", "workflow-rc-cut", "PRODUCES", "Workflow", "workflow-stage-release"),
    ("Workflow", "workflow-rc-cut", "GATES", "Workflow", "workflow-prod-release"),
    # Code freeze gates merges before RC
    ("Workflow", "workflow-code-freeze", "GATES", "Workflow", "workflow-rc-cut"),
    # Stage promoter moves stage-validated images to prod
    ("Workflow", "gha-stage-promoter", "EXECUTES", "Workflow", "workflow-prod-release"),
    # ── Upstream → downstream ────────────────────────────────────────────
    ("Repository", "opendatahub-operator", "UPSTREAM_OF", "Repository", "rhods-operator"),
]


EC_POLICIES = [
    {
        "id": "ec-registry-rhoai-stage",
        "name": "registry-rhoai-stage",
        "environment": "stage",
        "scope": "registry",
        "description": "Stage EnterpriseContractPolicy for component images — catches compliance issues early in a lower-risk environment before production enforcement. Less strict than prod — allows deprecated-image-check and snyk-check warnings. Used by stage ReleasePlanAdmission. The stage/prod gap lets teams iterate on fixes without blocking development.",
        "url": "https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/config/stone-prod-p02.hjvn.p1/product/EnterpriseContractPolicy/registry-rhoai-stage.yaml",
        "doc_url": "https://konflux-ci.dev/docs/compliance/customizing-policy/",
    },
    {
        "id": "ec-registry-rhoai-prod",
        "name": "registry-rhoai-prod",
        "environment": "prod",
        "scope": "registry",
        "description": "Production EnterpriseContractPolicy for component images. Full enforcement of SLSA provenance, RPM signatures, FIPS compliance, and all security checks. Gate for GA release.",
        "url": "https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/config/stone-prod-p02.hjvn.p1/product/EnterpriseContractPolicy/registry-rhoai-prod.yaml",
        "doc_url": "https://conforma.dev/docs/policy/index.html",
    },
    {
        "id": "ec-fbc-rhoai-stage",
        "name": "fbc-rhoai-stage",
        "environment": "stage",
        "scope": "fbc",
        "description": "Stage EnterpriseContractPolicy for FBC fragment images — validates the operator catalog structure early to catch malformed entries before they reach production. Checks related images, basic provenance, and catalog fragment integrity. FBC-specific rules (like target-index pruning) are lighter in stage to allow iterative catalog development.",
        "url": "https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/config/stone-prod-p02.hjvn.p1/product/EnterpriseContractPolicy/fbc-rhoai-stage.yaml",
        "doc_url": "https://konflux-ci.dev/docs/end-to-end/building-olm/",
    },
    {
        "id": "ec-fbc-rhoai-prod",
        "name": "fbc-rhoai-prod",
        "environment": "prod",
        "scope": "fbc",
        "description": "Production EnterpriseContractPolicy for FBC fragment images — the final gate for catalog changes reaching customers via OperatorHub. Includes target-index pruning checks ensuring old operator versions are properly removed, preventing version bloat and upgrade path issues in the shipped catalog.",
        "url": "https://gitlab.cee.redhat.com/releng/konflux-release-data/-/blob/main/config/stone-prod-p02.hjvn.p1/product/EnterpriseContractPolicy/fbc-rhoai-prod.yaml",
        "doc_url": "https://conforma.dev/docs/policy/index.html",
    },
]


# ─── Seeding functions ─────────────────────────────────────────────────────────

def _merge_node(tx, label: str, data: dict[str, Any]):
    """MERGE a node by id, setting all properties."""
    props = {k: v for k, v in data.items() if k != "id"}
    prop_str = ", ".join(f"n.{k} = ${k}" for k in props)
    query = f"MERGE (n:{label} {{id: $id}}) SET {prop_str}, n._source = 'seed', n._seeded_at = datetime()"
    tx.run(query, id=data["id"], **props)


def _merge_relationship(tx, from_label, from_id, rel_type, to_label, to_id, props=None):
    """MERGE a relationship between two nodes by id."""
    query = (
        f"MATCH (a:{from_label} {{id: $from_id}}), (b:{to_label} {{id: $to_id}}) "
        f"MERGE (a)-[r:{rel_type}]->(b)"
    )
    if props:
        set_clause = ", ".join(f"r.{k} = ${k}" for k in props)
        query += f" SET {set_clause}"
    tx.run(query, from_id=from_id, to_id=to_id, **(props or {}))


def seed_static_nodes(driver):
    """Seed static infrastructure nodes."""
    with driver.session() as s:
        for repo in REPOSITORIES:
            s.execute_write(_merge_node, "Repository", repo)
        logger.info("Seeded %d Repository nodes", len(REPOSITORIES))

        for wf in WORKFLOWS:
            s.execute_write(_merge_node, "Workflow", wf)
        logger.info("Seeded %d Workflow nodes", len(WORKFLOWS))

        for pl in PIPELINES:
            s.execute_write(_merge_node, "Pipeline", pl)
        logger.info("Seeded %d Pipeline nodes", len(PIPELINES))

        for task in TEKTON_TASKS:
            s.execute_write(_merge_node, "TektonTask", task)
        logger.info("Seeded %d TektonTask nodes", len(TEKTON_TASKS))

        for auto in AUTOMATIONS:
            s.execute_write(_merge_node, "Automation", auto)
        logger.info("Seeded %d Automation nodes", len(AUTOMATIONS))

        for policy in EC_POLICIES:
            s.execute_write(_merge_node, "ECPolicy", policy)
        logger.info("Seeded %d ECPolicy nodes", len(EC_POLICIES))


def seed_static_relationships(driver):
    """Seed static relationships between infrastructure nodes."""
    with driver.session() as s:
        for from_label, from_id, rel_type, to_label, to_id in STATIC_RELATIONSHIPS:
            s.execute_write(_merge_relationship, from_label, from_id, rel_type, to_label, to_id)
    logger.info("Seeded %d relationships", len(STATIC_RELATIONSHIPS))


# ─── Live data seeding ─────────────────────────────────────────────────────────

def _github_get(path: str) -> Any:
    """GET from GitHub API with optional auth."""
    import urllib.request
    import urllib.error

    url = f"https://api.github.com/{path}"
    req = urllib.request.Request(url)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("GitHub API %s: %s", path, e)
        return None


def seed_components_from_ic(driver):
    """Seed Component nodes from IC API (live cluster data).

    Calls IC's HTTP API — no Python imports from IC.
    Falls back gracefully if IC is unavailable.
    """
    components = []
    ic_url = os.environ.get("IC_API_URL", "http://localhost:8000")

    try:
        import urllib.request
        import urllib.error

        # Get list of applications from IC
        req = urllib.request.Request(f"{ic_url}/api/v1/applications")
        with urllib.request.urlopen(req, timeout=10) as resp:
            apps_data = json.loads(resp.read())

        app_names = [a.get("name") or a.get("application") for a in apps_data if a]

        for app_name in app_names:
            if not app_name:
                continue
            req = urllib.request.Request(f"{ic_url}/api/v1/applications/{app_name}/working")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    working = json.loads(resp.read())
                for comp in working.get("components", working if isinstance(working, list) else []):
                    name = comp.get("component_name") or comp.get("name", "")
                    if not name:
                        continue
                    components.append({
                        "id": f"comp-{name}",
                        "name": name,
                        "application": app_name,
                        "source_repo": comp.get("source_repo", ""),
                        "source_branch": comp.get("source_branch", ""),
                        "container_image": comp.get("container_image", ""),
                        "source": "ic-api",
                    })
            except urllib.error.HTTPError:
                logger.warning("Could not fetch components for %s", app_name)

        logger.info("Fetched %d components from IC API", len(components))
    except Exception as e:
        logger.warning("IC API not available at %s: %s", ic_url, e)

    if not components:
        logger.info("No components from cluster; skipping component seeding")
        return

    with driver.session() as s:
        # Seed Application nodes
        apps = {c["application"] for c in components}
        for app_name in apps:
            s.execute_write(_merge_node, "Application", {
                "id": f"app-{app_name}",
                "name": app_name,
            })

        # Seed Component nodes and CONTAINS relationships
        for comp in components:
            s.execute_write(_merge_node, "Component", comp)
            s.execute_write(
                _merge_relationship,
                "Application", f"app-{comp['application']}",
                "CONTAINS", "Component", comp["id"],
            )

            # Link components to their build pipeline
            pipeline_id = "pipeline-fbc-builder" if "fbc" in comp["name"] else "pipeline-container-build"
            s.execute_write(
                _merge_relationship,
                "Component", comp["id"],
                "BUILDS_WITH", "Pipeline", pipeline_id,
            )

    logger.info("Seeded %d components across %d applications", len(components), len(apps))

    # Seed nudge chain relationships (component → operator → bundle → FBC)
    _seed_nudge_chains(driver, components)


def _seed_nudge_chains(driver, components):
    """Infer and seed nudge chain relationships from component names."""
    comp_ids = {c["name"]: c["id"] for c in components}
    nudge_count = 0

    with driver.session() as s:
        for name, cid in comp_ids.items():
            # operator → bundle (operator-bundle suffix)
            bundle_name = f"{name}-bundle"
            if bundle_name in comp_ids:
                s.execute_write(
                    _merge_relationship,
                    "Component", cid,
                    "NUDGES", "Component", comp_ids[bundle_name],
                )
                nudge_count += 1

            # bundle → FBC fragment (naming convention)
            if name.endswith("-bundle"):
                for fbc_name, fbc_id in comp_ids.items():
                    if "fbc-fragment" in fbc_name:
                        s.execute_write(
                            _merge_relationship,
                            "Component", cid,
                            "NUDGES", "Component", fbc_id,
                        )
                        nudge_count += 1
                        break

    if nudge_count:
        logger.info("Inferred %d nudge chain relationships", nudge_count)


def seed_ec_policy_rules(driver):
    """Seed ECPolicy → validates → Application relationships and policy rules."""
    with driver.session() as s:
        # Link policies to applications
        for app_suffix, env_pairs in [
            ("v3-5", [
                ("ec-registry-rhoai-stage", "stage"),
                ("ec-registry-rhoai-prod", "prod"),
                ("ec-fbc-rhoai-stage", "stage"),
                ("ec-fbc-rhoai-prod", "prod"),
            ]),
        ]:
            for policy_id, env in env_pairs:
                s.run(
                    "MATCH (p:ECPolicy {id: $pid}) "
                    "MATCH (a:Application) WHERE a.name CONTAINS $suffix "
                    "MERGE (p)-[:VALIDATES {environment: $env}]->(a)",
                    pid=policy_id, suffix=app_suffix, env=env,
                )

    logger.info("Linked EC policies to applications")


# ─── Verification ──────────────────────────────────────────────────────────────

def verify_graph(driver):
    """Check graph integrity: orphan nodes, missing relationships."""
    issues = []
    with driver.session() as s:
        # Orphan components (no application)
        result = s.run(
            "MATCH (c:Component) WHERE NOT (c)<-[:CONTAINS]-(:Application) RETURN count(c) AS n"
        )
        orphans = result.single()["n"]
        if orphans:
            issues.append(f"{orphans} orphan Component nodes (no Application)")

        # Workflows not linked to repos
        result = s.run(
            "MATCH (w:Workflow) WHERE NOT (w)<-[:CONTAINS]-(:Repository) RETURN count(w) AS n"
        )
        orphans = result.single()["n"]
        if orphans:
            issues.append(f"{orphans} orphan Workflow nodes (no Repository)")

        # Components without build pipeline
        result = s.run(
            "MATCH (c:Component) WHERE NOT (c)-[:BUILDS_WITH]->(:Pipeline) RETURN count(c) AS n"
        )
        no_pipeline = result.single()["n"]
        if no_pipeline:
            issues.append(f"{no_pipeline} Components with no build pipeline link")

    if issues:
        logger.warning("Graph integrity issues:")
        for issue in issues:
            logger.warning("  - %s", issue)
    else:
        logger.info("Graph integrity check passed")
    return issues


def print_stats(driver):
    """Print node and relationship counts."""
    with driver.session() as s:
        result = s.run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC"
        )
        print("\n=== Node Counts ===")
        total = 0
        for rec in result:
            print(f"  {rec['label']:<20} {rec['count']:>5}")
            total += rec['count']
        print(f"  {'TOTAL':<20} {total:>5}")

        result = s.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY count DESC"
        )
        print("\n=== Relationship Counts ===")
        total = 0
        for rec in result:
            print(f"  {rec['type']:<25} {rec['count']:>5}")
            total += rec['count']
        print(f"  {'TOTAL':<25} {total:>5}")
        print()


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Seed Neo4j system map graph")
    parser.add_argument("--schema-only", action="store_true", help="Only create constraints/indexes")
    parser.add_argument("--verify", action="store_true", help="Verify graph integrity")
    parser.add_argument("--stats", action="store_true", help="Show node/edge counts")
    parser.add_argument("--no-cluster", action="store_true", help="Skip cluster data (static only)")
    args = parser.parse_args()

    driver = _get_driver()

    if args.stats:
        print_stats(driver)
        return

    if args.verify:
        verify_graph(driver)
        return

    create_schema(driver)
    if args.schema_only:
        return

    seed_static_nodes(driver)
    seed_static_relationships(driver)

    if not args.no_cluster:
        seed_components_from_ic(driver)
        seed_ec_policy_rules(driver)

    verify_graph(driver)
    print_stats(driver)

    logger.info("System map seeding complete")


if __name__ == "__main__":
    main()
