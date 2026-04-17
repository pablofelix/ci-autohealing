"""Tekton Results API client for fetching logs and results.

Tekton Results provides an API to query stored PipelineRun and TaskRun logs.
This is more reliable than querying pods directly since Results are persisted.
"""

import subprocess
import json
import requests
from typing import Optional, List, Dict, Any
from functools import lru_cache


class TektonResultsClient:
    """Client for interacting with Tekton Results API.

    Tekton Results stores execution records and logs for PipelineRuns and TaskRuns.
    The API follows the pattern: /apis/results.tekton.dev/v1alpha2/namespaces/{ns}/results/{id}/records/{record_id}
    """

    def __init__(self, namespace: str = 'NAMESPACE_PLACEHOLDER', api_url: Optional[str] = None):
        """Initialize Tekton Results client.

        Args:
            namespace: Kubernetes namespace.
            api_url: Tekton Results API base URL. If None, auto-discovered.
        """
        self.namespace = namespace
        self.api_url = api_url or self._discover_api_url()
        self.token = self._get_auth_token()
        self.session = self._create_session()

    @staticmethod
    def _discover_api_url() -> str:
        """Discover Tekton Results API URL.

        Returns:
            API base URL (typically the Kubernetes API server).
        """
        try:
            # Get current server from kubeconfig
            result = subprocess.run(
                ['oc', 'whoami', '--show-server'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            # Fallback to default
            return "https://api.CLUSTER_DOMAIN:6443"

    @staticmethod
    def _get_auth_token() -> str:
        """Get authentication token.

        Returns:
            Bearer token for API authentication.
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
            raise RuntimeError(f"Failed to get token: {e}")

    def _create_session(self) -> requests.Session:
        """Create requests session with authentication.

        Returns:
            Configured requests.Session.
        """
        session = requests.Session()
        session.headers.update({
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json'
        })
        # Disable SSL verification for OpenShift internal certs
        session.verify = False
        return session

    def get_pipelinerun_result_id(self, pipelinerun_name: str) -> Optional[str]:
        """Get Tekton Results ID from PipelineRun annotations.

        Args:
            pipelinerun_name: PipelineRun name.

        Returns:
            Results ID or None.
        """
        try:
            result = subprocess.run(
                ['oc', 'get', 'pipelinerun', pipelinerun_name,
                 '-n', self.namespace, '-o', 'json'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True,
                timeout=10
            )
            data = json.loads(result.stdout)
            annotations = data.get('metadata', {}).get('annotations', {})

            # results.tekton.dev/result contains: namespace/results/uid
            result_path = annotations.get('results.tekton.dev/result')
            if result_path:
                # Extract uid from: NAMESPACE_PLACEHOLDER/results/1059601c-6881-45db-a9b6-5d66b5b48451
                parts = result_path.split('/')
                if len(parts) >= 3:
                    return parts[2]  # The UID

            return None
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def get_result(self, result_id: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get Result from Tekton Results API.

        Args:
            result_id: Result ID (typically PipelineRun UID).
            namespace: Kubernetes namespace.

        Returns:
            Result JSON or None.
        """
        ns = namespace or self.namespace

        # Tekton Results API path
        url = f"{self.api_url}/apis/results.tekton.dev/v1alpha2/parents/{ns}/results/{ns}/results/{result_id}"

        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except requests.RequestException:
            return None

    def get_record(self, result_id: str, record_id: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get Record from Tekton Results API.

        Args:
            result_id: Result ID.
            record_id: Record ID.
            namespace: Kubernetes namespace.

        Returns:
            Record JSON or None.
        """
        ns = namespace or self.namespace

        url = f"{self.api_url}/apis/results.tekton.dev/v1alpha2/parents/{ns}/results/{result_id}/records/{record_id}"

        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except requests.RequestException:
            return None

    def list_records(self, result_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all Records for a Result.

        Args:
            result_id: Result ID.
            namespace: Kubernetes namespace.

        Returns:
            List of Record JSON objects.
        """
        ns = namespace or self.namespace

        url = f"{self.api_url}/apis/results.tekton.dev/v1alpha2/parents/{ns}/results/{result_id}/records"

        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return data.get('items', [])
            return []
        except requests.RequestException:
            return []

    def get_pipelinerun_logs_from_results(
        self,
        pipelinerun_name: str,
        namespace: Optional[str] = None
    ) -> Optional[str]:
        """Get PipelineRun logs from Tekton Results API.

        This is more reliable than querying pods since Results are persisted.

        Args:
            pipelinerun_name: PipelineRun name.
            namespace: Kubernetes namespace.

        Returns:
            Combined logs or None.
        """
        ns = namespace or self.namespace

        # Get Result ID from PipelineRun annotations
        result_id = self.get_pipelinerun_result_id(pipelinerun_name)
        if not result_id:
            return None

        # Get all records (TaskRuns)
        records = self.list_records(result_id, ns)
        if not records:
            return None

        # Collect logs from each record
        all_logs = []
        for record in records:
            record_data = record.get('data', {})

            # Extract TaskRun name
            taskrun_name = record_data.get('metadata', {}).get('name', 'unknown')

            # Get logs if available in record
            # Note: Logs might be in different fields depending on Tekton Results version
            logs = record_data.get('status', {}).get('logs')
            if logs:
                all_logs.append(f"===== TaskRun: {taskrun_name} (from Results API) =====\n{logs}")

        return "\n\n".join(all_logs) if all_logs else None

    def get_pipelinerun_with_results(
        self,
        pipelinerun_name: str,
        namespace: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get PipelineRun data including results from Tekton Results API.

        Args:
            pipelinerun_name: PipelineRun name.
            namespace: Kubernetes namespace.

        Returns:
            PipelineRun data with results or None.
        """
        ns = namespace or self.namespace

        # Get Result ID
        result_id = self.get_pipelinerun_result_id(pipelinerun_name)
        if not result_id:
            return None

        # Get Result
        result = self.get_result(result_id, ns)
        if not result:
            return None

        # The Result contains the PipelineRun data
        return result.get('spec', {}).get('resource')
