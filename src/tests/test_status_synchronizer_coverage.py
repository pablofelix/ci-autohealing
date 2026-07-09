"""Comprehensive tests for collectors.status_synchronizer module.

Covers:
- get_failing_build_components (standalone function)
- get_failing_conforma_components (standalone function)
- StatusSynchronizer class methods:
  __init__, get_component_metadata, _find_latest_push,
  get_current_status, sync_component, _auto_verdict,
  _correlate_skill_runs, run
"""

import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, Mock, patch

import pytest


# ---------------------------------------------------------------------------
# Guard against sys.modules pollution from other test files.
# Some tests replace 'config', 'repositories.connection', etc. with MagicMock
# at module level. Force-reload the real modules before importing.
# ---------------------------------------------------------------------------
def _ensure_real_module(name):
    # Also restore parent packages (e.g. 'repositories' for 'repositories.connection')
    parts = name.split('.')
    for i in range(len(parts)):
        partial = '.'.join(parts[:i + 1])
        mod = sys.modules.get(partial)
        if mod is not None and isinstance(mod, MagicMock):
            sys.modules.pop(partial, None)
    mod = sys.modules.get(name)
    if mod is None or isinstance(mod, MagicMock):
        sys.modules.pop(name, None)
        importlib.import_module(name)


_ensure_real_module('config')
_ensure_real_module('repositories.connection')

# Reload status_synchronizer so it picks up the real config/connection modules
# instead of any MagicMock references it cached during initial import.
if 'collectors.status_synchronizer' in sys.modules:
    importlib.reload(sys.modules['collectors.status_synchronizer'])

from collectors.status_synchronizer import (
    StatusSynchronizer,
    get_failing_build_components,
    get_failing_conforma_components,
)
from config import CollectorConfig, DatabaseConfig, KubernetesConfig
from models import BuildStatus, Component

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_build_repo():
    repo = MagicMock()
    repo.db = MagicMock()
    return repo


@pytest.fixture
def mock_k8s():
    return MagicMock()


@pytest.fixture
def mock_tekton_results():
    return MagicMock()


@pytest.fixture
def synchronizer(config, mock_build_repo, mock_k8s, mock_tekton_results):
    return StatusSynchronizer(
        config,
        build_repo=mock_build_repo,
        k8s=mock_k8s,
        tekton_results=mock_tekton_results,
    )


def _make_pr(component, event_type, status, reason="Completed",
             ts="2024-01-02T00:00:00Z", name="pr-1", uid="uid-1",
             scenario=None, pipeline_type="build", conditions_present=True,
             commit_sha=None):
    """Helper to build a pipelinerun dict for testing."""
    labels = {
        'appstudio.openshift.io/component': component,
        'pipelinesascode.tekton.dev/event-type': event_type,
        'pipelines.appstudio.openshift.io/type': pipeline_type,
    }
    if scenario:
        labels['test.appstudio.openshift.io/scenario'] = scenario

    annotations = {}
    if commit_sha:
        annotations['build.appstudio.redhat.com/commit_sha'] = commit_sha

    pr = {
        'metadata': {
            'name': name,
            'uid': uid,
            'creationTimestamp': ts,
            'labels': labels,
            'annotations': annotations,
        },
        'status': {},
    }
    if conditions_present:
        pr['status']['conditions'] = [
            {'status': status, 'reason': reason}
        ]
    else:
        pr['status']['conditions'] = []
    return pr


# ===========================================================================
# Tests for get_failing_build_components
# ===========================================================================

class TestGetFailingBuildComponents:

    @patch('collectors.status_synchronizer.query_pipelineruns', return_value=[])
    def test_empty_pipelineruns(self, mock_qpr, config):
        result = get_failing_build_components(config)
        assert result == {'failing': set(), 'retriggered': set(), 'running': {}}

    @patch('collectors.status_synchronizer.query_pipelineruns', return_value=None)
    def test_none_pipelineruns(self, mock_qpr, config):
        result = get_failing_build_components(config)
        assert result == {'failing': set(), 'retriggered': set(), 'running': {}}

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_single_failing_component(self, mock_qpr, config):
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='BuildFailed'),
        ]
        result = get_failing_build_components(config)
        assert 'comp-a' in result['failing']
        assert result['retriggered'] == set()
        assert 'comp-a' in result['details']
        assert result['details']['comp-a']['reason'] == 'BuildFailed'

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_multiple_components_mixed(self, mock_qpr, config):
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='BuildFailed',
                      ts='2024-01-02T00:00:00Z', name='pr-a', uid='uid-a'),
            _make_pr('comp-b', 'push', 'True', reason='Succeeded',
                      ts='2024-01-02T00:00:00Z', name='pr-b', uid='uid-b'),
        ]
        result = get_failing_build_components(config)
        assert result['failing'] == {'comp-a'}
        assert 'comp-b' not in result['details']

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_retriggered_component(self, mock_qpr, config):
        """Push failed, then incoming succeeded later -> retriggered."""
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='BuildFailed',
                      ts='2024-01-01T00:00:00Z', name='pr-push', uid='uid-push'),
            _make_pr('comp-a', 'incoming', 'True', reason='Succeeded',
                      ts='2024-01-02T00:00:00Z', name='pr-inc', uid='uid-inc'),
        ]
        result = get_failing_build_components(config)
        assert 'comp-a' in result['failing']
        assert 'comp-a' in result['retriggered']

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_retriggered_not_if_incoming_older(self, mock_qpr, config):
        """Incoming succeeded but BEFORE push failed -> not retriggered."""
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='BuildFailed',
                      ts='2024-01-02T00:00:00Z'),
            _make_pr('comp-a', 'incoming', 'True', reason='Succeeded',
                      ts='2024-01-01T00:00:00Z'),
        ]
        result = get_failing_build_components(config)
        assert 'comp-a' in result['failing']
        assert 'comp-a' not in result['retriggered']

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_running_builds_no_conditions(self, mock_qpr, config):
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', None, conditions_present=False,
                      ts='2024-01-03T00:00:00Z', name='pr-running'),
        ]
        result = get_failing_build_components(config)
        assert result['failing'] == set()
        assert 'comp-a' in result['running']
        assert result['running']['comp-a']['pipelinerun_name'] == 'pr-running'

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_running_builds_unknown_status(self, mock_qpr, config):
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'Unknown',
                      ts='2024-01-03T00:00:00Z', name='pr-running'),
        ]
        result = get_failing_build_components(config)
        assert result['failing'] == set()
        assert 'comp-a' in result['running']

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_running_not_active_if_older_than_completed(self, mock_qpr, config):
        """Running build older than a completed build should not appear."""
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'True', reason='Succeeded',
                      ts='2024-01-03T00:00:00Z'),
            _make_pr('comp-a', 'push', None, conditions_present=False,
                      ts='2024-01-01T00:00:00Z', name='pr-old-running'),
        ]
        result = get_failing_build_components(config)
        assert 'comp-a' not in result['running']

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_no_failing_all_succeeded(self, mock_qpr, config):
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'True', reason='Succeeded'),
            _make_pr('comp-b', 'push', 'True', reason='Succeeded'),
        ]
        result = get_failing_build_components(config)
        assert result['failing'] == set()
        assert result['retriggered'] == set()

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_application_override(self, mock_qpr, config):
        mock_qpr.return_value = []
        get_failing_build_components(config, application_override='custom-app')
        called_selector = mock_qpr.call_args[0][1]
        assert 'custom-app' in called_selector

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_recent_days_passed(self, mock_qpr, config):
        mock_qpr.return_value = []
        get_failing_build_components(config, recent_days=3)
        assert mock_qpr.call_args[1]['recent_days'] == 3

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_pr_without_component_label_skipped(self, mock_qpr, config):
        """PRs missing component label are silently skipped."""
        pr = _make_pr('comp-a', 'push', 'False')
        del pr['metadata']['labels']['appstudio.openshift.io/component']
        mock_qpr.return_value = [pr]
        result = get_failing_build_components(config)
        assert result['failing'] == set()

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_non_push_event_ignored(self, mock_qpr, config):
        """pull_request event type should be ignored for push/incoming tracking."""
        mock_qpr.return_value = [
            _make_pr('comp-a', 'pull_request', 'False', reason='BuildFailed'),
        ]
        result = get_failing_build_components(config)
        assert result['failing'] == set()

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_latest_push_wins(self, mock_qpr, config):
        """When multiple push builds, only the latest status counts."""
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='OldFailure',
                      ts='2024-01-01T00:00:00Z', name='pr-old', uid='uid-old'),
            _make_pr('comp-a', 'push', 'True', reason='Succeeded',
                      ts='2024-01-02T00:00:00Z', name='pr-new', uid='uid-new'),
        ]
        result = get_failing_build_components(config)
        assert result['failing'] == set()


# ===========================================================================
# Tests for get_failing_conforma_components
# ===========================================================================

class TestGetFailingConformaComponents:

    @patch('collectors.status_synchronizer.query_pipelineruns', return_value=[])
    def test_empty_pipelineruns(self, mock_qpr, config):
        result = get_failing_conforma_components(config)
        assert result == {'failing': set(), 'details': {}, 'running': {}}

    @patch('collectors.status_synchronizer.query_pipelineruns', return_value=None)
    def test_none_pipelineruns(self, mock_qpr, config):
        result = get_failing_conforma_components(config)
        assert result == {'failing': set(), 'details': {}, 'running': {}}

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_failing_conforma(self, mock_qpr, config):
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='Failed',
                      scenario='conforma-stage', pipeline_type='test',
                      name='pr-test', uid='uid-test', ts='2024-01-02T00:00:00Z'),
        ]
        result = get_failing_conforma_components(config)
        assert 'comp-a' in result['failing']
        assert result['details']['comp-a']['scenario'] == 'conforma-stage'

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_non_conforma_filtered(self, mock_qpr, config):
        """Non-conforma scenarios (not starting with 'conforma-') are skipped."""
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='Failed',
                      scenario='enterprise-contract', pipeline_type='test'),
        ]
        result = get_failing_conforma_components(config)
        assert result['failing'] == set()

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_non_test_pipeline_filtered(self, mock_qpr, config):
        """Pipeline type != 'test' is skipped even with conforma scenario."""
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='Failed',
                      scenario='conforma-stage', pipeline_type='build'),
        ]
        result = get_failing_conforma_components(config)
        assert result['failing'] == set()

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_running_conforma(self, mock_qpr, config):
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', None, conditions_present=False,
                      scenario='conforma-stage', pipeline_type='test',
                      ts='2024-01-03T00:00:00Z', name='pr-running'),
        ]
        result = get_failing_conforma_components(config)
        assert result['failing'] == set()
        assert 'comp-a' in result['running']
        assert result['running']['comp-a']['scenario'] == 'conforma-stage'

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_running_conforma_unknown_status(self, mock_qpr, config):
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'Unknown',
                      scenario='conforma-stage', pipeline_type='test',
                      ts='2024-01-03T00:00:00Z', name='pr-running'),
        ]
        result = get_failing_conforma_components(config)
        assert result['failing'] == set()
        assert 'comp-a' in result['running']

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_mixed_statuses(self, mock_qpr, config):
        """comp-a failing, comp-b succeeded in conforma tests."""
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='Failed',
                      scenario='conforma-stage', pipeline_type='test',
                      name='pr-a', uid='uid-a'),
            _make_pr('comp-b', 'push', 'True', reason='Succeeded',
                      scenario='conforma-stage', pipeline_type='test',
                      name='pr-b', uid='uid-b'),
        ]
        result = get_failing_conforma_components(config)
        assert result['failing'] == {'comp-a'}
        assert 'comp-b' not in result['details']

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_latest_conforma_wins(self, mock_qpr, config):
        """Latest conforma result should win."""
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='Failed',
                      scenario='conforma-stage', pipeline_type='test',
                      ts='2024-01-01T00:00:00Z', name='pr-old', uid='uid-old'),
            _make_pr('comp-a', 'push', 'True', reason='Succeeded',
                      scenario='conforma-stage', pipeline_type='test',
                      ts='2024-01-02T00:00:00Z', name='pr-new', uid='uid-new'),
        ]
        result = get_failing_conforma_components(config)
        assert result['failing'] == set()

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_running_not_active_if_older(self, mock_qpr, config):
        """Running conforma older than completed should not appear."""
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'True', reason='Succeeded',
                      scenario='conforma-stage', pipeline_type='test',
                      ts='2024-01-03T00:00:00Z'),
            _make_pr('comp-a', 'push', None, conditions_present=False,
                      scenario='conforma-stage', pipeline_type='test',
                      ts='2024-01-01T00:00:00Z', name='pr-old-running'),
        ]
        result = get_failing_conforma_components(config)
        assert 'comp-a' not in result['running']


# ===========================================================================
# Tests for StatusSynchronizer.__init__
# ===========================================================================

class TestStatusSynchronizerInit:

    def test_init_with_mocks(self, synchronizer, mock_build_repo, mock_k8s,
                             mock_tekton_results):
        assert synchronizer.build_repo is mock_build_repo
        assert synchronizer.k8s is mock_k8s
        assert synchronizer.tekton_results is mock_tekton_results

    def test_config_stored(self, synchronizer, config):
        assert synchronizer.config is config

    @patch('collectors.status_synchronizer.TektonResultsClient')
    @patch('collectors.status_synchronizer.KubernetesClient')
    @patch('collectors.status_synchronizer.BuildFailureRepository')
    @patch('collectors.status_synchronizer.DatabaseConnection')
    def test_init_creates_defaults(self, mock_db_conn, mock_repo_cls,
                                   mock_k8s_cls, mock_tr_cls, config):
        sync = StatusSynchronizer(config)
        mock_db_conn.assert_called_once_with(config.db)
        mock_repo_cls.assert_called_once()
        mock_k8s_cls.assert_called_once_with(namespace="test-ns")
        mock_tr_cls.assert_called_once_with(namespace="test-ns")


# ===========================================================================
# Tests for get_component_metadata
# ===========================================================================

class TestGetComponentMetadata:

    def test_found(self, synchronizer):
        synchronizer.k8s.get_component_metadata.return_value = {
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
        }
        comp = synchronizer.get_component_metadata('comp-a')
        assert comp is not None
        assert comp.name == 'comp-a'
        assert comp.repository_url == 'https://github.com/org/repo'
        assert comp.branch == 'main'
        assert comp.namespace == 'test-ns'

    def test_not_found(self, synchronizer):
        synchronizer.k8s.get_component_metadata.return_value = None
        comp = synchronizer.get_component_metadata('comp-missing')
        assert comp is None


# ===========================================================================
# Tests for _find_latest_push
# ===========================================================================

class TestFindLatestPush:

    def test_empty_list(self, synchronizer):
        assert synchronizer._find_latest_push([]) is None

    def test_single_push_pr(self, synchronizer):
        pr = _make_pr('comp-a', 'push', 'True', reason='Succeeded',
                       ts='2024-01-01T00:00:00Z', name='pr-1', uid='uid-1',
                       commit_sha='abc123')
        result = synchronizer._find_latest_push([pr])
        assert result is not None
        ts, name, uid, reason, sha = result
        assert ts == '2024-01-01T00:00:00Z'
        assert name == 'pr-1'
        assert uid == 'uid-1'
        assert reason == 'Succeeded'
        assert sha == 'abc123'

    def test_multiple_push_latest_wins(self, synchronizer):
        prs = [
            _make_pr('comp-a', 'push', 'False', reason='OldFail',
                      ts='2024-01-01T00:00:00Z', name='pr-old', uid='uid-old'),
            _make_pr('comp-a', 'push', 'True', reason='Succeeded',
                      ts='2024-01-02T00:00:00Z', name='pr-new', uid='uid-new'),
        ]
        result = synchronizer._find_latest_push(prs)
        ts, name, uid, reason, sha = result
        assert name == 'pr-new'
        assert reason == 'Succeeded'

    def test_non_push_filtered(self, synchronizer):
        pr = _make_pr('comp-a', 'pull_request', 'True', reason='Succeeded')
        assert synchronizer._find_latest_push([pr]) is None

    def test_no_conditions_filtered(self, synchronizer):
        pr = _make_pr('comp-a', 'push', None, conditions_present=False)
        assert synchronizer._find_latest_push([pr]) is None

    def test_incoming_accepted(self, synchronizer):
        """Incoming event type is also valid for find_latest_push."""
        pr = _make_pr('comp-a', 'incoming', 'True', reason='Succeeded',
                       name='pr-inc', uid='uid-inc')
        result = synchronizer._find_latest_push([pr])
        assert result is not None
        assert result[1] == 'pr-inc'

    def test_commit_sha_from_pipelinesascode(self, synchronizer):
        """Falls back to pipelinesascode sha annotation."""
        pr = _make_pr('comp-a', 'push', 'True', reason='Succeeded')
        pr['metadata']['annotations'] = {
            'pipelinesascode.tekton.dev/sha': 'fallback-sha'
        }
        result = synchronizer._find_latest_push([pr])
        assert result[4] == 'fallback-sha'


# ===========================================================================
# Tests for get_current_status
# ===========================================================================

class TestGetCurrentStatus:

    @patch('collectors.status_synchronizer.classify_build_status')
    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_found_in_kubearchive(self, mock_qpr, mock_classify, synchronizer):
        mock_qpr.return_value = [
            _make_pr('comp-a', 'push', 'True', reason='Succeeded',
                      name='pr-1', uid='uid-1', commit_sha='sha123'),
        ]
        mock_classify.return_value = BuildStatus.SUCCEEDED
        result = synchronizer.get_current_status('comp-a')
        assert result is not None
        assert result['name'] == 'pr-1'
        assert result['status'] == BuildStatus.SUCCEEDED
        assert result['commit_sha'] == 'sha123'

    @patch('collectors.status_synchronizer.classify_build_status')
    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_fallback_to_tekton_results(self, mock_qpr, mock_classify, synchronizer):
        # KubeArchive returns no push PRs -> fallback to TR
        mock_qpr.return_value = []
        synchronizer.tekton_results.query_component_build_history.return_value = [
            _make_pr('comp-a', 'push', 'False', reason='BuildFailed',
                      name='pr-tr', uid='uid-tr', commit_sha='shatr'),
        ]
        mock_classify.return_value = BuildStatus.FAILED
        result = synchronizer.get_current_status('comp-a')
        assert result is not None
        assert result['name'] == 'pr-tr'
        assert result['status'] == BuildStatus.FAILED

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_nothing_found(self, mock_qpr, synchronizer):
        mock_qpr.return_value = []
        synchronizer.tekton_results.query_component_build_history.return_value = []
        result = synchronizer.get_current_status('comp-a')
        assert result is None

    @patch('collectors.status_synchronizer.query_pipelineruns')
    def test_tekton_results_exception(self, mock_qpr, synchronizer):
        mock_qpr.return_value = []
        synchronizer.tekton_results.query_component_build_history.side_effect = \
            Exception("TR unavailable")
        result = synchronizer.get_current_status('comp-a')
        assert result is None


# ===========================================================================
# Tests for sync_component
# ===========================================================================

class TestSyncComponent:

    def _make_component(self, name='comp-a', repo_url='https://github.com/org/repo',
                        branch='main'):
        return Component(name=name, repository_url=repo_url, branch=branch,
                         namespace='test-ns')

    def test_was_failing_now_succeeded(self, synchronizer):
        """Resolution path: was failing, now succeeded."""
        comp = self._make_component()
        synchronizer.build_repo.count_unresolved.return_value = 1
        synchronizer.build_repo.mark_resolved.return_value = True
        synchronizer.build_repo.record_successful_build.return_value = True

        with patch.object(synchronizer, 'get_current_status') as mock_status, \
             patch.object(synchronizer, '_auto_verdict') as mock_verdict, \
             patch.object(synchronizer, '_correlate_skill_runs') as mock_correlate:
            mock_status.return_value = {
                'name': 'pr-1', 'uid': 'uid-1',
                'status': BuildStatus.SUCCEEDED, 'commit_sha': 'sha123',
            }
            result = synchronizer.sync_component(comp)

        assert result['was_failing'] is True
        assert result['now_resolved'] is True
        assert result['success_recorded'] is True
        assert result['current_status'] == 'Succeeded'
        mock_verdict.assert_called_once()
        mock_correlate.assert_called_once()

    def test_was_failing_component_deleted(self, synchronizer):
        """Component deleted from cluster -> auto-resolve."""
        comp = self._make_component(repo_url='')
        synchronizer.build_repo.count_unresolved.return_value = 1
        synchronizer.build_repo.mark_resolved_deleted.return_value = True

        result = synchronizer.sync_component(comp)
        assert result['now_resolved'] is True
        assert result['current_status'] == 'component-deleted'

    def test_was_failing_no_current_status(self, synchronizer):
        """No PipelineRuns found -> fall back to DB status."""
        comp = self._make_component()
        synchronizer.build_repo.count_unresolved.return_value = 1
        synchronizer.build_repo.get_last_status.return_value = 'Failed'

        with patch.object(synchronizer, 'get_current_status', return_value=None):
            result = synchronizer.sync_component(comp)

        assert result['was_failing'] is True
        assert result['now_resolved'] is False
        assert result['current_status'] == 'Failed'

    def test_was_failing_no_current_status_no_db(self, synchronizer):
        """No PipelineRuns and no DB status -> 'unknown'."""
        comp = self._make_component()
        synchronizer.build_repo.count_unresolved.return_value = 1
        synchronizer.build_repo.get_last_status.return_value = None

        with patch.object(synchronizer, 'get_current_status', return_value=None):
            result = synchronizer.sync_component(comp)

        assert result['current_status'] == 'unknown'

    def test_was_failing_still_failing(self, synchronizer):
        """Was failing, still failing -> no resolution."""
        comp = self._make_component()
        synchronizer.build_repo.count_unresolved.return_value = 1

        with patch.object(synchronizer, 'get_current_status') as mock_status:
            mock_status.return_value = {
                'name': 'pr-1', 'uid': 'uid-1',
                'status': BuildStatus.FAILED, 'commit_sha': None,
            }
            result = synchronizer.sync_component(comp)

        assert result['was_failing'] is True
        assert result['now_resolved'] is False
        assert result['current_status'] == 'Failed'

    def test_not_failing_succeeded(self, synchronizer):
        """Not previously failing, current succeeded -> record success."""
        comp = self._make_component()
        synchronizer.build_repo.count_unresolved.return_value = 0
        synchronizer.build_repo.record_successful_build.return_value = True

        with patch.object(synchronizer, 'get_current_status') as mock_status:
            mock_status.return_value = {
                'name': 'pr-1', 'uid': 'uid-1',
                'status': BuildStatus.SUCCEEDED, 'commit_sha': 'sha1',
            }
            result = synchronizer.sync_component(comp)

        assert result['was_failing'] is False
        assert result['now_resolved'] is False
        assert result['success_recorded'] is True

    def test_triage_auto_resolve_succeeds(self, synchronizer):
        """Triage auto-resolve returns resolved IDs."""
        comp = self._make_component()
        synchronizer.build_repo.count_unresolved.return_value = 1
        synchronizer.build_repo.mark_resolved.return_value = True
        synchronizer.build_repo.record_successful_build.return_value = True

        mock_triage_repo = MagicMock()
        mock_triage_repo.auto_resolve_for_component.return_value = [101, 102]

        with patch.object(synchronizer, 'get_current_status') as mock_status, \
             patch.object(synchronizer, '_auto_verdict'), \
             patch.object(synchronizer, '_correlate_skill_runs'), \
             patch('collectors.status_synchronizer.TriageRepository',
                   create=True) as mock_triage_cls:
            # The import happens inside the method, so we patch it at module level
            pass

        # Use a different approach: patch the import inside sync_component
        with patch.object(synchronizer, 'get_current_status') as mock_status, \
             patch.object(synchronizer, '_auto_verdict'), \
             patch.object(synchronizer, '_correlate_skill_runs'), \
             patch.dict('sys.modules', {
                 'repositories.triage_repository': MagicMock(
                     TriageRepository=MagicMock(return_value=mock_triage_repo)
                 )
             }):
            mock_status.return_value = {
                'name': 'pr-1', 'uid': 'uid-1',
                'status': BuildStatus.SUCCEEDED, 'commit_sha': 'sha1',
            }
            result = synchronizer.sync_component(comp)

        assert result.get('triage_auto_resolved') == [101, 102]

    def test_triage_auto_resolve_fails(self, synchronizer):
        """Triage auto-resolve raises exception -> silently caught."""
        comp = self._make_component()
        synchronizer.build_repo.count_unresolved.return_value = 1
        synchronizer.build_repo.mark_resolved.return_value = True
        synchronizer.build_repo.record_successful_build.return_value = True

        mock_triage_cls = MagicMock(side_effect=Exception("DB error"))

        with patch.object(synchronizer, 'get_current_status') as mock_status, \
             patch.object(synchronizer, '_auto_verdict'), \
             patch.object(synchronizer, '_correlate_skill_runs'), \
             patch.dict('sys.modules', {
                 'repositories.triage_repository': MagicMock(
                     TriageRepository=mock_triage_cls
                 )
             }):
            mock_status.return_value = {
                'name': 'pr-1', 'uid': 'uid-1',
                'status': BuildStatus.SUCCEEDED, 'commit_sha': 'sha1',
            }
            result = synchronizer.sync_component(comp)

        # Should not crash, triage_auto_resolved should not be set
        assert 'triage_auto_resolved' not in result

    def test_mark_resolved_returns_false(self, synchronizer):
        """mark_resolved returns False -> now_resolved stays False, no verdict/correlate."""
        comp = self._make_component()
        synchronizer.build_repo.count_unresolved.return_value = 1
        synchronizer.build_repo.mark_resolved.return_value = False
        synchronizer.build_repo.record_successful_build.return_value = True

        with patch.object(synchronizer, 'get_current_status') as mock_status, \
             patch.object(synchronizer, '_auto_verdict') as mock_verdict, \
             patch.object(synchronizer, '_correlate_skill_runs') as mock_correlate:
            mock_status.return_value = {
                'name': 'pr-1', 'uid': 'uid-1',
                'status': BuildStatus.SUCCEEDED, 'commit_sha': 'sha1',
            }
            result = synchronizer.sync_component(comp)

        assert result['now_resolved'] is False
        mock_verdict.assert_not_called()
        mock_correlate.assert_not_called()
        # But success should still be recorded since was_failing + succeeded
        assert result['success_recorded'] is True

    def test_component_deleted_mark_fails(self, synchronizer):
        """Component deleted but mark_resolved_deleted returns False."""
        comp = self._make_component(repo_url='')
        synchronizer.build_repo.count_unresolved.return_value = 1
        synchronizer.build_repo.mark_resolved_deleted.return_value = False

        with patch.object(synchronizer, 'get_current_status', return_value=None):
            synchronizer.build_repo.get_last_status.return_value = 'unknown'
            result = synchronizer.sync_component(comp)

        # Did not return early, fell through to get_current_status path
        assert result['now_resolved'] is False


# ===========================================================================
# Tests for _auto_verdict
# ===========================================================================

class TestAutoVerdict:

    def test_no_analysis(self, synchronizer):
        """No analysis found -> no verdict recorded."""
        mock_ai_repo = MagicMock()
        mock_ai_repo.get_analysis_by_component.return_value = None

        with patch.dict('sys.modules', {
            'repositories.ai_analysis_repository': MagicMock(
                AIAnalysisRepository=MagicMock(return_value=mock_ai_repo)
            )
        }):
            synchronizer._auto_verdict('comp-a', 'test-app')

        mock_ai_repo.record_verdict.assert_not_called()

    def test_already_has_verdict(self, synchronizer):
        """Analysis already has a human_verdict -> skip."""
        mock_ai_repo = MagicMock()
        mock_ai_repo.get_analysis_by_component.return_value = {
            'human_verdict': 'correct'
        }

        with patch.dict('sys.modules', {
            'repositories.ai_analysis_repository': MagicMock(
                AIAnalysisRepository=MagicMock(return_value=mock_ai_repo)
            )
        }):
            synchronizer._auto_verdict('comp-a', 'test-app')

        mock_ai_repo.record_verdict.assert_not_called()

    def test_no_github_token_default_verdict(self, synchronizer, config):
        """No github_token -> verdict defaults to 'correct'."""
        mock_ai_repo = MagicMock()
        mock_ai_repo.get_analysis_by_component.return_value = {
            'human_verdict': None
        }

        with patch.dict('sys.modules', {
            'repositories.ai_analysis_repository': MagicMock(
                AIAnalysisRepository=MagicMock(return_value=mock_ai_repo)
            )
        }):
            synchronizer._auto_verdict('comp-a', 'test-app',
                                       resolution_commit_sha='sha123')

        mock_ai_repo.record_verdict.assert_called_once_with(
            'comp-a', 'test-app', 'correct', verdict_by='auto-resolve'
        )

    def test_github_token_present_correlation(self, synchronizer):
        """github_token present -> tries VerdictCorrelator."""
        synchronizer.config = MagicMock()
        synchronizer.config.github_token = 'ghp_test123'

        mock_ai_repo = MagicMock()
        mock_ai_repo.get_analysis_by_component.return_value = {
            'human_verdict': None
        }
        mock_correlator = MagicMock()
        mock_correlator.correlate_build_resolution.return_value = 'partially_correct'

        with patch.dict('sys.modules', {
            'repositories.ai_analysis_repository': MagicMock(
                AIAnalysisRepository=MagicMock(return_value=mock_ai_repo)
            ),
            'collectors.verdict_correlator': MagicMock(
                VerdictCorrelator=MagicMock(return_value=mock_correlator)
            ),
        }):
            synchronizer._auto_verdict('comp-a', 'test-app',
                                       resolution_commit_sha='sha123')

        mock_ai_repo.record_verdict.assert_called_once_with(
            'comp-a', 'test-app', 'partially_correct', verdict_by='auto-resolve'
        )

    def test_correlation_fails_fallback(self, synchronizer):
        """VerdictCorrelator raises -> falls back to 'correct'."""
        synchronizer.config = MagicMock()
        synchronizer.config.github_token = 'ghp_test123'

        mock_ai_repo = MagicMock()
        mock_ai_repo.get_analysis_by_component.return_value = {
            'human_verdict': None
        }
        mock_correlator_cls = MagicMock(side_effect=Exception("GitHub API error"))

        with patch.dict('sys.modules', {
            'repositories.ai_analysis_repository': MagicMock(
                AIAnalysisRepository=MagicMock(return_value=mock_ai_repo)
            ),
            'collectors.verdict_correlator': MagicMock(
                VerdictCorrelator=mock_correlator_cls
            ),
        }):
            synchronizer._auto_verdict('comp-a', 'test-app',
                                       resolution_commit_sha='sha123')

        mock_ai_repo.record_verdict.assert_called_once_with(
            'comp-a', 'test-app', 'correct', verdict_by='auto-resolve'
        )

    def test_outer_exception_caught(self, synchronizer):
        """Complete failure in _auto_verdict is silently caught."""
        with patch.dict('sys.modules', {
            'repositories.ai_analysis_repository': MagicMock(
                AIAnalysisRepository=MagicMock(
                    side_effect=Exception("import failed")
                )
            )
        }):
            # Should not raise
            synchronizer._auto_verdict('comp-a', 'test-app')


# ===========================================================================
# Tests for _correlate_skill_runs
# ===========================================================================

class TestCorrelateSkillRuns:

    def test_success(self, synchronizer):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 3
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        synchronizer.build_repo.db.connection.return_value = mock_conn

        synchronizer._correlate_skill_runs('comp-a', 'test-app')

        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert 'UPDATE skill_runs' in sql
        assert 'build_passed' in sql
        mock_conn.commit.assert_called_once()

    def test_no_rows_updated(self, synchronizer):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        synchronizer.build_repo.db.connection.return_value = mock_conn

        synchronizer._correlate_skill_runs('comp-a', 'test-app')

        # commit should NOT be called when rowcount == 0
        mock_conn.commit.assert_not_called()

    def test_failure_caught(self, synchronizer):
        synchronizer.build_repo.db.connection.side_effect = Exception("DB down")
        # Should not raise
        synchronizer._correlate_skill_runs('comp-a', 'test-app')


# ===========================================================================
# Tests for run
# ===========================================================================

class TestRun:

    def test_with_components(self, synchronizer):
        comp = Component(name='comp-a', repository_url='https://github.com/org/repo',
                         branch='main', namespace='test-ns')
        with patch.object(synchronizer, 'sync_component') as mock_sync, \
             patch.object(synchronizer, 'get_component_metadata', return_value=None):
            mock_sync.return_value = {
                'component': 'comp-a',
                'was_failing': True,
                'now_resolved': True,
                'success_recorded': True,
                'current_status': 'Succeeded',
            }
            result = synchronizer.run(components=[comp])

        assert result['components_synced'] == 1
        assert result['resolved'] == 1
        assert result['successes'] == 1
        assert 'duration' in result

    def test_without_components_loads_from_db(self, synchronizer):
        synchronizer.build_repo.find_unresolved_component_names.return_value = [
            'comp-a', 'comp-b'
        ]
        synchronizer.k8s.get_component_metadata.return_value = {
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
        }

        with patch.object(synchronizer, 'sync_component') as mock_sync:
            mock_sync.return_value = {
                'component': 'comp-a',
                'was_failing': False,
                'now_resolved': False,
                'success_recorded': False,
                'current_status': 'unknown',
            }
            result = synchronizer.run()

        assert result['components_synced'] == 2
        synchronizer.build_repo.find_unresolved_component_names.assert_called_once()

    def test_no_unresolved(self, synchronizer):
        synchronizer.build_repo.find_unresolved_component_names.return_value = []

        result = synchronizer.run()

        assert result['components_synced'] == 0
        assert result['resolved'] == 0
        assert result['successes'] == 0

    def test_enriches_metadata(self, synchronizer):
        """Components without repo_url get enriched from cluster metadata."""
        comp = Component(name='comp-a', repository_url='', branch='',
                         namespace='test-ns')
        synchronizer.k8s.get_component_metadata.return_value = {
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
        }

        with patch.object(synchronizer, 'sync_component') as mock_sync:
            mock_sync.return_value = {
                'component': 'comp-a',
                'was_failing': False,
                'now_resolved': False,
                'success_recorded': False,
                'current_status': 'unknown',
            }
            synchronizer.run(components=[comp])

        # get_component_metadata should have been called to enrich
        synchronizer.k8s.get_component_metadata.assert_called_once_with('comp-a')

    def test_metadata_enrichment_returns_none(self, synchronizer):
        """Component not found in cluster -> still synced with empty repo_url."""
        comp = Component(name='comp-missing', repository_url='', branch='',
                         namespace='test-ns')
        synchronizer.k8s.get_component_metadata.return_value = None

        with patch.object(synchronizer, 'sync_component') as mock_sync:
            mock_sync.return_value = {
                'component': 'comp-missing',
                'was_failing': False,
                'now_resolved': False,
                'success_recorded': False,
                'current_status': 'unknown',
            }
            synchronizer.run(components=[comp])

        mock_sync.assert_called_once()

    def test_triage_resolved_counted(self, synchronizer):
        comp = Component(name='comp-a', repository_url='https://github.com/org/repo',
                         branch='main', namespace='test-ns')

        with patch.object(synchronizer, 'sync_component') as mock_sync, \
             patch.object(synchronizer, 'get_component_metadata', return_value=None):
            mock_sync.return_value = {
                'component': 'comp-a',
                'was_failing': True,
                'now_resolved': True,
                'success_recorded': True,
                'current_status': 'Succeeded',
                'triage_auto_resolved': [1, 2, 3],
            }
            result = synchronizer.run(components=[comp])

        assert result['triage_auto_resolved'] == 3

    def test_component_with_repo_url_not_enriched(self, synchronizer):
        """Components that already have repo_url should not be enriched."""
        comp = Component(name='comp-a',
                         repository_url='https://github.com/org/repo',
                         branch='main', namespace='test-ns')

        with patch.object(synchronizer, 'sync_component') as mock_sync, \
             patch.object(synchronizer, 'get_component_metadata') as mock_meta:
            mock_sync.return_value = {
                'component': 'comp-a',
                'was_failing': False,
                'now_resolved': False,
                'success_recorded': False,
                'current_status': 'Succeeded',
            }
            synchronizer.run(components=[comp])

        # get_component_metadata should NOT be called
        mock_meta.assert_not_called()
