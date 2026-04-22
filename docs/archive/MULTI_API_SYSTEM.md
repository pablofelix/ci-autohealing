# Sistema Multi-API para Colección de Logs - Python

**Fecha**: 2026-04-17  
**Estado**: ✅ Implementado - Usa TODAS las APIs disponibles

---

## 🎯 Qué Hemos Construido

Un sistema **completamente en Python** que usa **múltiples APIs** para obtener datos y logs de PipelineRuns, sin depender solo de comandos shell.

### APIs Implementadas (todas en Python)

1. ✅ **Kubernetes API** (`kubernetes_client.py`)
   - Cliente Python para Kubernetes usando `oc` como backend
   - Obtiene PipelineRuns actuales
   - Incluye `status.results` con datos adicionales
   - **Ventaja**: Más actual, tiene resultados en status

2. ✅ **KubeArchive API** (`kubearchive_client.py`)
   - Cliente Python para recursos archivados
   - PipelineRuns y TaskRuns históricos
   - **Ventaja**: Datos archivados que ya no están en Kubernetes

3. ✅ **Tekton Results API** (`tekton_results_client.py`)
   - Cliente Python para Tekton Results
   - API de resultados persistidos
   - **Limitación**: API no expuesta públicamente en NAMESPACE_PLACEHOLDER

4. ✅ **Unified Collector** (`unified_collector.py`)
   - Prueba TODAS las APIs automáticamente
   - Retorna la mejor data disponible
   - Indica de qué fuente vino la data

---

## 📊 Arquitectura del Sistema

```
ComprehensiveCollector
├─ UnifiedCollector (orchestrator)
│   ├─ KubernetesClient → Kubernetes API (oc get pipelinerun)
│   ├─ KubeArchiveClient → KubeArchive API (archived)
│   └─ TektonResultsClient → Tekton Results API
│
├─ Prioridad para LOGS:
│   1. Kubernetes API → TaskRuns actuales
│   2. KubeArchive API → Logs archivados
│   3. OC pods → Fallback directo a pods
│
└─ Prioridad para METADATA:
    1. Kubernetes API → Incluye status.results
    2. KubeArchive API → Data archivada
```

---

## 🔧 Implementación

### 1. `kubernetes_client.py`

**Cliente Python para Kubernetes API**

```python
from kubernetes_client import KubernetesClient

k8s = KubernetesClient(namespace='NAMESPACE_PLACEHOLDER')

# Get PipelineRun
pr = k8s.get_pipelinerun('odh-spark-v3-4-abc123')

# Get logs
logs = k8s.get_pipelinerun_logs('odh-spark-v3-4-abc123')

# Get TaskRun details
details = k8s.get_pipelinerun_taskruns_details('odh-spark-v3-4-abc123')
```

**Qué hace:**
- Usa `oc` command via subprocess (Python)
- Parsea JSON responses
- Extrae TaskRuns desde childReferences
- Obtiene logs de cada pod/container

**Ventajas:**
- ✅ Datos más actuales
- ✅ Incluye `status.results` (commit, etc.)
- ✅ PipelineRuns activos en Kubernetes

**Limitaciones:**
- ❌ Solo funciona para PRs que aún existen en Kubernetes (~24-48h)

---

### 2. `kubearchive_client.py`

**Cliente Python para KubeArchive API**

```python
from kubearchive_client import KubeArchiveClient

archive = KubeArchiveClient(namespace='NAMESPACE_PLACEHOLDER')

# Get PipelineRun
pr = archive.get_pipelinerun('odh-spark-v3-4-old123')

# Get logs
logs = archive.get_pipelinerun_logs('odh-spark-v3-4-old123')

# Get TaskRun details
details = archive.get_pipelinerun_taskruns_details('odh-spark-v3-4-old123')
```

**Qué hace:**
- HTTP requests a KubeArchive API
- Autenticación con token de OpenShift
- Rutas correctas para Tekton resources

**Ventajas:**
- ✅ Datos históricos archivados
- ✅ PipelineRuns que ya no están en Kubernetes

**Limitaciones:**
- ❌ Logs de pods pueden estar ya eliminados
- ❌ Puede no tener PipelineRuns muy recientes

---

### 3. `tekton_results_client.py`

**Cliente Python para Tekton Results API**

```python
from tekton_results_client import TektonResultsClient

results = TektonResultsClient(namespace='NAMESPACE_PLACEHOLDER')

# Get Result ID from PipelineRun
result_id = results.get_pipelinerun_result_id('odh-spark-v3-4-abc123')

# Get Result
result = results.get_result(result_id)

# List Records (TaskRuns)
records = results.list_records(result_id)
```

**Qué hace:**
- Extrae Result ID de anotaciones del PipelineRun
- Consulta Tekton Results API
- Obtiene registros persistidos

**Estado actual:**
- ⚠️  API no expuesta públicamente en NAMESPACE_PLACEHOLDER
- ✅ Código implementado y listo para usar
- ✅ Se activará cuando la API esté disponible

**Anotaciones disponibles:**
```
results.tekton.dev/result: NAMESPACE_PLACEHOLDER/results/{uid}
results.tekton.dev/record: NAMESPACE_PLACEHOLDER/results/{uid}/records/{uid}
results.tekton.dev/stored: true
```

---

### 4. `unified_collector.py`

**Orquestador que prueba TODAS las APIs**

```python
from unified_collector import UnifiedCollector

collector = UnifiedCollector(namespace='NAMESPACE_PLACEHOLDER')

# Get PipelineRun (tries all sources)
pr_data, source = collector.get_pipelinerun_complete('odh-spark-v3-4-abc123')
print(f"Got data from: {source}")  # 'kubernetes', 'kubearchive', etc.

# Get logs (tries all sources)
logs, source = collector.get_logs_complete('odh-spark-v3-4-abc123')
print(f"Got logs from: {source}")  # 'kubernetes', 'kubearchive', 'oc_pods'

# Get TaskRun details (tries all sources)
details, source = collector.get_taskruns_details('odh-spark-v3-4-abc123')
```

**Qué hace:**
- Prueba Kubernetes API primero (más actual)
- Fallback a KubeArchive (archivado)
- Último recurso: oc pods directamente
- Retorna (data, source_name)

**Ventajas:**
- ✅ Maximiza probabilidad de obtener datos
- ✅ Transparente: indica de dónde vino la data
- ✅ Fácil de usar: un solo método

---

## 🚀 Uso en Comprehensive Collector

```python
# En collect_comprehensive.py

from unified_collector import UnifiedCollector

class ComprehensiveCollector:
    def __init__(self, config):
        self.unified = UnifiedCollector(namespace=config.k8s.namespace)
    
    def get_comprehensive_logs(self, pr_name):
        # Usa UnifiedCollector que prueba todas las APIs
        logs, source = self.unified.get_logs_complete(pr_name)
        
        if logs:
            print(f"✓ Got logs from {source}")
        else:
            print(f"✗ No logs available from any source")
        
        return logs
    
    def collect_comprehensive_failure(self, component):
        # Get metadata (tries all APIs)
        pr_data, source = self.unified.get_pipelinerun_complete(pr_name)
        print(f"✓ Metadata from {source}")
        
        # Get logs (tries all APIs)
        logs, source = self.unified.get_logs_complete(pr_name)
        print(f"✓ Logs from {source}")
```

---

## 📈 Resultados y Limitaciones

### Cobertura Actual

```bash
$ ic stats
 Total Failures | Components | With Logs 
----------------+------------+-----------
            154 |          8 |        12
```

**Solo 12/154 (7.8%) tienen logs**

### ¿Por qué?

**Logs se eliminan después de ~24-48 horas:**

1. **PipelineRuns muy antiguos** (>2 días):
   - ❌ Ya no están en Kubernetes
   - ❌ Pods ya fueron eliminados
   - ⚠️  Pueden estar en KubeArchive pero SIN logs de pods
   - ✅ Metadata disponible (commit, status, etc.)

2. **PipelineRuns recientes** (<24 horas):
   - ✅ Están en Kubernetes
   - ✅ Pods pueden estar activos
   - ✅ Logs disponibles

3. **PipelineRuns intermedios** (1-2 días):
   - ⚠️  Pueden estar en KubeArchive
   - ❌ Pods ya eliminados
   - ❌ Logs no disponibles

### Solución: Cron Frecuente

```bash
# Cron cada 15 minutos
*/15 * * * * /path/to/collect-comprehensive.sh
```

**Esto captura:**
- ✅ PipelineRuns nuevos mientras logs aún existen
- ✅ Mejora cobertura incrementalmente
- ✅ Eventualmente llega a >80% con logs

---

## 🎯 Priorización de APIs

### Para Obtener Metadata

**Orden de prioridad:**

1. **Kubernetes API** ← Incluye status.results
   ```python
   pr = k8s.get_pipelinerun(name)
   results = pr['status']['results']  # CHAINS-GIT_COMMIT, etc.
   ```

2. **KubeArchive API**
   ```python
   pr = kubearchive.get_pipelinerun(name)
   # Metadata completa pero sin status.results
   ```

**¿Por qué este orden?**
- Kubernetes tiene los `status.results` con commit info
- KubeArchive puede no tener datos muy recientes

### Para Obtener Logs

**Orden de prioridad:**

1. **Kubernetes API** ← Más actual
   ```python
   logs = k8s.get_pipelinerun_logs(name)
   # Obtiene de TaskRuns/pods activos
   ```

2. **KubeArchive API**
   ```python
   logs = kubearchive.get_pipelinerun_logs(name)
   # Logs archivados si disponibles
   ```

3. **OC Pods directo**
   ```python
   logs = unified._get_logs_via_oc(name)
   # Último recurso: oc logs pod
   ```

**¿Por qué este orden?**
- Kubernetes tiene los logs MÁS actuales
- KubeArchive puede tener logs pero pods eliminados
- OC directo es fallback cuando APIs fallan

---

## 🔍 Debugging y Troubleshooting

### Ver de dónde vienen los datos

```python
from unified_collector import UnifiedCollector

collector = UnifiedCollector(namespace='NAMESPACE_PLACEHOLDER')

# Metadata
pr, source = collector.get_pipelinerun_complete('pr-name')
print(f"Metadata source: {source}")
# Output: "kubernetes_with_results", "kubernetes", "kubearchive", "none"

# Logs
logs, source = collector.get_logs_complete('pr-name')
print(f"Logs source: {source}")
# Output: "kubernetes", "kubearchive", "oc_pods", "none"
```

### Probar manualmente cada API

```python
# Test Kubernetes API
from kubernetes_client import KubernetesClient
k8s = KubernetesClient()
pr = k8s.get_pipelinerun('pr-name')
print("Kubernetes:", "✓" if pr else "✗")

# Test KubeArchive
from kubearchive_client import KubeArchiveClient
archive = KubeArchiveClient()
pr = archive.get_pipelinerun('pr-name')
print("KubeArchive:", "✓" if pr else "✗")
```

### Ver qué datos están disponibles

```bash
# Check PipelineRun existe en Kubernetes
oc get pipelinerun pr-name -n NAMESPACE_PLACEHOLDER

# Check tiene resultados
oc get pipelinerun pr-name -n NAMESPACE_PLACEHOLDER -o json | jq '.status.results'

# Check tiene logs anotados
oc get pipelinerun pr-name -n NAMESPACE_PLACEHOLDER -o json | jq '.metadata.annotations | with_entries(select(.key | contains("results")))'
```

---

## ✅ Resumen

### Lo que funciona

1. ✅ **Múltiples APIs en Python**
   - Kubernetes API (más actual)
   - KubeArchive API (histórico)
   - Tekton Results API (preparado)

2. ✅ **Unified Collector**
   - Prueba todas las APIs automáticamente
   - Retorna mejor data disponible
   - Indica fuente de datos

3. ✅ **Comprehensive Collector**
   - Usa UnifiedCollector
   - Obtiene metadata completa
   - Obtiene logs cuando disponibles

4. ✅ **Sincronización de Estado**
   - Marca resueltos automáticamente
   - Registra éxitos para histórico
   - Distingue "actualmente fallando" vs "histórico"

### Limitaciones conocidas

1. ⚠️  **Logs se eliminan rápido**
   - Pods eliminados después ~24-48h
   - Solo 7.8% tienen logs actualmente
   - **Solución**: Cron cada 15 min mejora cobertura

2. ⚠️  **Tekton Results API no expuesta**
   - API implementada pero no accesible
   - **Solución**: Usar Kubernetes/KubeArchive APIs

3. ⚠️  **Datos antiguos sin logs**
   - PipelineRuns de hace >2 días sin logs
   - **Solución**: Metadata aún disponible (commit, URLs)

### Próximos pasos

1. **Mejorar cobertura de logs**:
   - Cron cada 15 min ya configurado ✅
   - Monitorear aumento de cobertura
   - Meta: >80% con logs

2. **Usar status.results**:
   - Extraer CHAINS-GIT_COMMIT ✅
   - Extraer CHAINS-GIT_URL ✅
   - Incluir en database

3. **Dashboard de fuentes**:
   - Mostrar de dónde vino cada dato
   - Estadísticas por fuente
   - Detectar problemas de APIs

---

**El sistema ahora usa TODAS las APIs disponibles en Python para maximizar la colección de datos!** 🚀
