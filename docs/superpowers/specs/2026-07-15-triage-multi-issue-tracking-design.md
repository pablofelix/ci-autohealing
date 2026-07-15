# Triage Multi-Issue Tracking

## Context

During RHOAI v3-5 GA triage (Jul 14-15), we hit a limitation: IC can only track ONE active triage item per component. When `odh-workbench-jupyter-pytorch-cuda-py312-v3-5` has both a build failure (hermeto checksum, triage #27) and a FIPS scan issue (RHAIENG-5898), the second issue gets shoehorned into the notes field of the first. Similarly, Slack URL fields accept any URL without validation — a merged GitHub PR URL was stored as a "Slack link", causing confusion during handoff.

These gaps were documented in `v3-5-triage-gaps-and-improvements-2026-07-15.md` as Gaps 3 and 4.

## Changes

### 1. Database migration — `014_triage_issue_type.sql`

Add to `triage_items`:
- `issue_type VARCHAR(50) DEFAULT 'build' CHECK (issue_type IN ('build', 'conforma', 'onboarding', 'release'))`
- `reference_urls TEXT[]`

Backfill: all existing rows get `issue_type = 'build'`.

No columns dropped. `slack_thread_urls` and `jira_key` unchanged.

### 2. Repository — `triage_repository.py`

**`find_by_component(component, application, issue_type='build')`**: Add `issue_type` filter to the WHERE clause. Same component with different issue_type → not blocked.

**`create_item(...)`**: Accept `issue_type` and `reference_urls` params.

**`add_slack_url(item_id, url)`**: Validate `'slack.com/archives/' in url` before inserting. Return `False` if invalid.

**`add_reference_url(item_id, url)`**: New method, same array-append pattern as `add_slack_url`, no domain validation.

**`_row_to_dict(row)`**: Add `issue_type` (index 14) and `reference_urls` (index 15) to the dict. Shift `application` to index 16 in `get_by_id`.

**All SELECT queries**: Add `issue_type` and `reference_urls` to the column list (after `notes`, before `resolution`).

### 3. MCP tools — `tools.py`

**`track_triage_item`**: Add params `issue_type: str = 'build'` and `reference_url: Optional[str] = None`. Pass `issue_type` to `find_by_component`. Pass `reference_urls=[reference_url]` to `create_item` when creating.

**`update_triage_item`**: Add param `reference_url: Optional[str] = None`. Call `repo.add_reference_url(item_id, reference_url)` when provided. On Slack URL validation failure, return `{"error": "URL doesn't look like a Slack thread (expected slack.com/archives/...). Use reference_url for PRs and other links."}`.

### 4. FastAPI routes — `api/routes/triage.py`

**`TrackRequest`**: Add `issue_type: str = 'build'` and `reference_url: Optional[str] = None`.

**`UpdateRequest`**: Add `reference_url: Optional[str] = None`.

Route logic mirrors MCP tool changes.

### 5. Tests

In `test_triage_repo_coverage.py`:

- `_sample_row`: extend to 16 elements (add `issue_type`, `reference_urls`)
- Test `find_by_component` with issue_type filter: same component, different types → not blocked
- Test `create_item` passes `issue_type` and `reference_urls` to SQL
- Test `add_slack_url` rejects non-Slack URL (returns False)
- Test `add_slack_url` accepts valid Slack URL
- Test `add_reference_url` appends URL
- Test `_row_to_dict` maps new fields

In `test_triage_errors.py` or new test file:

- Test MCP `track_triage_item` allows creating two items for same component with different issue_types
- Test MCP `track_triage_item` still blocks same component + same issue_type
- Test MCP `update_triage_item` with invalid Slack URL returns error
- Test backward compat: items created without issue_type default to 'build'

## Files to modify

- `db/migrations/014_triage_issue_type.sql` (new)
- `db/schema.sql` (update CREATE TABLE)
- `src/repositories/triage_repository.py`
- `src/mcp_server/tools.py`
- `src/api/routes/triage.py`
- `src/tests/test_triage_repo_coverage.py`

## Verification

1. Run migration against local DB: `psql -f db/migrations/014_triage_issue_type.sql`
2. Run tests: `python3 -m pytest src/tests/test_triage_repo_coverage.py -x -q --tb=short`
3. Start MCP server, test:
   - `track_triage_item(component="test-comp", issue_type="build")` → created
   - `track_triage_item(component="test-comp", issue_type="conforma")` → created (not blocked)
   - `track_triage_item(component="test-comp", issue_type="build")` → exists (blocked, same type)
   - `update_triage_item(item_id=X, slack_thread_url="https://github.com/...")` → error about Slack validation
   - `update_triage_item(item_id=X, reference_url="https://github.com/...")` → updated
4. Full test suite: `python3 -m pytest src/tests/ -x -q --tb=short`
