# RHOAI 3.4 Conforma - JIRA Tickets

Date: 2026-04-27
Label: conforma-violation
Component: DevOps

---

## JIRA 1: odh-workbench-jupyter-datascience-cpu-py312-v3-4

**Title:** [Conforma] odh-workbench-jupyter-datascience-cpu-py312-v3-4 - rpm_packages.unique_version (s390x RPM mismatch)

**Status:** Exception filed, fix = rebuild s390x

```
Description of problem

Conforma policy violation rpm_packages.unique_version on odh-workbench-jupyter-datascience-cpu-py312-v3-4. 4 violations on the image index manifest. The multi-arch build picked up different RPM versions on s390x compared to amd64/arm64/ppc64le:

- openssl: s390x has 3.2.2-6.el9_5.1, others have 3.2.2-7.el9_6.2
- openssl-libs: s390x has 3.2.2-6.el9_5.1, others have 3.2.2-7.el9_6.2
- python3: s390x has 3.9.21-2.el9_6.2, others have 3.9.21-2.el9_6.4
- python3-libs: s390x has 3.9.21-2.el9_6.2, others have 3.9.21-2.el9_6.4

Prerequisites (if any, like setup, operators/versions)

- RHOAI v3.4
- Konflux build pipeline (multi-arch: amd64, arm64, ppc64le, s390x)
- Conforma scenario: conforma-registry-acme-prod-v3-4-single-component

Steps to Reproduce

1. Build was triggered from commit 818fd1d5 on https://github.com/acme-org/notebooks
2. Conforma validation ran against snapshot acme-v2-0-20260424-051112-000
3. PipelineRun: https://konflux-ui.apps.CLUSTER_DOMAIN/ns/NAMESPACE_PLACEHOLDER/applications/acme-v2-0/pipelineruns/conforma-registry-acme-prod-v3-4-single-component-97cgs/logs
4. Check Security tab in the PipelineRun, filter by rule rpm_packages.unique_version

Actual results

4 Conforma violations (rpm_packages.unique_version) on the image index manifest (sha256:ad49a88a). s390x arch has older RPM versions than the other three architectures. Total result: 4 violations, 89 warnings, 620 successes.

Expected results

All architectures should have identical RPM versions. 0 violations.

Reproducibility (Always/Intermittent/Only Once)

Intermittent. Per comforma.pdf section 9: "Sometimes the reason for this mismatch is that there was a newer version of said RPM package released to the public around the same time as the Konflux build was happening. This would result in some architectures that build faster pick the older version while the architectures that usually take longer to build (s390x/ppc) could be installing that just-released version."

Found in what build

- Snapshot: acme-v2-0-20260424-051112-000
- Commit: 818fd1d5b2da0a81e1a4d61eb6d2ea186d296beb
- First detected: 2026-04-24
- Last seen: 2026-04-27

Describe any workarounds

Exception filed in conforma-reporter for rpm_packages.unique_version:openssl, rpm_packages.unique_version:openssl-libs, rpm_packages.unique_version:python3, rpm_packages.unique_version:python3-libs. See https://github.com/acme-org/conforma-reporter/blob/acme-3.4/conforma-exceptions-components.yaml

Additional information

Per comforma.pdf section 9: Rebuild the component in Konflux so all arches pick up the same RPM versions. If after rebuild the issue persists, it needs to be highlighted to the component team (notebooks) who should provide the solution — i.e. install the same version of the RPM in all container images. They might need DevOps support. If none of the above options are possible, seek a Conforma Policy exception per the process documented at https://spaces.redhat.com/pages/591271667/PSRD+Exception+Submission+Quick+Guide

Component tier: Workbench image (Stable). This is a compliance blocker for release. Fix (rebuild) is low-risk and requires no code change. Suggest fixing in current release cycle via rebuild. Customer impact is zero at runtime — the image functions correctly but the RPM version mismatch blocks Conforma policy compliance.

Repository: https://github.com/acme-org/notebooks
Image: quay.io/acme/odh-workbench-jupyter-datascience-cpu-py312-rhel9
```

---

## JIRA 2: odh-spark-operator-v3-4

**Title:** [Conforma] odh-spark-operator-v3-4 - rpm_repos.ids_known (40 violations, unknown/disallowed repository IDs)

**Status:** Exception filed (22 per-package entries), fix = rename repo IDs in Dockerfile/ubi.repo + rebuild

```
Description of problem

Conforma policy violation rpm_repos.ids_known on odh-spark-operator-v3-4. 40 violations across 4 per-arch images (amd64, arm64, ppc64le, s390x). RPM packages in the SBOM specify repository IDs (ubi-9-appstream-rpms, ubi-9-baseos-rpms, epel) that are not in the list of known and permitted repository IDs.

Affected repo IDs:
- ubi-9-appstream-rpms (44 occurrences across arches)
- ubi-9-baseos-rpms (32 occurrences across arches)
- epel (4 occurrences — tini package on amd64 and arm64)

Example violation: "RPM repo id check failed: An RPM component in the SBOM specified an unknown or disallowed repository_id: pkg:rpm/redhat/abattis-cantarell-fonts@0.301-4.el9?arch=noarch&repository_id=ubi-9-appstream-rpms"

Prerequisites (if any, like setup, operators/versions)

- RHOAI v3.4
- Konflux build pipeline (multi-arch: amd64, arm64, ppc64le, s390x)
- Conforma scenario: conforma-registry-acme-prod-v3-4-single-component

Steps to Reproduce

1. Component built from https://github.com/acme-org/spark-operator
2. Conforma validation ran against the latest snapshot
3. Check the Security tab in the Conforma PipelineRun, filter by rule rpm_repos.ids_known
4. Violations visible in conforma-reporter CSV: https://github.com/acme-org/conforma-reporter/blob/acme-3.4/conforma-violations-report.csv

Actual results

40 violations of rpm_repos.ids_known. RPM repository IDs in the SBOM are not recognized by Conforma policy. Affected packages include abattis-cantarell-fonts, acl, adobe-source-code-pro-fonts, adwaita-cursor-theme, adwaita-icon-theme, alsa-lib, alternatives, at-spi2-atk, basesystem, ca-certificates, copy-jdk-configs, crypto-policies, dbus-common, NetworkManager-libnm, and tini (from epel).

Expected results

All RPM repository IDs in the SBOM should be in the list of known/permitted repository IDs. 0 violations.

Reproducibility (Always/Intermittent/Only Once)

Always — the repository IDs in the Dockerfile/ubi.repo are not architecture-qualified.

Found in what build

- Reported in conforma-reporter CSV on acme-3.4 branch
- Image: quay.io/acme/odh-spark-operator-rhel9
- First detected: 2026-04-24
- Last seen: 2026-04-27

Describe any workarounds

Exception filed in conforma-reporter with 22 per-package entries covering the affected RPMs. See https://github.com/acme-org/conforma-reporter/blob/acme-3.4/conforma-exceptions-components.yaml

Additional information

Per comforma.pdf section 10: The repository IDs need to include the architecture as part of their name/ID. For example, instead of [ubi-9-baseos-rpms] should be [ubi-9-for-x86_64-baseos-rpms]. Fix requires:
1. Rename the repository IDs in the Dockerfile/ubi.repo used by this component (e.g. https://github.com/acme-org/data-science-pipelines/blob/070718e9089c7b5f828c24f6f0ea6d9ecd9380a6/ubi.repo)
2. Rebuild the rpms.lock.yaml file using https://konflux.pages.redhat.com/docs/users/building/prefetching-dependencies.html#rpm
3. Commit/merge the changes to the repository
4. Rebuild the component in Konflux so the newly built container image will have the new repo IDs recorded

All allowed repo IDs: https://github.com/release-engineering/rhtap-ec-policy/blob/main/data/known_rpm_repositories.yml

The tini package from epel repo is a special case — component team should evaluate whether tini can be sourced from a Red Hat repository, built from source, or if a legal agreement/exception is needed.

Component tier: Stable. This is a compliance blocker for release. Suggest fixing in current release cycle. Customer impact is zero at runtime.

Repository: https://github.com/acme-org/spark-operator
Image: quay.io/acme/odh-spark-operator-rhel9
```

---

## JIRA 3: odh-pipelines-components-v3-4

**Title:** [Conforma] odh-pipelines-components-v3-4 - hermetic_task.hermetic (4 violations, buildah-remote-oci-ta not hermetic)

**Status:** Exception filed (blanket hermetic_task.hermetic), fix = set HERMETIC=true in pipeline spec

```
Description of problem

Conforma policy violation hermetic_task.hermetic on odh-pipelines-components-v3-4. 4 violations across 4 per-arch images (amd64, arm64, ppc64le, s390x). The buildah-remote-oci-ta task was not invoked with the hermetic parameter set to true.

Violation message: "Task 'buildah-remote-oci-ta' was not invoked with the hermetic parameter set"

Prerequisites (if any, like setup, operators/versions)

- RHOAI v3.4
- Konflux build pipeline (multi-arch: amd64, arm64, ppc64le, s390x)
- Conforma scenario: conforma-registry-acme-prod-v3-4-single-component

Steps to Reproduce

1. Build triggered from commit ca1a585a on https://github.com/acme-org/pipelines-components
2. Conforma ran against snapshot acme-v2-0-20260423-205410-000
3. PipelineRun: conforma-registry-acme-prod-v3-4-single-component-whxgs
4. Check the Security tab, filter by rule hermetic_task.hermetic

Actual results

4 violations — buildah-remote-oci-ta task invoked without HERMETIC=true on all 4 per-arch images.

Expected results

All build tasks should be invoked with HERMETIC=true. 0 violations.

Reproducibility (Always/Intermittent/Only Once)

Always — the pipeline spec does not include the HERMETIC=true parameter for this task.

Found in what build

- Snapshot: acme-v2-0-20260423-205410-000
- Commit: ca1a585aad0581b9f416f138c77ca895439a7a19
- PipelineRun: conforma-registry-acme-prod-v3-4-single-component-whxgs
- First detected: 2026-04-24
- Last seen: 2026-04-27

Describe any workarounds

Exception filed in conforma-reporter with blanket "hermetic_task.hermetic" entry. See https://github.com/acme-org/conforma-reporter/blob/acme-3.4/conforma-exceptions-components.yaml

Additional information

Per comforma.pdf section 1: All container images built by Internal Red Hat Konflux are required to be built in Hermetic environment. The 'hermetic' parameter of PipelineRun spec should be set to 'true'. Hermetic builds don't have access to the Internet or any network resources outside the build node. All packages (pip, RPMs, gomod etc.) have to be prefetched as part of the 'prefetch-dependencies' step.

Fix requires:
1. Set HERMETIC=true in the .tekton/ pipeline YAML files for this component
2. Ensure all dependencies are prefetched (see https://konflux.pages.redhat.com/docs/users/building/prefetching-dependencies.html)
3. Rebuild the component in Konflux

The component team has to be working on making the builds hermetic with DevOps team supporting the effort (e.g. helping with rpms lock files etc.).

If hermetic build is not possible, exception can be filed for a specific container image URL to the conforma policy (see https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/commit/22ab05656abf096ea4bce97df7a755093d7b91ae for example).

Component tier: Stable. This is a compliance blocker for release. Suggest fixing in current release cycle. Customer impact is zero at runtime.

Repository: https://github.com/acme-org/pipelines-components
Image: quay.io/acme/odh-pipelines-components-rhel9
```

---

## JIRA 4: acme-autorag-v3-4

**Title:** [Conforma] acme-autorag-v3-4 - sbom_spdx.allowed_package_sources (60 violations, huggingface model files)

**Status:** Exception filed (15 huggingface + 1 sqlite entry), fix = vendor models to RH-approved repo or maintain exception

```
Description of problem

Conforma policy violation sbom_spdx.allowed_package_sources on acme-autorag-v3-4. 60 violations across 4 per-arch images (amd64, arm64, ppc64le, s390x). The hermetic build prefetch (Hermeto) fetches files from huggingface.co which is not an allowed package source.

15 unique files from 2 HuggingFace repos are fetched during the build:
- docling-project/docling-layout-heron (6 files: .gitattributes, config.json, docling_heron_400.png, model.safetensors, preprocessor_config.json, README.md)
- docling-project/docling-models v2.3.0 (9 files: .gitattributes, .gitignore, config.json, README.md, tableformer_accurate.safetensors, tableformer_fast.safetensors, 2x tm_config.json)

15 files x 4 arches = 60 violations.

Prerequisites (if any, like setup, operators/versions)

- RHOAI v3.4
- Konflux build pipeline (multi-arch: amd64, arm64, ppc64le, s390x)
- Conforma scenario: conforma-registry-acme-prod-v3-4-single-component

Steps to Reproduce

1. Build triggered from commit ca1a585a on https://github.com/acme-org/pipelines-components
2. Conforma ran against snapshot acme-v2-0-20260423-205409-000
3. PipelineRun: conforma-registry-acme-prod-v3-4-single-component-2bqp8
4. Check the Security tab, filter by rule sbom_spdx.allowed_package_sources

Actual results

60 violations — files fetched from huggingface.co are not from an allowed package source.

Expected results

All packages fetched during build should come from allowed/approved sources. 0 violations.

Reproducibility (Always/Intermittent/Only Once)

Always — the build requires docling model files from huggingface.co for the autorag functionality.

Found in what build

- Snapshot: acme-v2-0-20260423-205409-000
- Commit: ca1a585aad0581b9f416f138c77ca895439a7a19
- PipelineRun: conforma-registry-acme-prod-v3-4-single-component-2bqp8
- First detected: 2026-04-24
- Last seen: 2026-04-27

Describe any workarounds

Exception filed in conforma-reporter with 15 individual per-file entries for the huggingface downloads + 1 sqlite.org entry. See https://github.com/acme-org/conforma-reporter/blob/acme-3.4/conforma-exceptions-components.yaml

Additional information

Per comforma.pdf section 8: Only software that meets certain criteria can be part of the RHOAI product:
1. Software built by RH (signed by key 199e2f91fd431d51)
2. Software from external sources where we can obtain source code and include it in the source container image
3. Software from an external source which RH has a legal agreement with (e.g. Intel, NVIDIA)

The component team should evaluate these options:
- Vendor the docling model files into a Red Hat-approved internal repository or registry
- If models cannot be vendored, ensure a valid legal support agreement with the vendor (docling-project/HuggingFace) and request a ProdSec exception
- The exception process: create JIRA at https://JIRA_CREATE_ISSUE_URL, explain business justification, create MR to exception file in konflux-release-data, ping @owatkins in #wg-3_0-openshift-ai-release for ProdSec approval

The sqlite.org download (sqlite-autoconf-3510300.tar.gz) also needs the same treatment — either vendor from a RH repo or maintain the exception.

Component tier: Stable. This is a compliance blocker for release. The underlying issue requires either vendoring models or legal agreement — not a simple rebuild fix. Suggest maintaining exception while component team works on proper resolution. Customer impact is zero at runtime.

Repository: https://github.com/acme-org/pipelines-components
Image: quay.io/acme/acme-autorag-rhel9
```
