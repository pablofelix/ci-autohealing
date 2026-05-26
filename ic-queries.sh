#!/bin/bash
# ic-queries.sh — Reusable SQL query functions for ic CLI
# Sourced by ic; do not execute directly.
# Depends on: sql() and sql_table() from ic, $APPLICATION_NAME from ic-config.sh

sql_count_failing_builds() {
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
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
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
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
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    sql "
        SELECT COUNT(DISTINCT component_name) FROM conforma_results
        WHERE application = '$app' AND is_resolved = FALSE;
    "
}

sql_resolve_build_number() {
    local target="$1"
    local retriggered_json="${2:-[]}"
    local app="${3:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
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
    local app="${2:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
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
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    sql "SELECT COALESCE(retriggered_components::text, '[]')
         FROM sync_status WHERE application = '$app';" 2>/dev/null
}

# -- Release readiness / history queries --

sql_failing_builds_with_health() {
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    sql "
        WITH latest_builds AS (
            SELECT DISTINCT ON (component_name)
                component_name, status, is_resolved
            FROM build_failures
            WHERE application = '$app'
            ORDER BY component_name, first_detected_at DESC
        )
        SELECT lb.component_name,
               COALESCE(ch.health_score::int::text, '-'),
               COALESCE(ch.consecutive_failures::text, '0')
        FROM latest_builds lb
        LEFT JOIN component_health ch ON lb.component_name = ch.component_name
            AND ch.application = '$app'
        WHERE lb.status = 'Failed' AND lb.is_resolved = FALSE
        ORDER BY ch.health_score ASC NULLS FIRST;
    "
}

sql_recently_resolved_builds() {
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    local days="${2:-7}"
    [[ "$days" =~ ^[0-9]+$ ]] || days=7
    sql "
        SELECT component_name,
               TO_CHAR(resolved_at, 'Mon DD') as resolved_date,
               LEFT(resolution_commit_sha, 8) as fix_commit
        FROM build_failures
        WHERE application = '$app'
          AND is_resolved = TRUE
          AND resolved_at >= NOW() - INTERVAL '${days} days'
        ORDER BY resolved_at DESC;
    "
}

sql_builds_resolved_between() {
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    local from_date="$2" to_date="$3"
    sql "
        SELECT component_name,
               TO_CHAR(resolved_at, 'Mon DD') as resolved_date,
               LEFT(resolution_commit_sha, 8) as fix_commit
        FROM build_failures
        WHERE application = '$app'
          AND is_resolved = TRUE
          AND resolved_at BETWEEN '$from_date' AND '$to_date'
        ORDER BY resolved_at;
    "
}

sql_conforma_blocking() {
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    sql "
        SELECT DISTINCT ON (component_name)
            component_name, violations_count, scenario
        FROM conforma_results
        WHERE application = '$app'
          AND is_resolved = FALSE
          AND is_future = FALSE
        ORDER BY component_name, last_updated_at DESC;
    "
}

sql_conforma_resolved_between() {
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    local from_date="$2" to_date="$3"
    sql "
        SELECT DISTINCT component_name, TO_CHAR(resolved_at, 'Mon DD')
        FROM conforma_results
        WHERE application = '$app'
          AND is_resolved = TRUE
          AND is_future = FALSE
          AND resolved_at BETWEEN '$from_date' AND '$to_date'
        ORDER BY component_name;
    "
}

sql_conforma_appeared_between() {
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    local from_date="$2" to_date="$3"
    sql "
        SELECT DISTINCT ON (component_name)
            component_name, TO_CHAR(first_detected_at, 'Mon DD'), violations_count
        FROM conforma_results
        WHERE application = '$app'
          AND is_future = FALSE
          AND first_detected_at BETWEEN '$from_date' AND '$to_date'
        ORDER BY component_name, first_detected_at;
    "
}

sql_release_ai_analyses() {
    local epoch="$1"
    [[ "$epoch" =~ ^[0-9]+$ ]] || return 1
    sql "
        SELECT release_name, failure_category,
               ROUND(confidence_score * 100)::int,
               TO_CHAR(analyzed_at, 'Mon DD')
        FROM ai_analysis
        WHERE release_name LIKE '%${epoch}%'
        ORDER BY analyzed_at DESC;
    "
}

# -- Freeze calendar queries --

sql_active_freeze() {
    sql "
        SELECT reason, TO_CHAR(start_date, 'Mon DD'), TO_CHAR(end_date, 'Mon DD')
        FROM release_freezes
        WHERE CURRENT_DATE BETWEEN start_date AND end_date
        LIMIT 1;
    "
}

sql_upcoming_freezes() {
    local days="${1:-30}"
    sql "
        SELECT TO_CHAR(start_date, 'Mon DD'), TO_CHAR(end_date, 'Mon DD'), reason
        FROM release_freezes
        WHERE start_date > CURRENT_DATE
          AND start_date <= CURRENT_DATE + INTERVAL '${days} days'
        ORDER BY start_date;
    "
}

sql_list_freezes() {
    sql "
        SELECT id, TO_CHAR(start_date, 'YYYY-MM-DD'), TO_CHAR(end_date, 'YYYY-MM-DD'), reason
        FROM release_freezes
        ORDER BY start_date;
    "
}

# -- JIRA linked issues query --

sql_linked_jira_keys() {
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    sql "
        SELECT DISTINCT jira_key FROM (
            SELECT jira_key FROM build_failures
            WHERE application = '$app' AND is_resolved = FALSE AND jira_key IS NOT NULL AND jira_key != ''
            UNION
            SELECT jira_key FROM conforma_results
            WHERE application = '$app' AND is_resolved = FALSE AND jira_key IS NOT NULL AND jira_key != ''
        ) combined;
    "
}

# -- Release schedule queries --

sql_get_release_schedule() {
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    sql "
        SELECT TO_CHAR(planning_freeze, 'YYYY-MM-DD'),
               TO_CHAR(feature_freeze, 'YYYY-MM-DD'),
               TO_CHAR(code_freeze, 'YYYY-MM-DD'),
               TO_CHAR(initial_rc, 'YYYY-MM-DD'),
               TO_CHAR(release_window_start, 'YYYY-MM-DD'),
               TO_CHAR(release_date, 'YYYY-MM-DD'),
               next_release,
               TO_CHAR(updated_at, 'YYYY-MM-DD HH24:MI')
        FROM release_schedule
        WHERE application = '$app';
    "
}

sql_upsert_release_schedule() {
    local app="${1:-$APPLICATION_NAME}"; local app="${app//\'/\'\'}"
    local planning_freeze="$2" feature_freeze="$3" code_freeze="$4"
    local initial_rc="$5" release_window="$6" release_date="$7"
    local next_release="$8" sheet_id="${9:-0}"
    local nr_esc="${next_release//\'/\'\'}"
    sql "
        INSERT INTO release_schedule (application, planning_freeze, feature_freeze,
            code_freeze, initial_rc, release_window_start, release_date,
            next_release, sheet_id, updated_at)
        VALUES ('$app',
                $([ -n "$planning_freeze" ] && echo "'$planning_freeze'" || echo "NULL"),
                $([ -n "$feature_freeze" ] && echo "'$feature_freeze'" || echo "NULL"),
                $([ -n "$code_freeze" ] && echo "'$code_freeze'" || echo "NULL"),
                $([ -n "$initial_rc" ] && echo "'$initial_rc'" || echo "NULL"),
                $([ -n "$release_window" ] && echo "'$release_window'" || echo "NULL"),
                $([ -n "$release_date" ] && echo "'$release_date'" || echo "NULL"),
                $([ -n "$next_release" ] && echo "'$nr_esc'" || echo "NULL"),
                $sheet_id, NOW())
        ON CONFLICT (application) DO UPDATE SET
            planning_freeze = EXCLUDED.planning_freeze,
            feature_freeze = EXCLUDED.feature_freeze,
            code_freeze = EXCLUDED.code_freeze,
            initial_rc = EXCLUDED.initial_rc,
            release_window_start = EXCLUDED.release_window_start,
            release_date = EXCLUDED.release_date,
            next_release = EXCLUDED.next_release,
            sheet_id = EXCLUDED.sheet_id,
            updated_at = NOW();
    "
}
