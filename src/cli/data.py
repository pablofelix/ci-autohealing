"""Data access layer for IC CLI.

Abstracts data source: local DB (direct psycopg2) or cluster API (REST).
CLI commands call these functions and don't care where data comes from.
"""

from cli import ic_config


def _is_cluster():
    return ic_config.get_mode() == 'cluster'


def _api():
    from cli.api_client import get_client
    return get_client()


def _app():
    from cli import config as cfg
    return cfg.APPLICATION_NAME


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
    build_repo = get_repo(BuildFailureRepository)
    conforma_repo = get_repo(ConformaRepository)
    return {
        'triage': build_repo.get_triage_summary(app),
        'conforma': conforma_repo.find_unresolved_component_names(app),
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
        return _api().get(f'/api/v1/applications/{app}/violations') or []
    from cli.db import get_repo
    from repositories.conforma_repository import ConformaRepository
    return get_repo(ConformaRepository).find_unresolved_component_names(app)


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
        return _api().get(f'/api/v1/applications/{app}/triage') or []
    from cli.db import get_repo
    from repositories.triage_repository import TriageRepository
    return get_repo(TriageRepository).get_active_items(app)


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
