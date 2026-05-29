# Skills Integration API

How external skills consume ic data — CLI JSON output and MCP tool reference.

---

## CLI Commands with `--json`

Every `ic get` and `ic describe` command supports `--json` for structured, parseable output.

```bash
# JSON flag works after the subcommand
./ic get alerts --json
./ic get components --json
./ic describe component my-component --json
```

### ic get alerts --json

All unresolved build failures and conforma violations.

```json
{
  "application": "rhoai-v3-5-ea-1",
  "build_failures": [
    {
      "component": "odh-vllm-v3-5-ea-1",
      "status": "Failed",
      "error_type": "Task Failure",
      "first_seen": "2026-05-20T10:00:00",
      "last_seen": "2026-05-21T08:00:00",
      "has_logs": true,
      "has_analysis": false
    }
  ],
  "conforma_violations": [
    {
      "component": "odh-dashboard-v3-5-ea-1",
      "status": "Conforma Violation",
      "error_type": "conforma-registry-rhoai-prod-...",
      "first_seen": "2026-05-18T12:00:00",
      "has_details": true,
      "has_analysis": true
    }
  ],
  "total_count": 2
}
```

### ic get components --json

Currently failing components.

```json
[
  {
    "component": "odh-vllm-v3-5-ea-1",
    "pipelinerun_name": "odh-vllm-v3-5-ea-1-on-push-abc12",
    "status": "Failed",
    "error_type": "Task Failure",
    "first_seen": "2026-05-20T10:00:00",
    "has_logs": true,
    "has_analysis": false
  }
]
```

### ic get conforma --json

Conforma violation summary.

```json
[
  {
    "component": "odh-dashboard-v3-5-ea-1",
    "scenario": "conforma-registry-rhoai-prod-...",
    "status": "Failed",
    "violations_count": 4,
    "warnings_count": 12,
    "successes_count": 200,
    "first_seen": "2026-05-18T12:00:00",
    "last_seen": "2026-05-21T08:00:00",
    "repository_url": "https://github.com/...",
    "has_analysis": true,
    "jira_key": "RHOAIENG-1234"
  }
]
```

### ic describe component \<name\> --json

Full build failure details for a component.

```json
[
  {
    "component": "odh-vllm-v3-5-ea-1",
    "pipelinerun_name": "odh-vllm-v3-5-ea-1-on-push-abc12",
    "status": "Failed",
    "repository_url": "https://github.com/...",
    "branch": "rhoai-3.5-ea.1",
    "failed_step": "build-images",
    "error_type": "Build Error",
    "error_message": "nothing provides glibc = 2.34...",
    "commit_sha": "abc123def456...",
    "commit_author": "developer",
    "commit_message": "chore(deps): update base image",
    "first_seen": "2026-05-20T10:00:00",
    "last_seen": "2026-05-21T08:00:00",
    "has_logs": true,
    "has_analysis": true,
    "jira_key": "RHOAIENG-1234",
    "output_image": "quay.io/rhoai/odh-vllm:latest"
  }
]
```

### ic describe conforma \<name\> --json

Conforma violation details for a component.

```json
[
  {
    "component": "odh-dashboard-v3-5-ea-1",
    "scenario": "conforma-registry-rhoai-prod-...",
    "pipelinerun_name": "...",
    "status": "Failed",
    "violations_count": 4,
    "warnings_count": 12,
    "successes_count": 200,
    "first_seen": "2026-05-18T12:00:00",
    "last_seen": "2026-05-21T08:00:00",
    "repository_url": "https://github.com/...",
    "commit_sha": "abc123...",
    "snapshot_name": "...",
    "has_details": true,
    "has_analysis": true,
    "jira_key": null
  }
]
```

### ic get apps --json

Available application versions.

```json
{
  "applications": [
    {"name": "rhoai-v3-5-ea-1", "records": 45, "current": true},
    {"name": "rhoai-v3-4", "records": 120, "current": false}
  ],
  "current": "rhoai-v3-5-ea-1"
}
```

### ic get pipelineruns --json

Recent PipelineRun failures.

```json
[
  {
    "pipelinerun_name": "odh-vllm-v3-5-ea-1-on-push-abc12",
    "component": "odh-vllm-v3-5-ea-1",
    "status": "Failed",
    "has_logs": true,
    "detected": "2026-05-20T10:00:00"
  }
]
```

---

## MCP Tool Reference

The MCP server exposes the same data as typed Pydantic models. AI agents (Claude Code, Copilot) see these as callable tools.

| MCP Tool | CLI Equivalent | Returns |
|----------|---------------|---------|
| `list_alerts` | `ic get alerts` | `AlertsSummary` |
| `get_failure` | `ic describe component` | `BuildFailureDetails` |
| `get_violation` | `ic describe conforma` | `ConformaViolationDetails` |
| `get_analysis` | `ic ai status <comp>` | `AnalysisDetails` |
| `get_stats` | `ic ai status` | `StatsResponse` |
| `get_triage` | `ic triage` | `TriageResponse` |
| `get_health` | `ic health` | Component health scores |
| `get_conforma_report` | `ic conforma report` | Conforma standup table |
| `search_failures` | `ic get components` | Filtered failure search |
| `get_component_history` | `ic history` | Build history timeline |
| `get_working` | `ic working` | Working components |
| `get_resolved` | `ic resolved` | Resolved components |
| `list_patterns` | `ic patterns list` | Error pattern library |
| `get_dashboard` | `ic dashboard` | Operational metrics |

### Pydantic Models

MCP tools return structured data matching these models (defined in `src/mcp_server/models.py`):

- **AlertsSummary**: `application`, `build_failures[]`, `conforma_violations[]`, `total_count`
- **BuildFailureDetails**: `component`, `pipelinerun_name`, `status`, `error_message`, `commit_sha`, `build_logs`
- **ConformaViolationDetails**: `component`, `scenario`, `violations_count`, `warnings_count`, `violation_details`
- **AnalysisDetails**: `component`, `category`, `root_cause`, `recommended_fix`, `confidence_score`, `can_auto_fix`
- **StatsResponse**: `build` (pending/analyzed/auto_fixable), `conforma` (same), `total_cost`

---

## Example Workflows

### Auto-analyze unanalyzed failures

```bash
./ic get alerts --json \
  | jq -r '.build_failures[] | select(.has_analysis==false) | .component' \
  | while read comp; do
      echo "Analyzing $comp..."
      ./ic ai analyze "$comp"
    done
```

### Get violation data for a conforma fix skill

```bash
component="odh-dashboard-v3-5-ea-1"
violation=$(./ic describe conforma "$component" --json)
echo "$violation" | jq '.[0].violations_count'
```

### Check if a component is still failing

```bash
component="odh-vllm-v3-5-ea-1"
status=$(./ic get alerts --json | jq -r --arg c "$component" \
  '.build_failures[] | select(.component==$c) | .status')
[ -z "$status" ] && echo "Not failing" || echo "Still failing: $status"
```

### Export failure as environment variables (for skills)

```bash
component="odh-vllm-v3-5-ea-1"
eval "$(./ic describe component "$component" --json | jq -r '
  .[0] | to_entries[] | select(.value != null) |
  "IC_\(.key | ascii_upcase)=\(.value | @sh)"')"
echo "Component: $IC_COMPONENT, Step: $IC_FAILED_STEP"
```
