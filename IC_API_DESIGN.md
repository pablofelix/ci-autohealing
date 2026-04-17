# IC Command - API Design Proposal

**Problema Actual**: Demasiados comandos inconsistentes, no sigue lógica tipo API/kubectl

---

## 🎯 Diseño Propuesto: Estilo API/kubectl

### Filosofía

```bash
ic <verb> <resource> [name] [--flags]
```

**Verbs**: `get`, `describe`, `list`, `logs`  
**Resources**: `component`, `pipelinerun`, `app`  
**Flags**: `--output`, `--repo`, `--log`, `--url`, `--limit`, etc.

---

## 📋 Comandos Propuestos

### 1. GET - Obtener información específica

```bash
# Get component summary (default)
ic get component <name>

# Get specific field with flags
ic get component <name> --repo          # Solo repository URL
ic get component <name> --branch        # Solo branch
ic get component <name> --commit        # Solo último commit SHA
ic get component <name> --url           # Solo Konflux UI URL
ic get component <name> --status        # Solo status (Failed/Succeeded)
ic get component <name> --error         # Solo error summary

# Output formats
ic get component <name> --output json   # JSON format
ic get component <name> --output yaml   # YAML format
ic get component <name> --output table  # Table format (default)

# Get PipelineRun
ic get pipelinerun <name>
ic get pipelinerun <name> --commit
ic get pipelinerun <name> --url
ic get pipelinerun <name> --status
```

### 2. DESCRIBE - Información detallada

```bash
# Describe component (full details)
ic describe component <name>

# Describe with specific sections
ic describe component <name> --with-logs       # Include logs
ic describe component <name> --with-history    # Include history
ic describe component <name> --with-analysis   # Include failure analysis
```

### 3. LOGS - Obtener logs

```bash
# Get logs for component (last PipelineRun)
ic logs component <name>

# Get logs for specific PipelineRun
ic logs pipelinerun <name>

# Logs with options
ic logs component <name> --tail 100            # Last 100 lines
ic logs component <name> --follow              # Follow logs (if running)
ic logs component <name> --previous            # Previous PipelineRun
ic logs component <name> --output raw          # Raw logs
ic logs component <name> --output json         # Structured JSON
```

### 4. LIST - Listar recursos

```bash
# List all failing components
ic list components

# List with filters
ic list components --status failed
ic list components --status succeeded
ic list components --with-logs
ic list components --without-logs

# List PipelineRuns
ic list pipelineruns
ic list pipelineruns --component <name>
ic list pipelineruns --limit 10
ic list pipelineruns --status failed
```

### 5. TRIAGE - Vista consolidada

```bash
# Triage dashboard (no cambios)
ic triage

# Triage with filters
ic triage --with-logs
ic triage --age 24h
ic triage --age 7d
```

### 6. WHY - Análisis de fallos

```bash
# Why is component failing (no cambios - ya funciona bien)
ic why <component>
```

---

## 🔧 Flags Comunes (GNU-style)

```bash
--output, -o <format>      # Output format: json, yaml, table, raw
--limit, -l <N>            # Limit results
--filter, -f <expr>        # Filter expression
--sort <field>             # Sort by field
--repo                     # Show only repository URL
--branch                   # Show only branch
--commit                   # Show only commit SHA
--url                      # Show only Konflux URL
--status                   # Show only status
--error                    # Show only error summary
--log                      # Show only logs
--with-logs                # Include logs in output
--with-history             # Include history
--with-analysis            # Include failure analysis
--tail <N>                 # Last N lines (for logs)
--follow, -f               # Follow logs
--previous                 # Previous resource
--age <duration>           # Filter by age (24h, 7d, etc.)
```

---

## 📊 Ejemplos de Uso

### DevOps Workflow

```bash
# 1. Overview - ¿Qué está fallando?
ic triage

# 2. Get component details
ic get component odh-spark-v3-4

# 3. Quick check - solo el error
ic get component odh-spark-v3-4 --error

# 4. Get repository to check code
ic get component odh-spark-v3-4 --repo
# Output: https://github.com/acme-org/spark-operator

# 5. Get commit SHA to investigate
ic get component odh-spark-v3-4 --commit
# Output: bc38c8a7

# 6. Get Konflux URL to see UI
ic get component odh-spark-v3-4 --url
# Output: https://konflux-ui.../pipelinerun/...

# 7. Get logs
ic logs component odh-spark-v3-4

# 8. Tail logs
ic logs component odh-spark-v3-4 --tail 50

# 9. Full analysis
ic why odh-spark-v3-4

# 10. Describe with all details
ic describe component odh-spark-v3-4 --with-logs --with-history
```

### Scripting / Automation

```bash
# Get all failing components as JSON
ic list components --status failed --output json

# Get repository for each component
for component in $(ic list components --output raw); do
    repo=$(ic get component $component --repo)
    echo "$component: $repo"
done

# Check if component has logs
if ic get component odh-spark-v3-4 --log > /dev/null 2>&1; then
    echo "Has logs"
fi

# Get commit SHAs for all failures
ic list components --output json | jq -r '.[].commit_sha'
```

### CI/CD Integration

```bash
# In CI pipeline - check if component is failing
status=$(ic get component odh-spark-v3-4 --status)
if [ "$status" == "Failed" ]; then
    # Send alert
    ic get component odh-spark-v3-4 --error | slack-notify
fi

# Get JSON for external tools
ic get component odh-spark-v3-4 --output json | jq '.'
```

---

## 🔄 Backward Compatibility

Mantener comandos viejos como aliases:

```bash
# Old style → New style mapping
ic 1                       → ic describe component #1
ic describe component X    → sin cambios
ic triage                  → sin cambios
ic why X                   → sin cambios
ic working                 → ic list components --status succeeded
ic history X               → ic describe component X --with-history
ic stats                   → ic get stats
```

---

## 📐 Implementación

### Estructura de código

```bash
# Parser principal
case "$VERB" in
    get)
        handle_get "$RESOURCE" "$NAME" "$@"
        ;;
    describe)
        handle_describe "$RESOURCE" "$NAME" "$@"
        ;;
    logs)
        handle_logs "$RESOURCE" "$NAME" "$@"
        ;;
    list)
        handle_list "$RESOURCE" "$@"
        ;;
    *)
        # Legacy commands
        handle_legacy "$VERB" "$@"
        ;;
esac
```

### Flag parsing

```bash
handle_get() {
    local resource="$1"
    local name="$2"
    shift 2
    
    # Default values
    local output_format="table"
    local field=""
    
    # Parse flags
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --output|-o)
                output_format="$2"
                shift 2
                ;;
            --repo)
                field="repository_url"
                shift
                ;;
            --commit)
                field="commit_sha"
                shift
                ;;
            --url)
                field="konflux_logs_url"
                shift
                ;;
            --error)
                field="error_summary"
                shift
                ;;
            *)
                echo "Unknown flag: $1"
                exit 1
                ;;
        esac
    done
    
    # Execute query
    if [ -n "$field" ]; then
        # Get specific field
        sql "SELECT $field FROM ... WHERE component_name = '$name'"
    else
        # Get full resource
        get_component_summary "$name" "$output_format"
    fi
}
```

---

## ✅ Beneficios

1. **Consistencia**: Misma estructura para todos los comandos
2. **Predecible**: Siguiendo convenciones de kubectl/docker
3. **Scriptable**: Fácil de usar en scripts con flags específicos
4. **Eficiente**: `--repo` solo obtiene repo, no todo
5. **Extensible**: Fácil agregar nuevos flags/recursos
6. **Backward compatible**: Comandos viejos siguen funcionando
7. **Output formats**: JSON para automation, table para humanos
8. **GNU-style flags**: `--flag` y `-f` corto

---

## 🚀 Próximos Pasos

1. Implementar parser de flags
2. Refactorizar comandos existentes
3. Agregar output formats (JSON, YAML, table)
4. Agregar tests para flags
5. Actualizar help con nuevo diseño
6. Mantener backward compatibility
