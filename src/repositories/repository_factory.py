"""Repository factory with connection pooling for server processes.

Used by MCP server and REST API. Provides init_pool() at startup
and get_repository() for lazy repository creation with shared pool.

CLI uses cli/db.py's get_repo() instead — different lifecycle.
"""

from repositories.connection import PooledDatabaseConnection

_db_pool = None
_repo_cache = {}


def init_pool(db_config, pool_size=10):
    global _db_pool
    if _db_pool is not None:
        return
    _db_pool = PooledDatabaseConnection(db_config, maxconn=pool_size)


def get_repository(repo_class):
    if _db_pool is None:
        raise RuntimeError("Call init_pool() before get_repository()")
    if repo_class not in _repo_cache:
        _repo_cache[repo_class] = repo_class(_db_pool)
    return _repo_cache[repo_class]


def get_pool():
    if _db_pool is None:
        raise RuntimeError("Call init_pool() first")
    return _db_pool


def close_pool():
    global _db_pool, _repo_cache
    if _db_pool:
        _db_pool.close_all()
        _db_pool = None
        _repo_cache = {}
