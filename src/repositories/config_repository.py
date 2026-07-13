"""Runtime configuration repository — API-managed settings stored in DB."""


class ConfigRepository:

    def __init__(self, db):
        self.db = db

    def get(self, key, default=None):
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM runtime_config WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row and row[0] else default

    def set(self, key, value, updated_by='api'):
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO runtime_config (key, value, updated_at, updated_by)
                VALUES (%s, %s, NOW(), %s)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW(),
                    updated_by = EXCLUDED.updated_by
            """, (key, value, updated_by))

    def get_all(self):
        with self.db.connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute("SELECT key, value, updated_at, updated_by FROM runtime_config ORDER BY key")
            except Exception:
                conn.rollback()
                return []
            cols = ['key', 'value', 'updated_at', 'updated_by']
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_watched_applications(self):
        val = self.get('watched_applications', '')
        return val.split() if val else []

    def set_watched_applications(self, apps, updated_by='api'):
        self.set('watched_applications', ' '.join(apps), updated_by)

    def add_watched_application(self, app, updated_by='api'):
        apps = self.get_watched_applications()
        if app not in apps:
            apps.append(app)
            self.set_watched_applications(apps, updated_by)
        return apps

    def remove_watched_application(self, app, updated_by='api'):
        apps = self.get_watched_applications()
        if app in apps:
            apps.remove(app)
            self.set_watched_applications(apps, updated_by)
        return apps
