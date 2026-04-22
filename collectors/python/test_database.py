"""Tests for Database class with mocked connections."""

import pytest
from unittest.mock import patch, MagicMock
from database import Database
from config import DatabaseConfig
from models import ScanResult


@pytest.fixture
def db():
    return Database(DatabaseConfig(
        host="localhost", port=5432, user="test",
        password="test", database="testdb"
    ))


# --- connection context manager ---

@patch('database.psycopg2.connect')
def test_connection_commits_on_success(mock_connect, db):
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn

    with db.connection() as conn:
        conn.cursor()

    mock_conn.commit.assert_called_once()
    mock_conn.close.assert_called_once()


@patch('database.psycopg2.connect')
def test_connection_rolls_back_on_error(mock_connect, db):
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn

    with pytest.raises(ValueError):
        with db.connection():
            raise ValueError("test error")

    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()


@patch('database.psycopg2.connect')
def test_connection_string_passed(mock_connect, db):
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn

    with db.connection():
        pass

    conn_str = mock_connect.call_args[0][0]
    assert "host=localhost" in conn_str
    assert "dbname=testdb" in conn_str


# --- create_scan ---

@patch('database.psycopg2.connect')
def test_create_scan_returns_uuid(mock_connect, db):
    mock_connect.return_value = MagicMock()
    scan_id = db.create_scan(scan_type='test', scan_mode='full')
    assert isinstance(scan_id, str)
    assert len(scan_id) == 36


@patch('database.psycopg2.connect')
def test_create_scan_inserts_running(mock_connect, db):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    db.create_scan(scan_type='python-comprehensive', scan_mode='full')
    sql, params = mock_cursor.execute.call_args[0]
    assert 'INSERT INTO scan_history' in sql
    assert params[1] == 'python-comprehensive'
    assert params[2] == 'full'
    assert params[3] == 'running'


# --- complete_scan ---

@patch('database.psycopg2.connect')
def test_complete_scan_updates(mock_connect, db):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    result = ScanResult(
        scan_id='test-id', components_scanned=5, failures_found=2,
        new_failures=1, logs_fetched=2, duration_seconds=10.5
    )
    db.complete_scan('test-id', result)

    sql, params = mock_cursor.execute.call_args[0]
    assert 'UPDATE scan_history' in sql
    assert params[0] == 10.5
    assert params[1] == 5
    assert params[5] == 'test-id'


# --- pipelinerun_exists ---

@patch('database.psycopg2.connect')
def test_pipelinerun_exists_true(mock_connect, db):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (1,)
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    assert db.pipelinerun_exists('pr-123') is True


@patch('database.psycopg2.connect')
def test_pipelinerun_exists_false(mock_connect, db):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    assert db.pipelinerun_exists('pr-nonexistent') is False


# --- update_component_health ---

@patch('database.psycopg2.connect')
def test_update_component_health(mock_connect, db):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    db.update_component_health('my-component')
    sql, params = mock_cursor.execute.call_args[0]
    assert 'update_component_health' in sql
    assert params == ('my-component',)
