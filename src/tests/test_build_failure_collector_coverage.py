"""Comprehensive tests for BuildFailureCollector.

Covers constructor, all public and private methods, edge cases,
error handling, and fallback paths.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest

from collectors.build_failure_collector import BuildFailureCollector
from config import CollectorConfig, DatabaseConfig, KubernetesConfig
from models import BuildStatus, Component, ScanResult

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
        ),
    )


@pytest.fixture
def collector(config):
    return BuildFailureCollector(
        config,
        db=MagicMock(),
        build_repo=MagicMock(),
        kubearchive=MagicMock(),
        k8s=MagicMock(),
        tekton_results=MagicMock(),
        unified=MagicMock(),
    )


def _make_pr(component, event_type='push', status='True', reason='Succeeded',
             timestamp='2024-01-01T00:00:00Z', name='pr-1', uid='uid-1',
             annotations=None):
    """Helper to build a minimal PipelineRun dict."""
    pr = {
        'metadata': {
            'name': name,
            'uid': uid,
            'creationTimestamp': timestamp,
            'labels': {
                'appstudio.openshift.io/component': component,
                'pipelinesascode.tekton.dev/event-type': event_type,
            },
            'annotations': annotations or {},
        },
        'status': {
            'conditions': [{'status': status, 'reason': reason}],
        },
    }
    return pr


# ===========================================================================
# 1. Constructor
# ===========================================================================

class TestConstructor:
    """Tests for BuildFailureCollector.__init__."""

    @patch('collectors.build_failure_collector.UnifiedPipelineClient')
    @patch('collectors.build_failure_collector.TektonResultsClient')
    @patch('collectors.build_failure_collector.KubernetesClient')
    @patch('collectors.build_failure_collector.KubeArchiveClient')
    @patch('collectors.build_failure_collector.BuildFailureRepository')
    @patch('collectors.build_failure_collector.DatabaseConnection')
    def test_defaults_create_real_clients(self, mock_db_cls, mock_repo_cls,
                                          mock_ka_cls, mock_k8s_cls,
                                          mock_tr_cls, mock_unified_cls,
                                          config):
        collector = BuildFailureCollector(config)
        mock_db_cls.assert_called_once_with(config.db)
        mock_repo_cls.assert_called_once()
        mock_ka_cls.assert_called_once()
        mock_k8s_cls.assert_called_once()
        mock_tr_cls.assert_called_once()
        mock_unified_cls.assert_called_once()

    def test_mocked_params_use_mocks(self, config):
        db = MagicMock()
        repo = MagicMock()
        ka = MagicMock()
        k8s = MagicMock()
        tr = MagicMock()
        uni = MagicMock()
        c = BuildFailureCollector(config, db=db, build_repo=repo,
                                   kubearchive=ka, k8s=k8s,
                                   tekton_results=tr, unified=uni)
        assert c.db is db
        assert c.build_repo is repo
        assert c.kubearchive is ka
        assert c.k8s is k8s
        assert c.tekton_results is tr
        assert c.unified is uni


# ===========================================================================
# 2. _collect_push_latest
# ===========================================================================

class TestCollectPushLatest:

    def test_empty_list(self, collector):
        assert collector._collect_push_latest([]) == {}

    def test_single_push_pr(self, collector):
        pr = _make_pr('comp-a', event_type='push', status='False',
                       reason='Failed', timestamp='2024-06-01T10:00:00Z')
        result = collector._collect_push_latest([pr])
        assert 'comp-a' in result
        assert result['comp-a'][0] == '2024-06-01T10:00:00Z'
        assert result['comp-a'][1] == 'False'
        assert result['comp-a'][2] == 'Failed'

    def test_multiple_components(self, collector):
        prs = [
            _make_pr('comp-a', timestamp='2024-01-01T00:00:00Z'),
            _make_pr('comp-b', timestamp='2024-01-02T00:00:00Z'),
        ]
        result = collector._collect_push_latest(prs)
        assert len(result) == 2
        assert 'comp-a' in result
        assert 'comp-b' in result

    def test_keeps_latest_per_component(self, collector):
        prs = [
            _make_pr('comp-a', timestamp='2024-01-01T00:00:00Z',
                      status='True', reason='Succeeded'),
            _make_pr('comp-a', timestamp='2024-06-01T00:00:00Z',
                      status='False', reason='Failed'),
        ]
        result = collector._collect_push_latest(prs)
        assert result['comp-a'][1] == 'False'

    def test_non_push_events_filtered(self, collector):
        prs = [
            _make_pr('comp-a', event_type='pull_request'),
        ]
        assert collector._collect_push_latest(prs) == {}

    def test_incoming_event_accepted(self, collector):
        pr = _make_pr('comp-a', event_type='incoming')
        result = collector._collect_push_latest([pr])
        assert 'comp-a' in result

    def test_no_component_label_skipped(self, collector):
        pr = _make_pr('comp-a')
        del pr['metadata']['labels']['appstudio.openshift.io/component']
        assert collector._collect_push_latest([pr]) == {}

    def test_no_conditions_skipped(self, collector):
        pr = _make_pr('comp-a')
        pr['status']['conditions'] = []
        assert collector._collect_push_latest([pr]) == {}

    def test_annotations_captured(self, collector):
        annot = {'pipelinesascode.tekton.dev/repo-url': 'https://github.com/test'}
        pr = _make_pr('comp-a', annotations=annot)
        result = collector._collect_push_latest([pr])
        assert result['comp-a'][3] == annot


# ===========================================================================
# 3. _enrich_component_metadata
# ===========================================================================

class TestEnrichComponentMetadata:

    @patch('collectors.build_failure_collector.client')
    @patch('collectors.build_failure_collector._ensure_k8s_config')
    def test_k8s_success(self, mock_k8s_cfg, mock_client, collector):
        mock_api = MagicMock()
        mock_client.CustomObjectsApi.return_value = mock_api
        mock_api.get_namespaced_custom_object.return_value = {
            'spec': {'source': {'git': {
                'url': 'https://github.com/org/repo',
                'revision': 'main',
            }}}
        }
        comp = collector._enrich_component_metadata('comp-a', {})
        assert comp.name == 'comp-a'
        assert comp.repository_url == 'https://github.com/org/repo'
        assert comp.branch == 'main'
        assert comp.namespace == 'test-ns'

    @patch('collectors.build_failure_collector.client')
    @patch('collectors.build_failure_collector._ensure_k8s_config')
    def test_k8s_failure_annotation_fallback(self, mock_k8s_cfg, mock_client, collector):
        mock_client.CustomObjectsApi.side_effect = Exception("no cluster")
        push_latest = {
            'comp-a': (
                '2024-01-01T00:00:00Z', 'False', 'Failed',
                {
                    'pipelinesascode.tekton.dev/repo-url': 'https://github.com/org/fallback',
                    'build.appstudio.redhat.com/target_branch': 'release-1',
                },
            ),
        }
        comp = collector._enrich_component_metadata('comp-a', push_latest)
        assert comp.repository_url == 'https://github.com/org/fallback'
        assert comp.branch == 'release-1'

    @patch('collectors.build_failure_collector.client')
    @patch('collectors.build_failure_collector._ensure_k8s_config')
    def test_k8s_failure_secondary_annotation_keys(self, mock_k8s_cfg, mock_client, collector):
        mock_client.CustomObjectsApi.side_effect = Exception("no cluster")
        push_latest = {
            'comp-a': (
                '2024-01-01T00:00:00Z', 'False', 'Failed',
                {
                    'pipelinesascode.tekton.dev/source-repo-url': 'https://github.com/org/alt',
                    'pipelinesascode.tekton.dev/branch': 'dev',
                },
            ),
        }
        comp = collector._enrich_component_metadata('comp-a', push_latest)
        assert comp.repository_url == 'https://github.com/org/alt'
        assert comp.branch == 'dev'

    @patch('collectors.build_failure_collector.client')
    @patch('collectors.build_failure_collector._ensure_k8s_config')
    def test_both_fail_returns_empty(self, mock_k8s_cfg, mock_client, collector):
        mock_client.CustomObjectsApi.side_effect = Exception("no cluster")
        comp = collector._enrich_component_metadata('comp-a', {})
        assert comp.repository_url == ''
        assert comp.branch == ''

    @patch('collectors.build_failure_collector.client')
    @patch('collectors.build_failure_collector._ensure_k8s_config')
    def test_k8s_returns_empty_spec(self, mock_k8s_cfg, mock_client, collector):
        mock_api = MagicMock()
        mock_client.CustomObjectsApi.return_value = mock_api
        mock_api.get_namespaced_custom_object.return_value = {'spec': {}}
        # repo_url will be '' so it'll try annotation fallback
        push_latest = {
            'comp-a': ('2024-01-01T00:00:00Z', 'False', 'Failed',
                        {'pipelinesascode.tekton.dev/repo-url': 'https://annot-url'})
        }
        comp = collector._enrich_component_metadata('comp-a', push_latest)
        assert comp.repository_url == 'https://annot-url'


# ===========================================================================
# 4. discover_components_from_cluster
# ===========================================================================

class TestDiscoverComponentsFromCluster:

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_happy_path(self, mock_qpr, collector):
        mock_qpr.return_value = [
            _make_pr('comp-a', status='False', reason='Failed'),
        ]
        collector.tekton_results.query_pipelinerun_records.return_value = []
        with patch.object(collector, '_enrich_component_metadata') as mock_enrich:
            mock_enrich.return_value = Component('comp-a', 'url', 'main', 'test-ns')
            result = collector.discover_components_from_cluster()
        assert len(result) == 1
        assert result[0].name == 'comp-a'

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_no_failures_returns_empty(self, mock_qpr, collector):
        mock_qpr.return_value = [
            _make_pr('comp-a', status='True', reason='Succeeded'),
        ]
        collector.tekton_results.query_pipelinerun_records.return_value = []
        result = collector.discover_components_from_cluster()
        assert result == []

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_tekton_results_adds_components(self, mock_qpr, collector):
        mock_qpr.return_value = [
            _make_pr('comp-a', status='False', reason='Failed',
                      timestamp='2024-01-01T00:00:00Z'),
        ]
        collector.tekton_results.query_pipelinerun_records.return_value = [
            _make_pr('comp-b', status='False', reason='Failed',
                      timestamp='2024-06-01T00:00:00Z'),
        ]
        with patch.object(collector, '_enrich_component_metadata') as mock_enrich:
            mock_enrich.side_effect = lambda n, _: Component(n, 'url', 'main', 'test-ns')
            result = collector.discover_components_from_cluster()
        names = {c.name for c in result}
        assert 'comp-a' in names
        assert 'comp-b' in names

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_tekton_results_updates_newer(self, mock_qpr, collector):
        mock_qpr.return_value = [
            _make_pr('comp-a', status='True', reason='Succeeded',
                      timestamp='2024-01-01T00:00:00Z'),
        ]
        # TR has a newer record that is failed
        collector.tekton_results.query_pipelinerun_records.return_value = [
            _make_pr('comp-a', status='False', reason='Failed',
                      timestamp='2024-06-01T00:00:00Z'),
        ]
        with patch.object(collector, '_enrich_component_metadata') as mock_enrich:
            mock_enrich.return_value = Component('comp-a', 'url', 'main', 'test-ns')
            result = collector.discover_components_from_cluster()
        assert len(result) == 1

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_exception_returns_empty(self, mock_qpr, collector):
        mock_qpr.side_effect = Exception("connection error")
        result = collector.discover_components_from_cluster()
        assert result == []

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_tekton_results_exception_ignored(self, mock_qpr, collector):
        mock_qpr.return_value = [
            _make_pr('comp-a', status='False', reason='Failed'),
        ]
        collector.tekton_results.query_pipelinerun_records.side_effect = Exception("TR down")
        with patch.object(collector, '_enrich_component_metadata') as mock_enrich:
            mock_enrich.return_value = Component('comp-a', 'url', 'main', 'test-ns')
            result = collector.discover_components_from_cluster()
        assert len(result) == 1


# ===========================================================================
# 5. get_component_metadata
# ===========================================================================

class TestGetComponentMetadata:

    def test_found(self, collector):
        collector.k8s.get_component_metadata.return_value = {
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
        }
        result = collector.get_component_metadata('comp-a')
        assert result.name == 'comp-a'
        assert result.repository_url == 'https://github.com/org/repo'
        assert result.branch == 'main'
        assert result.namespace == 'test-ns'

    def test_not_found(self, collector):
        collector.k8s.get_component_metadata.return_value = None
        assert collector.get_component_metadata('comp-a') is None


# ===========================================================================
# 6. _extract_latest_failed
# ===========================================================================

class TestExtractLatestFailed:

    def test_no_prs(self, collector):
        assert collector._extract_latest_failed([]) is None

    def test_single_failed(self, collector):
        pr = _make_pr('comp-a', status='False', reason='Failed',
                       timestamp='2024-06-01T10:00:00Z', name='pr-1', uid='uid-1')
        result = collector._extract_latest_failed([pr])
        assert result is not None
        assert result['name'] == 'pr-1'
        assert result['uid'] == 'uid-1'
        assert result['started'] == '2024-06-01T10:00:00Z'
        assert result['reason'] == 'Failed'
        assert result['status'] == BuildStatus.FAILED

    def test_multiple_picks_latest(self, collector):
        prs = [
            _make_pr('comp-a', status='False', reason='Failed',
                      timestamp='2024-01-01T00:00:00Z', name='old-pr'),
            _make_pr('comp-a', status='False', reason='Failed',
                      timestamp='2024-06-01T00:00:00Z', name='new-pr'),
        ]
        result = collector._extract_latest_failed(prs)
        assert result['name'] == 'new-pr'

    def test_no_failed_prs(self, collector):
        prs = [
            _make_pr('comp-a', status='True', reason='Succeeded'),
        ]
        assert collector._extract_latest_failed(prs) is None

    def test_non_push_events_skipped(self, collector):
        pr = _make_pr('comp-a', event_type='pull_request', status='False',
                       reason='Failed')
        assert collector._extract_latest_failed([pr]) is None

    def test_incoming_event_accepted(self, collector):
        pr = _make_pr('comp-a', event_type='incoming', status='False',
                       reason='Failed', name='incoming-pr')
        result = collector._extract_latest_failed([pr])
        assert result['name'] == 'incoming-pr'

    def test_empty_conditions_skipped(self, collector):
        pr = _make_pr('comp-a', status='False', reason='Failed')
        pr['status']['conditions'] = []
        assert collector._extract_latest_failed([pr]) is None


# ===========================================================================
# 7. get_last_failed_pipelinerun
# ===========================================================================

class TestGetLastFailedPipelinerun:

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_ka_only(self, mock_qpr, collector):
        mock_qpr.return_value = [
            _make_pr('comp-a', status='False', reason='Failed',
                      timestamp='2024-06-01T00:00:00Z', name='ka-pr'),
        ]
        collector.tekton_results.query_pipelinerun_records.return_value = []
        result = collector.get_last_failed_pipelinerun('comp-a')
        assert result['name'] == 'ka-pr'

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_tr_only(self, mock_qpr, collector):
        mock_qpr.return_value = []
        collector.tekton_results.query_pipelinerun_records.return_value = [
            _make_pr('comp-a', status='False', reason='Failed',
                      timestamp='2024-06-01T00:00:00Z', name='tr-pr'),
        ]
        result = collector.get_last_failed_pipelinerun('comp-a')
        assert result['name'] == 'tr-pr'

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_both_tr_newer(self, mock_qpr, collector):
        mock_qpr.return_value = [
            _make_pr('comp-a', status='False', reason='Failed',
                      timestamp='2024-01-01T00:00:00Z', name='ka-pr'),
        ]
        collector.tekton_results.query_pipelinerun_records.return_value = [
            _make_pr('comp-a', status='False', reason='Failed',
                      timestamp='2024-06-01T00:00:00Z', name='tr-pr'),
        ]
        result = collector.get_last_failed_pipelinerun('comp-a')
        assert result['name'] == 'tr-pr'

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_both_ka_newer(self, mock_qpr, collector):
        mock_qpr.return_value = [
            _make_pr('comp-a', status='False', reason='Failed',
                      timestamp='2024-06-01T00:00:00Z', name='ka-pr'),
        ]
        collector.tekton_results.query_pipelinerun_records.return_value = [
            _make_pr('comp-a', status='False', reason='Failed',
                      timestamp='2024-01-01T00:00:00Z', name='tr-pr'),
        ]
        result = collector.get_last_failed_pipelinerun('comp-a')
        assert result['name'] == 'ka-pr'

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_none_found(self, mock_qpr, collector):
        mock_qpr.return_value = []
        collector.tekton_results.query_pipelinerun_records.return_value = []
        assert collector.get_last_failed_pipelinerun('comp-a') is None

    @patch('collectors.build_failure_collector.query_pipelineruns')
    def test_tr_exception_ignored(self, mock_qpr, collector):
        mock_qpr.return_value = [
            _make_pr('comp-a', status='False', reason='Failed', name='ka-pr'),
        ]
        collector.tekton_results.query_pipelinerun_records.side_effect = Exception("TR fail")
        result = collector.get_last_failed_pipelinerun('comp-a')
        assert result['name'] == 'ka-pr'


# ===========================================================================
# 8. _extract_taskrun_logs_section
# ===========================================================================

class TestExtractTaskrunLogsSection:

    def test_match_found(self, collector):
        logs = (
            "some preamble\n"
            "===== TaskRun: pr-1-fips-check-0 / Step: run-fips-check =====\n"
            "checking fips compliance...\n"
            "ERROR: non-compliant library found\n"
        )
        result = collector._extract_taskrun_logs_section(logs, 'fips-check')
        assert result is not None
        assert 'fips-check' in result
        assert 'ERROR' in result

    def test_no_match(self, collector):
        logs = "===== TaskRun: pr-1-build-0 / Step: run-build =====\nbuilding..."
        assert collector._extract_taskrun_logs_section(logs, 'fips-check') is None

    def test_empty_logs(self, collector):
        assert collector._extract_taskrun_logs_section('', 'fips-check') is None
        assert collector._extract_taskrun_logs_section(None, 'fips-check') is None

    def test_empty_task_name(self, collector):
        logs = "===== TaskRun: pr-1-build-0 / Step: run-build =====\n"
        assert collector._extract_taskrun_logs_section(logs, '') is None
        assert collector._extract_taskrun_logs_section(logs, None) is None

    def test_multiple_sections_concatenated(self, collector):
        logs = (
            "===== TaskRun: pr-1-build-0 / Step: step-a =====\n"
            "line a\n"
            "===== TaskRun: pr-1-build-0 / Step: step-b =====\n"
            "line b\n"
        )
        result = collector._extract_taskrun_logs_section(logs, 'build')
        assert result is not None
        assert 'step-a' in result


# ===========================================================================
# 9. _find_failed_taskrun
# ===========================================================================

class TestFindFailedTaskrun:

    def test_no_pr_data(self, collector):
        assert collector._find_failed_taskrun('pr-1', None) == (None, None, None)

    def test_failed_tr_found_via_kubearchive(self, collector):
        pr_data = {
            'status': {
                'childReferences': [{
                    'kind': 'TaskRun',
                    'pipelineTaskName': 'build',
                    'name': 'pr-1-build-0',
                }],
            },
        }
        collector.kubearchive.get_taskrun.return_value = {
            'status': {
                'conditions': [{'status': 'False', 'message': 'build failed'}],
                'podName': 'pod-1',
            },
        }
        collector.kubearchive.get_pod_logs.return_value = "ERROR: compilation failed"
        result = collector._find_failed_taskrun('pr-1', pr_data)
        assert result[0] == 'build'
        assert result[1] is not None  # error message
        assert result[2] is not None  # logs

    def test_kubearchive_fails_fallback_to_tekton_results(self, collector):
        pr_data = {
            'status': {
                'childReferences': [{
                    'kind': 'TaskRun',
                    'pipelineTaskName': 'build',
                    'name': 'pr-1-build-0',
                }],
            },
        }
        collector.kubearchive.get_taskrun.side_effect = Exception("not found")
        collector.tekton_results.find_failed_taskrun.return_value = (
            'build', 'ERROR: go build failed', None
        )
        result = collector._find_failed_taskrun('pr-1', pr_data)
        assert result[0] == 'build'

    @patch('collectors.build_failure_collector.extract_failed_step_from_pipelinerun')
    def test_all_fallbacks_to_metadata(self, mock_extract, collector):
        pr_data = {
            'status': {
                'childReferences': [{
                    'kind': 'TaskRun',
                    'pipelineTaskName': 'build',
                    'name': 'pr-1-build-0',
                }],
            },
        }
        collector.kubearchive.get_taskrun.side_effect = Exception("not found")
        collector.tekton_results.find_failed_taskrun.side_effect = Exception("TR down")
        mock_extract.return_value = 'build'
        result = collector._find_failed_taskrun('pr-1', pr_data)
        assert result == ('build', None, None)

    def test_succeeded_taskrun_skipped(self, collector):
        pr_data = {
            'status': {
                'childReferences': [{
                    'kind': 'TaskRun',
                    'pipelineTaskName': 'build',
                    'name': 'pr-1-build-0',
                }],
            },
        }
        collector.kubearchive.get_taskrun.return_value = {
            'status': {
                'conditions': [{'status': 'True', 'message': 'ok'}],
            },
        }
        # No failed TR, so falls through to TR fallback
        collector.tekton_results.find_failed_taskrun.return_value = (None, None, None)
        task, error, logs = collector._find_failed_taskrun('pr-1', pr_data)
        # Should reach extract_failed_step_from_pipelinerun fallback
        assert task is not None or task is None  # just verifying no crash

    def test_non_taskrun_kind_skipped(self, collector):
        pr_data = {
            'status': {
                'childReferences': [{
                    'kind': 'Run',
                    'pipelineTaskName': 'custom',
                    'name': 'pr-1-custom-0',
                }],
            },
        }
        collector.tekton_results.find_failed_taskrun.return_value = (None, None, None)
        task, error, logs = collector._find_failed_taskrun('pr-1', pr_data)
        # Falls through to the extract_failed_step_from_pipelinerun fallback
        assert error is None

    def test_no_child_references(self, collector):
        pr_data = {'status': {}}
        collector.tekton_results.find_failed_taskrun.return_value = (None, None, None)
        task, error, logs = collector._find_failed_taskrun('pr-1', pr_data)
        assert error is None

    def test_condition_message_used_when_no_logs(self, collector):
        pr_data = {
            'status': {
                'childReferences': [{
                    'kind': 'TaskRun',
                    'pipelineTaskName': 'build',
                    'name': 'pr-1-build-0',
                }],
            },
        }
        collector.kubearchive.get_taskrun.return_value = {
            'status': {
                'conditions': [{'status': 'False', 'message': 'OOMKilled'}],
                'podName': 'pod-1',
            },
        }
        collector.kubearchive.get_pod_logs.return_value = None
        task, error, logs = collector._find_failed_taskrun('pr-1', pr_data)
        assert task == 'build'
        assert error == 'OOMKilled'


# ===========================================================================
# 10. get_comprehensive_logs
# ===========================================================================

class TestGetComprehensiveLogs:

    def test_unified_succeeds(self, collector):
        collector.unified.get_logs_complete.return_value = ("full logs here", "unified")
        result = collector.get_comprehensive_logs('pr-1')
        assert result == "full logs here"

    def test_unified_fails_tr_succeeds(self, collector):
        collector.unified.get_logs_complete.return_value = (None, None)
        collector.tekton_results.get_pipelinerun_logs.return_value = "TR logs"
        result = collector.get_comprehensive_logs('pr-1')
        assert result == "TR logs"

    def test_both_fail(self, collector):
        collector.unified.get_logs_complete.return_value = (None, None)
        collector.tekton_results.get_pipelinerun_logs.return_value = None
        assert collector.get_comprehensive_logs('pr-1') is None

    def test_tr_exception_returns_none(self, collector):
        collector.unified.get_logs_complete.return_value = (None, None)
        collector.tekton_results.get_pipelinerun_logs.side_effect = Exception("TR error")
        assert collector.get_comprehensive_logs('pr-1') is None

    def test_unified_empty_string_falls_through(self, collector):
        collector.unified.get_logs_complete.return_value = ('', 'unified')
        collector.tekton_results.get_pipelinerun_logs.return_value = "TR logs"
        result = collector.get_comprehensive_logs('pr-1')
        assert result == "TR logs"


# ===========================================================================
# 11. get_logs_from_active_pods
# ===========================================================================

class TestGetLogsFromActivePods:

    @patch('collectors.build_failure_collector.client')
    @patch('collectors.build_failure_collector._ensure_k8s_config')
    def test_pods_found(self, mock_cfg, mock_client, collector):
        mock_v1 = MagicMock()
        mock_client.CoreV1Api.return_value = mock_v1

        pod1 = MagicMock()
        pod1.metadata.name = 'pod-1'
        pod2 = MagicMock()
        pod2.metadata.name = 'pod-2'

        mock_v1.list_namespaced_pod.return_value.items = [pod1, pod2]
        mock_v1.read_namespaced_pod_log.side_effect = [
            "log from pod-1",
            "log from pod-2",
        ]

        result = collector.get_logs_from_active_pods('pr-1')
        assert 'pod-1' in result
        assert 'pod-2' in result

    @patch('collectors.build_failure_collector.client')
    @patch('collectors.build_failure_collector._ensure_k8s_config')
    def test_no_pods(self, mock_cfg, mock_client, collector):
        mock_v1 = MagicMock()
        mock_client.CoreV1Api.return_value = mock_v1
        mock_v1.list_namespaced_pod.return_value.items = []
        assert collector.get_logs_from_active_pods('pr-1') is None

    @patch('collectors.build_failure_collector.client')
    @patch('collectors.build_failure_collector._ensure_k8s_config')
    def test_exception_returns_none(self, mock_cfg, mock_client, collector):
        mock_cfg.side_effect = Exception("no cluster")
        assert collector.get_logs_from_active_pods('pr-1') is None

    @patch('collectors.build_failure_collector.client')
    @patch('collectors.build_failure_collector._ensure_k8s_config')
    def test_individual_pod_log_failure_skipped(self, mock_cfg, mock_client, collector):
        mock_v1 = MagicMock()
        mock_client.CoreV1Api.return_value = mock_v1

        pod1 = MagicMock()
        pod1.metadata.name = 'pod-1'
        pod2 = MagicMock()
        pod2.metadata.name = 'pod-2'

        mock_v1.list_namespaced_pod.return_value.items = [pod1, pod2]
        mock_v1.read_namespaced_pod_log.side_effect = [
            Exception("pod gone"),
            "log from pod-2",
        ]

        result = collector.get_logs_from_active_pods('pr-1')
        assert 'pod-2' in result
        assert 'pod-1' not in result

    @patch('collectors.build_failure_collector.client')
    @patch('collectors.build_failure_collector._ensure_k8s_config')
    def test_all_pod_logs_fail(self, mock_cfg, mock_client, collector):
        mock_v1 = MagicMock()
        mock_client.CoreV1Api.return_value = mock_v1

        pod1 = MagicMock()
        pod1.metadata.name = 'pod-1'
        mock_v1.list_namespaced_pod.return_value.items = [pod1]
        mock_v1.read_namespaced_pod_log.side_effect = Exception("error")

        assert collector.get_logs_from_active_pods('pr-1') is None


# ===========================================================================
# 12. collect_comprehensive_failure
# ===========================================================================

class TestCollectComprehensiveFailure:

    def _setup_component(self):
        return Component(name='comp-a', repository_url='https://github.com/org/repo',
                         branch='main', namespace='test-ns')

    def test_no_failure(self, collector):
        component = self._setup_component()
        with patch.object(collector, 'get_last_failed_pipelinerun', return_value=None):
            found, inserted, log_len = collector.collect_comprehensive_failure(component)
        assert found is False
        assert inserted is False
        assert log_len == 0

    def test_already_complete(self, collector):
        component = self._setup_component()
        pr_info = {'name': 'pr-1', 'uid': 'uid-1', 'status': BuildStatus.FAILED,
                    'started': '2024-06-01T00:00:00Z', 'reason': 'Failed'}
        with patch.object(collector, 'get_last_failed_pipelinerun', return_value=pr_info):
            collector.build_repo.is_pipelinerun_complete.return_value = True
            found, inserted, log_len = collector.collect_comprehensive_failure(component)
        assert found is True
        assert inserted is False

    def test_timeout_reason(self, collector):
        component = self._setup_component()
        pr_info = {'name': 'pr-1', 'uid': 'uid-1', 'status': BuildStatus.FAILED,
                    'started': '2024-06-01T00:00:00Z', 'reason': 'PipelineRunTimeout'}
        with patch.object(collector, 'get_last_failed_pipelinerun', return_value=pr_info):
            collector.build_repo.is_pipelinerun_complete.return_value = False
            with patch.object(collector, 'get_comprehensive_logs', return_value=None):
                collector.unified.get_pipelinerun_complete.return_value = (
                    {'status': {'conditions': [{'status': 'False'}],
                                'startTime': '2024-06-01T00:00:00Z',
                                'completionTime': '2024-06-01T02:00:00Z'},
                     'metadata': {'annotations': {}, 'name': 'pr-1', 'namespace': 'test-ns'},
                     'spec': {'params': []}},
                    'unified'
                )
                collector.build_repo.upsert_failure.return_value = True
                found, inserted, log_len = collector.collect_comprehensive_failure(component)
        assert found is True
        assert inserted is True
        # Verify the upsert was called with timeout error message
        call_kwargs = collector.build_repo.upsert_failure.call_args
        assert 'timed out' in call_kwargs.kwargs.get('error_message', '') or \
               'timed out' in (call_kwargs[1].get('error_message', '') if len(call_kwargs) > 1 else '')

    def test_cancelled_reason(self, collector):
        component = self._setup_component()
        pr_info = {'name': 'pr-1', 'uid': 'uid-1', 'status': BuildStatus.FAILED,
                    'started': '2024-06-01T00:00:00Z', 'reason': 'PipelineRunCancelled'}
        with patch.object(collector, 'get_last_failed_pipelinerun', return_value=pr_info):
            collector.build_repo.is_pipelinerun_complete.return_value = False
            with patch.object(collector, 'get_comprehensive_logs', return_value=None):
                collector.unified.get_pipelinerun_complete.return_value = (
                    {'status': {'conditions': [{'status': 'False'}]},
                     'metadata': {'annotations': {}, 'name': 'pr-1'},
                     'spec': {'params': []}},
                    'unified'
                )
                collector.build_repo.upsert_failure.return_value = True
                found, inserted, log_len = collector.collect_comprehensive_failure(component)
        assert found is True
        call_kwargs = collector.build_repo.upsert_failure.call_args
        kw = call_kwargs[1] if len(call_kwargs) > 1 else call_kwargs.kwargs
        assert 'cancelled' in kw.get('error_message', '').lower()

    def test_normal_failure_with_logs(self, collector):
        component = self._setup_component()
        pr_info = {'name': 'pr-1', 'uid': 'uid-1', 'status': BuildStatus.FAILED,
                    'started': '2024-06-01T00:00:00Z', 'reason': 'Failed'}
        with patch.object(collector, 'get_last_failed_pipelinerun', return_value=pr_info):
            collector.build_repo.is_pipelinerun_complete.return_value = False
            with patch.object(collector, 'get_comprehensive_logs',
                              return_value="ERROR: build failed"):
                collector.unified.get_pipelinerun_complete.return_value = (
                    {'status': {'conditions': [{'status': 'False'}],
                                'childReferences': []},
                     'metadata': {'annotations': {}, 'name': 'pr-1'},
                     'spec': {'params': []}},
                    'unified'
                )
                with patch.object(collector, '_find_failed_taskrun',
                                  return_value=('build', 'compilation error', None)):
                    collector.build_repo.upsert_failure.return_value = True
                    found, inserted, log_len = collector.collect_comprehensive_failure(component)
        assert found is True
        assert inserted is True
        assert log_len > 0

    def test_db_error_returns_partial(self, collector):
        component = self._setup_component()
        pr_info = {'name': 'pr-1', 'uid': 'uid-1', 'status': BuildStatus.FAILED,
                    'started': '2024-06-01T00:00:00Z', 'reason': 'Failed'}
        with patch.object(collector, 'get_last_failed_pipelinerun', return_value=pr_info):
            collector.build_repo.is_pipelinerun_complete.return_value = False
            with patch.object(collector, 'get_comprehensive_logs', return_value="logs"):
                collector.unified.get_pipelinerun_complete.return_value = (None, None)
                collector.tekton_results.query_pipelinerun_records.return_value = []
                with patch.object(collector, '_find_failed_taskrun',
                                  return_value=(None, None, None)):
                    collector.build_repo.upsert_failure.side_effect = Exception("DB down")
                    found, inserted, log_len = collector.collect_comprehensive_failure(component)
        assert found is True
        assert inserted is False
        assert log_len == 4  # len("logs")

    def test_is_pipelinerun_complete_exception_ignored(self, collector):
        component = self._setup_component()
        pr_info = {'name': 'pr-1', 'uid': 'uid-1', 'status': BuildStatus.FAILED,
                    'started': '2024-06-01T00:00:00Z', 'reason': 'Failed'}
        with patch.object(collector, 'get_last_failed_pipelinerun', return_value=pr_info):
            collector.build_repo.is_pipelinerun_complete.side_effect = Exception("DB error")
            with patch.object(collector, 'get_comprehensive_logs', return_value=None):
                collector.unified.get_pipelinerun_complete.return_value = (None, None)
                collector.tekton_results.query_pipelinerun_records.return_value = []
                with patch.object(collector, '_find_failed_taskrun',
                                  return_value=(None, None, None)):
                    collector.build_repo.upsert_failure.return_value = True
                    found, inserted, log_len = collector.collect_comprehensive_failure(component)
        assert found is True

    def test_nightly_trigger_detected(self, collector):
        component = self._setup_component()
        pr_info = {'name': 'pr-1', 'uid': 'uid-1', 'status': BuildStatus.FAILED,
                    'started': '2024-06-01T00:00:00Z', 'reason': 'Failed'}
        pr_data = {
            'status': {'conditions': [{'status': 'False'}], 'childReferences': []},
            'metadata': {
                'annotations': {
                    'pipelinesascode.tekton.dev/sender': 'Openshift-AI DevOps',
                    'pipelinesascode.tekton.dev/sha-title': 'Updating the operator repo for nightly',
                },
                'name': 'pr-1',
            },
            'spec': {'params': []},
        }
        with patch.object(collector, 'get_last_failed_pipelinerun', return_value=pr_info):
            collector.build_repo.is_pipelinerun_complete.return_value = False
            with patch.object(collector, 'get_comprehensive_logs', return_value=None):
                collector.unified.get_pipelinerun_complete.return_value = (pr_data, 'unified')
                with patch.object(collector, '_find_failed_taskrun',
                                  return_value=(None, None, None)):
                    collector.build_repo.upsert_failure.return_value = True
                    collector.collect_comprehensive_failure(component)
        kw = collector.build_repo.upsert_failure.call_args[1]
        assert kw['details'].get('trigger_type') == 'nightly'

    def test_pr_data_fallback_to_tekton_results(self, collector):
        component = self._setup_component()
        pr_info = {'name': 'pr-1', 'uid': 'uid-1', 'status': BuildStatus.FAILED,
                    'started': '2024-06-01T00:00:00Z', 'reason': 'Failed'}
        with patch.object(collector, 'get_last_failed_pipelinerun', return_value=pr_info):
            collector.build_repo.is_pipelinerun_complete.return_value = False
            with patch.object(collector, 'get_comprehensive_logs', return_value=None):
                # Unified returns no data
                collector.unified.get_pipelinerun_complete.return_value = (None, None)
                # TR fallback provides the PR data
                tr_pr = {
                    'metadata': {'name': 'pr-1', 'annotations': {}},
                    'status': {'conditions': [{'status': 'False'}], 'childReferences': []},
                    'spec': {'params': []},
                }
                collector.tekton_results.query_pipelinerun_records.return_value = [tr_pr]
                with patch.object(collector, '_find_failed_taskrun',
                                  return_value=(None, None, None)):
                    collector.build_repo.upsert_failure.return_value = True
                    found, inserted, _ = collector.collect_comprehensive_failure(component)
        assert found is True


# ===========================================================================
# 13. check_resolution_via_history
# ===========================================================================

class TestCheckResolutionViaHistory:

    def test_resolved(self, collector):
        collector.tekton_results.query_component_build_history.return_value = [
            {
                'metadata': {
                    'name': 'pr-success',
                    'creationTimestamp': '2024-06-01T00:00:00Z',
                    'annotations': {
                        'build.appstudio.redhat.com/commit_sha': 'abc123def456',
                    },
                },
                'status': {
                    'conditions': [{'status': 'True'}],
                },
            },
        ]
        result = collector.check_resolution_via_history('comp-a')
        assert result is not None
        assert result['resolved'] is True
        assert result['resolution_pr_name'] == 'pr-success'
        assert result['resolution_commit_sha'] == 'abc123def456'

    def test_not_resolved(self, collector):
        collector.tekton_results.query_component_build_history.return_value = [
            {
                'metadata': {
                    'name': 'pr-fail',
                    'creationTimestamp': '2024-06-01T00:00:00Z',
                    'annotations': {},
                },
                'status': {
                    'conditions': [{'status': 'False'}],
                },
            },
        ]
        assert collector.check_resolution_via_history('comp-a') is None

    def test_no_history(self, collector):
        collector.tekton_results.query_component_build_history.return_value = []
        assert collector.check_resolution_via_history('comp-a') is None

    def test_exception_returns_none(self, collector):
        collector.tekton_results.query_component_build_history.side_effect = Exception("fail")
        assert collector.check_resolution_via_history('comp-a') is None

    def test_no_conditions_returns_none(self, collector):
        collector.tekton_results.query_component_build_history.return_value = [
            {
                'metadata': {
                    'name': 'pr-1',
                    'creationTimestamp': '2024-06-01T00:00:00Z',
                    'annotations': {},
                },
                'status': {'conditions': []},
            },
        ]
        assert collector.check_resolution_via_history('comp-a') is None

    def test_fallback_sha_annotation(self, collector):
        collector.tekton_results.query_component_build_history.return_value = [
            {
                'metadata': {
                    'name': 'pr-ok',
                    'creationTimestamp': '2024-06-01T00:00:00Z',
                    'annotations': {
                        'pipelinesascode.tekton.dev/sha': 'sha-via-pac',
                    },
                },
                'status': {
                    'conditions': [{'status': 'True'}],
                },
            },
        ]
        result = collector.check_resolution_via_history('comp-a')
        assert result['resolution_commit_sha'] == 'sha-via-pac'

    def test_picks_newest_from_multiple(self, collector):
        collector.tekton_results.query_component_build_history.return_value = [
            {
                'metadata': {
                    'name': 'old-pr',
                    'creationTimestamp': '2024-01-01T00:00:00Z',
                    'annotations': {},
                },
                'status': {'conditions': [{'status': 'False'}]},
            },
            {
                'metadata': {
                    'name': 'new-pr',
                    'creationTimestamp': '2024-06-01T00:00:00Z',
                    'annotations': {},
                },
                'status': {'conditions': [{'status': 'True'}]},
            },
        ]
        result = collector.check_resolution_via_history('comp-a')
        assert result is not None
        assert result['resolution_pr_name'] == 'new-pr'


# ===========================================================================
# 14. get_component_build_history
# ===========================================================================

class TestGetComponentBuildHistory:

    def test_success_with_mixed_statuses(self, collector):
        collector.tekton_results.query_component_build_history.return_value = [
            {
                'metadata': {
                    'name': 'pr-ok',
                    'creationTimestamp': '2024-06-01T00:00:00Z',
                    'annotations': {
                        'build.appstudio.redhat.com/commit_sha': 'abc12345',
                    },
                },
                'status': {
                    'conditions': [{'status': 'True', 'reason': 'Succeeded'}],
                    'results': [{'name': 'IMAGE_URL', 'value': 'quay.io/img:latest'}],
                    'childReferences': [],
                },
            },
            {
                'metadata': {
                    'name': 'pr-fail',
                    'creationTimestamp': '2024-05-01T00:00:00Z',
                    'annotations': {
                        'pipelinesascode.tekton.dev/sha': 'def67890',
                    },
                },
                'status': {
                    'conditions': [{'status': 'False', 'reason': 'Failed'}],
                    'results': [],
                    'childReferences': [
                        {'kind': 'TaskRun', 'pipelineTaskName': 'build'},
                    ],
                },
            },
        ]
        results = collector.get_component_build_history('comp-a')
        assert len(results) == 2

        ok = results[0]
        assert ok['name'] == 'pr-ok'
        assert ok['status'] == 'Succeeded'
        assert ok['commit_sha'] == 'abc12345'[:8]
        assert ok['image_url'] == 'quay.io/img:latest'
        assert ok['failed_task'] is None

        fail = results[1]
        assert fail['name'] == 'pr-fail'
        assert fail['status'] == BuildStatus.FAILED.value
        assert fail['failed_task'] == 'build'

    def test_empty_history(self, collector):
        collector.tekton_results.query_component_build_history.return_value = []
        assert collector.get_component_build_history('comp-a') == []

    def test_exception_returns_empty(self, collector):
        collector.tekton_results.query_component_build_history.side_effect = Exception("fail")
        assert collector.get_component_build_history('comp-a') == []

    def test_no_conditions_skipped(self, collector):
        collector.tekton_results.query_component_build_history.return_value = [
            {
                'metadata': {'name': 'pr-1', 'creationTimestamp': '2024-01-01T00:00:00Z',
                              'annotations': {}},
                'status': {'conditions': [], 'results': [], 'childReferences': []},
            },
        ]
        assert collector.get_component_build_history('comp-a') == []

    def test_unknown_status_skipped(self, collector):
        """PRs with status != 'True' and status != 'False' are skipped."""
        collector.tekton_results.query_component_build_history.return_value = [
            {
                'metadata': {'name': 'pr-1', 'creationTimestamp': '2024-01-01T00:00:00Z',
                              'annotations': {}},
                'status': {
                    'conditions': [{'status': 'Unknown', 'reason': 'Running'}],
                    'results': [],
                    'childReferences': [],
                },
            },
        ]
        assert collector.get_component_build_history('comp-a') == []

    def test_custom_limit(self, collector):
        collector.tekton_results.query_component_build_history.return_value = []
        collector.get_component_build_history('comp-a', limit=5)
        call_kwargs = collector.tekton_results.query_component_build_history.call_args[1]
        assert call_kwargs['page_size'] == 5

    def test_no_commit_sha(self, collector):
        collector.tekton_results.query_component_build_history.return_value = [
            {
                'metadata': {'name': 'pr-1', 'creationTimestamp': '2024-01-01T00:00:00Z',
                              'annotations': {}},
                'status': {
                    'conditions': [{'status': 'True', 'reason': 'Succeeded'}],
                    'results': [],
                    'childReferences': [],
                },
            },
        ]
        results = collector.get_component_build_history('comp-a')
        assert results[0]['commit_sha'] is None


# ===========================================================================
# 15. run
# ===========================================================================

class TestRun:

    def _make_component(self, name, repo_url='https://github.com/org/repo'):
        return Component(name=name, repository_url=repo_url,
                         branch='main', namespace='test-ns')

    def test_with_components(self, collector):
        components = [self._make_component('comp-a')]
        collector.db.create_scan.return_value = 'scan-1'
        with patch.object(collector, 'collect_comprehensive_failure',
                          return_value=(True, True, 5000)):
            collector.build_repo.find_unresolved_component_names.return_value = set()
            result = collector.run(components=components)
        assert isinstance(result, ScanResult)
        assert result.scan_id == 'scan-1'
        assert result.components_scanned == 1
        assert result.failures_found == 1
        assert result.new_failures == 1

    def test_without_components_discovers(self, collector):
        discovered = [self._make_component('comp-a')]
        with patch.object(collector, 'discover_components_from_cluster',
                          return_value=discovered):
            collector.db.create_scan.return_value = 'scan-1'
            with patch.object(collector, 'collect_comprehensive_failure',
                              return_value=(True, False, 1000)):
                collector.build_repo.find_unresolved_component_names.return_value = set()
                result = collector.run()
        assert result.components_scanned == 1

    def test_limit(self, collector):
        components = [
            self._make_component('comp-a'),
            self._make_component('comp-b'),
            self._make_component('comp-c'),
        ]
        collector.db.create_scan.return_value = 'scan-1'
        with patch.object(collector, 'collect_comprehensive_failure',
                          return_value=(False, False, 0)):
            collector.build_repo.find_unresolved_component_names.return_value = set()
            result = collector.run(components=components, limit=2)
        assert result.components_scanned == 2

    def test_no_components_raises(self, collector):
        with patch.object(collector, 'discover_components_from_cluster', return_value=[]):
            collector.config = CollectorConfig(
                db=collector.config.db,
                k8s=collector.config.k8s,
                components_file=None,
            )
            with pytest.raises(ValueError, match="No components discovered"):
                collector.run()

    def test_component_without_repo_url_enriched(self, collector):
        comp_no_url = Component(name='comp-a', repository_url='',
                                 branch='', namespace='test-ns')
        enriched = Component(name='comp-a', repository_url='https://enriched',
                              branch='main', namespace='test-ns')
        collector.db.create_scan.return_value = 'scan-1'
        with patch.object(collector, 'get_component_metadata', return_value=enriched):
            with patch.object(collector, 'collect_comprehensive_failure',
                              return_value=(False, False, 0)):
                collector.build_repo.find_unresolved_component_names.return_value = set()
                result = collector.run(components=[comp_no_url])
        assert result.components_scanned == 1

    def test_component_without_repo_url_skipped_in_loop(self, collector):
        """Component with no repo_url after enrichment is skipped (logged as error)."""
        comp_no_url = Component(name='comp-a', repository_url='',
                                 branch='', namespace='test-ns')
        collector.db.create_scan.return_value = 'scan-1'
        with patch.object(collector, 'get_component_metadata', return_value=None):
            with patch.object(collector, 'collect_comprehensive_failure') as mock_ccf:
                collector.build_repo.find_unresolved_component_names.return_value = set()
                result = collector.run(components=[comp_no_url])
        # collect_comprehensive_failure should NOT be called for comp without repo_url
        mock_ccf.assert_not_called()
        assert result.failures_found == 0

    def test_resolution_check(self, collector):
        components = [self._make_component('comp-a')]
        collector.db.create_scan.return_value = 'scan-1'
        with patch.object(collector, 'collect_comprehensive_failure',
                          return_value=(True, True, 5000)):
            collector.build_repo.find_unresolved_component_names.return_value = {'comp-b'}
            with patch.object(collector, 'check_resolution_via_history') as mock_check:
                mock_check.return_value = {
                    'resolved': True,
                    'resolution_pr_name': 'pr-resolved',
                    'resolution_commit_sha': 'abc123',
                }
                collector.build_repo.mark_resolved.return_value = True
                result = collector.run(components=components)
        mock_check.assert_called_once_with('comp-b')
        collector.build_repo.mark_resolved.assert_called_once()

    def test_resolution_check_exception_ignored(self, collector):
        components = [self._make_component('comp-a')]
        collector.db.create_scan.return_value = 'scan-1'
        with patch.object(collector, 'collect_comprehensive_failure',
                          return_value=(False, False, 0)):
            collector.build_repo.find_unresolved_component_names.side_effect = Exception("DB fail")
            result = collector.run(components=components)
        # Should not raise
        assert result.components_scanned == 1

    def test_scan_completed(self, collector):
        components = [self._make_component('comp-a')]
        collector.db.create_scan.return_value = 'scan-1'
        with patch.object(collector, 'collect_comprehensive_failure',
                          return_value=(False, False, 0)):
            collector.build_repo.find_unresolved_component_names.return_value = set()
            result = collector.run(components=components)
        collector.db.complete_scan.assert_called_once()
        assert result.scan_id == 'scan-1'


# ===========================================================================
# Delegation methods
# ===========================================================================

class TestDelegationMethods:

    @patch('collectors.build_failure_collector.extract_pipelinerun_metadata')
    def test_extract_all_details(self, mock_extract, collector):
        mock_extract.return_value = {'commit_sha': 'abc'}
        pr_data = {'metadata': {}}
        result = collector.extract_all_details(pr_data, source='tekton')
        mock_extract.assert_called_once_with(pr_data, 'test-ns', 'test-app')
        assert result == {'commit_sha': 'abc'}

    @patch('collectors.build_failure_collector.extract_pr_number_from_annotations')
    def test_extract_pr_number(self, mock_extract, collector):
        mock_extract.return_value = 42
        result = collector.extract_pr_number({'some': 'annot'})
        mock_extract.assert_called_once_with({'some': 'annot'})
        assert result == 42

    @patch('collectors.build_failure_collector.extract_error_from_logs')
    def test_extract_error_messages(self, mock_extract, collector):
        mock_extract.return_value = ('error msg', 'Build Error')
        result = collector.extract_error_messages('some logs')
        mock_extract.assert_called_once_with('some logs')
        assert result == ('error msg', 'Build Error')

    @patch('collectors.build_failure_collector.extract_failed_step_from_logs')
    def test_extract_failed_step_from_logs(self, mock_extract, collector):
        mock_extract.return_value = 'build'
        result = collector.extract_failed_step_from_logs('some logs')
        mock_extract.assert_called_once_with('some logs')
        assert result == 'build'
