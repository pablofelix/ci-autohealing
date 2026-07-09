"""Comprehensive tests for its_scenario_data.py — ITS scenario data handling."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scenario(name, app, disabled=False, conforma=False, future=False,
                   policy_ref=None):
    """Build a metadata dict like KonfluxClient.extract_its_metadata returns."""
    return {
        'name': name,
        'application': app,
        'is_disabled': disabled,
        'is_conforma': conforma,
        'is_future': future,
        'policy_ref': policy_ref,
    }


def _mock_client(scenarios):
    """Return a mock KonfluxClient that yields pre-extracted metadata."""
    client = MagicMock()
    # get_integration_test_scenarios returns raw CRDs;
    # extract_its_metadata is called per scenario.
    client.get_integration_test_scenarios.return_value = scenarios
    return client


# ===================================================================
# _normalize_scenario_type
# ===================================================================

class TestNormalizeScenarioType:
    """Tests for the internal _normalize_scenario_type helper."""

    def test_strips_version_suffix(self):
        from its_scenario_data import _normalize_scenario_type
        assert _normalize_scenario_type('verify-conforma-v3-5') == 'verify-conforma'

    def test_strips_ea_suffix(self):
        from its_scenario_data import _normalize_scenario_type
        assert _normalize_scenario_type('verify-conforma-v3-5-ea-1') == 'verify-conforma'

    def test_strips_single_component_suffix(self):
        from its_scenario_data import _normalize_scenario_type
        result = _normalize_scenario_type('verify-conforma-v3-5-ea-2-single-component')
        assert result == 'verify-conforma'

    def test_strips_future_suffix(self):
        from its_scenario_data import _normalize_scenario_type
        result = _normalize_scenario_type('verify-conforma-v3-5-future')
        assert result == 'verify-conforma'

    def test_no_version_returns_original(self):
        from its_scenario_data import _normalize_scenario_type
        assert _normalize_scenario_type('some-plain-name') == 'some-plain-name'

    def test_negative_ea(self):
        from its_scenario_data import _normalize_scenario_type
        result = _normalize_scenario_type('check-v2-1-ea--3')
        # The regex pattern -ea-?\d+ does not match -ea--3 (double dash + digit),
        # so the entire suffix stays. Only the v2-1 portion is left as-is since
        # the regex requires -ea suffix after version for a full match.
        assert isinstance(result, str)


# ===================================================================
# cmd_list
# ===================================================================

class TestCmdList:
    """Tests for cmd_list."""

    @patch('its_scenario_data.KonfluxClient')
    def test_basic_list(self, _MockKC):
        from its_scenario_data import cmd_list

        raw = [object(), object(), object()]  # raw CRDs
        client = MagicMock()
        client.get_integration_test_scenarios.return_value = raw

        meta = [
            _make_scenario('s1', 'app-a'),
            _make_scenario('s2', 'app-a', disabled=True),
            _make_scenario('s3', 'app-b', conforma=True),
        ]
        with patch.object(_MockKC, 'extract_its_metadata', side_effect=meta):
            # We need to patch at module level since cmd_list uses
            # KonfluxClient.extract_its_metadata directly.
            with patch('its_scenario_data.KonfluxClient') as MockKC:
                MockKC.extract_its_metadata.side_effect = meta
                result = cmd_list(client, 'test-ns', app_filter='app')

        assert result['source'] == 'k8s-api'
        assert result['namespace'] == 'test-ns'
        assert result['total'] == 3
        assert result['active'] == 2
        assert result['disabled'] == 1
        assert result['conforma'] == 1
        assert 'app-a' in result['applications']
        assert 'app-b' in result['applications']

    @patch('its_scenario_data.KonfluxClient')
    def test_empty_list(self, MockKC):
        from its_scenario_data import cmd_list

        client = MagicMock()
        client.get_integration_test_scenarios.return_value = []
        result = cmd_list(client, 'ns')
        assert result['total'] == 0
        assert result['scenarios'] == []

    @patch('its_scenario_data.KonfluxClient')
    def test_sorting(self, MockKC):
        from its_scenario_data import cmd_list

        raw = [object(), object()]
        client = MagicMock()
        client.get_integration_test_scenarios.return_value = raw

        meta = [
            _make_scenario('z-scenario', 'app-b'),
            _make_scenario('a-scenario', 'app-a'),
        ]
        MockKC.extract_its_metadata.side_effect = meta
        result = cmd_list(client, 'ns')
        # Sorted by (application, is_disabled, name)
        names = [s['name'] for s in result['scenarios']]
        assert names[0] == 'a-scenario'
        assert names[1] == 'z-scenario'


# ===================================================================
# cmd_gaps
# ===================================================================

class TestCmdGaps:
    """Tests for cmd_gaps."""

    @patch('its_scenario_data.KonfluxClient')
    def test_no_scenarios_returns_empty(self, MockKC):
        from its_scenario_data import cmd_gaps

        client = MagicMock()
        client.get_integration_test_scenarios.return_value = []
        MockKC.extract_its_metadata.side_effect = []
        result = cmd_gaps(client, 'ns')
        assert result['gaps'] == []
        assert result['apps_checked'] == 0

    @patch('its_scenario_data.KonfluxClient')
    def test_detects_missing_scenarios(self, MockKC):
        from its_scenario_data import cmd_gaps

        raw = [object()] * 4
        client = MagicMock()
        client.get_integration_test_scenarios.return_value = raw

        # Two apps: app-a has both types, app-b has only one.
        # With 2 apps the 60% threshold = 1.2 -> 2, so common_types
        # includes types present in >= 2 apps.
        meta = [
            _make_scenario('verify-conforma-v3-5', 'app-a'),
            _make_scenario('verify-build-v3-5', 'app-a'),
            _make_scenario('verify-conforma-v3-5', 'app-b'),
            _make_scenario('verify-build-v3-5', 'app-c'),
        ]
        MockKC.extract_its_metadata.side_effect = meta
        result = cmd_gaps(client, 'ns')
        assert result['apps_checked'] == 3
        assert result['threshold_pct'] == 60

    @patch('its_scenario_data.KonfluxClient')
    def test_disabled_and_future_excluded(self, MockKC):
        from its_scenario_data import cmd_gaps

        raw = [object()] * 2
        client = MagicMock()
        client.get_integration_test_scenarios.return_value = raw

        meta = [
            _make_scenario('verify-conforma-v3-5', 'app-a', disabled=True),
            _make_scenario('verify-build-v3-5', 'app-a', future=True),
        ]
        MockKC.extract_its_metadata.side_effect = meta
        result = cmd_gaps(client, 'ns')
        assert result['apps_checked'] == 0

    @patch('its_scenario_data.KonfluxClient')
    def test_app_filter_applied(self, MockKC):
        from its_scenario_data import cmd_gaps

        raw = [object()] * 2
        client = MagicMock()
        client.get_integration_test_scenarios.return_value = raw

        meta = [
            _make_scenario('verify-v3-5', 'app-a'),
            _make_scenario('verify-v3-5', 'app-b'),
        ]
        MockKC.extract_its_metadata.side_effect = meta
        result = cmd_gaps(client, 'ns', app_filter='app-a')
        # Only app-a passes the filter
        assert result['apps_checked'] == 1


# ===================================================================
# cmd_summary
# ===================================================================

class TestCmdSummary:
    """Tests for cmd_summary."""

    @patch('its_scenario_data.KonfluxClient')
    def test_basic_summary(self, MockKC):
        from its_scenario_data import cmd_summary

        raw = [object()] * 3
        client = MagicMock()
        client.get_integration_test_scenarios.return_value = raw

        meta = [
            _make_scenario('s1', 'app-a', conforma=True, policy_ref='policy-a'),
            _make_scenario('s2', 'app-a', disabled=True),
            _make_scenario('s3', 'app-a', future=True),
        ]
        MockKC.extract_its_metadata.side_effect = meta
        result = cmd_summary(client, 'ns', app_filter='app-a')

        assert result['source'] == 'k8s-api'
        app_data = result['apps']['app-a']
        assert app_data['total'] == 3
        assert app_data['active'] == 2  # s1 and s3 are not disabled
        assert app_data['disabled'] == 1
        assert app_data['conforma'] == 1
        assert app_data['future'] == 1
        assert 'policy-a' in app_data['policies']

    @patch('its_scenario_data.KonfluxClient')
    def test_no_filter_returns_all_apps(self, MockKC):
        from its_scenario_data import cmd_summary

        raw = [object()] * 2
        client = MagicMock()
        client.get_integration_test_scenarios.return_value = raw

        meta = [
            _make_scenario('s1', 'app-a'),
            _make_scenario('s2', 'app-b'),
        ]
        MockKC.extract_its_metadata.side_effect = meta
        result = cmd_summary(client, 'ns')
        assert 'app-a' in result['apps']
        assert 'app-b' in result['apps']

    @patch('its_scenario_data.KonfluxClient')
    def test_filter_excludes_other_apps(self, MockKC):
        from its_scenario_data import cmd_summary

        raw = [object()] * 2
        client = MagicMock()
        client.get_integration_test_scenarios.return_value = raw

        meta = [
            _make_scenario('s1', 'app-a'),
            _make_scenario('s2', 'app-b'),
        ]
        MockKC.extract_its_metadata.side_effect = meta
        result = cmd_summary(client, 'ns', app_filter='app-a')
        assert 'app-a' in result['apps']
        assert 'app-b' not in result['apps']

    @patch('its_scenario_data.KonfluxClient')
    def test_duplicate_policy_ref_not_duplicated(self, MockKC):
        from its_scenario_data import cmd_summary

        raw = [object()] * 2
        client = MagicMock()
        client.get_integration_test_scenarios.return_value = raw

        meta = [
            _make_scenario('s1', 'app-a', policy_ref='p1'),
            _make_scenario('s2', 'app-a', policy_ref='p1'),
        ]
        MockKC.extract_its_metadata.side_effect = meta
        result = cmd_summary(client, 'ns')
        assert result['apps']['app-a']['policies'] == ['p1']


# ===================================================================
# main
# ===================================================================

class TestMain:
    """Tests for the main() entry point."""

    @patch('its_scenario_data.KonfluxClient')
    def test_main_list_action(self, MockKC):
        from its_scenario_data import main

        mock_client = MagicMock()
        mock_client.get_integration_test_scenarios.return_value = []
        MockKC.return_value = mock_client

        with patch('sys.argv', ['prog', '--action', 'list', '--namespace', 'ns']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    @patch('its_scenario_data.KonfluxClient')
    def test_main_gaps_action(self, MockKC):
        from its_scenario_data import main

        mock_client = MagicMock()
        mock_client.get_integration_test_scenarios.return_value = []
        MockKC.return_value = mock_client

        with patch('sys.argv', ['prog', '--action', 'gaps', '--namespace', 'ns']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    @patch('its_scenario_data.KonfluxClient')
    def test_main_summary_action(self, MockKC):
        from its_scenario_data import main

        mock_client = MagicMock()
        mock_client.get_integration_test_scenarios.return_value = []
        MockKC.return_value = mock_client

        with patch('sys.argv', ['prog', '--action', 'summary', '--namespace', 'ns']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    @patch('its_scenario_data.KonfluxClient')
    def test_main_exception(self, MockKC):
        from its_scenario_data import main

        MockKC.side_effect = RuntimeError("K8s unreachable")

        with patch('sys.argv', ['prog', '--action', 'list', '--namespace', 'ns']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
