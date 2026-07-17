"""Shared constants used across CLI, API, and MCP layers."""

import functools
import os

NAMESPACE = os.environ.get('NAMESPACE', '')
RELENG_NAMESPACE = os.environ.get('RELENG_NAMESPACE', '')
APPLICATION_NAME = os.environ.get('APPLICATION_NAME', '')
KONFLUX_UI_BASE = os.environ.get('KONFLUX_UI_BASE', '')
KUBEARCHIVE_URL = os.environ.get('KUBEARCHIVE_URL', '')
SYSTEM_MAP_URL = os.environ.get('SYSTEM_MAP_URL', 'http://localhost:3001')
JIRA_BASE_URL = os.environ.get('JIRA_BASE_URL', 'https://issues.redhat.com')
JIRA_PROJECT = os.environ.get('JIRA_PROJECT', 'RHOAIENG')


def require_namespace(fn):
    """Guard: return error dict if NAMESPACE is not configured."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not NAMESPACE:
            return {"error": "NAMESPACE not configured. Set NAMESPACE=rhoai-tenant in .env"}
        return fn(*args, **kwargs)
    return wrapper


def validate_config():
    """Check all required env vars and return status per check.

    Returns dict keyed by check name, each with 'status' (ok/warn/fail)
    and 'hint' (fix suggestion when not ok).
    """
    checks = {}

    checks['namespace'] = (
        {'status': 'ok', 'value': NAMESPACE}
        if NAMESPACE
        else {'status': 'fail', 'hint': 'Set NAMESPACE=rhoai-tenant in .env'}
    )

    checks['releng_namespace'] = (
        {'status': 'ok', 'value': RELENG_NAMESPACE}
        if RELENG_NAMESPACE
        else {'status': 'warn', 'hint': 'Set RELENG_NAMESPACE=rhtap-releng-tenant in .env (needed for release queries)'}
    )

    checks['application'] = (
        {'status': 'ok', 'value': APPLICATION_NAME}
        if APPLICATION_NAME
        else {'status': 'warn', 'hint': 'Set APPLICATION_NAME in .env'}
    )

    checks['konflux_ui'] = (
        {'status': 'ok', 'value': KONFLUX_UI_BASE}
        if KONFLUX_UI_BASE
        else {'status': 'warn', 'hint': 'Set KONFLUX_UI_BASE for build URLs in triage reports'}
    )

    return checks


def make_konflux_pipelinerun_url(pipelinerun_name):
    """Build the Konflux UI URL for a PipelineRun logs page.

    Pure function — returns empty string when any required config is missing
    so callers can safely display it only when truthy.

    >>> make_konflux_pipelinerun_url('')
    ''
    """
    if not pipelinerun_name or not KONFLUX_UI_BASE or not NAMESPACE:
        return ''
    return '{}/ns/{}/pipelinerun/{}/logs'.format(
        KONFLUX_UI_BASE.rstrip('/'), NAMESPACE, pipelinerun_name)


def make_system_map_url(component_name):
    """Build a System Map URL that highlights a specific component node.

    >>> make_system_map_url('')
    ''
    """
    if not component_name or not SYSTEM_MAP_URL:
        return ''
    return '{}/?highlight=comp-{}'.format(
        SYSTEM_MAP_URL.rstrip('/'), component_name)
