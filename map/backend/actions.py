"""Action detection — suggests rebuild, triage, and improvement actions from IC data.

Analyzes the IC diagnostic data already fetched for the chat context and
returns a list of actionable suggestions.  Each action has a type, label,
params, and confirmation prompt so the frontend can render buttons that
the user must explicitly confirm before execution.
"""

import logging

logger = logging.getLogger(__name__)

# --- action types -------------------------------------------------------

REBUILD = "rebuild"
TRIAGE = "triage"
IMPROVEMENT = "improvement"

# Categories that indicate transient/retriable failures
_TRANSIENT_CATEGORIES = {
    "timeout", "transient", "infrastructure", "flaky",
    "pipeline_timeout", "resource_limit", "network",
}


def detect_actions(
    ic_data: dict | None,
    panoramic_data: dict | None,
    node_id: str | None,
    application: str = "rhoai-v3-5",
) -> list[dict]:
    """Compute suggested actions from IC diagnostic data.

    Returns a list of action dicts, each with:
      - type: rebuild | triage | improvement
      - label: button text
      - description: why this action is suggested
      - params: dict passed to the execution endpoint
      - confirmation: prompt shown before execution
    """
    actions: list[dict] = []

    if node_id and node_id.startswith("comp-") and ic_data:
        component = node_id[5:]
        _detect_rebuild(actions, ic_data, component, application)
        _detect_triage(actions, ic_data, component, application)

    if panoramic_data:
        _detect_stale_rebuilds(actions, panoramic_data, application)
        _detect_release_prep(actions, panoramic_data, application)
        _detect_health_warnings(actions, panoramic_data)

    return actions


def _detect_rebuild(actions, ic_data, component, application):
    """Suggest rebuild if failure looks transient."""
    failure = ic_data.get("failure")
    analysis = ic_data.get("analysis")
    if not failure:
        return

    category = ""
    if analysis:
        category = (analysis.get("failure_category") or "").lower()

    is_transient = category in _TRANSIENT_CATEGORIES

    failed_step = ""
    if isinstance(failure, dict):
        failed_step = failure.get("failed_step", "")

    if is_transient or "timeout" in failed_step.lower():
        actions.append({
            "type": REBUILD,
            "label": f"Rebuild {component}",
            "description": (
                f"Last failure was {category or 'a timeout'} — "
                "a rebuild is likely to fix it."
            ),
            "params": {
                "component": component,
                "application": application,
            },
            "confirmation": f"Trigger a new build for {component}?",
        })


def _detect_triage(actions, ic_data, component, application):
    """Suggest triage tracking if failure exists but no triage item."""
    failure = ic_data.get("failure")
    triage = ic_data.get("triage")
    if not failure:
        return

    already_tracked = False
    if triage:
        items = triage if isinstance(triage, list) else [triage]
        already_tracked = any(
            component == t.get("component")
            or component in t.get("components", [])
            for t in items
        )

    if already_tracked:
        return

    analysis = ic_data.get("analysis")
    root_cause = ""
    failed_step = ""
    if analysis:
        root_cause = analysis.get("root_cause", "")
    if isinstance(failure, dict):
        failed_step = failure.get("failed_step", "")

    actions.append({
        "type": TRIAGE,
        "label": f"Track {component} in triage",
        "description": "This failure is not being tracked yet.",
        "params": {
            "component": component,
            "application": application,
            "root_cause": root_cause,
            "failed_step": failed_step,
        },
        "confirmation": f"Create a triage item for {component}?",
    })


def _detect_stale_rebuilds(actions, panoramic_data, application):
    """Suggest rebuilds for stale components (unbuilt commits)."""
    stale = panoramic_data.get("stale")
    if not stale:
        return

    items = stale if isinstance(stale, list) else stale.get("components", [])
    for item in items[:3]:
        comp = item if isinstance(item, str) else item.get("component", "")
        if not comp:
            continue
        commits_behind = ""
        if isinstance(item, dict):
            commits_behind = item.get("commits_behind", "")

        desc = f"{comp} has unbuilt commits"
        if commits_behind:
            desc += f" ({commits_behind} behind)"

        actions.append({
            "type": REBUILD,
            "label": f"Rebuild {comp}",
            "description": desc,
            "params": {
                "component": comp,
                "application": application,
            },
            "confirmation": f"Trigger a new build for {comp}?",
        })


def _detect_release_prep(actions, panoramic_data, application):
    """Surface release blockers as improvement suggestions."""
    readiness = panoramic_data.get("readiness")
    if not readiness:
        return

    verdict = (readiness.get("verdict") or "").upper()
    if verdict in ("READY", "PASS"):
        return

    checks = readiness.get("checks", [])
    failing = [c for c in checks if c.get("status") in ("FAIL", "fail")]
    if not failing:
        return

    blocker_names = ", ".join(c.get("name", "?") for c in failing[:3])
    total = len(failing)
    desc = f"{total} release blocker(s): {blocker_names}"
    if total > 3:
        desc += f" (+{total - 3} more)"

    actions.append({
        "type": IMPROVEMENT,
        "label": "View release blockers",
        "description": desc,
        "params": {"application": application},
        "confirmation": None,
    })


def _detect_health_warnings(actions, panoramic_data):
    """Surface proactive health warnings."""
    warnings = panoramic_data.get("health_warnings")
    if not warnings:
        return

    items = warnings if isinstance(warnings, list) else warnings.get("warnings", [])
    for w in items[:2]:
        msg = w if isinstance(w, str) else w.get("message", str(w))
        actions.append({
            "type": IMPROVEMENT,
            "label": "Health warning",
            "description": str(msg)[:200],
            "params": {},
            "confirmation": None,
        })
