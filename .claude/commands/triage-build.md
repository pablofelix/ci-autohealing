Triage build failures: investigate, analyze, assess, and take action.

Usage: /triage-build [component-name]

- No argument: full scan of all active build failures
- With component: investigate that specific component only

---

## Instructions

You are triaging Konflux CI/CD **build failures** for the RHOAI project. Follow this workflow step by step. Use the `ic` CLI tool for ALL data — never hardcode or guess failure data.

The working directory is: PROJECT_DIR

### Step 1: Scan the current alert landscape

Run these two commands and parse their output:

```bash
./ic get alerts 2>/dev/null
```

```bash
./ic get alerts --group 2>/dev/null
```

If the user provided a specific component as `$ARGUMENTS`, skip this step and go directly to Step 2 for that component only.

From the output, focus on **build failures only** (ignore conforma violations):
- Total build failures (count and component names)
- Root cause groups (which failures share the same failed step / error type)
- In-progress builds (components currently building)
- Which failures already have Jira tickets linked

Present a brief summary:
```
## Current Build Failures
- X build failures
- Y root cause groups (list the groups briefly)
- Z failures already have Jira tickets
```

### Step 2: Investigate each build failure

For each build failure from the alert list, gather data using these `ic` commands. Run them in parallel where possible.

```bash
./ic <N> 2>/dev/null                         # Full component details + build history
./ic describe component <name> 2>/dev/null   # Detailed component info
./ic describe component <name> --log 2>/dev/null  # Complete logs if needed
```

Key data to extract per failure:
- Failed step (e.g., init-task, fips-check, build-images)
- Error message from logs
- How long it's been failing (build history timeline)
- Whether logs are available
- Whether AI analysis exists
- Whether it already has a Jira ticket

### Step 3: Get AI analysis

Check what analysis already exists:
```bash
./ic ai status 2>/dev/null
```

For failures that have AI analysis, query the full recommended fix:
```bash
./ic db query "SELECT component_name, failure_category, confidence_score, root_cause, recommended_fix, can_auto_fix FROM ai_analysis a JOIN build_failures b ON a.build_failure_id = b.id WHERE b.component_name = '<component>' ORDER BY a.created_at DESC LIMIT 1" 2>/dev/null
```

For failures WITHOUT analysis that have logs available:
- Run `./ic ai analyze <component>` to generate analysis
- Then query the results as above

For failures without logs: note them as "needs log collection" — do NOT run AI analysis on them.

### Step 4: Assess each failure

For each failure, classify it:

| Classification | Criteria | Suggested Action |
|---|---|---|
| **Actionable — PR** | AI confidence >= 80%, can_auto_fix = true | Generate PR fix |
| **Actionable — Jira** | AI confidence >= 70%, clear root cause, not auto-fixable | Create Jira ticket |
| **Needs investigation** | AI confidence < 60%, or no analysis, or no logs | Manual investigation needed |
| **Already covered** | Has Jira ticket linked | Note as covered, skip |
| **Transient** | AI says infrastructure/retry, already resolved | Skip |

### Step 5: Present the assessment

Show a summary table:

```
## Build Triage Assessment

| # | Component | Failed Step | Root Cause | AI Conf. | Action |
|---|-----------|-------------|------------|----------|--------|
| 1 | comp-a    | init-task  | SA missing | — | Needs logs |
| 2 | comp-b    | fips-check  | base image | 90% | Jira ticket |
| ...
```

Then for each actionable failure, briefly explain:
- What the root cause is
- What the fix would be
- Risk level (high confidence = low risk)

### Step 6: Ask user what to do

Use AskUserQuestion to ask the user what actions to take. Group related failures when they share a root cause.

For each actionable failure (or group), present these options:
- **Create Jira ticket** — generate with `ic export <component> jira`
- **Send Slack message** — generate with `ic export <component> slack`
- **Generate PR fix** — suggest user runs `./ic fix <N>`
- **Skip** — move on

**Show-then-confirm pattern for Jira and Slack:**

When the user selects "Create Jira ticket" or "Send Slack message":
1. Generate: `./ic export <component> jira` (or `slack`)
2. Show the full generated message to the user
3. Ask: "Send as-is", "Edit the message", or "Cancel"
4. If edit: ask what to change, apply, show again, re-ask
5. If send: show the formatted content for the user to copy

When the user selects "Generate PR fix":
- Note that `ic fix <N>` is interactive — suggest the user run it directly
- Explain what it will do (dry-run diff, then ask to push)

### Step 7: Summary

After processing all failures, show:

```
## Build Triage Complete

Actions taken:
- Jira content generated for: comp-a, comp-b
- Slack messages generated for: comp-c
- PR fix suggested for: comp-d (run: ./ic fix 2)

Still pending:
- comp-e: needs log collection first
- comp-f: needs manual investigation (low AI confidence)

Next steps:
- Collect logs: cd collectors/python && python3.11 fetch_archived_logs.py
- Run pending AI analysis: ./ic ai analyze --all
```

## Notes

- ALL failure data must come from `ic` commands — never hardcode or invent data
- Do NOT create PRs or send Jira tickets automatically — always show and let user decide
- Do NOT run `ic fix` directly — it's interactive, suggest user runs it
- When multiple failures share a root cause, present them together with a single action
