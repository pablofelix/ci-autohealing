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
    StatsResponse,
    TriageResponse,
)

from cli.config import APPLICATION_NAME, KONFLUX_UI_BASE, NAMESPACE

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
    build_failures = []
    for comp in triage.get('failing_components', []):
        build_failures.append(FailureSummary(
            component=comp['component'],
            status=comp.get('status', 'Failed'),
            error_type=comp.get('error_type'),
            first_seen=comp.get('first_detected_at', datetime.utcnow()),
            last_seen=comp.get('last_updated_at', datetime.utcnow()),
            occurrence_count=comp.get('failure_count', 1),
            has_logs=comp.get('has_logs', False),
            has_context=comp.get('has_context', False),
            has_analysis=comp.get('ai_analyzed', False),
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
    logs = row.get('build_logs') if include_logs else None
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
def get_working(application: str = DEFAULT_APPLICATION) -> List[Dict[str, Any]]:
    """Get components currently working (last build = success).

    Args:
        application: Which version to query
    """
    return _build_repo().get_working_components(application)


@mcp.tool()
@async_tool
def get_resolved(application: str = DEFAULT_APPLICATION) -> List[Dict[str, Any]]:
    """Get components that were resolved (previously failing, now fixed).

    Args:
        application: Which version to query
    """
    return _build_repo().get_resolved_components(application)


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
    for check in checks:
        for item in check.get('items', []):
            warnings.append(HealthWarning(
                type=check.get('check', 'unknown'),
                component=item.get('component', ''),
                message=item.get('message', str(item)),
                severity=check.get('severity', 'warning'),
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
