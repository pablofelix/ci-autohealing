"""Tests for onboarding status endpoints and helper functions."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import register_error_handlers
from api.routes.onboarding import (
    _build_component_status,
    _check_branch,
    _check_builds,
    _check_container_image,
    _check_last_built,
    _check_nudges,
    _check_pac,
    _check_repo,
    _detect_component_type,
    _onboarding_score,
    router,
)


@pytest.fixture
def client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helper: _check_repo
# ---------------------------------------------------------------------------

def test_check_repo_pass():
    result = _check_repo({'repository_url': 'https://github.com/org/repo'})
    assert result['status'] == 'PASS'
    assert 'github.com' in result['detail']


def test_check_repo_fail_empty():
    result = _check_repo({'repository_url': ''})
    assert result['status'] == 'FAIL'
    assert 'fix' in result


def test_check_repo_fail_missing():
    result = _check_repo({})
    assert result['status'] == 'FAIL'


# ---------------------------------------------------------------------------
# Helper: _check_branch
# ---------------------------------------------------------------------------

def test_check_branch_pass():
    result = _check_branch({'branch': 'rhoai-2.16'})
    assert result['status'] == 'PASS'
    assert result['detail'] == 'rhoai-2.16'


def test_check_branch_fail():
    result = _check_branch({'branch': ''})
    assert result['status'] == 'FAIL'
    assert 'fix' in result


# ---------------------------------------------------------------------------
# Helper: _check_container_image
# ---------------------------------------------------------------------------

def test_check_container_image_pass():
    result = _check_container_image({'container_image': 'quay.io/org/img:latest'})
    assert result['status'] == 'PASS'


def test_check_container_image_fail():
    result = _check_container_image({})
    assert result['status'] == 'FAIL'
    assert 'fix' in result


# ---------------------------------------------------------------------------
# Helper: _check_pac
# ---------------------------------------------------------------------------

def test_check_pac_pass():
    comp = {'repository_url': 'https://github.com/org/repo'}
    pac_repos = [{'url': 'https://github.com/org/repo'}]
    result = _check_pac(comp, pac_repos)
    assert result['status'] == 'PASS'


def test_check_pac_warn_no_match():
    comp = {'repository_url': 'https://github.com/org/repo'}
    pac_repos = [{'url': 'https://github.com/other/repo'}]
    result = _check_pac(comp, pac_repos)
    assert result['status'] == 'WARN'
    assert 'fix' in result


def test_check_pac_skip_no_repo():
    result = _check_pac({}, [])
    assert result['status'] == 'SKIP'


# ---------------------------------------------------------------------------
# Helper: _check_builds
# ---------------------------------------------------------------------------

def test_check_builds_pass_succeeded():
    comp = {'name': 'my-comp'}
    runs = {'my-comp': [{'status': 'succeeded'}]}
    result = _check_builds(comp, runs)
    assert result['status'] == 'PASS'


def test_check_builds_fail_no_runs():
    comp = {'name': 'my-comp'}
    result = _check_builds(comp, {})
    assert result['status'] == 'FAIL'
    assert 'fix' in result


def test_check_builds_pass_no_runs_but_last_built():
    comp = {'name': 'my-comp', 'last_built_commit': 'abc123def456'}
    result = _check_builds(comp, {})
    assert result['status'] == 'PASS'
    assert 'abc123def456' in result['detail']


def test_check_builds_warn_running():
    comp = {'name': 'my-comp'}
    runs = {'my-comp': [{'status': 'running'}]}
    result = _check_builds(comp, runs)
    assert result['status'] == 'WARN'


def test_check_builds_fail_latest_failed():
    comp = {'name': 'my-comp'}
    runs = {'my-comp': [{'status': 'failed'}]}
    result = _check_builds(comp, runs)
    assert result['status'] == 'FAIL'
    assert 'fix' in result


# ---------------------------------------------------------------------------
# Helper: _check_nudges
# ---------------------------------------------------------------------------

def test_check_nudges_pass():
    result = _check_nudges({'nudges': ['comp-a', 'comp-b']})
    assert result['status'] == 'PASS'
    assert '2' in result['detail']


def test_check_nudges_info_empty():
    result = _check_nudges({'nudges': []})
    assert result['status'] == 'INFO'


def test_check_nudges_info_missing():
    result = _check_nudges({})
    assert result['status'] == 'INFO'


# ---------------------------------------------------------------------------
# Helper: _check_last_built
# ---------------------------------------------------------------------------

def test_check_last_built_pass():
    result = _check_last_built({'last_built_commit': 'abc123def456789'})
    assert result['status'] == 'PASS'
    assert 'abc123def456' in result['detail']


def test_check_last_built_warn():
    result = _check_last_built({})
    assert result['status'] == 'WARN'
    assert 'fix' in result


# ---------------------------------------------------------------------------
# Helper: _onboarding_score
# ---------------------------------------------------------------------------

def test_score_all_pass():
    checks = {
        'repository': {'status': 'PASS'},
        'branch': {'status': 'PASS'},
        'container_image': {'status': 'PASS'},
        'pac': {'status': 'PASS'},
        'builds': {'status': 'PASS'},
        'last_built': {'status': 'PASS'},
        'nudges': {'status': 'PASS'},
    }
    assert _onboarding_score(checks) == 100


def test_score_all_fail():
    checks = {
        'repository': {'status': 'FAIL'},
        'branch': {'status': 'FAIL'},
        'container_image': {'status': 'FAIL'},
        'pac': {'status': 'FAIL'},
        'builds': {'status': 'FAIL'},
        'last_built': {'status': 'FAIL'},
        'nudges': {'status': 'FAIL'},
    }
    assert _onboarding_score(checks) == 0


def test_score_mixed():
    checks = {
        'repository': {'status': 'PASS'},      # 20
        'branch': {'status': 'PASS'},           # 15
        'container_image': {'status': 'FAIL'},  # 0
        'pac': {'status': 'WARN'},              # 7
        'builds': {'status': 'PASS'},           # 20
        'last_built': {'status': 'WARN'},       # 5
        'nudges': {'status': 'INFO'},           # 2
    }
    assert _onboarding_score(checks) == 69


def test_score_empty_checks():
    assert _onboarding_score({}) == 0


# ---------------------------------------------------------------------------
# Helper: _detect_component_type
# ---------------------------------------------------------------------------

def test_detect_type_odh_by_repo():
    comp = {'repository_url': 'https://github.com/opendatahub-io/something'}
    assert _detect_component_type(comp) == 'odh'


def test_detect_type_odh_by_name():
    comp = {'name': 'odh-dashboard', 'repository_url': ''}
    assert _detect_component_type(comp) == 'odh'


def test_detect_type_rhoai_by_repo():
    comp = {'repository_url': 'https://github.com/red-hat-data-services/model-registry'}
    assert _detect_component_type(comp) == 'rhoai'


def test_detect_type_rhoai_by_name():
    comp = {'name': 'rhoai-dashboard', 'repository_url': ''}
    assert _detect_component_type(comp) == 'rhoai'


def test_detect_type_rhods_by_name():
    comp = {'name': 'rhods-operator', 'repository_url': ''}
    assert _detect_component_type(comp) == 'rhoai'


def test_detect_type_unknown():
    comp = {'name': 'my-component', 'repository_url': 'https://github.com/acme/foo'}
    assert _detect_component_type(comp) == 'unknown'


# ---------------------------------------------------------------------------
# Helper: _build_component_status
# ---------------------------------------------------------------------------

def _make_full_comp(**overrides):
    base = {
        'name': 'test-comp',
        'application': 'test-app',
        'repository_url': 'https://github.com/org/repo',
        'branch': 'main',
        'container_image': 'quay.io/org/img',
        'last_built_commit': 'abc123def456789',
        'nudges': ['comp-a'],
    }
    base.update(overrides)
    return base


def test_build_status_complete():
    comp = _make_full_comp()
    pac_repos = [{'url': 'https://github.com/org/repo'}]
    runs = {'test-comp': [{'status': 'succeeded'}]}
    result = _build_component_status(comp, pac_repos, runs)
    assert result['overall'] == 'complete'
    assert result['score'] == 100
    assert result['component'] == 'test-comp'
    assert result['failing'] == []
    assert result['warnings'] == []


def test_build_status_incomplete():
    comp = {'name': 'bare', 'application': 'app'}
    result = _build_component_status(comp, [], {})
    assert result['overall'] == 'incomplete'
    assert result['score'] < 100
    assert len(result['failing']) > 0


def test_build_status_partial():
    comp = _make_full_comp()
    pac_repos = []  # no PaC match => WARN
    runs = {'test-comp': [{'status': 'succeeded'}]}
    result = _build_component_status(comp, pac_repos, runs)
    assert result['overall'] == 'partial'
    assert 'pac' in result['warnings']
    assert result['failing'] == []


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/onboarding — NAMESPACE not set
# ---------------------------------------------------------------------------

def test_get_onboarding_no_namespace(client, monkeypatch):
    monkeypatch.delenv('NAMESPACE', raising=False)
    resp = client.get("/api/v1/applications/test-app/onboarding")
    data = resp.json()
    assert 'error' in data
    assert 'NAMESPACE' in data['error']


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/onboarding — with mocked K8s
# ---------------------------------------------------------------------------

def test_get_onboarding_with_components(client, monkeypatch):
    monkeypatch.setenv('NAMESPACE', 'test-ns')

    mock_k8s = MagicMock()
    mock_k8s.list_components.return_value = [
        {'name': 'comp-a', 'application': 'test-app',
         'repository_url': 'https://github.com/org/a', 'branch': 'main',
         'container_image': 'quay.io/org/a', 'last_built_commit': 'abc',
         'nudges': []},
    ]
    mock_k8s.list_pac_repositories.return_value = []

    with patch('clients.kubernetes.KubernetesClient', return_value=mock_k8s), \
         patch('api.routes.onboarding._build_jira_map', return_value={}):
        resp = client.get("/api/v1/applications/test-app/onboarding")

    data = resp.json()
    assert data['application'] == 'test-app'
    assert data['total'] == 1
    assert len(data['components']) == 1


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/onboarding/{component} — NAMESPACE not set
# ---------------------------------------------------------------------------

def test_get_component_onboarding_no_namespace(client, monkeypatch):
    monkeypatch.delenv('NAMESPACE', raising=False)
    resp = client.get("/api/v1/applications/test-app/onboarding/my-comp")
    data = resp.json()
    assert 'error' in data
    assert 'NAMESPACE' in data['error']


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/onboarding/{component} — component found
# ---------------------------------------------------------------------------

def test_get_component_onboarding_found(client, monkeypatch):
    monkeypatch.setenv('NAMESPACE', 'test-ns')

    mock_k8s = MagicMock()
    mock_k8s.list_components.return_value = [
        {'name': 'my-comp', 'application': 'test-app',
         'repository_url': 'https://github.com/org/repo', 'branch': 'main',
         'container_image': 'quay.io/org/img', 'last_built_commit': 'abc',
         'nudges': [], 'created_at': '2026-01-01'},
    ]
    mock_k8s.list_pac_repositories.return_value = []
    mock_k8s.list_recent_pipelineruns.return_value = [
        {'status': 'succeeded', 'name': 'run-1', 'created_at': '2026-01-02'},
    ]

    mock_kfx = MagicMock()
    mock_kfx.get_release_plan_admissions.return_value = []
    mock_kfx.get_dependency_updates.return_value = []

    with patch('clients.kubernetes.KubernetesClient', return_value=mock_k8s), \
         patch('clients.konflux_client.KonfluxClient', return_value=mock_kfx), \
         patch('api.routes.onboarding._enrich_with_jira'):
        resp = client.get("/api/v1/applications/test-app/onboarding/my-comp")

    data = resp.json()
    assert data['component'] == 'my-comp'
    assert 'phase' in data


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/onboarding/{component} — not found
# ---------------------------------------------------------------------------

def test_get_component_onboarding_not_found(client, monkeypatch):
    monkeypatch.setenv('NAMESPACE', 'test-ns')

    mock_k8s = MagicMock()
    mock_k8s.list_components.return_value = []
    mock_k8s.list_pac_repositories.return_value = []

    with patch('clients.kubernetes.KubernetesClient', return_value=mock_k8s):
        resp = client.get("/api/v1/applications/test-app/onboarding/missing")

    data = resp.json()
    assert 'error' in data
    assert 'not found' in data['error']


# ---------------------------------------------------------------------------
# Endpoint: POST /applications/{app}/onboarding/{component}/analyze
# ---------------------------------------------------------------------------

def test_analyze_no_namespace(client, monkeypatch):
    monkeypatch.delenv('NAMESPACE', raising=False)
    resp = client.post("/api/v1/applications/test-app/onboarding/comp/analyze")
    data = resp.json()
    assert 'error' in data
