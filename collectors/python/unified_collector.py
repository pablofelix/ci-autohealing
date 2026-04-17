"""Unified multi-API collector - Uses ALL available Python APIs to get complete data.

Priority order:
1. Kubernetes API (oc/kubectl) - Most current, includes status.results
2. KubeArchive API - Archived data
3. Active pods - Live logs

All using Python, no shell commands for log collection.
"""

import subprocess
import json
from typing import Optional, Dict, Any, Tuple

from kubernetes_client import KubernetesClient
from kubearchive_client import KubeArchiveClient


class UnifiedCollector:
    """Unified collector that tries all available APIs to get complete data."""

    def __init__(self, namespace: str = 'NAMESPACE_PLACEHOLDER'):
        """Initialize unified collector.

        Args:
            namespace: Kubernetes namespace.
        """
        self.namespace = namespace
        self.k8s = KubernetesClient(namespace=namespace)
        self.kubearchive = KubeArchiveClient(namespace=namespace)

    def get_pipelinerun_complete(
        self,
        pr_name: str
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """Get PipelineRun with ALL available data.

        Tries multiple sources and returns the most complete data.

        Args:
            pr_name: PipelineRun name.

        Returns:
            Tuple of (pipelinerun_data, source_name).
        """
        # Method 1: Try Kubernetes API (most current, includes status.results)
        pr_data = self.k8s.get_pipelinerun(pr_name)
        if pr_data:
            # Check if it has results in status
            results = pr_data.get('status', {}).get('results', [])
            if results:
                return pr_data, 'kubernetes_with_results'
            return pr_data, 'kubernetes'

        # Method 2: Try KubeArchive
        pr_data = self.kubearchive.get_pipelinerun(pr_name)
        if pr_data:
            return pr_data, 'kubearchive'

        return None, 'none'

    def get_logs_complete(
        self,
        pr_name: str,
        max_size: int = 200000
    ) -> Tuple[Optional[str], str]:
        """Get logs using ALL available methods.

        Args:
            pr_name: PipelineRun name.
            max_size: Maximum log size in bytes.

        Returns:
            Tuple of (logs, source_name).
        """
        # Method 1: Try Kubernetes API (gets from current pods/taskruns)
        logs = self.k8s.get_pipelinerun_logs(pr_name)
        if logs:
            return logs[:max_size], 'kubernetes'

        # Method 2: Try KubeArchive
        logs = self.kubearchive.get_pipelinerun_logs(pr_name)
        if logs:
            return logs[:max_size], 'kubearchive'

        # Method 3: Try active pods via oc (uses subprocess but reliable)
        logs = self._get_logs_via_oc(pr_name)
        if logs:
            return logs[:max_size], 'oc_pods'

        return None, 'none'

    def _get_logs_via_oc(self, pr_name: str) -> Optional[str]:
        """Get logs using oc command as last resort.

        Args:
            pr_name: PipelineRun name.

        Returns:
            Combined logs or None.
        """
        try:
            # Get pods for this PipelineRun
            result = subprocess.run(
                ['oc', 'get', 'pod', '-n', self.namespace,
                 f'-l', f'tekton.dev/pipelineRun={pr_name}',
                 '--no-headers', '-o', 'custom-columns=:metadata.name'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True,
                timeout=10
            )

            pods = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
            if not pods:
                return None

            # Collect logs from all pods
            all_logs = []
            for pod in pods[:10]:  # Limit to 10 pods
                try:
                    logs_result = subprocess.run(
                        ['oc', 'logs', pod, '-n', self.namespace,
                         '--all-containers', '--tail=5000'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=30
                    )
                    if logs_result.stdout:
                        all_logs.append(f"===== Pod: {pod} =====\n{logs_result.stdout}")
                except subprocess.TimeoutExpired:
                    continue

            return "\n\n".join(all_logs) if all_logs else None

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def get_taskruns_details(
        self,
        pr_name: str
    ) -> Tuple[list, str]:
        """Get detailed TaskRun information.

        Args:
            pr_name: PipelineRun name.

        Returns:
            Tuple of (taskrun_details_list, source_name).
        """
        # Try Kubernetes API
        details = self.k8s.get_pipelinerun_taskruns_details(pr_name)
        if details:
            return details, 'kubernetes'

        # Try KubeArchive
        details = self.kubearchive.get_pipelinerun_taskruns_details(pr_name)
        if details:
            return details, 'kubearchive'

        return [], 'none'
