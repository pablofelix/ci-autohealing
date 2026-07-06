Triage build failures: investigate, analyze, assess, and take action.

Usage: /triage-build [component-name]

- No argument: full scan of all active build failures
- With component: investigate that specific component only

---

## Instructions

You are triaging Konflux CI/CD **build failures** for the RHOAI project. Your primary goal is to **find the root cause and solution** for each failure, not just classify them.

Use MCP tools (`mcp__ic__*`) for structured data, CLI (`ic`) as fallback. Works in both local and cluster mode.

The working directory is: .

### Educational Mode

When presenting findings, explain Konflux concepts in simple terms for engineers who may not be familiar with the platform. Use the Konflux docs MCP to look up and cite documentation links.

**How to fetch docs:**
1. `mcp__konflux-docs__list_doc_sources()` — get available doc URLs
2. `mcp__konflux-docs__fetch_docs(url)` — fetch specific doc page

**Key concepts to explain when they appear:**
- **PipelineRun**: A Kubernetes resource representing a single build execution. Contains multiple TaskRuns (steps) that run sequentially or in parallel. Status can be Completed, Failed, or PipelineRunTimeout.
- **TaskRun**: A single step within a PipelineRun (e.g., `clone`, `build-images`, `push`). When a build fails, the failed TaskRun tells you which step broke.
- **Hermetic builds**: Konflux builds run without network access by default. All dependencies must be pre-fetched (prefetch step) before the build starts. This ensures reproducibility.
- **Prefetch dependencies**: The `prefetch-dependencies` TaskRun resolves and downloads all dependencies (Go modules, pip packages, npm packages) before the hermetic build step runs. Failures here often mean a dependency can't be resolved.
- **PipelineRunTimeout**: The build exceeded its time limit. The container image may be partially pushed to Quay, but source-build never completes — leading to guaranteed source_image.exists violations downstream.
- **Component CR**: Defines what to build — the git repo, branch, Containerfile path, and build pipeline to use.

Format explanations as:
> **What is [concept]?** Brief explanation. [Learn more](doc-url)

Fetch relevant Konflux documentation pages when explaining. Don't explain every concept every time — explain when a concept first appears or is central to the failure.

### Step 1: Check triage state and scan alerts

**Using MCP tools (preferred):**
- `mcp__ic__list_alerts()` — all current failures and violations
- `mcp__ic__get_triage()` — tracked triage items

**Using CLI (fallback):**
```bash
ic triage show 2>/dev/null
ic get alerts 2>/dev/null
ic get alerts --group 2>/dev/null
```

If the user provided a specific component as `$ARGUMENTS`, skip the full scan and go directly to Step 2.

Present a brief summary:
```
## Current Build Failures
- X build failures (Y already tracked, Z untracked)
- N root cause groups
- Tracked items: [list with Jira keys if any]
```

### Step 2: Investigate each build failure

**Using MCP tools (preferred):**
- `mcp__ic__get_failure(component=<name>)` — full details + logs + commit context
- `mcp__ic__get_component_history(component=<name>)` — build history timeline

**Using CLI (fallback):**
```bash
ic describe component <name> 2>/dev/null
ic describe component <name> --log 2>/dev/null
```

**Key data to extract:**
- Failed step and error message (logs are auto-filtered to show error context)
- Build history — new failure or chronic?
- AI analysis status
- Jira ticket linked?
- Triggering commit

### Step 3: Run AI analysis for EVERY failure without it

**For individual failures:**
```bash
ic ai analyze component <name> 2>/dev/null
```

**For all pending at once:**
```bash
ic ai batch 2>/dev/null
```

In cluster mode, the LLM runs locally using your Vertex AI credentials. Results upload to the cluster automatically.

Run multiple `ic ai analyze` in parallel for different components.

**Check existing analysis:**
- MCP: `mcp__ic__get_analysis(component=<name>)`
- CLI: `ic ai status 2>/dev/null`

### Step 4: Synthesize solutions

For each failure, combine all evidence:
1. **Root cause** — what is broken and why
2. **Solution** — concrete fix steps
3. **Confidence** — AI confidence score
4. **Owner** — infra / code / config issue

Group failures by shared root cause.

### Step 5: Present assessment

```
## Build Triage Assessment

| # | Component | Failed Step | Root Cause | AI Conf. | Tracked | Solution |
|---|-----------|-------------|------------|----------|---------|----------|
| 1 | comp-a    | init-task   | SA missing | 90%      | #3      | Create SA |
| 2 | comp-b    | build-images| dep issue  | 85%      | —       | Add to prefetch |
```

Detailed diagnosis per failure or group.

### Step 6: Track untracked failures

**MCP:** `mcp__ic__track_triage_item(component=<name>, group_label="label", root_cause="cause")`

**CLI:**
```bash
ic triage track <component> --group "label" --cause "description" 2>/dev/null
ic triage track <second-comp> --add-to <item-id> 2>/dev/null
```

### Step 7: Ask user what to do

For each actionable failure:
- **Create Jira ticket** — `ic export <component> jira`
- **Send Slack message** — `ic export <component> slack`
- **Auto-fix PR** — `ic fix <component> --execute` (high confidence only)
- **Retrigger build** — if transient issue
- **Skip**

Show-then-confirm: generate content, show to user, ask approval before sending.

**After ANY action, update the triage item:**

**MCP:** `mcp__ic__update_triage_item(item_id=<id>, jira_key="RHOAIENG-XXXXX")`

**CLI:**
```bash
ic triage update <id> --jira RHOAIENG-XXXXX 2>/dev/null
ic triage update <id> --slack "https://..." 2>/dev/null
```

### Step 8: Summary

```bash
ic triage report 2>/dev/null
```

## Notes

- ALL data from `ic` / MCP tools — never invent data
- Do NOT auto-send Jira/Slack — show and let user decide
- Run `ic ai analyze` proactively — don't just report it's missing
- Logs are auto-filtered by error patterns — `--log` for full output
- AI analysis in cluster mode: LLM local, results to cluster
- ALWAYS track failures in `ic triage`
- Use the Konflux docs MCP to explain concepts — your audience may not know Konflux
