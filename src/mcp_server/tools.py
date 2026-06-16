"""MCP tools for Konflux CI monitoring.

All tools are read-only queries backed by repository methods.
Each tool accepts an `application` parameter for multi-version support.
"""

import asyncio
import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional, TypeVar
from functools import wraps

from mcp_server import mcp
from mcp_server.models import (
    AlertsSummary,
    AnalysisDetails,
    ApplicationInfo,
    BuildFailureDetails,
    ComponentHistoryResponse,
    ConformaViolationDetails,
    DashboardResponse,
    FailureSummary,
    HealthWarning,
    SkillInfo,
    SkillPrerequisiteResult,
    SkillSourceInfo,
    SkillValidationFinding,
    SkillValidationResult,
    StatsResponse,
    TriageResponse,
)

from shared_config import APPLICATION_NAME, KONFLUX_UI_BASE, NAMESPACE

DEFAULT_APPLICATION = APPLICATION_NAME

T = TypeVar('T')


def async_tool(sync_func: Callable[..., T]) -> Callable[..., "asyncio.Future[T]"]:
    @wraps(sync_func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: sync_func(*args, **kwargs))
    return wrapper


def _get_repo(repo_class):
    from repositories.repository_factory import get_repository
    return get_repository(repo_class)


def _build_repo():
    from repositories.build_failure_repository import BuildFailureRepository
    return _get_repo(BuildFailureRepository)


def _conforma_repo():
    from repositories.conforma_repository import ConformaRepository
    return _get_repo(ConformaRepository)


def _ai_repo():
    from repositories.ai_analysis_repository import AIAnalysisRepository
    return _get_repo(AIAnalysisRepository)


def _pattern_repo():
    from repositories.error_pattern_repository import ErrorPatternRepository
    return _get_repo(ErrorPatternRepository)


def _resolution_repo():
    from repositories.resolution_attempt_repository import ResolutionAttemptRepository
    return _get_repo(ResolutionAttemptRepository)


def _triage_repo():
    from repositories.triage_repository import TriageRepository
    return _get_repo(TriageRepository)


def _konflux_url(pr_name: str) -> str:
    return f"{KONFLUX_UI_BASE}/ns/{NAMESPACE}/pipelinerun/{pr_name}/logs"


# ---------------------------------------------------------------------------
# Query tools (12 refactored from original MCP server)
# ---------------------------------------------------------------------------

@mcp.tool()
@async_tool
def list_applications() -> List[ApplicationInfo]:
    """List all available RHOAI versions/applications.

    Discovers what versions exist in the database so agents can choose which to query.
    """
    repo = _build_repo()
    apps = repo.get_applications()

    conforma_repo = _conforma_repo()
    results = []
    for app_row in apps:
        app_name = app_row['application']
        conforma_failing = conforma_repo.find_unresolved_component_names(app_name)
        stats = repo.get_overview_stats(app_name)
        results.append(ApplicationInfo(
            name=app_name,
            component_count=stats['components'],
            failure_count=stats['total'],
            conforma_count=len(conforma_failing),
        ))
    return results


@mcp.tool()
@async_tool
def list_alerts(application: str = DEFAULT_APPLICATION) -> AlertsSummary:
    """Get all unresolved alerts (build failures + Conforma violations).

    Returns a unified view of all current issues for an application version.

    Args:
        application: Which version to query. Use list_applications() to discover versions.
    """
    triage = _build_repo().get_triage_summary(application)

    dep_updates = {}
    try:
        from clients.konflux_client import KonfluxClient
        kc = KonfluxClient(namespace=NAMESPACE)
        for u in kc.get_dependency_updates(hours=48):
            comp = u.get('component', '')
            if comp and comp not in dep_updates:
                dep_updates[comp] = '{} {} → {}'.format(
                    u.get('package', ''), u.get('from_version', ''), u.get('to_version', ''))
    except Exception:
        pass

    build_failures = []
    for comp in triage.get('failing_components', []):
        component_name = comp['component']
        cause = None
        if component_name in dep_updates:
            cause = 'dependency_update: {}'.format(dep_updates[component_name])

        build_failures.append(FailureSummary(
            component=component_name,
            status=comp.get('status', 'Failed'),
            error_type=comp.get('error_type'),
            first_seen=comp.get('first_detected_at', datetime.utcnow()),
            last_seen=comp.get('last_updated_at', datetime.utcnow()),
            occurrence_count=comp.get('failure_count', 1),
            has_logs=comp.get('has_logs', False),
            has_context=comp.get('has_context', False),
            has_analysis=comp.get('ai_analyzed', False),
            possible_cause=cause,
        ))

    conforma_violations = []
    conforma_failing = _conforma_repo().find_unresolved_component_names(application)
    for comp_name in sorted(conforma_failing):
        details = _conforma_repo().get_violation_details(comp_name, application)
        if details:
            conforma_violations.append(FailureSummary(
                component=comp_name,
                status='Conforma Violation',
                error_type=details.get('scenario'),
                first_seen=details.get('first_detected_at', datetime.utcnow()),
                last_seen=details.get('last_updated_at', datetime.utcnow()),
                occurrence_count=1,
                has_logs=details.get('violation_summary') is not None,
                has_analysis=details.get('ai_analyzed', False),
            ))

    all_dates = ([f.last_seen for f in build_failures] +
                 [v.last_seen for v in conforma_violations])
    last_sync = max(all_dates) if all_dates else datetime.utcnow()

    return AlertsSummary(
        application=application,
        build_failures=build_failures,
        conforma_violations=conforma_violations,
        total_count=len(build_failures) + len(conforma_violations),
        last_sync=last_sync,
    )


@mcp.tool()
@async_tool
def get_failure(
    component: str,
    application: str = DEFAULT_APPLICATION,
    include_logs: bool = True,
    include_commit_context: bool = True,
) -> Optional[BuildFailureDetails]:
    """Get full build failure details for a component.

    Returns logs, commit diff, Dockerfile, .tekton configs — everything needed for analysis.

    Args:
        component: Component name (e.g., "odh-vllm-cpu-v3-4")
        application: Which version to query
        include_logs: Include build_logs text (can be large)
        include_commit_context: Include commit_context JSONB (diffs, tekton configs)
    """
    row = _build_repo().get_failure_details(component, application)
    if not row:
        return None

    konflux = row.get('konflux_url') or _konflux_url(row.get('pipelinerun_name', ''))
    logs = None
    if include_logs:
        from utils.log_filter import filter_error_lines
        raw_logs = row.get('build_logs')
        logs = filter_error_lines(raw_logs) if raw_logs else None
    context = row.get('commit_context') if include_commit_context else None

    return BuildFailureDetails(
        component=row['component_name'],
        pipelinerun_name=row.get('pipelinerun_name'),
        error_message=row.get('error_message'),
        error_type=row.get('error_type'),
        failed_task=row.get('failed_task_name'),
        failed_step=row.get('failed_step_name'),
        build_logs=logs,
        commit_sha=row.get('commit_sha'),
        commit_message=row.get('commit_message'),
        commit_author=row.get('commit_author'),
        commit_url=row.get('commit_url'),
        repository_url=row.get('repository_url'),
        branch=row.get('branch'),
        commit_context=context,
        konflux_url=konflux,
        first_detected_at=row.get('first_detected_at', datetime.utcnow()),
    )


@mcp.tool()
@async_tool
def get_violation(
    component: str,
    application: str = DEFAULT_APPLICATION,
    include_details: bool = True,
) -> Optional[ConformaViolationDetails]:
    """Get full Conforma policy violation details.

    Args:
        component: Component name
        application: Which version to query
        include_details: Include violation_details JSONB
    """
    row = _conforma_repo().get_violation_details(component, application)
    if not row:
        return None

    konflux = _konflux_url(row.get('pipelinerun_name', ''))
    details = row.get('violation_details') if include_details else None

    return ConformaViolationDetails(
        component=row['component_name'],
        scenario=row['scenario'],
        violations_count=row['violations_count'],
        warnings_count=row['warnings_count'],
        successes_count=row['successes_count'],
        violation_summary=row.get('violation_summary', ''),
        violation_details=details,
        repository_url=row.get('repository_url'),
        commit_sha=row.get('commit_sha'),
        snapshot_name=row.get('snapshot_name'),
        konflux_url=konflux,
        first_detected_at=row.get('first_detected_at', datetime.utcnow()),
    )


@mcp.tool()
@async_tool
def get_analysis(
    component: str,
    application: str = DEFAULT_APPLICATION,
    type: Literal["auto", "build", "conforma"] = "auto",
) -> Optional[AnalysisDetails]:
    """Get existing AI analysis for a component.

    Auto-detects whether it's a build failure or Conforma violation unless specified.

    Args:
        component: Component name
        application: Which version to query
        type: "auto", "build", or "conforma"
    """
    row = _ai_repo().get_analysis_by_component(component, application, type)
    if not row:
        return None
    return _row_to_analysis(row)


def _row_to_analysis(row: Dict[str, Any]) -> AnalysisDetails:
    files = row.get('recommended_files') or []
    if isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]
    return AnalysisDetails(
        type=row['analysis_type'],
        component=row['component_name'],
        model_used=row.get('model_used', ''),
        root_cause=row.get('root_cause', ''),
        failure_category=row.get('failure_category', ''),
        confidence_score=float(row.get('confidence_score') or 0),
        recommended_fix=row.get('recommended_fix', ''),
        recommended_files=files,
        can_auto_fix=bool(row.get('can_auto_fix')),
        requires_human_review=bool(row.get('requires_human_review')),
        analyzed_at=row.get('analyzed_at', datetime.utcnow()),
        langfuse_trace_url=row.get('langfuse_trace_url'),
        tokens_used=row.get('tokens_used') or 0,
        cost_usd=float(row.get('cost_usd') or 0),
    )


@mcp.tool()
@async_tool
def search_failures(
    application: str = DEFAULT_APPLICATION,
    category: Optional[str] = None,
    resolved: bool = False,
    has_analysis: Optional[bool] = None,
    limit: int = 10,
) -> List[FailureSummary]:
    """Search and filter build failures and Conforma violations.

    Args:
        application: Which version to query
        category: Filter by AI analysis category (e.g., "dependency_issue")
        resolved: Include resolved failures (default: False = only active)
        has_analysis: Filter by analysis status (True=analyzed, False=pending, None=all)
        limit: Maximum results (default: 10)
    """
    results: List[FailureSummary] = []

    triage = _build_repo().get_triage_summary(application)
    for comp in triage.get('failing_components', []):
        if has_analysis is True and not comp.get('ai_analyzed'):
            continue
        if has_analysis is False and comp.get('ai_analyzed'):
            continue
        results.append(FailureSummary(
            component=comp['component'],
            status=comp.get('status', 'Failed'),
            error_type=comp.get('error_type'),
            first_seen=comp.get('first_detected_at', datetime.utcnow()),
            last_seen=comp.get('last_updated_at', datetime.utcnow()),
            occurrence_count=comp.get('failure_count', 1),
            has_logs=comp.get('has_logs', False),
            has_analysis=comp.get('ai_analyzed', False),
        ))

    conforma_failing = _conforma_repo().find_unresolved_component_names(application)
    for comp_name in sorted(conforma_failing):
        details = _conforma_repo().get_violation_details(comp_name, application)
        if details:
            if has_analysis is True and not details.get('ai_analyzed'):
                continue
            if has_analysis is False and details.get('ai_analyzed'):
                continue
            results.append(FailureSummary(
                component=comp_name,
                status='Conforma Violation',
                error_type=details.get('scenario'),
                first_seen=details.get('first_detected_at', datetime.utcnow()),
                last_seen=details.get('last_updated_at', datetime.utcnow()),
                occurrence_count=1,
                has_logs=details.get('violation_summary') is not None,
                has_analysis=details.get('ai_analyzed', False),
            ))

    results.sort(key=lambda f: f.last_seen, reverse=True)
    return results[:limit]


@mcp.tool()
@async_tool
def get_stats(application: str = DEFAULT_APPLICATION) -> StatsResponse:
    """Get summary statistics for an application version.

    Returns pending counts, analysis stats, and costs (like `ic ai status`).

    Args:
        application: Which version to query
    """
    ai_repo = _ai_repo()
    extended = ai_repo.get_extended_status(application)
    recent = ai_repo.get_recent_analyses(application, days=7, limit=10)

    build_stats = extended.get('build', {})
    conforma_stats = extended.get('conforma', {})

    return StatsResponse(
        application=application,
        build_failures={
            'pending': build_stats.get('pending', 0),
            'analyzed': build_stats.get('analyzed', 0),
            'autofixable': build_stats.get('auto_fixable', 0),
        },
        conforma_violations={
            'pending': conforma_stats.get('pending', 0),
            'analyzed': conforma_stats.get('analyzed', 0),
            'autofixable': conforma_stats.get('auto_fixable', 0),
        },
        total_cost_30d=extended.get('total_cost', 0),
        recent_analyses=recent,
    )


# ---------------------------------------------------------------------------
# New query tools (12 new)
# ---------------------------------------------------------------------------

@mcp.tool()
@async_tool
def get_triage(application: str = DEFAULT_APPLICATION) -> TriageResponse:
    """Get triage summary: failing vs working components overview.

    Args:
        application: Which version to query
    """
    triage = _build_repo().get_triage_summary(application)
    return TriageResponse(
        application=application,
        total=triage.get('total', 0),
        failing=triage.get('failing', 0),
        working=triage.get('working', 0),
        failing_components=triage.get('failing_components', []),
    )


@mcp.tool()
@async_tool
def get_triage_report(application: str = DEFAULT_APPLICATION,
                      date: Optional[str] = None) -> Dict[str, Any]:
    """Get triage tracking report: tracked items with status, Slack/Jira links.

    Returns active and resolved triage items with their tracking info.
    Use date parameter (YYYY-MM-DD) to filter to a specific day.

    Args:
        application: Which version to query
        date: Optional date filter (YYYY-MM-DD)
    """
    repo = _triage_repo()
    items = repo.get_report(application, date=date)
    summary = repo.get_summary(application)
    return {"application": application, "summary": summary, "items": items}


@mcp.tool()
@async_tool
def track_triage_item(component: str,
                      application: str = DEFAULT_APPLICATION,
                      group_label: Optional[str] = None,
                      root_cause: Optional[str] = None,
                      failed_step: Optional[str] = None,
                      slack_thread_url: Optional[str] = None,
                      jira_key: Optional[str] = None,
                      notes: Optional[str] = None,
                      add_to_id: Optional[int] = None) -> Dict[str, Any]:
    """Track a build failure in triage. Creates or updates a triage item.

    Use add_to_id to add a component to an existing triage group.
    Slack URLs accumulate — each call adds to the list.

    Args:
        component: Component name to track
        application: Which version
        group_label: Group label (e.g., "Go 1.26 mismatch")
        root_cause: Root cause description
        failed_step: Failed build step
        slack_thread_url: Slack thread URL (appended to list)
        jira_key: Jira ticket key
        notes: Additional notes
        add_to_id: Add to existing triage item ID instead of creating new
    """
    repo = _triage_repo()

    if add_to_id:
        existing = repo.get_by_id(add_to_id)
        if not existing:
            return {"error": "Triage item #{} not found".format(add_to_id)}
        components = list(existing['components'])
        if component not in components:
            components.append(component)
        updates = {'components': components}
        if jira_key:
            updates['jira_key'] = jira_key
        if notes:
            updates['notes'] = notes
        repo.update_item(add_to_id, **updates)
        if slack_thread_url:
            repo.add_slack_url(add_to_id, slack_thread_url)
        return {"action": "added", "item_id": add_to_id, "component": component}

    existing = repo.find_by_component(component, application)
    if existing:
        return {"action": "exists", "item": existing}

    item_id = repo.create_item(
        application=application, components=[component],
        group_label=group_label, root_cause=root_cause,
        failed_step=failed_step,
        slack_thread_urls=[slack_thread_url] if slack_thread_url else None,
        jira_key=jira_key, notes=notes,
    )
    return {"action": "created", "item_id": item_id, "component": component}


@mcp.tool()
@async_tool
def update_triage_item(item_id: int,
                       slack_thread_url: Optional[str] = None,
                       jira_key: Optional[str] = None,
                       notes: Optional[str] = None,
                       root_cause: Optional[str] = None,
                       status: Optional[str] = None,
                       resolution: Optional[str] = None,
                       resolution_pr_url: Optional[str] = None) -> Dict[str, Any]:
    """Update a triage item's tracking info or resolve it.

    Slack URLs accumulate — each call appends to the list (does not replace).

    Args:
        item_id: Triage item ID
        slack_thread_url: Slack thread URL (appended to list, not replaced)
        jira_key: Jira ticket key
        notes: Additional notes
        root_cause: Root cause description
        status: New status (active, monitoring, resolved)
        resolution: Resolution description (when resolving)
        resolution_pr_url: Fix PR URL (when resolving)
    """
    repo = _triage_repo()
    existing = repo.get_by_id(item_id)
    if not existing:
        return {"error": "Triage item #{} not found".format(item_id)}

    if status == 'resolved':
        repo.resolve_item(item_id, resolution=resolution, pr_url=resolution_pr_url)
        return {"action": "resolved", "item_id": item_id}

    updates = {}
    if jira_key:
        updates['jira_key'] = jira_key
    if notes:
        updates['notes'] = notes
    if root_cause:
        updates['root_cause'] = root_cause
    if status:
        updates['status'] = status

    if updates:
        repo.update_item(item_id, **updates)

    if slack_thread_url:
        repo.add_slack_url(item_id, slack_thread_url)
        updates['slack_thread_urls'] = 'added'

    if not updates and not slack_thread_url:
        return {"error": "No updates provided"}
    return {"action": "updated", "item_id": item_id, "updates": list(updates.keys())}


@mcp.tool()
@async_tool
def get_component_status(component: str) -> Dict[str, Any]:
    """Get component promotion health: last build vs last promoted image, downstream nudges.

    Returns source repo, branch, container image, last built commit,
    last promoted image, and nudges from the Kubernetes cluster.

    Args:
        component: Component name
    """
    from clients.kubernetes import KubernetesClient
    from openshift_auth import _ensure_k8s_config
    _ensure_k8s_config()
    kc = KubernetesClient(namespace=NAMESPACE)
    meta = kc.get_component_metadata(component)
    if not meta:
        return {"error": "Component not found: {}".format(component)}
    return {"component": component, **meta}


@mcp.tool()
@async_tool
def get_component_prs(component: str, state: str = "open", limit: int = 10) -> Dict[str, Any]:
    """Get GitHub PRs for a component's source repo and branch.

    Looks up the component's repo/branch from the cluster, then queries GitHub
    for PRs against that branch. Useful for finding fix PRs.

    Args:
        component: Component name
        state: PR state filter: "open", "closed", or "all"
        limit: Maximum PRs to return (default: 10)
    """
    import os
    from clients.kubernetes import KubernetesClient
    from clients.github_client import GitHubClient, parse_github_repo
    from openshift_auth import _ensure_k8s_config
    _ensure_k8s_config()
    kc = KubernetesClient(namespace=NAMESPACE)
    meta = kc.get_component_metadata(component)
    if not meta or not meta.get('repository_url'):
        return {"error": "Component not found or no repo URL: {}".format(component)}
    parsed = parse_github_repo(meta['repository_url'])
    if not parsed:
        return {"error": "Cannot parse GitHub URL: {}".format(meta['repository_url'])}
    owner, repo = parsed
    token = os.environ.get('GITHUB_TOKEN', '')
    gh = GitHubClient(token)
    prs = gh.list_pull_requests(owner, repo, base=meta.get('branch'), state=state, limit=limit)
    return {
        "component": component,
        "repository": "{}/{}".format(owner, repo),
        "branch": meta.get('branch', ''),
        "prs": prs,
    }


@mcp.tool()
@async_tool
def get_working(application: str = DEFAULT_APPLICATION) -> List[Dict[str, Any]]:
    """Get components currently working (last build = success).

    Args:
        application: Which version to query
    """
    return _build_repo().get_working_components(application)


@mcp.tool()
@async_tool
def get_resolved(application: str = DEFAULT_APPLICATION, days: Optional[int] = None, since: Optional[str] = None, to: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get components that were resolved (previously failing, now fixed).

    Args:
        application: Which version to query
        days: Only show components resolved in the last N days (default: all)
        since: Only show components resolved since date (YYYY-MM-DD)
        to: Upper bound date (YYYY-MM-DD), use with since for a range
    """
    return _build_repo().get_resolved_components(application, days=days, since=since, to=to)


@mcp.tool()
@async_tool
def get_component_history(
    component: str,
    application: str = DEFAULT_APPLICATION,
    limit: int = 20,
) -> ComponentHistoryResponse:
    """Get build history for a component (successes + failures).

    Args:
        component: Component name
        application: Which version to query
        limit: Number of recent builds to show
    """
    data = _build_repo().get_component_history(component, application, limit)
    return ComponentHistoryResponse(
        component=component,
        application=application,
        summary=data.get('summary', {}),
        builds=data.get('builds', []),
    )


@mcp.tool()
@async_tool
def get_daily_stats(
    application: str = DEFAULT_APPLICATION,
    days: int = 7,
) -> List[Dict[str, Any]]:
    """Get failure counts by day (time series).

    Args:
        application: Which version to query
        days: Number of days to show (default: 7)
    """
    return _build_repo().get_daily_stats(application, days)


@mcp.tool()
@async_tool
def get_health(application: str = DEFAULT_APPLICATION) -> List[Dict[str, Any]]:
    """Get component health summary (scores, status, failure rates).

    Args:
        application: Which version to query
    """
    from repositories.repository_factory import get_pool
    from proactive.health_monitor import HealthMonitor
    monitor = HealthMonitor(get_pool())
    summary = monitor.get_component_health_summary()
    return summary or []


@mcp.tool()
@async_tool
def get_snapshot_status(application: str = DEFAULT_APPLICATION) -> Dict[str, Any]:
    """Get latest Snapshot CR status: component images and integration test results.

    Snapshots are created after successful builds and tested by the Integration Service.
    Missing components in a Snapshot = silent build failures. Failed conditions = integration test failures.

    Args:
        application: Which version to query
    """
    from clients.konflux_client import KonfluxClient
    kc = KonfluxClient(namespace=NAMESPACE)
    snapshots = kc.get_snapshots(app_filter=application, limit=5)
    if not snapshots:
        return {'application': application, 'snapshots': [], 'error': 'No snapshots found'}
    results = []
    override_count = 0
    warning_count = 0
    event_types = {}
    for snap in snapshots:
        status = kc.extract_snapshot_status(snap)
        if status.get('is_override'):
            override_count += 1
        warning_count += len(status.get('warnings', []))
        et = status.get('event_type', 'push')
        event_types[et] = event_types.get(et, 0) + 1
        results.append(status)
    summary = {'event_type_breakdown': event_types}
    if override_count:
        summary['override_snapshots'] = override_count
        summary['override_note'] = 'Manual overrides detected — teams may be working around automation failures'
    if warning_count:
        summary['total_warnings'] = warning_count
        summary['warning_note'] = 'Tests passing with warnings — potential degradation signal'
    return {
        'application': application,
        'snapshot_count': len(results),
        'snapshots': results,
        **summary,
    }


@mcp.tool()
@async_tool
def get_release_status(application: str = DEFAULT_APPLICATION) -> Dict[str, Any]:
    """Get recent Release CR status: release pipeline progress, timing, and validation.

    Tracks the full release lifecycle — which Snapshot is being released, pipeline status,
    and post-validation results. Detects stuck or failed releases.

    Args:
        application: Which version to query
    """
    from clients.konflux_client import KonfluxClient
    kc = KonfluxClient(namespace=NAMESPACE)
    releases = kc.get_releases(app_filter=application, limit=5)
    if not releases:
        return {'application': application, 'releases': [], 'error': 'No releases found'}
    results = []
    for rel in releases:
        results.append(kc.extract_release_status(rel))
    return {'application': application, 'release_count': len(results), 'releases': results}


@mcp.tool()
@async_tool
def get_release_vulnerabilities(application: str = DEFAULT_APPLICATION) -> Dict[str, Any]:
    """Get CVE vulnerability summary for the latest release/snapshot.

    Queries SARIF scan results from the OCI registry for each component image,
    aggregates by severity. Shows security posture of the release at a glance.

    Args:
        application: Which version to query
    """
    from clients.konflux_client import KonfluxClient
    from clients.registry_client import RegistryClient

    kc = KonfluxClient(namespace=NAMESPACE)
    snapshots = kc.get_snapshots(app_filter=application, limit=1)
    if not snapshots:
        return {'application': application, 'error': 'No snapshots found'}

    snapshot = snapshots[0]
    snap_name = snapshot.get('metadata', {}).get('name', '')
    components = snapshot.get('spec', {}).get('components', [])

    rc = RegistryClient()
    comp_results = []
    totals = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
    severity_map = {'error': 'critical', 'warning': 'high', 'note': 'medium'}

    batch = rc.fetch_sarif_batch(components, timeout=60)

    for name, results in batch.items():
        if not results:
            continue

        counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
        top_cves = []
        for r in results:
            sev = severity_map.get(r.get('level', ''), 'low')
            counts[sev] += 1
            if len(top_cves) < 5:
                rule_id = r.get('ruleId', '')
                if rule_id and rule_id not in top_cves:
                    top_cves.append(rule_id)

        for sev in totals:
            totals[sev] += counts[sev]

        comp_results.append({
            'name': name,
            **counts,
            'total': sum(counts.values()),
            'top_cves': top_cves,
        })

    comp_results.sort(key=lambda c: c.get('critical', 0) + c.get('high', 0), reverse=True)

    return {
        'application': application,
        'snapshot': snap_name,
        'components_scanned': len(comp_results),
        'components_total': len(components),
        **{'total_{}'.format(k): v for k, v in totals.items()},
        'components': comp_results,
    }


@mcp.tool()
@async_tool
def get_test_configuration(application: str = DEFAULT_APPLICATION) -> Dict[str, Any]:
    """Analyze IntegrationTestScenario configuration for an application.

    Detects: disabled tests, context misconfigurations, missing Conforma scenarios,
    tests scoped to specific components, and coverage gaps.

    Args:
        application: Which version to query
    """
    from clients.konflux_client import KonfluxClient
    kc = KonfluxClient(namespace=NAMESPACE)
    scenarios = kc.get_integration_test_scenarios(NAMESPACE, app_filter=application)
    if not scenarios:
        return {'application': application, 'scenarios': [], 'error': 'No ITS found'}

    results = []
    issues = []
    disabled_count = 0
    conforma_count = 0
    for s in scenarios:
        meta = kc.extract_its_metadata(s)
        results.append(meta)

        if meta['is_disabled']:
            disabled_count += 1
            issues.append('DISABLED: {} — not running any tests'.format(meta['name']))
        if meta['is_conforma']:
            conforma_count += 1
        if meta['is_future']:
            issues.append('FUTURE: {} — will become active later'.format(meta['name']))

        component_scoped = [c for c in meta['contexts'] if c.startswith('component_')]
        if component_scoped:
            issues.append('SCOPED: {} — only runs for {}'.format(
                meta['name'], ', '.join(component_scoped)))

    if conforma_count == 0:
        issues.append('NO CONFORMA: no Enterprise Contract test scenario found')

    return {
        'application': application,
        'total_scenarios': len(results),
        'disabled': disabled_count,
        'conforma_scenarios': conforma_count,
        'issues': issues,
        'scenarios': results,
    }


@mcp.tool()
@async_tool
def get_health_warnings(application: str = DEFAULT_APPLICATION) -> List[HealthWarning]:
    """Get proactive warnings (trending issues, degradation signals).

    Args:
        application: Which version to query
    """
    from repositories.repository_factory import get_pool
    from proactive.health_monitor import HealthMonitor
    monitor = HealthMonitor(get_pool())
    checks = monitor.run_checks()
    warnings = []
    for w in checks:
        warnings.append(HealthWarning(
            type=w.signal_type,
            component=w.component_name,
            message=w.message,
            severity=w.severity,
        ))
    return warnings


@mcp.tool()
@async_tool
def get_dashboard(application: str = DEFAULT_APPLICATION) -> DashboardResponse:
    """Full dashboard view (analysis queue, enrichment, patterns, costs).

    Args:
        application: Which version to query
    """
    build_repo = _build_repo()
    ai_repo = _ai_repo()

    overview = build_repo.get_overview_stats(application)
    enrichment = build_repo.get_enrichment_coverage(application)
    overview['enrichment'] = enrichment

    ai_status = ai_repo.get_extended_status(application)
    cost = ai_repo.get_cost_summary()

    patterns = _pattern_repo().get_library_summary()
    fixes = _resolution_repo().get_outcome_summary()

    return DashboardResponse(
        application=application,
        overview=overview,
        ai_status={**ai_status, 'cost_30d': cost},
        patterns=patterns,
        fixes=fixes,
    )


@mcp.tool()
@async_tool
def list_patterns(
    failure_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List known error patterns from the pattern library.

    Args:
        failure_type: Filter by type ("build" or "conforma"). None for all.
    """
    return _pattern_repo().get_all(failure_type)


@mcp.tool()
@async_tool
def get_pattern(name: str) -> Optional[Dict[str, Any]]:
    """Get detailed pattern info (description, typical fix, docs).

    Args:
        name: Pattern name or failure category
    """
    return _pattern_repo().get_by_name_or_category(name)


@mcp.tool()
@async_tool
def get_fix_history(days: int = 30) -> Dict[str, Any]:
    """Get fix attempt history and success rates.

    Args:
        days: Number of days to look back (default: 30)
    """
    repo = _resolution_repo()
    attempts = repo.get_all(days)
    summary = repo.get_outcome_summary(days)
    return {
        'summary': summary,
        'attempts': attempts,
    }


@mcp.tool()
@async_tool
def get_conforma_report(
    application: str = DEFAULT_APPLICATION,
) -> Dict[str, Any]:
    """Get Conforma violations report for an application.

    Args:
        application: Which version to query
    """
    conforma_repo = _conforma_repo()
    failing = conforma_repo.find_unresolved_component_names(application)
    violations = []
    for comp_name in sorted(failing):
        details = conforma_repo.get_violation_details(comp_name, application)
        if details:
            violations.append(details)
    return {
        'application': application,
        'total_violations': len(violations),
        'violations': violations,
    }


# ---------------------------------------------------------------------------
# Freeze calendar & release readiness tools
# ---------------------------------------------------------------------------

def _db_connection():
    from repositories.connection import DatabaseConnection
    from config import CollectorConfig
    cfg = CollectorConfig.from_env()
    return DatabaseConnection(cfg.db)


@mcp.tool()
@async_tool
def list_freezes() -> List[Dict[str, Any]]:
    """List all pipeline freeze windows.

    Returns scheduled freeze periods with status (active/upcoming/past).
    """
    from datetime import date
    db = _db_connection()
    today = date.today()
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, start_date, end_date, reason FROM release_freezes ORDER BY start_date"
        )
        results = []
        for row in cursor.fetchall():
            fid, start, end, reason = row
            if today > end:
                status = "past"
            elif today < start:
                status = "upcoming"
            else:
                status = "active"
            results.append({
                'id': fid,
                'start_date': str(start),
                'end_date': str(end),
                'reason': reason,
                'status': status,
            })
        return results


@mcp.tool()
@async_tool
def get_active_freeze() -> Optional[Dict[str, Any]]:
    """Get the currently active pipeline freeze, if any.

    Returns None if no freeze is active today.
    """
    db = _db_connection()
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, start_date, end_date, reason FROM release_freezes "
            "WHERE CURRENT_DATE BETWEEN start_date AND end_date LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            'id': row[0],
            'start_date': str(row[1]),
            'end_date': str(row[2]),
            'reason': row[3],
            'status': 'active',
        }


@mcp.tool()
@async_tool
def get_release_readiness(
    application: str = DEFAULT_APPLICATION,
) -> Dict[str, Any]:
    """Get release readiness assessment: build health, conforma, freeze status.

    Combines build failures, conforma violations, and freeze calendar
    into a single readiness verdict (READY / AT_RISK / NOT_READY).

    Args:
        application: Which version to query
    """
    build_repo = _build_repo()
    conforma_repo = _conforma_repo()

    failing = build_repo.find_failing_component_names(application) or set()
    fail_count = len(failing)

    unresolved_conforma = conforma_repo.find_unresolved_component_names(application)
    conforma_count = len(unresolved_conforma)

    freeze = get_active_freeze.__wrapped__()

    blockers = []
    risks = []

    if conforma_count > 0:
        blockers.append(f"{conforma_count} component(s) with unexcepted conforma violations")
    if freeze:
        blockers.append(f"Pipeline frozen until {freeze['end_date']} ({freeze['reason']})")
    if fail_count > 0:
        risks.append(f"{fail_count} component(s) with failing builds")

    if blockers:
        verdict = "NOT_READY"
    elif risks:
        verdict = "AT_RISK"
    else:
        verdict = "READY"

    schedule = get_release_schedule.__wrapped__(application)

    return {
        'application': application,
        'verdict': verdict,
        'build_failures': fail_count,
        'conforma_violations': conforma_count,
        'freeze': freeze,
        'blockers': blockers,
        'risks': risks,
        'schedule': schedule,
    }


@mcp.tool()
@async_tool
def get_release_schedule(
    application: str = DEFAULT_APPLICATION,
) -> Optional[Dict[str, Any]]:
    """Get release milestone dates from Smartsheet cache.

    Returns all milestone dates: planning freeze, feature freeze, code freeze,
    initial RC, release window, release date, and next release version.

    Args:
        application: Which version to query
    """
    db = _db_connection()
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT planning_freeze, feature_freeze, code_freeze, initial_rc, "
            "release_window_start, release_date, next_release, updated_at "
            "FROM release_schedule WHERE application = %s",
            (application,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        from datetime import date as date_type
        today = date_type.today()
        fields = ['planning_freeze', 'feature_freeze', 'code_freeze',
                  'initial_rc', 'release_window_start', 'release_date']
        result = {'application': application}
        for i, f in enumerate(fields):
            result[f] = str(row[i]) if row[i] else None
            if row[i] and hasattr(row[i], 'year'):
                result[f'{f}_days'] = (row[i] - today).days
        result['next_release'] = row[6] if row[6] else None
        result['updated_at'] = str(row[7]) if row[7] else None
        return result


# ---------------------------------------------------------------------------
# Export tools (formatting logic, backed by query tools above)
# ---------------------------------------------------------------------------

@mcp.tool()
@async_tool
def export_jira(
    component: str,
    application: str = DEFAULT_APPLICATION,
) -> str:
    """Export failure or violation as Jira ticket markup.

    Args:
        component: Component name
        application: Which version to query
    """
    failure = _build_repo().get_failure_details(component, application)
    if failure:
        analysis = _ai_repo().get_analysis_by_component(component, application, 'build')
        return _format_build_jira(failure, analysis)

    violation = _conforma_repo().get_violation_details(component, application)
    if violation:
        analysis = _ai_repo().get_analysis_by_component(component, application, 'conforma')
        return _format_conforma_jira(violation, analysis)

    return f"No unresolved failures found for component: {component} in {application}"


@mcp.tool()
@async_tool
def export_markdown(
    component: str,
    application: str = DEFAULT_APPLICATION,
) -> str:
    """Export failure as GitHub-flavored Markdown.

    Args:
        component: Component name
        application: Which version to query
    """
    failure = _build_repo().get_failure_details(component, application)
    analysis_row = _ai_repo().get_analysis_by_component(component, application)
    ai = _row_to_analysis(analysis_row) if analysis_row else None

    if failure:
        konflux = failure.get('konflux_url') or _konflux_url(failure.get('pipelinerun_name', ''))
        lines = [f"## Build Failure: {failure['component_name']}"]
        lines.append(f"\n**PipelineRun:** `{failure.get('pipelinerun_name')}`")
        lines.append(f"**Error:** {failure.get('error_message')}")
        if failure.get('failed_step_name'):
            lines.append(f"**Failed Step:** {failure['failed_step_name']}")
        lines.append(f"**First Seen:** {failure.get('first_detected_at')}")
        if failure.get('repository_url'):
            lines.append(f"**Repository:** [{failure['repository_url']}]({failure['repository_url']})")
        if failure.get('commit_sha') and failure.get('commit_url'):
            lines.append(f"**Commit:** [{failure['commit_sha'][:8]}]({failure['commit_url']})")
        lines.append(f"**Konflux:** [{konflux}]({konflux})")
        if ai:
            lines.append(f"\n### AI Analysis ({int(ai.confidence_score * 100)}% confidence)")
            lines.append(f"\n**Category:** {ai.failure_category}")
            lines.append(f"**Can Auto-Fix:** {'Yes' if ai.can_auto_fix else 'No'}")
            lines.append(f"\n**Root Cause:** {ai.root_cause}")
            lines.append(f"\n**Recommended Fix:** {ai.recommended_fix}")
            if ai.recommended_files:
                lines.append("\n**Files:** " + ", ".join(f"`{f}`" for f in ai.recommended_files))
        return "\n".join(lines)

    violation = _conforma_repo().get_violation_details(component, application)
    if violation:
        lines = [f"## Conforma Violation: {violation['component_name']}"]
        lines.append(f"\n**Scenario:** {violation['scenario']}")
        lines.append(f"**Violations:** {violation['violations_count']} violations, {violation['warnings_count']} warnings")
        lines.append(f"**First Seen:** {violation.get('first_detected_at')}")
        if ai:
            lines.append(f"\n### AI Analysis ({int(ai.confidence_score * 100)}% confidence)")
            lines.append(f"\n**Category:** {ai.failure_category}")
            lines.append(f"**Root Cause:** {ai.root_cause}")
            lines.append(f"\n**Recommended Fix:** {ai.recommended_fix}")
        return "\n".join(lines)

    return f"No unresolved failures found for {component} in {application}"


@mcp.tool()
@async_tool
def export_json(
    component: str,
    application: str = DEFAULT_APPLICATION,
    include_logs: bool = False,
) -> str:
    """Export failure as JSON for automation pipelines.

    Args:
        component: Component name
        application: Which version to query
        include_logs: Include build_logs (can be very large)
    """
    result: Dict[str, Any] = {"component": component, "application": application}

    failure = _build_repo().get_failure_details(component, application)
    if failure:
        if not include_logs:
            failure.pop('build_logs', None)
        failure.pop('commit_context', None)
        result["type"] = "build_failure"
        result["failure"] = failure
    else:
        violation = _conforma_repo().get_violation_details(component, application)
        if violation:
            result["type"] = "conforma_violation"
            result["violation"] = violation
        else:
            result["type"] = "not_found"
            return json.dumps(result, indent=2, default=str)

    analysis_row = _ai_repo().get_analysis_by_component(component, application)
    if analysis_row:
        result["analysis"] = analysis_row

    return json.dumps(result, indent=2, default=str)


@mcp.tool()
@async_tool
def export_slack(
    component: str,
    application: str = DEFAULT_APPLICATION,
) -> str:
    """Export failure as Slack message (mrkdwn format).

    Args:
        component: Component name
        application: Which version to query
    """
    failure = _build_repo().get_failure_details(component, application)
    analysis_row = _ai_repo().get_analysis_by_component(component, application)
    ai = _row_to_analysis(analysis_row) if analysis_row else None

    if failure:
        konflux = failure.get('konflux_url') or _konflux_url(failure.get('pipelinerun_name', ''))
        text = f":red_circle: *Build Failure: {failure['component_name']}*\n"
        text += f"PipelineRun: `{failure.get('pipelinerun_name')}`\n"
        text += f"Error: {failure.get('error_message')}\n"
        if failure.get('failed_step_name'):
            text += f"Failed Step: `{failure['failed_step_name']}`\n"
        if failure.get('commit_sha') and failure.get('commit_url'):
            text += f"Commit: <{failure['commit_url']}|{failure['commit_sha'][:8]}> by {failure.get('commit_author')}\n"

        if failure.get('repository_url'):
            repo_name = failure['repository_url'].rstrip('/').split('/')[-1]
            text += f"Repo: <{failure['repository_url']}|{repo_name}>"
            if failure.get('branch'):
                text += f" (`{failure['branch']}`)"
            text += "\n"

        first_detected = failure.get('first_detected_at')
        if first_detected:
            from datetime import datetime, timezone
            first = first_detected
            if hasattr(first, 'tzinfo') and (first.tzinfo is None):
                first = first.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days = (now - first).days
            if days == 0:
                text += "Failing since: today\n"
            elif days == 1:
                text += "Failing since: yesterday\n"
            else:
                text += "Failing since: {} ({} days)\n".format(first.strftime('%b %d'), days)

        open_prs = []
        if failure.get('repository_url'):
            try:
                import os
                from clients.github_client import GitHubClient, parse_github_repo
                parsed = parse_github_repo(failure['repository_url'])
                token = os.environ.get('GITHUB_TOKEN', '')
                if parsed and token:
                    owner, repo = parsed
                    gh = GitHubClient(token)
                    open_prs = gh.list_pull_requests(owner, repo, base=failure.get('branch'),
                                                    state='open', limit=3)
            except Exception:
                pass
        if open_prs:
            text += "\n:mag: *Open PRs:*\n"
            for p in open_prs:
                title = p['title'][:50] + ('...' if len(p['title']) > 50 else '')
                text += "  <{}|#{}> {} ({})\n".format(p['url'], p['number'], title, p['author'])

        contacts = set()
        if failure.get('commit_author'):
            contacts.add(failure['commit_author'])
        for p in open_prs:
            if p.get('author'):
                contacts.add(p['author'])
        if contacts:
            text += ":bust_in_silhouette: Contacts: {}\n".format(', '.join(sorted(contacts)))

        if ai:
            text += f"\n:robot_face: *AI Analysis* ({int(ai.confidence_score * 100)}% confidence)\n"
            text += f"Category: `{ai.failure_category}`\n"
            auto_fix = ":white_check_mark:" if ai.can_auto_fix else ":x:"
            text += f"Auto-fixable: {auto_fix}\n"
            text += f"Root Cause: {ai.root_cause[:200]}\n"
        text += f"\n<{konflux}|View in Konflux>"
        return text

    violation = _conforma_repo().get_violation_details(component, application)
    if violation:
        konflux = _konflux_url(violation.get('pipelinerun_name', ''))
        text = f":warning: *Conforma Violation: {violation['component_name']}*\n"
        text += f"Scenario: {violation['scenario']}\n"
        text += f"Violations: {violation['violations_count']} | Warnings: {violation['warnings_count']}\n"
        if ai:
            text += f"\n:robot_face: *AI Analysis* ({int(ai.confidence_score * 100)}% confidence)\n"
            text += f"Category: `{ai.failure_category}`\n"
            text += f"Root Cause: {ai.root_cause[:200]}\n"
        text += f"\n<{konflux}|View in Konflux>"
        return text

    return f"No unresolved failures found for {component} in {application}"


@mcp.tool()
@async_tool
def lookup_image(image_ref: str) -> Dict[str, Any]:
    """Given a quay.io image URL or sha256 digest, find which component produced it.

    Args:
        image_ref: Image URL, sha256 digest, or partial digest
    """
    db_matches = _build_repo().find_by_image(image_ref)

    cluster_matches = []
    try:
        from clients.kubernetes import KubernetesClient
        kc = KubernetesClient(namespace=NAMESPACE)
        all_comps = kc.list_components()
        term = image_ref.lower()
        cluster_matches = [c for c in all_comps if term in (c.get('container_image') or '').lower()]
    except Exception:
        pass

    return {
        'query': image_ref,
        'db_matches': db_matches,
        'cluster_matches': cluster_matches,
        'total_matches': len(db_matches) + len(cluster_matches),
    }


@mcp.tool()
@async_tool
def get_stale_components(application: str = DEFAULT_APPLICATION) -> Dict[str, Any]:
    """Detect components where the branch has commits that never triggered a build.

    Compares each component's lastBuiltCommit (from cluster) against the GitHub
    branch HEAD. Stale components may indicate webhook failures or build misconfigs.

    Args:
        application: Which version to query
    """
    from proactive.health_monitor import HealthMonitor
    monitor = HealthMonitor(db=None)
    return monitor.get_stale_components(application)


@mcp.tool()
@async_tool
def get_nightly_status(application: str = DEFAULT_APPLICATION) -> Dict[str, Any]:
    """Get nightly build status: FBC fragment health + what's blocking it.

    Shows the FBC (Fully Bundled Catalog) fragment component status and
    lists any failing components that block the nightly build.

    Args:
        application: Which version to query
    """
    from proactive.health_monitor import HealthMonitor
    from repositories.connection import DatabaseConnection
    from config import CollectorConfig

    cfg = CollectorConfig.from_env()
    db = DatabaseConnection(cfg.db)
    monitor = HealthMonitor(db)
    return monitor.get_nightly_status(application)


@mcp.tool()
def get_nightly_history(application: str = DEFAULT_APPLICATION, days: int = 14) -> Dict[str, Any]:
    """Get nightly operator build history and FBC fragment freshness.

    Shows the last N days of nightly builds (operator builds tagged trigger_type='nightly')
    and the current freshness of FBC fragment components.
    """
    from config import CollectorConfig
    from repositories.build_failure_repository import BuildFailureRepository
    from repositories.connection import DatabaseConnection
    repo = BuildFailureRepository(DatabaseConnection(CollectorConfig.from_env().db))
    return repo.get_nightly_history(application, days=days)


# ---------------------------------------------------------------------------
# Skill registry tools (read-only)
# ---------------------------------------------------------------------------

def _skill_registry():
    from skills.db_registry import get_registry
    return get_registry()


def _entry_to_info(entry) -> SkillInfo:
    return SkillInfo(
        qualified_name=entry.qualified_name,
        name=entry.name,
        source=entry.source,
        description=entry.metadata.description,
        status=entry.status,
        tags=entry.tags,
        category=entry.metadata.category or None,
        allowed_tools=entry.metadata.allowed_tools,
        user_invocable=entry.metadata.user_invocable,
    )


@mcp.tool()
@async_tool
def list_skills(
    tag: Optional[str] = None,
    source: Optional[str] = None,
) -> List[SkillInfo]:
    """List registered skills from the skill registry.

    Skills are loaded from external Git repos (e.g., aiops-infra, ai-helpers)
    and provide automated fixes, onboarding, and utility operations.

    Args:
        tag: Filter by tag (e.g., "onboarding", "conforma")
        source: Filter by source repo name
    """
    registry = _skill_registry()
    entries = registry.list_skills(tag=tag, source=source)
    return [_entry_to_info(e) for e in entries]


@mcp.tool()
@async_tool
def get_skill_info(name: str) -> Optional[SkillInfo]:
    """Get detailed info about a registered skill.

    Accepts either a short name (e.g., "fix-hermetic") or qualified name
    (e.g., "aiops-infra/fix-hermetic"). Short names that match multiple
    skills will raise an error listing the qualified names.

    Args:
        name: Skill name or qualified name (source/name)
    """
    registry = _skill_registry()
    entry = registry.get_skill(name)
    if not entry:
        return None
    return _entry_to_info(entry)


@mcp.tool()
@async_tool
def list_skill_sources() -> List[SkillSourceInfo]:
    """List registered skill sources (Git repos providing skills).

    Shows which repos have been added and how many skills each provides.
    """
    registry = _skill_registry()
    sources = registry.list_sources()
    skills = registry.list_skills()

    source_counts = {}
    for s in skills:
        source_counts[s.source] = source_counts.get(s.source, 0) + 1

    return [
        SkillSourceInfo(
            name=src.name,
            url=src.url,
            commit=src.commit or None,
            skill_count=source_counts.get(src.name, 0),
        )
        for src in sources
    ]


@mcp.tool()
@async_tool
def validate_skill(name: str) -> SkillValidationResult:
    """Run static security analysis on a registered skill.

    Checks for hardcoded secrets, destructive operations, data exfiltration
    patterns, and unsafe shell constructs. Returns findings with file and
    line number.

    Args:
        name: Skill name or qualified name (source/name).
    """
    registry = _skill_registry()
    skill = registry.get_skill(name)
    if not skill:
        return SkillValidationResult(
            skill_name=name, passed=False, critical_count=1, warning_count=0,
            findings=[SkillValidationFinding(
                severity='critical', check='not_found',
                message='Skill not found: {}'.format(name),
                file='', line=0,
            )],
        )

    from skills.validator import SkillValidator
    validator = SkillValidator()
    result = validator.validate(skill.path, skill.metadata)

    return SkillValidationResult(
        skill_name=result.skill_name,
        passed=result.passed,
        critical_count=result.critical_count,
        warning_count=result.warning_count,
        findings=[
            SkillValidationFinding(
                severity=f.severity, check=f.check,
                message=f.message, file=f.file, line=f.line,
            )
            for f in result.findings
        ],
    )


@mcp.tool()
@async_tool
def check_skill_prerequisites(
    name: Optional[str] = None,
) -> List[SkillPrerequisiteResult]:
    """Check if required tools and env vars are available for skills.

    Without a name, checks all registered skills. With a name, checks
    only that skill.

    Args:
        name: Optional skill name. If omitted, checks all registered skills.
    """
    from skills.validator import check_prerequisites

    registry = _skill_registry()

    if name:
        skill = registry.get_skill(name)
        if not skill:
            return [SkillPrerequisiteResult(
                skill_name=name, status='fail', tools={}, env={},
            )]
        skills_to_check = [(skill.qualified_name, skill.metadata)]
    else:
        skills_to_check = [
            (s.qualified_name, s.metadata) for s in registry.list_skills()
        ]

    results = []
    for qname, metadata in skills_to_check:
        prereqs = check_prerequisites(metadata)
        results.append(SkillPrerequisiteResult(
            skill_name=qname,
            status=prereqs['status'],
            tools=prereqs['tools'],
            env=prereqs['env'],
        ))
    return results


@mcp.tool()
def run_skill(
    name: str,
    dry_run: bool = True,
    params: Optional[Dict[str, str]] = None,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Execute a registered skill.

    IMPORTANT: dry_run defaults to True. Set dry_run=False to actually execute.
    Low-risk skills (read-only) can be executed directly.
    Medium/high-risk skills should be previewed first with dry_run=True.

    Args:
        name: Skill name or qualified name (e.g. 'validate-component-onboarding-jira')
        dry_run: If True, show steps without executing. Default True for safety.
        params: Key-value parameters passed as environment variables to the skill.
        timeout: Max seconds per step (default 300).

    Returns:
        Execution result with status, output, risk assessment, and steps.
    """
    registry = _skill_registry()
    entry = registry.get_skill(name)
    if not entry:
        for s in registry.list_skills():
            if s.name == name:
                entry = s
                break
    if not entry:
        return {'error': 'Skill not found: {}'.format(name)}

    from skills.executor import SkillExecutor
    executor = SkillExecutor(entry, params=params or {}, dry_run=dry_run, timeout=timeout)
    assessment = executor.assess()
    result = executor.execute()

    try:
        registry.record_run(result)
    except Exception:
        pass

    return {
        **result.to_dict(),
        'risk_reasons': assessment.reasons,
        'security_warnings': assessment.security_warnings,
    }


# ---------------------------------------------------------------------------
# Jira formatting helpers
# ---------------------------------------------------------------------------

def _format_build_jira(failure: Dict[str, Any], analysis: Optional[Dict[str, Any]]) -> str:
    comp = failure['component_name']
    pr_name = failure.get('pipelinerun_name', '')
    konflux = failure.get('konflux_url') or _konflux_url(pr_name)
    ai = _row_to_analysis(analysis) if analysis else None

    lines = [f"h2. Build Failure: {comp}", ""]
    lines.append("||Field||Value||")
    lines.append(f"|Component|{comp}|")
    lines.append("|Status|Failed|")
    lines.append(f"|PipelineRun|[{pr_name}|{konflux}]|")
    lines.append(f"|First Seen|{failure.get('first_detected_at')}|")
    lines.append(f"|Last Seen|{failure.get('last_updated_at')}|")
    lines.append("")

    if ai:
        confidence = int(ai.confidence_score * 100)
        lines.append(f"h3. AI Analysis ({confidence}% confidence)")
        lines.append("")
        lines.append(f"||Category||{ai.failure_category}||")
        lines.append(f"|Can Auto-Fix|{'Yes' if ai.can_auto_fix else 'No'}|")
        lines.append(f"|Requires Review|{'Yes' if ai.requires_human_review else 'No'}|")
        lines.append("")
        lines.append("h4. Root Cause")
        lines.append("")
        lines.append(ai.root_cause or "")
        lines.append("")
        lines.append("h4. Recommended Fix")
        lines.append("")
        lines.append(ai.recommended_fix or "")
        lines.append("")
        if ai.recommended_files:
            lines.append(f"*Files to modify:* {', '.join(ai.recommended_files)}")
            lines.append("")

    lines.append("h3. Context")
    lines.append("")
    if failure.get('repository_url'):
        repo_name = failure['repository_url'].rstrip("/").split("/")[-1]
        lines.append(f"*Repository:* [{repo_name}|{failure['repository_url']}]")
    lines.append(f"*Branch:* {failure.get('branch')}")
    if failure.get('commit_sha') and failure.get('commit_url'):
        lines.append(f"*Commit:* [{failure['commit_sha'][:8]}|{failure['commit_url']}]")
    if failure.get('commit_message'):
        lines.append(f"*Commit Message:* {failure['commit_message']}")
    lines.append("")
    if failure.get('failed_step_name'):
        lines.append(f"*Failed Step:* {failure['failed_step_name']}")
    if failure.get('error_type'):
        lines.append(f"*Error Type:* {failure['error_type']}")
    if failure.get('error_message'):
        lines.append("*Error Message:*")
        lines.append("{code}")
        lines.append(str(failure['error_message']))
        lines.append("{code}")
    lines.append("")
    lines.append("h3. Resources")
    lines.append("")
    lines.append(f"* [Full Build Logs|{konflux}]")
    if ai and ai.langfuse_trace_url:
        lines.append(f"* [Analysis Trace|{ai.langfuse_trace_url}]")

    return "\n".join(lines)


def _format_conforma_jira(violation: Dict[str, Any], analysis: Optional[Dict[str, Any]]) -> str:
    comp = violation['component_name']
    pr_name = violation.get('pipelinerun_name', '')
    konflux = _konflux_url(pr_name)
    ai = _row_to_analysis(analysis) if analysis else None

    lines = [f"h2. Conforma Policy Violation: {comp}", ""]
    lines.append("||Field||Value||")
    lines.append(f"|Component|{comp}|")
    lines.append("|Status|Policy Violation|")
    lines.append(f"|Scenario|{violation['scenario']}|")
    lines.append(f"|Violations|{violation['violations_count']} violations, {violation['warnings_count']} warnings|")
    lines.append(f"|PipelineRun|[{pr_name}|{konflux}]|")
    if violation.get('snapshot_name'):
        lines.append(f"|Snapshot|{violation['snapshot_name']}|")
    lines.append(f"|First Seen|{violation.get('first_detected_at')}|")
    lines.append(f"|Last Seen|{violation.get('last_updated_at')}|")
    lines.append("")

    if ai:
        confidence = int(ai.confidence_score * 100)
        lines.append(f"h3. AI Analysis ({confidence}% confidence)")
        lines.append("")
        lines.append(f"||Category||{ai.failure_category}||")
        lines.append(f"|Can Auto-Fix|{'Yes' if ai.can_auto_fix else 'No'}|")
        lines.append(f"|Requires Review|{'Yes' if ai.requires_human_review else 'No'}|")
        lines.append("")
        lines.append("h4. Root Cause")
        lines.append("")
        lines.append(ai.root_cause or "")
        lines.append("")
        lines.append("h4. Recommended Fix")
        lines.append("")
        lines.append(ai.recommended_fix or "")
        lines.append("")

    lines.append("h3. Resources")
    lines.append("")
    if violation.get('repository_url'):
        lines.append(f"*Repository:* {violation['repository_url']}")
    if violation.get('commit_sha') and violation.get('commit_url'):
        lines.append(f"*Commit:* [{violation['commit_sha'][:8]}|{violation['commit_url']}]")
    lines.append(f"* [Konflux PipelineRun|{konflux}]")
    if ai and ai.langfuse_trace_url:
        lines.append(f"* [Analysis Trace|{ai.langfuse_trace_url}]")

    return "\n".join(lines)
