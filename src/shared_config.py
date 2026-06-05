"""Shared constants used across CLI, API, and MCP layers."""

import os

NAMESPACE = os.environ.get('NAMESPACE', '')
APPLICATION_NAME = os.environ.get('APPLICATION_NAME', '')
KONFLUX_UI_BASE = os.environ.get('KONFLUX_UI_BASE', '')
KUBEARCHIVE_URL = os.environ.get('KUBEARCHIVE_URL', '')
