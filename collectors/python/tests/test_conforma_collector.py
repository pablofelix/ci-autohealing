"""Tests for ConformaViolationCollector parsing functions."""

import pytest
from unittest.mock import MagicMock, patch
from collectors.conforma_violation_collector import ConformaViolationCollector
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
    return ConformaViolationCollector(
        config,
        db=MagicMock(),
        conforma_repo=MagicMock(),
        kubearchive=MagicMock(),
        k8s=MagicMock(),
    )


# --- get_verify_taskrun ---

def test_verify_taskrun_found(collector):
    pr_data = {
        'status': {
            'childReferences': [
                {'kind': 'TaskRun', 'pipelineTaskName': 'init', 'name': 'tr-init'},
                {'kind': 'TaskRun', 'pipelineTaskName': 'verify', 'name': 'tr-verify-456'},
                {'kind': 'TaskRun', 'pipelineTaskName': 'report', 'name': 'tr-report'},
            ]
        }
    }
    assert collector.get_verify_taskrun(pr_data) == 'tr-verify-456'


def test_verify_taskrun_missing(collector):
    pr_data = {'status': {'childReferences': [
        {'kind': 'TaskRun', 'pipelineTaskName': 'init', 'name': 'tr-init'},
    ]}}
    assert collector.get_verify_taskrun(pr_data) is None


def test_verify_taskrun_empty_refs(collector):
    assert collector.get_verify_taskrun({'status': {'childReferences': []}}) is None


def test_verify_taskrun_no_status(collector):
    assert collector.get_verify_taskrun({}) is None


def test_verify_taskrun_skips_non_taskrun(collector):
    pr_data = {'status': {'childReferences': [
        {'kind': 'Run', 'pipelineTaskName': 'verify', 'name': 'run-verify'},
    ]}}
    assert collector.get_verify_taskrun(pr_data) is None


# --- extract_component_info ---

def test_component_info_full(collector):
    collector.get_component_repo_url = MagicMock(return_value='')
    pr_data = {
        'metadata': {
            'labels': {'appstudio.openshift.io/snapshot': 'snap-abc'},
            'annotations': {
                'pipelinesascode.tekton.dev/repo-url': 'https://github.com/org/repo',
                'build.appstudio.redhat.com/commit_sha': 'deadbeef1234',
            }
        },
        'spec': {'params': [{'name': 'IMAGES', 'value': 'quay.io/org/image:tag'}]}
    }
    result = collector.extract_component_info(pr_data, 'comp')
    assert result['snapshot_name'] == 'snap-abc'
    assert result['container_image'] == 'quay.io/org/image:tag'
    assert result['repository_url'] == 'https://github.com/org/repo'
    assert result['commit_sha'] == 'deadbeef1234'
    assert 'deadbeef1234' in result['commit_url']


def test_component_info_no_image(collector):
    collector.get_component_repo_url = MagicMock(return_value='')
    pr_data = {
        'metadata': {'labels': {}, 'annotations': {}},
        'spec': {'params': []}
    }
    assert collector.extract_component_info(pr_data, 'comp')['container_image'] == ''


def test_component_info_sha_fallback(collector):
    collector.get_component_repo_url = MagicMock(return_value='')
    pr_data = {
        'metadata': {
            'labels': {},
            'annotations': {
                'pipelinesascode.tekton.dev/sha': 'fallbacksha',
                'pipelinesascode.tekton.dev/repo-url': 'https://github.com/org/repo',
            }
        },
        'spec': {'params': []}
    }
    assert collector.extract_component_info(pr_data, 'comp')['commit_sha'] == 'fallbacksha'


def test_component_info_repo_lookup_fallback(collector):
    collector.get_component_repo_url = MagicMock(return_value='https://github.com/org/fallback')
    pr_data = {
        'metadata': {'labels': {}, 'annotations': {}},
        'spec': {'params': []}
    }
    assert collector.extract_component_info(pr_data, 'comp')['repository_url'] == 'https://github.com/org/fallback'


def test_component_info_strips_git_suffix(collector):
    collector.get_component_repo_url = MagicMock(return_value='')
    pr_data = {
        'metadata': {
            'labels': {},
            'annotations': {
                'pipelinesascode.tekton.dev/repo-url': 'https://github.com/org/repo.git',
                'build.appstudio.redhat.com/commit_sha': 'abc123',
            }
        },
        'spec': {'params': []}
    }
    result = collector.extract_component_info(pr_data, 'comp')
    assert result['commit_url'] == 'https://github.com/org/repo/commit/abc123'


# --- get_failing_conforma_pipelineruns ---

def _make_pr(component, scenario, timestamp, status):
    return {
        'metadata': {
            'name': f'pr-{component}-{timestamp}',
            'uid': f'uid-{component}-{timestamp}',
            'creationTimestamp': timestamp,
            'labels': {
                'appstudio.openshift.io/component': component,
                'test.appstudio.openshift.io/scenario': scenario,
                'pipelines.appstudio.openshift.io/type': 'test',
            }
        },
        'status': {'conditions': [{'status': status}]}
    }


@patch('subprocess.run')
def test_failing_pr_newer_pass_clears(mock_run, collector):
    """Component with failure then pass should NOT appear as failing."""
    mock_run.return_value = MagicMock(returncode=1)
    prs = [
        _make_pr('comp-a', 'conforma-check', '2026-04-10T10:00:00Z', 'False'),
        _make_pr('comp-a', 'conforma-check', '2026-04-11T10:00:00Z', 'True'),
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {'items': prs, 'metadata': {}}
    collector.kubearchive.session.get.return_value = mock_resp

    result = collector.get_failing_conforma_pipelineruns()
    assert len(result) == 0


@patch('subprocess.run')
def test_failing_pr_latest_is_failure(mock_run, collector):
    """Component whose latest run is a failure should appear."""
    mock_run.return_value = MagicMock(returncode=1)
    prs = [
        _make_pr('comp-b', 'conforma-check', '2026-04-10T10:00:00Z', 'True'),
        _make_pr('comp-b', 'conforma-check', '2026-04-11T10:00:00Z', 'False'),
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {'items': prs, 'metadata': {}}
    collector.kubearchive.session.get.return_value = mock_resp

    result = collector.get_failing_conforma_pipelineruns()
    assert 'comp-b' in result


@patch('subprocess.run')
def test_unknown_status_skipped(mock_run, collector):
    """Unknown (still running) PipelineRuns should be ignored, keeping prior failure."""
    mock_run.return_value = MagicMock(returncode=1)
    prs = [
        _make_pr('comp-c', 'conforma-check', '2026-04-10T10:00:00Z', 'False'),
        _make_pr('comp-c', 'conforma-check', '2026-04-11T10:00:00Z', 'Unknown'),
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {'items': prs, 'metadata': {}}
    collector.kubearchive.session.get.return_value = mock_resp

    result = collector.get_failing_conforma_pipelineruns()
    assert 'comp-c' in result


@patch('subprocess.run')
def test_non_conforma_scenario_ignored(mock_run, collector):
    """PipelineRuns with non-conforma scenarios should be ignored."""
    mock_run.return_value = MagicMock(returncode=1)
    prs = [{
        'metadata': {
            'name': 'pr-x', 'uid': 'uid-x',
            'creationTimestamp': '2026-04-10T10:00:00Z',
            'labels': {
                'appstudio.openshift.io/component': 'comp-d',
                'test.appstudio.openshift.io/scenario': 'other-test',
                'pipelines.appstudio.openshift.io/type': 'test',
            }
        },
        'status': {'conditions': [{'status': 'False'}]}
    }]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {'items': prs, 'metadata': {}}
    collector.kubearchive.session.get.return_value = mock_resp

    result = collector.get_failing_conforma_pipelineruns()
    assert len(result) == 0
