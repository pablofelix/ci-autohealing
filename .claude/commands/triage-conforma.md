Triage conforma violations: investigate, analyze, assess, and take action.

Usage: /triage-conforma [component-name]

- No argument: full scan of all active conforma violations
- With component: investigate that specific component only

---

## Instructions

You are triaging Konflux CI/CD **Conforma (Enterprise Contract) violations** for the RHOAI project. Follow this workflow step by step. Use the `ic` CLI tool for ALL data — never hardcode or guess failure data.

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

From the output, focus on **conforma violations only** (ignore build failures):
- Total conforma failures (count and component names)
- Root cause groups (which violations share the same rule / scenario)
- Which violations already have exceptions

Present a brief summary:
```
## Current Conforma Violations
- X conforma violations
- Y root cause groups (list the groups briefly)
- Z violations with existing exceptions
```

### Step 2: Investigate each conforma violation

For each conforma violation from the alert list, gather data:

```bash
./ic describe conforma <component> 2>/dev/null   # Violation rules, affected images, solutions
```

Key data to extract per violation:
- Violated rule(s) (e.g., hermetic_build_task, labels, test.no_test_warnings)
- Violation reason / message
- Affected images
- Whether it has an exception (YES/PARTIAL/NO from alerts)
- Whether AI analysis exists
- Suggested solution from the violation data

### Step 3: Get AI analysis

Check what analysis already exists:
```bash
./ic ai status 2>/dev/null
```

For violations that have AI analysis, query the full recommended fix:
```bash
./ic db query "SELECT c.component_name, a.failure_category, a.confidence_score, a.root_cause, a.recommended_fix, a.can_auto_fix FROM ai_analysis a JOIN conforma_results c ON a.conforma_result_id = c.id WHERE c.component_name = '<component>' ORDER BY a.created_at DESC LIMIT 1" 2>/dev/null
```

For violations WITHOUT analysis that have violation_summary data:
- Run `./ic ai analyze <component>` to generate analysis
- Then query the results as above

### Step 4: Assess each violation

For each violation, classify it using **exception-aware** logic:

| Classification | Criteria | Suggested Action |
|---|---|---|
| **Actionable — PR** | AI confidence >= 80%, can_auto_fix = true, NO exception | Generate PR fix |
| **Actionable — Jira** | AI confidence >= 70%, clear root cause, NO exception | Create Jira ticket |
| **Partially covered** | Has PARTIAL exception — some violations remain | Action on uncovered violations |
| **Already covered** | Has YES (policy) exception — fully excepted | Note as covered, skip |
| **Needs investigation** | AI confidence < 60%, or no analysis | Manual investigation needed |

Check the Exception column from `ic get alerts`:
- **YES (policy)**: fully excepted — likely covered, skip
- **PARTIAL**: partially covered — investigate uncovered violations
- **NO**: no exception — needs action

### Step 5: Present the assessment

Show a summary table:

```
## Conforma Triage Assessment

| # | Component | Rule | Exception | AI Conf. | Action |
|---|-----------|------|-----------|----------|--------|
| 1 | comp-a    | hermetic_task | NO | 95% | PR fix possible |
| 2 | comp-b    | labels | PARTIAL | 82% | Jira ticket |
| 3 | comp-c    | test_warnings | YES | — | Covered |
| ...
```

Then for each actionable violation, briefly explain:
- What the violated rule requires
- What the fix would be
- Whether an exception might be appropriate (e.g., upstream dependency)

### Step 6: Ask user what to do

Use AskUserQuestion to ask the user what actions to take. Group violations when they share a root cause rule.

For each actionable violation (or group), present these options:
- **Create Jira ticket** — generate with `ic export <component> jira`
- **Send Slack message** — generate with `ic export <component> slack`
- **Generate PR fix** — suggest user runs `./ic fix <N>`
- **Request exception** — if the violation is upstream / out of scope
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

### Step 7: Summary

After processing all violations, show:

```
## Conforma Triage Complete

Actions taken:
- Jira content generated for: comp-a, comp-b
- Slack messages generated for: comp-c
- PR fix suggested for: comp-d (run: ./ic fix 5)

Already covered (exceptions):
- comp-e: YES (policy) exception
- comp-f: YES (policy) exception

Still pending:
- comp-g: needs manual investigation (low AI confidence)

Next steps:
- Run pending AI analysis: ./ic ai analyze --all
- Check exception status: ./ic get exceptions
- View conforma scenarios: ./ic conforma scenarios
```

## Notes

- ALL failure data must come from `ic` commands — never hardcode or invent data
- Do NOT create PRs or send Jira tickets automatically — always show and let user decide
- Do NOT run `ic fix` directly — it's interactive, suggest user runs it
- When multiple violations share a root cause rule, present them together with a single action
- Pay attention to exception status — fully excepted violations don't need action
