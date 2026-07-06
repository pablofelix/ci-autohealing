"""Component onboarding status API routes."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

from fastapi import APIRouter

from api.validators import validate_application_name

logger = logging.getLogger(__name__)

router = APIRouter(tags=["onboarding"])


def _check_repo(comp: dict) -> dict:
    url = comp.get('repository_url', '')
    if not url:
        return {'status': 'FAIL', 'detail': 'No repository URL configured',
                'fix': 'Set spec.source.git.url on the Component CR'}
    return {'status': 'PASS', 'detail': url}


def _check_branch(comp: dict) -> dict:
    branch = comp.get('branch', '')
    if not branch:
        return {'status': 'FAIL', 'detail': 'No branch configured',
                'fix': 'Set spec.source.git.revision on the Component CR'}
    return {'status': 'PASS', 'detail': branch}


def _check_container_image(comp: dict) -> dict:
    image = comp.get('container_image', '')
    if not image:
        return {'status': 'FAIL', 'detail': 'No container image configured',
                'fix': 'Set spec.containerImage on the Component CR'}
    return {'status': 'PASS', 'detail': image}


def _check_pac(comp: dict, pac_repos: list) -> dict:
    repo_url = comp.get('repository_url', '')
    if not repo_url:
        return {'status': 'SKIP', 'detail': 'No repository URL to match'}
    for pac in pac_repos:
        if pac.get('url', '') == repo_url:
            return {'status': 'PASS', 'detail': 'PaC Repository CR found'}
    return {'status': 'WARN', 'detail': 'No PaC Repository CR found for this repo',
            'fix': 'Create a PipelinesAsCode Repository CR or enable PaC via Konflux UI'}


def _check_builds(comp: dict, recent_pipelineruns: dict) -> dict:
    name = comp.get('name', '')
    runs = recent_pipelineruns.get(name, [])
    if not runs:
        commit = comp.get('last_built_commit', '')
        if commit:
            return {'status': 'PASS', 'detail': f'Built (commit {commit[:12]})'}
        return {'status': 'FAIL', 'detail': 'No builds found',
                'fix': 'Trigger an initial build via push or manual PipelineRun'}
    latest = runs[0]
    status = latest.get('status', 'unknown')
    if status == 'succeeded':
        return {'status': 'PASS', 'detail': 'Latest build succeeded'}
    if status == 'running':
        return {'status': 'WARN', 'detail': 'Build currently running'}
    return {'status': 'FAIL', 'detail': f'Latest build {status}',
            'fix': 'Check PipelineRun logs for errors'}


def _check_nudges(comp: dict) -> dict:
    nudges = comp.get('nudges', [])
    if nudges:
        return {'status': 'PASS', 'detail': f'{len(nudges)} nudge(s) configured'}
    return {'status': 'INFO', 'detail': 'No nudges configured (optional for leaf components)'}


def _check_last_built(comp: dict) -> dict:
    commit = comp.get('last_built_commit', '')
    if commit:
        return {'status': 'PASS', 'detail': f'Last built commit: {commit[:12]}'}
    return {'status': 'WARN', 'detail': 'No successful build recorded in status',
            'fix': 'Component needs at least one successful build'}


def _onboarding_score(checks: dict) -> int:
    """0-100 score based on check results."""
    weights = {
        'repository': 20,
        'branch': 15,
        'container_image': 15,
        'pac': 15,
        'builds': 20,
        'last_built': 10,
        'nudges': 5,
    }
    score = 0
    for key, weight in weights.items():
        check = checks.get(key, {})
        status = check.get('status', 'FAIL')
        if status == 'PASS':
            score += weight
        elif status in ('WARN', 'INFO'):
            score += weight // 2
    return score


def _build_component_status(comp: dict, pac_repos: list,
                            recent_pipelineruns: dict) -> dict:
    checks = {
        'repository': _check_repo(comp),
        'branch': _check_branch(comp),
        'container_image': _check_container_image(comp),
        'pac': _check_pac(comp, pac_repos),
        'builds': _check_builds(comp, recent_pipelineruns),
        'last_built': _check_last_built(comp),
        'nudges': _check_nudges(comp),
    }
    score = _onboarding_score(checks)

    failing = [k for k, v in checks.items() if v.get('status') == 'FAIL']
    warnings = [k for k, v in checks.items() if v.get('status') == 'WARN']

    if not failing and not warnings:
        overall = 'complete'
    elif not failing:
        overall = 'partial'
    else:
        overall = 'incomplete'

    return {
        'component': comp.get('name', ''),
        'application': comp.get('application', ''),
        'overall': overall,
        'score': score,
        'checks': checks,
        'failing': failing,
        'warnings': warnings,
    }


def _detect_component_type(comp: dict) -> str:
    """Detect if component is ODH (upstream) or RHOAI (downstream)."""
    repo = comp.get('repository_url', '').lower()
    name = comp.get('name', '').lower()
    if 'opendatahub' in repo or 'odh-' in name:
        return 'odh'
    if 'red-hat-data-services' in repo or 'rhoai' in name or 'rhods' in name:
        return 'rhoai'
    return 'unknown'


_ODH_STEPS = [
    ('component_cr', 'Component CR created in Konflux'),
    ('repository', 'Git repository URL configured'),
    ('branch', 'Release branch (rhoai-X.Y) created'),
    ('dockerfile', 'Downstream Dockerfile present'),
    ('container_image', 'Container image registry configured'),
    ('pac', 'PipelinesAsCode webhook enabled'),
    ('first_build', 'First successful build completed'),
    ('nudges', 'Build nudge references configured'),
    ('release_plan', 'ReleasePlan targets configured'),
]

_RHOAI_STEPS = [
    ('component_cr', 'Component CR created in Konflux'),
    ('repository', 'Git repository URL configured'),
    ('branch', 'Release branch configured'),
    ('container_image', 'Container image registry configured'),
    ('pac', 'PipelinesAsCode webhook enabled'),
    ('first_build', 'First successful build completed'),
    ('nudges', 'Build nudge references configured'),
    ('release_plan', 'ReleasePlan targets configured'),
]


def _evaluate_step(step_key: str, comp: dict, pac_repos: list,
                   recent_pipelineruns: dict, release_plans: list) -> dict:
    name = comp.get('name', '')
    created_at = comp.get('created_at', '')

    if step_key == 'component_cr':
        return {'status': 'done', 'detail': f'Component {name} exists',
                'completed_at': created_at}

    if step_key == 'repository':
        url = comp.get('repository_url', '')
        if url:
            return {'status': 'done', 'detail': url, 'completed_at': created_at}
        return {'status': 'blocked', 'detail': 'No repository URL',
                'fix': 'Set spec.source.git.url on Component CR'}

    if step_key == 'branch':
        branch = comp.get('branch', '')
        if branch:
            return {'status': 'done', 'detail': branch, 'completed_at': created_at}
        return {'status': 'blocked', 'detail': 'No branch configured',
                'fix': 'Set spec.source.git.revision'}

    if step_key == 'dockerfile':
        return {'status': 'unknown', 'detail': 'Cannot verify remotely',
                'fix': 'Ensure Dockerfile.rhoai or Containerfile exists in repo'}

    if step_key == 'container_image':
        image = comp.get('container_image', '')
        if image:
            return {'status': 'done', 'detail': image, 'completed_at': created_at}
        return {'status': 'blocked', 'detail': 'No container image configured',
                'fix': 'Set spec.containerImage'}

    if step_key == 'pac':
        repo_url = comp.get('repository_url', '')
        for pac in pac_repos:
            if pac.get('url', '') == repo_url:
                return {'status': 'done', 'detail': 'PaC Repository CR found'}
        return {'status': 'pending', 'detail': 'No PaC webhook configured',
                'fix': 'Enable PaC via Konflux UI or create Repository CR'}

    if step_key == 'first_build':
        runs = recent_pipelineruns.get(name, [])
        if not runs:
            commit = comp.get('last_built_commit', '')
            if commit:
                return {'status': 'done', 'detail': f'Built commit {commit[:12]}'}
            return {'status': 'pending', 'detail': 'No builds found',
                    'fix': 'Push to trigger first build or create manual PipelineRun'}
        succeeded = [r for r in runs if r.get('status') == 'succeeded']
        failed = [r for r in runs if r.get('status') == 'failed']
        latest = runs[0]
        st = latest.get('status', 'unknown')
        result = {
            'total_runs': len(runs),
            'succeeded': len(succeeded),
            'failed': len(failed),
            'latest_run': latest.get('name', ''),
            'latest_status': st,
            'latest_at': latest.get('created_at', ''),
        }
        if st == 'succeeded':
            result.update({'status': 'done', 'detail': 'Latest build succeeded',
                           'completed_at': latest.get('created_at', '')})
        elif st == 'running':
            result.update({'status': 'in_progress',
                           'detail': f'Build running since {latest.get("created_at", "?")}',
                           'started_at': latest.get('created_at', '')})
        else:
            reason = latest.get('reason', '')
            result.update({'status': 'failed',
                           'detail': f'Latest build {st}' +
                                     (f' ({reason})' if reason else ''),
                           'failed_at': latest.get('created_at', ''),
                           'fix': 'Check PipelineRun logs for errors'})
            if succeeded:
                result['last_success_at'] = succeeded[0].get('created_at', '')
        return result

    if step_key == 'nudges':
        nudges = comp.get('nudges', [])
        if nudges:
            return {'status': 'done', 'detail': f'{len(nudges)} nudge(s)',
                    'nudge_refs': nudges}
        return {'status': 'pending',
                'detail': 'No nudges (optional for leaf components)'}

    if step_key == 'release_plan':
        comp_name = comp.get('name', '')
        has_rp = any(rp.get('component', '') == comp_name or
                     not rp.get('component')
                     for rp in release_plans)
        if has_rp:
            return {'status': 'done', 'detail': 'ReleasePlan covers this component'}
        return {'status': 'pending', 'detail': 'No ReleasePlan found',
                'fix': 'Create a ReleasePlan targeting this component'}

    return {'status': 'unknown'}


def _describe_component(comp: dict, pac_repos: list,
                        recent_pipelineruns: dict,
                        release_plans: list) -> dict:
    comp_type = _detect_component_type(comp)
    steps = _ODH_STEPS if comp_type == 'odh' else _RHOAI_STEPS

    step_results = []
    current_step = None
    blocked_at = None
    for key, label in steps:
        result = _evaluate_step(key, comp, pac_repos,
                                recent_pipelineruns, release_plans)
        result['step'] = key
        result['label'] = label
        step_results.append(result)
        if result['status'] in ('blocked', 'failed') and not blocked_at:
            blocked_at = key
        if result['status'] in ('pending', 'in_progress') and not current_step:
            current_step = key

    done = sum(1 for s in step_results if s['status'] == 'done')
    total = len(step_results)
    progress = round(done / total * 100) if total else 0

    if blocked_at:
        phase = 'blocked'
    elif done == total:
        phase = 'complete'
    elif current_step:
        phase = 'in_progress'
    else:
        phase = 'not_started'

    timeline = []
    for s in step_results:
        ts = s.get('completed_at') or s.get('started_at') or s.get('failed_at')
        if ts:
            timeline.append({'step': s['step'], 'date': ts, 'status': s['status']})
    timeline.sort(key=lambda t: t['date'])

    return {
        'component': comp.get('name', ''),
        'application': comp.get('application', ''),
        'type': comp_type,
        'phase': phase,
        'progress': progress,
        'steps_done': done,
        'steps_total': total,
        'current_step': current_step,
        'blocked_at': blocked_at,
        'created_at': comp.get('created_at', ''),
        'steps': step_results,
        'timeline': timeline,
    }


@router.post("/applications/{application}/onboarding/{component}/analyze")
def analyze_component_onboarding(application: str,
                                 component: str) -> Dict[str, Any]:
    """Run AI analysis on a component's onboarding progress."""
    validate_application_name(application)

    data = get_component_onboarding(application, component)
    if data.get('error'):
        return data

    try:
        from analyzers.onboarding_analyzer import OnboardingAnalyzer
        from clients.llm_provider import create_llm_provider
        from config import CollectorConfig

        config = CollectorConfig.from_env()
        if not config.llm:
            return {'error': 'LLM not configured — set LLM_PROVIDER'}

        llm = create_llm_provider(config.llm)
        analyzer = OnboardingAnalyzer(llm)
        result = analyzer.analyze(data)
        result['component'] = component
        result['application'] = application
        return result
    except Exception as e:
        logger.error("Onboarding analysis failed for %s: %s", component, e)
        return {'error': 'Analysis failed: {}'.format(e)}


@router.get("/applications/{application}/onboarding/{component}")
def get_component_onboarding(application: str, component: str,
                             diff: bool = False) -> Dict[str, Any]:
    """Describe onboarding progress for a specific component."""
    validate_application_name(application)

    namespace = os.environ.get('NAMESPACE', '')
    if not namespace:
        return {'error': 'NAMESPACE not set — cannot query cluster'}

    from clients.kubernetes import KubernetesClient

    k8s = KubernetesClient(namespace=namespace)
    components = k8s.list_components(application=application)
    pac_repos = k8s.list_pac_repositories()

    comp = None
    for c in components:
        if c.get('name', '') == component:
            comp = c
            break

    if not comp:
        return {'error': f'Component {component} not found in {application}'}

    try:
        runs = k8s.list_recent_pipelineruns(component, limit=3)
    except Exception:
        runs = []
    recent_pipelineruns = {component: runs}

    release_plans = []
    nudge_prs = []
    try:
        from clients.konflux_client import KonfluxClient
        kfx = KonfluxClient(namespace=namespace)
        rpas = kfx.get_release_plan_admissions(name_filter=application)
        release_plans = rpas or []
        updates = kfx.get_dependency_updates(component_filter=component,
                                             hours=720)
        nudge_prs = [u for u in updates if u.get('pr_url')]
    except Exception:
        pass

    result = _describe_component(comp, pac_repos, recent_pipelineruns,
                                 release_plans)
    if nudge_prs:
        result['nudge_prs'] = [{
            'pr_url': pr.get('pr_url', ''),
            'package': pr.get('package', ''),
            'from_version': pr.get('from_version', ''),
            'to_version': pr.get('to_version', ''),
            'merged': pr.get('merged', False),
            'created': pr.get('created', ''),
        } for pr in nudge_prs]

    _enrich_with_jira(result, component)

    if diff:
        from utils.onboarding_jira import build_diff_data
        automation = result.get('automation_steps', [])
        result['diff'] = build_diff_data(result.get('steps', []), automation)

    return result


def _enrich_with_jira(result: dict, component: str) -> None:
    """Add Jira ticket data, automation steps, and heuristic analysis."""
    from utils.onboarding_jira import (
        analyze_bot_comments,
        build_onboarding_report,
        compute_heuristic_analysis,
        extract_pr_links,
        get_jira_client_from_env,
        map_labels_to_steps,
        search_onboarding_jira,
    )

    jira = get_jira_client_from_env()
    if not jira:
        result['jira_available'] = False
        result['jira_notice'] = 'Set JIRA_TOKEN for automation tracking'
        return

    result['jira_available'] = True
    tickets = search_onboarding_jira(component, jira)

    if not tickets:
        result['jira_tickets'] = []
        return

    result['jira_tickets'] = [{
        'key': t['key'],
        'summary': t['summary'],
        'status': t['status'],
        'type': t['type'],
        'url': t['url'],
    } for t in tickets]

    rhoai_ticket = next(
        (t for t in tickets if t['type'] == 'rhoai'), None)

    if rhoai_ticket:
        labels = rhoai_ticket.get('labels', [])
        automation_steps = map_labels_to_steps(labels)

        comments = rhoai_ticket.get('comments', [])
        pr_links = extract_pr_links(comments)

        for step in automation_steps:
            links = pr_links.get(step['key'], [])
            if links:
                step['pr_links'] = [lnk['url'] for lnk in links]

        result['automation_steps'] = automation_steps
        result['automation_pr_links'] = {
            k: [lnk['url'] for lnk in v]
            for k, v in pr_links.items()
        }

        done_count = sum(1 for s in automation_steps if s['status'] == 'done')
        result['automation_progress'] = '{}/{}'.format(
            done_count, len(automation_steps))

        analysis = compute_heuristic_analysis(
            result.get('steps', []), automation_steps, pr_links)
        result['analysis'] = analysis

        bot_analysis = analyze_bot_comments(comments)
        if bot_analysis['has_errors']:
            result['bot_error_analysis'] = bot_analysis

        report = build_onboarding_report(
            component, tickets, automation_steps,
            pr_links, bot_analysis, analysis)
        result['report'] = report

    odh_ticket = next(
        (t for t in tickets if t['type'] == 'odh'), None)
    if odh_ticket:
        odh_comments = odh_ticket.get('comments', [])
        odh_pr_links = extract_pr_links(odh_comments)
        result['odh_pr_links'] = {
            k: [lnk['url'] for lnk in v]
            for k, v in odh_pr_links.items()
        }
        odh_bot = analyze_bot_comments(odh_comments)
        if odh_bot['has_errors']:
            result['odh_bot_error_analysis'] = odh_bot


@router.get("/applications/{application}/onboarding")
def get_onboarding_status(application: str) -> Dict[str, Any]:
    """Get onboarding status for all components in an application."""
    validate_application_name(application)

    namespace = os.environ.get('NAMESPACE', '')
    if not namespace:
        return {'application': application, 'components': [],
                'error': 'NAMESPACE not set — cannot query cluster'}

    from clients.kubernetes import KubernetesClient

    k8s = KubernetesClient(namespace=namespace)
    components = k8s.list_components(application=application)
    pac_repos = k8s.list_pac_repositories()

    jira_map = _build_jira_map(components)

    def _process_one(comp):
        status = _build_component_status(comp, pac_repos, {})
        comp_name = comp.get('name', '')
        jira_info = jira_map.get(comp_name)
        if jira_info:
            status['jira_key'] = jira_info['key']
            status['jira_status'] = jira_info['status']
            status['jira_url'] = jira_info['url']
        return status

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_process_one, c): c for c in components}
        for future in as_completed(futures, timeout=30):
            try:
                results.append(future.result())
            except Exception as exc:
                comp = futures[future]
                logger.warning("Onboarding check failed for %s: %s",
                               comp.get('name', '?'), exc)

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


def _build_jira_map(components: list) -> dict:
    """Build component→jira info map from triage items (fast, no Jira API).

    Falls back to triage DB which is cheaper than per-component Jira search.
    """
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
                    'url': 'https://issues.redhat.com/browse/{}'.format(
                        item['jira_key']),
                }
    except Exception:
        pass
    return jira_map
