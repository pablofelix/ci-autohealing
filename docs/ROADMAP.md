# Roadmap: CI-Autohealing — From Monitoring to Auto-Resolution

**Created:** 2026-04-22
**Last Updated:** 2026-04-28

## Context

The ci-autohealing project has evolved into a solid failure detection and diagnosis system: cron collects data every 20min from KubeArchive, the `ic` CLI provides rich querying (alerts, describe, historical), and the database stores build failures + Conforma violations with full metadata. The next step is to build an AI agent that analyzes and resolves failures automatically.

## Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 0 | **Complete** | Cleanup: dead code removal, docs consolidation, tests, ADRs |
| Phase 1 | **Complete** | AI analysis of failures using Claude (1.1-1.7 complete) |
| Phase 1.8 | **Complete** | Conforma daily reporting & exception tracking |
| Phase 2 | Planned | Auto-resolution of build failures via PRs |
| Phase 3 | Planned | Auto-resolution of Conforma compliance violations |

## Sequencing

```
Phase 0 (Cleanup) ──> Phase 1 (AI Analysis) ──┬──> Phase 2 (Build Fixes)
                                               └──> Phase 3 (Conforma Fixes)
```

Phase 2 before Phase 3 because build failures block releases and dependency fixes are more mechanical.

---

## Phase 0: Cleanup (Complete)

**Goal:** Remove dead code, consolidate docs, fix stale infrastructure, add tests.

What was done:
- Deleted 9 stale shell collectors and 4 stale Python files
- Fixed `cron/install-cron.sh` to install `collect-comprehensive.sh`
- Fixed PATH in `cron/collect-comprehensive.sh` for cron environment
- Moved 12 scattered markdown docs into `docs/archive/`, `docs/design/`
- Created 4 ADRs in `docs/adr/`
- Added pytest tests for comprehensive collector, conforma collector, and database (45 tests)
- Added `pytest.ini` configuration

---

## Phase 1: AI Analysis

**Goal:** Use Claude API to analyze failures, populate `ai_analysis` table, show analysis in `ic describe`. Analysis only — no PRs, no auto-fixes.

**Status:** **COMPLETE** - All subphases (1.1-1.7) implemented. MCP server (1.7) enables external agents to access Konflux data.

**Provider:** Claude on Vertex AI (provider-independent architecture allows swapping to direct Anthropic API or other LLMs).

### 1.1 Build failure analyzer (COMPLETE)

**Files:** 
- `collectors/python/analyzers/build_failure_analyzer.py` - Orchestrator
- `collectors/python/clients/llm_provider.py` - Provider ABC
- `collectors/python/clients/vertex_ai_provider.py` - Vertex AI adapter
- `collectors/python/clients/langfuse_tracker.py` - Langfuse tracking
- `collectors/python/repositories/ai_analysis_repository.py` - Database operations
- `collectors/python/analyze_failures.py` - Entry point

**Implementation:**
- Provider-independent architecture (LLMProvider ABC) - swap providers by changing config
- Current provider: Claude on Vertex AI via `AnthropicVertex` (GCP Application Default Credentials)
- Query `build_failures` where `ai_analyzed = FALSE AND is_resolved = FALSE AND build_logs IS NOT NULL`
- Construct prompt with: component name, error message/type, failed step, logs (truncated to ~50K chars), commit info, repo URL
- Call Claude (Sonnet) with structured output via tool_use: `root_cause`, `failure_category`, `confidence_score`, `recommended_fix`, `recommended_files`, `can_auto_fix`
- Insert into `ai_analysis` table, update `build_failures.ai_analyzed`
- Track in Langfuse (optional - graceful degradation if not configured)
- Batch limit: configurable via `AI_MAX_PER_RUN` (default: 5)

Failure categories: `dependency_issue`, `build_error`, `test_failure`, `resource_limit`, `config_error`, `git_sync_issue`, `infrastructure`

### 1.2 Conforma analyzer (COMPLETE)

**Files:**
- `collectors/python/analyzers/conforma_analyzer.py` - Conforma analysis orchestrator
- `collectors/python/analyze_conforma.py` - Entry point
- `db/migrations/003_add_conforma_analysis.sql` - Schema migration for unified ai_analysis table

**Implementation:**
- Parse structured `violation_details` JSON (SBOM data, policy rules, violation terms)
- 14 known Conforma patterns (hermetic build, unpinned tasks, package sources, etc.)
- Classify violations into policy categories: `policy_hermetic_build`, `policy_unpinned_task`, `policy_untrusted_image`, `policy_signing_key`, `policy_package_source`, `policy_rpm_repository`, `policy_version_label`, `policy_fips_check`, `policy_deprecated_task`
- Assess auto-fixability per violation type (vendor dependency, policy exception, approved alternative)
- Unified `ai_analysis` table supports both build failures and Conforma violations (polymorphic foreign key)
- CLI integration: `ic ai analyze conforma <component>`, `ic export conforma <name> jira`

### 1.3 Integrate into cron (COMPLETE)

**Modified:** `cron/collect-comprehensive.sh` — added step 6:
```bash
# Step 6: AI analysis (optional - only if LLM_PROVIDER is set)
if [ -n "$LLM_PROVIDER" ]; then
    python3 analyze_failures.py
fi
```

Optional execution - only runs if `LLM_PROVIDER` env var is set. Non-critical failure (continues if AI analysis fails).

### 1.4 CLI integration (COMPLETE)

**Modified:** `ic` — 3-part command structure:

**Part 1: Display Analysis (Read-Only)**
- `ic describe component <name>` and `ic 1` now show AI analysis inline if available
- Shows category, confidence, root cause, recommended fix, auto-fix status
- Warns if analysis is >7 days old
- Implemented via `display_ai_analysis()` function

**Part 2: Trigger Analysis (`ic ai` namespace)**
- `ic ai analyze <num|component>` — Analyze specific failure
- `ic ai analyze --all` — Analyze all pending failures
- `ic ai analyze --force` — Re-analyze even if already analyzed
- `ic ai status` — Summary: pending, analyzed, auto-fixable, success rate, costs
- `ic ai stats` — Detailed statistics by category and date

**Part 3: Export to Tickets (`ic export` namespace)**
- `ic export <num|component> jira` — Complete Jira ticket template
- `ic export <num|component> jira --clipboard` — Copy to clipboard
- Calculated fields: Priority (P1/P2/P3), Risk Level (HIGH/MEDIUM/LOW), Estimated Fix Time
- Templates for markdown, json, slack (marked for future implementation)

**Files modified:**
- `ic` — Added ~600 lines for AI commands and export templates
- `analyze_failures.py` — Added CLI arguments: `--component`, `--limit`, `--force`
- `analyzers/build_failure_analyzer.py` — Updated `run()` to accept component_filter and force
- `repositories/ai_analysis_repository.py` — Updated `get_pending_failures()` to support filtering

**Future:** Phase 2+ will add `ic jira` namespace for automated ticket creation via Jira API.

### 1.5 Langfuse integration (COMPLETE)

**File:** `collectors/python/clients/langfuse_tracker.py`
- Creates traces per analysis run, generations per Claude call
- Records tokens, cost, duration
- Links trace IDs to `ai_analysis.langfuse_trace_id`
- Graceful degradation: if Langfuse not configured, tracking is disabled but analysis works

### 1.6 Export templates (COMPLETE)

**Implemented:** Jira ticket export templates with computed fields

**Build failures:**
- Priority calculation: P1 (occurrence > 10 OR days_failing > 7), P2 (can_auto_fix AND confidence > 0.9), P3 (default)
- Risk level: HIGH (requires_review), MEDIUM (can't auto_fix OR confidence < 0.7), LOW (otherwise)
- Estimated fix time by category: dependency_issue (5-15 min), test_failure (30-60 min), etc.
- Complete template with failure context, AI analysis, debug links, action plan

**Conforma violations:**
- Policy-focused template (not code-focused)
- Includes violation terms, affected images/architectures
- Policy exception process with JIRA template and MR instructions
- Fix options: vendor dependency (preferred), approved alternative, request exception

**Future:** Markdown (GitHub Issues), JSON (automation), Slack message formats

### 1.7 MCP Server (COMPLETE)

**Goal:** Expose Konflux collectors as MCP tools for external AI agents (Claude Desktop, GitHub Copilot, Cursor, etc.)

**Files:**
- `konflux-mcp-server/src/konflux_mcp/server.py` - FastMCP server
- `konflux-mcp-server/src/konflux_mcp/tools.py` - 7 MCP tools
- `konflux-mcp-server/src/konflux_mcp/models.py` - Pydantic response models
- `konflux-mcp-server/src/konflux_mcp/repository_factory.py` - DB connection (no config dependency)

**Architecture:** Application-agnostic, stateless tools
- Each tool accepts `application` parameter (no config coupling)
- Supports hybrid pattern:
  - **Parallel comparison** (read-only): Compare stats, failures across versions
  - **Sequential fixing** (write ops): Focus on ONE version when creating PRs
  - **Smart reuse**: Check if fix applies to other versions without context mixing

**7 MCP Tools:**
1. `list_applications()` - Discover available RHOAI versions
2. `list_alerts(application)` - Get all alerts (build + Conforma)
3. `get_failure(component, application)` - Full build failure details
4. `get_violation(component, application)` - Full Conforma violation details
5. `get_analysis(component, application, type)` - AI analysis if exists
6. `search_failures(application, category, ...)` - Search/filter failures
7. `get_stats(application)` - Summary stats (pending, analyzed, costs)

**Tool Patterns (documented in tool descriptions):**
- ✅ Parallel for analysis: `get_stats("v3-4")`, `get_stats("v3-5")` → decide which to fix first
- ✅ Sequential for fixing: Complete all v3-4 PRs → then move to v3-5
- ✅ Hybrid check: While fixing v3-4, query v3-5 to check if same fix applies (smart reuse)
- ❌ Anti-pattern: Don't interleave PRs across versions (context switching nightmare)

**Response Models (Pydantic):**
- `ApplicationInfo` - Version metadata
- `FailureSummary` - Brief failure info (lists)
- `BuildFailureDetails` - Full context (logs, commit diff, Dockerfile, Tekton configs)
- `ConformaViolationDetails` - Full context (SBOM, policy rules, violation terms)
- `AnalysisDetails` - AI analysis result
- `AlertsSummary` - Unified view (build + Conforma)
- `StatsResponse` - Summary statistics

**Benefits:**
- No SSH/DB access needed - agents query via MCP protocol
- Other agents can integrate (not just local `ic` CLI)
- Enables AI-powered workflows: analyze → compare → fix → PR → test
- Reuses existing repositories (no duplication)
- Read-only (no orchestration - analysis stays in Python CLI)

### 1.8 Conforma Daily Reporting & Exception Tracking (COMPLETE)

**Goal:** Automate daily Conforma violation tracking for DLY Progress standup. Combine DB data, GitHub conforma-reporter exceptions, and GitLab konflux-release-data policy exceptions into a single report.

**Modified:** `ic` — added ~400 lines for reporting, exception cross-referencing, and expiring exception tracking.

**New CLI commands:**
- `ic conforma report` — Daily report with DLY-ready summary table
- `ic conforma report 3.4` — Filter by version
- `ic conforma report --summary` — Summary only (no per-component detail)
- `ic conforma report --no-cache` — Force re-fetch from GitHub/GitLab

**New helper functions in `ic`:**

| Function | Purpose |
|----------|---------|
| `app_to_reporter_branch()` | Converts `acme-v2-0` to `acme-3.4` branch name |
| `fetch_conforma_exceptions()` | Fetches exception rules from GitHub conforma-reporter (cached daily) |
| `fetch_gitlab_exceptions()` | Fetches active exception rules from GitLab policy files (cached daily) |
| `fetch_expiring_exceptions()` | Fetches expiring exceptions with dates from GitLab (cached daily) |
| `check_exception_status()` | Cross-references violations against both GitHub and GitLab sources |
| `find_component_expiry()` | Finds earliest expiring exception date per component |

**Data sources cross-referenced:**
1. **Local PostgreSQL** (`conforma_results` + `ai_analysis` tables) — violation data
2. **GitHub** `acme-org/conforma-reporter` — per-version branch exception/exclusion YAML (suppresses from CSV report only)
3. **GitLab** `releng/konflux-release-data` — 4 EnterpriseContractPolicy files (actual Conforma enforcement):
   - `registry-acme-prod.yaml`, `fbc-acme-prod.yaml`
   - `registry-acme-stage.yaml`, `fbc-acme-stage.yaml`

**Exception status logic:**
- `YES` — covered by both GitHub and GitLab
- `PARTIAL (github)` — suppressed from CSV report but still fires in PipelineRuns
- `PARTIAL (gitlab)` — policy exception exists but still in reporter CSV
- `NO` — no exception in either source

**Expiring exceptions:**
- 21-day window (matches Grafana alerts)
- Color-coded: red ≤7d ("expiring soon"), yellow ≤14d, normal ≤21d
- Groups by date with day name
- Shows already-expired exceptions
- Per-component expiry date in detailed breakdown

**Report output sections:**
1. DLY Progress table (copy-paste ready for standup)
2. Expiring exceptions detail (grouped by date, color-coded)
3. Already expired exceptions
4. Detailed breakdown per version (component, violations, exception source, AI status, expiry, action)
5. Source URLs for manual verification

**Also updated:** `ic get alerts` conforma section — Exception column now shows source (github/gitlab) with wider format.

---

## Phase 2: Build Failure Auto-Resolution

**Goal:** Auto-fix common build failures, create PRs. Start narrow, expand.

**Design Decision Pending:** Agent vs Tool approach
- **Agent approach**: Complex orchestration (analyze → validate → branch → commit → PR → monitor)
- **Tool approach**: Reusable MCP tool (simpler but less flexible)
- **Recommendation**: Start with agent, extract tool later after learning what's reusable
- Agent can use MCP tools (get_failure, get_analysis) within its workflow

### 2.1 MVP: dependency_issue only

**New file:** `collectors/python/agent.py`

Workflow:
1. Query `ai_analysis` where `can_auto_fix = TRUE` and no resolution attempt exists
2. Clone repo (shallow, specific branch)
3. Read files identified in `recommended_files`
4. Call Claude with: error, analysis, file contents — ask for specific diff
5. Apply diff, create branch `ci-autohealing/<component>/<failure-id>`
6. Push and create PR via GitHub API
7. Insert into `resolution_attempts`, update `build_failures.ai_fix_attempted`

### 2.2 GitHub integration

**New file:** `collectors/python/github_client.py`
- `clone_repo()`, `create_branch()`, `commit_and_push()`, `create_pull_request()`
- Uses `GITHUB_TOKEN` from `.env`
- PR template includes: PipelineRun link, AI analysis, what changed, automated fix notice

### 2.3 Fix verification loop

**New file:** `collectors/python/verify_fixes.py`
- Check `resolution_attempts` where `status = 'pr_created'`
- Was PR merged? Did next build succeed?
- Update `was_successful`, mark failure resolved if fix worked

### 2.4 Safety controls

Config in `.env`:
- `AI_AUTO_FIX_ENABLED=false` (explicit opt-in)
- `AI_MIN_CONFIDENCE=0.85`
- `AI_MAX_FIX_ATTEMPTS=3` per failure
- `AI_AUTO_FIX_CATEGORIES=dependency_issue` (start narrow)
- `AI_DRY_RUN=true` (generate fixes without pushing)
- `AI_REQUIRE_APPROVAL=true` (draft PRs)

Guardrails: never modify tests, max 3 files / 50 lines per fix, always new branch, 1 fix per component per day

### 2.5 Expand categories (after >70% success rate)

- Phase 2b: `config_error` — missing Dockerfile labels, YAML syntax
- Phase 2c: `build_error` — missing COPY sources, base image updates
- Phase 2d: `git_sync_issue` — branch reference corrections

### 2.6 CLI

New commands: `ic get fixes`, `ic fix <component>`, `ic fix <component> --dry-run`

### 2.7 ADR

`docs/adr/006-auto-fix-safety-controls.md`

---

## Phase 3: Conforma Auto-Resolution

**Goal:** Fix Enterprise Contract compliance violations. Different from build failures — these are policy violations, not code bugs.

### 3.1 Conforma fix agent

**New file:** `collectors/python/conforma_agent.py`

Priority order by auto-fixability:
1. **Missing required labels** (95% fixable) — add `LABEL` to Dockerfile
2. **Deprecated API version** (90% fixable) — update apiVersion in YAML
3. **Base image from unapproved registry** (60% fixable) — update FROM line
4. **Missing CVE scan** (low) — usually pipeline config, flag for manual review

### 3.2 Schema extension

Add to `conforma_results`: `ai_analyzed`, `ai_analysis_id`, `ai_fix_attempted`
Or create `conforma_analysis` table mirroring `ai_analysis`.

### 3.3 CLI

- `ic describe conforma <name>` — add AI analysis showing which violations are fixable
- `ic fix conforma <name>` — trigger fix attempt

### 3.4 ADR

`docs/adr/007-conforma-fix-strategy.md`

---

## Key Files

| File | Purpose |
|------|---------|
| `db/schema.sql` | `ai_analysis` and `resolution_attempts` tables already defined |
| `collectors/python/database.py` | Active Database class |
| `collectors/python/collect_comprehensive.py` | Primary collector, patterns to follow |
| `cron/collect-comprehensive.sh` | Orchestration to extend |
| `ic` | CLI to extend with analysis/fix commands |
| `.env` | Already has `ANTHROPIC_API_KEY`, `AI_*` config, `GITHUB_TOKEN` placeholders |
