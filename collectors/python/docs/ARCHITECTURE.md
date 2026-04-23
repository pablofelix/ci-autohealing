# CI Auto-Healing Architecture

## Overview

This Python package collects and stores CI/CD build failure data from Red Hat's Konflux platform to enable automated troubleshooting and AI-assisted failure analysis.

**What it does:**
- Monitors Tekton PipelineRun failures across multiple components
- Fetches comprehensive logs, commit info, and error details
- Tracks Conforma (Enterprise Contract) test violations
- Stores everything in PostgreSQL for historical analysis
- Powers the `ic` CLI tool for triage and investigation

**Data sources:**
- **KubeArchive** - Archived Kubernetes resources (primary)
- **Live OpenShift cluster** - Current PipelineRuns and Components
- **Tekton Results API** - Alternative historical data source

---

## Architecture Layers

The codebase follows a clean 4-layer architecture with clear separation of concerns:

```
┌─────────────────────────────────────────────────────┐
│  Entry Points (5 scripts)                           │
│  collect_comprehensive.py, sync_component_status.py │
│  collect_conforma.py, check_*.py                    │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│  Collectors (orchestration)                         │
│  BuildFailureCollector, ConformaViolationCollector  │
│  StatusSynchronizer                                 │
└────────┬───────────────────────────────┬────────────┘
         │                               │
┌────────▼────────────┐         ┌────────▼────────────┐
│  Clients (I/O)      │         │  Repositories (SQL) │
│  KubeArchive        │         │  BuildFailure       │
│  Kubernetes         │         │  Conforma           │
│  TektonResults      │         │  SyncStatus         │
│  UnifiedPipeline    │         └─────────────────────┘
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

**Examples:**
- `extract_pipelinerun_metadata()` - Extract commit SHA, URLs, author from PR JSON
- `extract_error_from_logs()` - Parse logs for error patterns
- `classify_build_status()` - Map Tekton status to BuildStatus enum

**Pattern:** All functions take `Dict[str, Any]` (Kubernetes JSON) and return typed data. No network calls, no database, no file I/O.

### Layer 2: Clients (I/O Adapters)

**Directory:** `clients/`

Adapters that fetch data from external systems. Abstract away transport details (HTTP, subprocess, API quirks) behind a common interface.

**Key pattern:** `PipelineRunSource` ABC defines the contract. Each client implements:
- `get_pipelinerun(name)` → Dict
- `get_taskrun(name)` → Dict  
- `get_pod_logs(pod, container)` → str

**Clients:**
- **KubeArchiveClient** - HTTP queries to archived K8s resources
- **KubernetesClient** - Live cluster via `oc` CLI
- **TektonResultsClient** - Tekton Results API
- **UnifiedPipelineClient** - Tries all sources with fallback

**See:** `clients/README.md` for detailed explanation of the ABC pattern and source priority.

### Layer 3: Repositories (Database)

**Directory:** `repositories/`

SQL operations on PostgreSQL tables. Parameterized queries, no business logic.

**Pattern:** One repository per table, methods grouped by query type (find, insert, update, mark_resolved).

**Repositories:**
- **BuildFailureRepository** - `build_failures` table (10 methods)
- **ConformaRepository** - `conforma_results` table
- **SyncStatusRepository** - `sync_status` cache table

**Example:**
```python
def find_failing_component_names(self, application):
    # type: (str) -> Set[str]
    with self.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT component_name
            FROM build_failures
            WHERE is_resolved = FALSE AND application = %s
        """, (application,))
        return {row[0] for row in cursor.fetchall()}
```

### Layer 4: Collectors (Orchestration)

**Directory:** `collectors/`

High-level workflows that coordinate clients + repositories to accomplish tasks.

**Collectors:**
- **BuildFailureCollector** - Discover failing components, fetch comprehensive logs, store in DB
- **ConformaViolationCollector** - Collect Conforma test violations and detailed reports
- **StatusSynchronizer** - Mark resolved failures, record successes, maintain status cache

**Pattern:** Thin orchestration. Collectors delegate to clients for I/O and repositories for persistence. Business logic stays in parsers.

---

## SOLID Principles Applied

This codebase was refactored to follow SOLID principles. See the [SOLID refactoring plan](../../../.claude/plans/streamed-dreaming-spark.md) for full details.

### 1. Dependency Inversion Principle

**All collectors accept dependencies via constructor with defaults:**

```python
class BuildFailureCollector:
    def __init__(self, config, db=None, build_repo=None,
                 kubearchive=None, k8s=None, tekton_results=None, unified=None):
        self.config = config
        if db is None:
            db = DatabaseConnection(config.db)
        self.build_repo = build_repo or BuildFailureRepository(db)
        self.kubearchive = kubearchive or KubeArchiveClient(...)
        # ... etc
```

**Benefits:**
- Easy to test (inject mocks)
- Easy to compose (inject custom implementations)
- Entry points call with config only; tests inject mocks directly

### 2. Single Responsibility Principle

**Eliminated duplicate code:**
- **Before:** 6 duplicate "query KubeArchive + live cluster" implementations
- **After:** Shared `query_pipelineruns()` function in `clients/pipelinerun_query.py`

- **Before:** Duplicate status classification in 3 places
- **After:** Single `classify_build_status()` pure function

- **Before:** 3 duplicate `get_component_metadata()` subprocess calls
- **After:** Single `KubernetesClient.get_component_metadata()` method

### 3. Interface Segregation Principle

**Removed dead code:**
- Deleted 3 unused methods from BuildFailureRepository (11 methods → 10 methods)
- `get_pipelineruns_without_logs()`, `update_logs()`, `insert_pipelinerun()` had zero callers

### 4. Open/Closed Principle

**Clients extend via ABC without modifying existing code:**
- `PipelineRunSource` ABC defines contract
- New data sources implement the ABC
- `UnifiedPipelineClient` composes multiple sources transparently

### 5. Liskov Substitution Principle

**Any PipelineRunSource can be swapped:**
```python
# All valid, all work identically
source = KubeArchiveClient(...)
source = KubernetesClient(...)
source = TektonResultsClient(...)

# Client code doesn't care which one
pr_data = source.get_pipelinerun('my-pipeline-run')
```

---

## Key Patterns

### Pattern 1: Query Deduplication by UID

**Problem:** PipelineRuns appear in both KubeArchive (archived) and live cluster. Fetching from both sources creates duplicates.

**Solution:** `query_pipelineruns()` deduplicates by UID:

```python
def query_pipelineruns(namespace, label_selector, ...):
    by_uid = {}
    
    # Fetch from KubeArchive
    for pr in kubearchive_results:
        by_uid[pr['metadata']['uid']] = pr
    
    # Fetch from live cluster (overwrites with newer data)
    for pr in live_cluster_results:
        by_uid[pr['metadata']['uid']] = pr
    
    return list(by_uid.values())
```

Live cluster data takes precedence (newer metadata).

### Pattern 2: Fallback Source Chain

**UnifiedPipelineClient tries sources in priority order:**

```
KubeArchive (fastest, most reliable)
    ↓ if 404
Live Cluster (current data)
    ↓ if not found
Tekton Results API (alternative historical source)
```

Callers don't know or care which source succeeded.

### Pattern 3: Structured Logging

**Never use `print()` for application output:**

```python
# ❌ Bad
print("Fetching component data...")

# ✅ Good
logger.info("Fetching component data...")
```

**Use `print(json.dumps())` only for machine-readable output:**

```python
# CLI tools that output JSON for scripts
print(json.dumps({'status': 'ok', 'count': 42}))
```

See `STYLE.md` for full logging guidelines.

### Pattern 4: Type Comments (Python 3.6 Compatibility)

**Use `# type:` comments instead of inline annotations:**

```python
def get_pipelinerun(self, name, namespace=None):
    # type: (str, Optional[str]) -> Optional[Dict[str, Any]]
    """Fetch PipelineRun by name."""
```

**Why:** Python 3.6.8 constraint. Modern syntax like `def foo(x: str) -> int:` breaks on 3.6.

---

## Entry Points

### Collection Scripts (Cron Jobs)

**`collect_comprehensive.py`**
- Discovers failing components from cluster
- Fetches last failure per component with comprehensive logs
- Stores in `build_failures` table
- **Used by:** `cron/collect-comprehensive.sh`

**`collect_conforma.py`**
- Discovers failing Conforma test PipelineRuns
- Extracts violation details from `verify` TaskRun logs
- Stores in `conforma_results` table
- **Used by:** `cron/collect-comprehensive.sh`

**`sync_component_status.py`**
- Marks failures as resolved when component builds succeed
- Records successful builds in database
- **Used by:** `cron/collect-comprehensive.sh`

### Status Check Scripts (Cache Updates)

**`check_sync_status.py`**
- Queries cluster for currently failing push builds
- Updates `sync_status` cache table
- Powers `ic get components` out-of-sync warnings
- **Used by:** `cron/collect-comprehensive.sh`, `ic` CLI

**`check_conforma_status.py`**
- Queries cluster for failing Conforma tests
- Updates `sync_status` cache table
- Powers `ic get conforma` listings
- **Used by:** `cron/collect-comprehensive.sh`, `ic` CLI

---

## Data Flow

### Build Failure Collection Flow

```
1. BuildFailureCollector.discover_components_from_cluster()
   ↓
2. query_pipelineruns(namespace, 'type=build')
   ├─ KubeArchive: archived PipelineRuns
   └─ Live cluster: current PipelineRuns
   ↓
3. Filter to push builds with status='False'
   ↓
4. For each failing component:
   ├─ get_component_metadata() → repo URL, branch
   ├─ get_last_failed_pipelinerun() → PR name, UID, status
   ├─ UnifiedPipelineClient.get_logs_complete() → comprehensive logs
   ├─ UnifiedPipelineClient.get_pipelinerun_complete() → metadata
   ├─ extract_error_from_logs() → error message, type
   └─ BuildFailureRepository.upsert_failure() → store in DB
```

### Status Synchronization Flow

```
1. StatusSynchronizer.run()
   ↓
2. BuildFailureRepository.find_unresolved_component_names()
   ↓
3. For each unresolved component:
   ├─ get_current_status() → latest push build status
   │   └─ query_pipelineruns() → KubeArchive + live cluster
   ├─ If status == SUCCEEDED:
   │   ├─ mark_resolved() → update is_resolved=TRUE
   │   └─ record_successful_build() → insert success record
   └─ get_component_metadata() → enrich repo/branch info
```

### IC CLI Query Flow

```
1. User runs: ic get components
   ↓
2. ic (Go binary) queries PostgreSQL:
   SELECT * FROM build_failures WHERE is_resolved=FALSE
   ↓
3. ic queries sync_status cache:
   SELECT * FROM sync_status WHERE type='build'
   ↓
4. Compare cluster vs DB, show out-of-sync warnings
   ↓
5. If stale, spawn background job:
   python3 check_sync_status.py
```

---

## Testing Strategy

**Location:** `tests/` directory (120 tests, all passing)

### Pure Function Tests (tekton_parsers)
- Unit tests with mock Kubernetes JSON
- Test all edge cases (missing fields, malformed data, empty arrays)
- Fast (no I/O), deterministic

### Client Tests (with mocks)
- Mock HTTP responses (KubeArchive)
- Mock subprocess output (Kubernetes, TektonResults)
- Verify retry logic, error handling, timeout behavior

### Collector Tests (with dependency injection)
- Inject `MagicMock()` for all dependencies
- Test orchestration logic in isolation
- No network, no database, no cluster access

**Example:**
```python
@pytest.fixture
def collector():
    config = CollectorConfig(...)
    return BuildFailureCollector(
        config,
        db=MagicMock(),
        build_repo=MagicMock(),
        kubearchive=MagicMock(),
        k8s=MagicMock(),
        tekton_results=MagicMock(),
        unified=MagicMock(),
    )
```

**Run tests:**
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -v
```

---

## Configuration

**Environment variables:**
```bash
DB_HOST=localhost
DB_PORT=5432
DB_USER=ciautohealing
DB_PASSWORD=...
DB_NAME=ciautohealing
K8S_NAMESPACE=NAMESPACE_PLACEHOLDER
K8S_APPLICATION_NAME=acme-v2-0
KUBEARCHIVE_API_URL=https://kubearchive-api-server...
```

**See:** `config.py` for `CollectorConfig` dataclass and `from_env()` loader.

---

## Evolution

**Refactoring history:**

1. **Step 1-8 (Code Quality)** - Extracted pure functions, created ABC for clients, parameterized SQL, structured logging
2. **SOLID Refactoring (Steps 1-5)** - Applied dependency inversion, eliminated duplicate code, removed dead methods
3. **Test Organization** - Moved tests to `tests/` directory, removed obsolete utilities

**Commits:**
- `274f87f` - Create collectors/ package and convert entry points to thin shims
- `dca6754` - Update STYLE.md with descriptive naming guidance
- `105b2d5` - Apply Dependency Inversion: constructors accept optional dependencies
- `6012445` - Unify status classification with classify_build_status pure function
- `ffd4df4` - Remove dead code from BuildFailureRepository (ISP cleanup)
- `8d7a4b1` - Extract shared PipelineRun query and component metadata (SRP)
- `3f35121` - Organize tests and remove unused utilities

**Current stats:**
- 120 tests passing
- ~3000 lines of production code
- ~1600 lines of test code
- 18 root modules + 3 organized subsystems
- Zero flake8 violations
- Type-checked with mypy (Python 3.6 mode)

---

## Related Documentation

- **[STYLE.md](../../../STYLE.md)** - Code style, naming conventions, logging
- **[clients/README.md](../clients/README.md)** - Client ABC pattern, source priority
- **[SOLID Plan](.claude/plans/streamed-dreaming-spark.md)** - Detailed refactoring plan and rationale
