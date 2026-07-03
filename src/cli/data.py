"""Data access layer for IC CLI.

Abstracts data source: local DB (direct psycopg2) or cluster API (REST).
CLI commands call these functions and don't care where data comes from.
"""

from cli import ic_config


def _is_cluster():
    return ic_config.get_mode() == 'cluster'


def _use_api():
    """Check if we should use the API (cluster mode or local API running)."""
    if _is_cluster():
        return True
    from cli.mode import has_api
    return has_api()


def _api():
    from cli.api_client import get_client, APIClient
    if _is_cluster():
        return get_client()
    url = ic_config.get_api_url() or 'http://localhost:8000'
    return APIClient(url)


def _app():
    from cli import config as cfg
    return cfg.APPLICATION_NAME


def _triage_jira_map(application):
    """Build component→jira_key map from all active triage items (single query)."""
    from cli.db import get_repo
    from repositories.triage_repository import TriageRepository
    jira_map = {}
    try:
        items = get_repo(TriageRepository).get_active(application)
        for item in items:
            jira_key = item.get('jira_key')
            if jira_key:
                for comp in item.get('components', []):
                    if comp not in jira_map:
                        jira_map[comp] = jira_key
    except Exception:
        pass
    return jira_map


def _triage_info_map(application):
    """Build component→{jira_key, group_label, root_cause} from active triage items."""
    from cli.db import get_repo
    from repositories.triage_repository import TriageRepository
    info = {}
    try:
        items = get_repo(TriageRepository).get_active(application)
        for item in items:
            entry = {
                'jira_key': item.get('jira_key'),
                'group_label': item.get('group_label'),
                'root_cause': item.get('root_cause'),
            }
            for comp in item.get('components', []):
                if comp not in info:
                    info[comp] = entry
    except Exception:
        pass
    return info


def _triage_jira_map_safe(application):
    """Build component→jira_key map, using local DB (works even in cluster mode)."""
    try:
        return _triage_jira_map(application)
    except Exception:
        return {}


def _triage_info_map_safe(application):
    """Build component→triage info map, using local DB (works even in cluster mode)."""
    try:
        return _triage_info_map(application)
    except Exception:
        return {}


def require_data():
    if _is_cluster():
        url = ic_config.get_api_url()
        if not url:
            import sys
            print("Error: no cluster URL configured.", file=sys.stderr)
            print("  ic config use-cluster https://your-api-url", file=sys.stderr)
            return False
        return True
    from cli.db import require_db
    return require_db()


def get_applications():
    if _is_cluster():
        return _api().get('/api/v1/applications') or []
    from cli.db import get_repo
    from repositories.build_failure_repository import BuildFailureRepository
    return get_repo(BuildFailureRepository).get_applications()


def get_overview_stats(application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/stats') or {}
    from cli.db import get_repo
    from repositories.build_failure_repository import BuildFailureRepository
    return get_repo(BuildFailureRepository).get_overview_stats(app)


def get_alerts(application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/alerts') or {}
    from cli.db import get_repo
    from repositories.build_failure_repository import BuildFailureRepository
    from repositories.conforma_repository import ConformaRepository
    from datetime import datetime
    build_repo = get_repo(BuildFailureRepository)
    conforma_repo = get_repo(ConformaRepository)

    triage = build_repo.get_triage_summary(app)
    build_failures = []
    for comp in triage.get('failing_components', []):
        build_failures.append({
            'component': comp['component'],
            'status': comp.get('status', 'Failed'),
            'error_type': comp.get('error_type'),
            'first_seen': str(comp.get('first_detected_at', '')),
            'last_seen': str(comp.get('last_updated_at', '')),
            'occurrence_count': comp.get('failure_count', 1),
            'has_logs': comp.get('has_logs', False),
            'has_analysis': comp.get('ai_analyzed', False),
        })

    from utils.conforma_utils import (
        extract_policy_from_scenario, extract_violation_rules,
        fetch_exceptions_by_policy, policy_url as _policy_url,
        categorize_policy, count_unique_violations,
        compute_coverage_by_env, policy_env,
        _counterpart_policy, policy_url_with_line,
    )
    from cli import config as cfg
    exceptions_by_policy = fetch_exceptions_by_policy(cfg.NAMESPACE)
    jira_map = _triage_jira_map(app)
    conforma_violations = []
    summaries = conforma_repo.get_violation_summaries(app)
    for s in summaries:
        comp_name = s['component_name']
        scenario = s.get('scenario', '')
        jira_key = s.get('jira_key') or jira_map.get(comp_name)
        exc_coverage = None
        exc_stage = None
        exc_prod = None
        exc_env_tag = None
        policy_url_stage = None
        policy_url_prod = None
        covered_rules_stage = []
        covered_rules_prod = []
        uncovered_rules_stage = []
        uncovered_rules_prod = []
        if exceptions_by_policy:
            rules = extract_violation_rules(s.get('violation_summary', ''))
            env_cov = compute_coverage_by_env(rules, scenario, exceptions_by_policy)
            stage_cov = (env_cov.get('stage') or {}).get('coverage')
            prod_cov = (env_cov.get('prod') or {}).get('coverage')
            exc_stage = stage_cov
            exc_prod = prod_cov
            exc_env_tag = env_cov.get('combined_tag')
            exc_coverage = stage_cov or prod_cov
            covered_rules_stage = (env_cov.get('stage') or {}).get('covered_rules', [])
            covered_rules_prod = (env_cov.get('prod') or {}).get('covered_rules', [])
            all_rules = set(rules) if rules else set()
            uncovered_rules_stage = sorted(all_rules - set(covered_rules_stage)) if stage_cov else []
            uncovered_rules_prod = sorted(all_rules - set(covered_rules_prod)) if prod_cov else []
            policy_name = extract_policy_from_scenario(scenario)
            counterpart = _counterpart_policy(policy_name)
            if stage_cov in ('fully_covered', 'partially_covered'):
                stage_pname = policy_name if policy_env(policy_name) == 'stage' else counterpart
                stage_rules = (env_cov.get('stage') or {}).get('covered_rules', [])
                policy_url_stage = policy_url_with_line(stage_pname, stage_rules)
            if prod_cov in ('fully_covered', 'partially_covered'):
                prod_pname = policy_name if policy_env(policy_name) == 'prod' else counterpart
                prod_rules = (env_cov.get('prod') or {}).get('covered_rules', [])
                policy_url_prod = policy_url_with_line(prod_pname, prod_rules)
        uv_count, _ = count_unique_violations(s.get('violation_summary', ''))
        conforma_violations.append({
            'component': comp_name,
            'status': 'Conforma Violation',
            'error_type': scenario,
            'first_seen': str(s.get('first_detected_at', '')),
            'last_seen': str(s.get('last_updated_at', '')),
            'occurrence_count': 1,
            'has_logs': s.get('violation_summary') is not None,
            'has_analysis': s.get('ai_analyzed', False),
            'violations_count': s.get('violations_count', 0),
            'unique_violations': uv_count or None,
            'warnings_count': s.get('warnings_count', 0),
            'scenario': scenario,
            'category': categorize_policy(scenario),
            'policy_url': _policy_url(scenario),
            'jira_key': jira_key,
            'exception_coverage': exc_coverage,
            'exception_coverage_stage': exc_stage,
            'exception_coverage_prod': exc_prod,
            'exception_env_tag': exc_env_tag,
            'policy_url_stage': policy_url_stage,
            'policy_url_prod': policy_url_prod,
            'covered_rules_stage': covered_rules_stage,
            'covered_rules_prod': covered_rules_prod,
            'uncovered_rules_stage': uncovered_rules_stage,
            'uncovered_rules_prod': uncovered_rules_prod,
        })

    now = datetime.utcnow().isoformat()
    return {
        'build_failures': build_failures,
        'conforma_violations': conforma_violations,
        'nightly_warnings': [],
        'total_count': len(build_failures) + len(conforma_violations),
        'last_sync': now,
    }


def get_component_summary(component, application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/failures/{component}')
    from cli.db import get_repo
    from repositories.build_failure_repository import BuildFailureRepository
    return get_repo(BuildFailureRepository).get_component_summary(component)


def get_failure_details(component, application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/failures/{component}')
    from cli.db import get_repo
    from repositories.build_failure_repository import BuildFailureRepository
    return get_repo(BuildFailureRepository).get_failure_details(component, app)


def get_conforma_violations(application=None, reporter_env=None, reporter_build_type=None):
    app = application or _app()
    if reporter_env or reporter_build_type:
        from clients.conforma_reporter_client import fetch_reporter_violations
        from cli.config import app_to_reporter_branch
        branch = app_to_reporter_branch(app)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        return fetch_reporter_violations(branch, env=env, build_type=build_type)
    if _is_cluster():
        alerts = _api().get(f'/api/v1/applications/{app}/alerts') or {}
        return alerts.get('conforma_violations', [])
    from cli.db import get_repo
    from repositories.conforma_repository import ConformaRepository
    from utils.conforma_utils import (
        extract_policy_from_scenario, extract_violation_rules, policy_url,
        enrich_with_coverage, fetch_exceptions_by_policy,
        compute_coverage_by_env,
    )
    from cli import config as cfg
    conforma_repo = get_repo(ConformaRepository)
    violations = conforma_repo.get_violation_summaries(app)
    jira_map = _triage_jira_map(app)
    exceptions_by_policy = fetch_exceptions_by_policy(cfg.NAMESPACE)
    for v in violations:
        if not v.get('jira_key'):
            v['jira_key'] = jira_map.get(v.get('component_name'))
        v['component'] = v.get('component_name', '')
        scenario = v.get('scenario', '')
        v['policy_name'] = extract_policy_from_scenario(scenario)
        v['policy_url'] = policy_url(scenario)
        if exceptions_by_policy:
            rules = extract_violation_rules(v.get('violation_summary', ''))
            env_cov = compute_coverage_by_env(rules, scenario, exceptions_by_policy)
            v['exception_coverage_stage'] = (env_cov.get('stage') or {}).get('coverage')
            v['exception_coverage_prod'] = (env_cov.get('prod') or {}).get('coverage')
            v['exception_env_tag'] = env_cov.get('combined_tag')
            stage_cov = v['exception_coverage_stage']
            prod_cov = v['exception_coverage_prod']
            v['covered_rules_stage'] = (env_cov.get('stage') or {}).get('covered_rules', [])
            v['covered_rules_prod'] = (env_cov.get('prod') or {}).get('covered_rules', [])
            all_rules = set(rules) if rules else set()
            v['uncovered_rules_stage'] = sorted(all_rules - set(v['covered_rules_stage'])) if stage_cov else []
            v['uncovered_rules_prod'] = sorted(all_rules - set(v['covered_rules_prod'])) if prod_cov else []
    enrich_with_coverage(violations, exceptions_by_policy)
    return violations


def get_conforma_rules(application=None, reporter_env=None, reporter_build_type=None):
    """Get violations grouped by rule. Returns list of rule dicts:
    [{'rule': str, 'title': str, 'solution': str, 'components': [str], 'count': int}]
    """
    app = application or _app()
    if reporter_env or reporter_build_type:
        from clients.conforma_reporter_client import fetch_reporter_rules
        from cli.config import app_to_reporter_branch
        branch = app_to_reporter_branch(app)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        return fetch_reporter_rules(branch, env=env, build_type=build_type)
    if _is_cluster():
        result = _api().get(f'/api/v1/applications/{app}/violations/rules',
                            params={'reporter_env': reporter_env,
                                    'reporter_build_type': reporter_build_type})
        return result if isinstance(result, list) else []
    from utils.conforma_utils import count_unique_violations
    violations = get_conforma_violations(application=app)
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


def get_conforma_details(component, application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/violations/{component}')
    from cli.db import get_repo
    from repositories.conforma_repository import ConformaRepository
    return get_repo(ConformaRepository).get_violation_details(component, app)


def get_triage_items(application=None):
    app = application or _app()
    if _is_cluster():
        import sys
        import io
        old_stderr = sys.stderr
        for path in [f'/api/v1/applications/{app}/triage/report',
                     f'/api/v1/applications/{app}/triage']:
            try:
                sys.stderr = io.StringIO()
                data = _api().get(path)
            except Exception:
                data = None
            finally:
                sys.stderr = old_stderr
            if data and isinstance(data, dict) and 'items' in data:
                return data
    try:
        from cli.db import get_repo
        from repositories.triage_repository import TriageRepository
        repo = get_repo(TriageRepository)
        items = repo.get_report(app)
        summary = repo.get_summary(app)
        return {'application': app, 'summary': summary, 'items': items}
    except Exception:
        return {'items': []}


def get_daily_stats(application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/stats/daily') or []
    from cli.db import get_repo
    from repositories.build_failure_repository import BuildFailureRepository
    return get_repo(BuildFailureRepository).get_daily_stats(app)


def get_fixes(application=None, show_all=False):
    if _is_cluster():
        params = {}
        if show_all:
            params['all'] = 'true'
        return _api().get('/api/v1/fixes', params=params) or []
    from cli.db import get_repo
    from repositories.resolution_attempt_repository import ResolutionAttemptRepository
    return get_repo(ResolutionAttemptRepository).get_all()


def get_conforma_report(application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/violations/report') or {}
    from cli.db import get_repo
    from repositories.conforma_repository import ConformaRepository
    return get_repo(ConformaRepository).get_standup_report(app)


def get_failures(application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/failures') or []
    from cli.db import get_repo
    from repositories.build_failure_repository import BuildFailureRepository
    return get_repo(BuildFailureRepository).get_failing_components(app)


def get_dashboard(application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/dashboard') or {}
    return {}


def get_export(component, fmt='slack', application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/export/{component}',
                          params={'format': fmt})
    return None


def get_schedule(application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/schedule')
    return None


def get_jira_issue(jira_key):
    if _is_cluster():
        return _api().get(f'/api/v1/jira/{jira_key}')
    return None


def get_policy_exceptions():
    if _is_cluster():
        return _api().get('/api/v1/policies/exceptions')
    from clients.konflux_client import KonfluxClient
    from cli import config as cfg
    try:
        client = KonfluxClient(namespace=cfg.NAMESPACE)
        policies = client.get_ec_policies()
        all_exceptions = []
        for policy in policies:
            all_exceptions.extend(client.extract_exceptions(policy))
        return {'exceptions': all_exceptions, 'total': len(all_exceptions)}
    except Exception:
        return None


def get_policy_bindings():
    if _is_cluster():
        return _api().get('/api/v1/policies/bindings')
    from clients.konflux_client import KonfluxClient
    from cli import config as cfg
    try:
        client = KonfluxClient(namespace=cfg.NAMESPACE)
        bindings = client.get_release_plan_admissions()
        return {'bindings': bindings, 'total': len(bindings)}
    except Exception:
        return None


def submit_analysis(component, analysis_data, application=None):
    app = application or _app()
    if _is_cluster():
        return _api().post(f'/api/v1/applications/{app}/analyses', analysis_data)
    return None


def get_watched_applications():
    if _is_cluster():
        data = _api().get('/api/v1/config/applications')
        return data.get('applications', []) if data else []
    import os
    return os.environ.get('WATCH_APPLICATIONS', '').split()


def add_watched_application(app):
    if _is_cluster():
        data = _api().post('/api/v1/config/applications', {'application': app})
        return data.get('applications', []) if data else []
    return None


def remove_watched_application(app):
    if _is_cluster():
        data = _api().delete(f'/api/v1/config/applications/{app}')
        return data.get('applications', []) if data else []
    return None
