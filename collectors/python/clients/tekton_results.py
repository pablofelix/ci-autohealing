"""Tekton Results API client for fetching logs and results.

Tekton Results provides an API to query stored PipelineRun and TaskRun logs.
This is more reliable than querying pods directly since Results are persisted.
"""

import subprocess
import json
import requests
from typing import Optional, List, Dict, Any

from openshift_auth import get_openshift_token, discover_openshift_api_url, create_authenticated_session


class TektonResultsClient:
    """Client for the Tekton Results API.

    Tekton Results stores execution records and logs for PipelineRuns/TaskRuns.
    API path: /apis/results.tekton.dev/v1alpha2/namespaces/{ns}/results/{id}/records/{record_id}
    """

    def __init__(self, namespace='NAMESPACE_PLACEHOLDER', api_url=None):
        # type: (str, Optional[str]) -> None
        self.namespace = namespace
        self.api_url = api_url or discover_openshift_api_url()
        self.token = get_openshift_token()
        if not self.token:
            raise RuntimeError("Failed to get OpenShift token")
        self.session = create_authenticated_session(self.token)
        self.session.verify = False

    def get_pipelinerun_result_id(self, pipelinerun_name):
        # type: (str,) -> Optional[str]
        """Get Tekton Results ID from PipelineRun annotations."""
        try:
            result = subprocess.run(
                ['oc', 'get', 'pipelinerun', pipelinerun_name,
                 '-n', self.namespace, '-o', 'json'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, check=True, timeout=10
            )
            data = json.loads(result.stdout)
            result_path = data.get('metadata', {}).get('annotations', {}).get('results.tekton.dev/result')
            if result_path:
                parts = result_path.split('/')
                if len(parts) >= 3:
                    return parts[2]
            return None
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def get_result(self, result_id, namespace=None):
        # type: (str, Optional[str]) -> Optional[Dict[str, Any]]
        ns = namespace or self.namespace
        url = "{}/apis/results.tekton.dev/v1alpha2/parents/{}/results/{}/results/{}".format(
            self.api_url, ns, ns, result_id
        )
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except requests.RequestException:
            return None

    def get_record(self, result_id, record_id, namespace=None):
        # type: (str, str, Optional[str]) -> Optional[Dict[str, Any]]
        ns = namespace or self.namespace
        url = "{}/apis/results.tekton.dev/v1alpha2/parents/{}/results/{}/records/{}".format(
            self.api_url, ns, result_id, record_id
        )
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except requests.RequestException:
            return None

    def list_records(self, result_id, namespace=None):
        # type: (str, Optional[str]) -> List[Dict[str, Any]]
        ns = namespace or self.namespace
        url = "{}/apis/results.tekton.dev/v1alpha2/parents/{}/results/{}/records".format(
            self.api_url, ns, result_id
        )
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return response.json().get('items', [])
            return []
        except requests.RequestException:
            return []

    def get_pipelinerun_logs_from_results(self, pipelinerun_name, namespace=None):
        # type: (str, Optional[str]) -> Optional[str]
        """Get PipelineRun logs from Tekton Results API."""
        result_id = self.get_pipelinerun_result_id(pipelinerun_name)
        if not result_id:
            return None

        ns = namespace or self.namespace
        records = self.list_records(result_id, ns)
        if not records:
            return None

        all_logs = []
        for record in records:
            record_data = record.get('data', {})
            taskrun_name = record_data.get('metadata', {}).get('name', 'unknown')
            logs = record_data.get('status', {}).get('logs')
            if logs:
                all_logs.append("===== TaskRun: {} (from Results API) =====\n{}".format(taskrun_name, logs))

        return "\n\n".join(all_logs) if all_logs else None

    def get_pipelinerun_with_results(self, pipelinerun_name, namespace=None):
        # type: (str, Optional[str]) -> Optional[Dict[str, Any]]
        """Get PipelineRun data including results from Tekton Results API."""
        result_id = self.get_pipelinerun_result_id(pipelinerun_name)
        if not result_id:
            return None

        ns = namespace or self.namespace
        result = self.get_result(result_id, ns)
        if not result:
            return None

        return result.get('spec', {}).get('resource')
