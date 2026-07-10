"""Tests for map/backend/actions.py — action detection from IC data."""

import sys
from pathlib import Path

# Ensure map package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "map"))

from backend.actions import (
    IMPROVEMENT,
    REBUILD,
    TRIAGE,
    detect_actions,
)


def test_detect_rebuild_for_timeout():
    ic_data = {
        "failure": {"failed_step": "build-container", "status": "FAILURE"},
        "analysis": {"failure_category": "timeout", "root_cause": "PipelineRunTimeout"},
    }
    actions = detect_actions(ic_data=ic_data, panoramic_data=None, node_id="comp-odh-vllm")
    rebuilds = [a for a in actions if a["type"] == REBUILD]
    assert len(rebuilds) == 1
    assert "odh-vllm" in rebuilds[0]["label"]
    assert rebuilds[0]["params"]["component"] == "odh-vllm"


def test_detect_rebuild_for_transient():
    ic_data = {
        "failure": {"failed_step": "prefetch", "status": "FAILURE"},
        "analysis": {"failure_category": "transient"},
    }
    actions = detect_actions(ic_data=ic_data, panoramic_data=None, node_id="comp-foo")
    assert any(a["type"] == REBUILD for a in actions)


def test_detect_rebuild_timeout_in_step_name():
    ic_data = {
        "failure": {"failed_step": "buildah-oci-ta-timeout", "status": "FAILURE"},
        "analysis": {"failure_category": "build_error"},
    }
    actions = detect_actions(ic_data=ic_data, panoramic_data=None, node_id="comp-bar")
    assert any(a["type"] == REBUILD for a in actions)


def test_no_rebuild_for_code_error():
    ic_data = {
        "failure": {"failed_step": "build-container", "status": "FAILURE"},
        "analysis": {"failure_category": "dependency_issue"},
    }
    actions = detect_actions(ic_data=ic_data, panoramic_data=None, node_id="comp-baz")
    assert not any(a["type"] == REBUILD for a in actions)


def test_detect_triage_for_untriaged_failure():
    ic_data = {
        "failure": {"failed_step": "buildah", "status": "FAILURE"},
        "analysis": {"root_cause": "Go 1.26 mismatch"},
    }
    actions = detect_actions(ic_data=ic_data, panoramic_data=None, node_id="comp-abc")
    triages = [a for a in actions if a["type"] == TRIAGE]
    assert len(triages) == 1
    assert triages[0]["params"]["root_cause"] == "Go 1.26 mismatch"


def test_no_triage_if_already_tracked():
    ic_data = {
        "failure": {"failed_step": "buildah", "status": "FAILURE"},
        "analysis": {"root_cause": "some error"},
        "triage": [{"component": "abc", "status": "active"}],
    }
    actions = detect_actions(ic_data=ic_data, panoramic_data=None, node_id="comp-abc")
    assert not any(a["type"] == TRIAGE for a in actions)


def test_no_triage_substring_match():
    """Ensure 'ab' is not treated as tracked when triage has 'abc'."""
    ic_data = {
        "failure": {"failed_step": "buildah", "status": "FAILURE"},
        "analysis": {"root_cause": "some error"},
        "triage": [{"component": "abc-full-name", "status": "active"}],
    }
    actions = detect_actions(ic_data=ic_data, panoramic_data=None, node_id="comp-abc")
    assert any(a["type"] == TRIAGE for a in actions)


def test_triage_tracked_via_components_list():
    """Match when component is in the 'components' list field."""
    ic_data = {
        "failure": {"failed_step": "buildah", "status": "FAILURE"},
        "analysis": {"root_cause": "some error"},
        "triage": [{"components": ["abc", "def"], "status": "active"}],
    }
    actions = detect_actions(ic_data=ic_data, panoramic_data=None, node_id="comp-abc")
    assert not any(a["type"] == TRIAGE for a in actions)


def test_no_triage_if_no_failure():
    ic_data = {"analysis": {"root_cause": "stale"}}
    actions = detect_actions(ic_data=ic_data, panoramic_data=None, node_id="comp-xyz")
    assert not any(a["type"] == TRIAGE for a in actions)


def test_detect_stale_rebuilds():
    panoramic = {
        "stale": [
            {"component": "comp-a", "commits_behind": 3},
            {"component": "comp-b", "commits_behind": 1},
        ],
    }
    actions = detect_actions(ic_data=None, panoramic_data=panoramic, node_id=None)
    rebuilds = [a for a in actions if a["type"] == REBUILD]
    assert len(rebuilds) == 2
    assert "comp-a" in rebuilds[0]["label"]


def test_stale_list_of_strings():
    panoramic = {"stale": ["alpha", "beta"]}
    actions = detect_actions(ic_data=None, panoramic_data=panoramic, node_id=None)
    rebuilds = [a for a in actions if a["type"] == REBUILD]
    assert len(rebuilds) == 2


def test_stale_capped_at_3():
    panoramic = {
        "stale": [{"component": f"c{i}"} for i in range(10)],
    }
    actions = detect_actions(ic_data=None, panoramic_data=panoramic, node_id=None)
    rebuilds = [a for a in actions if a["type"] == REBUILD]
    assert len(rebuilds) == 3


def test_detect_release_prep_blockers():
    panoramic = {
        "readiness": {
            "verdict": "NOT_READY",
            "checks": [
                {"name": "build_failures", "status": "FAIL"},
                {"name": "conforma", "status": "FAIL"},
                {"name": "fbc_health", "status": "PASS"},
            ],
        },
    }
    actions = detect_actions(ic_data=None, panoramic_data=panoramic, node_id=None)
    improvements = [a for a in actions if a["type"] == IMPROVEMENT]
    assert len(improvements) >= 1
    assert "2 release blocker" in improvements[0]["description"]


def test_no_release_prep_if_ready():
    panoramic = {
        "readiness": {"verdict": "READY", "checks": []},
    }
    actions = detect_actions(ic_data=None, panoramic_data=panoramic, node_id=None)
    improvements = [a for a in actions if a["type"] == IMPROVEMENT]
    assert len(improvements) == 0


def test_detect_health_warnings():
    panoramic = {
        "health_warnings": [
            {"message": "odh-dashboard failure rate increasing"},
            {"message": "nightly build broken for 3 days"},
        ],
    }
    actions = detect_actions(ic_data=None, panoramic_data=panoramic, node_id=None)
    improvements = [a for a in actions if a["type"] == IMPROVEMENT]
    assert len(improvements) == 2


def test_no_actions_with_empty_data():
    actions = detect_actions(ic_data=None, panoramic_data=None, node_id=None)
    assert actions == []


def test_no_actions_for_non_component_node():
    ic_data = {
        "failure": {"failed_step": "build", "status": "FAILURE"},
        "analysis": {"failure_category": "timeout"},
    }
    actions = detect_actions(ic_data=ic_data, panoramic_data=None, node_id="app-rhoai")
    assert not any(a["type"] == REBUILD for a in actions)
    assert not any(a["type"] == TRIAGE for a in actions)


def test_combined_component_and_panoramic():
    ic_data = {
        "failure": {"failed_step": "buildah-timeout", "status": "FAILURE"},
        "analysis": {"failure_category": "timeout", "root_cause": "k8s timeout"},
    }
    panoramic = {
        "stale": [{"component": "other-comp"}],
        "readiness": {
            "verdict": "AT_RISK",
            "checks": [{"name": "stale", "status": "FAIL"}],
        },
    }
    actions = detect_actions(
        ic_data=ic_data, panoramic_data=panoramic, node_id="comp-my-comp",
    )
    assert any(a["type"] == REBUILD and "my-comp" in a["label"] for a in actions)
    assert any(a["type"] == REBUILD and "other-comp" in a["label"] for a in actions)
    assert any(a["type"] == IMPROVEMENT for a in actions)


def test_improvement_has_no_confirmation():
    panoramic = {
        "readiness": {
            "verdict": "NOT_READY",
            "checks": [{"name": "builds", "status": "FAIL"}],
        },
    }
    actions = detect_actions(ic_data=None, panoramic_data=panoramic, node_id=None)
    improvements = [a for a in actions if a["type"] == IMPROVEMENT]
    assert improvements[0]["confirmation"] is None
