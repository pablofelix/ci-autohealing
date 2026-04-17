#!/bin/bash

# Fetch logs for PipelineRuns using KubeArchive API
# Method:
# 1. Get TaskRun names from PipelineRun
# 2. Query KubeArchive API for TaskRun details
# 3. Get failed container logs from KubeArchive

set -e

# Load configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/../.env" ]; then
    set -a  # Export all variables
    source "$SCRIPT_DIR/../.env"
    set +a
fi

NAMESPACE="${NAMESPACE:-NAMESPACE_PLACEHOLDER}"
export PGPASSWORD="admin"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "========================================"
echo -e "${BLUE}Obteniendo Logs via KubeArchive API${NC}"
echo "========================================"
echo ""

# Get KubeArchive API URL
echo -n "Obteniendo KubeArchive API URL... "

# Get from ConfigMap (correct approach)
KUBEARCHIVE_API=$(oc get cm -n product-kubearchive kubearchive-api-url -o jsonpath='{.data.URL}' 2>/dev/null || echo "")

if [ -z "$KUBEARCHIVE_API" ]; then
    # Fallback to known URL for CLUSTER_NAME
    KUBEARCHIVE_API="https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN"
fi

echo -e "${GREEN}✓${NC}"
echo "  API: $KUBEARCHIVE_API"
echo ""

# Get authentication token
TOKEN=$(oc whoami -t 2>/dev/null)

# Get PRs without logs (prioritize those with completion time, or all if NULL)
PRS_WITHOUT_LOGS=$(docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -tAc \
    "SELECT pipelinerun_name, pipelinerun_uid FROM build_failures
     WHERE build_logs IS NULL
     AND pipelinerun_uid IS NOT NULL
     ORDER BY build_completion_time DESC NULLS LAST
     LIMIT 10;" 2>/dev/null)

if [ -z "$PRS_WITHOUT_LOGS" ]; then
    echo "No hay PipelineRuns antiguos sin logs"
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

    # Step 1: Get PipelineRun details to find TaskRuns (try multiple methods)
    echo -n "  Obteniendo TaskRuns... "

    TASKRUNS=""
    PR_DETAILS=""

    # Method 1: Try active cluster first (fastest)
    PR_DETAILS=$(oc get pipelinerun "$pr_name" -n "$NAMESPACE" -o json 2>/dev/null || echo "")
    if [ -n "$PR_DETAILS" ]; then
        TASKRUNS=$(echo "$PR_DETAILS" | jq -r '.status.childReferences[]? | select(.kind=="TaskRun") | .name' 2>/dev/null || echo "")
    fi

    # Method 2: Try kubectl tekton if method 1 failed
    if [ -z "$TASKRUNS" ]; then
        PR_DETAILS=$(timeout 30 bash -c "yes '' 2>/dev/null | kubectl tekton pr describe '$pr_name' -n '$NAMESPACE' -o yaml 2>/dev/null" || echo "")
        if [ -n "$PR_DETAILS" ]; then
            TASKRUNS=$(echo "$PR_DETAILS" | grep -A 1000 "childReferences:" | grep "name:" | grep -v "pipelineTask:" | awk '{print $2}' | grep -v "^$" || echo "")
        fi
    fi

    # Method 3: Try KubeArchive API as last resort
    if [ -z "$TASKRUNS" ]; then
        PR_DETAILS=$(curl -s -H "Authorization: Bearer $TOKEN" \
            "$KUBEARCHIVE_API/apis/tekton.dev/v1/namespaces/$NAMESPACE/pipelineruns/$pr_name" 2>/dev/null || echo "")
        if [ -n "$PR_DETAILS" ]; then
            TASKRUNS=$(echo "$PR_DETAILS" | jq -r '.status.childReferences[]? | select(.kind=="TaskRun") | .name' 2>/dev/null || echo "")
        fi
    fi

    if [ -z "$TASKRUNS" ]; then
        echo -e "${RED}✗ No TaskRuns encontrados${NC}"
        echo ""
        continue
    fi

    TASKRUN_COUNT=$(echo "$TASKRUNS" | wc -l)
    echo -e "${GREEN}$TASKRUN_COUNT${NC}"

    # Step 2: Get logs from each TaskRun
    ALL_LOGS=""
    LOGS_FOUND=false

    while IFS= read -r taskrun_name; do
        [ -z "$taskrun_name" ] && continue

        echo -n "    $taskrun_name... "

        # Get TaskRun details (using correct Tekton API path)
        TR_DETAILS=$(curl -s -H "Authorization: Bearer $TOKEN" \
            "$KUBEARCHIVE_API/apis/tekton.dev/v1/namespaces/$NAMESPACE/taskruns/$taskrun_name" 2>/dev/null || echo "")

        if [ -z "$TR_DETAILS" ]; then
            echo -e "${YELLOW}⚠ No encontrado${NC}"
            continue
        fi

        # Get pod name and failed steps
        POD_NAME=$(echo "$TR_DETAILS" | jq -r '.status.podName // ""')

        # Find failed steps
        FAILED_STEPS=$(echo "$TR_DETAILS" | jq -r '.status.steps[]? | select(.terminated.exitCode != 0) | .name' 2>/dev/null || echo "")

        if [ -z "$POD_NAME" ]; then
            echo -e "${YELLOW}⚠ Sin pod${NC}"
            continue
        fi

        # Get logs from failed steps (or all steps if none explicitly failed)
        if [ -n "$FAILED_STEPS" ]; then
            # Get logs from failed steps
            while IFS= read -r step_name; do
                [ -z "$step_name" ] && continue

                STEP_LOGS=$(curl -s -H "Authorization: Bearer $TOKEN" \
                    "$KUBEARCHIVE_API/api/v1/namespaces/$NAMESPACE/pods/$POD_NAME/log?container=$step_name" 2>/dev/null || echo "")

                if [ -n "$STEP_LOGS" ]; then
                    ALL_LOGS="$ALL_LOGS

===== TaskRun: $taskrun_name / Step: $step_name =====
$STEP_LOGS
"
                    LOGS_FOUND=true
                fi
            done <<< "$FAILED_STEPS"
        else
            # Get logs from all containers
            CONTAINERS=$(echo "$TR_DETAILS" | jq -r '.status.steps[]?.name' 2>/dev/null || echo "")

            if [ -n "$CONTAINERS" ]; then
                while IFS= read -r container_name; do
                    [ -z "$container_name" ] && continue

                    CONTAINER_LOGS=$(curl -s -H "Authorization: Bearer $TOKEN" \
                        "$KUBEARCHIVE_API/api/v1/namespaces/$NAMESPACE/pods/$POD_NAME/log?container=$container_name" 2>/dev/null || echo "")

                    if [ -n "$CONTAINER_LOGS" ]; then
                        ALL_LOGS="$ALL_LOGS

===== TaskRun: $taskrun_name / Container: $container_name =====
$CONTAINER_LOGS
"
                        LOGS_FOUND=true
                    fi
                done <<< "$CONTAINERS"
            fi
        fi

        if [ "$LOGS_FOUND" = true ]; then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${YELLOW}⚠${NC}"
        fi

    done <<< "$TASKRUNS"

    # Step 3: Save logs to database
    if [ "$LOGS_FOUND" = true ]; then
        echo -n "  Guardando en DB... "

        # Truncate to 100000 chars
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
        echo "  ℹ  No hay logs disponibles"
    fi

    echo ""

done <<< "$PRS_WITHOUT_LOGS"

echo "========================================"
echo -e "${GREEN}Proceso Completado${NC}"
echo "========================================"
echo ""
