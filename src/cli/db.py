"""Database helpers for IC CLI.

Provides sql() and sql_table() equivalents from the bash ic script.
Uses psycopg2 directly (same library the collectors use) instead of
shelling out to docker exec psql.
"""

import os
import sys

import psycopg2
import psycopg2.extras


_DB_CONTAINER = os.environ.get('DB_CONTAINER', 'ci-autohealing-db')
_DB_NAME = os.environ.get('DB_NAME', 'konflux_monitoring')
_DB_USER = os.environ.get('DB_USER', 'postgres')
_DB_PASS = os.environ.get('PGPASSWORD', 'admin')
_DB_HOST = os.environ.get('DB_HOST', 'localhost')
_DB_PORT = int(os.environ.get('DB_PORT', '5432'))

_conn = None
_db_available = None
_db_connection = None
_repo_cache = {}


def _get_connection():
    global _conn
    if _conn is not None and not _conn.closed:
        return _conn
    _conn = psycopg2.connect(
        host=_DB_HOST, port=_DB_PORT,
        user=_DB_USER, password=_DB_PASS,
        dbname=_DB_NAME,
    )
    _conn.autocommit = True
    return _conn


def check_db():
    # type: () -> bool
    global _db_available
    if _db_available is not None:
        return _db_available
    try:
        _get_connection()
        _db_available = True
    except Exception:
        _db_available = False
    return _db_available


def require_db():
    # type: () -> bool
    if not check_db():
        from cli.formatting import red, cyan
        print(red('Error: database is not running ({})'.format(_DB_CONTAINER)),
              file=sys.stderr)
        print(cyan('Start it: docker start {}'.format(_DB_CONTAINER)),
              file=sys.stderr)
        return False
    return True


def sql(query):
    # type: (str) -> Optional[str]
    """Run a query and return the first column of the first row (like bash sql())."""
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
            if row is None:
                return None
            return str(row[0]) if row[0] is not None else None
    except Exception:
        return None


def sql_rows(query):
    # type: (str) -> List[Tuple]
    """Run a query and return all rows as a list of tuples."""
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchall()
    except Exception:
        return []



def sql_table(query):
    # type: (str) -> None
    """Run a query and print results as a formatted table (like psql output)."""
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(query)
            if cur.description is None:
                return
            headers = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            _print_table(headers, rows)
    except Exception as e:
        print('DB error: {}'.format(e), file=sys.stderr)


def sql_execute(query):
    # type: (str) -> int
    """Run a write query and return rows affected."""
    try:
        conn = _get_connection()
        with conn.cursor() as cur:
            cur.execute(query)
            return cur.rowcount
    except Exception:
        return 0


def _get_db_connection():
    global _db_connection
    if _db_connection is None:
        from config import CollectorConfig
        from repositories.connection import DatabaseConnection
        cfg = CollectorConfig.from_env()
        _db_connection = DatabaseConnection(cfg.db)
    return _db_connection


def get_repo(repo_class):
    if repo_class not in _repo_cache:
        _repo_cache[repo_class] = repo_class(_get_db_connection())
    return _repo_cache[repo_class]


def print_table(headers, rows):
    # type: (list, list) -> None
    """Print a formatted table from headers + rows."""
    _print_table(headers, rows)


def _print_table(headers, rows):
    # type: (List[str], List[Tuple]) -> None
    """Print a psql-style table."""
    if not rows:
        print('(0 rows)')
        return

    str_rows = []
    for row in rows:
        str_rows.append([_fmt_cell(v) for v in row])

    widths = [len(h) for h in headers]
    for row in str_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    header_line = ' | '.join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep_line = '-+-'.join('-' * w for w in widths)

    print(' ' + header_line)
    print('-' + sep_line)
    for row in str_rows:
        print(' ' + ' | '.join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    print('({} row{})'.format(len(rows), '' if len(rows) == 1 else 's'))


def _fmt_cell(value):
    # type: (Any) -> str
    if value is None:
        return ''
    return str(value)
