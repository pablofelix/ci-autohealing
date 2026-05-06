#!/bin/bash
# ic-queries.sh — Reusable SQL query functions for ic CLI
# Sourced by ic; do not execute directly.
# Depends on: sql() and sql_table() from ic, $APPLICATION_NAME from ic-config.sh

sql_count_failing_builds() {
    local app="${1:-$APPLICATION_NAME}"
    sql "
        WITH latest_builds AS (
            SELECT DISTINCT ON (component_name)
                component_name, status, is_resolved
            FROM build_failures
            WHERE application = '$app'
            ORDER BY component_name, first_detected_at DESC
        )
        SELECT COUNT(*) FROM latest_builds
        WHERE status = 'Failed' AND is_resolved = FALSE;
    "
}

sql_count_working_builds() {
    local app="${1:-$APPLICATION_NAME}"
    sql "
        WITH latest_builds AS (
            SELECT DISTINCT ON (component_name)
                component_name, status, is_resolved
            FROM build_failures
            WHERE application = '$app'
            ORDER BY component_name, first_detected_at DESC
        )
        SELECT COUNT(*) FROM latest_builds WHERE status = 'Succeeded';
    "
}

sql_count_unresolved_conforma() {
    local app="${1:-$APPLICATION_NAME}"
    sql "
        SELECT COUNT(DISTINCT component_name) FROM conforma_results
        WHERE application = '$app' AND is_resolved = FALSE;
    "
}

sql_resolve_build_number() {
    local target="$1"
    local retriggered_json="${2:-[]}"
    local app="${3:-$APPLICATION_NAME}"
    sql "
        WITH latest_builds AS (
            SELECT DISTINCT ON (component_name)
                component_name, status, is_resolved, first_detected_at,
                (SELECT COUNT(*) FROM build_failures bf
                 WHERE bf.component_name = build_failures.component_name) as total_failures
            FROM build_failures
            WHERE application = '$app'
            ORDER BY component_name, first_detected_at DESC
        ),
        failing AS (
            SELECT component_name, total_failures,
                CASE WHEN component_name IN (
                    SELECT value FROM json_array_elements_text('${retriggered_json}')
                ) THEN 2 ELSE 1 END as sort_group
            FROM latest_builds
            WHERE status = 'Failed' AND is_resolved = FALSE
        )
        SELECT component_name FROM failing
        ORDER BY sort_group, total_failures DESC
        OFFSET $((target - 1))
        LIMIT 1;
    "
}

sql_resolve_conforma_number() {
    local offset="$1"
    local app="${2:-$APPLICATION_NAME}"
    sql "
        WITH latest_conforma AS (
            SELECT DISTINCT ON (component_name)
                component_name, violations_count
            FROM conforma_results
            WHERE application = '$app'
              AND is_resolved = FALSE
            ORDER BY component_name, last_updated_at DESC
        )
        SELECT component_name FROM latest_conforma
        ORDER BY violations_count DESC
        OFFSET $offset
        LIMIT 1;
    " 2>/dev/null
}

sql_get_retriggered_json() {
    local app="${1:-$APPLICATION_NAME}"
    sql "SELECT COALESCE(retriggered_components::text, '[]')
         FROM sync_status WHERE application = '$app';" 2>/dev/null
}
