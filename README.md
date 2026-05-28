# CI AutoHealing

**Autonomous Multi-Agent AI for CI Observability & Self-Remediation**

CI AutoHealing monitors [Konflux](https://konflux-ci.dev/) CI/CD pipelines, detects build failures and policy violations, uses LLMs to diagnose root causes, and autonomously generates fix PRs — all orchestrated through a kubectl-style CLI, an MCP server for AI agents, and a REST API.

---

## Key Features

- **Continuous Monitoring** — Event-driven Kubernetes watch daemon for real-time CI monitoring, plus cron-driven pipeline scans every 20 minutes. Tracks build failures, Conforma (Enterprise Contract) policy violations, and release readiness across multiple application versions.
- **AI Root Cause Analysis** — LLM-powered failure classification with confidence scoring, actionable fix recommendations, and automatic error pattern learning.
- **Autonomous Fix PRs** — High-confidence failures get automatically fixed via GitHub PRs, with safety gates (confidence >= 0.95, no prior attempts, branch deduplication) and verification on the next cron cycle.
- **CVE Scanning** — Parallel SARIF vulnerability scanning across all container images in a snapshot, surfacing critical and high CVEs before release.
- **Jira Integration** — Create tickets, monitor comments, draft AI replies, and track resolution — all from the CLI.
- **MCP Server** — 40+ tools accessible by Claude Code, GitHub Copilot, or any MCP-compatible AI agent for interactive triage sessions.
- **Pattern Learning** — An `error_patterns` table accumulates institutional knowledge from repeated analyses, improving diagnosis accuracy over time.
- **Three Interfaces, One Data Layer** — CLI, MCP server, and REST API all share the same repositories and PostgreSQL database.

---

## Architecture

```
                    ┌─────────────────────────────────┐
                    │        AI Agents                │
                    │  Claude Code · Copilot · Custom │
                    └──────────────┬──────────────────┘
                                   │ MCP protocol
                                   │
  ┌──────────┐    ┌────────────────▼────────────────┐    ┌───────────┐
  │  ic CLI  │───▶│         Shared Data Layer       │◀───│ REST API  │
  └──────────┘    │  Repositories · Analyzers ·     │    │           │
                  │  Collectors · Fixers · Clients   │   └───────────┘
                  └────────────────┬────────────────┘
                                   │
                  ┌────────────────▼────────────────┐
                  │          PostgreSQL             │
                  │  build_failures · conforma ·    │
                  │  ai_analysis · error_patterns · │
                  │  component_health · releases    │
                  └─────────────────────────────────┘
                                   ▲
          ┌─────────────────────┐  ┌──────────────────────┐
          │  Watch Daemon       │  │  Cron Pipeline       │
          │  K8s event streams  │  │  Collect → Analyze   │
          │  real-time detect   │  │  → Fix → Verify      │
          └──────────┬──────────┘  └──────────┬───────────┘
                     │                        │
                  ┌──┴────────────────────────┴──┐
                  │       Konflux Platform       │
                  │  Tekton · KubeArchive · Quay │
                  │  GitHub · GitLab · Jira      │
                  └──────────────────────────────┘
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Docker or Podman
- [Task](https://taskfile.dev/) — install: `go install github.com/go-task/task/v3/cmd/task@latest`

### Setup

```bash
cp .env.example .env              # edit with your namespace and app name
task up                           # starts PostgreSQL + server, applies schema
./ic get apps                     # verify — should list your application
```

Minimum `.env` config:

```bash
NAMESPACE=your-tenant
APPLICATION_NAME=your-app-name
```

Everything else (AI, Jira, GitHub) is optional and will be skipped gracefully if not configured.

---

## Try It Without a Cluster

The interactive demo uses pre-recorded output with synthetic data — no database, cluster, or credentials needed:

```bash
./demo.sh              # press Enter to advance through each section
./demo.sh --auto       # auto-play with timed delays (good for recordings)
```

Covers: alert dashboard, failure inspection, AI analysis, Conforma violations, Jira/Slack export, release readiness, and more.

---

## Daily Workflow

### Triage

```bash
ic get alerts                              # all current failures and violations
ic describe failure my-component-v3-4      # inspect a build failure in detail
ic describe conforma my-component-v3-4     # inspect a conforma violation
```

### Fix

```bash
ic fix my-component-v3-4                   # interactive: AI analysis → action menu
ic fix my-component-v3-4 --execute         # push fix PR to GitHub
ic get fixes                               # track all fix attempts
```

### Report

```bash
ic export my-component-v3-4 jira           # generate Jira ticket
ic export my-component-v3-4 slack          # generate Slack message
ic conforma report                         # Conforma standup table
```

---

## Claude Code Integration

### MCP Server

The MCP server exposes 40+ tools for AI agents to query failures, run analysis, and generate exports programmatically.

```bash
task mcp:setup                # generate .mcp.json for Claude Code
task serve:mcp                # run MCP server (stdio)
task serve                    # run REST API + MCP SSE on port 8000
```

### Slash Commands

The project includes custom Claude Code slash commands for guided workflows. Open the project with `claude` and use:

| Command | Purpose |
|---------|---------|
| `/triage [component]` | Investigate a failure — auto-routes to build or Conforma |
| `/triage-build <component>` | Deep-dive into build failure: logs, diffs, fix options |
| `/triage-conforma <component>` | Policy violation analysis: rules, exceptions, remediation |
| `/demo` | Interactive guided tour of all ic features |

---

## How It Works

### Watch Daemon (Real-Time)

The watch daemon uses Kubernetes watch streams to detect build failures and policy violations as they happen — no polling delay. It monitors PipelineRuns, test runs, and Component resources across multiple application versions simultaneously.

```bash
ic watch start                # start the watch daemon
ic config watch list          # show watched applications and watcher status
ic config watch add my-app    # add an application to the watch list
ic config watch disable jira  # disable specific watchers (builds, tests, conforma, jira, components)
```

Features: per-application watch streams, automatic reconnection on 410 Gone errors, UID-based deduplication, optional AI auto-analysis on new failures, periodic reconciliation, and Jira comment polling.

### Cron Pipeline (Batch)

Every 20 minutes, the cron pipeline runs:

| Step | Script | What it does |
|------|--------|-------------|
| 1 | `collect_comprehensive.py` | Scan for new build failures |
| 2 | `sync_component_status.py` | Mark resolved / passed components |
| 3 | `verify_fixes.py` | Check if fix PRs merged and builds passed |
| 4 | `check_conforma_status.py` | Update Conforma pass/fail status |
| 5 | `collect_conforma.py` | Fetch violation details |
| 6 | `collect_commit_context.py` | Gather diffs, Dockerfiles, Tekton configs |
| 7 | `analyze_failures.py` | AI root cause analysis |
| 8 | `auto_fix.py` | Generate fix PRs (autonomous, opt-in) |
| 9 | `poll_jira_comments.py` | Draft replies to Jira comments |

Steps 6-9 are optional — they require additional credentials and are skipped if not configured.

Install the cron job:

```bash
./cron/install-cron.sh
```

### Autonomous Mode

When `AUTONOMOUS_MODE=true`, the system automatically creates fix PRs for high-confidence failures.

Safety gates (all must pass): AI confidence >= 0.95, `can_auto_fix = true`, `requires_human_review = false`, no prior fix attempts, no existing fix branch. Rate-limited to 3 PRs per cron run.

Enable only after manual validation of at least 5 fixes.

---

## Taskfile Commands

```bash
task test              # run 278 tests
task lint              # ruff linter
task check             # lint + tests

task db:start          # start PostgreSQL
task db:migrate        # apply migrations
task db:psql           # open psql shell

task serve             # API + MCP server (port 8000)
task serve:mcp         # MCP stdio (for Claude Code)
task mcp:setup         # generate .mcp.json

task up                # docker compose: server + db
task down              # stop containers
task deploy            # deploy to Kubernetes
task deps:check        # verify all dependencies
```

---

<details>
<summary><strong>Full CLI Reference</strong></summary>

### Inspection

```bash
ic get alerts                              # unified: build + conforma
ic get alerts --all                        # all application versions
ic get alerts --date 2026-05-10            # failures on a specific date
ic get components                          # components with active build failures
ic get conforma                            # conforma violation summary
ic get exceptions                          # policy exceptions expiring soon
ic get pipelineruns [--limit N]            # recent PipelineRun failures
ic get apps                                # available application versions
ic get vulnerabilities [--component X]     # CVE summary from SARIF scans
```

### Detail Views

```bash
ic <N>                                     # Nth component from alerts
ic describe component <name> [--log]       # full build failure + logs
ic describe conforma <name>                # conforma violation details
ic history <component>                     # full build history
```

### Fixing

```bash
ic fix <N|name>                            # interactive: AI → action menu
ic fix <N|name> --execute                  # push PR directly
ic get fixes                               # PR fix attempts (last 30 days)
```

### AI

```bash
ic ai analyze <N|name>                     # analyze one failure
ic ai analyze --all                        # all pending
ic ai status                               # pending / analyzed / skipped
ic patterns list                           # error pattern library
ic patterns show <name>                    # pattern details
```

### Conforma

```bash
ic conforma report [version...]            # daily standup table
ic conforma report --summary               # summary row only
ic describe conforma <name>                # violation details
ic conforma history <name>                 # historical violations
ic get exceptions                          # expiring policy exceptions
```

### Jira

```bash
ic get jira [key]                          # linked tickets with status
ic jira link <component> <key>             # link ticket to component
ic jira create conforma <name>             # preview ticket
ic jira create conforma <name> --execute   # POST to Jira
ic jira inbox                              # unreviewed AI reply drafts
ic jira inbox refine <N>                   # refine draft interactively
```

### Export

```bash
ic export <N|name> jira                    # Jira ticket format
ic export <N|name> slack [--jira KEY]      # Slack mrkdwn
ic export <N|name> markdown                # GitHub Markdown
ic export <N|name> json                    # raw JSON
ic export <N|name> <format> --clipboard    # copy to clipboard
```

### Releases

```bash
ic get releases                            # recent Release CRs
ic release status                          # pipeline checklist
ic release readiness                       # go/no-go verdict
ic dashboard                               # operational metrics
```

### Watch Daemon

```bash
ic watch start                             # start the event-driven watch daemon
ic config watch list                       # show applications and watcher status
ic config watch add <app>                  # add application to watch list
ic config watch remove <app>               # remove application from watch list
ic config watch enable <watcher>           # enable a watcher (builds, tests, conforma, jira, components)
ic config watch disable <watcher>          # disable a watcher
```

### Configuration

```bash
ic config                                  # show current config
ic config set-app my-app-v2-1              # switch application
ic db status                               # check DB connection
```

</details>

<details>
<summary><strong>Configuration</strong></summary>

Copy `.env.example` to `.env`. The file is sourced by `ic` automatically.

| Variable | Required | Description |
|----------|----------|-------------|
| `NAMESPACE` | Yes | Kubernetes namespace for your tenant |
| `APPLICATION_NAME` | Yes | Konflux application name (e.g., `my-app-v2-1`) |
| `KNOWN_APPLICATIONS` | No | Space-separated list for `--all` queries |
| `DB_HOST` / `DB_PORT` | No | PostgreSQL connection (default: `localhost:5432`) |
| `GITHUB_TOKEN` | For fixes | GitHub token for commit context and PR creation |
| `LLM_PROVIDER` | For AI | `anthropic` or `vertex_ai` |
| `ANTHROPIC_API_KEY` | For AI | Direct Anthropic API key |
| `JIRA_URL` / `JIRA_TOKEN` | For Jira | Jira instance and API token |
| `KUBEARCHIVE_URL` | For logs | KubeArchive API endpoint |
| `KONFLUX_UI_BASE` | For links | Konflux UI base URL |
| `QUAY_ORG` | For CVEs | Quay.io organization for container images |
| `WATCH_APPLICATIONS` | For watch | Space-separated list of applications to watch |
| `WATCH_AUTO_ANALYZE` | No | `true` to auto-analyze new failures from watch daemon |
| `WATCH_DISABLE` | No | Space-separated list of watchers to disable |
| `AUTONOMOUS_MODE` | No | `true` to enable autonomous fix PRs (default: `false`) |

See `.env.example` for the full list with descriptions.

</details>

<details>
<summary><strong>Project Structure</strong></summary>

```
.
├── ic                        # Main CLI (bash wrapper → Python)
├── ic-config.sh              # Configuration defaults
├── Taskfile.yml              # Task runner: db, serve, test, deploy
├── docker-compose.yml        # Local dev: server + PostgreSQL
├── demo.sh                   # Standalone interactive demo
│
├── src/
│   ├── cli/                  # CLI implementation (Click)
│   ├── mcp_server/           # MCP server (FastMCP, 40+ tools)
│   ├── api/                  # REST API (FastAPI, OpenAPI at /docs)
│   ├── watcher/              # K8s watch daemon (event-driven monitoring)
│   ├── analyzers/            # LLM analyzers (build, conforma, release)
│   ├── fixers/               # Fix generators (PR, Jira, verification)
│   ├── collectors/           # Data collectors (failures, violations)
│   ├── clients/              # External APIs (GitHub, GitLab, Jira, K8s, Quay)
│   ├── repositories/         # Database repositories (SQL)
│   ├── proactive/            # Health monitoring and CVE warnings
│   ├── tests/                # Test suite (278 tests)
│   └── serve.py              # Unified server entry point
│
├── prompts/                  # LLM system prompts (editable without code changes)
├── db/migrations/            # PostgreSQL schema migrations (001–015)
├── cron/                     # Cron scripts for automated collection
├── k8s/                      # Kubernetes deployment manifests
├── .claude/commands/         # Claude Code slash commands (/triage, /release, etc.)
└── .env.example              # Configuration template
```

</details>

---

## License

MIT License — see [LICENSE](LICENSE) for details.
