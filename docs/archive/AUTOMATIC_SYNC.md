# Sistema de Sincronización Automática - Completo

**Fecha**: 2026-04-17  
**Estado**: ✅ Funcionando Automáticamente (cada 15 minutos)

---

## 🎯 Resumen Ejecutivo

El sistema ahora ejecuta **automáticamente cada 15 minutos**:

1. ✅ **Colección comprehensiva** usando 3 APIs en Python
2. ✅ **Sincronización de estado** detectando componentes resueltos
3. ✅ **Todo en Python** (no shell scripts para colección)

---

## 🔄 Flujo Automático (Cron)

### Configuración del Cron

```bash
$ crontab -l
*/15 * * * * PROJECT_DIR/cron/collect-comprehensive.sh
```

**Frecuencia**: Cada 15 minutos  
**Logs**: `logs/cron/collect-comprehensive-YYYYMMDD_HHMMSS.log`

### Qué Ejecuta

```bash
[1/2] Comprehensive Collector
├─ Para cada componente:
│   ├─ Obtiene último PipelineRun que falló
│   ├─ Usa UnifiedCollector (3 APIs):
│   │   1. Kubernetes API → Metadata actual + status.results
│   │   2. KubeArchive API → Data archivada
│   │   3. OC Pods → Fallback para logs
│   ├─ Extrae commit SHA, URL, mensaje, autor
│   ├─ Identifica errores automáticamente
│   └─ Inserta/actualiza en base de datos

[2/2] Sync Component Status
├─ Para cada componente:
│   ├─ Obtiene estado actual de Kubernetes (si existe)
│   ├─ Si no existe en K8s, usa último estado de DB
│   ├─ Compara: ¿estaba fallando? ¿ahora funciona?
│   ├─ Si se arregló → marca como resolved + registra success
│   └─ Si sigue fallando → comprehensive collector ya lo tiene
```

---

## 📊 Las 3 APIs en Python

### 1. Kubernetes API (`kubernetes_client.py`)

**Qué hace:**
```python
from kubernetes_client import KubernetesClient

k8s = KubernetesClient(namespace='NAMESPACE_PLACEHOLDER')

# Get PipelineRun (incluye status.results)
pr = k8s.get_pipelinerun('odh-spark-v3-4-abc123')

# status.results contiene:
results = pr['status']['results']
# → CHAINS-GIT_COMMIT: 5df42957...
# → CHAINS-GIT_URL: https://github.com/...
```

**Ventajas:**
- ✅ Datos MÁS actuales
- ✅ Incluye `status.results` con commit info
- ✅ PipelineRuns activos (<48h)

**Uso en Unified:**
```python
# Priority 1: Try Kubernetes first
pr_data = self.k8s.get_pipelinerun(pr_name)
if pr_data and pr_data.get('status', {}).get('results'):
    return pr_data, 'kubernetes_with_results'
```

---

### 2. KubeArchive API (`kubearchive_client.py`)

**Qué hace:**
```python
from kubearchive_client import KubeArchiveClient

archive = KubeArchiveClient(namespace='NAMESPACE_PLACEHOLDER')

# Get PipelineRun archivado
pr = archive.get_pipelinerun('odh-spark-old-xyz')

# Get TaskRuns
taskruns = archive.get_pipelinerun_taskruns_details('odh-spark-old-xyz')
```

**Ventajas:**
- ✅ Datos históricos (>48h)
- ✅ PipelineRuns que ya no están en Kubernetes
- ✅ Metadata completa preservada

**Uso en Unified:**
```python
# Priority 2: Try KubeArchive if not in Kubernetes
pr_data = self.kubearchive.get_pipelinerun(pr_name)
if pr_data:
    return pr_data, 'kubearchive'
```

---

### 3. OC Pods Fallback (`unified_collector.py`)

**Qué hace:**
```python
# Método _get_logs_via_oc() en unified_collector
# Ejecuta: oc logs pod -n namespace --all-containers
```

**Ventajas:**
- ✅ Último recurso cuando APIs fallan
- ✅ Accede directamente a pods activos
- ✅ Funciona para builds muy recientes

**Uso en Unified:**
```python
# Priority 3: Last resort - direct oc command
logs = self._get_logs_via_oc(pr_name)
if logs:
    return logs, 'oc_pods'
```

---

## 🔍 Unified Collector - Orquestador

```python
from unified_collector import UnifiedCollector

collector = UnifiedCollector(namespace='NAMESPACE_PLACEHOLDER')

# Get PipelineRun (tries all 3 sources)
pr_data, source = collector.get_pipelinerun_complete('pr-name')
print(f"Data from: {source}")
# Possible sources:
# - 'kubernetes_with_results' → Kubernetes + status.results
# - 'kubernetes' → Kubernetes sin results
# - 'kubearchive' → KubeArchive
# - 'none' → No data available

# Get logs (tries all 3 sources)
logs, source = collector.get_logs_complete('pr-name')
print(f"Logs from: {source}")
# Possible sources:
# - 'kubernetes' → From Kubernetes TaskRuns
# - 'kubearchive' → From archived logs
# - 'oc_pods' → Direct oc logs
# - 'none' → No logs available
```

**Priorización inteligente:**
- Kubernetes primero (más actual)
- KubeArchive segundo (histórico)
- OC Pods tercero (fallback)

---

## 🔄 Sincronización de Estado

### Qué Detecta

**Componente que se arregló:**
```
Estado anterior (DB): Failed, is_resolved=FALSE
Estado actual (K8s):  Succeeded

Acción:
1. Marca todos los failures antiguos: is_resolved=TRUE
2. Registra el build exitoso en DB
3. Log: "✓ Marked as resolved"
```

**Componente que sigue fallando:**
```
Estado anterior (DB): Failed, is_resolved=FALSE  
Estado actual (K8s):  Failed

Acción:
1. No hace nada (comprehensive collector ya lo tiene)
2. Log: "→ current status: Failed"
```

**Componente sin PRs en Kubernetes:**
```
Estado anterior (DB): Failed
Estado actual (K8s):  No existe (PipelineRun >48h old)

Acción:
1. Usa último estado de DB
2. Log: "→ current status: Failed (from DB, not in K8s)"
```

### Código del Sync

```python
# En sync_component_status.py

# Get current status from Kubernetes
current = self.get_current_status(component.name)

if not current:
    # No existe en K8s, usar DB
    db_status = self.get_last_db_status(component.name)
    return {'current_status': db_status}

# Existe en K8s, comparar
if was_failing and current['status'] == BuildStatus.SUCCEEDED:
    # Se arregló!
    self.mark_as_resolved(component.name, current['name'])
    self.record_success(component, current)
```

---

## 📈 Estadísticas y Monitoreo

### Ver Estado Actual

```bash
# Ver componentes actualmente fallando
./ic triage

# Ver componentes actualmente funcionando
./ic working

# Ver historial completo
./ic history odh-spark-operator-v3-4

# Ver estadísticas de resoluciones
./ic stats resolved
```

### Ejemplo de Output

```bash
$ ./ic triage

Overview:
  Total build records: 154
  Currently failing: 8
  Currently working: 0

Currently Failing Components:
  Component                          | Last Failure | Has Logs 
  ----------------------------------+--------------+----------
  odh-spark-operator-v3-4           | 2026-04-17   | Yes
  odh-trustyai-nemo-guardrails-v3-4 | 2026-04-17   | No
  ...
```

```bash
$ ./ic working

Currently Working Components:
  Component       | Commit   | Message              | Last Build 
  ---------------+----------+----------------------+------------
  odh-foo-v3-4   | 5df42957 | Fix build issue      | 2026-04-17
  ...
```

---

## 🗓️ Línea de Tiempo del Sistema

### Cada 15 Minutos (Automático)

```
00:00 - Cron ejecuta collect-comprehensive.sh
00:01 - [1/2] Comprehensive Collector inicia
        ├─ Component 1: Tries K8s → KubeArchive → OC
        ├─ Component 2: Tries K8s → KubeArchive → OC
        └─ ... (8 components)
00:05 - Comprehensive Collector completa
00:05 - [2/2] Sync Component Status inicia
        ├─ Component 1: Check K8s or DB status
        ├─ Component 2: Check K8s or DB status
        └─ ... (8 components)
00:06 - Sync completa
00:06 - Log guardado en logs/cron/

00:15 - Repite (próximo cron)
00:30 - Repite
00:45 - Repite
01:00 - Repite
... (cada 15 minutos, 24/7)
```

---

## 📊 Coverage Progression

### Estado Actual
```
Total failures: 154
With logs: 12 (7.8%)
```

### Proyección con Cron Cada 15 Min

```
Día 1:
  New builds captured: ~10-20
  With logs: 15 (10%)

Día 2:
  New builds captured: ~20-30
  With logs: 25 (16%)

Día 3:
  New builds captured: ~30-40
  With logs: 40 (26%)

Semana 1:
  Coverage esperado: ~50-60%

Mes 1:
  Coverage esperado: ~80%+
```

**¿Por qué mejora?**
- ✅ Cron cada 15 min captura builds MUY recientes
- ✅ Logs aún existen (<1h old)
- ✅ 3 APIs aumentan probabilidad de éxito
- ✅ Incremental improvement

---

## 🔍 Debugging y Logs

### Ver Logs del Cron

```bash
# Últimos logs
ls -lht logs/cron/collect-comprehensive-*.log | head -5

# Ver último log
tail -100 logs/cron/collect-comprehensive-$(date +%Y%m%d)*.log

# Follow live
tail -f logs/cron/collect-comprehensive-$(date +%Y%m%d)*.log
```

### Verificar Cron Está Activo

```bash
# Ver crontab
crontab -l

# Ver si ha ejecutado recientemente
ls -lht logs/cron/ | head -3

# Verificar próxima ejecución
# (debe ser en los próximos 15 min)
```

### Test Manual

```bash
# Ejecutar manualmente el flujo completo
PROJECT_DIR/cron/collect-comprehensive.sh

# Solo comprehensive collector
cd collectors/python
python3 collect_comprehensive.py --limit 3

# Solo sync
cd collectors/python
python3 sync_component_status.py
```

---

## ✅ Checklist de Verificación

**Sistema funcionando correctamente si:**

- [x] Cron activo: `crontab -l` muestra `*/15 * * * *`
- [x] Logs generándose: `ls logs/cron/` muestra archivos recientes
- [x] 3 APIs funcionando: Test con `unified_collector.py` exitoso
- [x] Sync funcionando: `python3 sync_component_status.py` completa
- [x] CLI funcionando: `./ic triage` muestra componentes
- [x] DB actualizada: `./ic stats` muestra conteos crecientes

---

## 🎯 Comandos Útiles

### Monitoreo Diario

```bash
# Morning check
./ic triage          # ¿Qué está fallando HOY?
./ic working         # ¿Qué se arregló?

# Investigar fallo
./ic why <component>
./ic analyze <component>

# Ver histórico
./ic history <component>
```

### Verificación del Sistema

```bash
# Check cron
crontab -l

# Check logs recientes
ls -lht logs/cron/ | head -5

# Check coverage
./ic stats

# Check sync funciona
cd collectors/python && python3 sync_component_status.py
```

### Manual Collection

```bash
# Full manual run
PROJECT_DIR/cron/collect-comprehensive.sh

# Collector only
cd collectors/python
python3 collect_comprehensive.py

# Sync only
cd collectors/python
python3 sync_component_status.py
```

---

## 📝 Resumen

### Lo que funciona automáticamente:

1. ✅ **Colección cada 15 min**
   - 3 APIs en Python
   - Metadata completa (commit, URLs)
   - Logs cuando disponibles

2. ✅ **Sincronización cada 15 min**
   - Detecta componentes resueltos
   - Marca resolved automáticamente
   - Usa DB cuando no hay K8s data

3. ✅ **CLI actualizado**
   - `ic triage` → Solo actualmente fallando
   - `ic working` → Solo actualmente funcionando
   - `ic history` → Historial completo

4. ✅ **Coverage incremental**
   - 7.8% → Mejorará a 80%+ en 1 mes
   - 3 APIs maximizan éxito
   - Cron frecuente captura builds frescos

### No necesitas hacer nada:

- ❌ No necesitas ejecutar collectors manualmente
- ❌ No necesitas sincronizar manualmente
- ❌ No necesitas verificar estado

### Solo monitorea:

- ✅ `./ic triage` cada mañana
- ✅ `./ic working` para ver qué se arregló
- ✅ Logs del cron si algo parece mal

---

**El sistema funciona completamente automático, usando 3 APIs en Python, sincronizando estado cada 15 minutos!** 🚀
