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

    jira_map = _triage_jira_map(app)
    conforma_violations = []
    summaries = conforma_repo.get_violation_summaries(app)
    for s in summaries:
        comp_name = s['component_name']
        jira_key = s.get('jira_key') or jira_map.get(comp_name)
        conforma_violations.append({
            'component': comp_name,
            'status': 'Conforma Violation',
            'error_type': s.get('scenario', ''),
            'first_seen': str(s.get('first_detected_at', '')),
            'last_seen': str(s.get('last_updated_at', '')),
            'occurrence_count': 1,
            'has_logs': s.get('violation_summary') is not None,
            'has_analysis': s.get('ai_analyzed', False),
            'violations_count': s.get('violations_count', 0),
            'warnings_count': s.get('warnings_count', 0),
            'scenario': s.get('scenario', ''),
            'jira_key': jira_key,
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


def get_conforma_violations(application=None):
    app = application or _app()
    if _is_cluster():
        alerts = _api().get(f'/api/v1/applications/{app}/alerts') or {}
        return alerts.get('conforma_violations', [])
    from cli.db import get_repo
    from repositories.conforma_repository import ConformaRepository
    conforma_repo = get_repo(ConformaRepository)
    violations = conforma_repo.get_violation_summaries(app)
    jira_map = _triage_jira_map(app)
    for v in violations:
        if not v.get('jira_key'):
            v['jira_key'] = jira_map.get(v.get('component_name'))
        v['component'] = v.get('component_name', '')
    return violations


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
