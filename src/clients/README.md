# Clients Package

Data source adapters for fetching Tekton PipelineRun and TaskRun data from multiple systems.

## Architecture Pattern: Abstract Base Class

All clients implement the `PipelineRunSource` ABC, which defines three primitive operations:

```python
from abc import ABC, abstractmethod

class PipelineRunSource(ABC):
    @abstractmethod
    def get_pipelinerun(self, name, namespace=None):
        # type: (str, Optional[str]) -> Optional[Dict[str, Any]]
        """Fetch PipelineRun JSON by name."""

    @abstractmethod
    def get_taskrun(self, name, namespace=None):
        # type: (str, Optional[str]) -> Optional[Dict[str, Any]]
        """Fetch TaskRun JSON by name."""

    @abstractmethod
    def get_pod_logs(self, pod_name, container=None, namespace=None, tail_lines=None):
        # type: (str, Optional[str], Optional[str], Optional[int]) -> Optional[str]
        """Fetch logs for a pod/container."""
```

**Why this works:**
- **Uniform interface** - Collectors don't care if data comes from KubeArchive, live cluster, or Tekton Results
- **Easy testing** - Mock the ABC, not the transport layer
- **Composability** - UnifiedPipelineClient tries multiple sources transparently
- **Replaceability** - Swap data sources without changing caller code

---

## Client Implementations

### KubeArchiveClient (Primary Source)

**Transport:** HTTP REST API  
**Strengths:**
- Fast (indexed queries)
- Reliable (archived data doesn't disappear)
- Complete (stores PipelineRuns, TaskRuns, Pod logs)

**Implementation:**
```python
class KubeArchiveClient(PipelineRunSource):
    def __init__(self, api_url=None, namespace='NAMESPACE_PLACEHOLDER'):
        self.api_url = api_url or discover_kubearchive_api_url()
        self.token = get_openshift_token()
        self.session = create_authenticated_session(self.token)
    
    def get_pipelinerun(self, name, namespace=None):
        url = f"{self.api_url}/apis/tekton.dev/v1/namespaces/{ns}/pipelineruns/{name}"
        response = self.session.get(url, timeout=30)
        return response.json() if response.status_code == 200 else None
```

**When to use:**
- Default choice for archived PipelineRuns (older than 1-2 hours)
- Pagination queries with label selectors
- Historical data analysis

**Limitations:**
- Recent PipelineRuns may not be archived yet (1-2 hour lag)
- Requires OpenShift token authentication

### KubernetesClient (Live Cluster)

**Transport:** `oc` CLI subprocess  
**Strengths:**
- Always up-to-date (no lag)
- Can fetch Component metadata (`oc get component`)
- Works for resources not yet in KubeArchive

**Implementation:**
```python
class KubernetesClient(PipelineRunSource):
    def get_pipelinerun(self, name, namespace=None):
        result = subprocess.run(
            ['oc', 'get', 'pipelinerun', name, '-n', ns, '-o', 'json'],
            stdout=subprocess.PIPE, timeout=10
        )
        return json.loads(result.stdout) if result.returncode == 0 else None
```

**When to use:**
- Very recent PipelineRuns (< 1 hour old)
- Component metadata lookups (`get_component_metadata`)
- Real-time status checks

**Limitations:**
- Slower than KubeArchive (subprocess + kubectl latency)
- Old PipelineRuns may be garbage-collected
- Requires valid `oc login` session

### TektonResultsClient (Fallback)

**Transport:** Tekton Results API (gRPC → HTTP gateway)  
**Strengths:**
- Alternative historical data source
- Can work when KubeArchive is unavailable

**Implementation:**
```python
class TektonResultsClient(PipelineRunSource):
    def get_pipelinerun(self, name, namespace=None):
        # Query Tekton Results API by resource name
        url = f"{self.api_url}/apis/results.tekton.dev/v1alpha2/parents/{namespace}/results/-/records"
        # Filter for PipelineRun with matching name
        # ...
```

**When to use:**
- Backup when KubeArchive is down
- Environments without KubeArchive
- Alternative data validation

**Limitations:**
- Slower queries (not optimized for our use case)
- Complex filtering (no direct name lookup)
- Less commonly used (may have bugs)

---

## UnifiedPipelineClient (Recommended)

**Pattern:** Composite client that tries sources in priority order with automatic fallback.

```python
class UnifiedPipelineClient:
    def __init__(self, sources=None, namespace='NAMESPACE_PLACEHOLDER'):
        if sources is None:
            sources = [
                KubeArchiveClient(namespace=namespace),
                KubernetesClient(namespace=namespace),
                TektonResultsClient(namespace=namespace),
            ]
        self.sources = sources
    
    def get_pipelinerun_complete(self, name):
        # type: (str) -> Tuple[Optional[Dict], str]
        """Try all sources until one succeeds."""
        for source in self.sources:
            try:
                pr_data = source.get_pipelinerun(name)
                if pr_data:
                    return pr_data, source.__class__.__name__
            except Exception:
                continue
        return None, 'none'
```

**Source priority:**

1. **KubeArchive** (fastest, most reliable)
   - Try first for archived data
   - If `404`, resource might be too new or very old

2. **Live Cluster** (current data)
   - Try second for recent resources
   - If not found, resource might be archived/deleted

3. **Tekton Results** (fallback)
   - Try last as backup
   - Slower, less optimized

**Return value includes source name** so callers know where data came from (useful for logging/debugging).

**Example:**
```python
unified = UnifiedPipelineClient()
pr_data, source = unified.get_pipelinerun_complete('my-pipeline-run-abc123')

if pr_data:
    logger.info("Got PipelineRun from %s", source)  # "KubeArchiveClient"
else:
    logger.error("PipelineRun not found in any source")
```

---

## When to Use Each Client

| Scenario | Recommended Client | Why |
|----------|-------------------|-----|
| Last failure per component | `UnifiedPipelineClient` | Covers both recent and archived |
| Bulk historical query | `KubeArchiveClient` directly | Faster pagination, no retries needed |
| Very recent PipelineRun (< 1 hr) | `KubernetesClient` directly | Not in archive yet |
| Component metadata | `KubernetesClient.get_component_metadata()` | Only in live cluster |
| Production collectors | `UnifiedPipelineClient` | Resilient to transient failures |
| Testing | Mock `PipelineRunSource` | Test logic, not transport |

---

## Advanced: Custom Source Priority

**Override source list for specific use cases:**

```python
# Only try KubeArchive (fail fast if not archived)
archived_only = UnifiedPipelineClient(sources=[
    KubeArchiveClient()
])

# Live cluster first (for real-time monitoring)
live_first = UnifiedPipelineClient(sources=[
    KubernetesClient(),
    KubeArchiveClient(),
])

# Single source (no fallback)
kubearchive = KubeArchiveClient()
pr_data = kubearchive.get_pipelinerun('my-pr')
```

---

## Shared Utility: query_pipelineruns()

**Module:** `pipelinerun_query.py`

Consolidates the common pattern: "query both KubeArchive and live cluster, deduplicate by UID."

```python
def query_pipelineruns(namespace, label_selector, 
                       kubearchive_url=None, session=None, 
                       max_pages=3):
    # type: (str, str, ...) -> List[Dict[str, Any]]
    """
    Fetch PipelineRuns from KubeArchive + live cluster.
    
    Deduplicates by UID (live cluster data takes precedence).
    Returns combined list.
    """
```

**Used by:**
- `BuildFailureCollector.discover_components_from_cluster()`
- `BuildFailureCollector.get_last_failed_pipelinerun()`
- `get_failing_build_components()` (status check)
- `get_failing_conforma_components()` (status check)
- `StatusSynchronizer.get_current_status()`
- `ConformaViolationCollector.get_failing_conforma_pipelineruns()`

**Why extract it:**
- Eliminates 6 duplicate implementations (256 lines saved)
- Centralized deduplication logic (UID-based)
- Consistent retry/timeout behavior
- Single place to fix bugs

**Example:**
```python
from clients.pipelinerun_query import query_pipelineruns

# Get all build failures for an application
prs = query_pipelineruns(
    namespace='NAMESPACE_PLACEHOLDER',
    label_selector='appstudio.openshift.io/application=acme-v2-0,pipelines.appstudio.openshift.io/type=build'
)

# Filter to failing components
for pr in prs:
    conditions = pr.get('status', {}).get('conditions', [])
    if conditions and conditions[-1].get('status') == 'False':
        component = pr['metadata']['labels']['appstudio.openshift.io/component']
        print(f"Component {component} is failing")
```

---

## Authentication & Discovery

**Module:** `openshift_auth.py`

Shared utilities for all clients:

```python
def get_openshift_token() -> Optional[str]:
    """Get token via `oc whoami -t`."""

def discover_kubearchive_api_url() -> str:
    """Read from ConfigMap or use fallback."""

def create_authenticated_session(token: str) -> requests.Session:
    """Create HTTP session with Bearer token."""
```

**Used by:**
- `KubeArchiveClient.__init__()` - Get token + session
- `query_pipelineruns()` - Auto-discover URL + token if not provided
- Entry points - Check `is_logged_in()` before starting

---

## Container Name Convention

**Different sources use different container naming:**

- **Live cluster pods:** `step-<name>` (e.g., `step-build`)
- **KubeArchive:** `step-<name>` (matches pod convention)
- **Tekton Results:** (varies, sometimes just `<name>`)

**Solution:** `step_container_name()` hook in PipelineRunSource:

```python
class PipelineRunSource(ABC):
    def step_container_name(self, step_name):
        # type: (str) -> str
        """Map step name to container name for this source."""
        return step_name  # Override in subclasses if needed

class KubeArchiveClient(PipelineRunSource):
    def step_container_name(self, step_name):
        return f"step-{step_name}"
```

**Used by:** `get_pipelinerun_logs()` to fetch logs from correct container.

---

## Error Handling

**All clients return `None` on failure** (don't raise exceptions):

```python
pr_data = client.get_pipelinerun('nonexistent')
# pr_data == None (not an exception)
```

**Why:**
- Callers can check `if pr_data:` without try/except
- UnifiedPipelineClient can silently try next source
- Transient network errors don't crash collectors

**When to raise exceptions:**
- Configuration errors (no token, invalid URL)
- Permanent failures (authentication denied)

**Logging:**
```python
try:
    response = session.get(url, timeout=30)
    if response.status_code != 200:
        return None  # Log at caller level
    return response.json()
except requests.RequestException:
    return None  # Transient failure, don't log here
```

Collectors log at `logger.warning()` level when all sources fail.

---

## Testing Clients

**Mock the ABC, not the transport:**

```python
from unittest.mock import MagicMock

def test_collector_with_mock_source():
    mock_source = MagicMock(spec=PipelineRunSource)
    mock_source.get_pipelinerun.return_value = {
        'metadata': {'name': 'test-pr'},
        'status': {'conditions': [{'status': 'False'}]}
    }
    
    collector = BuildFailureCollector(
        config, unified=mock_source
    )
    
    result = collector.some_method()
    # Test logic without network calls
```

**For integration tests** (if needed), use `@patch('subprocess.run')` to mock `oc` commands or `@patch('requests.Session.get')` for HTTP.

---

## LLM Provider Clients (AI Analysis)

### Pattern: Provider-Independent LLM Interface

Similar to how `PipelineRunSource` abstracts Tekton data sources, `LLMProvider` abstracts LLM providers (Vertex AI, Anthropic API, Gemini, etc.).

**Why provider independence:**
- Swap providers by changing config (no code changes)
- Test analyzers with mock LLM responses
- Future-proof against provider changes
- Support multiple providers in same codebase

### LLMProvider ABC

```python
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    @abstractmethod
    def create_message(self, system, user_content, tools=None, max_tokens=4096):
        # type: (str, str, Optional[List[Dict]], int) -> LLMResponse
        """Send a message to the LLM and return a structured response."""

    @abstractmethod
    def model_name(self):
        # type: () -> str
        """Return the model identifier for logging/tracking."""
```

**LLMResponse dataclass:**
```python
@dataclass(frozen=True)
class LLMResponse:
    content: str                  # Text response
    tool_calls: List[Dict]        # Structured output (for tool_use)
    model: str                    # Model identifier
    input_tokens: int             # Token count (input)
    output_tokens: int            # Token count (output)
    stop_reason: str              # Why generation stopped
```

### Current Providers

#### VertexAIProvider (Default)

**Transport:** Claude on Vertex AI via `anthropic` SDK's `AnthropicVertex` client  
**Strengths:**
- Same SDK as direct Anthropic API (same tool_use, same prompts)
- GCP Application Default Credentials (no API keys to manage)
- Integrated with GCP billing and IAM

**Authentication:**
```bash
gcloud auth application-default login
```

**Configuration:**
```bash
LLM_PROVIDER=vertex_ai
VERTEX_PROJECT_ID=your-gcp-project-id
VERTEX_REGION=us-east5
LLM_MODEL=claude-sonnet-4-5-20250929
```

**Available regions:** us-east5, europe-west1, asia-southeast1

**When to use:**
- You have GCP access
- You want to avoid managing API keys
- You need GCP integration (billing, IAM, logging)

**Limitations:**
- Requires Claude models enabled in Vertex AI Model Garden
- GCP account and project required
- Regional availability may vary

#### AnthropicDirectProvider (Future)

**Transport:** Direct Anthropic API via `anthropic` SDK  
**Strengths:**
- No GCP dependency
- All Claude models available
- Direct access to latest features

**Configuration:**
```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here
LLM_MODEL=claude-sonnet-4-5-20250929
```

**When to use:**
- You have an Anthropic API key
- You don't use GCP
- You need models not yet on Vertex AI

### Adding a New Provider

To add a new LLM provider (e.g., Gemini, Azure OpenAI):

1. **Create adapter class** (`clients/gemini_provider.py`):
   ```python
   from clients.llm_provider import LLMProvider, LLMResponse
   
   class GeminiProvider(LLMProvider):
       def create_message(self, system, user_content, tools=None, max_tokens=4096):
           # Call Gemini SDK, map response to LLMResponse
       
       def model_name(self):
           return self._model
   ```

2. **Update factory** in `clients/llm_provider.py`:
   ```python
   def create_llm_provider(config):
       if config.provider == 'gemini':
           from clients.gemini_provider import GeminiProvider
           return GeminiProvider(...)
   ```

3. **Update config** (`config.py`):
   Add Gemini-specific fields to `LLMConfig` if needed.

4. **Document** in `.env.example` and this README.

**Analyzers never change** - they only depend on `LLMProvider` ABC.

### Factory Pattern

```python
from clients.llm_provider import create_llm_provider

# Create provider from config
llm = create_llm_provider(config.llm)

# Call without knowing the concrete provider
response = llm.create_message(
    system="You are a CI troubleshooting expert",
    user_content="Analyze this failure...",
    tools=[ANALYSIS_TOOL],
)
```

**Lazy imports:** Factory only imports the SDK that's configured, so you can run collectors without AI dependencies installed.

### Langfuse Tracking

**Module:** `langfuse_tracker.py`

Wraps Langfuse SDK to track all LLM calls for observability. Records:
- Traces per analysis run
- Generations per LLM call
- Token usage, cost, duration
- Links trace IDs to database records

**Graceful degradation:** If Langfuse not configured (missing `LANGFUSE_PUBLIC_KEY`), tracking is disabled but analysis still works.

**Usage:**
```python
tracker = LangfuseTracker(enabled=True)

# Create trace
trace = tracker.create_trace(
    name='build-failure-analysis',
    metadata={'failure_id': 123}
)

# Record LLM generation
tracker.record_generation(
    trace, name='diagnose-root-cause',
    model='claude-sonnet-4-5',
    prompt=prompt, completion=response.content,
    input_tokens=100, output_tokens=200,
    duration_ms=1500
)

# Finalize
tracker.end_trace(trace, output=analysis_result)
tracker.flush()
```

### Testing LLM Clients

**Mock the ABC:**
```python
from unittest.mock import MagicMock
from clients.llm_provider import LLMProvider, LLMResponse

def test_analyzer_with_mock_llm():
    mock_llm = MagicMock(spec=LLMProvider)
    mock_llm.create_message.return_value = LLMResponse(
        content='',
        tool_calls=[{
            'name': 'record_analysis',
            'input': {
                'root_cause': 'Dependency conflict',
                'failure_category': 'dependency_issue',
                'confidence_score': 0.95,
                'recommended_fix': 'Pin to version X',
                'can_auto_fix': True,
            }
        }],
        model='claude-sonnet-4-5',
        input_tokens=1000,
        output_tokens=200,
        stop_reason='end_turn',
    )
    
    analyzer = BuildFailureAnalyzer(config, llm=mock_llm)
    result = analyzer.analyze_failure(failure_data)
    
    # Test analysis logic without calling real LLM
```

---

## Related Documentation

- **[ARCHITECTURE.md](../docs/ARCHITECTURE.md)** - Overall system architecture
- **[STYLE.md](../../../STYLE.md)** - Code style and conventions
- **Source code:** `clients/` directory for full implementations
