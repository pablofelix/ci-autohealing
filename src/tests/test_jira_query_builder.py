"""Tests for Jira blocker query builder."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from clients.jira_query_builder import app_to_jira_versions


@pytest.mark.parametrize("application,expected", [
    ('rhoai-v3-5', ['rhoai-3.5', '3.5 GA RHOAI RELEASE']),
    ('rhoai-v3-5-ea-1', ['rhoai-3.5.EA1', '3.5 EA1 RHOAI RELEASE']),
    ('rhoai-v3-5-ea-2', ['rhoai-3.5.EA2', '3.5 EA2 RHOAI RELEASE']),
    ('rhoai-v3-6', ['rhoai-3.6', '3.6 GA RHOAI RELEASE']),
    ('rhoai-v4-0', ['rhoai-4.0', '4.0 GA RHOAI RELEASE']),
    ('rhoai-v4-0-ea-1', ['rhoai-4.0.EA1', '4.0 EA1 RHOAI RELEASE']),
    ('unknown-app', []),
    ('', []),
    ('rhoai-v3-5-rc-1', ['rhoai-3.5.RC1', '3.5 RC1 RHOAI RELEASE']),
])
def test_app_to_jira_versions(application, expected):
    assert app_to_jira_versions(application) == expected
