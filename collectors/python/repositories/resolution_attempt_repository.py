"""Repository for resolution_attempts table operations.

Records PR fix attempts and their outcomes. Written when fix_generator
creates a PR (--execute), updated when verify_fixes checks merge+build status.
"""

from typing import Any, Dict, List, Optional


class ResolutionAttemptRepository:
    """SQL operations on the resolution_attempts table."""

    def __init__(self, db):
        self.db = db

    def record_pr_created(self, build_failure_id, pr_url, pr_number,
                          pr_branch, files_modified, changes_description,
                          notes=None):
        # type: (int, str, int, str, List[str], str, Optional[str]) -> int
        """Insert a resolution attempt row when a PR is created.

        Also marks build_failures.ai_fix_attempted = TRUE atomically.
        Returns the new resolution_attempts.id.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COALESCE(MAX(attempt_number), 0) + 1
                FROM resolution_attempts
                WHERE build_failure_id = %s
            """, (build_failure_id,))
            attempt_number = cursor.fetchone()[0]

            cursor.execute("""
                INSERT INTO resolution_attempts (
                    build_failure_id,
                    attempt_number,
                    attempted_by,
                    resolution_strategy,
                    changes_description,
                    pr_created,
                    pr_number,
                    pr_url,
                    pr_branch,
                    files_modified,
                    status,
                    notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                build_failure_id,
                attempt_number,
                'ic-fix',
                'code_fix',
                changes_description,
                True,
                pr_number,
                pr_url,
                pr_branch,
                files_modified,
                'pr_created',
                notes,
            ))
            attempt_id = cursor.fetchone()[0]

            cursor.execute("""
                UPDATE build_failures
                SET ai_fix_attempted = TRUE
                WHERE id = %s
            """, (build_failure_id,))

            conn.commit()
            return attempt_id

    def get_pending_verification(self):
        # type: () -> List[Dict[str, Any]]
        """Return attempts where PR merge status has not been checked yet.

        These are rows with pr_url set, pr_merged IS NULL, and status='pr_created'.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    ra.id,
                    ra.build_failure_id,
                    ra.pr_url,
                    ra.pr_number,
                    ra.pr_branch,
                    ra.attempted_at,
                    bf.component_name,
                    bf.application
                FROM resolution_attempts ra
                JOIN build_failures bf ON bf.id = ra.build_failure_id
                WHERE ra.pr_url IS NOT NULL
                  AND ra.pr_merged IS NULL
                  AND ra.status = 'pr_created'
                ORDER BY ra.attempted_at ASC
            """)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def update_verification(self, attempt_id, pr_merged, pr_merged_at,
                            result_pipelinerun_name, result_build_status,
                            was_successful, verification_notes):
        # type: (int, bool, Any, Optional[str], Optional[str], Optional[bool], str) -> None
        """Write verification outcome: PR merge status and Konflux build result."""
        if was_successful:
            new_status = 'success'
        elif pr_merged and not was_successful:
            new_status = 'failed'
        else:
            new_status = 'abandoned'

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE resolution_attempts
                SET pr_merged              = %s,
                    pr_merged_at           = %s,
                    result_pipelinerun_name = %s,
                    result_build_status    = %s,
                    was_successful         = %s,
                    verified_at            = NOW(),
                    verification_notes     = %s,
                    status                 = %s
                WHERE id = %s
            """, (
                pr_merged,
                pr_merged_at,
                result_pipelinerun_name,
                result_build_status,
                was_successful,
                verification_notes,
                new_status,
                attempt_id,
            ))

            if was_successful:
                cursor.execute("""
                    UPDATE build_failures
                    SET ai_fix_successful = TRUE
                    WHERE id = (
                        SELECT build_failure_id FROM resolution_attempts WHERE id = %s
                    )
                """, (attempt_id,))

            conn.commit()

    def get_all(self, days=30):
        # type: (int) -> List[Dict[str, Any]]
        """Return attempts from the last N days, newest first. Used by ic get fixes."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    ra.id,
                    bf.component_name,
                    ra.status,
                    ra.pr_url,
                    ra.pr_number,
                    ra.was_successful,
                    ra.attempted_at,
                    ra.verified_at,
                    ra.verification_notes
                FROM resolution_attempts ra
                JOIN build_failures bf ON bf.id = ra.build_failure_id
                WHERE ra.attempted_at >= NOW() - INTERVAL '%s days'
                ORDER BY ra.attempted_at DESC
            """, (days,))
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
