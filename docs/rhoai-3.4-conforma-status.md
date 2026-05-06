# RHOAI 3.4 Conforma Violations Status

Date: 2026-04-28

Sources:
- Konflux monitoring DB (acme-v2-0 application)
- conforma-reporter acme-3.4 branch: https://github.com/acme-org/conforma-reporter/blob/acme-3.4/
- conforma-reporter CSV: https://github.com/acme-org/conforma-reporter/blob/acme-3.4/latest/conforma-violations-report.csv
- Reporter workflow: https://github.com/acme-org/conforma-reporter/actions/workflows/conforma-reporter.yaml
- GitLab policy (prod): https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_SHORT/product/EnterpriseContractPolicy/registry-acme-prod.yaml
- GitLab policy (prod FBC): https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_SHORT/product/EnterpriseContractPolicy/fbc-acme-prod.yaml
- GitLab policy (stage): https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_SHORT/product/EnterpriseContractPolicy/registry-acme-stage.yaml
- GitLab policy (stage FBC): https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_SHORT/product/EnterpriseContractPolicy/fbc-acme-stage.yaml

## Exception Sources

There are two independent exception systems:
- **GitHub conforma-reporter**: suppresses violations from the CSV report only (no enforcement impact)
- **GitLab konflux-release-data**: actual Conforma policy enforcement in PipelineRuns (volatileConfig.exclude)

A component needs exceptions in BOTH sources to be fully covered.

## Summary

- Components with violations: 9
- Total violations: 296
- Exception status: 5 fully excepted (YES), 3 partially excepted, 1 unknown
- JIRAs filed: PROJECT-59965, PROJECT-59982, PROJECT-59984, PROJECT-59987

## Violations by Component

### 1. acme-fbc-fragment-v3-4

- Rule: test.no_failed_tests
- Violations: 5 (in reporter), 17 (in Konflux DB, includes per-arch)
- Exception: NO - not covered
- Status: NEW - needs JIRA
- Source: FBC fragment build pipeline
- Details: The task "fbc-target-index-pruning-check" from the build pipeline reports a failed test on all arch images (amd64, arm64, ppc64le, s390x, and index)
- AI Analysis: not yet run
- Reporter: https://github.com/acme-org/conforma-reporter/blob/acme-3.4/latest/conforma-violations-report.csv

### 2. acme-autorag-v3-4

- Rule: sbom_spdx.allowed_package_sources
- Violations: 60
- Exception: YES (github + gitlab)
- GitHub: 15 huggingface file exceptions individually listed
- GitLab: sbom_spdx.allowed_package_sources excepted in policy
- Status: Fully covered in both sources
- JIRA: PROJECT-59987
- Source: https://github.com/acme-org/pipelines-components (ca1a585a)
- Details: Hermetic build fetches files from huggingface.co (docling-project/docling-layout-heron, docling-project/docling-models). 15 unique files x 4 arches = 60 violations.
- AI Analysis: 93% confidence, policy_package_source. Fix: vendor docling models to Red Hat-approved repo or request exception.

### 3. odh-spark-operator-v3-4

- Rule: rpm_repos.ids_known
- Violations: 40
- Exception: PARTIAL (github) — 22 per-package entries in GitHub, no GitLab policy exception
- GitHub: 22 per-package entries covering affected RPMs
- GitLab: No exception for rpm_repos.ids_known — violations still appear in PipelineRuns
- Status: Suppressed from CSV report but still fires in pipelines
- JIRA: PROJECT-59982
- Source: https://github.com/acme-org/spark-operator
- Details: RPM packages from ubi-9-appstream-rpms, ubi-9-baseos-rpms, and epel repos are not in the allowed list. Fix: rename repo IDs to include architecture (e.g. ubi-9-baseos-rpms → ubi-9-for-x86_64-baseos-rpms). tini from epel is a separate case (legal agreement needed).
- Expiring exceptions: hermetic_task.hermetic expires 2026-05-01, base_image_registries expires 2026-05-04

### 4. rhai-on-xks-chart-v3-4

- Rule: multiple (base_image_registries, cve, labels, sbom, slsa, test, trusted_task)
- Violations: 17
- Exception: NO blanket exception for these rules
- Status: Helm chart image missing fundamental Konflux compliance infrastructure
- Source: https://github.com/acme-org/RHOAI-Build-Config (0e0e6d14)
- Details: Missing required labels (com.redhat.component, description, distribution-scope, io.k8s.description, io.k8s.display-name, io.openshift.tags, name, release, summary, url, vcs-ref, vcs-type, vendor, version), missing SBOM, missing CVE scan, missing SLSA provenance. This is a chart image, not a standard container.
- AI Analysis: 82% confidence, config_error. Full pipeline compliance remediation needed.
- Reporter: NOT in reporter (different scenario or not in scope)

### 5. rhai-on-openshift-chart-v3-4

- Rule: multiple (aggregated from all components in chart scenario)
- Violations: 11144
- Exception: various
- Status: This is the CHART-LEVEL scenario (conforma-registry-acme-chart-prod-v3-4), not single-component. Aggregates ALL components in the acme-v2-0 release.
- Details: The high violation count is because this scenario checks ALL images in the snapshot (not just one component). Most violations are the same per-component issues repeated across all images.
- AI Analysis: 72% confidence, policy_version_label
- Reporter: NOT in reporter (reporter only covers single-component scenarios)

### 6. odh-trustyai-service-operator-v3-4

- Rule: trusted_task.trusted
- Violations: 5
- Exception: YES - "trusted_task.trusted:<NAMELESS>" is in exceptions
- Status: COVERED - should clear when exceptions are applied
- Source: quay.io/acme/odh-trustyai-service-operator-rhel9
- Details: 5 per-arch images each have 1 violation for untrusted task (arm64, amd64, s390x, ppc64le, index)
- AI Analysis: not yet run

### 7. odh-mod-arch-eval-hub-v3-4

- Rule: trusted_task.trusted
- Violations: 5
- Exception: YES - "trusted_task.trusted:<NAMELESS>" is in exceptions
- Status: COVERED - should clear when exceptions are applied
- Source: https://github.com/acme-org/odh-dashboard (9311a43f)
- Details: Same pattern as trustyai - untrusted task across all arches
- AI Analysis: not yet run

### 8. odh-ta-lmes-driver-v3-4

- Rule: trusted_task.trusted
- Violations: 5
- Exception: YES - "trusted_task.trusted:<NAMELESS>" is in exceptions
- Status: COVERED - should clear when exceptions are applied
- Source: quay.io/acme/odh-ta-lmes-driver-rhel9
- Details: Same pattern - untrusted task across all arches
- AI Analysis: not yet run

### 9. odh-workbench-jupyter-datascience-cpu-py312-v3-4

- Rule: rpm_packages.unique_version
- Violations: 4
- Exception: PARTIAL (github) — GitHub has per-package exceptions, GitLab has no policy exception
- GitHub: openssl, openssl-libs, python3, python3-libs all excepted
- GitLab: No exception — violations still appear in PipelineRuns
- Status: Suppressed from CSV report but still fires in pipelines
- JIRA: PROJECT-59965
- Source: https://github.com/acme-org/notebooks (818fd1d)
- Details: s390x has openssl-3.2.2-6.el9_5.1 while other arches have 3.2.2-7.el9_6.2. Same for python3 (3.9.21-2.el9_6.2 vs 3.9.21-2.el9_6.4). Root cause: s390x base content not synced. Rebuild won't fix until RHEL content is updated.
- AI Analysis: 95% confidence, policy_rpm_repository.

### 10. odh-pipelines-components-v3-4

- Rule: hermetic_task.hermetic
- Violations: 4
- Exception: YES (github + gitlab)
- GitHub: "hermetic_task.hermetic" blanket-excepted
- GitLab: hermetic_task.hermetic excepted in policy
- Status: Fully covered in both sources
- JIRA: PROJECT-59984
- Source: quay.io/acme/odh-pipelines-components-rhel9
- Details: buildah-remote-oci-ta task invoked without HERMETIC=true parameter. 4 per-arch images affected.
- AI Analysis: 95% confidence, policy_hermetic_build. Fix: add HERMETIC=true to pipeline spec.

## Cross-Reference: DB vs Reporter

| Component | In Konflux DB | In Reporter | Notes |
|-----------|:---:|:---:|-------|
| acme-fbc-fragment-v3-4 | YES | YES | test.no_failed_tests - NEW |
| acme-autorag-v3-4 | YES | NO* | sbom_spdx - exceptions may filter from reporter |
| odh-spark-operator-v3-4 | NO | YES | rpm_repos - not in our monitoring scope |
| rhai-on-xks-chart-v3-4 | YES | NO | Chart image - different scope |
| rhai-on-openshift-chart-v3-4 | YES | NO | Chart-level scenario - different scope |
| odh-trustyai-service-operator-v3-4 | YES | YES | trusted_task - COVERED |
| odh-mod-arch-eval-hub-v3-4 | YES | YES | trusted_task - COVERED |
| odh-ta-lmes-driver-v3-4 | YES | YES | trusted_task - COVERED |
| odh-workbench-jupyter-datascience-cpu-py312-v3-4 | YES | YES | rpm_packages - COVERED |
| odh-pipelines-components-v3-4 | YES | YES | hermetic_task - COVERED |

## Action Items

### JIRAs Filed (pending team action)

1. PROJECT-59965 - odh-workbench-jupyter-datascience-cpu-py312-v3-4 - rpm_packages.unique_version — pinged @redhat-ai-notebooks
2. PROJECT-59982 - odh-spark-operator-v3-4 - rpm_repos.ids_known — pinged @rhai-data-processing
3. PROJECT-59984 - odh-pipelines-components-v3-4 - hermetic_task.hermetic — pinged @openshift-ai-devtestops-ic
4. PROJECT-59987 - acme-autorag-v3-4 - sbom_spdx.allowed_package_sources — pinged @openshift-ai-devtestops-ic

### Fully Excepted (both GitHub + GitLab)

5. acme-autorag-v3-4 - YES
6. odh-trustyai-service-operator-v3-4 - YES
7. odh-mod-arch-eval-hub-v3-4 - YES
8. odh-ta-lmes-driver-v3-4 - YES
9. odh-pipelines-components-v3-4 - YES

### Partially Excepted (GitHub only — still fires in PipelineRuns)

10. odh-workbench-jupyter-datascience-cpu-py312-v3-4 - PARTIAL (github)
11. odh-spark-operator-v3-4 - PARTIAL (github)

### Partially Excepted (GitLab only — still in reporter CSV)

12. acme-fbc-fragment-v3-4 - PARTIAL (gitlab)
13. rhai-on-xks-chart-v3-4 - PARTIAL (gitlab)

### Expiring Exceptions (within 3 weeks)

- 2026-04-30 (1d): test.no_failed_tests, trusted_task.trusted, tasks.required_tasks_found, test.no_erred_tests
- 2026-05-01 (2d): hermetic_task.hermetic (spark-operator)
- 2026-05-04 (5d): base_image_registries.base_image_permitted (spark-operator)
- 2026-05-15 (16d): hermetic_task.hermetic (trustyai-nemo-guardrails), sbom_spdx.allowed_package_sources, fbc-fips-check

### Chart-Level (different scope, informational)

14. rhai-on-openshift-chart-v3-4 - 180 violations - chart-level scenario (odh-model-controller)
