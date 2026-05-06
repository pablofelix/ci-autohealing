# Code Style Guide

This is the governing style for the ci-autohealing codebase. Follow it for all new code and when refactoring existing code.

## Core Principle

**Data transformation is functions. Resource access is objects. They never mix.**

A function that parses a PipelineRun dict must not call an API or write to a database. A class that manages an HTTP session must not contain parsing logic. A collector method calls a pure function to transform, then calls a repository to persist — two separate calls, never interleaved.

## Functional vs OOP

Use **pure functions** for:
- Parsing and extracting data from dicts/JSON
- Validating and transforming inputs
- Building URLs, formatting output
- Any logic that takes data in and returns data out

Use **classes** for:
- Resources with a lifecycle (DB connections, HTTP sessions, auth tokens)
- Stateful abstractions that need setup/teardown
- Protocol implementations (API clients conforming to a shared interface)

Use **frozen dataclasses** for:
- Configuration (`@dataclass(frozen=True)`)
- Domain models and value objects
- Function inputs/outputs when a plain dict is too loose

Use **Pydantic models** (Python 3.7+) for:
- LLM output validation (catch hallucinations, enforce schemas)
- API request/response validation (MCP tools, external APIs)
- Complex validation rules (field validators, cross-field checks)

```python
from pydantic import BaseModel, Field, field_validator

class AnalysisResult(BaseModel):
    root_cause: str = Field(..., min_length=10)
    failure_category: Literal['dependency_issue', 'build_error', ...]
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    
    @field_validator('confidence_score')
    def round_confidence(cls, v):
        return round(v, 2)
```

Use frozen dataclasses for internal configuration. Use Pydantic for external boundary validation.

## Design Patterns

Use patterns where they solve a real problem, not for decoration.

**Adapter** — Each API client (`KubeArchiveClient`, `KubernetesClient`) adapts a different transport (HTTP, subprocess) to a common `PipelineRunSource` protocol. Parsing logic lives outside the adapter.

**Chain of Responsibility** — `UnifiedCollector` holds an ordered list of `PipelineRunSource` adapters. It tries each until one succeeds. Adding a new source means writing one adapter, not modifying the chain.

**Repository** — Database access goes through repository classes (`BuildFailureRepository`, `ConformaRepository`). Collectors never see SQL. Repositories use parameterized queries exclusively.

**Factory Method** — `Config.from_env()`, `Database.from_config()`. Use classmethods to build complex objects from external state (env vars, files). Keep `__init__` simple and deterministic.

**Protocol** — Define interfaces with `typing.Protocol` (or ABC for Python 3.6). Clients implement protocols; consumers depend on the protocol, not the concrete class.

Avoid: Singleton `__new__` (use module-level instances or factory functions), Builder (objects aren't complex enough), deep inheritance (prefer composition), Visitor, Abstract Factory.

## Type Annotations

Use type hints in Python 3.7+ code (analyzers, MCP server). Use type comments in Python 3.6.8 code (collectors).

**Python 3.7+ (analyzers, MCP):**
```python
from typing import List, Dict, Optional, Literal

def get_failure(component: str, application: str = "acme-v2-0") -> Optional[BuildFailureDetails]:
    """Get build failure details."""
    pass
```

**Python 3.6.8 (collectors):**
```python
def get_failure(component, application="acme-v2-0"):
    # type: (str, str) -> Optional[Dict[str, Any]]
    """Get build failure details."""
    pass
```

Run `mypy` on analyzers and MCP server code (not collectors — mypy requires Python 3.7+).

## Naming

```
modules:          snake_case.py          (tekton_parsers.py, not TektonParsers.py)
classes:          PascalCase             (KubeArchiveClient, BuildFailureRepository)
functions:        snake_case             (extract_error_messages, not extractErrorMessages)
constants:        UPPER_SNAKE_CASE       (MAX_LOG_SIZE, DEFAULT_NAMESPACE)
private:          _single_leading_underscore (_create_session, not __create_session)
booleans:         is_/has_/can_/should_  (is_resolved, has_logs, can_auto_fix)
factory methods:  from_*                 (from_env, from_file, from_pipelinerun)
```

Name things after what they *are*, not where they *go*. `tekton_parsers.py` not `utils.py`. `build_failure_repository.py` not `helpers.py`.

**Descriptive names over abbreviations.** Names should read as natural English for humans and be greppable for machines:
- `extract_taskrun_names` not `extract_trs` or `get_trs`
- `build_failure_repository.py` not `bf_repo.py`
- `discover_kubearchive_api_url` not `get_url`
- `is_pipelinerun_failed` not `check_pr`
- `conforma_violation_collector` not `conforma_coll`

A reader should understand what a function does from its name alone, without reading the docstring. A `grep` for any domain term should find all related code.

## Module Structure

```
collectors/python/
    config.py                  # Frozen dataclasses for configuration
    models.py                  # Domain types (dataclasses, enums)
    exceptions.py              # Exception hierarchy

    tekton_parsers.py          # Pure functions: parse PipelineRun/TaskRun dicts
    
    clients/                   # I/O adapters, one per data source
        pipeline_source.py     # PipelineRunSource ABC
        kubearchive.py         # KubeArchive REST API adapter
        kubernetes.py          # Live cluster via oc/subprocess adapter
        tekton_results.py      # Tekton Results API adapter
        unified.py             # Chain of Responsibility over sources
        llm_provider.py        # LLMProvider ABC + factory
        vertex_ai_provider.py  # Vertex AI adapter (Claude on GCP)
        langfuse_tracker.py    # Langfuse tracking wrapper
    
    repositories/              # Database access, parameterized queries only
        build_failure_repository.py   # BuildFailureRepository
        conforma_repository.py        # ConformaRepository
        sync_status_repository.py     # SyncStatusRepository
        ai_analysis_repository.py     # AIAnalysisRepository
        connection.py                 # Connection manager (context manager)

    collectors/                # Orchestration: compose pure functions + I/O
        build_failure_collector.py       # BuildFailureCollector
        conforma_violation_collector.py  # ConformaViolationCollector
        status_synchronizer.py           # StatusSynchronizer + cluster queries

    analyzers/                 # AI orchestration: LLM-powered analysis
        base.py                          # Shared Protocol + utilities (future)
        build_failure_analyzer.py        # BuildFailureAnalyzer
        conforma_analyzer.py             # ConformaAnalyzer

    # Entry-point shims (called by cron and ic tool)
    collect_comprehensive.py   # -> collectors.build_failure_collector
    collect_conforma.py        # -> collectors.conforma_violation_collector
    sync_component_status.py   # -> collectors.status_synchronizer
    check_sync_status.py       # -> collectors.status_synchronizer
    check_conforma_status.py   # -> collectors.status_synchronizer
    analyze_failures.py        # -> analyzers.build_failure_analyzer
    analyze_conforma.py        # -> analyzers.conforma_analyzer
```

**Note:** The MCP server lives in a separate package (`konflux-mcp-server/`) with Python 3.11+ requirement. It reuses repositories via a thin factory layer but doesn't depend on `config.py` or collectors.

No `utils/`, `helpers/`, `common/`, `misc/`. If something doesn't have a domain name, it doesn't belong.

## Security

**SQL injection** — Always use parameterized queries (`%s` placeholders). Never use f-strings, `.format()`, or string concatenation to build SQL. No exceptions.

```python
# correct
cursor.execute("SELECT * FROM builds WHERE name = %s", (name,))

# wrong — SQL injection vector
cursor.execute(f"SELECT * FROM builds WHERE name = '{name}'")
```

**Secrets** — Never hardcode credentials. Load from `.env` via `config.py`. Never log tokens, passwords, or API keys. The `.env` file is in `.gitignore`.

**Subprocess** — Pass arguments as lists, never as shell strings. Never use `shell=True`. Always set `timeout`. Validate subprocess outputs before using them.

```python
# correct
subprocess.run(['oc', 'get', 'pod', name], timeout=10)

# wrong — shell injection vector
subprocess.run(f'oc get pod {name}', shell=True)
```

**Input validation** — Validate data at system boundaries: user input, API responses, environment variables. Trust internal data flowing between modules.

**Dependencies** — Pin major versions in `requirements.txt`. Audit before adding new dependencies.

## Observability

**Logging** — Use the `logger` module, not `print()`. Use structured JSON logging in production (cron). Log at appropriate levels:
- `ERROR`: something broke and needs attention
- `WARNING`: unexpected but handled (API fallback, retry)
- `INFO`: significant state changes (scan started, component resolved, N failures found)
- `DEBUG`: detailed flow for troubleshooting

**Langfuse** — All Claude API calls go through Langfuse-tracked clients. Record: model, tokens, cost, latency, trace ID. Link trace IDs to database records (`ai_analysis.langfuse_trace_id`).

**Metrics** — Collection runs record: duration, components scanned, failures found, API source used, errors encountered. Stored in `scan_history` table.

**Error context** — When catching exceptions, log enough context to diagnose without reproducing:
```python
logger.error("Failed to fetch PipelineRun",
             extra={"pipelinerun": pr_name, "source": "kubearchive", "error": str(e)})
```

Never silently swallow exceptions with bare `except: pass`. At minimum, log a warning.

## Performance

**API calls** — Reuse HTTP sessions (connection pooling). Set timeouts on every network call. Paginate KubeArchive queries (500 items/page, max 3 pages). Cache auth tokens per session, not per call.

**Database** — Use connection context managers (open/close per operation, don't hold connections). Use `UPSERT` (`ON CONFLICT DO UPDATE`) instead of SELECT-then-INSERT. Use `DISTINCT ON` for latest-per-group queries instead of subqueries.

**Subprocess** — Always set `timeout`. Limit `--tail` on log fetches. Run independent subprocess calls sequentially — don't fan out (the cluster is the bottleneck, not local CPU).

**Data** — Truncate logs before storing (`max_log_size`). Don't load full log text into memory when only counting/searching. Use generators for large result sets.

**Batch limits** — AI analysis processes max 5 failures per cron run. Collection processes max 1500 PipelineRuns (3 pages). These limits prevent runaway resource usage.

## Error Handling

Use the exception hierarchy in `exceptions.py`. Raise specific exceptions, catch specific exceptions.

```python
# in a client
raise KubeArchiveAPIError(f"HTTP {resp.status_code} fetching {url}")

# in a collector
try:
    pr_data = client.get_pipelinerun(name)
except KubeArchiveAPIError:
    logger.warning("KubeArchive unavailable, trying live cluster")
    pr_data = fallback_client.get_pipelinerun(name)
```

**At system boundaries** (API clients, subprocess calls): catch transport errors, wrap in domain exceptions, log context.

**Inside pure functions**: don't catch — let errors propagate. A parsing function should raise `ValueError` if the input is malformed, not return `None` silently.

**In orchestrators** (collectors, CLI): catch domain exceptions, decide whether to skip/retry/abort, log the decision.

Never return `None` to signal an error when an exception is the right tool. `None` means "not found" or "not applicable" — it shouldn't mean "something broke."

## Async Code (MCP Server)

The MCP server uses async/await. Follow these patterns:

**Tools are async:**
```python
@mcp.tool()
async def get_failure(component: str) -> BuildFailureDetails:
    # DB operations are synchronous - run in executor
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_get_failure, component)

def _sync_get_failure(component: str) -> BuildFailureDetails:
    # Synchronous DB query
    with db.connection() as conn:
        ...
```

**Error handling in async:**
```python
try:
    result = await some_async_operation()
except SpecificError as e:
    logger.error("Async operation failed", extra={"error": str(e)})
    raise
```

**Don't:**
- Mix sync and async DB operations without executor
- Forget `await` (silent bugs)
- Use blocking I/O in async functions

## Documentation

**Docstrings:**
- Required for: public functions, classes, complex logic
- Optional for: private functions (if name is self-documenting)
- Format: Google style (simple, readable)

```python
def extract_error_messages(logs, max_lines=100):
    # type: (str, int) -> List[str]
    """Extract error messages from build logs.
    
    Args:
        logs: Raw build log text
        max_lines: Maximum lines to scan from the end
        
    Returns:
        List of error message strings, empty if none found
        
    Example:
        >>> extract_error_messages("Error: file not found\nWarning: deprecated")
        ['Error: file not found']
    """
```

**README structure:**
- Purpose (what problem does this solve?)
- Quick start (minimal working example)
- Configuration (env vars, files)
- Usage (common commands)
- Architecture (if complex)

**Changelogs:** Not required — use git history and ADRs for major decisions.

## Testing

**What to test:**
- All pure functions (parsers, extractors, validators) — no mocking needed
- Repository methods — mock the DB connection
- Client adapters — mock HTTP/subprocess responses
- Critical collector orchestration — mock clients and repositories

**What not to test:**
- Configuration loading (tested once, rarely changes)
- CLI argument parsing
- Print/logging output

**Test style:** Use pytest, not unittest. Plain functions, not classes. Use fixtures for shared setup. Name tests after behavior: `test_extract_error_messages_returns_none_for_empty_logs`.

**Run:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -v`

---

## Bash / Shell Scripts

The `ic` CLI is bash. These rules prevent the antipatterns that made the script hard to maintain at 5000+ lines.

### Core Principle

**Bash is glue, not application logic.** Bash dispatches commands, formats terminal output, and pipes between programs. Data transformation, parsing, and business logic belong in Python or SQL — never in bash heredocs.

### Shell Safety

Every bash script starts with:

```bash
#!/usr/bin/env bash
set -euo pipefail
```

- `set -e` — exit on error (already present)
- `set -u` — treat unset variables as errors
- `set -o pipefail` — propagate pipe failures (a `sql "..." | head -1` that fails silently is a bug)

### No Inline Python

Never embed Python code in bash heredocs or `python3 -c "..."` blocks. Extract to standalone `.py` files.

```bash
# wrong — untestable, unlintable, no IDE support
result=$(python3 -c "
import yaml, sys, json
data = yaml.safe_load(sys.stdin)
for item in data.get('rules', []):
    print(item['value'])
" <<< "$yaml_content")

# correct — standalone script, testable, lintable
result=$(python3 lib/parse_exceptions.py <<< "$yaml_content")
```

**Why:** Inline Python gets no syntax checking, no type hints, no test coverage, no IDE navigation. A typo in line 80 of a heredoc only surfaces at runtime. Variable interpolation between bash and Python (`'''$var'''`) breaks on quotes in data.

**Where to put extracted scripts:**
- Data transformation helpers → `lib/` directory (new)
- Domain logic → `collectors/python/` (existing)

### No Inline SQL

Never build SQL strings with variable interpolation in bash. Use parameterized helper functions or delegate to Python repositories.

```bash
# wrong — SQL injection risk, duplicated everywhere, hard to grep
count=$(sql "SELECT COUNT(*) FROM build_failures WHERE component_name = '$component' AND application = '$APPLICATION_NAME' AND is_resolved = FALSE")

# correct — named query function, single source of truth
count=$(sql_count_unresolved "$component")
```

**SQL query functions** live in a sourced file (`lib/queries.sh`) with parameterized helpers:

```bash
sql_count_unresolved() {
    local component="$1"
    sql "SELECT COUNT(*) FROM build_failures
         WHERE component_name = '$(sql_escape "$1")'
         AND application = '$(sql_escape "$APPLICATION_NAME")'
         AND is_resolved = FALSE"
}

sql_escape() {
    echo "${1//\'/\'\'}"
}
```

**Common query patterns** (CTEs like `latest_builds`, `latest_conforma`) must be defined once and reused — never copy-pasted.

### Function Size and Responsibility

**Maximum ~80 lines per function.** If a function exceeds this, it's doing too much. Split by responsibility:

```bash
# wrong — god function mixing data + formatting + logic
cmd_get_alerts() {
    # 400 lines: parse args, query builds, query conforma, 
    # query exceptions, format tables, compute summaries...
}

# correct — one function per concern
cmd_get_alerts() {
    parse_alert_args "$@"
    local build_data conforma_data
    build_data=$(fetch_alert_builds)
    conforma_data=$(fetch_alert_conforma)
    display_alert_builds "$build_data"
    display_alert_conforma "$conforma_data"
    display_alert_summary
}
```

### Formatting Helpers

Use helper functions for repeated formatting patterns. Define them in `lib/format.sh`:

```bash
section_header() {
    echo -e "${BOLD}========================================${NC}"
    echo -e "${BOLD}$1${NC}"
    echo -e "${BOLD}========================================${NC}"
}

trim() {
    echo "$1" | xargs 2>/dev/null || echo ""
}

color() {
    local color_code="$1" text="$2"
    echo -e "${color_code}${text}${NC}"
}
```

Never write raw ANSI escape codes inline — always use the color variables (`$RED`, `$GREEN`, `$BOLD`, `$NC`).

### Error Handling

**Never suppress errors silently.** `2>/dev/null` hides real problems.

```bash
# wrong — silently produces empty output when DB is down
count=$(sql "SELECT COUNT(*) ..." 2>/dev/null)

# correct — check and report
count=$(sql "SELECT COUNT(*) ...") || {
    echo -e "${RED}Error: database query failed${NC}" >&2
    return 1
}
```

**Use `return`, not `exit`**, in functions called from the main dispatcher. `exit` kills the shell if the script is sourced.

**Validate at entry points:**

```bash
require_db() {
    if ! docker exec ci-autohealing-db pg_isready -q 2>/dev/null; then
        echo -e "${RED}Error: database is not running${NC}" >&2
        return 1
    fi
}
```

### Configuration

**No hard-coded values in function bodies.** Define all configuration at the top of the script or in a sourced config file:

```bash
# top of script or lib/config.sh
: "${PGPASSWORD:=admin}"
: "${DB_CONTAINER:=ci-autohealing-db}"
: "${DB_NAME:=konflux_monitoring}"
: "${KUBEARCHIVE_URL:=https://kubearchive-api.example.com}"
```

Use `${VAR:=default}` syntax — reads from environment, falls back to default. Never hard-code credentials; load from `.env`.

### Source Structure

```
ic                     # Main CLI: dispatch + display only
lib/
    config.sh          # Configuration variables and defaults
    format.sh          # section_header(), trim(), color(), table helpers
    queries.sh         # Named SQL query functions (sql_latest_builds, etc.)
    resolve.sh         # Shared logic (resolve_alert_number, clipboard_or_print)
    parse_exceptions.py    # GitLab YAML exception parser
    parse_csv.py           # CSV violation parser/differ
    parse_jira.py          # Jira JSON parser/payload builder
    parse_pipelineruns.py  # PipelineRun history formatter
```

The `ic` script sources bash helpers:

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/config.sh"
source "$SCRIPT_DIR/lib/format.sh"
source "$SCRIPT_DIR/lib/queries.sh"
source "$SCRIPT_DIR/lib/resolve.sh"
```

### Naming (Bash)

```
functions:     snake_case           (fetch_alert_builds, display_conforma_table)
cmd handlers:  cmd_<verb>_<noun>    (cmd_get_alerts, cmd_describe_component)
variables:     snake_case           (build_count, component_name)
constants:     UPPER_SNAKE_CASE     (APPLICATION_NAME, MAX_LOG_SIZE)
temp files:    use mktemp           (never hard-code /tmp/ic-*)
```

### Subshell Pitfalls

Variables set inside a pipeline or `while ... done <<< ...` subshell are lost when the subshell exits. This is a common bash bug source.

```bash
# wrong — counter is always 0 after the loop
count=0
echo "$data" | while read -r line; do
    ((count++))
done
echo "$count"  # prints 0

# correct — use process substitution or here-string without pipe
count=0
while read -r line; do
    ((count++))
done <<< "$data"
echo "$count"  # prints actual count
```

### Duplication Prevention

**Before writing a new function**, grep for existing implementations:

- Does a similar SQL query already exist? → Reuse or parameterize
- Does similar formatting already exist? → Extract a shared helper
- Is there a Python module that does this? → Call it instead of reimplementing in bash

**The "three strikes" rule:** If you write the same pattern three times, extract it. Two is tolerable; three is a refactoring signal.

---

## SQL / PostgreSQL

### Parameterized Queries

**In Python:** Always use `%s` placeholders via psycopg2. Never interpolate.

```python
# correct
cursor.execute(
    "SELECT * FROM build_failures WHERE component_name = %s AND application = %s",
    (component, application)
)

# wrong
cursor.execute(f"SELECT * FROM build_failures WHERE component_name = '{component}'")
```

**In Bash:** Use the `sql_escape()` helper from `lib/queries.sh` for any user-provided values. Prefer calling Python repositories for complex queries.

### Query Organization

**One definition per query pattern.** The `WITH latest_builds AS (SELECT DISTINCT ON ...)` CTE must be defined once, not copy-pasted 9 times.

In bash (`lib/queries.sh`):

```bash
CTE_LATEST_BUILDS="
WITH latest_builds AS (
    SELECT DISTINCT ON (component_name)
        component_name, pipelinerun_name, status, error_type,
        error_message, first_detected_at, is_resolved
    FROM build_failures
    WHERE application = '\$APP'
    ORDER BY component_name, first_detected_at DESC
)"

sql_latest_failing_builds() {
    local app="${1:-$APPLICATION_NAME}"
    local cte="${CTE_LATEST_BUILDS//\$APP/$app}"
    sql "$cte SELECT * FROM latest_builds WHERE is_resolved = FALSE ORDER BY first_detected_at DESC"
}
```

In Python: queries live in repository classes — never in collectors or analyzers.

### Schema Conventions

```
tables:        snake_case plural    (build_failures, conforma_results)
columns:       snake_case           (component_name, first_detected_at)
booleans:      is_/has_ prefix      (is_resolved, has_logs)
timestamps:    _at suffix           (created_at, resolved_at, first_detected_at)
foreign keys:  <table_singular>_id  (build_failure_id, conforma_result_id)
indexes:       idx_<table>_<cols>   (idx_build_failures_component_app)
```

### Query Patterns

**Latest per group** — use PostgreSQL `DISTINCT ON`, not subqueries:

```sql
SELECT DISTINCT ON (component_name)
    component_name, status, first_detected_at
FROM build_failures
WHERE application = %s
ORDER BY component_name, first_detected_at DESC
```

**Upsert** — use `ON CONFLICT DO UPDATE`, not SELECT-then-INSERT:

```sql
INSERT INTO build_failures (pipelinerun_name, component_name, ...)
VALUES (%s, %s, ...)
ON CONFLICT (pipelinerun_name) DO UPDATE SET
    status = EXCLUDED.status,
    updated_at = NOW()
```

**Counting** — use `COUNT(*)` with proper WHERE, not fetching all rows and counting in application code.

### Migrations

Schema changes go in numbered migration files: `migrations/NNN_description.sql`. Never ALTER TABLE in inline SQL. Document breaking changes in commit messages.

---

## SOLID Principles (Applied)

These principles govern all code — Python, bash, and SQL.

### Single Responsibility (SRP)

Each module, class, and function does one thing.

- A **parser** transforms data — it doesn't fetch or store it
- A **client** handles network I/O — it doesn't parse responses beyond basic deserialization
- A **repository** manages persistence — it doesn't contain business logic
- A **collector** orchestrates: call client → call parser → call repository
- A **bash cmd function** dispatches and displays — it doesn't transform data

**Violation signal:** A function that imports from both `clients/` and `repositories/` (unless it's a collector). A bash function that contains both SQL and Python.

### Open/Closed (OCP)

Add new behavior by adding new modules, not by editing existing ones.

- New API source → new client implementing `PipelineRunSource` protocol
- New failure type → new collector + repository, not a flag in the existing collector
- New CLI command → new `cmd_*` function, add to dispatch — don't modify existing commands

### Liskov Substitution (LSP)

Any `PipelineRunSource` implementation can replace another without breaking callers. `UnifiedPipelineClient` depends on the protocol, not on `KubeArchiveClient` specifically.

### Interface Segregation (ISP)

Don't force implementations to provide methods they don't use. The `PipelineRunSource` protocol has only the methods that all sources share. Source-specific capabilities (KubeArchive pagination, Tekton Results API filtering) stay on the concrete class.

### Dependency Inversion (DIP)

High-level modules (collectors, analyzers) depend on abstractions (protocols, repository interfaces), not concrete implementations. Constructors accept dependencies via parameters:

```python
class BuildFailureCollector:
    def __init__(self, config, db=None, build_repo=None, k8s=None):
        self.build_repo = build_repo or BuildFailureRepository(db)
        self.k8s = k8s or KubernetesClient(namespace=config.k8s.namespace)
```

This enables testing with mocks and swapping implementations without changing the collector.

---

## Design Patterns Reference

### When to Use Each Pattern

| Pattern | Use When | Example in Codebase |
|---------|----------|-------------------|
| **Adapter** | Wrapping external APIs behind a common interface | `KubeArchiveClient`, `KubernetesClient` → `PipelineRunSource` |
| **Chain of Responsibility** | Trying multiple sources in order | `UnifiedPipelineClient` tries KubeArchive → live cluster → Tekton Results |
| **Repository** | Isolating persistence from business logic | `BuildFailureRepository`, `ConformaRepository` |
| **Factory Method** | Building objects from external state | `CollectorConfig.from_env()`, `DatabaseConnection.from_config()` |
| **Protocol/Interface** | Defining contracts between layers | `PipelineRunSource`, `LLMProvider` |
| **Strategy** | Swapping algorithms at runtime | Different `LLMProvider` implementations (Vertex AI, local) |
| **Template Method** | Shared orchestration with varying steps | Base collector pattern: discover → fetch → parse → store |

### Patterns to Avoid

| Pattern | Why | Alternative |
|---------|-----|-------------|
| **Singleton** | Hidden global state, hard to test | Module-level instances or factory functions |
| **Builder** | Objects aren't complex enough to justify | Constructor with defaults |
| **Deep inheritance** (>2 levels) | Fragile, hard to reason about | Composition + protocols |
| **Observer/Event bus** | Adds indirection without benefit at this scale | Direct function calls |
| **God Object** | Accumulates responsibilities | Split by SRP |

---

## Functional Programming Principles

### Pure Functions

A pure function:
- Returns the same output for the same input (deterministic)
- Has no side effects (no I/O, no mutation, no global state)
- Is trivially testable (no mocking needed)

**All data transformation must be pure.** This is the single most important rule for maintainability.

```python
# pure — takes data, returns data
def classify_build_status(reason: str) -> BuildStatus:
    if reason in ('Succeeded', 'Completed'):
        return BuildStatus.SUCCEEDED
    if reason in ('Failed', 'CouldntGetTask'):
        return BuildStatus.FAILED
    return BuildStatus.UNKNOWN

# impure — calls subprocess (I/O side effect)
def get_pod_logs(pod_name: str) -> str:
    result = subprocess.run(['oc', 'logs', pod_name], ...)
    return result.stdout
```

### Immutability

Prefer immutable data structures:

- `@dataclass(frozen=True)` for configuration and domain models
- Tuple over list when the collection shouldn't change
- `dataclasses.replace()` to create modified copies instead of mutating

```python
# correct — create new object
updated = replace(config, namespace="new-ns")

# wrong — mutate in place
config.namespace = "new-ns"
```

### Composition Over Inheritance

Build complex behavior by composing simple functions:

```python
# compose pure functions in a pipeline
raw = fetch_pipelinerun(name)           # I/O
metadata = extract_pipelinerun_metadata(raw)  # pure
status = classify_build_status(metadata['reason'])  # pure
save_result(metadata, status)           # I/O
```

Each step is independently testable. The composition (collector) is the only place that touches I/O.

### Higher-Order Functions

Use `map`, `filter`, list comprehensions for data transformation. Avoid imperative loops when a declarative approach is clearer:

```python
# declarative
failing = [c for c in components if c.status == BuildStatus.FAILED]

# avoid when the declarative form is clearer
failing = []
for c in components:
    if c.status == BuildStatus.FAILED:
        failing.append(c)
```

### Separation of Effects

Keep I/O at the edges. The architecture should look like a sandwich:

```
I/O (fetch data from APIs, DB)
  ↓
Pure logic (parse, transform, classify, decide)
  ↓
I/O (store results, display output)
```

Never interleave I/O and logic. A function that parses a dict and then makes an API call is doing two things — split it.
