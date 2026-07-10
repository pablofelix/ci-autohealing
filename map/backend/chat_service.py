"""Chat service — proxies user questions to Claude with graph context.

Uses the same LLMProvider abstraction as IC analyzers (Vertex AI or Anthropic).
Builds a system prompt from the selected node's topology and live IC data so
Claude can explain relationships, diagnose failures, and suggest next steps.
"""

import logging
import os

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the RHOAI System Map assistant — an expert on Red Hat OpenShift AI \
CI/CD infrastructure built on Konflux. You help engineers understand the \
build system, diagnose failures, and plan next steps using the live data \
provided in the context.

Rules:
- Answer in 2-4 sentences unless the user asks for detail.
- Reference specific node names, error messages, and Jira keys from the context.
- When diagnosing failures, cite the error message and failed task/step.
- When asked about release readiness, summarize the verdict and list blockers.
- When summarizing overall state, lead with the headline (X failing, Y working) \
then mention the most urgent items (nearest deadline, oldest unresolved failure).
- When asked about trends, compare today vs previous days using the daily stats. \
Say whether things are improving, stable, or degrading.
- When asked what changed, highlight new failures, recent resolutions, and any \
deadline approaching within 7 days.
- If the context doesn't contain enough info, say so honestly.
- Suggest concrete next steps (rebuild, file Jira, check triage, etc.) when relevant.
- Use plain language. Avoid marketing speak.
- When a concept narrative is provided, use it as the basis for your explanation. \
Expand on it naturally — add detail, clarify terms, and connect it to the \
highlighted nodes (the user can see them glowing on the map). \
Every technical term must be briefly explained the first time you use it. \
Be technical but accessible — assume the reader is smart but new to this system.
"""


def _build_context_block(node_detail, impact, graph_stats, ic_data=None,
                         panoramic_data=None):
    """Build a structured context string from graph + IC data."""
    parts = []

    if node_detail:
        props = node_detail.get("props", {})
        parts.append("Selected node: {} (type: {})".format(
            props.get("name", node_detail.get("id", "unknown")),
            node_detail.get("type", "unknown"),
        ))
        desc = props.get("description", "")
        if desc:
            parts.append("Description: {}".format(desc))

        neighbors = node_detail.get("neighbors", [])
        if neighbors:
            lines = []
            for n in neighbors[:15]:
                arrow = "->" if n.get("direction") == "outgoing" else "<-"
                lines.append("  {} [{}] {}".format(arrow, n.get("relationship", ""), n.get("name", n.get("id", ""))))
            parts.append("Connections:\n{}".format("\n".join(lines)))

        gaps = node_detail.get("gaps", [])
        if gaps:
            parts.append("Issues: {}".format(
                ", ".join("{}: {}".format(g["type"], g["message"]) for g in gaps[:5])
            ))

    if impact:
        parts.append("Impact analysis: {} affected nodes downstream".format(
            impact.get("total_affected", 0)
        ))
        by_type = impact.get("by_type", {})
        if by_type:
            parts.append("Affected by type: {}".format(
                ", ".join("{}: {}".format(k, v) for k, v in by_type.items())
            ))

    if graph_stats:
        node_counts = graph_stats.get("nodes", [])
        total = sum(s.get("count", 0) for s in node_counts)
        parts.append("Graph: {} total nodes".format(total))

    if ic_data:
        parts.append(_build_ic_context(ic_data))

    if panoramic_data:
        parts.append(_build_panoramic_context(panoramic_data))

    return "\n".join(parts) if parts else "No graph context available."


def _build_ic_context(ic_data):
    """Format IC operational data into a context block for the LLM."""
    sections = []

    failure = ic_data.get("failure")
    if failure:
        lines = ["[Build Failure]"]
        lines.append("  Status: {}".format(failure.get("status", "unknown")))
        if failure.get("error_message"):
            lines.append("  Error: {}".format(failure["error_message"][:300]))
        if failure.get("failed_task"):
            lines.append("  Failed task: {} / step: {}".format(
                failure.get("failed_task", ""), failure.get("failed_step", "")))
        if failure.get("jira_key"):
            lines.append("  Jira: {}".format(failure["jira_key"]))
        if failure.get("konflux_url"):
            lines.append("  Konflux: {}".format(failure["konflux_url"]))
        history = failure.get("build_history", [])
        if history:
            recent = history[:5]
            results = [h.get("status", "?") for h in recent]
            lines.append("  Recent builds: {}".format(" → ".join(results)))
        sections.append("\n".join(lines))

    analysis = ic_data.get("analysis")
    if analysis:
        lines = ["[AI Analysis]"]
        if analysis.get("root_cause"):
            lines.append("  Root cause: {}".format(analysis["root_cause"][:300]))
        if analysis.get("failure_category"):
            lines.append("  Category: {}".format(analysis["failure_category"]))
        if analysis.get("confidence_score") is not None:
            lines.append("  Confidence: {:.0%}".format(analysis["confidence_score"]))
        if analysis.get("recommended_fix"):
            lines.append("  Fix: {}".format(analysis["recommended_fix"][:200]))
        if analysis.get("can_auto_fix"):
            lines.append("  Auto-fixable: yes")
        sections.append("\n".join(lines))

    violation = ic_data.get("violation")
    if violation:
        lines = ["[Conforma Violation]"]
        rules = violation.get("violations", [])
        if rules:
            for r in rules[:5]:
                lines.append("  - {} ({})".format(
                    r.get("title", r.get("rule", "unknown")),
                    r.get("status", "")))
        if violation.get("exception_coverage"):
            lines.append("  Exception coverage: {}".format(violation["exception_coverage"]))
        sections.append("\n".join(lines))

    triage_items = ic_data.get("triage")
    if triage_items:
        items = triage_items if isinstance(triage_items, list) else triage_items.get("items", [])
        active = [t for t in items if t.get("status") == "active"]
        if active:
            lines = ["[Active Triage ({} items)]".format(len(active))]
            for t in active[:5]:
                comp = t.get("component", "?")
                root = t.get("root_cause", "")
                jira = t.get("jira_key", "")
                line = "  - {}".format(comp)
                if root:
                    line += ": {}".format(root[:100])
                if jira:
                    line += " [{}]".format(jira)
                lines.append(line)
            sections.append("\n".join(lines))

    readiness = ic_data.get("readiness")
    if readiness:
        lines = ["[Release Readiness]"]
        lines.append("  Verdict: {}".format(readiness.get("verdict", "unknown")))
        checks = readiness.get("checks", [])
        fails = [c for c in checks if c.get("status") == "FAIL"]
        warns = [c for c in checks if c.get("status") == "WARN"]
        if fails:
            lines.append("  Blockers ({}):".format(len(fails)))
            for c in fails[:5]:
                lines.append("    - {}: {}".format(c.get("name", ""), c.get("detail", "")))
                if c.get("fix"):
                    lines.append("      Fix: {}".format(c["fix"][:150]))
        if warns:
            lines.append("  Risks ({}):".format(len(warns)))
            for c in warns[:3]:
                lines.append("    - {}: {}".format(c.get("name", ""), c.get("detail", "")))
        sections.append("\n".join(lines))

    blockers = ic_data.get("blockers")
    if blockers:
        items = blockers if isinstance(blockers, list) else blockers.get("blockers", [])
        if items:
            lines = ["[Jira Blockers ({} issues)]".format(len(items))]
            for b in items[:5]:
                key = b.get("key", "?")
                summary = b.get("summary", "")[:80]
                signals = b.get("critical_signals", [])
                line = "  - {} — {}".format(key, summary)
                if signals:
                    line += " [{}]".format(", ".join(signals[:2]))
                lines.append(line)
            sections.append("\n".join(lines))

    return "\n".join(sections) if sections else ""


def _build_panoramic_context(data):
    """Format app-wide IC data into a panoramic context block for the LLM."""
    sections = []

    alerts = data.get("alerts")
    triage_summary = data.get("triage_summary")
    if alerts or triage_summary:
        lines = ["[Current State]"]
        if triage_summary:
            lines.append("  Components: {} failing, {} working (of {} total)".format(
                triage_summary.get("failing", 0),
                triage_summary.get("working", 0),
                triage_summary.get("total", 0),
            ))
        if alerts:
            failures = alerts.get("build_failures", [])
            violations = alerts.get("conforma_violations", [])
            if failures:
                lines.append("  Build failures ({}):".format(len(failures)))
                for f in failures[:8]:
                    age = f.get("age_hours")
                    age_str = " ({:.0f}h old)".format(age) if age else ""
                    new_tag = " [NEW]" if f.get("is_new") else ""
                    lines.append("    - {}{}{}".format(
                        f.get("component", "?"), age_str, new_tag))
            if violations:
                lines.append("  Conforma violations ({})".format(len(violations)))
            freeze = alerts.get("freeze_countdown")
            if freeze:
                lines.append("  Freeze: {} (urgency: {})".format(
                    freeze.get("message", ""), freeze.get("urgency", "")))
        sections.append("\n".join(lines))

    daily = data.get("daily_stats")
    if daily:
        lines = ["[Failure Trend (7 days)]"]
        entries = daily if isinstance(daily, list) else []
        for d in entries[:7]:
            lines.append("  {}: {} failures".format(
                d.get("date", "?"), d.get("count", 0)))
        sections.append("\n".join(lines))

    resolved = data.get("resolved")
    if resolved:
        items = resolved if isinstance(resolved, list) else resolved.get("resolved", [])
        if items:
            lines = ["[Recent Resolutions ({} in 7 days)]".format(len(items))]
            for r in items[:8]:
                lines.append("  - {} (resolved {})".format(
                    r.get("component", "?"),
                    r.get("resolved_at", r.get("last_seen", "?"))[:10]))
            sections.append("\n".join(lines))

    readiness = data.get("readiness")
    schedule = data.get("schedule")
    if readiness or schedule:
        lines = ["[Release Status]"]
        if readiness:
            lines.append("  Verdict: {}".format(readiness.get("verdict", "unknown")))
            checks = readiness.get("checks", [])
            fails = [c for c in checks if c.get("status") == "FAIL"]
            if fails:
                lines.append("  Blockers ({}):".format(len(fails)))
                for c in fails[:5]:
                    lines.append("    - {}: {}".format(
                        c.get("name", ""), c.get("detail", "")))
        if schedule:
            for key in ("feature_freeze", "code_freeze", "release_date"):
                entry = schedule.get(key)
                if entry:
                    remaining = entry.get("days_remaining")
                    if remaining is not None:
                        lines.append("  {}: {} ({} days)".format(
                            key.replace("_", " ").title(),
                            entry.get("date", "?")[:10],
                            remaining))
        sections.append("\n".join(lines))

    nightly = data.get("nightly")
    if nightly:
        lines = ["[Nightly Build Health]"]
        fbc = nightly.get("fbc_health", {})
        if fbc:
            lines.append("  FBC status: {}".format(fbc.get("status", "unknown")))
        blockers = nightly.get("blockers", [])
        if blockers:
            lines.append("  Nightly blockers ({}):".format(len(blockers)))
            for b in blockers[:5]:
                lines.append("    - {}".format(
                    b.get("component", b) if isinstance(b, dict) else b))
        sections.append("\n".join(lines))

    warnings = data.get("health_warnings")
    if warnings:
        items = warnings if isinstance(warnings, list) else warnings.get("warnings", [])
        if items:
            lines = ["[Health Warnings ({} signals)]".format(len(items))]
            for w in items[:5]:
                lines.append("  - {}: {} [{}]".format(
                    w.get("component", "?"),
                    w.get("message", "")[:100],
                    w.get("severity", w.get("type", ""))))
            sections.append("\n".join(lines))

    stale = data.get("stale")
    if stale:
        items = stale if isinstance(stale, list) else stale.get("stale", [])
        if items:
            lines = ["[Stale Components ({} with untriggered commits)]".format(len(items))]
            for s in items[:5]:
                built = s.get("built_commit", "?")
                head = s.get("head_commit", "?")
                lines.append("  - {} (built: {} → HEAD: {})".format(
                    s.get("component", "?"), built, head))
            sections.append("\n".join(lines))

    return "\n".join(sections) if sections else ""


def _create_provider():
    """Create LLM provider from environment variables."""
    import sys
    src_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'src')
    if src_dir not in sys.path:
        sys.path.insert(0, os.path.abspath(src_dir))

    from config import LLMConfig
    from clients.llm_provider import create_llm_provider

    provider = os.getenv('LLM_PROVIDER', '')
    if not provider:
        if os.getenv('ANTHROPIC_VERTEX_PROJECT_ID'):
            provider = 'vertex_ai'
        elif os.getenv('ANTHROPIC_API_KEY'):
            provider = 'anthropic'

    if not provider:
        return None

    default_model = 'claude-sonnet-4-6'

    config = LLMConfig(
        provider=provider,
        model=os.getenv('LLM_MODEL', default_model),
        project_id=os.getenv('VERTEX_PROJECT_ID') or os.getenv('ANTHROPIC_VERTEX_PROJECT_ID'),
        region=os.getenv('VERTEX_REGION') or os.getenv('CLOUD_ML_REGION', 'us-east5'),
        api_key=os.getenv('ANTHROPIC_API_KEY'),
    )
    return create_llm_provider(config)


class ChatService:
    """Handles chat requests with graph-aware context."""

    def __init__(self, provider=None):
        self._provider = provider

    def _get_provider(self):
        if self._provider is None:
            self._provider = _create_provider()
        return self._provider

    def chat(self, message, node_detail=None, impact=None, graph_stats=None,
             ic_data=None, panoramic_data=None, concept_narrative=None):
        """Send a chat message with graph context to Claude.

        Returns dict with 'response', 'model', 'tokens' or None on failure.
        """
        provider = self._get_provider()
        if not provider:
            return None

        context = _build_context_block(node_detail, impact, graph_stats, ic_data,
                                       panoramic_data=panoramic_data)

        if concept_narrative:
            context += "\n\n[Concept Explanation]\n{}".format(concept_narrative)

        user_content = "Graph context:\n{}\n\nQuestion: {}".format(context, message)

        try:
            result = provider.create_message(
                system=SYSTEM_PROMPT,
                user_content=user_content,
                max_tokens=1024,
            )
            return {
                "response": result.content,
                "model": result.model,
                "tokens": {
                    "input": result.input_tokens,
                    "output": result.output_tokens,
                },
            }
        except Exception as exc:
            logger.warning("Chat LLM call failed: %s", exc)
            return None
