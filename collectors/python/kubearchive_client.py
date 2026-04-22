"""KubeArchive API client for querying archived Tekton resources."""

import subprocess
from typing import Optional, List, Dict, Any
from functools import lru_cache
import requests

from models import PipelineRun, TaskRun, BuildStatus
from tekton_parsers import extract_taskrun_names, extract_failed_step_names, build_taskrun_detail
from openshift_auth import get_openshift_token, discover_kubearchive_api_url, create_authenticated_session


class KubeArchiveClient:
    """Client for interacting with KubeArchive API.

    KubeArchive stores archived Kubernetes resources including:
    - PipelineRuns (Tekton CI/CD pipelines)
    - TaskRuns (Individual tasks within pipelines)
    - Pod logs

    API follows standard Kubernetes REST API structure:
    - Tekton resources: /apis/tekton.dev/v1/namespaces/{ns}/{resource}/{name}
    - Pod logs: /api/v1/namespaces/{ns}/pods/{pod}/log
    """

    def __init__(self, api_url: Optional[str] = None, namespace: str = 'NAMESPACE_PLACEHOLDER'):
        """Initialize KubeArchive client.

        Args:
            api_url: KubeArchive API base URL. If None, auto-discovered from cluster.
            namespace: Default Kubernetes namespace.
        """
        self.namespace = namespace
        self.api_url = api_url or discover_kubearchive_api_url()
        self.token = get_openshift_token()
        if not self.token:
            raise RuntimeError("Failed to get OpenShift token")
        self.session = create_authenticated_session(self.token)

    def get_pipelinerun(self, name: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch PipelineRun details from KubeArchive.

        Args:
            name: PipelineRun name.
            namespace: Kubernetes namespace (uses default if not specified).

        Returns:
            PipelineRun JSON data or None if not found.
        """
        ns = namespace or self.namespace
        url = f"{self.api_url}/apis/tekton.dev/v1/namespaces/{ns}/pipelineruns/{name}"

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return None

    def get_taskrun(self, name: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch TaskRun details from KubeArchive.

        Args:
            name: TaskRun name.
            namespace: Kubernetes namespace (uses default if not specified).

        Returns:
            TaskRun JSON data or None if not found.
        """
        ns = namespace or self.namespace
        url = f"{self.api_url}/apis/tekton.dev/v1/namespaces/{ns}/taskruns/{name}"

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return None

    def get_pod_logs(
        self,
        pod_name: str,
        container: Optional[str] = None,
        namespace: Optional[str] = None,
        tail_lines: Optional[int] = None
    ) -> Optional[str]:
        """Fetch pod logs from KubeArchive.

        Args:
            pod_name: Pod name.
            container: Container name within pod. If None, gets all containers.
            namespace: Kubernetes namespace (uses default if not specified).
            tail_lines: Number of lines to tail (KubeArchive supports this feature).

        Returns:
            Pod logs as string or None if not found.
        """
        ns = namespace or self.namespace
        url = f"{self.api_url}/api/v1/namespaces/{ns}/pods/{pod_name}/log"

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

    def extract_taskruns(self, pipelinerun_data):
        # type: (Dict[str, Any]) -> List[str]
        """Extract TaskRun names from PipelineRun childReferences."""
        return extract_taskrun_names(pipelinerun_data)

    def extract_failed_steps(self, taskrun_data):
        # type: (Dict[str, Any]) -> List[str]
        """Extract names of failed steps from TaskRun."""
        return extract_failed_step_names(taskrun_data)

    def get_taskrun_details(self, taskrun_data):
        # type: (Dict[str, Any]) -> Dict[str, Any]
        """Extract detailed information from TaskRun."""
        return build_taskrun_detail(taskrun_data)

    def get_pipelinerun_logs(
        self,
        pipelinerun_name: str,
        namespace: Optional[str] = None,
        max_log_size: int = 100000
    ) -> Optional[str]:
        """Fetch complete logs for a PipelineRun.

        This orchestrates fetching the PipelineRun, finding TaskRuns,
        and collecting logs from all failed steps.

        Args:
            pipelinerun_name: PipelineRun name.
            namespace: Kubernetes namespace.
            max_log_size: Maximum log size in characters.

        Returns:
            Combined logs from all TaskRuns or None if not available.
        """
        ns = namespace or self.namespace

        # Get PipelineRun details
        pr_data = self.get_pipelinerun(pipelinerun_name, ns)
        if not pr_data:
            return None

        # Extract TaskRuns
        taskrun_names = self.extract_taskruns(pr_data)
        if not taskrun_names:
            return None

        # Collect logs from each TaskRun
        all_logs = []
        for tr_name in taskrun_names:
            tr_data = self.get_taskrun(tr_name, ns)
            if not tr_data:
                continue

            pod_name = tr_data.get('status', {}).get('podName')
            if not pod_name:
                continue

            # Get failed steps (or all steps if none explicitly failed)
            failed_steps = self.extract_failed_steps(tr_data)
            steps_to_fetch = failed_steps if failed_steps else [
                step['name'] for step in tr_data.get('status', {}).get('steps', [])
            ]

            # Fetch logs for each step
            for step in steps_to_fetch[:3]:  # Limit to first 3 steps per TaskRun
                # Tekton prefixes step names with "step-" when creating containers
                container_name = f"step-{step}"
                logs = self.get_pod_logs(pod_name, container=container_name, namespace=ns)
                if logs:
                    all_logs.append(f"===== TaskRun: {tr_name} / Step: {step} =====\n{logs}")

        # Combine and truncate logs
        combined = "\n\n".join(all_logs)
        return combined[:max_log_size] if combined else None

    def get_pipelinerun_taskruns_details(
        self,
        pipelinerun_name: str,
        namespace: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get detailed information about all TaskRuns in a PipelineRun.

        Args:
            pipelinerun_name: PipelineRun name.
            namespace: Kubernetes namespace.

        Returns:
            List of TaskRun details dictionaries.
        """
        ns = namespace or self.namespace

        # Get PipelineRun
        pr_data = self.get_pipelinerun(pipelinerun_name, ns)
        if not pr_data:
            return []

        # Extract TaskRuns
        taskrun_names = self.extract_taskruns(pr_data)
        if not taskrun_names:
            return []

        # Get details for each TaskRun
        taskrun_details = []
        for tr_name in taskrun_names:
            tr_data = self.get_taskrun(tr_name, ns)
            if tr_data:
                details = self.get_taskrun_details(tr_data)
                taskrun_details.append(details)

        return taskrun_details
