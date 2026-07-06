# PCC Cache Stale - FBC Fragment pierde versiones del catálogo

**Estado**: Abierto - causa raíz identificada, workaround manual en uso
**Reportado**: 2026-07-06 (Slack: sjagtap, Moulali, Akshay)
**Impacto**: El catálogo de 3.5.ea.2 no incluye 3.4.2; se necesita un nuevo RC (Release Candidate)
**Recurrencia**: También ocurrió en el ciclo de release anterior

---

## Glosario

| Sigla | Significado | Qué es |
|-------|-------------|--------|
| **PCC** | Pre-Computed Catalog | Cache creado por el equipo RHOAI (no es de Konflux ni de OLM). Es un GitHub Actions workflow que pre-genera los ficheros de catálogo para que los builds de FBC fragment no tengan que regenerarlos cada vez |
| **FBC** | File-Based Catalog | El formato que usa OLM (Operator Lifecycle Manager) para definir qué operadores están disponibles en OpenShift y cómo se actualizan. Es un fichero YAML/JSON que lista versiones, channels y rutas de upgrade |
| **OCP** | OpenShift Container Platform | La plataforma de Red Hat donde corren los operadores. Cada versión de OCP (4.14, 4.15, ..., 4.22) tiene su propio índice de operadores |
| **OLM** | Operator Lifecycle Manager | El componente de OpenShift que gestiona la instalación y actualización de operadores. Lee los catálogos FBC para saber qué versiones ofrecer |
| **OPM** | Operator Package Manager | Herramienta CLI que genera, migra y valida catálogos de operadores. Usada internamente por el workflow de PCC |
| **FBC Fragment** | - | Una imagen de contenedor que contiene un trozo del catálogo de operadores (solo la parte de RHOAI). Se combina con el índice general de Red Hat en producción |
| **IIB** | Index Image Builder | Servicio de Red Hat que construye las imágenes del índice de operadores cuando se publica una nueva versión |
| **RC** | Release Candidate | Versión candidata a release. Si el catálogo es incorrecto, hay que generar un RC nuevo |

---

## Qué es PCC y por qué existe

### El problema que resuelve

Cuando RHOAI construye sus imágenes de FBC fragment (una por cada versión de
OpenShift soportada), el build necesita saber el estado actual del **índice de
operadores de Red Hat**: qué versiones del operador RHOAI se han publicado y
cuál es la estructura de channels y upgrade paths.

Sin PCC, cada build de FBC fragment tendría que:

1. Descargar la imagen completa del índice de operadores de Red Hat para cada
   versión de OpenShift (`registry.redhat.io/redhat/redhat-operator-index:v4.XX`).
   Estas imágenes son grandes.
2. Ejecutar `opm migrate` para extraer el paquete `rhods-operator` del índice.
3. Ejecutar `opm alpha convert-template basic` para generar un template.
4. Ejecutar `opm alpha render-template basic` para renderizar el catálogo final.

Esto tarda **30-40 minutos** y requiere credenciales de registry en build time.

### Qué es PCC exactamente

PCC **no es un concepto de Konflux, ni de OLM, ni de OPM**. Es un mecanismo
**inventado por el equipo de RHOAI** para acelerar los builds. Consiste en:

1. Un **directorio `pcc/`** en el repo
   [RHOAI-Build-Config](https://github.com/red-hat-data-services/RHOAI-Build-Config/tree/main/pcc)
   que contiene ficheros de catálogo YAML pre-generados.
2. Un **GitHub Actions workflow** llamado `regen-pcc-cache` que regenera esos
   ficheros. Escrito por ckodama.

El directorio `pcc/` contiene:

```
pcc/
  catalog-v4.14.yaml              ← catálogo pre-generado para OpenShift 4.14
  catalog-v4.15.yaml              ← catálogo pre-generado para OpenShift 4.15
  ...
  catalog-v4.22.yaml              ← catálogo pre-generado para OpenShift 4.22
  shipped_rhoai_versions.txt      ← lista de versiones publicadas en registry
  shipped_rhoai_versions_granular.txt
```

Cuando el build de FBC fragment se ejecuta, **toma directamente estos YAML
pre-generados** en vez de hacer todo el proceso de descarga + migración + render.
Así el build es rápido.

### Qué contienen los ficheros de catálogo

Cada `catalog-v4.XX.yaml` define tres cosas para OLM:

- **`olm.package`**: nombre del paquete (`rhods-operator`) y channel por defecto
- **`olm.channel`**: qué versiones del operador están en cada channel
  (`stable-3.x`, `fast`, etc.) y las rutas de upgrade entre ellas
- **`olm.bundle`**: las imágenes de cada versión del operador con sus metadatos

Si una versión no aparece en estos ficheros, **OLM no la ve** — es como si no
existiera. Los clientes no pueden instalarla ni actualizarse a ella.

---

## Qué pasó con 3.5.ea.2

### Secuencia de eventos

```
1. Se publica rhods-operator 3.4.2 en registry.redhat.io
   └─ El índice de operadores de Red Hat se reconstruye incluyendo 3.4.2

2. NADIE ejecuta el workflow regen-pcc-cache
   └─ Los ficheros catalog-v4.XX.yaml siguen sin incluir 3.4.2

3. Se ejecuta el stage promoter para 3.5.ea.2
   └─ Construye el FBC fragment usando los YAML de pcc/ (que son viejos)

4. El FBC fragment resultante NO contiene 3.4.2
   └─ El check de validación detecta que se está "podando" una versión:
      !FAILURE! - FBC fragment prunes rhods-operator.3.4.2
                  from rhods-operator.stable-3.x channel

5. Si este catálogo se pushea a producción:
   └─ SOBRESCRIBE el catálogo de prod
   └─ 3.4.2 DESAPARECE del channel stable-3.x
   └─ Clientes en 3.4.2 PIERDEN su upgrade path
```

### Evidencia

Pipeline run donde se ve el pruning:
```
https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ns/rhoai-tenant/
  applications/rhoai-fbc-fragment-ocp-421/pipelineruns/
  rhoai-fbc-fragment-rhoai-35-ea2-ocp-421-on-push-8zn96
```

GitHub Actions del regen manual que hizo sjagtap:
```
https://github.com/red-hat-data-services/rhods-devops-infra/actions/runs/28659465462/job/84996456625
```

---

## Qué hace exactamente la regeneración de PCC

El workflow está en
[regen-pcc-cache.yaml](https://github.com/red-hat-data-services/RHOAI-Build-Config/blob/main/.github/workflows/regen-pcc-cache.yaml).

### Paso a paso

**1. Actualiza la lista de versiones publicadas**
- Se autentica en `registry.redhat.io` con una cuenta de servicio
- Ejecuta `skopeo list-tags docker://registry.redhat.io/rhoai/odh-operator-bundle`
  para obtener todas las versiones publicadas
- Compara con la lista cacheada en `shipped_rhoai_versions_granular.txt`
- La sobreescribe con los datos actuales

**2. Para cada versión de OpenShift soportada** (leídas de `config/config.yaml`):
- **`opm migrate`**: Descarga el índice de operadores de Red Hat para esa
  versión de OpenShift y extrae el paquete `rhods-operator`
- **`opm alpha convert-template basic`**: Convierte el catálogo migrado en un
  template básico (solo estructura de channels y upgrade edges, sin metadatos
  pesados de los bundles)
- **`opm alpha render-template basic`**: Renderiza el template en el catálogo
  YAML completo, resolviendo los metadatos de cada bundle desde el registry
- Para OpenShift >= 4.17: añade `--migrate-level=bundle-object-to-csv-metadata`

**3. Valida el resultado**
- Ejecuta `catalog_validator.py -op validate-pcc` para verificar consistencia
  con la configuración del build y las versiones publicadas

**4. Commitea y pushea**
- Si los ficheros cambiaron, commitea y pushea a `main` del repo RHOAI-Build-Config

---

## Causa raíz

### El workflow es solo manual

El workflow `regen-pcc-cache` se configura como **`workflow_dispatch`** solamente.
Eso significa que solo se ejecuta cuando alguien lo lanza manualmente desde la
UI de GitHub Actions o vía API.

**No existe ningún trigger automático** que conecte "nueva versión publicada en
registry.redhat.io" con "regenerar PCC". Ni:
- Un `repository_dispatch` disparado por IIB cuando publica un nuevo bundle
- Un cron job que lo ejecute periódicamente
- Un step en el stage-promoter que lo invoque antes de construir el FBC fragment

### Por qué ocurre ahora (y antes también)

No es un bug intermitente ni un fallo técnico. Es un **gap en la automatización**:
el paso de regenerar PCC depende de que alguien se acuerde de ejecutarlo. Cuando
eso no ocurre (como pasó con 3.4.2, y también en el release anterior), el PCC
queda desactualizado y el FBC fragment se construye con datos viejos.

sjagtap investigó ~1 hora sin encontrar un trigger automático roto — porque
probablemente **nunca existió** como automatización completa. Funcionaba cuando
alguien lo hacía manualmente como parte del proceso de release.

---

## Soluciones propuestas

| Opción | Qué cambia | Coste | Riesgo |
|--------|-----------|-------|--------|
| **Manual siempre** | Ejecutar `regen-pcc` antes de cada RC y stage promoter | +30-40 min por RC, manual | La gente se olvida (ya pasó 2 veces) |
| **Auto en stage-promoter** | Añadir step de regen PCC al job del stage-promoter | +30-40 min por RC, automático | Ninguno significativo |
| **Fix root cause** | Añadir `repository_dispatch` trigger cuando IIB publica | Investigación + implementación | Mejor solución largo plazo |

**Recomendación**: Opción 2 (auto en stage-promoter) como fix inmediato. Opción 3
en paralelo como mejora a largo plazo. Opción 1 es inaceptable como solución
permanente.

---

## Contactos

- **sjagtap** — investigó el problema, conoce el mecanismo de PCC
- **ckodama** — escribió el workflow `regen-pcc-cache`
- **Moulali** — release manager, al tanto del issue
- **Akshay Ghodake** — involucrado en el thread

## Links

- [RHOAI-Build-Config: directorio pcc/](https://github.com/red-hat-data-services/RHOAI-Build-Config/tree/main/pcc)
- [Workflow regen-pcc-cache](https://github.com/red-hat-data-services/RHOAI-Build-Config/blob/main/.github/workflows/regen-pcc-cache.yaml)
- [Pipeline run con el error](https://konflux-ui.apps.stone-prod-p02.hjvn.p1.openshiftapps.com/ns/rhoai-tenant/applications/rhoai-fbc-fragment-ocp-421/pipelineruns/rhoai-fbc-fragment-rhoai-35-ea2-ocp-421-on-push-8zn96)
