"""Tests for resolution commit SHA tracking and related features."""

from unittest.mock import MagicMock, patch

import pytest

from collectors.status_synchronizer import StatusSynchronizer
from config import CollectorConfig, DatabaseConfig, KubernetesConfig
from models import BuildStatus


@pytest.fixture
def config():
    return CollectorConfig(
        db=DatabaseConfig(
            host="localhost", port=5432, user="test",
            password="test", database="testdb"
        ),
        k8s=KubernetesConfig(
            namespace="test-ns", application_name="test-app",
            kubearchive_api_url="https://kubearchive.example.com"
        )
    )


@pytest.fixture
def synchronizer(config):
    return StatusSynchronizer(
        config,
        db=MagicMock(),
        build_repo=MagicMock(),
        k8s=MagicMock(),
        tekton_results=MagicMock(),
    )


# -- _find_latest_push commit SHA extraction --

def test_find_latest_push_extracts_commit_sha(synchronizer):
    pipelineruns = [{
        'metadata': {
            'name': 'pr-1',
            'uid': 'uid-1',
            'creationTimestamp': '2025-05-20T10:00:00Z',
            'labels': {
                'pipelinesascode.tekton.dev/event-type': 'push',
            },
            'annotations': {
                'build.appstudio.redhat.com/commit_sha': 'abc123def456',
            },
        },
        'status': {
            'conditions': [{'status': 'True', 'reason': 'Succeeded'}]
        }
    }]
    result = synchronizer._find_latest_push(pipelineruns)
    assert result is not None
    ts, name, uid, reason, commit_sha = result
    assert commit_sha == 'abc123def456'
    assert name == 'pr-1'


def test_find_latest_push_falls_back_to_pac_sha(synchronizer):
    pipelineruns = [{
        'metadata': {
            'name': 'pr-2',
            'uid': 'uid-2',
            'creationTimestamp': '2025-05-20T10:00:00Z',
            'labels': {
                'pipelinesascode.tekton.dev/event-type': 'push',
            },
            'annotations': {
                'pipelinesascode.tekton.dev/sha': 'fallback789',
            },
        },
        'status': {
            'conditions': [{'status': 'True', 'reason': 'Succeeded'}]
        }
    }]
    result = synchronizer._find_latest_push(pipelineruns)
    _, _, _, _, commit_sha = result
    assert commit_sha == 'fallback789'


def test_find_latest_push_no_annotations(synchronizer):
    pipelineruns = [{
        'metadata': {
            'name': 'pr-3',
            'uid': 'uid-3',
            'creationTimestamp': '2025-05-20T10:00:00Z',
            'labels': {
                'pipelinesascode.tekton.dev/event-type': 'push',
            },
        },
        'status': {
            'conditions': [{'status': 'True', 'reason': 'Succeeded'}]
        }
    }]
    result = synchronizer._find_latest_push(pipelineruns)
    _, _, _, _, commit_sha = result
    assert commit_sha is None


def test_find_latest_push_picks_newest(synchronizer):
    pipelineruns = [
        {
            'metadata': {
                'name': 'pr-old', 'uid': 'uid-old',
                'creationTimestamp': '2025-05-19T10:00:00Z',
                'labels': {'pipelinesascode.tekton.dev/event-type': 'push'},
                'annotations': {'build.appstudio.redhat.com/commit_sha': 'old-sha'},
            },
            'status': {'conditions': [{'status': 'True', 'reason': 'Succeeded'}]}
        },
        {
            'metadata': {
                'name': 'pr-new', 'uid': 'uid-new',
                'creationTimestamp': '2025-05-20T10:00:00Z',
                'labels': {'pipelinesascode.tekton.dev/event-type': 'push'},
                'annotations': {'build.appstudio.redhat.com/commit_sha': 'new-sha'},
            },
            'status': {'conditions': [{'status': 'True', 'reason': 'Succeeded'}]}
        },
    ]
    result = synchronizer._find_latest_push(pipelineruns)
    _, name, _, _, commit_sha = result
    assert name == 'pr-new'
    assert commit_sha == 'new-sha'


# -- get_current_status returns commit_sha --

def test_get_current_status_includes_commit_sha(synchronizer, config):
    pr_data = [{
        'metadata': {
            'name': 'pr-success', 'uid': 'uid-success',
            'creationTimestamp': '2025-05-20T10:00:00Z',
            'labels': {'pipelinesascode.tekton.dev/event-type': 'push'},
            'annotations': {'build.appstudio.redhat.com/commit_sha': 'fix-commit-sha'},
        },
        'status': {'conditions': [{'status': 'True', 'reason': 'Succeeded'}]}
    }]

    with patch('collectors.status_synchronizer.query_pipelineruns', return_value=pr_data):
        result = synchronizer.get_current_status('test-component')

    assert result is not None
    assert result['commit_sha'] == 'fix-commit-sha'
    assert result['status'] == BuildStatus.SUCCEEDED


# -- sync_component passes commit SHA to mark_resolved --

def test_sync_component_passes_commit_sha_on_resolve(synchronizer):
    from models import Component
    component = Component(name='comp-a', namespace='test-ns',
                          repository_url='https://github.com/org/repo', branch='main')

    synchronizer.build_repo.count_unresolved.return_value = 1
    synchronizer.build_repo.mark_resolved.return_value = True
    synchronizer.build_repo.record_successful_build.return_value = True

    with patch.object(synchronizer, 'get_current_status', return_value={
        'name': 'pr-fix', 'uid': 'uid-fix',
        'status': BuildStatus.SUCCEEDED, 'commit_sha': 'resolution-sha-123'
    }):
        result = synchronizer.sync_component(component)

    assert result['now_resolved'] is True
    synchronizer.build_repo.mark_resolved.assert_called_once_with(
        'comp-a', 'test-app', 'test-ns', 'pr-fix',
        resolution_commit_sha='resolution-sha-123'
    )


def test_sync_component_handles_missing_commit_sha(synchronizer):
    from models import Component
    component = Component(name='comp-b', namespace='test-ns',
                          repository_url='https://github.com/org/repo', branch='main')

    synchronizer.build_repo.count_unresolved.return_value = 1
    synchronizer.build_repo.mark_resolved.return_value = True
    synchronizer.build_repo.record_successful_build.return_value = True

    with patch.object(synchronizer, 'get_current_status', return_value={
        'name': 'pr-fix', 'uid': 'uid-fix',
        'status': BuildStatus.SUCCEEDED, 'commit_sha': None
    }):
        result = synchronizer.sync_component(component)

    assert result['now_resolved'] is True
    synchronizer.build_repo.mark_resolved.assert_called_once_with(
        'comp-b', 'test-app', 'test-ns', 'pr-fix',
        resolution_commit_sha=None
    )


# -- MCP model has_context field --

def test_failure_summary_has_context_field():
    with patch.dict('os.environ', {}, clear=False), \
         patch('repositories.repository_factory.init_pool'):
        from mcp_server.models import FailureSummary
    from datetime import datetime
    summary = FailureSummary(
        component='test', status='Failed',
        first_seen=datetime.utcnow(), last_seen=datetime.utcnow(),
        occurrence_count=1, has_logs=True, has_context=True, has_analysis=False
    )
    assert summary.has_context is True


def test_failure_summary_has_context_defaults_false():
    with patch.dict('os.environ', {}, clear=False), \
         patch('repositories.repository_factory.init_pool'):
        from mcp_server.models import FailureSummary
    from datetime import datetime
    summary = FailureSummary(
        component='test', status='Failed',
        first_seen=datetime.utcnow(), last_seen=datetime.utcnow(),
        occurrence_count=1, has_logs=True, has_analysis=False
    )
    assert summary.has_context is False
