"""Tekton Results API client for querying archived PipelineRuns, TaskRuns, and logs.

Tekton Results persists execution records and logs beyond the cluster's pruning
policy. Records are queried via a filter expression language; logs are stored in
cloud storage (S3 via Vector) and retrieved on demand.

API base: https://tekton-results-tekton-results.apps.{domain}/apis/results.tekton.dev/v1alpha2
Records:  /parents/{ns}/results/-/records?filter=...&order_by=create_time desc
Logs:     /parents/{ns}/results/{result_id}/logs/{record_id}
"""

import base64
import json
import logging
from urllib.parse import urlparse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from openshift_auth import get_openshift_token, discover_openshift_api_url, create_authenticated_session

logger = logging.getLogger(__name__)


def _derive_tekton_results_url(openshift_api_url):
    # type: (str) -> str
    hostname = urlparse(openshift_api_url).hostname or ''
    if hostname.startswith('api.'):
        base_domain = hostname[4:]
        return (
            "https://tekton-results-tekton-results.apps.{}"
            "/apis/results.tekton.dev/v1alpha2"
        ).format(base_domain)
    raise ValueError("Cannot derive Tekton Results URL from: {}".format(openshift_api_url))


class TektonResultsClient:
    """Client for the Tekton Results API.

    Provides three capabilities that complement KubeArchive:
      1. query_pipelinerun_records() - discover PipelineRuns by label filters
      2. query_taskrun_records()     - find TaskRuns for a specific PipelineRun
      3. get_taskrun_logs()          - fetch logs for a specific TaskRun record
    """

    def __init__(self, namespace='NAMESPACE_PLACEHOLDER', api_url=None):
        # type: (str, Optional[str]) -> None
        self.namespace = namespace
        if api_url:
            self.api_url = api_url
        else:
            openshift_url = discover_openshift_api_url()
            self.api_url = _derive_tekton_results_url(openshift_url)
        self.token = get_openshift_token()
        if not self.token:
            raise RuntimeError("Failed to get OpenShift token")
        self.session = create_authenticated_session(self.token)
        self.session.verify = False

    def _query_records(self, filter_expr, page_size=20, order_by='create_time desc'):
        # type: (str, int, str) -> List[Dict[str, Any]]
        url = "{}/parents/{}/results/-/records".format(self.api_url, self.namespace)
        page_size = max(5, min(page_size, 10000))
        params = {
            'filter': filter_expr,
            'page_size': page_size,
            'order_by': order_by,
        }
        try:
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json().get('records', [])
            logger.warning("Tekton Results query returned %d", resp.status_code)
            return []
        except requests.RequestException as e:
            logger.warning("Tekton Results query failed: %s", e)
            return []

    @staticmethod
    def _decode_record(record):
        # type: (Dict[str, Any]) -> Optional[Dict[str, Any]]
        data_value = record.get('data', {}).get('value')
        if not data_value:
            return None
        try:
            return json.loads(base64.b64decode(data_value).decode('utf-8'))
        except Exception:
            return None

    def query_pipelinerun_records(self, application, component=None, page_size=20):
        # type: (str, Optional[str], int) -> List[Dict[str, Any]]
        """Query PipelineRun records filtered by application and optionally component.

        Returns decoded PipelineRun dicts (same shape as Kubernetes API responses).
        """
        parts = [
            "(data_type == 'tekton.dev/v1beta1.PipelineRun' || data_type == 'tekton.dev/v1.PipelineRun')",
            "data.metadata.labels['pipelines.appstudio.openshift.io/type']=='build'",
            "data.metadata.labels['appstudio.openshift.io/application']=='{}'".format(application),
        ]
        if component:
            parts.append(
                "data.metadata.labels['appstudio.openshift.io/component']=='{}'".format(component)
            )
        filter_expr = " && ".join(parts)

        records = self._query_records(filter_expr, page_size=page_size)
        results = []
        for r in records:
            decoded = self._decode_record(r)
            if decoded:
                results.append(decoded)
        return results

    def query_taskrun_records(self, pipelinerun_name):
        # type: (str) -> List[Tuple[Dict[str, Any], str]]
        """Query TaskRun records for a given PipelineRun.

        Returns list of (decoded_taskrun_dict, record_name) tuples.
        The record_name is needed for fetching logs.
        """
        filter_expr = (
            "data_type == 'tekton.dev/v1.TaskRun' && "
            "data.metadata.labels['tekton.dev/pipelineRun']=='{}'".format(pipelinerun_name)
        )
        records = self._query_records(filter_expr, page_size=50)
        results = []
        for r in records:
            decoded = self._decode_record(r)
            if decoded:
                results.append((decoded, r.get('name', '')))
        return results

    def get_taskrun_logs(self, record_name):
        # type: (str) -> Optional[str]
        """Fetch logs for a TaskRun using its record_name.

        record_name format: {namespace}/results/{result_id}/records/{record_id}
        Logs endpoint:      /parents/{namespace}/results/{result_id}/logs/{record_id}
        """
        parts = record_name.split('/')
        if len(parts) < 5:
            return None

        result_id = parts[2]
        record_id = parts[4]

        url = "{}/parents/{}/results/{}/logs/{}".format(
            self.api_url, self.namespace, result_id, record_id
        )
        try:
            resp = self.session.get(url, timeout=60)
            if resp.status_code == 200:
                return resp.text
            logger.debug("Logs request returned %d for %s", resp.status_code, record_name)
            return None
        except requests.RequestException as e:
            logger.debug("Failed to fetch logs for %s: %s", record_name, e)
            return None

    def find_failed_taskrun(self, pipelinerun_name):
        # type: (str) -> Tuple[Optional[str], Optional[str], Optional[str]]
        """Find the failed TaskRun in a PipelineRun and fetch its logs.

        When no logs are available (e.g. PodCreationFailed, ImagePullFailed),
        falls back to the TaskRun condition message as the log content.

        Returns:
            (task_name, logs, record_name) or (None, None, None)
        """
        taskruns = self.query_taskrun_records(pipelinerun_name)

        for tr_data, record_name in taskruns:
            conditions = tr_data.get('status', {}).get('conditions', [])
            if not conditions:
                continue
            if conditions[-1].get('status') != 'False':
                continue

            task_name = tr_data.get('metadata', {}).get('labels', {}).get(
                'tekton.dev/pipelineTask', ''
            )
            logs = self.get_taskrun_logs(record_name)
            if not logs:
                condition_msg = conditions[-1].get('message', '')
                condition_reason = conditions[-1].get('reason', '')
                if condition_msg:
                    logs = "{}: {}".format(condition_reason, condition_msg)
            logger.info("Found failed TaskRun via Tekton Results: %s (task: %s)", record_name, task_name)
            return task_name, logs, record_name

        return None, None, None

    def query_component_build_history(self, application, component, page_size=10):
        # type: (str, str, int) -> List[Dict[str, Any]]
        """Query build history for a component, returning decoded PipelineRuns
        ordered by creation time (newest first).

        Each result includes full PipelineRun data: status, conditions, results, labels.
        Filters to push/incoming event types only.
        """
        parts = [
            "(data_type == 'tekton.dev/v1beta1.PipelineRun' || data_type == 'tekton.dev/v1.PipelineRun')",
            "data.metadata.labels['pipelines.appstudio.openshift.io/type']=='build'",
            "data.metadata.labels['appstudio.openshift.io/application']=='{}'".format(application),
            "data.metadata.labels['appstudio.openshift.io/component']=='{}'".format(component),
        ]
        filter_expr = " && ".join(parts)

        records = self._query_records(filter_expr, page_size=page_size)
        results = []
        for r in records:
            decoded = self._decode_record(r)
            if decoded:
                event_type = decoded.get('metadata', {}).get('labels', {}).get(
                    'pipelinesascode.tekton.dev/event-type', '')
                if event_type in ('push', 'incoming'):
                    results.append(decoded)
        return results

    def query_conforma_records(self, application, component=None, page_size=20):
        # type: (str, Optional[str], int) -> List[Dict[str, Any]]
        """Query Conforma (Enterprise Contract) test PipelineRun records."""
        parts = [
            "(data_type == 'tekton.dev/v1beta1.PipelineRun' || data_type == 'tekton.dev/v1.PipelineRun')",
            "data.metadata.labels['pipelines.appstudio.openshift.io/type']=='test'",
            "data.metadata.labels['appstudio.openshift.io/application']=='{}'".format(application),
        ]
        if component:
            parts.append(
                "data.metadata.labels['appstudio.openshift.io/component']=='{}'".format(component)
            )
        filter_expr = " && ".join(parts)

        records = self._query_records(filter_expr, page_size=page_size)
        results = []
        for r in records:
            decoded = self._decode_record(r)
            if decoded:
                results.append(decoded)
        return results

    def get_pipelinerun_logs(self, pipelinerun_name, max_log_size=200000, failed_only=False):
        # type: (str, int, bool) -> Optional[str]
        """Fetch combined logs for TaskRuns in a PipelineRun from Tekton Results.

        When failed_only=True, only downloads logs from failed TaskRuns —
        dramatically faster for on-demand fetching (1-2 TaskRuns vs 30+).
        """
        taskruns = self.query_taskrun_records(pipelinerun_name)
        if not taskruns:
            return None

        all_logs = []
        total_size = 0

        for tr_data, record_name in taskruns:
            if failed_only:
                conditions = tr_data.get('status', {}).get('conditions', [])
                if not conditions or conditions[-1].get('status') != 'False':
                    continue

            task_name = tr_data.get('metadata', {}).get('labels', {}).get(
                'tekton.dev/pipelineTask', 'unknown'
            )
            logs = self.get_taskrun_logs(record_name)
            if not logs and failed_only:
                condition_msg = tr_data.get('status', {}).get('conditions', [{}])[-1].get('message', '')
                condition_reason = tr_data.get('status', {}).get('conditions', [{}])[-1].get('reason', '')
                if condition_msg:
                    logs = "{}: {}".format(condition_reason, condition_msg)
            if logs:
                section = "===== TaskRun: {} / Task: {} =====\n{}".format(
                    tr_data.get('metadata', {}).get('name', ''), task_name, logs
                )
                total_size += len(section)
                if total_size > max_log_size:
                    break
                all_logs.append(section)

        return "\n\n".join(all_logs) if all_logs else None
