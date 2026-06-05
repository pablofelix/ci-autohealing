"""Diagnose why a component's build was not triggered.

All functions are pure — they take pre-fetched data and return a diagnosis.
No I/O happens here; callers are responsible for fetching PaC Repository CRs,
PipelineRuns, and component metadata before calling these functions.
"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class TriggerDiagnosis:
    cause: str
    severity: str
    detail: str
    evidence: Dict[str, Any] = field(default_factory=dict)


def diagnose_build_running(component_name, recent_pipelineruns):
    """A build is currently in progress — not really stale."""
    for pr in recent_pipelineruns:
        status = (pr.get('status') or '').lower()
        if status in ('running', 'pending', 'started'):
            return TriggerDiagnosis(
                cause='build_running',
                severity='info',
                detail='Build in progress: {}'.format(pr.get('name', '')),
                evidence={'pipelinerun': pr.get('name')},
            )
    return None


def diagnose_recent_build_failure(component_name, recent_pipelineruns, head_commit):
    """Build WAS triggered for the HEAD commit but failed."""
    if not head_commit:
        return None
    head_short = head_commit[:12]
    for pr in recent_pipelineruns:
        pr_commit = pr.get('commit_sha', '')
        if pr_commit and pr_commit.startswith(head_short):
            status = (pr.get('status') or '').lower()
            if status in ('failed', 'error'):
                return TriggerDiagnosis(
                    cause='recent_build_failed',
                    severity='warning',
                    detail='Build triggered for HEAD {} but failed: {}'.format(
                        head_short, pr.get('name', '')),
                    evidence={
                        'pipelinerun': pr.get('name'),
                        'commit': head_short,
                        'status': status,
                    },
                )
    return None


def diagnose_missing_webhook(component_name, repository_url, pac_repositories):
    """No PipelinesAsCode Repository CR matches this component's repo URL."""
    if not repository_url or not pac_repositories:
        return None

    repo_normalized = repository_url.rstrip('/').lower()
    if repo_normalized.endswith('.git'):
        repo_normalized = repo_normalized[:-4]

    for pac in pac_repositories:
        pac_url = (pac.get('url') or '').rstrip('/').lower()
        if pac_url.endswith('.git'):
            pac_url = pac_url[:-4]
        if pac_url and pac_url == repo_normalized:
            return None

    return TriggerDiagnosis(
        cause='missing_pac_repository',
        severity='critical',
        detail='No PipelinesAsCode webhook for {}'.format(
            repository_url.rstrip('/').split('/')[-1]),
        evidence={'repository_url': repository_url},
    )


def diagnose_nudge_misconfiguration(component_name, nudge_refs, all_component_names):
    """Nudge refs point to components that don't exist."""
    if not nudge_refs:
        return None

    invalid = [ref for ref in nudge_refs if ref not in all_component_names]
    if not invalid:
        return None

    return TriggerDiagnosis(
        cause='nudge_misconfiguration',
        severity='warning',
        detail='Nudge refs point to missing components: {}'.format(
            ', '.join(invalid[:3])),
        evidence={'invalid_refs': invalid, 'valid_count': len(nudge_refs) - len(invalid)},
    )


def diagnose_stale_trigger(
    component_name,
    repository_url,
    pac_repositories,
    nudge_refs,
    all_component_names,
    recent_pipelineruns,
    head_commit,
):
    """Chain all diagnoses in priority order. Returns first match or 'unknown'."""
    checks = [
        lambda: diagnose_build_running(component_name, recent_pipelineruns),
        lambda: diagnose_recent_build_failure(
            component_name, recent_pipelineruns, head_commit),
        lambda: diagnose_missing_webhook(
            component_name, repository_url, pac_repositories),
        lambda: diagnose_nudge_misconfiguration(
            component_name, nudge_refs, all_component_names),
    ]

    for check in checks:
        result = check()
        if result is not None:
            return result

    return TriggerDiagnosis(
        cause='unknown',
        severity='info',
        detail='No specific cause identified — webhook may be misconfigured or commit not pushed to tracked branch',
    )
