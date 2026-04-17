#!/bin/bash

# Recolector de datos REALES de Konflux para PostgreSQL
# Usa kubectl tekton (histórico) y oc get pipelinerun (recientes)

set -e

NAMESPACE="NAMESPACE_PLACEHOLDER"
COMPONENTS_FILE="HOME_DIR/components-ui-failed.txt"
export PGPASSWORD="admin"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "========================================"
echo -e "${BLUE}Recolectando Datos Reales de Konflux${NC}"
echo "========================================"
echo ""

# Verify DB is running
if ! docker ps | grep -q ci-autohealing-db; then
    echo -e "${RED}Error: PostgreSQL container not running${NC}"
    echo "Start it with: ./db-start.sh"
    exit 1
fi

echo -e "${YELLOW}[1/3] Verificando PostgreSQL...${NC}"
if ! docker exec ci-autohealing-db pg_isready -U postgres &> /dev/null; then
    echo -e "${RED}✗ PostgreSQL no está listo${NC}"
    exit 1
fi
echo -e "${GREEN}✓ PostgreSQL conectado${NC}"
echo ""

# Read components
echo -e "${YELLOW}[2/3] Leyendo componentes...${NC}"
if [ ! -f "$COMPONENTS_FILE" ]; then
    echo -e "${RED}Error: $COMPONENTS_FILE no encontrado${NC}"
    exit 1
fi

COMPONENTS=$(grep -v '^#' "$COMPONENTS_FILE" | grep -v '^$')
COMPONENT_COUNT=$(echo "$COMPONENTS" | wc -l)
echo -e "${GREEN}✓ $COMPONENT_COUNT componentes${NC}"
echo ""

# Start scan
SCAN_ID=$(uuidgen)
docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c \
    "INSERT INTO scan_history (scan_id, scan_type, scan_mode, status) VALUES ('$SCAN_ID', 'manual', 'full', 'running');" > /dev/null

# Counters
TOTAL_SCANNED=0
TOTAL_FAILURES=0
NEW_INSERTED=0

echo -e "${YELLOW}[3/3] Procesando componentes...${NC}"
echo ""

# Process each component
while IFS= read -r component; do
    [ -z "$component" ] && continue

    TOTAL_SCANNED=$((TOTAL_SCANNED + 1))

    echo -e "${BLUE}[$TOTAL_SCANNED/$COMPONENT_COUNT] $component${NC}"

    # Get component metadata
    echo -n "  Metadata... "
    COMP_DATA=$(oc get component "$component" -n "$NAMESPACE" -o json 2>/dev/null || echo "")

    if [ -z "$COMP_DATA" ]; then
        echo -e "${RED}✗ No encontrado${NC}"
        continue
    fi

    REPO_URL=$(echo "$COMP_DATA" | jq -r '.spec.source.git.url // ""')
    REPO_SHORT=$(echo "$REPO_URL" | sed 's|https://github.com/||' | sed 's|.git||')
    BRANCH=$(echo "$COMP_DATA" | jq -r '.spec.source.git.revision // ""')

    echo -e "${GREEN}✓${NC} ($REPO_SHORT)"

    # Get PipelineRuns from Tekton Results (historical)
    echo -n "  Buscando en Tekton Results... "

    TEKTON_OUTPUT=$(timeout 20 bash -c "yes '' 2>/dev/null | kubectl tekton get pr -n '$NAMESPACE' \
        --labels='appstudio.openshift.io/component=$component' \
        --limit 10 2>/dev/null" | \
        grep -v "^NAME" | \
        grep -v "^Next" | \
        grep -v "^$" | \
        grep -v "Press any key" || echo "")

    TEKTON_COUNT=0
    ALL_PR_NAMES=""

    if [ -n "$TEKTON_OUTPUT" ]; then
        ALL_PR_NAMES=$(echo "$TEKTON_OUTPUT" | awk '{print $1}')
        TEKTON_COUNT=$(echo "$ALL_PR_NAMES" | wc -l)
    fi

    echo -e "${GREEN}$TEKTON_COUNT${NC}"

    if [ -z "$ALL_PR_NAMES" ]; then
        echo "  ℹ  No hay PipelineRuns"
        echo ""
        continue
    fi

    echo "  Procesando $(echo "$ALL_PR_NAMES" | wc -l) PipelineRuns..."

    # Process each unique PipelineRun
    while IFS= read -r pr_name; do
        [ -z "$pr_name" ] && continue

        # Check if already in DB
        EXISTS=$(docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -tAc \
            "SELECT 1 FROM build_failures WHERE pipelinerun_name = '$pr_name' LIMIT 1" 2>/dev/null || echo "0")

        if [ "$EXISTS" = "1" ]; then
            continue  # Skip, already in DB
        fi

        # Get PR details from Tekton Results
        PR_JSON=$(timeout 15 bash -c "yes '' 2>/dev/null | kubectl tekton get pr '$pr_name' -n '$NAMESPACE' -o json 2>/dev/null" || echo "")

        # If not in Tekton Results, try Kubernetes
        if [ -z "$PR_JSON" ]; then
            PR_JSON=$(oc get pipelinerun "$pr_name" -n "$NAMESPACE" -o json 2>/dev/null || echo "")
        fi

        if [ -z "$PR_JSON" ]; then
            continue  # Skip if can't get details
        fi

        # Extract status
        STATUS_REASON=$(echo "$PR_JSON" | jq -r '.status.conditions[0].reason // ""' 2>/dev/null)

        # Only insert Failed PRs
        if [ "$STATUS_REASON" != "Failed" ]; then
            continue
        fi

        TOTAL_FAILURES=$((TOTAL_FAILURES + 1))

        # Extract data
        COMMIT_SHA=$(echo "$PR_JSON" | jq -r '.metadata.annotations."pipelinesascode.tekton.dev/sha" // ""' 2>/dev/null)
        COMMIT_SHORT=${COMMIT_SHA:0:7}
        COMMIT_MSG=$(echo "$PR_JSON" | jq -r '.metadata.annotations."pipelinesascode.tekton.dev/sha-title" // ""' 2>/dev/null)
        COMMIT_URL=$(echo "$PR_JSON" | jq -r '.metadata.annotations."pipelinesascode.tekton.dev/sha-url" // ""' 2>/dev/null)
        START_TIME=$(echo "$PR_JSON" | jq -r '.status.startTime // ""' 2>/dev/null)
        COMPLETION_TIME=$(echo "$PR_JSON" | jq -r '.status.completionTime // ""' 2>/dev/null)
        ERROR_MSG=$(echo "$PR_JSON" | jq -r '.status.conditions[0].message // ""' 2>/dev/null | head -c 500)

        # Escape single quotes for SQL
        COMMIT_MSG_ESC=$(echo "$COMMIT_MSG" | sed "s/'/''/g")
        ERROR_MSG_ESC=$(echo "$ERROR_MSG" | sed "s/'/''/g")
        REPO_SHORT_ESC=$(echo "$REPO_SHORT" | sed "s/'/''/g")
        REPO_URL_ESC=$(echo "$REPO_URL" | sed "s/'/''/g")
        COMMIT_URL_ESC=$(echo "$COMMIT_URL" | sed "s/'/''/g")

        # Insert into DB
        docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c "
        INSERT INTO build_failures (
            component_name, pipelinerun_name, namespace, repository, repository_url,
            branch, commit_sha, commit_short_sha, commit_message, commit_url,
            status, error_message, build_start_time, build_completion_time, first_detected_at
        ) VALUES (
            '$component', '$pr_name', '$NAMESPACE', '$REPO_SHORT_ESC', '$REPO_URL_ESC',
            '$BRANCH', '$COMMIT_SHA', '$COMMIT_SHORT', '$COMMIT_MSG_ESC', '$COMMIT_URL_ESC',
            'Failed', '$ERROR_MSG_ESC',
            $([ -n "$START_TIME" ] && echo "'$START_TIME'" || echo "NULL"),
            $([ -n "$COMPLETION_TIME" ] && echo "'$COMPLETION_TIME'" || echo "NULL"),
            NOW()
        );
        " > /dev/null 2>&1

        if [ $? -eq 0 ]; then
            echo "    ✓ $pr_name"
            NEW_INSERTED=$((NEW_INSERTED + 1))
        else
            echo "    ✗ $pr_name (error SQL)"
        fi

    done <<< "$ALL_PR_NAMES"

    # Update component health
    docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c \
        "SELECT update_component_health('$component');" > /dev/null 2>&1 || true

    echo ""

done <<< "$COMPONENTS"

# Complete scan
docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c "
UPDATE scan_history
SET status = 'completed', completed_at = NOW(),
    duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at)),
    components_scanned = $TOTAL_SCANNED,
    failures_found = $TOTAL_FAILURES,
    new_failures = $NEW_INSERTED
WHERE scan_id = '$SCAN_ID';
" > /dev/null

echo "========================================"
echo -e "${GREEN}Recolección Completada${NC}"
echo "========================================"
echo ""
echo "Resumen:"
echo "  Componentes escaneados: $TOTAL_SCANNED"
echo "  Fallos encontrados: $TOTAL_FAILURES"
echo "  Nuevos insertados: $NEW_INSERTED"
echo ""
echo "Ver resultados: ./show-dashboard.sh"
echo ""
