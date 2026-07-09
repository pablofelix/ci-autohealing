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
    from cli.api_client import APIClient, get_client
    if _is_cluster():
        return get_client()
    return APIClient('http://localhost:8000')


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


def get_ai_stats(application=None):
    app = application or _app()
    if _use_api():
        return _api().get(f'/api/v1/applications/{app}/stats') or {}
    from cli.db import get_repo
    from repositories.ai_analysis_repository import AIAnalysisRepository
    ai_repo = get_repo(AIAnalysisRepository)
    return ai_repo.get_extended_status(app)


def get_alerts(application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/alerts') or {}
    from datetime import datetime

    from cli.db import get_repo
    from repositories.build_failure_repository import BuildFailureRepository
    from repositories.conforma_repository import ConformaRepository
    build_repo = get_repo(BuildFailureRepository)
    conforma_repo = get_repo(ConformaRepository)

    from repositories.triage_repository import TriageRepository
    triage = build_repo.get_triage_summary(app)
    triage_jira_build = get_repo(TriageRepository).build_jira_map(app)
    from shared_config import make_konflux_pipelinerun_url
    build_failures = []
    for comp in triage.get('failing_components', []):
        comp_name = comp['component']
        jira = comp.get('jira_key') or triage_jira_build.get(comp_name)
        # Prefer stored URL, fall back to generating from pipelinerun_name
        kurl = comp.get('konflux_url') or make_konflux_pipelinerun_url(
            comp.get('pipelinerun_name', ''))
        build_failures.append({
            'component': comp_name,
            'status': comp.get('status', 'Failed'),
            'error_type': comp.get('error_type'),
            'failed_step': comp.get('failed_step', ''),
            'first_seen': str(comp.get('first_detected_at', '')),
            'last_seen': str(comp.get('last_updated_at', '')),
            'occurrence_count': comp.get('failure_count', 1),
            'has_logs': comp.get('has_logs', False),
            'has_analysis': comp.get('ai_analyzed', False),
            'jira_key': jira,
            'is_nightly': comp.get('is_nightly', False),
            'konflux_url': kurl,
        })

    from cli import config as cfg
    from conforma.policy_tools import (
        apply_policy_correction,
        categorize_policy,
        compute_blocks,
        compute_exception_coverage_details,
        count_unique_violations,
        extract_policy_from_scenario,
        extract_violation_rules,
        fetch_exceptions_by_policy,
        is_wrong_policy_for_artifact,
    )
    from conforma.policy_tools import (
        policy_env as _policy_env,
    )
    from conforma.policy_tools import (
        policy_url as _policy_url,
    )
    exceptions_by_policy = fetch_exceptions_by_policy(cfg.NAMESPACE)
    jira_map = _triage_jira_map(app)
    conforma_violations = []
    summaries = conforma_repo.get_violation_summaries(app)
    for s in summaries:
        comp_name = s['component_name']
        scenario = s.get('scenario', '')
        jira_key = s.get('jira_key') or jira_map.get(comp_name)
        rules = extract_violation_rules(s.get('violation_summary', ''))
        cov = compute_exception_coverage_details(
            rules, scenario, exceptions_by_policy)
        uv_count, _ = count_unique_violations(s.get('violation_summary', ''))
        # Generate Konflux UI link from pipelinerun_name (pure, no extra query)
        pr_name = s.get('pipelinerun_name', '')
        kurl = make_konflux_pipelinerun_url(pr_name)
        conforma_violations.append({
            'component': comp_name,
            'component_name': comp_name,
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
            'violation_summary': s.get('violation_summary', ''),
            'exception_coverage': cov['coverage'],
            'exception_coverage_stage': cov['stage'],
            'exception_coverage_prod': cov['prod'],
            'exception_env_tag': cov['env_tag'],
            'policy_env': _policy_env(extract_policy_from_scenario(scenario)),
            'blocks': compute_blocks(scenario, cov['stage'], cov['prod']),
            'policy_url_stage': cov['policy_url_stage'],
            'policy_url_prod': cov['policy_url_prod'],
            'covered_rules_stage': cov['covered_rules_stage'],
            'covered_rules_prod': cov['covered_rules_prod'],
            'uncovered_rules_stage': cov['uncovered_rules_stage'],
            'uncovered_rules_prod': cov['uncovered_rules_prod'],
            'trigger_type': s.get('trigger_type', 'push'),
            'is_nightly_data': s.get('trigger_type') == 'scheduled',
            # Explicitly set is_wrong_policy using the pure detection function,
            # independent of whether policy exceptions exist for the artifact type.
            # apply_policy_correction() only sets it when exceptions ARE registered;
            # this ensures charts/FBC are always marked correctly.
            'is_wrong_policy': is_wrong_policy_for_artifact(comp_name, scenario),
            'konflux_url': kurl,
        })

    apply_policy_correction(conforma_violations, exceptions_by_policy)

    from datetime import date as _date
    schedule = None
    try:
        with build_repo.db.connection() as _conn:
            _cur = _conn.cursor()
            _cur.execute(
                "SELECT planning_freeze, feature_freeze, code_freeze, initial_rc, "
                "release_window_start, release_date, next_release "
                "FROM release_schedule WHERE application = %s",
                (app,)
            )
            _row = _cur.fetchone()
        if _row:
            _fields = ['planning_freeze', 'feature_freeze', 'code_freeze',
                       'initial_rc', 'release_window_start', 'release_date']
            schedule = {'application': app}
            for _i, _f in enumerate(_fields):
                schedule[_f] = str(_row[_i]) if _row[_i] else None
                if _row[_i] and hasattr(_row[_i], 'year'):
                    schedule[f'{_f}_days'] = (_row[_i] - _date.today()).days
            schedule['next_release'] = _row[6] if _row[6] else None
    except Exception:
        pass

    # Nightly status — most recent nightly build per FBC component.
    # Single query, no extra cost. Used to show the nightly FIPS results.
    nightly_status = []
    try:
        history = build_repo.get_nightly_history(app, days=2)
        for b in history.get('nightly_builds', [])[:3]:
            pr_name = b.get('pipelinerun_name', '')
            kurl = make_konflux_pipelinerun_url(pr_name)
            nightly_status.append({
                'component': b.get('component_name', ''),
                'status': b.get('status', ''),
                'build_date': str(b.get('build_date', '')),
                'commit_sha': (b.get('commit_sha') or '')[:7],
                'pipelinerun_name': pr_name,
                'konflux_url': kurl,
            })
    except Exception:
        pass

    # Release pipeline: latest stage/prod Release CRs (single K8s call).
    release_pipeline = _fetch_release_pipeline(app, nightly_status)

    # Onboarding: lightweight check from triage DB (no K8s calls).
    onboarding = _fetch_onboarding_from_triage(app)

    now = datetime.utcnow().isoformat()
    return {
        'build_failures': build_failures,
        'conforma_violations': conforma_violations,
        'nightly_status': nightly_status,
        'nightly_warnings': [],
        'release_pipeline': release_pipeline,
        'onboarding': onboarding,
        'total_count': len(build_failures) + len(conforma_violations),
        'last_sync': now,
        'release_schedule': schedule,
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


def get_conforma_violations(application=None, reporter_env=None,
                            reporter_build_type=None, include_future=None):
    app = application or _app()
    if reporter_env or reporter_build_type:
        from cli.config import app_to_reporter_branch
        from clients.conforma_reporter_client import fetch_reporter_violations
        branch = app_to_reporter_branch(app)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        return fetch_reporter_violations(branch, env=env, build_type=build_type)
    if _is_cluster():
        alerts = _api().get(f'/api/v1/applications/{app}/alerts') or {}
        return alerts.get('conforma_violations', [])
    from cli import config as cfg
    from cli.db import get_repo
    from conforma.policy_tools import (
        apply_policy_correction,
        compute_blocks,
        compute_exception_coverage_details,
        count_unique_violations,
        enrich_with_coverage,
        extract_policy_from_scenario,
        extract_violation_rules,
        fetch_exceptions_by_policy,
        is_wrong_policy_for_artifact,
        policy_url,
    )
    from conforma.policy_tools import (
        policy_env as _penv,
    )
    from repositories.conforma_repository import ConformaRepository
    from shared_config import make_konflux_pipelinerun_url as _mk_kurl
    conforma_repo = get_repo(ConformaRepository)
    violations = conforma_repo.get_violation_summaries(app, include_future=include_future)
    jira_map = _triage_jira_map(app)
    exceptions_by_policy = fetch_exceptions_by_policy(cfg.NAMESPACE)
    for v in violations:
        if not v.get('jira_key'):
            v['jira_key'] = jira_map.get(v.get('component_name'))
        v['component'] = v.get('component_name', '')
        scenario = v.get('scenario', '')
        v['policy_name'] = extract_policy_from_scenario(scenario)
        v['policy_url'] = policy_url(scenario)
        rules = extract_violation_rules(v.get('violation_summary', ''))
        cov = compute_exception_coverage_details(
            rules, scenario, exceptions_by_policy)
        v['policy_env'] = _penv(v['policy_name'])
        v['exception_coverage_stage'] = cov['stage']
        v['exception_coverage_prod'] = cov['prod']
        v['exception_env_tag'] = cov['env_tag']
        v['blocks'] = compute_blocks(scenario, cov['stage'], cov['prod'])
        v['covered_rules_stage'] = cov['covered_rules_stage']
        v['covered_rules_prod'] = cov['covered_rules_prod']
        v['uncovered_rules_stage'] = cov['uncovered_rules_stage']
        v['uncovered_rules_prod'] = cov['uncovered_rules_prod']
        unique_count, unique_rules = count_unique_violations(
            v.get('violation_summary', ''))
        v['unique_violations'] = unique_count if v.get('violation_summary') else None
        v['unique_rules'] = unique_rules
        # Enrich with is_wrong_policy and Konflux UI link (pure, no extra query)
        comp_name = v.get('component_name', '')
        v['is_wrong_policy'] = is_wrong_policy_for_artifact(comp_name, scenario)
        v['konflux_url'] = _mk_kurl(v.get('pipelinerun_name', ''))
    enrich_with_coverage(violations, exceptions_by_policy)
    apply_policy_correction(violations, exceptions_by_policy)
    return violations


def get_conforma_rules(application=None, reporter_env=None,
                       reporter_build_type=None, include_future=None):
    """Get violations grouped by rule. Returns list of rule dicts:
    [{'rule': str, 'title': str, 'solution': str, 'components': [str], 'count': int}]
    """
    app = application or _app()
    if reporter_env or reporter_build_type:
        from cli.config import app_to_reporter_branch
        from clients.conforma_reporter_client import fetch_reporter_rules
        branch = app_to_reporter_branch(app)
        env = reporter_env or 'prod'
        build_type = reporter_build_type or 'latest'
        return fetch_reporter_rules(branch, env=env, build_type=build_type)
    if _is_cluster():
        result = _api().get(f'/api/v1/applications/{app}/violations/rules',
                            params={'reporter_env': reporter_env,
                                    'reporter_build_type': reporter_build_type})
        return result if isinstance(result, list) else []
    from conforma.policy_tools import count_unique_violations
    violations = get_conforma_violations(application=app, include_future=include_future)
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
        import io
        import sys
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
    triage = get_repo(BuildFailureRepository).get_triage_summary(app)
    return triage.get('failing_components', [])


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
    if _use_api():
        return _api().get(f'/api/v1/applications/{app}/schedule')
    return None


def get_schedule_all():
    """Get schedule for all watched applications."""
    import os
    watch_apps = os.environ.get('WATCH_APPLICATIONS', '').split()
    if not watch_apps:
        app = _app()
        watch_apps = [app] if app else []
    results = []
    for app in watch_apps:
        sched = get_schedule(app)
        if sched:
            results.append(sched)
    return results


def sync_schedule_from_product_pages(pp_data):
    """Sync schedule entries from Product Pages data to the DB."""
    if _use_api():
        return _api().post('/api/v1/releases/schedule/sync', data=pp_data)
    return None


def get_release_details(name, application=None, include_artifacts=False):
    app = application or _app()
    if _use_api():
        params = {}
        if include_artifacts:
            params['include_artifacts'] = 'true'
        return _api().get(
            f'/api/v1/applications/{app}/releases/{name}', params=params)
    from api.routes.releases import (
        _build_release_details,
        _fetch_release_cr,
    )
    from cli import config as cfg
    try:
        rel_json = _fetch_release_cr(name, cfg.NAMESPACE)
    except Exception:
        return None
    return _build_release_details(rel_json, cfg.NAMESPACE, include_artifacts=include_artifacts)


def get_jira_issue(jira_key):
    if _is_cluster():
        return _api().get(f'/api/v1/jira/{jira_key}')
    return None


def get_policy_exceptions():
    if _is_cluster():
        return _api().get('/api/v1/policies/exceptions')
    from cli import config as cfg
    from clients.konflux_client import KonfluxClient
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
    from cli import config as cfg
    from clients.konflux_client import KonfluxClient
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


def get_onboarding_status(application=None):
    app = application or _app()
    if _is_cluster():
        return _api().get(f'/api/v1/applications/{app}/onboarding')
    return _onboarding_direct(app)


def _onboarding_direct(application):
    """Fetch onboarding status directly from K8s — bypasses the API server."""
    import os
    namespace = os.environ.get('NAMESPACE', '')
    if not namespace:
        return {'application': application, 'components': [],
                'error': 'NAMESPACE not set'}

    from clients.kubernetes import KubernetesClient
    from onboarding.checks import build_component_status
    from openshift_auth import _ensure_k8s_config

    _ensure_k8s_config()
    k8s = KubernetesClient(namespace=namespace)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_comp = pool.submit(k8s.list_components, application=application)
        f_pac = pool.submit(k8s.list_pac_repositories)
        try:
            components = f_comp.result(timeout=90)
            pac_repos = f_pac.result(timeout=90)
        except Exception as exc:
            return {'application': application, 'components': [],
                    'error': f'K8s timeout ({type(exc).__name__}): {exc}'}

    jira_map = _onboarding_jira_map(components)

    results = []
    for comp in components:
        status = build_component_status(comp, pac_repos, {})
        comp_name = comp.get('name', '')
        jira_info = jira_map.get(comp_name)
        if jira_info:
            status['jira_key'] = jira_info['key']
            status['jira_status'] = jira_info['status']
            status['jira_url'] = jira_info['url']
        results.append(status)

    results.sort(key=lambda r: (r['score'], r['component']))
    complete = sum(1 for r in results if r['overall'] == 'complete')
    partial = sum(1 for r in results if r['overall'] == 'partial')
    incomplete = sum(1 for r in results if r['overall'] == 'incomplete')

    return {
        'application': application,
        'total': len(results),
        'complete': complete,
        'partial': partial,
        'incomplete': incomplete,
        'components': results,
    }


def _onboarding_jira_map(components):
    """Build component→jira map from triage DB (no Jira API calls)."""
    jira_map = {}
    try:
        from cli.db import get_repo
        from repositories.triage_repository import TriageRepository
        triage_repo = get_repo(TriageRepository)
        for comp in components:
            name = comp.get('name', '')
            item = triage_repo.find_by_component(
                name, comp.get('application', ''))
            if item and item.get('jira_key'):
                jira_map[name] = {
                    'key': item['jira_key'],
                    'status': item.get('status', ''),
                    'url': f'https://issues.redhat.com/browse/{item["jira_key"]}',
                }
    except Exception:
        pass
    return jira_map


def _fetch_release_pipeline(application, nightly_status):
    """Build release pipeline summary: nightly + stage + prod Release CRs.

    Reuses already-fetched nightly_status (zero extra queries for nightly).
    Makes one K8s call for Release CRs; gracefully returns empty if unavailable.
    """
    from cli import config as cfg

    result = {
        'nightly': nightly_status,
        'stage': None,
        'prod': None,
    }
    namespace = cfg.NAMESPACE
    if not namespace:
        return result

    try:
        from clients.konflux_client import KonfluxClient
        from openshift_auth import _ensure_k8s_config
        _ensure_k8s_config()
        kfx = KonfluxClient(namespace=namespace)
        # Extract version token for filtering (rhoai-v3-5 → v3-5)
        version = application.replace('rhoai-', '') if application else ''
        releases = kfx.get_releases(limit=30)
        for rel in releases:
            info = KonfluxClient.extract_release_status(rel)
            rp = info.get('release_plan', '')
            if version and version not in rp:
                continue
            target = 'prod' if 'prod' in rp else 'stage'
            if target == 'stage' and not result['stage']:
                result['stage'] = info
            elif target == 'prod' and not result['prod']:
                result['prod'] = info
            if result['stage'] and result['prod']:
                break
    except Exception:
        pass
    return result


def _fetch_onboarding_from_triage(application):
    """Get onboarding items from triage DB (no K8s calls, fast).

    Returns list of triage items whose group_label contains 'onboarding'.
    Empty list means all components are fully onboarded.
    """
    try:
        from cli.db import get_repo
        from repositories.triage_repository import TriageRepository
        items = get_repo(TriageRepository).get_active(application)
        onboarding_items = []
        for item in items:
            group = (item.get('group_label') or '').lower()
            notes = (item.get('notes') or '').lower()
            if 'onboarding' in group or 'onboarding' in notes:
                onboarding_items.append({
                    'components': item.get('components', []),
                    'group_label': item.get('group_label', ''),
                    'jira_key': item.get('jira_key'),
                    'status': item.get('status', ''),
                    'root_cause': item.get('root_cause', ''),
                })
        return onboarding_items
    except Exception:
        return []


def get_onboarding_describe(component, application=None, diff=False):
    app = application or _app()
    if _use_api():
        params = {}
        if diff:
            params['diff'] = 'true'
        return _api().get(
            f'/api/v1/applications/{app}/onboarding/{component}',
            params=params if params else None)
    return None
