# CI AutoHealing Architecture

## Overview

CI AutoHealing monitors Konflux CI/CD pipelines, detects build failures and policy violations, uses LLMs to diagnose root causes, and autonomously generates fix PRs — all orchestrated through a CLI, MCP server, and REST API.

**What it does:**
- Monitors Tekton PipelineRun failures across multiple components via event-driven K8s watch streams and cron-based batch collection
- Fetches comprehensive logs, commit diffs, Dockerfiles, and Tekton configs
- Tracks Conforma (Enterprise Contract) policy violations
- Uses AI to classify failures and recommend fixes
- Generates fix PRs autonomously for high-confidence failures
- Stores everything in PostgreSQL for historical analysis
- Powers the `ic` CLI, MCP server (40+ tools), and REST API

**Data sources:**
- **KubeArchive** — Archived Kubernetes resources (primary)
- **Live OpenShift cluster** — Current PipelineRuns and Components
- **Tekton Results API** — Alternative historical data source
- **GitHub API** — Commit diffs, PRs, Dockerfiles, Tekton configs

---

## Architecture Layers

```
┌──────────────────────────────────────────────────────────────────┐
│  External Agents (Claude Code, GitHub Copilot, etc.)            │
│  Access via MCP Protocol                                         │
└────────────────────────────┬─────────────────────────────────────┘
                             │ MCP (stdio / SSE)
┌────────────────────────────▼─────────────────────────────────────┐
│  Interfaces                                                      │
│  CLI (ic) · MCP Server (40+ tools) · REST API (FastAPI)         │
│  All share the same repository layer — zero duplication          │
└────────────────────────────┬─────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│  Fixers                                                          │
│  FixGenerator · JiraManager · VerifyFix                          │
│  Autonomous PR creation with safety gates                        │
└────────────────────────────┬─────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│  Analyzers (AI)                                                  │
│  BuildFailureAnalyzer · ConformaAnalyzer · ReleaseAnalyzer      │
│  LLM-powered root cause analysis with confidence scoring         │
└────────────────────────────┬─────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│  Watch Daemon (event-driven)                                     │
│  WatchDaemon · BuildEventHandler · ComponentEventHandler        │
│  K8s watch streams, UID dedup, auto-reconnect, Jira polling     │
├──────────────────────────────────────────────────────────────────┤
│  Collectors (batch orchestration)                                │
│  BuildFailureCollector · ConformaViolationCollector              │
│  StatusSynchronizer · CommitContextCollector                     │
└────────┬───────────────────────────────┬─────────────────────────┘
         │                               │
┌────────▼────────────┐         ┌────────▼────────────┐
│  Clients (I/O)      │         │  Repositories (SQL) │
│  KubeArchive        │         │  BuildFailure       │
│  Kubernetes         │         │  AIAnalysis         │
│  TektonResults      │         │  Conforma           │
│  UnifiedPipeline    │         │  ComponentHealth    │
│  GitHub · GitLab    │         │  ErrorPatterns      │
│  Jira · Quay        │         │  SyncStatus         │
│  LLMProvider ABC    │         └─────────────────────┘
│  LangfuseTracker    │
└────────┬────────────┘
         │
┌────────▼────────────┐
│  Parsers (pure)     │
│  tekton_parsers.py  │
│  extract_*, classify_* │
└─────────────────────┘
```

### Layer 1: Parsers (Pure Functions)

**Module:** `tekton_parsers.py`

Pure functions that transform raw Kubernetes JSON into structured data. Zero I/O, no side effects, highly testable.

- `extract_pipelinerun_metadata()` — Extract commit SHA, URLs, author from PR JSON
- `extract_error_from_logs()` — Parse logs for error patterns
- `classify_build_status()` — Map Tekton status to BuildStatus enum

### Layer 2: Clients (I/O Adapters)

**Directory:** `clients/`

Adapters that fetch data from external systems behind a common interface.

**Key pattern:** `PipelineRunSource` ABC defines the contract:
- `get_pipelinerun(name)` → Dict
- `get_taskrun(name)` → Dict
- `get_pod_logs(pod, container)` → str

**Pipeline clients:**
- **KubeArchiveClient** — HTTP queries to archived K8s resources
- **KubernetesClient** — Live cluster via kubernetes Python API
- **TektonResultsClient** — Tekton Results API
- **UnifiedPipelineClient** — Tries all sources with fallback

**Integration clients:**
- **GitHubClient** — Commit diffs, PRs, file content, PR creation
- **GitLabClient** — Policy exception files, MRs
- **JiraClient** — Ticket creation, comment polling, AI reply drafts
- **QuayClient** — Container image SARIF vulnerability scanning

**AI clients:**
- **LLMProvider ABC** — Provider-independent LLM interface
- **VertexAIProvider** — Claude on Google Cloud Vertex AI
- **AnthropicDirectProvider** — Direct Anthropic API
- **LangfuseTracker** — LLM observability and cost tracking

### Layer 3: Repositories (Database)

**Directory:** `repositories/`

SQL operations on PostgreSQL tables. Parameterized queries, no business logic.

**Repositories:**
- **BuildFailureRepository** — `build_failures` table
- **AIAnalysisRepository** — `ai_analysis` table
- **ConformaRepository** — `conforma_results` table
- **ComponentHealthRepository** — `component_health` table
- **ErrorPatternRepository** — `error_patterns` table
- **SyncStatusRepository** — `sync_status` cache table
- **DatabaseSkillRegistry** — `skill_sources` + `skills` tables (dual-mode: PostgreSQL in cluster, JSON file locally)

### Layer 4a: Watch Daemon (Event-Driven)

**Directory:** `watcher/`

Real-time CI monitoring via Kubernetes watch streams. Detects build failures and policy violations as they happen, eliminating polling delay.

- **WatchDaemon** — Orchestrates multiple watch streams per application, manages lifecycle and reconnection
- **BuildEventHandler** — Processes PipelineRun events, detects terminal states (success/failure), UID-based deduplication, optional AI auto-analysis
- **ComponentEventHandler** — Tracks Component resource changes (added/modified/deleted)

**Key patterns:**
- Watch streams reconnect automatically on 410 Gone errors (resource version expired)
- Per-watcher enable/disable via `WatcherConfig.disabled` and `WATCH_DISABLE` env var
- Available watchers: `builds`, `tests`, `conforma`, `jira`, `components`
- Periodic reconciliation loop catches events missed during reconnection windows
- Jira comment polling runs as a background task within the daemon

### Layer 4b: Collectors (Batch Orchestration)

**Directory:** `collectors/`

High-level workflows that coordinate clients + repositories. Run via cron every 20 minutes.

- **BuildFailureCollector** — Discover failing components, fetch logs, store in DB
- **ConformaViolationCollector** — Collect policy violations and detailed reports
- **StatusSynchronizer** — Mark resolved failures, record successes
- **CommitContextCollector** — Fetch diffs, Dockerfiles, Tekton configs from GitHub

### Layer 5: Analyzers (AI)

**Directory:** `analyzers/`

LLM-powered analysis workflows with confidence scoring.

- **BuildFailureAnalyzer** — Analyze build failures (dependency issues, config errors, etc.)
- **ConformaAnalyzer** — Analyze policy violations (hermetic builds, signing, package sources)
- **ReleaseAnalyzer** — Release readiness assessment

**Output validation** uses Pydantic models:
```python
class AnalysisResult(BaseModel):
    root_cause: str = Field(..., min_length=10)
    failure_category: Literal['dependency_issue', 'build_error', ...]
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    recommended_fix: str
    can_auto_fix: bool
    requires_human_review: bool
```

### Layer 6: Fixers

**Directory:** `fixers/`

Autonomous fix generation with safety gates.

- **FixGenerator** — Generate code fixes and create GitHub PRs
- **JiraManager** — Create tickets, poll comments, draft AI replies
- **VerifyFix** — Check if fix PRs merged and builds passed

**Safety gates** (all must pass for autonomous mode):
- AI confidence >= 0.95
- `can_auto_fix = true`
- `requires_human_review = false`
- No prior fix attempts
- No existing fix branch
- Rate-limited to 3 PRs per cron run

### Layer 7: Interfaces

**Three interfaces, one data layer:**

**CLI (`ic`)** — Bash wrapper delegating to Python Click CLI. Context-aware via `.env`.

**MCP Server** (FastMCP, 40+ tools):
- Transports: stdio (Claude Code) and SSE (remote agents)
- Each tool accepts `application` parameter for multi-version queries
- Includes skill registry tools: `list_skills`, `get_skill_info`, `list_skill_sources`
- Run: `python -m serve --mcp`

**REST API** (FastAPI):
- OpenAPI docs at `/docs`
- API key auth via `IC_API_KEY` env var
- Skill endpoints: `GET /api/v1/skills`, `GET /api/v1/skills/sources`, `GET /api/v1/skills/{name}`
- Run: `python -m serve --api`

**Combined:** `python -m serve --api --mcp-sse` runs both in one process.

### Skill Registry

**Directory:** `skills/`

External skill management — load, register, and query skills from Git repos.

- **SkillRegistry** (`registry.py`) — JSON file-backed registry for local dev
- **DatabaseSkillRegistry** (`db_registry.py`) — PostgreSQL-backed registry for cluster deployment
- **`get_registry()`** factory — Returns DB registry if PostgreSQL is available, JSON file fallback otherwise
- **Loader** (`loader.py`) — Clone repos, discover `SKILL.md` files, parse frontmatter
- **Known Sources** (`known_sources.py`) — Shorthand catalog for well-known repos
- **SkillValidator** (`validator.py`) — Static security scanning (secrets, destructive ops, exfiltration, unsafe shell patterns). Runs automatically on `ic skills add`, blocking critical findings. Also available standalone via `ic skills validate`
- **`check_prerequisites()`** (`validator.py`) — Runtime check for required tools (`shutil.which`) and env vars. Powers `ic skills doctor`

**Dual-mode storage:** In a cluster, skills are stored in PostgreSQL (`skill_sources` + `skills` tables with GIN-indexed tag arrays). Locally, they persist in `~/.ic/skills.json`. The CLI writes (git clone + DB insert), while MCP/API provide read-only access.

**Tables:** `skill_sources` (name, url, commit), `skills` (qualified_name, source, tags[], metadata JSONB)

---

## Key Patterns

### Fallback Source Chain

UnifiedPipelineClient tries sources in priority order:

```
KubeArchive (fastest, most reliable)
    ↓ if 404
Live Cluster (current data)
    ↓ if not found
Tekton Results API (alternative historical source)
```

### Query Deduplication by UID

PipelineRuns appear in both KubeArchive and live cluster. `query_pipelineruns()` deduplicates by UID, with live cluster data taking precedence.

### Dependency Inversion

All collectors accept dependencies via constructor with defaults:

```python
class BuildFailureCollector:
    def __init__(self, config, db=None, build_repo=None,
                 kubearchive=None, k8s=None, unified=None):
        self.build_repo = build_repo or BuildFailureRepository(db)
        self.kubearchive = kubearchive or KubeArchiveClient(...)
```

Production code passes config only; tests inject mocks directly.

### Error Pattern Learning

The `error_patterns` table accumulates institutional knowledge from repeated analyses. When analyzing new failures, the system matches against known patterns to improve accuracy over time.

---

## Data Flow

### Watch Daemon (Real-Time)

```
WatchDaemon.start()
├─ For each application:
│   ├─ watch-build: K8s watch on PipelineRuns (type=build)
│   │   └─ BuildEventHandler: terminal state → upsert_failure / record_success
│   ├─ watch-test: K8s watch on PipelineRuns (type=test)
│   │   └─ BuildEventHandler: same handler, different pipeline type
│   └─ watch-components: K8s watch on Component resources
│       └─ ComponentEventHandler: log additions/modifications/deletions
├─ reconciliation: periodic full-scan to catch missed events
└─ jira-poller: poll for new Jira comments, draft AI replies
```

### Cron Pipeline (every 20 minutes)

```
collect_comprehensive.py     → Scan for new build failures
sync_component_status.py     → Mark resolved / passed components
verify_fixes.py              → Check if fix PRs merged and builds passed
check_conforma_status.py     → Update Conforma pass/fail status
collect_conforma.py          → Fetch violation details
collect_commit_context.py    → Gather diffs, Dockerfiles, Tekton configs
analyze_failures.py          → AI root cause analysis
auto_fix.py                  → Generate fix PRs (autonomous, opt-in)
poll_jira_comments.py        → Draft replies to Jira comments
```

### Build Failure Collection

```
1. Discover failing components from cluster
   ├─ KubeArchive: archived PipelineRuns
   └─ Live cluster: current PipelineRuns
2. Filter to push builds with status='False'
3. For each failing component:
   ├─ get_component_metadata() → repo URL, branch
   ├─ UnifiedPipelineClient.get_logs_complete() → comprehensive logs
   ├─ extract_error_from_logs() → error message, type
   └─ BuildFailureRepository.upsert_failure() → store in DB
```

---

## Testing

**278 tests**, all passing. Run with `task test`.

- **Parser tests** — Pure function tests with mock Kubernetes JSON, no I/O
- **Client tests** — Mock HTTP/API responses, verify retry and error handling
- **Collector tests** — Inject mocks for all dependencies, test orchestration logic
- **CLI tests** — Test command output formatting and argument parsing
- **Repository tests** — Test SQL query construction with mock cursors

---

## Configuration

All configuration via environment variables in `.env`. See `.env.example` for the full list.

Core: `NAMESPACE`, `APPLICATION_NAME`
Database: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
AI: `LLM_PROVIDER`, `ANTHROPIC_API_KEY`
Integrations: `GITHUB_TOKEN`, `JIRA_URL`, `JIRA_TOKEN`, `KUBEARCHIVE_URL`
Skills: `IC_SKILLS_DIR` (optional, default `~/.ic`)
