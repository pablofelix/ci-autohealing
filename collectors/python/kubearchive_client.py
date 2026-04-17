"""KubeArchive API client for querying archived Tekton resources."""

import subprocess
from typing import Optional, List, Dict, Any
from functools import lru_cache
import requests

from models import PipelineRun, TaskRun, BuildStatus


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
        self.api_url = api_url or self._discover_api_url()
        self.token = self._get_auth_token()
        self.session = self._create_session()

    @staticmethod
    def _discover_api_url() -> str:
        """Discover KubeArchive API URL from cluster ConfigMap.

        Returns:
            KubeArchive API base URL.

        Raises:
            RuntimeError: If URL cannot be discovered.
        """
        try:
            result = subprocess.run(
                ['oc', 'get', 'cm', '-n', 'product-kubearchive', 'kubearchive-api-url',
                 '-o', 'jsonpath={.data.URL}'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            # Fallback to known URL
            return "https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN"

    @staticmethod
    def _get_auth_token() -> str:
        """Get OpenShift authentication token.

        Returns:
            Bearer token for API authentication.

        Raises:
            RuntimeError: If token cannot be retrieved.
        """
        try:
            result = subprocess.run(
                ['oc', 'whoami', '-t'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to get OpenShift token: {e}")

    def _create_session(self) -> requests.Session:
        """Create requests session with authentication.

        Returns:
            Configured requests.Session with auth headers.
        """
        session = requests.Session()
        session.headers.update({
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json'
        })
        return session

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

    def extract_taskruns(self, pipelinerun_data: Dict[str, Any]) -> List[str]:
        """Extract TaskRun names from PipelineRun childReferences.

        Args:
            pipelinerun_data: PipelineRun JSON data.

        Returns:
            List of TaskRun names.
        """
        child_refs = pipelinerun_data.get('status', {}).get('childReferences', [])
        return [
            ref['name']
            for ref in child_refs
            if ref.get('kind') == 'TaskRun'
        ]

    def extract_failed_steps(self, taskrun_data: Dict[str, Any]) -> List[str]:
        """Extract names of failed steps from TaskRun.

        Args:
            taskrun_data: TaskRun JSON data.

        Returns:
            List of failed step names.
        """
        steps = taskrun_data.get('status', {}).get('steps', [])
        return [
            step['name']
            for step in steps
            if step.get('terminated', {}).get('exitCode', 0) != 0
        ]

    def get_taskrun_details(self, taskrun_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract detailed information from TaskRun.

        Args:
            taskrun_data: TaskRun JSON data.

        Returns:
            Dict with extracted details.
        """
        status = taskrun_data.get('status', {})
        spec = taskrun_data.get('spec', {})
        metadata = taskrun_data.get('metadata', {})

        steps_info = []
        for step in status.get('steps', []):
            terminated = step.get('terminated', {})
            steps_info.append({
                'name': step.get('name'),
                'container': step.get('container'),
                'exit_code': terminated.get('exitCode'),
                'reason': terminated.get('reason'),
                'started': terminated.get('startedAt'),
                'finished': terminated.get('finishedAt')
            })

        return {
            'name': metadata.get('name'),
            'pod_name': status.get('podName'),
            'task_name': spec.get('taskRef', {}).get('name'),
            'pipeline_task': metadata.get('labels', {}).get('tekton.dev/pipelineTask'),
            'start_time': status.get('startTime'),
            'completion_time': status.get('completionTime'),
            'steps': steps_info,
            'failed_steps': self.extract_failed_steps(taskrun_data)
        }

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
                logs = self.get_pod_logs(pod_name, container=step, namespace=ns)
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
