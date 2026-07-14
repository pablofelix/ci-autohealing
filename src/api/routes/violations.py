"""Conforma violation endpoints."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from mcp_server.models import (
    ConformaViolationDetails,
    ExceptionLifecycleSummary,
    ExceptionStatus,
)
from repositories.conforma_repository import ConformaRepository
from repositories.repository_factory import get_repository
from repositories.triage_repository import TriageRepository
from shared_config import NAMESPACE, make_konflux_pipelinerun_url

logger = logging.getLogger(__name__)
router = APIRouter(tags=["violations"])


def _conforma_repo():
    return get_repository(ConformaRepository)


def _triage_repo():
    return get_repository(TriageRepository)


def _triage_jira_map(application):
    """Build component→jira_key map from all active triage items (single query)."""
    return _triage_repo().build_jira_map(application)


def _konflux_url(pr_name: str) -> str:
    return make_konflux_pipelinerun_url(pr_name)


@router.get("/applications/{application}/violations")
def list_violations(
    application: str,
    reporter_env: Optional[str] = None,
    reporter_build_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List unresolved Conforma violations (summary only, no violation_details).

    Pass reporter_env/reporter_build_type to fetch from conforma-reporter instead
    of cluster data. Example: ?reporter_env=stage&reporter_build_type=latest
    """
    from api.validators import validate_application_name
    application = validate_application_name(application)
    if reporter_env or reporter_build_type:
        from cli.config import app_to_reporter_branch
        from clients.conforma_reporter_client import fetch_reporter_violations
        branch = app_to_reporter_branch(application)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        return fetch_reporter_violations(branch, env=env, build_type=build_type)
    from conforma.policy_tools import (
        categorize_policy,
        compute_blocks,
        compute_exception_coverage_details,
        count_unique_violations,
        extract_policy_from_scenario,
        extract_violation_rules,
        fetch_exceptions_by_policy,
        policy_env,
    )
    repo = _conforma_repo()
    violations = repo.get_violation_summaries(application)
    jira_map = _triage_jira_map(application)
    try:
        exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    except Exception as e:
        logger.warning("Could not fetch exceptions: %s", e)
        exceptions_by_policy = {}
    for v in violations:
        if not v.get('jira_key'):
            v['jira_key'] = jira_map.get(v.get('component_name'))
        uv_count, _ = count_unique_violations(v.get('violation_summary', ''))
        v['unique_violations'] = uv_count or None
        scenario = v.get('scenario', '')
        v['category'] = categorize_policy(scenario)
        pname = extract_policy_from_scenario(scenario)
        v['policy_env'] = policy_env(pname)
        rules = extract_violation_rules(v.get('violation_summary', ''))
        cov = compute_exception_coverage_details(rules, scenario, exceptions_by_policy)
        v['exception_coverage'] = cov['coverage']
        v['exception_coverage_stage'] = cov['stage']
        v['exception_coverage_prod'] = cov['prod']
        v['blocks'] = compute_blocks(scenario, cov['stage'], cov['prod'])
    return violations


@router.get("/applications/{application}/violations/report")
def get_conforma_report(
    application: str,
    reporter_env: Optional[str] = None,
    reporter_build_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Conforma violations report (summary, no violation_details).

    Pass reporter_env/reporter_build_type to fetch from conforma-reporter instead.
    """
    from api.validators import validate_application_name
    application = validate_application_name(application)
    if reporter_env or reporter_build_type:
        from cli.config import app_to_reporter_branch
        from clients.conforma_reporter_client import fetch_reporter_violations
        branch = app_to_reporter_branch(application)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        violations = fetch_reporter_violations(branch, env=env, build_type=build_type)
        return {
            'application': application,
            'source': '{}/{}'.format(env, build_type),
            'total_violations': len(violations),
            'violations': violations,
        }
    from conforma.policy_tools import (
        categorize_policy,
        compute_blocks,
        compute_exception_coverage_details,
        count_unique_violations,
        extract_policy_from_scenario,
        extract_violation_rules,
        fetch_exceptions_by_policy,
        policy_env,
    )
    repo = _conforma_repo()
    violations = repo.get_violation_summaries(application)
    jira_map = _triage_jira_map(application)
    try:
        exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    except Exception as e:
        logger.warning("Could not fetch exceptions: %s", e)
        exceptions_by_policy = {}
    total_unique = 0
    groups = {}
    for v in violations:
        if not v.get('jira_key'):
            v['jira_key'] = jira_map.get(v.get('component_name'))
        v['policy_name'] = extract_policy_from_scenario(v.get('scenario', ''))
        v['category'] = categorize_policy(v.get('scenario', ''))
        v['policy_env'] = policy_env(v['policy_name'])
        uv_count, _ = count_unique_violations(v.get('violation_summary', ''))
        v['unique_violations'] = uv_count or None
        total_unique += uv_count
        cat = v['category']
        if cat not in groups:
            groups[cat] = {'components': 0, 'unique_violations': 0}
        groups[cat]['components'] += 1
        groups[cat]['unique_violations'] += uv_count
        scenario = v.get('scenario', '')
        rules = extract_violation_rules(v.get('violation_summary', ''))
        cov = compute_exception_coverage_details(rules, scenario, exceptions_by_policy)
        v['exception_coverage'] = cov['coverage']
        v['exception_coverage_stage'] = cov['stage']
        v['exception_coverage_prod'] = cov['prod']
        v['blocks'] = compute_blocks(scenario, cov['stage'], cov['prod'])
    coverage_summary = {'fully_covered': 0, 'partially_covered': 0, 'not_covered': 0}
    for v in violations:
        cov = v.get('exception_coverage')
        if cov in coverage_summary:
            coverage_summary[cov] += 1
    for cat in ('FBC', 'Components', 'Charts'):
        groups.setdefault(cat, {'components': 0, 'unique_violations': 0})
    return {
        'application': application,
        'total_violations': len(violations),
        'total_unique_violations': total_unique,
        'groups': groups,
        'coverage_summary': coverage_summary if exceptions_by_policy else None,
        'violations': violations,
    }


@router.get("/applications/{application}/violations/rules")
def list_violation_rules(
    application: str,
    reporter_env: Optional[str] = None,
    reporter_build_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Violations grouped by rule with affected components and solutions."""
    from api.validators import validate_application_name
    application = validate_application_name(application)
    if reporter_env or reporter_build_type:
        from cli.config import app_to_reporter_branch
        from clients.conforma_reporter_client import fetch_reporter_rules
        branch = app_to_reporter_branch(application)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        return fetch_reporter_rules(branch, env=env, build_type=build_type)
    from conforma.policy_tools import count_unique_violations
    repo = _conforma_repo()
    violations = repo.get_violation_summaries(application)
    rules_map = {}
    for v in violations:
        comp = v.get('component_name', '')
        _, uv_rules = count_unique_violations(v.get('violation_summary', ''))
        for r in uv_rules:
            rule = r['rule']
            if rule not in rules_map:
                rules_map[rule] = {
                    'rule': rule,
                    'title': '',
                    'solution': '',
                    'components': set(),
                    'violation_rows': 0,
                }
            rules_map[rule]['components'].add(comp)
            rules_map[rule]['violation_rows'] += 1
    result = []
    for entry in rules_map.values():
        entry['components'] = sorted(entry['components'])
        entry['count'] = len(entry['components'])
        result.append(entry)
    result.sort(key=lambda r: (-r['count'], r['rule']))
    return result


@router.get("/applications/{application}/violations/{component}",
            response_model=Optional[ConformaViolationDetails])
def get_violation(component: str, application: str, include_details: bool = True):
    """Full Conforma policy violation details."""
    from conforma.policy_tools import (
        compute_blocks,
        count_unique_violations,
        extract_policy_from_scenario,
        extract_violation_rules,
        fetch_exceptions_by_policy,
    )
    from conforma.policy_tools import (
        policy_env as _policy_env,
    )
    row = _conforma_repo().get_violation_details(component, application)
    if not row:
        from api.errors import not_found
        not_found('Conforma violation', component,
                  suggestion="Run 'ic get conforma' to see current violations")

    details = row.get('violation_details') if include_details else None
    konflux = _konflux_url(row.get('pipelinerun_name', ''))
    scenario = row['scenario']
    policy_name = extract_policy_from_scenario(scenario)

    cov_fields = {}
    exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    if exceptions_by_policy:
        from conforma.policy_tools import compute_exception_coverage_details
        rules = extract_violation_rules(row.get('violation_summary', ''))
        cov = compute_exception_coverage_details(rules, scenario, exceptions_by_policy)
        cov_fields = {
            'exception_coverage': cov['coverage'],
            'exception_coverage_stage': cov['stage'],
            'exception_coverage_prod': cov['prod'],
            'exception_env_tag': cov['env_tag'],
            'covered_rules': cov.get('covered_rules_stage', []) or cov.get('covered_rules_prod', []),
            'uncovered_rules': cov.get('uncovered_rules_stage', []) or cov.get('uncovered_rules_prod', []),
            'uncovered_rules_stage': cov.get('uncovered_rules_stage'),
            'uncovered_rules_prod': cov.get('uncovered_rules_prod'),
        }

    uv_count, uv_rules = count_unique_violations(row.get('violation_summary', ''))
    vr_list = [
        r['rule'] + (':' + r['detail'] if r['detail'] else '')
        for r in uv_rules
    ]

    p_env = _policy_env(policy_name)
    blocks = compute_blocks(
        scenario,
        cov_fields.get('exception_coverage_stage'),
        cov_fields.get('exception_coverage_prod'),
    )

    return ConformaViolationDetails(
        component=row['component_name'],
        scenario=scenario,
        policy_name=policy_name,
        policy_env=p_env,
        blocks=blocks,
        violations_count=row['violations_count'],
        unique_violations=uv_count or None,
        violation_rules=vr_list or None,
        warnings_count=row['warnings_count'],
        successes_count=row['successes_count'],
        violation_summary=row.get('violation_summary', ''),
        violation_details=details,
        repository_url=row.get('repository_url'),
        commit_sha=row.get('commit_sha'),
        snapshot_name=row.get('snapshot_name'),
        konflux_url=konflux,
        first_detected_at=row.get('first_detected_at', datetime.utcnow()),
        **cov_fields,
    )


@router.get("/exceptions/lifecycle", response_model=ExceptionLifecycleSummary)
def get_exception_lifecycle():
    """Track EC policy exception lifecycle across all policies.

    Returns all exceptions with their status: active, expiring_soon (<7 days),
    or expired (past effectiveUntil date). Helps identify exceptions that should
    be cleaned up (expired) or that need urgent root-cause fixes (expiring soon).
    """
    from conforma.policy_tools import fetch_exceptions_by_policy

    exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    if not exceptions_by_policy:
        return ExceptionLifecycleSummary(
            total_exceptions=0, permanent=0, active_temporary=0,
            expiring_soon=0, expired=0, exceptions=[])

    all_exceptions = []
    perm_count = 0
    active_temp = 0
    expiring_count = 0
    expired_count = 0

    for policy_name, exc_list in exceptions_by_policy.items():
        for exc in exc_list:
            is_permanent = exc.get('permanent', False)
            days_left = exc.get('days_left')

            if is_permanent:
                status = 'active'
                perm_count += 1
            elif days_left is not None and days_left < 0:
                status = 'expired'
                expired_count += 1
            elif days_left is not None and days_left < 7:
                status = 'expiring_soon'
                expiring_count += 1
                active_temp += 1
            else:
                status = 'active'
                active_temp += 1

            all_exceptions.append(ExceptionStatus(
                rule=exc.get('value', ''),
                policy=policy_name,
                permanent=is_permanent,
                effective_until=exc.get('effectiveUntil'),
                days_left=days_left,
                status=status,
                reference=exc.get('reference'),
                gitlab_link=exc.get('gitlab_link'),
            ))

    all_exceptions.sort(key=lambda e: (
        e.status != 'expired',
        e.status != 'expiring_soon',
        e.days_left if e.days_left is not None else 9999,
    ))

    return ExceptionLifecycleSummary(
        total_exceptions=len(all_exceptions),
        permanent=perm_count,
        active_temporary=active_temp,
        expiring_soon=expiring_count,
        expired=expired_count,
        exceptions=all_exceptions,
    )
