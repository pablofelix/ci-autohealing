"""Repository for conforma_rule_catalog table.

Reference catalog of all Conforma (Enterprise Contract) policy rules.
Provides rule descriptions, solutions, and metadata for the AI analyzer
and CLI tools. Seeded from conforma.dev via data/conforma_rule_catalog.json.
"""


class ConformaRuleCatalogRepository:
    """SQL operations on the conforma_rule_catalog table."""

    def __init__(self, db):
        self.db = db

    def get_by_rule_ids(self, rule_ids):
        """Batch lookup by rule_id. Used by the analyzer for prompt context.

        Accepts both dotted ('hermetic_task.hermetic') and double-underscore
        ('hermetic_task__hermetic') formats — normalizes to double-underscore.
        """
        if not rule_ids:
            return []
        normalized = [r.replace('.', '__') for r in rule_ids]
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT rule_id, rule_package, rule_name, description,
                       policy_type, collections, typical_fix,
                       reporter_solution, doc_url
                FROM conforma_rule_catalog
                WHERE rule_id = ANY(%s)
            """, (normalized,))
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_by_package(self, package):
        """All rules in a package (e.g., 'cve' returns all cve__ rules)."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT rule_id, rule_package, rule_name, description,
                       policy_type, collections, typical_fix,
                       reporter_solution, doc_url
                FROM conforma_rule_catalog
                WHERE rule_package = %s
                ORDER BY rule_id
            """, (package,))
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def search(self, query):
        """Full-text search across rule_name and description."""
        pattern = '%{}%'.format(query)
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT rule_id, rule_package, rule_name, description,
                       policy_type, collections, typical_fix,
                       reporter_solution, doc_url
                FROM conforma_rule_catalog
                WHERE rule_name ILIKE %s OR description ILIKE %s
                ORDER BY rule_package, rule_id
            """, (pattern, pattern))
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_all(self):
        """Dump entire catalog."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT rule_id, rule_package, rule_name, description,
                       policy_type, collections, typical_fix,
                       reporter_solution, doc_url
                FROM conforma_rule_catalog
                ORDER BY rule_package, rule_id
            """)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def upsert(self, rule_id, rule_package, rule_name, description=None,
               policy_type=None, collections=None, typical_fix=None,
               reporter_solution=None, doc_url=None):
        """Insert or update a single rule."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO conforma_rule_catalog
                    (rule_id, rule_package, rule_name, description,
                     policy_type, collections, typical_fix,
                     reporter_solution, doc_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (rule_id) DO UPDATE SET
                    rule_package = EXCLUDED.rule_package,
                    rule_name = EXCLUDED.rule_name,
                    description = COALESCE(EXCLUDED.description, conforma_rule_catalog.description),
                    policy_type = COALESCE(EXCLUDED.policy_type, conforma_rule_catalog.policy_type),
                    collections = COALESCE(EXCLUDED.collections, conforma_rule_catalog.collections),
                    typical_fix = COALESCE(EXCLUDED.typical_fix, conforma_rule_catalog.typical_fix),
                    reporter_solution = COALESCE(EXCLUDED.reporter_solution, conforma_rule_catalog.reporter_solution),
                    doc_url = COALESCE(EXCLUDED.doc_url, conforma_rule_catalog.doc_url),
                    updated_at = NOW()
            """, (rule_id, rule_package, rule_name, description,
                  policy_type, collections, typical_fix,
                  reporter_solution, doc_url))

    def update_reporter_solution(self, rule_id, solution):
        """Set/update the RHOAI-specific solution from conforma-reporter."""
        normalized = rule_id.replace('.', '__')
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE conforma_rule_catalog
                SET reporter_solution = %s, updated_at = NOW()
                WHERE rule_id = %s
            """, (solution, normalized))
            return cursor.rowcount > 0

    def get_catalog_stats(self):
        """Count total rules, how many have reporter_solution, etc."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE reporter_solution IS NOT NULL) as with_reporter,
                    COUNT(*) FILTER (WHERE typical_fix IS NOT NULL) as with_fix,
                    COUNT(DISTINCT rule_package) as packages,
                    COUNT(DISTINCT policy_type) as policy_types
                FROM conforma_rule_catalog
            """)
            row = cursor.fetchone()
            return {
                'total': row[0],
                'with_reporter_solution': row[1],
                'with_typical_fix': row[2],
                'packages': row[3],
                'policy_types': row[4],
            }

    @staticmethod
    def _row_to_dict(row):
        cols = ['rule_id', 'rule_package', 'rule_name', 'description',
                'policy_type', 'collections', 'typical_fix',
                'reporter_solution', 'doc_url']
        return dict(zip(cols, row))
