"""Database connection manager and scan tracking."""

import uuid
from contextlib import contextmanager
from typing import Generator
import psycopg2

from config import DatabaseConfig
from models import ScanResult


class DatabaseConnection:
    """PostgreSQL connection manager."""

    def __init__(self, config):
        # type: (DatabaseConfig,) -> None
        self.config = config

    @contextmanager
    def connection(self):
        # type: () -> Generator[psycopg2.extensions.connection, None, None]
        conn = psycopg2.connect(self.config.connection_string)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_scan(self, scan_type='python', scan_mode='full'):
        # type: (str, str) -> str
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

    def complete_scan(self, scan_id, result):
        # type: (str, ScanResult) -> None
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
