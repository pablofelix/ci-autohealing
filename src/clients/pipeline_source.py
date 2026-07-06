"""Abstract base class for pipeline data sources.

Defines the contract that all pipeline data adapters must implement.
Concrete implementations adapt different transports (HTTP to KubeArchive,
Python kubernetes client) behind this common interface.
"""

from abc import ABC, abstractmethod

from tekton_parsers import build_taskrun_detail, extract_failed_step_names, extract_taskrun_names


class PipelineRunSource(ABC):
    """Abstract interface for fetching Tekton pipeline data.

    Subclasses implement the three primitive operations (get_pipelinerun,
    get_taskrun, get_pod_logs). Higher-level operations like fetching all
    logs for a PipelineRun are built on top of these primitives.
    """

    @abstractmethod
    def get_pipelinerun(self, name, namespace=None):
        """Fetch PipelineRun data by name."""

    @abstractmethod
    def get_taskrun(self, name, namespace=None):
        """Fetch TaskRun data by name."""

    @abstractmethod
    def get_pod_logs(self, pod_name, container=None, namespace=None, tail_lines=None):
        """Fetch logs for a pod/container."""

    def step_container_name(self, step_name):
        """Map a Tekton step name to the container name used to fetch logs.

        Override in subclasses where the convention differs (e.g. KubeArchive
        uses 'step-<name>' while the live cluster uses '<name>' directly).
        """
        return step_name

    def get_pipelinerun_logs(self, pipelinerun_name, namespace=None, max_log_size=200000):
        """Fetch combined logs for all failed steps in a PipelineRun.

        Orchestrates: PipelineRun -> TaskRuns -> failed steps -> pod logs.
        """
        ns = namespace or getattr(self, 'namespace', None)

        pr_data = self.get_pipelinerun(pipelinerun_name, ns)
        if not pr_data:
            return None

        taskrun_names = extract_taskrun_names(pr_data)
        if not taskrun_names:
            return None

        all_logs = []
        for tr_name in taskrun_names:
            tr_data = self.get_taskrun(tr_name, ns)
            if not tr_data:
                continue

            pod_name = tr_data.get('status', {}).get('podName')
            if not pod_name:
                continue

            failed_steps = extract_failed_step_names(tr_data)
            steps_to_fetch = failed_steps if failed_steps else [
                step['name'] for step in tr_data.get('status', {}).get('steps', [])
            ]

            for step in steps_to_fetch[:5]:
                container = self.step_container_name(step)
                # Get complete logs (no tail limit) for AI analysis
                logs = self.get_pod_logs(pod_name, container=container, namespace=ns, tail_lines=None)
                if logs:
                    all_logs.append("===== TaskRun: {} / Step: {} =====\n{}".format(tr_name, step, logs))

        combined = "\n\n".join(all_logs)
        if combined and len(combined) > max_log_size:
            return combined[-max_log_size:]
        return combined or None

    def get_pipelinerun_taskruns_details(self, pipelinerun_name, namespace=None):
        """Get detailed information about all TaskRuns in a PipelineRun."""
        ns = namespace or getattr(self, 'namespace', None)

        pr_data = self.get_pipelinerun(pipelinerun_name, ns)
        if not pr_data:
            return []

        taskrun_names = extract_taskrun_names(pr_data)
        if not taskrun_names:
            return []

        details = []
        for tr_name in taskrun_names:
            tr_data = self.get_taskrun(tr_name, ns)
            if tr_data:
                details.append(build_taskrun_detail(tr_data))

        return details
