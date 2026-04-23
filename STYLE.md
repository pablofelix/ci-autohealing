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

## Design Patterns

Use patterns where they solve a real problem, not for decoration.

**Adapter** — Each API client (`KubeArchiveClient`, `KubernetesClient`) adapts a different transport (HTTP, subprocess) to a common `PipelineRunSource` protocol. Parsing logic lives outside the adapter.

**Chain of Responsibility** — `UnifiedCollector` holds an ordered list of `PipelineRunSource` adapters. It tries each until one succeeds. Adding a new source means writing one adapter, not modifying the chain.

**Repository** — Database access goes through repository classes (`BuildFailureRepository`, `ConformaRepository`). Collectors never see SQL. Repositories use parameterized queries exclusively.

**Factory Method** — `Config.from_env()`, `Database.from_config()`. Use classmethods to build complex objects from external state (env vars, files). Keep `__init__` simple and deterministic.

**Protocol** — Define interfaces with `typing.Protocol` (or ABC for Python 3.6). Clients implement protocols; consumers depend on the protocol, not the concrete class.

Avoid: Singleton `__new__` (use module-level instances or factory functions), Builder (objects aren't complex enough), deep inheritance (prefer composition), Visitor, Abstract Factory.

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
    
    repositories/              # Database access, parameterized queries only
        build_failure_repository.py   # BuildFailureRepository
        conforma_repository.py        # ConformaRepository
        sync_status_repository.py     # SyncStatusRepository
        connection.py                 # Connection manager (context manager)

    collectors/                # Orchestration: compose pure functions + I/O
        build_failure_collector.py       # BuildFailureCollector
        conforma_violation_collector.py  # ConformaViolationCollector
        status_synchronizer.py           # StatusSynchronizer + cluster queries

    # Entry-point shims (called by cron and ic tool)
    collect_comprehensive.py   # -> collectors.build_failure_collector
    collect_conforma.py        # -> collectors.conforma_violation_collector
    sync_component_status.py   # -> collectors.status_synchronizer
    check_sync_status.py       # -> collectors.status_synchronizer
    check_conforma_status.py   # -> collectors.status_synchronizer
```

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
