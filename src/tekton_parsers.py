"""Pure functions for parsing Tekton PipelineRun and TaskRun data.

All functions in this module are pure: they take dicts (raw Kubernetes JSON)
and return transformed data. No I/O, no side effects, no network calls.
"""

import re


def extract_taskrun_names(pipelinerun_data):
    """Extract TaskRun names from PipelineRun childReferences."""
    child_refs = pipelinerun_data.get('status', {}).get('childReferences', [])
    return [
        ref['name']
        for ref in child_refs
        if ref.get('kind') == 'TaskRun'
    ]


def extract_failed_step_names(taskrun_data):
    """Extract names of steps that exited with non-zero code."""
    steps = taskrun_data.get('status', {}).get('steps', [])
    return [
        step['name']
        for step in steps
        if step.get('terminated', {}).get('exitCode', 0) != 0
    ]


def build_taskrun_detail(taskrun_data):
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
    (r'(?:exit|return) code ([1-9]\d*)', 'Exit Code'),
    (r'(?:fatal|FATAL):\s*(.{0,500})', 'Fatal Error'),
    (r'(?:Exception|exception):\s*(.{0,500})', 'Exception'),
    (r'(?:Traceback|traceback)', 'Python Error'),
    (r'timed?\s*out', 'Timeout'),
    (r'(?:returned?|status(?:\s+code)?)\s+404', 'Resource Not Found'),
]


def extract_error_from_logs(logs):
    """Extract an error message and its category from build logs.

    Filters out script source code, metadata, and Slack messages.
    Only matches actual execution errors.

    Returns:
        (error_message, error_type) or (None, None) if no error found.
    """
    if not logs:
        return None, None

    # Split into lines for context-aware filtering
    lines = logs.split('\n')

    # Patterns that indicate script source code (not execution)
    script_indicators = [
        r'^\s*(echo|cat|printf|print)\s+["\']',  # echo "ERROR..."
        r'^\s*#',                                  # Comments
        r'<<["\']?EOF',                            # Heredocs
        r'^\s*function\s+',                        # Function definitions
        r'^\s*\w+\(\)\s*{',                        # Function definitions (bash)
    ]

    # Patterns to skip (metadata, not actual errors)
    metadata_patterns = [
        r'Slack Message:',                         # Slack notifications
        r'pull request pipeline detected',         # PR detection messages
        r'CC -.*subteam',                          # Slack mentions
        r'Status:.*\(cluster:',                    # Status messages
        r'Build URL:',                             # Metadata URLs
        r'DEBUG INFORMATION',                      # Debug sections
    ]

    for i, line in enumerate(lines):
        # Skip if this looks like script source code
        if any(re.match(pattern, line) for pattern in script_indicators):
            continue

        # Skip if line ends with >&2 or similar (script redirects)
        if re.search(r'>&\d+\s*$', line):
            continue

        # Skip metadata/notification messages
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in metadata_patterns):
            continue

        # Now try to match error patterns on execution output only
        for pattern, error_type in _ERROR_PATTERNS:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                # Get some context (2 lines before and after)
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                context = '\n'.join(lines[start:end])
                return context[:500], error_type

    return None, None


def extract_failed_step_from_logs(logs):
    """Extract the failed step name from TaskRun/Step markers in logs."""
    if not logs:
        return None
    match = re.search(r'===== TaskRun: ([^/]+) / Step: ([^=]+) =====', logs)
    if match:
        return match.group(2).strip()
    return None


def extract_failed_step_from_pipelinerun(pr_data):
    """Extract the failed task/step name from PipelineRun metadata.

    More reliable than parsing logs. Checks childReferences for failed TaskRuns.

    Returns:
        Name of the failed pipelineTask (e.g., 'build', 'test', 'init-task')
    """
    if not pr_data:
        return None

    # Get the failed tasks from status
    child_refs = pr_data.get('status', {}).get('childReferences', [])

    # Look for the first TaskRun that failed
    for ref in child_refs:
        if ref.get('kind') == 'TaskRun':
            task_name = ref.get('pipelineTaskName')
            # If whenExpressions exist and are False, this task was skipped
            when_expr = ref.get('whenExpressions', [])
            if when_expr and all(w.get('output') == 'false' for w in when_expr):
                continue
            # Return the first non-skipped task (likely the failed one)
            if task_name:
                return task_name

    return None


# -- PipelineRun metadata extraction --

def extract_pr_number_from_annotations(annotations):
    """Extract pull request number from PipelineRun annotations."""
    pr_url = annotations.get('pipelinesascode.tekton.dev/pull-request-url', '')
    if pr_url:
        match = re.search(r'/pull/(\d+)', pr_url)
        if match:
            return int(match.group(1))
    return None


def extract_pipelinerun_metadata(pr_data, namespace, application_name):
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
            from shared_config import KONFLUX_UI_BASE
            konflux_url = (
                "{}/ns/{}/applications/{}/pipelineruns/{}/logs"
            ).format(KONFLUX_UI_BASE, ns, application_name, pr_name)

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

    # Extract pipeline output results (IMAGE_URL, IMAGE_DIGEST, etc.)
    pr_results = status.get('results', [])
    results_map = {r['name']: r.get('value', '') for r in pr_results if 'name' in r}

    image_url = results_map.get('IMAGE_URL')
    image_digest = results_map.get('IMAGE_DIGEST')
    chains_git_url = results_map.get('CHAINS-GIT_URL')
    chains_git_commit = results_map.get('CHAINS-GIT_COMMIT')

    # Task completion summary from conditions message
    task_summary = None
    conditions = status.get('conditions', [])
    if conditions:
        msg = conditions[-1].get('message', '')
        if 'Tasks Completed' in msg:
            task_summary = msg

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
        'pr_url': annotations.get('pipelinesascode.tekton.dev/pull-request-url'),
        'output_image': image_url,
        'image_digest': image_digest,
        'chains_git_url': chains_git_url,
        'chains_git_commit': chains_git_commit,
        'task_summary': task_summary,
    }


# -- PipelineRun status classification --

def classify_pipelinerun_status(reason):
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
    """Find the 'verify' TaskRun name from PipelineRun childReferences."""
    child_refs = pr_data.get('status', {}).get('childReferences', [])
    for ref in child_refs:
        if ref.get('kind') == 'TaskRun' and ref.get('pipelineTaskName') == 'verify':
            return ref.get('name')
    return None
