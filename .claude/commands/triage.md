Triage CI failures: choose build or conforma focus, with full tracking.

Usage: /triage [component-name]

- No argument: show triage dashboard, then ask what type to triage
- With component: auto-detect type and investigate

---

## Instructions

The working directory is: .

### Always start by showing the triage dashboard:

Use MCP tools first, fall back to CLI:

1. **Preferred**: Call `mcp__ic__list_alerts()` and `mcp__ic__get_triage()` for structured data
2. **Fallback**: `ic triage show 2>/dev/null` and `ic get alerts 2>/dev/null`

This shows tracked items, untracked failures, and the overall triage state. Present this to the user first.

### If a specific component was provided as `$ARGUMENTS`:

Auto-detect the failure type from the alerts data:
- Build failure → follow the /triage-build workflow for that component
- Conforma violation → follow the /triage-conforma workflow for that component
- Both → handle build first, then conforma

### If no component was provided:

Ask the user what type of failures to triage using AskUserQuestion:

Options:
- **Build failures** — follow the /triage-build workflow
- **Conforma violations** — follow the /triage-conforma workflow
- **Both** — run /triage-build first, then /triage-conforma

### Workflow references

- Build triage: follow the exact steps in /triage-build
- Conforma triage: follow the exact steps in /triage-conforma

**CRITICAL**: ALL failures/violations MUST be tracked via `ic triage track` or `mcp__ic__track_triage_item()`. After any action (Jira, Slack, fix), update the triage item. This maintains state across sessions.

### Notes

- Works in both local mode and cluster mode (`ic config use-cluster`)
- Use MCP tools (`mcp__ic__*`) when available — structured data, no ANSI parsing
- AI analysis runs locally using your Vertex AI credentials even in cluster mode
- Use `ic` (not `./ic`) — the CLI is installed via PyPI or available at project root
