"""Tests for Release Pipeline, Onboarding, and conforma ordering features.

Covers:
- _fetch_release_pipeline() — namespace selection, Release CR parsing, stage/prod classification
- _fetch_onboarding_from_triage() — keyword matching, field extraction
- _release_phase_icon() — phase-to-icon mapping
- conforma table sort ordering — current before future, wrong_policy after real
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

from cli.data import _fetch_onboarding_from_triage, _fetch_release_pipeline
from cli.main import _release_phase_icon

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_release_cr(name, release_plan, phase='Released', created='2026-07-09T10:00:00Z'):
    """Build a minimal Release CR dict matching KonfluxClient.get_releases() output."""
    return {
        'metadata': {
            'name': name,
            'creationTimestamp': created,
        },
        'status': {
            'conditions': [{
                'type': 'Released',
                'status': 'True',
                'message': '',
            }],
            'processing': {
                'releaseStrategy': '',
                'pipelineRun': 'rhtap-releng-tenant/managed-abc',
            },
        },
        'spec': {
            'releasePlan': release_plan,
            'snapshot': 'snap-123',
        },
    }


def _make_triage_item(components, group_label='', notes='', jira_key=None, status='active'):
    return {
        'components': components,
        'group_label': group_label,
        'notes': notes,
        'jira_key': jira_key,
        'status': status,
        'root_cause': '',
    }


# ---------------------------------------------------------------------------
# _release_phase_icon
# ---------------------------------------------------------------------------

class TestReleasePhaseIcon:

    def _id(self, s):
        return s

    def test_success_phases(self):
        for phase in ('Released', 'Validated', 'FinalPipelineProcessed'):
            result = _release_phase_icon(phase, self._id, self._id, self._id)
            assert '✅' in result

    def test_progress_phases(self):
        for phase in ('Processed', 'Processing', 'Progressing', 'PostDeployed', 'Deploying'):
            result = _release_phase_icon(phase, self._id, self._id, self._id)
            assert '⏳' in result

    def test_failure_phases(self):
        for phase in ('Failed', 'Unknown', '', 'SomethingElse'):
            result = _release_phase_icon(phase, self._id, self._id, self._id)
            assert '❌' in result

    def test_uses_correct_color_function(self):
        green = MagicMock(return_value='GREEN')
        yellow = MagicMock(return_value='YELLOW')
        red = MagicMock(return_value='RED')

        _release_phase_icon('Released', green, yellow, red)
        green.assert_called_once()
        yellow.assert_not_called()
        red.assert_not_called()

        green.reset_mock()
        _release_phase_icon('Processed', green, yellow, red)
        yellow.assert_called_once()
        green.assert_not_called()

        yellow.reset_mock()
        _release_phase_icon('Failed', green, yellow, red)
        red.assert_called_once()


# ---------------------------------------------------------------------------
# _fetch_release_pipeline
# ---------------------------------------------------------------------------

class TestFetchReleasePipeline:

    def test_empty_namespace_returns_nightly_only(self):
        nightly = [{'component': 'odh-operator', 'status': 'Succeeded'}]
        with patch('cli.config.NAMESPACE', ''):
            result = _fetch_release_pipeline('rhoai-v3-5', nightly)
        assert result['nightly'] is nightly
        assert result['stage'] is None
        assert result['prod'] is None

    @patch('openshift_auth._ensure_k8s_config')
    @patch('clients.konflux_client.KonfluxClient')
    def test_classifies_stage_and_prod(self, MockKfx, _k8s):
        with patch('cli.config.NAMESPACE', 'rhoai-tenant'):
            kfx = MockKfx.return_value
            kfx.get_releases.return_value = [
                _make_release_cr('stage-rel', 'rhoai-v3-5-stage'),
                _make_release_cr('prod-rel', 'rhoai-v3-5-fbc-prod'),
            ]
            MockKfx.extract_release_status = lambda cr: {
                'name': cr['metadata']['name'],
                'release_plan': cr['spec']['releasePlan'],
                'phase': 'Released',
                'created': cr['metadata']['creationTimestamp'],
            }
            result = _fetch_release_pipeline('rhoai-v3-5', [])

        assert result['stage']['name'] == 'stage-rel'
        assert result['prod']['name'] == 'prod-rel'

    @patch('openshift_auth._ensure_k8s_config')
    @patch('clients.konflux_client.KonfluxClient')
    def test_first_match_wins(self, MockKfx, _k8s):
        """Only the most recent stage/prod release is kept."""
        with patch('cli.config.NAMESPACE', 'rhoai-tenant'):
            kfx = MockKfx.return_value
            kfx.get_releases.return_value = [
                _make_release_cr('stage-new', 'rhoai-v3-5-stage-new'),
                _make_release_cr('stage-old', 'rhoai-v3-5-stage-old'),
            ]
            MockKfx.extract_release_status = lambda cr: {
                'name': cr['metadata']['name'],
                'release_plan': cr['spec']['releasePlan'],
                'phase': 'Released',
                'created': cr['metadata']['creationTimestamp'],
            }
            result = _fetch_release_pipeline('rhoai-v3-5', [])

        assert result['stage']['name'] == 'stage-new'

    @patch('openshift_auth._ensure_k8s_config')
    @patch('clients.konflux_client.KonfluxClient')
    def test_stops_early_when_both_found(self, MockKfx, _k8s):
        """Breaks from loop after finding both stage and prod."""
        with patch('cli.config.NAMESPACE', 'rhoai-tenant'):
            releases = [
                _make_release_cr('s', 'rhoai-v3-5-stage'),
                _make_release_cr('p', 'rhoai-v3-5-fbc-prod'),
                _make_release_cr('extra', 'rhoai-v3-5-prod-extra'),
            ]
            kfx = MockKfx.return_value
            kfx.get_releases.return_value = releases
            MockKfx.extract_release_status = lambda cr: {
                'name': cr['metadata']['name'],
                'release_plan': cr['spec']['releasePlan'],
                'phase': 'Released',
                'created': cr['metadata']['creationTimestamp'],
            }
            result = _fetch_release_pipeline('rhoai-v3-5', [])

        assert result['stage'] is not None
        assert result['prod'] is not None

    @patch('openshift_auth._ensure_k8s_config')
    @patch('clients.konflux_client.KonfluxClient')
    def test_k8s_error_returns_nightly_only(self, MockKfx, _k8s):
        with patch('cli.config.NAMESPACE', 'rhoai-tenant'):
            MockKfx.side_effect = Exception('connection refused')
            result = _fetch_release_pipeline('rhoai-v3-5', ['nightly-data'])

        assert result['nightly'] == ['nightly-data']
        assert result['stage'] is None
        assert result['prod'] is None

    @patch('openshift_auth._ensure_k8s_config')
    @patch('clients.konflux_client.KonfluxClient')
    def test_no_releases_returns_empty(self, MockKfx, _k8s):
        with patch('cli.config.NAMESPACE', 'rhoai-tenant'):
            kfx = MockKfx.return_value
            kfx.get_releases.return_value = []
            result = _fetch_release_pipeline('rhoai-v3-5', [])

        assert result['stage'] is None
        assert result['prod'] is None


# ---------------------------------------------------------------------------
# _fetch_onboarding_from_triage
# ---------------------------------------------------------------------------

class TestFetchOnboardingFromTriage:

    @patch('cli.db.get_repo')
    def test_matches_group_label(self, mock_get_repo):
        repo = MagicMock()
        repo.get_active.return_value = [
            _make_triage_item(['comp-a'], group_label='Onboarding - new component'),
        ]
        mock_get_repo.return_value = repo
        result = _fetch_onboarding_from_triage('rhoai-v3-5')

        assert len(result) == 1
        assert result[0]['components'] == ['comp-a']

    @patch('cli.db.get_repo')
    def test_matches_notes(self, mock_get_repo):
        repo = MagicMock()
        repo.get_active.return_value = [
            _make_triage_item(['comp-b'], notes='needs onboarding to Konflux'),
        ]
        mock_get_repo.return_value = repo
        result = _fetch_onboarding_from_triage('rhoai-v3-5')

        assert len(result) == 1

    @patch('cli.db.get_repo')
    def test_case_insensitive(self, mock_get_repo):
        repo = MagicMock()
        repo.get_active.return_value = [
            _make_triage_item(['comp-c'], group_label='ONBOARDING'),
        ]
        mock_get_repo.return_value = repo
        result = _fetch_onboarding_from_triage('rhoai-v3-5')

        assert len(result) == 1

    @patch('cli.db.get_repo')
    def test_filters_non_onboarding(self, mock_get_repo):
        repo = MagicMock()
        repo.get_active.return_value = [
            _make_triage_item(['comp-a'], group_label='Go 1.26 mismatch'),
            _make_triage_item(['comp-b'], group_label='Onboarding'),
        ]
        mock_get_repo.return_value = repo
        result = _fetch_onboarding_from_triage('rhoai-v3-5')

        assert len(result) == 1
        assert result[0]['components'] == ['comp-b']

    @patch('cli.db.get_repo')
    def test_preserves_jira_key(self, mock_get_repo):
        repo = MagicMock()
        repo.get_active.return_value = [
            _make_triage_item(
                ['comp-x'], group_label='onboarding',
                jira_key='RHOAIENG-12345', status='active'),
        ]
        mock_get_repo.return_value = repo
        result = _fetch_onboarding_from_triage('rhoai-v3-5')

        assert result[0]['jira_key'] == 'RHOAIENG-12345'
        assert result[0]['status'] == 'active'

    @patch('cli.db.get_repo')
    def test_empty_when_no_onboarding(self, mock_get_repo):
        repo = MagicMock()
        repo.get_active.return_value = [
            _make_triage_item(['comp-a'], group_label='build failure'),
        ]
        mock_get_repo.return_value = repo
        result = _fetch_onboarding_from_triage('rhoai-v3-5')

        assert result == []

    @patch('cli.db.get_repo')
    def test_exception_returns_empty(self, mock_get_repo):
        mock_get_repo.side_effect = Exception('DB down')
        result = _fetch_onboarding_from_triage('rhoai-v3-5')

        assert result == []

    @patch('cli.db.get_repo')
    def test_none_group_label_handled(self, mock_get_repo):
        repo = MagicMock()
        repo.get_active.return_value = [
            {'components': ['x'], 'group_label': None, 'notes': None,
             'jira_key': None, 'status': 'active', 'root_cause': ''},
        ]
        mock_get_repo.return_value = repo
        result = _fetch_onboarding_from_triage('rhoai-v3-5')

        assert result == []


# ---------------------------------------------------------------------------
# Conforma table sort ordering
# ---------------------------------------------------------------------------

class TestConformaSortOrdering:
    """Verify the sort key groups current before future, real before wrong-policy."""

    def _sort_rows(self, rows):
        return sorted(rows, key=lambda r: (
            r['type'] != 'current', r['wrong_policy'], -r['viol']))

    def test_current_before_future(self):
        rows = [
            {'type': 'future', 'wrong_policy': False, 'viol': 10},
            {'type': 'current', 'wrong_policy': False, 'viol': 5},
        ]
        result = self._sort_rows(rows)
        assert result[0]['type'] == 'current'
        assert result[1]['type'] == 'future'

    def test_real_before_wrong_policy(self):
        rows = [
            {'type': 'current', 'wrong_policy': True, 'viol': 20},
            {'type': 'current', 'wrong_policy': False, 'viol': 5},
        ]
        result = self._sort_rows(rows)
        assert result[0]['wrong_policy'] is False
        assert result[1]['wrong_policy'] is True

    def test_within_group_sorted_by_violations_desc(self):
        rows = [
            {'type': 'current', 'wrong_policy': False, 'viol': 3},
            {'type': 'current', 'wrong_policy': False, 'viol': 10},
            {'type': 'current', 'wrong_policy': False, 'viol': 7},
        ]
        result = self._sort_rows(rows)
        viols = [r['viol'] for r in result]
        assert viols == [10, 7, 3]

    def test_full_ordering(self):
        rows = [
            {'type': 'future', 'wrong_policy': True, 'viol': 1},
            {'type': 'future', 'wrong_policy': False, 'viol': 5},
            {'type': 'current', 'wrong_policy': True, 'viol': 20},
            {'type': 'current', 'wrong_policy': False, 'viol': 8},
            {'type': 'current', 'wrong_policy': False, 'viol': 12},
        ]
        result = self._sort_rows(rows)
        expected_order = [
            ('current', False, 12),
            ('current', False, 8),
            ('current', True, 20),
            ('future', False, 5),
            ('future', True, 1),
        ]
        actual_order = [(r['type'], r['wrong_policy'], r['viol']) for r in result]
        assert actual_order == expected_order


# ---------------------------------------------------------------------------
# Release Pipeline display integration
# ---------------------------------------------------------------------------

class TestReleasePipelineStructure:
    """Verify the data structure returned by _fetch_release_pipeline."""

    def test_result_has_all_keys(self):
        with patch('cli.config.NAMESPACE', ''):
            result = _fetch_release_pipeline('rhoai-v3-5', [])
        assert set(result.keys()) == {'nightly', 'stage', 'prod'}

    def test_nightly_passthrough(self):
        nightly = [{'component': 'op', 'status': 'Failed'}]
        with patch('cli.config.NAMESPACE', ''):
            result = _fetch_release_pipeline('rhoai-v3-5', nightly)
        assert result['nightly'] is nightly
