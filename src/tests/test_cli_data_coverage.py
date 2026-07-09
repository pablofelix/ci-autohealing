"""Comprehensive tests for cli/data.py — data access layer.

Tests both cluster mode (API path) and local mode (DB path) for all
~40 functions, covering happy path, error cases, and edge cases.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

from cli.data import (
    _api,
    _app,
    _is_cluster,
    _triage_info_map,
    _triage_info_map_safe,
    _triage_jira_map,
    _triage_jira_map_safe,
    _use_api,
    add_watched_application,
    get_ai_stats,
    get_alerts,
    get_applications,
    get_component_summary,
    get_conforma_details,
    get_conforma_report,
    get_conforma_rules,
    get_conforma_violations,
    get_daily_stats,
    get_dashboard,
    get_export,
    get_failure_details,
    get_failures,
    get_fixes,
    get_jira_issue,
    get_onboarding_describe,
    get_onboarding_status,
    get_overview_stats,
    get_policy_bindings,
    get_policy_exceptions,
    get_release_details,
    get_schedule,
    get_schedule_all,
    get_triage_items,
    get_watched_applications,
    remove_watched_application,
    require_data,
    submit_analysis,
    sync_schedule_from_product_pages,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_api_client():
    """Return a MagicMock that behaves like APIClient."""
    client = MagicMock()
    client.get.return_value = {}
    client.post.return_value = {}
    client.delete.return_value = {}
    return client


# ===================================================================
# _is_cluster / _use_api / _api / _app
# ===================================================================

class TestPrivateHelpers:
    """Tests for _is_cluster, _use_api, _api, _app."""

    @patch('cli.data.ic_config.get_mode', return_value='cluster')
    def test_is_cluster_true(self, _mock):
        assert _is_cluster() is True

    @patch('cli.data.ic_config.get_mode', return_value='local')
    def test_is_cluster_false(self, _mock):
        assert _is_cluster() is False

    @patch('cli.data._is_cluster', return_value=True)
    def test_use_api_cluster_mode(self, _mock):
        assert _use_api() is True

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.mode.has_api', return_value=True)
    def test_use_api_local_with_api(self, _mock_api, _mock_cluster):
        assert _use_api() is True

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.mode.has_api', return_value=False)
    def test_use_api_local_no_api(self, _mock_api, _mock_cluster):
        assert _use_api() is False

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_api_cluster_mode(self, mock_get_client, _mock_cluster):
        sentinel = object()
        mock_get_client.return_value = sentinel
        assert _api() is sentinel

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.api_client.APIClient')
    def test_api_local_mode(self, mock_cls, _mock_cluster):
        result = _api()
        mock_cls.assert_called_once_with('http://localhost:8000')

    @patch('cli.config.APPLICATION_NAME', 'test-app')
    def test_app_returns_config_value(self):
        assert _app() == 'test-app'


# ===================================================================
# _triage_jira_map / _triage_info_map
# ===================================================================

class TestTriageMaps:
    """Tests for _triage_jira_map and _triage_info_map."""

    @patch('cli.db.get_repo')
    def test_triage_jira_map_happy(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_active.return_value = [
            {'jira_key': 'RHOAI-100', 'components': ['comp-a', 'comp-b']},
            {'jira_key': 'RHOAI-101', 'components': ['comp-c']},
            {'components': ['comp-d']},  # no jira_key
        ]
        result = _triage_jira_map('myapp')
        assert result == {
            'comp-a': 'RHOAI-100',
            'comp-b': 'RHOAI-100',
            'comp-c': 'RHOAI-101',
        }

    @patch('cli.db.get_repo')
    def test_triage_jira_map_first_wins(self, mock_get_repo):
        """First jira_key for a component wins (no overwrite)."""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_active.return_value = [
            {'jira_key': 'FIRST', 'components': ['comp-a']},
            {'jira_key': 'SECOND', 'components': ['comp-a']},
        ]
        result = _triage_jira_map('myapp')
        assert result['comp-a'] == 'FIRST'

    @patch('cli.db.get_repo', side_effect=Exception('db down'))
    def test_triage_jira_map_exception_returns_empty(self, _mock):
        assert _triage_jira_map('myapp') == {}

    @patch('cli.db.get_repo')
    def test_triage_info_map_happy(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_active.return_value = [
            {
                'jira_key': 'RHOAI-100',
                'group_label': 'fips',
                'root_cause': 'FIPS check failure',
                'components': ['comp-a'],
            },
        ]
        result = _triage_info_map('myapp')
        assert result == {
            'comp-a': {
                'jira_key': 'RHOAI-100',
                'group_label': 'fips',
                'root_cause': 'FIPS check failure',
            },
        }

    @patch('cli.db.get_repo', side_effect=Exception('db down'))
    def test_triage_info_map_exception_returns_empty(self, _mock):
        assert _triage_info_map('myapp') == {}

    @patch('cli.data._triage_jira_map', return_value={'a': 'JIRA-1'})
    def test_triage_jira_map_safe_happy(self, _mock):
        assert _triage_jira_map_safe('app') == {'a': 'JIRA-1'}

    @patch('cli.data._triage_jira_map', side_effect=Exception('boom'))
    def test_triage_jira_map_safe_error(self, _mock):
        assert _triage_jira_map_safe('app') == {}

    @patch('cli.data._triage_info_map', return_value={'a': {'jira_key': 'X'}})
    def test_triage_info_map_safe_happy(self, _mock):
        assert _triage_info_map_safe('app') == {'a': {'jira_key': 'X'}}

    @patch('cli.data._triage_info_map', side_effect=Exception('boom'))
    def test_triage_info_map_safe_error(self, _mock):
        assert _triage_info_map_safe('app') == {}


# ===================================================================
# require_data
# ===================================================================

class TestRequireData:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data.ic_config.get_api_url', return_value='https://api.example.com')
    def test_cluster_mode_with_url(self, _url, _cluster):
        assert require_data() is True

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data.ic_config.get_api_url', return_value='')
    def test_cluster_mode_no_url(self, _url, _cluster):
        assert require_data() is False

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.require_db', return_value=True)
    def test_local_mode_db_available(self, _db, _cluster):
        assert require_data() is True

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.require_db', return_value=False)
    def test_local_mode_db_unavailable(self, _db, _cluster):
        assert require_data() is False


# ===================================================================
# get_applications
# ===================================================================

class TestGetApplications:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_cluster_mode(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = ['app1', 'app2']
        assert get_applications() == ['app1', 'app2']
        client.get.assert_called_once_with('/api/v1/applications')

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_cluster_mode_none_returns_empty(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        assert get_applications() == []

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.get_repo')
    def test_local_mode(self, mock_get_repo, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_applications.return_value = ['local-app']
        assert get_applications() == ['local-app']


# ===================================================================
# get_overview_stats
# ===================================================================

class TestGetOverviewStats:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='default-app')
    def test_cluster_default_app(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'total': 5}
        assert get_overview_stats() == {'total': 5}
        client.get.assert_called_once_with('/api/v1/applications/default-app/stats')

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_cluster_explicit_app(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'total': 3}
        assert get_overview_stats('my-app') == {'total': 3}
        client.get.assert_called_once_with('/api/v1/applications/my-app/stats')

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='x')
    def test_cluster_none_returns_empty(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        assert get_overview_stats() == {}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.get_repo')
    @patch('cli.data._app', return_value='local-app')
    def test_local_mode(self, _app_mock, mock_get_repo, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_overview_stats.return_value = {'active': 2}
        assert get_overview_stats() == {'active': 2}
        mock_repo.get_overview_stats.assert_called_once_with('local-app')


# ===================================================================
# get_ai_stats
# ===================================================================

class TestGetAiStats:

    @patch('cli.data._use_api', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_api_mode(self, _app_mock, mock_api, _use):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'analyses': 10}
        assert get_ai_stats() == {'analyses': 10}

    @patch('cli.data._use_api', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_api_mode_none_fallback(self, _app_mock, mock_api, _use):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        assert get_ai_stats() == {}

    @patch('cli.data._use_api', return_value=False)
    @patch('cli.db.get_repo')
    @patch('cli.data._app', return_value='app')
    def test_local_mode(self, _app_mock, mock_get_repo, _use):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_extended_status.return_value = {'cost': 1.5}
        assert get_ai_stats() == {'cost': 1.5}


# ===================================================================
# get_alerts — the most complex function
# ===================================================================

class TestGetAlerts:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        expected = {'build_failures': [], 'conforma_violations': []}
        client.get.return_value = expected
        assert get_alerts() == expected

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode_none(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        assert get_alerts() == {}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.data._app', return_value='myapp')
    @patch('cli.db.get_repo')
    @patch('cli.data._triage_jira_map', return_value={})
    @patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={})
    def test_local_mode_empty(self, _exc, _jira, mock_get_repo, _app_mock, _cluster):
        build_repo = MagicMock()
        conforma_repo = MagicMock()

        def repo_factory(cls):
            name = cls.__name__
            if name == 'BuildFailureRepository':
                return build_repo
            return conforma_repo

        mock_get_repo.side_effect = repo_factory
        build_repo.get_triage_summary.return_value = {'failing_components': []}
        conforma_repo.get_violation_summaries.return_value = []

        result = get_alerts()
        assert result['build_failures'] == []
        assert result['conforma_violations'] == []
        assert result['total_count'] == 0
        assert 'last_sync' in result

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.data._app', return_value='myapp')
    @patch('cli.db.get_repo')
    @patch('cli.data._triage_jira_map', return_value={'comp-x': 'JIRA-1'})
    @patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={})
    @patch('conforma.policy_tools.apply_policy_correction')
    @patch('conforma.policy_tools.compute_blocks', return_value='none')
    @patch('conforma.policy_tools.compute_exception_coverage_details',
           return_value={
               'coverage': 'none', 'stage': 'none', 'prod': 'none',
               'env_tag': '', 'policy_url_stage': '', 'policy_url_prod': '',
               'covered_rules_stage': [], 'covered_rules_prod': [],
               'uncovered_rules_stage': [], 'uncovered_rules_prod': [],
           })
    @patch('conforma.policy_tools.count_unique_violations', return_value=(2, []))
    @patch('conforma.policy_tools.extract_violation_rules', return_value=[])
    @patch('conforma.policy_tools.categorize_policy', return_value='release')
    @patch('conforma.policy_tools.policy_url', return_value='http://policy')
    @patch('conforma.policy_tools.policy_env', return_value='stage')
    @patch('conforma.policy_tools.extract_policy_from_scenario', return_value='rhtap')
    def test_local_mode_with_data(self, _ep, _pe, _pu, _cp, _evr, _cuv,
                                   _cecd, _cb, _apc, _exc, _jira,
                                   mock_get_repo, _app_mock, _cluster):
        build_repo = MagicMock()
        conforma_repo = MagicMock()

        def repo_factory(cls):
            name = cls.__name__
            if name == 'BuildFailureRepository':
                return build_repo
            return conforma_repo

        mock_get_repo.side_effect = repo_factory
        build_repo.get_triage_summary.return_value = {
            'failing_components': [{
                'component': 'comp-a',
                'status': 'Failed',
                'error_type': 'build',
                'first_detected_at': '2024-01-01',
                'last_updated_at': '2024-01-02',
                'failure_count': 3,
                'has_logs': True,
                'ai_analyzed': True,
            }],
        }
        conforma_repo.get_violation_summaries.return_value = [{
            'component_name': 'comp-x',
            'scenario': 'rhtap-stage',
            'violation_summary': 'rule1: msg',
            'violations_count': 1,
            'warnings_count': 0,
            'first_detected_at': '2024-01-01',
            'last_updated_at': '2024-01-02',
            'ai_analyzed': False,
        }]

        result = get_alerts()
        assert len(result['build_failures']) == 1
        assert result['build_failures'][0]['component'] == 'comp-a'
        assert result['build_failures'][0]['occurrence_count'] == 3
        assert len(result['conforma_violations']) == 1
        assert result['conforma_violations'][0]['jira_key'] == 'JIRA-1'
        assert result['total_count'] == 2


# ===================================================================
# get_component_summary / get_failure_details
# ===================================================================

class TestComponentSummaryAndFailureDetails:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_component_summary_cluster(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'name': 'comp-a'}
        assert get_component_summary('comp-a') == {'name': 'comp-a'}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.get_repo')
    @patch('cli.data._app', return_value='app')
    def test_component_summary_local(self, _app_mock, mock_get_repo, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_component_summary.return_value = {'comp': 'local'}
        assert get_component_summary('comp-b') == {'comp': 'local'}

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_failure_details_cluster(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'detail': True}
        assert get_failure_details('comp-a') == {'detail': True}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.get_repo')
    @patch('cli.data._app', return_value='app')
    def test_failure_details_local(self, _app_mock, mock_get_repo, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_failure_details.return_value = {'local': True}
        assert get_failure_details('comp-a') == {'local': True}
        mock_repo.get_failure_details.assert_called_once_with('comp-a', 'app')


# ===================================================================
# get_conforma_violations
# ===================================================================

class TestGetConformaViolations:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'conforma_violations': [{'comp': 'x'}]}
        result = get_conforma_violations()
        assert result == [{'comp': 'x'}]

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode_none_alerts(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        result = get_conforma_violations()
        assert result == []

    @patch('cli.data._app', return_value='app')
    @patch('cli.config.app_to_reporter_branch', return_value='rhoai-v2.18')
    @patch('clients.conforma_reporter_client.fetch_reporter_violations',
           return_value=[{'reporter': True}])
    def test_reporter_env_path(self, mock_fetch, _branch, _app_mock):
        result = get_conforma_violations(reporter_env='stage')
        assert result == [{'reporter': True}]
        mock_fetch.assert_called_once_with('rhoai-v2.18', env='stage',
                                           build_type='latest')

    @patch('cli.data._app', return_value='app')
    @patch('cli.config.app_to_reporter_branch', return_value='rhoai-v2.18')
    @patch('clients.conforma_reporter_client.fetch_reporter_violations',
           return_value=[])
    def test_reporter_build_type_path(self, mock_fetch, _branch, _app_mock):
        result = get_conforma_violations(reporter_build_type='nightly')
        assert result == []
        mock_fetch.assert_called_once_with('rhoai-v2.18', env='prod',
                                           build_type='nightly')

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.data._app', return_value='app')
    @patch('cli.db.get_repo')
    @patch('cli.data._triage_jira_map', return_value={})
    @patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={})
    @patch('conforma.policy_tools.enrich_with_coverage')
    @patch('conforma.policy_tools.apply_policy_correction')
    @patch('conforma.policy_tools.compute_blocks', return_value='none')
    @patch('conforma.policy_tools.compute_exception_coverage_details',
           return_value={
               'stage': 'none', 'prod': 'none', 'env_tag': '',
               'covered_rules_stage': [], 'covered_rules_prod': [],
               'uncovered_rules_stage': [], 'uncovered_rules_prod': [],
           })
    @patch('conforma.policy_tools.count_unique_violations', return_value=(0, []))
    @patch('conforma.policy_tools.extract_violation_rules', return_value=[])
    @patch('conforma.policy_tools.policy_url', return_value='')
    @patch('conforma.policy_tools.policy_env', return_value='')
    @patch('conforma.policy_tools.extract_policy_from_scenario', return_value='')
    def test_local_mode(self, _ep, _pe, _pu, _evr, _cuv, _cecd, _cb,
                        _apc, _ewc, _exc, _jira, mock_get_repo,
                        _app_mock, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_violation_summaries.return_value = [{
            'component_name': 'comp-a',
            'scenario': 'rhtap',
            'violation_summary': '',
        }]
        result = get_conforma_violations()
        assert len(result) == 1
        assert result[0]['component'] == 'comp-a'


# ===================================================================
# get_conforma_rules
# ===================================================================

class TestGetConformaRules:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode_list(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = [{'rule': 'r1', 'count': 2}]
        result = get_conforma_rules()
        assert result == [{'rule': 'r1', 'count': 2}]

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode_non_list(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = 'error string'
        result = get_conforma_rules()
        assert result == []

    @patch('cli.data._app', return_value='app')
    @patch('cli.config.app_to_reporter_branch', return_value='branch')
    @patch('clients.conforma_reporter_client.fetch_reporter_rules',
           return_value=[{'rule': 'r1'}])
    def test_reporter_path(self, mock_fetch, _branch, _app_mock):
        result = get_conforma_rules(reporter_env='stage')
        assert result == [{'rule': 'r1'}]

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.data.get_conforma_violations', return_value=[
        {'component_name': 'comp-a', 'violation_summary': 'deny - r1\ndeny - r2'},
        {'component_name': 'comp-b', 'violation_summary': 'deny - r1'},
    ])
    @patch('conforma.policy_tools.count_unique_violations')
    @patch('cli.data._app', return_value='app')
    def test_local_mode_aggregation(self, _app_mock, mock_cuv, _violations, _cluster):
        mock_cuv.side_effect = [
            (2, [{'rule': 'r1'}, {'rule': 'r2'}]),
            (1, [{'rule': 'r1'}]),
        ]
        result = get_conforma_rules()
        # r1 appears in 2 components, r2 in 1
        assert result[0]['rule'] == 'r1'
        assert result[0]['count'] == 2
        assert result[1]['rule'] == 'r2'
        assert result[1]['count'] == 1


# ===================================================================
# get_conforma_details
# ===================================================================

class TestGetConformaDetails:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'violations': []}
        assert get_conforma_details('comp-a') == {'violations': []}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.get_repo')
    @patch('cli.data._app', return_value='app')
    def test_local_mode(self, _app_mock, mock_get_repo, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_violation_details.return_value = {'local': True}
        assert get_conforma_details('comp-a') == {'local': True}
        mock_repo.get_violation_details.assert_called_once_with('comp-a', 'app')


# ===================================================================
# get_triage_items
# ===================================================================

class TestGetTriageItems:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_first_path_success(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'items': [{'id': 1}]}
        result = get_triage_items()
        assert result == {'items': [{'id': 1}]}

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_fallback_to_second_path(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        # First call returns no 'items' key, second succeeds
        client.get.side_effect = [
            {'something': 'else'},
            {'items': [{'id': 2}]},
        ]
        result = get_triage_items()
        assert result == {'items': [{'id': 2}]}

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    @patch('cli.db.get_repo')
    def test_cluster_api_raises_exception(self, mock_get_repo,
                                          _app_mock, mock_api, _cluster):
        """Cover the except branch (lines 373-374) when _api().get raises."""
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.side_effect = Exception('network error')
        # After both API paths raise, falls through to local DB
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_report.return_value = []
        mock_repo.get_summary.return_value = {}
        result = get_triage_items()
        assert result['items'] == []

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    @patch('cli.db.get_repo')
    def test_cluster_both_fail_fallback_to_local(self, mock_get_repo,
                                                  _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.side_effect = [None, None]
        # Falls through to local DB try block
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_report.return_value = [{'item': 1}]
        mock_repo.get_summary.return_value = {'total': 1}
        result = get_triage_items()
        assert result['items'] == [{'item': 1}]
        assert result['application'] == 'app'

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.data._app', return_value='app')
    @patch('cli.db.get_repo')
    def test_local_mode(self, mock_get_repo, _app_mock, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_report.return_value = [{'x': 1}]
        mock_repo.get_summary.return_value = {'s': 2}
        result = get_triage_items()
        assert result == {'application': 'app', 'summary': {'s': 2}, 'items': [{'x': 1}]}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.data._app', return_value='app')
    @patch('cli.db.get_repo', side_effect=Exception('db fail'))
    def test_local_mode_exception(self, _repo, _app_mock, _cluster):
        result = get_triage_items()
        assert result == {'items': []}


# ===================================================================
# get_daily_stats
# ===================================================================

class TestGetDailyStats:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = [{'day': '2024-01-01'}]
        assert get_daily_stats() == [{'day': '2024-01-01'}]

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode_none(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        assert get_daily_stats() == []

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.get_repo')
    @patch('cli.data._app', return_value='app')
    def test_local_mode(self, _app_mock, mock_get_repo, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_daily_stats.return_value = [{'day': 'local'}]
        assert get_daily_stats() == [{'day': 'local'}]


# ===================================================================
# get_fixes
# ===================================================================

class TestGetFixes:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_cluster_mode_no_all(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = [{'fix': 1}]
        result = get_fixes()
        assert result == [{'fix': 1}]
        client.get.assert_called_once_with('/api/v1/fixes', params={})

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_cluster_mode_show_all(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = [{'fix': 1}, {'fix': 2}]
        result = get_fixes(show_all=True)
        client.get.assert_called_once_with('/api/v1/fixes', params={'all': 'true'})

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_cluster_mode_none(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        assert get_fixes() == []

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.get_repo')
    def test_local_mode(self, mock_get_repo, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_all.return_value = [{'local_fix': True}]
        assert get_fixes() == [{'local_fix': True}]


# ===================================================================
# get_conforma_report
# ===================================================================

class TestGetConformaReport:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'report': True}
        assert get_conforma_report() == {'report': True}

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode_none(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        assert get_conforma_report() == {}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.get_repo')
    @patch('cli.data._app', return_value='app')
    def test_local_mode(self, _app_mock, mock_get_repo, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_standup_report.return_value = {'standup': 1}
        assert get_conforma_report() == {'standup': 1}


# ===================================================================
# get_failures
# ===================================================================

class TestGetFailures:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = [{'comp': 'a'}]
        assert get_failures() == [{'comp': 'a'}]

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode_none(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        assert get_failures() == []

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.db.get_repo')
    @patch('cli.data._app', return_value='app')
    def test_local_mode(self, _app_mock, mock_get_repo, _cluster):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_triage_summary.return_value = {
            'failing_components': [{'component': 'x'}]
        }
        assert get_failures() == [{'component': 'x'}]


# ===================================================================
# get_dashboard
# ===================================================================

class TestGetDashboard:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'dash': True}
        assert get_dashboard() == {'dash': True}

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode_none(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        assert get_dashboard() == {}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.data._app', return_value='app')
    def test_local_mode_returns_empty(self, _app_mock, _cluster):
        assert get_dashboard() == {}


# ===================================================================
# get_export
# ===================================================================

class TestGetExport:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode_default_fmt(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'text': 'exported'}
        result = get_export('comp-a')
        assert result == {'text': 'exported'}
        client.get.assert_called_once_with(
            '/api/v1/applications/app/export/comp-a',
            params={'format': 'slack'})

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode_custom_fmt(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'md': True}
        result = get_export('comp-a', fmt='jira')
        client.get.assert_called_once_with(
            '/api/v1/applications/app/export/comp-a',
            params={'format': 'jira'})

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.data._app', return_value='app')
    def test_local_mode_returns_none(self, _app_mock, _cluster):
        assert get_export('comp') is None


# ===================================================================
# get_schedule / get_schedule_all / sync_schedule_from_product_pages
# ===================================================================

class TestScheduleFunctions:

    @patch('cli.data._use_api', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_schedule_api(self, _app_mock, mock_api, _use):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'milestones': []}
        assert get_schedule() == {'milestones': []}

    @patch('cli.data._use_api', return_value=False)
    @patch('cli.data._app', return_value='app')
    def test_schedule_no_api(self, _app_mock, _use):
        assert get_schedule() is None

    @patch.dict(os.environ, {'WATCH_APPLICATIONS': 'app1 app2'})
    @patch('cli.data.get_schedule')
    def test_schedule_all_with_env(self, mock_sched):
        mock_sched.side_effect = [{'app': 'app1'}, {'app': 'app2'}]
        result = get_schedule_all()
        assert len(result) == 2

    @patch.dict(os.environ, {'WATCH_APPLICATIONS': 'app1 app2'})
    @patch('cli.data.get_schedule')
    def test_schedule_all_filters_none(self, mock_sched):
        mock_sched.side_effect = [{'app': 'app1'}, None]
        result = get_schedule_all()
        assert len(result) == 1

    @patch.dict(os.environ, {'WATCH_APPLICATIONS': ''})
    @patch('cli.data._app', return_value='default-app')
    @patch('cli.data.get_schedule')
    def test_schedule_all_no_env_uses_app(self, mock_sched, _app_mock):
        mock_sched.return_value = {'app': 'default-app'}
        result = get_schedule_all()
        assert len(result) == 1
        mock_sched.assert_called_once_with('default-app')

    @patch.dict(os.environ, {'WATCH_APPLICATIONS': ''})
    @patch('cli.data._app', return_value='')
    def test_schedule_all_no_env_no_app(self, _app_mock):
        result = get_schedule_all()
        assert result == []

    @patch('cli.data._use_api', return_value=True)
    @patch('cli.data._api')
    def test_sync_schedule(self, mock_api, _use):
        client = _mock_api_client()
        mock_api.return_value = client
        client.post.return_value = {'synced': 3}
        result = sync_schedule_from_product_pages({'data': 'pp'})
        assert result == {'synced': 3}
        client.post.assert_called_once_with(
            '/api/v1/releases/schedule/sync', data={'data': 'pp'})

    @patch('cli.data._use_api', return_value=False)
    def test_sync_schedule_no_api(self, _use):
        assert sync_schedule_from_product_pages({'data': 'pp'}) is None


# ===================================================================
# get_release_details
# ===================================================================

class TestGetReleaseDetails:

    @patch('cli.data._use_api', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_api_mode(self, _app_mock, mock_api, _use):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'release': 'v1'}
        result = get_release_details('v1')
        assert result == {'release': 'v1'}
        client.get.assert_called_once_with(
            '/api/v1/applications/app/releases/v1', params={})

    @patch('cli.data._use_api', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_api_mode_with_artifacts(self, _app_mock, mock_api, _use):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'release': 'v1', 'artifacts': []}
        result = get_release_details('v1', include_artifacts=True)
        client.get.assert_called_once_with(
            '/api/v1/applications/app/releases/v1',
            params={'include_artifacts': 'true'})

    @patch('cli.data._use_api', return_value=False)
    @patch('cli.data._app', return_value='app')
    @patch('api.routes.releases._fetch_release_cr')
    @patch('api.routes.releases._build_release_details')
    @patch('cli.config.NAMESPACE', 'test-ns')
    def test_local_mode(self, mock_build, mock_fetch, _app_mock, _use):
        mock_fetch.return_value = {'kind': 'Release'}
        mock_build.return_value = {'name': 'v1', 'status': 'running'}
        result = get_release_details('v1')
        assert result == {'name': 'v1', 'status': 'running'}
        mock_fetch.assert_called_once_with('v1', 'test-ns')

    @patch('cli.data._use_api', return_value=False)
    @patch('cli.data._app', return_value='app')
    @patch('api.routes.releases._fetch_release_cr', side_effect=Exception('not found'))
    @patch('cli.config.NAMESPACE', 'test-ns')
    def test_local_mode_exception(self, mock_fetch, _app_mock, _use):
        result = get_release_details('v1')
        assert result is None


# ===================================================================
# get_jira_issue
# ===================================================================

class TestGetJiraIssue:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_cluster_mode(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'key': 'RHOAI-123'}
        assert get_jira_issue('RHOAI-123') == {'key': 'RHOAI-123'}

    @patch('cli.data._is_cluster', return_value=False)
    def test_local_mode(self, _cluster):
        assert get_jira_issue('RHOAI-123') is None


# ===================================================================
# get_policy_exceptions / get_policy_bindings
# ===================================================================

class TestPolicyFunctions:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_exceptions_cluster(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'exceptions': [], 'total': 0}
        assert get_policy_exceptions() == {'exceptions': [], 'total': 0}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.config.NAMESPACE', 'ns')
    @patch('clients.konflux_client.KonfluxClient')
    def test_exceptions_local(self, mock_kc_cls, _cluster):
        mock_client = MagicMock()
        mock_kc_cls.return_value = mock_client
        mock_client.get_ec_policies.return_value = [{'policy': 1}]
        mock_client.extract_exceptions.return_value = [{'rule': 'r1'}]
        result = get_policy_exceptions()
        assert result == {'exceptions': [{'rule': 'r1'}], 'total': 1}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.config.NAMESPACE', 'ns')
    @patch('clients.konflux_client.KonfluxClient', side_effect=Exception('fail'))
    def test_exceptions_local_error(self, _kc, _cluster):
        assert get_policy_exceptions() is None

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_bindings_cluster(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'bindings': [], 'total': 0}
        assert get_policy_bindings() == {'bindings': [], 'total': 0}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.config.NAMESPACE', 'ns')
    @patch('clients.konflux_client.KonfluxClient')
    def test_bindings_local(self, mock_kc_cls, _cluster):
        mock_client = MagicMock()
        mock_kc_cls.return_value = mock_client
        mock_client.get_release_plan_admissions.return_value = [{'b': 1}]
        result = get_policy_bindings()
        assert result == {'bindings': [{'b': 1}], 'total': 1}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.config.NAMESPACE', 'ns')
    @patch('clients.konflux_client.KonfluxClient', side_effect=Exception('fail'))
    def test_bindings_local_error(self, _kc, _cluster):
        assert get_policy_bindings() is None


# ===================================================================
# submit_analysis
# ===================================================================

class TestSubmitAnalysis:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_cluster_mode(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.post.return_value = {'id': 42}
        data = {'root_cause': 'deps'}
        result = submit_analysis('comp-a', data)
        assert result == {'id': 42}
        client.post.assert_called_once_with(
            '/api/v1/applications/app/analyses', data)

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.data._app', return_value='app')
    def test_local_mode(self, _app_mock, _cluster):
        assert submit_analysis('comp-a', {}) is None


# ===================================================================
# get_watched_applications / add / remove
# ===================================================================

class TestWatchedApplications:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_get_cluster(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'applications': ['a', 'b']}
        assert get_watched_applications() == ['a', 'b']

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_get_cluster_none(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = None
        assert get_watched_applications() == []

    @patch('cli.data._is_cluster', return_value=False)
    @patch.dict(os.environ, {'WATCH_APPLICATIONS': 'app1 app2'})
    def test_get_local(self, _cluster):
        assert get_watched_applications() == ['app1', 'app2']

    @patch('cli.data._is_cluster', return_value=False)
    @patch.dict(os.environ, {'WATCH_APPLICATIONS': ''})
    def test_get_local_empty(self, _cluster):
        # ''.split() returns [], not ['']
        assert get_watched_applications() == []

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_add_cluster(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.post.return_value = {'applications': ['a', 'new']}
        result = add_watched_application('new')
        assert result == ['a', 'new']

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_add_cluster_none(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.post.return_value = None
        assert add_watched_application('new') == []

    @patch('cli.data._is_cluster', return_value=False)
    def test_add_local(self, _cluster):
        assert add_watched_application('new') is None

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_remove_cluster(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.delete.return_value = {'applications': ['a']}
        result = remove_watched_application('b')
        assert result == ['a']
        client.delete.assert_called_once_with('/api/v1/config/applications/b')

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    def test_remove_cluster_none(self, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.delete.return_value = None
        assert remove_watched_application('b') == []

    @patch('cli.data._is_cluster', return_value=False)
    def test_remove_local(self, _cluster):
        assert remove_watched_application('b') is None


# ===================================================================
# get_onboarding_status / get_onboarding_describe
# ===================================================================

class TestOnboarding:

    @patch('cli.data._is_cluster', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_status_api(self, _app_mock, mock_api, _cluster):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'ready': 5, 'pending': 2}
        assert get_onboarding_status() == {'ready': 5, 'pending': 2}

    @patch('cli.data._is_cluster', return_value=False)
    @patch('cli.data._app', return_value='app')
    def test_status_local_mode(self, _app_mock, _cluster):
        result = get_onboarding_status()
        assert isinstance(result, dict)
        assert 'application' in result

    @patch('cli.data._use_api', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_describe_api(self, _app_mock, mock_api, _use):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'component': 'comp-a', 'status': 'ready'}
        result = get_onboarding_describe('comp-a')
        assert result == {'component': 'comp-a', 'status': 'ready'}
        client.get.assert_called_once_with(
            '/api/v1/applications/app/onboarding/comp-a', params=None)

    @patch('cli.data._use_api', return_value=True)
    @patch('cli.data._api')
    @patch('cli.data._app', return_value='app')
    def test_describe_api_with_diff(self, _app_mock, mock_api, _use):
        client = _mock_api_client()
        mock_api.return_value = client
        client.get.return_value = {'diff': 'some diff'}
        result = get_onboarding_describe('comp-a', diff=True)
        client.get.assert_called_once_with(
            '/api/v1/applications/app/onboarding/comp-a',
            params={'diff': 'true'})

    @patch('cli.data._use_api', return_value=False)
    @patch('cli.data._app', return_value='app')
    def test_describe_no_api(self, _app_mock, _use):
        assert get_onboarding_describe('comp-a') is None
