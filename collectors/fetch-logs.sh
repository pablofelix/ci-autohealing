#!/bin/bash

# Fetch logs for PipelineRuns that don't have logs yet

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
echo -e "${BLUE}Obteniendo Logs de PipelineRuns${NC}"
echo "========================================"
echo ""

# Get PRs without logs
PRS_WITHOUT_LOGS=$(docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -tAc \
    "SELECT pipelinerun_name, pipelinerun_uid FROM build_failures
     WHERE pipelinerun_uid IS NOT NULL AND build_logs IS NULL
     ORDER BY build_completion_time DESC NULLS LAST
     LIMIT 20;" 2>/dev/null)

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
    echo "  UID: $pr_uid"

    # Get logs using UID
    echo -n "  Obteniendo logs... "

    LOGS=$(timeout 30 bash -c "yes '' 2>/dev/null | kubectl tekton logs pr --uid '$pr_uid' -n '$NAMESPACE' 2>/dev/null" || echo "")

    if [ -z "$LOGS" ]; then
        echo -e "${RED}✗ No disponibles${NC}"
        continue
    fi

    # Truncate logs to 10000 chars
    LOGS_TRUNCATED=$(echo "$LOGS" | head -c 10000)

    # Escape for SQL
    LOGS_ESC=$(echo "$LOGS_TRUNCATED" | sed "s/'/''/g")

    # Update database
    docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c "
    UPDATE build_failures
    SET build_logs = '$LOGS_ESC'
    WHERE pipelinerun_uid = '$pr_uid';
    " > /dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Guardados${NC}"
    else
        echo -e "${RED}✗ Error SQL${NC}"
    fi

    echo ""

done <<< "$PRS_WITHOUT_LOGS"

echo "========================================"
echo -e "${GREEN}Logs Obtenidos${NC}"
echo "========================================"
echo ""
