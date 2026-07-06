# Investigar fallos de verify-conforma en release pipelines

Guia paso a paso para investigar por que falla `verify-conforma` en una release
de stage/prod. Basada en la investigacion de wznoinsk para el fallo de EA2
(managed-fqnhj, 2026-07-01).

## Contexto: por que falla verify-conforma

El task `verify-conforma` en la release pipeline evalua TODAS las imagenes del
snapshot contra la policy de Enterprise Contract (stage o prod). Cuando falla,
puede ser por:

1. **Problema de policy** (falso positivo) → se resuelve con excepcion
2. **Problema de build** (build roto o snapshot desactualizado) → se resuelve
   arreglando el build y/o regenerando el snapshot

La mayoria de fallos de `source_image.exists` y `builtin.attestation.signature_check`
son del tipo 2: sintomas de builds rotos, no de policy incorrecta.

## Requisitos

- Estar logado en OpenShift: `oc whoami` debe devolver tu usuario
- Estar en el directorio del proyecto IC: `cd ~/claude/ci-autohealing`
- Los builds estan en el namespace `rhoai-tenant` (no en `rhtap-releng-tenant`)
- Las releases estan en `rhtap-releng-tenant`

## Paso 1: Identificar las violaciones concretas

Dado el nombre del PipelineRun de la release (lo sacas del link de Konflux UI
o del mensaje en Slack), busca el TaskRun de `verify-conforma` y extrae las
violaciones.

```bash
# Cambiar RELEASE_PR por el nombre del PipelineRun de la release
RELEASE_PR="managed-fqnhj"

# Listar todos los TaskRuns y su estado
PYTHONPATH=src python3 -c "
from clients.tekton_results import TektonResultsClient
tr = TektonResultsClient(namespace='rhtap-releng-tenant')
taskruns = tr.query_taskrun_records('${RELEASE_PR}')
for data, rn in taskruns:
    task = data.get('metadata',{}).get('labels',{}).get('tekton.dev/pipelineTask','')
    status = data.get('status',{}).get('conditions',[{}])[-1].get('reason','')
    print(f'{task:40s} {status}')
"
```

Deberias ver `verify-conforma` con estado `StepFailed`.

## Paso 2: Descargar los logs y extraer violaciones

Los logs de verify-conforma son enormes (~32MB, ~93K lineas). Extraemos solo las
violaciones:

```bash
RELEASE_PR="managed-fqnhj"

PYTHONPATH=src python3 -c "
from clients.tekton_results import TektonResultsClient
tr = TektonResultsClient(namespace='rhtap-releng-tenant')
taskruns = tr.query_taskrun_records('${RELEASE_PR}')
for data, rn in taskruns:
    task = data.get('metadata',{}).get('labels',{}).get('tekton.dev/pipelineTask','')
    if task == 'verify-conforma':
        logs = tr.get_taskrun_logs(rn)
        if logs:
            lines = logs.split('\n')
            i = 0
            while i < len(lines):
                if '✕ [Violation]' in lines[i]:
                    for j in range(i, min(i+6, len(lines))):
                        print(lines[j])
                    print()
                    i += 6
                else:
                    i += 1
        break
"
```

De cada violacion necesitas:
- **Nombre de la regla**: `source_image.exists`, `builtin.attestation.signature_check`, etc.
- **ImageRef**: la imagen concreta — de aqui sacas el componente y el SHA
- **Reason**: la causa del fallo

## Paso 3: Mapear ImageRef a componente

De cada `ImageRef`, extrae el nombre del componente. El patron es:

```
quay.io/rhoai/<nombre-imagen>-rhel9@sha256:<SHA>
        -> componente: <nombre-imagen>-<version>  (ej: -v3-5-ea-2)
```

Ejemplos:
- `quay.io/rhoai/odh-mod-arch-agent-ops-rhel9@sha256:fcee8fc...`
  → componente: `odh-mod-arch-agent-ops-v3-5-ea-2`
- `quay.io/rhoai/odh-workbench-jupyter-minimal-cpu-py312-rhel9@sha256:e2a0bb1...`
  → componente: `odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2`

Apunta tambien el SHA de cada violacion — lo necesitaras para comparar.

## Paso 4: Comprobar el estado del build del componente

Los builds estan en el namespace `rhoai-tenant` (NO en `rhtap-releng-tenant`
donde estan las releases).

### 4a. Si la violacion es `builtin.attestation.signature_check`

Sin build exitoso no se genera la attestation SLSA firmada → la verificacion
de firma falla.

```bash
COMPONENT="odh-mod-arch-agent-ops-v3-5-ea-2"

PYTHONPATH=src python3 -c "
from clients.tekton_results import TektonResultsClient
tr = TektonResultsClient(namespace='rhoai-tenant')
parts = [
    \"(data_type == 'tekton.dev/v1beta1.PipelineRun' || data_type == 'tekton.dev/v1.PipelineRun')\",
    \"data.metadata.labels['pipelines.appstudio.openshift.io/type']=='build'\",
    \"data.metadata.labels['appstudio.openshift.io/component']=='${COMPONENT}'\",
]
records = tr._query_records(' && '.join(parts), page_size=5)
for r in records:
    d = tr._decode_record(r)
    if d:
        name = d['metadata']['name']
        created = d['metadata']['creationTimestamp']
        conds = d.get('status',{}).get('conditions',[])
        status = conds[-1]['reason'] if conds else 'Unknown'
        print(f'{created}  {status:20s}  {name}')
"
```

Si el ultimo build esta en `Failed`, esa es la root cause.

Para saber POR QUE fallo el build, busca el TaskRun fallido dentro de ese build:

```bash
BUILD_PR="odh-mod-arch-agent-ops-v3-5-ea-2-on-push-csgfh"

PYTHONPATH=src python3 -c "
from clients.tekton_results import TektonResultsClient
tr = TektonResultsClient(namespace='rhoai-tenant')
for data, rn in tr.query_taskrun_records('${BUILD_PR}'):
    conds = data.get('status',{}).get('conditions',[])
    if conds and conds[-1].get('status') == 'False':
        task = data['metadata']['labels'].get('tekton.dev/pipelineTask','')
        reason = conds[-1].get('reason','')
        msg = conds[-1].get('message','')[:300]
        print(f'Task:    {task}')
        print(f'Reason:  {reason}')
        print(f'Message: {msg}')
"
```

### 4b. Si la violacion es `source_image.exists`

La source container image no se publico. Puede ser porque:
1. El build fallo/timeout antes de publicar la source image
2. El snapshot de la release apunta a un build antiguo/fallido

```bash
COMPONENT="odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2"

PYTHONPATH=src python3 -c "
from clients.tekton_results import TektonResultsClient
tr = TektonResultsClient(namespace='rhoai-tenant')
parts = [
    \"(data_type == 'tekton.dev/v1beta1.PipelineRun' || data_type == 'tekton.dev/v1.PipelineRun')\",
    \"data.metadata.labels['pipelines.appstudio.openshift.io/type']=='build'\",
    \"data.metadata.labels['appstudio.openshift.io/component']=='${COMPONENT}'\",
]
records = tr._query_records(' && '.join(parts), page_size=5)
for r in records:
    d = tr._decode_record(r)
    if d:
        name = d['metadata']['name']
        created = d['metadata']['creationTimestamp']
        conds = d.get('status',{}).get('conditions',[])
        status = conds[-1]['reason'] if conds else 'Unknown'
        sha = ''
        for res in d.get('status',{}).get('results',[]):
            if res.get('name') == 'IMAGE_DIGEST':
                sha = res['value'][:25]
        print(f'{created}  {status:20s}  sha={sha:30s}  {name}')
"
```

Ahora compara el SHA de la violacion (paso 2) con los SHA de los builds.
Si coincide con un build fallido/timeout, el snapshot esta usando el build
equivocado.

## Paso 5: Trazar el SHA (como hizo wznoinsk)

Este es el paso clave que wznoinsk explico en Slack. La logica es:

1. Coge un SHA de las violaciones del verify-conforma, por ejemplo:
   `sha256:6e6e80d5d6849a95f53b471bcff60469107e1013d1fa5d5992031a02984802ee`

2. Buscalo en el historial de builds del componente (paso 4b)

3. Si ese SHA pertenece a un build con `PipelineRunTimeout` o `Failed`:
   - El snapshot de la release esta usando un build roto
   - El build puede haber generado la imagen (parcialmente) pero NO la source image
   - La release falla porque verifica la source image, que no existe

4. Si hay un build posterior exitoso con un SHA diferente:
   - El retrigger funciono, pero el snapshot no lo incluyo
   - Hay que regenerar el nightly/RC para que use el build correcto

Ejemplo real (managed-fqnhj):
```
SHA de la violacion:  sha256:6e6e80d5d6849...
Build qjhfc (timeout): sha256:6e6e80d5d6849...  ← COINCIDE, este es el problema
Build j76bz (ok):       sha256:8ad6545b82014...  ← retrigger exitoso, SHA diferente
```
→ El snapshot usa qjhfc (timeout). Hay que regenerar con j76bz.

wznoinsk tambien lo verifico en la UI de Konflux:
```
https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ns/rhoai-tenant/
  applications/rhoai-v3-5-ea-2/pipelineruns/<build-pr-name>/logs?task=build-image-index
```
El link al build fallido muestra que produjo esas imagenes pero no completo.

## Paso 6: Decidir la accion

| Violacion | Root cause | Accion |
|-----------|-----------|--------|
| `attestation.signature_check` | Build fallido | Escalar a build guardian. Retrigger del build. |
| `source_image.exists` | Build fallido/timeout | Escalar a build guardian. Retrigger del build. |
| `source_image.exists` | Snapshot con build viejo | Pedir nuevo nightly/RC que incluya el build correcto. |
| Cualquiera | Falso positivo real | Crear MR con excepcion en la policy (stage/prod). |

**Importante**: NO pongas excepciones si el problema es un build roto. Las
excepciones son para falsos positivos reales, no para enmascarar builds rotos.

## Paso 7: Trigger nuevo nightly para regenerar el snapshot

Si la root cause es un snapshot con builds viejos/rotos (paso 5), hay que
regenerar el nightly para que incluya los builds exitosos.

### 7a. Verificar que los builds estan green

Antes de triggear, confirma que los builds de los componentes afectados ya
estan exitosos (paso 4). Si no, primero hay que resolver el build failure.

### 7b. Trigger nightly

1. Ir a GitHub Actions de rhods-devops-infra:
   https://github.com/red-hat-data-services/rhods-devops-infra/actions/workflows/trigger-nightlies.yaml

2. Click "Run workflow" → seleccionar rama y version (ej: `rhoai-3.5-ea.2`)

3. Si tambien hace falta rebuild del operator:
   https://github.com/red-hat-data-services/rhods-operator/actions/workflows/trigger-nightly-operator-build.yaml

4. Documentacion completa del proceso nightly:
   https://docs.google.com/document/d/1Q5M-E-QqUNI0bHSc_662MQ7-xgn2JgQFSKh9c2lm4gU/edit

### 7c. Reintentar push-to-stage

Una vez el nightly completa exitosamente con el nuevo snapshot, Moulali (o
quien gestione la release) puede reintentar el push-to-stage. El nuevo
snapshot incluira los builds correctos y verify-conforma deberia pasar.

### 7d. Verificar que conforma pasa

Despues del push-to-stage, verificar que verify-conforma paso:

```bash
RELEASE_PR="managed-XXXXX"  # nuevo PipelineRun

PYTHONPATH=src python3 -c "
from clients.tekton_results import TektonResultsClient
tr = TektonResultsClient(namespace='rhtap-releng-tenant')
taskruns = tr.query_taskrun_records('${RELEASE_PR}')
for data, rn in taskruns:
    task = data.get('metadata',{}).get('labels',{}).get('tekton.dev/pipelineTask','')
    if task == 'verify-conforma':
        status = data.get('status',{}).get('conditions',[{}])[-1].get('reason','')
        print(f'verify-conforma: {status}')
"
```

Si sale `Succeeded`, la release puede continuar.

## Ejemplo completo: EA2 managed-fqnhj (2026-07-01)

### Violaciones encontradas
- `source_image.exists` x5 → `odh-workbench-jupyter-minimal-cpu-py312`
- `builtin.attestation.signature_check` x5 → `odh-mod-arch-agent-ops`

### Root cause 1: odh-mod-arch-agent-ops
- Ultimo build `csgfh` (Jul 01 16:32): **Failed**
- Task fallido: `sast-shell-check`
- Razon: `TaskRunImagePullFailed` — no pudo hacer pull de `quay.io/konflux-ci/oras:latest`
- Es un problema de infra de Konflux, no del componente
- Accion: retrigger del build (build guardian)

### Root cause 2: odh-workbench-jupyter-minimal-cpu-py312
- Build `qjhfc` (Jun 30): **PipelineRunTimeout** — SHA `6e6e80d5...`
- Build `j76bz` (Jun 30): **Completed** (retrigger) — SHA `8ad6545b...`
- El snapshot de la release usaba SHA `6e6e80d5...` (del build con timeout)
- Accion: regenerar nightly/RC para incluir el build exitoso `j76bz`

### Resultado
- No se necesitan excepciones en la policy
- Se necesita: rebuild de mod-arch-agent-ops + nuevo nightly con jupyter correcto

### Resolucion (2026-07-02)
- Build de mod-arch-agent-ops: ya green (rebuild exitoso)
- Build de jupyter-minimal-cpu: ya green (rebuild exitoso j76bz)
- Wiktor confirmo: "conforma was fixed to green yesterday, no outstanding
  work on conforma side"

### Problema: nuevo nightly sigue con SHA roto

Triggear un nuevo nightly NO fue suficiente. El nightly del 2 de julio
(build `94ngg`, 05:24) sigue usando el SHA del build fallido `qjhfc`.

**Por que pasa esto:**
- El build `qjhfc` (timeout) publico las imagenes en quay ANTES de fallar.
  El timeout ocurrio despues del push de la imagen pero antes del
  source-image. Por eso quay tiene el SHA del build roto.
- El build exitoso `j76bz` publico imagenes con SHA diferente, pero el
  mecanismo de "nudging" en el operator no se actualizo.
- El nightly FBC coge las imagenes desde quay via el operator-nudging config,
  no directamente de Konflux. Si el nudging no ocurrio para `j76bz`, el
  nightly sigue usando el SHA viejo.

**Solucion inmediata:**
1. Actualizar manualmente el SHA en el fichero de nudging del operator:
   https://github.com/red-hat-data-services/rhods-operator/blob/rhoai-3.5-ea.2/build/operator-nudging.yaml
   (linea ~201 para odh-workbench-jupyter-minimal-cpu-py312)
   Cambiar el SHA al del build exitoso `j76bz`.

2. Una vez mergeado, triggear nuevo nightly:
   https://github.com/red-hat-data-services/rhods-devops-infra/actions/workflows/trigger-nightlies.yaml

3. Reintentar push-to-stage con el nuevo snapshot.

**Solucion a largo plazo (pregunta abierta):**
- Wiktor: "the check (or prevention) to not include unfinished builds in
  the fbc should be done way earlier - at the operator level"
- Moulali: "Have to develop such check"
- Se necesita un consistency check en el operator-processor que valide que
  el build asociado al SHA esta Completed antes de incluirlo en el FBC.
  Esto NO es responsabilidad de conforma-reporter.
- Deepak Chourasia: el nightly coge latest de quay, no de Konflux. El
  problema es que el nudging no se triggeo para el build exitoso.

---

## Anexo A: Como funciona el nudging (operator-processor)

El "nudging" es el mecanismo por el cual los SHAs de las imagenes de cada
componente se propagan desde quay hasta el FBC (File-Based Catalog) que
se usa en la release. Entender esta cadena es clave para diagnosticar
por que un snapshot puede contener SHAs de builds rotos.

### La cadena completa

ckodama confirmo (Jul 2 5:10 PM) que tanto el operator-processor como
el bundle-processor usan el fichero de nudging, no quay directamente:

> "from this it looks like the operator-processor and bundle-processor are
> using the image sha from the nudged file (operator-nudging.yaml) rather
> than grabbing the latest from quay"

```
quay.io/rhoai/<imagen>
       │
       ▼
operator-processor (GitHub Action nightly en rhods-operator)
       │  Lee los SHA "latest" de cada imagen en quay
       │  Actualiza operator-nudging.yaml con esos SHAs
       ▼
rhods-operator/build/operator-nudging.yaml
       │  Contiene el mapping imagen → SHA para cada componente
       │  Ejemplo linea ~201: odh-workbench-jupyter-minimal-cpu-py312
       ├──────────────────────────────────────────┐
       ▼                                          ▼
RHOAI-Build-Config/                    bundle-processor (GitHub Action)
  catalog/v4.22/rhods-operator/          Genera el CSV con los SHAs
  catalog.yaml                           del nudging file
       │  relatedImages generadas          → rhods-operator.clusterserviceversion.yaml
       │  a partir de nudging
       ▼
rhoai-fbc-fragment (build en Konflux)
       │  Construye el FBC fragment usando el catalog.yaml
       │  Este FBC fragment tiene un SHA propio
       ▼
stage promoter (GitHub Action en rhods-devops-infra)
       │  Usa el SHA del FBC fragment como input
       │  Lanza el push-to-stage release pipeline en Konflux
       ▼
verify-conforma (task dentro del release pipeline)
       Evalua TODAS las imagenes del FBC contra la policy EC
```

### Ficheros clave

| Fichero | Repo | Descripcion |
|---------|------|-------------|
| `build/operator-nudging.yaml` | rhods-operator | Mapping imagen→SHA, actualizado por operator-processor. Fuente de verdad para operator-processor Y bundle-processor. |
| `build/operands-map.yaml` | rhods-operator | Mapa de operandos, usado por Deepak para verificar SHAs con tracer |
| `catalog/v4.22/rhods-operator/catalog.yaml` | RHOAI-Build-Config | relatedImages generadas a partir de nudging |
| `bundle/manifests/rhods-operator.clusterserviceversion.yaml` | RHOAI-Build-Config | CSV generado por bundle-processor, tambien usa SHAs del nudging |

### El problema de los builds con timeout

**Evidencia**: Moulali confirmo en Slack (Jul 2 1:38 PM):
> "image was already pushed to quay. and conforma is using the same build."
> "we have another build after this which is not being picked up."

Un build que hace timeout puede haber publicado las imagenes en quay
**antes** de fallar. El timeout ocurre despues del `build-image-index`
(que publica) pero antes del `source-build` (que genera la source image).

Deepak Chourasia confirmo el mecanismo (Jul 2 1:19 PM):
> "it doesn't look at Konflux, it picksup latest from quay"

Resultado:
- quay tiene el SHA del build roto (la imagen se publico)
- El operator-processor lee ese SHA como "latest" de quay
- Lo bake en `operator-nudging.yaml`
- El FBC se construye con el SHA roto
- verify-conforma falla porque la source image no existe

El operator-processor **no verifica** que el build de Konflux asociado
al SHA este en estado `Completed`. Solo lee lo que hay en quay.

---

## Anexo B: Troubleshooting chain (EA2 managed-fqnhj, 2026-07-02)

Cadena de troubleshooting completa, con las evidencias de cada persona
que participo en la investigacion.

### Contexto: el nightly no corrigio el problema

Moulali reporto que el push-to-stage seguia fallando despues del nightly
del 2 de julio. Deepak pregunto por que, asumiendo que un nuevo nightly
deberia coger las imagenes correctas:

> **Deepak** [1:08 PM]: "@Moulali even if nudging did not happen, we built
> a new nightly right? so all the latest images should automatically get
> included"
>
> **Moulali** [1:15 PM]: "Yes we built a new nightly today and that
> component build was green on 30th June."
>
> **Deepak** [1:15 PM]: "then there is no reason for it to pickup an old
> image"

Pero Moulali demostro que el nightly SI usaba el build roto:

> **Moulali** [1:17 PM]: Nightly notification:
> `quay.io/rhoai/rhoai-fbc-fragment:rhoai-3.5-ea.2@sha256:c9042945d482...`
> Build: `rhoai-fbc-fragment-v3-5-ea-2-on-schedule-94ngg`
>
> **Moulali** [1:18 PM]: "It has picked up this [qjhfc] which was failed
> due to timeout"
>
> **Moulali** [1:19 PM]: "This is the latest [j76bz] which is green"

Builds en Konflux UI:
- **Build roto (qjhfc)**: https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ns/rhoai-tenant/applications/rhoai-v3-5-ea-2/pipelineruns/odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2-on-push-qjhfc
- **Build exitoso (j76bz)**: https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ns/rhoai-tenant/applications/rhoai-v3-5-ea-2/pipelineruns/odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2-on-push-j76bz

### Deepak verifica el operands-map con tracer

Deepak uso el tracer para verificar que el nightly SI tenia el SHA correcto
en operands-map.yaml:

> **Deepak** [1:27 PM]: "the nightly build has correct image, checked using
> tracer" https://github.com/red-hat-data-services/rhods-operator/blob/804b5e2759d41e926d3a7a7762faf7d2c1e8676d/build/operands-map.yaml#L201C11-L201C92
>
> "Can you check if your conforma run is using the correct build?"

Esto confirmo que `operands-map.yaml` tenia el SHA correcto, pero el
FBC fragment no lo usaba. La discrepancia estaba en `operator-nudging.yaml`.

### Moulali identifica el nudging como el problema

> **Moulali** [12:57 PM]: "no clue found why that nudging for
> odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2 did not happen."
>
> "Checking if we can updated the latest sha manually here"
> https://github.com/red-hat-data-services/rhods-operator/blob/64e75eabaf0269c4a5c81e2762335807b4dae937/build/operator-nudging.yaml#L201
>
> "@ckodama can we?"

### Wiktor sobre donde debe ir el fix

> **wznoinsk** [12:54 PM]: "the conforma will always highlight these
> inconsistencies since they break the compliance, but the check (or
> prevention) to not include unfinished builds in the fbc should be done
> way earlier - at the operator level I guess"
>
> **Moulali**: "Have to develop such check"

### ckodama: troubleshooting sistematico paso a paso

ckodama hizo el troubleshooting completo y lo documento en un thread
de Slack para que Moulali pudiera seguirlo. Cada paso con su evidencia:

#### Paso 1: Confirmar que el build esta green pero el SHA no coincide

> **ckodama** [~4:45 PM]: "starting with
> `[Violation] source_image.exists`
> `ImageRef: quay.io/rhoai/odh-workbench-jupyter-minimal-cpu-py312-rhel9@sha256:57ae15927111044a5695406cd748551ebcea42509a14132eda879d5d5af857d8`
>
> first thing is, let's see what the state of the konflux build for this
> is. I can see here that the latest build for this is green, as of Jun 30.
>
> Looking at the latest green build, i want to confirm the image digest
> is correct. I can see from the build results section that the SHA of the
> green build is `sha256:8ad6545b82014b75b799ff301274f0d21f72c778a35b9ffefc7e460c4417da65`
> — this doesn't match, and it doesn't match ANY of the SHAs in the
> conforma violations (usually the conforma violation will be duplicated,
> one for the top level sha, and one more for each of the cpu arch shas)
>
> so, the conclusion here, so far, is that the nightly is not picking up
> the latest green build of the component"
>
> **Moulali**: "thats correct"

**SHAs clave:**
| SHA | Pertenece a | Estado |
|-----|-------------|--------|
| `sha256:6e6e80d5d6849a95f53b471bcff60469107e1013d1fa5d5992031a02984802ee` | Build qjhfc | PipelineRunTimeout |
| `sha256:8ad6545b82014b75b799ff301274f0d21f72c778a35b9ffefc7e460c4417da65` | Build j76bz | Completed (green) |
| `sha256:57ae15927111044a5695406cd748551ebcea42509a14132eda879d5d5af857d8` | Build qjhfc (arch-specific) | PipelineRunTimeout |

#### Paso 2: Trazar el FBC fragment usado por el stage promoter

> **ckodama**: "Now, looking at the stage promoter run:
> https://github.com/red-hat-data-services/rhods-devops-infra/actions/runs/28592998136
>
> It is using `quay.io/rhoai/rhoai-fbc-fragment@sha256:e576379ea5fc0069eecb0cc15e836605a34d0b9a0a0c1e92cebdb8256bb2e3d0`
> as the input. Does this match the nightly build (the one that is still
> running the FIPS check right now)?
>
> looking at the latest fbc (-on-schedule) build here, i can see that its
> build sha is `sha256:e576379ea5fc...`, so that lines up correctly. Now
> need to work backwards - what workbench image SHA was included in this
> build?"

**Evidencia**: Stage promoter run → FBC fragment SHA → match confirmado.

#### Paso 3: Verificar el SHA roto en RHOAI-Build-Config/catalog.yaml

> **ckodama**: "can do an initial confirmation by looking at the relatedImages
> of the catalog, at the commit sha that created the fbc fragment
> https://github.com/red-hat-data-services/RHOAI-Build-Config/blob/626a01dd51283a6d1622af8a4b3d918a8ff520d3/catalog/v4.22/rhods-operator/catalog.yaml
>
> I can see here that the workbench-minimal-cpu-py312 image sha is
> `sha256:6e6e80d5d6849a95f53b471bcff60469107e1013d1fa5d5992031a02984802ee`,
> which is one of the SHAs that is in the conforma violation. more evidence
> that it's not picking up the latest green build"
>
> **Moulali**: "CORRECT"

**Evidencia**: `catalog.yaml` en commit `626a01dd` contiene el SHA roto.

#### Paso 4: Trazar al operator-processor y operator-nudging.yaml

> **ckodama**: "Now it's worth jumping to the nightly operator processor,
> since that is where the image shas get updated. the latest run of nightly
> operator processor was
> https://github.com/red-hat-data-services/rhods-operator/actions/runs/28587599451
>
> it created a commit `66cc90bb`, and looking at the nudge file:
> https://github.com/red-hat-data-services/rhods-operator/blob/66cc90bb77710450b7de2b5b6967a289229ac402/build/operator-nudging.yaml#L200C1-L201C141
>
> it's using the 'bad' image sha"
>
> **Moulali**: "YES"
> "I also triggered the operator processor manually, but nudging did not
> happend"

**Evidencia**: Commit `66cc90bb` del operator-processor contiene el SHA
roto en linea 200-201 de `operator-nudging.yaml`.

#### Paso 5: Verificar el bundle trigger (bundle-processor)

> **ckodama** [5:09 PM]: "ok, looking at the bundle trigger next -
> https://github.com/red-hat-data-services/RHOAI-Build-Config/actions/runs/28588655238
>
> this one created RHOAI build config commit `52b20c7`, and looking at the
> cluster service version, it is also using the 'bad' sha at `6e6e..`"
>
> **ckodama** [5:10 PM]: "@Rishab Prasad from this it looks like the
> operator-processor and bundle-processor are using the image sha from the
> nudged file (operator-nudging.yaml) rather than grabbing the latest from
> quay"

**Evidencia**: Bundle trigger run `28588655238` → commit `52b20c7` →
`rhods-operator.clusterserviceversion.yaml` tambien contiene el SHA roto.
Esto confirma que el bundle-processor lee de `operator-nudging.yaml`,
no de quay directamente.

#### Paso 6: Confirmar que el nudging nunca ocurrio

ckodama filtro los PRs de nudging para el componente afectado:

> **ckodama** [5:22 PM]: "looking at a filtered view of MRs into
> rhods-operator - the nudging for the 'good' image sha `8ad6545` never
> happened :disappointed:"
> https://github.com/red-hat-data-services/rhods-operator/pulls?q=is%3Apr++base%3Arhoai-3.5-ea.2+odh-workbench-jupyter-minimal

**Evidencia**: No existe ningun PR de nudging con el SHA del build exitoso.
El nudging simplemente no se ejecuto para este componente.

#### Paso 7: Fix manual — PR #33945

> **ckodama** [5:26 PM]: "so @Moulali we need to update
> operator-nudging.yaml with the correct SHA, which will simulate what a
> nudge would do, and then rerun the nightly build"
>
> **ckodama** [5:31 PM]: "went ahead and PRd this"
> https://github.com/red-hat-data-services/rhods-operator/pull/33945
> ("manual nudge of workbench image")
>
> **Moulali** [5:29 PM]: "Yes, this is what i was proposing in the thread"
>
> **ckodama** [5:30 PM]: "well then I think you were spot on :slightly_smiling_face:"

**Evidencia**: PR #33945 mergeado → ckodama triggeo nuevo nightly stage
build a las 5:38 PM.

### Jul 3: el fix manual no fue suficiente — y Deepak contradice el diagnostico

Despues de mergear PR #33945 y triggear nuevo nightly, el bundle build
del 3 de julio seguia con el SHA roto:

> **Moulali** [Jul 3 7:57 AM]: "@Deepak Chourasia Same problem nuding is
> not happening. We decided to update the operator-nuding.yaml manually and
> triggered the nightly build and then push to stage yesterday. We still
> see the same conforma failure!
>
> nightly bundle build -
> https://github.com/red-hat-data-services/RHOAI-Build-Config/actions/runs/28603567080/job/84817839039
>
> it still has the 'bad' `6e6e80` sha in the bundle..."

Deepak investigo y llego a una conclusion diferente:

> **Deepak** [8:02 AM]: "I confirmed yesterday that nudging cannot cause
> such issue. So just to clarify no build issues here, it just needs to
> pick the right build"
>
> **Deepak** [8:35 AM]: "I verified, the latest workbench image is
> `6e6e80d5d684` and the FBC build is using the latest image, ... what you
> are posting `8ad6545b8201` is an old image and should not be used."
>
> **Deepak** [8:37 AM]: "I can explain it over a call, but IMO you need to
> do more investigation on this issue, it doesn't seems to be the build
> issue"

**Esto contradice lo que ckodama habia encontrado el dia anterior.** Segun
Deepak, `6e6e80` (el SHA del build con timeout qjhfc) ES el latest en
quay y `8ad6545` (el SHA del build exitoso j76bz) es un SHA antiguo.
Esto sugiere que quay resolvio correctamente el "latest" al SHA del build
mas reciente que publico imagenes — que fue qjhfc (timeout), no j76bz.

> **Moulali** [8:50 AM]: "I already checked this twice yesterday, and
> Chris also verified it once. I'll check it again to be sure and get back
> to you with an update."

### Resolucion final (Jul 3): nuevo build + nuevo nightly

Despues de una llamada con Rishab Prasad (11:27 AM), Moulali decidio
la solucion correcta: triggear un build completamente nuevo del
workbench (no reusar j76bz) y esperar al nightly:

> **Moulali** [11:39 AM]: Imagen confirmada:
> `quay.io/rhoai/odh-workbench-jupyter-minimal-cpu-py312-rhel9:rhoai-3.5-ea.2@sha256:8ad6545b82014b75b799ff301274f0d21f72c778a35b9ffefc7e460c4417da65`
>
> **Moulali** [11:41 AM]: Build de referencia (j76bz):
> https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ns/rhoai-tenant/applications/rhoai-v3-5-ea-2/pipelineruns/odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2-on-push-j76bz
>
> **Moulali** [11:47 AM]: "I had started a new workbench build which is
> green and nightly build is inprogress..."
>
> **Moulali** [12:17 PM]: "Triggered Push to stage"

**Evidencia**: La solucion final fue:
1. Triggear un **nuevo build** de workbench (no el j76bz anterior)
2. Esperar a que el nightly cogiera el nuevo SHA via nudging
3. Relanzar push-to-stage con el nuevo FBC fragment

### Leccion aprendida

El diagnostico de ckodama (el nudging no cogio el SHA de j76bz) fue
correcto en cuanto al mecanismo, pero la solucion de "actualizar
manualmente operator-nudging.yaml" no fue suficiente porque:

1. El bundle-processor no reflejo el cambio manual (posible cache o
   fuente de datos diferente)
2. Deepak indico que `6e6e80` era en realidad el SHA "latest" en quay,
   lo que significa que el operator-processor funciono correctamente —
   cogio lo que habia en quay. El problema es que quay tenia el SHA del
   build con timeout porque ese build publico sus imagenes antes de fallar.

La solucion real fue forzar un **nuevo build** para que quay tuviera un
SHA nuevo y correcto como "latest", en lugar de intentar parchear el
nudging manualmente.

### Conclusion: cadena completa con evidencias

```
Build qjhfc (timeout, Jun 30)
  → publica imagen en quay (SHA 6e6e80d5...)
  → operator-processor nightly (run 28587599451, commit 66cc90bb)
     lee SHA de quay, NO verifica estado del build en Konflux
  → escribe SHA roto en operator-nudging.yaml linea 200-201
     (https://github.com/red-hat-data-services/rhods-operator/blob/66cc90bb77710450b7de2b5b6967a289229ac402/build/operator-nudging.yaml#L200)
  → catalog.yaml en RHOAI-Build-Config se genera con SHA roto
     (https://github.com/red-hat-data-services/RHOAI-Build-Config/blob/626a01dd51283a6d1622af8a4b3d918a8ff520d3/catalog/v4.22/rhods-operator/catalog.yaml)
  → FBC fragment build (94ngg) incluye relatedImages con SHA roto
     (sha256:c9042945d482... / e576379ea5fc...)
  → stage promoter run (28592998136) usa este FBC fragment
  → verify-conforma falla: source_image.exists
     (la source image del build con timeout no existe)
```

**Build exitoso** (j76bz, Jun 30) produjo SHA `8ad6545b...` pero el
operator-processor nunca lo cogio porque quay ya tenia el SHA del build
con timeout como "latest".

### Intento 1 (Jul 2): fix manual en operator-nudging.yaml — NO funciono

1. ckodama creo PR #33945 para actualizar manualmente `operator-nudging.yaml`
   con el SHA del build exitoso `j76bz` (`sha256:8ad6545b...`)
   https://github.com/red-hat-data-services/rhods-operator/pull/33945
2. Mergeado y triggeado nuevo nightly a las 5:38 PM
3. **Resultado**: el bundle build del 3 de julio seguia con el SHA
   `6e6e80`. El fix manual no fue suficiente.

### Intento 2 (Jul 3): nuevo build de workbench — FUNCIONO

1. Moulali triggeo un nuevo build de workbench desde Konflux
2. El build salio green
3. El nightly cogio el nuevo SHA correctamente
4. Moulali triggeo push-to-stage a las 12:17 PM

### Por que el fix manual no funciono

Deepak verifico que `6e6e80` era el SHA "latest" en quay — el
operator-processor funciono correctamente cogiendo lo que habia en quay.
El problema era que quay tenia como "latest" el SHA del build con timeout
(qjhfc) porque ese build publico imagenes antes de fallar.

La solucion correcta era forzar un **nuevo build** para que quay tuviera
un nuevo SHA como "latest", no parchear el nudging manualmente.

### Solucion a largo plazo (pendiente)

**Jira**: [RHOAIENG-73431](https://redhat.atlassian.net/browse/RHOAIENG-73431) —
"Investigate Quay image push discrepancy for odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2"
(creado por Moulali, Jul 3, status: New, sin asignar)

El operator-processor necesita un consistency check que valide que el
build de Konflux asociado al SHA esta en estado `Completed` antes de
incluirlo en el nudging. Wiktor y Moulali estan de acuerdo en que este
check debe hacerse a nivel del operator, no en conforma.

El Jira documenta el impacto: 5 violaciones `source_image.exists`,
release pipeline `managed-scdhb` bloqueada, todas las arquitecturas
afectadas (ppc64le, s390x, amd64, arm64 + multi-arch manifest).

Responsables: ckodama + Deepak Chourasia.

### Analisis del codigo del operator-processor (mchamsed, Jul 3)

mchamsed (Mohamed Chamseddine) analizo el codigo fuente del operator-processor
y descubrio dos cosas importantes:

#### 1. bundle-patch.yaml lleva congelado desde el 18 de mayo

mchamsed comparo los digests entre `bundle-patch.yaml` y `operator-nudging.yaml`
en la rama `rhoai-3.5-ea.2`:

- **110 entradas compartidas** entre ambos ficheros
- **107 de 110 tienen digests diferentes** — solo 3 coinciden
- Los digests de `bundle-patch.yaml` no se han actualizado desde que se
  creo la rama desde `rhoai-3.5-ea.1` el 18 de mayo

**Moulali aclaro** que esto no importa para los builds: "build infrastructure
always fetches the latest image directly from Quay, so it doesn't matter."
Es decir, `bundle-patch.yaml` esta stale pero la infra de build ignora esos
digests y siempre coge la imagen directamente de Quay.

**Pregunta abierta de mchamsed**: si la infra de build siempre coge de Quay,
por que no funciono para `odh-workbench-jupyter-minimal-cpu-py312`? Moulali
confirmo que eso es exactamente lo que hay que investigar en RHOAIENG-73431.

#### 2. Mecanismo real del operator-processor (lectura del codigo)

mchamsed leyo `operator-processor.py` y documento la funcion
`get_all_latest_images_using_operands_map()` (linea 137):

```
1. Itera por operands-map.yaml (skipping FBC, BUNDLE, ODH_OPERATOR)
2. Para cada imagen, parsea registry/org/repo del valor actual
3. Llama a quay_controller.get_all_tags(repo, rhoai_version)
   → Consulta la API de Quay buscando tags que coincidan con la rama
4. Para cada tag que coincide:
   a. Busca un tag con sufijo ".sig" (firma cosign)
   b. Si lo encuentra → la imagen esta firmada
   c. Coge el manifest_digest de ese tag
5. Tambien extrae labels git.url y git.commit del manifest
```

**Dato clave**: el operator-processor **no coge cualquier imagen de Quay** —
solo coge imagenes que tienen un tag `.sig` asociado (es decir, imagenes
**firmadas**). Si el build con timeout publico la imagen pero la firma no
se genero correctamente, eso podria explicar comportamientos inesperados.

Este detalle complementa el Anexo A (mecanismo de nudging) con el flujo
real del codigo.

#### 3. Verificacion de firmas en Quay (Jul 3, analisis propio)

Consultamos la API de Quay con `skopeo` para verificar si el build con
timeout fue firmado (lo cual explicaria por que el operator-processor lo
cogio):

| Build | SHA | `.sig` | `.src` | `.att` | `.sbom` |
|-------|-----|:------:|:------:|:------:|:-------:|
| qjhfc (timeout) | `6e6e80d5d684...` | SI | **NO** | SI | SI |
| j76bz (exitoso) | `8ad6545b8201...` | SI | SI | SI | SI |
| Nuevo (Moulali Jul 3) | `cb4f5b201f21...` | SI | SI | SI | SI |

**Conclusion**: el build con timeout **si fue firmado** (tiene `.sig`), por
lo que el operator-processor lo cogio correctamente segun su logica. La unica
diferencia es que le falta el `.src` (source container), que es exactamente
lo que evalua la regla `source_image.exists` en conforma.

Esto confirma que el pipeline de Konflux **firma las imagenes antes de que
el build complete todas sus fases**. El timeout ocurre en una fase posterior
(generacion del source container), pero la imagen ya esta firmada y publicada.

El tag `rhoai-3.5-ea.2` actualmente apunta al nuevo build de Moulali
(SHA `cb4f5b20...`, commit `a15f5837...`, creado Jul 3 08:30 UTC).

### Referencias completas

| Recurso | URL |
|---------|-----|
| Release pipeline (managed-fqnhj) | https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ns/rhtap-releng-tenant/applications/rhoai-v3-5-ea-2/pipelineruns/managed-fqnhj |
| Build roto (qjhfc, timeout) | https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ns/rhoai-tenant/applications/rhoai-v3-5-ea-2/pipelineruns/odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2-on-push-qjhfc |
| Build exitoso (j76bz) | https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ns/rhoai-tenant/applications/rhoai-v3-5-ea-2/pipelineruns/odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2-on-push-j76bz |
| Stage promoter run | https://github.com/red-hat-data-services/rhods-devops-infra/actions/runs/28592998136 |
| Operator-processor run (nightly) | https://github.com/red-hat-data-services/rhods-operator/actions/runs/28587599451 |
| operator-nudging.yaml (commit con SHA roto) | https://github.com/red-hat-data-services/rhods-operator/blob/66cc90bb77710450b7de2b5b6967a289229ac402/build/operator-nudging.yaml#L200 |
| operator-nudging.yaml (Moulali check) | https://github.com/red-hat-data-services/rhods-operator/blob/64e75eabaf0269c4a5c81e2762335807b4dae937/build/operator-nudging.yaml#L201 |
| RHOAI-Build-Config catalog.yaml | https://github.com/red-hat-data-services/RHOAI-Build-Config/blob/626a01dd51283a6d1622af8a4b3d918a8ff520d3/catalog/v4.22/rhods-operator/catalog.yaml |
| operands-map.yaml (Deepak tracer) | https://github.com/red-hat-data-services/rhods-operator/blob/804b5e2759d41e926d3a7a7762faf7d2c1e8676d/build/operands-map.yaml#L201 |
| Bundle trigger run | https://github.com/red-hat-data-services/RHOAI-Build-Config/actions/runs/28588655238 |
| PR #33945 (manual nudge fix) | https://github.com/red-hat-data-services/rhods-operator/pull/33945 |
| Nudging PRs filtrados (ckodama) | https://github.com/red-hat-data-services/rhods-operator/pulls?q=is%3Apr++base%3Arhoai-3.5-ea.2+odh-workbench-jupyter-minimal |
| Nightly bundle build (Jul 3, aun roto) | https://github.com/red-hat-data-services/RHOAI-Build-Config/actions/runs/28603567080/job/84817839039 |
| Nightly FBC fragment (94ngg, Jul 2) | `quay.io/rhoai/rhoai-fbc-fragment:rhoai-3.5-ea.2@sha256:c9042945d4823676ae66075199dc964482a6fa2bf5e7a055e498c7ab7e78ae1c` |
| FBC fragment usado por stage promoter Jul 2 | `quay.io/rhoai/rhoai-fbc-fragment@sha256:e576379ea5fc0069eecb0cc15e836605a34d0b9a0a0c1e92cebdb8256bb2e3d0` |
| FBC fragment Jul 3 (aun con SHA roto) | `quay.io/rhoai/rhoai-fbc-fragment@sha256:e390bddf862a131c7970a7747d622fb4d061c0d6de88417718de87828e3f9d3b` |
| Jira RHOAIENG-73431 (investigar discrepancia quay) | https://redhat.atlassian.net/browse/RHOAIENG-73431 |
| Release pipeline (managed-scdhb, bloqueada) | https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ns/rhtap-releng-tenant/applications/rhoai-v3-5-ea-2/pipelineruns/managed-scdhb |
| Exception MR (cerrada, no necesaria) | https://gitlab.cee.redhat.com/releng/konflux-release-data/-/merge_requests/19850 |
| Cluster | `stone-prod-p02.hjvn.p1.openshiftapps.com` |
| Build namespace | `rhoai-tenant` |
| Release namespace | `rhtap-releng-tenant` |
