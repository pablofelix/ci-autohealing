"""Database connection manager and scan tracking."""

import uuid
from contextlib import contextmanager
import psycopg2
from psycopg2 import pool



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


class PooledDatabaseConnection:
    """PostgreSQL connection pool for long-running server processes.

    Same connection() API as DatabaseConnection but backed by
    ThreadedConnectionPool for concurrent request handling.
    """

    def __init__(self, config, minconn=1, maxconn=10):
        self.config = config
        self._pool = pool.ThreadedConnectionPool(
            minconn=minconn,
            maxconn=maxconn,
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            dbname=config.database,
        )

    @contextmanager
    def connection(self):
        conn = self._pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def close_all(self):
        self._pool.closeall()
