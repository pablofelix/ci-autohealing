#!/bin/bash

# Smart log fetcher - tries all methods and saves to database
# Usage: ./fetch-logs-smart.sh [pipelinerun-name]
# If no name provided, fetches logs for all PRs without logs

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
export PGPASSWORD="admin"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Function to get logs via multiple methods
get_logs() {
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

    # Method 3: KubeArchive API (for archived PRs)
    TOKEN=$(oc whoami -t 2>/dev/null)
    KUBEARCHIVE_API="https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN"

    if [ -n "$TOKEN" ]; then
        PR_JSON=$(curl -s -H "Authorization: Bearer $TOKEN" \
            "$KUBEARCHIVE_API/api/v1/namespaces/$NAMESPACE/pipelineruns/$pr_name" 2>/dev/null)

        if echo "$PR_JSON" | jq -e '.kind == "PipelineRun"' &>/dev/null; then
            TASKRUNS=$(echo "$PR_JSON" | jq -r '.status.childReferences[]? | select(.kind=="TaskRun") | .name')

            if [ -n "$TASKRUNS" ]; then
                while IFS= read -r tr; do
                    [ -z "$tr" ] && continue

                    TR_JSON=$(curl -s -H "Authorization: Bearer $TOKEN" \
                        "$KUBEARCHIVE_API/api/v1/namespaces/$NAMESPACE/taskruns/$tr" 2>/dev/null)

                    POD_NAME=$(echo "$TR_JSON" | jq -r '.status.podName // ""')

                    if [ -n "$POD_NAME" ]; then
                        # Get failed step or all steps
                        FAILED_STEP=$(echo "$TR_JSON" | jq -r '.status.steps[] | select(.terminated.exitCode != 0) | .name' | head -1)

                        if [ -n "$FAILED_STEP" ]; then
                            STEP_LOGS=$(curl -s -H "Authorization: Bearer $TOKEN" \
                                "$KUBEARCHIVE_API/api/v1/namespaces/$NAMESPACE/pods/$POD_NAME/log?container=$FAILED_STEP" 2>/dev/null)

                            if [ -n "$STEP_LOGS" ]; then
                                logs+="
===== TaskRun: $tr / Step: $FAILED_STEP =====
$STEP_LOGS
"
                            fi
                        fi
                    fi
                done <<< "$TASKRUNS"

                if [ -n "$logs" ]; then
                    echo "$logs"
                    return 0
                fi
            fi
        fi
    fi

    # Method 4: kubectl tekton (slow but comprehensive)
    TEKTON_LOGS=$(timeout 45 bash -c "yes '' 2>/dev/null | kubectl tekton logs pr '$pr_name' -n '$NAMESPACE' 2>/dev/null" || echo "")

    if [ -n "$TEKTON_LOGS" ]; then
        echo "$TEKTON_LOGS"
        return 0
    fi

    # No logs found
    return 1
}

# Main execution
if [ $# -eq 1 ]; then
    # Single PipelineRun mode
    PR_NAME="$1"

    echo -e "${BOLD}========================================${NC}"
    echo -e "${BLUE}Fetching logs for: $PR_NAME${NC}"
    echo -e "${BOLD}========================================${NC}"
    echo ""

    LOGS=$(get_logs "$PR_NAME")

    if [ -n "$LOGS" ]; then
        echo -e "${GREEN}✓ Logs retrieved${NC}"
        echo ""
        echo "$LOGS"

        # Save to database
        echo ""
        echo -e "${CYAN}Saving to database...${NC}"

        LOGS_TRUNCATED=$(echo "$LOGS" | head -c 100000)
        LOGS_ESC=$(echo "$LOGS_TRUNCATED" | sed "s/'/''/g")

        docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c "
        UPDATE build_failures
        SET build_logs = '$LOGS_ESC'
        WHERE pipelinerun_name = '$PR_NAME';
        " > /dev/null 2>&1

        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ Saved to database${NC}"
        else
            echo -e "${RED}✗ Database save failed${NC}"
        fi
    else
        echo -e "${RED}✗ Unable to retrieve logs${NC}"
        echo ""
        echo -e "View in Konflux UI: ${CYAN}https://konflux-ui.apps.CLUSTER_DOMAIN/ns/$NAMESPACE/applications/$APPLICATION_NAME/pipelineruns/$PR_NAME/logs${NC}"
        exit 1
    fi

else
    # Batch mode - fetch logs for all PRs without logs
    echo -e "${BOLD}========================================${NC}"
    echo -e "${BLUE}Fetching logs for all PRs without logs${NC}"
    echo -e "${BOLD}========================================${NC}"
    echo ""

    PRS_WITHOUT_LOGS=$(docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -tAc \
        "SELECT pipelinerun_name FROM build_failures
         WHERE build_logs IS NULL
         ORDER BY first_detected_at DESC
         LIMIT 20;" 2>/dev/null)

    if [ -z "$PRS_WITHOUT_LOGS" ]; then
        echo "No PipelineRuns without logs"
        exit 0
    fi

    TOTAL=$(echo "$PRS_WITHOUT_LOGS" | wc -l)
    CURRENT=0
    SUCCESS=0

    while IFS= read -r pr_name; do
        [ -z "$pr_name" ] && continue

        CURRENT=$((CURRENT + 1))
        echo -e "${BLUE}[$CURRENT/$TOTAL] $pr_name${NC}"

        LOGS=$(get_logs "$pr_name")

        if [ -n "$LOGS" ]; then
            echo -e "  ${GREEN}✓ Logs retrieved${NC}"

            # Save to database
            LOGS_TRUNCATED=$(echo "$LOGS" | head -c 100000)
            LOGS_ESC=$(echo "$LOGS_TRUNCATED" | sed "s/'/''/g")

            docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c "
            UPDATE build_failures
            SET build_logs = '$LOGS_ESC'
            WHERE pipelinerun_name = '$pr_name';
            " > /dev/null 2>&1

            if [ $? -eq 0 ]; then
                echo -e "  ${GREEN}✓ Saved to database${NC}"
                SUCCESS=$((SUCCESS + 1))
            else
                echo -e "  ${RED}✗ Database save failed${NC}"
            fi
        else
            echo -e "  ${YELLOW}⚠ No logs available${NC}"
        fi

        echo ""
    done <<< "$PRS_WITHOUT_LOGS"

    echo -e "${BOLD}========================================${NC}"
    echo -e "${GREEN}Completed${NC}"
    echo -e "${BOLD}========================================${NC}"
    echo ""
    echo "  Processed: $TOTAL"
    echo "  Success: $SUCCESS"
    echo ""
fi
