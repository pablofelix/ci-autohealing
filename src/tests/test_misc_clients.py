"""Tests for GitLab, KubeArchive, Unified, PipelineRunQuery, and LangfuseTracker clients."""

import base64
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ═══════════════════════════════════════════════════════════════════════
# GitLab client tests
# ═══════════════════════════════════════════════════════════════════════

class TestGitLabClient:
    def _make_client(self):
        from clients.gitlab_client import GitLabClient
        with patch.dict(os.environ, {'GITLAB_API_BASE': 'https://gitlab.example.com/api/v4',
                                      'GITLAB_TOKEN': ''}):
            c = GitLabClient(token='test-token', api_base='https://gitlab.example.com/api/v4')
        return c

    def test_init_with_token(self):
        c = self._make_client()
        assert c._session.headers.get('PRIVATE-TOKEN') == 'test-token'

    def test_init_without_token(self):
        from clients.gitlab_client import GitLabClient
        with patch.dict(os.environ, {'GITLAB_TOKEN': '', 'GITLAB_API_BASE': ''}):
            c = GitLabClient(token=None, api_base='https://example.com')
        assert 'PRIVATE-TOKEN' not in c._session.headers

    def test_get_success(self):
        c = self._make_client()
        mock_resp = MagicMock(status_code=200)
        c._session.get = MagicMock(return_value=mock_resp)
        result = c._get('/test')
        assert result == mock_resp

    def test_get_404(self):
        c = self._make_client()
        c._session.get = MagicMock(return_value=MagicMock(status_code=404))
        assert c._get('/test') is None

    def test_get_auth_error(self):
        c = self._make_client()
        c._session.get = MagicMock(return_value=MagicMock(status_code=401))
        assert c._get('/test') is None

    def test_get_timeout(self):
        import requests
        c = self._make_client()
        c._session.get = MagicMock(side_effect=requests.Timeout)
        assert c._get('/test') is None

    def test_get_request_exception(self):
        import requests
        c = self._make_client()
        c._session.get = MagicMock(side_effect=requests.RequestException("fail"))
        assert c._get('/test') is None

    def test_get_file_content_success(self):
        c = self._make_client()
        encoded = base64.b64encode(b'hello world').decode('ascii')
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'content': encoded, 'encoding': 'base64'}
        c._session.get = MagicMock(return_value=mock_resp)

        result = c.get_file_content('group/project', 'file.txt', ref='main')
        assert result == 'hello world'

    def test_get_file_content_truncation(self):
        c = self._make_client()
        big_content = 'x' * 60000
        encoded = base64.b64encode(big_content.encode()).decode('ascii')
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'content': encoded, 'encoding': 'base64'}
        c._session.get = MagicMock(return_value=mock_resp)

        result = c.get_file_content('group/project', 'big.txt')
        assert result.endswith('... (truncated)')
        assert len(result) < 60000

    def test_get_file_content_not_found(self):
        c = self._make_client()
        c._session.get = MagicMock(return_value=MagicMock(status_code=404))
        assert c.get_file_content('group/project', 'missing.txt') is None

    def test_get_file_content_no_base64(self):
        c = self._make_client()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'content': 'plain text', 'encoding': 'text'}
        c._session.get = MagicMock(return_value=mock_resp)
        result = c.get_file_content('group/project', 'file.txt')
        assert result == 'plain text'

    def test_list_directory_success(self):
        c = self._make_client()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = [
            {'name': 'a.yaml', 'type': 'blob', 'path': 'dir/a.yaml'},
            {'name': 'b.yaml', 'type': 'blob', 'path': 'dir/b.yaml'},
        ]
        c._session.get = MagicMock(return_value=mock_resp)

        result = c.list_directory('group/project', 'dir')
        assert len(result) == 2
        assert result[0]['name'] == 'a.yaml'

    def test_list_directory_not_found(self):
        c = self._make_client()
        c._session.get = MagicMock(return_value=MagicMock(status_code=404))
        assert c.list_directory('group/project', 'missing') is None


# ═══════════════════════════════════════════════════════════════════════
# KubeArchive client tests
# ═══════════════════════════════════════════════════════════════════════

class TestKubeArchiveClient:
    @patch('clients.kubearchive.get_openshift_token', return_value='test-token')
    @patch('clients.kubearchive.create_authenticated_session')
    @patch('clients.kubearchive.discover_kubearchive_api_url', return_value='https://ka.example.com')
    def _make_client(self, mock_discover, mock_session, mock_token):
        from clients.kubearchive import KubeArchiveClient
        mock_sess = MagicMock()
        mock_session.return_value = mock_sess
        c = KubeArchiveClient(namespace='test-ns')
        return c, mock_sess

    def test_init(self):
        c, _ = self._make_client()
        assert c.namespace == 'test-ns'
        assert c.api_url == 'https://ka.example.com'

    @patch('clients.kubearchive.get_openshift_token', return_value=None)
    @patch('clients.kubearchive.discover_kubearchive_api_url', return_value='https://ka.example.com')
    def test_init_no_token(self, mock_discover, mock_token):
        from clients.kubearchive import KubeArchiveClient
        with pytest.raises(RuntimeError, match="Failed to get OpenShift token"):
            KubeArchiveClient(namespace='ns')

    def test_step_container_name(self):
        c, _ = self._make_client()
        assert c.step_container_name('build') == 'step-build'

    def test_get_pipelinerun_success(self):
        c, session = self._make_client()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'metadata': {'name': 'pr-1'}}
        mock_resp.raise_for_status = MagicMock()
        session.get.return_value = mock_resp
        result = c.get_pipelinerun('pr-1')
        assert result['metadata']['name'] == 'pr-1'

    def test_get_pipelinerun_error(self):
        import requests
        c, session = self._make_client()
        session.get.side_effect = requests.RequestException("fail")
        assert c.get_pipelinerun('pr-1') is None

    def test_get_taskrun_success(self):
        c, session = self._make_client()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'metadata': {'name': 'tr-1'}}
        mock_resp.raise_for_status = MagicMock()
        session.get.return_value = mock_resp
        assert c.get_taskrun('tr-1')['metadata']['name'] == 'tr-1'

    def test_get_pod_logs_success(self):
        c, session = self._make_client()
        mock_resp = MagicMock(status_code=200, text='log output')
        mock_resp.raise_for_status = MagicMock()
        session.get.return_value = mock_resp
        assert c.get_pod_logs('pod-1', container='step-build') == 'log output'

    def test_get_pod_logs_error(self):
        import requests
        c, session = self._make_client()
        session.get.side_effect = requests.RequestException("fail")
        assert c.get_pod_logs('pod-1') is None


# ═══════════════════════════════════════════════════════════════════════
# Unified pipeline client tests
# ═══════════════════════════════════════════════════════════════════════

class TestUnifiedPipelineClient:
    def test_get_pipelinerun_complete_first_source(self):
        from clients.unified import UnifiedPipelineClient
        src1 = MagicMock()
        src1.get_pipelinerun.return_value = {
            'metadata': {'name': 'pr-1'},
            'status': {'results': [{'name': 'IMAGE_URL'}]},
        }
        client = UnifiedPipelineClient(namespace='ns', sources=[src1])
        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data is not None
        assert 'with_results' in source_name

    def test_get_pipelinerun_complete_fallback(self):
        from clients.unified import UnifiedPipelineClient
        src1 = MagicMock()
        src1.get_pipelinerun.return_value = None
        src2 = MagicMock()
        src2.get_pipelinerun.return_value = {'metadata': {'name': 'pr-1'}, 'status': {}}
        src2.configure_mock(**{'__class__.__name__': 'KubeArchiveClient'})
        client = UnifiedPipelineClient(namespace='ns', sources=[src1, src2])
        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data is not None

    def test_get_pipelinerun_complete_none(self):
        from clients.unified import UnifiedPipelineClient
        src1 = MagicMock()
        src1.get_pipelinerun.return_value = None
        client = UnifiedPipelineClient(namespace='ns', sources=[src1])
        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data is None
        assert source_name == 'none'

    def test_get_logs_complete_first_source(self):
        from clients.unified import UnifiedPipelineClient
        src1 = MagicMock()
        src1.get_pipelinerun_logs.return_value = 'some logs'
        client = UnifiedPipelineClient(namespace='ns', sources=[src1])
        logs, source_name = client.get_logs_complete('pr-1')
        assert logs == 'some logs'

    def test_get_logs_complete_truncation(self):
        from clients.unified import UnifiedPipelineClient
        src1 = MagicMock()
        src1.get_pipelinerun_logs.return_value = 'x' * 500000
        client = UnifiedPipelineClient(namespace='ns', sources=[src1])
        logs, _ = client.get_logs_complete('pr-1', max_size=1000)
        assert len(logs) == 1000

    def test_get_logs_complete_fallback_to_pods(self):
        from clients.unified import UnifiedPipelineClient
        src1 = MagicMock()
        src1.get_pipelinerun_logs.return_value = None
        client = UnifiedPipelineClient(namespace='ns', sources=[src1])
        with patch.object(client, '_get_logs_via_api', return_value='pod logs'):
            logs, source_name = client.get_logs_complete('pr-1')
        assert logs == 'pod logs'
        assert source_name == 'k8s_pods'

    def test_get_logs_complete_none(self):
        from clients.unified import UnifiedPipelineClient
        src1 = MagicMock()
        src1.get_pipelinerun_logs.return_value = None
        client = UnifiedPipelineClient(namespace='ns', sources=[src1])
        with patch.object(client, '_get_logs_via_api', return_value=None):
            logs, source_name = client.get_logs_complete('pr-1')
        assert logs is None
        assert source_name == 'none'

    def test_get_taskruns_details(self):
        from clients.unified import UnifiedPipelineClient
        src1 = MagicMock()
        src1.get_pipelinerun_taskruns_details.return_value = [{'name': 'tr-1'}]
        client = UnifiedPipelineClient(namespace='ns', sources=[src1])
        details, source_name = client.get_taskruns_details('pr-1')
        assert len(details) == 1


# ═══════════════════════════════════════════════════════════════════════
# PipelineRun query tests
# ═══════════════════════════════════════════════════════════════════════

class TestPipelineRunQuery:
    @patch('clients.pipelinerun_query._query_live_cluster')
    def test_query_pipelineruns_live_only(self, mock_live):
        from clients.pipelinerun_query import query_pipelineruns
        mock_live.return_value = [
            {'metadata': {'uid': 'uid-1', 'labels': {'appstudio.openshift.io/component': 'c1'}}}
        ]
        results = query_pipelineruns('ns', 'app=test', kubearchive_url=None, session=None)
        assert len(results) == 1

    @patch('clients.pipelinerun_query._query_live_cluster')
    def test_query_pipelineruns_dedup(self, mock_live):
        from clients.pipelinerun_query import query_pipelineruns
        mock_live.return_value = [
            {'metadata': {'uid': 'uid-1', 'labels': {'appstudio.openshift.io/component': 'c1'}}},
            {'metadata': {'uid': 'uid-1', 'labels': {'appstudio.openshift.io/component': 'c1'}}},
        ]
        results = query_pipelineruns('ns', 'app=test', kubearchive_url=None, session=None)
        assert len(results) == 1

    @patch('clients.pipelinerun_query._query_live_cluster')
    def test_query_pipelineruns_with_kubearchive(self, mock_live):
        from clients.pipelinerun_query import query_pipelineruns
        mock_live.return_value = []
        mock_session = MagicMock()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            'items': [
                {'metadata': {'uid': 'uid-2', 'labels': {'appstudio.openshift.io/component': 'c2'}}}
            ],
            'metadata': {},
        }
        mock_session.get.return_value = mock_resp
        results = query_pipelineruns('ns', 'app=test',
                                     kubearchive_url='https://ka.example.com',
                                     session=mock_session)
        assert len(results) == 1

    def test_count_components(self):
        from clients.pipelinerun_query import _count_components
        by_uid = {
            'a': {'metadata': {'labels': {'appstudio.openshift.io/component': 'c1'}}},
            'b': {'metadata': {'labels': {'appstudio.openshift.io/component': 'c2'}}},
            'c': {'metadata': {'labels': {'appstudio.openshift.io/component': 'c1'}}},
        }
        assert _count_components(by_uid) == 2

    def test_count_components_empty(self):
        from clients.pipelinerun_query import _count_components
        assert _count_components({}) == 0


# ═══════════════════════════════════════════════════════════════════════
# Langfuse tracker tests
# ═══════════════════════════════════════════════════════════════════════

class TestLangfuseTracker:
    def test_init_disabled(self):
        from clients.langfuse_tracker import LangfuseTracker
        tracker = LangfuseTracker(enabled=False)
        assert tracker.enabled is False
        assert tracker._langfuse is None

    def test_init_enabled_import_error(self):
        import builtins
        real_import = builtins.__import__
        def _fake_import(name, *args, **kwargs):
            if name == 'langfuse':
                raise ImportError("no langfuse")
            return real_import(name, *args, **kwargs)
        from clients.langfuse_tracker import LangfuseTracker
        with patch.object(builtins, '__import__', side_effect=_fake_import):
            tracker = LangfuseTracker(enabled=True)
        assert tracker.enabled is False

    def test_create_trace_disabled(self):
        from clients.langfuse_tracker import LangfuseTracker
        tracker = LangfuseTracker(enabled=False)
        assert tracker.create_trace('test') is None

    def test_create_trace_enabled(self):
        from clients.langfuse_tracker import LangfuseTracker
        tracker = LangfuseTracker(enabled=False)
        tracker.enabled = True
        tracker._langfuse = MagicMock()
        mock_trace = MagicMock()
        tracker._langfuse.trace.return_value = mock_trace
        result = tracker.create_trace('test', input_data={'key': 'val'})
        assert result == mock_trace

    def test_record_generation_disabled(self):
        from clients.langfuse_tracker import LangfuseTracker
        tracker = LangfuseTracker(enabled=False)
        tracker.record_generation(None, 'gen', 'model', 'prompt', 'resp', 100, 50, 500)

    def test_record_generation_enabled(self):
        from clients.langfuse_tracker import LangfuseTracker
        tracker = LangfuseTracker(enabled=False)
        tracker.enabled = True
        mock_trace = MagicMock()
        tracker.record_generation(mock_trace, 'gen', 'model', 'prompt', 'resp', 100, 50, 500)
        mock_trace.generation.assert_called_once()

    def test_end_trace_disabled(self):
        from clients.langfuse_tracker import LangfuseTracker
        tracker = LangfuseTracker(enabled=False)
        tracker.end_trace(None, output={'result': 'ok'})

    def test_end_trace_enabled(self):
        from clients.langfuse_tracker import LangfuseTracker
        tracker = LangfuseTracker(enabled=False)
        tracker.enabled = True
        mock_trace = MagicMock()
        tracker.end_trace(mock_trace, output={'result': 'ok'})
        mock_trace.update.assert_called_once_with(output={'result': 'ok'})

    def test_flush_disabled(self):
        from clients.langfuse_tracker import LangfuseTracker
        tracker = LangfuseTracker(enabled=False)
        tracker.flush()

    def test_flush_enabled(self):
        from clients.langfuse_tracker import LangfuseTracker
        tracker = LangfuseTracker(enabled=False)
        tracker.enabled = True
        tracker._langfuse = MagicMock()
        tracker.flush()
        tracker._langfuse.flush.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# Konflux client static method tests
# ═══════════════════════════════════════════════════════════════════════

class TestKonfluxClientStatic:
    def test_extract_exceptions_permanent(self):
        from clients.konflux_client import KonfluxClient
        policy = {
            'metadata': {'name': 'test-policy'},
            'spec': {
                'sources': [{
                    'config': {'exclude': ['rule1', 'rule2']},
                    'volatileConfig': {'exclude': []},
                }],
            },
        }
        results = KonfluxClient.extract_exceptions(policy)
        assert len(results) == 2
        assert results[0]['permanent'] is True
        assert results[0]['value'] == 'rule1'
        assert results[0]['days_left'] is None

    def test_extract_exceptions_volatile(self):
        from clients.konflux_client import KonfluxClient
        policy = {
            'metadata': {'name': 'test-policy'},
            'spec': {
                'sources': [{
                    'config': {'exclude': []},
                    'volatileConfig': {
                        'exclude': [{
                            'value': 'temp-rule',
                            'effectiveUntil': '2099-01-01T00:00:00Z',
                            'reference': 'JIRA-123',
                            'imageUrl': 'quay.io/img',
                        }]
                    },
                }],
            },
        }
        results = KonfluxClient.extract_exceptions(policy)
        assert len(results) == 1
        assert results[0]['permanent'] is False
        assert results[0]['value'] == 'temp-rule'
        assert results[0]['days_left'] is not None
        assert results[0]['days_left'] > 0

    def test_extract_snapshot_status(self):
        from clients.konflux_client import KonfluxClient
        snapshot = {
            'metadata': {
                'name': 'snap-1',
                'creationTimestamp': '2026-01-01T00:00:00Z',
                'labels': {'test.appstudio.openshift.io/type': 'push'},
            },
            'spec': {
                'application': 'test-app',
                'components': [{'name': 'comp-1'}, {'name': 'comp-2'}],
            },
            'status': {
                'conditions': [{
                    'type': 'AppStudioTestSucceeded',
                    'status': 'True',
                    'reason': 'Passed',
                    'message': 'All tests passed',
                    'lastTransitionTime': '2026-01-01T01:00:00Z',
                }],
            },
        }
        result = KonfluxClient.extract_snapshot_status(snapshot)
        assert result['name'] == 'snap-1'
        assert result['component_count'] == 2
        assert result['is_override'] is False
        assert 'Passed' in result['test_results']

    def test_extract_release_status(self):
        from clients.konflux_client import KonfluxClient
        release = {
            'metadata': {'name': 'rel-1', 'creationTimestamp': '2026-01-01'},
            'spec': {'snapshot': 'snap-1', 'releasePlan': 'plan-1'},
            'status': {
                'conditions': [{'type': 'Released', 'status': 'True', 'message': 'ok'}],
                'managedProcessing': {
                    'pipelineRun': 'pr-1',
                    'startTime': '2026-01-01T01:00:00Z',
                    'completionTime': '2026-01-01T02:00:00Z',
                },
                'automated': True,
                'validation': {'failedPostValidation': False},
            },
        }
        result = KonfluxClient.extract_release_status(release)
        assert result['name'] == 'rel-1'
        assert result['snapshot'] == 'snap-1'
        assert result['phase'] == 'Released'
        assert result['automated'] is True

    def test_extract_rpa_bindings(self):
        from clients.konflux_client import KonfluxClient
        rpas = [{
            'metadata': {'name': 'rpa-prod'},
            'spec': {
                'applications': ['app-1'],
                'policy': 'policy-1',
            },
        }]
        results = KonfluxClient.extract_rpa_bindings(rpas)
        assert len(results) == 1
        assert results[0]['policy'] == 'policy-1'
        assert results[0]['target'] == 'prod'

    def test_extract_rpa_bindings_with_filter(self):
        from clients.konflux_client import KonfluxClient
        rpas = [{
            'metadata': {'name': 'rpa-1'},
            'spec': {'applications': ['app-1'], 'policy': 'policy-A'},
        }, {
            'metadata': {'name': 'rpa-2'},
            'spec': {'applications': ['app-2'], 'policy': 'policy-B'},
        }]
        results = KonfluxClient.extract_rpa_bindings(rpas, policy_names=['policy-A'])
        assert len(results) == 1

    def test_extract_its_metadata(self):
        from clients.konflux_client import KonfluxClient
        scenario = {
            'metadata': {'name': 'my-conforma-test'},
            'spec': {
                'application': 'test-app',
                'contexts': [{'name': 'component'}, {'name': 'group'}],
                'params': [{'name': 'POLICY_CONFIGURATION', 'value': 'my-policy'}],
                'resolverRef': {
                    'params': [
                        {'name': 'url', 'value': 'https://github.com/org/repo'},
                        {'name': 'pathInRepo', 'value': 'pipelines/enterprise-contract.yaml'},
                    ],
                },
            },
        }
        result = KonfluxClient.extract_its_metadata(scenario)
        assert result['name'] == 'my-conforma-test'
        assert result['policy_ref'] == 'my-policy'
        assert result['is_conforma'] is True
        assert result['is_disabled'] is False

    def test_extract_its_metadata_disabled(self):
        from clients.konflux_client import KonfluxClient
        scenario = {
            'metadata': {'name': 'test-future'},
            'spec': {
                'application': 'app',
                'contexts': [{'name': 'disabled'}],
                'params': [],
                'resolverRef': {'params': []},
            },
        }
        result = KonfluxClient.extract_its_metadata(scenario)
        assert result['is_disabled'] is True
        assert result['is_future'] is True

    def test_extract_dependency_update(self):
        from clients.konflux_client import KonfluxClient
        duc = {
            'metadata': {
                'name': 'duc-1',
                'creationTimestamp': '2026-01-01',
                'labels': {'appstudio.openshift.io/component': 'comp-1'},
            },
            'spec': {
                'packageName': 'golang',
                'currentVersion': '1.21',
                'newVersion': '1.22',
            },
            'status': {
                'pullRequestUrl': 'https://github.com/org/repo/pull/1',
                'merged': False,
            },
        }
        result = KonfluxClient.extract_dependency_update(duc)
        assert result['component'] == 'comp-1'
        assert result['package'] == 'golang'
        assert result['from_version'] == '1.21'
        assert result['to_version'] == '1.22'
        assert result['merged'] is False


# ═══════════════════════════════════════════════════════════════════════
# PipelineSource abstract base tests
# ═══════════════════════════════════════════════════════════════════════

class TestPipelineSource:
    def test_step_container_name_default(self):
        from clients.pipeline_source import PipelineRunSource
        src = MagicMock(spec=PipelineRunSource)
        assert PipelineRunSource.step_container_name(src, 'build') == 'build'

    @patch('clients.pipeline_source.extract_taskrun_names', return_value=['tr-1'])
    @patch('clients.pipeline_source.extract_failed_step_names', return_value=['step-build'])
    @patch('clients.pipeline_source.build_taskrun_detail', return_value={'name': 'tr-1'})
    def test_get_pipelinerun_logs(self, mock_detail, mock_failed, mock_names):
        from clients.pipeline_source import PipelineRunSource

        class FakeSource(PipelineRunSource):
            def __init__(self):
                self.namespace = 'ns'
            def get_pipelinerun(self, name, ns=None):
                return {'metadata': {'name': name}}
            def get_taskrun(self, name, ns=None):
                return {'status': {'podName': 'pod-1', 'steps': []}}
            def get_pod_logs(self, pod_name, container=None, namespace=None, tail_lines=None):
                return 'log output for {}'.format(container)

        src = FakeSource()
        logs = src.get_pipelinerun_logs('pr-1')
        assert 'log output' in logs
        assert 'step-build' in logs

    @patch('clients.pipeline_source.extract_taskrun_names', return_value=[])
    def test_get_pipelinerun_logs_no_taskruns(self, mock_names):
        from clients.pipeline_source import PipelineRunSource

        class FakeSource(PipelineRunSource):
            def __init__(self):
                self.namespace = 'ns'
            def get_pipelinerun(self, name, ns=None):
                return {'metadata': {'name': name}}
            def get_taskrun(self, name, ns=None):
                return None
            def get_pod_logs(self, pod_name, container=None, namespace=None, tail_lines=None):
                return None

        src = FakeSource()
        assert src.get_pipelinerun_logs('pr-1') is None
