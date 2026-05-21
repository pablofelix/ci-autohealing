# CI Auto-Healing

Monitors Konflux CI/CD pipeline failures, diagnoses root causes with AI, generates fixes, and manages Jira communication — all from the command line.

---

## How it works

```
Konflux pipeline fails
        │
        ▼  every 20 min (cron)
┌─────────────────────────────────────────────────────────────────┐
│  collect-comprehensive.sh                                       │
│  Step 1  collect_comprehensive.py   → build_failures           │
│  Step 2  sync_component_status.py   → mark resolved / passed   │
│  Step 2.5 verify_fixes.py          → check PR merged + green   │
│  Step 3  check_sync_status.py       → sync cache               │
│  Step 4  check_conforma_status.py   → conforma pass/fail       │
│  Step 5  collect_conforma.py        → violation details        │
│  Step 6  collect_commit_context.py  → diff + Dockerfile        │
│  Step 7  analyze_failures.py        → AI root cause            │
│  Step 7.5 auto_fix.py             → autonomous PRs (opt-in)   │
│  Step 8  collect_doc_context.py     → enrich error patterns    │
│  Step 9  poll_jira_comments.py      → Jira reply drafts        │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼  you
┌───────────────────┐
│   ic CLI          │  ic get alerts → ic 1 → ic fix 1
└───────────────────┘
        │
        ├──► PR      branch → commit → GitHub PR → verify next cron
        ├──► Jira    generate ticket → edit → POST (--execute)
        └──► Slack   generate message → clipboard → paste
```

Steps 6 (commit context), 7 (AI analysis), 7.5 (autonomous), and 9 (Jira polling) are optional — they require additional credentials and are skipped gracefully if not configured.

---

## Setup

### 1. Start the database

```bash
task db:start
```

### 2. Apply migrations

```bash
task db:migrate
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill in values. The file is sourced by `ic` automatically.

```bash
# Always required
NAMESPACE=my-tenant
APPLICATION_NAME=my-app

# Required for AI analysis (choose one)
LLM_PROVIDER=vertex_ai
ANTHROPIC_VERTEX_PROJECT_ID=my-gcp-project    # Vertex AI (uses GCP ADC)
# or
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...                  # Direct API

# Required for PR creation and commit context
GITHUB_TOKEN=ghp_...

# Required for Jira ticket creation and comment monitoring
JIRA_EMAIL=you@example.com
JIRA_TOKEN=...
JIRA_URL=https://your-jira.example.com
JIRA_PROJECT=MYPROJECT
```

Full variable reference is at the bottom of this file.

### 4. Install the cron job

```bash
./cron/install-cron.sh   # every 20 minutes
```

### 5. Verify

```bash
ic stats
```

---

## Daily operator workflow

### Morning triage

```bash
ic get alerts            # unified view: build failures + conforma violations
ic get alerts --all      # same, across all application versions
```

The alert view shows:
- Build failures: component name, consecutive failure count, last failed date, AI category
- Conforma violations: component, scenario, violation/warning counts, linked Jira key

```bash
ic 1                     # inspect failure #1 in detail
ic describe component odh-mlmd-grpc-server-v3-4  # by name
ic why odh-mlmd-grpc-server-v3-4                 # AI root cause only
```

### Conforma standup report

```bash
ic conforma report           # full table: all versions
ic conforma report 3.4       # filter to acme-v2-0
ic conforma report 3.4 3.5   # multiple versions side by side
ic conforma report --summary # summary row only
```

### Check what was resolved

```bash
ic stats                 # overall counts
ic stats daily           # failures by day
ic resolved              # recently resolved components
ic working               # components where last build passed
```

---

## Fixing a failure

`ic fix` is the main triage command. It shows the AI analysis and lets you choose what to do.

```bash
ic fix 1                              # by number from ic get alerts
ic fix odh-mlmd-grpc-server-v3-4      # by name
```

The interactive menu shows:

```
[1] Create GitHub PR (auto-fix)
[2] Create Jira ticket
[3] Generate Slack message
[q] Quit
```

### Option 1: GitHub PR

Available when AI analysis says `can_auto_fix=true`. The fixer generates a branch, commits the change, and shows a diff.

```
→ dry-run by default. Review the diff, then re-run with --execute to push.
```

```bash
ic fix 1 --execute       # push branch and open PR
```

PR branches follow the format `ci-autohealing/conforma/<component>/<id>` or `ci-autohealing/build/<component>/<id>`. After the next cron run, `verify_fixes.py` checks if the PR merged and the subsequent build passed, and marks the failure resolved.

Track all fix attempts:

```bash
ic get fixes             # last 30 days
ic get fixes --all       # all time
```

### Option 2: Jira ticket

The Jira export generates a fully-formatted ticket description with failure details, AI analysis, reproduction steps, and links.

```bash
# Preview only (no API call)
ic export 1 jira
ic export odh-mlmd-grpc-server-v3-4 jira

# Copy to clipboard
ic export 1 jira --clipboard

# For conforma violations specifically (also supports --execute to POST)
ic jira create conforma odh-mlmd-grpc-server-v3-4             # dry-run
ic jira create conforma odh-mlmd-grpc-server-v3-4 --execute   # POST to Jira
```

Link an existing Jira key to a failure in the DB:

```bash
ic jira link odh-mlmd-grpc-server-v3-4 PROJECT-12345
```

View all linked tickets with live status:

```bash
ic get jira
ic get jira PROJECT-12345   # single ticket details
ic describe jira PROJECT-12345
```

### Option 3: Slack message

Generates a polite mrkdwn message asking the component team for help. Includes the Jira link, failure summary, and Konflux link.

```bash
ic export 1 slack
ic export 1 slack --jira PROJECT-12345    # include Jira link
ic export 1 slack --clipboard              # copy instead of print

# Conforma-specific
ic export conforma odh-mlmd-grpc-server-v3-4 slack --jira PROJECT-99999
```

Paste the output directly into the team's Slack channel. The message tags the correct team handle from the RHOAI component data YAML.

---

## Jira comment monitoring

When a Jira ticket linked to a failure receives new comments, the cron job (step 9) drafts an AI reply and fires a desktop notification.

### View the inbox

```bash
ic jira inbox            # unreviewed items with original comment + AI draft
```

### Refine a draft

```bash
ic jira inbox refine 3   # interactive refinement loop for item #3
```

Give feedback in the prompt (e.g. "make it shorter", "add the PR link", "be more specific about the fix"). The LLM revises the draft. Repeat until satisfied.

When you exit, if you made revisions the system proposes an addition to the reply-drafting prompt (`prompts/jira_reply_drafter.md`) based on what it learned about your style. You approve or skip.

### Mark as done

After posting the reply in Jira manually:

```bash
ic jira inbox done 3     # mark item 3 reviewed
```

---

## AI management

AI analysis runs automatically in cron step 7. These commands let you inspect and trigger it manually.

```bash
ic ai status                    # pending / analyzed / skipped counts
ic ai stats                     # breakdown by category and date

ic ai analyze odh-mlmd-grpc-server-v3-4   # analyze one component now
ic ai analyze 1                            # by number
ic ai analyze --all                        # all pending (slow)

ic analyze odh-mlmd-grpc-server-v3-4      # alias
ic why odh-mlmd-grpc-server-v3-4          # just the AI summary
```

### Error pattern library

The system builds up institutional memory in an `error_patterns` table. Patterns accumulate from repeated analyses of the same failure category and are injected into the prompt on re-analysis.

```bash
ic patterns list              # known patterns with frequency
ic patterns show dependency_issue
```

---

## Autonomous mode (opt-in)

When `AUTONOMOUS_MODE=true`, cron step 7.5 automatically creates PRs for high-confidence conforma violations without human approval.

**Safety gates** — all must pass before a PR is created:
- `can_auto_fix = TRUE` and `requires_human_review = FALSE` (from AI analysis)
- `confidence_score >= 0.95`
- `ai_fix_attempted = FALSE` (not already tried)
- No existing `ci-autohealing/conforma/<component>/<id>` branch on GitHub

**Rate limit:** 3 PRs per cron run (configurable with `AUTO_FIX_MAX_PER_RUN`).

**How to enable** (only after manual validation):

```bash
# 1. Run ic fix manually on at least 5 conforma violations across two versions
# 2. Review the generated PRs — confirm the diffs are correct
# 3. Enable in .env:
AUTONOMOUS_MODE=true
```

To disable: remove `AUTONOMOUS_MODE=true` or set it to `false`.

The `attempted_by` column in `resolution_attempts` records `'autonomous'` vs `'ic-fix'` so you can see which PRs came from which path.

---

## Export formats

All export subcommands accept either a number (from `ic get alerts`) or a component name.

```bash
ic export 1 jira         # Jira ticket description (ADF format)
ic export 1 slack        # Slack mrkdwn message
ic export 1 markdown     # GitHub-flavored Markdown
ic export 1 json         # Raw JSON (for scripting)

ic export 1 --clipboard  # copy output to clipboard (any format)
```

---

## Full command reference

### Inspection

```bash
ic get alerts                              # unified: build + conforma
ic get alerts --all                        # all application versions
ic get alerts --date 2026-05-10            # failures on a specific date
ic get alerts --from 2026-05-01 --to 2026-05-10   # changes in a range
ic get components                          # components with active build failures
ic get conforma                            # conforma violation summary
ic get exceptions                          # policy exceptions expiring soon
ic get pipelineruns [--limit N]            # recent PipelineRun failures
ic get pipelinerun <name>                  # single PipelineRun details
ic get apps                                # available application versions
ic get failures [--limit N]                # raw failure list
```

### Detail views

```bash
ic <N>                                     # shortcut: Nth component from alerts
ic describe component <name> [--log]       # full build failure + logs
ic describe conforma <name>                # conforma violation details
ic describe pipelinerun <name>             # PipelineRun details + logs
ic history <component>                     # full build history
```

### Fixing

```bash
ic fix <N|name>                            # interactive: AI → action menu
ic fix <N|name> --execute                  # skip confirmation, push PR
ic get fixes                               # PR fix attempts (last 30 days)
ic get fixes --all                         # all time
```

### AI

```bash
ic ai analyze <N|name>                     # analyze one failure now
ic ai analyze --all                        # all pending
ic ai status                               # pending / analyzed / skipped
ic ai stats                                # by category and date
ic why <name>                              # AI root cause (quick view)
ic analyze <name>                          # full AI analysis (alias)
ic errors <name>                           # extract raw error messages
ic triage                                  # components where last build = failed
```

### Conforma

```bash
ic get conforma                            # violation summary
ic conforma report [version...]            # daily standup table
ic conforma report 3.4                     # filter by version
ic conforma report 3.4 3.5                 # multiple versions
ic conforma report --summary               # summary row only
ic describe conforma <name>                # violation details
ic conforma history <name>                 # historical violations
ic conforma csv                            # dump violations as CSV
ic get exceptions                          # expiring/expired policy exceptions
```

### Jira

```bash
ic get jira [key]                          # linked tickets with live status
ic describe jira <key>                     # full ticket detail
ic jira link <component> <key>             # link existing ticket to component
ic jira create conforma <name>             # dry-run: preview ticket
ic jira create conforma <name> --execute   # POST ticket to Jira
ic jira inbox                              # unreviewed comment drafts
ic jira inbox refine <N>                   # refine draft N interactively
ic jira inbox done <N>                     # mark item reviewed
```

### Export

```bash
ic export <N|name> jira
ic export <N|name> slack [--jira KEY]
ic export <N|name> markdown
ic export <N|name> json
ic export <N|name> <format> --clipboard
```

### Stats and history

```bash
ic stats                                   # overall counts
ic stats daily                             # by day
ic stats resolved                          # resolved failures
ic resolved                                # recently resolved components
ic working                                 # components where last build passed
ic patterns list                           # error pattern library
ic patterns show <name>                    # full pattern + doc excerpt
```

### Configuration

```bash
ic config                                  # show current config
ic config set-app acme-v2-1-ea-1          # switch APPLICATION_NAME
ic get apps                                # available versions (alias: ic apps)
ic db status                               # check DB connection
ic db query "SELECT ..."                   # run custom SQL
```

---

## Environment variables

```bash
# Database
DB_CONTAINER=ci-autohealing-db            # Docker container name
DB_NAME=konflux_monitoring
DB_USER=postgres
PGPASSWORD=admin

# Kubernetes / Konflux
NAMESPACE=my-tenant
APPLICATION_NAME=my-app
KNOWN_APPLICATIONS=my-app-v1 my-app-v2       # space-separated; used by --all

# AI analysis
LLM_PROVIDER=vertex_ai                    # or: anthropic
ANTHROPIC_VERTEX_PROJECT_ID=...           # Vertex AI (GCP ADC)
ANTHROPIC_API_KEY=...                     # Direct Anthropic API

# Observability
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...

# GitHub (commit context + PR creation)
GITHUB_TOKEN=ghp_...

# Jira
JIRA_EMAIL=you@example.com
JIRA_TOKEN=...
JIRA_URL=https://your-jira.example.com
JIRA_PROJECT=MYPROJECT

# Autonomous mode
AUTONOMOUS_MODE=false                     # set true only after manual validation
AUTO_FIX_MAX_PER_RUN=3                    # max PRs per cron run
AUTO_FIX_MIN_CONFIDENCE=0.95              # min AI confidence score

# Component data (team handle lookup)
COMPONENT_DATA_URL=                       # URL to component-data YAML
COMPONENT_DATA_CACHE=/tmp/component-data.yaml
COMPONENT_DATA_TTL=86400                  # seconds (24h)

# KubeArchive / Konflux UI
KUBEARCHIVE_URL=                          # KubeArchive API endpoint
KONFLUX_UI_BASE=                          # Konflux UI base URL
```

---

## Key files

| Path | Purpose |
|------|---------|
| `ic` | Main CLI |
| `ic-config.sh` | Configuration defaults (sourced by `ic`) |
| `cron/collect-comprehensive.sh` | Cron orchestrator (9 steps) |
| `src/analyzers/build_failure_analyzer.py` | Build failure LLM analysis |
| `src/analyzers/conforma_analyzer.py` | Conforma violation LLM analysis |
| `src/fixers/fix_generator.py` | PR and Jira fix generator |
| `src/fixers/auto_fix.py` | Autonomous cron runner (step 7.5) |
| `src/fixers/verify_fixes.py` | Fix verification loop |
| `src/poll_jira_comments.py` | Jira comment polling + draft generation |
| `src/jira_refine.py` | Interactive draft refinement |
| `src/clients/jira_client.py` | Jira REST API (Basic auth) |
| `src/clients/github_client.py` | GitHub Contents API |
| `src/clients/llm_provider.py` | LLM provider abstraction (Vertex / Anthropic) |
| `src/repositories/` | All DB repository classes |
| `src/tests/` | Test suite |
| `prompts/*.md` | LLM system prompts (edit without code changes) |
| `parsers/` | Standalone Python parsers called from bash |
| `db/migrations/` | Schema migrations (001–009) |
| `docs/adr/` | Architecture decision records |
| `konflux-mcp-server/` | MCP server for external AI agents |
| `.env` | Local secrets (not committed) |
