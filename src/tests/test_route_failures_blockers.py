"""Tests for list_blockers endpoint with query builder integration."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import register_error_handlers
from api.routes.failures import router


@pytest.fixture
def client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


class TestListBlockersEndpoint:
    @patch('clients.jira_client.JiraClient')
    def test_returns_blockers_with_version_filter(self, mock_jira_cls, client):
        mock_jira = MagicMock()
        mock_jira_cls.return_value = mock_jira
        mock_jira.search_blockers.return_value = [{
            'key': 'RHOAIENG-76252',
            'summary': 'KServe Local Model Cache stuck',
            'status': 'In Progress',
            'status_category': 'indeterminate',
            'assignee': 'Milind Waykole',
            'created': '2026-07-13T04:11:39.458+0000',
            'updated': '2026-07-13T06:15:29.694+0000',
            'resolution': None,
            'labels': [],
        }]
        resp = client.get('/api/v1/applications/rhoai-v3-5/blockers')
        assert resp.status_code == 200
        data = resp.json()
        assert data['open_blockers'] >= 1
        call_args = mock_jira.search_blockers.call_args
        jql = call_args[1].get('jql_override') or call_args[0][0] if call_args[0] else ''
        assert 'rhoai-3.5' in str(call_args)

    @patch('clients.jira_client.JiraClient')
    def test_signoff_tickets_categorized(self, mock_jira_cls, client):
        mock_jira = MagicMock()
        mock_jira_cls.return_value = mock_jira
        mock_jira.search_blockers.return_value = [{
            'key': 'RHOAIENG-68842',
            'summary': '[RHOAI 3.5.0-EA2] Product Sign Off - AI Core Platform Team',
            'status': 'In Progress',
            'status_category': 'indeterminate',
            'assignee': 'Adrian Sanz Gomiz',
            'created': '2026-06-14T22:03:25.383+0000',
            'updated': '2026-07-13T12:17:14.419+0000',
            'resolution': None,
            'labels': ['blocker-process-notified'],
        }]
        resp = client.get('/api/v1/applications/rhoai-v3-5-ea-2/blockers')
        assert resp.status_code == 200
        data = resp.json()
        assert data['blockers'][0]['category'] == 'signoff'
        assert data['signoff_blockers'] == 1

    @patch('clients.jira_client.JiraClient')
    def test_jira_unavailable_returns_signal(self, mock_jira_cls, client):
        mock_jira_cls.side_effect = Exception('Connection refused')
        resp = client.get('/api/v1/applications/rhoai-v3-5/blockers')
        assert resp.status_code == 200
        data = resp.json()
        assert data['total_blockers'] == 0
        assert any('unavailable' in s for s in data['critical_signals'])
