"""MCP tools for Konflux CI monitoring.

All tools are read-only queries backed by repository methods.
Each tool accepts an `application` parameter for multi-version support.
"""

import asyncio
import json
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Callable, Dict, List, Literal, Optional, TypeVar

from mcp_server import mcp
from mcp_server.models import (
    AlertsSummary,
    AnalysisDetails,
    ApplicationInfo,
    BlockersSummary,
    BouncingIssuesSummary,
    BuildFailureDetails,
    ComponentHistoryResponse,
    ConformaViolationDetails,
    DashboardResponse,
    ExceptionLifecycleSummary,
    FailureSummary,
    FBCFragmentHistory,
    FixPropagationSummary,
    HealthWarning,
    JiraTokenHealth,
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

    from api.routes.failures import _compute_age_signals

    build_failures = []
    for comp in triage.get('failing_components', []):
        component_name = comp['component']
        cause = None
        if component_name in dep_updates:
            cause = 'dependency_update: {}'.format(dep_updates[component_name])

        b_fs = comp.get('first_detected_at', datetime.utcnow())
        b_ls = comp.get('last_updated_at', datetime.utcnow())
        b_age, b_new, b_chg = _compute_age_signals(b_fs, b_ls)
        build_failures.append(FailureSummary(
            component=component_name,
            status=comp.get('status', 'Failed'),
            error_type=comp.get('error_type'),
            first_seen=b_fs,
            last_seen=b_ls,
            occurrence_count=comp.get('failure_count', 1),
            has_logs=comp.get('has_logs', False),
            has_context=comp.get('has_context', False),
            has_analysis=comp.get('ai_analyzed', False),
            possible_cause=cause,
            age_hours=b_age,
            is_new=b_new,
            status_changed=b_chg,
        ))

    from api.routes.failures import _detect_systemic_patterns
    _detect_systemic_patterns(build_failures)

    conforma_violations = []
    triage_jira = _triage_repo().build_jira_map(application)
    from conforma.policy_tools import (
        categorize_policy,
        compute_exception_coverage_details,
        count_unique_violations,
        extract_violation_rules,
        fetch_exceptions_by_policy,
    )
    from conforma.policy_tools import (
        policy_url as _policy_url,
    )
    exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    summaries = _conforma_repo().get_violation_summaries(application)
    for s in summaries:
        comp_name = s['component_name']
        scenario = s.get('scenario', '')
        jira_key = s.get('jira_key') or triage_jira.get(comp_name)
        rules = extract_violation_rules(s.get('violation_summary', ''))
        cov = compute_exception_coverage_details(
            rules, scenario, exceptions_by_policy)
        uv_count, _ = count_unique_violations(s.get('violation_summary', ''))
        c_fs = s.get('first_detected_at', datetime.utcnow())
        c_ls = s.get('last_updated_at', datetime.utcnow())
        c_age, c_new, c_chg = _compute_age_signals(c_fs, c_ls)
        conforma_violations.append(FailureSummary(
            component=comp_name,
            status='Conforma Violation',
            error_type=scenario,
            first_seen=c_fs,
            last_seen=c_ls,
            occurrence_count=1,
            age_hours=c_age,
            is_new=c_new,
            status_changed=c_chg,
            has_logs=s.get('violation_summary') is not None,
            has_analysis=s.get('ai_analyzed', False),
            violations_count=s.get('violations_count', 0),
            unique_violations=uv_count or None,
            warnings_count=s.get('warnings_count', 0),
            scenario=scenario,
            category=categorize_policy(scenario),
            policy_url=_policy_url(scenario),
            jira_key=jira_key,
            exception_coverage=cov['coverage'],
            exception_coverage_stage=cov['stage'],
            exception_coverage_prod=cov['prod'],
            exception_env_tag=cov['env_tag'],
            policy_url_stage=cov['policy_url_stage'],
            policy_url_prod=cov['policy_url_prod'],
            uncovered_rules_stage=cov['uncovered_rules_stage'],
            uncovered_rules_prod=cov['uncovered_rules_prod'],
        ))

    all_dates = ([f.last_seen for f in build_failures] +
                 [v.last_seen for v in conforma_violations])
    last_sync = max(all_dates) if all_dates else datetime.utcnow()

    schedule = None
    freeze_countdown = None
    try:
        from api.routes.failures import _compute_freeze_countdown
        from api.routes.releases import get_schedule
        schedule = get_schedule(application)
        freeze_countdown = _compute_freeze_countdown(schedule)
    except Exception:
        pass

    return AlertsSummary(
        application=application,
        build_failures=build_failures,
        conforma_violations=conforma_violations,
        total_count=len(build_failures) + len(conforma_violations),
        last_sync=last_sync,
        release_schedule=schedule,
        freeze_countdown=freeze_countdown,
    )


@mcp.tool()
@async_tool
def check_jira_health() -> "JiraTokenHealth":
    """Check if the Jira API token is valid and working.

    Returns status (valid, expired, forbidden, unreachable, missing),
    the authenticated user name, and a human-readable message.
    Call this before relying on Jira data — an expired token silently
    returns empty results (e.g., "0 blockers" when 50 exist).
    """
    from api.routes.failures import check_jira_health as _check
    return _check()


@mcp.tool()
@async_tool
def get_exception_lifecycle() -> "ExceptionLifecycleSummary":
    """Track EC (Enterprise Contract) policy exception lifecycle.

    Returns all exceptions with status: active, expiring_soon (<7 days), or
    expired. Helps identify exceptions needing cleanup (expired) or urgent
    root-cause fixes (expiring soon). Never default to creating new exceptions —
    always fix the root cause first.
    """
    from api.routes.violations import get_exception_lifecycle as _get
    return _get()


@mcp.tool()
@async_tool
def get_fbc_history(
    application: str = DEFAULT_APPLICATION,
    limit: int = 20,
) -> "FBCFragmentHistory":
    """Track FBC (File-Based Catalog) fragment image SHAs over time.

    Returns the build history for the FBC fragment component with image
    digests. Helps trace which FBC SHA is current and map test runs to
    specific builds — critical during RC testing when multiple SHAs may
    be produced in a single day.

    Args:
        application: Which version to query.
        limit: Max builds to return (default 20).
    """
    from api.routes.releases import get_fbc_history as _get
    return _get(application, limit=limit)


@mcp.tool()
@async_tool
def check_fix_propagation(
    application: str = DEFAULT_APPLICATION,
    days: int = 14,
    target_branch: str = 'main',
) -> "FixPropagationSummary":
    """Check if resolved build failure fixes have propagated to the main branch.

    Looks at recently resolved failures and verifies their fix commits exist
    on the target branch. Flags fixes only on the release branch that haven't
    been backported — a common source of regressions across releases.

    Args:
        application: Which version to query.
        days: How many days back to check (default 14).
        target_branch: Branch to verify fix exists on (default "main").
    """
    from api.routes.failures import check_fix_propagation as _check
    return _check(application, days=days, target_branch=target_branch)


@mcp.tool()
@async_tool
def list_blockers(application: str = DEFAULT_APPLICATION) -> "BlockersSummary":
    """Get Jira blocker analysis: age, assignment, staleness signals.

    Returns blockers with health signals: unassigned >24h, no updates >48h,
    resolved-but-not-closed. Critical signals are highlighted for AI triage.

    Args:
        application: Which version to query. Use list_applications() to discover versions.
    """
    from api.routes.failures import list_blockers as _list_blockers
    return _list_blockers(application)


@mcp.tool()
@async_tool
def list_bouncing_issues(
    application: str = DEFAULT_APPLICATION,
    min_bounces: int = 2,
) -> "BouncingIssuesSummary":
    """Detect Jira issues that keep bouncing between teams without resolution.

    Analyzes Jira changelog for component/assignee reassignments. Issues with
    >= min_bounces changes are flagged — these need escalation or root cause triage.

    Args:
        application: Which version to query.
        min_bounces: Minimum reassignment count to flag (default 2).
    """
    from api.routes.failures import list_bouncing_issues as _list_bouncing
    return _list_bouncing(application, min_bounces=min_bounces)


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
        from log_filter import filter_error_lines
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
    from conforma.policy_tools import (
        compute_violation_coverage,
        count_unique_violations,
        extract_policy_from_scenario,
        extract_violation_rules,
        fetch_exceptions_by_policy,
        lookup_exceptions,
        policy_url,
    )
    row = _conforma_repo().get_violation_details(component, application)
    if not row:
        return None

    konflux = _konflux_url(row.get('pipelinerun_name', ''))
    details = row.get('violation_details') if include_details else None
    scenario = row['scenario']
    policy_name = extract_policy_from_scenario(scenario)

    cov_fields = {}
    exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    if exceptions_by_policy:
        rules = extract_violation_rules(row.get('violation_summary', ''))
        cov = compute_violation_coverage(
            rules, lookup_exceptions(policy_name, scenario, exceptions_by_policy))
        if cov:
            cov_fields = {
                'exception_coverage': cov['coverage'],
                'covered_rules': cov['covered_rules'],
                'uncovered_rules': cov['uncovered_rules'],
                'matching_exceptions': cov['matching_exceptions'],
            }

    uv_count, uv_rules = count_unique_violations(row.get('violation_summary', ''))
    vr_list = [
        r['rule'] + (':' + r['detail'] if r['detail'] else '')
        for r in uv_rules
    ]

    return ConformaViolationDetails(
        component=row['component_name'],
        scenario=scenario,
        policy_name=policy_name,
        policy_url=policy_url(scenario),
        violations_count=row['violations_count'],
        unique_violations=uv_count or None,
        violation_rules=vr_list or None,
        warnings_count=row['warnings_count'],
        successes_count=row['successes_count'],
        violation_summary=row.get('violation_summary', ''),
        violation_details=details,
        repository_url=row.get('repository_url'),
        commit_sha=row.get('commit_sha'),
        snapshot_name=row.get('snapshot_name'),
        konflux_url=konflux,
        first_detected_at=row.get('first_detected_at', datetime.utcnow()),
        **cov_fields,
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


def _extract_from_analysis_json(analysis_json):
    """Extract evidence_references, source_transparency, and fix_action_type from analysis_json JSONB."""
    evidence = []
    transparency = None
    fix_action_type = None
    if not analysis_json:
        return evidence, transparency, fix_action_type
    try:
        data = analysis_json if isinstance(analysis_json, (list, dict)) else json.loads(analysis_json)
        if isinstance(data, dict) and 'tool_calls' in data:
            items = data['tool_calls']
        elif isinstance(data, list):
            items = data
        else:
            items = [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            inp = item.get('input', item)
            for ref in inp.get('evidence_references', []):
                if isinstance(ref, dict):
                    evidence.append(ref)
            st = inp.get('source_transparency')
            if isinstance(st, dict):
                transparency = st
            fat = inp.get('fix_action_type')
            if fat and isinstance(fat, str):
                fix_action_type = fat
            if evidence or transparency or fix_action_type:
                break
    except Exception:
        pass
    return evidence, transparency, fix_action_type


def _row_to_analysis(row: Dict[str, Any]) -> AnalysisDetails:
    files = row.get('recommended_files') or []
    if isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]
    evidence, transparency, fix_action_type = _extract_from_analysis_json(row.get('analysis_json'))
    return AnalysisDetails(
        type=row['analysis_type'],
        component=row['component_name'],
        model_used=row.get('model_used', ''),
        root_cause=row.get('root_cause', ''),
        failure_category=row.get('failure_category', ''),
        confidence_score=float(row.get('confidence_score') or 0),
        recommended_fix=row.get('recommended_fix', ''),
        recommended_files=files,
        fix_action_type=fix_action_type,
        can_auto_fix=bool(row.get('can_auto_fix')),
        requires_human_review=bool(row.get('requires_human_review')),
        analyzed_at=row.get('analyzed_at', datetime.utcnow()),
        langfuse_trace_url=row.get('langfuse_trace_url'),
        tokens_used=row.get('tokens_used') or 0,
        cost_usd=float(row.get('cost_usd') or 0),
        evidence_references=evidence,
        source_transparency=transparency,
    )


@mcp.tool()
def submit_analysis(
    component: str,
    root_cause: str,
    failure_category: str,
    confidence_score: float,
    recommended_fix: str,
    application: str = DEFAULT_APPLICATION,
    analysis_type: str = 'build',
    model_used: str = 'claude',
    recommended_files: Optional[List[str]] = None,
    can_auto_fix: bool = False,
    tokens_used: int = 0,
    cost_usd: float = 0.0,
) -> Dict[str, Any]:
    """Submit an AI analysis result for a component failure.

    Use this to store analysis results when the LLM runs externally
    (e.g., via Claude Code) rather than on the server. The result is
    stored in the same database as server-side analyses.

    Args:
        component: Component name being analyzed
        root_cause: Description of the root cause
        failure_category: Category (dependency_issue, build_error, etc.)
        confidence_score: 0.0-1.0 confidence in the diagnosis
        recommended_fix: Suggested fix description
        application: Application name
        analysis_type: 'build' or 'conforma'
        model_used: Model identifier
        recommended_files: Files that need changes
        can_auto_fix: Whether this can be auto-fixed
        tokens_used: Tokens consumed
        cost_usd: Cost of the analysis
    """
    repo = _ai_repo()
    build_repo = _build_repo()

    failure = build_repo.get_failure_details(component, application)
    if not failure:
        return {"error": "Component not found: {}".format(component)}

    failure_id = failure.get('id')
    if not failure_id:
        return {"error": "Failure has no ID"}

    analysis_id = repo.insert_analysis(
        build_failure_id=failure_id if analysis_type == 'build' else None,
        conforma_result_id=failure_id if analysis_type == 'conforma' else None,
        model_used=model_used,
        root_cause=root_cause,
        failure_category=failure_category,
        confidence_score=confidence_score,
        recommended_fix=recommended_fix,
        recommended_files=recommended_files or [],
        can_auto_fix=can_auto_fix,
        requires_human_review=not can_auto_fix,
        tokens_used=tokens_used,
        cost_usd=cost_usd,
        analysis_duration=0,
        analysis_json=None,
    )

    return {
        "action": "stored",
        "analysis_id": analysis_id,
        "component": component,
        "confidence": confidence_score,
    }


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
def resolve_triage_item(
    item_id: int,
    resolution: str = '',
    pr_url: str = '',
    verdict: str = '',
    application: str = DEFAULT_APPLICATION,
) -> Dict[str, Any]:
    """Resolve a triage item (mark investigation as complete).

    Args:
        item_id: Triage item ID to resolve
        resolution: Resolution description (e.g. "PR merged", "config fix applied")
        pr_url: URL of the fix PR (optional)
        verdict: AI diagnosis accuracy — correct, partial, or incorrect (optional)
        application: Application name
    """
    repo = _triage_repo()
    repo.resolve_item(item_id, resolution=resolution, pr_url=pr_url)

    if verdict and verdict in ('correct', 'partial', 'incorrect'):
        item = repo.get_by_id(item_id)
        if item and item.get('components'):
            from repositories.ai_analysis_repository import AIAnalysisRepository
            ai_repo = AIAnalysisRepository(_db_connection())
            ai_repo.record_verdict(item['components'][0], application, verdict, verdict_by='mcp')

    return {"action": "resolved", "item_id": item_id, "resolution": resolution}


@mcp.tool()
def get_conforma_categories(
    application: str = DEFAULT_APPLICATION,
    reporter_env: Optional[str] = None,
    reporter_build_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Get violation categories with counts across all failing components.

    Returns a summary of which Conforma policy rules are failing and how many
    components are affected by each. Includes high-level grouping by artifact
    type (FBC, Components, Charts) matching the conforma-reporter Slack format.

    Pass reporter_env/reporter_build_type to fetch from the conforma-reporter
    instead of cluster data.

    Args:
        application: Which version to query
        reporter_env: 'stage' or 'prod' — fetch from reporter
        reporter_build_type: 'latest', 'nightly', or 'release_day'
    """
    if reporter_env or reporter_build_type:
        from cli.config import app_to_reporter_branch
        from clients.conforma_reporter_client import fetch_reporter_violations
        branch = app_to_reporter_branch(application)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        data = fetch_reporter_violations(branch, env=env, build_type=build_type)
        groups = {}
        for v in data:
            cat = 'Components'
            if cat not in groups:
                groups[cat] = {'components': 0, 'violations': 0}
            groups[cat]['components'] += 1
            groups[cat]['violations'] += v.get('unique_violations', 0)
        for cat in ('FBC', 'Components', 'Charts'):
            groups.setdefault(cat, {'components': 0, 'violations': 0})
        return {
            'application': application,
            'source': '{}/{}'.format(env, build_type),
            'groups': groups,
            'categories': {},
        }
    from conforma.policy_tools import (
        categorize_policy,
        compute_violation_coverage,
        count_unique_violations,
        extract_policy_from_scenario,
        extract_violation_rules,
        fetch_exceptions_by_policy,
        lookup_exceptions,
    )
    from repositories.conforma_repository import ConformaRepository
    repo = ConformaRepository(_db_connection())
    exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    comps = repo.find_unresolved_component_names(application)
    categories = {}
    groups = {}
    for comp in comps:
        details = repo.get_violation_details(comp, application)
        if details:
            scenario = details.get('scenario', 'unknown')
            if scenario not in categories:
                categories[scenario] = {
                    'count': 0,
                    'components': [],
                    'total_violations': 0,
                    'total_image_violations': 0,
                    'policy_name': extract_policy_from_scenario(scenario),
                    'category': categorize_policy(scenario),
                    'covered_count': 0,
                    'uncovered_count': 0,
                }
            categories[scenario]['count'] += 1
            categories[scenario]['components'].append(comp)
            uv_count, _ = count_unique_violations(details.get('violation_summary', ''))
            categories[scenario]['total_violations'] += uv_count
            categories[scenario]['total_image_violations'] += details.get('violations_count', 0)
            if exceptions_by_policy:
                policy_name = categories[scenario]['policy_name']
                rules = extract_violation_rules(details.get('violation_summary', ''))
                cov = compute_violation_coverage(
                    rules, lookup_exceptions(policy_name, scenario, exceptions_by_policy))
                if cov and cov['coverage'] == 'fully_covered':
                    categories[scenario]['covered_count'] += 1
                elif cov:
                    categories[scenario]['uncovered_count'] += 1
            cat = categorize_policy(scenario)
            if cat not in groups:
                groups[cat] = {
                    'components': 0,
                    'unique_violations': 0,
                    'image_violations': 0,
                }
            groups[cat]['components'] += 1
            groups[cat]['unique_violations'] += uv_count
            groups[cat]['image_violations'] += details.get('violations_count', 0)
    for cat in ('FBC', 'Components', 'Charts'):
        groups.setdefault(cat, {
            'components': 0,
            'unique_violations': 0,
            'image_violations': 0,
        })
    return {
        "application": application,
        "groups": groups,
        "categories": categories,
    }


@mcp.tool()
@async_tool
def get_conforma_rules(
    application: str = DEFAULT_APPLICATION,
    reporter_env: Optional[str] = None,
    reporter_build_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Get violations grouped by rule with affected components and solutions.

    Each rule entry includes the rule code, title, solution text (when available
    from conforma-reporter), the list of affected component names, and a count.
    Sorted by component count descending.

    Pass reporter_env/reporter_build_type to fetch from conforma-reporter.

    Args:
        application: Which version to query
        reporter_env: 'stage' or 'prod' — fetch from reporter
        reporter_build_type: 'latest', 'nightly', or 'release_day'
    """
    if reporter_env or reporter_build_type:
        from cli.config import app_to_reporter_branch
        from clients.conforma_reporter_client import fetch_reporter_rules
        branch = app_to_reporter_branch(application)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        rules = fetch_reporter_rules(branch, env=env, build_type=build_type)
        return {
            'application': application,
            'source': '{}/{}'.format(env, build_type),
            'total_rules': len(rules),
            'rules': rules,
        }
    from conforma.policy_tools import count_unique_violations
    from repositories.conforma_repository import ConformaRepository
    repo = ConformaRepository(_db_connection())
    violations = repo.get_violation_summaries(application)
    rules_map = {}
    for v in violations:
        comp = v.get('component_name', '')
        _, uv_rules = count_unique_violations(v.get('violation_summary', ''))
        for r in uv_rules:
            rule = r['rule']
            if rule not in rules_map:
                rules_map[rule] = {
                    'rule': rule,
                    'title': '',
                    'solution': '',
                    'components': [],
                    'violation_rows': 0,
                }
            if comp not in rules_map[rule]['components']:
                rules_map[rule]['components'].append(comp)
            rules_map[rule]['violation_rows'] += 1
    result = []
    for entry in rules_map.values():
        entry['components'] = sorted(entry['components'])
        entry['count'] = len(entry['components'])
        result.append(entry)
    result.sort(key=lambda r: (-r['count'], r['rule']))
    return {
        'application': application,
        'total_rules': len(result),
        'rules': result,
    }


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

    from clients.github_client import GitHubClient, parse_github_repo
    from clients.kubernetes import KubernetesClient
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
    from proactive.health_monitor import HealthMonitor
    from repositories.repository_factory import get_pool
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
    from proactive.health_monitor import HealthMonitor
    from repositories.repository_factory import get_pool
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
    reporter_env: Optional[str] = None,
    reporter_build_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Get Conforma violations report for an application.

    By default returns cluster data. Pass reporter_env/reporter_build_type to
    fetch from the conforma-reporter (GitHub) instead — useful for stage policy
    or nightly build violations that the cluster doesn't run.

    Args:
        application: Which version to query
        reporter_env: 'stage' or 'prod' — fetch from reporter instead of cluster
        reporter_build_type: 'latest', 'nightly', or 'release_day' — build type filter
    """
    if reporter_env or reporter_build_type:
        from cli.config import app_to_reporter_branch
        from clients.conforma_reporter_client import fetch_reporter_violations
        branch = app_to_reporter_branch(application)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        violations = fetch_reporter_violations(branch, env=env, build_type=build_type)
        source = '{}/{}'.format(env, build_type)
        return {
            'application': application,
            'source': source,
            'total_violations': len(violations),
            'violations': violations,
        }
    from conforma.policy_tools import (
        categorize_policy,
        count_unique_violations,
        enrich_with_coverage,
        extract_policy_from_scenario,
        fetch_exceptions_by_policy,
        is_wrong_policy_for_artifact,
        policy_url,
    )
    conforma_repo = _conforma_repo()
    exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    failing = conforma_repo.find_unresolved_component_names(application)
    violations = []
    total_unique = 0
    groups = {}
    for comp_name in sorted(failing):
        details = conforma_repo.get_violation_details(comp_name, application)
        if details:
            scenario = details.get('scenario', '')
            details['policy_name'] = extract_policy_from_scenario(scenario)
            details['policy_url'] = policy_url(scenario)
            details['category'] = categorize_policy(scenario)
            details['is_wrong_policy'] = is_wrong_policy_for_artifact(comp_name, scenario)
            details['is_nightly'] = details.get('trigger_type') == 'scheduled'
            uv_count, uv_rules = count_unique_violations(
                details.get('violation_summary', ''))
            details['unique_violations'] = uv_count
            details['violation_rules'] = [
                r['rule'] + (':' + r['detail'] if r['detail'] else '')
                for r in uv_rules
            ]
            total_unique += uv_count
            cat = details['category']
            if cat not in groups:
                groups[cat] = {'components': 0, 'unique_violations': 0}
            groups[cat]['components'] += 1
            groups[cat]['unique_violations'] += uv_count
            violations.append(details)
    enrich_with_coverage(violations, exceptions_by_policy)
    by_policy = {}
    coverage_summary = {'fully_covered': 0, 'partially_covered': 0, 'not_covered': 0}
    wrong_policy_components = []
    for v in violations:
        pn = v.get('policy_name', 'unknown')
        by_policy.setdefault(pn, 0)
        by_policy[pn] += 1
        cov = v.get('exception_coverage')
        if cov in coverage_summary:
            coverage_summary[cov] += 1
        if v.get('is_wrong_policy'):
            wrong_policy_components.append(v.get('component_name', ''))
    for cat in ('FBC', 'Components', 'Charts'):
        groups.setdefault(cat, {'components': 0, 'unique_violations': 0})
    result = {
        'application': application,
        'total_violations': len(violations),
        'total_unique_violations': total_unique,
        'groups': groups,
        'violations_by_policy': by_policy,
        'coverage_summary': coverage_summary if exceptions_by_policy else None,
        'violations': violations,
    }
    if wrong_policy_components:
        result['wrong_policy_note'] = (
            '{} component(s) are false positives due to a Konflux ITS scoping limitation: '
            'FBC fragments and Helm chart OCI artifacts are evaluated against the generic '
            'registry-rhoai-prod policy (context: "component") in addition to their correct '
            'component-specific ITS. The generic ITS is marked optional and does NOT block '
            'releases. Affected: {}. '
            'Fix: MR to releng/konflux-release-data tenants-config/ to scope the generic ITS, '
            'or wait for Konflux NudgeConfig cross-app support before splitting Applications.'
        ).format(len(wrong_policy_components), ', '.join(wrong_policy_components))
    return result


# ---------------------------------------------------------------------------
# Freeze calendar & release readiness tools
# ---------------------------------------------------------------------------

def _db_connection():
    from config import CollectorConfig
    from repositories.connection import DatabaseConnection
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
    full: bool = False,
) -> Dict[str, Any]:
    """Pre-release readiness checklist with infrastructure health checks.

    Returns a structured verdict (READY / AT_RISK / NOT_READY) with:
    - Core checks: build failures, conforma violations, freeze status
    - Infrastructure checks: FBC health, PCC cache, GHA nightly,
      stale components, snapshot freshness, artifact health

    Each check returns name, status (PASS/FAIL/WARN/SKIP), detail, and fix.
    FAIL = blocker, WARN = risk, PASS = ok, SKIP = inconclusive.

    Args:
        application: Which version to query
        full: Include slow checks (artifact health for all ~100 components, ~60s)
    """
    from api.routes.releases import _run_readiness_checks

    build_repo = _build_repo()
    conforma_repo = _conforma_repo()

    failing = build_repo.find_failing_component_names(application) or set()
    fail_count = len(failing)

    unresolved_conforma = conforma_repo.find_unresolved_component_names(application)
    conforma_count = len(unresolved_conforma)

    from conforma.policy_tools import (
        compute_blocks,
        compute_exception_coverage_details,
        extract_violation_rules,
        fetch_exceptions_by_policy,
    )
    summaries = conforma_repo.get_violation_summaries(application)
    exceptions_by_policy = fetch_exceptions_by_policy()
    blocking_components = set()
    for s in summaries:
        rules = extract_violation_rules(s.get('violation_summary', ''))
        cov = compute_exception_coverage_details(
            rules, s.get('scenario', ''), exceptions_by_policy)
        blocks = compute_blocks(s.get('scenario', ''), cov['stage'], cov['prod'])
        if blocks not in ('none', ''):
            blocking_components.add(s['component_name'])
    blocking_count = len(blocking_components)

    freeze = get_active_freeze.__wrapped__()

    blockers = []
    risks = []

    if blocking_count > 0:
        blockers.append(f"{blocking_count} component(s) with unexcepted conforma violations")
    if freeze:
        blockers.append(f"Pipeline frozen until {freeze['end_date']} ({freeze['reason']})")
    if fail_count > 0:
        risks.append(f"{fail_count} component(s) with failing builds")

    schedule = get_release_schedule.__wrapped__(application)

    checks = _run_readiness_checks(application, full=full)
    for check in checks:
        if check['status'] == 'FAIL':
            blockers.append(f"{check['name']}: {check['detail']}")
        elif check['status'] == 'WARN':
            risks.append(f"{check['name']}: {check['detail']}")

    if blockers:
        verdict = "NOT_READY"
    elif risks:
        verdict = "AT_RISK"
    else:
        verdict = "READY"

    return {
        'application': application,
        'verdict': verdict,
        'build_failures': fail_count,
        'conforma_violations': conforma_count,
        'conforma_blockers': blocking_count,
        'failing_components': sorted(failing),
        'conforma_components': sorted(unresolved_conforma),
        'conforma_blocking_components': sorted(blocking_components),
        'freeze': freeze,
        'blockers': blockers,
        'risks': risks,
        'schedule': schedule,
        'checks': checks,
    }


@mcp.tool()
@async_tool
def get_release_schedule(
    application: str = DEFAULT_APPLICATION,
) -> Optional[Dict[str, Any]]:
    """Get release milestone dates from Product Pages cache.

    Returns all milestone dates: planning freeze, feature freeze, code freeze,
    initial RC, release window, release date, and next release version.
    Data sourced from Product Pages (Red Hat AI product, entity rhai-3.5).

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


@mcp.tool()
def get_onboarding_status(
    application: str = DEFAULT_APPLICATION,
) -> Optional[Dict[str, Any]]:
    """Get onboarding status for all components in an application.

    Returns per-component onboarding checks: repository, branch, container
    image, PaC webhook, builds, nudges. Each component gets a score (0-100)
    and overall status (complete/partial/incomplete). Includes Jira ticket
    keys when available from triage tracking.

    Use this to check which components are fully onboarded and which need work.

    Args:
        application: Application to check (e.g. rhoai-v3-5-ea-2)
    """
    from api.routes.onboarding import get_onboarding_status as _get
    return _get(application)


@mcp.tool()
def get_onboarding_describe(
    application: str = DEFAULT_APPLICATION,
    component: str = "",
    diff: bool = False,
) -> Optional[Dict[str, Any]]:
    """Describe step-by-step onboarding progress for a specific component.

    Shows each onboarding step with status (done/pending/blocked/failed),
    dates, build history, and nudge PRs. Detects whether the component is
    ODH (upstream) or RHOAI (downstream) and uses the appropriate checklist.

    When JIRA_TOKEN is set, also shows:
    - Linked Jira tickets (RHOAI and ODH onboarding)
    - Automation step progress from Jira labels
    - PR/MR links extracted from bot comments
    - Heuristic analysis with fix suggestions (component + automation)

    Use diff=True to see expected vs actual for every step.

    Args:
        application: Application name
        component: Component name to describe
        diff: If True, include expected vs actual comparison for all steps
    """
    if not component:
        return {'error': 'component parameter is required'}
    from api.routes.onboarding import get_component_onboarding as _get
    return _get(application, component, diff=diff)


@mcp.tool()
def analyze_onboarding(
    application: str = DEFAULT_APPLICATION,
    component: str = "",
) -> Optional[Dict[str, Any]]:
    """Run AI analysis on a component's onboarding progress to diagnose blockers.

    Fetches the full onboarding report (Konflux steps, automation steps, bot
    error history, PR links, heuristic analysis) and feeds it to the LLM for
    deep diagnosis. Returns root cause, category, fix recommendation, and
    whether it can be auto-fixed.

    Requires LLM_PROVIDER + credentials to be configured.

    Args:
        application: Application name
        component: Component name to analyze
    """
    if not component:
        return {'error': 'component parameter is required'}
    from api.routes.onboarding import analyze_component_onboarding as _analyze
    return _analyze(application, component)


# ---------------------------------------------------------------------------
# Export tools (formatting logic, backed by query tools above)
# ---------------------------------------------------------------------------

@mcp.tool()
@async_tool
def export_jira(
    component: str,
    application: str = DEFAULT_APPLICATION,
) -> str:
    """Export failure, violation, or release analysis as Jira ticket markup.

    For releases, pass the release name as the component argument
    (e.g., rhoai-v3-5-ea-2-stage-1783018561).

    Args:
        component: Component name or release name
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

    release_analysis = _ai_repo().get_analysis_for_release(component)
    if release_analysis:
        return _format_release_jira(component, release_analysis)

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
        from conforma.policy_tools import count_unique_violations as _cuv
        _uvc, _uvr = _cuv(violation.get('violation_summary', ''))
        if _uvc:
            lines.append(f"**Violations:** {_uvc} unique violations ({violation['violations_count']} per-image), {violation['warnings_count']} warnings")
        else:
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
            from datetime import datetime
            first = first_detected
            if hasattr(first, 'tzinfo') and (first.tzinfo is None):
                first = first.replace(tzinfo=UTC)
            now = datetime.now(UTC)
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
        from conforma.policy_tools import count_unique_violations as _cuv2
        _uvc2, _ = _cuv2(violation.get('violation_summary', ''))
        if _uvc2:
            text += f"Violations: {_uvc2} unique ({violation['violations_count']} per-image) | Warnings: {violation['warnings_count']}\n"
        else:
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

    Searches build records (DB), cluster components, and Quay registry manifests.
    For per-architecture SHA digests from EC violations, resolves back to the
    component via manifest list inspection.

    Args:
        image_ref: Image URL, sha256 digest, or partial digest
    """
    db_matches = _build_repo().find_by_image(image_ref)

    cluster_matches = []
    all_comps = []
    url_component = None
    try:
        from clients.kubernetes import KubernetesClient
        kc = KubernetesClient(namespace=NAMESPACE)
        all_comps = kc.list_components()
        term = image_ref.lower()
        cluster_matches = [c for c in all_comps if term in (c.get('container_image') or '').lower()]

        if not cluster_matches and '/' in image_ref:
            path = image_ref.split('/')[-1].split('@')[0].split(':')[0]
            if path:
                url_component = path
                cluster_matches = [c for c in all_comps if c.get('name', '') == path]
                if not cluster_matches:
                    import re
                    core = re.sub(r'-(rhel[0-9]+|v[0-9]+-[0-9]+-.*|ea-[0-9]+.*)$', '', path)
                    if core and core != path:
                        cluster_matches = [c for c in all_comps
                                           if c.get('name', '').startswith(core)]
    except Exception:
        pass

    quay_match = None
    if not db_matches and not cluster_matches:
        try:
            from clients.registry_client import RegistryClient
            rc = RegistryClient()
            target_digest = image_ref.strip()
            if not target_digest.startswith('sha256:') and '@sha256:' in image_ref:
                target_digest = image_ref.split('@')[-1]

            if target_digest.startswith('sha256:') and len(target_digest) >= 20:
                if '/' in image_ref and '@' in image_ref:
                    registry, repository, _ = rc.parse_image_ref(image_ref)
                    result = rc.resolve_per_arch_digest(registry, repository, target_digest)
                    if result:
                        comp_name = repository.rsplit('/', 1)[-1] if '/' in repository else repository
                        result['component'] = comp_name
                        quay_match = result
                if not quay_match:
                    for comp in all_comps[:20]:
                        ci = comp.get('container_image', '')
                        if not ci:
                            continue
                        registry, repository, _ = rc.parse_image_ref(ci)
                        result = rc.resolve_per_arch_digest(registry, repository, target_digest)
                        if result:
                            result['component'] = comp.get('name', '')
                            quay_match = result
                            break
        except Exception:
            pass

    total = len(db_matches) + len(cluster_matches) + (1 if quay_match else 0)
    result = {
        'query': image_ref,
        'db_matches': db_matches,
        'cluster_matches': cluster_matches,
        'total_matches': total,
    }
    if url_component:
        result['url_component'] = url_component
    if quay_match:
        result['quay_match'] = quay_match
    return result


@mcp.tool()
@async_tool
def get_snapshot_freshness(application: str = DEFAULT_APPLICATION) -> Dict[str, Any]:
    """Check snapshot images against latest builds to detect stale/failed images.

    For each component in the latest Snapshot, checks whether its image matches
    the most recent build. Flags components where the latest build failed (snapshot
    has a pre-failure image) or a newer successful build exists. Use this to diagnose
    verify-conforma failures caused by stale snapshot images.

    Args:
        application: Which version to query
    """
    from proactive.health_monitor import HealthMonitor
    monitor = HealthMonitor(db=None)
    return monitor.check_snapshot_freshness(application)


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
    from config import CollectorConfig
    from proactive.health_monitor import HealthMonitor
    from repositories.connection import DatabaseConnection

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
# Runtime configuration tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_watched_applications() -> Dict[str, Any]:
    """Get the list of applications being monitored by the worker and watcher."""
    from config import CollectorConfig
    from repositories.config_repository import ConfigRepository
    from repositories.connection import DatabaseConnection
    repo = ConfigRepository(DatabaseConnection(CollectorConfig.from_env().db))
    return {'applications': repo.get_watched_applications()}


@mcp.tool()
def add_watch_application(application: str) -> Dict[str, Any]:
    """Add an application to the watch list. The worker and watcher will start monitoring it."""
    from config import CollectorConfig
    from repositories.config_repository import ConfigRepository
    from repositories.connection import DatabaseConnection
    repo = ConfigRepository(DatabaseConnection(CollectorConfig.from_env().db))
    apps = repo.add_watched_application(application, updated_by='mcp')
    return {'applications': apps}


@mcp.tool()
def remove_watch_application(application: str) -> Dict[str, Any]:
    """Remove an application from the watch list."""
    from config import CollectorConfig
    from repositories.config_repository import ConfigRepository
    from repositories.connection import DatabaseConnection
    repo = ConfigRepository(DatabaseConnection(CollectorConfig.from_env().db))
    apps = repo.remove_watched_application(application, updated_by='mcp')
    return {'applications': apps}


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
            branch=src.branch,
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
    executor = SkillExecutor(entry, params=params or {}, dry_run=dry_run,
                             timeout=timeout, triggered_by='mcp')
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
    from conforma.policy_tools import count_unique_violations as _cuv3
    _uvc3, _ = _cuv3(violation.get('violation_summary', ''))
    if _uvc3:
        lines.append(f"|Violations|{_uvc3} unique violations ({violation['violations_count']} per-image), {violation['warnings_count']} warnings|")
    else:
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


def _format_release_jira(release_name: str, analysis: Dict[str, Any]) -> str:
    """Format a release AI analysis as Jira ticket markup."""
    evidence, transparency, fix_action_type = _extract_from_analysis_json(
        analysis.get('analysis_json'))

    confidence = int(float(analysis.get('confidence_score', 0)) * 100)
    root_cause = analysis.get('root_cause', '')
    recommended_fix = analysis.get('recommended_fix', '')
    category = analysis.get('failure_category', '')
    can_auto_fix = analysis.get('can_auto_fix', False)
    requires_review = analysis.get('requires_human_review', True)
    files = analysis.get('recommended_files') or []
    if isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]

    # Extract affected_images and owner_team from analysis_json
    affected_images = []
    owner_team = ''
    aj = analysis.get('analysis_json')
    if aj:
        try:
            data = aj if isinstance(aj, (list, dict)) else json.loads(aj)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                inp = item.get('input', item)
                if inp.get('affected_images'):
                    affected_images = inp['affected_images']
                if inp.get('owner_team'):
                    owner_team = inp['owner_team']
                if affected_images or owner_team:
                    break
        except Exception:
            pass

    lines = [f"h2. Release Failure: {release_name}", ""]

    lines.append("||Field||Value||")
    lines.append(f"|Release|{release_name}|")
    lines.append(f"|Category|{category}|")
    if fix_action_type:
        lines.append(f"|Fix Action|{fix_action_type}|")
    if owner_team:
        lines.append(f"|Owner Team|{owner_team}|")
    lines.append(f"|AI Confidence|{confidence}%|")
    lines.append(f"|Can Auto-Fix|{'Yes' if can_auto_fix else 'No'}|")
    lines.append(f"|Requires Review|{'Yes' if requires_review else 'No'}|")
    lines.append("")

    lines.append("h3. Root Cause")
    lines.append("")
    lines.append(root_cause)
    lines.append("")

    lines.append("h3. Recommended Fix")
    lines.append("")
    lines.append(recommended_fix)
    lines.append("")

    if files:
        lines.append(f"*Files to modify:* {', '.join(files)}")
        lines.append("")

    if affected_images:
        lines.append("h3. Affected Images")
        lines.append("")
        lines.append("||Image Reference||")
        for img in affected_images[:20]:
            lines.append(f"|{img}|")
        lines.append("")

    if evidence:
        lines.append("h3. Evidence")
        lines.append("")
        for ref in evidence:
            ref_type = ref.get('type', '')
            url = ref.get('url', '')
            desc = ref.get('description', '')
            if url:
                lines.append(f"* \\[{ref_type}\\] [{desc}|{url}]")
            else:
                lines.append(f"* \\[{ref_type}\\] {desc}")
        lines.append("")

    if transparency:
        consulted = transparency.get('sources_consulted', [])
        unavailable = transparency.get('sources_unavailable', [])
        limitations = transparency.get('limitations', [])
        if consulted or unavailable or limitations:
            lines.append("h3. Analysis Transparency")
            lines.append("")
            if consulted:
                lines.append(f"*Sources used:* {', '.join(consulted)}")
            if unavailable:
                lines.append(f"*Sources unavailable:* {', '.join(unavailable)}")
            if limitations:
                lines.append(f"*Limitations:* {', '.join(limitations)}")
            lines.append("")

    lines.append("h3. Acceptance Criteria")
    lines.append("")
    if fix_action_type == 'rebuild':
        lines.append("# Component is rebuilt successfully (new PipelineRun completes)")
        lines.append("# Source container image exists for the new build")
        lines.append("# SHA propagates through nudging chain (operator-nudging → bundle → FBC)")
        lines.append("# New release passes verify-conforma")
    elif fix_action_type == 'file_change':
        lines.append("# Fix PR is merged to the correct branch")
        lines.append("# New build completes successfully with the fix")
        lines.append("# New release passes verify-conforma")
    else:
        lines.append("# Root cause is addressed")
        lines.append("# New release passes verify-conforma")
        lines.append("# Fix is verified to prevent recurrence")
    lines.append("")

    langfuse_id = analysis.get('langfuse_trace_id')
    if langfuse_id:
        lines.append(f"_AI analysis trace: {langfuse_id}_")

    return "\n".join(lines)


@mcp.tool()
def get_ai_quality_metrics(
    application: str = DEFAULT_APPLICATION,
    days: int = 30,
) -> Dict[str, Any]:
    """AI analysis quality metrics: accuracy from human verdicts and auto-correlation.

    Returns verdict counts (correct/partial/incorrect), accuracy rate,
    average confidence for correct vs incorrect diagnoses, and breakdown
    by failure category.

    Args:
        application: Application name
        days: Number of days to look back (default 30)
    """
    from repositories.ai_analysis_repository import AIAnalysisRepository
    repo = AIAnalysisRepository(_db_connection())
    return repo.get_quality_metrics(application=application, days=days)


@mcp.tool()
@async_tool
def get_ec_policy_summary(
    application: str = DEFAULT_APPLICATION,
    name_filter: str = "rhoai",
) -> Dict[str, Any]:
    """EC policy summary: active exceptions, expiring soon, and stage-vs-prod gap.

    Cross-references all EnterpriseContractPolicy CRDs to surface:
    - Active and expired exceptions
    - Exceptions expiring within 30 days
    - Policy gap (rules excepted in stage but not prod)

    Args:
        application: Application name (used for policy-gap filtering)
        name_filter: Filter policies by name substring (default: rhoai)
    """
    import os

    from clients.konflux_client import KonfluxClient

    ns = NAMESPACE
    releng_ns = os.environ.get('RELENG_NAMESPACE', '')

    client = KonfluxClient(namespace=ns)

    policies = client.get_ec_policies(name_filter=name_filter)
    all_exceptions = []
    for p in policies:
        all_exceptions.extend(client.extract_exceptions(p))

    active = [e for e in all_exceptions if e['days_left'] is None or e['days_left'] >= 0]
    expired = [e for e in all_exceptions if e['days_left'] is not None and e['days_left'] < 0]
    expiring = [e for e in all_exceptions
                if e['days_left'] is not None and 0 <= e['days_left'] <= 30]
    expiring.sort(key=lambda e: e['days_left'])

    policy_gap = {}
    if releng_ns:
        try:
            releng_client = KonfluxClient(namespace=releng_ns)
            rpas = releng_client.get_release_plan_admissions(name_filter=application)
            bindings = KonfluxClient.extract_rpa_bindings(rpas)
            exception_map = {}
            for p in policies:
                pname = p['metadata']['name']
                exception_map[pname] = client.extract_exceptions(p)

            for b in bindings:
                target = b.get('target', '')
                policy = b.get('policy', '')
                if target == 'stage':
                    stage_excs = set(e['value'] for e in exception_map.get(policy, []))
                elif target == 'prod':
                    prod_excs = set(e['value'] for e in exception_map.get(policy, []))

            if 'stage_excs' in dir() and 'prod_excs' in dir():
                policy_gap = {
                    'stage_only_count': len(stage_excs - prod_excs),
                    'prod_only_count': len(prod_excs - stage_excs),
                    'stage_only_rules': sorted(stage_excs - prod_excs),
                }
        except Exception:
            pass

    return {
        'policies_count': len(policies),
        'total_exceptions': len(all_exceptions),
        'active_exceptions': len(active),
        'expired_exceptions': len(expired),
        'expiring_within_30d': len(expiring),
        'expiring_details': expiring[:10],
        'policy_gap': policy_gap,
    }


@mcp.tool()
@async_tool
def get_scenario_coverage(
    application: str = DEFAULT_APPLICATION,
) -> Dict[str, Any]:
    """ITS scenario coverage analysis: gaps, disabled scenarios, missing conforma.

    Queries IntegrationTestScenario CRDs and analyzes coverage for the application.
    Detects disabled scenarios, missing conforma coverage, and configuration issues.

    Args:
        application: Application name to analyze
    """
    from clients.konflux_client import KonfluxClient

    ns = NAMESPACE
    client = KonfluxClient(namespace=ns)
    scenarios = client.get_integration_test_scenarios(
        namespace=ns, app_filter=application,
    )
    metadata = [KonfluxClient.extract_its_metadata(s) for s in scenarios]

    active = [s for s in metadata if not s['is_disabled']]
    disabled = [s for s in metadata if s['is_disabled']]
    conforma = [s for s in metadata if s['is_conforma']]
    future = [s for s in metadata if s['is_future']]

    apps = sorted(set(s['application'] for s in metadata))

    gaps = []
    conforma_apps = set(s['application'] for s in conforma if not s['is_disabled'])
    for app in apps:
        if app not in conforma_apps:
            gaps.append({'application': app, 'issue': 'no_active_conforma_scenario'})

    disabled_conforma = [s for s in conforma if s['is_disabled']]

    return {
        'total_scenarios': len(metadata),
        'active': len(active),
        'disabled': len(disabled),
        'conforma_scenarios': len(conforma),
        'future_scenarios': len(future),
        'applications': apps,
        'gaps': gaps,
        'disabled_conforma': [
            {'name': s['name'], 'application': s['application'], 'policy_ref': s['policy_ref']}
            for s in disabled_conforma
        ],
        'scenario_summary': [
            {
                'name': s['name'],
                'application': s['application'],
                'is_disabled': s['is_disabled'],
                'is_conforma': s['is_conforma'],
                'is_future': s['is_future'],
                'policy_ref': s['policy_ref'],
            }
            for s in sorted(metadata, key=lambda x: (x['application'], x['name']))
        ],
    }


@mcp.tool()
def get_resolved_patterns(
    application: str = DEFAULT_APPLICATION,
    days: int = 90,
) -> Dict[str, Any]:
    """Resolution patterns from solved conforma violations.

    Groups resolved violations by rule pattern and shows how they were resolved
    (rebuild, config change, exception). Essential for identifying which violations
    are transient (auto-fixable by rebuild) vs structural (require code/config changes).

    Args:
        application: Application name
        days: Number of days to look back (default 90)
    """
    db = _db_connection()
    with db.connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                CASE
                    WHEN violation_summary LIKE '%%hermetic_task%%' THEN 'hermetic_task'
                    WHEN violation_summary LIKE '%%trusted_task%%' THEN 'trusted_task'
                    WHEN violation_summary LIKE '%%source_image%%' THEN 'source_image'
                    WHEN violation_summary LIKE '%%labels%%' THEN 'labels'
                    WHEN violation_summary LIKE '%%fips%%' THEN 'fips'
                    WHEN violation_summary LIKE '%%slsa%%' THEN 'slsa'
                    WHEN violation_summary LIKE '%%sbom%%' THEN 'sbom'
                    WHEN violation_summary LIKE '%%rpm%%' THEN 'rpm'
                    WHEN violation_summary LIKE '%%deprecated%%' THEN 'deprecated'
                    WHEN violation_summary LIKE '%%signature%%' THEN 'signature'
                    WHEN violation_summary LIKE '%%snyk%%' THEN 'snyk'
                    ELSE 'other'
                END as rule_group,
                COUNT(*) as total_resolved,
                COUNT(*) FILTER (WHERE ai_analyzed) as with_ai_analysis,
                AVG(EXTRACT(EPOCH FROM (resolved_at - first_detected_at)) / 3600)::numeric(8,1)
                    as avg_hours_to_resolve,
                MIN(first_detected_at) as first_seen,
                MAX(resolved_at) as last_resolved
            FROM conforma_results
            WHERE is_resolved = TRUE
              AND resolved_at >= NOW() - INTERVAL '%s days'
            GROUP BY rule_group
            ORDER BY total_resolved DESC
        """, (days,))
        cols = ['rule_group', 'total_resolved', 'with_ai_analysis',
                'avg_hours_to_resolve', 'first_seen', 'last_resolved']
        patterns = [dict(zip(cols, row)) for row in cursor.fetchall()]

        for p in patterns:
            if p['first_seen']:
                p['first_seen'] = p['first_seen'].isoformat()
            if p['last_resolved']:
                p['last_resolved'] = p['last_resolved'].isoformat()
            if p['avg_hours_to_resolve']:
                p['avg_hours_to_resolve'] = float(p['avg_hours_to_resolve'])

        cursor.execute("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE ai_analyzed) as with_ai
            FROM conforma_results
            WHERE is_resolved = TRUE
              AND resolved_at >= NOW() - INTERVAL '%s days'
        """, (days,))
        row = cursor.fetchone()
        total = row[0]
        with_ai = row[1]

    transient_rules = {'trusted_task', 'slsa', 'signature', 'snyk'}
    for p in patterns:
        p['likely_transient'] = p['rule_group'] in transient_rules

    return {
        'total_resolved': total,
        'with_ai_analysis': with_ai,
        'ai_coverage_pct': round(with_ai * 100 / max(total, 1), 1),
        'days': days,
        'patterns': patterns,
    }


@mcp.tool()
@async_tool
def get_config_analysis(
    application: str = DEFAULT_APPLICATION,
) -> Dict[str, Any]:
    """Run Konflux configuration analysis using AI.

    Audits EC policies, ITS scenarios, and violation patterns to find
    misconfigurations, coverage gaps, and auto-rebuild opportunities.
    Requires LLM_PROVIDER to be configured.

    Args:
        application: Application name to analyze
    """
    from config import CollectorConfig
    config = CollectorConfig.from_env()
    if not config.llm:
        return {'error': 'LLM not configured. Set LLM_PROVIDER env var.'}

    from analyzers.config_analyzer import ConfigAnalyzer
    db = _db_connection()
    analyzer = ConfigAnalyzer(config, db=db)
    result = analyzer.run(application=application)
    if not result.get('analyzed'):
        return {'error': 'Analysis could not complete'}
    analysis = result['analysis']
    return {
        'findings_count': len(analysis.get('findings', [])),
        'overall_severity': analysis.get('overall_severity'),
        'confidence': analysis.get('confidence_score'),
        'summary': analysis.get('summary'),
        'findings': analysis.get('findings', []),
        'auto_rebuild_candidates': analysis.get('auto_rebuild_candidates', []),
        'cost_usd': result.get('cost_usd', 0),
        'model': result.get('model', ''),
    }


@mcp.tool()
@async_tool
def get_build_config_analysis(
    application: str = DEFAULT_APPLICATION,
) -> Dict[str, Any]:
    """Run build pipeline configuration analysis using AI.

    Audits stale components, recurring transient failures, webhook issues,
    build chain problems, and quota bottlenecks. Identifies auto-rebuild
    candidates. Requires LLM_PROVIDER to be configured.

    Args:
        application: Application name to analyze
    """
    from config import CollectorConfig
    config = CollectorConfig.from_env()
    if not config.llm:
        return {'error': 'LLM not configured. Set LLM_PROVIDER env var.'}

    from analyzers.build_config_analyzer import BuildConfigAnalyzer
    db = _db_connection()
    analyzer = BuildConfigAnalyzer(config, db=db)
    result = analyzer.run(application=application)
    if not result.get('analyzed'):
        return {'error': 'Analysis could not complete'}
    analysis = result['analysis']
    return {
        'findings_count': len(analysis.get('findings', [])),
        'overall_severity': analysis.get('overall_severity'),
        'confidence': analysis.get('confidence_score'),
        'summary': analysis.get('summary'),
        'findings': analysis.get('findings', []),
        'auto_rebuild_candidates': analysis.get('auto_rebuild_candidates', []),
        'cost_usd': result.get('cost_usd', 0),
        'model': result.get('model', ''),
    }


@mcp.tool()
@async_tool
def get_release_config_analysis(
    application: str = DEFAULT_APPLICATION,
) -> Dict[str, Any]:
    """Run release configuration analysis using AI.

    Audits conforma blockers, PCC cache freshness, exception coverage,
    snapshot SHA drift, and nightly build health. Identifies release
    blockers. Requires LLM_PROVIDER to be configured.

    Args:
        application: Application name to analyze
    """
    from config import CollectorConfig
    config = CollectorConfig.from_env()
    if not config.llm:
        return {'error': 'LLM not configured. Set LLM_PROVIDER env var.'}

    from analyzers.release_config_analyzer import ReleaseConfigAnalyzer
    db = _db_connection()
    analyzer = ReleaseConfigAnalyzer(config, db=db)
    result = analyzer.run(application=application)
    if not result.get('analyzed'):
        return {'error': 'Analysis could not complete'}
    analysis = result['analysis']
    return {
        'findings_count': len(analysis.get('findings', [])),
        'overall_severity': analysis.get('overall_severity'),
        'confidence': analysis.get('confidence_score'),
        'summary': analysis.get('summary'),
        'findings': analysis.get('findings', []),
        'release_blockers': analysis.get('release_blockers', []),
        'cost_usd': result.get('cost_usd', 0),
        'model': result.get('model', ''),
    }


@mcp.tool()
@async_tool
def get_regression_report(
    domain: str = "conforma",
    application: str = DEFAULT_APPLICATION,
    limit: int = 50,
) -> Dict[str, Any]:
    """Run AI analyzer regression tests against resolved data.

    Measures category accuracy, confidence calibration, coverage gaps, and
    suggests improvements. Uses resolved failures/violations as ground truth.

    Args:
        domain: Which analyzer to test: 'conforma', 'build', or 'release'
        application: Application name (used by conforma and build)
        limit: Max items to evaluate (default 50)
    """
    from config import CollectorConfig
    config = CollectorConfig.from_env()
    db = _db_connection()

    if domain == 'build':
        from analyzers.build_regression import BuildRegressionTester
        tester = BuildRegressionTester(config, db=db)
        return tester.run(application=application, limit=limit)
    elif domain == 'release':
        from analyzers.release_regression import ReleaseRegressionTester
        tester = ReleaseRegressionTester(config, db=db)
        return tester.run(limit=limit)
    else:
        from analyzers.conforma_regression import ConformaRegressionTester
        tester = ConformaRegressionTester(config, db=db)
        return tester.run(application=application, limit=limit)


@mcp.tool()
@async_tool
def trigger_rebuild(
    component: str,
    namespace: str = "",
) -> Dict[str, Any]:
    """Trigger a fresh Konflux build for a component.

    Annotates the Component CR with build.appstudio.openshift.io/request=trigger-pac-build,
    which starts a new PipelineRun (event_type=incoming). Use when a component needs
    rebuilding (e.g., PipelineRunTimeout caused source_image.exists violation).

    Args:
        component: Component name (e.g., odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2)
        namespace: Kubernetes namespace (default: from config)
    """
    from clients.kubernetes import KubernetesClient
    ns = namespace or NAMESPACE
    kc = KubernetesClient(namespace=ns)

    meta = kc.get_component_metadata(component, namespace=ns)
    if meta is None:
        return {'success': False, 'error': 'Component not found: {}'.format(component)}

    try:
        kc.trigger_rebuild(component, namespace=ns)
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'manual_command': (
                'kubectl annotate components/{} -n {} '
                'build.appstudio.openshift.io/request=trigger-pac-build --overwrite'
            ).format(component, ns),
        }

    return {
        'success': True,
        'component': component,
        'namespace': ns,
        'repository_url': meta.get('repository_url', ''),
        'branch': meta.get('branch', ''),
        'message': 'Build triggered. Konflux will start a new PipelineRun.',
        'monitor_command': 'ic get pipelineruns | head',
    }
