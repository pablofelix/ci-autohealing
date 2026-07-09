"""Comprehensive unit tests for UnifiedPipelineClient.

Covers the chain-of-responsibility pattern: fallback between data sources
(KubernetesClient, KubeArchiveClient, TektonResultsClient), log retrieval
via the Kubernetes pod API as last resort, and edge cases like empty sources,
truncation, and exception handling.

All underlying clients and Kubernetes API calls are mocked.
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(sources=None, namespace='test-ns'):
    """Build a UnifiedPipelineClient with injected sources, bypassing __init__."""
    from clients.unified import UnifiedPipelineClient
    upc = UnifiedPipelineClient.__new__(UnifiedPipelineClient)
    upc.namespace = namespace
    upc.sources = sources if sources is not None else []
    return upc


def _mock_source(name='MockSource', pr_data=None, logs=None, taskruns=None):
    """Create a mock data source with configurable return values."""
    source = MagicMock()
    type(source).__name__ = name
    source.get_pipelinerun.return_value = pr_data
    source.get_pipelinerun_logs.return_value = logs
    source.get_pipelinerun_taskruns_details.return_value = taskruns
    return source


# ═══════════════════════════════════════════════════════════════════════
# Constructor tests
# ═══════════════════════════════════════════════════════════════════════

class TestUnifiedPipelineClientInit:
    """Constructor: custom sources vs default chain building."""

    @patch('clients.unified.TektonResultsClient')
    @patch('clients.unified.KubeArchiveClient')
    @patch('clients.unified.KubernetesClient')
    def test_default_sources_all_succeed(self, mock_k8s, mock_ka, mock_tr):
        from clients.unified import UnifiedPipelineClient
        client = UnifiedPipelineClient(namespace='ns')
        assert client.namespace == 'ns'
        assert len(client.sources) == 3
        mock_k8s.assert_called_once_with(namespace='ns')
        mock_ka.assert_called_once_with(namespace='ns')
        mock_tr.assert_called_once_with(namespace='ns')

    @patch('clients.unified.TektonResultsClient', side_effect=Exception('no TR'))
    @patch('clients.unified.KubeArchiveClient', side_effect=RuntimeError('no KA'))
    @patch('clients.unified.KubernetesClient')
    def test_default_sources_fallback_skips_failures(self, mock_k8s, mock_ka, mock_tr):
        from clients.unified import UnifiedPipelineClient
        client = UnifiedPipelineClient(namespace='ns')
        # Only KubernetesClient should remain
        assert len(client.sources) == 1

    @patch('clients.unified.TektonResultsClient')
    @patch('clients.unified.KubeArchiveClient', side_effect=RuntimeError('no KA'))
    @patch('clients.unified.KubernetesClient')
    def test_default_sources_kubearchive_fails_tekton_works(self, mock_k8s, mock_ka, mock_tr):
        from clients.unified import UnifiedPipelineClient
        client = UnifiedPipelineClient(namespace='ns')
        assert len(client.sources) == 2

    def test_custom_sources_used_directly(self):
        src1 = _mock_source('Custom1')
        src2 = _mock_source('Custom2')
        from clients.unified import UnifiedPipelineClient
        client = UnifiedPipelineClient(namespace='ns', sources=[src1, src2])
        assert client.sources == [src1, src2]

    def test_namespace_defaults_to_none(self):
        from clients.unified import UnifiedPipelineClient
        client = UnifiedPipelineClient(sources=[])
        assert client.namespace is None


# ═══════════════════════════════════════════════════════════════════════
# get_pipelinerun_complete
# ═══════════════════════════════════════════════════════════════════════

class TestGetPipelinerunComplete:
    """Chain-of-responsibility for PipelineRun data retrieval."""

    def test_first_source_returns_data_with_results(self):
        pr = {'metadata': {'name': 'pr-1'}, 'status': {'results': [{'name': 'IMAGE_DIGEST'}]}}
        src = _mock_source('KubernetesClient', pr_data=pr)
        client = _make_client(sources=[src])

        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data == pr
        assert source_name == 'KubernetesClient_with_results'

    def test_first_source_returns_data_without_results(self):
        pr = {'metadata': {'name': 'pr-1'}, 'status': {}}
        src = _mock_source('KubernetesClient', pr_data=pr)
        client = _make_client(sources=[src])

        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data == pr
        assert source_name == 'KubernetesClient'

    def test_fallback_to_second_source(self):
        src1 = _mock_source('KubernetesClient', pr_data=None)
        pr = {'metadata': {'name': 'pr-1'}, 'status': {'results': [{'name': 'r1'}]}}
        src2 = _mock_source('KubeArchiveClient', pr_data=pr)
        client = _make_client(sources=[src1, src2])

        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data == pr
        assert source_name == 'KubeArchiveClient_with_results'
        src1.get_pipelinerun.assert_called_once_with('pr-1')
        src2.get_pipelinerun.assert_called_once_with('pr-1')

    def test_fallback_to_third_source(self):
        src1 = _mock_source('K8s', pr_data=None)
        src2 = _mock_source('KA', pr_data=None)
        pr = {'metadata': {'name': 'pr-1'}, 'status': {}}
        src3 = _mock_source('TR', pr_data=pr)
        client = _make_client(sources=[src1, src2, src3])

        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data == pr
        assert source_name == 'TR'

    def test_no_sources_returns_none(self):
        client = _make_client(sources=[])
        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data is None
        assert source_name == 'none'

    def test_all_sources_return_none(self):
        src1 = _mock_source('A', pr_data=None)
        src2 = _mock_source('B', pr_data=None)
        client = _make_client(sources=[src1, src2])

        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data is None
        assert source_name == 'none'

    def test_empty_dict_is_falsy_returns_none(self):
        """An empty dict {} is falsy, so the chain should skip it."""
        src = _mock_source('K8s', pr_data={})
        client = _make_client(sources=[src])
        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data is None
        assert source_name == 'none'

    def test_status_missing_results_key(self):
        """PR data with status but no 'results' key -> no _with_results suffix."""
        pr = {'metadata': {}, 'status': {'conditions': []}}
        src = _mock_source('K8s', pr_data=pr)
        client = _make_client(sources=[src])

        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data == pr
        assert source_name == 'K8s'

    def test_status_with_empty_results_list(self):
        """Empty results list -> no _with_results suffix."""
        pr = {'metadata': {}, 'status': {'results': []}}
        src = _mock_source('K8s', pr_data=pr)
        client = _make_client(sources=[src])

        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data == pr
        assert source_name == 'K8s'

    def test_no_status_key_at_all(self):
        """PR data with no 'status' key at all."""
        pr = {'metadata': {'name': 'pr-1'}}
        src = _mock_source('K8s', pr_data=pr)
        client = _make_client(sources=[src])

        data, source_name = client.get_pipelinerun_complete('pr-1')
        assert data == pr
        assert source_name == 'K8s'


# ═══════════════════════════════════════════════════════════════════════
# get_logs_complete
# ═══════════════════════════════════════════════════════════════════════

class TestGetLogsComplete:
    """Log retrieval with fallback and truncation."""

    def test_first_source_returns_logs(self):
        src = _mock_source('K8s', logs='build output here')
        client = _make_client(sources=[src])

        logs, source_name = client.get_logs_complete('pr-1')
        assert logs == 'build output here'
        assert source_name == 'K8s'

    def test_fallback_to_second_source(self):
        src1 = _mock_source('K8s', logs=None)
        src2 = _mock_source('KA', logs='archived logs')
        client = _make_client(sources=[src1, src2])

        logs, source_name = client.get_logs_complete('pr-1')
        assert logs == 'archived logs'
        assert source_name == 'KA'

    def test_truncation_when_logs_exceed_max_size(self):
        big_logs = 'A' * 300000
        src = _mock_source('K8s', logs=big_logs)
        client = _make_client(sources=[src])

        logs, source_name = client.get_logs_complete('pr-1', max_size=200000)
        assert len(logs) == 200000
        # Truncation takes the tail (logs[-max_size:])
        assert logs == 'A' * 200000

    def test_logs_exactly_at_max_size_not_truncated(self):
        exact_logs = 'B' * 200000
        src = _mock_source('K8s', logs=exact_logs)
        client = _make_client(sources=[src])

        logs, source_name = client.get_logs_complete('pr-1', max_size=200000)
        assert len(logs) == 200000

    def test_empty_string_logs_is_falsy_falls_through(self):
        """Empty string '' is falsy, so the chain skips it."""
        src1 = _mock_source('K8s', logs='')
        src2 = _mock_source('KA', logs='real logs')
        client = _make_client(sources=[src1, src2])

        logs, source_name = client.get_logs_complete('pr-1')
        assert logs == 'real logs'
        assert source_name == 'KA'

    def test_fallback_to_api_pods(self):
        """When all sources fail, falls back to _get_logs_via_api."""
        src1 = _mock_source('K8s', logs=None)
        client = _make_client(sources=[src1])

        with patch.object(client, '_get_logs_via_api', return_value='pod logs') as mock_api:
            logs, source_name = client.get_logs_complete('pr-1')
        assert logs == 'pod logs'
        assert source_name == 'k8s_pods'
        mock_api.assert_called_once_with('pr-1')

    def test_api_fallback_truncation(self):
        """API fallback logs are also truncated."""
        big_pod_logs = 'X' * 500000
        src = _mock_source('K8s', logs=None)
        client = _make_client(sources=[src])

        with patch.object(client, '_get_logs_via_api', return_value=big_pod_logs):
            logs, source_name = client.get_logs_complete('pr-1', max_size=100)
        assert len(logs) == 100
        assert source_name == 'k8s_pods'

    def test_api_fallback_also_returns_none(self):
        """When both sources and API fallback fail, returns (None, 'none')."""
        src = _mock_source('K8s', logs=None)
        client = _make_client(sources=[src])

        with patch.object(client, '_get_logs_via_api', return_value=None):
            logs, source_name = client.get_logs_complete('pr-1')
        assert logs is None
        assert source_name == 'none'

    def test_no_sources_falls_to_api(self):
        """Empty sources list -> goes straight to API fallback."""
        client = _make_client(sources=[])
        with patch.object(client, '_get_logs_via_api', return_value='fallback') as mock_api:
            logs, source_name = client.get_logs_complete('pr-1')
        assert logs == 'fallback'
        assert source_name == 'k8s_pods'


# ═══════════════════════════════════════════════════════════════════════
# get_taskruns_details
# ═══════════════════════════════════════════════════════════════════════

class TestGetTaskrunsDetails:
    """TaskRun detail retrieval with chain fallback."""

    def test_first_source_returns_details(self):
        details = [{'name': 'build-task', 'status': 'Succeeded'}]
        src = _mock_source('K8s', taskruns=details)
        client = _make_client(sources=[src])

        result, source_name = client.get_taskruns_details('pr-1')
        assert result == details
        assert source_name == 'K8s'

    def test_fallback_to_second_source(self):
        src1 = _mock_source('K8s', taskruns=None)
        details = [{'name': 'task-1'}]
        src2 = _mock_source('TR', taskruns=details)
        client = _make_client(sources=[src1, src2])

        result, source_name = client.get_taskruns_details('pr-1')
        assert result == details
        assert source_name == 'TR'

    def test_empty_list_is_falsy_skipped(self):
        """An empty list [] is falsy, so the chain skips it."""
        src1 = _mock_source('K8s', taskruns=[])
        details = [{'name': 'task-from-tr'}]
        src2 = _mock_source('TR', taskruns=details)
        client = _make_client(sources=[src1, src2])

        result, source_name = client.get_taskruns_details('pr-1')
        assert result == details
        assert source_name == 'TR'

    def test_all_sources_return_none(self):
        src1 = _mock_source('K8s', taskruns=None)
        src2 = _mock_source('KA', taskruns=None)
        client = _make_client(sources=[src1, src2])

        result, source_name = client.get_taskruns_details('pr-1')
        assert result == []
        assert source_name == 'none'

    def test_no_sources(self):
        client = _make_client(sources=[])
        result, source_name = client.get_taskruns_details('pr-1')
        assert result == []
        assert source_name == 'none'


# ═══════════════════════════════════════════════════════════════════════
# _get_logs_via_api — Kubernetes pod log retrieval
# ═══════════════════════════════════════════════════════════════════════

class TestGetLogsViaApi:
    """Last-resort pod-based log retrieval via Kubernetes Python client."""

    @patch('clients.unified._ensure_k8s_config')
    @patch('clients.unified.client.CoreV1Api')
    def test_happy_path_multiple_pods(self, mock_core_api_cls, mock_config):
        mock_v1 = MagicMock()
        mock_core_api_cls.return_value = mock_v1

        pod1 = MagicMock()
        pod1.metadata.name = 'pr-1-build-pod'
        pod2 = MagicMock()
        pod2.metadata.name = 'pr-1-test-pod'
        mock_v1.list_namespaced_pod.return_value = MagicMock(items=[pod1, pod2])
        mock_v1.read_namespaced_pod_log.side_effect = [
            'build log output', 'test log output'
        ]

        client = _make_client(namespace='test-ns')
        result = client._get_logs_via_api('pr-1')

        assert '===== Pod: pr-1-build-pod =====' in result
        assert 'build log output' in result
        assert '===== Pod: pr-1-test-pod =====' in result
        assert 'test log output' in result
        mock_v1.list_namespaced_pod.assert_called_once_with(
            'test-ns',
            label_selector='tekton.dev/pipelineRun=pr-1',
            _request_timeout=10,
        )

    @patch('clients.unified._ensure_k8s_config')
    @patch('clients.unified.client.CoreV1Api')
    def test_no_pods_found(self, mock_core_api_cls, mock_config):
        mock_v1 = MagicMock()
        mock_core_api_cls.return_value = mock_v1
        mock_v1.list_namespaced_pod.return_value = MagicMock(items=[])

        client = _make_client(namespace='test-ns')
        assert client._get_logs_via_api('pr-1') is None

    @patch('clients.unified._ensure_k8s_config')
    @patch('clients.unified.client.CoreV1Api')
    def test_pod_log_exception_skipped(self, mock_core_api_cls, mock_config):
        """Individual pod log failures are skipped, other pods still collected."""
        mock_v1 = MagicMock()
        mock_core_api_cls.return_value = mock_v1

        pod1 = MagicMock()
        pod1.metadata.name = 'pod-ok'
        pod2 = MagicMock()
        pod2.metadata.name = 'pod-fail'
        mock_v1.list_namespaced_pod.return_value = MagicMock(items=[pod1, pod2])
        mock_v1.read_namespaced_pod_log.side_effect = [
            'good logs',
            Exception('container not found'),
        ]

        client = _make_client(namespace='ns')
        result = client._get_logs_via_api('pr-1')
        assert 'good logs' in result
        assert 'pod-fail' not in result

    @patch('clients.unified._ensure_k8s_config')
    @patch('clients.unified.client.CoreV1Api')
    def test_all_pod_logs_fail(self, mock_core_api_cls, mock_config):
        """When all pod log reads fail, returns None."""
        mock_v1 = MagicMock()
        mock_core_api_cls.return_value = mock_v1

        pod = MagicMock()
        pod.metadata.name = 'pod-1'
        mock_v1.list_namespaced_pod.return_value = MagicMock(items=[pod])
        mock_v1.read_namespaced_pod_log.side_effect = Exception('fail')

        client = _make_client(namespace='ns')
        assert client._get_logs_via_api('pr-1') is None

    @patch('clients.unified._ensure_k8s_config')
    @patch('clients.unified.client.CoreV1Api')
    def test_empty_pod_log_skipped(self, mock_core_api_cls, mock_config):
        """Pods returning empty/None logs are skipped."""
        mock_v1 = MagicMock()
        mock_core_api_cls.return_value = mock_v1

        pod1 = MagicMock()
        pod1.metadata.name = 'pod-empty'
        pod2 = MagicMock()
        pod2.metadata.name = 'pod-ok'
        mock_v1.list_namespaced_pod.return_value = MagicMock(items=[pod1, pod2])
        mock_v1.read_namespaced_pod_log.side_effect = ['', 'real output']

        client = _make_client(namespace='ns')
        result = client._get_logs_via_api('pr-1')
        # Only pod-ok should be in the result since empty string is falsy
        assert 'pod-ok' in result
        assert 'pod-empty' not in result

    @patch('clients.unified._ensure_k8s_config', side_effect=Exception('no kubeconfig'))
    def test_config_failure_returns_none(self, mock_config):
        """If k8s config fails, returns None gracefully."""
        client = _make_client(namespace='ns')
        assert client._get_logs_via_api('pr-1') is None

    @patch('clients.unified._ensure_k8s_config')
    @patch('clients.unified.client.CoreV1Api')
    def test_list_pods_exception_returns_none(self, mock_core_api_cls, mock_config):
        mock_v1 = MagicMock()
        mock_core_api_cls.return_value = mock_v1
        mock_v1.list_namespaced_pod.side_effect = Exception('API unreachable')

        client = _make_client(namespace='ns')
        assert client._get_logs_via_api('pr-1') is None

    @patch('clients.unified._ensure_k8s_config')
    @patch('clients.unified.client.CoreV1Api')
    def test_max_10_pods(self, mock_core_api_cls, mock_config):
        """Only first 10 pods are read even if more exist."""
        mock_v1 = MagicMock()
        mock_core_api_cls.return_value = mock_v1

        pods = []
        for i in range(15):
            pod = MagicMock()
            pod.metadata.name = 'pod-{}'.format(i)
            pods.append(pod)
        mock_v1.list_namespaced_pod.return_value = MagicMock(items=pods)
        mock_v1.read_namespaced_pod_log.return_value = 'log'

        client = _make_client(namespace='ns')
        client._get_logs_via_api('pr-1')
        assert mock_v1.read_namespaced_pod_log.call_count == 10

    @patch('clients.unified._ensure_k8s_config')
    @patch('clients.unified.client.CoreV1Api')
    def test_pod_without_metadata_skipped(self, mock_core_api_cls, mock_config):
        """Pods with metadata=None are filtered out by the list comprehension."""
        mock_v1 = MagicMock()
        mock_core_api_cls.return_value = mock_v1

        pod_ok = MagicMock()
        pod_ok.metadata.name = 'pod-ok'
        pod_no_meta = MagicMock()
        pod_no_meta.metadata = None
        mock_v1.list_namespaced_pod.return_value = MagicMock(items=[pod_no_meta, pod_ok])
        mock_v1.read_namespaced_pod_log.return_value = 'output'

        client = _make_client(namespace='ns')
        result = client._get_logs_via_api('pr-1')
        assert 'pod-ok' in result
        # read_namespaced_pod_log should only be called once (for pod-ok)
        assert mock_v1.read_namespaced_pod_log.call_count == 1
