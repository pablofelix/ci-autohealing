"""Data models for CI Auto-Healing system."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum


class BuildStatus(Enum):
    """Build failure status."""

    FAILED = "Failed"
    SUCCEEDED = "Succeeded"
    RUNNING = "Running"
    PENDING = "Pending"


@dataclass
class TaskRun:
    """Tekton TaskRun details."""

    name: str
    pod_name: Optional[str] = None
    failed_steps: List[str] = field(default_factory=list)
    exit_code: Optional[int] = None


@dataclass
class PipelineRun:
    """Tekton PipelineRun with associated data."""

    name: str
    uid: str
    namespace: str
    component: str
    repository: str
    repository_url: str
    branch: str
    status: BuildStatus
    task_runs: List[TaskRun] = field(default_factory=list)
    build_logs: Optional[str] = None
    start_time: Optional[datetime] = None
    completion_time: Optional[datetime] = None
    commit_sha: Optional[str] = None
    commit_url: Optional[str] = None

    @property
    def konflux_logs_url(self) -> str:
        """Generate Konflux UI logs URL."""
        from shared_config import KONFLUX_UI_BASE
        return (
            f"{KONFLUX_UI_BASE}"
            f"/ns/{self.namespace}/applications/{{app}}/pipelineruns/{self.name}/logs"
        )

    @property
    def has_logs(self) -> bool:
        """Check if build logs are available."""
        return self.build_logs is not None and len(self.build_logs) > 0


@dataclass
class Component:
    """Konflux component information."""

    name: str
    repository_url: str
    branch: str
    namespace: str

    @classmethod
    def from_file(cls, file_path: str, namespace: str) -> List['Component']:
        """Load components from a file.

        Args:
            file_path: Path to file with component names (one per line).
            namespace: Kubernetes namespace.

        Returns:
            List of Component instances (repository info loaded separately).
        """
        from pathlib import Path

        components = []
        with Path(file_path).open() as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    # Repository info will be loaded from Kubernetes
                    components.append(cls(
                        name=line,
                        repository_url="",
                        branch="",
                        namespace=namespace
                    ))
        return components


@dataclass
class ScanResult:
    """Results from a collection scan."""

    scan_id: str
    components_scanned: int
    failures_found: int
    new_failures: int
    logs_fetched: int
    duration_seconds: float
