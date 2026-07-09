"""Pure onboarding check functions — no FastAPI or heavy framework dependencies."""


def check_repo(comp: dict) -> dict:
    url = comp.get('repository_url', '')
    if not url:
        return {'status': 'FAIL', 'detail': 'No repository URL configured',
                'fix': 'Set spec.source.git.url on the Component CR'}
    return {'status': 'PASS', 'detail': url}


def check_branch(comp: dict) -> dict:
    branch = comp.get('branch', '')
    if not branch:
        return {'status': 'FAIL', 'detail': 'No branch configured',
                'fix': 'Set spec.source.git.revision on the Component CR'}
    return {'status': 'PASS', 'detail': branch}


def check_container_image(comp: dict) -> dict:
    image = comp.get('container_image', '')
    if not image:
        return {'status': 'FAIL', 'detail': 'No container image configured',
                'fix': 'Set spec.containerImage on the Component CR'}
    return {'status': 'PASS', 'detail': image}


def check_pac(comp: dict, pac_repos: list) -> dict:
    repo_url = comp.get('repository_url', '')
    if not repo_url:
        return {'status': 'SKIP', 'detail': 'No repository URL to match'}
    for pac in pac_repos:
        if pac.get('url', '') == repo_url:
            return {'status': 'PASS', 'detail': 'PaC Repository CR found'}
    return {'status': 'WARN', 'detail': 'No PaC Repository CR found for this repo',
            'fix': 'Create a PipelinesAsCode Repository CR or enable PaC via Konflux UI'}


def check_builds(comp: dict, recent_pipelineruns: dict) -> dict:
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


def check_nudges(comp: dict) -> dict:
    nudges = comp.get('nudges', [])
    if nudges:
        return {'status': 'PASS', 'detail': f'{len(nudges)} nudge(s) configured'}
    return {'status': 'INFO', 'detail': 'No nudges configured (optional for leaf components)'}


def check_last_built(comp: dict) -> dict:
    commit = comp.get('last_built_commit', '')
    if commit:
        return {'status': 'PASS', 'detail': f'Last built commit: {commit[:12]}'}
    return {'status': 'WARN', 'detail': 'No successful build recorded in status',
            'fix': 'Component needs at least one successful build'}


def onboarding_score(checks: dict) -> int:
    weights = {
        'repository': 20, 'branch': 15, 'container_image': 15,
        'pac': 15, 'builds': 20, 'last_built': 10, 'nudges': 5,
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


def build_component_status(comp: dict, pac_repos: list,
                           recent_pipelineruns: dict) -> dict:
    checks = {
        'repository': check_repo(comp),
        'branch': check_branch(comp),
        'container_image': check_container_image(comp),
        'pac': check_pac(comp, pac_repos),
        'builds': check_builds(comp, recent_pipelineruns),
        'last_built': check_last_built(comp),
        'nudges': check_nudges(comp),
    }
    score = onboarding_score(checks)

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
