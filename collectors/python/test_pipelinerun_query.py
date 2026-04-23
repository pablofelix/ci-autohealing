"""Tests for query_pipelineruns shared function."""

import json
import pytest
from unittest.mock import MagicMock, patch
from clients.pipelinerun_query import query_pipelineruns


def _make_pr(name, uid, component, event_type='push', status='False'):
    return {
        'metadata': {
            'name': name,
            'uid': uid,
            'creationTimestamp': '2026-04-20T10:00:00Z',
            'labels': {
                'appstudio.openshift.io/component': component,
                'pipelinesascode.tekton.dev/event-type': event_type,
            }
        },
        'status': {'conditions': [{'status': status, 'reason': 'Failed'}]}
    }


def _mock_oc_result(items, returncode=0):
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = json.dumps({'items': items})
    return mock


@patch('clients.pipelinerun_query.subprocess.run')
def test_deduplicates_by_uid(mock_run):
    pr_archive = _make_pr('pr-1', 'uid-1', 'comp-a')
    pr_live = _make_pr('pr-1-live', 'uid-1', 'comp-a')

    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'items': [pr_archive], 'metadata': {}}
    session.get.return_value = resp

    mock_run.return_value = _mock_oc_result([pr_live])

    result = query_pipelineruns(
        'test-ns', 'app=test',
        kubearchive_url='https://kubearchive.example.com',
        session=session,
    )

    assert len(result) == 1
    assert result[0]['metadata']['name'] == 'pr-1-live'


@patch('clients.pipelinerun_query.subprocess.run')
def test_combines_unique_prs(mock_run):
    pr_archive = _make_pr('pr-1', 'uid-1', 'comp-a')
    pr_live = _make_pr('pr-2', 'uid-2', 'comp-b')

    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'items': [pr_archive], 'metadata': {}}
    session.get.return_value = resp

    mock_run.return_value = _mock_oc_result([pr_live])

    result = query_pipelineruns(
        'test-ns', 'app=test',
        kubearchive_url='https://kubearchive.example.com',
        session=session,
    )

    assert len(result) == 2
    names = {pr['metadata']['name'] for pr in result}
    assert names == {'pr-1', 'pr-2'}


@patch('clients.pipelinerun_query.subprocess.run')
def test_kubearchive_failure_still_returns_live(mock_run):
    pr_live = _make_pr('pr-1', 'uid-1', 'comp-a')

    session = MagicMock()
    session.get.side_effect = Exception("connection refused")

    mock_run.return_value = _mock_oc_result([pr_live])

    result = query_pipelineruns(
        'test-ns', 'app=test',
        kubearchive_url='https://kubearchive.example.com',
        session=session,
    )

    assert len(result) == 1


@patch('clients.pipelinerun_query.subprocess.run')
def test_live_cluster_failure_still_returns_archive(mock_run):
    pr_archive = _make_pr('pr-1', 'uid-1', 'comp-a')

    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'items': [pr_archive], 'metadata': {}}
    session.get.return_value = resp

    mock_run.side_effect = Exception("oc not found")

    result = query_pipelineruns(
        'test-ns', 'app=test',
        kubearchive_url='https://kubearchive.example.com',
        session=session,
    )

    assert len(result) == 1


@patch('clients.pipelinerun_query.subprocess.run')
def test_both_fail_returns_empty(mock_run):
    session = MagicMock()
    session.get.side_effect = Exception("connection refused")
    mock_run.side_effect = Exception("oc not found")

    result = query_pipelineruns(
        'test-ns', 'app=test',
        kubearchive_url='https://kubearchive.example.com',
        session=session,
    )

    assert result == []


@patch('clients.pipelinerun_query.subprocess.run')
def test_pagination(mock_run):
    pr1 = _make_pr('pr-1', 'uid-1', 'comp-a')
    pr2 = _make_pr('pr-2', 'uid-2', 'comp-b')

    session = MagicMock()
    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {'items': [pr1], 'metadata': {'continue': 'token123'}}
    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.json.return_value = {'items': [pr2], 'metadata': {}}
    session.get.side_effect = [resp1, resp2]

    mock_run.return_value = MagicMock(returncode=1)

    result = query_pipelineruns(
        'test-ns', 'app=test',
        kubearchive_url='https://kubearchive.example.com',
        session=session,
    )

    assert len(result) == 2
    assert session.get.call_count == 2


@patch('clients.pipelinerun_query.subprocess.run')
def test_respects_max_pages(mock_run):
    pr = _make_pr('pr-1', 'uid-1', 'comp-a')

    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'items': [pr], 'metadata': {'continue': 'token123'}}
    session.get.return_value = resp

    mock_run.return_value = MagicMock(returncode=1)

    query_pipelineruns(
        'test-ns', 'app=test',
        kubearchive_url='https://kubearchive.example.com',
        session=session,
        max_pages=2,
    )

    assert session.get.call_count == 2


@patch('clients.pipelinerun_query.subprocess.run')
@patch('openshift_auth.get_openshift_token', return_value=None)
@patch('openshift_auth.discover_kubearchive_api_url', return_value='https://kubearchive.example.com')
def test_skips_kubearchive_when_no_token(mock_discover, mock_token, mock_run):
    """When no token available, KubeArchive is skipped but live cluster works."""
    pr_live = _make_pr('pr-1', 'uid-1', 'comp-a')
    mock_run.return_value = _mock_oc_result([pr_live])

    result = query_pipelineruns('test-ns', 'app=test')

    assert len(result) == 1
    assert result[0]['metadata']['name'] == 'pr-1'
