# RHOAI 3.4 Conforma - JIRA Export Templates

Date: 2026-04-27
Generated with: ./ic export conforma <component> jira

These are Jira markup templates ready to paste into JIRA tickets.
Each section is the output of the ic export tool for one component.

---

## 1. rhai-on-openshift-chart-v3-4

```
h2. Conforma Policy Violation: rhai-on-openshift-chart-v3-4

||Field||Value||
|Component|rhai-on-openshift-chart-v3-4|
|Status|Policy Violation|
|Scenario|conforma-registry-acme-chart-prod-v3-4|
|Violations|11144 violations, 7593 warnings, 52050 successes|
|PipelineRun|[conforma-registry-acme-chart-prod-v3-4-rc9nf|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-chart-prod-v3-4-rc9nf/logs]|
|Snapshot|acme-v2-0-20260424-030955-000 |
|First Seen|2026-04-27 06:34|
|Last Seen|2026-04-27 08:17|
|Occurrences|1 times|

h3. AI Analysis (72% confidence)

||Category||policy_version_label||
|Can Auto-Fix|No|
|Requires Review|Yes|

h4. Root Cause

The component rhai-on-openshift-chart-v3-4 (quay.io/acme/rhai-on-openshift-chart@sha256:29d54fe7309869da3e68f2ca078f23f507823f612efa2f2ce3a58fbd44ae6fbf) shows 1 violation, 0 warnings, and 0 successes — an unusual pattern where no checks passed at all. A result of 0 successes alongside 0 warnings and 1 violation strongly suggests a version label mismatch (policy rule labels.version_label_mismatch). This pattern occurs when a container image is built before the version label was updated for the new release cycle — in this case v3.4 — and Conforma cannot reconcile the image's embedded label against the expected label from the conforma-reporter configuration. The broader snapshot acme-v2-0-20260424-030955-000 shows 11,144 total violations across many components, with the majority concentrated on architecture-specific image variants (amd64, arm64, ppc64le, s390x) of workbench images. This is consistent with mismatched RPM versions across multi-arch builds (sbom_spdx.mismatched_rpm_versions), which is a known timing issue where some architectures complete their build after a new RPM version is published. The acme-fbc-fragment-v3-4 per-arch variants each show exactly 2 violations while the index image itself shows 0 — this is a known pattern consistent with FBC FIPS check (fips.fbc_fips_check) being absent on push builds, which is expected and generally safe to ignore until nightly builds confirm actual failures. The odh-wb-jupyter-pytorch-llmcompressor-cuda-py312 amd64 variant stands out with 392 violations — far exceeding the ~20-130 violations seen in other workbench images. This outlier count is consistent with disallowed package sources (sbom_spdx.allowed_package_sources), where a large number of Python packages were fetched from unapproved sources (e.g., PyPI packages not covered by RHOAI agreements, or external model repositories) during the hermetic build prefetch phase.

h4. Recommended Fix

- For rhai-on-openshift-chart-v3-4 (0 successes, 1 violation): Trigger a fresh rebuild of this component in Konflux. The 0-successes result strongly suggests a version label mismatch where the image predates the v3.4 label configuration. A rebuild will pick up the current version label from the conforma-reporter config automatically — no code change needed. - For acme-fbc-fragment per-arch variants (2 violations each on amd64, ppc64le, arm64, s390x): Check whether the violations are fips.fbc_fips_check failures. If so, these are expected on push builds and can be ignored. Verify by checking the nightly Conforma build logs for the fbc-target-index-pruning-check task — search for "!FAILURE!" in those logs before escalating. - For workbench image arch variants showing 20-131 violations (odh-workbench-jupyter-minimal-*, odh-workbench-jupyter-pytorch-*, odh-workbench-jupyter-tensorflow-*, odh-workbench-jupyter-trustyai-*): These are likely sbom_spdx.mismatched_rpm_versions violations caused by multi-arch build timing differences. Rebuild all affected workbench components together in a coordinated Konflux rebuild so all architectures use the same RPM snapshot. No code change is needed. - For odh-wb-jupyter-pytorch-llmcompressor-cuda-py312-v3-4-amd64 (392 violations — the extreme outlier): Investigate the SBOM for this specific image to determine whether the violations are sbom_spdx.allowed_package_sources or sbom_cyclonedx.allowed_sigstore_keys. Pull the violation details from the Conforma PipelineRun logs for this component specifically. If packages are being fetched from unapproved sources (e.g., huggingface.co, unapproved PyPI), you will need to either vendor those packages into the source image or request a policy exception. - If a policy exception is needed for any component (especially the llmcompressor image): Create a JIRA at https://JIRA_CREATE_ISSUE_URL explaining the business justification, then attach it to an exception merge request at https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_DOMAIN/product/EnterpriseContractPolicy/fbc-acme-prod.yaml and ping @owatkins in #wg-3_0-openshift-ai-release Slack for ProdSec approval. - To get more precise violation detail per component: Check the full Conforma PipelineRun logs for conforma-registry-acme-chart-prod-v3-4-rc9nf in Konflux and filter by component name to see the exact rule names (e.g., sbom_spdx.allowed_package_sources, sbom_spdx.mismatched_rpm_versions) triggering each violation count. This will allow targeted fixes rather than blanket exception requests.

*Files/Configs to modify:*
{code}
{}
{code}

h5. Policy Exception Process

If the recommended fix isn't immediately feasible:
# File JIRA at: [Create Issue|https://JIRA_CREATE_ISSUE_URL]
# Explain business justification and timeline for proper fix
# Create MR to exception file: {{konflux-release-data/.../fbc-acme-prod.yaml}}
# Add exception terms (check AI analysis for specific package URLs)
# Ping @owatkins (ProdSec) in #wg-3_0-openshift-ai-release for approval

h3. Context & Resources

*Repository:* https://github.com/acme-org/RHOAI-Build-Config
*Commit:* [a2ede697|https://github.com/acme-org/RHOAI-Build-Conf/commit/a2ede69714d0eea437b6cd3b82abb5435847a02b]

*Useful Links:*
* [Konflux PipelineRun|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-chart-prod-v3-4-rc9nf/logs]
* [Quay Repository|https://quay.io/repository/rhoai/rhai-on-openshift-chart-v3-4]
* [Enterprise Contract Docs|https://enterprisecontract.dev/]

h3. Suggested Next Steps

{panel:bgColor=#FFF4E6|title=Compliance Resolution}
This violation requires investigation:
1. Review the violation details in Konflux UI
2. Determine if fix or policy exception is appropriate
3. If exception needed, follow policy exception process above
{panel}

---
_Automated analysis by claude-sonnet-4-6  (2026-04-27 07:28:43)._
_Confidence: 72% | Category: policy_version_label_
```

---

## 2. acme-autorag-v3-4

```
h2. Conforma Policy Violation: acme-autorag-v3-4

||Field||Value||
|Component|acme-autorag-v3-4|
|Status|Policy Violation|
|Scenario|conforma-registry-acme-prod-v3-4-single-component|
|Violations|60 violations, 100 warnings, 631 successes|
|PipelineRun|[conforma-registry-acme-prod-v3-4-single-component-2bqp8|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-2bqp8/logs]|
|Snapshot|acme-v2-0-20260423-205409-000 |
|First Seen|2026-04-24 06:07|
|Last Seen|2026-04-27 08:15|
|Occurrences|1 times|

h3. AI Analysis (93% confidence)

||Category||policy_package_source||
|Can Auto-Fix|No|
|Requires Review|Yes|

h4. Root Cause

The acme-autorag-v3-4 component is violating the sbom_spdx.allowed_package_sources policy because the hermetic build is fetching files directly from huggingface.co at build time, which is not an approved package source. Violation rule `sbom_spdx.allowed_package_sources` reports: packages from two docling model repositories on HuggingFace are being fetched during the hermetic build — specifically from `https://huggingface.co/docling-project/docling-layout-heron/` and `https://huggingface.co/docling-project/docling-models/resolve/v2.3.0/`. The affected files include `.gitattributes`, `.gitignore`, `README.md`, and `config.json`, as well as likely additional model weight files (15 violations appear on each of the amd64, s390x, arm64, and ppc64le architecture images). The Hermeto dependency prefetcher, used during hermetic builds, captured these external references and recorded them in the SBOM. Conforma then flagged them because `huggingface.co` is not on the approved package source list for RHOAI builds. The multi-arch index image (acme-autorag-v3-4, sha256:6041) shows 0 violations because it is the manifest list — the per-arch images (amd64, s390x, arm64, ppc64le) each independently show the same 15 violations, confirming this is a build-time fetch issue affecting all architectures equally. The docling-layout-heron and docling-models projects are AI model repositories. Fetching model artifacts (including metadata files like .gitattributes and config.json) directly from HuggingFace at build time bypasses Red Hat's legal and security vetting processes for third-party software, which is what this policy exists to enforce.

h4. Recommended Fix

- Vendor the docling model files into a Red Hat-approved internal repository instead of fetching from huggingface.co at build time. The violations show files fetched from `https://huggingface.co/docling-project/docling-layout-heron/` and `https://huggingface.co/docling-project/docling-models/resolve/v2.3.0/`. These models should be mirrored to an internal artifact store (e.g., Red Hat's internal Nexus or a source container image) and the build should reference the internal location instead. - Check whether the docling-models or docling-layout-heron models are available as Red Hat RPM packages or via an already-approved internal mirror. Consult the RHOAI approved package spreadsheet at https://docs.google.com/spreadsheets/d/1o2j87H-k33eBsDcxR4oeqpNJJZe_TqHarnEBw-PqepM/edit?gid=1354667519 to see if any legal agreement already covers these packages. - If vendoring or an approved mirror is not feasible before the release deadline, request a policy exception. Create a JIRA issue at https://JIRA_CREATE_ISSUE_URL explaining why docling-layout-heron and docling-models must be fetched from HuggingFace and why no approved alternative currently exists. - For the exception request, add exclusion entries to the exception file at https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_DOMAIN/product/EnterpriseContractPolicy/fbc-acme-prod.yaml. Each of the 15 violating package terms (one per fetched file) must be individually listed in the `exclude` section using the format `sbom_spdx.allowed_package_sources:pkg:generic/...`. The violation details include the exact term string to copy for each entry. - Ping @owatkins in the #wg-3_0-openshift-ai-release Slack channel to request ProdSec approval once the JIRA and exception MR are ready. Note that with 15 violations per arch across 4 architectures, the exception file will need all 15 unique package terms listed (they are the same across arches, so 15 unique entries should cover all 60 violations).

*Files/Configs to modify:*
{code}
{Dockerfile,hermeto.yaml,"fetch-deps configuration"}
{code}

h5. Policy Exception Process

If the recommended fix isn't immediately feasible:
# File JIRA at: [Create Issue|https://JIRA_CREATE_ISSUE_URL]
# Explain business justification and timeline for proper fix
# Create MR to exception file: {{konflux-release-data/.../fbc-acme-prod.yaml}}
# Add exception terms (check AI analysis for specific package URLs)
# Ping @owatkins (ProdSec) in #wg-3_0-openshift-ai-release for approval

h3. Context & Resources

*Repository:* https://github.com/acme-org/pipelines-components
*Commit:* [ca1a585a|https://github.com/acme-org/pipelines-components/commit/ca1a585aad0581b9f416f138c77ca895439a7a19]

*Useful Links:*
* [Konflux PipelineRun|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-2bqp8/logs]
* [Quay Repository|https://quay.io/repository/rhoai/acme-autorag-v3-4]
* [Enterprise Contract Docs|https://enterprisecontract.dev/]

h3. Suggested Next Steps

{panel:bgColor=#FFF4E6|title=Compliance Resolution}
This violation requires investigation:
1. Review the violation details in Konflux UI
2. Determine if fix or policy exception is appropriate
3. If exception needed, follow policy exception process above
{panel}

---
_Automated analysis by claude-sonnet-4-6  (2026-04-24 14:37:43)._
_Confidence: 93% | Category: policy_package_source_
```

---

## 3. acme-fbc-fragment-v3-4

```
h2. Conforma Policy Violation: acme-fbc-fragment-v3-4

||Field||Value||
|Component|acme-fbc-fragment-v3-4|
|Status|Policy Violation|
|Scenario|conforma-registry-acme-prod-v3-4-single-component|
|Violations|17 violations, 55 warnings, 606 successes|
|PipelineRun|[conforma-registry-acme-prod-v3-4-single-component-8wd77|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-8wd77/logs]|
|Snapshot|acme-v2-0-20260427-035033-000 |
|First Seen|2026-04-27 06:34|
|Last Seen|2026-04-27 08:17|
|Occurrences|1 times|

h3. AI Analysis (82% confidence)

||Category||policy_fips_check||
|Can Auto-Fix|No|
|Requires Review|Yes|

h4. Root Cause

The `tasks.required_tasks_found` violation is firing across all 5 architecture variants (amd64, arm64, ppc64le, s390x, and the multi-arch index) of `acme-fbc-fragment-v3-4` because neither `fbc-fips-check` nor `fbc-fips-check-oci-ta` appears in the PipelineRun attestation for this build (snapshot: acme-v2-0-20260424-131516-000, commit: e7299bfc9b04ca658040cfe34ee6a9a27d9b74be). This is a well-known and expected behaviour for FBC (File Based Catalog) components: the FIPS check task is intentionally disabled on CI push builds because it takes 2–4 hours to run. It is only executed on nightly builds. The violation is NOT an indication of a real FIPS compliance failure — it is Conforma flagging the *absence of the task record* in the attestation, not an actual FIPS test failure. Supporting evidence from the warnings: - A volatile exclude rule `test.no_erred_tests:fbc-fips-check-oci-ta` is already in place (expires 2026-05-15), confirming the team is aware the FIPS task is expected to be absent/erred on push builds. - A volatile exclude rule `test.no_failed_tests:fbc-fips-check-oci-ta` is also active, again confirming this is a known, managed exception. - The expired volatile rule `schedule.weekday_restriction` (expired 2026-04-20) should be cleaned up from the policy config to avoid confusion. Additionally, there are non-blocking warnings about the `prefetch-dependencies-oci-ta` task bundle being outdated (will be unsupported as of 2026-05-23) and a newer version being available (deadline 2026-06-08). These should be addressed proactively but are not the root cause of the violation failures.

h4. Recommended Fix

**Primary Violation (`tasks.required_tasks_found` — FIPS check missing): Likely a false positive for push builds.** **Step 1 — Verify this is expected (push build behaviour)** Check whether this PipelineRun (`conforma-fbc-acme-prod-v3-4-single-component-2qhxg`) was triggered by a push event rather than a nightly schedule. If so, this violation is expected — the FIPS check task is intentionally skipped on push builds because it takes 2–4 hours. **Step 2 — Check the nightly build** Before treating this as a real problem, check the most recent *nightly* Conforma run for `acme-fbc-fragment-v3-4`. If the nightly run passes (or shows actual FIPS task results), this push-build violation can safely be ignored. **Step 3 — If it IS appearing on nightly builds too** Then it is a real violation. The fix is to ensure `fbc-fips-check-oci-ta` is included in the build pipeline. Check the acme-fbc-fragment pipeline definition in konflux-central to confirm the task is present and not accidentally removed or conditionally skipped. **Step 4 — Extend the expiring volatile exception rules (action needed soon)** Two volatile exception rules are expiring in ~20 days (2026-05-15): - `test.no_erred_tests:fbc-fips-check-oci-ta` - `test.no_failed_tests:fbc-fips-check-oci-ta` If the FIPS task is still expected to fail/error on push builds after 2026-05-15, extend these rules in the exception file before they expire. File: https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/exceptions/fbc-acme-prod.yaml — ping @owatkins (ProdSec) for approval. **Step 5 — Clean up the expired volatile rule** Remove the expired rule `schedule.weekday_restriction` (expired 2026-04-20) from the policy config. It is no longer doing anything and is generating noise in the warning output. **Step 6 — Address the `prefetch-dependencies-oci-ta` deprecation warning (non-blocking, but act before 2026-05-23)** The `prefetch-dependencies-oci-ta` task bundle is outdated and will become unsupported on 2026-05-23. A newer version is available (`sha256:4736b695b658a0b304a122dc53836bb22484ff28f7fe112cc44d3c12566b5220`). Update the task bundle digest in konflux-central — check for open Renovate PRs at https://github.com/acme-org/konflux-central/pulls that may already have this update ready to merge. **If an exception is needed for `tasks.required_tasks_found:fbc-fips-check-oci-ta` (push builds)** - Add `tasks.required_tasks_found:fbc-fips-check-oci-ta` and/or `tasks.required_tasks_found:fbc-fips-check` to the `exclude` section of the policy config (as the violation description itself suggests) - File a JIRA at: https://JIRA_CREATE_ISSUE_URL - Reference the exception MR: https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_DOMAIN/product/EnterpriseContractPolicy/fbc-acme-prod.yaml - Explain that FIPS check runs only on nightly builds and is intentionally absent from push/CI builds due to runtime constraints (2–4 hour execution time) - Tag @owatkins in #wg-3_0-openshift-ai-release for approval

*Files/Configs to modify:*
{code}
{"konflux-central pipeline definition for acme-fbc-fragment",https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/exceptions/fbc-acme-prod.yaml,https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_DOMAIN/product/EnterpriseContractPolicy/fbc-acme-prod.yaml}
{code}

h5. Policy Exception Process

If the recommended fix isn't immediately feasible:
# File JIRA at: [Create Issue|https://JIRA_CREATE_ISSUE_URL]
# Explain business justification and timeline for proper fix
# Create MR to exception file: {{konflux-release-data/.../fbc-acme-prod.yaml}}
# Add exception terms (check AI analysis for specific package URLs)
# Ping @owatkins (ProdSec) in #wg-3_0-openshift-ai-release for approval

h3. Context & Resources

*Repository:* https://github.com/acme-org/RHOAI-Build-Config
*Commit:* [fb2a4a59|https://github.com/acme-org/RHOAI-Build-Conf/commit/fb2a4a59a218ab41a6a23ca95bf99c180863d3d4]

*Useful Links:*
* [Konflux PipelineRun|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-8wd77/logs]
* [Quay Repository|https://quay.io/repository/rhoai/acme-fbc-fragment-v3-4]
* [Enterprise Contract Docs|https://enterprisecontract.dev/]

h3. Suggested Next Steps

{panel:bgColor=#FFF4E6|title=Compliance Resolution}
This violation requires investigation:
1. Review the violation details in Konflux UI
2. Determine if fix or policy exception is appropriate
3. If exception needed, follow policy exception process above
{panel}

---
_Automated analysis by claude-sonnet-4-6  (2026-04-24 14:12:25)._
_Confidence: 82% | Category: policy_fips_check_
```

---

## 4. rhai-on-xks-chart-v3-4

```
h2. Conforma Policy Violation: rhai-on-xks-chart-v3-4

||Field||Value||
|Component|rhai-on-xks-chart-v3-4|
|Status|Policy Violation|
|Scenario|conforma-registry-acme-prod-v3-4-single-component|
|Violations|17 violations, 20 warnings, 117 successes|
|PipelineRun|[conforma-registry-acme-prod-v3-4-single-component-n8qb6|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-n8qb6/logs]|
|Snapshot|acme-v2-0-20260424-050229-000-3x |
|First Seen|2026-04-24 06:09|
|Last Seen|2026-04-27 08:17|
|Occurrences|1 times|

h3. AI Analysis (82% confidence)

||Category||config_error||
|Can Auto-Fix|No|
|Requires Review|Yes|

h4. Root Cause

The component `rhai-on-xks-chart-v3-4` appears to be a Helm chart packaged as a container image, but its build pipeline is missing foundational Konflux/Conforma compliance infrastructure entirely. Specifically: 1. **No SBOM produced** (`sbom.found`): The build pipeline does not generate an SBOM attestation (neither CycloneDX nor SPDX). This is a prerequisite for almost all other Conforma checks. 2. **No base image info** (`base_image_registries.base_image_info_found`): Because no SBOM exists, Conforma cannot determine what base image was used during the build. 3. **No CVE scan** (`cve.cve_results_found`) / **Missing required tasks** (`tasks.required_tasks_found`): The build pipeline is missing required Konflux tasks — specifically `clair-scan` or `roxctl-scan`. These security scanning tasks are mandatory in the Konflux pipeline definition. 4. **All required labels missing** (`labels.required_labels` — 11 violations): The container image is missing every required Red Hat label: `com.redhat.component`, `description`, `distribution-scope`, `io.k8s.description`, `name`, `release`, `url`, `vcs-ref`, `vcs-type`, `vendor`, and `version`. This strongly suggests the Dockerfile for this chart image was built without any of the standard Red Hat label scaffolding. The root cause is that `rhai-on-xks-chart-v3-4` was likely onboarded to Konflux without a properly configured Konflux-compliant build pipeline. It is missing the standard Konflux task bundle steps (SBOM generation, CVE scanning) and its Dockerfile/build process does not set required Red Hat container labels. This is a "blank slate" onboarding issue rather than a regression in an existing compliant component.

h4. Recommended Fix

This component needs a full Konflux pipeline compliance remediation across three areas. The quickest path forward is to address all three in parallel: --- ### 1. Add Required Labels to the Dockerfile (fixes 11 `labels.required_labels` violations) Add the following `LABEL` block to the component's Dockerfile (or equivalent build spec). These are mandatory Red Hat container labels: ```dockerfile LABEL com.redhat.component="rhai-on-xks-chart" \ name="rhoai/rhai-on-xks-chart" \ version="3.4" \ release="1" \ vendor="Red Hat, Inc." \ description="RHOAI Helm chart for deployment on XKS clusters" \ io.k8s.description="RHOAI Helm chart for deployment on XKS clusters" \ distribution-scope="public" \ url="https://github.com/acme-org/RHOAI-Build-Config" \ vcs-type="git" \ vcs-ref="0e0e6d14da890c5356634b90e035337615bc04c2" ``` Update `vcs-ref` dynamically at build time using Konflux's `git-clone` task output if possible. **File to modify**: The Dockerfile or equivalent build config in https://github.com/acme-org/RHOAI-Build-Config for `rhai-on-xks-chart-v3-4`. --- ### 2. Add Required Konflux Pipeline Tasks (fixes `sbom.found`, `cve.cve_results_found`, `base_image_registries.base_image_info_found`, `tasks.required_tasks_found`) The build pipeline for this component is missing mandatory Konflux task bundle steps. You need to ensure the Tekton PipelineRun includes: - **SBOM generation task** (e.g., `create-sbom`, `syft`, or the Konflux-standard `spdx-sbom-generator`) — resolves `sbom.found` and `base_image_registries.base_image_info_found` - **CVE scanning task** — either `clair-scan` or `roxctl-scan` — resolves `cve.cve_results_found` and `tasks.required_tasks_found` Check the Konflux standard pipeline template for reference: - https://github.com/konflux-ci/build-definitions/tree/main/pipelines Compare the current PipelineRun `conforma-registry-acme-prod-v3-4-single-component-n8qb6` against the standard Konflux Docker-build pipeline to identify which tasks are missing. If this component uses a custom pipeline (common for Helm chart images), it may need to be migrated to use the standard Konflux pipeline or have these tasks manually added. **File to modify**: The `.tekton/` pipeline YAML files in the component's repository. --- ### 3. If an Immediate Fix Isn't Feasible — Request a Policy Exception If this component is newly onboarded and not yet release-blocking, you can request a temporary exception while the pipeline is being brought into compliance: - **Create a JIRA**: https://JIRA_CREATE_ISSUE_URL - List all violated rules: `sbom.found`, `cve.cve_results_found`, `base_image_registries.base_image_info_found`, `tasks.required_tasks_found`, `labels.required_labels:*` - Explain: "Component rhai-on-xks-chart-v3-4 was recently onboarded without a fully compliant Konflux pipeline. Pipeline remediation is in progress (ETA: [date])." - **Attach to exception MR**: https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_DOMAIN/product/EnterpriseContractPolicy/fbc-acme-prod.yaml - **Ping for approval**: @owatkins in #wg-3_0-openshift-ai-release Slack --- ### Priority Order 1. Labels fix (low risk, pure Dockerfile change) → rebuild 2. Pipeline task additions (moderate effort, requires `.tekton/` YAML work) 3. Exception request as a bridge if release is imminent

*Files/Configs to modify:*
{code}
{Dockerfile,.tekton/push.yaml,.tekton/pull-request.yaml}
{code}

h5. Policy Exception Process

If the recommended fix isn't immediately feasible:
# File JIRA at: [Create Issue|https://JIRA_CREATE_ISSUE_URL]
# Explain business justification and timeline for proper fix
# Create MR to exception file: {{konflux-release-data/.../fbc-acme-prod.yaml}}
# Add exception terms (check AI analysis for specific package URLs)
# Ping @owatkins (ProdSec) in #wg-3_0-openshift-ai-release for approval

h3. Context & Resources

*Repository:* https://github.com/acme-org/RHOAI-Build-Config
*Commit:* [0e0e6d14|https://github.com/acme-org/RHOAI-Build-Conf/commit/0e0e6d14da890c5356634b90e035337615bc04c2]

*Useful Links:*
* [Konflux PipelineRun|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-n8qb6/logs]
* [Quay Repository|https://quay.io/repository/rhoai/rhai-on-xks-chart-v3-4]
* [Enterprise Contract Docs|https://enterprisecontract.dev/]

h3. Suggested Next Steps

{panel:bgColor=#FFF4E6|title=Compliance Resolution}
This violation requires investigation:
1. Review the violation details in Konflux UI
2. Determine if fix or policy exception is appropriate
3. If exception needed, follow policy exception process above
{panel}

---
_Automated analysis by claude-sonnet-4-6  (2026-04-24 14:13:00)._
_Confidence: 82% | Category: config_error_
```

---

## 5. odh-trustyai-service-operator-v3-4

```
h2. Conforma Policy Violation: odh-trustyai-service-operator-v3-4

||Field||Value||
|Component|odh-trustyai-service-operator-v3-4|
|Status|Policy Violation|
|Scenario|conforma-registry-acme-prod-v3-4-single-component|
|Violations|5 violations, 94 warnings, 626 successes|
|PipelineRun|[conforma-registry-acme-prod-v3-4-single-component-77r9g|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-77r9g/logs]|
|Snapshot|acme-v2-0-20260423-101731-000 |
|First Seen|2026-04-23 10:39|
|Last Seen|2026-04-27 08:16|
|Occurrences|1 times|

h3. Context & Resources

*Repository:* https://github.com/acme-org/trustyai-service-operator.git
*Commit:* [11ad35d1|https://github.com/acme-org/trustyai-service-operator/commit/11ad35d1875d34be9ce0f45b9afafcda5e5529ff]

*Useful Links:*
* [Konflux PipelineRun|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-77r9g/logs]
* [Quay Repository|https://quay.io/repository/rhoai/odh-trustyai-service-operator-v3-4]
* [Enterprise Contract Docs|https://enterprisecontract.dev/]

h3. Suggested Next Steps

{panel:bgColor=#FFF4E6|title=Compliance Resolution}
This violation requires investigation:
1. Review the violation details in Konflux UI
2. Determine if fix or policy exception is appropriate
3. If exception needed, follow policy exception process above
{panel}

---
_No automated analysis available yet. Run: ic ai analyze conforma odh-trustyai-service-operator-v3-4_
```

---

## 6. odh-mod-arch-eval-hub-v3-4

```
h2. Conforma Policy Violation: odh-mod-arch-eval-hub-v3-4

||Field||Value||
|Component|odh-mod-arch-eval-hub-v3-4|
|Status|Policy Violation|
|Scenario|conforma-registry-acme-prod-v3-4-single-component|
|Violations|5 violations, 104 warnings, 626 successes|
|PipelineRun|[conforma-registry-acme-prod-v3-4-single-component-ncjd4|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-ncjd4/logs]|
|Snapshot|acme-v2-0-20260423-103012-000 |
|First Seen|2026-04-23 11:13|
|Last Seen|2026-04-27 08:15|
|Occurrences|1 times|

h3. Context & Resources

*Repository:* https://github.com/acme-org/odh-dashboard.git
*Commit:* [9311a43f|https://github.com/acme-org/odh-dashboard/commit/9311a43f381c04938aa19fe21346c293808dc036]

*Useful Links:*
* [Konflux PipelineRun|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-ncjd4/logs]
* [Quay Repository|https://quay.io/repository/rhoai/odh-mod-arch-eval-hub-v3-4]
* [Enterprise Contract Docs|https://enterprisecontract.dev/]

h3. Suggested Next Steps

{panel:bgColor=#FFF4E6|title=Compliance Resolution}
This violation requires investigation:
1. Review the violation details in Konflux UI
2. Determine if fix or policy exception is appropriate
3. If exception needed, follow policy exception process above
{panel}

---
_No automated analysis available yet. Run: ic ai analyze conforma odh-mod-arch-eval-hub-v3-4_
```

---

## 7. odh-ta-lmes-driver-v3-4

```
h2. Conforma Policy Violation: odh-ta-lmes-driver-v3-4

||Field||Value||
|Component|odh-ta-lmes-driver-v3-4|
|Status|Policy Violation|
|Scenario|conforma-registry-acme-prod-v3-4-single-component|
|Violations|5 violations, 94 warnings, 621 successes|
|PipelineRun|[conforma-registry-acme-prod-v3-4-single-component-twxwx|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-twxwx/logs]|
|Snapshot|acme-v2-0-20260423-101755-000 |
|First Seen|2026-04-23 10:38|
|Last Seen|2026-04-27 08:16|
|Occurrences|1 times|

h3. Context & Resources

*Repository:* https://github.com/acme-org/trustyai-service-operator.git
*Commit:* [11ad35d1|https://github.com/acme-org/trustyai-service-operator/commit/11ad35d1875d34be9ce0f45b9afafcda5e5529ff]

*Useful Links:*
* [Konflux PipelineRun|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-twxwx/logs]
* [Quay Repository|https://quay.io/repository/rhoai/odh-ta-lmes-driver-v3-4]
* [Enterprise Contract Docs|https://enterprisecontract.dev/]

h3. Suggested Next Steps

{panel:bgColor=#FFF4E6|title=Compliance Resolution}
This violation requires investigation:
1. Review the violation details in Konflux UI
2. Determine if fix or policy exception is appropriate
3. If exception needed, follow policy exception process above
{panel}

---
_No automated analysis available yet. Run: ic ai analyze conforma odh-ta-lmes-driver-v3-4_
```

---

## 8. odh-workbench-jupyter-datascience-cpu-py312-v3-4

```
h2. Conforma Policy Violation: odh-workbench-jupyter-datascience-cpu-py312-v3-4

||Field||Value||
|Component|odh-workbench-jupyter-datascience-cpu-py312-v3-4|
|Status|Policy Violation|
|Scenario|conforma-registry-acme-prod-v3-4-single-component|
|Violations|4 violations, 89 warnings, 620 successes|
|PipelineRun|[conforma-registry-acme-prod-v3-4-single-component-97cgs|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-97cgs/logs]|
|Snapshot|acme-v2-0-20260424-051112-000 |
|First Seen|2026-04-24 06:09|
|Last Seen|2026-04-27 08:16|
|Occurrences|1 times|

h3. AI Analysis (95% confidence)

||Category||policy_rpm_repository||
|Can Auto-Fix|Yes|
|Requires Review|No|

h4. Root Cause

The multi-arch build for `odh-workbench-jupyter-datascience-cpu-py312-v3-4` picked up different RPM versions across architectures during the build window. Specifically, the `linux/s390x` build runner installed older package versions while `amd64`, `arm64`, and `ppc64le` runners installed newer ones. This is a classic mid-build RPM release timing issue: - **openssl**: s390x got `3.2.2-6.el9_5.1` while amd64/arm64/ppc64le got `3.2.2-7.el9_6.2` - **openssl-libs**: s390x got `3.2.2-6.el9_5.1` while amd64/arm64/ppc64le got `3.2.2-7.el9_6.2` - **python3**: s390x got `3.9.21-2.el9_6.2` while amd64/arm64/ppc64le got `3.9.21-2.el9_6.4` - **python3-libs**: s390x got `3.9.21-2.el9_6.2` while amd64/arm64/ppc64le got `3.9.21-2.el9_6.4` All 4 violations are on the Image Index manifest (`sha256:ad49a88a...`), which is expected — the individual arch manifests pass. The s390x runner appears to have completed its build slightly earlier (or pulled from a different mirror snapshot), before the newer RPM updates propagated or were selected. The CVE warnings (89 total) are non-blocking and informational only — no action required for those.

h4. Recommended Fix

**The quickest path forward is a simple rebuild in Konflux.** No code changes are needed. ### Why a rebuild fixes this: When you trigger a fresh build today, all four architecture runners (amd64, arm64, ppc64le, s390x) will pull from the same current RPM repository state and install the same versions of `openssl`, `openssl-libs`, `python3`, and `python3-libs`. ### Steps: 1. Go to the Konflux UI and find the `odh-workbench-jupyter-datascience-cpu-py312-v3-4` component 2. Trigger a new build (either push a no-op commit or use the Konflux UI "Rebuild" option) 3. Verify the new snapshot passes `rpm_packages.unique_version` for all four affected packages ### If a second rebuild also fails: This would indicate that the s390x RPM repositories are persistently behind the other arches (a known infrastructure lag issue). In that rare case, you have two options: **Option A — Wait and rebuild again**: Give the s390x mirrors 24–48 hours to catch up, then rebuild. **Option B — Request a policy exception** (if release timeline is urgent): - Create a JIRA ticket: https://JIRA_CREATE_ISSUE_URL - Request exclusion of: `rpm_packages.unique_version:openssl`, `rpm_packages.unique_version:openssl-libs`, `rpm_packages.unique_version:python3`, `rpm_packages.unique_version:python3-libs` - Attach the exception to the policy file: https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_DOMAIN/product/EnterpriseContractPolicy/fbc-acme-prod.yaml - Ping @owatkins in `#wg-3_0-openshift-ai-release` Slack for ProdSec approval - Note: exceptions like this are granted temporarily while the underlying cause is investigated ### Regarding the 89 CVE warnings: The `cve.unpatched_cve_warnings` entries (CVE-2026-2004, CVE-2026-2005, CVE-2026-2006, CVE-2026-32280, CVE-2026-33810, etc.) are **non-blocking warnings** — they will not prevent release. These CVEs have no known fix yet; they'll resolve naturally when Red Hat ships patches. No action needed on those unless they escalate to blocking violations.

*Files/Configs to modify:*
{code}
{}
{code}

h5. Policy Exception Process

If the recommended fix isn't immediately feasible:
# File JIRA at: [Create Issue|https://JIRA_CREATE_ISSUE_URL]
# Explain business justification and timeline for proper fix
# Create MR to exception file: {{konflux-release-data/.../fbc-acme-prod.yaml}}
# Add exception terms (check AI analysis for specific package URLs)
# Ping @owatkins (ProdSec) in #wg-3_0-openshift-ai-release for approval

h3. Context & Resources

*Repository:* https://github.com/acme-org/notebooks
*Commit:* [818fd1d5|https://github.com/acme-org/notebooks/commit/818fd1d5b2da0a81e1a4d61eb6d2ea186d296beb]

*Useful Links:*
* [Konflux PipelineRun|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-97cgs/logs]
* [Quay Repository|https://quay.io/repository/rhoai/odh-workbench-jupyter-datascience-cpu-py312-v3-4]
* [Enterprise Contract Docs|https://enterprisecontract.dev/]

h3. Suggested Next Steps

{panel:bgColor=#FFF4E6|title=Compliance Resolution}
The suggested fix appears straightforward:
1. Review the recommended approach above
2. Apply the fix (vendoring, config update, etc.)
3. Rebuild and verify Conforma passes
{panel}

---
_Automated analysis by claude-sonnet-4-6  (2026-04-24 14:14:03)._
_Confidence: 95% | Category: policy_rpm_repository_
```

---

## 9. odh-pipelines-components-v3-4

```
h2. Conforma Policy Violation: odh-pipelines-components-v3-4

||Field||Value||
|Component|odh-pipelines-components-v3-4|
|Status|Policy Violation|
|Scenario|conforma-registry-acme-prod-v3-4-single-component|
|Violations|4 violations, 101 warnings, 501 successes|
|PipelineRun|[conforma-registry-acme-prod-v3-4-single-component-whxgs|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-whxgs/logs]|
|Snapshot|acme-v2-0-20260423-205410-000 |
|First Seen|2026-04-24 06:08|
|Last Seen|2026-04-27 08:15|
|Occurrences|1 times|

h3. AI Analysis (95% confidence)

||Category||policy_hermetic_build||
|Can Auto-Fix|No|
|Requires Review|Yes|

h4. Root Cause

All 4 arch-specific builds of `odh-pipelines-components-v3-4` (ppc64le, arm64, amd64, and the manifest-list index) are failing because the `buildah-remote-oci-ta` task was invoked **without** the `HERMETIC` parameter set to `true`. This means the container build was not executed in a network-isolated (hermetic) environment, violating Conforma's `hermetic_task.hermetic` policy rule. The pipeline spec for `odh-pipelines-components` in the `pipelines-components` repository is either missing the `hermetic: true` parameter or has it explicitly set to `false`.

h4. Recommended Fix

**Quickest path to fix — update the pipeline spec in konflux-central (or the component's pipeline definition):** 1. **Locate the pipeline definition** for `odh-pipelines-components-v3-4` — this is typically in the `.tekton/` directory of the `pipelines-components` repo (https://github.com/acme-org/pipelines-components) or in `konflux-central`. 2. **Find the `buildah-remote-oci-ta` task invocation** and ensure the `HERMETIC` param is set: ```yaml spec: params: - name: hermetic value: "true" ``` Or within the task step params directly: ```yaml - name: HERMETIC value: "true" ``` 3. **Rebuild the component** in Konflux after the fix is merged. All 4 components (ppc64le, arm64, amd64, and the index) need to pass — since they all share the same pipeline config, a single fix should resolve all 4 violations. 4. **Reference**: See how other RHOAI components have done this, e.g., https://github.com/acme-org/konflux-central/pull/1358 for pinning/parameter patterns. --- **If enabling hermetic builds immediately isn't feasible** (e.g., the build genuinely requires network access and cannot be refactored right now), you can request a policy exception: 1. **File a JIRA**: https://JIRA_CREATE_ISSUE_URL - Explain *why* the `buildah-remote-oci-ta` task for `odh-pipelines-components` cannot run hermetically - Describe what network resources are needed and what mitigation is in place 2. **Open a merge request** to the exception file: https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_DOMAIN/product/EnterpriseContractPolicy/fbc-acme-prod.yaml - Add `hermetic_task.hermetic` to the `exclude` section for this component 3. **Tag @owatkins** (ProdSec) in `#wg-3_0-openshift-ai-release` Slack for approval. --- **Note on CVE warnings**: The 101 warnings are all `cve.unpatched_cve_warnings` for high-severity CVEs with no available fix yet. These are **non-blocking** (warnings only, not violations) and do not need to be resolved now — monitor for when fixes become available upstream.

*Files/Configs to modify:*
{code}
{pipelines-components/.tekton/odh-pipelines-components-v3-4-push.yaml,pipelines-components/.tekton/odh-pipelines-components-v3-4-pull-request.yaml}
{code}

h5. Policy Exception Process

If the recommended fix isn't immediately feasible:
# File JIRA at: [Create Issue|https://JIRA_CREATE_ISSUE_URL]
# Explain business justification and timeline for proper fix
# Create MR to exception file: {{konflux-release-data/.../fbc-acme-prod.yaml}}
# Add exception terms (check AI analysis for specific package URLs)
# Ping @owatkins (ProdSec) in #wg-3_0-openshift-ai-release for approval

h3. Context & Resources

*Repository:* https://github.com/acme-org/pipelines-components
*Commit:* [ca1a585a|https://github.com/acme-org/pipelines-components/commit/ca1a585aad0581b9f416f138c77ca895439a7a19]

*Useful Links:*
* [Konflux PipelineRun|https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-whxgs/logs]
* [Quay Repository|https://quay.io/repository/rhoai/odh-pipelines-components-v3-4]
* [Enterprise Contract Docs|https://enterprisecontract.dev/]

h3. Suggested Next Steps

{panel:bgColor=#FFF4E6|title=Compliance Resolution}
This violation requires investigation:
1. Review the violation details in Konflux UI
2. Determine if fix or policy exception is appropriate
3. If exception needed, follow policy exception process above
{panel}

---
_Automated analysis by claude-sonnet-4-6  (2026-04-24 14:14:28)._
_Confidence: 95% | Category: policy_hermetic_build_
```

---

