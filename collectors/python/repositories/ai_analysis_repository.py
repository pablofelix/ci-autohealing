"""Repository for AI analysis operations.

SQL operations on the ai_analysis table. Follows the same patterns as
BuildFailureRepository: parameterized queries, connection context manager,
returns dicts/sets.
"""

import json
from typing import Any, Dict, List, Optional


class AIAnalysisRepository:
    """SQL operations on the ai_analysis table."""

    def __init__(self, db):
        self.db = db

    def insert_analysis(self, model_used, root_cause,
                       failure_category, confidence_score, recommended_fix,
                       recommended_files, can_auto_fix, requires_human_review,
                       tokens_used, cost_usd, analysis_duration,
                       analysis_json, langfuse_trace_id=None,
                       langfuse_trace_url=None, langfuse_observation_id=None,
                       build_failure_id=None, conforma_result_id=None):
        # type: (...) -> int
        """Insert AI analysis for either build failure or Conforma violation.

        Uses a transaction: INSERT into ai_analysis + UPDATE source table.

        Args:
            model_used: Model identifier (e.g., 'claude-sonnet-4-6')
            root_cause: Human-readable root cause description
            failure_category: One of the predefined categories
            confidence_score: 0.0-1.0 confidence in the analysis
            recommended_fix: Suggested fix description
            recommended_files: List of files to modify
            can_auto_fix: Whether this can be auto-fixed
            requires_human_review: Whether human review is required
            tokens_used: Total tokens (input + output)
            cost_usd: Estimated cost in USD
            analysis_duration: Analysis duration in seconds
            analysis_json: Full structured response (dict or list)
            langfuse_trace_id: Optional Langfuse trace ID
            langfuse_trace_url: Optional Langfuse trace URL
            langfuse_observation_id: Optional Langfuse observation ID
            build_failure_id: Foreign key to build_failures (mutually exclusive with conforma_result_id)
            conforma_result_id: Foreign key to conforma_results (mutually exclusive with build_failure_id)

        Returns:
            ID of the inserted ai_analysis record

        Raises:
            ValueError: If both or neither IDs are provided
        """
        # Backward compatibility: if first arg is an int, assume it's build_failure_id
        if isinstance(model_used, int) and build_failure_id is None:
            # Old signature: insert_analysis(build_failure_id, model_used, ...)
            build_failure_id = model_used
            model_used = root_cause  # Shift all args
            root_cause = failure_category
            failure_category = confidence_score
            confidence_score = recommended_fix
            recommended_fix = recommended_files
            recommended_files = can_auto_fix
            can_auto_fix = requires_human_review
            requires_human_review = tokens_used
            tokens_used = cost_usd
            cost_usd = analysis_duration
            analysis_duration = analysis_json
            analysis_json = langfuse_trace_id
            langfuse_trace_id = langfuse_trace_url
            langfuse_trace_url = langfuse_observation_id
            langfuse_observation_id = None

        if sum(x is not None for x in (build_failure_id, conforma_result_id)) != 1:
            raise ValueError("Exactly one of build_failure_id or conforma_result_id must be provided")

        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Insert analysis
            cursor.execute("""
                INSERT INTO ai_analysis (
                    build_failure_id, conforma_result_id,
                    model_used, root_cause, failure_category,
                    confidence_score, recommended_fix, recommended_files,
                    can_auto_fix, requires_human_review,
                    langfuse_trace_id, langfuse_trace_url, langfuse_observation_id,
                    tokens_used, cost_usd, analysis_duration_seconds, analysis_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                build_failure_id, conforma_result_id,
                model_used, root_cause, failure_category,
                confidence_score, recommended_fix, recommended_files,
                can_auto_fix, requires_human_review,
                langfuse_trace_id, langfuse_trace_url, langfuse_observation_id,
                tokens_used, cost_usd, analysis_duration,
                json.dumps(analysis_json) if analysis_json else None
            ))

            analysis_id = cursor.fetchone()[0]

            # Mark source record as analyzed
            if build_failure_id is not None:
                cursor.execute("""
                    UPDATE build_failures
                    SET ai_analyzed = TRUE, ai_analysis_id = %s
                    WHERE id = %s
                """, (analysis_id, build_failure_id))
            else:
                cursor.execute("""
                    UPDATE conforma_results
                    SET ai_analyzed = TRUE, ai_analysis_id = %s
                    WHERE id = %s
                """, (analysis_id, conforma_result_id))

            conn.commit()
            return analysis_id

    def get_analysis_for_failure(self, build_failure_id):
        # type: (int,) -> Optional[Dict[str, Any]]
        """Get existing analysis for a failure (avoid re-analyzing).

        Args:
            build_failure_id: ID in build_failures table

        Returns:
            Dict with analysis data or None if not analyzed
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, model_used, root_cause, failure_category,
                       confidence_score, recommended_fix, recommended_files,
                       can_auto_fix, requires_human_review, analyzed_at,
                       langfuse_trace_id, tokens_used, cost_usd
                FROM ai_analysis
                WHERE build_failure_id = %s
                ORDER BY analyzed_at DESC
                LIMIT 1
            """, (build_failure_id,))

            row = cursor.fetchone()
            if not row:
                return None

            return {
                'id': row[0],
                'model_used': row[1],
                'root_cause': row[2],
                'failure_category': row[3],
                'confidence_score': row[4],
                'recommended_fix': row[5],
                'recommended_files': row[6],
                'can_auto_fix': row[7],
                'requires_human_review': row[8],
                'analyzed_at': row[9],
                'langfuse_trace_id': row[10],
                'tokens_used': row[11],
                'cost_usd': row[12],
            }

    def increment_attempts(self, build_failure_id=None, conforma_result_id=None):
        # type: (Optional[int], Optional[int]) -> int
        """Increment ai_attempts before an LLM call. Returns the new count.

        Called once per analysis attempt so callers can circuit-break after
        MAX_ANALYSIS_RETRIES without a separate query.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()

            if build_failure_id is not None:
                cursor.execute(
                    "UPDATE build_failures SET ai_attempts = ai_attempts + 1 WHERE id = %s"
                    " RETURNING ai_attempts",
                    (build_failure_id,)
                )
            else:
                cursor.execute(
                    "UPDATE conforma_results SET ai_attempts = ai_attempts + 1 WHERE id = %s"
                    " RETURNING ai_attempts",
                    (conforma_result_id,)
                )

            row = cursor.fetchone()
            conn.commit()
            return row[0] if row else 1

    def mark_skipped(self, reason, build_failure_id=None, conforma_result_id=None):
        # type: (str, Optional[int], Optional[int]) -> None
        """Permanently exclude a row from the analysis queue.

        Sets ai_skip_reason and ai_analyzed=TRUE so the pending queries
        stop returning it. reason should be 'no_logs' or 'max_retries'.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()

            if build_failure_id is not None:
                cursor.execute(
                    "UPDATE build_failures SET ai_skip_reason = %s, ai_analyzed = TRUE"
                    " WHERE id = %s",
                    (reason, build_failure_id)
                )
            else:
                cursor.execute(
                    "UPDATE conforma_results SET ai_skip_reason = %s, ai_analyzed = TRUE"
                    " WHERE id = %s",
                    (reason, conforma_result_id)
                )

            conn.commit()

    def skip_no_logs_timeouts(self, application, timeout_days=7):
        # type: (str, int) -> int
        """Mark build failures that never got logs after timeout_days as skipped.

        These rows would otherwise stay in the pending queue forever.
        Returns the number of rows newly marked.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE build_failures
                SET ai_skip_reason = 'no_logs',
                    ai_analyzed    = TRUE
                WHERE application   = %s
                  AND is_resolved   = FALSE
                  AND build_logs    IS NULL
                  AND ai_analyzed   = FALSE
                  AND ai_skip_reason IS NULL
                  AND first_detected_at < NOW() - (%s || ' days')::INTERVAL
            """, (application, timeout_days))
            count = cursor.rowcount
            conn.commit()
            return count

    def get_status_counts(self, application):
        # type: (str,) -> Dict[str, Any]
        """Return all counts needed for ic ai status in a single query per type."""
        with self.db.connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE NOT ai_analyzed AND build_logs IS NOT NULL AND ai_skip_reason IS NULL) AS pending,
                    COUNT(*) FILTER (WHERE NOT ai_analyzed AND build_logs IS NULL  AND ai_skip_reason IS NULL) AS no_logs,
                    COUNT(*) FILTER (WHERE ai_skip_reason IS NOT NULL)                                         AS skipped,
                    COUNT(*) FILTER (WHERE ai_analyzed AND ai_skip_reason IS NULL)                             AS analyzed
                FROM build_failures
                WHERE application = %s AND is_resolved = FALSE
            """, (application,))
            row = cursor.fetchone()
            build = {
                'pending':  row[0],
                'no_logs':  row[1],
                'skipped':  row[2],
                'analyzed': row[3],
            }

            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE NOT ai_analyzed AND violation_summary IS NOT NULL AND ai_skip_reason IS NULL) AS pending,
                    COUNT(*) FILTER (WHERE ai_skip_reason IS NOT NULL)                                                    AS skipped,
                    COUNT(*) FILTER (WHERE ai_analyzed AND ai_skip_reason IS NULL)                                        AS analyzed
                FROM conforma_results
                WHERE application = %s AND is_resolved = FALSE
            """, (application,))
            row = cursor.fetchone()
            conforma = {
                'pending':  row[0],
                'skipped':  row[1],
                'analyzed': row[2],
            }

            # Low-confidence analyzed in last 30 days (both types)
            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE a.build_failure_id IS NOT NULL) AS build_low,
                    COUNT(*) FILTER (WHERE a.conforma_result_id IS NOT NULL) AS conforma_low
                FROM ai_analysis a
                LEFT JOIN build_failures   b ON b.id = a.build_failure_id
                LEFT JOIN conforma_results c ON c.id = a.conforma_result_id
                WHERE (b.application = %s OR c.application = %s)
                  AND a.confidence_score < 0.7
                  AND a.analyzed_at > NOW() - INTERVAL '30 days'
            """, (application, application))
            row = cursor.fetchone()
            build['low_confidence']    = row[0]
            conforma['low_confidence'] = row[1]

            return {'build': build, 'conforma': conforma}

    def get_pending_failures(self, application, limit=5, component_filter=None, force=False):
        # type: (str, int, Optional[str], bool) -> List[Dict[str, Any]]
        """Get failures awaiting AI analysis.

        Criteria: ai_analyzed=FALSE (unless force=True), is_resolved=FALSE,
        build_logs IS NOT NULL, ai_skip_reason IS NULL.

        Args:
            application: Application name (e.g., 'acme-v2-0')
            limit: Maximum number of failures to return
            component_filter: If specified, only get failures for this component
            force: If True, include already-analyzed failures (for re-analysis)

        Returns:
            List of dicts with failure data ready for analysis
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Build WHERE clause dynamically (all conditions use bf. alias)
            where_conditions = [
                "bf.application = %s",
                "bf.is_resolved = FALSE",
                "bf.build_logs IS NOT NULL",
                "bf.ai_skip_reason IS NULL",
            ]
            params = [application]

            if not force:
                where_conditions.append("bf.ai_analyzed = FALSE")

            if component_filter:
                where_conditions.append("bf.component_name = %s")
                params.append(component_filter)

            params.append(limit)

            # LEFT JOIN to previous analysis → pattern → doc_context so the
            # analyzer can include known solutions in its prompt on re-analysis.
            query = """
                SELECT bf.id, bf.component_name, bf.pipelinerun_name, bf.error_message,
                       bf.error_type, bf.failed_task_name, bf.failed_step_name,
                       bf.build_logs, bf.commit_sha, bf.commit_message, bf.commit_author,
                       bf.repository, bf.branch, bf.commit_context, bf.repository_url,
                       ep.typical_fix    AS pattern_typical_fix,
                       ep.doc_context    AS pattern_doc_context,
                       ep.pattern_name   AS pattern_name,
                       ep.id             AS pattern_id
                FROM build_failures bf
                LEFT JOIN LATERAL (
                    SELECT failure_category
                    FROM ai_analysis
                    WHERE build_failure_id = bf.id
                    ORDER BY analyzed_at DESC
                    LIMIT 1
                ) prev ON TRUE
                LEFT JOIN error_patterns ep
                    ON ep.failure_type = 'build'
                    AND ep.failure_category = prev.failure_category
                WHERE {where_clause}
                ORDER BY bf.first_detected_at DESC
                LIMIT %s
            """.format(where_clause=" AND ".join(where_conditions))

            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'component_name': row[1],
                    'pipelinerun_name': row[2],
                    'error_message': row[3],
                    'error_type': row[4],
                    'failed_task_name': row[5],
                    'failed_step_name': row[6],
                    'build_logs': row[7],
                    'commit_sha': row[8],
                    'commit_message': row[9],
                    'commit_author': row[10],
                    'repository': row[11],
                    'branch': row[12],
                    'commit_context': row[13],
                    'repository_url': row[14],
                    'pattern_typical_fix': row[15],
                    'pattern_doc_context': row[16],
                    'pattern_name': row[17],
                    'pattern_id': row[18],
                })

            return results

    def get_pending_count(self, application):
        # type: (str,) -> int
        """Count failures awaiting AI analysis.

        Args:
            application: Application name

        Returns:
            Count of pending failures
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*)
                FROM build_failures
                WHERE application = %s
                  AND ai_analyzed = FALSE
                  AND is_resolved = FALSE
                  AND build_logs IS NOT NULL
                  AND ai_skip_reason IS NULL
            """, (application,))

            return cursor.fetchone()[0]

    def get_pending_conforma_violations(self, application, limit=5, component_filter=None, force=False):
        # type: (str, int, Optional[str], bool) -> List[Dict[str, Any]]
        """Get Conforma violations awaiting AI analysis.

        Criteria: ai_analyzed=FALSE (unless force=True), is_resolved=FALSE,
        violation_summary IS NOT NULL, ai_skip_reason IS NULL.

        Args:
            application: Application name (e.g., 'acme-v2-0')
            limit: Maximum number of violations to return
            component_filter: If specified, only get violations for this component
            force: If True, include already-analyzed violations (for re-analysis)

        Returns:
            List of dicts with violation data ready for analysis
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Build WHERE clause dynamically (all conditions use cr. alias)
            where_conditions = [
                "cr.application = %s",
                "cr.is_resolved = FALSE",
                "cr.violation_summary IS NOT NULL",
                "cr.ai_skip_reason IS NULL",
            ]
            params = [application]

            if not force:
                where_conditions.append("cr.ai_analyzed = FALSE")

            if component_filter:
                where_conditions.append("cr.component_name = %s")
                params.append(component_filter)

            params.append(limit)

            query = """
                SELECT cr.id, cr.component_name, cr.pipelinerun_name, cr.scenario,
                       cr.violations_count, cr.warnings_count, cr.successes_count,
                       cr.violation_summary, cr.violation_details,
                       cr.commit_sha, cr.commit_url,
                       cr.repository_url, cr.snapshot_name,
                       ep.typical_fix    AS pattern_typical_fix,
                       ep.doc_context    AS pattern_doc_context,
                       ep.pattern_name   AS pattern_name,
                       ep.id             AS pattern_id
                FROM conforma_results cr
                LEFT JOIN LATERAL (
                    SELECT failure_category
                    FROM ai_analysis
                    WHERE conforma_result_id = cr.id
                    ORDER BY analyzed_at DESC
                    LIMIT 1
                ) prev ON TRUE
                LEFT JOIN error_patterns ep
                    ON ep.failure_type = 'conforma'
                    AND ep.failure_category = prev.failure_category
                WHERE {where_clause}
                ORDER BY cr.first_detected_at DESC
                LIMIT %s
            """.format(where_clause=" AND ".join(where_conditions))

            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'component_name': row[1],
                    'pipelinerun_name': row[2],
                    'scenario': row[3],
                    'violations_count': row[4],
                    'warnings_count': row[5],
                    'successes_count': row[6],
                    'violation_summary': row[7],
                    'violation_details': row[8],
                    'commit_sha': row[9],
                    'commit_url': row[10],
                    'repository_url': row[11],
                    'snapshot_name': row[12],
                    'repository': row[11],
                    'commit_message': None,
                    'commit_author': None,
                    'pattern_typical_fix': row[13],
                    'pattern_doc_context': row[14],
                    'pattern_name': row[15],
                    'pattern_id': row[16],
                })

            return results

    def get_pending_conforma_count(self, application):
        # type: (str,) -> int
        """Count Conforma violations awaiting AI analysis.

        Args:
            application: Application name

        Returns:
            Count of pending violations
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*)
                FROM conforma_results
                WHERE application = %s
                  AND ai_analyzed = FALSE
                  AND is_resolved = FALSE
                  AND violation_summary IS NOT NULL
                  AND ai_skip_reason IS NULL
            """, (application,))

            return cursor.fetchone()[0]

    def insert_release_analysis(self, release_name, model_used, root_cause,
                                failure_category, confidence_score, recommended_fix,
                                recommended_files, can_auto_fix, requires_human_review,
                                tokens_used, cost_usd, analysis_duration,
                                analysis_json, langfuse_trace_id=None,
                                **kwargs):
        # type: (...) -> int
        """Insert AI analysis for a release failure.

        Unlike build/conforma analyses which reference source table rows,
        release analyses are keyed by release_name (the Release CR name).
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ai_analysis (
                    release_name,
                    model_used, root_cause, failure_category,
                    confidence_score, recommended_fix, recommended_files,
                    can_auto_fix, requires_human_review,
                    langfuse_trace_id,
                    tokens_used, cost_usd, analysis_duration_seconds, analysis_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                release_name,
                model_used, root_cause, failure_category,
                confidence_score, recommended_fix, recommended_files,
                can_auto_fix, requires_human_review,
                langfuse_trace_id,
                tokens_used, cost_usd, analysis_duration,
                json.dumps(analysis_json) if analysis_json else None
            ))
            analysis_id = cursor.fetchone()[0]
            conn.commit()
            return analysis_id

    def get_analysis_for_release(self, release_name):
        # type: (str,) -> Optional[Dict[str, Any]]
        """Get existing analysis for a release (avoid re-analyzing)."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, model_used, root_cause, failure_category,
                       confidence_score, recommended_fix, recommended_files,
                       can_auto_fix, requires_human_review, analyzed_at,
                       langfuse_trace_id, tokens_used, cost_usd
                FROM ai_analysis
                WHERE release_name = %s
                ORDER BY analyzed_at DESC
                LIMIT 1
            """, (release_name,))

            row = cursor.fetchone()
            if not row:
                return None

            return {
                'id': row[0],
                'model_used': row[1],
                'root_cause': row[2],
                'failure_category': row[3],
                'confidence_score': row[4],
                'recommended_fix': row[5],
                'recommended_files': row[6],
                'can_auto_fix': row[7],
                'requires_human_review': row[8],
                'analyzed_at': row[9],
                'langfuse_trace_id': row[10],
                'tokens_used': row[11],
                'cost_usd': row[12],
            }
