Triage conforma violations: investigate, analyze, assess, and take action.

Usage: /triage-conforma [component-name]

- No argument: full scan of all active conforma violations
- With component: investigate that specific component only

---

## Instructions

You are triaging Konflux CI/CD **Conforma (Enterprise Contract) violations** for the RHOAI project. Your primary goal is to **find the root cause and solution** for each violation, not just classify them.

Use MCP tools (`mcp__ic__*`) for structured data, CLI (`ic`) as fallback. Works in both local and cluster mode.

The working directory is: .

### Educational Mode

When presenting findings, explain Konflux concepts in simple terms for engineers who may not be familiar with the platform. Use the Konflux docs MCP to look up and cite documentation links.

**How to fetch docs:**
1. `mcp__konflux-docs__list_doc_sources()` — get available doc URLs
2. `mcp__konflux-docs__fetch_docs(url)` — fetch specific doc page

**Key concepts to explain when they appear:**
- **Enterprise Contract (EC) / Conforma**: A policy engine that validates container images before they can be released. It checks that images meet security, provenance, and compliance requirements defined in policy files.
- **EC Policy file**: A YAML file (hosted on GitLab/GitHub) listing which rules to enforce and which to exclude. Stage and prod targets often use different policies with different exclusion sets.
- **Exceptions / Exclusions**: Rules can be excluded from a policy file so violations don't block the release. Exceptions should be temporary — the root cause should be fixed upstream. Never default to requesting exceptions; fix the root cause instead.
- **Violated rules**: Common rules include:
  - `source_image.exists` — every built image must have a corresponding source image
  - `labels.required_labels` — Dockerfiles must include required OCI labels
  - `hermetic_task.hermetic` — builds must run in hermetic (network-isolated) mode
  - `base_image_registries.allowed` — base images must come from trusted registries
- **SLSA provenance**: Build attestations proving where and how an image was built. Required for supply chain security compliance.
- **IntegrationTestScenario (ITS)**: Defines which Conforma/EC tests run for which applications and contexts (push, release).

Format explanations as:
> **What is [concept]?** Brief explanation. [Learn more](doc-url)

Fetch relevant Konflux documentation pages when explaining. Don't explain every concept every time — explain when a concept first appears or is central to the violation.

### Step 0: Choose application and cross-reference with reporter

**Determine which application to triage.** The user may specify an app (e.g., rhoai-v3-5, rhoai-v3-5-ea-2). If not specified, use the default from config.

**Important:** `ic get conforma --all` defaults to the configured application (often an EA milestone). The conforma-reporter at GitHub runs against the **GA application** with **future policy**. These are DIFFERENT datasets. Always clarify which one the user wants.

**Cross-reference with conforma-reporter (when available):**

The reporter generates a comprehensive violation guide at `red-hat-data-services/conforma-reporter` repo, branch `rhoai-<major>.<minor>` (e.g., `rhoai-3.5`), file `prod/conforma-status-and-resolution-guide.md`.

```bash
# Fetch the reporter's guide (uses GITHUB_TOKEN from env)
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  "https://raw.githubusercontent.com/red-hat-data-services/conforma-reporter/rhoai-3.5/prod/conforma-status-and-resolution-guide.md" \
  | head -100
```

**Compare IC data with reporter:**
- Does IC see the same components as the reporter?
- Are there violations in the reporter that IC missed? (coverage gap)
- Are there violations in IC that the reporter doesn't show? (may be EA-only)

If there's a mismatch, flag it — this is a data completeness issue worth investigating.

### Step 0b (Optional): Evaluate future policy proactively

Before triaging, consider running a future policy evaluation to catch violations that will appear when the policy is updated. This replicates the conforma-reporter's comprehensive view from cluster data.

```bash
# Quick: evaluate a single component (~20s)
ic conforma evaluate --component <name> 2>/dev/null

# Full: evaluate all components against future policy (~40 min)
ic conforma evaluate 2>/dev/null

# Evaluate against stage policy instead
ic conforma evaluate --policy stage 2>/dev/null
```

Results are saved to the DB with `is_future=True`. After evaluation, use `ic get conforma --all` to see both current and future violations together.

**Use the rule catalog** to look up solutions for any rule:
```bash
ic conforma catalog --search <rule-name> 2>/dev/null   # look up rule details + reporter solutions
ic conforma catalog --stats 2>/dev/null                 # catalog overview (189 rules)
```

### Step 0c: Check resolved patterns and configuration health

**Check what similar violations were resolved before:**
- MCP: `mcp__ic__get_resolved_patterns()` — shows resolution patterns grouped by rule
- This tells you which violations are typically transient (fixed by rebuild) vs structural

**Check EC policy and scenario configuration:**
- MCP: `mcp__ic__get_ec_policy_summary()` — active/expired/expiring exceptions, policy gap
- MCP: `mcp__ic__get_scenario_coverage()` — ITS scenario gaps, disabled scenarios

This context helps prioritize: if a violation type is typically resolved quickly, it's likely transient. If a policy exception is about to expire, that's urgent.

### Step 1: Check triage state and scan conforma landscape

**Using MCP tools (preferred):**
- `mcp__ic__list_alerts()` — all current failures and violations
- `mcp__ic__get_triage()` — tracked triage items
- `mcp__ic__get_conforma_report()` — violation summary per component

**Using CLI (fallback):**
```bash
ic triage show 2>/dev/null
ic get alerts 2>/dev/null
ic get conforma --all 2>/dev/null
ic conforma categories 2>/dev/null
```

**Tip:** Use `--all` with `ic get conforma` to include future policy violations from `ic conforma evaluate` results.

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
- Reporter match: [X/Y violations match reporter] (if cross-referenced)
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
ic conforma catalog --search <rule-name> 2>/dev/null   # look up rule description + fix
```

**Rule catalog lookup:** For each violated rule, check `ic conforma catalog --search <rule>` to get the rule's description, documentation URL, and reporter solution (fix steps). This often contains the exact fix or exclusion string needed.

**Jira cross-reference:** If the violation has a linked Jira key, verify the Jira is accurate:
- Does the Jira summary match this specific violation (component, rule, version)?
- Is the Jira status current (not stale "New" from months ago)?
- Is the Jira assigned and has a fixVersion?

Use `mcp__plugin_atlassian_atlassian__getJiraIssue()` to check Jira details.

**Key data per violation:**
- Violated rule(s) (e.g., `hermetic_task.hermetic`, `labels.required_labels`)
- Violation reason and suggested solution (Conforma provides fix hints)
- Affected images (single vs multi-arch — same violation × 4 arches?)
- Violations vs warnings vs successes
- Exception status (YES/PARTIAL/NO)
- How long failing (Since column)
- Linked Jira (accurate? current?)

When violations share a rule across components, investigate one deeply and apply to all.

### Step 3: Classify transient vs structural + auto-rebuild candidates

**Transient violations (rebuild likely fixes):**
- `builtin.attestation.signature_check` — signing failures
- `slsa_source_correlated.source_code_reference_provided` — provenance gaps
- `tasks.successful_pipeline_tasks` (fips-check timeout) — task timeouts
- `test.no_erred_tests` (snyk-check) — transient task errors
- `rpm_packages.unique_version` — multi-arch RPM sync issues

**Structural violations (rebuild won't fix):**
- `sbom_spdx.disallowed_package_attributes` — binary wheels in Python deps
- `labels.required_labels` — missing labels in Dockerfile
- `hermetic_task.hermetic` — pipeline config change needed
- `rpm_repos.ids_known` — unknown repo IDs in SBOM

For transient violations, recommend rebuild:
```bash
ic rebuild <component> --dry-run   # show what would happen
ic rebuild <component>             # trigger actual rebuild
```

### Step 4: Run AI analysis for EVERY violation without it

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

### Step 5: Synthesize solutions with resolution guide format

For each violation (or group sharing a rule), produce a **resolution guide**:

```
### Violation: <rule_name> on <component>

**Root cause:** Why the violation exists (1-2 sentences)

**How to fix it:**
1. Step-by-step fix instructions
2. With specific commands or file changes
3. Include rebuild if applicable
4. Include exception path as last resort (with exact exclusion string)

**Can I fix it?**
- What IC/automation CAN do (e.g., trigger rebuild, check config)
- What requires HUMAN action (e.g., modify signing keys, approve exception)
- Estimated effort: low/medium/high

**Priority:** [blocking release / has exception / informational]
**Scope:** N components affected
**Similar resolved:** [from get_resolved_patterns — how this was fixed before]
```

Use the violation's own "Solution" field — it often has specific fix steps and exclusion strings.
Cross-reference with `ic conforma catalog --search <rule>` for reporter-verified solutions from the 189-rule catalog.

### Step 6: Present assessment

```
## Conforma Triage Assessment

| # | Component | Rule | Violations | Exception | Tracked | AI Conf. | Fix Type | Solution |
|---|-----------|------|------------|-----------|---------|----------|----------|----------|
| 1 | comp-a    | hermetic_task | 4 | NO | #5 | 95% | config | Add hermetic param |
| 2 | comp-b    | signature_check | 1 | NO | — | 90% | rebuild | `ic rebuild comp-b` |
```

Then detailed resolution guide per root cause group.

### Step 7: Track ALL untracked violations

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

### Step 8: Ask user what to do

For each actionable violation (or group):
- **Trigger rebuild** — `ic rebuild <component>` (for transient violations)
- **Create Jira ticket** — `ic export <component> jira`
- **Send Slack message** — `ic export <component> slack`
- **Request exception** — show exact exclusion string from violation data
- **Skip**

Show-then-confirm: generate content, show to user, ask approval before sending.

### Step 9: Summary

```bash
ic triage report 2>/dev/null
```

```
## Conforma Triage Complete

Diagnosed:
- Group A (labels, 2 components): [cause + solution] — Triage #N
- Group B (base_image, 4 components): [cause + solution] — Triage #M

Auto-rebuild candidates:
- comp-x: `ic rebuild comp-x` (transient signature_check)
- comp-y: `ic rebuild comp-y` (transient fips-check timeout)

Actions:
- Jira generated: comp-a (RHOAIENG-XXXXX)
- Triage items created: #N, #M

Already covered (exceptions):
- comp-e: full policy exception

Pending:
- comp-g: needs investigation — Triage #Q

Reporter match: X/Y violations covered by IC
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
- Use the Konflux docs MCP to explain concepts — your audience may not know Konflux
- Cross-reference with conforma-reporter when triaging GA releases
- Verify linked Jiras are accurate (component, rule, version match)
- Classify violations as transient vs structural — recommend rebuild for transient ones
- Use `get_resolved_patterns()` to check how similar violations were fixed before
- Present fixes in resolution guide format (steps + "can I fix it?" + priority)
