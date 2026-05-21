Triage build failures: investigate, analyze, assess, and take action.

Usage: /triage-build [component-name]

- No argument: full scan of all active build failures
- With component: investigate that specific component only

---

## Instructions

You are triaging Konflux CI/CD **build failures** for the RHOAI project. Your primary goal is to **find the root cause and solution** for each failure, not just classify them. Use the `ic` CLI tool for ALL data — never hardcode or guess failure data.

The working directory is: .

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

### Step 2: Investigate each build failure in depth

For each build failure, gather data using ALL relevant `ic` commands. Run them in parallel where possible. The goal is to understand the failure deeply enough to suggest a concrete fix.

**Core investigation commands (run for every failure):**

```bash
./ic <N> 2>/dev/null                          # Full component details + build history + logs
./ic history <component-name> 2>/dev/null     # Full build history (when did it start failing? was it ever green?)
```

**Key data to extract per failure:**
- Failed step (e.g., init-task, fips-check, build-images)
- Error message from logs
- How long it's been failing (build history timeline — is this new or chronic?)
- Whether it was ever green (important for new components vs regressions)
- Whether AI analysis exists
- Whether it already has a Jira ticket
- What commit triggered the failure (look at the commit message for clues)

**When you need more detail on the error:**
```bash
./ic describe component <name> --log 2>/dev/null   # Complete logs (not just error context)
```

### Step 3: Run AI analysis for EVERY failure that doesn't have it

This is critical — don't just note "AI analysis not available" and move on. Proactively generate analysis.

First, check what analysis already exists:
```bash
./ic ai status 2>/dev/null
```

**For each failure WITHOUT analysis — run it immediately:**
```bash
./ic ai analyze <component-name> 2>/dev/null
```

Run multiple `ic ai analyze` calls in parallel for different components to save time.

**For failures that have AI analysis, query the full details:**
```bash
./ic db query "SELECT component_name, failure_category, confidence_score, root_cause, recommended_fix, can_auto_fix FROM ai_analysis a JOIN build_failures b ON a.build_failure_id = b.id WHERE b.component_name = '<component>' ORDER BY a.created_at DESC LIMIT 1" 2>/dev/null
```

**For failures without logs:** Try to fetch them on-demand first:
```bash
cd src && python3 fetch_logs.py <pipelinerun-name> 2>/dev/null
```
Then run AI analysis. Only skip if log fetching also fails.

### Step 4: Synthesize the solution for each failure

For each failure, combine all the evidence (logs, AI analysis, build history, commit info) to produce:

1. **Root cause** — What specifically is broken and why
2. **Solution** — Concrete steps to fix it (not just "investigate further")
3. **Confidence** — How sure are we about this diagnosis
4. **Who should fix it** — Is this an infra issue, a code issue, or a config issue?

Group failures by shared root cause — don't repeat the same analysis 5 times for 5 components with the same fips-check failure.

### Step 5: Present the assessment

Show a summary table:

```
## Build Triage Assessment

| # | Component | Failed Step | Root Cause | AI Conf. | Solution |
|---|-----------|-------------|------------|----------|----------|
| 1 | comp-a    | init-task  | SA missing | 90% | Create SA in namespace |
| 2 | comp-b    | fips-check  | base image | 85% | Update base image digest |
| ...
```

Then for each failure (or group), provide a **detailed diagnosis**:
- What the root cause is (with evidence from logs/AI)
- What the concrete fix would be
- Whether this is a regression (it used to work) or a new component issue
- Whether a retry/retrigger might fix it (transient vs persistent)

### Step 6: Ask user what to do

Use AskUserQuestion to ask the user what actions to take. Group related failures when they share a root cause.

For each actionable failure (or group), present these options:
- **Create Jira ticket** — generate with `ic export <component> jira`
- **Send Slack message** — generate with `ic export <component> slack`
- **Retrigger build** — suggest user retriggers if analysis indicates transient issue
- **Skip** — move on

**Show-then-confirm pattern for Jira and Slack:**

When the user selects "Create Jira ticket" or "Send Slack message":
1. Generate: `./ic export <component> jira` (or `slack`)
2. Show the full generated message to the user
3. Ask: "Send as-is", "Edit the message", or "Cancel"
4. If edit: ask what to change, apply, show again, re-ask
5. If send: show the formatted content for the user to copy

### Step 7: Summary

After processing all failures, show:

```
## Build Triage Complete

Diagnosed:
- Group A (fips-check, 5 components): [root cause + solution]
- Group B (build-images, 1 component): [root cause + solution]

Actions taken:
- Jira content generated for: comp-a, comp-b
- Slack messages generated for: comp-c

Still pending:
- comp-e: needs manual investigation (reason)

Next steps:
- [Specific actionable items based on the diagnosis]
```

## Notes

- ALL failure data must come from `ic` commands — never hardcode or invent data
- Do NOT create PRs or send Jira tickets automatically — always show and let user decide
- The goal is to FIND THE SOLUTION, not just classify failures
- Run `ic ai analyze` proactively for any failure missing analysis — don't just report it's missing
- When multiple failures share a root cause, investigate one deeply and apply findings to the group
- Use `ic history <component>` to understand whether this is a regression or a chronic issue
- Use the AI analysis `recommended_fix` field — it often contains the specific fix steps
