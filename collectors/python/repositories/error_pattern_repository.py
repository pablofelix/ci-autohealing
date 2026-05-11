"""Repository for error_patterns table.

Tracks known failure patterns across build failures and conforma violations.
Each pattern records its typical solution, a documentation reference, and
occurrence statistics so the analyzers can include institutional memory in
their prompts.

Pattern granularity: one row per (failure_type, failure_category). This is
intentionally coarse for v1 — subtype columns can be added later if needed.
"""

from typing import Any, Dict, List, Optional


class ErrorPatternRepository:
    """SQL operations on the error_patterns table."""

    def __init__(self, db):
        self.db = db

    def find_or_create(self, failure_type, failure_category, pattern_name=None):
        # type: (str, str, Optional[str]) -> Dict[str, Any]
        """Return the pattern for (failure_type, failure_category), creating it if absent.

        When creating, pattern_name defaults to failure_category if not given.
        """
        name = pattern_name or failure_category

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO error_patterns (failure_type, failure_category, pattern_name, created_by)
                VALUES (%s, %s, %s, 'auto')
                ON CONFLICT (failure_type, failure_category) DO NOTHING
            """, (failure_type, failure_category, name))

            cursor.execute("""
                SELECT id, failure_type, failure_category, pattern_name,
                       description, typical_fix, doc_url, doc_context, doc_fetched_at,
                       occurrence_count, avg_confidence, first_seen_at, last_seen_at, created_by
                FROM error_patterns
                WHERE failure_type = %s AND failure_category = %s
            """, (failure_type, failure_category))
            row = cursor.fetchone()
            conn.commit()
            return self._row_to_dict(row)

    def record_occurrence(self, pattern_id, confidence_score):
        # type: (int, float) -> None
        """Increment occurrence_count and update rolling avg_confidence and last_seen_at."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE error_patterns
                SET occurrence_count = occurrence_count + 1,
                    avg_confidence   = CASE
                        WHEN avg_confidence IS NULL THEN %s
                        ELSE (avg_confidence * occurrence_count + %s) / (occurrence_count + 1)
                    END,
                    last_seen_at = NOW()
                WHERE id = %s
            """, (confidence_score, confidence_score, pattern_id))
            conn.commit()

    def update_typical_fix(self, pattern_id, typical_fix):
        # type: (int, str) -> None
        """Overwrite typical_fix for a pattern (manual curation or auto-update)."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE error_patterns SET typical_fix = %s WHERE id = %s
            """, (typical_fix, pattern_id))
            conn.commit()

    def update_doc_context(self, pattern_id, doc_context):
        # type: (int, str) -> None
        """Store a freshly fetched doc excerpt."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE error_patterns
                SET doc_context = %s, doc_fetched_at = NOW()
                WHERE id = %s
            """, (doc_context, pattern_id))
            conn.commit()

    def get_needing_doc_fetch(self, stale_days=7):
        # type: (int,) -> List[Dict[str, Any]]
        """Return patterns that have a doc_url but missing or stale doc_context."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, failure_type, failure_category, pattern_name,
                       description, typical_fix, doc_url, doc_context, doc_fetched_at,
                       occurrence_count, avg_confidence, first_seen_at, last_seen_at, created_by
                FROM error_patterns
                WHERE doc_url IS NOT NULL
                  AND (
                      doc_context IS NULL
                      OR doc_fetched_at < NOW() - INTERVAL '%s days'
                  )
                ORDER BY occurrence_count DESC
            """, (stale_days,))
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_all(self, failure_type=None):
        # type: (Optional[str],) -> List[Dict[str, Any]]
        """Return all patterns, optionally filtered by type. Used by ic patterns list."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if failure_type:
                cursor.execute("""
                    SELECT id, failure_type, failure_category, pattern_name,
                           description, typical_fix, doc_url, doc_context, doc_fetched_at,
                           occurrence_count, avg_confidence, first_seen_at, last_seen_at, created_by
                    FROM error_patterns
                    WHERE failure_type = %s
                    ORDER BY occurrence_count DESC, failure_type, failure_category
                """, (failure_type,))
            else:
                cursor.execute("""
                    SELECT id, failure_type, failure_category, pattern_name,
                           description, typical_fix, doc_url, doc_context, doc_fetched_at,
                           occurrence_count, avg_confidence, first_seen_at, last_seen_at, created_by
                    FROM error_patterns
                    ORDER BY occurrence_count DESC, failure_type, failure_category
                """)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_by_category(self, failure_type, failure_category):
        # type: (str, str) -> Optional[Dict[str, Any]]
        """Look up a single pattern by type + category. Returns None if not found."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, failure_type, failure_category, pattern_name,
                       description, typical_fix, doc_url, doc_context, doc_fetched_at,
                       occurrence_count, avg_confidence, first_seen_at, last_seen_at, created_by
                FROM error_patterns
                WHERE failure_type = %s AND failure_category = %s
            """, (failure_type, failure_category))
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def link_analysis(self, analysis_id, pattern_id):
        # type: (int, int) -> None
        """Set ai_analysis.error_pattern_id after pattern is resolved."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE ai_analysis SET error_pattern_id = %s WHERE id = %s
            """, (pattern_id, analysis_id))
            conn.commit()

    @staticmethod
    def _row_to_dict(row):
        # type: (Any,) -> Dict[str, Any]
        if row is None:
            return {}
        cols = [
            'id', 'failure_type', 'failure_category', 'pattern_name',
            'description', 'typical_fix', 'doc_url', 'doc_context', 'doc_fetched_at',
            'occurrence_count', 'avg_confidence', 'first_seen_at', 'last_seen_at', 'created_by',
        ]
        return dict(zip(cols, row))
