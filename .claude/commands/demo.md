Interactive demo of the ic CLI tool and CI Auto-Healing system.

Usage: /demo

---

## Instructions

You are running an interactive, educational demo of **ic** — a kubectl-style CLI and MCP server for monitoring Konflux CI/CD pipelines in Red Hat OpenShift AI (RHOAI). Walk the user through the main features step by step, using the MCP server tools to get live data and presenting it as clean, well-formatted markdown.

### How to Get Data

1. **Preferred**: Use `mcp__ic__*` tools for structured data
2. **Fallback**: Run via Bash if MCP tools aren't available:
   ```bash
   ./ic <command> 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g'
   ```
3. Never make up data — always call the actual tool/command

### Presentation Rules

- **All output in markdown** — tables, code blocks, bold, formatted text. Claude Code renders these natively.
- **One section at a time** — present a focused topic, then navigate.
- **Summarize intelligently** — show the 5-8 most interesting rows from large datasets. Focus on variety and signal, not volume. Highlight outliers, patterns, and critical issues.
- **Explain the "why"** — don't just show features, explain why they matter in a CI ops workflow. Connect features to real problems they solve.
- **Use tables** for structured data (components, alerts, stats).
- **Use code blocks** for logs, commands, and architecture diagrams.

### Chapter Progress Tracker

At **every navigation point**, before presenting options, show a compact progress bar:

```
┌─ ic demo ──────────────────────────────────────────────┐
│  ► 0·Intro  1·Overview  2·Alerts  3·Failures  4·AI      │
│    5·Conforma  6·Export  7·Dashboard  8·Release  9·Arch  │
└────────────────────────────────────────────────────────┘
```

- Mark the current chapter with `►`
- Use `·` as separator for readability
- Keep it compact (2 lines max)
- This appears BEFORE the AskUserQuestion navigation block

### Navigation Consistency

Every chapter ends with **exactly 4 options** via AskUserQuestion:

1. **Continue** — proceed to the next chapter in sequence (label says which chapter is next)
2. **Deep dive** — technology explainer for the current topic (label describes what the deep dive covers, e.g., "Deep dive: What is Konflux?")
3. **View index** — show the full chapter table with one-line descriptions and let user jump to any section
4. **Exit** — jump to the Wrap-up section

When the user selects **Deep dive**, show the "Under the Hood" content for that chapter, then return to the same navigation point (same 4 options, but replace "Deep dive" with "Continue" since they already saw the deep dive).

When the user selects **View index**, show the chapter index table from Section 0 and use AskUserQuestion to let them pick a chapter number.

---

## Pre-Flight Check

Before starting the demo, verify connectivity:

1. Call `mcp__ic__list_applications()`

2. **If it fails**, explain what's needed:
   - Running PostgreSQL database (`task db:start`)
   - `.env` file with `NAMESPACE`, `APPLICATION_NAME`
   - For some features: `oc login` to cluster
   - Ask if user wants to set up first or continue anyway

3. **If it succeeds**, show a brief confirmation:
   - "Connected to ic. Found **N** applications, current: **app-name** with **X** tracked failures."
   - Set the default application to `my-app-v2-1` for demo queries
   - Proceed to Section 0

---

## Section 0: Welcome — The Problem ic Solves

### Opening: Paint the Problem

**Present** this scenario in your own words — make it vivid:

> **Imagine:** You're a release engineer. Monday morning. 69 container images must ship this sprint for Red Hat OpenShift AI 3.5. You log in and discover 11 are broken — build failures, policy violations blocking the pipeline.
>
> You don't know which components are failing. You don't know why they broke. You don't know who to notify. Your Tekton build logs are expiring. The release gate is in 3 days.
>
> You need answers **now**. Which failures are critical? Which are duplicates? What changed in the last commit? Does this match a known pattern? Can we auto-fix it?
>
> **This is the problem ic was built to solve.**

**Explain briefly:**
- Konflux pipelines generate massive amounts of data: build logs, policy checks, git commits, container metadata
- Traditional tools require stitching together 5+ systems to understand one failure
- ic aggregates everything into a single CLI/API, adds AI analysis, and surfaces actionable insights

### Show the Chapter Index

| Ch | Title | What You'll Learn |
|----|-------|-------------------|
| 0 | Welcome | The problem ic solves (you are here) |
| 1 | Overview | What ic is and how it connects to your pipelines |
| 2 | Alert Dashboard | One view of everything that's broken |
| 3 | Failure Inspection | Drilling into a specific failure — logs, commits, root cause |
| 4 | AI Analysis | How the AI engine classifies failures and suggests fixes |
| 5 | Conforma | Supply-chain security policies and why they block releases |
| 6 | Export | Turning data into Jira tickets and Slack messages |
| 7 | Dashboard | The complete picture — trends, costs, patterns |
| 8 | Release Readiness & Security | CVE/SAST scan, freeze calendar, pipeline status |
| 9 | Architecture | How CLI, MCP server, and REST API fit together |

**Explain:** The demo is modular — you can jump to any chapter. Each chapter builds on previous ones but is self-contained. Estimated time: ~2-3 minutes per chapter.

### Run Pre-Flight Check

Execute the pre-flight check described above. Show connection summary.

### Navigate

Show chapter tracker (► on 0·Intro) and offer:
1. **Continue → Chapter 1: Overview** — Learn what ic is and how it works
2. **Deep dive: Why CI/CD monitoring is hard** — Konflux architecture, Tekton pipelines, and the data aggregation challenge
3. **View chapter index** — Jump to any section
4. **Exit demo** — End now

**Under the Hood — "Why CI/CD Monitoring is Hard":**

Explain (when user selects deep dive):
- **The data fragmentation problem**: Build logs live in Tekton Results (or KubeArchive), policy results live in Conforma, git context lives in GitHub, pipeline definitions live in `.tekton/` directories. Understanding one failure requires querying 4-5 systems.
- **The ephemerality problem**: Tekton stores build logs in Kubernetes pods. Pods get garbage collected. Logs disappear within days. If you don't capture them fast, you lose the evidence.
- **The scale problem**: A product like RHOAI has 69+ container images, each built for multiple architectures (amd64, arm64, ppc64le, s390x). That's 200+ builds per push. Manual triage doesn't scale.
- **ic's approach**: Proactively sync data into PostgreSQL before it expires. Provide a single query interface across all data sources. Add AI to automate the analysis that humans would do manually.

---

## Section 1: Overview — What is ic?

**Explain:**
- ic is a **kubectl-style CLI** + **MCP server** + **REST API** for monitoring CI/CD pipelines
- Resource-oriented design: `get`, `describe`, `report` — if you know kubectl, you know ic
- It tracks two types of issues: **build failures** (code won't compile/build) and **Conforma violations** (image doesn't meet security policy)
- Three interfaces share the same data layer — CLI for terminals, MCP for AI agents (this demo), REST API for dashboards

**Show:** Call `mcp__ic__list_applications`. Present as a markdown table:

| Application | Components | Build Failures | Conforma Violations |
|-------------|-----------|----------------|---------------------|
| my-app-v2-1 | 69 | X | Y |
| ... | ... | ... | ... |

**Explain:** Each "application" groups all container images for one product release version. `my-app-v2-1` means "version 2.1, Early Access 1".

**Navigate:** Show chapter tracker (► on 1·Overview) and offer:
1. **Continue → Chapter 2: Alert Dashboard**
2. **Deep dive: What is Konflux?** — The CI/CD platform ic monitors
3. **View index**
4. **Exit**

**Under the Hood — "What is Konflux?":**

Explain when user selects deep dive:
- **Konflux** is Red Hat's cloud-native CI/CD platform, built on top of Tekton (Kubernetes-native pipelines). It's the build system — ic is the monitoring layer on top.
- **Key concepts**: An **Application** in Konflux groups related container images. Each image is a **Component**. When code is pushed, Konflux creates a **PipelineRun** — a Tekton pipeline execution that builds, tests, and validates the image.
- **The pipeline stages**: Source checkout → Build (Dockerfile/Buildah) → Test → Security scan → Conforma policy check → Push to registry
- **Multi-arch builds**: Each component is built for multiple CPU architectures (amd64, arm64, ppc64le, sometimes s390x). A single code push triggers 3-4 parallel builds.
- **Why ic exists**: Konflux provides a web UI, but it's per-component. When you have 69 components and need to know "what's broken across the entire release?", you need ic.

---

## Section 2: Alert Dashboard

**Explain:** The alert dashboard is the starting point for daily triage — one unified view of everything that's currently broken. Instead of checking the Konflux UI component by component, you get the full picture in one query.

**Show:** Call `mcp__ic__list_alerts`. Present as TWO separate tables:

**Build Failures:**

| # | Component | Status | Since | Has Logs | AI Analyzed |
|---|-----------|--------|-------|----------|-------------|
| 1 | comp-a | Failed | May 19 | Yes | No |

**Conforma Violations:**

| # | Component | Scenario | Since | Has Details | AI Analyzed |
|---|-----------|----------|-------|-------------|-------------|
| 1 | comp-b | registry-prod | May 21 | Yes | Yes |

**Explain the key columns:**
- **Has Logs / Has Details** — whether ic has captured enough diagnostic data for AI analysis. Without logs, the AI can't help.
- **AI Analyzed** — whether the AI engine has already examined this failure. If "No", it's waiting in the queue.
- **Since** — how long it's been broken. Older failures = higher priority (they may have fallen through the cracks).

Highlight any interesting observations: clusters of similar failures, long-standing issues, components that appear in both tables.

**Navigate:** Show chapter tracker (► on 2·Alerts) and offer:
1. **Continue → Chapter 3: Failure Inspection** — Drill into a specific failure
2. **Deep dive: The daily triage workflow** — How release engineers use alerts to prioritize work
3. **View index**
4. **Exit**

**Under the Hood — "The Daily Triage Workflow":**

Explain when user selects deep dive:
- **Why unified alerts matter**: Without ic, a release engineer has to check the Konflux UI (for builds), the Conforma dashboard (for policy), GitHub (for commit context), and Tekton Results (for logs) — four different systems just to understand what's broken.
- **Triage priority**: The mental model is `age × severity × data availability`. A 3-day-old build failure with logs available is higher priority than a 1-day-old warning without logs.
- **Team routing**: Build failures typically route to component owners (the team that owns the source code). Conforma violations typically route to release engineering (the team that manages policy compliance). ic's categorization helps route work to the right team automatically.
- **The "zero alerts" goal**: A healthy release has zero unresolved alerts. The dashboard shows progress toward that goal — and makes it visible to everyone.

---

## Section 3: Drilling Into a Failure

**Explain:** Let's pick a specific failure and see everything ic knows about it. This is the "investigate" phase of triage.

**Show:** Pick the first build failure from Section 2. Call `mcp__ic__get_failure` with that component name (use `include_logs=false`, `include_commit_context=false` for a compact view). Present the key fields:

| Field | Value |
|-------|-------|
| **PipelineRun** | `pipeline-run-name` |
| **Failed Step** | `step-name` |
| **Error Type** | Build Error / Task Failure |
| **Repository** | [repo-name](url) |
| **Branch** | `branch-name` |
| **Commit** | `sha[:8]` by author |
| **Commit Message** | the commit message |
| **First Seen** | date |

**Error Message:**
```
the error message here
```

**Explain the three key questions this answers:**
- **WHERE** did it break? → The failed step (e.g., `build-images`, `fips-check`, `sbom-generate`)
- **WHY** did it break? → The error message
- **WHAT changed?** → The commit info (author, message, diff) — often the root cause

**Interactive decision point:**
Before showing AI analysis, ask the user:

"Looking at this error, what would you check first?"
- A) The full build logs for more context
- B) The commit diff to see exactly what changed
- C) Whether other components have the same error (pattern)

After the user answers, explain how ic supports each approach:
- A → `mcp__ic__get_failure` with `include_logs=true` pulls the full build log
- B → `mcp__ic__get_failure` with `include_commit_context=true` includes the diff
- C → `mcp__ic__search_failures` with category filter finds similar failures

Then call `mcp__ic__get_analysis` for the same component. If analysis exists, show:

| Field | Value |
|-------|-------|
| **Category** | `dependency_issue` / `infrastructure` / etc. |
| **Confidence** | High / Medium / Low (with percentage) |
| **Can Auto-Fix** | Yes / No |

**Root Cause:** (the analysis text)

**Recommended Fix:** (the fix text)

If no analysis exists, say: "No AI analysis yet. In a real workflow, you'd run `./ic ai analyze <component>` to generate one, or use `/triage` for a guided session."

**Navigate:** Show chapter tracker (► on 3·Failures) and offer:
1. **Continue → Chapter 4: AI Analysis** — How the AI engine works
2. **Deep dive: Tekton pipelines and build logs** — The infrastructure behind the failures
3. **View index**
4. **Exit**

**Under the Hood — "Tekton Pipelines and Build Logs":**

Explain when user selects deep dive:
- **What is Tekton?** Tekton is a Kubernetes-native CI/CD framework. Pipelines are defined as Kubernetes custom resources (CRDs). Each pipeline is a sequence of Tasks, and each Task contains Steps (individual container executions).
- **The Pipeline → Task → Step hierarchy**: A build pipeline might have tasks like `git-clone`, `buildah` (container build), `fips-check`, `conforma-check`, `push-image`. Each task runs in its own pod. Each step runs in a container within that pod.
- **Why logs are ephemeral**: Build logs are stored in the pod's container stdout/stderr. When the pod is garbage collected (typically 24-72 hours after completion), the logs are gone forever. Tekton Results provides a longer-term store, but even that has retention limits.
- **How ic captures logs**: ic proactively syncs logs from two sources: **Tekton Results** (the primary API for completed pipeline results) and **KubeArchive** (a Kubernetes archival system). It stores them in PostgreSQL, where they persist indefinitely. This is why the "Has Logs" column matters — without ic's capture, you'd be racing against garbage collection.

---

## Section 4: AI Analysis Engine

**Explain:** ic's AI analysis engine is the brain of the auto-healing system. When a build failure or policy violation occurs, the AI reads the complete context — build logs, commit diffs, Dockerfiles, Tekton configs — and provides a root cause diagnosis with a suggested fix. Every analysis costs one LLM invocation (~$0.06 per component).

**Show:** Call `mcp__ic__get_stats`. Present:

### AI Analysis Status

| Metric | Build Failures | Conforma Violations |
|--------|---------------|---------------------|
| Pending | X | Y |
| Analyzed | X | Y |
| Low confidence | X | Y |
| Auto-fixable | X | Y |

**Total LLM cost (30 days):** $X.XX (N analyses, ~$0.06 each)

Also show recent analyses if available in the stats response. Present as a table: Component, Type, Category, Confidence, Auto-Fix.

**Explain:**
- **Pending** = failures with data collected but not yet analyzed
- **Analyzed** = failures the AI has examined and categorized
- **Auto-fixable** = the AI is confident enough to generate a PR fix automatically
- **Cost transparency** = exact dollar amount per analysis. Compare ~$0.06 per AI analysis vs. 15-30 minutes of engineer time for manual triage.
- Confidence scores help prioritize: high confidence → act immediately, low confidence → investigate deeper

**Navigate:** Show chapter tracker (► on 4·AI) and offer:
1. **Continue → Chapter 5: Conforma** — Supply-chain security policies
2. **Deep dive: How AI analysis works** — The analysis pipeline, classification categories, and pattern library
3. **View index**
4. **Exit**

**Under the Hood — "How AI Analysis Works":**

Explain when user selects deep dive:
- **Input assembly**: When a build fails, ic collects comprehensive context: build logs (truncated to the most relevant sections — usually the last 500 lines where errors appear), commit diff (what changed), Dockerfile (the build definition), and Tekton pipeline configs (the CI/CD orchestration).
- **Classification**: The LLM analyzes this context and classifies the failure into categories: `dependency_issue` (missing/incompatible dependencies), `infrastructure` (platform or resource problems), `build_error` (code compilation failures), `config_error` (misconfigured pipelines or Dockerfiles), `policy_fips_check` (FIPS compliance), and more.
- **Output**: For each failure, the AI produces: root cause explanation (human-readable diagnosis), recommended fix (specific steps or code changes), confidence score (how certain the AI is), and auto-fixability assessment (can this be fixed via automated PR?).
- **Pattern library**: Every successful analysis contributes to a growing pattern library. When a new failure appears, ic first checks if it matches a known pattern. If it does, classification is instant and free. Only truly novel failures need a full LLM analysis. This creates a flywheel: more analyses → better patterns → faster classification → lower cost.
- **Quality control**: When an auto-fix is attempted, ic tracks whether it resolves the failure. This feedback loop continuously improves confidence scoring.

---

## Section 5: Conforma / Enterprise Contract

**Explain:** Now we shift from build failures to security compliance. Conforma — also called Enterprise Contract — is Red Hat's supply-chain security enforcement system. Every container image must pass a battery of policy checks before it can ship. These aren't optional. A single violation blocks the entire release pipeline.

**Show:** Call `mcp__ic__get_conforma_report`. Present a summary table: Component, Violations, Warnings, Successes, Pass Rate.

Then pick one component with violations (preferably one with AI analysis) and call `mcp__ic__get_violation`. Show key fields: Scenario, Violations count, Warnings count, Successes count, Repository, Snapshot.

Show 3-5 actual violation rule names from the response (e.g., `hermetic_task.hermetic`, `sbom_spdx.allowed_package_sources`).

**Explain:**
- **Violations** = hard blockers. The image cannot ship until these are resolved.
- **Warnings** = informational. They don't block release but indicate issues.
- **Critical detail**: Violations appear multiplied by architecture. "4 violations" for `hermetic_task.hermetic` is often 1 rule × 4 architectures (amd64, arm64, ppc64le, s390x).
- ic also tracks **volatile config warnings** — these flag when temporary policy exceptions are about to expire. This prevents last-minute surprises.

**Interactive decision point:**
Ask: "This component has a `hermetic_task.hermetic` violation. What does 'hermetic' mean for a build?"
- A) The build can't access the network — prevents supply-chain injection
- B) The build is reproducible — same input always gives same output
- C) Both — hermetic builds are both isolated and reproducible
- Skip this question

After user answers, explain: The answer is C. A hermetic build is network-isolated (no external access during build, preventing supply-chain injection like the SolarWinds attack) AND reproducible (same source + deps = identical output). Conforma's `hermetic_task.hermetic` rule enforces this by requiring `HERMETIC=true` in the Tekton task definition.

**Navigate:** Show chapter tracker (► on 5·Conforma) and offer:
1. **Continue → Chapter 6: Export** — Turn data into Jira tickets and Slack messages
2. **Deep dive: Supply-chain security** — SLSA framework, SBOMs, FIPS, and why Conforma exists
3. **View index**
4. **Exit**

**Under the Hood — "Supply-Chain Security and Enterprise Contract":**

Explain when user selects deep dive:
- **Real-world attacks**: SolarWinds (2020) — attackers compromised the build pipeline, injecting malware into trusted software updates, affecting 18,000 customers. Log4Shell (2021) — companies didn't know they were using the vulnerable library because they had no SBOM (Software Bill of Materials). These attacks exploit trust in the software supply chain.
- **SLSA framework**: Supply-chain Levels for Software Artifacts defines 4 levels of security. Level 1: basic provenance (know who built it). Level 2: signed provenance (cryptographic verification). Level 3: hardened builds (isolated, reproducible, auditable). Level 4: two-party review. Conforma enforces Level 3 requirements.
- **Key concepts**:
  - **Hermetic builds**: No network access during build. All dependencies pre-fetched. Prevents runtime injection.
  - **SBOMs**: Machine-readable inventory of all software in an image, like ingredients labels on food. Enables "does this image contain Log4j?" queries.
  - **FIPS compliance**: US government cryptography requirements (FIPS 140-2/140-3). Non-FIPS crypto is banned from government deployments.
  - **Base image registries**: Only images from approved registries (registry.redhat.io) are allowed. Prevents use of unvetted Docker Hub images.
- **Violations vs. warnings**: Violations are hard blockers (release cannot proceed). Warnings are informational (often rules in "monitoring mode" before enforcement).

---

## Section 6: Export — From Data to Action

**Explain:** You've identified a failure and understand the root cause. Now you need to act — file a Jira ticket, notify your team in Slack, escalate to the component owner. ic generates ready-to-paste content so you don't have to manually assemble error details, logs, and context.

**Show:** Pick a component (preferably one with AI analysis). Call `mcp__ic__export_jira` and `mcp__ic__export_slack` in parallel. Show the first 20-25 lines of each in code blocks.

**Explain:**
- **Jira format**: Uses Jira markup syntax (`h2`, `{code}`, tables) — paste directly into a ticket description
- **Slack format**: Uses Slack's mrkdwn syntax (bold, code blocks, links) — paste into a channel message
- **Also available**: `export_markdown` (GitHub Issues, GitLab) and `export_json` (automation pipelines, webhooks)
- **The value**: Without ic, creating a detailed ticket takes 10-15 minutes (copy logs, paste errors, write context, format links). With ic, it's one command and a copy-paste — 30 seconds. And every ticket has the same structure and level of detail.

**Navigate:** Show chapter tracker (► on 6·Export) and offer:
1. **Continue → Chapter 7: Dashboard** — Trends, costs, and the complete picture
2. **Deep dive: From alert to resolution** — The full lifecycle of a failure
3. **View index**
4. **Exit**

**Under the Hood — "From Alert to Resolution: The Full Workflow":**

Explain when user selects deep dive:
- **The full lifecycle**: (1) Build fails → ic ingests data automatically. (2) Alert appears in dashboard → engineer triages. (3) AI analyzes root cause → suggests fix. (4) Export generates ticket → assigned to owner. (5) Owner commits fix → next build succeeds. (6) ic detects resolution → alert clears. (7) Successful fix becomes a pattern → future similar failures classified instantly.
- **The closed-loop vision**: Steps 1, 2, 3, 5, 6, 7 are already automated. Step 4 (the fix itself) still needs human judgment in most cases. The long-term goal: for routine, well-understood failures, the entire cycle runs without human intervention. Engineers focus on novel problems.
- **Integration points**: Jira (structured tickets), Slack (instant notifications), GitHub Issues (for open-source components), JSON export (for custom automation — PagerDuty, Datadog, release dashboards).

---

## Section 7: Dashboard & Trends

**Explain:** The dashboard is the "morning standup" view for release health — it combines metrics from across the system into one snapshot that tells you whether today will be a good day or a firefight.

**Show:** Call `mcp__ic__get_dashboard`. Present key metrics in a table:

| Metric | Value |
|--------|-------|
| Total failures tracked | X |
| Components monitored | Y |
| Failures with logs | Z (percentage) |
| AI analyses (30d) | N |
| LLM cost (30d) | $X.XX |
| Fix attempts | M |
| Fix success rate | P% |

Then call `mcp__ic__get_daily_stats` with `days=7`. Present as a simple ASCII bar chart AND a data table showing date and failure count.

**Explain:**
- If "failures with logs" < total: some builds are missing diagnostic data (Tekton Results sync gap)
- Cost transparency: exact dollar amount per analysis, no mystery billing
- Highlight any spikes in the daily stats — sudden jumps often indicate: base image updates (affects many components), infrastructure changes (cluster/registry issues), or batch merge days

**Interactive decision point (if there's a spike in the data):**
Ask: "You see a spike of X failures on date Y. As a release engineer, what would you investigate first?"
- A) What all the failures have in common (pattern analysis)
- B) Whether infrastructure changed that day (platform issue)
- C) Whether a base image was updated (upstream dependency)
- D) All of the above, starting with the cheapest check

After user answers, explain: D is usually correct. Start with pattern analysis (free — ic already classified most failures), then check upstream dependencies (quick manual check), then platform archaeology (most time-intensive). The pattern library is what makes approach A so powerful — every past AI analysis becomes reusable knowledge.

**Navigate:** Show chapter tracker (► on 7·Dashboard) and offer:
1. **Continue → Chapter 8: Release Readiness & Security** — Can we ship? CVE scan, freeze calendar, pipeline status
2. **Deep dive: Pattern libraries and organizational learning** — How the system gets smarter over time
3. **View index**
4. **Exit**

**Under the Hood — "Pattern Libraries and Organizational Learning":**

Explain when user selects deep dive:
- **How patterns are created**: AI analyzes a failure → produces category, root cause, suggested fix → that analysis becomes a reusable pattern with a structured signature (error message patterns, exit codes, file paths).
- **The flywheel effect**: Month 1: 100 failures, 0 patterns → 100 AI analyses needed ($6). Month 6: 100 failures, 67 patterns → 85 match existing patterns (free) → only 15 need AI ($0.90). The cost drops dramatically as the pattern library grows.
- **Error categories and team routing**: `dependency_issue` → component owner. `infrastructure` → platform team. `policy_violation` → release engineering. Each pattern includes routing metadata so tickets go to the right team automatically.
- **Fix success rates as calibration**: ic tracks whether auto-fix PRs actually resolve failures. High success rate (>80%) → increase confidence. Low success rate (<50%) → pattern needs refinement. This creates a feedback loop that improves the system over time.

---

## Section 8: Release Readiness & Security Posture

**Explain:** Everything so far has been reactive — looking at what's broken. Release readiness flips the question: **"Can we ship?"** It combines pipeline status, build health, conforma compliance, linked JIRA tickets, and security scanning into a single verdict. This is the view a release manager opens before the weekly readiness meeting.

**Show:** Call `mcp__ic__get_release_readiness`. Present the verdict and blockers/risks as a table:

| Section | Status | Details |
|---------|--------|---------|
| Verdict | READY / AT_RISK / NOT_READY | — |
| Build Failures | N | X failing component(s) |
| Conforma Violations | N | Y unexcepted violation(s) |
| Freeze | Active/None | reason, end date |

**Then show the security posture.** Call `mcp__ic__get_release_vulnerabilities` to show vulnerability scan results from SARIF:

| Component | Critical | High | Medium | Low | Top Findings |
|-----------|----------|------|--------|-----|-------------|

**Explain:**
- ic fetches SARIF scan results directly from the OCI registry via the **referrers API** — no external scanner integration needed
- SARIF is a standard format used by security scanners (Clair, Grype, ShellCheck, Snyk) to report findings
- Results include both **CVEs** (known vulnerabilities in dependencies) and **SAST** findings (code quality issues from static analysis)
- SARIF severity levels map: `error` → Critical, `warning` → High, `note` → Medium
- This is also available as a CLI command: `ic get vulnerabilities [--component X] [--severity critical|high]`

Then call `mcp__ic__get_release_status` for pipeline progress. Present the sections:
- **Pipeline** — Stage N/N and Prod N/N with blockers
- **Build Health** — Failing components with health scores, recently fixed
- **Conforma Compliance** — Blocking violations with exception status
- **Linked JIRAs** — Open/resolved tickets for active failures
- **Verdict** — READY / AT RISK / NOT READY with specific blockers

**Explain each section:**
- **Pipeline**: Shows how far the current release epoch has progressed through stage and prod release plans. Retries count indicates flaky pipelines.
- **Build Health**: Cross-references build failures with health scores. A health score of 25 means the component fails 75% of the time. Recently fixed builds show what's been resolved in the last 7 days.
- **Conforma**: Only shows "current policy" violations (not future policy). Each violation is checked against exception rules — excepted violations don't block release.
- **JIRA**: If failures have linked JIRA tickets, shows their status (Open/In Progress/Closed) and priority. Blocker-priority JIRAs are flagged.
- **Verdict**: Computed from all sections. "NOT READY" if pipeline blocked OR unexcepted conforma violations. "AT RISK" if builds failing or pipeline still in progress. "READY" if everything clear.

**Then show freeze calendar:**
```bash
./ic freeze list 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g'
```

**Explain:** The freeze calendar stores scheduled pipeline freezes — holidays, infrastructure maintenance windows, or team-defined blackout periods. During a freeze, the readiness view shows a prominent banner. Freezes are stored in PostgreSQL, not YAML files, so they're accessible from CLI, MCP, and API.

**Show commands:**
```
ic freeze add 2026-07-04 2026-07-04 "Independence Day"
ic freeze list
ic freeze rm <id>
```

**Then show release history:**
```bash
./ic release history --last 3 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g'
```

**Explain:**
- **Epoch**: Each release attempt gets a unique 10-digit Unix timestamp
- **Snapshot diff**: Shows how many container images changed between epochs (e.g., "79 updated"). This tells you the scope of change.
- **Retry count**: More than 2 retries signals a flaky pipeline or persistent blocker
- **DB-sourced context**: Builds fixed, conforma violations resolved/appeared between epochs

**Interactive decision point:**
Ask: "You see 'NOT READY' with 2 blockers: conforma violations and a frozen pipeline. What's your move?"
- A) Wait for the freeze to end, then address conforma
- B) Request conforma exceptions now so we're ready when the freeze lifts
- C) Check if the conforma violations are already being worked on in JIRA

After user answers, explain: B is usually optimal. Freeze windows are predictable — use the downtime to prepare exceptions or fixes. By the time the freeze lifts, you want zero blockers. Option C is also good — the readiness view shows linked JIRAs precisely for this reason.

**Navigate:** Show chapter tracker (► on 8·Release) and offer:
1. **Continue → Chapter 9: Architecture** — How CLI, MCP, and REST API fit together
2. **Deep dive: Operational awareness** — Outage detection, freeze scheduling, and release timeline
3. **View index**
4. **Exit**

**Under the Hood — "Operational Awareness":**

Explain when user selects deep dive:
- **Outage detection**: ic queries the public status.redhat.com API (Statuspage.io v2) for unresolved incidents affecting CI-relevant components — RHTAP (Konflux), Quay.io (container registry), and Red Hat Container Registry. Results are cached hourly. If an outage is active, it appears as a banner in alerts, health warnings, and release readiness. This prevents engineers from debugging build failures that are actually caused by platform outages.
- **Freeze calendar**: Pipeline freezes are stored in the `release_freezes` table. The `show_freeze_banner()` function checks for active freezes and upcoming ones (next 7 days). Freeze dates are validated before insertion. The freeze appears in `ic get alerts`, `ic health warnings`, and `ic release readiness`.
- **JIRA integration**: The `jira_key` column in both `build_failures` and `conforma_results` tables links failures to JIRA tickets. During readiness evaluation, ic fetches live status from the JIRA API for each linked ticket. This surfaces whether blockers are being actively worked on or stuck.
- **Cross-epoch snapshot diffs**: When releases happen daily, knowing "what changed since yesterday's attempt" is critical. ic compares the Konflux Snapshot CRs between consecutive epochs and reports updated/added/removed component images. This helps identify whether a failed epoch might succeed now because the failing component was rebuilt.
- **Non-blocking design**: All operational awareness features degrade gracefully. If JIRA is unreachable, the section is silently skipped. If status.redhat.com is down, no banner. If the DB has no freeze data, no banner. Nothing blocks the core readiness evaluation.

---

## Section 9: Architecture & Integration

**Explain:** Everything in this demo — every MCP tool call, every data lookup — was powered by the same unified architecture. ic exposes its capabilities through three interfaces that share one data layer.

**Show the architecture diagram:**
```
                    ┌─────────────┐
                    │  PostgreSQL  │
                    │  (Failures,  │
                    │   Analyses,  │
                    │   Patterns)  │
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │ Repositories │
                    │   (Python)   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴─────┐ ┌───┴───┐ ┌─────┴─────┐
        │  CLI (ic)  │ │  MCP  │ │ REST API  │
        │  kubectl-  │ │Server │ │  FastAPI  │
        │   style    │ │       │ │  /docs    │
        └────────────┘ └───────┘ └───────────┘
              │            │            │
         Terminal      Claude &     Dashboards,
         workflows     AI agents    webhooks,
                       (this demo)  automation
```

**Explain the data flow:**
1. **Ingestion**: Konflux runs CI builds → PipelineRuns produce results → ic syncs from Tekton Results and KubeArchive into PostgreSQL
2. **AI layer**: Analysis runs on-demand → results stored alongside failure data
3. **Multi-interface access**: CLI, MCP, and REST API all read from the same repository layer. No data duplication, no sync lag.

**Explain the key insight:** You don't choose one interface upfront. Debugging in terminal? → CLI. Working with an AI assistant? → MCP. Building automation? → REST API. All three work on the same live data.

**Navigate:** Show chapter tracker (► on 9·Arch) and offer:
1. **Continue → Wrap-up** — Summary and next steps
2. **Deep dive: Model Context Protocol (MCP)** — How AI agents talk to tools
3. **View index**
4. **Exit**

**Under the Hood — "MCP: How AI Agents Talk to Tools":**

Explain when user selects deep dive:
- **What is MCP?** Model Context Protocol is Anthropic's open standard for connecting AI models to external tools. Think of it as "LSP for AI" — just like Language Server Protocol standardized how editors talk to language tooling, MCP standardizes how AI agents talk to the world.
- **Why MCP beats CLI parsing**: Traditional approach — AI runs `./ic list alerts`, parses ANSI-colored terminal output, gets confused by line wrapping. MCP approach — AI calls `mcp__ic__list_alerts`, receives structured JSON with typed fields. No parsing errors, no ANSI stripping, rich nested data.
- **How ic's MCP server works**: The server exposes 32 tools (all the `mcp__ic__*` functions used in this demo). Each tool accepts structured parameters and returns structured JSON. The server runs on stdio for Claude Desktop integration or SSE for network access.
- **The vision**: MCP is not Claude-specific. Any AI agent that supports MCP can use ic's tools. A Slack bot could call `get_triage`. A GitHub Action could call `export_json`. The same tool definitions work everywhere.

---

## Wrap-up

Present a "What You Learned" summary:

| Chapter | Concept | ic Feature | MCP Tool |
|---------|---------|------------|----------|
| 0 | The CI monitoring problem | — | — |
| 1 | Resource-oriented CI tools | Application listing | `list_applications` |
| 2 | Unified alert triage | Alert dashboard | `list_alerts` |
| 3 | Root cause investigation | Failure inspection | `get_failure`, `get_analysis` |
| 4 | AI-assisted classification | Analysis engine | `get_stats` |
| 5 | Supply-chain security | Conforma/EC policies | `get_conforma_report`, `get_violation` |
| 6 | Actionable exports | Jira/Slack/MD/JSON | `export_jira`, `export_slack` |
| 7 | Trend analysis | Dashboard & patterns | `get_dashboard`, `get_daily_stats` |
| 8 | Release readiness & security | Pipeline, CVE/SAST scan, verdict | `get_release_readiness`, `get_release_vulnerabilities`, `get_release_status` |
| 9 | Multi-interface architecture | CLI + MCP + REST | — |

**Key takeaways** (present as a numbered list):
1. **Resource-oriented design**: ic treats CI artifacts like Kubernetes resources — list, inspect, analyze, export with consistent verbs.
2. **AI as force multiplier**: Compresses investigation from 30 minutes to 30 seconds — triage 10x more failures.
3. **Pattern library flywheel**: Every analysis makes the next one faster and cheaper.
4. **Multi-interface flexibility**: Terminal, AI agent, or dashboard — same data, your choice.
5. **Cost transparency**: Know exactly what AI analysis costs, and watch it trend toward zero as patterns accumulate.

**Next steps — three paths forward:**
1. **`/release`** — Full daily release workflow: readiness, triage, fix, report
2. **`/triage`** — Quick triage session (build or conforma focus)
3. **`/status`** — Executive-level CI/CD status briefing

**Close with:** "Thanks for exploring ic. Everything you saw was real data from your CI pipelines — no mocks, no staging. The system is live."
