"""Repository factory for MCP server.

Creates DB connections with pooling and repositories.
Reads DB config from environment variables, reuses DatabaseConfig from collectors.
"""

import os
import sys
from pathlib import Path
from contextlib import contextmanager
from typing import Generator
import psycopg2
from psycopg2 import pool

# Add src/ to path to import config and repositories
src_path = Path(__file__).parent.parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

from config import DatabaseConfig  # type: ignore
from repositories.build_failure_repository import BuildFailureRepository  # type: ignore
from repositories.conforma_repository import ConformaRepository  # type: ignore
from repositories.ai_analysis_repository import AIAnalysisRepository  # type: ignore


class PooledDatabaseConnection:
    """Database connection with connection pooling for MCP server.

    Unlike the standard DatabaseConnection used by collectors (which are
    short-lived cron jobs), the MCP server is a long-lived process that
    handles concurrent requests from AI agents. Connection pooling improves
    performance by reusing connections instead of creating new ones per request.
    """

    def __init__(self, config: DatabaseConfig, minconn: int = 1, maxconn: int = 10):
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
    def connection(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """Get a connection from the pool."""
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
        """Close all connections in the pool (for cleanup)."""
        self._pool.closeall()


def create_db_config() -> DatabaseConfig:
    """Create DatabaseConfig from environment variables.

    Reuses DatabaseConfig from collectors/config.py instead of
    duplicating the configuration class.
    """
    return DatabaseConfig(
        host=os.getenv('DB_HOST', 'localhost'),
        port=int(os.getenv('DB_PORT', '5433')),
        database=os.getenv('DB_NAME', 'konflux_monitoring'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', 'admin'),
    )


def create_db_connection() -> PooledDatabaseConnection:
    """Create pooled DB connection from environment."""
    db_config = create_db_config()
    return PooledDatabaseConnection(db_config)


# Global database connection pool and repositories
# These are reused across all MCP tool calls (long-lived server process)
_db = create_db_connection()
build_repo = BuildFailureRepository(_db)
conforma_repo = ConformaRepository(_db)
ai_repo = AIAnalysisRepository(_db)
