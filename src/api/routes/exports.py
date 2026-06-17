"""Export endpoints: Jira, Markdown, Slack, JSON formats."""

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from security.redact import redact_secrets

from mcp_server.models import AnalysisDetails
from repositories.ai_analysis_repository import AIAnalysisRepository
from repositories.build_failure_repository import BuildFailureRepository
from repositories.conforma_repository import ConformaRepository
from repositories.repository_factory import get_repository

from shared_config import KONFLUX_UI_BASE, NAMESPACE


def _sanitize_for_jira(text):
    if not text:
        return text
    text = redact_secrets(text)
    text = re.sub(r'\{[a-z]+[^}]*\}', '', text)
    text = re.sub(r'\[~[^\]]+\]', '', text)
    return text


def _sanitize_for_slack(text):
    if not text:
        return text
    text = redact_secrets(text)
    text = text.replace('@here', '(at)here').replace('@channel', '(at)channel')
    text = re.sub(r'<@[A-Z0-9]+>', '', text)
    return text

router = APIRouter(tags=["exports"])


def _build_repo():
    return get_repository(BuildFailureRepository)


def _conforma_repo():
    return get_repository(ConformaRepository)


def _ai_repo():
    return get_repository(AIAnalysisRepository)


def _konflux_url(pr_name: str) -> str:
    return f"{KONFLUX_UI_BASE}/ns/{NAMESPACE}/pipelinerun/{pr_name}/logs"


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


@router.get("/applications/{application}/export/{component}")
def export_component(
    component: str,
    application: str,
    format: Literal["jira", "markdown", "slack", "json"] = "jira",
    include_logs: bool = False,
) -> PlainTextResponse:
    """Export failure/violation in the specified format."""
    handlers = {
        "jira": _export_jira,
        "markdown": _export_markdown,
        "slack": _export_slack,
        "json": _export_json,
    }
    text = handlers[format](component, application, include_logs)
    if format == 'jira':
        text = _sanitize_for_jira(text)
    elif format == 'slack':
        text = _sanitize_for_slack(text)
    media = "application/json" if format == "json" else "text/plain"
    return PlainTextResponse(content=text, media_type=media)


def _export_jira(component: str, application: str, _include_logs: bool) -> str:
    failure = _build_repo().get_failure_details(component, application)
    if failure:
        analysis = _ai_repo().get_analysis_by_component(component, application, 'build')
        return _format_build_jira(failure, analysis)

    violation = _conforma_repo().get_violation_details(component, application)
    if violation:
        analysis = _ai_repo().get_analysis_by_component(component, application, 'conforma')
        return _format_conforma_jira(violation, analysis)

    return f"No unresolved failures found for component: {component} in {application}"


def _export_markdown(component: str, application: str, _include_logs: bool) -> str:
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


def _format_failure_duration(first_detected_at):
    """Format how long a failure has been active."""
    if not first_detected_at:
        return None
    first = first_detected_at
    if hasattr(first, 'tzinfo') and (first.tzinfo is None):
        first = first.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    days = (now - first).days
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return "{} ({} days)".format(first.strftime('%b %d'), days)


def _fetch_open_prs(repo_url, branch, limit=3):
    """Fetch open GitHub PRs for a component's branch. Returns [] on any error."""
    if not repo_url:
        return []
    try:
        from clients.github_client import GitHubClient, parse_github_repo
        parsed = parse_github_repo(repo_url)
        if not parsed:
            return []
        owner, repo = parsed
        token = os.environ.get('GITHUB_TOKEN', '')
        if not token:
            return []
        gh = GitHubClient(token)
        return gh.list_pull_requests(owner, repo, base=branch or None, state='open', limit=limit)
    except Exception:
        return []


def _export_slack(component: str, application: str, _include_logs: bool) -> str:
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

        duration = _format_failure_duration(failure.get('first_detected_at'))
        if duration:
            text += f"Failing since: {duration}\n"

        open_prs = _fetch_open_prs(failure.get('repository_url'), failure.get('branch'))
        if open_prs:
            text += "\n:mag: *Open PRs:*\n"
            for p in open_prs:
                title = p['title'][:50] + ('...' if len(p['title']) > 50 else '')
                text += f"  <{p['url']}|#{p['number']}> {title} ({p['author']})\n"

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


def _export_json(component: str, application: str, include_logs: bool) -> str:
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
