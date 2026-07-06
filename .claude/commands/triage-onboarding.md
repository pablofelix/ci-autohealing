Triage onboarding blockers: investigate, diagnose, and resolve component onboarding issues.

Usage: /triage-onboarding [component-name]

- No argument: scan all incomplete onboardings and prioritize
- With component: investigate that specific component only

---

## Instructions

You are triaging **component onboarding blockers** for the RHOAI project. Your primary goal is to **find the root cause and resolve** each blocker — not just classify them.

Use MCP tools (`mcp__ic__*`) for structured data, CLI (`ic`) as fallback. Works in both local and cluster mode.

The working directory is: .

### Educational Mode

When presenting findings, explain onboarding concepts in simple terms. Use the Konflux docs MCP to look up and cite documentation links.

**How to fetch docs:**
1. `mcp__konflux-docs__list_doc_sources()` — get available doc URLs
2. `mcp__konflux-docs__fetch_docs(url)` — fetch specific doc page

**Key concepts to explain when they appear:**
- **Onboarding two-phase model**: Components go through Konflux setup (9 steps: repository, branch, container, application, component, pipeline, build, EC policy, push pipeline) and then Jira automation (14 steps driven by the devtestops bot via labels on RHOAIENG tickets).
- **Automation steps**: The Jira bot executes steps sequentially: yaml_attached, schema_validated, quay_repo, okc_pr, krd_mr, tekton_pr, bundle_integration, operator_pr, delivery_repo, release_validation, auto_merge, product_listing, renovate, rkc. Each creates PRs or configs that must be reviewed/merged.
- **Bot error patterns**: The bot can get stuck with retry storms, branch conflicts, cluster connectivity issues, missing Dockerfiles, vendor inconsistencies, etc. These are visible in Jira comments.
- **Nudge PRs**: Dependency update PRs created during onboarding (e.g., updating operator repo to reference the new component). These must be merged for onboarding to complete.

Format explanations as:
> **What is [concept]?** Brief explanation. [Learn more](doc-url)

### Step 0: Survey onboarding landscape

**Using MCP tools (preferred):**
- `mcp__ic__get_onboarding_status()` — all components' onboarding status
- If a specific component was given as `$ARGUMENTS`, skip to Step 1

**Using CLI (fallback):**
```bash
ic onboard status 2>/dev/null
```

Present overview:
```
## Onboarding Landscape

- Total components: N
- Fully onboarded: X
- Partially onboarded: Y (have blockers)
- Not started: Z

Blocked components (prioritized by progress):
| Component | Konflux | Automation | Phase | Blocked At |
|-----------|---------|------------|-------|------------|
| comp-a    | 9/9     | 8/14       | automation | krd_mr |
| comp-b    | 7/9     | 0/14       | konflux    | pipeline |
```

### Step 1: Investigate each blocked component

**Using MCP tools (preferred):**
- `mcp__ic__get_onboarding_describe(component=<name>)` — full step-by-step report
- `mcp__ic__analyze_onboarding(component=<name>)` — AI analysis of the blocker

**Using CLI (fallback):**
```bash
ic onboard describe <component> 2>/dev/null
ic ai analyze onboarding <component> 2>/dev/null
```

**Key data per component:**
- Which phase: Konflux setup or Jira automation?
- Which step is blocked and why?
- Bot error history — look for retry storms or stuck steps
- PR links — any PRs waiting for review?
- Jira tickets — RHOAI and ODH onboarding tickets
- Nudge PRs — dependency updates pending merge

### Step 2: Classify the blocker

For each component, determine the blocker category:

**Automation stuck** — bot retrying a step with errors:
- Check bot error timeline for the pattern
- Common: retry_storm, cluster_connectivity, workflow_dispatch failures
- Fix: often a nudge (retry) or fixing the upstream error

**PR review needed** — a PR was created but not reviewed/merged:
- Check PR links from the describe output
- PRs in: okc (operator-config), krd (konflux-release-data), tekton, operator repo
- Fix: ping the PR reviewer or merge if approved

**Missing prerequisite** — a step needs something that doesn't exist yet:
- Quay repository not created
- Component not registered in Konflux application
- Dockerfile missing in the source repo
- Fix: create the missing resource

**Configuration error** — bad config in YAML attachment or pipeline:
- schema_validated failures, vendor inconsistencies
- Fix: update the YAML and re-trigger

**Branch conflict** — component branch already exists or targets wrong ref:
- branch_exists errors from the bot
- Fix: resolve the branch conflict or update the target

**Infrastructure issue** — cluster, API, or tooling problems:
- cluster_connectivity, http_422, CI environment errors
- Fix: usually transient — wait and retry

**First build failing** — onboarding complete but initial build fails:
- Pipeline errors, dependency resolution, hermetic build issues
- Fix: investigate build failure (may need /triage-build)

**Manual intervention** — bot can't proceed without human action:
- PR merge conflicts, approval gates, manual config steps
- Fix: specific to the situation

**Upstream dependency** — blocked by another component or team:
- Component depends on another that isn't onboarded yet
- Fix: onboard the dependency first

### Step 3: Run AI analysis for EVERY blocked component without it

**Check existing analysis:**
- MCP: `mcp__ic__analyze_onboarding(component=<name>)`

**Run analysis:**
```bash
ic ai analyze onboarding <component> 2>/dev/null
```

Review the AI output:
- Root cause — does it match your manual assessment?
- Recommended fix — is it actionable?
- Confidence — if < 70%, investigate manually
- Can auto-fix — if true, try the suggested fix

### Step 4: Synthesize resolution plan

For each blocker (or group sharing a root cause), produce a resolution guide:

```
### Blocker: <category> on <component>

**Root cause:** Why onboarding is stuck (1-2 sentences)

**How to fix it:**
1. Step-by-step fix instructions
2. With specific ic commands
3. PR links to review/merge
4. Jira updates needed

**Next action:**
- What to do RIGHT NOW to unblock
- Who needs to act (you, bot, reviewer, team)

**Priority:** [blocking release / can wait / informational]
**Estimated effort:** low/medium/high
```

### Step 5: Take action

For each actionable blocker, suggest the resolution:

**Check current progress and next action:**
```bash
ic onboard describe <component> 2>/dev/null
```
The output includes a "→ Next:" line showing the computed next action.

**Common resolution actions:**
- **Retry automation** — nudge the Jira ticket (re-add the onboarding label)
- **Review PR** — follow the PR link and review/merge
- **Fix configuration** — update YAML attachment on the Jira ticket
- **Create Jira ticket** — for issues needing team attention
- **Escalate** — for infrastructure or upstream issues

**Cross-reference with Jira:**
```bash
# Check the onboarding Jira ticket status
ic onboard describe <component> 2>/dev/null  # shows linked Jira keys
```
Use `mcp__plugin_atlassian_atlassian__getJiraIssue()` to check Jira details when relevant.

### Step 6: Track ALL unresolved blockers

**CRITICAL: Every blocker must be tracked via `ic triage`.** This maintains state across sessions.

**MCP (preferred):** `mcp__ic__track_triage_item(component=<name>, group_label="onboarding", root_cause="cause")`

**CLI (fallback):**
```bash
ic triage track <component> --group "onboarding" --cause "root cause" 2>/dev/null
```

Group components sharing the same root cause:
```bash
ic triage track <first-comp> --group "onboarding-pr-review" --cause "PRs waiting review" 2>/dev/null
ic triage track <second-comp> --add-to <item-id> 2>/dev/null
```

**After ANY action, update the triage item:**
```bash
ic triage update <id> --jira RHOAIENG-XXXXX 2>/dev/null
```

### Step 7: Summary

```
## Onboarding Triage Complete

Diagnosed:
- Group A (automation_stuck, 3 components): [cause + fix] — Triage #N
- Group B (pr_review, 2 components): [cause + PRs] — Triage #M

Resolved during triage:
- comp-x: retried automation, now progressing
- comp-y: merged blocking PR

Pending human action:
- comp-z: needs config fix by team — Triage #Q

Fully onboarded:
- comp-a, comp-b (no issues)

Onboarding progress: X/Y components complete (Z%)
```

### Step 8: Run regression to improve

After completing triage, run the regression tester to measure how well the analyzer predicted the blockers:

```bash
ic ai regression onboarding 2>/dev/null
```

Review the improvements section and apply any suggestions to the analyzer or infrastructure.

## Notes

- ALL data from `ic` / MCP tools — never invent data
- Do NOT auto-send Jira/Slack — show and let user decide
- Run `ic ai analyze onboarding` proactively — don't just report it's missing
- ALWAYS track blockers in `ic triage` — this is how state persists
- Use `ic` (not `./ic`) — installed via PyPI or available at project root
- The onboarding has two phases: Konflux setup (9 steps) + Automation (14 steps)
- Bot errors are visible in Jira comments — check the error timeline
- PRs created by the bot need human review — check PR links
- Use `_compute_next_action()` output ("→ Next:" line) to prioritize
- Cross-reference with Jira to verify ticket status and assignments
- When multiple components share a root cause, investigate one deeply and apply to group
