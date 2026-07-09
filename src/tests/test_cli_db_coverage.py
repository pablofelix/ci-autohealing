import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

"""Tests for cli.db — Database helpers for the IC CLI."""

from unittest.mock import MagicMock, patch

import pytest

import cli.db as db_module


@pytest.fixture(autouse=True)
def reset_db_globals():
    """Reset all module-level globals between tests."""
    db_module._conn = None
    db_module._db_available = None
    db_module._db_connection = None
    db_module._repo_cache = {}
    yield
    db_module._conn = None
    db_module._db_available = None
    db_module._db_connection = None
    db_module._repo_cache = {}


# ---------------------------------------------------------------------------
# _get_connection
# ---------------------------------------------------------------------------

class TestGetConnection:
    @patch('cli.db.psycopg2')
    def test_creates_new_connection_when_none(self, mock_psycopg2):
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        result = db_module._get_connection()

        assert result is mock_conn
        mock_psycopg2.connect.assert_called_once_with(
            host=db_module._DB_HOST,
            port=db_module._DB_PORT,
            user=db_module._DB_USER,
            password=db_module._DB_PASS,
            dbname=db_module._DB_NAME,
        )
        assert mock_conn.autocommit is True

    @patch('cli.db.psycopg2')
    def test_reuses_existing_open_connection(self, mock_psycopg2):
        mock_conn = MagicMock()
        mock_conn.closed = False
        db_module._conn = mock_conn

        result = db_module._get_connection()

        assert result is mock_conn
        mock_psycopg2.connect.assert_not_called()

    @patch('cli.db.psycopg2')
    def test_creates_new_when_existing_closed(self, mock_psycopg2):
        old_conn = MagicMock()
        old_conn.closed = True
        db_module._conn = old_conn

        new_conn = MagicMock()
        mock_psycopg2.connect.return_value = new_conn

        result = db_module._get_connection()

        assert result is new_conn
        mock_psycopg2.connect.assert_called_once()
        assert new_conn.autocommit is True


# ---------------------------------------------------------------------------
# check_db
# ---------------------------------------------------------------------------

class TestCheckDb:
    def test_returns_cached_true(self):
        db_module._db_available = True
        assert db_module.check_db() is True

    def test_returns_cached_false(self):
        db_module._db_available = False
        assert db_module.check_db() is False

    @patch('cli.db._get_connection')
    def test_returns_true_on_successful_connection(self, mock_get_conn):
        mock_get_conn.return_value = MagicMock()

        result = db_module.check_db()

        assert result is True
        assert db_module._db_available is True

    @patch('cli.db._get_connection')
    def test_returns_false_on_connection_failure(self, mock_get_conn):
        mock_get_conn.side_effect = Exception("Connection refused")

        result = db_module.check_db()

        assert result is False
        assert db_module._db_available is False


# ---------------------------------------------------------------------------
# require_db
# ---------------------------------------------------------------------------

class TestRequireDb:
    @patch('cli.db.check_db', return_value=True)
    def test_returns_true_when_db_available(self, mock_check):
        assert db_module.require_db() is True

    @patch('cli.db.check_db', return_value=False)
    def test_returns_false_and_prints_error(self, mock_check, capsys):
        result = db_module.require_db()

        assert result is False
        captured = capsys.readouterr()
        assert 'Error: database is not running' in captured.err
        assert db_module._DB_CONTAINER in captured.err
        assert 'docker start' in captured.err


# ---------------------------------------------------------------------------
# sql
# ---------------------------------------------------------------------------

class TestSql:
    @patch('cli.db._get_connection')
    def test_returns_string_value(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (42,)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        result = db_module.sql("SELECT count(*) FROM builds")

        assert result == '42'
        mock_cursor.execute.assert_called_once_with("SELECT count(*) FROM builds")

    @patch('cli.db._get_connection')
    def test_returns_none_when_no_rows(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        result = db_module.sql("SELECT id FROM builds WHERE 1=0")

        assert result is None

    @patch('cli.db._get_connection')
    def test_returns_none_when_value_is_none(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (None,)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        result = db_module.sql("SELECT null")

        assert result is None

    @patch('cli.db._get_connection')
    def test_returns_none_on_exception(self, mock_get_conn):
        mock_get_conn.side_effect = Exception("DB error")

        result = db_module.sql("SELECT 1")

        assert result is None


# ---------------------------------------------------------------------------
# sql_rows
# ---------------------------------------------------------------------------

class TestSqlRows:
    @patch('cli.db._get_connection')
    def test_returns_rows(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [('a', 1), ('b', 2)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        result = db_module.sql_rows("SELECT name, id FROM builds")

        assert result == [('a', 1), ('b', 2)]

    @patch('cli.db._get_connection')
    def test_returns_empty_list_on_exception(self, mock_get_conn):
        mock_get_conn.side_effect = Exception("DB error")

        result = db_module.sql_rows("SELECT 1")

        assert result == []


# ---------------------------------------------------------------------------
# sql_dicts
# ---------------------------------------------------------------------------

class TestSqlDicts:
    @patch('cli.db._get_connection')
    def test_returns_dicts(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {'name': 'build-a', 'status': 'passed'},
            {'name': 'build-b', 'status': 'failed'},
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        result = db_module.sql_dicts("SELECT name, status FROM builds")

        assert len(result) == 2
        assert result[0] == {'name': 'build-a', 'status': 'passed'}
        assert result[1] == {'name': 'build-b', 'status': 'failed'}

    @patch('cli.db._get_connection')
    def test_returns_empty_list_on_exception(self, mock_get_conn):
        mock_get_conn.side_effect = Exception("DB error")

        result = db_module.sql_dicts("SELECT 1")

        assert result == []


# ---------------------------------------------------------------------------
# sql_table
# ---------------------------------------------------------------------------

class TestSqlTable:
    @patch('cli.db._get_connection')
    def test_prints_formatted_table(self, mock_get_conn, capsys):
        mock_cursor = MagicMock()
        mock_cursor.description = [('name',), ('count',)]
        mock_cursor.fetchall.return_value = [('alpha', 10), ('beta', 5)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        db_module.sql_table("SELECT name, count FROM t")

        captured = capsys.readouterr()
        assert 'name' in captured.out
        assert 'count' in captured.out
        assert 'alpha' in captured.out
        assert '(2 rows)' in captured.out

    @patch('cli.db._get_connection')
    def test_returns_silently_when_no_description(self, mock_get_conn, capsys):
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        db_module.sql_table("INSERT INTO t VALUES (1)")

        captured = capsys.readouterr()
        assert captured.out == ''
        assert captured.err == ''

    @patch('cli.db._get_connection')
    def test_prints_error_on_exception(self, mock_get_conn, capsys):
        mock_get_conn.side_effect = Exception("connection lost")

        db_module.sql_table("SELECT 1")

        captured = capsys.readouterr()
        assert 'DB error' in captured.err
        assert 'connection lost' in captured.err


# ---------------------------------------------------------------------------
# sql_execute
# ---------------------------------------------------------------------------

class TestSqlExecute:
    @patch('cli.db._get_connection')
    def test_returns_rowcount(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 3
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        result = db_module.sql_execute("DELETE FROM builds WHERE status='old'")

        assert result == 3

    @patch('cli.db._get_connection')
    def test_returns_zero_on_exception(self, mock_get_conn):
        mock_get_conn.side_effect = Exception("DB error")

        result = db_module.sql_execute("DELETE FROM builds")

        assert result == 0


# ---------------------------------------------------------------------------
# _get_db_connection
# ---------------------------------------------------------------------------

class TestGetDbConnection:
    @patch('cli.db.DatabaseConnection', create=True)
    @patch('cli.db.CollectorConfig', create=True)
    def test_creates_and_caches_connection(self, mock_config_cls, mock_db_conn_cls):
        mock_cfg = MagicMock()
        mock_config_cls.from_env.return_value = mock_cfg
        mock_db_conn = MagicMock()
        mock_db_conn_cls.return_value = mock_db_conn

        with patch.dict('sys.modules', {
            'config': MagicMock(CollectorConfig=mock_config_cls),
            'repositories': MagicMock(),
            'repositories.connection': MagicMock(DatabaseConnection=mock_db_conn_cls),
        }):
            result = db_module._get_db_connection()

        assert result is not None
        assert db_module._db_connection is not None

    def test_returns_cached_connection_on_subsequent_calls(self):
        sentinel = MagicMock()
        db_module._db_connection = sentinel

        result = db_module._get_db_connection()

        assert result is sentinel


# ---------------------------------------------------------------------------
# get_repo
# ---------------------------------------------------------------------------

class TestGetRepo:
    def test_creates_and_caches_repo(self):
        mock_db_conn = MagicMock()
        db_module._db_connection = mock_db_conn

        mock_repo_class = MagicMock()
        mock_repo_instance = MagicMock()
        mock_repo_class.return_value = mock_repo_instance

        with patch.dict('sys.modules', {
            'config': MagicMock(),
            'repositories': MagicMock(),
            'repositories.connection': MagicMock(),
        }):
            result = db_module.get_repo(mock_repo_class)

        assert result is mock_repo_instance
        mock_repo_class.assert_called_once_with(mock_db_conn)
        assert mock_repo_class in db_module._repo_cache

    def test_returns_cached_repo_on_second_call(self):
        mock_db_conn = MagicMock()
        db_module._db_connection = mock_db_conn

        mock_repo_class = MagicMock()
        mock_repo_instance = MagicMock()
        mock_repo_class.return_value = mock_repo_instance

        with patch.dict('sys.modules', {
            'config': MagicMock(),
            'repositories': MagicMock(),
            'repositories.connection': MagicMock(),
        }):
            first = db_module.get_repo(mock_repo_class)
            second = db_module.get_repo(mock_repo_class)

        assert first is second
        mock_repo_class.assert_called_once()


# ---------------------------------------------------------------------------
# print_table (public wrapper)
# ---------------------------------------------------------------------------

class TestPrintTable:
    def test_delegates_to_internal(self, capsys):
        db_module.print_table(['col'], [('val',)])

        captured = capsys.readouterr()
        assert 'col' in captured.out
        assert 'val' in captured.out


# ---------------------------------------------------------------------------
# _print_table
# ---------------------------------------------------------------------------

class TestInternalPrintTable:
    def test_prints_zero_rows_message(self, capsys):
        db_module._print_table(['id', 'name'], [])

        captured = capsys.readouterr()
        assert captured.out.strip() == '(0 rows)'

    def test_prints_single_row_singular(self, capsys):
        db_module._print_table(['id'], [(1,)])

        captured = capsys.readouterr()
        assert '(1 row)' in captured.out
        # Must NOT say "rows" (plural)
        assert '(1 rows)' not in captured.out

    def test_prints_multiple_rows_plural(self, capsys):
        db_module._print_table(['id'], [(1,), (2,), (3,)])

        captured = capsys.readouterr()
        assert '(3 rows)' in captured.out

    def test_handles_none_values(self, capsys):
        db_module._print_table(['name', 'val'], [('a', None)])

        captured = capsys.readouterr()
        lines = captured.out.split('\n')
        # The data row should have 'a' and an empty cell for None
        data_lines = [l for l in lines if 'a' in l and 'name' not in l]
        assert len(data_lines) >= 1

    def test_column_width_calculation(self, capsys):
        db_module._print_table(
            ['id', 'longername'],
            [('1', 'short'), ('2', 'a-very-long-value')]
        )

        captured = capsys.readouterr()
        lines = captured.out.strip().split('\n')

        # Header line
        assert 'id' in lines[0]
        assert 'longername' in lines[0]
        # Separator line starts with '-'
        assert lines[1].startswith('-')
        assert '-+-' in lines[1]
        # Row count
        assert '(2 rows)' in lines[-1]

    def test_separator_and_header_alignment(self, capsys):
        db_module._print_table(['ab', 'cd'], [('xy', 'zz')])

        captured = capsys.readouterr()
        # Split without stripping to preserve leading whitespace
        lines = [l for l in captured.out.split('\n') if l]
        # Header line starts with space
        assert lines[0].startswith(' ')
        # Separator line starts with dash
        assert lines[1].startswith('-')
        # Data line starts with space
        assert lines[2].startswith(' ')
        # Pipe separators in header and data
        assert ' | ' in lines[0]
        assert ' | ' in lines[2]


# ---------------------------------------------------------------------------
# _fmt_cell
# ---------------------------------------------------------------------------

class TestFmtCell:
    def test_none_returns_empty_string(self):
        assert db_module._fmt_cell(None) == ''

    def test_converts_int_to_string(self):
        assert db_module._fmt_cell(42) == '42'

    def test_converts_string_passthrough(self):
        assert db_module._fmt_cell('hello') == 'hello'

    def test_converts_bool_to_string(self):
        assert db_module._fmt_cell(True) == 'True'

    def test_converts_float_to_string(self):
        assert db_module._fmt_cell(3.14) == '3.14'
