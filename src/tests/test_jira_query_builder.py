"""Tests for Jira blocker query builder."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch

from clients.jira_query_builder import app_to_jira_versions, JiraBlockerQuery


@pytest.mark.parametrize(
    "application,expected",
    [
        ("rhoai-v3-5", ["rhoai-3.5", "3.5 GA RHOAI RELEASE"]),
        ("rhoai-v3-5-ea-1", ["rhoai-3.5.EA1", "3.5 EA1 RHOAI RELEASE"]),
        ("rhoai-v3-5-ea-2", ["rhoai-3.5.EA2", "3.5 EA2 RHOAI RELEASE"]),
        ("rhoai-v3-6", ["rhoai-3.6", "3.6 GA RHOAI RELEASE"]),
        ("rhoai-v4-0", ["rhoai-4.0", "4.0 GA RHOAI RELEASE"]),
        ("rhoai-v4-0-ea-1", ["rhoai-4.0.EA1", "4.0 EA1 RHOAI RELEASE"]),
        ("unknown-app", []),
        ("", []),
        ("rhoai-v3-5-rc-1", ["rhoai-3.5.RC1", "3.5 RC1 RHOAI RELEASE"]),
    ],
)
def test_app_to_jira_versions(application, expected):
    assert app_to_jira_versions(application) == expected


class TestJiraBlockerQueryBuilder:
    def test_ga_query_has_version_filter(self):
        jql = JiraBlockerQuery().for_application("rhoai-v3-5").build()
        assert "affectedVersion" in jql
        assert "'rhoai-3.5'" in jql
        assert "'3.5 GA RHOAI RELEASE'" in jql

    def test_ga_query_has_label_exclusions(self):
        jql = JiraBlockerQuery().for_application("rhoai-v3-5").build()
        assert "RHOAI-releases" in jql
        assert "RHOAI-internal" in jql
        assert "devtestops-service" in jql
        assert "test-failed" in jql
        assert "test-skipped" in jql

    def test_ga_query_has_component_exclusions(self):
        jql = JiraBlockerQuery().for_application("rhoai-v3-5").build()
        assert "Documentation" in jql
        assert "PXE" in jql

    def test_ga_query_has_release_blocker_filter(self):
        jql = JiraBlockerQuery().for_application("rhoai-v3-5").build()
        assert "'Release Blocker' != Rejected" in jql

    def test_ga_query_has_status_filter(self):
        jql = JiraBlockerQuery().for_application("rhoai-v3-5").build()
        assert "status not in (Closed, Resolved)" in jql

    def test_ea_query_no_component_exclusions(self):
        jql = JiraBlockerQuery().for_application("rhoai-v3-5-ea-2").build()
        assert "Documentation" not in jql
        assert "PXE" not in jql

    def test_ea_query_has_status_category_filter(self):
        jql = JiraBlockerQuery().for_application("rhoai-v3-5-ea-2").build()
        assert "statusCategory != Done" in jql

    def test_ea_query_has_version_filter(self):
        jql = JiraBlockerQuery().for_application("rhoai-v3-5-ea-2").build()
        assert "'rhoai-3.5.EA2'" in jql
        assert "'3.5 EA2 RHOAI RELEASE'" in jql

    def test_default_projects(self):
        jql = JiraBlockerQuery().for_application("rhoai-v3-5").build()
        assert "RHAIENG" in jql
        assert "RHOAIENG" in jql

    def test_custom_projects_from_env(self):
        with patch.dict(os.environ, {"JIRA_BLOCKER_PROJECTS": "MYPROJ"}):
            jql = JiraBlockerQuery().for_application("rhoai-v3-5").build()
        assert "MYPROJ" in jql
        assert "RHAIENG" not in jql

    def test_no_application_omits_version_filter(self):
        jql = JiraBlockerQuery().build()
        assert "affectedVersion" not in jql
        assert "'Target Version'" not in jql

    def test_unknown_app_omits_version_filter(self):
        jql = JiraBlockerQuery().for_application("unknown").build()
        assert "affectedVersion" not in jql


class TestSignoffCategorization:
    def test_product_signoff_detected(self):
        from clients.jira_query_builder import categorize_blocker
        assert categorize_blocker('[RHOAI 3.5.0-EA2] Product Sign Off - AI Core Platform Team') == 'signoff'

    def test_regular_bug_is_product(self):
        from clients.jira_query_builder import categorize_blocker
        assert categorize_blocker('KServe Local Model Cache stuck in NodeDownloadPending') == 'product'

    def test_infra_bug_detected(self):
        from clients.jira_query_builder import categorize_blocker
        assert categorize_blocker('Jenkins CI cluster timeout on s390x') == 'infra'

    def test_tfa_bug_detected(self):
        from clients.jira_query_builder import categorize_blocker
        assert categorize_blocker('TFA: test-failure in smoke suite') == 'tfa'
