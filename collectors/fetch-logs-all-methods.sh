#!/bin/bash

# Comprehensive log fetcher - tries all available methods
# 1. Active Kubernetes pods (oc logs)
# 2. KubeArchive API (archived)
# 3. Tekton API (if accessible)
# 4. Konflux API (experimental)

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
NC='\033[0m'

# Show usage
if [ $# -lt 1 ]; then
    cat << EOF
${BLUE}Fetch logs using all available methods${NC}

USAGE:
    $0 <pipelinerun-name> [namespace]

METHODS TRIED:
    1. Active Kubernetes pods (oc logs)
    2. KubeArchive API (archived PipelineRuns)
    3. Tekton Results API (if accessible)
    4. Direct TaskRun pod logs

EXAMPLES:
    $0 odh-workbench-jupyter-pytorch-llmcompressor-cuda-py312-v3-kn8bx
    $0 odh-mod-arch-mlflow-v3-4-on-push-kglz6 NAMESPACE_PLACEHOLDER

EOF
    exit 1
fi

PR_NAME="$1"
NAMESPACE="${2:-$NAMESPACE}"

echo -e "${BOLD}========================================${NC}"
echo -e "${BLUE}Fetching logs for: $PR_NAME${NC}"
echo -e "${BOLD}========================================${NC}"
echo ""

# Method 1: Try active Kubernetes pods
echo -e "${CYAN}[Method 1]${NC} Checking active Kubernetes pods..."

PODS=$(oc get pod -n "$NAMESPACE" -l tekton.dev/pipelineRun="$PR_NAME" --no-headers 2>/dev/null | awk '{print $1}')

if [ -n "$PODS" ]; then
    echo -e "${GREEN}✓ Found active pods${NC}"

    while IFS= read -r pod; do
        [ -z "$pod" ] && continue

        echo ""
        echo -e "${BLUE}===== Pod: $pod =====${NC}"
        oc logs "$pod" -n "$NAMESPACE" --all-containers 2>/dev/null || echo -e "${YELLOW}⚠ No logs available${NC}"
    done <<< "$PODS"

    exit 0
fi

echo -e "${YELLOW}✗ No active pods found${NC}"
echo ""

# Method 2: Try recent PipelineRun via oc
echo -e "${CYAN}[Method 2]${NC} Checking recent PipelineRun..."

if oc get pipelinerun "$PR_NAME" -n "$NAMESPACE" &>/dev/null; then
    echo -e "${GREEN}✓ PipelineRun exists in cluster${NC}"

    # Get TaskRuns
    TASKRUNS=$(oc get pipelinerun "$PR_NAME" -n "$NAMESPACE" -o json 2>/dev/null | \
        jq -r '.status.childReferences[]? | select(.kind=="TaskRun") | .name')

    if [ -n "$TASKRUNS" ]; then
        echo -e "${GREEN}✓ Found TaskRuns${NC}"

        while IFS= read -r tr; do
            [ -z "$tr" ] && continue

            echo ""
            echo -e "${BLUE}===== TaskRun: $tr =====${NC}"

            # Get pod from TaskRun
            POD=$(oc get taskrun "$tr" -n "$NAMESPACE" -o json 2>/dev/null | jq -r '.status.podName // ""')

            if [ -n "$POD" ]; then
                oc logs "$POD" -n "$NAMESPACE" --all-containers 2>/dev/null || echo -e "${YELLOW}⚠ No logs available${NC}"
            else
                echo -e "${YELLOW}⚠ Pod not found${NC}"
            fi
        done <<< "$TASKRUNS"

        exit 0
    fi
fi

echo -e "${YELLOW}✗ Not found in active cluster${NC}"
echo ""

# Method 3: Try KubeArchive API
echo -e "${CYAN}[Method 3]${NC} Checking KubeArchive API..."

TOKEN=$(oc whoami -t 2>/dev/null)
# Get KubeArchive API URL from ConfigMap
KUBEARCHIVE_API=$(oc get cm -n product-kubearchive kubearchive-api-url -o jsonpath='{.data.URL}' 2>/dev/null || echo "https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN")

if [ -z "$TOKEN" ]; then
    echo -e "${RED}✗ Not logged into OpenShift${NC}"
    exit 1
fi

# Get PipelineRun from KubeArchive (using correct Tekton API path)
PR_JSON=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "$KUBEARCHIVE_API/apis/tekton.dev/v1/namespaces/$NAMESPACE/pipelineruns/$PR_NAME" 2>/dev/null)

if echo "$PR_JSON" | grep -q "unauthorized"; then
    echo -e "${YELLOW}✗ KubeArchive API: unauthorized${NC}"
    echo -e "${YELLOW}  (You may need additional permissions)${NC}"
    echo ""
elif echo "$PR_JSON" | jq -e '.kind == "PipelineRun"' &>/dev/null; then
    echo -e "${GREEN}✓ PipelineRun found in KubeArchive${NC}"

    # Get TaskRuns
    TASKRUNS=$(echo "$PR_JSON" | jq -r '.status.childReferences[]? | select(.kind=="TaskRun") | .name')

    if [ -z "$TASKRUNS" ]; then
        echo -e "${YELLOW}✗ No TaskRuns found${NC}"
        exit 1
    fi

    echo -e "${GREEN}✓ Found TaskRuns${NC}"

    while IFS= read -r tr; do
        [ -z "$tr" ] && continue

        echo ""
        echo -e "${BLUE}===== TaskRun: $tr =====${NC}"

        # Get TaskRun details (using correct Tekton API path)
        TR_JSON=$(curl -s -H "Authorization: Bearer $TOKEN" \
            "$KUBEARCHIVE_API/apis/tekton.dev/v1/namespaces/$NAMESPACE/taskruns/$tr" 2>/dev/null)

        # Get pod name
        POD_NAME=$(echo "$TR_JSON" | jq -r '.status.podName // ""')

        if [ -z "$POD_NAME" ]; then
            echo -e "${YELLOW}⚠ Pod name not found${NC}"
            continue
        fi

        # Get failed step (or all steps)
        FAILED_STEP=$(echo "$TR_JSON" | jq -r '.status.steps[] | select(.terminated.exitCode != 0) | .name' | head -1)

        if [ -n "$FAILED_STEP" ]; then
            echo -e "${CYAN}Failed step: $FAILED_STEP${NC}"
            curl -s -H "Authorization: Bearer $TOKEN" \
                "$KUBEARCHIVE_API/api/v1/namespaces/$NAMESPACE/pods/$POD_NAME/log?container=$FAILED_STEP" 2>/dev/null
        else
            # Get all containers
            CONTAINERS=$(echo "$TR_JSON" | jq -r '.status.steps[]?.name')

            for container in $CONTAINERS; do
                echo -e "${CYAN}Container: $container${NC}"
                curl -s -H "Authorization: Bearer $TOKEN" \
                    "$KUBEARCHIVE_API/api/v1/namespaces/$NAMESPACE/pods/$POD_NAME/log?container=$container" 2>/dev/null
                echo ""
            done
        fi
    done <<< "$TASKRUNS"

    exit 0
else
    echo -e "${YELLOW}✗ Not found in KubeArchive${NC}"
fi

echo ""

# Method 4: Try kubectl tekton (slow but comprehensive)
echo -e "${CYAN}[Method 4]${NC} Trying kubectl tekton logs (may be slow)..."

if timeout 30 bash -c "yes '' 2>/dev/null | kubectl tekton logs pr '$PR_NAME' -n '$NAMESPACE' 2>&1" | head -100; then
    echo -e "${GREEN}✓ Logs retrieved${NC}"
    exit 0
fi

echo -e "${YELLOW}✗ kubectl tekton timed out or failed${NC}"
echo ""

# No logs found
echo -e "${RED}========================================${NC}"
echo -e "${RED}Unable to fetch logs${NC}"
echo -e "${RED}========================================${NC}"
echo ""
echo "The PipelineRun may be:"
echo "  • Too old (archived and purged)"
echo "  • Not yet archived"
echo "  • Inaccessible with current permissions"
echo ""
echo -e "View in Konflux UI: ${CYAN}https://konflux-ui.apps.CLUSTER_DOMAIN/ns/$NAMESPACE/applications/$APPLICATION_NAME/pipelineruns/$PR_NAME/logs${NC}"
echo ""

exit 1
