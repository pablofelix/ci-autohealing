#!/bin/bash
# ic-config.sh — Configuration defaults for ic CLI
# Sourced by ic; do not execute directly.
# Override any value via .env or environment variables.

: "${NAMESPACE:=NAMESPACE_PLACEHOLDER}"
: "${APPLICATION_NAME:=acme-v2-0}"
: "${DB_CONTAINER:=ci-autohealing-db}"
: "${DB_NAME:=konflux_monitoring}"
: "${DB_USER:=postgres}"
: "${PGPASSWORD:=admin}"
export PGPASSWORD

: "${KUBEARCHIVE_URL:=https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN}"
: "${KONFLUX_UI_BASE:=https://konflux-ui.apps.CLUSTER_DOMAIN}"

require_db() {
    docker exec "$DB_CONTAINER" pg_isready -q 2>/dev/null || {
        echo -e "${RED}Error: database is not running (${DB_CONTAINER})${NC}" >&2
        echo -e "${CYAN}Start it: docker start ${DB_CONTAINER}${NC}" >&2
        return 1
    }
}

# Langfuse observability — inherited by python3.11 subprocesses (analyze_failures.py etc.)
: "${LANGFUSE_HOST:=http://localhost:3000}"
export LANGFUSE_HOST LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

: "${COMPONENT_DATA_URL:=https://GITLAB_INTERNAL_HOST/wznoinsk/rhoai-monitoring/-/raw/main/data/rhoai-component-data.yaml}"
: "${COMPONENT_DATA_CACHE:=/tmp/rhoai-component-data.yaml}"
: "${COMPONENT_DATA_TTL:=86400}"

GITLAB_API_BASE="https://GITLAB_INTERNAL_HOST/api/v4/projects/releng%2Fkonflux-release-data/repository/files"
GITLAB_POLICY_PATH="config%2FCLUSTER_SHORT%2Fproduct%2FEnterpriseContractPolicy"
GITLAB_POLICY_FILES=(
    "${GITLAB_POLICY_PATH}%2Fregistry-acme-prod.yaml"
    "${GITLAB_POLICY_PATH}%2Ffbc-acme-prod.yaml"
    "${GITLAB_POLICY_PATH}%2Fregistry-acme-stage.yaml"
    "${GITLAB_POLICY_PATH}%2Ffbc-acme-stage.yaml"
)
