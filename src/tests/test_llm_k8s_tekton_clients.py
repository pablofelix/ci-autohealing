"""Unit tests for LLM providers, Kubernetes client, and Tekton Results client.

Covers:
  - LLMResponse dataclass immutability and construction
  - create_llm_provider factory function
  - AnthropicDirectProvider: create_message, create_conversation, _parse_response
  - KubernetesClient: CRUD operations, label validation, error handling
  - TektonResultsClient: URL derivation, record queries, log fetching, decoding
"""

import base64
import importlib
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Module reload guard
# ---------------------------------------------------------------------------
# Other test files (e.g. test_poll_jira_coverage) replace
# sys.modules['clients.llm_provider'] with a MagicMock at import time and
# never restore it.  When pytest runs the full suite, our file may be
# *collected* before the contaminator, but test *execution* happens after
# contamination is already in place.
#
# Fix: a module-scoped autouse fixture reloads the affected modules right
# before the first test in this file runs, guaranteeing fresh real classes.
# ---------------------------------------------------------------------------

def _reload_llm_modules():
    """Reload LLM provider modules to undo any sys.modules contamination.

    Must be called at test-execution time, not at collection time, because
    the contamination (sys.modules replacement) may happen between collection
    and execution.
    """
    # 1. Restore real llm_provider module (undo MagicMock replacement)
    llm_mod = sys.modules.get('clients.llm_provider')
    if isinstance(llm_mod, MagicMock):
        # The module entry is a MagicMock; delete it so importlib re-imports
        sys.modules.pop('clients.llm_provider', None)
    importlib.reload(importlib.import_module('clients.llm_provider'))

    # 2. Reload anthropic_provider so it picks up the real LLMResponse/LLMProvider.
    #    anthropic_provider imports ``from anthropic import Anthropic`` at module
    #    level — mock ``anthropic`` during reload so we don't need the real SDK.
    if 'clients.anthropic_provider' in sys.modules:
        with patch.dict('sys.modules', {'anthropic': MagicMock()}):
            importlib.reload(sys.modules['clients.anthropic_provider'])

    # 3. Same for vertex_ai_provider
    if 'clients.vertex_ai_provider' in sys.modules:
        with patch.dict('sys.modules', {'anthropic': MagicMock()}):
            importlib.reload(sys.modules['clients.vertex_ai_provider'])


@pytest.fixture(autouse=True, scope='module')
def _ensure_clean_llm_modules():
    """Reload LLM modules before any test in this file executes.

    After reloading, re-bind the module-level names (LLMResponse,
    create_llm_provider) so that tests using them get the real classes
    instead of stale references captured at collection time.
    """
    _reload_llm_modules()

    # Re-bind module-level names from the freshly-reloaded modules.
    import clients.llm_provider as _fresh
    this_module = sys.modules[__name__]
    this_module.LLMResponse = _fresh.LLMResponse
    this_module.create_llm_provider = _fresh.create_llm_provider


# Import after defining the fixture — at collection time these may bind to
# mocks, but the fixture will reload and re-bind before tests execute.
from clients.llm_provider import LLMResponse, create_llm_provider

# ---------------------------------------------------------------------------
# LLMResponse dataclass
# ---------------------------------------------------------------------------

class TestLLMResponse(unittest.TestCase):
    """Tests for the LLMResponse frozen dataclass."""

    def test_create_with_all_fields(self):
        resp = LLMResponse(
            content='hello',
            tool_calls=[{'name': 'foo'}],
            model='claude-test',
            input_tokens=10,
            output_tokens=20,
            stop_reason='end_turn',
        )
        self.assertEqual(resp.content, 'hello')
        self.assertEqual(resp.tool_calls, [{'name': 'foo'}])
        self.assertEqual(resp.model, 'claude-test')
        self.assertEqual(resp.input_tokens, 10)
        self.assertEqual(resp.output_tokens, 20)
        self.assertEqual(resp.stop_reason, 'end_turn')

    def test_frozen_immutable(self):
        resp = LLMResponse(
            content='x', tool_calls=[], model='m',
            input_tokens=0, output_tokens=0, stop_reason='end',
        )
        with self.assertRaises(AttributeError):
            resp.content = 'modified'


# ---------------------------------------------------------------------------
# create_llm_provider factory
# ---------------------------------------------------------------------------

class TestCreateLLMProvider(unittest.TestCase):
    """Tests for the LLM provider factory function."""

    @patch('clients.llm_provider.VertexAIProvider', create=True)
    def test_vertex_ai_provider(self, mock_vertex_cls):
        """vertex_ai config creates VertexAIProvider."""
        # The factory does a lazy import; we patch the import target.
        config = MagicMock()
        config.provider = 'vertex_ai'
        config.project_id = 'proj'
        config.region = 'us-east1'
        config.model = 'claude-test'

        with patch('clients.vertex_ai_provider.VertexAIProvider') as mock_cls:
            from clients.llm_provider import create_llm_provider as factory
            factory(config)
            mock_cls.assert_called_once_with(
                project_id='proj', region='us-east1', model='claude-test',
            )

    @patch('clients.anthropic_provider.AnthropicDirectProvider')
    def test_anthropic_provider(self, mock_cls):
        """anthropic config creates AnthropicDirectProvider."""
        config = MagicMock()
        config.provider = 'anthropic'
        config.api_key = 'sk-test'
        config.model = 'claude-test'

        from clients.llm_provider import create_llm_provider as factory
        factory(config)
        mock_cls.assert_called_once_with(api_key='sk-test', model='claude-test')

    def test_unknown_provider_raises(self):
        config = MagicMock()
        config.provider = 'openai'
        with self.assertRaises(ValueError) as ctx:
            create_llm_provider(config)
        self.assertIn('openai', str(ctx.exception))


# ---------------------------------------------------------------------------
# AnthropicDirectProvider
# ---------------------------------------------------------------------------

class TestParseResponse(unittest.TestCase):
    """Tests for _parse_response module-level function."""

    def _make_block(self, block_type, **kwargs):
        block = MagicMock()
        block.type = block_type
        for k, v in kwargs.items():
            setattr(block, k, v)
        return block

    def _make_response(self, blocks, model='claude-test',
                       input_tokens=5, output_tokens=10, stop_reason='end_turn'):
        resp = MagicMock()
        resp.content = blocks
        resp.model = model
        resp.usage.input_tokens = input_tokens
        resp.usage.output_tokens = output_tokens
        resp.stop_reason = stop_reason
        return resp

    def test_text_block_only(self):
        from clients.anthropic_provider import _parse_response
        blocks = [self._make_block('text', text='Hello world')]
        resp = self._make_response(blocks)

        result = _parse_response(resp)

        self.assertEqual(result.content, 'Hello world')
        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.model, 'claude-test')

    def test_tool_use_block_only(self):
        from clients.anthropic_provider import _parse_response
        blocks = [self._make_block(
            'tool_use', id='toolu_1', name='analyze', input={'x': 1})]
        resp = self._make_response(blocks)

        result = _parse_response(resp)

        self.assertEqual(result.content, '')
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0]['id'], 'toolu_1')
        self.assertEqual(result.tool_calls[0]['name'], 'analyze')
        self.assertEqual(result.tool_calls[0]['input'], {'x': 1})

    def test_mixed_text_and_tool_use(self):
        from clients.anthropic_provider import _parse_response
        blocks = [
            self._make_block('text', text='Analyzing...'),
            self._make_block('tool_use', id='t1', name='search', input={'q': 'test'}),
            self._make_block('tool_use', id='t2', name='fetch', input={'url': '/'}),
        ]
        resp = self._make_response(blocks)

        result = _parse_response(resp)

        self.assertEqual(result.content, 'Analyzing...')
        self.assertEqual(len(result.tool_calls), 2)
        self.assertEqual(result.tool_calls[0]['name'], 'search')
        self.assertEqual(result.tool_calls[1]['name'], 'fetch')


@patch('clients.anthropic_provider.Anthropic')
class TestAnthropicDirectProvider(unittest.TestCase):
    """Tests for AnthropicDirectProvider."""

    def _make_mock_response(self):
        block = MagicMock()
        block.type = 'text'
        block.text = 'test response'
        resp = MagicMock()
        resp.content = [block]
        resp.model = 'claude-test'
        resp.usage.input_tokens = 100
        resp.usage.output_tokens = 50
        resp.stop_reason = 'end_turn'
        return resp

    def test_create_message_without_tools(self, mock_anthropic_cls):
        from clients.anthropic_provider import AnthropicDirectProvider
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_mock_response()

        provider = AnthropicDirectProvider(api_key='sk-test', model='claude-test')
        result = provider.create_message('system prompt', 'user input')

        call_kwargs = mock_client.messages.create.call_args[1]
        self.assertEqual(call_kwargs['model'], 'claude-test')
        self.assertEqual(call_kwargs['system'], 'system prompt')
        self.assertNotIn('tool_choice', call_kwargs)
        self.assertNotIn('tools', call_kwargs)
        self.assertIsInstance(result, LLMResponse)

    def test_create_message_with_tools(self, mock_anthropic_cls):
        from clients.anthropic_provider import AnthropicDirectProvider
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_mock_response()

        provider = AnthropicDirectProvider(api_key='sk-test', model='claude-test')
        tools = [{'name': 'analyze', 'description': 'Analyze', 'input_schema': {}}]
        provider.create_message('sys', 'usr', tools=tools)

        call_kwargs = mock_client.messages.create.call_args[1]
        self.assertEqual(call_kwargs['tools'], tools)
        self.assertEqual(call_kwargs['tool_choice'], {'type': 'any'})

    def test_create_conversation_without_tools(self, mock_anthropic_cls):
        from clients.anthropic_provider import AnthropicDirectProvider
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_mock_response()

        provider = AnthropicDirectProvider(api_key='sk-test', model='claude-test')
        messages = [{'role': 'user', 'content': 'hi'}]
        result = provider.create_conversation('sys', messages)

        call_kwargs = mock_client.messages.create.call_args[1]
        self.assertEqual(call_kwargs['messages'], messages)
        self.assertNotIn('tool_choice', call_kwargs)
        self.assertIsInstance(result, LLMResponse)

    def test_create_conversation_with_tools_uses_auto(self, mock_anthropic_cls):
        from clients.anthropic_provider import AnthropicDirectProvider
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_mock_response()

        provider = AnthropicDirectProvider(api_key='sk-test', model='claude-test')
        tools = [{'name': 'x'}]
        provider.create_conversation('sys', [{'role': 'user', 'content': 'hi'}], tools=tools)

        call_kwargs = mock_client.messages.create.call_args[1]
        self.assertEqual(call_kwargs['tool_choice'], {'type': 'auto'})

    def test_model_name(self, mock_anthropic_cls):
        from clients.anthropic_provider import AnthropicDirectProvider
        provider = AnthropicDirectProvider(api_key='sk-test', model='claude-sonnet-4-5-20250929')
        self.assertEqual(provider.model_name(), 'claude-sonnet-4-5-20250929')

    def test_create_message_returns_llmresponse(self, mock_anthropic_cls):
        from clients.anthropic_provider import AnthropicDirectProvider
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_mock_response()

        provider = AnthropicDirectProvider(api_key='sk-test', model='claude-test')
        result = provider.create_message('sys', 'usr')

        self.assertEqual(result.content, 'test response')
        self.assertEqual(result.input_tokens, 100)
        self.assertEqual(result.output_tokens, 50)
        self.assertEqual(result.stop_reason, 'end_turn')


# ---------------------------------------------------------------------------
# KubernetesClient
# ---------------------------------------------------------------------------

@patch('clients.kubernetes._ensure_k8s_config')
class TestKubernetesClient(unittest.TestCase):
    """Tests for KubernetesClient wrapping the Python kubernetes library."""

    def _get_client(self):
        from clients.kubernetes import KubernetesClient
        return KubernetesClient(namespace='test-ns')

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_get_pipelinerun_found(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.get_namespaced_custom_object.return_value = {'metadata': {'name': 'pr-1'}}

        k = self._get_client()
        result = k.get_pipelinerun('pr-1')

        self.assertEqual(result, {'metadata': {'name': 'pr-1'}})
        mock_api.get_namespaced_custom_object.assert_called_once_with(
            group='tekton.dev', version='v1', namespace='test-ns',
            plural='pipelineruns', name='pr-1', _request_timeout=10,
        )

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_get_pipelinerun_not_found(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.get_namespaced_custom_object.side_effect = Exception('404')

        k = self._get_client()
        result = k.get_pipelinerun('nonexistent')

        self.assertIsNone(result)

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_get_taskrun_found(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.get_namespaced_custom_object.return_value = {'metadata': {'name': 'tr-1'}}

        k = self._get_client()
        result = k.get_taskrun('tr-1')

        self.assertEqual(result['metadata']['name'], 'tr-1')

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_get_taskrun_not_found(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.get_namespaced_custom_object.side_effect = Exception('Not found')

        k = self._get_client()
        self.assertIsNone(k.get_taskrun('gone'))

    @patch('clients.kubernetes.client.CoreV1Api')
    def test_get_pod_logs_success(self, mock_core_cls, mock_config):
        mock_v1 = MagicMock()
        mock_core_cls.return_value = mock_v1
        mock_v1.read_namespaced_pod_log.return_value = 'log line 1\nlog line 2'

        k = self._get_client()
        result = k.get_pod_logs('pod-1')

        self.assertEqual(result, 'log line 1\nlog line 2')

    @patch('clients.kubernetes.client.CoreV1Api')
    def test_get_pod_logs_with_container(self, mock_core_cls, mock_config):
        mock_v1 = MagicMock()
        mock_core_cls.return_value = mock_v1
        mock_v1.read_namespaced_pod_log.return_value = 'container logs'

        k = self._get_client()
        k.get_pod_logs('pod-1', container='step-build')

        call_kwargs = mock_v1.read_namespaced_pod_log.call_args
        self.assertEqual(call_kwargs[1]['container'], 'step-build')

    @patch('clients.kubernetes.client.CoreV1Api')
    def test_get_pod_logs_with_tail_lines(self, mock_core_cls, mock_config):
        mock_v1 = MagicMock()
        mock_core_cls.return_value = mock_v1
        mock_v1.read_namespaced_pod_log.return_value = 'tail logs'

        k = self._get_client()
        k.get_pod_logs('pod-1', tail_lines=100)

        call_kwargs = mock_v1.read_namespaced_pod_log.call_args
        self.assertEqual(call_kwargs[1]['tail_lines'], 100)

    @patch('clients.kubernetes.client.CoreV1Api')
    def test_get_pod_logs_error(self, mock_core_cls, mock_config):
        mock_v1 = MagicMock()
        mock_core_cls.return_value = mock_v1
        mock_v1.read_namespaced_pod_log.side_effect = Exception('timeout')

        k = self._get_client()
        self.assertIsNone(k.get_pod_logs('pod-1'))

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_get_component_metadata_success(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.get_namespaced_custom_object.return_value = {
            'spec': {
                'source': {'git': {'url': 'https://github.com/org/repo', 'revision': 'main'}},
                'containerImage': 'quay.io/org/img',
                'build-nudges-ref': ['dep-1'],
            },
            'status': {
                'lastPromotedImage': 'quay.io/org/img@sha256:abc',
                'lastBuiltCommit': 'abc123',
            },
        }

        k = self._get_client()
        meta = k.get_component_metadata('my-component')

        self.assertEqual(meta['repository_url'], 'https://github.com/org/repo')
        self.assertEqual(meta['branch'], 'main')
        self.assertEqual(meta['container_image'], 'quay.io/org/img')
        self.assertEqual(meta['last_built_commit'], 'abc123')

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_get_component_metadata_error(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.get_namespaced_custom_object.side_effect = Exception('nope')

        k = self._get_client()
        self.assertIsNone(k.get_component_metadata('missing'))

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_list_components(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.list_namespaced_custom_object.return_value = {
            'items': [
                {
                    'metadata': {'name': 'comp-a', 'creationTimestamp': '2025-01-01'},
                    'spec': {
                        'application': 'myapp',
                        'containerImage': 'img-a',
                        'source': {'git': {'url': 'https://gh.com/a', 'revision': 'main'}},
                    },
                    'status': {'lastBuiltCommit': 'sha1'},
                },
            ],
        }

        k = self._get_client()
        result = k.list_components()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], 'comp-a')
        self.assertEqual(result[0]['application'], 'myapp')

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_list_components_with_application_filter(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.list_namespaced_custom_object.return_value = {
            'items': [
                {'metadata': {'name': 'c1'}, 'spec': {'application': 'app-a'}, 'status': {}},
                {'metadata': {'name': 'c2'}, 'spec': {'application': 'app-b'}, 'status': {}},
            ],
        }

        k = self._get_client()
        result = k.list_components(application='app-a')

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], 'c1')

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_list_components_error(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.list_namespaced_custom_object.side_effect = Exception('err')

        k = self._get_client()
        self.assertEqual(k.list_components(), [])

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_list_pac_repositories(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.list_namespaced_custom_object.return_value = {
            'items': [
                {
                    'metadata': {'name': 'repo-1'},
                    'spec': {
                        'url': 'https://github.com/org/repo',
                        'git_provider': {'branch': 'main'},
                    },
                },
            ],
        }

        k = self._get_client()
        result = k.list_pac_repositories()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], 'repo-1')
        self.assertEqual(result[0]['url'], 'https://github.com/org/repo')

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_list_pac_repositories_error(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.list_namespaced_custom_object.side_effect = Exception('err')

        k = self._get_client()
        self.assertEqual(k.list_pac_repositories(), [])

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_list_recent_pipelineruns(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.list_namespaced_custom_object.return_value = {
            'items': [
                {
                    'metadata': {
                        'name': 'pr-2', 'creationTimestamp': '2025-06-02T00:00:00Z',
                        'labels': {}, 'annotations': {},
                    },
                    'status': {'conditions': [
                        {'type': 'Succeeded', 'status': 'True'},
                    ]},
                },
                {
                    'metadata': {
                        'name': 'pr-1', 'creationTimestamp': '2025-06-01T00:00:00Z',
                        'labels': {}, 'annotations': {},
                    },
                    'status': {'conditions': [
                        {'type': 'Succeeded', 'status': 'False'},
                    ]},
                },
                {
                    'metadata': {
                        'name': 'pr-3', 'creationTimestamp': '2025-06-03T00:00:00Z',
                        'labels': {}, 'annotations': {},
                    },
                    'status': {'conditions': [
                        {'type': 'Succeeded', 'status': 'True', 'reason': 'Running'},
                    ]},
                },
            ],
        }

        k = self._get_client()
        result = k.list_recent_pipelineruns('my-comp', limit=2)

        # Sorted newest first, limited to 2
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['name'], 'pr-3')
        self.assertEqual(result[0]['status'], 'running')
        self.assertEqual(result[1]['name'], 'pr-2')
        self.assertEqual(result[1]['status'], 'succeeded')

    def test_list_recent_pipelineruns_invalid_name(self, mock_config):
        k = self._get_client()
        with self.assertRaises(ValueError):
            k.list_recent_pipelineruns('invalid name with spaces!')

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_list_recent_pipelineruns_error(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api
        mock_api.list_namespaced_custom_object.side_effect = Exception('timeout')

        k = self._get_client()
        self.assertEqual(k.list_recent_pipelineruns('valid-comp'), [])

    @patch('clients.kubernetes.client.CustomObjectsApi')
    def test_trigger_rebuild_success(self, mock_co_api_cls, mock_config):
        mock_api = MagicMock()
        mock_co_api_cls.return_value = mock_api

        k = self._get_client()
        result = k.trigger_rebuild('my-component')

        self.assertTrue(result)
        mock_api.patch_namespaced_custom_object.assert_called_once()
        call_kwargs = mock_api.patch_namespaced_custom_object.call_args
        body = call_kwargs[1]['body']
        self.assertEqual(
            body['metadata']['annotations']['build.appstudio.openshift.io/request'],
            'trigger-pac-build',
        )

    def test_trigger_rebuild_invalid_name(self, mock_config):
        k = self._get_client()
        with self.assertRaises(ValueError):
            k.trigger_rebuild('-bad-name')


# ---------------------------------------------------------------------------
# TektonResultsClient — URL derivation
# ---------------------------------------------------------------------------

class TestDeriveTektonResultsUrl(unittest.TestCase):
    """Tests for _derive_tekton_results_url standalone function."""

    def test_standard_api_url(self):
        from clients.tekton_results import _derive_tekton_results_url
        result = _derive_tekton_results_url('https://api.mycluster.example.com:6443')
        self.assertEqual(
            result,
            'https://tekton-results-tekton-results.apps.mycluster.example.com'
            '/apis/results.tekton.dev/v1alpha2',
        )

    def test_non_standard_hostname_raises(self):
        from clients.tekton_results import _derive_tekton_results_url
        with self.assertRaises(ValueError):
            _derive_tekton_results_url('https://mycluster.example.com:6443')


# ---------------------------------------------------------------------------
# TektonResultsClient
# ---------------------------------------------------------------------------

@patch('clients.tekton_results.create_authenticated_session')
@patch('clients.tekton_results.get_openshift_token')
@patch('clients.tekton_results.discover_openshift_api_url')
class TestTektonResultsClient(unittest.TestCase):
    """Tests for TektonResultsClient."""

    def _make_client(self, mock_discover, mock_token, mock_session,
                     api_url=None, token='test-token'):
        mock_discover.return_value = 'https://api.cluster.example.com:6443'
        mock_token.return_value = token
        mock_session.return_value = MagicMock()

        from clients.tekton_results import TektonResultsClient
        return TektonResultsClient(namespace='test-ns', api_url=api_url)

    def test_init_with_explicit_url(self, mock_discover, mock_token, mock_session):
        client = self._make_client(
            mock_discover, mock_token, mock_session,
            api_url='https://custom-tekton.example.com/api',
        )
        self.assertEqual(client.api_url, 'https://custom-tekton.example.com/api')
        mock_discover.assert_not_called()

    def test_init_derives_url(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session)
        self.assertIn('tekton-results', client.api_url)
        mock_discover.assert_called_once()

    def test_init_no_token_raises(self, mock_discover, mock_token, mock_session):
        mock_discover.return_value = 'https://api.cluster.example.com:6443'
        mock_token.return_value = None

        from clients.tekton_results import TektonResultsClient
        with self.assertRaises(RuntimeError):
            TektonResultsClient(namespace='ns')

    # --- _query_records ---

    def test_query_records_success(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'records': [{'name': 'r1'}, {'name': 'r2'}]}
        client.session.get.return_value = resp

        result = client._query_records('filter_expr')
        self.assertEqual(len(result), 2)

    def test_query_records_clamps_page_size(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'records': []}
        client.session.get.return_value = resp

        # page_size too small
        client._query_records('f', page_size=1)
        params = client.session.get.call_args[1]['params']
        self.assertEqual(params['page_size'], 5)

        # page_size too large
        client._query_records('f', page_size=99999)
        params = client.session.get.call_args[1]['params']
        self.assertEqual(params['page_size'], 10000)

    def test_query_records_http_error(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')
        resp = MagicMock()
        resp.status_code = 500
        client.session.get.return_value = resp

        result = client._query_records('f')
        self.assertEqual(result, [])

    def test_query_records_request_exception(self, mock_discover, mock_token, mock_session):
        import requests
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')
        client.session.get.side_effect = requests.RequestException('timeout')

        result = client._query_records('f')
        self.assertEqual(result, [])

    # --- _decode_record ---

    def test_decode_record_valid(self, mock_discover, mock_token, mock_session):
        from clients.tekton_results import TektonResultsClient
        payload = {'metadata': {'name': 'test'}}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        record = {'data': {'value': encoded}}

        result = TektonResultsClient._decode_record(record)
        self.assertEqual(result, payload)

    def test_decode_record_missing_data_value(self, mock_discover, mock_token, mock_session):
        from clients.tekton_results import TektonResultsClient
        self.assertIsNone(TektonResultsClient._decode_record({'data': {}}))
        self.assertIsNone(TektonResultsClient._decode_record({}))

    def test_decode_record_invalid_base64(self, mock_discover, mock_token, mock_session):
        from clients.tekton_results import TektonResultsClient
        record = {'data': {'value': '!!!not-base64!!!'}}
        self.assertIsNone(TektonResultsClient._decode_record(record))

    # --- query_pipelinerun_records ---

    def test_query_pipelinerun_records_with_application(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        payload = {'metadata': {'name': 'pr-1'}}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'records': [{'data': {'value': encoded}}]}
        client.session.get.return_value = resp

        result = client.query_pipelinerun_records('myapp')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['metadata']['name'], 'pr-1')

        filter_used = client.session.get.call_args[1]['params']['filter']
        self.assertIn("'myapp'", filter_used)

    def test_query_pipelinerun_records_with_component(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'records': []}
        client.session.get.return_value = resp

        client.query_pipelinerun_records('myapp', component='mycomp')
        filter_used = client.session.get.call_args[1]['params']['filter']
        self.assertIn("'mycomp'", filter_used)

    # --- query_taskrun_records ---

    def test_query_taskrun_records(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        payload = {'metadata': {'name': 'tr-1'}}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'records': [
            {'data': {'value': encoded}, 'name': 'ns/results/r1/records/rec1'},
        ]}
        client.session.get.return_value = resp

        result = client.query_taskrun_records('pr-1')
        self.assertEqual(len(result), 1)
        decoded, record_name = result[0]
        self.assertEqual(decoded['metadata']['name'], 'tr-1')
        self.assertEqual(record_name, 'ns/results/r1/records/rec1')

    # --- get_taskrun_logs ---

    def test_get_taskrun_logs_success(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        resp = MagicMock()
        resp.status_code = 200
        resp.text = 'step output logs here'
        client.session.get.return_value = resp

        result = client.get_taskrun_logs('ns/results/abc/records/def')
        self.assertEqual(result, 'step output logs here')

    def test_get_taskrun_logs_short_record_name(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        self.assertIsNone(client.get_taskrun_logs('too/short'))

    def test_get_taskrun_logs_http_error(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        resp = MagicMock()
        resp.status_code = 404
        client.session.get.return_value = resp

        self.assertIsNone(client.get_taskrun_logs('ns/results/abc/records/def'))

    # --- find_failed_taskrun ---

    def test_find_failed_taskrun_found(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        tr_data = {
            'metadata': {'name': 'tr-fail', 'labels': {'tekton.dev/pipelineTask': 'build'}},
            'status': {'conditions': [{'status': 'False', 'message': 'failed', 'reason': 'Failed'}]},
        }
        encoded = base64.b64encode(json.dumps(tr_data).encode()).decode()

        # Mock _query_records for query_taskrun_records
        query_resp = MagicMock()
        query_resp.status_code = 200
        query_resp.json.return_value = {'records': [
            {'data': {'value': encoded}, 'name': 'ns/results/r1/records/rec1'},
        ]}

        # Mock get for logs
        log_resp = MagicMock()
        log_resp.status_code = 200
        log_resp.text = 'error: build failed'

        client.session.get.side_effect = [query_resp, log_resp]

        task_name, logs, record_name = client.find_failed_taskrun('pr-1')

        self.assertEqual(task_name, 'build')
        self.assertEqual(logs, 'error: build failed')
        self.assertEqual(record_name, 'ns/results/r1/records/rec1')

    def test_find_failed_taskrun_none(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        # All TaskRuns succeeded
        tr_data = {
            'metadata': {'name': 'tr-ok', 'labels': {'tekton.dev/pipelineTask': 'test'}},
            'status': {'conditions': [{'status': 'True'}]},
        }
        encoded = base64.b64encode(json.dumps(tr_data).encode()).decode()

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'records': [
            {'data': {'value': encoded}, 'name': 'ns/results/r1/records/rec1'},
        ]}
        client.session.get.return_value = resp

        task_name, logs, record_name = client.find_failed_taskrun('pr-ok')
        self.assertIsNone(task_name)
        self.assertIsNone(logs)
        self.assertIsNone(record_name)

    def test_find_failed_taskrun_condition_message_fallback(self, mock_discover, mock_token, mock_session):
        """When logs are unavailable, falls back to condition message."""
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        tr_data = {
            'metadata': {'name': 'tr-fail', 'labels': {'tekton.dev/pipelineTask': 'deploy'}},
            'status': {'conditions': [{
                'status': 'False',
                'reason': 'PodCreationFailed',
                'message': 'could not create pod',
            }]},
        }
        encoded = base64.b64encode(json.dumps(tr_data).encode()).decode()

        query_resp = MagicMock()
        query_resp.status_code = 200
        query_resp.json.return_value = {'records': [
            {'data': {'value': encoded}, 'name': 'ns/results/r1/records/rec1'},
        ]}

        # Logs return 404
        log_resp = MagicMock()
        log_resp.status_code = 404

        client.session.get.side_effect = [query_resp, log_resp]

        task_name, logs, record_name = client.find_failed_taskrun('pr-fail')

        self.assertEqual(task_name, 'deploy')
        self.assertIn('PodCreationFailed', logs)
        self.assertIn('could not create pod', logs)

    # --- get_pipelinerun_logs ---

    def test_get_pipelinerun_logs_combines(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        tr1 = {
            'metadata': {'name': 'tr-1', 'labels': {'tekton.dev/pipelineTask': 'init'}},
            'status': {'conditions': [{'status': 'True'}]},
        }
        tr2 = {
            'metadata': {'name': 'tr-2', 'labels': {'tekton.dev/pipelineTask': 'build'}},
            'status': {'conditions': [{'status': 'True'}]},
        }
        enc1 = base64.b64encode(json.dumps(tr1).encode()).decode()
        enc2 = base64.b64encode(json.dumps(tr2).encode()).decode()

        query_resp = MagicMock()
        query_resp.status_code = 200
        query_resp.json.return_value = {'records': [
            {'data': {'value': enc1}, 'name': 'ns/results/r1/records/rec1'},
            {'data': {'value': enc2}, 'name': 'ns/results/r2/records/rec2'},
        ]}

        log_resp1 = MagicMock()
        log_resp1.status_code = 200
        log_resp1.text = 'init log'

        log_resp2 = MagicMock()
        log_resp2.status_code = 200
        log_resp2.text = 'build log'

        client.session.get.side_effect = [query_resp, log_resp1, log_resp2]

        result = client.get_pipelinerun_logs('pr-1')

        self.assertIn('init log', result)
        self.assertIn('build log', result)
        self.assertIn('TaskRun: tr-1', result)
        self.assertIn('TaskRun: tr-2', result)

    def test_get_pipelinerun_logs_failed_only(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        tr_ok = {
            'metadata': {'name': 'tr-ok', 'labels': {'tekton.dev/pipelineTask': 'init'}},
            'status': {'conditions': [{'status': 'True'}]},
        }
        tr_fail = {
            'metadata': {'name': 'tr-fail', 'labels': {'tekton.dev/pipelineTask': 'build'}},
            'status': {'conditions': [{'status': 'False'}]},
        }
        enc_ok = base64.b64encode(json.dumps(tr_ok).encode()).decode()
        enc_fail = base64.b64encode(json.dumps(tr_fail).encode()).decode()

        query_resp = MagicMock()
        query_resp.status_code = 200
        query_resp.json.return_value = {'records': [
            {'data': {'value': enc_ok}, 'name': 'ns/results/r1/records/rec1'},
            {'data': {'value': enc_fail}, 'name': 'ns/results/r2/records/rec2'},
        ]}

        log_resp = MagicMock()
        log_resp.status_code = 200
        log_resp.text = 'failed build log'

        client.session.get.side_effect = [query_resp, log_resp]

        result = client.get_pipelinerun_logs('pr-1', failed_only=True)

        self.assertIn('failed build log', result)
        # Should NOT contain the successful TaskRun
        self.assertNotIn('tr-ok', result)

    def test_get_pipelinerun_logs_truncation(self, mock_discover, mock_token, mock_session):
        """Second TaskRun is dropped when cumulative size exceeds max_log_size."""
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        tr1 = {
            'metadata': {'name': 'tr-1', 'labels': {'tekton.dev/pipelineTask': 'init'}},
            'status': {'conditions': [{'status': 'True'}]},
        }
        tr2 = {
            'metadata': {'name': 'tr-2', 'labels': {'tekton.dev/pipelineTask': 'build'}},
            'status': {'conditions': [{'status': 'True'}]},
        }
        enc1 = base64.b64encode(json.dumps(tr1).encode()).decode()
        enc2 = base64.b64encode(json.dumps(tr2).encode()).decode()

        query_resp = MagicMock()
        query_resp.status_code = 200
        query_resp.json.return_value = {'records': [
            {'data': {'value': enc1}, 'name': 'ns/results/r1/records/rec1'},
            {'data': {'value': enc2}, 'name': 'ns/results/r2/records/rec2'},
        ]}

        log_resp1 = MagicMock()
        log_resp1.status_code = 200
        log_resp1.text = 'short'  # fits within limit

        log_resp2 = MagicMock()
        log_resp2.status_code = 200
        log_resp2.text = 'x' * 500  # would exceed limit

        client.session.get.side_effect = [query_resp, log_resp1, log_resp2]

        # First section (~40 header + 5 body = ~45) fits; second (~540) pushes over
        result = client.get_pipelinerun_logs('pr-1', max_log_size=100)

        self.assertIsNotNone(result)
        self.assertIn('tr-1', result)
        # tr-2 should be excluded because cumulative size exceeded max
        self.assertNotIn('tr-2', result)

    def test_get_pipelinerun_logs_no_taskruns(self, mock_discover, mock_token, mock_session):
        client = self._make_client(mock_discover, mock_token, mock_session,
                                   api_url='https://tr.example.com/api')

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'records': []}
        client.session.get.return_value = resp

        result = client.get_pipelinerun_logs('pr-empty')
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
