# Roadmap: CI-Autohealing — From Monitoring to Auto-Resolution

**Created:** 2026-04-22
**Last Updated:** 2026-04-22

## Context

The ci-autohealing project has evolved into a solid failure detection and diagnosis system: cron collects data every 20min from KubeArchive, the `ic` CLI provides rich querying (alerts, describe, historical), and the database stores build failures + Conforma violations with full metadata. The next step is to build an AI agent that analyzes and resolves failures automatically.

## Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 0 | **Complete** | Cleanup: dead code removal, docs consolidation, tests, ADRs |
| Phase 1 | Planned | AI analysis of failures using Claude API |
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

### 1.1 Build failure analyzer

**New file:** `collectors/python/analyzer.py`

- Query `build_failures` where `ai_analyzed = FALSE AND is_resolved = FALSE AND build_logs IS NOT NULL`
- Construct prompt with: component name, error message/type, failed step, logs (truncated to ~50K chars), commit info, repo URL
- Call Claude (Sonnet) with structured output via tool_use: `root_cause`, `failure_category`, `confidence_score`, `recommended_fix`, `recommended_files`, `can_auto_fix`
- Insert into `ai_analysis` table, update `build_failures.ai_analyzed`
- Track in Langfuse
- Batch limit: 5 per run

Failure categories: `dependency_issue`, `build_error`, `test_failure`, `resource_limit`, `config_error`, `git_sync_issue`, `infrastructure`

### 1.2 Conforma analyzer

**New file:** `collectors/python/conforma_analyzer.py`

- Parse structured `violation_details` JSON (not raw logs)
- Classify each violation: missing labels, CVE scan missing, base image issues, deprecated API
- Assess auto-fixability per violation type
- Add `ai_analyzed` column to `conforma_results` table

### 1.3 Integrate into cron

**Modify:** `cron/collect-comprehensive.sh` — add step 6:
```
[6/6] Running AI analysis on new failures...
python3 analyzer.py --limit 5
```

### 1.4 CLI display

**Modify:** `ic` — add to `cmd_describe_component`:
```
========================================
AI Analysis
========================================
Category:     dependency_issue
Confidence:   0.92
Root Cause:   Package nvidia-ml-py==12.535.77 not found on PyPI...
Recommended:  Update requirements.txt line 42: nvidia-ml-py==12.535.161
Can Auto-Fix: YES
```

New commands: `ic get analysis`, `ic get analysis --pending`

### 1.5 Langfuse integration

**New file:** `collectors/python/langfuse_client.py`
- Create traces per analysis run, generations per Claude call
- Record tokens, cost, duration
- Link trace IDs to `ai_analysis.langfuse_trace_id`

### 1.6 ADR

`docs/adr/005-ai-analysis-architecture.md` — Sonnet choice, batch strategy, prompt design, category taxonomy

---

## Phase 2: Build Failure Auto-Resolution

**Goal:** Agent that fixes common build failures, creates PRs. Start narrow, expand.

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
