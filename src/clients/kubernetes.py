"""Kubernetes client for fetching live pipeline data via the Python API."""

from kubernetes import client

from clients.pipeline_source import PipelineRunSource
from openshift_auth import _ensure_k8s_config


class KubernetesClient(PipelineRunSource):
    """Adapter for the live Kubernetes cluster via the Python kubernetes client.

    Fetches current PipelineRuns, TaskRuns, and pod logs directly from
    the cluster. Best for recent data not yet archived in KubeArchive.
    """

    def __init__(self, namespace=None):
        self.namespace = namespace

    def get_pipelinerun(self, name, namespace=None):
        ns = namespace or self.namespace
        try:
            _ensure_k8s_config()
            api = client.CustomObjectsApi()
            return api.get_namespaced_custom_object(
                group='tekton.dev', version='v1', namespace=ns,
                plural='pipelineruns', name=name,
                _request_timeout=10,
            )
        except Exception:
            return None

    def get_taskrun(self, name, namespace=None):
        ns = namespace or self.namespace
        try:
            _ensure_k8s_config()
            api = client.CustomObjectsApi()
            return api.get_namespaced_custom_object(
                group='tekton.dev', version='v1', namespace=ns,
                plural='taskruns', name=name,
                _request_timeout=10,
            )
        except Exception:
            return None

    def get_pod_logs(self, pod_name, container=None, namespace=None, tail_lines=5000):
        ns = namespace or self.namespace
        try:
            _ensure_k8s_config()
            v1 = client.CoreV1Api()
            kwargs = {'_request_timeout': 30}
            if container:
                kwargs['container'] = container
            if tail_lines:
                kwargs['tail_lines'] = tail_lines
            logs = v1.read_namespaced_pod_log(pod_name, ns, **kwargs)
            return logs if logs else None
        except Exception:
            return None

    def get_component_metadata(self, component_name, namespace=None):
        """Fetch component repository URL, branch, and promotion status from cluster."""
        ns = namespace or self.namespace
        try:
            _ensure_k8s_config()
            api = client.CustomObjectsApi()
            data = api.get_namespaced_custom_object(
                group='appstudio.redhat.com', version='v1alpha1',
                namespace=ns, plural='components', name=component_name,
            )
            spec = data.get('spec', {})
            status = data.get('status', {})
            return {
                'repository_url': spec.get('source', {}).get('git', {}).get('url', ''),
                'branch': spec.get('source', {}).get('git', {}).get('revision', ''),
                'last_promoted_image': status.get('lastPromotedImage', ''),
                'last_built_commit': status.get('lastBuiltCommit', ''),
                'container_image': spec.get('containerImage', ''),
                'nudges': spec.get('build-nudges-ref', []),
            }
        except Exception:
            return None
