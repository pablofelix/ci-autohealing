"""Shared constants used across CLI, API, and MCP layers."""

import os

NAMESPACE = os.environ.get('NAMESPACE', '')
RELENG_NAMESPACE = os.environ.get('RELENG_NAMESPACE', '')
APPLICATION_NAME = os.environ.get('APPLICATION_NAME', '')
KONFLUX_UI_BASE = os.environ.get('KONFLUX_UI_BASE', '')
KUBEARCHIVE_URL = os.environ.get('KUBEARCHIVE_URL', '')
SYSTEM_MAP_URL = os.environ.get('SYSTEM_MAP_URL', 'http://localhost:3001')
JIRA_BASE_URL = os.environ.get('JIRA_BASE_URL', 'https://issues.redhat.com')
JIRA_PROJECT = os.environ.get('JIRA_PROJECT', 'RHOAIENG')


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
