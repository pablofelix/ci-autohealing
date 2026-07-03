"""Conforma violation endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from mcp_server.models import ConformaViolationDetails
from repositories.conforma_repository import ConformaRepository
from repositories.repository_factory import get_repository
from repositories.triage_repository import TriageRepository

from shared_config import KONFLUX_UI_BASE, NAMESPACE

router = APIRouter(tags=["violations"])


def _conforma_repo():
    return get_repository(ConformaRepository)


def _triage_repo():
    return get_repository(TriageRepository)


def _triage_jira_map(application):
    """Build component→jira_key map from all active triage items (single query)."""
    jira_map = {}
    try:
        for item in _triage_repo().get_active_items(application):
            jk = item.get('jira_key')
            if jk:
                for c in item.get('components', []):
                    if c not in jira_map:
                        jira_map[c] = jk
    except Exception:
        pass
    return jira_map


def _konflux_url(pr_name: str) -> str:
    return f"{KONFLUX_UI_BASE}/ns/{NAMESPACE}/pipelinerun/{pr_name}/logs"


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
        from clients.conforma_reporter_client import fetch_reporter_violations
        from cli.config import app_to_reporter_branch
        branch = app_to_reporter_branch(application)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        return fetch_reporter_violations(branch, env=env, build_type=build_type)
    from utils.conforma_utils import count_unique_violations, categorize_policy
    repo = _conforma_repo()
    violations = repo.get_violation_summaries(application)
    jira_map = _triage_jira_map(application)
    for v in violations:
        if not v.get('jira_key'):
            v['jira_key'] = jira_map.get(v.get('component_name'))
        uv_count, _ = count_unique_violations(v.get('violation_summary', ''))
        v['unique_violations'] = uv_count or None
        v['category'] = categorize_policy(v.get('scenario', ''))
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
        from clients.conforma_reporter_client import fetch_reporter_violations
        from cli.config import app_to_reporter_branch
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
    from utils.conforma_utils import (
        extract_policy_from_scenario, enrich_with_coverage,
        fetch_exceptions_by_policy, categorize_policy,
        count_unique_violations,
    )
    repo = _conforma_repo()
    violations = repo.get_violation_summaries(application)
    jira_map = _triage_jira_map(application)
    total_unique = 0
    groups = {}
    for v in violations:
        if not v.get('jira_key'):
            v['jira_key'] = jira_map.get(v.get('component_name'))
        v['policy_name'] = extract_policy_from_scenario(v.get('scenario', ''))
        v['category'] = categorize_policy(v.get('scenario', ''))
        uv_count, _ = count_unique_violations(v.get('violation_summary', ''))
        v['unique_violations'] = uv_count or None
        total_unique += uv_count
        cat = v['category']
        if cat not in groups:
            groups[cat] = {'components': 0, 'unique_violations': 0}
        groups[cat]['components'] += 1
        groups[cat]['unique_violations'] += uv_count
    exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    enrich_with_coverage(violations, exceptions_by_policy)
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
        from clients.conforma_reporter_client import fetch_reporter_rules
        from cli.config import app_to_reporter_branch
        branch = app_to_reporter_branch(application)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        return fetch_reporter_rules(branch, env=env, build_type=build_type)
    from utils.conforma_utils import count_unique_violations
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
    from utils.conforma_utils import (
        extract_policy_from_scenario, extract_violation_rules,
        compute_violation_coverage, fetch_exceptions_by_policy,
        lookup_exceptions, count_unique_violations,
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
        rules = extract_violation_rules(row.get('violation_summary', ''))
        cov = compute_violation_coverage(
            rules, lookup_exceptions(policy_name, scenario, exceptions_by_policy))
        if cov:
            cov_fields = {
                'exception_coverage': cov['coverage'],
                'covered_rules': cov['covered_rules'],
                'uncovered_rules': cov['uncovered_rules'],
                'matching_exceptions': cov['matching_exceptions'],
            }
        from utils.conforma_utils import (
            compute_coverage_by_env,
        )
        env_cov = compute_coverage_by_env(rules, scenario, exceptions_by_policy)
        cov_fields['exception_coverage_stage'] = (env_cov.get('stage') or {}).get('coverage')
        cov_fields['exception_coverage_prod'] = (env_cov.get('prod') or {}).get('coverage')
        cov_fields['exception_env_tag'] = env_cov.get('combined_tag')
        all_rules = set(rules) if rules else set()
        s_cov = set((env_cov.get('stage') or {}).get('covered_rules', []))
        p_cov = set((env_cov.get('prod') or {}).get('covered_rules', []))
        if cov_fields['exception_coverage_stage']:
            cov_fields['uncovered_rules_stage'] = sorted(all_rules - s_cov)
        if cov_fields['exception_coverage_prod']:
            cov_fields['uncovered_rules_prod'] = sorted(all_rules - p_cov)

    uv_count, uv_rules = count_unique_violations(row.get('violation_summary', ''))
    vr_list = [
        r['rule'] + (':' + r['detail'] if r['detail'] else '')
        for r in uv_rules
    ]

    return ConformaViolationDetails(
        component=row['component_name'],
        scenario=scenario,
        policy_name=policy_name,
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
