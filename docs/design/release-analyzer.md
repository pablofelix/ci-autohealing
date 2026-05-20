# Release Failure Analyzer

## Overview

Extend AI analysis to support **release pipeline failures** in addition to build failures and Conforma violations.

When a release pipeline fails (e.g., `verify-conforma` in the v3.4 prod release), the current `ic` CLI shows *that* it failed and *which* task failed, but not *why* in an actionable way. Engineers must manually:

1. Dig through KubeArchive logs to find specific violations
2. Cross-reference violation image refs against the operator bundle CSV in RHOAI-Build-Config
3. Cross-reference image mappings against the RPA in konflux-release-data
4. Determine whether the issue is a wrong image ref, a missing prod push, or an RPA typo
5. Figure out who owns the fix (RHOAI team vs RHAII team vs RelEng)

The existing build and conforma AI analyzers solve this exact pattern for their domains. This design extends the same architecture to release failures.

## Key Differences: Existing Analyzers vs Release Analyzer

| Aspect | Build Failures | Conforma Violations | **Release Failures** |
|--------|---------------|---------------------|----------------------|
| **Data source** | DB (`build_failures`) | DB (`conforma_results`) | **Live cluster + KubeArchive + APIs** |
| **Root cause** | Code/config bugs | Policy non-compliance | **Image mapping / cross-product deps** |
| **Evidence** | Build logs, commit diffs | Violation details, SBOM | **Pipeline logs, RPA, bundle CSV** |
| **Fix type** | Code changes | Policy exceptions | **RPA fixes, image promotions** |
| **Fix owner** | Component developer | RelEng + developer | **Cross-team (RHOAI, RHAII, RelEng)** |
| **Auto-fix** | Sometimes | Rarely | **Rarely (coordination needed)** |

## Architecture Overview

Context collection happens **entirely in Python**, reusing the same clients
that the build and conforma analyzers already use. The shell (`ic`) just
invokes the Python entry point and displays results — same as the other
analyzers.

```
                                    ic ai analyze release <name>
                                              |
                          +-------------------+-------------------+
                          |                                       |
                    Shell (ic)                         Python (analyze_release.py)
                    Just invokes:                               |
                    python3.11                     ReleaseFailureAnalyzer
                    analyze_release.py                          |
                    --release <name>               +------------+------------+
                    --namespace <ns>               |            |            |
                          |                  Collect ctx   LLM call    DB storage
                          |                        |            |            |
                          |              +---------+---------+  |     ai_analysis
                          |              |         |         |  |       table
                          |         KubernetesClient  |  GitHubClient
                          |         (oc CLI)     |         |
                          |              KubeArchiveClient  |
                          |              (REST)    GitLabClient
                          |                        (REST - NEW)
                          |                              |
                          |                         tool_use → Pydantic
                          |                              |
                          v                              v
                    Display results              Langfuse trace
                    (display_ai_analysis)
```

### Existing Python clients reused

| Client | Source | What it fetches |
|--------|--------|-----------------|
| `KubernetesClient` | `clients/kubernetes.py` | Release CR, Snapshot CR via `oc` CLI |
| `KubeArchiveClient` | `clients/kubearchive.py` | GC'd PipelineRun, TaskRun, pod logs |
| `GitHubClient` | `clients/github_client.py` | Bundle images from RHOAI-Build-Config |
| `GitLabClient` | `clients/gitlab_client.py` **(NEW)** | RPA mappings + EC policies from konflux-release-data |

## Data Flow — What Gets Collected

```
+---------------------------+     +---------------------------+     +---------------------------+
|     CLUSTER (oc CLI)      |     |    KUBEARCHIVE (REST)     |     |     GITLAB (REST API)     |
+---------------------------+     +---------------------------+     +---------------------------+
| Release CR                |     | PipelineRun (GC'd)        |     | RPA component mappings    |
|   .spec.snapshot          |     | TaskRun (verify-conforma)  |     |   (konflux-release-data)  |
|   .spec.releasePlan       |     | Pod logs (step-validate)   |     |                           |
|   .status.conditions[]    |     |   -> violation details     |     | EC Policy files           |
|   .status.managedProcessing|    |   -> UNAUTHORIZED errors   |     |   (prod + stage)          |
|                           |     |   -> TEST_OUTPUT results   |     |   -> exception rules      |
| Snapshot CR               |     +---------------------------+     +---------------------------+
|   .spec.components[]      |
|     .name                 |                                       +---------------------------+
|     .containerImage       |                                       |    GITHUB (REST API)      |
+---------------------------+                                       +---------------------------+
                                                                    | bundle/additional-images- |
                                                                    |   patch.yaml              |
                                                                    |   -> RELATED_IMAGE_* refs |
                                                                    |                           |
                                                                    | bundle/manifests/         |
                                                                    |   rhods-operator.csv.yaml |
                                                                    |   -> operator CSV images  |
                                                                    +---------------------------+
```

## Context Package (built by ReleaseFailureAnalyzer.collect_context)

```json
{
  "release_name": "acme-v2-0-prod-1778657574",
  "application": "acme-v2-0",
  "snapshot": "acme-v2-0-1778062796",
  "release_plan": "rhoai-onprem-v3-4-components-prod",
  "target": "prod",
  "type": "components",
  "created_at": "2026-05-13T07:48:34Z",

  "conditions": [
    {"type": "Released", "status": "False", "reason": "Failed", "message": "..."},
    {"type": "ManagedPipelineProcessed", "status": "False", "reason": "Failed",
     "message": "task verify-conforma failed: \"step-assert\" exited with code 1"}
  ],

  "pipeline": {
    "ref": "releng-tenant/managed-qmgwd",
    "failed_task": "verify-conforma",
    "test_output": {"successes": 53391, "failures": 6, "warnings": 5400, "result": "FAILURE"}
  },

  "logs": "level=error msg=\"failed to fetch image\" error=\"GET https://registry.redhat.io/v2/rhaii/vllm-cuda-rhel9/manifests/sha256:d3e57...: UNAUTHORIZED\"\n...",

  "snapshot_components": [
    {"name": "odh-dashboard-v3-4", "image": "quay.io/acme/odh-dashboard@sha256:abc..."},
    {"name": "acme-operator-bundle-v3-4", "image": "quay.io/acme/acme-operator-bundle@sha256:4dc4..."}
  ],

  "rpa_mappings": {
    "source": "konflux-release-data RPA rhoai-onperm-v3-4-components-prod.yaml",
    "components": [
      {"name": "odh-dashboard-v3-4", "registry_path": "registry.redhat.io/rhoai/odh-dashboard-rhel9"},
      {"name": "odh-llm-d-batch-gateway-gc-rhel9-v3-4", "registry_path": "registry.redhat.io/rhoai/odh-llm-d-batch-gateway-gc-rhel9"}
    ]
  },

  "bundle_images": {
    "source": "RHOAI-Build-Config bundle/additional-images-patch.yaml",
    "images": [
      {"name": "RELATED_IMAGE_RHAII_VLLM_CUDA_IMAGE", "value": "registry.stage.redhat.io/rhaii/vllm-cuda-rhel9:3.4.0@sha256:d3e57..."},
      {"name": "RELATED_IMAGE_RHAII_VLLM_GAUDI_IMAGE", "value": "registry.stage.redhat.io/rhaii/vllm-gaudi-rhel9:3.4.0@sha256:1adc5..."}
    ]
  },

  "ec_exceptions": ["rule1_name", "rule2_name"]
}
```

## Component Architecture

```
collectors/python/
  clients/
    kubearchive.py                # EXISTING — KubeArchive REST client
    kubernetes.py                 # EXISTING — oc CLI wrapper
    github_client.py              # EXISTING — GitHub REST client
    gitlab_client.py              # NEW — GitLab REST client (RPA + EC policies)
  analyzers/
    build_failure_analyzer.py     # EXISTING
    conforma_analyzer.py          # EXISTING
    release_failure_analyzer.py   # NEW — follows same pattern, uses clients above
    models.py                     # Add ReleaseAnalysisResult
  analyze_release.py              # NEW — CLI entry point (like analyze_failures.py)

prompts/
  release_failure_analyzer.md     # NEW — system prompt with known patterns

ic                                # Add: ic ai analyze release <name>
                                  # (NO context collection — just invokes Python)

db/
  migrations/
    NNN_add_release_analysis.sql  # NEW — add release_name to ai_analysis
```

## How It Follows Existing Patterns

| Component | Build Analyzer | Conforma Analyzer | **Release Analyzer** |
|-----------|---------------|-------------------|---------------------|
| Source data | `build_failures` table | `conforma_results` table | Cluster + KubeArchive (live) |
| Tool schema | `record_analysis` | `record_conforma_analysis` | `record_release_analysis` |
| Categories | 7 (dependency, build, test...) | 14 (hermetic, unpinned...) | 8 (unmapped, rpa_typo...) |
| Pydantic model | `AnalysisResult` | `ConformaAnalysisResult` | `ReleaseAnalysisResult` |
| System prompt | `build_failure_analyzer.md` | `conforma_analyzer.md` | `release_failure_analyzer.md` |
| LLM provider | reuse | reuse | reuse |
| Langfuse | reuse | reuse | reuse |
| DB storage | `ai_analysis.build_failure_id` | `ai_analysis.conforma_result_id` | `ai_analysis.release_name` |
| Display | `display_ai_analysis` | `display_ai_analysis` | `display_ai_analysis` (reuse) |
| Pattern lib | `error_patterns` (type=build) | `error_patterns` (type=conforma) | `error_patterns` (type=release) |

## Tool Schema: `record_release_analysis`

```python
RELEASE_ANALYSIS_TOOL = {
    'name': 'record_release_analysis',
    'description': 'Record the analysis of a release pipeline failure',
    'input_schema': {
        'type': 'object',
        'properties': {
            'root_cause': {
                'type': 'string',
                'description': 'What caused the release to fail'
            },
            'failure_category': {
                'type': 'string',
                'enum': [
                    'unmapped_image',        # Image in CSV not in snapshot or target registry
                    'rpa_mapping_typo',      # Component name wrong in RPA
                    'cross_product_dependency', # Image from another product not in target registry
                    'missing_ec_exception',  # Violation needs policy exception
                    'validation_error',      # Release failed pre-pipeline (bad ReleasePlan, etc.)
                    'publish_failure',       # Pyxis or registry push failed
                    'access_denied',         # SA permissions issue
                    'infrastructure'         # Transient / platform issue
                ]
            },
            'confidence_score': { ... },
            'recommended_fix': { ... },
            'recommended_files': { ... },  # Files in Build-Config or konflux-release-data
            'can_auto_fix': { ... },
            'requires_human_review': { ... },
            'affected_images': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'List of image refs that caused the failure'
            },
            'owner_team': {
                'type': 'string',
                'description': 'Which team should fix this (e.g., RHOAI, RHAII, RelEng)'
            }
        },
        'required': ['root_cause', 'failure_category', 'confidence_score',
                     'recommended_fix', 'can_auto_fix']
    }
}
```

## Prompt Design — Known Release Failure Patterns

The system prompt (`prompts/release_failure_analyzer.md`) will document these patterns:

### Pattern 1: `olm.unmapped_references` — Image not accessible in target registry

```
Symptom:  "failed to fetch image" + "UNAUTHORIZED" in verify-conforma logs
Cause:    Operator CSV references an image that's not in the target registry
Examples: - Image points to registry.stage.redhat.io (not promoted to prod)
          - Image uses wrong digest (rebuild happened, digest changed)
          - Image from another product not yet released (RHAII vLLM)
Fix:      Update image reference in RHOAI-Build-Config bundle/additional-images-patch.yaml
          OR wait for owning team to push images to prod registry
Owner:    Depends on image — check if registry path starts with product prefix
          rhaii/ -> RHAII team, rhoai/ -> RHOAI team
```

### Pattern 2: RPA component name typo

```
Symptom:  "UNAUTHORIZED" for one image where the registry path looks wrong
Cause:    Component name in RPA doesn't match actual component name
Example:  RPA has "odh-llm-d-batch-gateway-gc-rhel9-v3-4" but component is
          "odh-llm-d-batch-gateway-gc-v3-4" (no rhel9 in name)
Fix:      Fix component name in konflux-release-data RPA YAML
Owner:    RelEng / Release team (Moulali)
```

### Pattern 3: Validation failure — ReleasePlan not found

```
Symptom:  "Validated: False" + "ReleasePlan ... not found"
Cause:    Release CR references a ReleasePlan that doesn't exist
Example:  Addon ReleasePlan not created for v3-4 GA
Fix:      Create the missing ReleasePlan or fix the reference
Owner:    Release team
```

### Pattern 4: Cross-product dependency not in target registry

```
Symptom:  Multiple UNAUTHORIZED errors for images from a different product namespace
Cause:    Operator bundles images from another product (RHAII, OpenShift, etc.)
          that hasn't been released to the target registry yet
Fix:      Coordinate with owning product team to push their images first
Owner:    Cross-team (identify from registry namespace)
```

### Pattern 5: Stage vs Prod registry mismatch

```
Symptom:  Image refs use registry.stage.redhat.io in prod release
Cause:    Bundle was built with stage registry refs, not updated for prod
Fix:      Update additional-images-patch.yaml to use registry.redhat.io
Owner:    RHOAI team
```

## Sequence Diagram — `ic ai analyze release <name>`

```
User              ic (shell)              Python (analyze_release.py)           LLM
 |                   |                              |                            |
 |-- ic ai analyze ->|                              |                            |
 |   release <name>  |                              |                            |
 |                   |-- python3.11 --------------->|                            |
 |                   |   analyze_release.py         |                            |
 |                   |   --release <name>           |                            |
 |                   |                              |                            |
 |                   |                  [Context Collection — Python]             |
 |                   |                              |                            |
 |                   |                  KubernetesClient.get_release()            |
 |                   |                  KubernetesClient.get_snapshot()           |
 |                   |                  KubeArchiveClient.get_pipelinerun()       |
 |                   |                  KubeArchiveClient.get_pod_logs()          |
 |                   |                  GitLabClient.get_file() [RPA]             |
 |                   |                  GitLabClient.get_file() [EC policy]       |
 |                   |                  GitHubClient.get_file() [bundle images]   |
 |                   |                              |                            |
 |                   |                  [Analysis — same as build/conforma]       |
 |                   |                              |                            |
 |                   |                              |-- build prompt ----------->|
 |                   |                              |   (system + context)       |
 |                   |                              |                            |
 |                   |                              |<- tool_use response ------|
 |                   |                              |   record_release_analysis  |
 |                   |                              |                            |
 |                   |                              |-- Pydantic validate       |
 |                   |                              |-- Save to ai_analysis     |
 |                   |                              |-- Langfuse trace          |
 |                   |                              |                            |
 |                   |<- JSON results (stdout) -----|                            |
 |                   |                              |                            |
 |<- Display --------|                              |                            |
 |   analysis        |                              |                            |
```

## Database Changes

```sql
-- Migration: add release analysis support to ai_analysis table
ALTER TABLE ai_analysis ADD COLUMN release_name VARCHAR(255);
CREATE INDEX idx_ai_release_name ON ai_analysis(release_name);

-- Add 'release' as valid failure_type in error_patterns
-- (already uses CHECK constraint, need to update)
ALTER TABLE error_patterns DROP CONSTRAINT IF EXISTS error_patterns_failure_type_check;
ALTER TABLE error_patterns ADD CONSTRAINT error_patterns_failure_type_check
    CHECK (failure_type IN ('build', 'conforma', 'release'));
```

## UX — Command Integration

```
ic ai analyze release <name>              Analyze a failed release
ic ai analyze release <name> --force      Re-analyze (overwrite previous)

ic describe release <name>                Now also shows AI analysis if available
```

### Example Output

```
$ ic ai analyze release acme-v2-0-prod-1778657574

Collecting release context...
  ✓ Release CR:     acme-v2-0-prod-1778657574 (Prod Components)
  ✓ Snapshot:       acme-v2-0-1778062796 (83 components)
  ✓ Pipeline logs:  managed-qmgwd (verify-conforma, 6 failures)
  ✓ RPA mappings:   rhoai-onperm-v3-4-components-prod.yaml
  ✓ Bundle images:  additional-images-patch.yaml (acme-3.4)

Analyzing with Claude...

AI Analysis:
  Category:       cross_product_dependency
  Confidence:     92%
  Owner:          RHAII team + RHOAI RelEng

  Root Cause:
  The verify-conforma task found 6 unmapped image references in the
  acme-operator-bundle-v3-4 CSV. 5 are RHAII vLLM images pointing at
  registry.stage.redhat.io which doesn't exist in the prod registry.
  1 is an RPA component name typo.

  Violation 1: RPA mapping error
  - Component "odh-llm-d-batch-gateway-gc-v3-4" is listed as
    "odh-llm-d-batch-gateway-gc-rhel9-v3-4" in the RPA, causing it
    to map to a wrong registry path.
  - Fix: Update component name in konflux-release-data RPA

  Violations 2-6: RHAII images not in prod
  - vllm-cuda-rhel9, vllm-gaudi-rhel9, vllm-rocm-rhel9,
    vllm-spyre-rhel9, vllm-cpu-rhel9 all reference
    registry.stage.redhat.io/rhaii/* digests
  - These need to be promoted to registry.redhat.io/rhaii/*
    by the RHAII team, then references updated in
    RHOAI-Build-Config bundle/additional-images-patch.yaml

  Recommended Fix:
  - Fix RPA typo: change "odh-llm-d-batch-gateway-gc-rhel9-v3-4" to
    "odh-llm-d-batch-gateway-gc-v3-4" in konflux-release-data

  - Coordinate with RHAII team to push vLLM images to
    registry.redhat.io/rhaii/*

  - Update bundle/additional-images-patch.yaml in RHOAI-Build-Config
    to use registry.redhat.io digests once images are promoted

  - Retry release after both fixes are in place

  Affected Images:
  - registry.redhat.io/rhoai/odh-llm-d-batch-gateway-gc-rhel9@sha256:3989a...
  - registry.redhat.io/rhaii/vllm-cuda-rhel9@sha256:d3e57...
  - registry.redhat.io/rhaii/vllm-gaudi-rhel9@sha256:1adc5...
  - registry.redhat.io/rhaii/vllm-rocm-rhel9@sha256:cfeac...
  - registry.redhat.io/rhaii/vllm-spyre-rhel9@sha256:5c0ed...
  - registry.redhat.io/rhaii/vllm-cpu-rhel9@sha256:562ae...

  Can Auto-Fix:   NO (requires cross-team coordination)
```

## GitLab API Paths Used

```
Base: https://GITLAB_INTERNAL_HOST/api/v4/projects/releng%2Fkonflux-release-data/repository/files

RPA (component mappings):
  config/CLUSTER_SHORT/product/ReleasePlanAdmission/rhoai/
    rhoai-onperm-v3-4-components-prod.yaml    <- Component prod mappings
    rhoai-onperm-v3-4-components-stage.yaml   <- Component stage mappings
    rhoai-onprem-v3-4-fbc-prod.yaml           <- FBC prod mappings
    rhoai-onprem-v3-4-fbc-stage.yaml          <- FBC stage mappings

EC Policies (exception rules):
  config/CLUSTER_SHORT/product/EnterpriseContractPolicy/
    registry-acme-prod.yaml                  <- Cluster-specific prod
    fbc-acme-prod.yaml                       <- Cluster-specific FBC prod

  config/common/product/EnterpriseContractPolicy/
    registry-standard.yaml                    <- Common prod
    fbc-standard.yaml                         <- Common FBC
```

## GitHub API Path Used

```
Base: https://api.github.com/repos/acme-org/RHOAI-Build-Config

Branch: acme-3.4 (derived from APPLICATION_NAME)

Files fetched:
  bundle/additional-images-patch.yaml         <- RELATED_IMAGE_* overrides
  bundle/manifests/rhods-operator.clusterserviceversion.yaml  <- Full CSV (fallback)
```

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `collectors/python/clients/gitlab_client.py` | CREATE | GitLab REST client for RPA + EC policy files |
| `collectors/python/analyzers/release_failure_analyzer.py` | CREATE | Main analyzer class (context collection + LLM) |
| `collectors/python/analyze_release.py` | CREATE | CLI entry point |
| `collectors/python/analyzers/models.py` | MODIFY | Add ReleaseAnalysisResult |
| `prompts/release_failure_analyzer.md` | CREATE | System prompt with known patterns |
| `ic` | MODIFY | Add `ic ai analyze release` (invokes Python, displays results) |
| `db/migrations/NNN_add_release_analysis.sql` | CREATE | DB schema change |

## Implementation Steps

### 1. Database Schema
- Add `release_name` column to `ai_analysis` table (nullable, like `conforma_result_id`)
- Add index `idx_ai_release_name` for lookup
- Update `error_patterns` CHECK constraint to include `'release'`

### 2. Pydantic Model
- Add `ReleaseAnalysisResult` to `analyzers/models.py`
- 8 failure categories: `unmapped_image`, `rpa_mapping_typo`, `cross_product_dependency`, `missing_ec_exception`, `validation_error`, `publish_failure`, `access_denied`, `infrastructure`
- Extra fields: `affected_images` (list), `owner_team` (string)
- Reuse existing validators (`round_confidence`, `validate_files`, `validate_not_placeholder`)

### 3. GitLab Client
- Create `clients/gitlab_client.py` — small REST client
- `get_file(project, path, ref)` — fetch raw file content from GitLab API
- `list_directory(project, path, ref)` — list files in a directory
- Uses `GITLAB_TOKEN` from env for auth

### 4. System Prompt
- Create `prompts/release_failure_analyzer.md`
- Document 5 known patterns (see Known Patterns section below)
- Confidence scoring guidelines
- Evidence citation rules (same as build/conforma analyzers)

### 5. Python Analyzer
- Create `analyzers/release_failure_analyzer.py` — main class
  - `collect_context()` — gathers data from all 4 sources (cluster, KubeArchive, GitLab, GitHub)
  - `build_analysis_prompt()` — formats context into LLM prompt
  - `parse_analysis_response()` — Pydantic validation
  - `analyze_release()` — orchestrates: context → prompt → LLM → parse → DB → Langfuse
- Create `analyze_release.py` — CLI entry point (thin shim like `analyze_failures.py`)

### 6. Shell Integration
- Add `ic ai analyze release <name>` command
- Just invokes `python3.11 analyze_release.py --release <name> --namespace $NAMESPACE`
- Parses JSON output from Python and displays using `display_ai_analysis`
- Add to `ic` usage text

### 7. Testing
- Test with live v3.4 failed release: `acme-v2-0-prod-1778657574`
- Verify context collection from all 4 sources
- Verify LLM analysis matches the known root cause (6 violations, RPA typo + RHAII images)
