# Phase 1.2: Conforma AI Analysis

## Overview
Extend AI analysis to support Conforma (Enterprise Contract) compliance violations in addition to build failures.

## Key Differences: Build Failures vs Conforma

| Aspect | Build Failures | Conforma Violations |
|--------|---------------|---------------------|
| **Root Cause** | Code/config bugs | Policy non-compliance |
| **Evidence** | Build logs, commit diffs | Violation details, SBOM, policy rules |
| **Fix Type** | Code changes | Policy exceptions or compliance fixes |
| **Tone** | "What broke?" | "What's not compliant?" |
| **Auto-fix** | Sometimes | Rarely (needs approval) |

## Implementation Steps

### 1. Database Schema
- Add `conforma_result_id` to `ai_analysis` table
- Make `build_failure_id` NULLABLE
- Add CHECK constraint: exactly one ID must be set

### 2. ConformaAnalyzer Class
- Similar to BuildFailureAnalyzer
- System prompt with 14 known patterns from comforma.pdf
- Different evidence format (violations, not logs)

### 3. Repository Updates
- AIAnalysisRepository handles both types
- Query methods accept optional `result_type` parameter

### 4. CLI Integration
- `/analyze` skill detects type automatically
- `ic ai analyze conforma <component>`
- Number shortcuts already work (e.g., `./ic 8`)

### 5. Export Templates
- Jira template for Conforma violations
- Include policy exception request process

## Known Conforma Patterns (14 total)

1. **hermetic_task.hermetic** - Build not hermetic
2. **tasks.unpinned_task_reference** - Task not pinned to sha
3. **attestation_task_bundle.trusted_task** - Untrusted build tasks
4. **sbom_spdx.allowed_package_sources** - Packages from disallowed sources
5. **sbom_cyclonedx.allowed_sigstore_keys** - Signing key not allowed
6. **sbom_spdx.mismatched_rpm_versions** - RPM version mismatch across arches
7. **sbom_spdx.unknown_repository_id** - Invalid RPM repo ID
8. **tasks.task_version_outdated** - Deprecated task version
9. **fips.fbc_fips_check** - FIPS check missing/failed
10. **labels.version_label_mismatch** - Version label doesn't match expected
11. **test.fbc_target_index_pruning** - FBC pruning check failed
12. (Additional patterns documented in system prompt)

Each pattern has:
- **Detection**: How to identify it
- **Root Cause**: Why it happens
- **Fix**: How to resolve
- **Exception Process**: When/how to request policy exception
