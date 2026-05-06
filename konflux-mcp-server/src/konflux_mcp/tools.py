"""MCP tools for Konflux CI monitoring.

All tools are read-only queries against PostgreSQL.
Each tool accepts an `application` parameter for multi-version support.
"""

import asyncio
import json
from datetime import datetime
from typing import List, Literal, Optional, Callable, TypeVar, Dict, Any
from functools import wraps
from fastmcp import FastMCP

from .models import (
    ApplicationInfo,
    AlertsSummary,
    BuildFailureDetails,
    ConformaViolationDetails,
    AnalysisDetails,
    FailureSummary,
    StatsResponse,
)
from .repository_factory import build_repo, conforma_repo, ai_repo

DEFAULT_APPLICATION = "acme-v2-0"
KONFLUX_UI_BASE = "https://konflux-ui.apps.CLUSTER_DOMAIN"

mcp = FastMCP("Konflux CI Monitoring")

T = TypeVar('T')


def async_tool(sync_func: Callable[..., T]) -> Callable[..., "asyncio.Future[T]"]:
    """Decorator to run synchronous DB queries in a thread executor."""
    @wraps(sync_func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: sync_func(*args, **kwargs))
    return wrapper


def _build_konflux_url(pr_name: str) -> str:
    return f"{KONFLUX_UI_BASE}/ns/NAMESPACE_PLACEHOLDER/pipelinerun/{pr_name}/logs"


def _row_to_failure_summary(row: tuple) -> FailureSummary:
    return FailureSummary(
        component=row[0], status=row[1], error_type=row[2],
        first_seen=row[3], last_seen=row[4],
        occurrence_count=row[5], has_logs=row[6], has_analysis=row[7],
    )


@mcp.tool()
@async_tool
def list_applications() -> List[ApplicationInfo]:
    """List all available RHOAI versions/applications.

    Discovers what versions exist in the database so agents can choose which to query.

    Returns:
        List of ApplicationInfo with name, component/failure/conforma counts, and last sync time.
    """
    with build_repo.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            WITH app_stats AS (
                SELECT
                    application,
                    COUNT(DISTINCT component_name) as component_count,
                    COUNT(*) FILTER (WHERE is_resolved = FALSE) as failure_count,
                    MAX(last_updated_at) as last_sync
                FROM build_failures
                GROUP BY application
            ),
            conforma_stats AS (
                SELECT
                    application,
                    COUNT(*) FILTER (WHERE is_resolved = FALSE) as conforma_count
                FROM conforma_results
                GROUP BY application
            )
            SELECT
                a.application,
                a.component_count,
                a.failure_count,
                COALESCE(c.conforma_count, 0) as conforma_count,
                a.last_sync
            FROM app_stats a
            LEFT JOIN conforma_stats c ON a.application = c.application
            ORDER BY a.application
        """)

        return [
            ApplicationInfo(
                name=row[0],
                component_count=row[1],
                failure_count=row[2],
                conforma_count=row[3],
                last_sync=row[4],
            )
            for row in cursor.fetchall()
        ]


@mcp.tool()
@async_tool
def list_alerts(application: str = DEFAULT_APPLICATION) -> AlertsSummary:
    """Get all unresolved alerts (build failures + Conforma violations).

    Returns a unified view of all current issues for an application version.

    Args:
        application: Which version to query. Use list_applications() to discover versions.
    """
    build_failures: List[FailureSummary] = []
    conforma_violations: List[FailureSummary] = []

    with build_repo.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                component_name, status, error_type,
                first_detected_at, last_updated_at,
                COUNT(*) OVER (PARTITION BY component_name) as occurrence_count,
                (build_logs IS NOT NULL) as has_logs,
                (ai_analyzed = TRUE) as has_analysis
            FROM (
                SELECT DISTINCT ON (component_name)
                    component_name, status, error_type,
                    first_detected_at, last_updated_at,
                    build_logs, ai_analyzed
                FROM build_failures
                WHERE application = %s AND is_resolved = FALSE
                ORDER BY component_name, last_updated_at DESC
            ) sub
            ORDER BY last_updated_at DESC
        """, (application,))

        for row in cursor.fetchall():
            build_failures.append(_row_to_failure_summary(row))

    with conforma_repo.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                component_name, 'Conforma Violation' as status,
                scenario as error_type,
                first_detected_at, last_updated_at,
                COUNT(*) OVER (PARTITION BY component_name) as occurrence_count,
                (violation_summary IS NOT NULL) as has_logs,
                (ai_analyzed = TRUE) as has_analysis
            FROM (
                SELECT DISTINCT ON (component_name)
                    component_name, scenario,
                    first_detected_at, last_updated_at,
                    violation_summary, ai_analyzed
                FROM conforma_results
                WHERE application = %s AND is_resolved = FALSE
                ORDER BY component_name, last_updated_at DESC
            ) sub
            ORDER BY last_updated_at DESC
        """, (application,))

        for row in cursor.fetchall():
            conforma_violations.append(_row_to_failure_summary(row))

    last_sync = datetime.utcnow()
    all_dates = [f.last_seen for f in build_failures] + [v.last_seen for v in conforma_violations]
    if all_dates:
        last_sync = max(all_dates)

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
    with build_repo.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                component_name, pipelinerun_name, error_message, error_type,
                failed_task_name, failed_step_name,
                CASE WHEN %s THEN LEFT(build_logs, 50000) END,
                commit_sha, commit_message, commit_author, commit_url,
                repository_url, branch,
                CASE WHEN %s THEN commit_context END,
                konflux_url, first_detected_at
            FROM build_failures
            WHERE component_name = %s
              AND application = %s
              AND is_resolved = FALSE
            ORDER BY last_updated_at DESC
            LIMIT 1
        """, (include_logs, include_commit_context, component, application))

        row = cursor.fetchone()
        if not row:
            return None

        konflux_url = row[14] or _build_konflux_url(row[1] or "")

        return BuildFailureDetails(
            component=row[0],
            pipelinerun_name=row[1],
            error_message=row[2],
            error_type=row[3],
            failed_task=row[4],
            failed_step=row[5],
            build_logs=row[6],
            commit_sha=row[7],
            commit_message=row[8],
            commit_author=row[9],
            commit_url=row[10],
            repository_url=row[11],
            branch=row[12],
            commit_context=row[13],
            konflux_url=konflux_url,
            first_detected_at=row[15],
        )


@mcp.tool()
@async_tool
def get_violation(
    component: str,
    application: str = DEFAULT_APPLICATION,
    include_details: bool = True,
) -> Optional[ConformaViolationDetails]:
    """Get full Conforma policy violation details.

    Returns violation summary, SBOM data, policy rules.

    Args:
        component: Component name
        application: Which version to query
        include_details: Include violation_details JSONB
    """
    with conforma_repo.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                component_name, scenario,
                violations_count, warnings_count, successes_count,
                violation_summary, violation_details,
                repository_url, commit_sha, snapshot_name,
                pipelinerun_name, first_detected_at
            FROM conforma_results
            WHERE component_name = %s
              AND application = %s
              AND is_resolved = FALSE
            ORDER BY last_updated_at DESC
            LIMIT 1
        """, (component, application))

        row = cursor.fetchone()
        if not row:
            return None

        konflux_url = _build_konflux_url(row[10] or "")
        details = row[6] if include_details else None

        return ConformaViolationDetails(
            component=row[0],
            scenario=row[1],
            violations_count=row[2],
            warnings_count=row[3],
            successes_count=row[4],
            violation_summary=row[5] or "",
            violation_details=details,
            repository_url=row[7],
            commit_sha=row[8],
            snapshot_name=row[9],
            konflux_url=konflux_url,
            first_detected_at=row[11],
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
    with ai_repo.db.connection() as conn:
        cursor = conn.cursor()

        if type in ("auto", "build"):
            cursor.execute("""
                SELECT
                    'build' as analysis_type,
                    b.component_name, a.model_used, a.root_cause,
                    a.failure_category, a.confidence_score,
                    a.recommended_fix, a.recommended_files,
                    a.can_auto_fix, a.requires_human_review,
                    a.analyzed_at, a.langfuse_trace_url,
                    a.tokens_used, COALESCE(a.cost_usd, 0)
                FROM ai_analysis a
                JOIN build_failures b ON a.build_failure_id = b.id
                WHERE b.component_name = %s
                  AND b.application = %s
                ORDER BY a.analyzed_at DESC
                LIMIT 1
            """, (component, application))
            row = cursor.fetchone()
            if row:
                return _row_to_analysis(row)

        if type in ("auto", "conforma"):
            cursor.execute("""
                SELECT
                    'conforma' as analysis_type,
                    c.component_name, a.model_used, a.root_cause,
                    a.failure_category, a.confidence_score,
                    a.recommended_fix, a.recommended_files,
                    a.can_auto_fix, a.requires_human_review,
                    a.analyzed_at, a.langfuse_trace_url,
                    a.tokens_used, COALESCE(a.cost_usd, 0)
                FROM ai_analysis a
                JOIN conforma_results c ON a.conforma_result_id = c.id
                WHERE c.component_name = %s
                  AND c.application = %s
                ORDER BY a.analyzed_at DESC
                LIMIT 1
            """, (component, application))
            row = cursor.fetchone()
            if row:
                return _row_to_analysis(row)

        return None


def _row_to_analysis(row: tuple) -> AnalysisDetails:
    files = row[7] or []
    if isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]
    return AnalysisDetails(
        type=row[0],
        component=row[1],
        model_used=row[2] or "",
        root_cause=row[3] or "",
        failure_category=row[4] or "",
        confidence_score=float(row[5] or 0),
        recommended_fix=row[6] or "",
        recommended_files=files,
        can_auto_fix=bool(row[8]),
        requires_human_review=bool(row[9]),
        analyzed_at=row[10],
        langfuse_trace_url=row[11],
        tokens_used=row[12] or 0,
        cost_usd=float(row[13] or 0),
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
        category: Filter by AI analysis category (e.g., "dependency_issue", "policy_package_source")
        resolved: Include resolved failures (default: False = only active)
        has_analysis: Filter by analysis status (True=analyzed, False=pending, None=all)
        limit: Maximum results (default: 10)
    """
    results: List[FailureSummary] = []

    with build_repo.db.connection() as conn:
        cursor = conn.cursor()

        conditions = ["b.application = %s"]
        params: list = [application]

        if not resolved:
            conditions.append("b.is_resolved = FALSE")

        if has_analysis is True:
            conditions.append("b.ai_analyzed = TRUE")
        elif has_analysis is False:
            conditions.append("b.ai_analyzed = FALSE")

        if category:
            conditions.append("a.failure_category = %s")
            params.append(category)

        params.append(limit)

        where = " AND ".join(conditions)
        join = "LEFT JOIN ai_analysis a ON a.build_failure_id = b.id" if category else ""

        cursor.execute(f"""
            SELECT component_name, status, error_type,
                   first_detected_at, last_updated_at,
                   occurrence_count, has_logs, has_analysis
            FROM (
                SELECT DISTINCT ON (b.component_name)
                    b.component_name, b.status, b.error_type,
                    b.first_detected_at, b.last_updated_at,
                    COUNT(*) OVER (PARTITION BY b.component_name) as occurrence_count,
                    (b.build_logs IS NOT NULL) as has_logs,
                    (b.ai_analyzed = TRUE) as has_analysis
                FROM build_failures b
                {join}
                WHERE {where}
                ORDER BY b.component_name, b.last_updated_at DESC
            ) sub
            LIMIT %s
        """, params)

        for row in cursor.fetchall():
            results.append(_row_to_failure_summary(row))

    with conforma_repo.db.connection() as conn:
        cursor = conn.cursor()

        conditions = ["c.application = %s"]
        params = [application]

        if not resolved:
            conditions.append("c.is_resolved = FALSE")

        if has_analysis is True:
            conditions.append("c.ai_analyzed = TRUE")
        elif has_analysis is False:
            conditions.append("c.ai_analyzed = FALSE")

        if category:
            conditions.append("a.failure_category = %s")
            params.append(category)

        params.append(limit)

        where = " AND ".join(conditions)
        join = "LEFT JOIN ai_analysis a ON a.conforma_result_id = c.id" if category else ""

        cursor.execute(f"""
            SELECT component_name, status, error_type,
                   first_detected_at, last_updated_at,
                   occurrence_count, has_logs, has_analysis
            FROM (
                SELECT DISTINCT ON (c.component_name)
                    c.component_name, 'Conforma Violation' as status, c.scenario as error_type,
                    c.first_detected_at, c.last_updated_at,
                    COUNT(*) OVER (PARTITION BY c.component_name) as occurrence_count,
                    (c.violation_summary IS NOT NULL) as has_logs,
                    (c.ai_analyzed = TRUE) as has_analysis
                FROM conforma_results c
                {join}
                WHERE {where}
                ORDER BY c.component_name, c.last_updated_at DESC
            ) sub
            LIMIT %s
        """, params)

        for row in cursor.fetchall():
            results.append(_row_to_failure_summary(row))

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
    with build_repo.db.connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE ai_analyzed = FALSE AND build_logs IS NOT NULL) as pending,
                0 as analyzed,
                0 as autofixable
            FROM build_failures
            WHERE application = %s AND is_resolved = FALSE
        """, (application,))
        bp, _, _ = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*), COUNT(*) FILTER (WHERE a.can_auto_fix = TRUE)
            FROM ai_analysis a
            JOIN build_failures b ON a.build_failure_id = b.id
            WHERE b.application = %s
              AND a.analyzed_at > NOW() - INTERVAL '30 days'
        """, (application,))
        ba, baf = cursor.fetchone()

    build_stats = {"pending": bp or 0, "analyzed": ba or 0, "autofixable": baf or 0}

    with conforma_repo.db.connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE ai_analyzed = FALSE AND violation_summary IS NOT NULL)
            FROM conforma_results
            WHERE application = %s AND is_resolved = FALSE
        """, (application,))
        cp = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*), COUNT(*) FILTER (WHERE a.can_auto_fix = TRUE)
            FROM ai_analysis a
            JOIN conforma_results c ON a.conforma_result_id = c.id
            WHERE c.application = %s
              AND a.analyzed_at > NOW() - INTERVAL '30 days'
        """, (application,))
        ca, caf = cursor.fetchone()

    conforma_stats = {"pending": cp or 0, "analyzed": ca or 0, "autofixable": caf or 0}

    with ai_repo.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(SUM(a.cost_usd), 0)
            FROM ai_analysis a
            LEFT JOIN build_failures b ON a.build_failure_id = b.id
            LEFT JOIN conforma_results c ON a.conforma_result_id = c.id
            WHERE (b.application = %s OR c.application = %s)
              AND a.analyzed_at > NOW() - INTERVAL '30 days'
        """, (application, application))
        total_cost = float(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT
                COALESCE(b.component_name, c.component_name) as component,
                CASE WHEN a.build_failure_id IS NOT NULL THEN 'build' ELSE 'conforma' END as type,
                a.failure_category,
                ROUND(a.confidence_score * 100) as confidence,
                a.can_auto_fix,
                a.analyzed_at
            FROM ai_analysis a
            LEFT JOIN build_failures b ON a.build_failure_id = b.id
            LEFT JOIN conforma_results c ON a.conforma_result_id = c.id
            WHERE (b.application = %s OR c.application = %s)
              AND a.analyzed_at > NOW() - INTERVAL '7 days'
            ORDER BY a.analyzed_at DESC
            LIMIT 10
        """, (application, application))

        recent = [
            {
                "component": row[0],
                "type": row[1],
                "category": row[2],
                "confidence": int(row[3] or 0),
                "can_auto_fix": row[4],
                "analyzed_at": row[5].isoformat() if row[5] else None,
            }
            for row in cursor.fetchall()
        ]

    return StatsResponse(
        application=application,
        build_failures=build_stats,
        conforma_violations=conforma_stats,
        total_cost_30d=total_cost,
        recent_analyses=recent,
    )


@mcp.tool()
@async_tool
def export_jira(
    component: str,
    application: str = DEFAULT_APPLICATION,
) -> str:
    """Export failure or violation as Jira ticket markup.

    Auto-detects build failure vs Conforma violation and generates appropriate template.

    Args:
        component: Component name
        application: Which version to query

    Returns:
        Jira markup string ready to paste into a ticket.
    """
    # Try build failure first
    with build_repo.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                b.component_name, b.pipelinerun_name, b.error_message,
                b.repository_url, b.branch, b.commit_sha, b.commit_message,
                b.commit_author, b.commit_url,
                TO_CHAR(b.first_detected_at, 'YYYY-MM-DD HH24:MI'),
                TO_CHAR(b.last_updated_at, 'YYYY-MM-DD HH24:MI'),
                EXTRACT(DAY FROM (NOW() - b.first_detected_at))::int,
                (SELECT COUNT(*) FROM build_failures
                 WHERE component_name = b.component_name AND is_resolved = FALSE),
                COALESCE(b.konflux_logs_url, b.konflux_url),
                b.failed_step_name, b.error_type
            FROM build_failures b
            WHERE b.component_name = %s
              AND b.application = %s
              AND b.is_resolved = FALSE
            ORDER BY b.last_updated_at DESC
            LIMIT 1
        """, (component, application))
        build_row = cursor.fetchone()

    if build_row:
        return _format_build_jira(build_row, component, application)

    # Try Conforma violation
    with conforma_repo.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                c.component_name, c.pipelinerun_name, c.scenario,
                c.violations_count, c.warnings_count, c.successes_count,
                TO_CHAR(c.first_detected_at, 'YYYY-MM-DD HH24:MI'),
                TO_CHAR(c.last_updated_at, 'YYYY-MM-DD HH24:MI'),
                EXTRACT(DAY FROM (NOW() - c.first_detected_at))::int,
                (SELECT COUNT(*) FROM conforma_results
                 WHERE component_name = c.component_name AND is_resolved = FALSE),
                COALESCE(c.repository_url, ''), COALESCE(c.commit_sha, ''),
                COALESCE(c.commit_url, ''), COALESCE(c.snapshot_name, ''),
                c.pipelinerun_name
            FROM conforma_results c
            WHERE c.component_name = %s
              AND c.application = %s
              AND c.is_resolved = FALSE
            ORDER BY c.last_updated_at DESC
            LIMIT 1
        """, (component, application))
        conforma_row = cursor.fetchone()

    if conforma_row:
        return _format_conforma_jira(conforma_row, component, application)

    return f"No unresolved failures found for component: {component} in {application}"


def _format_build_jira(row: tuple, component: str, application: str) -> str:
    comp, pr_name, error_msg = row[0], row[1], row[2]
    repo_url, branch, sha, commit_msg = row[3], row[4], row[5], row[6]
    author, commit_url = row[7], row[8]
    first_seen, last_seen, days_failing, occurrences = row[9], row[10], row[11], row[12]
    konflux_url, failed_step, error_type = row[13], row[14], row[15]

    if not konflux_url:
        konflux_url = _build_konflux_url(pr_name or "")

    ai = get_analysis.__wrapped__(component, application, "build")

    lines = []
    lines.append(f"h2. Build Failure: {comp}")
    lines.append("")
    lines.append("||Field||Value||")
    lines.append(f"|Component|{comp}|")
    lines.append("|Status|Failed|")
    lines.append(f"|PipelineRun|[{pr_name}|{konflux_url}]|")
    lines.append(f"|First Seen|{first_seen}|")
    lines.append(f"|Last Seen|{last_seen}|")
    lines.append(f"|Occurrences|{occurrences} times|")
    lines.append("")

    if ai:
        confidence = int(ai.confidence_score * 100)
        lines.append(f"h3. AI Analysis ({confidence}% confidence)")
        lines.append("")
        lines.append(f"||Category||{ai.failure_category}||")
        auto_str = "Yes" if ai.can_auto_fix else "No"
        review_str = "Yes" if ai.requires_human_review else "No"
        lines.append(f"|Can Auto-Fix|{auto_str}|")
        lines.append(f"|Requires Review|{review_str}|")
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
    if repo_url:
        repo_name = repo_url.rstrip("/").split("/")[-1] if repo_url else ""
        lines.append(f"*Repository:* [{repo_name}|{repo_url}]")
    lines.append(f"*Branch:* {branch}")
    if sha and commit_url:
        lines.append(f"*Commit:* [{sha[:8]}|{commit_url}] by {author}")
    lines.append(f"*Commit Message:* {commit_msg}")
    lines.append("")
    if failed_step:
        lines.append(f"*Failed Step:* {failed_step}")
    if error_type:
        lines.append(f"*Error Type:* {error_type}")
    if error_msg:
        lines.append("*Error Message:*")
        lines.append("{code}")
        lines.append(str(error_msg))
        lines.append("{code}")
    lines.append("")
    lines.append("h3. Resources")
    lines.append("")
    lines.append(f"* [Full Build Logs|{konflux_url}]")
    if ai and ai.langfuse_trace_url:
        lines.append(f"* [Analysis Trace|{ai.langfuse_trace_url}]")
    lines.append("")
    lines.append("---")
    if ai:
        confidence = int(ai.confidence_score * 100)
        lines.append(f"_Analysis by {ai.model_used} ({ai.analyzed_at}). Confidence: {confidence}%._")
    else:
        lines.append(f"_No AI analysis available. Run: ic ai analyze component {comp}_")

    return "\n".join(lines)


def _format_conforma_jira(row: tuple, component: str, application: str) -> str:
    comp, pr_name, scenario = row[0], row[1], row[2]
    violations, warnings, successes = row[3], row[4], row[5]
    first_seen, last_seen, days_failing, occurrences = row[6], row[7], row[8], row[9]
    repo_url, sha, commit_url, snapshot = row[10], row[11], row[12], row[13]

    konflux_url = _build_konflux_url(pr_name or "")

    ai = get_analysis.__wrapped__(component, application, "conforma")

    lines = []
    lines.append(f"h2. Conforma Policy Violation: {comp}")
    lines.append("")
    lines.append("||Field||Value||")
    lines.append(f"|Component|{comp}|")
    lines.append("|Status|Policy Violation|")
    lines.append(f"|Scenario|{scenario}|")
    lines.append(f"|Violations|{violations} violations, {warnings} warnings, {successes} successes|")
    lines.append(f"|PipelineRun|[{pr_name}|{konflux_url}]|")
    if snapshot:
        lines.append(f"|Snapshot|{snapshot}|")
    lines.append(f"|First Seen|{first_seen}|")
    lines.append(f"|Last Seen|{last_seen}|")
    lines.append(f"|Occurrences|{occurrences} times|")
    lines.append("")

    if ai:
        confidence = int(ai.confidence_score * 100)
        lines.append(f"h3. AI Analysis ({confidence}% confidence)")
        lines.append("")
        lines.append(f"||Category||{ai.failure_category}||")
        auto_str = "Yes" if ai.can_auto_fix else "No"
        review_str = "Yes" if ai.requires_human_review else "No"
        lines.append(f"|Can Auto-Fix|{auto_str}|")
        lines.append(f"|Requires Review|{review_str}|")
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

        lines.append("h5. Policy Exception Process")
        lines.append("")
        lines.append("If the fix isn't immediately feasible:")
        lines.append("# File JIRA at: [Create Issue|https://JIRA_CREATE_ISSUE_URL]")
        lines.append("# Explain business justification and timeline")
        lines.append("# Create MR to exception file: {{konflux-release-data/.../fbc-acme-prod.yaml}}")
        lines.append("# Ping @owatkins (ProdSec) in #wg-3_0-openshift-ai-release for approval")
        lines.append("")

    lines.append("h3. Resources")
    lines.append("")
    if repo_url:
        lines.append(f"*Repository:* {repo_url}")
    if sha and commit_url:
        lines.append(f"*Commit:* [{sha[:8]}|{commit_url}]")
    lines.append(f"* [Konflux PipelineRun|{konflux_url}]")
    lines.append(f"* [Quay Repository|https://quay.io/repository/rhoai/{comp}]")
    if ai and ai.langfuse_trace_url:
        lines.append(f"* [Analysis Trace|{ai.langfuse_trace_url}]")
    lines.append("* [Enterprise Contract Docs|https://enterprisecontract.dev/]")
    lines.append("")
    lines.append("---")
    if ai and ai.model_used:
        confidence = int(ai.confidence_score * 100)
        lines.append(f"_Analysis by {ai.model_used} ({ai.analyzed_at}). Confidence: {confidence}%._")
    else:
        lines.append(f"_No AI analysis available. Run: ic ai analyze conforma {comp}_")

    return "\n".join(lines)


@mcp.tool()
@async_tool
def export_markdown(
    component: str,
    application: str = DEFAULT_APPLICATION,
) -> str:
    """Export failure as GitHub-flavored Markdown.

    Suitable for GitHub Issues, PR descriptions, or documentation.

    Args:
        component: Component name
        application: Which version to query
    """
    failure = get_failure.__wrapped__(component, application, include_logs=False, include_commit_context=False)
    analysis_data = get_analysis.__wrapped__(component, application)

    if not failure:
        violation = get_violation.__wrapped__(component, application, include_details=False)
        if not violation:
            return f"No unresolved failures found for {component} in {application}"

        lines = [f"## Conforma Policy Violation: {violation.component}"]
        lines.append("")
        lines.append(f"**Scenario:** {violation.scenario}")
        lines.append(f"**Violations:** {violation.violations_count} violations, {violation.warnings_count} warnings")
        lines.append(f"**First Seen:** {violation.first_detected_at}")
        lines.append(f"**Konflux:** [{violation.konflux_url}]({violation.konflux_url})")
        lines.append("")
        if analysis_data:
            lines.append(f"### AI Analysis ({int(analysis_data.confidence_score * 100)}% confidence)")
            lines.append("")
            lines.append(f"**Category:** {analysis_data.failure_category}")
            lines.append(f"**Root Cause:** {analysis_data.root_cause}")
            lines.append("")
            lines.append(f"**Recommended Fix:** {analysis_data.recommended_fix}")
        return "\n".join(lines)

    lines = [f"## Build Failure: {failure.component}"]
    lines.append("")
    lines.append(f"**PipelineRun:** `{failure.pipelinerun_name}`")
    lines.append(f"**Error:** {failure.error_message}")
    lines.append(f"**Failed Step:** {failure.failed_step}")
    lines.append(f"**First Seen:** {failure.first_detected_at}")
    if failure.repository_url:
        lines.append(f"**Repository:** [{failure.repository_url}]({failure.repository_url})")
    if failure.commit_sha and failure.commit_url:
        lines.append(f"**Commit:** [{failure.commit_sha[:8]}]({failure.commit_url}) by {failure.commit_author}")
    lines.append(f"**Konflux:** [{failure.konflux_url}]({failure.konflux_url})")
    lines.append("")

    if analysis_data:
        lines.append(f"### AI Analysis ({int(analysis_data.confidence_score * 100)}% confidence)")
        lines.append("")
        lines.append(f"**Category:** {analysis_data.failure_category}")
        lines.append(f"**Can Auto-Fix:** {'Yes' if analysis_data.can_auto_fix else 'No'}")
        lines.append("")
        lines.append(f"**Root Cause:** {analysis_data.root_cause}")
        lines.append("")
        lines.append(f"**Recommended Fix:** {analysis_data.recommended_fix}")
        if analysis_data.recommended_files:
            lines.append("")
            lines.append("**Files:** " + ", ".join(f"`{f}`" for f in analysis_data.recommended_files))

    return "\n".join(lines)


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

    failure = get_failure.__wrapped__(component, application, include_logs=include_logs, include_commit_context=False)
    if failure:
        result["type"] = "build_failure"
        result["failure"] = failure.model_dump(mode="json")
    else:
        violation = get_violation.__wrapped__(component, application, include_details=True)
        if violation:
            result["type"] = "conforma_violation"
            result["violation"] = violation.model_dump(mode="json")
        else:
            result["type"] = "not_found"
            return json.dumps(result, indent=2, default=str)

    analysis_data = get_analysis.__wrapped__(component, application)
    if analysis_data:
        result["analysis"] = analysis_data.model_dump(mode="json")

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
    failure = get_failure.__wrapped__(component, application, include_logs=False, include_commit_context=False)
    analysis_data = get_analysis.__wrapped__(component, application)

    if not failure:
        violation = get_violation.__wrapped__(component, application, include_details=False)
        if not violation:
            return f"No unresolved failures found for {component} in {application}"

        text = f":warning: *Conforma Policy Violation: {violation.component}*\n"
        text += f"Scenario: {violation.scenario}\n"
        text += f"Violations: {violation.violations_count} | Warnings: {violation.warnings_count}\n"
        if analysis_data:
            text += f"\n:robot_face: *AI Analysis* ({int(analysis_data.confidence_score * 100)}% confidence)\n"
            text += f"Category: `{analysis_data.failure_category}`\n"
            text += f"Root Cause: {analysis_data.root_cause[:200]}\n"
        text += f"\n<{violation.konflux_url}|View in Konflux>"
        return text

    text = f":red_circle: *Build Failure: {failure.component}*\n"
    text += f"PipelineRun: `{failure.pipelinerun_name}`\n"
    text += f"Error: {failure.error_message}\n"
    if failure.failed_step:
        text += f"Failed Step: `{failure.failed_step}`\n"
    if failure.commit_sha and failure.commit_url:
        text += f"Commit: <{failure.commit_url}|{failure.commit_sha[:8]}> by {failure.commit_author}\n"

    if analysis_data:
        text += f"\n:robot_face: *AI Analysis* ({int(analysis_data.confidence_score * 100)}% confidence)\n"
        text += f"Category: `{analysis_data.failure_category}`\n"
        auto_fix = ":white_check_mark:" if analysis_data.can_auto_fix else ":x:"
        text += f"Auto-fixable: {auto_fix}\n"
        text += f"Root Cause: {analysis_data.root_cause[:200]}\n"

    text += f"\n<{failure.konflux_url}|View in Konflux>"
    return text
