"""Repository for context enrichment operations.

SQL operations for tracking and storing enrichment data in the
build_failures.enriched_context JSONB column.
"""

import json
from typing import Any, Dict, List, Optional

from repositories.connection import DatabaseConnection


class ContextEnrichmentRepository:
    """SQL operations for context enrichment tracking."""

    def __init__(self, db: DatabaseConnection):
        """Initialize repository with database connection.

        Args:
            db: Database connection manager
        """
        self.db = db

    def get_pending_enrichments(self, application: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get failures that need enrichment.

        Criteria:
        - Has commit_sha (so we can fetch context)
        - Does NOT have enriched_context yet
        - Is unresolved
        - Not yet AI analyzed (enrich before analysis)

        Args:
            application: Application name (e.g., 'acme-v2-0')
            limit: Maximum number of failures to return

        Returns:
            List of failure dicts ready for enrichment
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, component_name, pipelinerun_name,
                       commit_sha, repository_url, error_type, error_message,
                       commit_context, application
                FROM build_failures
                WHERE application = %s
                  AND commit_sha IS NOT NULL
                  AND repository_url IS NOT NULL
                  AND enriched_context IS NULL
                  AND ai_analyzed = FALSE
                  AND is_resolved = FALSE
                ORDER BY first_detected_at DESC
                LIMIT %s
            """, (application, limit))

            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'component_name': row[1],
                    'pipelinerun_name': row[2],
                    'commit_sha': row[3],
                    'repository_url': row[4],
                    'error_type': row[5],
                    'error_message': row[6],
                    'commit_context': row[7],
                    'application': row[8],
                })
            return results

    def update_enriched_context(self, failure_id: int, enriched_context: Dict[str, Any]) -> None:
        """Store enriched context for a failure.

        Args:
            failure_id: Database ID of the failure
            enriched_context: Enrichment data dict (will be JSON-serialized)
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE build_failures
                SET enriched_context = %s,
                    last_updated_at = NOW()
                WHERE id = %s
            """, (json.dumps(enriched_context), failure_id))
            conn.commit()

    def increment_enrichment_attempts(self, failure_id: int) -> int:
        """Increment enrichment_attempts counter.

        Used to track retry count and circuit-break after max attempts.

        Args:
            failure_id: Database ID of the failure

        Returns:
            New attempt count
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE build_failures
                SET enrichment_attempts = enrichment_attempts + 1
                WHERE id = %s
                RETURNING enrichment_attempts
            """, (failure_id,))
            row = cursor.fetchone()
            conn.commit()
            return row[0] if row else 1

    def mark_enrichment_failed(self, failure_id: int, error_message: str) -> None:
        """Mark a failure as permanently unenrichable.

        Args:
            failure_id: Database ID of the failure
            error_message: Why enrichment failed
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE build_failures
                SET enrichment_error = %s,
                    last_updated_at = NOW()
                WHERE id = %s
            """, (error_message, failure_id))
            conn.commit()

    def get_enrichment_coverage(self, application: str) -> Dict[str, Any]:
        """Get enrichment coverage statistics.

        Args:
            application: Application name

        Returns:
            Dict with coverage stats:
            - total_with_commit_sha: Failures that could be enriched
            - enriched: Failures with enriched_context
            - pending: Failures awaiting enrichment
            - failed: Failures with enrichment_error
            - coverage_pct: Percentage enriched
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE commit_sha IS NOT NULL AND is_resolved = FALSE) as total,
                    COUNT(*) FILTER (WHERE enriched_context IS NOT NULL) as enriched,
                    COUNT(*) FILTER (WHERE enriched_context IS NULL AND enrichment_error IS NULL) as pending,
                    COUNT(*) FILTER (WHERE enrichment_error IS NOT NULL) as failed
                FROM build_failures
                WHERE application = %s
                  AND commit_sha IS NOT NULL
            """, (application,))

            row = cursor.fetchone()
            total, enriched, pending, failed = row

            coverage_pct = (enriched / total * 100.0) if total > 0 else 0.0

            return {
                'total_with_commit_sha': total,
                'enriched': enriched,
                'pending': pending,
                'failed': failed,
                'coverage_pct': round(coverage_pct, 1),
            }
