"""Repository for build_failures table operations."""

import psycopg2
from typing import Optional, List, Set, Dict, Any

from repositories.connection import DatabaseConnection


class BuildFailureRepository:
    """All SQL operations on the build_failures table."""

    def __init__(self, db):
        # type: (DatabaseConnection,) -> None
        self.db = db

    def pipelinerun_exists(self, name):
        # type: (str,) -> bool
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM build_failures WHERE pipelinerun_name = %s LIMIT 1",
                (name,)
            )
            return cursor.fetchone() is not None

    def is_pipelinerun_complete(self, pr_name):
        # type: (str,) -> bool
        """Check if a PipelineRun has complete data (logs, commit, URL)."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT build_logs IS NOT NULL AND LENGTH(build_logs) > 100,
                       commit_sha IS NOT NULL,
                       konflux_url IS NOT NULL
                FROM build_failures
                WHERE pipelinerun_name = %s
                """,
                (pr_name,)
            )
            result = cursor.fetchone()
            return bool(result and all(result))

    def find_unresolved_component_names(self, application):
        # type: (str,) -> Set[str]
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT DISTINCT component_name
                    FROM build_failures
                    WHERE is_resolved = FALSE
                      AND application = %s
                    """,
                    (application,)
                )
                return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def find_failing_component_names(self, application):
        # type: (str,) -> Optional[Set[str]]
        """Get components whose latest build is failed and unresolved."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    WITH latest_builds AS (
                        SELECT DISTINCT ON (component_name)
                            component_name, status, is_resolved
                        FROM build_failures
                        WHERE application = %s
                        ORDER BY component_name, first_detected_at DESC
                    )
                    SELECT component_name FROM latest_builds
                    WHERE status = 'Failed' AND is_resolved = FALSE
                    """,
                    (application,)
                )
                return {row[0] for row in cursor.fetchall()}
        except Exception:
            return None

    def count_unresolved(self, component_name, application):
        # type: (str, str) -> int
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM build_failures
                    WHERE component_name = %s
                      AND is_resolved = FALSE
                      AND application = %s
                    """,
                    (component_name, application)
                )
                return cursor.fetchone()[0]
        except Exception:
            return 0

    def get_last_status(self, component_name, application):
        # type: (str, str) -> Optional[str]
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT status
                    FROM build_failures
                    WHERE component_name = %s
                      AND application = %s
                    ORDER BY first_detected_at DESC
                    LIMIT 1
                    """,
                    (component_name, application)
                )
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def mark_resolved(self, component_name, application, namespace, resolution_pr_name):
        # type: (str, str, str, str) -> bool
        try:
            resolution_url = (
                "https://konflux-ui.apps.CLUSTER_DOMAIN"
                "/ns/{}/pipelinerun/{}".format(namespace, resolution_pr_name)
            )
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE build_failures
                    SET is_resolved = TRUE,
                        resolved_at = NOW(),
                        resolution_type = 'auto-detected',
                        resolution_pr_url = %s,
                        last_updated_at = NOW()
                    WHERE component_name = %s
                      AND is_resolved = FALSE
                      AND application = %s
                    """,
                    (resolution_url, component_name, application)
                )
                return cursor.rowcount > 0
        except Exception:
            return False

    def record_successful_build(self, component_name, pr_name, pr_uid,
                                application, namespace, repo_url, branch):
        # type: (str, str, str, str, str, str, str) -> bool
        """Record a successful build. Returns False if already recorded."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM build_failures WHERE pipelinerun_name = %s",
                    (pr_name,)
                )
                if cursor.fetchone():
                    return False

                repository = repo_url.replace('https://github.com/', '').replace('.git', '')
                cursor.execute(
                    """
                    INSERT INTO build_failures (
                        component_name, pipelinerun_name, pipelinerun_uid,
                        application, namespace, repository, repository_url, branch,
                        status, is_resolved
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (component_name, pr_name, pr_uid, application, namespace,
                     repository, repo_url, branch, 'Succeeded', True)
                )
                return True
        except Exception:
            return False

    def upsert_failure(self, pr_name, pr_uid, component_name, application, namespace,
                       repo_url, branch, status, logs=None, details=None,
                       error_message=None, error_type=None, failed_step=None, duration=None):
        # type: (str, str, str, str, str, str, str, str, Optional[str], Optional[Dict], Optional[str], Optional[str], Optional[str], Optional[int]) -> bool
        """Insert or update a build failure with comprehensive data. Returns True if inserted new."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM build_failures WHERE pipelinerun_name = %s",
                    (pr_name,)
                )
                exists = cursor.fetchone()
                d = details or {}

                if exists:
                    cursor.execute(
                        """
                        UPDATE build_failures SET
                            build_logs = COALESCE(%s, build_logs),
                            commit_sha = COALESCE(%s, commit_sha),
                            commit_short_sha = COALESCE(%s, commit_short_sha),
                            commit_url = COALESCE(%s, commit_url),
                            commit_message = COALESCE(%s, commit_message),
                            commit_author = COALESCE(%s, commit_author),
                            konflux_url = COALESCE(%s, konflux_url),
                            logs_full_url = COALESCE(%s, logs_full_url),
                            pr_number = COALESCE(%s, pr_number),
                            pr_url = COALESCE(%s, pr_url),
                            error_message = COALESCE(%s, error_message),
                            error_type = COALESCE(%s, error_type),
                            failed_step_name = COALESCE(%s, failed_step_name),
                            build_duration_seconds = COALESCE(%s, build_duration_seconds),
                            repository_url = COALESCE(%s, repository_url),
                            branch = COALESCE(%s, branch),
                            last_updated_at = NOW()
                        WHERE pipelinerun_name = %s
                        """,
                        (logs, d.get('commit_sha'), d.get('commit_short_sha'),
                         d.get('commit_url'), d.get('commit_message'),
                         d.get('commit_author'), d.get('konflux_url'),
                         d.get('pipeline_url'), d.get('pr_number'),
                         d.get('pr_url'), error_message, error_type,
                         failed_step, duration, d.get('repository_url'),
                         d.get('branch'), pr_name)
                    )
                    return False
                else:
                    repository = repo_url.replace('https://github.com/', '').replace('.git', '')
                    cursor.execute(
                        """
                        INSERT INTO build_failures (
                            component_name, pipelinerun_name, pipelinerun_uid,
                            application, namespace, repository, repository_url, branch,
                            commit_sha, commit_short_sha, commit_url, commit_message, commit_author,
                            pr_number, pr_url, status, error_message, error_type,
                            failed_step_name, build_duration_seconds,
                            konflux_url, logs_full_url, build_logs
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (component_name, pr_name, pr_uid,
                         application, namespace, repository, repo_url, branch,
                         d.get('commit_sha'), d.get('commit_short_sha'),
                         d.get('commit_url'), d.get('commit_message'),
                         d.get('commit_author'), d.get('pr_number'),
                         d.get('pr_url'), status,
                         error_message, error_type,
                         failed_step, duration, d.get('konflux_url'),
                         d.get('pipeline_url'), logs)
                    )
                    return True
        except Exception:
            raise

    def update_component_health(self, component_name):
        # type: (str,) -> None
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT update_component_health(%s)",
                (component_name,)
            )

