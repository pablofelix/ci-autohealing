#!/bin/bash

# Simple collector - uses tabular output from kubectl tekton
# Only inserts basic info (component, pipelinerun, status) without detailed queries

set -e

# Load configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/../.env" ]; then
    set -a  # Export all variables
    source "$SCRIPT_DIR/../.env"
    set +a
fi

NAMESPACE="${NAMESPACE:-NAMESPACE_PLACEHOLDER}"
APPLICATION_NAME="${APPLICATION_NAME:-acme-v2-0}"
COMPONENTS_FILE="${COMPONENTS_FILE:-HOME_DIR/components-ui-failed.txt}"
export PGPASSWORD="admin"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Function to get logs quickly (tries active pods first)
get_logs_fast() {
    local pr_name="$1"
    local logs=""

    # Method 1: Active pods (fastest, most reliable for recent PRs)
    PODS=$(oc get pod -n "$NAMESPACE" -l tekton.dev/pipelineRun="$pr_name" --no-headers 2>/dev/null | awk '{print $1}')

    if [ -n "$PODS" ]; then
        while IFS= read -r pod; do
            [ -z "$pod" ] && continue
            POD_LOGS=$(oc logs "$pod" -n "$NAMESPACE" --all-containers 2>/dev/null || echo "")
            if [ -n "$POD_LOGS" ]; then
                logs+="
===== Pod: $pod =====
$POD_LOGS
"
            fi
        done <<< "$PODS"

        if [ -n "$logs" ]; then
            echo "$logs"
            return 0
        fi
    fi

    # Method 2: Recent PipelineRun TaskRuns
    if oc get pipelinerun "$pr_name" -n "$NAMESPACE" &>/dev/null; then
        TASKRUNS=$(oc get pipelinerun "$pr_name" -n "$NAMESPACE" -o json 2>/dev/null | \
            jq -r '.status.childReferences[]? | select(.kind=="TaskRun") | .name')

        if [ -n "$TASKRUNS" ]; then
            while IFS= read -r tr; do
                [ -z "$tr" ] && continue

                POD=$(oc get taskrun "$tr" -n "$NAMESPACE" -o json 2>/dev/null | jq -r '.status.podName // ""')

                if [ -n "$POD" ]; then
                    TR_LOGS=$(oc logs "$POD" -n "$NAMESPACE" --all-containers 2>/dev/null || echo "")
                    if [ -n "$TR_LOGS" ]; then
                        logs+="
===== TaskRun: $tr =====
$TR_LOGS
"
                    fi
                fi
            done <<< "$TASKRUNS"

            if [ -n "$logs" ]; then
                echo "$logs"
                return 0
            fi
        fi
    fi

    # No logs found
    return 1
}

echo "========================================"
echo -e "${BLUE}Recolector Simple - Solo Status${NC}"
echo "========================================"
echo ""

# Verify DB
if ! docker ps | grep -q ci-autohealing-db; then
    echo -e "${RED}Error: PostgreSQL container not running${NC}"
    exit 1
fi

# Read components
COMPONENTS=$(grep -v '^#' "$COMPONENTS_FILE" | grep -v '^$')
COMPONENT_COUNT=$(echo "$COMPONENTS" | wc -l)
echo -e "${GREEN}✓ $COMPONENT_COUNT componentes${NC}"
echo ""

# Start scan
SCAN_ID=$(uuidgen)
docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c \
    "INSERT INTO scan_history (scan_id, scan_type, scan_mode, status) VALUES ('$SCAN_ID', 'manual', 'simple', 'running');" > /dev/null

# Counters
TOTAL_SCANNED=0
TOTAL_FAILURES=0
NEW_INSERTED=0
LOGS_FETCHED=0

echo -e "${YELLOW}Procesando componentes...${NC}"
echo ""

# Process each component
while IFS= read -r component; do
    [ -z "$component" ] && continue

    TOTAL_SCANNED=$((TOTAL_SCANNED + 1))

    echo -e "${BLUE}[$TOTAL_SCANNED/$COMPONENT_COUNT] $component${NC}"

    # Get component metadata
    COMP_DATA=$(oc get component "$component" -n "$NAMESPACE" -o json 2>/dev/null || echo "")

    if [ -z "$COMP_DATA" ]; then
        echo -e "  ${RED}✗ No encontrado${NC}"
        continue
    fi

    REPO_URL=$(echo "$COMP_DATA" | jq -r '.spec.source.git.url // ""')
    REPO_SHORT=$(echo "$REPO_URL" | sed 's|https://github.com/||' | sed 's|.git||')
    BRANCH=$(echo "$COMP_DATA" | jq -r '.spec.source.git.revision // ""')

    echo "  Repo: $REPO_SHORT ($BRANCH)"

    # Get PRs from Tekton Results using tabular output (FAST!)
    echo -n "  Buscando PRs... "

    PR_LIST=$(timeout 20 bash -c "yes '' 2>/dev/null | kubectl tekton get pr -n '$NAMESPACE' \
        --labels='appstudio.openshift.io/component=$component' \
        --limit 10 2>/dev/null" | \
        grep -v "^NAME" | \
        grep -v "^Next" | \
        grep -v "^$" | \
        grep -v "Press any key" || echo "")

    if [ -z "$PR_LIST" ]; then
        echo -e "${GREEN}0${NC}"
        echo ""
        continue
    fi

    PR_COUNT=$(echo "$PR_LIST" | wc -l)
    echo -e "${GREEN}$PR_COUNT${NC}"

    # Parse tabular output
    # Format: NAME  UID  STARTED  DURATION  STATUS
    while IFS= read -r pr_line; do
        [ -z "$pr_line" ] && continue

        # Extract fields (space-separated)
        PR_NAME=$(echo "$pr_line" | awk '{print $1}')
        PR_UID=$(echo "$pr_line" | awk '{print $2}')
        PR_STATUS=$(echo "$pr_line" | awk '{print $NF}')  # Last field is STATUS

        # Skip if not Failed
        if [[ "$PR_STATUS" != "Failed"* ]]; then
            continue
        fi

        TOTAL_FAILURES=$((TOTAL_FAILURES + 1))

        # Check if already in DB
        EXISTS=$(docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -tAc \
            "SELECT 1 FROM build_failures WHERE pipelinerun_name = '$PR_NAME' LIMIT 1" 2>/dev/null || echo "0")

        if [ "$EXISTS" = "1" ]; then
            echo "    ~ $PR_NAME (ya existe)"
            continue
        fi

        # Escape quotes
        REPO_SHORT_ESC=$(echo "$REPO_SHORT" | sed "s/'/''/g")
        REPO_URL_ESC=$(echo "$REPO_URL" | sed "s/'/''/g")

        # Insert with minimal data (includes UID and Konflux logs URL)
        # Derive application from component version (e.g., v3-4 -> acme-v2-0)
        APPLICATION="acme-v2-0"  # All current components use this application
        KONFLUX_LOGS_URL="https://konflux-ui.apps.CLUSTER_DOMAIN/ns/$NAMESPACE/applications/$APPLICATION/pipelineruns/$PR_NAME/logs"

        docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c "
        INSERT INTO build_failures (
            component_name, pipelinerun_name, pipelinerun_uid, namespace, repository, repository_url,
            branch, status, konflux_logs_url, first_detected_at
        ) VALUES (
            '$component', '$PR_NAME', '$PR_UID', '$NAMESPACE', '$REPO_SHORT_ESC', '$REPO_URL_ESC',
            '$BRANCH', 'Failed', '$KONFLUX_LOGS_URL', NOW()
        );
        " > /dev/null 2>&1

        if [ $? -eq 0 ]; then
            echo "    ✓ $PR_NAME"
            NEW_INSERTED=$((NEW_INSERTED + 1))

            # Immediately try to fetch logs while PR is still fresh
            echo -n "      Fetching logs... "
            LOGS=$(get_logs_fast "$PR_NAME" 2>/dev/null || echo "")

            if [ -n "$LOGS" ]; then
                # Save logs to database
                LOGS_TRUNCATED=$(echo "$LOGS" | head -c 100000)
                LOGS_ESC=$(echo "$LOGS_TRUNCATED" | sed "s/'/''/g")

                docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c "
                UPDATE build_failures
                SET build_logs = '$LOGS_ESC'
                WHERE pipelinerun_name = '$PR_NAME';
                " > /dev/null 2>&1

                if [ $? -eq 0 ]; then
                    echo -e "${GREEN}✓${NC}"
                    LOGS_FETCHED=$((LOGS_FETCHED + 1))
                else
                    echo -e "${YELLOW}⚠${NC}"
                fi
            else
                echo -e "${YELLOW}⚠ Not available yet${NC}"
            fi
        else
            echo "    ✗ $PR_NAME (error SQL)"
        fi

    done <<< "$PR_LIST"

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
    new_failures = $NEW_INSERTED,
    logs_fetched = $LOGS_FETCHED
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
echo "  Logs descargados: $LOGS_FETCHED"
echo ""
echo "Ver resultados: ./show-dashboard.sh"
echo ""
