#!/bin/bash

# Fetch logs for PipelineRuns using TaskRuns (correct method)
# Method:
# 1. Get PipelineRun YAML to find TaskRun names
# 2. Fetch logs from each TaskRun

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
echo -e "${BLUE}Obteniendo Logs via TaskRuns${NC}"
echo "========================================"
echo ""

# Get PRs without logs (limit to recent ones)
PRS_WITHOUT_LOGS=$(docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -tAc \
    "SELECT pipelinerun_name, pipelinerun_uid FROM build_failures
     WHERE build_logs IS NULL
     ORDER BY build_completion_time DESC NULLS LAST
     LIMIT 10;" 2>/dev/null)

if [ -z "$PRS_WITHOUT_LOGS" ]; then
    echo "No hay PipelineRuns sin logs"
    exit 0
fi

TOTAL=$(echo "$PRS_WITHOUT_LOGS" | wc -l)
CURRENT=0

echo -e "${GREEN}Encontrados: $TOTAL PipelineRuns sin logs${NC}"
echo ""

while IFS='|' read -r pr_name pr_uid; do
    [ -z "$pr_name" ] || [ -z "$pr_uid" ] && continue

    CURRENT=$((CURRENT + 1))
    echo -e "${BLUE}[$CURRENT/$TOTAL] $pr_name${NC}"

    # Step 1: Get TaskRun names from PipelineRun
    echo -n "  Obteniendo TaskRuns... "

    # Try with kubectl tekton first
    TASKRUNS=$(timeout 15 bash -c "yes '' 2>/dev/null | kubectl tekton get pr '$pr_name' -n '$NAMESPACE' -o yaml 2>/dev/null" | \
        grep -A 2 "childReferences:" | \
        grep "name:" | \
        awk '{print $2}' || echo "")

    # Fallback to oc if kubectl tekton fails
    if [ -z "$TASKRUNS" ]; then
        TASKRUNS=$(oc get pipelinerun "$pr_name" -n "$NAMESPACE" -o json 2>/dev/null | \
            jq -r '.status.childReferences[]? | select(.kind=="TaskRun") | .name' || echo "")
    fi

    if [ -z "$TASKRUNS" ]; then
        echo -e "${RED}✗ No encontrados${NC}"
        echo ""
        continue
    fi

    TASKRUN_COUNT=$(echo "$TASKRUNS" | wc -l)
    echo -e "${GREEN}$TASKRUN_COUNT${NC}"

    # Step 2: Get logs from each TaskRun
    ALL_LOGS=""

    while IFS= read -r taskrun_name; do
        [ -z "$taskrun_name" ] && continue

        echo -n "    Logs de $taskrun_name... "

        # Get TaskRun logs
        TR_LOGS=$(timeout 20 bash -c "yes '' 2>/dev/null | kubectl tekton logs tr '$taskrun_name' -n '$NAMESPACE' 2>/dev/null" || echo "")

        # Fallback to oc logs via pod
        if [ -z "$TR_LOGS" ]; then
            # Get pod name from TaskRun
            POD_NAME=$(oc get taskrun "$taskrun_name" -n "$NAMESPACE" -o json 2>/dev/null | \
                jq -r '.status.podName // ""')

            if [ -n "$POD_NAME" ]; then
                TR_LOGS=$(oc logs "$POD_NAME" -n "$NAMESPACE" --all-containers 2>/dev/null || echo "")
            fi
        fi

        if [ -n "$TR_LOGS" ]; then
            echo -e "${GREEN}✓${NC}"
            ALL_LOGS="$ALL_LOGS

===== TaskRun: $taskrun_name =====
$TR_LOGS
"
        else
            echo -e "${YELLOW}⚠${NC}"
        fi

    done <<< "$TASKRUNS"

    # Step 3: Save logs to database
    if [ -n "$ALL_LOGS" ]; then
        echo -n "  Guardando en DB... "

        # Truncate to 50000 chars
        LOGS_TRUNCATED=$(echo "$ALL_LOGS" | head -c 50000)

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
        echo "  ℹ  No hay logs disponibles para este PR"
    fi

    echo ""

done <<< "$PRS_WITHOUT_LOGS"

echo "========================================"
echo -e "${GREEN}Proceso Completado${NC}"
echo "========================================"
echo ""
