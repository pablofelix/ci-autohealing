"""Unified pipeline client using Chain of Responsibility.

Tries multiple PipelineRunSource implementations in priority order,
returning the first successful result.
"""

import subprocess
import json
from typing import Optional, List, Dict, Any, Tuple

from clients.pipeline_source import PipelineRunSource
from clients.kubernetes import KubernetesClient
from clients.kubearchive import KubeArchiveClient


class UnifiedPipelineClient:
    """Tries multiple data sources in priority order.

    Default chain: Kubernetes (live) -> KubeArchive (archived).
    Falls back to direct oc pod log retrieval as a last resort.
    """

    def __init__(self, namespace='NAMESPACE_PLACEHOLDER', sources=None):
        # type: (str, Optional[List[PipelineRunSource]]) -> None
        self.namespace = namespace
        if sources is not None:
            self.sources = sources
        else:
            self.sources = [KubernetesClient(namespace=namespace)]
            try:
                self.sources.append(KubeArchiveClient(namespace=namespace))
            except RuntimeError:
                pass

    def get_pipelinerun_complete(self, pr_name):
        # type: (str,) -> Tuple[Optional[Dict[str, Any]], str]
        """Get PipelineRun data from the first source that has it."""
        for source in self.sources:
            pr_data = source.get_pipelinerun(pr_name)
            if pr_data:
                source_name = type(source).__name__
                results = pr_data.get('status', {}).get('results', [])
                if results:
                    return pr_data, '{}_with_results'.format(source_name)
                return pr_data, source_name
        return None, 'none'

    def get_logs_complete(self, pr_name, max_size=200000):
        # type: (str, int) -> Tuple[Optional[str], str]
        """Get logs from the first source that has them."""
        for source in self.sources:
            logs = source.get_pipelinerun_logs(pr_name)
            if logs:
                return logs[:max_size], type(source).__name__
        logs = self._get_logs_via_oc(pr_name)
        if logs:
            return logs[:max_size], 'oc_pods'
        return None, 'none'

    def get_taskruns_details(self, pr_name):
        # type: (str,) -> Tuple[List[Dict[str, Any]], str]
        """Get TaskRun details from the first source that has them."""
        for source in self.sources:
            details = source.get_pipelinerun_taskruns_details(pr_name)
            if details:
                return details, type(source).__name__
        return [], 'none'

    def _get_logs_via_oc(self, pr_name):
        # type: (str,) -> Optional[str]
        """Last-resort: get logs by finding pods labeled with the PipelineRun."""
        try:
            result = subprocess.run(
                ['oc', 'get', 'pod', '-n', self.namespace,
                 '-l', 'tekton.dev/pipelineRun={}'.format(pr_name),
                 '--no-headers', '-o', 'custom-columns=:metadata.name'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, check=True, timeout=10
            )
            pods = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
            if not pods:
                return None

            all_logs = []
            for pod in pods[:10]:
                try:
                    # Get complete logs (no --tail limit) for AI analysis
                    logs_result = subprocess.run(
                        ['oc', 'logs', pod, '-n', self.namespace,
                         '--all-containers'],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        universal_newlines=True, timeout=30
                    )
                    if logs_result.stdout:
                        all_logs.append("===== Pod: {} =====\n{}".format(pod, logs_result.stdout))
                except subprocess.TimeoutExpired:
                    continue

            return "\n\n".join(all_logs) if all_logs else None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
