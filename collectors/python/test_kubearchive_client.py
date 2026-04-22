"""Unit tests for KubeArchive API client."""

import unittest
from unittest.mock import Mock, patch, MagicMock
import subprocess
import requests
from kubearchive_client import KubeArchiveClient


class TestKubeArchiveClient(unittest.TestCase):
    """Test KubeArchiveClient."""

    @patch('kubearchive_client.create_authenticated_session')
    @patch('kubearchive_client.get_openshift_token', return_value='test-token')
    @patch('kubearchive_client.discover_kubearchive_api_url', return_value='https://kubearchive.example.com')
    def test_discover_api_url_success(self, mock_url, mock_token, mock_session):
        """Test successful API URL discovery."""
        mock_session.return_value = MagicMock()
        client = KubeArchiveClient()
        self.assertEqual(client.api_url, "https://kubearchive.example.com")

    @patch('kubearchive_client.create_authenticated_session')
    @patch('kubearchive_client.get_openshift_token', return_value='test-token')
    @patch('kubearchive_client.discover_kubearchive_api_url')
    def test_discover_api_url_fallback(self, mock_url, mock_token, mock_session):
        """Test API URL fallback when discovery returns fallback."""
        mock_url.return_value = "https://kubearchive-api-server.kubearchive.svc.cluster.local"
        mock_session.return_value = MagicMock()
        client = KubeArchiveClient()
        self.assertIn("kubearchive-api-server", client.api_url)

    @patch('openshift_auth.subprocess.run')
    def test_get_openshift_token_success(self, mock_run):
        """Test successful token retrieval via openshift_auth."""
        from openshift_auth import get_openshift_token
        mock_run.return_value = MagicMock(
            stdout="sha256~test-token-123",
            returncode=0
        )
        token = get_openshift_token()
        self.assertEqual(token, "sha256~test-token-123")

    @patch('openshift_auth.subprocess.run')
    def test_get_openshift_token_failure(self, mock_run):
        """Test token retrieval failure returns None."""
        from openshift_auth import get_openshift_token
        mock_run.side_effect = subprocess.CalledProcessError(1, 'oc')
        token = get_openshift_token()
        self.assertIsNone(token)

    @patch('kubearchive_client.create_authenticated_session')
    @patch('kubearchive_client.get_openshift_token', return_value='test-token')
    @patch('kubearchive_client.discover_kubearchive_api_url', return_value='https://api.example.com')
    def test_create_session(self, mock_url, mock_token, mock_session):
        """Test session creation with auth headers."""
        session = MagicMock()
        session.headers = {'Authorization': 'Bearer test-token', 'Accept': 'application/json'}
        mock_session.return_value = session

        client = KubeArchiveClient()
        self.assertIn('Authorization', client.session.headers)
        self.assertEqual(client.session.headers['Authorization'], 'Bearer test-token')
        self.assertEqual(client.session.headers['Accept'], 'application/json')

    @patch('kubearchive_client.create_authenticated_session')
    @patch('kubearchive_client.get_openshift_token', return_value='test-token')
    @patch('kubearchive_client.discover_kubearchive_api_url', return_value='https://api.example.com')
    def test_get_pipelinerun_success(self, mock_url, mock_token, mock_session):
        """Test successful PipelineRun fetch."""
        mock_session.return_value = MagicMock()
        client = KubeArchiveClient(namespace="test-ns")

        mock_response = Mock()
        mock_response.json.return_value = {
            'kind': 'PipelineRun',
            'metadata': {'name': 'test-pr'}
        }
        client.session.get = Mock(return_value=mock_response)

        result = client.get_pipelinerun("test-pr")
        self.assertEqual(result['kind'], 'PipelineRun')
        self.assertEqual(result['metadata']['name'], 'test-pr')

        call_args = client.session.get.call_args
        self.assertIn('/apis/tekton.dev/v1/namespaces/test-ns/pipelineruns/test-pr', call_args[0][0])

    @patch('kubearchive_client.create_authenticated_session')
    @patch('kubearchive_client.get_openshift_token', return_value='test-token')
    @patch('kubearchive_client.discover_kubearchive_api_url', return_value='https://api.example.com')
    def test_get_pipelinerun_not_found(self, mock_url, mock_token, mock_session):
        """Test PipelineRun not found."""
        mock_session.return_value = MagicMock()
        client = KubeArchiveClient()
        client.session.get = Mock(side_effect=requests.RequestException("Not found"))

        result = client.get_pipelinerun("non-existent-pr")
        self.assertIsNone(result)

    @patch('kubearchive_client.create_authenticated_session')
    @patch('kubearchive_client.get_openshift_token', return_value='test-token')
    @patch('kubearchive_client.discover_kubearchive_api_url', return_value='https://api.example.com')
    def test_get_taskrun_success(self, mock_url, mock_token, mock_session):
        """Test successful TaskRun fetch."""
        mock_session.return_value = MagicMock()
        client = KubeArchiveClient(namespace="test-ns")

        mock_response = Mock()
        mock_response.json.return_value = {
            'kind': 'TaskRun',
            'metadata': {'name': 'test-tr'}
        }
        client.session.get = Mock(return_value=mock_response)

        result = client.get_taskrun("test-tr")
        self.assertEqual(result['kind'], 'TaskRun')

        call_args = client.session.get.call_args
        self.assertIn('/apis/tekton.dev/v1/namespaces/test-ns/taskruns/test-tr', call_args[0][0])

    @patch('kubearchive_client.create_authenticated_session')
    @patch('kubearchive_client.get_openshift_token', return_value='test-token')
    @patch('kubearchive_client.discover_kubearchive_api_url', return_value='https://api.example.com')
    def test_get_pod_logs(self, mock_url, mock_token, mock_session):
        """Test pod logs retrieval."""
        mock_session.return_value = MagicMock()
        client = KubeArchiveClient()

        mock_response = Mock()
        mock_response.text = "Log line 1\nLog line 2\n"
        client.session.get = Mock(return_value=mock_response)

        logs = client.get_pod_logs("test-pod", container="build")
        self.assertEqual(logs, "Log line 1\nLog line 2\n")

    @patch('kubearchive_client.create_authenticated_session')
    @patch('kubearchive_client.get_openshift_token', return_value='test-token')
    @patch('kubearchive_client.discover_kubearchive_api_url', return_value='https://api.example.com')
    def test_extract_taskruns(self, mock_url, mock_token, mock_session):
        """Test extracting TaskRun names from PipelineRun."""
        mock_session.return_value = MagicMock()
        client = KubeArchiveClient()

        pr_data = {
            'status': {
                'childReferences': [
                    {'kind': 'TaskRun', 'name': 'tr-1'},
                    {'kind': 'TaskRun', 'name': 'tr-2'},
                    {'kind': 'Run', 'name': 'run-1'},
                ]
            }
        }

        taskruns = client.extract_taskruns(pr_data)
        self.assertEqual(len(taskruns), 2)
        self.assertIn('tr-1', taskruns)
        self.assertIn('tr-2', taskruns)
        self.assertNotIn('run-1', taskruns)

    @patch('kubearchive_client.create_authenticated_session')
    @patch('kubearchive_client.get_openshift_token', return_value='test-token')
    @patch('kubearchive_client.discover_kubearchive_api_url', return_value='https://api.example.com')
    def test_extract_failed_steps(self, mock_url, mock_token, mock_session):
        """Test extracting failed step names from TaskRun."""
        mock_session.return_value = MagicMock()
        client = KubeArchiveClient()

        tr_data = {
            'status': {
                'steps': [
                    {'name': 'init', 'terminated': {'exitCode': 0}},
                    {'name': 'build', 'terminated': {'exitCode': 1}},
                    {'name': 'push', 'terminated': {'exitCode': 1}},
                ]
            }
        }

        failed_steps = client.extract_failed_steps(tr_data)
        self.assertEqual(len(failed_steps), 2)
        self.assertIn('build', failed_steps)
        self.assertIn('push', failed_steps)
        self.assertNotIn('init', failed_steps)

    @patch('kubearchive_client.create_authenticated_session')
    @patch('kubearchive_client.get_openshift_token', return_value='test-token')
    @patch('kubearchive_client.discover_kubearchive_api_url', return_value='https://api.example.com')
    def test_get_pipelinerun_logs_integration(self, mock_url, mock_token, mock_session):
        """Test complete PipelineRun logs fetch (integration)."""
        mock_session.return_value = MagicMock()
        client = KubeArchiveClient()

        pr_data = {
            'status': {
                'childReferences': [
                    {'kind': 'TaskRun', 'name': 'tr-1'}
                ]
            }
        }

        tr_data = {
            'status': {
                'podName': 'pod-1',
                'steps': [
                    {'name': 'build', 'terminated': {'exitCode': 1}}
                ]
            }
        }

        logs_text = "Error: build failed"

        client.get_pipelinerun = Mock(return_value=pr_data)
        client.get_taskrun = Mock(return_value=tr_data)
        client.get_pod_logs = Mock(return_value=logs_text)

        result = client.get_pipelinerun_logs("test-pr")
        self.assertIn("build failed", result)
        self.assertIn("TaskRun: tr-1", result)


if __name__ == '__main__':
    unittest.main()
