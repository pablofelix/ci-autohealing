"""Tests for proactive.trigger_diagnosis — pure diagnosis functions."""

from proactive.trigger_diagnosis import (
    diagnose_build_running,
    diagnose_missing_webhook,
    diagnose_nudge_misconfiguration,
    diagnose_recent_build_failure,
    diagnose_stale_trigger,
)


def test_diagnose_build_running_active():
    runs = [{'name': 'pr-123', 'status': 'running', 'commit_sha': 'abc'}]
    result = diagnose_build_running('comp-a', runs)
    assert result.cause == 'build_running'
    assert result.severity == 'info'


def test_diagnose_build_running_no_active():
    runs = [{'name': 'pr-123', 'status': 'succeeded', 'commit_sha': 'abc'}]
    assert diagnose_build_running('comp-a', runs) is None


def test_diagnose_build_running_empty():
    assert diagnose_build_running('comp-a', []) is None


def test_diagnose_recent_build_failure_match():
    runs = [{'name': 'pr-456', 'status': 'failed', 'commit_sha': 'abcdef123456'}]
    result = diagnose_recent_build_failure('comp-a', runs, 'abcdef123456full')
    assert result.cause == 'recent_build_failed'
    assert 'pr-456' in result.detail


def test_diagnose_recent_build_failure_no_match():
    runs = [{'name': 'pr-456', 'status': 'failed', 'commit_sha': '999999'}]
    assert diagnose_recent_build_failure('comp-a', runs, 'abcdef123456') is None


def test_diagnose_recent_build_failure_succeeded_not_flagged():
    runs = [{'name': 'pr-456', 'status': 'succeeded', 'commit_sha': 'abcdef123456'}]
    assert diagnose_recent_build_failure('comp-a', runs, 'abcdef123456full') is None


def test_diagnose_missing_webhook_no_pac():
    pac = [{'name': 'other-repo', 'url': 'https://github.com/org/other'}]
    result = diagnose_missing_webhook(
        'comp-a', 'https://github.com/org/my-repo', pac)
    assert result.cause == 'missing_pac_repository'
    assert result.severity == 'critical'


def test_diagnose_missing_webhook_match():
    pac = [{'name': 'my-repo', 'url': 'https://github.com/org/my-repo'}]
    assert diagnose_missing_webhook(
        'comp-a', 'https://github.com/org/my-repo', pac) is None


def test_diagnose_missing_webhook_git_suffix():
    pac = [{'name': 'my-repo', 'url': 'https://github.com/org/my-repo.git'}]
    assert diagnose_missing_webhook(
        'comp-a', 'https://github.com/org/my-repo', pac) is None


def test_diagnose_missing_webhook_case_insensitive():
    pac = [{'name': 'my-repo', 'url': 'https://GitHub.com/Org/My-Repo'}]
    assert diagnose_missing_webhook(
        'comp-a', 'https://github.com/org/my-repo', pac) is None


def test_diagnose_nudge_misconfiguration_invalid():
    result = diagnose_nudge_misconfiguration(
        'comp-a', ['comp-x', 'comp-y'], {'comp-a', 'comp-b'})
    assert result.cause == 'nudge_misconfiguration'
    assert 'comp-x' in result.detail


def test_diagnose_nudge_misconfiguration_valid():
    assert diagnose_nudge_misconfiguration(
        'comp-a', ['comp-b', 'comp-c'], {'comp-a', 'comp-b', 'comp-c'}) is None


def test_diagnose_nudge_misconfiguration_empty():
    assert diagnose_nudge_misconfiguration('comp-a', [], {'comp-a'}) is None


def test_diagnose_stale_trigger_priority_build_running_first():
    """Build running takes priority over missing webhook."""
    result = diagnose_stale_trigger(
        component_name='comp-a',
        repository_url='https://github.com/org/repo',
        pac_repositories=[],
        nudge_refs=[],
        all_component_names={'comp-a'},
        recent_pipelineruns=[{'name': 'pr-1', 'status': 'running', 'commit_sha': ''}],
        head_commit='abc123',
    )
    assert result.cause == 'build_running'


def test_diagnose_stale_trigger_unknown_fallback():
    result = diagnose_stale_trigger(
        component_name='comp-a',
        repository_url='https://github.com/org/repo',
        pac_repositories=[{'name': 'repo', 'url': 'https://github.com/org/repo'}],
        nudge_refs=[],
        all_component_names={'comp-a'},
        recent_pipelineruns=[],
        head_commit='abc123',
    )
    assert result.cause == 'unknown'


def test_diagnose_stale_trigger_failed_before_webhook():
    """Recent build failure takes priority over missing webhook."""
    result = diagnose_stale_trigger(
        component_name='comp-a',
        repository_url='https://github.com/org/repo',
        pac_repositories=[],
        nudge_refs=[],
        all_component_names={'comp-a'},
        recent_pipelineruns=[
            {'name': 'pr-1', 'status': 'failed', 'commit_sha': 'abc123456789'}],
        head_commit='abc123456789full',
    )
    assert result.cause == 'recent_build_failed'
