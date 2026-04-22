"""Kubernetes API client for fetching PipelineRuns and logs directly.

Fallback client when KubeArchive doesn't have the data.
Uses kubernetes-client Python library.
"""

import subprocess
import json
from typing import Optional, List, Dict, Any
from functools import lru_cache

from tekton_parsers import extract_taskrun_names, extract_failed_step_names, build_taskrun_detail


class KubernetesClient:
    """Client for interacting with Kubernetes API directly.

    This is a fallback when KubeArchive doesn't have the data yet.
    Uses 'oc' command line tool to interact with Kubernetes API.
    """

    def __init__(self, namespace: str = 'NAMESPACE_PLACEHOLDER'):
        """Initialize Kubernetes client.

        Args:
            namespace: Default Kubernetes namespace.
        """
        self.namespace = namespace

    def get_pipelinerun(self, name: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch PipelineRun from Kubernetes API.

        Args:
            name: PipelineRun name.
            namespace: Kubernetes namespace (uses default if not specified).

        Returns:
            PipelineRun JSON data or None if not found.
        """
        ns = namespace or self.namespace

        try:
            result = subprocess.run(
                ['oc', 'get', 'pipelinerun', name, '-n', ns, '-o', 'json'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True,
                timeout=10
            )
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def get_taskrun(self, name: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch TaskRun from Kubernetes API.

        Args:
            name: TaskRun name.
            namespace: Kubernetes namespace (uses default if not specified).

        Returns:
            TaskRun JSON data or None if not found.
        """
        ns = namespace or self.namespace

        try:
            result = subprocess.run(
                ['oc', 'get', 'taskrun', name, '-n', ns, '-o', 'json'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True,
                timeout=10
            )
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def get_pod_logs(
        self,
        pod_name: str,
        container: Optional[str] = None,
        namespace: Optional[str] = None,
        tail_lines: Optional[int] = 5000
    ) -> Optional[str]:
        """Fetch pod logs from Kubernetes API.

        Args:
            pod_name: Pod name.
            container: Container name within pod. If None, gets all containers.
            namespace: Kubernetes namespace (uses default if not specified).
            tail_lines: Number of lines to tail.

        Returns:
            Pod logs as string or None if not found.
        """
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
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30
            )

            if result.returncode == 0 and result.stdout:
                return result.stdout
            return None

        except subprocess.TimeoutExpired:
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
        max_log_size: int = 200000
    ) -> Optional[str]:
        """Fetch complete logs for a PipelineRun from Kubernetes API.

        This orchestrates fetching the PipelineRun, finding TaskRuns,
        and collecting logs from all steps.

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

            # Fetch logs for each step (limit to 5 steps per TaskRun)
            for step in steps_to_fetch[:5]:
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
