"""Shared fixtures for BDD tests."""
import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from api.errors import register_error_handlers
from api.routes import mount_routes


@pytest.fixture
def app():
    """Create FastAPI application with all routes mounted."""
    application = FastAPI()
    register_error_handlers(application)
    mount_routes(application)
    return application


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def context():
    return {}


@pytest.fixture
def mock_build_repo():
    repo = MagicMock()
    repo.find_by_application.return_value = []
    repo.find_by_component.return_value = None
    repo.find_failing_component_names.return_value = set()
    repo.get_latest_failures.return_value = []
    repo.get_failure_details.return_value = None
    repo.get_component_history.return_value = {'builds': []}
    repo.get_triage_summary.return_value = {'failing_components': []}
    return repo


@pytest.fixture
def mock_conforma_repo():
    repo = MagicMock()
    repo.find_by_application.return_value = []
    repo.find_unresolved_component_names.return_value = set()
    repo.get_violation_summaries.return_value = []
    repo.get_violation_details.return_value = None
    return repo


@pytest.fixture
def mock_triage_repo():
    repo = MagicMock()
    repo.find_by_application.return_value = []
    repo.find_by_id.return_value = None
    repo.find_by_component.return_value = None
    repo.get_report.return_value = []
    repo.get_summary.return_value = {
        'total_items': 0, 'urgent_items': 0, 'new_items': 0,
        'active_items': 0, 'resolved_items': 0,
    }
    repo.build_jira_map.return_value = {}
    return repo


def _make_repo_router(*repos):
    """Build a get_repository side_effect that routes by class name."""
    repo_map = {}
    for repo in repos:
        repo_map[repo._mock_name or 'unknown'] = repo
    def _side_effect(cls):
        name = cls.__name__
        for key, r in repo_map.items():
            if key.lower() in name.lower():
                return r
        return MagicMock()
    return _side_effect


@pytest.fixture
def mock_db():
    """Mock DatabaseConnection returned by _db()."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    db = MagicMock()
    db.connection.return_value = mock_conn
    return db
