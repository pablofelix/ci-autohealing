"""Tests for KubernetesEventsSource enrichment source."""

import os
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

sys.modules.setdefault('logger', MagicMock(setup_logger=MagicMock(return_value=MagicMock())))
sys.modules.setdefault('repositories.connection', MagicMock())
sys.modules.setdefault('psycopg2', MagicMock())
sys.modules.setdefault('psycopg2.pool', MagicMock())

from enrichment.sources.kubernetes_events import KubernetesEventsSource

INFRA_REASONS = {
    'OOMKilling', 'Evicted', 'FailedScheduling', 'NodeNotReady',
    'BackOff', 'FailedMount', 'FailedCreatePodSandBox', 'Preempting',
}


def _make_config(namespace='rhoai-v3-5-tenant'):
    config = MagicMock()
    config.k8s.namespace = namespace
    return config


def _make_event(reason, message, pod_name, timestamp=None):
    """Build a mock K8s Event object matching kubernetes client format."""
    ts = timestamp or datetime(2026, 7, 16, 10, 21, 0, tzinfo=UTC)
    event = MagicMock()
    event.reason = reason
    event.message = message
    event.last_timestamp = ts
    event.event_time = None
    event.involved_object = MagicMock()
    event.involved_object.name = pod_name
    event.involved_object.kind = 'Pod'
    return event


class TestKubernetesEventsSourceProperties:

    def test_source_name(self):
        src = KubernetesEventsSource(_make_config())
        assert src.source_name() == 'kubernetes_events'

    def test_requires_external_api(self):
        src = KubernetesEventsSource(_make_config())
        assert src.requires_external_api is True

    def test_timeout_seconds(self):
        src = KubernetesEventsSource(_make_config())
        assert src.timeout_seconds == 10


class TestKubernetesEventsFetch:

    @patch('enrichment.sources.kubernetes_events._ensure_k8s_config')
    @patch('enrichment.sources.kubernetes_events.client')
    def test_finds_eviction_event(self, mock_client, mock_k8s_config):
        pr_name = 'pytorch-rocm-on-push-bzlvt'
        pod_name = pr_name + '-build-images-pod'
        event = _make_event('Evicted', 'low on ephemeral-storage', pod_name)

        mock_v1 = MagicMock()
        mock_v1.list_namespaced_event.return_value.items = [event]
        mock_client.CoreV1Api.return_value = mock_v1

        src = KubernetesEventsSource(_make_config())
        result = src.fetch({
            'pipelinerun_name': pr_name,
            'component_name': 'pytorch-rocm-v3-5',
        })

        assert result is not None
        events = result['kubernetes_events']['events']
        assert len(events) == 1
        assert events[0]['reason'] == 'Evicted'
        assert result['kubernetes_events']['has_infra_events'] is True

    @patch('enrichment.sources.kubernetes_events._ensure_k8s_config')
    @patch('enrichment.sources.kubernetes_events.client')
    def test_filters_non_infra_events(self, mock_client, mock_k8s_config):
        pr_name = 'my-pipeline-run'
        pod_name = pr_name + '-build-pod'
        event = _make_event('Pulled', 'image pulled successfully', pod_name)

        mock_v1 = MagicMock()
        mock_v1.list_namespaced_event.return_value.items = [event]
        mock_client.CoreV1Api.return_value = mock_v1

        src = KubernetesEventsSource(_make_config())
        result = src.fetch({
            'pipelinerun_name': pr_name,
            'component_name': 'test',
        })

        assert result is None

    @patch('enrichment.sources.kubernetes_events._ensure_k8s_config')
    @patch('enrichment.sources.kubernetes_events.client')
    def test_empty_events_returns_none(self, mock_client, mock_k8s_config):
        mock_v1 = MagicMock()
        mock_v1.list_namespaced_event.return_value.items = []
        mock_client.CoreV1Api.return_value = mock_v1

        src = KubernetesEventsSource(_make_config())
        result = src.fetch({
            'pipelinerun_name': 'some-pr',
            'component_name': 'test',
        })

        assert result is None

    @patch('enrichment.sources.kubernetes_events._ensure_k8s_config')
    @patch('enrichment.sources.kubernetes_events.client')
    def test_api_failure_returns_none(self, mock_client, mock_k8s_config):
        mock_v1 = MagicMock()
        mock_v1.list_namespaced_event.side_effect = Exception("API unreachable")
        mock_client.CoreV1Api.return_value = mock_v1

        src = KubernetesEventsSource(_make_config())
        result = src.fetch({
            'pipelinerun_name': 'some-pr',
            'component_name': 'test',
        })

        assert result is None

    def test_missing_pipelinerun_name_returns_none(self):
        src = KubernetesEventsSource(_make_config())
        result = src.fetch({
            'component_name': 'test',
        })
        assert result is None

    @patch('enrichment.sources.kubernetes_events._ensure_k8s_config')
    @patch('enrichment.sources.kubernetes_events.client')
    def test_filters_events_by_pipelinerun_prefix(self, mock_client, mock_k8s_config):
        pr_name = 'my-pipeline-run'
        matching_event = _make_event('OOMKilling', 'oom kill', pr_name + '-step-pod')
        unrelated_event = _make_event('OOMKilling', 'oom kill', 'other-pipeline-pod')

        mock_v1 = MagicMock()
        mock_v1.list_namespaced_event.return_value.items = [matching_event, unrelated_event]
        mock_client.CoreV1Api.return_value = mock_v1

        src = KubernetesEventsSource(_make_config())
        result = src.fetch({
            'pipelinerun_name': pr_name,
            'component_name': 'test',
        })

        assert result is not None
        assert result['kubernetes_events']['event_count'] == 1

    @patch('enrichment.sources.kubernetes_events._ensure_k8s_config')
    @patch('enrichment.sources.kubernetes_events.client')
    def test_multiple_infra_events(self, mock_client, mock_k8s_config):
        pr_name = 'my-pipeline-run'
        events = [
            _make_event('OOMKilling', 'memory limit', pr_name + '-pod-a'),
            _make_event('Evicted', 'disk pressure', pr_name + '-pod-b'),
        ]

        mock_v1 = MagicMock()
        mock_v1.list_namespaced_event.return_value.items = events
        mock_client.CoreV1Api.return_value = mock_v1

        src = KubernetesEventsSource(_make_config())
        result = src.fetch({
            'pipelinerun_name': pr_name,
            'component_name': 'test',
        })

        assert result is not None
        assert result['kubernetes_events']['event_count'] == 2

    @patch('enrichment.sources.kubernetes_events._ensure_k8s_config')
    @patch('enrichment.sources.kubernetes_events.client')
    def test_event_time_fallback(self, mock_client, mock_k8s_config):
        """When last_timestamp is None, use event_time."""
        pr_name = 'my-pr'
        event = _make_event('Evicted', 'evicted', pr_name + '-pod')
        event.last_timestamp = None
        event.event_time = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

        mock_v1 = MagicMock()
        mock_v1.list_namespaced_event.return_value.items = [event]
        mock_client.CoreV1Api.return_value = mock_v1

        src = KubernetesEventsSource(_make_config())
        result = src.fetch({
            'pipelinerun_name': pr_name,
            'component_name': 'test',
        })

        assert result is not None
        assert '2026-07-16' in result['kubernetes_events']['events'][0]['timestamp']
