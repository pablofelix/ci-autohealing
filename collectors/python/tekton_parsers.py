"""Pure functions for parsing Tekton PipelineRun and TaskRun data.

All functions in this module are pure: they take dicts (raw Kubernetes JSON)
and return transformed data. No I/O, no side effects, no network calls.
"""

import re
from typing import Dict, Any, List, Optional, Tuple


def extract_taskrun_names(pipelinerun_data):
    # type: (Dict[str, Any]) -> List[str]
    """Extract TaskRun names from PipelineRun childReferences."""
    child_refs = pipelinerun_data.get('status', {}).get('childReferences', [])
    return [
        ref['name']
        for ref in child_refs
        if ref.get('kind') == 'TaskRun'
    ]


def extract_failed_step_names(taskrun_data):
    # type: (Dict[str, Any]) -> List[str]
    """Extract names of steps that exited with non-zero code."""
    steps = taskrun_data.get('status', {}).get('steps', [])
    return [
        step['name']
        for step in steps
        if step.get('terminated', {}).get('exitCode', 0) != 0
    ]


def build_taskrun_detail(taskrun_data):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Build a structured detail dict from raw TaskRun JSON."""
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
        'failed_steps': extract_failed_step_names(taskrun_data)
    }


# -- Log parsing --

_ERROR_PATTERNS = [
    (r'ERROR:\s*(.{0,500})', 'Build Error'),
    (r'FAIL(?:ED)?:\s*(.{0,500})', 'Test Failure'),
    (r'(?:exit|return) code (\d+)', 'Exit Code'),
    (r'(timeout|timed out)', 'Timeout'),
    (r'(resource not found|404)', 'Resource Not Found'),
    (r'(?:fatal|FATAL):\s*(.{0,500})', 'Fatal Error'),
]


def extract_error_from_logs(logs):
    # type: (Optional[str]) -> Tuple[Optional[str], Optional[str]]
    """Extract an error message and its category from build logs.

    Returns:
        (error_message, error_type) or (None, None) if no error found.
    """
    if not logs:
        return None, None

    for pattern, error_type in _ERROR_PATTERNS:
        match = re.search(pattern, logs, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(0)[:500], error_type

    return None, None


def extract_failed_step_from_logs(logs):
    # type: (Optional[str]) -> Optional[str]
    """Extract the failed step name from TaskRun/Step markers in logs."""
    if not logs:
        return None
    match = re.search(r'===== TaskRun: ([^/]+) / Step: ([^=]+) =====', logs)
    if match:
        return match.group(2).strip()
    return None


# -- PipelineRun metadata extraction --

def extract_pr_number_from_annotations(annotations):
    # type: (Dict[str, str]) -> Optional[int]
    """Extract pull request number from PipelineRun annotations."""
    pr_url = annotations.get('pipelinesascode.tekton.dev/pull-request-url', '')
    if pr_url:
        match = re.search(r'/pull/(\d+)', pr_url)
        if match:
            return int(match.group(1))
    return None


def extract_pipelinerun_metadata(pr_data, namespace, application_name):
    # type: (Optional[Dict[str, Any]], str, str) -> Dict[str, Any]
    """Extract all useful metadata from a PipelineRun dict.

    Returns a flat dict with commit info, URLs, timing, and failed tasks.
    """
    if not pr_data:
        return {}

    annotations = pr_data.get('metadata', {}).get('annotations', {})
    params = pr_data.get('spec', {}).get('params', [])
    status = pr_data.get('status', {})

    commit_sha = (
        annotations.get('build.appstudio.redhat.com/commit_sha') or
        annotations.get('pipelinesascode.tekton.dev/sha') or
        next((p['value'] for p in params if p.get('name') == 'revision'), None)
    )

    commit_url = annotations.get('pipelinesascode.tekton.dev/sha-url')
    if not commit_url and commit_sha:
        repo_url = next((p['value'] for p in params if p.get('name') == 'git-url'), None)
        if repo_url:
            commit_url = "{}/commit/{}".format(repo_url.rstrip('.git'), commit_sha)

    commit_message = annotations.get('pipelinesascode.tekton.dev/sha-title', '')
    commit_author = annotations.get('pipelinesascode.tekton.dev/sender', '')

    konflux_url = annotations.get('pipelinesascode.tekton.dev/log-url')
    if not konflux_url:
        pr_name = pr_data.get('metadata', {}).get('name')
        ns = pr_data.get('metadata', {}).get('namespace', namespace)
        if pr_name:
            konflux_url = (
                "https://konflux-ui.apps.CLUSTER_DOMAIN"
                "/ns/{}/applications/{}/pipelineruns/{}/logs"
            ).format(ns, application_name, pr_name)

    pipeline_url = (
        annotations.get('build.appstudio.openshift.io/pipeline-url') or
        annotations.get('pipelinesascode.tekton.dev/repo-url', '')
    )

    repo_url = next((p['value'] for p in params if p.get('name') == 'git-url'), '')
    branch = annotations.get('pipelinesascode.tekton.dev/branch', '')

    start_time = status.get('startTime')
    completion_time = status.get('completionTime')

    failed_tasks = []
    for ref in status.get('childReferences', []):
        if ref.get('kind') == 'TaskRun':
            task_name = ref.get('pipelineTaskName')
            if task_name:
                failed_tasks.append(task_name)

    return {
        'commit_sha': commit_sha,
        'commit_short_sha': commit_sha[:8] if commit_sha else None,
        'commit_url': commit_url,
        'commit_message': commit_message,
        'commit_author': commit_author,
        'konflux_url': konflux_url,
        'pipeline_url': pipeline_url,
        'repository_url': repo_url,
        'branch': branch,
        'start_time': start_time,
        'completion_time': completion_time,
        'failed_tasks': failed_tasks,
        'pr_number': extract_pr_number_from_annotations(annotations),
        'pr_url': annotations.get('pipelinesascode.tekton.dev/pull-request-url')
    }


# -- PipelineRun status classification --

def classify_pipelinerun_status(reason):
    # type: (str) -> str
    """Map a PipelineRun condition reason to a human-readable status.

    Returns one of: 'Failed', 'Cancelled', 'Timeout', 'Succeeded', 'Running', 'Pending'.
    """
    if reason == 'PipelineRunCancelled':
        return 'Cancelled'
    if reason in ('PipelineRunTimeout', 'TaskRunTimeout'):
        return 'Timeout'
    if reason in ('Succeeded', 'Completed'):
        return 'Succeeded'
    if reason == 'Failed':
        return 'Failed'
    if reason == 'Running':
        return 'Running'
    return 'Pending'


def classify_build_status(reason):
    # type: (str) -> 'BuildStatus'
    """Map a PipelineRun condition reason to a BuildStatus enum value.

    Wraps classify_pipelinerun_status and maps to the BuildStatus enum.
    Cancelled and Timeout are treated as FAILED (not infrastructure issues
    that should be ignored, but failures that need attention).
    """
    from models import BuildStatus
    status_str = classify_pipelinerun_status(reason)
    _MAP = {
        'Failed': BuildStatus.FAILED,
        'Cancelled': BuildStatus.FAILED,
        'Timeout': BuildStatus.FAILED,
        'Succeeded': BuildStatus.SUCCEEDED,
        'Running': BuildStatus.RUNNING,
        'Pending': BuildStatus.PENDING,
    }
    return _MAP.get(status_str, BuildStatus.PENDING)


# -- Conforma-specific parsing --

def extract_conforma_component_info(pr_data, component_name, repo_url_fallback=''):
    # type: (Dict[str, Any], str, str) -> Dict[str, Any]
    """Extract snapshot, image, repo, and commit info from a Conforma PipelineRun.

    Args:
        pr_data: Raw PipelineRun JSON.
        component_name: Component name (used for fallback lookups).
        repo_url_fallback: Repository URL from external lookup (e.g., oc get component).
    """
    labels = pr_data.get('metadata', {}).get('labels', {})
    annotations = pr_data.get('metadata', {}).get('annotations', {})
    params = pr_data.get('spec', {}).get('params', [])

    snapshot = labels.get('appstudio.openshift.io/snapshot', '')

    image = ''
    for p in params:
        if p.get('name') == 'IMAGES':
            image = p.get('value', '')
            break

    repo_url = annotations.get('pipelinesascode.tekton.dev/repo-url', '')
    commit_sha = (
        annotations.get('build.appstudio.redhat.com/commit_sha', '') or
        annotations.get('pipelinesascode.tekton.dev/sha', '')
    )

    if not repo_url:
        repo_url = repo_url_fallback

    commit_url = ''
    if repo_url and commit_sha:
        commit_url = "{}/commit/{}".format(repo_url.rstrip('.git'), commit_sha)

    return {
        'snapshot_name': snapshot,
        'container_image': image,
        'repository_url': repo_url,
        'commit_sha': commit_sha,
        'commit_url': commit_url
    }


def extract_verify_taskrun_name(pr_data):
    # type: (Dict[str, Any]) -> Optional[str]
    """Find the 'verify' TaskRun name from PipelineRun childReferences."""
    child_refs = pr_data.get('status', {}).get('childReferences', [])
    for ref in child_refs:
        if ref.get('kind') == 'TaskRun' and ref.get('pipelineTaskName') == 'verify':
            return ref.get('name')
    return None
