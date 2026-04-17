"""
Database operations for CI Auto-Healing system
"""

import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
import uuid

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import SimpleConnectionPool

logger = logging.getLogger(__name__)


class Database:
    """Database interface for build failures"""

    def __init__(self):
        self.pool = self._create_pool()

    def _create_pool(self) -> SimpleConnectionPool:
        """Create connection pool"""
        return SimpleConnectionPool(
            minconn=1,
            maxconn=int(os.getenv('DB_POOL_SIZE', '10')),
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', '5432')),
            database=os.getenv('DB_NAME', 'konflux_monitoring'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', '')
        )

    @contextmanager
    def get_conn(self):
        """Get connection from pool"""
        conn = self.pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            self.pool.putconn(conn)

    def build_failure_exists(self, pipelinerun_name: str) -> bool:
        """Check if build failure already exists"""
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM build_failures WHERE pipelinerun_name = %s",
                    (pipelinerun_name,)
                )
                return cur.fetchone() is not None

    def insert_build_failure(self, data: Dict[str, Any]) -> int:
        """Insert new build failure"""
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                # Convert dict to JSONB if needed
                if 'raw_pipelinerun_yaml' in data and isinstance(data['raw_pipelinerun_yaml'], dict):
                    data['raw_pipelinerun_yaml'] = Json(data['raw_pipelinerun_yaml'])

                columns = ', '.join(data.keys())
                placeholders = ', '.join(['%s'] * len(data))

                query = f"""
                    INSERT INTO build_failures ({columns})
                    VALUES ({placeholders})
                    ON CONFLICT (pipelinerun_name) DO UPDATE SET
                        status = EXCLUDED.status,
                        error_message = EXCLUDED.error_message,
                        build_completion_time = EXCLUDED.build_completion_time,
                        last_updated_at = NOW()
                    RETURNING id
                """

                cur.execute(query, list(data.values()))
                result = cur.fetchone()

                # Log event
                self._log_event(cur, 'build_failed', data.get('component_name'), data.get('pipelinerun_name'))

                return result[0] if result else None

    def start_scan(self, scan_type: str, scan_mode: str, config: Dict = None) -> str:
        """Start a new scan and return scan_id"""
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO scan_history (scan_type, scan_mode, config, status)
                    VALUES (%s, %s, %s, 'running')
                    RETURNING scan_id
                """, (scan_type, scan_mode, Json(config or {})))

                result = cur.fetchone()
                return str(result[0]) if result else None

    def complete_scan(
        self,
        scan_id: str,
        components_scanned: int,
        failures_found: int,
        new_failures: int
    ):
        """Mark scan as completed"""
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE scan_history
                    SET status = 'completed',
                        completed_at = NOW(),
                        duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at)),
                        components_scanned = %s,
                        failures_found = %s,
                        new_failures = %s
                    WHERE scan_id = %s
                """, (components_scanned, failures_found, new_failures, uuid.UUID(scan_id)))

    def get_unresolved_failures(self, limit: int = 100) -> List[Dict]:
        """Get unresolved failures"""
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM build_failures
                    WHERE is_resolved = FALSE
                    ORDER BY build_completion_time DESC
                    LIMIT %s
                """, (limit,))
                return cur.fetchall()

    def get_failures_needing_analysis(self, limit: int = 10) -> List[Dict]:
        """Get failures that need AI analysis"""
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM build_failures
                    WHERE is_resolved = FALSE
                      AND ai_analyzed = FALSE
                    ORDER BY build_completion_time DESC
                    LIMIT %s
                """, (limit,))
                return cur.fetchall()

    def insert_ai_analysis(self, data: Dict[str, Any]) -> int:
        """Insert AI analysis result"""
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                # Convert analysis_json to JSONB
                if 'analysis_json' in data and isinstance(data['analysis_json'], dict):
                    data['analysis_json'] = Json(data['analysis_json'])

                columns = ', '.join(data.keys())
                placeholders = ', '.join(['%s'] * len(data))

                query = f"""
                    INSERT INTO ai_analysis ({columns})
                    VALUES ({placeholders})
                    RETURNING id
                """

                cur.execute(query, list(data.values()))
                result = cur.fetchone()
                analysis_id = result[0] if result else None

                # Update build_failure
                if analysis_id and 'build_failure_id' in data:
                    cur.execute("""
                        UPDATE build_failures
                        SET ai_analyzed = TRUE,
                            ai_analysis_id = %s,
                            last_updated_at = NOW()
                        WHERE id = %s
                    """, (analysis_id, data['build_failure_id']))

                return analysis_id

    def insert_resolution_attempt(self, data: Dict[str, Any]) -> int:
        """Insert resolution attempt"""
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                columns = ', '.join(data.keys())
                placeholders = ', '.join(['%s'] * len(data))

                query = f"""
                    INSERT INTO resolution_attempts ({columns})
                    VALUES ({placeholders})
                    RETURNING id
                """

                cur.execute(query, list(data.values()))
                result = cur.fetchone()

                # Update build_failure
                if result and 'build_failure_id' in data:
                    cur.execute("""
                        UPDATE build_failures
                        SET ai_fix_attempted = TRUE,
                            last_updated_at = NOW()
                        WHERE id = %s
                    """, (data['build_failure_id'],))

                return result[0] if result else None

    def mark_failure_resolved(
        self,
        failure_id: int,
        resolution_type: str,
        resolution_commit_sha: Optional[str] = None,
        resolution_pr_url: Optional[str] = None,
        notes: Optional[str] = None
    ):
        """Mark failure as resolved"""
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE build_failures
                    SET is_resolved = TRUE,
                        resolved_at = NOW(),
                        resolution_type = %s,
                        resolution_commit_sha = %s,
                        resolution_pr_url = %s,
                        resolution_notes = %s,
                        last_updated_at = NOW()
                    WHERE id = %s
                """, (resolution_type, resolution_commit_sha, resolution_pr_url, notes, failure_id))

    def get_component_health(self, component_name: str) -> Optional[Dict]:
        """Get health status of a component"""
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM component_health
                    WHERE component_name = %s
                """, (component_name,))
                return cur.fetchone()

    def _log_event(
        self,
        cur,
        event_type: str,
        component_name: Optional[str] = None,
        pipelinerun_name: Optional[str] = None,
        payload: Optional[Dict] = None,
        event_source: str = 'scanner'
    ):
        """Log event to event_log table"""
        cur.execute("""
            INSERT INTO event_log (event_type, event_source, component_name, pipelinerun_name, payload)
            VALUES (%s, %s, %s, %s, %s)
        """, (event_type, event_source, component_name, pipelinerun_name, Json(payload or {})))

    def close(self):
        """Close connection pool"""
        self.pool.closeall()
