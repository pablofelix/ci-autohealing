"""Repository for conforma_results table operations."""

import json
from typing import Optional, Set, Dict, Any, Tuple

from repositories.connection import DatabaseConnection


class ConformaRepository:
    """All SQL operations on the conforma_results table."""

    def __init__(self, db):
        # type: (DatabaseConnection,) -> None
        self.db = db

    def find_unresolved_component_names(self, application):
        # type: (str,) -> Set[str]
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT DISTINCT component_name FROM conforma_results
                    WHERE application = %s AND is_resolved = FALSE
                    """,
                    (application,)
                )
                return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def upsert_violation(self, application, component, scenario,
                         pr_name, pr_uid, violations, comp_info):
        # type: (str, str, str, str, str, Dict[str, Any], Dict[str, Any]) -> bool
        """Insert or update a Conforma violation. Returns True on success."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM conforma_results WHERE pipelinerun_name = %s",
                    (pr_name,)
                )
                exists = cursor.fetchone()

                violation_details_json = json.dumps(violations.get('violation_details')) \
                    if violations.get('violation_details') else None

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
                            jira_key
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (application, component, scenario,
                         pr_name, pr_uid, 'Failed',
                         violations['violations_count'], violations['warnings_count'],
                         violations['successes_count'], violations['violation_summary'],
                         violation_details_json,
                         comp_info.get('snapshot_name'), comp_info.get('container_image'),
                         comp_info.get('repository_url'), comp_info.get('commit_sha'),
                         comp_info.get('commit_url'),
                         prev_jira_key)
                    )
                    return True
        except Exception:
            return False

    def resolve_fixed_components(self, application, currently_failing, all_seen):
        # type: (str, Set[Tuple[str, str]], Set[Tuple[str, str]]) -> int
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
