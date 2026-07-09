"""Repository for build_failures table operations."""

import json

from clients.blob_store import (
    get_blob_store,
    make_blob_key,
    resolve_blob_fields,
    should_offload,
)


class BuildFailureRepository:
    """All SQL operations on the build_failures table."""

    def __init__(self, db):
        self.db = db

    def pipelinerun_exists(self, name):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM build_failures WHERE pipelinerun_name = %s LIMIT 1",
                (name,)
            )
            return cursor.fetchone() is not None

    def is_pipelinerun_complete(self, pr_name):
        """Check if a PipelineRun has complete data (logs, commit, URL)."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT (build_logs IS NOT NULL AND LENGTH(build_logs) > 100)
                       OR (blob_refs ? 'build_logs'),
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

    def mark_resolved(self, component_name, application, namespace, resolution_pr_name,
                       resolution_commit_sha=None):
        try:
            from shared_config import KONFLUX_UI_BASE
            resolution_url = (
                "{}/ns/{}/pipelinerun/{}".format(KONFLUX_UI_BASE, namespace, resolution_pr_name)
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
                        resolution_commit_sha = %s,
                        last_updated_at = NOW()
                    WHERE component_name = %s
                      AND is_resolved = FALSE
                      AND application = %s
                    """,
                    (resolution_url, resolution_commit_sha, component_name, application)
                )
                return cursor.rowcount > 0
        except Exception:
            return False

    def mark_resolved_deleted(self, component_name, application):
        """Mark all unresolved failures as resolved because the component was deleted from the cluster."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE build_failures
                    SET is_resolved = TRUE,
                        resolved_at = NOW(),
                        resolution_type = 'component-deleted',
                        resolution_notes = 'Component CR no longer exists in cluster',
                        last_updated_at = NOW()
                    WHERE component_name = %s
                      AND is_resolved = FALSE
                      AND application = %s
                    """,
                    (component_name, application)
                )
                return cursor.rowcount > 0
        except Exception:
            return False

    def record_successful_build(self, component_name, pr_name, pr_uid,
                                application, namespace, repo_url, branch):
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
        """Insert or update a build failure with comprehensive data. Returns True if inserted new."""
        try:
            from security.redact import sanitize_for_storage
            if logs:
                logs = sanitize_for_storage(logs)
            if error_message:
                error_message = sanitize_for_storage(error_message)

            blob_refs = {}
            if logs and should_offload(logs):
                key = make_blob_key('build-failures', component_name, pr_name, 'build_logs')
                get_blob_store().put(key, logs)
                blob_refs['build_logs'] = key
                logs = None

            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM build_failures WHERE pipelinerun_name = %s",
                    (pr_name,)
                )
                exists = cursor.fetchone()
                d = details or {}

                if exists:
                    unresolve_clause = ""
                    if status == 'Failed':
                        unresolve_clause = "is_resolved = FALSE, resolved_at = NULL, resolution_type = NULL, resolution_pr_url = NULL,"
                    cursor.execute(
                        """
                        UPDATE build_failures SET
                            {unresolve}
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
                            output_image = COALESCE(%s, output_image),
                            image_digest = COALESCE(%s, image_digest),
                            task_summary = COALESCE(%s, task_summary),
                            chains_git_url = COALESCE(%s, chains_git_url),
                            chains_git_commit = COALESCE(%s, chains_git_commit),
                            trigger_type = COALESCE(%s, trigger_type),
                            last_updated_at = NOW()
                        WHERE pipelinerun_name = %s
                        """.format(unresolve=unresolve_clause),
                        (logs, d.get('commit_sha'), d.get('commit_short_sha'),
                         d.get('commit_url'), d.get('commit_message'),
                         d.get('commit_author'), d.get('konflux_url'),
                         d.get('pipeline_url'), d.get('pr_number'),
                         d.get('pr_url'), error_message, error_type,
                         failed_step, duration, d.get('repository_url'),
                         d.get('branch'),
                         d.get('output_image'), d.get('image_digest'),
                         d.get('task_summary'),
                         d.get('chains_git_url'), d.get('chains_git_commit'),
                         d.get('trigger_type'),
                         pr_name)
                    )
                    if blob_refs:
                        cursor.execute(
                            """UPDATE build_failures
                               SET build_logs = NULL,
                                   blob_refs = COALESCE(blob_refs, '{}') || %s::jsonb
                               WHERE pipelinerun_name = %s""",
                            (json.dumps(blob_refs), pr_name)
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
                            konflux_url, logs_full_url, build_logs,
                            output_image, image_digest, task_summary,
                            chains_git_url, chains_git_commit, blob_refs,
                            trigger_type
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
                         d.get('pipeline_url'), logs,
                         d.get('output_image'), d.get('image_digest'),
                         d.get('task_summary'),
                         d.get('chains_git_url'), d.get('chains_git_commit'),
                         json.dumps(blob_refs), d.get('trigger_type', 'push'))
                    )
                    return True
        except Exception:
            raise

    def update_component_health(self, component_name):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT update_component_health(%s)",
                (component_name,)
            )

    def get_component_summary(self, name):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE build_logs IS NOT NULL OR blob_refs ? 'build_logs'),
                       MIN(first_detected_at),
                       MAX(first_detected_at)
                FROM build_failures WHERE component_name = %s
            """, (name,))
            row = cursor.fetchone()
            if not row or row[0] == 0:
                return None
            return {
                'total': row[0], 'with_logs': row[1],
                'first_seen': row[2], 'last_seen': row[3],
            }

    def get_applications(self):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT application, COUNT(*) as cnt
                FROM build_failures
                WHERE application IS NOT NULL
                GROUP BY application ORDER BY application
            """)
            return [{'application': r[0], 'count': r[1]} for r in cursor.fetchall()]

    def get_overview_stats(self, application=None):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if application:
                cursor.execute("""
                    SELECT COUNT(*),
                           COUNT(DISTINCT component_name),
                           COUNT(*) FILTER (WHERE build_logs IS NOT NULL OR blob_refs ? 'build_logs')
                    FROM build_failures WHERE application = %s
                """, (application,))
            else:
                cursor.execute("""
                    SELECT COUNT(*),
                           COUNT(DISTINCT component_name),
                           COUNT(*) FILTER (WHERE build_logs IS NOT NULL OR blob_refs ? 'build_logs')
                    FROM build_failures
                """)
            row = cursor.fetchone()
            return {'total': row[0], 'components': row[1], 'with_logs': row[2]}

    def get_daily_stats(self, application, days=7):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DATE(first_detected_at), COUNT(*)
                FROM build_failures
                WHERE application = %s
                GROUP BY DATE(first_detected_at)
                ORDER BY DATE(first_detected_at) DESC
                LIMIT %s
            """, (application, days))
            return [{'date': r[0], 'count': r[1]} for r in cursor.fetchall()]

    def get_resolved_stats(self, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE is_resolved = TRUE),
                       COUNT(*) FILTER (WHERE is_resolved = FALSE),
                       COUNT(*) FILTER (WHERE status = 'Succeeded')
                FROM build_failures WHERE application = %s
            """, (application,))
            row = cursor.fetchone()
            return {
                'total': row[0], 'resolved': row[1],
                'unresolved': row[2], 'successes': row[3],
            }

    def get_triage_summary(self, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                WITH latest_builds AS (
                    SELECT DISTINCT ON (component_name)
                        component_name, pipelinerun_name, status,
                        is_resolved, first_detected_at,
                        (build_logs IS NOT NULL OR blob_refs ? 'build_logs') as has_logs
                    FROM build_failures WHERE application = %s
                    ORDER BY component_name, first_detected_at DESC
                )
                SELECT
                    (SELECT COUNT(*) FROM build_failures WHERE application = %s) as total,
                    COUNT(*) FILTER (WHERE status = 'Failed' AND is_resolved = FALSE) as failing,
                    COUNT(*) FILTER (WHERE status = 'Succeeded') as working
                FROM latest_builds
            """, (application, application))
            row = cursor.fetchone()
            summary = {'total': row[0], 'failing': row[1], 'working': row[2]}

            cursor.execute("""
                WITH latest_builds AS (
                    SELECT DISTINCT ON (component_name)
                        component_name, first_detected_at, last_updated_at,
                        error_type, jira_key, trigger_type, pipelinerun_name,
                        COALESCE(konflux_url, '') AS konflux_url,
                        COUNT(*) OVER (PARTITION BY component_name) as failure_count,
                        (build_logs IS NOT NULL OR blob_refs ? 'build_logs') as has_logs,
                        (commit_context IS NOT NULL OR blob_refs ? 'commit_context') as has_context,
                        (ai_analysis_id IS NOT NULL) as ai_analyzed
                    FROM build_failures
                    WHERE application = %s AND status = 'Failed' AND is_resolved = FALSE
                    ORDER BY component_name, first_detected_at DESC
                )
                SELECT component_name, first_detected_at, last_updated_at,
                       error_type, jira_key, failure_count, has_logs, has_context,
                       ai_analyzed, trigger_type, pipelinerun_name, konflux_url
                FROM latest_builds ORDER BY first_detected_at DESC
            """, (application,))
            summary['failing_components'] = [
                {
                    'component': r[0],
                    'first_detected_at': r[1],
                    'last_updated_at': r[2],
                    'error_type': r[3],
                    'jira_key': r[4],
                    'failure_count': r[5],
                    'has_logs': r[6],
                    'has_context': r[7],
                    'ai_analyzed': r[8],
                    'trigger_type': r[9] or 'push',
                    'is_nightly': (r[9] or 'push') == 'nightly',
                    'pipelinerun_name': r[10] or '',
                    'konflux_url': r[11] or '',
                }
                for r in cursor.fetchall()
            ]
            return summary

    def get_resolved_components(self, application, days=None, since=None, to=None):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            date_filter = ""
            params = [application]
            if since is not None:
                date_filter = " AND resolved_at >= %s"
                params.append(since)
            elif days is not None:
                date_filter = " AND resolved_at >= NOW() - INTERVAL '1 day' * %s"
                params.append(days)
            if to is not None:
                date_filter += " AND resolved_at < %s::date + INTERVAL '1 day'"
                params.append(to)
            cursor.execute("""
                SELECT component_name, MAX(resolved_at), COUNT(*),
                       MAX(resolution_pr_url), MAX(resolution_commit_sha)
                FROM build_failures
                WHERE is_resolved = TRUE AND application = %s{}
                GROUP BY component_name
                ORDER BY MAX(resolved_at) DESC
            """.format(date_filter), params)
            return [
                {
                    'component': r[0], 'resolved_at': r[1], 'count': r[2],
                    'resolution_url': r[3], 'commit_sha': r[4],
                }
                for r in cursor.fetchall()
            ]

    def get_working_components(self, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                WITH latest_builds AS (
                    SELECT DISTINCT ON (component_name)
                        component_name, commit_short_sha, commit_message,
                        first_detected_at, status
                    FROM build_failures WHERE application = %s
                    ORDER BY component_name, first_detected_at DESC
                )
                SELECT component_name, LEFT(commit_short_sha, 8),
                       LEFT(commit_message, 50), first_detected_at::date
                FROM latest_builds WHERE status = 'Succeeded'
                ORDER BY first_detected_at DESC
            """, (application,))
            return [
                {'component': r[0], 'commit': r[1], 'message': r[2], 'date': r[3]}
                for r in cursor.fetchall()
            ]

    def find_by_image(self, image_ref):
        """Find build records matching an image URL or digest.

        Accepts full quay.io URL, sha256 digest, or partial digest.
        """
        term = image_ref.strip()
        if len(term) < 6:
            return []
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT component_name, application, pipelinerun_name,
                       status, output_image, image_digest,
                       first_detected_at, is_resolved,
                       repository_url, branch, commit_sha, commit_author
                FROM build_failures
                WHERE output_image ILIKE %s
                   OR image_digest ILIKE %s
                ORDER BY first_detected_at DESC
                LIMIT 10
            """, ('%{}%'.format(term), '%{}%'.format(term)))
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_component_history(self, component, application, limit=20):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE status = 'Failed'),
                       COUNT(*) FILTER (WHERE status = 'Succeeded'),
                       COUNT(*) FILTER (WHERE is_resolved = TRUE)
                FROM build_failures
                WHERE component_name = %s AND application = %s
            """, (component, application))
            row = cursor.fetchone()
            summary = {
                'total': row[0], 'failures': row[1],
                'successes': row[2], 'resolved': row[3],
            }

            cursor.execute("""
                SELECT pipelinerun_name, status,
                       CASE WHEN is_resolved THEN TRUE ELSE FALSE END,
                       LEFT(commit_short_sha, 8), first_detected_at
                FROM build_failures
                WHERE component_name = %s AND application = %s
                ORDER BY first_detected_at DESC LIMIT %s
            """, (component, application, limit))
            builds = [
                {'pipelinerun': r[0], 'status': r[1], 'resolved': r[2],
                 'commit': r[3], 'date': r[4]}
                for r in cursor.fetchall()
            ]

            cursor.execute("""
                SELECT status FROM build_failures
                WHERE component_name = %s AND application = %s
                ORDER BY first_detected_at DESC LIMIT 1
            """, (component, application))
            row = cursor.fetchone()
            last_status = row[0] if row else None

            return {'summary': summary, 'builds': builds, 'last_status': last_status}

    def get_enrichment_coverage(self, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE commit_sha IS NOT NULL AND is_resolved = FALSE),
                    COUNT(*) FILTER (WHERE commit_sha IS NOT NULL AND enriched_context IS NOT NULL),
                    COUNT(*) FILTER (WHERE commit_sha IS NOT NULL AND enrichment_error IS NOT NULL)
                FROM build_failures WHERE application = %s
            """, (application,))
            row = cursor.fetchone()
            total = row[0]
            enriched = row[1]
            return {
                'total': total, 'enriched': enriched, 'failed': row[2],
                'pct': enriched * 100 // max(total, 1),
            }

    def get_failure_details(self, component, application):
        """Full failure details for a component. Used by MCP/API for describe views."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    id,
                    component_name, pipelinerun_name, error_message, error_type,
                    failed_task_name, failed_step_name,
                    build_logs,
                    commit_sha, commit_message, commit_author, commit_url,
                    repository_url, branch, commit_context,
                    konflux_url, first_detected_at, last_updated_at,
                    status, ai_analyzed, jira_key, blob_refs
                FROM build_failures
                WHERE component_name = %s
                  AND application = %s
                  AND is_resolved = FALSE
                ORDER BY last_updated_at DESC
                LIMIT 1
            """, (component, application))
            row = cursor.fetchone()
            if not row:
                return None
            cols = [
                'id',
                'component_name', 'pipelinerun_name', 'error_message', 'error_type',
                'failed_task_name', 'failed_step_name', 'build_logs',
                'commit_sha', 'commit_message', 'commit_author', 'commit_url',
                'repository_url', 'branch', 'commit_context',
                'konflux_url', 'first_detected_at', 'last_updated_at',
                'status', 'ai_analyzed', 'jira_key', 'blob_refs',
            ]
            result = dict(zip(cols, row))
            return resolve_blob_fields(result)

    def get_analysis_queue(self, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE ai_analyzed = FALSE AND is_resolved = FALSE AND ai_skip_reason IS NULL),
                    COUNT(*) FILTER (WHERE ai_analyzed = TRUE)
                FROM build_failures WHERE application = %s
            """, (application,))
            row = cursor.fetchone()
            return {'pending': row[0], 'analyzed': row[1]}

    def get_failing_nightly_components(self, application):
        """Return the set of components whose most recent nightly build is failing.

        Uses DISTINCT ON to pick the latest nightly build per component and
        returns only those still unresolved. Called once per display function —
        callers must not loop and call this per-component.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT ON (component_name)
                    component_name
                FROM build_failures
                WHERE application = %s
                  AND trigger_type = 'nightly'
                  AND is_resolved = FALSE
                ORDER BY component_name, first_detected_at DESC
            """, (application,))
            return {row[0] for row in cursor.fetchall()}

    def get_nightly_history(self, application, days=14):
        """Get nightly operator build history with FBC fragment status."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    build_completion_time::date AS build_date,
                    pipelinerun_name,
                    component_name,
                    status,
                    build_completion_time,
                    commit_sha
                FROM build_failures
                WHERE application = %s
                  AND trigger_type = 'nightly'
                  AND build_completion_time > NOW() - (%s || ' days')::INTERVAL
                ORDER BY build_completion_time DESC
            """, (application, str(days)))
            cols = [d[0] for d in cursor.description]
            nightly_builds = [dict(zip(cols, row)) for row in cursor.fetchall()]

            cursor.execute("""
                SELECT component_name, last_successful_build, current_status
                FROM component_health
                WHERE component_name LIKE '%%fbc-fragment%%'
                  AND application = %s
            """, (application,))
            cols2 = [d[0] for d in cursor.description]
            fbc_rows = [dict(zip(cols2, row)) for row in cursor.fetchall()]

            return {
                'application': application,
                'nightly_builds': nightly_builds,
                'fbc_fragments': fbc_rows,
                'days': days,
            }

