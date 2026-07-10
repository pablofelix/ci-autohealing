"""Concept paths for interactive onboarding — maps CI/CD concepts to graph nodes and edges.

Each concept defines which nodes and edges to highlight when the user asks about
a CI/CD topic.  The chat service detects concept queries, returns highlight
instructions alongside the LLM response, and the frontend dims non-relevant
nodes while glowing the concept path.

Edge IDs follow the React Flow convention: ``{source}-{label}-{target}``.
"""

import re

CONCEPT_PATHS = {
    "conforma": {
        "title": "Conforma Validation",
        "triggers": [
            "explain conforma", "what is conforma", "how.*conforma work",
            "ec policy", "enterprise contract",
            "policy check", "explain.*compliance",
        ],
        "nodes": [
            "automation-conforma",
            "automation-tekton-chains",
            "pipeline-container-build",
            "ec-registry-rhoai-stage",
            "ec-registry-rhoai-prod",
            "ec-fbc-rhoai-stage",
            "ec-fbc-rhoai-prod",
            "workflow-stage-release",
            "workflow-prod-release",
            "gha-conforma-reporter",
        ],
        "edges": [
            "automation-conforma-ENFORCES-ec-registry-rhoai-stage",
            "automation-conforma-ENFORCES-ec-registry-rhoai-prod",
            "automation-conforma-ENFORCES-ec-fbc-rhoai-stage",
            "automation-conforma-ENFORCES-ec-fbc-rhoai-prod",
            "automation-conforma-VALIDATES_ATTESTATION_FROM-automation-tekton-chains",
            "automation-tekton-chains-SIGNS_OUTPUT_OF-pipeline-container-build",
            "workflow-stage-release-VALIDATED_BY-ec-registry-rhoai-stage",
            "workflow-stage-release-VALIDATED_BY-ec-fbc-rhoai-stage",
            "workflow-prod-release-VALIDATED_BY-ec-registry-rhoai-prod",
            "workflow-prod-release-VALIDATED_BY-ec-fbc-rhoai-prod",
            "gha-conforma-reporter-RUNS-automation-conforma",
        ],
        "glow_color": "#be185d",
        "narrative": (
            "Conforma is the security checkpoint that every container image "
            "must pass before it can be released to customers. Think of it "
            "like airport security — it checks that each image has a valid "
            "'passport' (a cryptographic signature proving it was built by "
            "our trusted build system, not tampered with). These signatures "
            "are created by Tekton Chains, which watches every build and "
            "signs the output automatically.\n\n"
            "There are two levels of checking: Stage policies are more "
            "relaxed (used during testing), while Production policies are "
            "strict (the real gate before customers get the software). The "
            "ECPolicy nodes you see on the map define exactly what rules each "
            "level enforces — things like 'was this built from approved source "
            "code?' and 'did all security scans pass?'\n\n"
            "If Conforma fails for any image, the entire release is blocked "
            "until the issue is fixed. This is intentional — it prevents "
            "shipping software that doesn't meet security requirements."
        ),
    },
    "nudging": {
        "title": "Nudge Chain (Dependency Propagation)",
        "triggers": [
            "nudg", "nudge chain", "dependency propagation",
            "image update", "digest update",
        ],
        "nodes": [
            "automation-nudge",
            "automation-renovate",
            "gha-verify-nudges",
            "pipeline-container-build",
        ],
        "edge_labels": ["NUDGES"],
        "edges": [
            "automation-nudge-USES-automation-renovate",
            "automation-nudge-TRIGGERS-pipeline-container-build",
            "gha-verify-nudges-MONITORS-automation-nudge",
        ],
        "glow_color": "#059669",
        "narrative": (
            "Nudging is the automatic update chain that keeps all our "
            "software pieces in sync. Imagine you have a chain of dominoes: "
            "when one piece gets rebuilt, every piece that depends on it "
            "needs to update too.\n\n"
            "Here's a real example: The main operator (the brain of RHOAI) "
            "gets a bug fix and rebuilds. The 'nudge' automation detects "
            "this and automatically creates a Pull Request (a code change "
            "proposal) in the operator-bundle (the packaging layer) to "
            "use the new version. Once that PR merges and the bundle "
            "rebuilds, nudge does the same thing again for the FBC "
            "fragment (the catalog entry that tells OpenShift where to "
            "find the operator).\n\n"
            "Without nudging, a human would need to manually update version "
            "references across 100+ repositories every time anything "
            "changes — that would take hours and be very error-prone. The "
            "verify-nudges workflow monitors the chain to make sure no "
            "updates get stuck or lost."
        ),
    },
    "build_pipeline": {
        "title": "Build Pipeline Flow",
        "triggers": [
            "build pipeline", "how.*build", "container build",
            "tekton task", "tekton pipeline", "pac",
            "pipelines.as.code",
        ],
        "nodes": [
            "konflux-central",
            "pipeline-container-build",
            "task-git-clone-oci-ta",
            "task-prefetch-dependencies",
            "task-buildah-oci-ta",
            "task-sast-snyk-check",
            "task-clair-scan",
            "task-build-image-index",
            "automation-pac",
            "automation-tekton-chains",
        ],
        "edges": [
            "konflux-central-CONTAINS-pipeline-container-build",
            "pipeline-container-build-USES_TASK-task-git-clone-oci-ta",
            "pipeline-container-build-USES_TASK-task-prefetch-dependencies",
            "pipeline-container-build-USES_TASK-task-buildah-oci-ta",
            "pipeline-container-build-USES_TASK-task-build-image-index",
            "pipeline-container-build-USES_TASK-task-sast-snyk-check",
            "pipeline-container-build-USES_TASK-task-clair-scan",
            "automation-pac-TRIGGERS-pipeline-container-build",
            "automation-tekton-chains-SIGNS_OUTPUT_OF-pipeline-container-build",
        ],
        "glow_color": "#d97706",
        "narrative": (
            "The build pipeline is the assembly line that turns source code "
            "into a container image (a portable package of software that can "
            "run anywhere). Here's what happens step by step:\n\n"
            "1. git-clone: Downloads the latest source code from the "
            "repository (like downloading a project from GitHub).\n"
            "2. prefetch-dependencies: Downloads all libraries and packages "
            "the code needs, in a sealed environment with no internet access. "
            "This ensures builds are reproducible — the same code always "
            "produces the same result.\n"
            "3. buildah: Compiles the code and packages it into a container "
            "image (think of it like putting your app in a shipping container).\n"
            "4. Security scans (Snyk and Clair): Automatically check the "
            "image for known security vulnerabilities — like a virus scanner "
            "for software packages.\n"
            "5. Tekton Chains: Signs the finished image with a cryptographic "
            "signature proving it was built by our trusted system.\n\n"
            "The whole process is triggered automatically by Pipelines-as-Code "
            "(PaC) — every time someone opens a Pull Request or merges code, "
            "a new build starts without anyone clicking a button."
        ),
    },
    "release_flow": {
        "title": "Release Lifecycle",
        "triggers": [
            "explain.*release", "release lifecycle", "how.*release work",
            "walk.*through.*release", "nightly.*release",
            "stage.*prod.*flow", "rc.*cut", "code freeze",
            "how.*ship", "ga release",
        ],
        "nodes": [
            "workflow-nightly-build",
            "gha-trigger-nightlies",
            "gha-trigger-nightly-bundle",
            "gha-trigger-nightly-fbc",
            "gha-conforma-reporter",
            "workflow-stage-release",
            "gha-push-to-stage",
            "workflow-code-freeze",
            "workflow-rc-cut",
            "workflow-prod-release",
            "gha-stage-promoter",
        ],
        "edges": [
            "workflow-nightly-build-ORCHESTRATES-gha-trigger-nightlies",
            "workflow-nightly-build-ORCHESTRATES-gha-trigger-nightly-bundle",
            "workflow-nightly-build-ORCHESTRATES-gha-trigger-nightly-fbc",
            "workflow-nightly-build-VALIDATED_BY-gha-conforma-reporter",
            "workflow-stage-release-TRIGGERS-gha-push-to-stage",
            "workflow-code-freeze-GATES-workflow-rc-cut",
            "workflow-rc-cut-PRODUCES-workflow-stage-release",
            "workflow-rc-cut-GATES-workflow-prod-release",
            "workflow-prod-release-PROMOTED_FROM-workflow-stage-release",
            "gha-stage-promoter-EXECUTES-workflow-prod-release",
        ],
        "glow_color": "#2563eb",
        "narrative": (
            "The release lifecycle is the journey software takes from "
            "'developers wrote it' to 'customers are using it'. It has "
            "multiple stages, each one more serious than the last:\n\n"
            "1. Nightly builds: Every night, the system automatically "
            "rebuilds all components to make sure everything still works "
            "together. This catches problems early — like a daily health "
            "check.\n"
            "2. Stage release: The software is deployed to a staging "
            "environment (a copy of the real thing) where the QE "
            "(Quality Engineering) team tests it. Security policies are "
            "checked but with some flexibility.\n"
            "3. Code freeze: At this point, no new features are allowed "
            "— only bug fixes. This stabilizes the software for release.\n"
            "4. Release Candidate (RC): A stage release that is promoted "
            "as 'this might be the final version'. QE does intensive testing. "
            "If bugs are found, a new RC is created.\n"
            "5. Production release: The final version passes the strictest "
            "security checks and is shipped to customers.\n\n"
            "Each stage acts as a gate — problems caught at stage are much "
            "cheaper to fix than problems found after customers are using "
            "the software."
        ),
    },
    "dependency_management": {
        "title": "Dependency Management",
        "triggers": [
            "renovate", "mintmaker", "dependency",
            "task bundle", "sha update",
        ],
        "nodes": [
            "automation-renovate",
            "automation-mintmaker",
            "build-definitions",
            "konflux-central",
            "pipeline-container-build",
            "gha-run-renovate",
        ],
        "edges": [
            "automation-renovate-UPDATES-pipeline-container-build",
            "automation-mintmaker-BUILT_ON-automation-renovate",
            "automation-mintmaker-CREATES_PR_FOR-rhods-operator",
            "automation-mintmaker-CREATES_PR_FOR-rhoai-build-config",
            "gha-run-renovate-RUNS-automation-renovate",
        ],
        "glow_color": "#7c3aed",
        "narrative": (
            "Renovate and MintMaker are automation tools that keep "
            "dependencies (external libraries and tools your code uses) "
            "up-to-date across all repositories.\n\n"
            "Renovate focuses on pipeline infrastructure: the build "
            "pipelines reference specific versions of Tekton tasks (the "
            "individual steps like 'clone code', 'build image', 'scan for "
            "vulnerabilities') using SHA digests (unique fingerprints). When "
            "a new version of a task is published in build-definitions, "
            "Renovate detects the change and creates PRs to update the "
            "pinned SHA in every pipeline that uses it.\n\n"
            "MintMaker extends Renovate to handle component-level "
            "dependencies: Go modules, Python packages, RPM packages "
            "(Red Hat-packaged software), and container base images. It "
            "runs every 4 hours, scans all component repositories for "
            "outdated dependencies, and creates PRs to update them.\n\n"
            "Without these tools, a developer would need to manually check "
            "and update versions across 100+ repositories — a process that "
            "would take days and inevitably lead to missed updates and "
            "version inconsistencies."
        ),
    },
}

_COMPILED_PATTERNS = None


def _compile_patterns():
    global _COMPILED_PATTERNS
    if _COMPILED_PATTERNS is not None:
        return _COMPILED_PATTERNS
    patterns = []
    for key, concept in CONCEPT_PATHS.items():
        for trigger in concept["triggers"]:
            patterns.append((re.compile(trigger, re.IGNORECASE), key))
    _COMPILED_PATTERNS = patterns
    return patterns


def detect_concept(message: str) -> str | None:
    """Return concept key if message matches a teaching query, else None."""
    patterns = _compile_patterns()
    for pattern, key in patterns:
        if pattern.search(message):
            return key
    return None


def needs_dynamic_edges(concept_key: str) -> bool:
    """Return True if the concept requires live edge data for expansion."""
    concept = CONCEPT_PATHS.get(concept_key)
    return bool(concept and concept.get("edge_labels"))


def get_highlight(concept_key: str, all_edges: list[dict] | None = None) -> dict | None:
    """Build highlight payload for a concept.

    If ``all_edges`` is provided and the concept defines ``edge_labels``,
    dynamically includes all edges with those labels (e.g. all NUDGES edges
    between components).
    """
    concept = CONCEPT_PATHS.get(concept_key)
    if not concept:
        return None

    nodes = list(concept["nodes"])
    edges = list(concept.get("edges", []))

    if all_edges and concept.get("edge_labels"):
        labels = set(concept["edge_labels"])
        for e in all_edges:
            label = e.get("label", "")
            if label in labels:
                eid = e.get("id", "")
                if eid and eid not in edges:
                    edges.append(eid)
                src = e.get("source", "")
                tgt = e.get("target", "")
                if src and src not in nodes:
                    nodes.append(src)
                if tgt and tgt not in nodes:
                    nodes.append(tgt)

    return {
        "nodes": nodes,
        "edges": edges,
        "dim_others": True,
        "glow_color": concept.get("glow_color", "#f97316"),
        "label": concept["title"],
    }


def get_concept_narrative(concept_key: str) -> str | None:
    """Return the teaching narrative for a concept."""
    concept = CONCEPT_PATHS.get(concept_key)
    return concept["narrative"] if concept else None


def list_concepts() -> list[dict]:
    """Return available concepts for suggestion chips."""
    return [
        {"key": k, "title": v["title"]}
        for k, v in CONCEPT_PATHS.items()
    ]
