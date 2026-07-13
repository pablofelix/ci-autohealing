# Nightly Build Chain View — Design Spec

## Goal

Replace the disconnected sections in `ic nightly` with a unified chain timeline that shows the nightly build as 4 sequential steps: GHA Trigger → Operator Build → FBC Fragment → PCC Cache. Make it immediately obvious where the chain broke.

## Architecture

Pure presentation logic on top of existing data. No new DB tables, no new API endpoints.

### Chain Correlation

`_correlate_nightly_chain(status, history)` takes existing `get_nightly_status()` + `get_nightly_history()` results and returns an ordered list of chain steps.

**Correlation heuristic:** nightly builds (trigger_type='nightly') that started within 2 hours of GHA trigger completion belong to the same chain run. The 2-hour window accounts for build queue delays.

**Chain steps:**

1. **GHA Trigger** — from `status['gha_validation']`. Status: success/failure/skip.
2. **Operator Build** — from `history['nightly_builds']`, filtered to operator components (excludes FBC fragment). Status: success if all passed, failure if any failed.
3. **FBC Fragment** — from `status['fbc_health']` + `history['nightly_builds']` filtered to FBC component. Status: success/failure based on last build.
4. **PCC Cache** — from `status['pcc_freshness']`. Status: fresh=pass, stale=fail, unknown=skip.

Each step returns: `name`, `status` (pass/fail/skip), `timestamp` (ISO string or None), `detail` (human-readable string), `url` (optional link).

### Graceful Degradation

- No GitHub token → step 1 shows SKIP, steps 2-4 still shown
- No nightly builds in window → steps 2-3 show SKIP with "no recent nightly builds"
- PCC check unavailable → step 4 shows SKIP

### Blocker Attribution

When step 3 (FBC) fails, the chain view lists blocker components inline. These come from `status['blockers']` — components whose build failure prevents FBC from succeeding.

## CLI Output

Default `ic nightly` output becomes the chain view:

```
Nightly Chain (last run: 2026-07-13):

  1. GHA Trigger     ✓ success  00:15 UTC  (trigger-nightlies.yaml)
  2. Operator Build   ✓ success  00:42 UTC  (3/3 passed)
  3. FBC Fragment     ✗ failed   01:18 UTC  (rhoai-fbc-fragment-v3-5)
     └─ 2 blockers: odh-trustyai-service-v3-5, odh-vllm-cpu-v3-5
  4. PCC Cache        ✓ fresh    (12 versions cached)

GHA: https://github.com/red-hat-data-services/rhods-devops-infra/actions/runs/...
```

The existing detail sections (blockers list with root causes, conforma violations, PCC missing versions) remain below the chain summary — the chain is a header, not a replacement.

`--json` flag returns the chain steps as a structured array alongside the existing nightly status data.

## MCP Tool

New `get_nightly_chain(application)` tool. Returns:

```json
{
  "application": "rhoai-v3-5",
  "chain_date": "2026-07-13",
  "steps": [
    {"name": "GHA Trigger", "status": "pass", "timestamp": "2026-07-13T00:15:00Z", "detail": "trigger-nightlies.yaml succeeded", "url": "https://..."},
    {"name": "Operator Build", "status": "pass", "timestamp": "2026-07-13T00:42:00Z", "detail": "3/3 operator builds passed"},
    {"name": "FBC Fragment", "status": "fail", "timestamp": "2026-07-13T01:18:00Z", "detail": "rhoai-fbc-fragment-v3-5 build failed", "blockers": ["odh-trustyai-service-v3-5"]},
    {"name": "PCC Cache", "status": "pass", "timestamp": null, "detail": "12 versions cached, fresh"}
  ],
  "chain_status": "broken",
  "break_point": "FBC Fragment"
}
```

`chain_status`: "healthy" (all pass), "broken" (any fail), "unknown" (all skip).
`break_point`: name of first failing step, or null if healthy.

## Files Modified

- `src/proactive/health_monitor.py` — add `get_nightly_chain(application)` method
- `src/cli/main.py` — update `nightly` command display to use chain view
- `src/mcp_server/tools.py` — add `get_nightly_chain` MCP tool
- `src/tests/test_nightly_chain.py` — new test file for chain correlation logic

## Out of Scope

- Slack build notification parsing (blocked — no data in Neo4j, no Slack token for ic)
- Map UI chain visualization (separate task)
- Historical chain view (ic nightly-history already covers build history)
- New API endpoint (MCP tool calls health_monitor directly, same as existing pattern)
