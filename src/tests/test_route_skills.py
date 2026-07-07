"""Tests for skill registry endpoints."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.skills import router


@pytest.fixture
def mock_metadata():
    return SimpleNamespace(
        description="Test skill",
        category="testing",
        allowed_tools="Bash",
        user_invocable=True,
    )


@pytest.fixture
def mock_entry(mock_metadata):
    return SimpleNamespace(
        qualified_name="test:my-skill",
        name="my-skill",
        source="test",
        status="active",
        tags=["ci"],
        path="/tmp/skills/test.sh",
        metadata=mock_metadata,
    )


@pytest.fixture
def mock_registry(mock_entry):
    registry = MagicMock()
    registry.list_skills.return_value = [mock_entry]
    registry.get_skill.return_value = mock_entry
    registry.list_sources.return_value = [
        SimpleNamespace(name="test", url="https://github.com/test/repo", commit="abc123"),
    ]
    registry.get_run_history.return_value = [
        {"skill_name": "my-skill", "status": "success", "exit_code": 0},
    ]
    return registry


@pytest.fixture
def client(mock_registry):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    with patch("api.routes.skills._registry", return_value=mock_registry):
        yield TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /skills
# ---------------------------------------------------------------------------


def test_list_skills(client, mock_registry):
    resp = client.get("/api/v1/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["qualified_name"] == "test:my-skill"
    assert data[0]["name"] == "my-skill"
    assert data[0]["source"] == "test"
    assert data[0]["description"] == "Test skill"
    assert data[0]["tags"] == ["ci"]
    assert data[0]["user_invocable"] is True
    mock_registry.list_skills.assert_called_once_with(tag=None, source=None)


def test_list_skills_with_tag_filter(client, mock_registry):
    client.get("/api/v1/skills?tag=ci")
    mock_registry.list_skills.assert_called_once_with(tag="ci", source=None)


def test_list_skills_with_source_filter(client, mock_registry):
    client.get("/api/v1/skills?source=test")
    mock_registry.list_skills.assert_called_once_with(tag=None, source="test")


# ---------------------------------------------------------------------------
# GET /skills/sources
# ---------------------------------------------------------------------------


def test_list_skill_sources(client, mock_registry):
    resp = client.get("/api/v1/skills/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "test"
    assert data[0]["url"] == "https://github.com/test/repo"
    assert data[0]["commit"] == "abc123"
    assert data[0]["skill_count"] == 1


# ---------------------------------------------------------------------------
# GET /skills/{name}
# ---------------------------------------------------------------------------


def test_get_skill_found(client, mock_registry):
    resp = client.get("/api/v1/skills/my-skill")
    assert resp.status_code == 200
    data = resp.json()
    assert data["qualified_name"] == "test:my-skill"
    assert data["name"] == "my-skill"
    assert data["category"] == "testing"
    mock_registry.get_skill.assert_called_once_with("my-skill")


def test_get_skill_not_found(client, mock_registry):
    mock_registry.get_skill.return_value = None
    resp = client.get("/api/v1/skills/nonexistent")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_get_skill_ambiguous(client, mock_registry):
    mock_registry.get_skill.side_effect = KeyError("Ambiguous name 'run': matches a:run, b:run")
    resp = client.get("/api/v1/skills/run")
    assert resp.status_code == 409
    assert "Ambiguous" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /skills/{name}/validate
# ---------------------------------------------------------------------------


def test_validate_skill(client, mock_registry):
    mock_validation = SimpleNamespace(
        skill_name="my-skill",
        passed=True,
        critical_count=0,
        warning_count=1,
        findings=[
            SimpleNamespace(
                severity="warning",
                check="no-secrets",
                message="Possible token in env",
                file="test.sh",
                line=10,
            ),
        ],
    )
    mock_validator_cls = MagicMock()
    mock_validator_cls.return_value.validate.return_value = mock_validation

    with patch("api.routes.skills.SkillValidator", mock_validator_cls, create=True), \
         patch.dict("sys.modules", {"skills.validator": MagicMock(SkillValidator=mock_validator_cls)}):
        resp = client.get("/api/v1/skills/my-skill/validate")

    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_name"] == "my-skill"
    assert data["passed"] is True
    assert data["warning_count"] == 1
    assert len(data["findings"]) == 1
    assert data["findings"][0]["severity"] == "warning"


def test_validate_skill_not_found(client, mock_registry):
    mock_registry.get_skill.return_value = None
    resp = client.get("/api/v1/skills/nonexistent/validate")
    assert resp.status_code == 404


def test_validate_skill_ambiguous(client, mock_registry):
    mock_registry.get_skill.side_effect = KeyError("Ambiguous")
    resp = client.get("/api/v1/skills/run/validate")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /skills/{name}/prerequisites
# ---------------------------------------------------------------------------


def test_check_prerequisites(client, mock_registry):
    mock_prereqs = {
        "status": "ok",
        "tools": {"bash": True, "oc": False},
        "env": {"KUBECONFIG": True},
    }
    mock_check_fn = MagicMock(return_value=mock_prereqs)

    with patch.dict("sys.modules", {"skills.validator": MagicMock(check_prerequisites=mock_check_fn)}):
        resp = client.get("/api/v1/skills/my-skill/prerequisites")

    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_name"] == "test:my-skill"
    assert data["status"] == "ok"
    assert data["tools"]["bash"] is True
    assert data["tools"]["oc"] is False
    assert data["env"]["KUBECONFIG"] is True


def test_check_prerequisites_not_found(client, mock_registry):
    mock_registry.get_skill.return_value = None
    resp = client.get("/api/v1/skills/nonexistent/prerequisites")
    assert resp.status_code == 404


def test_check_prerequisites_ambiguous(client, mock_registry):
    mock_registry.get_skill.side_effect = KeyError("Ambiguous")
    resp = client.get("/api/v1/skills/run/prerequisites")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /skills/{name}/run
# ---------------------------------------------------------------------------


def test_run_skill_dry_run(client, mock_registry):
    mock_assessment = SimpleNamespace(
        level="low", reasons=[], security_warnings=[],
    )
    mock_result = SimpleNamespace(
        skill_name="my-skill",
        status="success",
        exit_code=0,
        stdout="output",
        stderr="",
        duration_seconds=1.5,
        steps_executed=3,
        steps_total=3,
        dry_run_steps=["step1", "step2", "step3"],
    )
    mock_executor_cls = MagicMock()
    mock_executor_inst = mock_executor_cls.return_value
    mock_executor_inst.assess.return_value = mock_assessment
    mock_executor_inst.execute.return_value = mock_result

    with patch.dict("sys.modules", {"skills.executor": MagicMock(SkillExecutor=mock_executor_cls)}):
        resp = client.post(
            "/api/v1/skills/my-skill/run",
            json={"dry_run": True, "params": {}, "timeout": 300},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_name"] == "my-skill"
    assert data["status"] == "success"
    assert data["exit_code"] == 0
    assert data["stdout"] == "output"
    assert data["risk_level"] == "low"
    assert data["steps_executed"] == 3
    assert data["dry_run_steps"] == ["step1", "step2", "step3"]
    mock_registry.record_run.assert_called_once_with(mock_result)


def test_run_skill_not_found(client, mock_registry):
    mock_registry.get_skill.return_value = None
    resp = client.post("/api/v1/skills/nonexistent/run", json={"dry_run": True})
    assert resp.status_code == 404


def test_run_skill_ambiguous(client, mock_registry):
    mock_registry.get_skill.side_effect = KeyError("Ambiguous")
    resp = client.post("/api/v1/skills/run/run", json={"dry_run": True})
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /skills/runs — route shadowed by /skills/{name}, test function directly
# ---------------------------------------------------------------------------
# NOTE: The /skills/runs endpoint is defined after /skills/{name} in the
# router, so FastAPI matches GET /skills/runs as get_skill(name="runs").
# These tests call list_skill_runs() directly to verify its logic.


def test_list_skill_runs_direct(mock_registry):
    """Test list_skill_runs function directly (route is shadowed by /skills/{name})."""
    from api.routes.skills import list_skill_runs

    with patch("api.routes.skills._registry", return_value=mock_registry):
        result = list_skill_runs(skill=None, limit=20)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["skill_name"] == "my-skill"
    assert result[0]["status"] == "success"
    mock_registry.get_run_history.assert_called_once_with(skill_name=None, limit=20)


def test_list_skill_runs_with_filter_direct(mock_registry):
    """Test list_skill_runs with skill filter and custom limit."""
    from api.routes.skills import list_skill_runs

    with patch("api.routes.skills._registry", return_value=mock_registry):
        list_skill_runs(skill="my-skill", limit=5)

    mock_registry.get_run_history.assert_called_once_with(skill_name="my-skill", limit=5)


def test_list_skill_runs_error_returns_empty(mock_registry):
    """Test that exceptions from get_run_history are swallowed, returning []."""
    from api.routes.skills import list_skill_runs

    mock_registry.get_run_history.side_effect = RuntimeError("db error")
    with patch("api.routes.skills._registry", return_value=mock_registry):
        result = list_skill_runs(skill=None, limit=20)

    assert result == []
