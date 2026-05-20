# Roadmap: CI-Autohealing — From Monitoring to Auto-Resolution

**Created:** 2026-04-22  
**Last Updated:** 2026-05-18 (P0 complete, P1 added)

## Context

The ci-autohealing project has evolved into a solid failure detection and diagnosis system: cron collects data every 20min from KubeArchive, the `ic` CLI provides rich querying (alerts, describe, historical), and the database stores build failures + Conforma violations with full metadata. The next step is to build an AI agent that analyzes and resolves failures automatically.

## Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 0 | **Complete** | Cleanup: dead code removal, docs consolidation, tests, ADRs |
| Phase 1 | **Complete** | AI analysis of failures using Claude (1.1-1.9 complete) |
| Phase 2 | **Complete** | Auto-resolution of build failures via PRs |
| Phase 3 | **Complete** | Auto-resolution of Conforma compliance violations |
| Phase 4 | **In Progress** | Hardening: multi-app support, test coverage, Slack API, autonomous mode |
| **P0** | **Complete** | Context enrichment, pattern matching, batch analysis (infrastructure) |
| **P1** | **In Progress** | Complete P0 integration, pattern learning, fix improvements, notifications |

## Sequencing

```
Phase 0 (Cleanup) ──> Phase 1 (AI Analysis) ──┬──> Phase 2 (Build Fixes) ✓
                                               └──> Phase 3 (Conforma Fixes) ✓
                                                              ↓
                                               Phase 4 (Hardening) ✓ (mostly)
                                                              ↓
                       P0 (Infrastructure) ──> P1 (Intelligence) ←─ here
```

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

## Phase 1: AI Analysis (Complete)

**Goal:** Use Claude API to analyze failures, populate `ai_analysis` table, show analysis in `ic describe`. Analysis only — no PRs, no auto-fixes.

**Status:** **COMPLETE** - All subphases (1.1-1.8) implemented. MCP server (1.7) enables external agents to access Konflux data.

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
- `ic export <num|component> slack [--jira KEY] [--clipboard]` — Slack message (mrkdwn)
- Calculated fields: Priority (P1/P2/P3), Risk Level (HIGH/MEDIUM/LOW), Estimated Fix Time

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

**Slack export (`ic export <N> slack`):**
- Team handle auto-lookup from `rhoai-component-data.yaml` (cached in `/tmp`, 24h TTL, VPN-graceful)
- Separate templates for build failures and conforma violations (mrkdwn format)
- Optional `--jira KEY` to include ticket link, `--clipboard` to copy
- `parsers/lookup_team.py` — standalone YAML→handle resolver

### 1.7 MCP Server (COMPLETE)

**Goal:** Expose Konflux collectors as MCP tools for external AI agents (Claude Desktop, GitHub Copilot, Cursor, etc.)

**Files:**
- `konflux-mcp-server/src/konflux_mcp/server.py` - FastMCP server
- `konflux-mcp-server/src/konflux_mcp/tools.py` - 7 MCP tools
- `konflux-mcp-server/src/konflux_mcp/models.py` - Pydantic response models
- `konflux-mcp-server/src/konflux_mcp/repository_factory.py` - DB connection (no config dependency)

**7 MCP Tools:**
1. `list_applications()` - Discover available RHOAI versions
2. `list_alerts(application)` - Get all alerts (build + Conforma)
3. `get_failure(component, application)` - Full build failure details
4. `get_violation(component, application)` - Full Conforma violation details
5. `get_analysis(component, application, type)` - AI analysis if exists
6. `search_failures(application, category, ...)` - Search/filter failures
7. `get_stats(application)` - Summary stats (pending, analyzed, costs)

### 1.8 Conforma Daily Reporting & Exception Tracking (COMPLETE)

**New CLI commands:**
- `ic conforma report` — Daily report with DLY-ready summary table
- `ic conforma report 3.4` — Filter by version
- `ic conforma report --summary` — Summary only (no per-component detail)
- `ic conforma report --no-cache` — Force re-fetch from GitHub/GitLab

**Data sources:** Local PostgreSQL + GitHub conforma-reporter exceptions + GitLab konflux-release-data policy files (4 EnterpriseContractPolicy files).

**Exception status logic:** `YES` / `PARTIAL (github)` / `PARTIAL (gitlab)` / `NO`

**Expiring exceptions:** 21-day window, color-coded (red ≤7d, yellow ≤14d).

---

## Phase 2: Build Failure Auto-Resolution (Complete)

**Goal:** Auto-fix common build failures, create PRs via GitHub API. Start narrow, expand.

**Note on implementation:** The roadmap originally described a git-clone approach. The actual implementation uses the GitHub Contents API exclusively (no git binary required, no disk cloning). This is simpler, more portable, and works in environments without git credentials configured.

### 2.1 Fix generator (COMPLETE)

**File:** `collectors/python/fixers/fix_generator.py`

**Two modes:**
- `--mode pr` — Claude generates JSON fix spec, files fetched from GitHub, diff shown, PR created with `--execute`
- `--mode jira` — Stdin ticket text, interactive edit loop (LLM-assisted), POST to Jira API

**PR workflow:**
1. Load failure + AI analysis from DB
2. Fetch recommended files from GitHub (Contents API, no clone)
3. Call Claude with failure context + file contents → JSON fix spec
4. Show unified diff (dry-run by default)
5. With `--execute`: `get_ref_sha` → `create_branch` → `put_file` per file → `create_pull_request`
6. Record in `resolution_attempts` table

### 2.2 GitHub client (COMPLETE)

**File:** `collectors/python/clients/github_client.py`

**Read operations:** `get_commit`, `get_file_content`, `get_directory_listing`, `get_pr_for_commit`, `get_commit_context`, `get_pull_request`, `check_rate_limit`

**Write operations:** `create_branch`, `put_file`, `create_pull_request`, `get_ref_sha`, `get_file_sha`

### 2.3 Fix verification loop (COMPLETE)

**File:** `collectors/python/fixers/verify_fixes.py`

**Cron step 2.5** (after sync_component_status.py, only if `GITHUB_TOKEN` set):
- Checks `resolution_attempts WHERE pr_merged IS NULL AND status='pr_created'`
- Fetches PR status from GitHub API
- If PR merged: checks `build_failures.is_resolved=TRUE AND resolved_at > pr_merged_at`
- Updates `was_successful`, `verification_notes`, `status` ('success'/'failed'/'abandoned')
- Idempotent: merged-but-not-yet-resolved stays `pr_merged=NULL` for next cron run

### 2.4 Resolution tracking (COMPLETE)

**File:** `collectors/python/repositories/resolution_attempt_repository.py`

**Table:** `resolution_attempts` — tracks all PR fix attempts for both build and conforma failures.
- `record_pr_created()` — build failure PR, marks `build_failures.ai_fix_attempted=TRUE`
- `record_conforma_pr_created()` — conforma PR, marks `conforma_results.ai_fix_attempted=TRUE`
- `get_pending_verification()` — returns both build and conforma pending attempts
- `update_verification()` — marks success on the correct parent table
- `get_all(days=30)` — used by `ic get fixes`, includes `failure_type` column

**Schema:** `build_failure_id` nullable, `conforma_result_id` FK added, CHECK constraint enforces exactly one is set. See `db/migrations/005_conforma_resolution.sql`.

### 2.5 Safety controls (COMPLETE)

- Dry-run by default — `--execute` required to push
- Interactive confirmation in `ic fix` before pushing
- `--execute` flag is the explicit opt-in; no autonomous pushing
- Branch naming: `ci-autohealing/<component>/<failure-id>`

### 2.6 CLI (COMPLETE)

- `ic fix <num|component>` — Interactive triage: AI analysis display → action menu [1-4]
  - [1] PR dry-run → optional push
  - [2] Jira ticket (interactive edit loop)
  - [3] Slack notification
  - [4] Skip
- `ic get fixes [--days N] [--all]` — Table of all resolution attempts with status
- `ic jira link <component> <JIRA-KEY>` — Link a Jira ticket to a failure in DB
- `ic jira create conforma <component>` — POST conforma violation to Jira (--execute flag)
- `ic export <N> slack [--jira KEY] [--clipboard]` — Slack message ready to paste

### 1.9 AI Analyzer Hardening + Institutional Memory (COMPLETE)

**Goal:** Make the analyzers more robust under failure conditions, avoid re-analyzing the same unsolvable failures forever, and feed historical knowledge of known errors back into the LLM prompt so repeated failures get better/faster diagnoses.

#### Analysis state machine

**Migration:** `db/migrations/006_analysis_state.sql`
- `build_failures` + `conforma_results` gain: `ai_attempts INTEGER DEFAULT 0`, `ai_skip_reason TEXT`
- Partial indexes updated to exclude skipped rows (`ai_skip_reason IS NULL`)
- `ai_skip_reason` values: `null` (pending), `'no_logs'` (logs never arrived), `'max_retries'` (3 consecutive LLM failures)

**New repository methods** (`ai_analysis_repository.py`):
- `increment_attempts(build_failure_id|conforma_result_id)` — atomic UPDATE RETURNING, returns new count
- `mark_skipped(reason, ...)` — sets `ai_skip_reason` + `ai_analyzed=TRUE`
- `skip_no_logs_timeouts(application, timeout_days=7)` — bulk-marks failures with NULL logs after 7 days

**Analyzer changes** (both `build_failure_analyzer.py` and `conforma_analyzer.py`):
- `MAX_RETRIES = 3` class constant
- `run()` calls `skip_no_logs_timeouts()` before fetching pending
- Each failure: `increment_attempts()` → analyze → on exception: if `attempts >= MAX_RETRIES`, call `mark_skipped('max_retries')`
- `get_pending_failures()` / `get_pending_conforma_violations()` now exclude skipped rows

**`ic ai status` enhancements:**
- Separate counts for `Pending`, `No logs yet`, `Skipped (max retries or no-logs timeout)`
- High confidence vs low confidence split within Analyzed

#### Prompts as Markdown files

**Files created:**
- `prompts/build_failure_analyzer.md` — YAML frontmatter + full CI/CD troubleshooting system prompt
- `prompts/conforma_analyzer.md` — compliance specialist prompt with 12-violation catalog
- `prompts/fix_generator_pr.md` — JSON spec format for PR generation
- `prompts/fix_generator_jira.md` — Jira editor persona

**Loader:** `collectors/python/prompt_loader.py`
- Strips YAML frontmatter (`---` ... `---`) automatically
- Fails fast at import time if prompt file missing (startup-time error, not runtime)
- Used by all three analyzers: `SYSTEM_PROMPT = load_prompt('build_failure_analyzer')`

**Benefit:** Prompts can be edited by non-developers, version-controlled separately, and tested by reading the Markdown directly.

#### Error pattern library (institutional memory)

**Migration:** `db/migrations/007_error_patterns.sql`
- New table `error_patterns`: one row per `(failure_type, failure_category)` — coarse-grained for v1
- Columns: `typical_fix TEXT`, `doc_url TEXT`, `doc_context TEXT` (cached doc excerpt), `occurrence_count`, `avg_confidence` (rolling average), `first/last_seen_at`
- FK from `ai_analysis.error_pattern_id` to `error_patterns`
- **Seeded with 17 known patterns** (7 build categories + 10 conforma categories) including `typical_fix` text for each

**Repository:** `collectors/python/repositories/error_pattern_repository.py`
- `find_or_create(failure_type, failure_category)` — upsert on unique key
- `record_occurrence(pattern_id, confidence_score)` — increments count, updates rolling avg_confidence
- `update_doc_context(pattern_id, doc_context)` — stores fetched doc excerpt
- `get_needing_doc_fetch(stale_days=7)` — patterns with `doc_url` but missing/stale `doc_context`
- `link_analysis(analysis_id, pattern_id)` — sets `ai_analysis.error_pattern_id`

**LATERAL JOIN in pending queries:** `get_pending_failures()` / `get_pending_conforma_violations()` now join `ai_analysis → error_patterns` to return `pattern_typical_fix`, `pattern_doc_context`, `pattern_name`, `pattern_id` alongside each pending failure — zero extra round-trips.

**Analyzer integration:** After each successful analysis, both analyzers call `find_or_create → record_occurrence → link_analysis`. Before calling the LLM, `_format_pattern_section()` injects a "Known Pattern" section into the prompt when prior fix/doc data exists.

#### Doc context collector

**File:** `collectors/python/collect_doc_context.py`
- Fetches Konflux (`https://konflux.pages.redhat.com/docs/users/`) and Conforma (`https://conforma.dev/docs/user-guide/`) documentation pages for patterns that have `doc_url` but missing/stale `doc_context`
- Uses stdlib `urllib.request` + `html.parser` — no external deps
- Cache: `/tmp/konflux-docs-cache/<md5(url)>.txt`, 7-day TTL
- VPN-graceful: any network failure → log warning → continue (never blocks analysis)
- Cron step 8/8 in `collect-comprehensive.sh`

**CLI:** `ic patterns list [--type build|conforma]` — table with frequency, avg confidence, doc status
**CLI:** `ic patterns show <name>` — full detail: description, typical fix, doc URL, 20-line doc excerpt

#### Bug fix: `sync_component_status.py` phantom failures

**File:** `collectors/python/collectors/status_synchronizer.py`

**Problem:** If `oc get component <name>` failed (component renamed, deleted, or cluster timeout), `run()` skipped the entire component with "Component not found in cluster" — it never called `get_current_status()`, so the failure stayed `is_resolved=FALSE` forever even if all builds had succeeded.

**Fix:** Removed the `continue` guard. `get_current_status()` only needs the component name (queries PipelineRuns by label selector) — `repository_url` is only needed for `record_successful_build()`, which gracefully accepts empty strings. Components without a Component CR now still get their build status checked and resolved if appropriate.

### 2.7 Commit context collection (COMPLETE)

**File:** `collectors/python/collect_commit_context.py`

**Cron step 6** (only if `GITHUB_TOKEN` set): fetches commit diff, Dockerfile, .tekton configs, and associated PR info for each unanalyzed failure. Stored in `build_failures` for richer AI context.

---

## Phase 3: Conforma Auto-Resolution (In Progress)

**Goal:** Fix Enterprise Contract compliance violations automatically. Separate pipeline from build fixes — different data, different fix strategies, different resolution tracking.

### 3.1 Schema extension (COMPLETE)

**Migration:** `db/migrations/005_conforma_resolution.sql`
- `conforma_results` gains: `ai_analyzed`, `ai_analysis_id`, `ai_fix_attempted`, `ai_fix_successful`
- `resolution_attempts` extended: `build_failure_id` made nullable, `conforma_result_id` FK added, CHECK constraint (exactly one set)
- Index: `idx_ra_conforma`

### 3.2 CLI triage flow (COMPLETE)

**`ic fix <conforma-component>`** — Jira-first sequential flow (separate from build menu):
1. Jira ticket draft → interactive edit → optional POST
2. After Jira: "Also generate an automated PR fix?" (offered when `ai_analysis.can_auto_fix=TRUE`)
3. PR dry-run → "Push to GitHub?" → `--execute`
4. "Send Slack notification?"

**PR fix dispatch** — `fix_generator.py` auto-detects the violation category from DB and routes to the correct deterministic fixer. Calling code (ic) only needs `--conforma-id`.

**`cmd_fix()` bug fixes applied:**
- Queries `conforma_results` (not `build_failures`) for conforma type
- AI analysis lookup uses `conforma_result_id` FK (not `build_failure_id`)
- Display shows "conforma violation in scenario X (N violations)"

### 3.3 Conforma fix generator — policy_deprecated_task (COMPLETE)

**File:** `collectors/python/fixers/fix_generator.py` (`run_conforma_pr_mode`)

**Deterministic fix (no LLM):**
1. Load `conforma_results` + AI analysis from DB (`load_conforma_and_analysis`)
2. Parse `violation_details` JSONB → extract old/new bundle ref pairs (`parse_deprecated_task_fixes`)
   - Regex: `oci://...@sha256:<digest>` pairs from `solution` field
3. Fetch all `.tekton/*.yaml` files from GitHub
4. Replace old refs with new refs (string substitution)
5. Show unified diff (dry-run), then `--execute` path: branch → push → PR → record attempt

**CLI arg:** `--conforma-id` (parallel to `--failure-id`, mutually exclusive)

### 3.4 Remaining conforma violation categories (In Progress)

#### 3.4.1 `policy_hermetic_build` (COMPLETE)

**File:** `collectors/python/fixers/fix_generator.py` (`run_conforma_hermetic_mode`)

**Fix:** Set `hermetic: "true"` in `.tekton/*.yaml` pipeline params. Deterministic — no LLM call.

**Key design decisions:**
- Searches `konflux-central` repo first (shared pipeline templates for RHOAI components), then falls back to the component's own repository. This matches the actual codebase structure: RHOAI push pipeline definitions live in `konflux-central`, not in each component repo.
- File discovery: lists `.tekton/` directory, prefers files whose filename contains the component name, falls back to content search if no name match
- Regex fix: `(- name: hermetic\n\s+(?:value|default): )(?:"false"|false)` → `"true"` (handles both quoted and unquoted, both `value` and `default`)
- Category dispatch: `main()` peeks at DB category before calling the right handler — no changes needed to `ic`
- Offered to user in `ic fix` whenever `ai_analysis.can_auto_fix=TRUE` (not hardcoded to a category name)

#### 3.4.2 `policy_unpinned_task` (COMPLETE)

**File:** `collectors/python/fixers/fix_generator.py` (`run_conforma_unpinned_task_mode`)

**Fix:** Find all floating `quay.io/repo:tag` refs (no `@sha256:`) across `.tekton/*.yaml`, resolve the current digest via quay.io v2 API (`GET /v2/{repo}/manifests/{tag}`, read `Docker-Content-Digest` header), and append `@sha256:<digest>`. Works independently of `violation_details` structure — uses registry API directly.

**Key design decisions:**
- `_FLOATING_REF_RE` regex with dual negative lookaheads: `(?![a-z0-9._\-])` (boundary) + `(?!@sha256:)` (exclude already-pinned). The boundary assertion prevents backtracking from matching partial tags within already-pinned refs.
- Searches component's own repo only (task refs live in component repos, not konflux-central)
- Refs that fail API resolution are left in place with a warning in the PR body

#### 3.4.3 `policy_untrusted_image` (COMPLETE)

**File:** `collectors/python/fixers/fix_generator.py` (`run_conforma_untrusted_image_mode`)

**Fix:** Extract flagged pinned refs from `violation_details` (msg, solution, description fields); re-resolve each tag to the current digest via quay.io v2 API; substitute in both `.tekton/*.yaml` and Containerfile candidates. Uses `_refresh_pinned_ref()` helper.

**Key design decisions:**
- Searches both `.tekton/` and Containerfile/Dockerfile candidates (unlike unpinned_task which is `.tekton/`-only)
- `oci://` prefix on refs is preserved when re-pinning
- Same-digest case (ref already current) returns the old ref unchanged, producing a no-op diff

#### 3.4.4 `policy_sbom_vendor_label` (COMPLETE)

**File:** `collectors/python/fixers/fix_generator.py` (`run_conforma_sbom_vendor_label_mode`)

**Fix:** Insert `LABEL vendor="Red Hat, Inc."` into `Containerfile` after the last existing `LABEL` line, or before `CMD`/`ENTRYPOINT` if no labels exist, or append at end. Idempotent — skips if vendor label already present.

#### 3.4.5 `policy_rpm_repository` (COMPLETE)

**File:** `collectors/python/fixers/fix_generator.py` (`run_conforma_rpm_repo_id_mode`)

**Fix:** Replace generic repo section IDs (`[ubi-N-foo-rpms]`) with arch-specific format (`[ubi-N-for-$basearch-foo-rpms]`) in `rpms.in.yaml` and `*.repo` files. Regex-only, no LLM.

#### 3.4.6 Other categories → manual review (implemented behavior)

All other violation categories (`policy_signing_key`, `policy_rpm_repository`, etc.) remain in the Jira-first flow with no auto-fix attempt. The `can_auto_fix` flag in the AI analysis controls whether the PR fix option is offered.

### 3.5 ADR (COMPLETE)

`docs/adr/006-conforma-fix-strategy.md` — documents the separate fix code paths, Jira-first flow rationale, and deterministic vs LLM fix strategy per violation type.

---

---

## P1: AI Analysis Infrastructure Improvements (In Progress)

**Goal:** Complete P0 integration, add pattern learning automation, improve auto-fix generation, and prepare notification infrastructure.

**Context:** P0 introduced context enrichment, pattern matching, and batch analysis. P1 focuses on making these systems production-ready and adding intelligence/automation.

### P1.1 Complete P0 Integration (In Progress)

**Goal:** Validate and complete the P0 improvements end-to-end.

**Tasks:**
1. **Populate pattern confidence** — Run AI analyses to populate `avg_confidence` in `error_patterns` table
   - Current: Patterns exist but `avg_confidence` is NULL (needs real analyses)
   - Target: 10+ analyses per pattern to establish baseline confidence
   - Enables pattern boost to work (currently inactive)

2. **Install anthropic module** — Enable batch analysis functionality
   - Current: `ModuleNotFoundError: No module named 'anthropic'`
   - Fix: `pip install anthropic` or add to `requirements.txt`
   - Validates `BatchAnalysisService` works end-to-end

3. **Run full enrichment** — Process all 58 pending failures
   - Current: 2.6% coverage (1/38 enriched)
   - Target: 100% coverage
   - Command: `./enrich_context.py --limit 100`
   - Validates both enrichment sources (dependency_changes + related_failures)

4. **Verify pattern boost** — Confirm confidence boost works with real data
   - Current: Infrastructure ready, needs `avg_confidence` populated
   - Validation: Query for analyses with `analysis_json->'pattern_boost'`
   - Measure: % of analyses that received boost

5. **Measure real metrics**
   - Enrichment coverage: X% → 100%
   - Pattern reuse: 0% → Y%
   - Batch throughput: measure actual analyses/hour
   - Queue ETA accuracy: compare predicted vs actual

**Blocked by:** None (all code complete)

### P1.2 Pattern Learning Automation (Not Started)

**Goal:** Automatically improve patterns based on fix outcomes.

**File:** `collectors/python/patterns/pattern_learner.py` (new)

**Features:**
1. **Auto-update patterns after successful fixes**
   - When `resolution_attempts.was_successful = TRUE`, update linked pattern
   - Increment `success_count`, update `avg_confidence` upward
   - Extract `typical_fix` from successful PR if not set

2. **Track pattern accuracy**
   - New columns: `error_patterns.success_count`, `error_patterns.fail_count`
   - Calculate accuracy: `success / (success + fail)`
   - Decay patterns with accuracy < 50% over 10+ attempts

3. **Auto-generate new patterns**
   - After 3 successful fixes with same `failure_category` but no pattern match
   - Create new pattern with `typical_fix` from most recent successful PR
   - Initial `avg_confidence` = mean of the 3 analyses

4. **Pattern decay/archival**
   - Mark patterns as `archived = TRUE` if:
     - No occurrences in last 90 days
     - Accuracy < 30% over 20+ attempts
   - Archived patterns excluded from matching but kept for historical data

**Integration:**
- `verify_fixes.py` calls `PatternLearner.update_from_resolution()` after marking success/fail
- `BuildFailureAnalyzer` calls `PatternLearner.suggest_new_pattern()` after each analysis
- `ic patterns list --show-archived` flag to view archived patterns

**Cron:** Step 2.6 (after `verify_fixes.py`)

### P1.3 Notification & Alerting System (Blocked — no Slack/email)

**Goal:** Automated alerts for queue health and analysis failures.

**Blocked by:** 
- Slack: No `SLACK_BOT_TOKEN` available (same blocker as Phase 4.3)
- Email: No SMTP server configured

**Design (ready for implementation when tokens available):**

**File:** `collectors/python/notifiers/alert_service.py` (new)

**Triggers:**
1. **Queue depth alerts**
   - Threshold: `build_pending > 100` OR `conforma_pending > 50`
   - Frequency: Once per day (don't spam)
   - Message: Queue depth, ETA, link to `ic ai status`

2. **Enrichment failures**
   - Threshold: `failed_enrichments > 10` in last hour
   - Message: Count, sample failure IDs, link to logs

3. **Analysis failures**
   - Threshold: `ai_skip_reason='max_retries'` count > 5 in last run
   - Message: Count, categories affected, suggest increasing `MAX_RETRIES`

4. **Daily summary** (optional)
   - Analyses completed: X
   - Fixes created: Y
   - Queue status: Z pending
   - Coverage: enrichment A%, analysis B%

**Channels:**
- Slack: Post to `#rhoai-ci-autohealing` (when token available)
- Email: Send to distribution list (when SMTP configured)
- Webhook: POST JSON to configurable URL (token-less option)

**Configuration:**
```bash
# .env additions (when available)
ALERT_SLACK_CHANNEL=#rhoai-ci-autohealing
ALERT_EMAIL_TO=team@example.com
ALERT_WEBHOOK_URL=https://...  # Alternative if no Slack/email
ALERT_QUEUE_THRESHOLD=100
```

**Workaround until tokens available:**
- Write alerts to `/tmp/ci-autohealing/alerts.log`
- `ic alerts check` command to view recent alerts
- Manual review of log file

### P1.4 Auto-fix Generation Improvements (Not Started)

**Goal:** Use enriched context and pattern matches to improve fix quality.

**File:** `collectors/python/fixers/fix_generator.py` (modify)

**Improvements:**

1. **Inject enriched_context into LLM prompts**
   - Current: Only uses `build_logs`, `commit_context`
   - Add: `enriched_context` (dependency_changes + related_failures)
   - Benefit: LLM sees related failures and their fixes, dependency changes that triggered failure

2. **Use pattern matches for fix suggestions**
   - Current: LLM generates fix from scratch
   - Add: If pattern match exists with `typical_fix`, inject as "known solution"
   - Prompt: "This failure matches pattern X. Previous fix: Y. Consider adapting this approach."

3. **Track fix success rate by pattern**
   - New table: `pattern_fix_outcomes` links `resolution_attempts` to `error_patterns`
   - Metrics: success rate per pattern, avg time-to-merge
   - Query: `ic patterns stats <name>` shows fix outcomes

4. **Learn from failed fixes**
   - When `was_successful = FALSE`, store failure reason
   - New column: `resolution_attempts.failure_reason TEXT`
   - Categories: "tests still failing", "PR rejected", "conflict with main", "wrong approach"
   - Feed back to pattern: decrement confidence, mark pattern as needing review

**Integration points:**
```python
# In build_analysis_prompt()
if enriched_context:
    prompt += format_enriched_context(enriched_context)
    
# In fix_generator.py
if pattern_boost_metadata:
    typical_fix = pattern_boost_metadata['pattern_name']
    prompt += f"Known solution: {typical_fix}\n"
```

**Expected impact:**
- Fix success rate: current unknown → target 70%+
- Time to fix: measure baseline → reduce by suggesting known solutions
- Pattern accuracy: track and improve over time

---

## Phase 4: Hardening (In Progress)

**Goal:** Remove environmental assumptions, broaden test coverage, add Slack API integration, and move toward autonomous operation.

### 4.1 Multi-application support (COMPLETE)

Remove the hard-wired `acme-v2-0` assumption so the tool works across all RHOAI versions without manual switching.

**Changes:**
- `ic-config.sh`: Add `KNOWN_APPLICATIONS` variable (replaces hard-coded list buried in `ic`)
- `ic conforma report`: Read `KNOWN_APPLICATIONS` instead of inline literal
- `fix_generator.py`: Remove `acme-v2-0` argparse default; derive from first app in DB if `--application` not passed
- `ic get alerts` / `ic alerts`: Add `--all` flag to show failures across all applications at once, grouped by application

### 4.2 Deterministic fixer test suite (COMPLETE)

55 unit tests across 8 pure functions in `collectors/python/tests/test_fix_generator.py`. Two real production bugs found and fixed:
- `_FLOATING_REF_RE` backtracking: partial tags within pinned refs were incorrectly matched as floating
- `apply_hermetic_fix` `\b` boundary: word-boundary fails for quoted `"false"` (non-word char on both sides)

### 4.3 Slack API integration (Blocked — no token)

When a Slack bot token is available, add `ic export <N> slack --send` to post directly to the team's channel instead of clipboard. Currently `export_slack()` generates mrkdwn text for manual paste.

**Prerequisite:** Slack bot token with `chat:write` scope, stored in `.env` as `SLACK_BOT_TOKEN`.

### 4.4 Autonomous mode infrastructure (COMPLETE — disabled by default)

**Files:**
- `collectors/python/fixers/auto_fix.py` — NEW autonomous runner
- `cron/collect-comprehensive.sh` — step 7.5 (guarded by `AUTONOMOUS_MODE=true`)
- `ic-config.sh` — `AUTONOMOUS_MODE` variable (default: false)
- `collectors/python/config.py` — `AUTO_FIX_MAX_PER_RUN` (3), `AUTO_FIX_MIN_CONFIDENCE` (0.95)
- `collectors/python/fixers/fix_generator.py` — `attempted_by='ic-fix'` default added to 7 functions
- `collectors/python/repositories/resolution_attempt_repository.py` — `attempted_by` param in 2 record functions

**Design:**
- All 5 safety gates must pass: `can_auto_fix=TRUE`, `requires_human_review=FALSE`, `confidence_score ≥ 0.95`, `ai_fix_attempted=FALSE`, no open ci-autohealing branch on GitHub
- Only conforma fixers — no LLM-generated build failure diffs
- Writes `attempted_by='autonomous'` to `resolution_attempts` for audit trail
- Capped at `AUTO_FIX_MAX_PER_RUN=3` per cron invocation
- `verify_fixes.py` picks up autonomous PRs automatically — no changes needed

**To enable:** Set `AUTONOMOUS_MODE=true` in `.env` after validating manual `ic fix` on at least 5 conforma violations across two application versions.

---

## Key Files

| File | Purpose |
|------|---------|
| `db/schema.sql` | Full schema (build_failures, conforma_results, ai_analysis, resolution_attempts, error_patterns) |
| `db/migrations/` | 003 (conforma AI), 004 (jira_key), 005 (conforma resolution), 006 (analysis state), 007 (error patterns) |
| `prompts/build_failure_analyzer.md` | Build failure LLM system prompt (Markdown, YAML frontmatter) |
| `prompts/conforma_analyzer.md` | Conforma violation LLM system prompt with 12-violation catalog |
| `prompts/fix_generator_pr.md` | PR fix spec generation prompt |
| `prompts/fix_generator_jira.md` | Jira ticket editor persona |
| `collectors/python/prompt_loader.py` | Loads prompt Markdown, strips frontmatter |
| `collectors/python/repositories/error_pattern_repository.py` | Error pattern library CRUD |
| `collectors/python/repositories/ai_analysis_repository.py` | AI analysis + attempt tracking + LATERAL JOIN for patterns |
| `collectors/python/collect_doc_context.py` | Fetches doc pages for known error patterns (cron step 8) |
| `collectors/python/analyzers/build_failure_analyzer.py` | Build failure orchestrator (prompts, state machine, pattern wiring) |
| `collectors/python/analyzers/conforma_analyzer.py` | Conforma violation orchestrator (same) |
| `collectors/python/fixers/fix_generator.py` | Build + conforma PR generator and Jira mode |
| `collectors/python/fixers/verify_fixes.py` | PR merge + build success verification loop |
| `collectors/python/clients/github_client.py` | GitHub Contents API (read + write) |
| `collectors/python/repositories/resolution_attempt_repository.py` | Fix attempt tracking |
| `collectors/python/collect_commit_context.py` | Commit context pre-fetcher |
| `collectors/python/collectors/status_synchronizer.py` | Component status sync + failure resolution |
| `cron/collect-comprehensive.sh` | Cron orchestration (steps 1-8) |
| `ic` | Main CLI (~5500 lines) |
| `ic-config.sh` | Defaults + COMPONENT_DATA_URL, GITLAB_API_BASE |
| `ic-queries.sh` | Reusable SQL query functions |
| `parsers/lookup_team.py` | YAML→Slack team handle resolver |
| `.env` | ANTHROPIC_API_KEY, GITHUB_TOKEN, JIRA_EMAIL, JIRA_TOKEN, LLM_PROVIDER |
