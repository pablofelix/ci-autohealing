# Python Collectors for CI Auto-Healing

Modern, maintainable Python implementation of the CI Auto-Healing collectors using the KubeArchive API.

## Features

- **KubeArchive API Integration**: Direct access to archived PipelineRuns, TaskRuns, and logs
- **Type-Safe**: Full type hints for better IDE support and error detection
- **Functional Programming**: Uses functional patterns (map, filter, comprehensions)
- **Clean Architecture**: Separated concerns (API client, database, models, config)
- **Multiple Fallbacks**: Tries active pods first, then KubeArchive for archived resources
- **Configurable**: Uses `.env` file for all configuration

## Architecture

```
collectors/python/
├── __init__.py                   # Package initialization
├── config.py                     # Configuration management (from .env)
├── models.py                     # Data models (PipelineRun, TaskRun, etc.)
├── kubearchive_client.py         # KubeArchive API client
├── database.py                   # PostgreSQL operations
├── collect_failures.py           # Main collector script
├── fetch_archived_logs.py        # Archived logs fetcher
├── requirements.txt              # Python dependencies
└── README.md                     # This file
```

## Installation

### 1. Install Dependencies

```bash
cd collectors/python

# Install required packages
pip3 install -r requirements.txt
```

### 2. Configure Environment

The collectors automatically load configuration from the `.env` file in the project root:

```bash
# Database
DB_HOST=localhost
DB_PORT=5433
DB_USER=postgres
DB_PASSWORD=admin
DB_NAME=konflux_monitoring

# Kubernetes
NAMESPACE=NAMESPACE_PLACEHOLDER
APPLICATION_NAME=acme-v2-0

# Components
COMPONENTS_FILE=HOME_DIR/components-ui-failed.txt

# Optional: KubeArchive API URL (auto-discovered if not set)
KUBEARCHIVE_API_URL=https://kubearchive-api-server-product-kubearchive.apps...
```

## Usage

### Collect Build Failures

Scan components for failed PipelineRuns and store in database:

```bash
# Basic collection (no logs)
python3 collect_failures.py

# Collect failures and fetch logs immediately
python3 collect_failures.py --fetch-logs

# Use custom components file
python3 collect_failures.py --components-file /path/to/components.txt
```

**Output:**
```
[1/8] odh-feature-server-v3-4
  Failures: 12, New: 2, Logs: 2
[2/8] acme-api-registry-sync-v3-4
  Failures: 0, New: 0, Logs: 0
...

========================================
Collection Complete
========================================
Components scanned: 8
Failures found: 35
New inserted: 5
Logs fetched: 5
Duration: 23.4s
```

### Fetch Archived Logs

Fetch logs for PipelineRuns that don't have logs yet (uses KubeArchive API):

```bash
# Fetch logs for up to 10 PipelineRuns
python3 fetch_archived_logs.py

# Fetch more
python3 fetch_archived_logs.py --limit 50
```

**Output:**
```
========================================
Fetching Archived Logs via KubeArchive
========================================

Found 10 PipelineRuns without logs

[1/10] Fetching logs for odh-model-registry-v3-4-on-push-abc123... ✓ Saved (45231 chars)
[2/10] Fetching logs for conforma-registry-acme-prod-v3-4-xyz789... ✓ Saved (12045 chars)
...

========================================
Complete
========================================
Processed: 10
Successful: 8
```

## How It Works

### 1. Failure Collection Flow

```
collect_failures.py
    ↓
Load components from file
    ↓
For each component:
    ├─ Get metadata from Kubernetes (oc get component)
    ├─ Find failed PipelineRuns (kubectl tekton)
    ├─ Check if already in database
    ├─ (Optional) Fetch logs from active pods
    └─ Insert into PostgreSQL
    ↓
Update scan statistics
```

### 2. Archived Logs Flow

```
fetch_archived_logs.py
    ↓
Query database for PipelineRuns without logs
    ↓
For each PipelineRun:
    ├─ Get PipelineRun from KubeArchive API
    ├─ Extract TaskRun names from childReferences
    ├─ For each TaskRun:
    │   ├─ Get TaskRun details
    │   ├─ Find failed steps (exitCode != 0)
    │   └─ Fetch pod logs for failed steps
    └─ Save combined logs to database
```

### 3. KubeArchive API Paths

**Tekton Resources** (PipelineRuns, TaskRuns):
```
/apis/tekton.dev/v1/namespaces/{namespace}/pipelineruns/{name}
/apis/tekton.dev/v1/namespaces/{namespace}/taskruns/{name}
```

**Pod Logs**:
```
/api/v1/namespaces/{namespace}/pods/{pod}/log?container={container}
```

## Key Modules

### `config.py` - Configuration Management

```python
from config import CollectorConfig

# Auto-loads from .env file
config = CollectorConfig.from_env()

# Access configuration
print(config.db.host)              # localhost
print(config.k8s.namespace)         # NAMESPACE_PLACEHOLDER
print(config.k8s.application_name)  # acme-v2-0
```

### `kubearchive_client.py` - KubeArchive API Client

```python
from kubearchive_client import KubeArchiveClient

# Client auto-discovers API URL and gets auth token
client = KubeArchiveClient()

# Get PipelineRun details
pr_data = client.get_pipelinerun("my-pipelinerun")

# Get TaskRun details
tr_data = client.get_taskrun("my-taskrun")

# Get pod logs
logs = client.get_pod_logs("my-pod", container="build")

# Get complete PipelineRun logs (orchestrates all the above)
logs = client.get_pipelinerun_logs("my-pipelinerun")
```

### `database.py` - PostgreSQL Operations

```python
from database import Database
from config import CollectorConfig

config = CollectorConfig.from_env()
db = Database(config.db)

# Create scan
scan_id = db.create_scan(scan_type='python', scan_mode='full')

# Check if PipelineRun exists
exists = db.pipelinerun_exists("my-pr")

# Insert new PipelineRun
db.insert_pipelinerun(pipelinerun, app_name)

# Update logs
db.update_pipelinerun_logs("my-pr", logs)
```

### `models.py` - Data Models

```python
from models import PipelineRun, TaskRun, BuildStatus, Component

# Type-safe data structures
pr = PipelineRun(
    name="odh-feature-server-v3-4-on-push-abc123",
    uid="550e8400-e29b-41d4-a716-446655440000",
    namespace="NAMESPACE_PLACEHOLDER",
    component="odh-feature-server-v3-4",
    repository="acme-org/feast",
    repository_url="https://github.com/acme-org/feast",
    branch="acme-3.4",
    status=BuildStatus.FAILED
)

# Auto-generates Konflux URLs
print(pr.konflux_logs_url)  # https://konflux-ui...
print(pr.has_logs)          # False
```

## Benefits Over Shell Scripts

### ✅ Better Maintainability
- **Type hints**: Catch errors before runtime
- **Clear structure**: Separated modules with single responsibilities
- **Better error handling**: Exception handling vs exit codes

### ✅ Easier Testing
- **Unit testable**: Mock API calls, database operations
- **Type checking**: `mypy` catches type errors
- **Consistent**: Python testing frameworks

### ✅ More Readable
- **Docstrings**: Every function documented
- **Functional patterns**: List comprehensions, map/filter
- **Named constants**: No magic strings

### ✅ Better API Integration
- **requests library**: Robust HTTP client
- **JSON handling**: Native Python dicts
- **Session management**: Connection pooling, retries

### ✅ Database Operations
- **Context managers**: Automatic connection cleanup
- **Parameterized queries**: SQL injection prevention
- **Transaction management**: Automatic rollback on errors

## Development

### Type Checking

```bash
pip3 install mypy
mypy collect_failures.py
```

### Code Formatting

```bash
pip3 install black
black *.py
```

### Add New Features

1. **New data model**: Add to `models.py`
2. **New API endpoint**: Add to `kubearchive_client.py`
3. **New DB operation**: Add to `database.py`
4. **New collector**: Create new script, import from modules

## Integration with Cron

You can use Python collectors in cron jobs:

```bash
# Add to crontab
*/15 * * * * cd PROJECT_DIR/collectors/python && python3 collect_failures.py --fetch-logs >> /var/log/ci-autohealing/python-collector.log 2>&1
```

## Comparison: Shell vs Python

| Feature | Shell Scripts | Python Collectors |
|---------|--------------|-------------------|
| API calls | `curl` pipes | `requests` library |
| JSON parsing | `jq` | Native dicts |
| Error handling | Exit codes | Exceptions |
| Type safety | None | Full type hints |
| Testing | Hard | Easy (unit tests) |
| Maintainability | Medium | High |
| Performance | Fast | Fast enough |
| Dependencies | Shell tools | Python packages |

## Troubleshooting

### KubeArchive API Unauthorized

```bash
# Check if you're logged into OpenShift
oc whoami

# Check KubeArchive API URL
oc get cm -n product-kubearchive kubearchive-api-url -o jsonpath='{.data.URL}'
```

### Database Connection Error

```bash
# Verify PostgreSQL is running
docker ps | grep ci-autohealing-db

# Test connection
docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -c "SELECT 1"
```

### Import Errors

```bash
# Make sure you're in the python/ directory
cd collectors/python

# Install dependencies
pip3 install -r requirements.txt
```

## Future Enhancements

- [ ] Async/await for parallel API calls
- [ ] Retry logic with exponential backoff
- [ ] Structured logging (JSON logs)
- [ ] Prometheus metrics export
- [ ] Unit test suite
- [ ] Integration tests
- [ ] CLI with rich progress bars
- [ ] Rate limiting for API calls
