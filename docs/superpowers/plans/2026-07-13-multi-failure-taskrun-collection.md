# Multi-Failure TaskRun Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix IC to collect all failed TaskRuns in a PipelineRun and pick the primary root cause, instead of stopping at the first failure.

**Architecture:** Add a pure `pick_primary_failure` heuristic to `tekton_parsers.py`. Update the collector, Tekton Results client, analyzer, and watcher to collect all failures before selecting the primary one. No schema changes — same return signatures.

**Tech Stack:** Python 3.11, pytest

## Global Constraints

- All new functions in `tekton_parsers.py` must be pure (no I/O, no side effects)
- Return signatures of `_find_failed_taskrun` and `find_failed_taskrun` must not change
- Run tests from `src/`: `cd src && python3 -m pytest tests/<file>::<test> -v`

---

### Task 1: Add `pick_primary_failure` heuristic to `tekton_parsers.py`

**Files:**
- Modify: `src/tekton_parsers.py` (add new function after line 131)
- Test: `src/tests/test_tekton_parsers.py`

**Interfaces:**
- Consumes: nothing
- Produces: `pick_primary_failure(failures: list[tuple[str, str|None, str|None]], pr_data: dict) -> tuple[str, str|None, str|None]` — takes a list of `(task_name, error_msg, logs)` tuples and PipelineRun data, returns the primary failure tuple. Used by Tasks 3 and 4.

- [ ] **Step 1: Write failing tests**

Add to `src/tests/test_tekton_parsers.py`:

```python
from tekton_parsers import pick_primary_failure


# -- pick_primary_failure --

def test_pick_primary_failure_single():
    failures = [('fips-check', 'patchelf not linked', 'full logs')]
    assert pick_primary_failure(failures, {}) == ('fips-check', 'patchelf not linked', 'full logs')


def test_pick_primary_failure_prefers_build_over_sideeffect():
    failures = [
        ('coverity-availability-check', 'license expired', 'coverity logs'),
        ('fips-check', 'patchelf not linked', 'fips logs'),
    ]
    result = pick_primary_failure(failures, {})
    assert result[0] == 'fips-check'


def test_pick_primary_failure_prefers_build_container():
    failures = [
        ('sast-snyk-check', 'snyk error', 'snyk logs'),
        ('build-container', 'OOM killed', 'build logs'),
    ]
    result = pick_primary_failure(failures, {})
    assert result[0] == 'build-container'


def test_pick_primary_failure_condition_message_match():
    pr_data = {'status': {'conditions': [
        {'status': 'False', 'message': 'Tasks Completed: 5 (Failed: 1): "fips-check"'}
    ]}}
    failures = [
        ('coverity-availability-check', 'license expired', 'coverity logs'),
        ('fips-check', 'patchelf not linked', 'fips logs'),
    ]
    result = pick_primary_failure(failures, pr_data)
    assert result[0] == 'fips-check'


def test_pick_primary_failure_prefers_logs_over_no_logs():
    failures = [
        ('fips-check', 'condition msg only', None),
        ('clamav-scan', 'virus found', 'full scan logs here'),
    ]
    result = pick_primary_failure(failures, {})
    # Same priority tier — prefer the one with logs
    assert result[0] == 'clamav-scan'


def test_pick_primary_failure_empty():
    assert pick_primary_failure([], {}) == (None, None, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/pestevez/claude/ci-autohealing/src && python3 -m pytest tests/test_tekton_parsers.py::test_pick_primary_failure_single -v`
Expected: `ImportError: cannot import name 'pick_primary_failure'`

- [ ] **Step 3: Implement `pick_primary_failure`**

Add to `src/tekton_parsers.py` after the `extract_error_from_logs` function (after line 131):

```python
_TASK_PRIORITY = {
    'build-container': 0,
    'build-images': 0,
    'build-image-index': 0,
    'fips-check': 1,
    'verify-conforma': 1,
    'sast-snyk-check': 2,
    'clamav-scan': 2,
    'rpms-signature-scan': 2,
    'coverity-availability-check': 3,
    'sast-coverity-check': 3,
}

_DEFAULT_PRIORITY = 2


def pick_primary_failure(failures, pr_data):
    """Pick the most relevant failure from a list of failed TaskRuns.

    Heuristic:
    1. Match task name mentioned in PipelineRun condition message
    2. Prefer build-critical tasks over side-effect tasks
    3. Prefer failures with actual logs over condition-only messages
    4. Fall back to first in list

    Args:
        failures: list of (task_name, error_msg, logs) tuples
        pr_data: PipelineRun dict for condition message parsing

    Returns:
        (task_name, error_msg, logs) or (None, None, None) if empty
    """
    if not failures:
        return (None, None, None)
    if len(failures) == 1:
        return failures[0]

    conditions = pr_data.get('status', {}).get('conditions', [])
    if conditions:
        msg = conditions[-1].get('message', '')
        for task_name, error_msg, logs in failures:
            if task_name and '"{}"'.format(task_name) in msg:
                return (task_name, error_msg, logs)

    def sort_key(entry):
        task_name, _, logs = entry
        priority = _TASK_PRIORITY.get(task_name, _DEFAULT_PRIORITY)
        has_logs = 0 if logs else 1
        return (priority, has_logs)

    return sorted(failures, key=sort_key)[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/pestevez/claude/ci-autohealing/src && python3 -m pytest tests/test_tekton_parsers.py -k pick_primary -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/pestevez/claude/ci-autohealing
git add src/tekton_parsers.py src/tests/test_tekton_parsers.py
git commit -m "feat: add pick_primary_failure heuristic for multi-failure TaskRuns"
```

---

### Task 2: Add `extract_all_errors_from_logs` to `tekton_parsers.py`

**Files:**
- Modify: `src/tekton_parsers.py` (refactor `extract_error_from_logs`)
- Test: `src/tests/test_tekton_parsers.py`

**Interfaces:**
- Consumes: nothing
- Produces: `extract_all_errors_from_logs(logs: str) -> list[tuple[str, str]]` — returns all `(error_message, error_type)` matches. Used by Task 3.

- [ ] **Step 1: Write failing tests**

Add to `src/tests/test_tekton_parsers.py`:

```python
from tekton_parsers import extract_all_errors_from_logs


def test_extract_all_errors_multiple():
    logs = (
        'time="12:00" level=info msg="starting"\n'
        'FAILED: Coverity license expired\n'
        'time="12:01" level=info msg="checking fips"\n'
        'ERROR: executable is not dynamically linked\n'
    )
    errors = extract_all_errors_from_logs(logs)
    assert len(errors) == 2
    assert 'Coverity license expired' in errors[0][0]
    assert errors[0][1] == 'Test Failure'
    assert 'not dynamically linked' in errors[1][0]
    assert errors[1][1] == 'Build Error'


def test_extract_all_errors_none():
    assert extract_all_errors_from_logs('all good\nno errors') == []
    assert extract_all_errors_from_logs('') == []
    assert extract_all_errors_from_logs(None) == []


def test_extract_all_errors_skips_metadata():
    logs = 'Slack Message: FAILED build\nERROR: real error here'
    errors = extract_all_errors_from_logs(logs)
    assert len(errors) == 1
    assert 'real error' in errors[0][0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/pestevez/claude/ci-autohealing/src && python3 -m pytest tests/test_tekton_parsers.py::test_extract_all_errors_multiple -v`
Expected: `ImportError: cannot import name 'extract_all_errors_from_logs'`

- [ ] **Step 3: Implement `extract_all_errors_from_logs`**

Refactor `src/tekton_parsers.py`. Replace the existing `extract_error_from_logs` with:

```python
def extract_all_errors_from_logs(logs):
    """Extract all error messages and their categories from build logs.

    Same filtering as extract_error_from_logs but returns ALL matches.

    Returns:
        List of (error_message, error_type) tuples. Empty list if none found.
    """
    if not logs:
        return []

    lines = logs.split('\n')
    results = []
    matched_lines = set()

    script_indicators = [
        r'^\s*(echo|cat|printf|print)\s+["\']',
        r'^\s*#',
        r'<<["\']?EOF',
        r'^\s*function\s+',
        r'^\s*\w+\(\)\s*{',
    ]

    metadata_patterns = [
        r'Slack Message:',
        r'pull request pipeline detected',
        r'CC -.*subteam',
        r'Status:.*\(cluster:',
        r'Build URL:',
        r'DEBUG INFORMATION',
    ]

    for i, line in enumerate(lines):
        if i in matched_lines:
            continue
        if any(re.match(pattern, line) for pattern in script_indicators):
            continue
        if re.search(r'>&\d+\s*$', line):
            continue
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in metadata_patterns):
            continue

        for pattern, error_type in _ERROR_PATTERNS:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                context = '\n'.join(lines[start:end])
                results.append((context[:500], error_type))
                matched_lines.add(i)
                break

    return results


def extract_error_from_logs(logs):
    """Extract an error message and its category from build logs.

    Returns:
        (error_message, error_type) or (None, None) if no error found.
    """
    errors = extract_all_errors_from_logs(logs)
    if errors:
        return errors[0]
    return None, None
```

- [ ] **Step 4: Run ALL error extraction tests to verify no regressions**

Run: `cd /home/pestevez/claude/ci-autohealing/src && python3 -m pytest tests/test_tekton_parsers.py -k "extract_error or extract_all" -v`
Expected: all existing + new tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/pestevez/claude/ci-autohealing
git add src/tekton_parsers.py src/tests/test_tekton_parsers.py
git commit -m "feat: add extract_all_errors_from_logs for multi-error log parsing"
```

---

### Task 3: Update `_find_failed_taskrun` in collector to collect all failures

**Files:**
- Modify: `src/collectors/build_failure_collector.py:307-368`
- Test: `src/tests/test_build_failure_collector_coverage.py`

**Interfaces:**
- Consumes: `pick_primary_failure(failures, pr_data)` from `tekton_parsers.py` (Task 1)
- Produces: same return signature `(task_name, error_msg, logs)` — now returns the primary failure

- [ ] **Step 1: Write failing test**

Add to `src/tests/test_build_failure_collector_coverage.py` (in the appropriate test class):

```python
def test_find_failed_taskrun_picks_primary_over_first(self, collector):
    """When multiple TaskRuns fail, pick the primary (build-critical) one."""
    coverity_tr = {
        'status': {
            'conditions': [{'status': 'False', 'message': 'coverity failed'}],
            'podName': 'coverity-pod',
        }
    }
    fips_tr = {
        'status': {
            'conditions': [{'status': 'False', 'message': 'fips failed'}],
            'podName': 'fips-pod',
        }
    }
    success_tr = {
        'status': {
            'conditions': [{'status': 'True', 'message': 'ok'}],
            'podName': 'ok-pod',
        }
    }

    pr_data = {'status': {'childReferences': [
        {'kind': 'TaskRun', 'name': 'pr-coverity-check', 'pipelineTaskName': 'coverity-availability-check'},
        {'kind': 'TaskRun', 'name': 'pr-build', 'pipelineTaskName': 'build-container'},
        {'kind': 'TaskRun', 'name': 'pr-fips-check-0', 'pipelineTaskName': 'fips-check'},
    ], 'conditions': []}}

    def mock_get_taskrun(name, **kwargs):
        return {
            'pr-coverity-check': coverity_tr,
            'pr-build': success_tr,
            'pr-fips-check-0': fips_tr,
        }.get(name)

    collector.kubearchive.get_taskrun = mock_get_taskrun
    collector.kubearchive.get_pod_logs = lambda *a, **kw: 'ERROR: some error'

    task_name, error_msg, logs = collector._find_failed_taskrun('pr-name', pr_data)
    assert task_name == 'fips-check'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/pestevez/claude/ci-autohealing/src && python3 -m pytest tests/test_build_failure_collector_coverage.py::TestFindFailedTaskrun::test_find_failed_taskrun_picks_primary_over_first -v`
Expected: FAIL — currently returns `coverity-availability-check` (first match)

- [ ] **Step 3: Update `_find_failed_taskrun`**

Replace lines 307-368 of `src/collectors/build_failure_collector.py`:

```python
    def _find_failed_taskrun(self, pr_name, pr_data):
        """Find the primary failed TaskRun and extract its error and logs.

        Collects ALL failed TaskRuns, then picks the most relevant one
        using pick_primary_failure heuristic.

        Returns:
            Tuple of (failed_task_name, error_from_taskrun, failed_task_logs)
        """
        if not pr_data:
            return None, None, None

        child_refs = pr_data.get('status', {}).get('childReferences', [])
        failures = []

        for ref in child_refs:
            if ref.get('kind') != 'TaskRun':
                continue

            task_name = ref.get('pipelineTaskName')
            tr_name = ref.get('name')

            if not tr_name:
                continue

            try:
                tr_data = self.kubearchive.get_taskrun(tr_name)
                if tr_data:
                    conditions = tr_data.get('status', {}).get('conditions', [])
                    if conditions and conditions[-1].get('status') == 'False':
                        logger.info("Found failed TaskRun: %s (task: %s)", tr_name, task_name)

                        condition_msg = conditions[-1].get('message', '')

                        pod_name = tr_data.get('status', {}).get('podName')
                        tr_logs = None
                        if pod_name:
                            tr_logs = self.kubearchive.get_pod_logs(pod_name, namespace=self.config.k8s.namespace)

                        error_msg = None
                        if tr_logs:
                            error_msg, _ = self.extract_error_messages(tr_logs)
                        if not error_msg and condition_msg:
                            error_msg = condition_msg

                        failures.append((task_name, error_msg, tr_logs))
            except Exception as e:
                logger.debug("Could not fetch TaskRun %s: %s", tr_name, e)
                continue

        if failures:
            return pick_primary_failure(failures, pr_data)

        # Fallback 1: Tekton Results
        try:
            task_name, logs, _ = self.tekton_results.find_failed_taskrun(pr_name)
            if task_name:
                logger.info("Found failed TaskRun via Tekton Results (task: %s)", task_name)
                error_msg = None
                if logs:
                    error_msg, _ = self.extract_error_messages(logs)
                return task_name, error_msg, logs
        except Exception as e:
            logger.debug("Tekton Results fallback failed: %s", e)

        # Fallback 2: simple metadata-based approach
        return extract_failed_step_from_pipelinerun(pr_data), None, None
```

Also add the import at the top of the file (near line 28):

```python
from tekton_parsers import (
    extract_error_from_logs,
    extract_failed_step_from_pipelinerun,
    pick_primary_failure,
)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/pestevez/claude/ci-autohealing/src && python3 -m pytest tests/test_build_failure_collector_coverage.py -k "find_failed" -v`
Expected: all PASS (new + existing)

- [ ] **Step 5: Commit**

```bash
cd /home/pestevez/claude/ci-autohealing
git add src/collectors/build_failure_collector.py src/tests/test_build_failure_collector_coverage.py
git commit -m "fix: collector collects all failed TaskRuns, picks primary via heuristic"
```

---

### Task 4: Update `find_failed_taskrun` in Tekton Results client

**Files:**
- Modify: `src/clients/tekton_results.py:170-200`
- Test: `src/tests/test_build_failure_collector_coverage.py` (or new test file)

**Interfaces:**
- Consumes: `pick_primary_failure(failures, pr_data)` from `tekton_parsers.py` (Task 1)
- Produces: same return signature `(task_name, logs, record_name)`

- [ ] **Step 1: Write failing test**

Add test for multi-failure scenario. Since `TektonResultsClient` requires API connectivity, mock it:

```python
# In a suitable test file or add to test_build_failure_collector_coverage.py

def test_tekton_results_find_failed_picks_primary(mocker):
    from clients.tekton_results import TektonResultsClient

    tr_client = TektonResultsClient.__new__(TektonResultsClient)

    coverity_td = {
        'metadata': {'labels': {'tekton.dev/pipelineTask': 'coverity-availability-check'}},
        'status': {'conditions': [{'status': 'False', 'message': 'license expired'}]},
    }
    fips_td = {
        'metadata': {'labels': {'tekton.dev/pipelineTask': 'fips-check'}},
        'status': {'conditions': [{'status': 'False', 'message': 'fips failed'}]},
    }
    success_td = {
        'metadata': {'labels': {'tekton.dev/pipelineTask': 'build-container'}},
        'status': {'conditions': [{'status': 'True', 'message': 'ok'}]},
    }

    mocker.patch.object(tr_client, 'query_taskrun_records', return_value=[
        (coverity_td, 'record-coverity'),
        (success_td, 'record-build'),
        (fips_td, 'record-fips'),
    ])
    mocker.patch.object(tr_client, 'get_taskrun_logs', side_effect=lambda r: 'logs for ' + r)
    mocker.patch.object(tr_client, 'get_pipelinerun', return_value={'status': {'conditions': []}})

    task_name, logs, record_name = tr_client.find_failed_taskrun('pr-name')
    assert task_name == 'fips-check'
    assert record_name == 'record-fips'
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — currently returns `coverity-availability-check`

- [ ] **Step 3: Update `find_failed_taskrun`**

Replace lines 170-200 of `src/clients/tekton_results.py`:

```python
    def find_failed_taskrun(self, pipelinerun_name):
        """Find the primary failed TaskRun in a PipelineRun and fetch its logs.

        Collects all failed TaskRuns, then picks the most relevant one.

        Returns:
            (task_name, logs, record_name) or (None, None, None)
        """
        from tekton_parsers import pick_primary_failure

        taskruns = self.query_taskrun_records(pipelinerun_name)

        failures = []
        for tr_data, record_name in taskruns:
            conditions = tr_data.get('status', {}).get('conditions', [])
            if not conditions:
                continue
            if conditions[-1].get('status') != 'False':
                continue

            task_name = tr_data.get('metadata', {}).get('labels', {}).get(
                'tekton.dev/pipelineTask', ''
            )
            logs = self.get_taskrun_logs(record_name)
            if not logs:
                condition_msg = conditions[-1].get('message', '')
                condition_reason = conditions[-1].get('reason', '')
                if condition_msg:
                    logs = "{}: {}".format(condition_reason, condition_msg)

            failures.append((task_name, logs, record_name))

        if not failures:
            return None, None, None

        pr_data = self.get_pipelinerun(pipelinerun_name) or {}
        primary = pick_primary_failure(
            [(t, l, None) for t, l, _ in failures], pr_data
        )
        primary_task = primary[0]
        for task_name, logs, record_name in failures:
            if task_name == primary_task:
                logger.info("Found primary failed TaskRun via Tekton Results: %s (task: %s)",
                            record_name, task_name)
                return task_name, logs, record_name

        return failures[0][0], failures[0][1], failures[0][2]
```

- [ ] **Step 4: Run tests**

Run: `cd /home/pestevez/claude/ci-autohealing/src && python3 -m pytest tests/ -k "tekton_results_find_failed" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/pestevez/claude/ci-autohealing
git add src/clients/tekton_results.py src/tests/test_build_failure_collector_coverage.py
git commit -m "fix: Tekton Results client collects all failed TaskRuns, picks primary"
```

---

### Task 5: Update analyzer to send all failed TaskRun logs to LLM

**Files:**
- Modify: `src/analyzers/build_failure_analyzer.py:557-567`

**Interfaces:**
- Consumes: `query_taskrun_records` from Tekton Results client
- Produces: `failed_logs` string now contains logs from ALL failed TaskRuns with markers

- [ ] **Step 1: Modify `_fetch_failed_taskrun_logs`**

Replace lines 557-567 of `src/analyzers/build_failure_analyzer.py`:

```python
                if not succeeded:
                    logs = tr.get_taskrun_logs(record_name)
                    if not logs:
                        msg = last_cond.get('message', '')
                        reason = last_cond.get('reason', '')
                        if msg:
                            logs = '{}: {}'.format(reason, msg)
                    if logs:
                        marker = '=== Failed TaskRun: {}{} ==='.format(
                            task,
                            ' [{}]'.format(platform) if platform else '')
                        if failed_logs is None:
                            failed_logs = '{}\n{}'.format(marker, logs)
                        else:
                            failed_logs += '\n\n{}\n{}'.format(marker, logs)
                        logger.info("Fetched TaskRun logs for '%s' (%s): %d chars",
                                    task, platform or 'no-platform', len(logs))
```

- [ ] **Step 2: Run existing analyzer tests to verify no regressions**

Run: `cd /home/pestevez/claude/ci-autohealing/src && python3 -m pytest tests/test_build_failure_analyzer_coverage.py -v --tb=short -q`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
cd /home/pestevez/claude/ci-autohealing
git add src/analyzers/build_failure_analyzer.py
git commit -m "fix: analyzer sends all failed TaskRun logs to LLM with markers"
```

---

### Task 6: Fix `failed_tasks` and watcher `failed_step` extraction

**Files:**
- Modify: `src/tekton_parsers.py:233-238` (rename field, add parser)
- Modify: `src/watcher/handlers.py:101` (use parsed failed tasks)
- Test: `src/tests/test_tekton_parsers.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `extract_failed_task_names_from_conditions(conditions_message: str) -> list[str]` — parses task names from Tekton condition message. `extract_pipelinerun_metadata` now returns `failed_tasks` with actual failed task names.

- [ ] **Step 1: Write failing tests**

Add to `src/tests/test_tekton_parsers.py`:

```python
from tekton_parsers import extract_failed_task_names_from_conditions


def test_extract_failed_task_names_from_conditions():
    msg = 'Tasks Completed: 10 (Failed: 2, Cancelled 0): "fips-check", "coverity-availability-check"'
    result = extract_failed_task_names_from_conditions(msg)
    assert result == ['fips-check', 'coverity-availability-check']


def test_extract_failed_task_names_no_failures():
    msg = 'Tasks Completed: 10 (Failed: 0, Cancelled 0)'
    assert extract_failed_task_names_from_conditions(msg) == []


def test_extract_failed_task_names_empty():
    assert extract_failed_task_names_from_conditions('') == []
    assert extract_failed_task_names_from_conditions(None) == []


def test_extract_pipelinerun_metadata_failed_tasks():
    pr_data = {
        'metadata': {'annotations': {}, 'name': 'pr-1', 'namespace': 'ns'},
        'spec': {'params': []},
        'status': {
            'childReferences': [
                {'kind': 'TaskRun', 'name': 'tr-1', 'pipelineTaskName': 'build-container'},
                {'kind': 'TaskRun', 'name': 'tr-2', 'pipelineTaskName': 'fips-check'},
                {'kind': 'TaskRun', 'name': 'tr-3', 'pipelineTaskName': 'coverity-availability-check'},
            ],
            'conditions': [
                {'status': 'False', 'message': 'Tasks Completed: 3 (Failed: 1): "fips-check"'}
            ],
        },
    }
    result = extract_pipelinerun_metadata(pr_data, 'ns', 'app')
    assert result['failed_tasks'] == ['fips-check']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/pestevez/claude/ci-autohealing/src && python3 -m pytest tests/test_tekton_parsers.py::test_extract_failed_task_names_from_conditions -v`
Expected: `ImportError`

- [ ] **Step 3: Implement `extract_failed_task_names_from_conditions`**

Add to `src/tekton_parsers.py` (before `extract_pipelinerun_metadata`):

```python
def extract_failed_task_names_from_conditions(message):
    """Parse failed task names from a PipelineRun condition message.

    Tekton formats: 'Tasks Completed: N (Failed: M): "task1", "task2"'

    Returns:
        List of task names, or empty list.
    """
    if not message:
        return []
    match = re.search(r'Failed:\s*(\d+)', message)
    if not match or match.group(1) == '0':
        return []
    return re.findall(r'"([^"]+)"', message)
```

- [ ] **Step 4: Update `extract_pipelinerun_metadata` to use it**

Replace lines 233-238 of `src/tekton_parsers.py`:

```python
    conditions = status.get('conditions', [])
    failed_tasks = []
    if conditions:
        msg = conditions[-1].get('message', '')
        failed_tasks = extract_failed_task_names_from_conditions(msg)
    if not failed_tasks:
        for ref in status.get('childReferences', []):
            if ref.get('kind') == 'TaskRun':
                task_name = ref.get('pipelineTaskName')
                if task_name:
                    failed_tasks.append(task_name)
```

Note: the fallback to all tasks preserves backward compatibility when the condition message doesn't contain task names.

- [ ] **Step 5: Run all tekton_parsers tests**

Run: `cd /home/pestevez/claude/ci-autohealing/src && python3 -m pytest tests/test_tekton_parsers.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
cd /home/pestevez/claude/ci-autohealing
git add src/tekton_parsers.py src/watcher/handlers.py src/tests/test_tekton_parsers.py
git commit -m "fix: failed_tasks now extracts actual failed task names from conditions"
```

---

### Task 7: Full regression test

**Files:**
- All modified files

- [ ] **Step 1: Run full test suite**

Run: `cd /home/pestevez/claude/ci-autohealing && python3 -m pytest src/tests/ -x -q --tb=short`
Expected: all PASS, no regressions

- [ ] **Step 2: Final commit if any fixups needed**

```bash
cd /home/pestevez/claude/ci-autohealing
git add -u
git commit -m "fix: multi-failure TaskRun collection — test fixups"
```
