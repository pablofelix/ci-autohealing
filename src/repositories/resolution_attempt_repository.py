"""Repository for resolution_attempts table operations.

Records PR fix attempts and their outcomes. Written when fix_generator
creates a PR (--execute), updated when verify_fixes checks merge+build status.

Build failures and conforma violations are tracked in parallel via separate FK
columns (build_failure_id / conforma_result_id). Exactly one is set per row.
"""


from logger import setup_logger

logger = setup_logger(__name__)


class ResolutionAttemptRepository:
    """SQL operations on the resolution_attempts table."""

    def __init__(self, db, pattern_repo=None):
        self.db = db
        self.pattern_repo = pattern_repo

    def record_pr_created(self, build_failure_id, pr_url, pr_number,
                          pr_branch, files_modified, changes_description,
                          notes=None, attempted_by='ic-fix'):
        """Insert a resolution attempt row when a build-failure PR is created.

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
                attempted_by,
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

    def record_conforma_pr_created(self, conforma_result_id, pr_url, pr_number,
                                   pr_branch, files_modified, changes_description,
                                   notes=None, attempted_by='ic-fix'):
        """Insert a resolution attempt row when a conforma PR is created.

        Also marks conforma_results.ai_fix_attempted = TRUE atomically.
        Returns the new resolution_attempts.id.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COALESCE(MAX(attempt_number), 0) + 1
                FROM resolution_attempts
                WHERE conforma_result_id = %s
            """, (conforma_result_id,))
            attempt_number = cursor.fetchone()[0]

            cursor.execute("""
                INSERT INTO resolution_attempts (
                    conforma_result_id,
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
                conforma_result_id,
                attempt_number,
                attempted_by,
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
                UPDATE conforma_results
                SET ai_fix_attempted = TRUE
                WHERE id = %s
            """, (conforma_result_id,))

            conn.commit()
            return attempt_id

    def get_pending_verification(self):
        """Return attempts where PR merge status has not been checked yet.

        Covers both build and conforma attempts. component_name and application
        are resolved from whichever FK is set.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    ra.id,
                    ra.build_failure_id,
                    ra.conforma_result_id,
                    ra.pr_url,
                    ra.pr_number,
                    ra.pr_branch,
                    ra.attempted_at,
                    COALESCE(bf.component_name, cr.component_name) AS component_name,
                    COALESCE(bf.application,    cr.application)    AS application
                FROM resolution_attempts ra
                LEFT JOIN build_failures   bf ON bf.id = ra.build_failure_id
                LEFT JOIN conforma_results cr ON cr.id = ra.conforma_result_id
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
        """Write verification outcome: PR merge status and Konflux build result.

        Marks ai_fix_successful on whichever parent table the attempt belongs to.
        """
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
                # Mark success on whichever parent table owns this attempt
                cursor.execute("""
                    UPDATE build_failures
                    SET ai_fix_successful = TRUE
                    WHERE id = (
                        SELECT build_failure_id FROM resolution_attempts WHERE id = %s
                    )
                      AND (SELECT build_failure_id FROM resolution_attempts WHERE id = %s) IS NOT NULL
                """, (attempt_id, attempt_id))

                cursor.execute("""
                    UPDATE conforma_results
                    SET ai_fix_successful = TRUE
                    WHERE id = (
                        SELECT conforma_result_id FROM resolution_attempts WHERE id = %s
                    )
                      AND (SELECT conforma_result_id FROM resolution_attempts WHERE id = %s) IS NOT NULL
                """, (attempt_id, attempt_id))

            conn.commit()

            # Pattern learning: update confidence based on fix outcome
            if self.pattern_repo and was_successful is not None:
                cursor.execute("""
                    SELECT build_failure_id, conforma_result_id
                    FROM resolution_attempts WHERE id = %s
                """, (attempt_id,))
                ids = cursor.fetchone()
                if ids:
                    pattern_id = self.pattern_repo.get_pattern_for_failure(
                        build_failure_id=ids[0],
                        conforma_result_id=ids[1]
                    )
                    if pattern_id:
                        self.pattern_repo.record_fix_outcome(pattern_id, was_successful)
                        logger.info(
                            "Pattern %d confidence updated (fix %s)",
                            pattern_id, "succeeded" if was_successful else "failed"
                        )

    def get_all(self, days=30):
        """Return attempts from the last N days, newest first. Used by ic get fixes."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    ra.id,
                    COALESCE(bf.component_name, cr.component_name) AS component_name,
                    CASE
                        WHEN ra.build_failure_id IS NOT NULL THEN 'build'
                        ELSE 'conforma'
                    END AS failure_type,
                    ra.status,
                    ra.pr_url,
                    ra.pr_number,
                    ra.was_successful,
                    ra.attempted_at,
                    ra.verified_at,
                    ra.verification_notes
                FROM resolution_attempts ra
                LEFT JOIN build_failures   bf ON bf.id = ra.build_failure_id
                LEFT JOIN conforma_results cr ON cr.id = ra.conforma_result_id
                WHERE ra.attempted_at >= NOW() - make_interval(days => %s)
                ORDER BY ra.attempted_at DESC
            """, (days,))
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_recent_for_application(self, application, days=7, limit=50):
        """Return recent PR activity for one application, newest first."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    ra.id,
                    COALESCE(bf.component_name, cr.component_name) AS component_name,
                    CASE
                        WHEN ra.build_failure_id IS NOT NULL THEN 'build'
                        ELSE 'conforma'
                    END AS failure_type,
                    ra.status,
                    ra.pr_url,
                    ra.pr_number,
                    ra.was_successful,
                    ra.pr_merged,
                    ra.attempted_at,
                    ra.verified_at
                FROM resolution_attempts ra
                LEFT JOIN build_failures   bf ON bf.id = ra.build_failure_id
                LEFT JOIN conforma_results cr ON cr.id = ra.conforma_result_id
                WHERE ra.attempted_at >= NOW() - make_interval(days => %s)
                  AND ra.pr_url IS NOT NULL
                  AND COALESCE(bf.application, cr.application) = %s
                ORDER BY ra.attempted_at DESC
                LIMIT %s
            """, (days, application, limit))
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_outcome_summary(self, days=30):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (WHERE was_successful = TRUE),
                    COUNT(*) FILTER (WHERE was_successful = FALSE),
                    COUNT(*) FILTER (WHERE was_successful IS NULL AND status = 'pr_created')
                FROM resolution_attempts
                WHERE attempted_at >= NOW() - make_interval(days => %s)
            """, (days,))
            row = cursor.fetchone()
            total, success, failed, pending = row
            resolved = success + failed
            return {
                'total': total, 'successful': success, 'failed': failed,
                'pending': pending, 'rate': success * 100 // max(resolved, 1),
            }
