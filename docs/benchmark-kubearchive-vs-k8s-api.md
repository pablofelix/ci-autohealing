# Benchmark: KubeArchive vs K8s API

**Fecha:** 2026-05-18  
**Objetivo:** Comparar velocidad y capacidades de KubeArchive API vs K8s API para PipelineRuns y logs

## Resumen Ejecutivo

El sistema actual **ya usa ambas APIs** y las combina inteligentemente:
- **K8s API** (vía `oc`) para datos recientes en cluster
- **KubeArchive API** (HTTP) para histórico completo + logs archivados
- **Deduplicación por UID** para evitar duplicados

**Conclusión:** El diseño actual es óptimo. No cambiar.

## Resultados del Benchmark

### Test 1: Query de PipelineRuns

Component: `odh-trustyai-nemo-guardrails-server-v3-4-ea-1`

| API | PipelineRuns Encontrados | Tiempo |
|-----|--------------------------|--------|
| **KubeArchive** | 50 (histórico completo) | 2153 ms (2.1s) |
| **K8s API** (oc) | 2 (solo en cluster) | 1006 ms (1.0s) |
| **Winner** | K8s API (más rápido) | - |

**Trade-off:**
- K8s API es **2x más rápido** pero solo ve datos recientes (2 builds)
- KubeArchive es más lento pero tiene **histórico completo** (50 builds)

### Test 2: Disponibilidad de Logs

PipelineRun: `odh-trustyai-nemo-guardrails-server-v3-4-ea-1-on-push-nkwtw` (build del 22 Abril)

| API | Logs Disponibles | Tamaño | Tiempo |
|-----|------------------|--------|--------|
| **KubeArchive** | ✅ Sí | 1,677 chars | 222 ms |
| **K8s API** (oc) | ❌ No | 0 chars | 1,412 ms |
| **Winner** | KubeArchive | - | - |

**Trade-off:**
- K8s API **no tiene logs** para builds antiguos (pods eliminados del cluster)
- KubeArchive **archiva logs** permanentemente
- K8s API es lento buscando pods que no existen (timeout/retry)

## Implementación Actual

Archivo: `collectors/python/clients/pipelinerun_query.py`

```python
def query_pipelineruns(namespace, label_selector,
                       kubearchive_url=None, session=None,
                       max_pages=3, page_size=500):
    """Fetch PipelineRuns from KubeArchive + live cluster, deduplicated by UID."""
    by_uid = {}

    # 1. Query KubeArchive (histórico)
    if kubearchive_url and session:
        url = f"{kubearchive_url}/apis/tekton.dev/v1/namespaces/{namespace}/pipelineruns"
        params = {'labelSelector': label_selector, 'limit': page_size}
        for _ in range(max_pages):
            resp = session.get(url, params=params, timeout=30)
            # ... paginación ...
            for pr in data.get('items', []):
                uid = pr.get('metadata', {}).get('uid')
                if uid:
                    by_uid[uid] = pr

    # 2. Query K8s API (recientes en cluster)
    result = subprocess.run(
        ['oc', 'get', 'pipelinerun', '-n', namespace,
         '-l', label_selector, '-o', 'json'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=15
    )
    for pr in data.get('items', []):
        uid = pr.get('metadata', {}).get('uid')
        if uid:
            by_uid[uid] = pr  # Sobrescribe si existe (datos más frescos)

    return list(by_uid.values())
```

**Deduplicación por UID** significa que:
- Si un PipelineRun está en ambas fuentes, **K8s API gana** (datos más frescos)
- Builds viejos solo aparecen de KubeArchive
- Builds recientes aparecen de K8s API (más rápido)

## Capacidades de Cada API

### KubeArchive API

**URL:** `https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN`

**Puede hacer:**
- ✅ Query de PipelineRuns con label selectors
- ✅ Get individual PipelineRun por nombre
- ✅ Get TaskRuns
- ✅ Get logs de pods (incluso después de que el pod fue eliminado)
- ✅ Paginación (continue token)
- ✅ Histórico completo (no se elimina nada)

**Limitaciones:**
- ⏱️ Más lento para queries grandes (2-3 segundos)
- 🔒 Requiere autenticación (OpenShift token)
- 📊 Limit de 500 items por página

**Cliente:** `collectors/python/clients/kubearchive.py`

### K8s API (vía `oc`)

**URL:** Auto-discovered via `oc whoami --show-server`

**Puede hacer:**
- ✅ Query de PipelineRuns con label selectors
- ✅ Get individual PipelineRun por nombre
- ✅ Get TaskRuns
- ✅ Get logs de pods **si el pod aún existe en cluster**
- ✅ Datos en tiempo real (estado actual exacto)

**Limitaciones:**
- ⏱️ Solo ve recursos que **aún existen en cluster**
- ❌ No tiene logs de pods eliminados (TTL ~7 días típicamente)
- 🔒 Requiere autenticación (oc login)
- 🐢 Lento si el pod no existe (timeout buscando)

**Cliente:** `collectors/python/clients/kubernetes.py`

## Conflux K8s API

**URL:** Same as K8s API + `/apis/appstudio.redhat.com/v1alpha1`

**Solo para CRDs específicos de Konflux:**
- `EnterpriseContractPolicy`
- `ReleasePlanAdmission`
- `IntegrationTestScenario`

**NO tiene:**
- ❌ PipelineRuns (esos están en Tekton API: `apis/tekton.dev/v1`)
- ❌ TaskRuns
- ❌ Logs

**Cliente:** `collectors/python/clients/konflux_client.py`

## Preguntas Frecuentes

### ¿Puede K8s API reemplazar KubeArchive?

**No.** Por dos razones críticas:

1. **Retención de datos:** K8s API solo tiene builds recientes (días), KubeArchive tiene histórico completo (meses)
2. **Logs archivados:** K8s API no tiene logs de pods eliminados, KubeArchive sí

### ¿Es K8s API más rápido?

**Sí, para queries de datos recientes:**
- K8s API: ~1 segundo para 2 PipelineRuns
- KubeArchive: ~2 segundos para 50 PipelineRuns

Pero **no para logs** de builds antiguos (K8s API busca pods que no existen).

### ¿Por qué el sistema usa ambos?

Para combinar **velocidad + histórico completo:**

1. **KubeArchive** trae histórico (50 builds, todos con logs)
2. **K8s API** trae estado actual (2 builds recientes, datos frescos)
3. **Deduplicación** elimina duplicados, priorizando K8s API (datos más frescos)

Ejemplo real del benchmark:
- Total unique PipelineRuns: 50 (48 solo en KubeArchive + 2 en ambos)
- Tiempo total: ~2-3 segundos (paralelo en el futuro?)

### ¿Qué pasa si un build está en ambos?

**K8s API gana** porque sus datos son más frescos. La deduplicación por UID sobrescribe:

```python
by_uid[uid] = pr  # K8s API se ejecuta después, sobrescribe
```

Esto significa que para builds recientes, tenemos:
- Metadata de K8s API (estado exacto actual)
- Logs de KubeArchive (archivados permanentemente)

## Recomendaciones

### ✅ Mantener diseño actual

No cambiar. El sistema actual es óptimo:
- Usa ambas APIs inteligentemente
- Deduplica correctamente
- Balance perfecto velocidad/completitud

### 💡 Optimización futura (opcional)

**Query paralelo:** Ejecutar KubeArchive y K8s API en paralelo para reducir latencia total de 3s a ~2s.

```python
import concurrent.futures

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
    future_ka = executor.submit(query_kubearchive, ...)
    future_k8s = executor.submit(query_k8s, ...)
    
    ka_results = future_ka.result()
    k8s_results = future_k8s.result()
    
    # Deduplicate...
```

**Ganancia:** ~30-40% reducción de tiempo total (de 3s a 2s).

### 📝 Documentar comportamiento

Agregar a `ic get components --help`:

```
NOTE: Shows components whose LATEST build failed.
      Historical failures (already fixed) are not shown.
      Use KubeArchive API for complete history.
```

## Archivos de Referencia

- **Query combinado:** `collectors/python/clients/pipelinerun_query.py`
- **KubeArchive client:** `collectors/python/clients/kubearchive.py`
- **K8s client:** `collectors/python/clients/kubernetes.py`
- **Konflux client:** `collectors/python/clients/konflux_client.py`
- **Benchmark script:** `collectors/python/benchmark_api_speed.py`

## Ejecución del Benchmark

```bash
cd PROJECT_DIR/collectors/python

# Test básico (query only)
python3 benchmark_api_speed.py <component-name>

# Test completo (query + logs)
python3 benchmark_api_speed.py <component-name> <pipelinerun-name>

# Ejemplo:
python3 benchmark_api_speed.py \
  odh-trustyai-nemo-guardrails-server-v3-4-ea-1 \
  odh-trustyai-nemo-guardrails-server-v3-4-ea-1-on-push-nkwtw
```

## Conclusión

**No cambiar nada.** El diseño actual que combina KubeArchive + K8s API es:
- ✅ Completo (histórico + estado actual)
- ✅ Rápido (1-3 segundos)
- ✅ Resiliente (funciona si una fuente falla)
- ✅ Correcto (deduplicación por UID)

La única optimización posible es hacer las queries en paralelo, pero la ganancia es marginal (~1 segundo).
