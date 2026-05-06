# Konflux MCP Server

Model Context Protocol (MCP) server that exposes Konflux CI monitoring data to AI agents.

## What It Does

Allows external AI agents (Claude Desktop, GitHub Copilot, Cursor, etc.) to query Konflux build failures and Conforma violations without SSH/DB access. Agents can:
- Discover available RHOAI versions
- Query build failures and Conforma violations
- Compare failures across versions
- Access AI analyses
- Get summary statistics

## Architecture

**Design:** Application-agnostic, stateless tools
- Each tool accepts `application` parameter (multi-version support)
- No `CollectorConfig` dependency (reads DB config from env vars)
- Reuses existing repositories from `collectors/python/repositories`
- Pydantic response models (validated outputs)
- Read-only (no orchestration, no writes)

**Pattern:** Hybrid approach
- **Parallel comparison** (read-only): Compare stats/failures across versions
- **Sequential fixing** (write ops): Focus on ONE version when creating PRs
- **Smart reuse**: Check if fix applies to other versions without context mixing

## 7 MCP Tools

### 1. `list_applications()`
Discover available RHOAI versions.

**Returns:** `List[ApplicationInfo]`
```json
[
  {"name": "acme-v2-0", "component_count": 45, "failure_count": 6},
  {"name": "acme-v2-1-ea-1", "component_count": 48, "failure_count": 22}
]
```

**Usage Pattern:** First call in any session to discover what versions exist.

---

### 2. `list_alerts(application: str = "acme-v2-0")`
Get all alerts (build failures + Conforma violations) for a version.

**Returns:** `AlertsSummary`
```json
{
  "application": "acme-v2-0",
  "build_failures": [...],
  "conforma_violations": [...],
  "total_count": 9,
  "last_sync": "2026-04-24T10:30:00Z"
}
```

**Usage Pattern:** Read-only (safe for parallel comparison)
- ✅ Compare alerts across versions to prioritize work
- ✅ Check version health before deciding which to fix

---

### 3. `get_failure(component: str, application: str = "acme-v2-0", include_logs: bool = True, include_commit_context: bool = True)`
Get full build failure details.

**Returns:** `BuildFailureDetails`
```json
{
  "component": "odh-vllm-cpu-v3-4",
  "pipelinerun_name": "odh-vllm-cpu-v3-4-abc123",
  "error_message": "ModuleNotFoundError: No module named 'nvidia'",
  "error_type": "dependency_issue",
  "build_logs": "...",
  "commit_sha": "a1b2c3d",
  "commit_context": {...},
  "konflux_url": "https://...",
  ...
}
```

**Usage Patterns:**
- ✅ **Parallel:** Compare same component across versions for regression detection
  ```python
  v34 = get_failure("odh-vllm-cpu-v3-4", "acme-v2-0")
  v35 = get_failure("odh-vllm-cpu-v3-5", "acme-v2-1-ea-1")
  # Check if root cause is the same
  ```
- ✅ **Sequential:** Get details for ONE version when fixing
  ```python
  failures = search_failures("acme-v2-0")
  for f in failures:
    details = get_failure(f.component, "acme-v2-0")
    # Fix this failure, then next
  ```

---

### 4. `get_violation(component: str, application: str = "acme-v2-0", include_details: bool = True)`
Get full Conforma violation details.

**Returns:** `ConformaViolationDetails`
```json
{
  "component": "acme-autorag-v3-4",
  "scenario": "rhoai-test",
  "violations_count": 2,
  "warnings_count": 0,
  "violation_summary": "...",
  "violation_details": {...},
  ...
}
```

**Usage Patterns:**
- ✅ **Parallel:** Check if policy violation exists across versions
- ✅ **Sequential:** Get details for ONE version when requesting exception

---

### 5. `get_analysis(component: str, application: str = "acme-v2-0", type: Literal["auto", "build", "conforma"] = "auto")`
Get existing AI analysis if available.

**Returns:** `Optional[AnalysisDetails]`
```json
{
  "type": "build",
  "component": "odh-vllm-cpu-v3-4",
  "model_used": "claude-sonnet-4-6",
  "root_cause": "Package nvidia-ml-py==12.535.77 not found on PyPI",
  "failure_category": "dependency_issue",
  "confidence_score": 0.92,
  "recommended_fix": "Update to nvidia-ml-py==12.535.161",
  "recommended_files": ["requirements.txt"],
  "can_auto_fix": true,
  "requires_human_review": false,
  ...
}
```

**Usage Patterns:**
- ✅ **Parallel:** Compare analyses to check if fix is reusable
  ```python
  v34_analysis = get_analysis("odh-vllm-cpu-v3-4", "acme-v2-0")
  v35_analysis = get_analysis("odh-vllm-cpu-v3-5", "acme-v2-1-ea-1")
  if v34_analysis.root_cause == v35_analysis.root_cause:
    # Same fix works for both (apply sequentially)
  ```
- ✅ **Hybrid:** Check v3-5 while fixing v3-4
  ```python
  # While creating PR for v3-4:
  v35_has_same = get_analysis(component, "acme-v2-1-ea-1")
  # If yes, note to apply same fix after v3-4 PR merges
  ```

---

### 6. `search_failures(application: str = "acme-v2-0", category: Optional[str] = None, resolved: bool = False, has_analysis: Optional[bool] = None, limit: int = 10)`
Search/filter failures.

**Returns:** `List[FailureSummary]`

**Usage Patterns:**
- ✅ **Parallel:** Compare patterns across versions
  ```python
  v34_deps = search_failures("acme-v2-0", category="dependency_issue")
  v35_deps = search_failures("acme-v2-1-ea-1", category="dependency_issue")
  # Which dependency issues are NEW in v3-5? (regressions)
  ```
- ✅ **Sequential:** Get work list for ONE version
  ```python
  failures = search_failures("acme-v2-0", resolved=False)
  for f in failures:
    # Fix each failure in v3-4
    # Don't start v3-5 until v3-4 is complete
  ```
- ❌ **Anti-pattern:** Interleaving fixes
  ```python
  # DON'T DO THIS:
  v34_failures = search_failures("acme-v2-0")
  v35_failures = search_failures("acme-v2-1-ea-1")
  for f34, f35 in zip(v34_failures, v35_failures):
    # Creates context switching nightmare
  ```

---

### 7. `get_stats(application: str = "acme-v2-0")`
Get summary statistics.

**Returns:** `StatsResponse`
```json
{
  "application": "acme-v2-0",
  "build_failures": {
    "pending": 6,
    "analyzed": 45,
    "autofixable": 8
  },
  "conforma_violations": {
    "pending": 3,
    "analyzed": 2,
    "autofixable": 0
  },
  "total_cost_30d": 2.34,
  "recent_analyses": [...]
}
```

**Usage Pattern:** Read-only (perfect for parallel comparison)
- ✅ Compare health across all versions to prioritize work
  ```python
  for app in list_applications():
    stats = get_stats(app)
  # Result: v3-4 has 6 failures, v3-5 has 22
  # Decision: Fix v3-4 first (fewer, easier wins)
  ```
- ✅ Release readiness check
  ```python
  stable = get_stats("acme-v2-0")
  candidate = get_stats("acme-v2-1-ea-1")
  if candidate.failure_count > stable.failure_count * 2:
    "v3-5 not ready - 2x more failures than stable"
  ```

---

## Installation

**Requirements:**
- Python 3.11+ (MCP server only - collectors stay on 3.6.8)
- Access to ci-autohealing PostgreSQL database

**Install:**
```bash
cd konflux-mcp-server
pip install -e .
```

**Configure Claude Desktop:**
```json
// ~/.config/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "konflux": {
      "command": "python",
      "args": ["-m", "konflux_mcp"],
      "env": {
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_NAME": "konflux_monitoring",
        "DB_USER": "postgres",
        "DB_PASSWORD": ""
      }
    }
  }
}
```

**Test:**
```bash
# List available tools
mcp list-tools

# Test a tool
mcp call list_applications
```

---

## Example Workflows

### 1. Situational Awareness (Parallel Comparison)
```
User: "What RHOAI versions do we have and which needs attention?"

Agent calls (parallel):
  list_applications()
  get_stats("acme-v2-0")
  get_stats("acme-v2-1-ea-1")

Result:
  - acme-v2-0: 6 failures (healthiest)
  - acme-v2-1-ea-1: 22 failures (3x worse!)

Agent: "v3-5 has 16 NEW failures vs v3-4. Start with v3-4 (easier wins)?"
```

### 2. Regression Detection (Parallel Comparison)
```
User: "Did odh-vllm-cpu break in v3-5 or was it already broken?"

Agent calls:
  get_failure("odh-vllm-cpu-v3-4", "acme-v2-0")
  get_failure("odh-vllm-cpu-v3-5", "acme-v2-1-ea-1")

Result: "NEW regression in v3-5. Didn't exist in v3-4."
```

### 3. Sequential Fixing (Focus on ONE Version)
```
User: "Fix all v3-4 failures"

Agent workflow (sequential):
  failures = search_failures("acme-v2-0", resolved=False)
  
  for failure in v3-4:
    get_failure(failure.component, "acme-v2-0")
    get_analysis(failure.component, "acme-v2-0")
    # Create PR for v3-4
    # Wait for CI
  
Agent: "All 6 v3-4 failures resolved ✓. Ready for v3-5?"
# Don't mix v3-5 PRs into this workflow
```

### 4. Smart Fix Reuse (Hybrid)
```
User (fixing v3-4): "Will this fix help v3-5?"

Agent (hybrid):
  # Quick check (read-only)
  v35_failure = get_failure(component, "acme-v2-1-ea-1")
  v35_analysis = get_analysis(component, "acme-v2-1-ea-1")
  
  if same_root_cause:
    "Yes! Fix v3-4 first, then I'll apply the same fix to v3-5."
    # Sequential: finish v3-4 PR, then create v3-5 PR
```

### 5. Release Readiness (Parallel Comparison)
```
User: "Can we promote v3-5 to stable?"

Agent calls:
  stable = get_stats("acme-v2-0")
  candidate = get_stats("acme-v2-1-ea-1")
  
Result: "NO - v3-5 has 3x more failures. Fix 16 regressions first."
```

---

## Response Models (Pydantic)

All responses use Pydantic models for validation:

```python
class ApplicationInfo(BaseModel):
    name: str
    component_count: int
    failure_count: int
    conforma_count: int = 0
    last_sync: Optional[datetime] = None

class FailureSummary(BaseModel):
    component: str
    status: str
    error_type: Optional[str]
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    has_logs: bool
    has_analysis: bool

class BuildFailureDetails(BaseModel):
    component: str
    pipelinerun_name: str
    error_message: str
    error_type: Optional[str]
    failed_task: Optional[str]
    failed_step: Optional[str]
    build_logs: Optional[str]  # Truncated to 50K chars
    commit_sha: Optional[str]
    commit_message: Optional[str]
    commit_author: Optional[str]
    commit_url: Optional[str]
    repository_url: str
    branch: str
    commit_context: Optional[dict]  # Full JSONB
    konflux_url: str
    first_detected_at: datetime

class ConformaViolationDetails(BaseModel):
    component: str
    scenario: str
    violations_count: int
    warnings_count: int
    successes_count: int
    violation_summary: str
    violation_details: Optional[dict]  # JSONB
    repository_url: Optional[str]
    commit_sha: Optional[str]
    snapshot_name: Optional[str]
    konflux_url: str
    first_detected_at: datetime

class AnalysisDetails(BaseModel):
    type: Literal["build", "conforma"]
    component: str
    model_used: str
    root_cause: str
    failure_category: str
    confidence_score: float
    recommended_fix: str
    recommended_files: List[str]
    can_auto_fix: bool
    requires_human_review: bool
    analyzed_at: datetime
    langfuse_trace_url: Optional[str]
    tokens_used: int
    cost_usd: float

class AlertsSummary(BaseModel):
    application: str
    build_failures: List[FailureSummary]
    conforma_violations: List[FailureSummary]
    total_count: int
    last_sync: datetime

class StatsResponse(BaseModel):
    application: str
    build_failures: dict  # pending, analyzed, autofixable
    conforma_violations: dict
    total_cost_30d: float
    recent_analyses: List[dict]
```

---

## Development

**Structure:**
```
konflux-mcp-server/
├── pyproject.toml
├── README.md
├── src/
│   └── konflux_mcp/
│       ├── __init__.py
│       ├── server.py          # FastMCP server
│       ├── tools.py           # 7 MCP tools
│       ├── models.py          # Pydantic response models
│       └── repository_factory.py  # DB connection (no config dependency)
└── tests/
    └── test_tools.py
```

**Run tests:**
```bash
pytest -v
```

**See also:**
- [collectors/python/docs/ARCHITECTURE.md](../collectors/python/docs/ARCHITECTURE.md) - Full system architecture
- [docs/ROADMAP.md](../docs/ROADMAP.md) - Phase 1.7 MCP Server details
- [STYLE.md](../STYLE.md) - Code style guide
