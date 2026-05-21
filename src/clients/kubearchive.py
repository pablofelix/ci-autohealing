"""KubeArchive API client for querying archived Tekton resources."""

import requests

from clients.pipeline_source import PipelineRunSource
from openshift_auth import get_openshift_token, discover_kubearchive_api_url, create_authenticated_session


class KubeArchiveClient(PipelineRunSource):
    """Adapter for the KubeArchive HTTP API.

    KubeArchive stores archived Kubernetes resources including PipelineRuns,
    TaskRuns, and pod logs. Its API follows standard Kubernetes REST structure.
    """

    def __init__(self, api_url=None, namespace=None):
        # type: (Optional[str], str) -> None
        self.namespace = namespace
        self.api_url = api_url or discover_kubearchive_api_url()
        self.token = get_openshift_token()
        if not self.token:
            raise RuntimeError("Failed to get OpenShift token")
        self.session = create_authenticated_session(self.token)

    def step_container_name(self, step_name):
        # type: (str) -> str
        """KubeArchive stores logs under 'step-<name>' containers."""
        return "step-{}".format(step_name)

    def get_pipelinerun(self, name, namespace=None):
        # type: (str, Optional[str]) -> Optional[Dict[str, Any]]
        ns = namespace or self.namespace
        url = "{}/apis/tekton.dev/v1/namespaces/{}/pipelineruns/{}".format(self.api_url, ns, name)
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return None

    def get_taskrun(self, name, namespace=None):
        # type: (str, Optional[str]) -> Optional[Dict[str, Any]]
        ns = namespace or self.namespace
        url = "{}/apis/tekton.dev/v1/namespaces/{}/taskruns/{}".format(self.api_url, ns, name)
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return None

    def get_pod_logs(self, pod_name, container=None, namespace=None, tail_lines=None):
        # type: (str, Optional[str], Optional[str], Optional[int]) -> Optional[str]
        ns = namespace or self.namespace
        url = "{}/api/v1/namespaces/{}/pods/{}/log".format(self.api_url, ns, pod_name)
        params = {}
        if container:
            params['container'] = container
        if tail_lines:
            params['tailLines'] = tail_lines
        try:
            response = self.session.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.text
        except requests.RequestException:
            return None

    # Legacy wrapper methods for backward compatibility
    def extract_taskruns(self, pipelinerun_data):
        # type: (Dict[str, Any]) -> List[str]
        from tekton_parsers import extract_taskrun_names
        return extract_taskrun_names(pipelinerun_data)

    def extract_failed_steps(self, taskrun_data):
        # type: (Dict[str, Any]) -> List[str]
        from tekton_parsers import extract_failed_step_names
        return extract_failed_step_names(taskrun_data)

    def get_taskrun_details(self, taskrun_data):
        # type: (Dict[str, Any]) -> Dict[str, Any]
        from tekton_parsers import build_taskrun_detail
        return build_taskrun_detail(taskrun_data)
