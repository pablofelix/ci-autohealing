Triage conforma violations: investigate, analyze, assess, and take action.

Usage: /triage-conforma [component-name]

- No argument: full scan of all active conforma violations
- With component: investigate that specific component only

---

## Instructions

You are triaging Konflux CI/CD **Conforma (Enterprise Contract) violations** for the RHOAI project. Your primary goal is to **find the root cause and solution** for each violation, not just classify them. Use the `ic` CLI tool for ALL data — never hardcode or guess failure data.

The working directory is: .

### Step 1: Scan the current conforma landscape

Run these commands to understand the full picture:

```bash
./ic get alerts 2>/dev/null
```

```bash
./ic get alerts --group 2>/dev/null
```

```bash
./ic get conforma 2>/dev/null
```

```bash
./ic conforma categories 2>/dev/null
```

If the user provided a specific component as `$ARGUMENTS`, skip this step and go directly to Step 2 for that component only.

From the output, focus on **conforma violations only** (ignore build failures):
- Total conforma violations (count and component names)
- Which violation rules are most common (from `conforma categories`)
- Which scenarios are active (from `conforma scenarios`)
- Which violations already have exceptions or Jira tickets

Present a brief summary:
```
## Current Conforma Violations
- X components failing conforma tests
- Y total violations across Z distinct rules
- Top violation categories: [list from conforma categories]
- Exceptions: [count] with exceptions, [count] without
```

### Step 2: Investigate each conforma violation in depth

For each component with violations, gather data using ALL relevant `ic` commands. Run them in parallel where possible. The goal is to understand each violation deeply enough to suggest a concrete fix.

**Core investigation commands (run for every violation):**

```bash
./ic describe conforma <component> 2>/dev/null   # Full violation details: rules, reasons, affected images, solutions
```

**Additional context commands (run as needed):**

```bash
./ic get exceptions 2>/dev/null                  # Check if policy exceptions exist or are expiring
./ic conforma scenarios 2>/dev/null              # Understand which scenarios/policies are active
./ic conforma scenarios --gaps 2>/dev/null       # Detect missing scenarios across apps
```

**Key data to extract per violation:**
- Violated rule(s) (e.g., `base_image_registries.base_image_permitted`, `labels.required_labels`, `hermetic_task.hermetic`)
- Violation reason / message (the actual error text)
- Suggested solution from the violation data (Conforma provides fix hints)
- Affected images (single arch vs multi-arch — same violation across all arches?)
- Total violations vs warnings vs successes (severity gauge)
- Whether it has an exception (YES/PARTIAL/NO)
- Whether it has a Jira ticket linked
- How long it's been failing (Since column)

**When violations share a rule across components, investigate one deeply and apply to all.**

### Step 3: Run AI analysis for EVERY violation that doesn't have it

This is critical — don't just note "AI analysis not available" and move on. Proactively generate analysis.

First, check what analysis already exists:
```bash
./ic ai status 2>/dev/null
```

**For each violation WITHOUT analysis — run it immediately:**
```bash
./ic ai analyze <component-name> 2>/dev/null
```

Run multiple `ic ai analyze` calls in parallel for different components to save time.

**For violations that have AI analysis, query the details:**
Call `mcp__ic__get_analysis(component=<component>)` to get the full AI analysis including category, confidence, root_cause, recommended_fix, and can_auto_fix. Or via CLI:
```bash
./ic why <component-name> 2>/dev/null
```

### Step 4: Synthesize the solution for each violation

For each violation (or group of violations sharing a rule), combine all evidence to produce:

1. **Root cause** — Why this violation exists (e.g., missing labels in Dockerfile, untrusted base image registry, missing SBOM, hermetic build not configured)
2. **Solution** — Concrete steps to fix it:
   - Which file to change (Dockerfile, .tekton pipeline YAML, etc.)
   - What specific change to make (add labels, switch registry, configure task)
   - Whether an exception is the right approach (upstream dependency, infra limitation)
3. **Scope** — How many components are affected by the same root cause
4. **Priority** — Blocking release? Has exception? Informational only?

Use the Conforma violation's own "Solution" field — it often contains the specific fix steps and even tells you which policy exclusion to add if you need an exception.

### Step 5: Present the assessment

Show a summary table:

```
## Conforma Triage Assessment

| # | Component | Rule | Violations | Exception | AI Conf. | Solution |
|---|-----------|------|------------|-----------|----------|----------|
| 1 | comp-a    | hermetic_task | 4 | NO | 95% | Add hermetic param to pipeline |
| 2 | comp-b    | labels | 11 | NO | 82% | Add required labels to Dockerfile |
| ...
```

Then for each root cause group, provide a **detailed diagnosis**:
- What the violated rule requires and why it matters
- What specifically needs to change to fix it
- Whether an exception is appropriate (and the exact exclusion string from the violation data)
- Whether this is a new violation or chronic (from the "Since" date)

### Step 6: Ask user what to do

Use AskUserQuestion to ask the user what actions to take. Group violations when they share a root cause rule.

For each actionable violation (or group), present these options:
- **Create Jira ticket** — generate with `ic export <component> jira`
- **Send Slack message** — generate with `ic export <component> slack`
- **Request exception** — if the violation is upstream / out of scope (show the exact exclusion string)
- **Skip** — move on

**Show-then-confirm pattern for Jira and Slack:**

When the user selects "Create Jira ticket" or "Send Slack message":
1. Generate: `./ic export <component> jira` (or `slack`)
2. Show the full generated message to the user
3. Ask: "Send as-is", "Edit the message", or "Cancel"
4. If edit: ask what to change, apply, show again, re-ask
5. If send: show the formatted content for the user to copy

### Step 7: Summary

After processing all violations, show:

```
## Conforma Triage Complete

Diagnosed:
- Group A (labels.required_labels, 2 components): [root cause + solution]
- Group B (base_image_registries, 4 components): [root cause + solution]
- Group C (cve.cve_results_found, 3 components): [root cause + solution]

Actions taken:
- Jira content generated for: comp-a, comp-b
- Slack messages generated for: comp-c

Already covered (exceptions):
- comp-e: full policy exception

Still pending:
- comp-g: needs manual investigation (reason)

Next steps:
- [Specific actionable items based on the diagnosis]
```

## Notes

- ALL failure data must come from `ic` commands — never hardcode or invent data
- Do NOT create PRs or send Jira tickets automatically — always show and let user decide
- The goal is to FIND THE SOLUTION, not just classify violations
- Run `ic ai analyze` proactively for any violation missing analysis — don't just report it's missing
- Use `ic conforma categories` to understand the violation landscape before diving into individual components
- Use `ic describe conforma <component>` to get the violation's own solution hints and exclusion strings
- When multiple components share the same violated rule, investigate one deeply and apply findings to the group
- Pay attention to exception status — fully excepted violations don't need action
- Use the AI analysis `recommended_fix` field — it often contains specific fix steps
