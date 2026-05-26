#!/bin/bash
# ic-config.sh — Configuration defaults for ic CLI
# Sourced by ic; do not execute directly.
# Override any value via .env or environment variables.

: "${PYTHON:=python3}"
: "${NAMESPACE:=}"
: "${APPLICATION_NAME:=}"
: "${KNOWN_APPLICATIONS:=}"
# Autonomous mode: set to "true" in .env to enable automatic PR creation for
# conforma violations during cron step 7.5. Off by default — enable only after
# manual ic fix validation confirms the fixers are working correctly.
: "${AUTONOMOUS_MODE:=false}"
: "${DB_CONTAINER:=ci-autohealing-db}"
: "${DB_NAME:=konflux_monitoring}"
: "${DB_USER:=postgres}"
: "${PGPASSWORD:=}"
export PGPASSWORD

: "${KUBEARCHIVE_URL:=}"
: "${KONFLUX_UI_BASE:=}"
: "${QUAY_ORG:=}"

require_db() {
    docker exec "$DB_CONTAINER" pg_isready -q 2>/dev/null || {
        echo -e "${RED}Error: database is not running (${DB_CONTAINER})${NC}" >&2
        echo -e "${CYAN}Start it: docker start ${DB_CONTAINER}${NC}" >&2
        return 1
    }
}

# Langfuse observability — inherited by Python subprocesses (analyze_failures.py etc.)
: "${LANGFUSE_HOST:=http://localhost:3000}"
export LANGFUSE_HOST LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

: "${COMPONENT_DATA_URL:=}"
: "${COMPONENT_DATA_CACHE:=/tmp/component-data.yaml}"
: "${COMPONENT_DATA_TTL:=86400}"

: "${GITLAB_API_BASE:=}"
: "${GITLAB_POLICY_PATH:=}"
: "${GITLAB_POLICY_FILENAMES:=}"
IFS=',' read -ra _policy_names <<< "$GITLAB_POLICY_FILENAMES"
GITLAB_POLICY_FILES=()
for _pf in "${_policy_names[@]}"; do
    [[ -n "$_pf" ]] && GITLAB_POLICY_FILES+=("${GITLAB_POLICY_PATH}%2F${_pf}")
done
unset _policy_names _pf
