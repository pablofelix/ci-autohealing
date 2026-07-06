"""Shared constants used across CLI, API, and MCP layers."""

import os

NAMESPACE = os.environ.get('NAMESPACE', '')
RELENG_NAMESPACE = os.environ.get('RELENG_NAMESPACE', '')
APPLICATION_NAME = os.environ.get('APPLICATION_NAME', '')
KONFLUX_UI_BASE = os.environ.get('KONFLUX_UI_BASE', '')
KUBEARCHIVE_URL = os.environ.get('KUBEARCHIVE_URL', '')
JIRA_BASE_URL = os.environ.get('JIRA_BASE_URL', 'https://issues.redhat.com')
JIRA_PROJECT = os.environ.get('JIRA_PROJECT', 'RHOAIENG')
