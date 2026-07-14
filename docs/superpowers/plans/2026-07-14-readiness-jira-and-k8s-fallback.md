# Release Readiness: Jira Checks + K8s DB Fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Jira health + blocker checks to release readiness, and add DB-backed fallback for 3 K8s-dependent checks so they produce useful results when the cluster is unreachable.

**Architecture:** Two new readiness check functions (`_check_jira_health`, `_check_blockers`) are added to the existing parallel check infrastructure. Three existing check functions (`_check_fbc_health`, `_check_stale_components`, `_check_snapshot_freshness`) gain DB fallback paths in their `if not k8s_reachable:` blocks. A `DatabaseConnection` reference (already instantiated in `_run_readiness_checks`) is passed to the check functions that need it.

**Tech Stack:** Python, FastAPI, PostgreSQL, unittest.mock

## Global Constraints

- All new code must have tests. TDD: write tests first.
- Check functions follow the existing pattern: return `{name, phase, status, detail, fix}`.
- Status values: PASS, FAIL, WARN, SKIP (no others).
- DB fallback results must suffix detail with `"(from DB — cluster unreachable)"` for transparency.
- Jira checks must gracefully degrade: if Jira is unavailable, return SKIP (not crash).
- No `oc` commands — all data comes from API/DB.
- Tests must mock all external calls (Jira, DB, GitHub). No real API calls.

---

### Task 1: Add `_check_jira_health()` and `_check_blockers()` checks

**Files:**
- Modify: `src/api/routes/releases.py:414-461` (add to check_fns list and check_names)
- Modify: `src/api/routes/releases.py:292-361` (extract jira fields into response)
- Modify: `src/api/routes/releases.py` (add two new check functions near line 499)
- Test: `src/tests/test_route_releases.py`

**Interfaces:**
- Consumes: `JiraClient.check_token_health()` from `src/clients/jira_client.py:42`
- Consumes: `JiraClient.search_blockers(jql_override=...)` from `src/clients/jira_client.py:256`
- Consumes: `JiraBlockerQuery` from `src/clients/jira_query_builder.py:51`
- Consumes: `categorize_blocker()` from `src/clients/jira_query_builder.py:109`
- Consumes: `JIRA_BASE_URL`, `JIRA_PROJECT` from `src/shared_config.py`
- Produces: Two new check result dicts in the `checks` list
- Produces: `jira_blockers` dict and `jira_health` string in readiness response

- [ ] **Step 1: Write failing tests for `_check_jira_health()`**

Add to `src/tests/test_route_releases.py`:

```python
from api.routes.releases import _check_jira_health, _check_blockers


def test_check_jira_health_valid():
    """Jira health check returns PASS when token is valid."""
    with patch('api.routes.releases.JiraClient') as MockJira:
        MockJira.return_value.check_token_health.return_value = {
            'status': 'valid',
            'user': 'test-user',
            'message': 'Token valid, authenticated as test-user',
        }
        result = _check_jira_health()

    assert result['name'] == 'Jira API health'
    assert result['phase'] == 'pre-release'
    assert result['status'] == 'PASS'
    assert 'valid' in result['detail'].lower()


def test_check_jira_health_expired():
    """Jira health check returns FAIL when token is expired."""
    with patch('api.routes.releases.JiraClient') as MockJira:
        MockJira.return_value.check_token_health.return_value = {
            'status': 'expired',
            'user': None,
            'message': 'Jira token expired or invalid (HTTP 401)',
        }
        result = _check_jira_health()

    assert result['status'] == 'FAIL'
    assert result['fix'] is not None
    assert 'regenerate' in result['fix'].lower() or 'token' in result['fix'].lower()


def test_check_jira_health_missing():
    """Jira health check returns WARN when no token configured."""
    with patch('api.routes.releases.JiraClient') as MockJira:
        MockJira.return_value.check_token_health.return_value = {
            'status': 'missing',
            'user': None,
            'message': 'JIRA_TOKEN environment variable is not set',
        }
        result = _check_jira_health()

    assert result['status'] == 'WARN'


def test_check_jira_health_unreachable():
    """Jira health check returns WARN when Jira API is unreachable."""
    with patch('api.routes.releases.JiraClient') as MockJira:
        MockJira.return_value.check_token_health.return_value = {
            'status': 'unreachable',
            'user': None,
            'message': 'Jira API unreachable: connection refused',
        }
        result = _check_jira_health()

    assert result['status'] == 'WARN'
```

- [ ] **Step 2: Write failing tests for `_check_blockers()`**

```python
def test_check_blockers_with_results():
    """Blocker check returns FAIL with category breakdown when blockers exist."""
    with patch('api.routes.releases.JiraClient') as MockJira:
        MockJira.return_value.search_blockers.return_value = [
            {'key': 'RHOAIENG-100', 'summary': 'Build fails', 'status': 'Open',
             'status_category': 'new', 'labels': []},
            {'key': 'RHOAIENG-101', 'summary': 'TFA: test failure analysis',
             'status': 'Open', 'status_category': 'new', 'labels': []},
            {'key': 'RHOAIENG-102', 'summary': 'Product Sign Off needed',
             'status': 'Closed', 'status_category': 'done', 'labels': []},
        ]
        result = _check_blockers('rhoai-v3-5-ea-2')

    assert result['status'] == 'FAIL'
    assert '2 open blocker' in result['detail']
    summary = result['blocker_summary']
    assert summary['total'] == 3
    assert summary['open'] == 2
    assert summary['by_category']['product'] == 1
    assert summary['by_category']['tfa'] == 1


def test_check_blockers_none():
    """Blocker check returns PASS when no blockers found."""
    with patch('api.routes.releases.JiraClient') as MockJira:
        MockJira.return_value.search_blockers.return_value = []
        result = _check_blockers('rhoai-v3-5-ea-2')

    assert result['status'] == 'PASS'
    assert '0' in result['detail']
    assert result['blocker_summary']['total'] == 0


def test_check_blockers_jira_unavailable():
    """Blocker check returns SKIP when Jira is unavailable."""
    with patch('api.routes.releases.JiraClient') as MockJira:
        MockJira.return_value.search_blockers.side_effect = Exception('connection refused')
        result = _check_blockers('rhoai-v3-5-ea-2')

    assert result['status'] == 'SKIP'
    assert 'blocker_summary' not in result or result.get('blocker_summary') is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest src/tests/test_route_releases.py -x -q --tb=short -k "jira_health or check_blockers"`
Expected: FAIL — `_check_jira_health` and `_check_blockers` not yet defined.

- [ ] **Step 4: Implement `_check_jira_health()` and `_check_blockers()`**

Add imports at top of `src/api/routes/releases.py` (near existing imports):

```python
from shared_config import JIRA_BASE_URL, JIRA_PROJECT
```

Add two new functions after the existing `_check_pcc_post_push()` function (around line 1100, in the pre-release checks section):

```python
def _check_jira_health():
    """Check Jira API token validity."""
    try:
        from clients.jira_client import JiraClient
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
        fix = None
        if health['status'] in ('expired', 'forbidden'):
            fix = 'Regenerate token: Jira > Profile > Personal Access Tokens'
        return {
            'name': 'Jira API health',
            'phase': 'pre-release',
            'status': status_map.get(health['status'], 'SKIP'),
            'detail': health['message'],
            'fix': fix,
        }
    except Exception as exc:
        logger.debug("Jira health check failed: %s", exc)
        return {
            'name': 'Jira API health',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_blockers(application):
    """Check Jira release blockers with category breakdown."""
    try:
        from clients.jira_client import JiraClient
        from clients.jira_query_builder import JiraBlockerQuery, categorize_blocker

        jira = JiraClient(
            base_url=JIRA_BASE_URL,
            email=os.environ.get('JIRA_EMAIL', ''),
            token=os.environ.get('JIRA_TOKEN', ''),
            project=JIRA_PROJECT,
        )
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
                'blocker_summary': {'total': len(raw), 'open': 0, 'by_category': {}},
            }

        parts = ['{} {}'.format(v, k) for k, v in sorted(
            categories.items(), key=lambda x: -x[1])]
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
    except Exception as exc:
        logger.debug("Blocker check failed: %s", exc)
        return {
            'name': 'Jira release blockers',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }
```

- [ ] **Step 5: Wire new checks into `_run_readiness_checks()`**

In `_run_readiness_checks()`, add to `check_fns` list (after the existing pre-release checks, before post-stage):

```python
lambda: _check_jira_health(),
lambda: _check_blockers(application),
```

Add to `check_names` list in the corresponding position:

```python
'Jira API health', 'Jira release blockers',
```

- [ ] **Step 6: Extract Jira fields into readiness response**

In `get_readiness()`, after the checks loop, extract the blocker summary and jira health:

```python
jira_blockers = None
jira_health = None
for check in checks:
    if check['name'] == 'Jira release blockers' and 'blocker_summary' in check:
        jira_blockers = check.pop('blocker_summary')
    if check['name'] == 'Jira API health':
        for status_word in ('valid', 'expired', 'forbidden', 'missing', 'unreachable'):
            if status_word in check['detail'].lower():
                jira_health = status_word
                break
```

Add to the return dict:

```python
'jira_blockers': jira_blockers,
'jira_health': jira_health,
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest src/tests/test_route_releases.py -x -q --tb=short -k "jira_health or check_blockers"`
Expected: 7 PASS

- [ ] **Step 8: Commit**

```bash
git add src/api/routes/releases.py src/tests/test_route_releases.py
git commit -m "feat: add Jira health + blocker checks to release readiness"
```

---

### Task 2: Add DB fallback for `_check_fbc_health()`

**Files:**
- Modify: `src/api/routes/releases.py:502-549` (`_check_fbc_health` function)
- Modify: `src/api/routes/releases.py:416` (pass `db` to check lambda)
- Test: `src/tests/test_route_releases.py`

**Interfaces:**
- Consumes: `db` (`DatabaseConnection`) from `_run_readiness_checks`, already instantiated at line 373
- Consumes: `_nightly_component_for_app(application)` from `proactive.health_monitor`
- Consumes: `component_health` table columns: `component_name`, `current_status`, `health_score`, `last_failed_build`, `last_successful_build`
- Produces: PASS/FAIL result with `"(from DB — cluster unreachable)"` suffix instead of SKIP

- [ ] **Step 1: Write failing tests for DB fallback**

Add to `src/tests/test_route_releases.py`:

```python
def test_fbc_health_db_fallback_pass():
    """FBC health uses DB fallback when K8s unreachable, build healthy."""
    monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    mock_cursor.fetchone.return_value = ('Succeeded', 100, None, datetime.now())

    with patch('api.routes.releases._nightly_component_for_app', return_value='odh-fbc-fragment-v3-5'):
        result = _check_fbc_health(monitor, 'rhoai-v3-5', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'PASS'
    assert 'from DB' in result['detail']


def test_fbc_health_db_fallback_fail():
    """FBC health uses DB fallback when K8s unreachable, build failed."""
    monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    mock_cursor.fetchone.return_value = ('Failed', 50, datetime.now(), None)

    with patch('api.routes.releases._nightly_component_for_app', return_value='odh-fbc-fragment-v3-5'):
        result = _check_fbc_health(monitor, 'rhoai-v3-5', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'FAIL'
    assert 'from DB' in result['detail']
    assert 'failed' in result['detail'].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest src/tests/test_route_releases.py -x -q --tb=short -k "fbc_health_db_fallback"`
Expected: FAIL — `_check_fbc_health` doesn't accept `db` parameter yet.

- [ ] **Step 3: Implement DB fallback in `_check_fbc_health()`**

Change signature from `def _check_fbc_health(monitor, application, k8s_reachable=True):`
to `def _check_fbc_health(monitor, application, k8s_reachable=True, db=None):`.

Replace the `if not k8s_reachable:` block (lines 505-512) with:

```python
if not k8s_reachable:
    if db:
        try:
            from proactive.health_monitor import _nightly_component_for_app
            fbc_name = _nightly_component_for_app(application)
            with db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT current_status, health_score, last_failed_build, last_successful_build
                    FROM component_health
                    WHERE component_name = %s
                """, (fbc_name,))
                row = cursor.fetchone()
            if row:
                current_status, score, last_fail, last_success = row
                if current_status == 'Failed':
                    return {
                        'name': 'FBC fragment health',
                        'phase': 'pre-release',
                        'status': 'FAIL',
                        'detail': 'FBC fragment last build failed (from DB — cluster unreachable)',
                        'fix': 'Check FBC fragment build logs: ic describe {}'.format(fbc_name),
                    }
                return {
                    'name': 'FBC fragment health',
                    'phase': 'pre-release',
                    'status': 'PASS',
                    'detail': 'FBC fragment healthy, score {} (from DB — cluster unreachable)'.format(
                        score or 'N/A'),
                    'fix': None,
                }
        except Exception as exc:
            logger.debug("FBC health DB fallback failed: %s", exc)
    return {
        'name': 'FBC fragment health',
        'phase': 'pre-release',
        'status': 'SKIP',
        'detail': 'K8s cluster unreachable, no DB fallback available',
        'fix': 'Verify VPN/kubeconfig and retry',
    }
```

Update the lambda in `_run_readiness_checks()` from:
`lambda: _check_fbc_health(monitor, application, k8s_reachable),`
to:
`lambda: _check_fbc_health(monitor, application, k8s_reachable, db),`

- [ ] **Step 4: Verify existing tests still pass (no regression)**

Run: `PYTHONPATH=src python3 -m pytest src/tests/test_route_releases.py -x -q --tb=short -k "fbc_health"`
Expected: All existing + new tests pass. The existing `test_check_fbc_health_k8s_unreachable` still passes because `db` defaults to `None`.

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/releases.py src/tests/test_route_releases.py
git commit -m "feat: add DB fallback for FBC health check when K8s unreachable"
```

---

### Task 3: Add DB fallback for `_check_stale_components()`

**Files:**
- Modify: `src/api/routes/releases.py:643-682` (`_check_stale_components` function)
- Modify: `src/api/routes/releases.py:419` (pass `db` to check lambda)
- Test: `src/tests/test_route_releases.py`

**Interfaces:**
- Consumes: `db` (`DatabaseConnection`) from `_run_readiness_checks`
- Consumes: `build_failures` table columns: `component_name`, `repository_url`, `branch`, `commit_sha`, `first_detected_at`
- Consumes: `GitHubClient.get_ref_sha(owner, repo, branch)` from `src/clients/github_client.py`
- Consumes: `parse_github_repo(url)` from `src/clients/github_client.py`
- Produces: WARN/PASS result with `"(from DB — cluster unreachable)"` suffix instead of SKIP

- [ ] **Step 1: Write failing test for stale components DB fallback**

```python
def test_stale_components_db_fallback():
    """Stale components uses DB fallback when K8s unreachable."""
    monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(
        return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    mock_cursor.description = [('component_name',), ('repository_url',), ('branch',), ('commit_sha',)]
    mock_cursor.fetchall.return_value = [
        ('comp-a', 'https://github.com/org/repo-a', 'main', 'aaa111'),
        ('comp-b', 'https://github.com/org/repo-b', 'main', 'bbb222'),
    ]

    with patch('api.routes.releases.GitHubClient') as MockGH, \
         patch('api.routes.releases.parse_github_repo', side_effect=[('org', 'repo-a'), ('org', 'repo-b')]):
        gh_instance = MockGH.return_value
        gh_instance.get_ref_sha.side_effect = ['ccc333', 'bbb222']  # comp-a stale, comp-b fresh

        result = _check_stale_components(monitor, 'rhoai-v3-5-ea-2', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'WARN'
    assert 'from DB' in result['detail']
    assert 'comp-a' in result['detail']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest src/tests/test_route_releases.py -x -q --tb=short -k "stale_components_db_fallback"`
Expected: FAIL

- [ ] **Step 3: Implement DB fallback in `_check_stale_components()`**

Change signature from `def _check_stale_components(monitor, application, k8s_reachable=True):`
to `def _check_stale_components(monitor, application, k8s_reachable=True, db=None):`.

Replace the `if not k8s_reachable:` block (lines 646-652) with:

```python
if not k8s_reachable:
    if db:
        try:
            from clients.github_client import GitHubClient, parse_github_repo
            with db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT ON (component_name)
                        component_name, repository_url, branch, commit_sha
                    FROM build_failures
                    WHERE application = %s
                      AND repository_url IS NOT NULL
                      AND branch IS NOT NULL
                      AND commit_sha IS NOT NULL
                    ORDER BY component_name, first_detected_at DESC
                """, (application,))
                cols = [d[0] for d in cursor.description]
                components = [dict(zip(cols, row)) for row in cursor.fetchall()]
            if not components:
                return {
                    'name': 'Stale components',
                    'phase': 'pre-release',
                    'status': 'SKIP',
                    'detail': 'No component build history in DB',
                    'fix': 'Verify VPN/kubeconfig and retry',
                }
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
                return {
                    'name': 'Stale components',
                    'phase': 'pre-release',
                    'status': 'WARN',
                    'detail': '{} component(s) with newer commits (from DB — cluster unreachable): {}'.format(
                        len(stale), ', '.join(stale[:5])),
                    'fix': 'Run ic get stale for details; trigger builds or verify non-functional commits',
                }
            return {
                'name': 'Stale components',
                'phase': 'pre-release',
                'status': 'PASS',
                'detail': '{} components checked (from DB — cluster unreachable)'.format(
                    len(components)),
                'fix': None,
            }
        except Exception as exc:
            logger.debug("Stale components DB fallback failed: %s", exc)
    return {
        'name': 'Stale components',
        'phase': 'pre-release',
        'status': 'SKIP',
        'detail': 'K8s cluster unreachable, no DB fallback available',
        'fix': 'Verify VPN/kubeconfig and retry',
    }
```

Update the lambda in `_run_readiness_checks()` from:
`lambda: _check_stale_components(monitor, application, k8s_reachable),`
to:
`lambda: _check_stale_components(monitor, application, k8s_reachable, db),`

- [ ] **Step 4: Verify all stale tests pass**

Run: `PYTHONPATH=src python3 -m pytest src/tests/test_route_releases.py -x -q --tb=short -k "stale"`
Expected: All existing + new tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/releases.py src/tests/test_route_releases.py
git commit -m "feat: add DB fallback for stale components check when K8s unreachable"
```

---

### Task 4: Add DB fallback for `_check_snapshot_freshness()`

**Files:**
- Modify: `src/api/routes/releases.py:685-724` (`_check_snapshot_freshness` function)
- Modify: `src/api/routes/releases.py:420` (pass `db` to check lambda)
- Test: `src/tests/test_route_releases.py`

**Interfaces:**
- Consumes: `db` (`DatabaseConnection`) from `_run_readiness_checks`
- Consumes: `component_health` table columns: `component_name`, `last_successful_build`, `application`
- Produces: WARN/PASS result with `"(from DB — cluster unreachable)"` suffix instead of SKIP

- [ ] **Step 1: Write failing tests for snapshot freshness DB fallback**

```python
from datetime import timezone


def test_snapshot_freshness_db_fallback_stale():
    """Snapshot freshness uses DB fallback when K8s unreachable, oldest build >24h."""
    monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(
        return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    old_time = datetime.now(timezone.utc) - timedelta(hours=30)
    mock_cursor.fetchall.return_value = [
        ('old-component', old_time),
        ('fresh-component', datetime.now(timezone.utc)),
    ]

    result = _check_snapshot_freshness(monitor, 'rhoai-v3-5-ea-2', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'WARN'
    assert 'from DB' in result['detail']
    assert 'old-component' in result['detail']


def test_snapshot_freshness_db_fallback_fresh():
    """Snapshot freshness uses DB fallback when K8s unreachable, all builds <24h."""
    monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(
        return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    recent_time = datetime.now(timezone.utc) - timedelta(hours=2)
    mock_cursor.fetchall.return_value = [
        ('comp-a', recent_time),
        ('comp-b', datetime.now(timezone.utc)),
    ]

    result = _check_snapshot_freshness(monitor, 'rhoai-v3-5-ea-2', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'PASS'
    assert 'from DB' in result['detail']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest src/tests/test_route_releases.py -x -q --tb=short -k "snapshot_freshness_db_fallback"`
Expected: FAIL

- [ ] **Step 3: Implement DB fallback in `_check_snapshot_freshness()`**

Change signature from `def _check_snapshot_freshness(monitor, application, k8s_reachable=True):`
to `def _check_snapshot_freshness(monitor, application, k8s_reachable=True, db=None):`.

Replace the `if not k8s_reachable:` block (lines 688-694) with:

```python
if not k8s_reachable:
    if db:
        try:
            from datetime import timezone
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
            if rows:
                oldest_name, oldest_ts = rows[0]
                if oldest_ts.tzinfo is None:
                    oldest_ts = oldest_ts.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - oldest_ts).total_seconds() / 3600
                if age_hours > 24:
                    return {
                        'name': 'Snapshot freshness',
                        'phase': 'pre-release',
                        'status': 'WARN',
                        'detail': '{} oldest successful build {:.0f}h ago (from DB — cluster unreachable)'.format(
                            oldest_name, age_hours),
                        'fix': 'Trigger rebuild or check for build failures',
                    }
                return {
                    'name': 'Snapshot freshness',
                    'phase': 'pre-release',
                    'status': 'PASS',
                    'detail': 'All components built within 24h (from DB — cluster unreachable)',
                    'fix': None,
                }
        except Exception as exc:
            logger.debug("Snapshot freshness DB fallback failed: %s", exc)
    return {
        'name': 'Snapshot freshness',
        'phase': 'pre-release',
        'status': 'SKIP',
        'detail': 'K8s cluster unreachable, no DB fallback available',
        'fix': 'Verify VPN/kubeconfig and retry',
    }
```

Update the lambda in `_run_readiness_checks()` from:
`lambda: _check_snapshot_freshness(monitor, application, k8s_reachable),`
to:
`lambda: _check_snapshot_freshness(monitor, application, k8s_reachable, db),`

- [ ] **Step 4: Verify all snapshot tests pass**

Run: `PYTHONPATH=src python3 -m pytest src/tests/test_route_releases.py -x -q --tb=short -k "snapshot_freshness"`
Expected: All existing + new tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/releases.py src/tests/test_route_releases.py
git commit -m "feat: add DB fallback for snapshot freshness check when K8s unreachable"
```

---

### Task 5: Full regression test

**Files:**
- No new files

- [ ] **Step 1: Run full release route test suite**

Run: `PYTHONPATH=src python3 -m pytest src/tests/test_route_releases.py -x -q --tb=short`
Expected: All tests pass (existing + 12 new)

- [ ] **Step 2: Run full project test suite**

Run: `PYTHONPATH=src python3 -m pytest src/tests/ -x -q --tb=short`
Expected: All tests pass, no regressions.

- [ ] **Step 3: Verify the check count**

Count total checks in `_run_readiness_checks()` — should now be 23 (was 21) in base mode, 27 (was 25) in full mode.

Verify that when K8s is unreachable, the checks that previously returned SKIP now return PASS/FAIL/WARN from DB fallback (FBC health, stale components, snapshot freshness — 3 of the 12 that were SKIP).
