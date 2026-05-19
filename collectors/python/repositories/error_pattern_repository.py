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
                      OR doc_fetched_at < NOW() - (%s || ' days')::INTERVAL
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

    def record_fix_outcome(self, pattern_id, was_successful):
        # type: (int, bool) -> None
        """Update pattern confidence based on whether the recommended fix worked.

        Uses exponential moving average with alpha=0.2 to weight recent
        outcomes more heavily than historical ones.
        """
        LEARNING_RATE = 0.2
        outcome_score = 1.0 if was_successful else 0.0

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE error_patterns
                SET avg_confidence = CASE
                        WHEN avg_confidence IS NULL THEN %s
                        ELSE avg_confidence * (1.0 - %s) + %s * %s
                    END,
                    match_count = match_count + 1,
                    last_used_at = NOW()
                WHERE id = %s
            """, (outcome_score, LEARNING_RATE, LEARNING_RATE, outcome_score, pattern_id))
            conn.commit()

    def get_pattern_for_failure(self, build_failure_id=None, conforma_result_id=None):
        # type: (Optional[int], Optional[int]) -> Optional[int]
        """Look up which pattern was used in the AI analysis for a failure."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if build_failure_id is not None:
                cursor.execute("""
                    SELECT error_pattern_id FROM ai_analysis
                    WHERE build_failure_id = %s AND error_pattern_id IS NOT NULL
                    ORDER BY created_at DESC LIMIT 1
                """, (build_failure_id,))
            elif conforma_result_id is not None:
                cursor.execute("""
                    SELECT error_pattern_id FROM ai_analysis
                    WHERE conforma_result_id = %s AND error_pattern_id IS NOT NULL
                    ORDER BY created_at DESC LIMIT 1
                """, (conforma_result_id,))
            else:
                return None
            row = cursor.fetchone()
            return row[0] if row else None

    MIN_OCCURRENCES_FOR_PATTERN = 3

    def discover_new_patterns(self):
        # type: () -> List[Dict[str, Any]]
        """Find failure categories that recur 3+ times but have no pattern yet.

        Returns list of newly created patterns.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.failure_category, COUNT(*) as cnt,
                       AVG(a.confidence_score) as avg_conf
                FROM ai_analysis a
                WHERE a.failure_category IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM error_patterns ep
                      WHERE ep.failure_type = 'build'
                        AND ep.failure_category = a.failure_category
                  )
                GROUP BY a.failure_category
                HAVING COUNT(*) >= %s
            """, (self.MIN_OCCURRENCES_FOR_PATTERN,))
            candidates = cursor.fetchall()

        created = []
        for category, count, avg_conf in candidates:
            pattern = self.find_or_create('build', category)
            if avg_conf:
                self._set_initial_confidence(pattern['id'], avg_conf, count)
            created.append(pattern)

        return created

    def _set_initial_confidence(self, pattern_id, avg_confidence, occurrence_count):
        # type: (int, float, int) -> None
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE error_patterns
                SET avg_confidence = %s, occurrence_count = %s
                WHERE id = %s AND avg_confidence IS NULL
            """, (avg_confidence, occurrence_count, pattern_id))
            conn.commit()

    def get_stale_patterns(self, inactive_days=90, min_accuracy=0.3):
        # type: (int, float) -> List[Dict[str, Any]]
        """Find patterns that should be archived: low accuracy or unused."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, failure_type, failure_category, pattern_name,
                       description, typical_fix, doc_url, doc_context, doc_fetched_at,
                       occurrence_count, avg_confidence, first_seen_at, last_seen_at, created_by
                FROM error_patterns
                WHERE (
                    (last_used_at IS NOT NULL AND last_used_at < NOW() - (%s || ' days')::INTERVAL)
                    OR (avg_confidence IS NOT NULL AND avg_confidence < %s AND match_count >= 5)
                )
                ORDER BY COALESCE(last_used_at, first_seen_at) ASC
            """, (inactive_days, min_accuracy))
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_cross_app_patterns(self):
        # type: () -> List[Dict[str, Any]]
        """Find patterns that appear across multiple applications.

        Joins error_patterns → ai_analysis → build_failures to see which
        applications share each pattern. Only returns patterns seen in 2+ apps.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    ep.id,
                    ep.pattern_name,
                    ep.failure_type,
                    ep.failure_category,
                    ep.avg_confidence,
                    ep.typical_fix,
                    COUNT(DISTINCT app) as app_count,
                    ARRAY_AGG(DISTINCT app) as applications,
                    SUM(cnt) as total_occurrences
                FROM error_patterns ep
                JOIN (
                    SELECT
                        a.error_pattern_id,
                        COALESCE(bf.application, cr.application) as app,
                        COUNT(*) as cnt
                    FROM ai_analysis a
                    LEFT JOIN build_failures bf ON bf.id = a.build_failure_id
                    LEFT JOIN conforma_results cr ON cr.id = a.conforma_result_id
                    WHERE a.error_pattern_id IS NOT NULL
                    GROUP BY a.error_pattern_id, COALESCE(bf.application, cr.application)
                ) usage ON usage.error_pattern_id = ep.id
                GROUP BY ep.id, ep.pattern_name, ep.failure_type, ep.failure_category,
                         ep.avg_confidence, ep.typical_fix
                HAVING COUNT(DISTINCT app) >= 2
                ORDER BY COUNT(DISTINCT app) DESC, SUM(cnt) DESC
            """)
            cols = ['id', 'pattern_name', 'failure_type', 'failure_category',
                    'avg_confidence', 'typical_fix', 'app_count', 'applications',
                    'total_occurrences']
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_patterns_for_app(self, application):
        # type: (str,) -> List[Dict[str, Any]]
        """Get patterns seen in a specific application, with cross-app context."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    ep.id,
                    ep.pattern_name,
                    ep.failure_type,
                    ep.failure_category,
                    ep.avg_confidence,
                    ep.typical_fix,
                    app_usage.cnt as app_occurrences,
                    ep.occurrence_count as total_occurrences,
                    (SELECT ARRAY_AGG(DISTINCT COALESCE(bf2.application, cr2.application))
                     FROM ai_analysis a2
                     LEFT JOIN build_failures bf2 ON bf2.id = a2.build_failure_id
                     LEFT JOIN conforma_results cr2 ON cr2.id = a2.conforma_result_id
                     WHERE a2.error_pattern_id = ep.id
                       AND COALESCE(bf2.application, cr2.application) != %s
                    ) as also_seen_in
                FROM error_patterns ep
                JOIN (
                    SELECT a.error_pattern_id, COUNT(*) as cnt
                    FROM ai_analysis a
                    LEFT JOIN build_failures bf ON bf.id = a.build_failure_id
                    LEFT JOIN conforma_results cr ON cr.id = a.conforma_result_id
                    WHERE COALESCE(bf.application, cr.application) = %s
                      AND a.error_pattern_id IS NOT NULL
                    GROUP BY a.error_pattern_id
                ) app_usage ON app_usage.error_pattern_id = ep.id
                ORDER BY app_usage.cnt DESC
            """, (application, application))
            cols = ['id', 'pattern_name', 'failure_type', 'failure_category',
                    'avg_confidence', 'typical_fix', 'app_occurrences',
                    'total_occurrences', 'also_seen_in']
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

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
