"""Kubernetes client for fetching live pipeline data via the Python API."""

import re

from kubernetes import client

from clients.pipeline_source import PipelineRunSource
from openshift_auth import _ensure_k8s_config

_LABEL_VALUE_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$')


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

    def list_components(self, namespace=None, application=None):
        """List all Component CRs in the namespace, optionally filtered by application."""
        ns = namespace or self.namespace
        try:
            _ensure_k8s_config()
            api = client.CustomObjectsApi()
            result = api.list_namespaced_custom_object(
                group='appstudio.redhat.com', version='v1alpha1',
                namespace=ns, plural='components',
            )
            components = []
            for item in result.get('items', []):
                spec = item.get('spec', {})
                status = item.get('status', {})
                app = spec.get('application', '')
                if application and app != application:
                    continue
                components.append({
                    'name': item.get('metadata', {}).get('name', ''),
                    'application': app,
                    'container_image': spec.get('containerImage', ''),
                    'repository_url': spec.get('source', {}).get('git', {}).get('url', ''),
                    'branch': spec.get('source', {}).get('git', {}).get('revision', ''),
                    'last_built_commit': status.get('lastBuiltCommit', ''),
                    'nudges': spec.get('build-nudges-ref', []),
                })
            return components
        except Exception:
            return []

    def list_pac_repositories(self, namespace=None):
        """List PipelinesAsCode Repository CRs — one per webhook-enabled repo."""
        ns = namespace or self.namespace
        try:
            _ensure_k8s_config()
            api = client.CustomObjectsApi()
            result = api.list_namespaced_custom_object(
                group='pipelinesascode.tekton.dev', version='v1alpha1',
                namespace=ns, plural='repositories',
            )
            repos = []
            for item in result.get('items', []):
                spec = item.get('spec', {})
                repos.append({
                    'name': item.get('metadata', {}).get('name', ''),
                    'url': spec.get('url', ''),
                    'branch': spec.get('git_provider', {}).get('branch', ''),
                })
            return repos
        except Exception:
            return []

    def list_recent_pipelineruns(self, component_name, namespace=None, limit=5):
        """List recent PipelineRuns for a component, newest first."""
        if not _LABEL_VALUE_RE.match(component_name):
            raise ValueError("Invalid component name for label selector: {!r}".format(
                component_name))
        ns = namespace or self.namespace
        try:
            _ensure_k8s_config()
            api = client.CustomObjectsApi()
            result = api.list_namespaced_custom_object(
                group='tekton.dev', version='v1',
                namespace=ns, plural='pipelineruns',
                label_selector='appstudio.openshift.io/component={}'.format(
                    component_name),
            )
            runs = []
            for item in result.get('items', []):
                meta = item.get('metadata', {})
                labels = meta.get('labels', {})
                conditions = item.get('status', {}).get('conditions', [])
                status = 'unknown'
                for cond in conditions:
                    if cond.get('type') == 'Succeeded':
                        status = 'succeeded' if cond.get('status') == 'True' else 'failed'
                        if cond.get('reason') == 'Running':
                            status = 'running'
                runs.append({
                    'name': meta.get('name', ''),
                    'status': status,
                    'commit_sha': labels.get(
                        'pipelinesascode.tekton.dev/sha', ''),
                    'event_type': labels.get(
                        'pipelinesascode.tekton.dev/event-type', ''),
                    'created_at': meta.get('creationTimestamp', ''),
                })
            runs.sort(key=lambda r: r['created_at'], reverse=True)
            return runs[:limit]
        except Exception:
            return []
