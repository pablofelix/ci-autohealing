"""Comprehensive tests for ec_policy_data module."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
from unittest.mock import MagicMock, call, patch

import pytest

from ec_policy_data import cmd_bindings, cmd_exceptions, cmd_expiring, cmd_policy_gap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client():
    """Create a MagicMock KonfluxClient with sensible defaults."""
    client = MagicMock()
    client.get_ec_policies.return_value = []
    client.get_release_plan_admissions.return_value = []
    client.extract_exceptions.return_value = []
    return client


def _make_policy(name):
    """Create a minimal policy dict."""
    return {'metadata': {'name': name}, 'spec': {'sources': []}}


def _make_exception(value, days_left, permanent=False, reference='', image_url=''):
    """Create an exception dict as returned by extract_exceptions."""
    return {
        'value': value,
        'days_left': days_left,
        'permanent': permanent,
        'reference': reference,
        'imageUrl': image_url,
    }


def _make_binding(rpa_name, application, policy, target):
    """Create a binding dict as returned by extract_rpa_bindings."""
    return {
        'rpa_name': rpa_name,
        'application': application,
        'policy': policy,
        'target': target,
    }


# ═══════════════════════════════════════════════════════════════════════
# cmd_exceptions tests
# ═══════════════════════════════════════════════════════════════════════

class TestCmdExceptions:
    """Tests for cmd_exceptions function."""

    def test_no_policies(self):
        client = _make_client()
        result = cmd_exceptions(client)

        assert result['source'] == 'k8s-api'
        assert result['policies_count'] == 0
        assert result['total_exceptions'] == 0
        assert result['active_exceptions'] == 0
        assert result['exceptions'] == []
        client.get_ec_policies.assert_called_once_with(name_filter=None)

    def test_single_policy_mixed_active_and_expired(self):
        client = _make_client()
        policy = _make_policy('rhoai-policy')
        client.get_ec_policies.return_value = [policy]

        active_permanent = _make_exception('rule.permanent', days_left=None, permanent=True)
        active_temp = _make_exception('rule.active', days_left=10)
        active_zero = _make_exception('rule.today', days_left=0)
        expired = _make_exception('rule.expired', days_left=-5)

        client.extract_exceptions.return_value = [
            active_permanent, active_temp, active_zero, expired,
        ]

        result = cmd_exceptions(client)

        assert result['policies_count'] == 1
        assert result['total_exceptions'] == 4
        assert result['active_exceptions'] == 3
        assert len(result['exceptions']) == 3
        assert active_permanent in result['exceptions']
        assert active_temp in result['exceptions']
        assert active_zero in result['exceptions']
        assert expired not in result['exceptions']

    def test_with_name_filter(self):
        client = _make_client()
        client.get_ec_policies.return_value = [_make_policy('rhoai-stage')]
        client.extract_exceptions.return_value = [
            _make_exception('rule.a', days_left=5),
        ]

        result = cmd_exceptions(client, name_filter='rhoai')

        client.get_ec_policies.assert_called_once_with(name_filter='rhoai')
        assert result['policies_count'] == 1
        assert result['active_exceptions'] == 1

    def test_multiple_policies_aggregated(self):
        client = _make_client()
        p1 = _make_policy('policy-a')
        p2 = _make_policy('policy-b')
        client.get_ec_policies.return_value = [p1, p2]

        def side_effect(policy):
            if policy is p1:
                return [_make_exception('rule.1', days_left=10)]
            return [_make_exception('rule.2', days_left=-1)]

        client.extract_exceptions.side_effect = side_effect

        result = cmd_exceptions(client)

        assert result['policies_count'] == 2
        assert result['total_exceptions'] == 2
        assert result['active_exceptions'] == 1


# ═══════════════════════════════════════════════════════════════════════
# cmd_expiring tests
# ═══════════════════════════════════════════════════════════════════════

class TestCmdExpiring:
    """Tests for cmd_expiring function."""

    def test_no_expiring(self):
        client = _make_client()
        result = cmd_expiring(client)

        assert result['source'] == 'k8s-api'
        assert result['policies_count'] == 0
        assert result['days_window'] == 30
        assert result['exceptions'] == []

    def test_some_expiring_within_window(self):
        client = _make_client()
        client.get_ec_policies.return_value = [_make_policy('p1')]

        exc_in_window = _make_exception('rule.soon', days_left=15)
        exc_permanent = _make_exception('rule.perm', days_left=None, permanent=True)
        exc_far = _make_exception('rule.far', days_left=90)

        client.extract_exceptions.return_value = [
            exc_in_window, exc_permanent, exc_far,
        ]

        result = cmd_expiring(client)

        assert len(result['exceptions']) == 1
        assert result['exceptions'][0] == exc_in_window

    def test_boundary_lower_minus_seven(self):
        """days_left = -7 is included (recently expired)."""
        client = _make_client()
        client.get_ec_policies.return_value = [_make_policy('p1')]

        at_lower = _make_exception('rule.lower', days_left=-7)
        below_lower = _make_exception('rule.below', days_left=-8)

        client.extract_exceptions.return_value = [at_lower, below_lower]

        result = cmd_expiring(client)

        assert len(result['exceptions']) == 1
        assert result['exceptions'][0] == at_lower

    def test_boundary_zero(self):
        """days_left = 0 is included (expiring today)."""
        client = _make_client()
        client.get_ec_policies.return_value = [_make_policy('p1')]
        client.extract_exceptions.return_value = [
            _make_exception('rule.today', days_left=0),
        ]

        result = cmd_expiring(client)

        assert len(result['exceptions']) == 1
        assert result['exceptions'][0]['days_left'] == 0

    def test_boundary_at_days_limit(self):
        """days_left = days is included (at the window edge)."""
        client = _make_client()
        client.get_ec_policies.return_value = [_make_policy('p1')]
        client.extract_exceptions.return_value = [
            _make_exception('rule.edge', days_left=30),
            _make_exception('rule.over', days_left=31),
        ]

        result = cmd_expiring(client, days=30)

        assert len(result['exceptions']) == 1
        assert result['exceptions'][0]['value'] == 'rule.edge'

    def test_sorted_by_days_left(self):
        client = _make_client()
        client.get_ec_policies.return_value = [_make_policy('p1')]
        client.extract_exceptions.return_value = [
            _make_exception('rule.later', days_left=20),
            _make_exception('rule.soon', days_left=5),
            _make_exception('rule.recent', days_left=-3),
        ]

        result = cmd_expiring(client)

        days = [e['days_left'] for e in result['exceptions']]
        assert days == [-3, 5, 20]

    def test_custom_days_window(self):
        client = _make_client()
        client.get_ec_policies.return_value = [_make_policy('p1')]
        client.extract_exceptions.return_value = [
            _make_exception('rule.a', days_left=5),
            _make_exception('rule.b', days_left=15),
        ]

        result = cmd_expiring(client, days=10)

        assert result['days_window'] == 10
        assert len(result['exceptions']) == 1
        assert result['exceptions'][0]['value'] == 'rule.a'


# ═══════════════════════════════════════════════════════════════════════
# cmd_bindings tests
# ═══════════════════════════════════════════════════════════════════════

class TestCmdBindings:
    """Tests for cmd_bindings function."""

    @patch('ec_policy_data.KonfluxClient')
    def test_empty(self, mock_klass):
        client = _make_client()
        mock_klass.extract_rpa_bindings.return_value = []

        result = cmd_bindings(client)

        assert result['source'] == 'k8s-api'
        assert result['policies_count'] == 0
        assert result['rpas_total'] == 0
        assert result['bindings'] == []

    @patch('ec_policy_data.KonfluxClient')
    def test_with_bindings_sorted(self, mock_klass):
        client = _make_client()
        p1 = _make_policy('policy-stage')
        p2 = _make_policy('policy-prod')
        client.get_ec_policies.return_value = [p1, p2]
        client.get_release_plan_admissions.return_value = [{'metadata': {'name': 'rpa1'}}, {'metadata': {'name': 'rpa2'}}]

        binding_b = _make_binding('rpa-z-components', 'beta-app', 'policy-stage', 'stage')
        binding_a = _make_binding('rpa-a-components', 'alpha-app', 'policy-prod', 'prod')
        mock_klass.extract_rpa_bindings.return_value = [binding_b, binding_a]

        result = cmd_bindings(client)

        assert result['policies_count'] == 2
        assert result['rpas_total'] == 2
        assert result['bindings'][0]['application'] == 'alpha-app'
        assert result['bindings'][1]['application'] == 'beta-app'

        # Verify policy_names set is passed correctly
        call_args = mock_klass.extract_rpa_bindings.call_args
        rpas_arg, policy_names_arg = call_args[0]
        assert 'policy-stage' in policy_names_arg
        assert 'policy-prod' in policy_names_arg


# ═══════════════════════════════════════════════════════════════════════
# cmd_policy_gap tests
# ═══════════════════════════════════════════════════════════════════════

class TestCmdPolicyGap:
    """Tests for cmd_policy_gap function."""

    @patch('ec_policy_data.KonfluxClient')
    def test_no_bindings(self, mock_klass):
        client = _make_client()
        releng_client = _make_client()
        mock_klass.extract_rpa_bindings.return_value = []

        result = cmd_policy_gap(client, releng_client)

        assert result['source'] == 'k8s-api'
        assert result['app_filter'] is None
        assert result['steps'] == []
        releng_client.get_release_plan_admissions.assert_called_once_with(name_filter=None)

    @patch('ec_policy_data.KonfluxClient')
    def test_stage_prod_comparison_with_common_and_diff(self, mock_klass):
        client = _make_client()
        releng_client = _make_client()

        mock_klass.extract_rpa_bindings.return_value = [
            _make_binding('rpa-components-stage', 'rhoai', 'stage-policy', 'stage'),
            _make_binding('rpa-components-prod', 'rhoai', 'prod-policy', 'prod'),
        ]

        stage_policy = _make_policy('stage-policy')
        prod_policy = _make_policy('prod-policy')
        client.get_ec_policies.return_value = [stage_policy, prod_policy]

        stage_excs = [
            _make_exception('common.rule', days_left=10),
            _make_exception('stage.only.rule', days_left=5, reference='JIRA-123'),
        ]
        prod_excs = [
            _make_exception('common.rule', days_left=20),
            _make_exception('prod.only.rule', days_left=15),
        ]

        def extract_side_effect(policy):
            name = policy['metadata']['name']
            if name == 'stage-policy':
                return stage_excs
            elif name == 'prod-policy':
                return prod_excs
            return []

        client.extract_exceptions.side_effect = extract_side_effect

        result = cmd_policy_gap(client, releng_client)

        assert len(result['steps']) == 1
        step = result['steps'][0]
        assert step['step'] == 'components'
        assert step['stage_policy'] == 'stage-policy'
        assert step['prod_policy'] == 'prod-policy'
        assert step['stage_exceptions'] == 2
        assert step['prod_exceptions'] == 2
        assert step['common'] == 1
        assert step['stage_only_count'] == 1
        assert step['prod_only_count'] == 1
        assert len(step['stage_only_rules']) == 1
        assert step['stage_only_rules'][0]['rule'] == 'stage.only.rule'
        assert step['stage_only_rules'][0]['reference'] == 'JIRA-123'

    @patch('ec_policy_data.KonfluxClient')
    def test_step_type_detection_chart(self, mock_klass):
        client = _make_client()
        releng_client = _make_client()

        mock_klass.extract_rpa_bindings.return_value = [
            _make_binding('rhoai-chart-stage-rpa', 'rhoai', 'chart-stage', 'stage'),
        ]

        client.get_ec_policies.return_value = [_make_policy('chart-stage')]
        client.extract_exceptions.return_value = []

        result = cmd_policy_gap(client, releng_client)

        assert len(result['steps']) == 1
        assert result['steps'][0]['step'] == 'charts'

    @patch('ec_policy_data.KonfluxClient')
    def test_step_type_detection_fbc(self, mock_klass):
        client = _make_client()
        releng_client = _make_client()

        mock_klass.extract_rpa_bindings.return_value = [
            _make_binding('rhoai-fbc-prod-rpa', 'rhoai', 'fbc-prod', 'prod'),
        ]

        client.get_ec_policies.return_value = [_make_policy('fbc-prod')]
        client.extract_exceptions.return_value = []

        result = cmd_policy_gap(client, releng_client)

        assert len(result['steps']) == 1
        assert result['steps'][0]['step'] == 'fbc'

    @patch('ec_policy_data.KonfluxClient')
    def test_step_type_detection_other(self, mock_klass):
        client = _make_client()
        releng_client = _make_client()

        mock_klass.extract_rpa_bindings.return_value = [
            _make_binding('rhoai-deploy-stage-rpa', 'rhoai', 'deploy-stage', 'stage'),
        ]

        client.get_ec_policies.return_value = [_make_policy('deploy-stage')]
        client.extract_exceptions.return_value = []

        result = cmd_policy_gap(client, releng_client)

        assert len(result['steps']) == 1
        assert result['steps'][0]['step'] == 'other'

    @patch('ec_policy_data.KonfluxClient')
    def test_app_filter_passed(self, mock_klass):
        client = _make_client()
        releng_client = _make_client()
        mock_klass.extract_rpa_bindings.return_value = []

        result = cmd_policy_gap(client, releng_client, app_filter='rhoai')

        assert result['app_filter'] == 'rhoai'
        releng_client.get_release_plan_admissions.assert_called_once_with(name_filter='rhoai')

    @patch('ec_policy_data.KonfluxClient')
    def test_temporary_vs_permanent_counts(self, mock_klass):
        client = _make_client()
        releng_client = _make_client()

        mock_klass.extract_rpa_bindings.return_value = [
            _make_binding('rpa-components-stage', 'rhoai', 'stage-p', 'stage'),
            _make_binding('rpa-components-prod', 'rhoai', 'prod-p', 'prod'),
        ]

        client.get_ec_policies.return_value = [
            _make_policy('stage-p'),
            _make_policy('prod-p'),
        ]

        def extract_side_effect(policy):
            name = policy['metadata']['name']
            if name == 'stage-p':
                return [
                    _make_exception('rule.a', days_left=10, permanent=False),
                    _make_exception('rule.b', days_left=None, permanent=True),
                ]
            elif name == 'prod-p':
                return [
                    _make_exception('rule.c', days_left=5, permanent=False),
                ]
            return []

        client.extract_exceptions.side_effect = extract_side_effect

        result = cmd_policy_gap(client, releng_client)

        step = result['steps'][0]
        assert step['stage_temporary'] == 1  # rule.a (permanent=False)
        assert step['prod_temporary'] == 1   # rule.c (permanent=False)


# ═══════════════════════════════════════════════════════════════════════
# main() tests
# ═══════════════════════════════════════════════════════════════════════

class TestMain:
    """Tests for main() CLI entry point."""

    @patch('ec_policy_data.KonfluxClient')
    @patch('ec_policy_data.cmd_exceptions')
    @patch.dict(os.environ, {'NAMESPACE': 'test-ns'}, clear=False)
    def test_action_exceptions(self, mock_cmd, mock_klass, capsys):
        mock_cmd.return_value = {'source': 'k8s-api', 'exceptions': []}

        with patch('sys.argv', ['ec_policy_data.py', '--action', 'exceptions', '--name-filter', 'rhoai']):
            with pytest.raises(SystemExit) as exc_info:
                from ec_policy_data import main
                main()

            assert exc_info.value.code == 0

        mock_klass.assert_called_once_with(namespace='test-ns')
        mock_cmd.assert_called_once()
        args, kwargs = mock_cmd.call_args
        assert args[1] == 'rhoai'  # name_filter

    @patch('ec_policy_data.KonfluxClient')
    @patch('ec_policy_data.cmd_expiring')
    @patch.dict(os.environ, {'NAMESPACE': 'ns1'}, clear=False)
    def test_action_expiring_with_days(self, mock_cmd, mock_klass, capsys):
        mock_cmd.return_value = {'source': 'k8s-api', 'exceptions': []}

        with patch('sys.argv', ['ec_policy_data.py', '--action', 'expiring', '--days', '14']):
            with pytest.raises(SystemExit) as exc_info:
                from ec_policy_data import main
                main()

            assert exc_info.value.code == 0

        mock_cmd.assert_called_once()
        args, kwargs = mock_cmd.call_args
        assert args[2] == 14  # days parameter

    @patch('ec_policy_data.KonfluxClient')
    @patch('ec_policy_data.cmd_bindings')
    @patch.dict(os.environ, {'NAMESPACE': 'ns1'}, clear=False)
    def test_action_bindings(self, mock_cmd, mock_klass, capsys):
        mock_cmd.return_value = {'source': 'k8s-api', 'bindings': []}

        with patch('sys.argv', ['ec_policy_data.py', '--action', 'bindings']):
            with pytest.raises(SystemExit) as exc_info:
                from ec_policy_data import main
                main()

            assert exc_info.value.code == 0

        mock_cmd.assert_called_once()

    @patch('ec_policy_data.KonfluxClient')
    @patch('ec_policy_data.cmd_policy_gap')
    @patch.dict(os.environ, {'NAMESPACE': 'ns1', 'RELENG_NAMESPACE': 'releng-ns'}, clear=False)
    def test_action_policy_gap_creates_releng_client(self, mock_cmd, mock_klass, capsys):
        mock_cmd.return_value = {'source': 'k8s-api', 'steps': []}

        with patch('sys.argv', ['ec_policy_data.py', '--action', 'policy-gap']):
            with pytest.raises(SystemExit) as exc_info:
                from ec_policy_data import main
                main()

            assert exc_info.value.code == 0

        # Two KonfluxClient calls: one for main namespace, one for releng
        assert mock_klass.call_count == 2
        calls = mock_klass.call_args_list
        assert calls[0] == call(namespace='ns1')
        assert calls[1] == call(namespace='releng-ns')

    @patch('ec_policy_data.KonfluxClient')
    @patch.dict(os.environ, {'NAMESPACE': 'ns1'}, clear=False)
    def test_error_handling_wraps_in_json(self, mock_klass, capsys):
        mock_klass.side_effect = RuntimeError('Connection refused')

        with patch('sys.argv', ['ec_policy_data.py', '--action', 'exceptions']):
            with pytest.raises(SystemExit) as exc_info:
                from ec_policy_data import main
                main()

            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output['source'] == 'error'
        assert 'Connection refused' in output['error']
