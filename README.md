# CI Auto-Healing

Monitors Konflux CI/CD pipeline failures, diagnoses root causes with AI, generates fixes, and manages Jira communication — all from the command line.

---

## End-to-end flow

```
Konflux pipeline fails
        │
        ▼  every 20 min (cron)
┌───────────────────┐
│  Data collection  │  collect_comprehensive.py → build_failures / conforma_results
└───────────────────┘
        │
        ▼  same cron run
┌───────────────────┐
│   AI analysis     │  analyze_failures.py / analyze_conforma.py
│                   │  → root cause, category, can_auto_fix flag
└───────────────────┘
        │
        ▼  you
┌───────────────────┐
│   ic CLI          │  ic get alerts  →  ic 1  →  ic fix 1
└───────────────────┘
        │
        ├──► Auto PR     ic fix 1 → [1] PR → review diff → push → GitHub PR
        │                Verified next cron: PR merged + build green → marked resolved
        │
        ├──► Jira        ic fix 1 → [2] Jira → edit loop → POST ticket
        │
        └──► Slack       ic fix 1 → [3] Slack message → clipboard → paste in channel

Jira reply monitoring (separate cron step 9):
        poll_jira_comments.py → notify-send → ic jira inbox → ic jira inbox refine N
```

---

## What is implemented

### Data collection

Cron script `cron/collect-comprehensive.sh` runs every 20 minutes with 9 steps:

- Step 1 — `collect_comprehensive.py`: Tekton PipelineRun failures from KubeArchive + Kubernetes API
- Step 2 — `sync_component_status.py`: marks failures resolved when builds pass
- Step 2.5 — `fixers/verify_fixes.py`: checks if auto-fix PRs merged and builds recovered (requires `GITHUB_TOKEN`)
- Step 3 — `check_sync_status.py`: updates sync status cache
- Step 4 — `check_conforma_status.py`: Conforma (Enterprise Contract) test results
- Step 5 — `collect_conforma.py`: full violation details per component
- Step 6 — `collect_commit_context.py`: commit diff, Dockerfile, .tekton/ configs from GitHub (requires `GITHUB_TOKEN`)
- Step 7 — `analyze_failures.py` + `analyze_conforma.py`: AI root cause analysis (requires `LLM_PROVIDER`)
- Step 8 — `collect_doc_context.py`: fetches Konflux/Conforma docs for known error patterns
- Step 9 — `poll_jira_comments.py`: polls Jira tickets for new comments, drafts AI replies, fires desktop notifications (requires `JIRA_EMAIL` + `JIRA_TOKEN` + `LLM_PROVIDER`)

### AI analysis

Both build failures and Conforma violations go through an LLM (Claude on Vertex AI or direct Anthropic API):

Build failures are classified into: `dependency_issue`, `build_error`, `test_failure`, `resource_limit`, `config_error`, `git_sync_issue`, `infrastructure`.

Conforma violations are classified into: `policy_hermetic_build`, `policy_unpinned_task`, `policy_untrusted_image`, `policy_deprecated_task`, `policy_signing_key`, and others.

Each analysis produces: `root_cause`, `failure_category`, `confidence_score`, `recommended_fix`, `can_auto_fix`. The `can_auto_fix` flag controls whether `ic fix` offers a PR.

State machine: failures with no logs after 7 days are marked `ai_skip_reason='no_logs'`. Failures that fail analysis 3 times are marked `ai_skip_reason='max_retries'`. Neither is retried.

Institutional memory: an `error_patterns` table stores per-category `typical_fix` and cached documentation excerpts. These are injected into the LLM prompt on re-analysis.

### ic CLI — main commands

```bash
# See what is failing
ic get alerts                        # build failures + conforma violations, unified
ic get components                    # components with active build failures
ic get conforma                      # conforma violation summary

# Inspect a failure
ic 1                                 # describe component #1 (shortcut)
ic describe component <name>         # full build failure details
ic describe conforma <name>          # conforma violation details
ic why <name>                        # AI root cause summary

# Triage and fix
ic fix 1                             # interactive: AI analysis → action menu
ic fix <component>                   # same, by name

# Jira integration
ic get jira                          # list linked tickets with live status
ic jira inbox                        # unreviewed Jira comments with AI drafts
ic jira inbox refine <N>             # refine draft N via AI conversation
ic jira inbox done <N>               # mark item N reviewed after posting

# Track fix attempts
ic get fixes                         # table of PR fix attempts and outcomes
ic get fixes --all                   # all time

# AI management
ic ai status                         # pending / analyzed / skipped counts
ic ai stats                          # breakdown by category and date
ic ai analyze <name>                 # trigger analysis for one component

# Export
ic export <N> jira                   # Jira ticket template (dry-run)
ic export <N> slack [--jira KEY]     # Slack mrkdwn message, ready to paste
ic export <N> --clipboard            # copy to clipboard instead of stdout

# Conforma reporting
ic conforma report                   # daily standup table
ic conforma report 3.4               # filter by version

# Error pattern library
ic patterns list                     # known error patterns with frequency
ic patterns show <name>              # full detail + doc excerpt
```

### Fix generation — what can be auto-fixed

Auto-fix creates a branch, commits the change, and opens a GitHub PR. Dry-run by default; `--execute` required to push. The fix type is dispatched from the AI analysis `failure_category` — `ic` only passes the failure ID.

**`policy_deprecated_task`** (conforma) — replaces old task bundle refs with pinned digests extracted from `violation_details.solution`. Targets the component's `.tekton/` directory.

**`policy_hermetic_build`** (conforma) — sets `hermetic: "true"` in `.tekton/*.yaml` pipeline params. Targets `konflux-central` (shared RHOAI pipeline templates) first, then the component repo as fallback.

**General build failures** — Claude generates a JSON fix spec (files + changes), fetched from GitHub, shown as diff, pushed with `--execute`.

### Jira comment monitoring

`poll_jira_comments.py` runs in cron step 9. For each Jira ticket linked in `build_failures` or `conforma_results` where `is_resolved=FALSE`:

- fetches comments from Jira REST API
- skips comments already in `jira_comment_drafts` (watermark: `UNIQUE(jira_key, comment_id)`)
- skips comments posted by the bot account itself
- calls LLM (system prompt: `prompts/jira_reply_drafter.md`) with full failure context
- inserts draft into `jira_comment_drafts`
- fires `notify-send` desktop notification

`ic jira inbox` shows unreviewed items with the original comment (fetched live) and the AI draft.

`ic jira inbox refine N` opens an interactive refinement loop: you give feedback, the LLM revises, repeat until satisfied. On exit, if at least one revision was made, a second LLM call (system prompt: `prompts/jira_prompt_analyzer.md`) checks whether your feedback reveals a systematic style preference and proposes an addition to `jira_reply_drafter.md` for your approval.

### MCP server

`konflux-mcp-server/` exposes 7 tools for external AI agents (Claude Desktop, etc.):

- `list_applications()`, `list_alerts(application)`, `get_failure(component, application)`
- `get_violation(component, application)`, `get_analysis(component, application, type)`
- `search_failures(...)`, `get_stats(application)`

---

## What is left to build

**Phase 3.4.2 — `policy_unpinned_task` auto-fixer**

Task bundle refs use floating tags (`quay.io/.../task:0.3`) instead of pinned digests. Fix: resolve current digest via quay.io REST API (`GET /v2/<repo>/manifests/<tag>`, read `Docker-Content-Digest` header), append `@sha256:<digest>` in `.tekton/*.yaml`.

Open question: whether `violation_details.solution` already contains the pinned ref (as `policy_deprecated_task` does) or only the unpinned ref. Needs verification against live data before implementing.

**Phase 3.4.3 — `policy_untrusted_image` auto-fixer**

Dockerfile `FROM` lines use non-approved registries. Fix: replace with the approved alternative from `violation_details.solution`. Different target from unpinned task (Dockerfile, not `.tekton/`).

**Phase 3.5 — ADR 007: conforma fix strategy**

`docs/adr/007-conforma-fix-strategy.md` — document the separate-pipelines decision, Jira-first flow rationale, and deterministic-vs-LLM per violation type.

**DB migration 008 — apply to activate Jira inbox**

`db/migrations/008_jira_comment_drafts.sql` creates the `jira_comment_drafts` table. Has been written; needs to be applied before `ic jira inbox` and `poll_jira_comments.py` can run.

```bash
docker exec <db-container> psql -U postgres -d konflux_monitoring \
  -f /path/to/db/migrations/008_jira_comment_drafts.sql
```

---

## Environment variables

```bash
# Required always
DB_HOST=localhost
DB_PORT=5433
DB_USER=postgres
DB_PASSWORD=admin
DB_NAME=konflux_monitoring

# Required for Kubernetes data collection
NAMESPACE=NAMESPACE_PLACEHOLDER
APPLICATION_NAME=acme-v2-0

# Required for AI analysis and Jira reply drafting
LLM_PROVIDER=vertex_ai            # or: anthropic
ANTHROPIC_VERTEX_PROJECT_ID=...   # Vertex AI (GCP ADC)
ANTHROPIC_API_KEY=...             # Direct Anthropic API

# Required for commit context and PR creation
GITHUB_TOKEN=...

# Required for Jira ticket creation and comment monitoring
JIRA_EMAIL=you@redhat.com
JIRA_TOKEN=...
JIRA_URL=https://JIRA_HOST
JIRA_PROJECT=RHOAIENG
```

---

## Setup

```bash
# Start database
./db-start.sh

# Apply all migrations
for f in db/migrations/*.sql; do
    docker exec <db-container> psql -U postgres -d konflux_monitoring -f /workspace/$f
done

# Install Python deps
pip install -r requirements.txt

# Install cron job (every 20 min)
./cron/install-cron.sh

# Verify
ic stats
```

---

## Key files

| Path | Purpose |
|------|---------|
| `ic` | Main CLI (~5600 lines) |
| `cron/collect-comprehensive.sh` | Cron orchestrator (9 steps) |
| `collectors/python/analyzers/build_failure_analyzer.py` | Build failure LLM orchestrator |
| `collectors/python/analyzers/conforma_analyzer.py` | Conforma violation LLM orchestrator |
| `collectors/python/fixers/fix_generator.py` | PR + Jira fix generator |
| `collectors/python/fixers/verify_fixes.py` | Fix verification loop |
| `collectors/python/poll_jira_comments.py` | Jira comment polling + draft generation |
| `collectors/python/jira_refine.py` | Interactive draft refinement + meta-learning |
| `collectors/python/clients/jira_client.py` | Jira REST API client |
| `collectors/python/clients/github_client.py` | GitHub Contents API client |
| `collectors/python/clients/llm_provider.py` | LLM provider interface (Vertex AI / Anthropic) |
| `collectors/python/repositories/` | All DB repository classes |
| `prompts/*.md` | LLM system prompts (editable without code changes) |
| `parsers/` | Standalone Python parsers called from bash |
| `db/migrations/` | Schema migrations (001-008) |
| `konflux-mcp-server/` | MCP server for external AI agent access |
| `.env` | Local secrets (not committed) |
