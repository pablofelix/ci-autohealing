"""Repository for sync_status table operations."""

import json
from typing import Optional, Set, Dict, Any

from repositories.connection import DatabaseConnection


class SyncStatusRepository:
    """All SQL operations on the sync_status table."""

    def __init__(self, db):
        # type: (DatabaseConnection,) -> None
        self.db = db

    def save_build_sync_status(self, application, status, duration):
        # type: (str, Dict[str, Any], float) -> None
        try:
            running_builds_json = json.dumps(status.get('running_builds', []))

            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO sync_status (
                        application, last_checked_at, in_sync, cluster_connected,
                        cluster_components, db_components, missing_in_db, extra_in_db,
                        retriggered_components, running_builds, error, check_duration_seconds
                    ) VALUES (
                        %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (application) DO UPDATE SET
                        last_checked_at = NOW(),
                        in_sync = EXCLUDED.in_sync,
                        cluster_connected = EXCLUDED.cluster_connected,
                        cluster_components = EXCLUDED.cluster_components,
                        db_components = EXCLUDED.db_components,
                        missing_in_db = EXCLUDED.missing_in_db,
                        extra_in_db = EXCLUDED.extra_in_db,
                        retriggered_components = EXCLUDED.retriggered_components,
                        running_builds = EXCLUDED.running_builds,
                        error = EXCLUDED.error,
                        check_duration_seconds = EXCLUDED.check_duration_seconds
                    """,
                    (
                        application,
                        status['in_sync'],
                        status['cluster_connected'],
                        json.dumps(status['cluster_components']),
                        json.dumps(status['db_components']),
                        json.dumps(status['missing_in_db']),
                        json.dumps(status['extra_in_db']),
                        json.dumps(status.get('retriggered_components', [])),
                        running_builds_json,
                        status.get('error'),
                        round(duration, 2)
                    )
                )
        except Exception:
            pass

    def save_conforma_sync_status(self, application, failing_components, running=None):
        # type: (str, Set[str], Optional[Dict[str, Any]]) -> None
        try:
            components_json = json.dumps(sorted(failing_components))
            running_json = json.dumps([
                {'component': comp, **info} for comp, info in sorted((running or {}).items())
            ])

            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE sync_status
                    SET conforma_components = %s,
                        running_conforma = %s
                    WHERE application = %s
                    """,
                    (components_json, running_json, application)
                )
        except Exception:
            pass
