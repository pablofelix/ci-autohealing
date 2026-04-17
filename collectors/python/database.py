"""Database operations for CI Auto-Healing system."""

import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional, Generator
import psycopg2
from psycopg2.extras import RealDictCursor

from config import DatabaseConfig
from models import PipelineRun, ScanResult


class Database:
    """PostgreSQL database interface for build failures."""

    def __init__(self, config: DatabaseConfig):
        """Initialize database connection.

        Args:
            config: Database configuration.
        """
        self.config = config

    @contextmanager
    def connection(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """Context manager for database connections.

        Yields:
            PostgreSQL connection.

        Example:
            with db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT ...")
        """
        conn = psycopg2.connect(self.config.connection_string)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_scan(self, scan_type: str = 'python', scan_mode: str = 'full') -> str:
        """Create a new scan record.

        Args:
            scan_type: Type of scan (e.g., 'manual', 'cron', 'python').
            scan_mode: Scan mode (e.g., 'simple', 'full', 'logs-only').

        Returns:
            Scan UUID.
        """
        scan_id = str(uuid.uuid4())

        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO scan_history (scan_id, scan_type, scan_mode, status)
                VALUES (%s, %s, %s, %s)
                """,
                (scan_id, scan_type, scan_mode, 'running')
            )

        return scan_id

    def complete_scan(self, scan_id: str, result: ScanResult) -> None:
        """Mark scan as completed with results.

        Args:
            scan_id: Scan UUID.
            result: Scan results.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE scan_history
                SET status = 'completed',
                    completed_at = NOW(),
                    duration_seconds = %s,
                    components_scanned = %s,
                    failures_found = %s,
                    new_failures = %s,
                    logs_fetched = %s
                WHERE scan_id = %s
                """,
                (
                    result.duration_seconds,
                    result.components_scanned,
                    result.failures_found,
                    result.new_failures,
                    result.logs_fetched,
                    scan_id
                )
            )

    def pipelinerun_exists(self, name: str) -> bool:
        """Check if PipelineRun already exists in database.

        Args:
            name: PipelineRun name.

        Returns:
            True if exists, False otherwise.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM build_failures WHERE pipelinerun_name = %s LIMIT 1",
                (name,)
            )
            return cursor.fetchone() is not None

    def insert_pipelinerun(self, pr: PipelineRun, application_name: str) -> bool:
        """Insert new PipelineRun failure record.

        Args:
            pr: PipelineRun instance.
            application_name: Konflux application name for URL generation.

        Returns:
            True if inserted successfully, False otherwise.
        """
        konflux_url = pr.konflux_logs_url.format(app=application_name)

        with self.connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO build_failures (
                        component_name,
                        pipelinerun_name,
                        pipelinerun_uid,
                        namespace,
                        repository,
                        repository_url,
                        branch,
                        status,
                        build_logs,
                        konflux_logs_url,
                        first_detected_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    )
                    """,
                    (
                        pr.component,
                        pr.name,
                        pr.uid,
                        pr.namespace,
                        pr.repository,
                        pr.repository_url,
                        pr.branch,
                        pr.status.value,
                        pr.build_logs,
                        konflux_url
                    )
                )
                return True
            except psycopg2.IntegrityError:
                # Already exists
                return False

    def update_pipelinerun_logs(self, name: str, logs: str) -> bool:
        """Update logs for an existing PipelineRun.

        Args:
            name: PipelineRun name.
            logs: Build logs content.

        Returns:
            True if updated successfully, False otherwise.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE build_failures
                SET build_logs = %s
                WHERE pipelinerun_name = %s
                """,
                (logs, name)
            )
            return cursor.rowcount > 0

    def get_pipelineruns_without_logs(self, limit: int = 10) -> List[tuple]:
        """Get PipelineRuns that don't have logs yet.

        Args:
            limit: Maximum number of records to return.

        Returns:
            List of (name, uid) tuples.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT pipelinerun_name, pipelinerun_uid
                FROM build_failures
                WHERE build_logs IS NULL
                  AND pipelinerun_uid IS NOT NULL
                ORDER BY first_detected_at DESC
                LIMIT %s
                """,
                (limit,)
            )
            return cursor.fetchall()

    def update_component_health(self, component_name: str) -> None:
        """Update component health statistics.

        Args:
            component_name: Component name.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT update_component_health(%s)",
                (component_name,)
            )
