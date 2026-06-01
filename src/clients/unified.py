"""Unified pipeline client using Chain of Responsibility.

Tries multiple PipelineRunSource implementations in priority order,
returning the first successful result.
"""

from kubernetes import client

from clients.kubernetes import KubernetesClient
from clients.kubearchive import KubeArchiveClient
from clients.tekton_results import TektonResultsClient
from openshift_auth import _ensure_k8s_config


class UnifiedPipelineClient:
    """Tries multiple data sources in priority order.

    Default chain: Kubernetes (live) -> KubeArchive (archived).
    Falls back to direct pod log retrieval via the Python API as a last resort.
    """

    def __init__(self, namespace=None, sources=None):
        self.namespace = namespace
        if sources is not None:
            self.sources = sources
        else:
            self.sources = [KubernetesClient(namespace=namespace)]
            try:
                self.sources.append(KubeArchiveClient(namespace=namespace))
            except RuntimeError:
                pass
            try:
                self.sources.append(TektonResultsClient(namespace=namespace))
            except Exception:
                pass

    def get_pipelinerun_complete(self, pr_name):
        """Get PipelineRun data from the first source that has it."""
        for source in self.sources:
            pr_data = source.get_pipelinerun(pr_name)
            if pr_data:
                source_name = type(source).__name__
                results = pr_data.get('status', {}).get('results', [])
                if results:
                    return pr_data, '{}_with_results'.format(source_name)
                return pr_data, source_name
        return None, 'none'

    def get_logs_complete(self, pr_name, max_size=200000):
        """Get logs from the first source that has them."""
        for source in self.sources:
            logs = source.get_pipelinerun_logs(pr_name)
            if logs:
                if len(logs) > max_size:
                    logs = logs[-max_size:]
                return logs, type(source).__name__
        logs = self._get_logs_via_api(pr_name)
        if logs:
            if len(logs) > max_size:
                logs = logs[-max_size:]
            return logs, 'k8s_pods'
        return None, 'none'

    def get_taskruns_details(self, pr_name):
        """Get TaskRun details from the first source that has them."""
        for source in self.sources:
            details = source.get_pipelinerun_taskruns_details(pr_name)
            if details:
                return details, type(source).__name__
        return [], 'none'

    def _get_logs_via_api(self, pr_name):
        """Last-resort: get logs by finding pods labeled with the PipelineRun."""
        try:
            _ensure_k8s_config()
            v1 = client.CoreV1Api()
            pod_list = v1.list_namespaced_pod(
                self.namespace,
                label_selector='tekton.dev/pipelineRun={}'.format(pr_name),
                _request_timeout=10,
            )
            pods = [p.metadata.name for p in pod_list.items if p.metadata]
            if not pods:
                return None

            all_logs = []
            for pod_name in pods[:10]:
                try:
                    logs = v1.read_namespaced_pod_log(
                        pod_name, self.namespace,
                        _request_timeout=30,
                    )
                    if logs:
                        all_logs.append("===== Pod: {} =====\n{}".format(pod_name, logs))
                except Exception:
                    continue

            return "\n\n".join(all_logs) if all_logs else None
        except Exception:
            return None
