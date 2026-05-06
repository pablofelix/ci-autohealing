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

        if (build_failure_id is None) == (conforma_result_id is None):
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

    def get_pending_failures(self, application, limit=5, component_filter=None, force=False):
        # type: (str, int, Optional[str], bool) -> List[Dict[str, Any]]
        """Get failures awaiting AI analysis.

        Criteria: ai_analyzed=FALSE (unless force=True), is_resolved=FALSE, build_logs IS NOT NULL.

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

            # Build WHERE clause dynamically
            where_conditions = ["application = %s", "is_resolved = FALSE", "build_logs IS NOT NULL"]
            params = [application]

            if not force:
                where_conditions.append("ai_analyzed = FALSE")

            if component_filter:
                where_conditions.append("component_name = %s")
                params.append(component_filter)

            params.append(limit)

            query = """
                SELECT id, component_name, pipelinerun_name, error_message,
                       error_type, failed_task_name, failed_step_name,
                       build_logs, commit_sha, commit_message, commit_author,
                       repository, branch, commit_context, repository_url
                FROM build_failures
                WHERE {where_clause}
                ORDER BY first_detected_at DESC
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
            """, (application,))

            return cursor.fetchone()[0]

    def get_pending_conforma_violations(self, application, limit=5, component_filter=None, force=False):
        # type: (str, int, Optional[str], bool) -> List[Dict[str, Any]]
        """Get Conforma violations awaiting AI analysis.

        Criteria: ai_analyzed=FALSE (unless force=True), is_resolved=FALSE, violation_summary IS NOT NULL.

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

            # Build WHERE clause dynamically
            where_conditions = ["application = %s", "is_resolved = FALSE", "violation_summary IS NOT NULL"]
            params = [application]

            if not force:
                where_conditions.append("ai_analyzed = FALSE")

            if component_filter:
                where_conditions.append("component_name = %s")
                params.append(component_filter)

            params.append(limit)

            query = """
                SELECT id, component_name, pipelinerun_name, scenario,
                       violations_count, warnings_count, successes_count,
                       violation_summary, violation_details,
                       commit_sha, commit_url,
                       repository_url, snapshot_name
                FROM conforma_results
                WHERE {where_clause}
                ORDER BY first_detected_at DESC
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
                    # Fields that don't exist in conforma_results
                    'repository': row[11],  # Use repository_url as repository
                    'commit_message': None,
                    'commit_author': None,
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
            """, (application,))

            return cursor.fetchone()[0]
