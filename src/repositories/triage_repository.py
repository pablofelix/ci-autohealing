"""Repository for triage item tracking."""



class TriageRepository:

    def __init__(self, db):
        self.db = db

    def create_item(self, application, components, group_label=None,
                    root_cause=None, failed_step=None,
                    slack_thread_urls=None, jira_key=None, notes=None):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO triage_items
                    (application, components, group_label, root_cause,
                     failed_step, slack_thread_urls, jira_key, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (application, components, group_label, root_cause,
                  failed_step, slack_thread_urls, jira_key, notes))
            return cursor.fetchone()[0]

    def build_jira_map(self, application):
        """Build component->jira_key map from active triage items."""
        jira_map = {}
        try:
            for item in self.get_active(application):
                jira_key = item.get('jira_key')
                if jira_key:
                    for comp in item.get('components', []):
                        if comp not in jira_map:
                            jira_map[comp] = jira_key
        except Exception:
            pass
        return jira_map

    def get_active(self, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, group_label, components, root_cause, failed_step,
                       status, slack_thread_urls, jira_key, notes,
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
                           status, slack_thread_urls, jira_key, notes,
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
                           status, slack_thread_urls, jira_key, notes,
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
                       status, slack_thread_urls, jira_key, notes,
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

    def find_by_jira_key(self, jira_key, application=None):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if application:
                cursor.execute("""
                    SELECT id, group_label, components, root_cause, failed_step,
                           status, slack_thread_urls, jira_key, notes,
                           resolution, resolution_pr_url, resolved_at,
                           created_at, updated_at
                    FROM triage_items
                    WHERE UPPER(jira_key) = UPPER(%s) AND application = %s
                    ORDER BY created_at DESC LIMIT 1
                """, (jira_key, application))
            else:
                cursor.execute("""
                    SELECT id, group_label, components, root_cause, failed_step,
                           status, slack_thread_urls, jira_key, notes,
                           resolution, resolution_pr_url, resolved_at,
                           created_at, updated_at
                    FROM triage_items
                    WHERE UPPER(jira_key) = UPPER(%s)
                    ORDER BY created_at DESC LIMIT 1
                """, (jira_key,))
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def find_by_component(self, component, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, group_label, components, root_cause, failed_step,
                       status, slack_thread_urls, jira_key, notes,
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
            'status', 'slack_thread_urls', 'jira_key', 'notes',
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

    def add_slack_url(self, item_id, url):
        """Append a Slack URL to the item's list (no duplicates)."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE triage_items
                SET slack_thread_urls = array_append(
                        COALESCE(slack_thread_urls, '{}'), %s),
                    updated_at = NOW()
                WHERE id = %s
                  AND (slack_thread_urls IS NULL
                       OR NOT %s = ANY(slack_thread_urls))
            """, (url, item_id, url))
            return cursor.rowcount > 0

    def resolve_item(self, item_id, resolution=None, pr_url=None, verdict=None):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE triage_items
                SET status = 'resolved', resolved_at = NOW(),
                    resolution = COALESCE(%s, resolution),
                    resolution_pr_url = COALESCE(%s, resolution_pr_url),
                    updated_at = NOW()
                WHERE id = %s
                RETURNING components, application
            """, (resolution, pr_url, item_id))
            row = cursor.fetchone()
            if not row or not verdict:
                return cursor.rowcount > 0
            components, application = row
            if components and application:
                from repositories.ai_analysis_repository import AIAnalysisRepository
                ai_repo = AIAnalysisRepository(self.db)
                for comp in components:
                    try:
                        ai_repo.record_verdict(comp, application, verdict,
                                               verdict_by='triage')
                    except Exception:
                        pass
            return True

    def get_report(self, application, date=None):
        """Get triage items grouped for a report, optionally filtered by date."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if date:
                cursor.execute("""
                    SELECT id, group_label, components, root_cause, failed_step,
                           status, slack_thread_urls, jira_key, notes,
                           resolution, resolution_pr_url, resolved_at,
                           created_at, updated_at
                    FROM triage_items
                    WHERE application = %s AND created_at::date = %s
                    ORDER BY status, created_at
                """, (application, date))
            else:
                cursor.execute("""
                    SELECT id, group_label, components, root_cause, failed_step,
                           status, slack_thread_urls, jira_key, notes,
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

    def auto_resolve_for_component(self, component_name, application,
                                    commit_sha=None, pipelinerun=None):
        """Auto-resolve triage items when all their components have passing builds."""
        items = self.find_all_by_component(component_name, application)
        resolved_ids = []
        for item in items:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*) FROM build_failures
                    WHERE component_name = ANY(%s) AND application = %s
                      AND is_resolved = FALSE
                """, (item['components'], application))
                still_failing = cursor.fetchone()[0]
            if still_failing == 0:
                parts = ['Auto-resolved: all component builds succeeded']
                if commit_sha:
                    parts.append('commit: {}'.format(commit_sha[:12]))
                if pipelinerun:
                    parts.append('build: {}'.format(pipelinerun))
                self.resolve_item(
                    item['id'],
                    resolution='. '.join(parts) if len(parts) > 1 else parts[0],
                )
                resolved_ids.append(item['id'])
        return resolved_ids

    def find_all_by_component(self, component, application):
        """Find all active triage items containing a component."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, group_label, components, root_cause, failed_step,
                       status, slack_thread_urls, jira_key, notes,
                       resolution, resolution_pr_url, resolved_at,
                       created_at, updated_at
                FROM triage_items
                WHERE application = %s AND %s = ANY(components)
                  AND status != 'resolved'
                ORDER BY created_at DESC
            """, (application, component))
            return [self._row_to_dict(r) for r in cursor.fetchall()]

    @staticmethod
    def _row_to_dict(row):
        return {
            'id': row[0],
            'group_label': row[1],
            'components': row[2] or [],
            'root_cause': row[3],
            'failed_step': row[4],
            'status': row[5],
            'slack_thread_urls': row[6] or [],
            'jira_key': row[7],
            'notes': row[8],
            'resolution': row[9],
            'resolution_pr_url': row[10],
            'resolved_at': row[11],
            'created_at': row[12],
            'updated_at': row[13],
        }
