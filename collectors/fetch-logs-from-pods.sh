#!/bin/bash

# Fetch logs for PipelineRuns from pods (most reliable method)
# Method:
# 1. Find pods associated with PipelineRun using labels
# 2. Get logs from all containers in each pod

set -e

NAMESPACE="NAMESPACE_PLACEHOLDER"
export PGPASSWORD="admin"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "========================================"
echo -e "${BLUE}Obteniendo Logs desde Pods${NC}"
echo "========================================"
echo ""

# Get PRs without logs (prioritize those with NULL completion time - likely still have pods)
PRS_WITHOUT_LOGS=$(docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -tAc \
    "SELECT pipelinerun_name FROM build_failures
     WHERE build_logs IS NULL
     ORDER BY build_completion_time DESC NULLS FIRST
     LIMIT 20;" 2>/dev/null)

if [ -z "$PRS_WITHOUT_LOGS" ]; then
    echo "No hay PipelineRuns sin logs"
    exit 0
fi

TOTAL=$(echo "$PRS_WITHOUT_LOGS" | wc -l)
CURRENT=0

echo -e "${GREEN}Encontrados: $TOTAL PipelineRuns sin logs${NC}"
echo ""

while IFS= read -r pr_name; do
    [ -z "$pr_name" ] && continue

    CURRENT=$((CURRENT + 1))
    echo -e "${BLUE}[$CURRENT/$TOTAL] $pr_name${NC}"

    # Step 1: Find pods for this PipelineRun using labels
    echo -n "  Buscando pods... "

    PODS=$(oc get pod -n "$NAMESPACE" \
        -l tekton.dev/pipelineRun="$pr_name" \
        --no-headers 2>/dev/null | awk '{print $1}' || echo "")

    if [ -z "$PODS" ]; then
        echo -e "${RED}✗ No encontrados${NC}"
        echo ""
        continue
    fi

    POD_COUNT=$(echo "$PODS" | wc -l)
    echo -e "${GREEN}$POD_COUNT${NC}"

    # Step 2: Get logs from each pod
    ALL_LOGS=""
    LOGS_FOUND=false

    while IFS= read -r pod_name; do
        [ -z "$pod_name" ] && continue

        # Check pod status
        POD_STATUS=$(oc get pod "$pod_name" -n "$NAMESPACE" -o json 2>/dev/null | jq -r '.status.phase // ""')

        echo -n "    $pod_name ($POD_STATUS)... "

        # Get logs from all containers
        POD_LOGS=$(oc logs "$pod_name" -n "$NAMESPACE" --all-containers 2>/dev/null || echo "")

        if [ -n "$POD_LOGS" ]; then
            echo -e "${GREEN}✓${NC}"
            LOGS_FOUND=true
            ALL_LOGS="$ALL_LOGS

===== Pod: $pod_name (Status: $POD_STATUS) =====
$POD_LOGS
"
        else
            echo -e "${YELLOW}⚠ Sin logs${NC}"
        fi

    done <<< "$PODS"

    # Step 3: Save logs to database
    if [ "$LOGS_FOUND" = true ]; then
        echo -n "  Guardando en DB... "

        # Truncate to 100000 chars (more than before to capture full build logs)
        LOGS_TRUNCATED=$(echo "$ALL_LOGS" | head -c 100000)

        # Escape for SQL
        LOGS_ESC=$(echo "$LOGS_TRUNCATED" | sed "s/'/''/g")

        # Update database
        docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c "
        UPDATE build_failures
        SET build_logs = '$LOGS_ESC'
        WHERE pipelinerun_name = '$pr_name';
        " > /dev/null 2>&1

        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${RED}✗ Error SQL${NC}"
        fi
    else
        echo "  ℹ  No hay logs disponibles (pods eliminados)"
    fi

    echo ""

done <<< "$PRS_WITHOUT_LOGS"

echo "========================================"
echo -e "${GREEN}Proceso Completado${NC}"
echo "========================================"
echo ""
