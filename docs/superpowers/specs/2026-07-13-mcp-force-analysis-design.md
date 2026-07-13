# MCP Force Analysis Tool — Design Spec

## Problem

The MCP tool `get_analysis` returns stored AI analysis results. When a failure changes (e.g., same component now fails at a different step after a partial fix), the stored analysis is stale. There is no MCP tool to trigger re-analysis — the only option is falling back to the CLI (`ic ai analyze <component> --force`).

## Solution

Add a new MCP tool `analyze_failure` that triggers AI analysis for a component, optionally with `force=True` to re-analyze even if an analysis already exists.

## Interface

```
Tool: analyze_failure
Parameters:
  - component (str, required): Component name
  - application (str, default "rhoai-v3-5"): Application name
  - force (bool, default False): Re-analyze even if analysis exists
Returns: AnalysisDetails (same schema as get_analysis)
```

## Implementation

### `src/mcp_server/tools.py`

New tool function `analyze_failure`:

1. Create `BuildFailureAnalyzer` instance (same setup as CLI `ai analyze` command in `src/cli/main.py:2153`)
2. Call `analyzer.run(component_filter=component, force=force, application=application, limit=1)`
3. After analysis completes, fetch and return the result via `ai_repo.get_analysis_by_component()`
4. Return the result in `AnalysisDetails` format (same as `get_analysis`)

### Error handling

- Component not found or no pending failure → return `{"error": "no_failure_found", "message": "..."}`
- Analysis fails (LLM error, timeout) → return `{"error": "analysis_failed", "message": "..."}`

### Timeout

The analysis can take 90-180 seconds (LLM call + enrichment + log fetching). The tool must handle this gracefully. Set tool-level timeout to 300 seconds.

## What does NOT change

- `get_analysis` remains as-is for reading cached results
- CLI `ic ai analyze` unchanged
- `BuildFailureAnalyzer` internals unchanged — we're just calling the existing API

## Testing

- Unit test: analyze_failure calls analyzer with correct parameters
- Unit test: force=True passes through to analyzer
- Unit test: returns AnalysisDetails format
- Unit test: handles missing component gracefully
