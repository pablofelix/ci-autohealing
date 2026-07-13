"""Build failure endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from mcp_server.models import (
    AlertsSummary,
    BlockerAlert,
    BlockersSummary,
    BouncingIssue,
    BouncingIssuesSummary,
    BuildFailureDetails,
    ComponentHistoryResponse,
    FailureSummary,
    FixPropagationSummary,
    FreezeCountdown,
    JiraTokenHealth,
    NightlyWarning,
    UnpropagatedFix,
)
from repositories.build_failure_repository import BuildFailureRepository
from repositories.conforma_repository import ConformaRepository
from repositories.repository_factory import get_pool, get_repository
from repositories.triage_repository import TriageRepository
from shared_config import JIRA_BASE_URL, JIRA_PROJECT, NAMESPACE, make_konflux_pipelinerun_url

router = APIRouter(tags=["failures"])


def _compute_freeze_countdown(schedule):
    """Compute release timeline countdown from schedule data."""
    if not schedule:
        return None

    code_freeze_days = schedule.get('code_freeze_days')
    rc_days = schedule.get('initial_rc_days')
    release_days = schedule.get('release_date_days')

    if code_freeze_days is None and rc_days is None and release_days is None:
        return None

    if code_freeze_days is not None and code_freeze_days > 0:
        phase = 'pre-freeze'
    elif code_freeze_days is not None and code_freeze_days <= 0 and (rc_days is None or rc_days > 0):
        phase = 'frozen'
    elif rc_days is not None and rc_days <= 0 and (release_days is None or release_days > 0):
        phase = 'rc'
    elif release_days is not None and release_days <= 0:
        phase = 'released'
    else:
        phase = 'unknown'

    nearest_deadline = None
    if code_freeze_days is not None and code_freeze_days > 0:
        nearest_deadline = code_freeze_days
    elif rc_days is not None and rc_days > 0:
        nearest_deadline = rc_days
    elif release_days is not None and release_days > 0:
        nearest_deadline = release_days

    if nearest_deadline is not None and nearest_deadline <= 3:
        urgency = 'critical'
    elif nearest_deadline is not None and nearest_deadline <= 7:
        urgency = 'high'
    elif nearest_deadline is not None and nearest_deadline <= 14:
        urgency = 'normal'
    else:
        urgency = 'low'

    parts = []
    if phase == 'pre-freeze':
        parts.append('{} days to code freeze'.format(code_freeze_days))
    elif phase == 'frozen':
        parts.append('Code freeze ACTIVE (day {})'.format(abs(code_freeze_days)))
    elif phase == 'rc':
        parts.append('RC phase (day {})'.format(abs(rc_days)))
    if release_days is not None:
        if release_days > 0:
            parts.append('{} days to GA'.format(release_days))
        elif release_days == 0:
            parts.append('GA release TODAY')
        else:
            parts.append('GA was {} days ago'.format(abs(release_days)))
    msg = '; '.join(parts) if parts else ''

    return FreezeCountdown(
        code_freeze_days=code_freeze_days,
        initial_rc_days=rc_days,
        release_date_days=release_days,
        phase=phase,
        urgency=urgency,
        message=msg,
    )


def _build_repo():
    return get_repository(BuildFailureRepository)


def _conforma_repo():
    return get_repository(ConformaRepository)


def _triage_repo():
    return get_repository(TriageRepository)


def _konflux_url(pr_name: str) -> str:
    return make_konflux_pipelinerun_url(pr_name)


def _compute_age_signals(first_seen, last_seen):
    """Compute age_hours, is_new, status_changed for an alert."""
    now = datetime.utcnow()
    age_hours = (now - first_seen).total_seconds() / 3600 if first_seen else 0
    is_new = age_hours <= 24
    hours_since_update = (now - last_seen).total_seconds() / 3600 if last_seen else 0
    status_changed = hours_since_update <= 24 and not is_new
    return round(age_hours, 1), is_new, status_changed


UPSTREAM_SYNC_PATTERNS = [
    'upstream-', '-upstream',
    'upstream_', '_upstream',
]


def _detect_unmapped_upstream(build_failures):
    """Flag components with upstream sync naming patterns that may lack team routing.

    Components matching *-upstream or upstream-* patterns are typically upstream sync
    mirrors. When they fail, if no team is routed, alerts go to nobody.
    Returns list of component names that look like unmapped upstream sync components.
    """
    unmapped = []
    for f in build_failures:
        name = (f.component or '').lower()
        if any(name.startswith(p) or name.endswith(p.rstrip('-'))
               for p in UPSTREAM_SYNC_PATTERNS):
            f.possible_cause = (
                (f.possible_cause + '; ' if f.possible_cause else '') +
                'upstream sync component — verify team routing exists')
            unmapped.append(f.component)
    return unmapped


SYSTEMIC_PATTERNS = [
    ('go_version', ['go version', 'go toolchain', 'gotoolchain', 'go1.']),
    ('npm_registry', ['npm err', 'npm package', 'npmjs', 'registry.npmjs']),
    ('base_image', ['base image', 'from registry', 'manifest unknown', 'image not found']),
    ('s390x_build', ['s390x', 'linux/s390x']),
    ('ppc64le_build', ['ppc64le', 'linux/ppc64le']),
    ('rpm_dependency', ['rpm', 'dnf', 'yum', 'no match for argument']),
]


def _detect_systemic_patterns(build_failures):
    """Detect cross-component systemic patterns and enrich possible_cause.

    When >=2 components share the same error pattern (Go version, npm registry,
    base image, arch-specific), tag them as systemic for coordinated fix.
    """
    pattern_hits = {}
    for f in build_failures:
        error = ((f.error_type or '') + ' ' + (f.component or '')).lower()
        for pattern_name, keywords in SYSTEMIC_PATTERNS:
            if any(kw in error for kw in keywords):
                pattern_hits.setdefault(pattern_name, []).append(f.component)

    for pattern_name, components in pattern_hits.items():
        if len(components) >= 2:
            label = pattern_name.replace('_', ' ')
            cause = 'systemic: {} affecting {} components ({})'.format(
                label, len(components), ', '.join(components[:5]))
            for f in build_failures:
                if f.component in components:
                    if f.possible_cause:
                        f.possible_cause += '; ' + cause
                    else:
                        f.possible_cause = cause


@router.get("/applications/{application}/alerts", response_model=AlertsSummary)
def list_alerts(application: str):
    """All unresolved alerts (build failures + Conforma violations)."""
    from api.validators import validate_application_name
    application = validate_application_name(application)
    build_repo = _build_repo()
    known_apps = {a['application'] for a in build_repo.get_applications()}
    if application not in known_apps:
        from api.errors import not_found
        not_found(
            'Application', application,
            suggestion='Available applications: {}. Use GET /api/v1/applications to list all.'.format(
                ', '.join(sorted(known_apps))))
    triage = build_repo.get_triage_summary(application)
    triage_jira_build = _triage_repo().build_jira_map(application)
    build_failures = []
    for comp in triage.get('failing_components', []):
        fs = comp.get('first_detected_at', datetime.utcnow())
        ls = comp.get('last_updated_at', datetime.utcnow())
        age_h, is_new, status_chg = _compute_age_signals(fs, ls)
        comp_name = comp['component']
        jira = comp.get('jira_key') or triage_jira_build.get(comp_name)
        build_failures.append(FailureSummary(
            component=comp_name,
            status=comp.get('status', 'Failed'),
            error_type=comp.get('error_type'),
            first_seen=fs,
            last_seen=ls,
            occurrence_count=comp.get('failure_count', 1),
            has_logs=comp.get('has_logs', False),
            has_context=comp.get('has_context', False),
            has_analysis=comp.get('ai_analyzed', False),
            age_hours=age_h,
            is_new=is_new,
            status_changed=status_chg,
            jira_key=jira,
            is_nightly=comp.get('is_nightly', False),
            failed_step=comp.get('failed_step') or None,
            konflux_url=comp.get('konflux_url') or None,
        ))

    _detect_systemic_patterns(build_failures)
    unmapped = _detect_unmapped_upstream(build_failures)

    offboarded = _triage_repo().get_offboarded_components(application)
    for f in build_failures:
        if f.component in offboarded:
            f.offboarding_note = offboarded[f.component]

    from conforma.exception_enrichment import enrich_with_exception_coverage
    from conforma.policy_tools import (
        categorize_policy,
        count_unique_violations,
        fetch_exceptions_by_policy,
    )
    exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    conforma_violations = []
    summaries = _conforma_repo().get_violation_summaries(application)
    triage_jira = _triage_repo().build_jira_map(application)
    for details in summaries:
        comp_name = details['component_name']
        scenario = details.get('scenario', '')
        jira_key = details.get('jira_key') or triage_jira.get(comp_name)
        cov = enrich_with_exception_coverage(
            details.get('violation_summary', ''), scenario, exceptions_by_policy)
        uv_count, _ = count_unique_violations(
            details.get('violation_summary', ''))
        c_fs = details.get('first_detected_at', datetime.utcnow())
        c_ls = details.get('last_updated_at', datetime.utcnow())
        c_age, c_new, c_chg = _compute_age_signals(c_fs, c_ls)
        conforma_violations.append(FailureSummary(
            component=comp_name,
            status='Conforma Violation',
            error_type=scenario,
            first_seen=c_fs,
            last_seen=c_ls,
            occurrence_count=1,
            age_hours=c_age,
            is_new=c_new,
            status_changed=c_chg,
            has_logs=details.get('violation_summary') is not None,
            has_analysis=details.get('ai_analyzed', False),
            violations_count=details.get('violations_count', 0),
            unique_violations=uv_count or None,
            warnings_count=details.get('warnings_count', 0),
            scenario=scenario,
            category=categorize_policy(scenario),
            policy_url=cov['policy_url'],
            jira_key=jira_key,
            exception_coverage=cov['coverage'],
            exception_coverage_stage=cov['stage'],
            exception_coverage_prod=cov['prod'],
            exception_env_tag=cov['env_tag'],
            policy_env=cov['policy_env'],
            blocks=cov['blocks'],
            policy_url_stage=cov['policy_url_stage'],
            policy_url_prod=cov['policy_url_prod'],
            uncovered_rules_stage=cov['uncovered_rules_stage'],
            uncovered_rules_prod=cov['uncovered_rules_prod'],
        ))

    nightly_warnings = []
    try:
        from proactive.health_monitor import HealthMonitor
        from repositories.connection import PooledDatabaseConnection
        db = PooledDatabaseConnection(get_pool())
        monitor = HealthMonitor(db)
        for w in monitor.get_stale_nightly_builds():
            nightly_warnings.append(NightlyWarning(
                component_name=w.component_name,
                severity=w.severity,
                message=w.message,
            ))
    except Exception as exc:
        nightly_warnings.append(NightlyWarning(
            component_name='_system',
            severity='warning',
            message='Nightly check unavailable: {}'.format(str(exc)[:100]),
        ))

    all_dates = ([f.last_seen for f in build_failures] +
                 [v.last_seen for v in conforma_violations])
    last_sync = max(all_dates) if all_dates else datetime.utcnow()

    schedule = None
    try:
        from api.routes.releases import get_schedule
        schedule = get_schedule(application)
    except Exception as exc:
        nightly_warnings.append(NightlyWarning(
            component_name='_system',
            severity='warning',
            message='Release schedule unavailable: {}'.format(str(exc)[:100]),
        ))

    freeze_countdown = _compute_freeze_countdown(schedule)

    blocker_signals = []
    try:
        jira_health = check_jira_health()
        if jira_health.status != 'valid':
            blocker_signals.append(
                'JIRA TOKEN {}: {} — blocker data may be missing'.format(
                    jira_health.status.upper(), jira_health.message))
    except Exception:
        pass
    try:
        blockers_result = list_blockers(application)
        blocker_signals.extend(blockers_result.critical_signals[:10])
    except Exception:
        pass
    if unmapped:
        blocker_signals.append(
            '{} upstream sync component(s) may lack team routing: {}'.format(
                len(unmapped), ', '.join(unmapped[:5])))

    return AlertsSummary(
        application=application,
        build_failures=build_failures,
        conforma_violations=conforma_violations,
        nightly_warnings=nightly_warnings,
        total_count=len(build_failures) + len(conforma_violations) + len(nightly_warnings),
        last_sync=last_sync,
        release_schedule=schedule,
        freeze_countdown=freeze_countdown,
        blocker_signals=blocker_signals,
    )


@router.get("/applications/{application}/failures")
def list_failures(
    application: str,
    has_analysis: Optional[bool] = None,
    limit: int = 50,
) -> List[FailureSummary]:
    """List active build failures."""
    triage = _build_repo().get_triage_summary(application)
    results = []
    for comp in triage.get('failing_components', []):
        if has_analysis is True and not comp.get('ai_analyzed'):
            continue
        if has_analysis is False and comp.get('ai_analyzed'):
            continue
        results.append(FailureSummary(
            component=comp['component'],
            status=comp.get('status', 'Failed'),
            error_type=comp.get('error_type'),
            first_seen=comp.get('first_detected_at', datetime.utcnow()),
            last_seen=comp.get('last_updated_at', datetime.utcnow()),
            occurrence_count=comp.get('failure_count', 1),
            has_logs=comp.get('has_logs', False),
            has_analysis=comp.get('ai_analyzed', False),
        ))
    results.sort(key=lambda f: f.last_seen, reverse=True)
    return results[:limit]


@router.get("/applications/{application}/failures/{component}",
            response_model=Optional[BuildFailureDetails])
def get_failure(
    component: str,
    application: str,
    include_logs: bool = True,
    include_commit_context: bool = True,
):
    """Full build failure details for a component."""
    row = _build_repo().get_failure_details(component, application)
    if not row:
        from api.errors import not_found
        not_found('Component', component,
                  suggestion="Run 'ic get alerts' to see current failures")

    konflux = row.get('konflux_url') or _konflux_url(row.get('pipelinerun_name', ''))
    logs = row.get('build_logs') if include_logs else None
    context = row.get('commit_context') if include_commit_context else None

    history_data = _build_repo().get_component_history(component, application, limit=10)
    builds = history_data.get('builds', [])

    return BuildFailureDetails(
        component=row['component_name'],
        pipelinerun_name=row.get('pipelinerun_name'),
        status=row.get('status'),
        error_message=row.get('error_message'),
        error_type=row.get('error_type'),
        failed_task=row.get('failed_task_name'),
        failed_step=row.get('failed_step_name'),
        task_summary=row.get('task_summary'),
        build_logs=logs,
        commit_sha=row.get('commit_sha'),
        commit_message=row.get('commit_message'),
        commit_author=row.get('commit_author'),
        commit_url=row.get('commit_url'),
        repository_url=row.get('repository_url'),
        branch=row.get('branch'),
        output_image=row.get('output_image'),
        jira_key=row.get('jira_key'),
        build_duration_seconds=row.get('build_duration_seconds'),
        ai_analyzed=row.get('ai_analyzed'),
        is_resolved=row.get('is_resolved'),
        commit_context=context,
        konflux_url=konflux,
        first_detected_at=row.get('first_detected_at', datetime.utcnow()),
        build_history=builds,
    )


@router.get("/applications/{application}/failures/{component}/history",
            response_model=ComponentHistoryResponse)
def get_component_history(component: str, application: str, limit: int = 20):
    """Build history for a component (successes + failures)."""
    data = _build_repo().get_component_history(component, application, limit)
    return ComponentHistoryResponse(
        component=component,
        application=application,
        summary=data.get('summary', {}),
        builds=data.get('builds', []),
    )


@router.get("/applications/{application}/working")
def get_working(application: str) -> List[Dict[str, Any]]:
    """Components currently working (last build = success)."""
    return _build_repo().get_working_components(application)


@router.get("/applications/{application}/resolved")
def get_resolved(application: str) -> List[Dict[str, Any]]:
    """Components resolved (previously failing, now fixed)."""
    return _build_repo().get_resolved_components(application)


@router.get("/lookup")
def lookup_image(image: str) -> Dict[str, Any]:
    """Find which component produced a given image or digest."""
    db_matches = _build_repo().find_by_image(image)

    cluster_matches = []
    try:
        from clients.kubernetes import KubernetesClient
        kc = KubernetesClient(namespace=NAMESPACE)
        all_comps = kc.list_components()
        term = image.lower()
        cluster_matches = [c for c in all_comps if term in (c.get('container_image') or '').lower()]
    except Exception:
        pass

    return {
        'query': image,
        'db_matches': db_matches,
        'cluster_matches': cluster_matches,
        'total_matches': len(db_matches) + len(cluster_matches),
    }


@router.get("/applications/{application}/blockers", response_model=BlockersSummary)
def list_blockers(application: str) -> BlockersSummary:
    """Jira blocker analysis: age, assignment, staleness signals."""
    import os

    from clients.jira_query_builder import JiraBlockerQuery, categorize_blocker

    try:
        from clients.jira_client import JiraClient
        jira = JiraClient(
            base_url=JIRA_BASE_URL,
            email=os.environ.get('JIRA_EMAIL', ''),
            token=os.environ.get('JIRA_TOKEN', ''),
            project=JIRA_PROJECT,
        )
        jql = JiraBlockerQuery().for_application(application).build()
        raw_blockers = jira.search_blockers(jql_override=jql)
    except Exception:
        return BlockersSummary(
            application=application, project=JIRA_PROJECT,
            total_blockers=0, open_blockers=0, blockers=[],
            critical_signals=['Jira API unavailable — cannot fetch blockers'],
        )

    now = datetime.utcnow()
    blockers = []
    critical_signals = []
    open_count = 0

    for b in raw_blockers:
        created_str = b.get('created', '')
        updated_str = b.get('updated', '')

        age_hours = 0.0
        hours_since_update = 0.0
        try:
            created_dt = datetime.strptime(created_str[:19], '%Y-%m-%dT%H:%M:%S')
            age_hours = (now - created_dt).total_seconds() / 3600
        except (ValueError, TypeError):
            pass
        try:
            updated_dt = datetime.strptime(updated_str[:19], '%Y-%m-%dT%H:%M:%S')
            hours_since_update = (now - updated_dt).total_seconds() / 3600
        except (ValueError, TypeError):
            pass

        is_open = b.get('status_category') != 'done'
        if is_open:
            open_count += 1

        summary_lower = b.get('summary', '').lower()
        labels_lower = ' '.join(b.get('labels', [])).lower()

        signals = []
        if is_open and not b.get('assignee') and age_hours > 24:
            signals.append('unassigned_24h')
            critical_signals.append('{} unassigned for {:.0f}h'.format(b['key'], age_hours))
        if is_open and hours_since_update > 48:
            signals.append('stale_48h')
            critical_signals.append('{} no update for {:.0f}h'.format(b['key'], hours_since_update))
        if b.get('resolution') and is_open:
            signals.append('verified_not_closed')
            critical_signals.append('{} resolved ({}) but still open'.format(
                b['key'], b['resolution']))

        blocker_category = categorize_blocker(b.get('summary', ''), b.get('labels', []))

        hw_keywords = ['gaudi', 'spyre', 'gpu', 'rocm', 'cuda', 'hardware']
        hw_match = [kw for kw in hw_keywords
                    if kw in summary_lower or kw in labels_lower]
        if hw_match and is_open and age_hours > 72:
            signals.append('hardware_blocked')
            critical_signals.append(
                '{} blocked on hardware ({}) for {:.0f}h — long-pole item'.format(
                    b['key'], '/'.join(hw_match), age_hours))

        blockers.append(BlockerAlert(
            key=b['key'],
            summary=b['summary'],
            status=b['status'],
            assignee=b.get('assignee'),
            age_hours=round(age_hours, 1),
            hours_since_update=round(hours_since_update, 1),
            resolution=b.get('resolution'),
            labels=b.get('labels', []),
            category=blocker_category,
            signals=signals,
        ))

    product_count = sum(1 for b in blockers if b.category == 'product')
    tfa_count = sum(1 for b in blockers if b.category == 'tfa')
    infra_count = sum(1 for b in blockers if b.category == 'infra')
    signoff_count = sum(1 for b in blockers if b.category == 'signoff')

    return BlockersSummary(
        application=application,
        project=JIRA_PROJECT,
        total_blockers=len(blockers),
        open_blockers=open_count,
        product_blockers=product_count,
        tfa_blockers=tfa_count,
        infra_blockers=infra_count,
        signoff_blockers=signoff_count,
        blockers=blockers,
        critical_signals=critical_signals,
    )


@router.get("/applications/{application}/bouncing-issues", response_model=BouncingIssuesSummary)
def list_bouncing_issues(application: str, min_bounces: int = 2) -> BouncingIssuesSummary:
    """Detect Jira issues reassigned between components/teams multiple times.

    Analyzes changelog for component and assignee changes. Issues with
    >= min_bounces reassignments without resolution are flagged.
    """
    import os

    BOUNCE_FIELDS = {'Component', 'assignee', 'Assignee'}

    try:
        from clients.jira_client import JiraClient
        jira = JiraClient(
            base_url=JIRA_BASE_URL,
            email=os.environ.get('JIRA_EMAIL', ''),
            token=os.environ.get('JIRA_TOKEN', ''),
            project=JIRA_PROJECT,
        )
        raw_blockers = jira.search_blockers(JIRA_PROJECT, max_results=30)
    except Exception:
        return BouncingIssuesSummary(
            application=application, project=JIRA_PROJECT,
            bouncing_issues=[], total_bouncing=0,
        )

    now = datetime.utcnow()
    bouncing = []

    for b in raw_blockers:
        if b.get('status_category') == 'done':
            continue
        try:
            changelog = jira.get_issue_changelog(b['key'])
        except Exception:
            continue

        reassignments = []
        for entry in changelog:
            if entry.get('field') in BOUNCE_FIELDS:
                reassignments.append('{}: {} → {} (by {})'.format(
                    entry.get('created', '')[:10],
                    entry.get('from', '?'),
                    entry.get('to', '?'),
                    entry.get('author', '?'),
                ))

        if len(reassignments) >= min_bounces:
            age_days = 0.0
            try:
                created_dt = datetime.strptime(b['created'][:19], '%Y-%m-%dT%H:%M:%S')
                age_days = (now - created_dt).total_seconds() / 86400
            except (ValueError, TypeError):
                pass

            bouncing.append(BouncingIssue(
                key=b['key'],
                summary=b['summary'],
                status=b['status'],
                bounce_count=len(reassignments),
                age_days=round(age_days, 1),
                reassignment_history=reassignments[-10:],
            ))

    bouncing.sort(key=lambda x: x.bounce_count, reverse=True)

    return BouncingIssuesSummary(
        application=application,
        project=JIRA_PROJECT,
        bouncing_issues=bouncing,
        total_bouncing=len(bouncing),
    )


@router.get("/jira/health", response_model=JiraTokenHealth)
def check_jira_health() -> JiraTokenHealth:
    """Check Jira API token validity. Returns status, authenticated user, and message."""
    import os
    try:
        from clients.jira_client import JiraClient
        jira = JiraClient(
            base_url=JIRA_BASE_URL,
            email=os.environ.get('JIRA_EMAIL', ''),
            token=os.environ.get('JIRA_TOKEN', ''),
            project=JIRA_PROJECT,
        )
        result = jira.check_token_health()
        return JiraTokenHealth(**result)
    except Exception as e:
        return JiraTokenHealth(
            status='unreachable',
            message='Failed to check Jira health: {}'.format(str(e)[:200]),
        )


@router.get("/applications/{application}/fix-propagation",
            response_model=FixPropagationSummary)
def check_fix_propagation(
    application: str,
    days: int = 14,
    target_branch: str = 'main',
) -> FixPropagationSummary:
    """Check if resolved build failure fixes have propagated to target branch.

    Looks at recently resolved failures with a resolution commit, then checks
    via GitHub API whether that commit exists on the target branch (default: main).
    Flags fixes that only exist on the release branch but haven't been backported.

    Args:
        application: Which version to query.
        days: How many days back to check resolved failures (default 14).
        target_branch: Branch to verify fix exists on (default "main").
    """
    import os
    import re

    resolved = _build_repo().get_resolved_components(application, days=days)
    if not resolved:
        return FixPropagationSummary(
            application=application, checked_count=0,
            unpropagated=[], total_missing=0)

    github_token = os.environ.get('GITHUB_TOKEN', '')
    if not github_token:
        return FixPropagationSummary(
            application=application, checked_count=0,
            unpropagated=[UnpropagatedFix(
                component='(check unavailable)',
                resolution_commit='',
                source_branch='',
                missing_branches=[target_branch],
                repository_url=None,
            )], total_missing=0)

    from clients.github_client import GitHubClient
    gh = GitHubClient(token=github_token)

    unpropagated = []
    checked = 0
    for comp in resolved:
        sha = comp.get('commit_sha', '')
        if not sha or len(sha) < 7:
            continue

        repo_url = comp.get('resolution_url', '') or ''
        owner, repo_name = '', ''
        m = re.search(r'github\.com/([^/]+)/([^/]+)', repo_url)
        if not m:
            with _build_repo().db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT repository_url, branch FROM build_failures
                    WHERE component_name = %s AND application = %s
                      AND resolution_commit_sha = %s
                    LIMIT 1
                """, (comp['component'], application, sha))
                row = cursor.fetchone()
                if row and row[0]:
                    m = re.search(r'github\.com/([^/]+)/([^/]+)', row[0])
                    source_branch = row[1] or ''
                else:
                    continue
        else:
            source_branch = ''

        if not m:
            continue

        owner = m.group(1)
        repo_name = m.group(2).rstrip('.git')
        checked += 1

        on_target = gh.is_commit_on_branch(owner, repo_name, sha, target_branch)
        if on_target is False:
            unpropagated.append(UnpropagatedFix(
                component=comp['component'],
                resolution_commit=sha[:12],
                source_branch=source_branch,
                missing_branches=[target_branch],
                repository_url='https://github.com/{}/{}'.format(owner, repo_name),
                resolved_at=comp.get('resolved_at'),
            ))

    return FixPropagationSummary(
        application=application,
        checked_count=checked,
        unpropagated=unpropagated,
        total_missing=len(unpropagated),
    )
