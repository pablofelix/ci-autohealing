"""Kubernetes CLI client for fetching live pipeline data via oc/kubectl."""

import subprocess
import json

from clients.pipeline_source import PipelineRunSource


class KubernetesClient(PipelineRunSource):
    """Adapter for the live Kubernetes cluster via the oc CLI.

    Fetches current PipelineRuns, TaskRuns, and pod logs directly from
    the cluster. Best for recent data not yet archived in KubeArchive.
    """

    def __init__(self, namespace='NAMESPACE_PLACEHOLDER'):
        # type: (str,) -> None
        self.namespace = namespace

    def get_pipelinerun(self, name, namespace=None):
        # type: (str, Optional[str]) -> Optional[Dict[str, Any]]
        ns = namespace or self.namespace
        try:
            result = subprocess.run(
                ['oc', 'get', 'pipelinerun', name, '-n', ns, '-o', 'json'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, check=True, timeout=10
            )
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def get_taskrun(self, name, namespace=None):
        # type: (str, Optional[str]) -> Optional[Dict[str, Any]]
        ns = namespace or self.namespace
        try:
            result = subprocess.run(
                ['oc', 'get', 'taskrun', name, '-n', ns, '-o', 'json'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, check=True, timeout=10
            )
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def get_pod_logs(self, pod_name, container=None, namespace=None, tail_lines=5000):
        # type: (str, Optional[str], Optional[str], Optional[int]) -> Optional[str]
        ns = namespace or self.namespace
        try:
            cmd = ['oc', 'logs', pod_name, '-n', ns]
            if container:
                cmd.extend(['-c', container])
            else:
                cmd.append('--all-containers')
            if tail_lines:
                cmd.extend(['--tail', str(tail_lines)])
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=30
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
            return None
        except subprocess.TimeoutExpired:
            return None

    def get_component_metadata(self, component_name, namespace=None):
        # type: (str, Optional[str]) -> Optional[Dict[str, str]]
        """Fetch component repository URL and branch from cluster."""
        ns = namespace or self.namespace
        try:
            from kubernetes import client, config
            config.load_kube_config()
            api = client.CustomObjectsApi()
            data = api.get_namespaced_custom_object(
                group='appstudio.redhat.com', version='v1alpha1',
                namespace=ns, plural='components', name=component_name,
            )
            return {
                'repository_url': data.get('spec', {}).get('source', {}).get('git', {}).get('url', ''),
                'branch': data.get('spec', {}).get('source', {}).get('git', {}).get('revision', ''),
            }
        except Exception:
            return None
