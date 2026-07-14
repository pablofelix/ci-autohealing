# Release Readiness: Jira Checks + K8s DB Fallback

## Problem

The release readiness endpoint (`/applications/{app}/readiness`) has two systemic gaps
identified during the RHOAI 3.5 EA2 release (Jul 14):

1. **No Jira awareness**: Readiness checks don't surface Jira blockers or Jira API health.
   When the Jira token expired on Jun 19, IC silently returned 0 blockers while 50+ existed.
   The `JiraBlockerQuery` builder and `categorize_blocker()` already exist but aren't wired
   into readiness.

2. **K8s-dependent checks SKIP without fallback**: 12 of 21 readiness checks return SKIP
   when the K8s cluster is unreachable (VPN down, kubeconfig stale). The DB already stores
   component health data (`component_health` table) that can serve as a degraded-but-useful
   fallback for 3 key checks.

## Scope

### In scope

- Add `_check_jira_health()` readiness check (GAP 4)
- Add `_check_blockers()` readiness check with category breakdown (GAPs 2, 5)
- Add DB fallback path in `_check_fbc_health()` (GAP 8)
- Add DB fallback path in `_check_stale_components()` (GAP 8)
- Add DB fallback path in `_check_snapshot_freshness()` (GAP 8)
- Add `jira_blockers` and `jira_health` fields to readiness response
- Tests for all new code paths

### Out of scope

- Dedicated sign-off tracking system (follow-up)
- Upstream auto-merge failure tracking (different system — GHA, not Konflux)
- Release bot health monitoring (meta-monitoring, follow-up)
- Code freeze exception tracking (follow-up)

## Design

### New check: `_check_jira_health()`

```python
def _check_jira_health():
    """Check Jira API token validity."""
    jira = JiraClient(
        base_url=JIRA_BASE_URL,
        email=os.environ.get('JIRA_EMAIL', ''),
        token=os.environ.get('JIRA_TOKEN', ''),
        project=JIRA_PROJECT,
    )
    health = jira.check_token_health()
    status_map = {
        'valid': 'PASS',
        'expired': 'FAIL',
        'forbidden': 'FAIL',
        'missing': 'WARN',
        'unreachable': 'WARN',
    }
    return {
        'name': 'Jira API health',
        'phase': 'pre-release',
        'status': status_map.get(health['status'], 'SKIP'),
        'detail': health['message'],
        'fix': 'Regenerate token at Jira > Profile > Personal Access Tokens'
               if health['status'] in ('expired', 'forbidden') else None,
    }
```

### New check: `_check_blockers(application)`

```python
def _check_blockers(application):
    """Check Jira release blockers with category breakdown."""
    jira = JiraClient(...)
    jql = JiraBlockerQuery().for_application(application).build()
    raw = jira.search_blockers(jql_override=jql)

    open_blockers = [b for b in raw if b.get('status_category') != 'done']
    categories = {}
    for b in open_blockers:
        cat = categorize_blocker(b.get('summary', ''), b.get('labels', []))
        categories[cat] = categories.get(cat, 0) + 1

    if not open_blockers:
        return {
            'name': 'Jira release blockers',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': '0 open release blockers',
            'fix': None,
            'blocker_summary': {'total': 0, 'open': 0, 'by_category': {}},
        }

    parts = ['{} {}'.format(v, k) for k, v in sorted(categories.items(),
             key=lambda x: -x[1])]
    return {
        'name': 'Jira release blockers',
        'phase': 'pre-release',
        'status': 'FAIL',
        'detail': '{} open blocker(s): {}'.format(len(open_blockers), ', '.join(parts)),
        'fix': 'Triage blockers: ic list blockers {}'.format(application),
        'blocker_summary': {
            'total': len(raw),
            'open': len(open_blockers),
            'by_category': categories,
        },
    }
```

The `blocker_summary` dict is extracted from the check result and placed in the
top-level readiness response as `jira_blockers`.

### DB fallback: `_check_fbc_health()`

Current behavior when `k8s_reachable=False`: returns SKIP immediately.

New behavior: query `component_health` table for the FBC fragment component.

```python
if not k8s_reachable:
    # DB fallback — component_health stores nightly build status
    fbc_name = _nightly_component_for_app(application)
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT current_status, health_score, last_failed_build, last_successful_build
            FROM component_health
            WHERE component_name = %s
        """, (fbc_name,))
        row = cursor.fetchone()

    if not row:
        return {'name': 'FBC fragment health', 'phase': 'pre-release',
                'status': 'SKIP', 'detail': 'No DB data for FBC fragment',
                'fix': 'Verify VPN/kubeconfig and retry'}

    current_status, score, last_fail, last_success = row
    if current_status == 'Failed':
        return {'name': 'FBC fragment health', 'phase': 'pre-release',
                'status': 'FAIL',
                'detail': 'FBC fragment last build failed (from DB — cluster unreachable)',
                'fix': 'Check FBC fragment build logs: ic describe {}'.format(fbc_name)}

    return {'name': 'FBC fragment health', 'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'FBC fragment healthy, score {} (from DB — cluster unreachable)'.format(
                score or 'N/A'),
            'fix': None}
```

### DB fallback: `_check_stale_components()`

Current behavior when `k8s_reachable=False`: returns SKIP immediately.

New behavior: query `component_health` for all components in the application
with `repository_url IS NOT NULL`, then compare the last built commit from the
`build_failures` table against GitHub branch HEAD.

```python
if not k8s_reachable:
    # DB fallback — use component_health + build_failures for commit info
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT ON (bf.component_name)
                bf.component_name, bf.repository_url, bf.branch, bf.commit_sha
            FROM build_failures bf
            WHERE bf.application = %s
              AND bf.repository_url IS NOT NULL
              AND bf.branch IS NOT NULL
              AND bf.commit_sha IS NOT NULL
            ORDER BY bf.component_name, bf.first_detected_at DESC
        """, (application,))
        components = [dict(zip([d[0] for d in cursor.description], row))
                      for row in cursor.fetchall()]

    if not components:
        return {'name': 'Stale components', 'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No component build history in DB',
                'fix': 'Verify VPN/kubeconfig and retry'}

    # Compare with GitHub HEAD using GitHubClient
    gh = GitHubClient(token=os.environ.get('GITHUB_TOKEN', ''))
    stale = []
    for comp in components:
        parsed = parse_github_repo(comp['repository_url'])
        if not parsed:
            continue
        try:
            head = gh.get_ref_sha(parsed[0], parsed[1], comp['branch'])
        except Exception:
            continue
        if head and comp['commit_sha'] and head[:12] != comp['commit_sha'][:12]:
            stale.append(comp['component_name'])

    if stale:
        return {'name': 'Stale components', 'phase': 'pre-release',
                'status': 'WARN',
                'detail': '{} component(s) with newer commits (from DB — cluster unreachable): {}'.format(
                    len(stale), ', '.join(stale[:5])),
                'fix': 'Run ic get stale for details'}

    return {'name': 'Stale components', 'phase': 'pre-release',
            'status': 'PASS',
            'detail': '{} components checked (from DB — cluster unreachable)'.format(
                len(components)),
            'fix': None}
```

The DB fallback passes `db` (the `DatabaseConnection` already created in
`_run_readiness_checks`) to the check functions. The function signatures change to
accept an optional `db` parameter.

### DB fallback: `_check_snapshot_freshness()`

Current behavior when `k8s_reachable=False`: returns SKIP.

New behavior: use `component_health.last_successful_build` timestamps as a proxy
for freshness. If any component's last success is >24h old, that's a staleness
signal.

```python
if not k8s_reachable:
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT component_name, last_successful_build
            FROM component_health
            WHERE application = %s
              AND last_successful_build IS NOT NULL
            ORDER BY last_successful_build ASC
            LIMIT 5
        """, (application,))
        rows = cursor.fetchall()

    if not rows:
        return {'name': 'Snapshot freshness', 'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No build history in DB',
                'fix': 'Verify VPN/kubeconfig and retry'}

    oldest_name, oldest_ts = rows[0]
    from datetime import datetime, timezone
    age_hours = (datetime.now(timezone.utc) - oldest_ts).total_seconds() / 3600
    if age_hours > 24:
        return {'name': 'Snapshot freshness', 'phase': 'pre-release',
                'status': 'WARN',
                'detail': '{} oldest successful build {:.0f}h ago (from DB — cluster unreachable)'.format(
                    oldest_name, age_hours),
                'fix': 'Trigger rebuild or check for build failures'}

    return {'name': 'Snapshot freshness', 'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'All components built within 24h (from DB — cluster unreachable)',
            'fix': None}
```

### Response structure changes

The readiness response gains two new top-level fields:

```python
{
    # ... existing fields ...
    'jira_blockers': {
        'total': 12,
        'open': 10,
        'by_category': {'product': 5, 'tfa': 4, 'signoff': 2, 'infra': 1},
    },
    'jira_health': 'valid',  # from check_token_health().status
}
```

The `_check_blockers()` result includes a `blocker_summary` key that gets
extracted and placed in the response. The `_check_jira_health()` result's
detail is used to derive `jira_health`.

### Wiring into `_run_readiness_checks()`

Add to `check_fns` list (pre-release section):

```python
lambda: _check_jira_health(),
lambda: _check_blockers(application),
```

Add to `check_names` list:

```python
'Jira API health', 'Jira release blockers',
```

Modify existing check lambdas to pass `db`:

```python
lambda: _check_fbc_health(monitor, application, k8s_reachable, db),
lambda: _check_stale_components(monitor, application, k8s_reachable, db),
lambda: _check_snapshot_freshness(monitor, application, k8s_reachable, db),
```

### Blocker impact on verdict

When `_check_blockers()` returns FAIL, it adds to `blockers` list, which
makes verdict = NOT_READY. This is correct — open Jira release blockers
should block the release.

When `_check_jira_health()` returns FAIL (expired token), it also adds to
`blockers` because without Jira we can't trust the blocker count.

## Files changed

- `src/api/routes/releases.py` — add 2 new check functions, modify 3 existing
  check functions with DB fallback, wire into `_run_readiness_checks()` and
  `get_readiness()`, extract jira fields into response
- `src/tests/test_route_releases.py` (or new test file) — 12 test cases

## Test plan

### Jira health check tests
1. `test_check_jira_health_valid` — token valid → PASS
2. `test_check_jira_health_expired` — HTTP 401 → FAIL
3. `test_check_jira_health_missing` — no token → WARN
4. `test_check_jira_health_unreachable` — connection error → WARN

### Blocker check tests
5. `test_check_blockers_with_results` — 3 blockers (2 product, 1 tfa) → FAIL with breakdown
6. `test_check_blockers_none` — 0 blockers → PASS
7. `test_check_blockers_jira_unavailable` — exception → SKIP

### DB fallback tests
8. `test_fbc_health_db_fallback_pass` — k8s_reachable=False, DB says Success → PASS with "(from DB)" suffix
9. `test_fbc_health_db_fallback_fail` — k8s_reachable=False, DB says Failed → FAIL with "(from DB)" suffix
10. `test_stale_components_db_fallback` — k8s_reachable=False, DB has components, GitHub shows newer commits → WARN
11. `test_snapshot_freshness_db_fallback_stale` — k8s_reachable=False, oldest build >24h → WARN
12. `test_snapshot_freshness_db_fallback_fresh` — k8s_reachable=False, all builds <24h → PASS

All tests mock JiraClient, DB connections, and GitHubClient — no real API calls.
