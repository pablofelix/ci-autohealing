# Excepciones Caducadas — 2026-06-30

**Policy productiva**: `custom-registry` (EnterpriseContractPolicy en rhoai-tenant)
**Violations activas hoy**: 35 componentes
**Excepciones expiradas causando violations**: 70 en custom-registry

---

## Resumen Ejecutivo

| Regla expirada | Excepciones | Componentes afectados hoy | Jira principal | MR pendiente |
|---------------|:-----------:|:-------------------------:|----------------|:------------:|
| `hermetic_task.hermetic` | 48 | 33 | RHOAIENG-39914 (ckodama) | !19385 |
| `sbom_spdx.disallowed_package_attributes` | 1 (global) | ~20 | RHOAIENG-16260 | !19385 |
| `hermetic_task` | 1 | 0 (odh-llama-stack no en EA2) | KONFLUX-7552 | — |
| `rpm_signature.allowed:*` | 7 keys | 0 (ya cubiertos en EA2-future) | PSX-1038..1042 | — |
| `sbom_spdx.allowed_package_sources:*` | 13 | 1 (nemo-guardrails) | RHOAIENG-47507 | — |

**Epic paraguas**: RHOAIENG-58768 "RHOAI Conforma Hardening 2026Q2" (wznoinsk, In Progress)
**Epic hermetic**: RHOAIENG-39914 "[Phase 2] Build Images Hermetically" (ckodama, In Progress)

---

## Detalle: hermetic_task.hermetic expiradas (48 excepciones)

Todas expiran `2026-06-30T00:00:00Z`. Cada línea = una excepción en la policy.

| # | Imagen (quay.io/rhoai/) | Jira | Componente EA2 | Violation hoy? |
|--:|------------------------|------|----------------|:--------------:|
| 1 | ALL (wildcard) | RHOAIENG-60044 | — | Sí (cubre residuales) |
| 2 | odh-built-in-detector-rhel9 | RHOAIENG-39914 | odh-built-in-detector-v3-5-ea-2 | **Sí** |
| 3 | odh-caikit-nlp-rhel9 | RHOAIENG-9147 | — | No (no en EA2 alerts) |
| 4 | odh-caikit-tgis-serving-rhel9 | RHOAIENG-39914 | — | No (no en EA2 alerts) |
| 5 | odh-feature-server-rhel9 | RHOAIENG-45300 | — | No (no en EA2 alerts) |
| 6 | odh-fms-guardrails-orchestrator-rhel9 | RHOAIENG-39914 | — | No (no en EA2 alerts) |
| 7 | odh-guardrails-detector-huggingface-runtime-rhel9 | RHOAIENG-39914 | odh-guardrails-detector-huggingface-runtime-v3-5-ea-2 | **Sí** |
| 8 | odh-kserve-agent-rhel9 | RHOAIENG-41817 | — | No |
| 9 | odh-kserve-controller-rhel9 | RHOAIENG-60043 | — | No |
| 10 | odh-kserve-storage-initializer-rhel9 | RHOAIENG-60045 | odh-kserve-storage-initializer-v3-5-ea-2 | **Sí** |
| 11 | odh-mlmd-grpc-server-rhel9 | RHOAIENG-31398 | — | No |
| 12 | odh-model-registry-job-async-upload-rhel9 | STONEBLD-4547 | — | No (expired 05-24) |
| 13 | odh-must-gather-rhel9 | RHOAIENG-39914 | — | No |
| 14 | odh-openvino-model-server-rhel9 | RHOAIENG-39914 | odh-openvino-model-server-v3-5-ea-2 | **Sí** |
| 15 | odh-pipeline-runtime-datascience-cpu-py312-rhel9 | RHAIENG-2873 | odh-pipeline-runtime-datascience-cpu-py312-v3-5-ea-2 | **Sí** |
| 16 | odh-pipeline-runtime-minimal-cpu-py312-rhel9 | RHAIENG-2874 | — | No (no en EA2 alerts) |
| 17 | odh-pipeline-runtime-pytorch-cuda-py312-rhel9 | RHAIENG-2876 | odh-pipeline-runtime-pytorch-cuda-py312-v3-5-ea-2 | **Sí** |
| 18 | odh-pipeline-runtime-pytorch-llmcompressor-cuda-py312-rhel9 | RHAIENG-2875 | odh-pipeline-runtime-pytorch-llmcompressor-cuda-py312-v3-5-ea-2 | **Sí** |
| 19 | odh-pipeline-runtime-pytorch-rocm-py312-rhel9 | RHAIENG-2877 | odh-pipeline-runtime-pytorch-rocm-py312-v3-5-ea-2 | **Sí** |
| 20 | odh-pipeline-runtime-tensorflow-cuda-py312-rhel9 | RHAIENG-2879 | odh-pipeline-runtime-tensorflow-cuda-py312-v3-5-ea-2 | **Sí** |
| 21 | odh-pipeline-runtime-tensorflow-rocm-py312-rhel9 | RHAIENG-2878 | odh-pipeline-runtime-tensorflow-rocm-py312-v3-5-ea-2 | **Sí** |
| 22 | odh-ta-lmes-driver-rhel9 | RHOAIENG-39914 | — | No |
| 23 | odh-ta-lmes-job-rhel9 | RHOAIENG-39914 | odh-ta-lmes-job-v3-5-ea-2 | **Sí** |
| 24 | odh-training-cuda121-torch24-py311-rhel9 | RHOAIENG-39914 | odh-training-cuda121-torch24-py311-v3-5-ea-2 | **Sí** |
| 25 | odh-training-cuda124-torch25-py311-rhel9 | RHOAIENG-39914 | odh-training-cuda124-torch25-py311-v3-5-ea-2 | **Sí** |
| 26 | odh-training-cuda128-torch28-py312-rhel9 | RHOAIENG-39914 | — | No (no en alerts hoy) |
| 27 | odh-training-cuda128-torch29-py312-rhel9 | RHOAIENG-46738 | odh-training-cuda128-torch29-py312-v3-5-ea-2 | **Sí** |
| 28 | odh-training-rocm62-torch24-py311-rhel9 | RHOAIENG-39914 | odh-training-rocm62-torch24-py311-v3-5-ea-2 | **Sí** |
| 29 | odh-training-rocm62-torch25-py311-rhel9 | RHOAIENG-39914 | odh-training-rocm62-torch25-py311-v3-5-ea-2 | **Sí** |
| 30 | odh-training-rocm64-torch28-py312-rhel9 | RHOAIENG-39914 | — | No (no en alerts hoy) |
| 31 | odh-training-rocm64-torch29-py312-rhel9 | RHOAIENG-46738 | odh-training-rocm64-torch29-py312-v3-5-ea-2 | **Sí** |
| 32 | odh-trustyai-nemo-guardrails-server-rhel9 | RHOAIENG-47507 | odh-trustyai-nemo-guardrails-server-v3-5-ea-2 | **Sí** |
| 33 | odh-trustyai-vllm-orchestrator-gateway-rhel9 | RHOAIENG-39914 | — | No |
| 34 | odh-vllm-cpu-rhel9 | RHOAIENG-39914 | odh-vllm-cpu-v3-5-ea-2 | **Sí** |
| 35 | odh-vllm-cuda-rhel9 | RHOAIENG-39914 | — | No |
| 36 | odh-vllm-gaudi-rhel9 | RHOAIENG-39914 | — | No |
| 37 | odh-vllm-rocm-rhel9 | RHOAIENG-39914 | — | No |
| 38 | odh-workbench-codeserver-datascience-cpu-py312-rhel9 | RHAIENG-2860 | — | No (build failure) |
| 39 | odh-workbench-jupyter-datascience-cpu-py312-rhel9 | RHAIENG-2861 | — | No (no en alerts) |
| 40 | odh-workbench-jupyter-minimal-cpu-py312-rhel9 | RHAIENG-2862 | — | No (no en alerts) |
| 41 | odh-workbench-jupyter-minimal-cuda-py312-rhel9 | RHAIENG-2863 | odh-workbench-jupyter-minimal-cuda-py312-v3-5-ea-2 | **Sí** |
| 42 | odh-workbench-jupyter-minimal-rocm-py312-rhel9 | RHAIENG-2864 | odh-workbench-jupyter-minimal-rocm-py312-v3-5-ea-2 | **Sí** |
| 43 | odh-workbench-jupyter-pytorch-cuda-py312-rhel9 | RHAIENG-2866 | odh-workbench-jupyter-pytorch-cuda-py312-v3-5-ea-2 | **Sí** |
| 44 | odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312-rhel9 | RHAIENG-2865 | odh-wb-jupyter-pytorch-llmcompressor-cuda-py312-v3-5-ea-2 | **Sí** |
| 45 | odh-workbench-jupyter-pytorch-rocm-py312-rhel9 | RHAIENG-2867 | odh-workbench-jupyter-pytorch-rocm-py312-v3-5-ea-2 | **Sí** |
| 46 | odh-workbench-jupyter-tensorflow-cuda-py312-rhel9 | RHAIENG-2869 | odh-workbench-jupyter-tensorflow-cuda-py312-v3-5-ea-2 | **Sí** |
| 47 | odh-workbench-jupyter-tensorflow-rocm-py312-rhel9 | RHAIENG-2868 | odh-workbench-jupyter-tensorflow-rocm-py312-v3-5-ea-2 | **Sí** |
| 48 | odh-workbench-jupyter-trustyai-cpu-py312-rhel9 | RHAIENG-2870 | odh-workbench-jupyter-trustyai-cpu-py312-v3-5-ea-2 | **Sí** |

### Resumen hermetic: 27 de 48 excepciones causan violations hoy

**Por Jira:**
| Jira | Componentes afectados | Owner |
|------|:---------------------:|-------|
| RHOAIENG-39914 | 14 | ckodama (Chris Kodama) |
| RHAIENG-2860..2879 | 11 (pipelines + workbenches) | Varios (1 Jira por componente) |
| RHOAIENG-46738 | 2 (torch29) | — |
| RHOAIENG-60044 | wildcard (residual) | — |
| RHOAIENG-60045 | 1 (kserve-storage-initializer) | — |
| RHOAIENG-47507 | 1 (nemo-guardrails) | — |

---

## Detalle: sbom_spdx.disallowed_package_attributes expirada (1 global)

| Regla | Jira | Expiración | Afecta |
|-------|------|-----------|--------|
| `sbom_spdx.disallowed_package_attributes` | RHOAIENG-16260 | 2026-06-30 | TODOS los componentes con paquetes pip binarios |

Esta es una excepción **global** (sin imageUrl): afecta a cualquier componente que tenga `hermeto:pip:package:binary=true` en su SBOM. Componentes afectados hoy: ~20 (todos los que instalan paquetes pip via hermeto).

---

## Detalle: sbom_spdx.allowed_package_sources expirada

| Regla | Jira | Expiración | Imagen afectada |
|-------|------|-----------|-----------------|
| `sbom_spdx.allowed_package_sources:en_core_web_lg` | RHOAIENG-47505 → RHOAIENG-47507 | 2026-05-15 | nemo-guardrails |
| `sbom_spdx.allowed_package_sources:libtokenizers` | RHOAIENG-28098 | 2026-06-30 | ALL |
| `sbom_spdx.allowed_package_sources:RPM-GPG-KEY-CentOS` | RHAIENG-2846 | 2026-06-30 | ALL |

---

## Violations activas SIN excepción previa (no son expiración)

Estos componentes fallan por razones que nunca tuvieron excepción:

| # | Componente | Regla | Root cause | Triage |
|--:|-----------|-------|------------|--------|
| 1 | odh-guardrails-detector-huggingface-runtime | `rpm_packages.unique_version` | RPM skew multi-arch | #12 |
| 2 | odh-training-* (8) + odh-vllm-cpu | `rpm_signature.allowed:a024f6f0e6d6a281` | NVIDIA/AMD signing key | #11 |
| 3 | rhai-on-openshift-chart, rhai-on-xks-chart | `labels`, `cve`, `base_image`, `sbom` | Helm chart, no aplica | #13 |
| 4 | rhoai-fbc-fragment | `base_image_registries`, `rpm_signature`, `test.no_failed_tests` | FBC fragment special | #14 |
| 5 | odh-automl, odh-cli, odh-mlserver, odh-ogx-core, odh-pipelines-components | `hermetic_task` + `sbom_spdx.disallowed` | Excepción expirada HOY | #20 (nuevo) |

**Nota**: Algunos componentes (training, vllm-cpu) tienen DOBLE fallo: `rpm_signature` (que nunca tuvo excepción) + `hermetic_task` (excepción expirada hoy).

---

## Tracking en ic triage

| Triage ID | Grupo | Componentes | Jira | Estado |
|:---------:|-------|:-----------:|------|--------|
| #20 | Exception expiration — hermetic + sbom_spdx | 6 (parcial) | RHOAIENG-39914 | active |
| #11 | rpm_signature.allowed (NVIDIA/AMD key) | 9 | RHOAIENG-38398 | active |
| #12 | rpm_packages.unique_version (multi-arch) | 1 | — | active |
| #13 | Helm chart policy | 2 | — | active |
| #14 | FBC fragment policy | 1 | — | active |
| #18 | Conforma infra failure | 2 | — | active |

**TODO**: Completar triage #20 añadiendo los 21 componentes restantes que faltan.

---

## Acciones recomendadas

### URGENTE (hoy)
1. **Merge MR !19385** en `konflux-release-data` — renueva todas las excepciones hermetic
2. **Extender excepción `sbom_spdx.disallowed_package_attributes`** (RHOAIENG-16260) — afecta ~20 componentes
3. **Contactar ckodama** (RHOAIENG-39914) para estado del MR

### A corto plazo
4. **rpm_signature.allowed** — necesita que ProdSec (PSX) renueve las excepciones de signing keys
5. **Helm charts** — necesitan policy separada (no son container images)
6. **FBC fragment** — investigar base image registry + test failures
7. **rpm_packages.unique_version** (guardrails) — rebuild ya triggerado, verificar resultado

---

*Generado: 2026-06-30 por ic triage + conforma skills analysis*
*Datos de: custom-registry volatileConfig + ic list_alerts + Jira MCP*
