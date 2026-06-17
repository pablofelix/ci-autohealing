"""External service proxy endpoints — Jira, GitLab, Konflux cluster.

These endpoints proxy calls to external services that the CLI would
otherwise need direct access to. Enables cluster mode for commands
like 'ic get exceptions', 'ic describe jira', 'ic jira create'.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter

router = APIRouter(tags=["external"])


def _jira_client():
    from clients.jira_client import JiraClient
    from config import CollectorConfig
    cfg = CollectorConfig.from_env()
    if not cfg.jira:
        return None
    return JiraClient(
        base_url=cfg.jira.base_url,
        email=cfg.jira.email,
        token=cfg.jira.token,
        project=cfg.jira.project,
    )


def _konflux_client():
    from clients.konflux_client import KonfluxClient
    return KonfluxClient()


@router.get("/jira/{jira_key}")
def get_jira_issue(jira_key: str) -> Dict[str, Any]:
    """Get Jira issue details."""
    from api.validators import validate_jira_key
    jira_key = validate_jira_key(jira_key)
    client = _jira_client()
    if not client:
        from api.errors import config_error
        config_error('Jira not configured', suggestion='Set JIRA_EMAIL and JIRA_TOKEN')
    try:
        status = client.get_issue_status(jira_key)
        comments = client.get_comments(jira_key)
        return {
            'key': jira_key,
            'status': status,
            'comments': comments,
        }
    except Exception as e:
        from api.errors import ICError
        raise ICError(502, 'jira_error', 'Jira API error: {}'.format(str(e)[:200]))


@router.post("/jira")
def create_jira_issue(
    summary: str,
    description: str = '',
    issue_type: str = 'Bug',
    component: Optional[str] = None,
    labels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a Jira issue."""
    client = _jira_client()
    if not client:
        from api.errors import config_error
        config_error('Jira not configured', suggestion='Set JIRA_EMAIL and JIRA_TOKEN')
    try:
        result = client.create_issue(
            summary=summary,
            description_text=description,
            issue_type=issue_type,
            labels=labels or [],
        )
        return result
    except Exception as e:
        from api.errors import ICError
        raise ICError(502, 'jira_error', 'Failed to create Jira issue: {}'.format(str(e)[:200]))


@router.get("/policies/exceptions")
def get_policy_exceptions() -> Dict[str, Any]:
    """Get Conforma policy exceptions (from GitLab release-data)."""
    try:
        client = _konflux_client()
        policies = client.get_ec_policies()
        all_exceptions = []
        for policy in policies:
            exceptions = client.extract_exceptions(policy)
            all_exceptions.extend(exceptions)
        return {'exceptions': all_exceptions, 'total': len(all_exceptions)}
    except Exception as e:
        from api.errors import ICError
        raise ICError(502, 'konflux_error',
                      'Failed to fetch policy exceptions: {}'.format(str(e)[:200]),
                      suggestion='Check KUBECONFIG and cluster connectivity')


@router.get("/policies/bindings")
def get_policy_bindings() -> Dict[str, Any]:
    """Get RPA -> EC policy bindings (from Konflux cluster)."""
    try:
        client = _konflux_client()
        rpas = client.get_release_plan_admissions()
        bindings = []
        for rpa in rpas:
            spec = rpa.get('spec', {})
            bindings.append({
                'name': rpa.get('metadata', {}).get('name', ''),
                'application': spec.get('application', ''),
                'policy': spec.get('policy', ''),
            })
        return {'bindings': bindings, 'total': len(bindings)}
    except Exception as e:
        from api.errors import ICError
        raise ICError(502, 'konflux_error',
                      'Failed to fetch policy bindings: {}'.format(str(e)[:200]),
                      suggestion='Check KUBECONFIG and cluster connectivity')
