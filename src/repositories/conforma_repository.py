"""Repository for conforma_results table operations."""

import json

from clients.blob_store import (
    get_blob_store,
    make_blob_key,
    resolve_blob_fields,
    should_offload,
)


class ConformaRepository:
    """All SQL operations on the conforma_results table."""

    def __init__(self, db):
        self.db = db

    def find_unresolved_component_names(self, application, include_future=True):
        """Find unresolved components. By default includes both current and future scenarios.

        Args:
            application: Application name to filter by
            include_future: If False, only returns current-policy violations (gate-blocking)
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                if include_future:
                    cursor.execute(
                        """
                        SELECT DISTINCT component_name FROM conforma_results
                        WHERE application = %s AND is_resolved = FALSE
                        """,
                        (application,)
                    )
                else:
                    cursor.execute(
                        """
                        SELECT DISTINCT component_name FROM conforma_results
                        WHERE application = %s AND is_resolved = FALSE AND is_future = FALSE
                        """,
                        (application,)
                    )
                return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def upsert_violation(self, application, component, scenario,
                         pr_name, pr_uid, violations, comp_info, is_future=False):
        """Insert or update a Conforma violation. Returns True on success.

        Args:
            is_future: True if scenario uses future EC policy (informational),
                      False if current policy (gate-blocking)
        """
        try:
            violation_details_json = json.dumps(violations.get('violation_details')) \
                if violations.get('violation_details') else None

            blob_refs = {}
            if violation_details_json and should_offload(violation_details_json):
                key = make_blob_key('conforma', component, pr_name,
                                    'violation_details', 'json')
                get_blob_store().put(key, violation_details_json)
                blob_refs['violation_details'] = key
                violation_details_json = None

            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM conforma_results WHERE pipelinerun_name = %s",
                    (pr_name,)
                )
                exists = cursor.fetchone()

                if exists:
                    cursor.execute(
                        """
                        UPDATE conforma_results SET
                            violations_count = %s,
                            warnings_count = %s,
                            successes_count = %s,
                            violation_summary = %s,
                            violation_details = %s,
                            repository_url = COALESCE(NULLIF(%s, ''), repository_url),
                            commit_url = COALESCE(NULLIF(%s, ''), commit_url),
                            last_updated_at = NOW()
                        WHERE pipelinerun_name = %s
                        """,
                        (violations['violations_count'], violations['warnings_count'],
                         violations['successes_count'], violations['violation_summary'],
                         violation_details_json,
                         comp_info.get('repository_url', ''),
                         comp_info.get('commit_url', ''),
                         pr_name)
                    )
                    if blob_refs:
                        cursor.execute(
                            """UPDATE conforma_results
                               SET violation_details = NULL,
                                   blob_refs = COALESCE(blob_refs, '{}') || %s::jsonb
                               WHERE pipelinerun_name = %s""",
                            (json.dumps(blob_refs), pr_name)
                        )
                    return True
                else:
                    cursor.execute(
                        """
                        SELECT jira_key FROM conforma_results
                        WHERE component_name = %s AND application = %s AND scenario = %s
                          AND is_resolved = FALSE AND jira_key IS NOT NULL
                        LIMIT 1
                        """,
                        (component, application, scenario)
                    )
                    prev = cursor.fetchone()
                    prev_jira_key = prev[0] if prev else None

                    cursor.execute(
                        """
                        UPDATE conforma_results
                        SET is_resolved = TRUE, resolved_at = NOW(), last_updated_at = NOW()
                        WHERE component_name = %s AND application = %s AND scenario = %s
                          AND is_resolved = FALSE
                        """,
                        (component, application, scenario)
                    )

                    cursor.execute(
                        """
                        INSERT INTO conforma_results (
                            application, component_name, scenario,
                            pipelinerun_name, pipelinerun_uid,
                            status, violations_count, warnings_count, successes_count,
                            violation_summary, violation_details,
                            snapshot_name, container_image, repository_url, commit_sha, commit_url,
                            jira_key, is_future, blob_refs
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (application, component, scenario,
                         pr_name, pr_uid, 'Failed',
                         violations['violations_count'], violations['warnings_count'],
                         violations['successes_count'], violations['violation_summary'],
                         violation_details_json,
                         comp_info.get('snapshot_name'), comp_info.get('container_image'),
                         comp_info.get('repository_url'), comp_info.get('commit_sha'),
                         comp_info.get('commit_url'),
                         prev_jira_key, is_future, json.dumps(blob_refs))
                    )
                    return True
        except Exception:
            return False

    def get_violation_summaries(self, application, include_future=None):
        """Summary of all unresolved violations (no violation_details). One query.

        When a component has multiple active scenarios due to Konflux ITS scoping
        (e.g., FBC evaluated against both its correct fbc-rhoai-prod policy and the
        generic registry-rhoai-prod policy), deduplicates by component_name keeping
        only the correct-policy scenario. This prevents false positives from appearing
        alongside real violations.

        Args:
            include_future: None = current only (default), True = future only,
                           'all' = both current and future.
        """
        from conforma.policy_tools import is_wrong_policy_for_artifact
        with self.db.connection() as conn:
            cursor = conn.cursor()
            future_clause = ''
            if include_future is None:
                future_clause = ' AND is_future = FALSE'
            elif include_future is True:
                future_clause = ' AND is_future = TRUE'
            cursor.execute("""
                SELECT DISTINCT ON (component_name, scenario)
                    component_name, scenario,
                    violations_count, warnings_count, successes_count,
                    violation_summary,
                    repository_url, commit_sha,
                    first_detected_at, last_updated_at,
                    ai_analyzed, jira_key, is_future
                FROM conforma_results
                WHERE application = %s AND is_resolved = FALSE{}
                ORDER BY component_name, scenario, last_updated_at DESC
            """.format(future_clause), (application,))
            cols = [
                'component_name', 'scenario',
                'violations_count', 'warnings_count', 'successes_count',
                'violation_summary',
                'repository_url', 'commit_sha',
                'first_detected_at', 'last_updated_at',
                'ai_analyzed', 'jira_key', 'is_future',
            ]
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]

        # Deduplicate: when a component has both a correct-policy and a wrong-policy
        # scenario for the same policy context (same is_future value), keep only the
        # correct one. Key on (component_name, is_future) so that current-policy and
        # future-policy rows for the same component are preserved as separate entries
        # when include_future='all' is used.
        seen: dict = {}
        for row in rows:
            comp = row['component_name']
            is_fut = row.get('is_future', False)
            key = (comp, is_fut)
            wrong = is_wrong_policy_for_artifact(comp, row['scenario'])
            if key not in seen:
                seen[key] = row
            elif wrong:
                # Incoming row is wrong-policy; prefer what is already stored
                pass
            else:
                # Incoming row is correct-policy; replace the stored wrong-policy one
                seen[key] = row
        return list(seen.values())

    def get_wrong_policy_components(self, application):
        """Return component names that have at least one wrong-policy scenario active.

        Scans all unresolved current-policy (is_future=False) (component, scenario)
        pairs in a single query and returns those where the scenario does not match
        the component's artifact type. Used by _check_its_scoping to detect Konflux
        ITS scoping false positives without N+1 queries.
        """
        from conforma.policy_tools import is_wrong_policy_for_artifact
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT component_name, scenario
                    FROM conforma_results
                    WHERE application = %s
                      AND is_resolved = FALSE
                      AND is_future = FALSE
                """, (application,))
                return [
                    comp for comp, scen in cursor.fetchall()
                    if is_wrong_policy_for_artifact(comp, scen)
                ]
        except Exception:
            return []

    def get_violation_details(self, component, application):
        """Full violation details for a component. Used by MCP/API for describe views.

        When a component has multiple active scenarios (e.g., FBC evaluated against
        both fbc-rhoai-prod and registry-rhoai-prod due to Konflux ITS scoping),
        prefers the scenario that matches the component's artifact type. This ensures
        IC shows real policy violations, not false positives from the wrong policy.
        """
        from conforma.policy_tools import is_wrong_policy_for_artifact
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    component_name, scenario,
                    violations_count, warnings_count, successes_count,
                    violation_summary, violation_details,
                    repository_url, commit_sha, commit_url,
                    snapshot_name, pipelinerun_name,
                    first_detected_at, last_updated_at,
                    ai_analyzed, jira_key, blob_refs
                FROM conforma_results
                WHERE component_name = %s
                  AND application = %s
                  AND is_resolved = FALSE
                ORDER BY last_updated_at DESC
            """, (component, application))
            cols = [
                'component_name', 'scenario',
                'violations_count', 'warnings_count', 'successes_count',
                'violation_summary', 'violation_details',
                'repository_url', 'commit_sha', 'commit_url',
                'snapshot_name', 'pipelinerun_name',
                'first_detected_at', 'last_updated_at',
                'ai_analyzed', 'jira_key', 'blob_refs',
            ]
            rows = cursor.fetchall()
            if not rows:
                return None
            # Prefer correct-policy row; fall back to most recent if all are wrong-policy
            preferred = None
            for row in rows:
                result = dict(zip(cols, row))
                if not is_wrong_policy_for_artifact(result['component_name'], result['scenario']):
                    preferred = result
                    break
            if preferred is None:
                preferred = dict(zip(cols, rows[0]))
            return resolve_blob_fields(preferred, fields=('violation_details',))

    def get_evaluated_images(self, application):
        """Return {component_name: container_image} for future-policy evaluations."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT ON (component_name)
                    component_name, container_image
                FROM conforma_results
                WHERE application = %s AND is_future = TRUE
                ORDER BY component_name, last_updated_at DESC
            """, (application,))
            return {row[0]: row[1] for row in cursor.fetchall()}

    def get_latest_future_timestamp(self, application):
        """Return the most recent last_updated_at for future evaluations, or None."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MAX(last_updated_at) FROM conforma_results
                WHERE application = %s AND is_future = TRUE
            """, (application,))
            row = cursor.fetchone()
            return row[0] if row else None

    def resolve_fixed_components(self, application, currently_failing, all_seen):
        """Mark (component, scenario) pairs as resolved when we observe them passing.

        Only resolves pairs that were seen in the current scan AND whose latest
        run passed. Pairs not seen at all (aged out of the KubeArchive window)
        are left untouched — absence is not evidence of fixing.
        """
        try:
            resolved_count = 0
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT DISTINCT component_name, scenario FROM conforma_results
                    WHERE application = %s AND is_resolved = FALSE
                    """,
                    (application,)
                )
                db_pairs = {(row[0], row[1]) for row in cursor.fetchall()}

                resolvable = (db_pairs & all_seen) - currently_failing

                for comp, scenario in resolvable:
                    cursor.execute(
                        """
                        UPDATE conforma_results
                        SET is_resolved = TRUE, resolved_at = NOW(), last_updated_at = NOW()
                        WHERE component_name = %s AND application = %s AND scenario = %s
                          AND is_resolved = FALSE
                        """,
                        (comp, application, scenario)
                    )
                    resolved_count += cursor.rowcount

            return resolved_count
        except Exception:
            return 0

    def resolve_deleted_component(self, component_name, application):
        """Resolve all violations for a component that was deleted from the cluster."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE conforma_results
                    SET is_resolved = TRUE, resolved_at = NOW(), last_updated_at = NOW()
                    WHERE component_name = %s AND application = %s
                      AND is_resolved = FALSE
                    """,
                    (component_name, application)
                )
                return cursor.rowcount
        except Exception:
            return 0
