"""Repository for triage item tracking."""



class TriageRepository:

    def __init__(self, db):
        self.db = db

    def create_item(self, application, components, group_label=None,
                    root_cause=None, failed_step=None,
                    slack_thread_url=None, jira_key=None, notes=None):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO triage_items
                    (application, components, group_label, root_cause,
                     failed_step, slack_thread_url, jira_key, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (application, components, group_label, root_cause,
                  failed_step, slack_thread_url, jira_key, notes))
            return cursor.fetchone()[0]

    def get_active(self, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, group_label, components, root_cause, failed_step,
                       status, slack_thread_url, jira_key, notes,
                       resolution, resolution_pr_url, resolved_at,
                       created_at, updated_at
                FROM triage_items
                WHERE application = %s AND status != 'resolved'
                ORDER BY created_at DESC
            """, (application,))
            return [self._row_to_dict(r) for r in cursor.fetchall()]

    def get_all(self, application, days=None):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if days:
                cursor.execute("""
                    SELECT id, group_label, components, root_cause, failed_step,
                           status, slack_thread_url, jira_key, notes,
                           resolution, resolution_pr_url, resolved_at,
                           created_at, updated_at
                    FROM triage_items
                    WHERE application = %s
                      AND created_at >= NOW() - INTERVAL '1 day' * %s
                    ORDER BY created_at DESC
                """, (application, days))
            else:
                cursor.execute("""
                    SELECT id, group_label, components, root_cause, failed_step,
                           status, slack_thread_url, jira_key, notes,
                           resolution, resolution_pr_url, resolved_at,
                           created_at, updated_at
                    FROM triage_items
                    WHERE application = %s
                    ORDER BY created_at DESC
                """, (application,))
            return [self._row_to_dict(r) for r in cursor.fetchall()]

    def get_by_id(self, item_id):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, group_label, components, root_cause, failed_step,
                       status, slack_thread_url, jira_key, notes,
                       resolution, resolution_pr_url, resolved_at,
                       created_at, updated_at, application
                FROM triage_items WHERE id = %s
            """, (item_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = self._row_to_dict(row)
            d['application'] = row[14]
            return d

    def find_by_component(self, component, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, group_label, components, root_cause, failed_step,
                       status, slack_thread_url, jira_key, notes,
                       resolution, resolution_pr_url, resolved_at,
                       created_at, updated_at
                FROM triage_items
                WHERE application = %s AND %s = ANY(components)
                  AND status != 'resolved'
                ORDER BY created_at DESC LIMIT 1
            """, (application, component))
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def update_item(self, item_id, **kwargs):
        allowed = {
            'group_label', 'components', 'root_cause', 'failed_step',
            'status', 'slack_thread_url', 'jira_key', 'notes',
            'resolution', 'resolution_pr_url',
        }
        fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not fields:
            return False
        set_parts = ['{} = %s'.format(k) for k in fields]
        set_parts.append('updated_at = NOW()')
        values = list(fields.values()) + [item_id]
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE triage_items SET {} WHERE id = %s'.format(', '.join(set_parts)),
                values,
            )
            return cursor.rowcount > 0

    def resolve_item(self, item_id, resolution=None, pr_url=None):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE triage_items
                SET status = 'resolved', resolved_at = NOW(),
                    resolution = COALESCE(%s, resolution),
                    resolution_pr_url = COALESCE(%s, resolution_pr_url),
                    updated_at = NOW()
                WHERE id = %s
            """, (resolution, pr_url, item_id))
            return cursor.rowcount > 0

    def get_report(self, application, date=None):
        """Get triage items grouped for a report, optionally filtered by date."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if date:
                cursor.execute("""
                    SELECT id, group_label, components, root_cause, failed_step,
                           status, slack_thread_url, jira_key, notes,
                           resolution, resolution_pr_url, resolved_at,
                           created_at, updated_at
                    FROM triage_items
                    WHERE application = %s AND created_at::date = %s
                    ORDER BY status, created_at
                """, (application, date))
            else:
                cursor.execute("""
                    SELECT id, group_label, components, root_cause, failed_step,
                           status, slack_thread_url, jira_key, notes,
                           resolution, resolution_pr_url, resolved_at,
                           created_at, updated_at
                    FROM triage_items
                    WHERE application = %s
                    ORDER BY
                        CASE status
                            WHEN 'active' THEN 0
                            WHEN 'monitoring' THEN 1
                            WHEN 'resolved' THEN 2
                        END,
                        created_at DESC
                """, (application,))
            return [self._row_to_dict(r) for r in cursor.fetchall()]

    def get_summary(self, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'active') as active,
                    COUNT(*) FILTER (WHERE status = 'monitoring') as monitoring,
                    COUNT(*) FILTER (WHERE status = 'resolved') as resolved,
                    COUNT(*) as total
                FROM triage_items WHERE application = %s
            """, (application,))
            row = cursor.fetchone()
            return {
                'active': row[0], 'monitoring': row[1],
                'resolved': row[2], 'total': row[3],
            }

    @staticmethod
    def _row_to_dict(row):
        return {
            'id': row[0],
            'group_label': row[1],
            'components': row[2] or [],
            'root_cause': row[3],
            'failed_step': row[4],
            'status': row[5],
            'slack_thread_url': row[6],
            'jira_key': row[7],
            'notes': row[8],
            'resolution': row[9],
            'resolution_pr_url': row[10],
            'resolved_at': row[11],
            'created_at': row[12],
            'updated_at': row[13],
        }
