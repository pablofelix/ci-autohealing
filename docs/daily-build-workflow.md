# Daily Workflow: Investigating and Fixing Build Failures with `ic`

This document walks through a typical day for someone working on resolving build and Conforma failures in Konflux, using the `ic` CLI tool.

---

## Automated Triage (Recommended)

If you use Claude Code, run the `/triage` skill to automate the full workflow:

```
/triage                              # Full scan of all failures
/triage acme-core-engine-v3-5-ea-1      # Investigate a specific component
```

This will:
1. Scan all active alerts and group by root cause
2. Investigate each failure (build history, error details, conforma violations)
3. Run AI analysis on unanalyzed failures
4. Assess each failure's actionability and confidence level
5. Ask you what to do — create Jira, send Slack, generate PR, or skip
6. Show generated messages and let you edit before sending

The rest of this document covers the manual command-by-command workflow for reference.

---

## 1. Morning Check: What's Broken?

Start by getting the unified alert view:

```bash
ic get alerts
```

This shows:
- **Build Failures**: components whose latest push build failed, with failed step, date, and Jira ticket status
- **Conforma Failures**: Enterprise Contract violations with policy, violation/warning counts, and exception status
- **In Progress**: builds or tests currently running

Example output:

```
Build Failures (2):
 # |               Component                |   Status    | Failed | Builds | JIRA
---+----------------------------------------+-------------+--------+--------+------
 1 | odh-llama-stack-core-v3-5-ea-1         | Push Failed | 18 May |      1 | -
 2 | acme-inference-operator-v3-5-ea-1 | Push Failed | 19 May |      1 | -

Conforma Failures (10):
  #    Component                                Policy      Viol   Warn  Exception
  3    acme-fbc-fragment-v3-5-ea-1             reg-prod      22     35  NO (in reg-stg)
  4    rhai-on-openshift-chart-v3-5-ea-1        reg-prod      16     10  NO (in ch-prod)
  ...
```

### Group by root cause

To see which failures share the same root cause:

```bash
ic get alerts --group
```

This groups build failures by `failed_step + error_type` and conforma failures by scenario, showing how many components are affected by each issue. Useful for prioritization — fix the root cause that unblocks the most components first.

```
Build Failures:
  init-task / Task Failure (2 components)
    "init-task task failed - check Konflux UI for details"
    odh-llama-stack-core-v3-5-ea-1, acme-inference-operator-v3-5-ea-1

Conforma Failures:
  conforma-registry-acme-prod-...-single-component (10 components, 80 total violations)
    odh-ai-gateway-...(4), odh-llama-stack-core-...(4), ...
```

### Check a different version or date

```bash
ic get alerts --all                 # All applications (v3.4, v3.5-ea.1, etc.)
ic get alerts --date yesterday      # What was failing yesterday
ic get alerts --date 2026-05-15     # Specific date
```

---

## 2. Investigate a Build Failure

### Quick look (by number)

The numbered list from `ic get alerts` lets you use shortcut numbers:

```bash
ic 1
```

This shows a full summary of component #1: latest PipelineRun, failed step, error message, commit info, Konflux UI link, and build history from Tekton Results.

Example output:

```
Component: odh-llama-stack-core-v3-5-ea-1

Latest PipelineRun: odh-llama-stack-core-v3-5-ea-1-on-push-9cjr9
Status:     Failed
Repository: https://github.com/acme-org/ogx-distribution
Branch:     acme-3.5-ea.1
Commit:     8f2d9d17
Author:     eoinfennessy
Message:    fix: add file_processors API and `inline::pypdf` provider (#102)

Failure Info:
  Failed Step: init-task
  Error Type:  Task Failure

Build History (Tekton Results):
  2026-05-18 11:53  ✗ failed  (init-task, 8f2d9d17)
  2026-05-15 17:52  ✗ failed  (init-task, 64831c5b)
  2026-05-15 17:21  ✗ failed  (init-task, 0fe94ff2)
  ...
```

### Why is it failing?

```bash
ic why odh-llama-stack-core-v3-5-ea-1
```

Shows the failure reason, commit info, and links. Quick summary when you just need the "why" without the full history.

### Extract errors from logs

```bash
ic errors odh-llama-stack-core-v3-5-ea-1
```

Parses stored build logs and extracts error messages. If logs aren't stored yet (shows "No logs available"), check the Konflux UI link from `ic 1`.

### Full logs

```bash
ic 1 --log
```

Shows the complete build logs (not just the error context). Useful for tracing the full failure sequence.

### Deep analysis

```bash
ic analyze odh-llama-stack-core-v3-5-ea-1
```

Shows a timeline of all builds with their outcomes — helps identify when the failure started and whether it correlates with a specific commit.

### Build history

```bash
ic history odh-llama-stack-core-v3-5-ea-1
```

Shows all builds from the database: successes, failures, and resolution status. Tells you whether this is a new problem or recurring.

---

## 3. Investigate a Conforma Failure

```bash
ic describe conforma odh-llama-stack-core-v3-5-ea-1
```

Shows:
- Which EC scenario failed
- How many violations, warnings, and successes
- Per-image breakdown (multi-arch images show per-architecture results)
- Each violation's rule, reason, and solution

Example:

```
Scenario: conforma-registry-acme-prod-v2-1-ea-1-single-component
Result:   FAILURE (4 violations, 100 warnings, 492 successes)

Violations:
✕ [Violation] trusted_task.trusted
  Reason: Code tampering detected, untrusted PipelineTask "fips-check"
  Solution: Ensure all Tasks in the build Pipeline are trusted...
```

### Check exception status

In the `ic get alerts` output, the Exception column tells you:
- **YES (policy)**: exception already configured in the release policy
- **PARTIAL (policy)**: some violations covered, others not
- **NO (in reg-stg)**: no exception in the prod policy (may exist in staging)

The policy link is shown below each conforma entry — click it to see or edit the exception configuration in GitLab.

### Conforma categories

```bash
ic conforma categories
```

Shows violation categories across all components — helps identify systemic issues (e.g., all components failing `trusted_task`).

---

## 4. AI-Assisted Analysis

### Analyze a single failure

```bash
ic ai analyze odh-llama-stack-core-v3-5-ea-1
```

Sends the failure data to Claude for root cause analysis. Returns: root cause, category, confidence, and recommended fix.

### Analyze all pending

```bash
ic ai analyze --all
```

Runs AI analysis on all unanalyzed failures.

### Check AI analysis status

```bash
ic ai status
```

Shows which failures have been analyzed and which are pending.

---

## 5. Taking Action

### Triage dashboard

```bash
ic triage
```

Shows all currently failing components with their last failure date and whether logs are available. Good starting point to pick what to work on next.

### Export for Jira

```bash
ic export odh-llama-stack-core-v3-5-ea-1 jira
```

Generates a formatted Jira ticket body with: description, prerequisites, steps to reproduce, actual/expected results, commit info, and links. Copy-paste ready.

### Export for Slack

```bash
ic export odh-llama-stack-core-v3-5-ea-1 slack
```

Generates a Slack message to ask for help on a failure. Includes error summary, commit info, and Konflux link.

```bash
ic export odh-llama-stack-core-v3-5-ea-1 slack --jira PROJECT-12345
```

Same message but includes the Jira ticket link.

### Interactive fix flow

```bash
ic fix 1
```

Interactive triage: shows AI recommendation, then prompts you to choose an action (PR, Jira, Slack). Dry-run for PRs (shows diff, doesn't push). Edit loop for Jira (review before posting).

### Export to other formats

```bash
ic export 1 markdown       # GitHub-flavored markdown
ic export 1 json            # Machine-readable JSON
ic export 1 --clipboard     # Copy to clipboard
```

---

## 6. Monitoring Progress

### What's working now?

```bash
ic working
```

Shows components whose last build succeeded — useful to confirm a fix landed.

### What was resolved?

```bash
ic resolved
```

Shows recently resolved components: when they were resolved and how many failures were fixed.

### Daily standup report

```bash
ic report build --summary
```

Copy-paste ready table for standup:

```
ITEM                             CURRENT STATUS                           NEXT STEPS
----                             --------------                           ----------
Build 3.4                        1 component(s) failing                   1 need Jira
Build 3.5-ea.1                   2 component(s) failing                   2 need Jira
```

Full report with per-component detail:

```bash
ic report build
```

### Conforma report

```bash
ic conforma report              # All versions
ic conforma report 3.5-ea-1     # Specific version
ic conforma report --summary    # Summary only
```

### Statistics

```bash
ic stats                  # Total failures, components, coverage
ic stats daily            # Failures by day
ic stats resolved         # Resolution stats
ic stats components       # Failures by component
```

---

## 7. Typical Investigation Flow

**With Claude Code** — run `/triage` and follow the interactive prompts.

**Manual sequence** when investigating without Claude Code:

```
1.  ic get alerts                    # What's broken?
2.  ic get alerts --group            # What root causes affect the most components?
3.  ic 1                             # Investigate the top failure
4.  ic why <component>               # Quick summary of why
5.  ic errors <component>            # Extract error messages
6.  ic 1 --log                       # Full logs if needed
7.  ic history <component>           # Is this new or recurring?
8.  ic describe conforma <component> # Check conforma status too
9.  ic ai analyze <component>        # Get AI root cause analysis
10. ic export <component> jira       # Generate Jira ticket content
11. ic export <component> slack      # Ask for help if needed
12. ic working                       # Confirm fix landed after retrigger
13. ic report build --summary        # Prepare standup summary
```

---

## 8. Version & Configuration

### Switch application version

```bash
ic get apps                     # List available versions
ic config set-app acme-v2-0    # Switch to v3.4
ic get alerts                   # Now shows v3.4 data
```

### Current configuration

```bash
ic config
```

### Custom SQL queries

```bash
ic db query "SELECT component_name, COUNT(*) FROM build_failures WHERE application = 'acme-v2-1-ea-1' GROUP BY component_name ORDER BY count DESC LIMIT 10"
```

---

## Quick Reference

| Task | Command |
|------|---------|
| See all failures | `ic get alerts` |
| Group by root cause | `ic get alerts --group` |
| Investigate #N | `ic N` |
| Why is it failing? | `ic why <component>` |
| Extract errors | `ic errors <component>` |
| Full logs | `ic N --log` |
| Build history | `ic history <component>` |
| Conforma details | `ic describe conforma <component>` |
| AI analysis | `ic ai analyze <component>` |
| Export to Jira | `ic export <component> jira` |
| Export to Slack | `ic export <component> slack` |
| Currently working | `ic working` |
| Resolved list | `ic resolved` |
| Standup report | `ic report build --summary` |
| Switch version | `ic config set-app <app>` |
