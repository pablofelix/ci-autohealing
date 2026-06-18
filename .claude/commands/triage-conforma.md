Triage conforma violations: investigate, analyze, assess, and take action.

Usage: /triage-conforma [component-name]

- No argument: full scan of all active conforma violations
- With component: investigate that specific component only

---

## Instructions

You are triaging Konflux CI/CD **Conforma (Enterprise Contract) violations** for the RHOAI project. Your primary goal is to **find the root cause and solution** for each violation, not just classify them.

Use MCP tools (`mcp__ic__*`) for structured data, CLI (`ic`) as fallback. Works in both local and cluster mode.

The working directory is: .

### Step 1: Check triage state and scan conforma landscape

**Using MCP tools (preferred):**
- `mcp__ic__list_alerts()` — all current failures and violations
- `mcp__ic__get_triage()` — tracked triage items
- `mcp__ic__get_conforma_report()` — violation summary per component

**Using CLI (fallback):**
```bash
ic triage show 2>/dev/null
ic get alerts 2>/dev/null
ic get conforma 2>/dev/null
ic conforma categories 2>/dev/null
```

If the user provided a specific component as `$ARGUMENTS`, skip the full scan and go directly to Step 2.

Focus on **conforma violations only** (ignore build failures):
- Which violations are already tracked in triage
- Which don't have triage items yet
- Top violation categories
- Exception status

Present:
```
## Current Conforma Violations
- X components failing (Y tracked, Z untracked)
- N violations across M rules
- Top categories: [from conforma categories]
- Exceptions: [count] with, [count] without
```

### Step 2: Investigate each violation

**Using MCP tools (preferred):**
- `mcp__ic__get_violation(component=<name>)` — full details: rules, reasons, solutions
- `mcp__ic__get_analysis(component=<name>)` — AI analysis if available

**Using CLI (fallback):**
```bash
ic describe conforma <component> 2>/dev/null
ic get exceptions 2>/dev/null
ic conforma scenarios 2>/dev/null
ic conforma scenarios --gaps 2>/dev/null
```

**Key data per violation:**
- Violated rule(s) (e.g., `hermetic_task.hermetic`, `labels.required_labels`)
- Violation reason and suggested solution (Conforma provides fix hints)
- Affected images (single vs multi-arch — same violation × 4 arches?)
- Violations vs warnings vs successes
- Exception status (YES/PARTIAL/NO)
- How long failing (Since column)

When violations share a rule across components, investigate one deeply and apply to all.

### Step 3: Run AI analysis for EVERY violation without it

**Check existing analysis:**
- MCP: `mcp__ic__get_analysis(component=<name>)`
- CLI: `ic ai status 2>/dev/null`

**For each violation WITHOUT analysis — run immediately:**
```bash
ic ai analyze <component-name> 2>/dev/null
```

In cluster mode, the LLM runs locally using your Vertex AI credentials. Results upload to the cluster automatically.

Run multiple `ic ai analyze` in parallel for different components.

**For all pending at once:**
```bash
ic ai batch 2>/dev/null
```

### Step 4: Synthesize solutions

For each violation (or group sharing a rule):
1. **Root cause** — why the violation exists
2. **Solution** — concrete fix (file, change, or exception exclusion string)
3. **Scope** — how many components affected
4. **Priority** — blocking release? has exception? informational?

Use the violation's own "Solution" field — it often has specific fix steps and exclusion strings.

### Step 5: Present assessment

```
## Conforma Triage Assessment

| # | Component | Rule | Violations | Exception | Tracked | AI Conf. | Solution |
|---|-----------|------|------------|-----------|---------|----------|----------|
| 1 | comp-a    | hermetic_task | 4 | NO | #5 | 95% | Add hermetic param |
| 2 | comp-b    | labels | 11 | NO | — | 82% | Add labels to Dockerfile |
```

Detailed diagnosis per root cause group.

### Step 6: Track ALL untracked violations

**CRITICAL: Every violation must be tracked in `ic triage`.** This maintains state across sessions and enables reporting.

**MCP (preferred):** `mcp__ic__track_triage_item(component=<name>, group_label="rule", root_cause="cause")`

**CLI (fallback):**
```bash
ic triage track <component> --group "rule" --cause "root cause" 2>/dev/null
```

Group components sharing the same rule:
```bash
ic triage track <first-comp> --group "base_image_registries" --cause "untrusted base image" 2>/dev/null
ic triage track <second-comp> --add-to <item-id> 2>/dev/null
```

**After ANY action (Jira, Slack, exception), update the triage item:**

**MCP:** `mcp__ic__update_triage_item(item_id=<id>, jira_key="RHOAIENG-XXXXX")`

**CLI:**
```bash
ic triage update <id> --jira RHOAIENG-XXXXX 2>/dev/null
ic triage update <id> --slack "https://..." 2>/dev/null
```

### Step 7: Ask user what to do

For each actionable violation (or group):
- **Create Jira ticket** — `ic export <component> jira`
- **Send Slack message** — `ic export <component> slack`
- **Request exception** — show exact exclusion string from violation data
- **Skip**

Show-then-confirm: generate content, show to user, ask approval before sending.

### Step 8: Summary

```bash
ic triage report 2>/dev/null
```

```
## Conforma Triage Complete

Diagnosed:
- Group A (labels, 2 components): [cause + solution] — Triage #N
- Group B (base_image, 4 components): [cause + solution] — Triage #M

Actions:
- Jira generated: comp-a (RHOAIENG-XXXXX)
- Triage items created: #N, #M

Already covered (exceptions):
- comp-e: full policy exception

Pending:
- comp-g: needs investigation — Triage #Q
```

## Notes

- ALL data from `ic` / MCP tools — never invent data
- Do NOT auto-send Jira/Slack — show and let user decide
- Run `ic ai analyze` proactively — don't just report it's missing
- ALWAYS track violations in `ic triage` — this is how state persists
- Update triage items with Jira keys and Slack URLs after creating them
- Use `ic` (not `./ic`) — installed via PyPI or available at project root
- AI analysis in cluster mode: LLM local, results to cluster
- When multiple components share a rule, investigate one deeply and apply to group
- Use the violation's own "Solution" field and exclusion strings
