"""Related failures source - finds similar failures from the same component.

Queries the database for recent failures with:
- Same component name
- Same error type
- Recent (last 7 days)
- Includes AI analysis if available
"""

from typing import Any, Dict, List, Optional

from config import CollectorConfig
from enrichment.context_source import ContextSource
from repositories.connection import DatabaseConnection
from logger import setup_logger

logger = setup_logger(__name__)

# Truncation limits for context size management
MAX_ERROR_MESSAGE_LENGTH = 200  # Preview for listings
MAX_ROOT_CAUSE_LENGTH = 300     # Enough for key insight


class RelatedFailuresSource(ContextSource):
    """Finds related failures from database to provide context."""

    def __init__(self, config: CollectorConfig, db: Optional[DatabaseConnection] = None):
        """Initialize related failures source.

        Args:
            config: Collector configuration
            db: Database connection (created if None)
        """
        super().__init__(config)
        self.db = db or DatabaseConnection(config.db)

    def fetch(self, failure: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find related failures for context.

        Args:
            failure: Failure dict with component_name, error_type, id

        Returns:
            Dict with structure:
            {
                'related_failures': [
                    {
                        'id': 123,
                        'component_name': '...',
                        'error_type': '...',
                        'error_message': '...',
                        'similarity_score': 0.85,
                        'first_detected_at': '...',
                        'ai_analyzed': True,
                        'root_cause': '...',
                        'confidence_score': 0.92
                    },
                    ...
                ]
            }
            Or None if no related failures found.
        """
        try:
            component_name = failure.get('component_name')
            error_type = failure.get('error_type')
            failure_id = failure.get('id')
            application = failure.get('application', self.config.k8s.application_name)

            if not component_name or not failure_id:
                return None

            related = self._query_related_failures(
                component_name=component_name,
                error_type=error_type,
                failure_id=failure_id,
                application=application,
                limit=3
            )

            if not related:
                return None

            logger.info("Found %d related failures", len(related))

            return {
                'related_failures': related
            }

        except Exception as e:
            logger.error("RelatedFailuresSource failed: %s", e)
            return None

    def _query_related_failures(
        self,
        component_name: str,
        error_type: Optional[str],
        failure_id: int,
        application: str,
        limit: int = 3
    ) -> List[Dict[str, Any]]:
        """Query database for similar failures.

        Ranking strategy:
        1. Same component + same error type (highest priority)
        2. Same component + different error type
        3. Recent failures prioritized

        Args:
            component_name: Component to match
            error_type: Error type to match (optional)
            failure_id: Current failure ID (exclude from results)
            application: Application name
            limit: Max results to return

        Returns:
            List of related failure dicts
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Query with ranking: exact match on error_type ranks higher
            cursor.execute("""
                SELECT
                    bf.id,
                    bf.component_name,
                    bf.error_type,
                    bf.error_message,
                    bf.pipelinerun_name,
                    bf.first_detected_at,
                    bf.ai_analyzed,
                    aa.root_cause,
                    aa.confidence_score,
                    aa.failure_category,
                    CASE
                        WHEN bf.error_type = %s THEN 1.0
                        WHEN bf.error_type IS NOT NULL THEN 0.7
                        ELSE 0.5
                    END as similarity_score
                FROM build_failures bf
                LEFT JOIN ai_analysis aa
                    ON bf.ai_analysis_id = aa.id
                WHERE bf.application = %s
                  AND bf.component_name = %s
                  AND bf.id != %s
                  AND bf.is_resolved = FALSE
                  AND bf.first_detected_at > NOW() - INTERVAL '7 days'
                ORDER BY similarity_score DESC, bf.first_detected_at DESC
                LIMIT %s
            """, (error_type, application, component_name, failure_id, limit))

            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'component_name': row[1],
                    'error_type': row[2],
                    'error_message': row[3][:MAX_ERROR_MESSAGE_LENGTH] if row[3] else '',
                    'pipelinerun_name': row[4],
                    'first_detected_at': row[5].isoformat() if row[5] else None,
                    'ai_analyzed': row[6],
                    'root_cause': row[7][:MAX_ROOT_CAUSE_LENGTH] if row[7] else None,
                    'confidence_score': float(row[8]) if row[8] else None,
                    'failure_category': row[9],
                    'similarity_score': float(row[10]),
                })

            return results

    def source_name(self) -> str:
        return 'related_failures'

    @property
    def requires_external_api(self) -> bool:
        return False

    @property
    def timeout_seconds(self) -> int:
        return 10
