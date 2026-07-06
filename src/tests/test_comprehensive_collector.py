"""Tests for BuildFailureCollector pure functions."""

from unittest.mock import MagicMock

import pytest

from collectors.build_failure_collector import BuildFailureCollector
from config import CollectorConfig, DatabaseConfig, KubernetesConfig


@pytest.fixture
def collector():
    config = CollectorConfig(
        db=DatabaseConfig(
            host="localhost", port=5432, user="test",
            password="test", database="testdb"
        ),
        k8s=KubernetesConfig(
            namespace="test-ns", application_name="test-app",
            kubearchive_api_url="https://kubearchive.example.com"
        )
    )
    return BuildFailureCollector(
        config,
        db=MagicMock(),
        build_repo=MagicMock(),
        kubearchive=MagicMock(),
        k8s=MagicMock(),
        tekton_results=MagicMock(),
        unified=MagicMock(),
    )


# --- extract_error_messages ---

def test_error_messages_none_logs(collector):
    assert collector.extract_error_messages(None) == (None, None)


def test_error_messages_empty_logs(collector):
    assert collector.extract_error_messages("") == (None, None)


def test_error_messages_build_error(collector):
    logs = "Step 3/8: RUN pip install\nERROR: Could not find package foo==1.2.3\nDone"
    msg, typ = collector.extract_error_messages(logs)
    assert typ == "Build Error"
    assert "Could not find package foo" in msg


def test_error_messages_test_failure(collector):
    logs = "Running tests...\nFAILED: test_auth_login - assertion error\nFinished"
    msg, typ = collector.extract_error_messages(logs)
    assert typ == "Test Failure"
    assert "test_auth_login" in msg


def test_error_messages_exit_code(collector):
    msg, typ = collector.extract_error_messages("Process terminated with exit code 137")
    assert typ == "Exit Code"
    assert "137" in msg


def test_error_messages_timeout(collector):
    _, typ = collector.extract_error_messages("Build timed out after 3600 seconds")
    assert typ == "Timeout"


def test_error_messages_fatal(collector):
    msg, typ = collector.extract_error_messages("fatal: repository 'https://github.com/x/y' not found")
    assert typ == "Fatal Error"
    assert "repository" in msg


def test_error_messages_resource_not_found(collector):
    _, typ = collector.extract_error_messages("GET /api/v1/thing returned 404")
    assert typ == "Resource Not Found"


def test_error_messages_no_match(collector):
    assert collector.extract_error_messages("Step 1: OK\nBuild succeeded.") == (None, None)


def test_error_messages_long_truncated(collector):
    logs = f"ERROR: {'x' * 1000}"
    msg, _ = collector.extract_error_messages(logs)
    assert len(msg) <= 510


# --- extract_failed_step_from_logs ---

def test_failed_step_none_logs(collector):
    assert collector.extract_failed_step_from_logs(None) is None


def test_failed_step_empty_logs(collector):
    assert collector.extract_failed_step_from_logs("") is None


def test_failed_step_found(collector):
    logs = "output\n===== TaskRun: build-container / Step: build =====\nERROR"
    assert collector.extract_failed_step_from_logs(logs) == "build"


def test_failed_step_no_marker(collector):
    assert collector.extract_failed_step_from_logs("random logs") is None


# --- extract_all_details ---

def test_all_details_none(collector):
    assert collector.extract_all_details(None) == {}


def test_all_details_empty(collector):
    result = collector.extract_all_details({})
    assert isinstance(result, dict)
    assert result.get('commit_sha') is None


def test_all_details_full_pr(collector):
    pr_data = {
        'metadata': {
            'name': 'pr-123',
            'namespace': 'test-ns',
            'annotations': {
                'build.appstudio.redhat.com/commit_sha': 'abc123def456',
                'pipelinesascode.tekton.dev/sha-url': 'https://github.com/org/repo/commit/abc123def456',
                'pipelinesascode.tekton.dev/sha-title': 'Fix the thing',
                'pipelinesascode.tekton.dev/sender': 'testuser',
                'pipelinesascode.tekton.dev/branch': 'main',
            }
        },
        'spec': {'params': [
            {'name': 'git-url', 'value': 'https://github.com/org/repo.git'},
        ]},
        'status': {
            'startTime': '2026-04-20T10:00:00Z',
            'completionTime': '2026-04-20T10:15:00Z',
            'childReferences': []
        }
    }
    result = collector.extract_all_details(pr_data)
    assert result['commit_sha'] == 'abc123def456'
    assert result['commit_short_sha'] == 'abc123de'
    assert result['commit_message'] == 'Fix the thing'
    assert result['commit_author'] == 'testuser'
    assert result['branch'] == 'main'
    assert result['start_time'] == '2026-04-20T10:00:00Z'


def test_all_details_sha_fallback_annotation(collector):
    pr_data = {
        'metadata': {'name': 'pr-456', 'annotations': {
            'pipelinesascode.tekton.dev/sha': 'fallback789'
        }},
        'spec': {'params': []},
        'status': {}
    }
    assert collector.extract_all_details(pr_data)['commit_sha'] == 'fallback789'


def test_all_details_sha_fallback_params(collector):
    pr_data = {
        'metadata': {'name': 'pr-789', 'annotations': {}},
        'spec': {'params': [{'name': 'revision', 'value': 'param_sha'}]},
        'status': {}
    }
    assert collector.extract_all_details(pr_data)['commit_sha'] == 'param_sha'


# --- extract_pr_number ---

def test_pr_number_from_url(collector):
    annotations = {
        'pipelinesascode.tekton.dev/pull-request-url': 'https://github.com/org/repo/pull/42'
    }
    assert collector.extract_pr_number(annotations) == 42


def test_pr_number_missing(collector):
    assert collector.extract_pr_number({}) is None


def test_pr_number_malformed_url(collector):
    annotations = {
        'pipelinesascode.tekton.dev/pull-request-url': 'https://github.com/org/repo/issues/10'
    }
    assert collector.extract_pr_number(annotations) is None
