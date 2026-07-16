"""Kubernetes Events enrichment source.

Queries the K8s Events API for pod-level events related to a failed PipelineRun.
Events have ~1 hour TTL, so this source may return None for old failures.
"""

from typing import Any, Dict, List, Optional

from kubernetes import client

from enrichment.context_source import ContextSource
from logger import setup_logger
from openshift_auth import _ensure_k8s_config

logger = setup_logger(__name__)

INFRA_REASONS = frozenset({
    'OOMKilling', 'Evicted', 'FailedScheduling', 'NodeNotReady',
    'BackOff', 'FailedMount', 'FailedCreatePodSandBox', 'Preempting',
})


class KubernetesEventsSource(ContextSource):
    """Queries K8s Events API for infra-related pod events."""

    def fetch(self, failure: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pr_name = failure.get('pipelinerun_name')
        if not pr_name:
            return None

        try:
            _ensure_k8s_config()
            v1 = client.CoreV1Api()
            ns = self.config.k8s.namespace

            event_list = v1.list_namespaced_event(
                namespace=ns,
                _request_timeout=self.timeout_seconds,
            )

            infra_events = self._filter_events(event_list.items, pr_name)
            if not infra_events:
                return None

            return {
                'kubernetes_events': {
                    'events': infra_events,
                    'event_count': len(infra_events),
                    'has_infra_events': True,
                }
            }
        except Exception as e:
            logger.warning("KubernetesEventsSource error: %s", e)
            return None

    def _filter_events(
        self, events: List, pr_name: str
    ) -> List[Dict[str, Any]]:
        result = []
        for event in events:
            obj_name = getattr(event.involved_object, 'name', '') or ''
            if not obj_name.startswith(pr_name):
                continue
            if event.reason not in INFRA_REASONS:
                continue

            ts = event.last_timestamp or event.event_time
            ts_str = ts.isoformat() if ts else 'unknown'

            result.append({
                'reason': event.reason,
                'message': event.message or '',
                'timestamp': ts_str,
                'involved_object': obj_name,
            })
        return result

    def source_name(self) -> str:
        return 'kubernetes_events'

    @property
    def timeout_seconds(self) -> int:
        return 10

    @property
    def requires_external_api(self) -> bool:
        return True
