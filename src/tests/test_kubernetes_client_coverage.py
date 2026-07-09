"""Comprehensive tests for KubernetesClient — covers every public method."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest

from clients.kubernetes import _LABEL_VALUE_RE, KubernetesClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def k8s_client():
    """Return a KubernetesClient with k8s config loading disabled."""
    with patch('clients.kubernetes._ensure_k8s_config'):
        return KubernetesClient(namespace='test-ns')


@pytest.fixture
def k8s_client_no_ns():
    """Return a KubernetesClient without a default namespace."""
    with patch('clients.kubernetes._ensure_k8s_config'):
        return KubernetesClient()


# ---------------------------------------------------------------------------
# _LABEL_VALUE_RE validation
# ---------------------------------------------------------------------------

class TestLabelValueRegex:
    def test_valid_simple_name(self):
        assert _LABEL_VALUE_RE.match('my-component')

    def test_valid_with_dots_and_underscores(self):
        assert _LABEL_VALUE_RE.match('odh_model.serving_v2')

    def test_valid_single_char(self):
        assert _LABEL_VALUE_RE.match('x')

    def test_valid_max_length(self):
        name = 'a' + 'b' * 62
        assert _LABEL_VALUE_RE.match(name)

    def test_invalid_starts_with_dash(self):
        assert _LABEL_VALUE_RE.match('-bad') is None

    def test_invalid_starts_with_dot(self):
        assert _LABEL_VALUE_RE.match('.bad') is None

    def test_invalid_empty(self):
        assert _LABEL_VALUE_RE.match('') is None

    def test_invalid_special_chars(self):
        assert _LABEL_VALUE_RE.match('!!!invalid') is None

    def test_invalid_too_long(self):
        name = 'a' + 'b' * 63  # 64 chars total, exceeds 63
        assert _LABEL_VALUE_RE.match(name) is None

    def test_invalid_space(self):
        assert _LABEL_VALUE_RE.match('has space') is None


# ---------------------------------------------------------------------------
# get_pipelinerun
# ---------------------------------------------------------------------------

class TestGetPipelinerun:
    def test_success_returns_dict(self, k8s_client):
        expected = {
            'metadata': {'name': 'build-abc'},
            'status': {'conditions': []},
        }
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.return_value = expected

            result = k8s_client.get_pipelinerun('build-abc')

        assert result == expected
        mock_api.get_namespaced_custom_object.assert_called_once_with(
            group='tekton.dev', version='v1', namespace='test-ns',
            plural='pipelineruns', name='build-abc',
            _request_timeout=10,
        )

    def test_exception_returns_none(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.side_effect = Exception('404 Not Found')

            result = k8s_client.get_pipelinerun('nonexistent')

        assert result is None

    def test_namespace_override(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.return_value = {}

            k8s_client.get_pipelinerun('run-1', namespace='override-ns')

        mock_api.get_namespaced_custom_object.assert_called_once_with(
            group='tekton.dev', version='v1', namespace='override-ns',
            plural='pipelineruns', name='run-1',
            _request_timeout=10,
        )

    def test_uses_default_namespace(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.return_value = {}

            k8s_client.get_pipelinerun('run-1')

        call_kwargs = mock_api.get_namespaced_custom_object.call_args
        assert call_kwargs[1]['namespace'] == 'test-ns'


# ---------------------------------------------------------------------------
# get_taskrun
# ---------------------------------------------------------------------------

class TestGetTaskrun:
    def test_success_returns_dict(self, k8s_client):
        expected = {'metadata': {'name': 'task-xyz'}, 'spec': {}}
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.return_value = expected

            result = k8s_client.get_taskrun('task-xyz')

        assert result == expected
        mock_api.get_namespaced_custom_object.assert_called_once_with(
            group='tekton.dev', version='v1', namespace='test-ns',
            plural='taskruns', name='task-xyz',
            _request_timeout=10,
        )

    def test_exception_returns_none(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.side_effect = RuntimeError('oops')

            result = k8s_client.get_taskrun('task-xyz')

        assert result is None

    def test_namespace_fallback(self, k8s_client):
        """When no namespace arg is given, uses self.namespace."""
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.return_value = {}

            k8s_client.get_taskrun('tr-1')

        call_kwargs = mock_api.get_namespaced_custom_object.call_args
        assert call_kwargs[1]['namespace'] == 'test-ns'

    def test_namespace_override(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.return_value = {}

            k8s_client.get_taskrun('tr-1', namespace='other-ns')

        call_kwargs = mock_api.get_namespaced_custom_object.call_args
        assert call_kwargs[1]['namespace'] == 'other-ns'


# ---------------------------------------------------------------------------
# get_pod_logs
# ---------------------------------------------------------------------------

class TestGetPodLogs:
    def test_with_container(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_v1.read_namespaced_pod_log.return_value = 'log line 1\nlog line 2'

            result = k8s_client.get_pod_logs('pod-1', container='step-build')

        assert result == 'log line 1\nlog line 2'
        mock_v1.read_namespaced_pod_log.assert_called_once_with(
            'pod-1', 'test-ns',
            _request_timeout=30, container='step-build', tail_lines=5000,
        )

    def test_without_container(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_v1.read_namespaced_pod_log.return_value = 'some output'

            result = k8s_client.get_pod_logs('pod-1')

        assert result == 'some output'
        mock_v1.read_namespaced_pod_log.assert_called_once_with(
            'pod-1', 'test-ns',
            _request_timeout=30, tail_lines=5000,
        )

    def test_custom_tail_lines(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_v1.read_namespaced_pod_log.return_value = 'output'

            k8s_client.get_pod_logs('pod-1', tail_lines=100)

        call_kwargs = mock_v1.read_namespaced_pod_log.call_args
        assert call_kwargs[1]['tail_lines'] == 100

    def test_tail_lines_zero_omitted(self, k8s_client):
        """When tail_lines is 0 (falsy), it should not be passed as kwarg."""
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_v1.read_namespaced_pod_log.return_value = 'output'

            k8s_client.get_pod_logs('pod-1', tail_lines=0)

        call_kwargs = mock_v1.read_namespaced_pod_log.call_args
        assert 'tail_lines' not in call_kwargs[1]

    def test_empty_logs_returns_none(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_v1.read_namespaced_pod_log.return_value = ''

            result = k8s_client.get_pod_logs('pod-1')

        assert result is None

    def test_none_logs_returns_none(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_v1.read_namespaced_pod_log.return_value = None

            result = k8s_client.get_pod_logs('pod-1')

        assert result is None

    def test_exception_returns_none(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_v1.read_namespaced_pod_log.side_effect = Exception('pod not found')

            result = k8s_client.get_pod_logs('pod-1')

        assert result is None

    def test_namespace_override(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_v1 = MagicMock()
            mock_client.CoreV1Api.return_value = mock_v1
            mock_v1.read_namespaced_pod_log.return_value = 'logs'

            k8s_client.get_pod_logs('pod-1', namespace='custom-ns')

        args = mock_v1.read_namespaced_pod_log.call_args[0]
        assert args[1] == 'custom-ns'


# ---------------------------------------------------------------------------
# get_component_metadata
# ---------------------------------------------------------------------------

class TestGetComponentMetadata:
    def _make_component_cr(self):
        return {
            'spec': {
                'source': {
                    'git': {
                        'url': 'https://github.com/org/repo',
                        'revision': 'rhoai-2.19',
                    },
                },
                'containerImage': 'quay.io/org/image:latest',
                'build-nudges-ref': ['dep-a', 'dep-b'],
            },
            'status': {
                'lastPromotedImage': 'quay.io/org/image@sha256:abc123',
                'lastBuiltCommit': 'deadbeef',
            },
        }

    def test_success_returns_full_dict(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.return_value = self._make_component_cr()

            result = k8s_client.get_component_metadata('my-component')

        assert result == {
            'repository_url': 'https://github.com/org/repo',
            'branch': 'rhoai-2.19',
            'last_promoted_image': 'quay.io/org/image@sha256:abc123',
            'last_built_commit': 'deadbeef',
            'container_image': 'quay.io/org/image:latest',
            'nudges': ['dep-a', 'dep-b'],
        }
        mock_api.get_namespaced_custom_object.assert_called_once_with(
            group='appstudio.redhat.com', version='v1alpha1',
            namespace='test-ns', plural='components', name='my-component',
            _request_timeout=10,
        )

    def test_missing_fields_return_defaults(self, k8s_client):
        """Component CR with minimal data still returns a valid dict."""
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.return_value = {'spec': {}, 'status': {}}

            result = k8s_client.get_component_metadata('bare-component')

        assert result['repository_url'] == ''
        assert result['branch'] == ''
        assert result['last_promoted_image'] == ''
        assert result['last_built_commit'] == ''
        assert result['container_image'] == ''
        assert result['nudges'] == []

    def test_exception_returns_none(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.side_effect = Exception('gone')

            result = k8s_client.get_component_metadata('missing')

        assert result is None

    def test_namespace_override(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.return_value = self._make_component_cr()

            k8s_client.get_component_metadata('comp', namespace='prod-ns')

        call_kwargs = mock_api.get_namespaced_custom_object.call_args
        assert call_kwargs[1]['namespace'] == 'prod-ns'


# ---------------------------------------------------------------------------
# list_components
# ---------------------------------------------------------------------------

class TestListComponents:
    def _make_items(self, count=2, application='rhoai'):
        items = []
        for i in range(count):
            items.append({
                'metadata': {
                    'name': f'comp-{i}',
                    'creationTimestamp': f'2026-07-0{i+1}T00:00:00Z',
                },
                'spec': {
                    'application': application,
                    'containerImage': f'quay.io/img-{i}',
                    'source': {'git': {'url': f'https://github.com/org/repo-{i}', 'revision': 'main'}},
                    'build-nudges-ref': [],
                },
                'status': {'lastBuiltCommit': f'sha{i}'},
            })
        return items

    def test_multiple_items(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {
                'items': self._make_items(3),
            }

            result = k8s_client.list_components()

        assert len(result) == 3
        assert result[0]['name'] == 'comp-0'
        assert result[1]['repository_url'] == 'https://github.com/org/repo-1'

    def test_filtered_by_application(self, k8s_client):
        items = self._make_items(2, application='rhoai') + self._make_items(1, application='odh')
        # Fix names so they don't collide
        items[2]['metadata']['name'] = 'odh-comp-0'
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_components(application='rhoai')

        assert len(result) == 2
        assert all(c['application'] == 'rhoai' for c in result)

    def test_no_items(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': []}

            result = k8s_client.list_components()

        assert result == []

    def test_exception_returns_empty_list(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.side_effect = Exception('network error')

            result = k8s_client.list_components()

        assert result == []

    def test_component_dict_fields(self, k8s_client):
        """Each returned dict has all expected keys."""
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {
                'items': self._make_items(1),
            }

            result = k8s_client.list_components()

        expected_keys = {
            'name', 'application', 'container_image', 'repository_url',
            'branch', 'last_built_commit', 'nudges', 'created_at',
        }
        assert set(result[0].keys()) == expected_keys


# ---------------------------------------------------------------------------
# list_pac_repositories
# ---------------------------------------------------------------------------

class TestListPacRepositories:
    def test_multiple_repos(self, k8s_client):
        items = [
            {
                'metadata': {'name': 'repo-a'},
                'spec': {
                    'url': 'https://github.com/org/repo-a',
                    'git_provider': {'branch': 'main'},
                },
            },
            {
                'metadata': {'name': 'repo-b'},
                'spec': {
                    'url': 'https://github.com/org/repo-b',
                    'git_provider': {'branch': 'rhoai-2.19'},
                },
            },
        ]
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_pac_repositories()

        assert len(result) == 2
        assert result[0] == {'name': 'repo-a', 'url': 'https://github.com/org/repo-a', 'branch': 'main'}
        assert result[1]['branch'] == 'rhoai-2.19'

    def test_empty_repos(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': []}

            result = k8s_client.list_pac_repositories()

        assert result == []

    def test_exception_returns_empty_list(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.side_effect = Exception('forbidden')

            result = k8s_client.list_pac_repositories()

        assert result == []

    def test_missing_git_provider(self, k8s_client):
        """Repo CR without git_provider still returns branch as empty string."""
        items = [
            {
                'metadata': {'name': 'repo-c'},
                'spec': {'url': 'https://github.com/org/repo-c'},
            },
        ]
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_pac_repositories()

        assert result[0]['branch'] == ''

    def test_calls_correct_api_group(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': []}

            k8s_client.list_pac_repositories()

        call_kwargs = mock_api.list_namespaced_custom_object.call_args
        assert call_kwargs[1]['group'] == 'pipelinesascode.tekton.dev'
        assert call_kwargs[1]['version'] == 'v1alpha1'
        assert call_kwargs[1]['plural'] == 'repositories'


# ---------------------------------------------------------------------------
# list_recent_pipelineruns
# ---------------------------------------------------------------------------

class TestListRecentPipelineruns:
    def _make_run(self, name, created_at, status_val='True', reason=None, has_conditions=True):
        conditions = []
        if has_conditions:
            cond = {'type': 'Succeeded', 'status': status_val}
            if reason:
                cond['reason'] = reason
            conditions.append(cond)
        return {
            'metadata': {
                'name': name,
                'creationTimestamp': created_at,
                'labels': {
                    'pipelinesascode.tekton.dev/sha': 'abc123',
                    'pipelinesascode.tekton.dev/event-type': 'push',
                },
                'annotations': {
                    'chains.tekton.dev/signed': 'true',
                },
            },
            'status': {'conditions': conditions},
        }

    def test_valid_name_with_results_sorted(self, k8s_client):
        items = [
            self._make_run('run-old', '2026-06-28T10:00:00Z'),
            self._make_run('run-new', '2026-06-30T12:00:00Z'),
            self._make_run('run-mid', '2026-06-29T08:00:00Z'),
        ]
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_recent_pipelineruns('my-component')

        assert len(result) == 3
        assert result[0]['name'] == 'run-new'
        assert result[1]['name'] == 'run-mid'
        assert result[2]['name'] == 'run-old'

    def test_respects_limit(self, k8s_client):
        items = [self._make_run(f'run-{i}', f'2026-06-{20+i:02d}T00:00:00Z') for i in range(10)]
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_recent_pipelineruns('my-component', limit=3)

        assert len(result) == 3

    def test_default_limit_is_5(self, k8s_client):
        items = [self._make_run(f'run-{i}', f'2026-06-{10+i:02d}T00:00:00Z') for i in range(8)]
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_recent_pipelineruns('my-component')

        assert len(result) == 5

    def test_status_succeeded(self, k8s_client):
        items = [self._make_run('run-ok', '2026-07-01T00:00:00Z', status_val='True')]
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_recent_pipelineruns('my-component')

        assert result[0]['status'] == 'succeeded'

    def test_status_failed(self, k8s_client):
        items = [self._make_run('run-fail', '2026-07-01T00:00:00Z', status_val='False')]
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_recent_pipelineruns('my-component')

        assert result[0]['status'] == 'failed'

    def test_status_running(self, k8s_client):
        items = [self._make_run('run-active', '2026-07-01T00:00:00Z', status_val='Unknown', reason='Running')]
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_recent_pipelineruns('my-component')

        assert result[0]['status'] == 'running'

    def test_status_unknown_no_conditions(self, k8s_client):
        items = [self._make_run('run-unknown', '2026-07-01T00:00:00Z', has_conditions=False)]
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_recent_pipelineruns('my-component')

        assert result[0]['status'] == 'unknown'

    def test_result_dict_fields(self, k8s_client):
        items = [self._make_run('run-1', '2026-07-01T00:00:00Z')]
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': items}

            result = k8s_client.list_recent_pipelineruns('my-component')

        expected_keys = {'name', 'status', 'commit_sha', 'event_type', 'created_at', 'chains_signed'}
        assert set(result[0].keys()) == expected_keys
        assert result[0]['commit_sha'] == 'abc123'
        assert result[0]['event_type'] == 'push'
        assert result[0]['chains_signed'] == 'true'

    def test_invalid_name_raises_valueerror(self, k8s_client):
        with pytest.raises(ValueError, match='Invalid component name'):
            k8s_client.list_recent_pipelineruns('!!!invalid')

    def test_empty_name_raises_valueerror(self, k8s_client):
        with pytest.raises(ValueError, match='Invalid component name'):
            k8s_client.list_recent_pipelineruns('')

    def test_name_starting_with_dash_raises_valueerror(self, k8s_client):
        with pytest.raises(ValueError, match='Invalid component name'):
            k8s_client.list_recent_pipelineruns('-starts-with-dash')

    def test_exception_returns_empty_list(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.side_effect = Exception('timeout')

            result = k8s_client.list_recent_pipelineruns('my-component')

        assert result == []

    def test_label_selector_format(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': []}

            k8s_client.list_recent_pipelineruns('odh-dashboard')

        call_kwargs = mock_api.list_namespaced_custom_object.call_args
        assert call_kwargs[1]['label_selector'] == 'appstudio.openshift.io/component=odh-dashboard'

    def test_no_items_returns_empty_list(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.list_namespaced_custom_object.return_value = {'items': []}

            result = k8s_client.list_recent_pipelineruns('my-component')

        assert result == []


# ---------------------------------------------------------------------------
# trigger_rebuild
# ---------------------------------------------------------------------------

class TestTriggerRebuild:
    def test_success_returns_true(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api

            result = k8s_client.trigger_rebuild('my-component')

        assert result is True

    def test_calls_patch_with_correct_body(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api

            k8s_client.trigger_rebuild('my-component')

        expected_body = {
            'metadata': {
                'annotations': {
                    'build.appstudio.openshift.io/request': 'trigger-pac-build',
                },
            },
        }
        mock_api.patch_namespaced_custom_object.assert_called_once_with(
            group='appstudio.redhat.com', version='v1alpha1',
            namespace='test-ns', plural='components', name='my-component',
            body=expected_body,
        )

    def test_invalid_name_raises_valueerror(self, k8s_client):
        with pytest.raises(ValueError, match='Invalid component name'):
            k8s_client.trigger_rebuild('!!!invalid')

    def test_empty_name_raises_valueerror(self, k8s_client):
        with pytest.raises(ValueError, match='Invalid component name'):
            k8s_client.trigger_rebuild('')

    def test_namespace_override(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api

            k8s_client.trigger_rebuild('comp-1', namespace='prod-ns')

        call_kwargs = mock_api.patch_namespaced_custom_object.call_args
        assert call_kwargs[1]['namespace'] == 'prod-ns'

    def test_calls_ensure_k8s_config(self, k8s_client):
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config') as mock_config:
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api

            k8s_client.trigger_rebuild('comp-1')

        mock_config.assert_called_once()

    def test_api_exception_propagates(self, k8s_client):
        """Unlike get_* methods, trigger_rebuild does NOT catch exceptions."""
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.patch_namespaced_custom_object.side_effect = Exception('forbidden')

            with pytest.raises(Exception, match='forbidden'):
                k8s_client.trigger_rebuild('comp-1')


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_none_default_namespace(self, k8s_client_no_ns):
        """Client with no namespace passes None to API calls."""
        with patch('clients.kubernetes.client') as mock_client, \
             patch('clients.kubernetes._ensure_k8s_config'):
            mock_api = MagicMock()
            mock_client.CustomObjectsApi.return_value = mock_api
            mock_api.get_namespaced_custom_object.return_value = {}

            k8s_client_no_ns.get_pipelinerun('run-1')

        call_kwargs = mock_api.get_namespaced_custom_object.call_args
        assert call_kwargs[1]['namespace'] is None

    def test_init_stores_namespace(self):
        with patch('clients.kubernetes._ensure_k8s_config'):
            c = KubernetesClient(namespace='my-ns')
        assert c.namespace == 'my-ns'

    def test_init_no_namespace(self):
        with patch('clients.kubernetes._ensure_k8s_config'):
            c = KubernetesClient()
        assert c.namespace is None

    def test_inherits_pipeline_run_source(self):
        """KubernetesClient implements PipelineRunSource interface."""
        from clients.pipeline_source import PipelineRunSource
        with patch('clients.kubernetes._ensure_k8s_config'):
            c = KubernetesClient()
        assert isinstance(c, PipelineRunSource)
