"""Comprehensive tests for api/routes/onboarding.py.

Covers pure helper functions (direct unit tests) and HTTP endpoints (via TestClient).
Target: 40+ tests covering happy paths, edge cases, and error paths.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api import create_app
from api.routes.onboarding import (
    _ODH_STEPS,
    _RHOAI_STEPS,
    _build_jira_map,
    _describe_component,
    _detect_component_type,
    _evaluate_step,
    _get_onboarding_sync,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """Full app with all routes mounted."""
    with patch.dict(os.environ, {
        'DB_HOST': 'localhost',
        'DB_NAME': 'test_db',
        'DB_USER': 'test_user',
        'DB_PASS': 'test_pass',
        'NAMESPACE': 'test-tenant',
    }), patch('api.init_pool'), patch('api.rate_limit.setup_rate_limiter'):
        app = create_app()
        yield TestClient(app, raise_server_exceptions=False)


def _make_comp(**overrides):
    """Build a minimal component dict."""
    base = {
        'name': 'my-comp',
        'application': 'rhoai-v3-5',
        'repository_url': 'https://github.com/red-hat-data-services/my-comp',
        'branch': 'rhoai-3.5',
        'container_image': 'quay.io/rhoai/my-comp',
        'created_at': '2026-01-15T10:00:00Z',
        'last_built_commit': '',
        'nudges': [],
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════════
# _detect_component_type
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectComponentType:
    def test_odh_by_repo_url(self):
        comp = {'repository_url': 'https://github.com/opendatahub-io/notebook'}
        assert _detect_component_type(comp) == 'odh'

    def test_odh_by_name(self):
        comp = {'name': 'odh-operator-v3-5', 'repository_url': ''}
        assert _detect_component_type(comp) == 'odh'

    def test_rhoai_by_repo_url(self):
        comp = {'repository_url': 'https://github.com/red-hat-data-services/foo'}
        assert _detect_component_type(comp) == 'rhoai'

    def test_rhoai_by_name_rhoai(self):
        comp = {'name': 'rhoai-operator', 'repository_url': ''}
        assert _detect_component_type(comp) == 'rhoai'

    def test_rhoai_by_name_rhods(self):
        comp = {'name': 'rhods-dashboard', 'repository_url': ''}
        assert _detect_component_type(comp) == 'rhoai'

    def test_unknown_type(self):
        comp = {'name': 'some-component', 'repository_url': 'https://github.com/other/repo'}
        assert _detect_component_type(comp) == 'unknown'

    def test_empty_comp(self):
        assert _detect_component_type({}) == 'unknown'

    def test_case_insensitive_repo(self):
        comp = {'repository_url': 'https://github.com/OpenDataHub-io/something'}
        assert _detect_component_type(comp) == 'odh'

    def test_case_insensitive_name(self):
        comp = {'name': 'ODH-Operator', 'repository_url': ''}
        assert _detect_component_type(comp) == 'odh'

    def test_odh_takes_priority_over_rhoai_in_repo(self):
        """ODH check runs first so if both match, ODH wins."""
        comp = {'name': 'odh-rhoai-mix', 'repository_url': 'https://github.com/opendatahub-io/x'}
        assert _detect_component_type(comp) == 'odh'


# ═══════════════════════════════════════════════════════════════════════════
# _evaluate_step — all step keys
# ═══════════════════════════════════════════════════════════════════════════


class TestEvaluateStepComponentCR:
    def test_component_cr_done(self):
        comp = _make_comp(name='test', created_at='2026-01-01')
        result = _evaluate_step('component_cr', comp, [], {}, [])
        assert result['status'] == 'done'
        assert 'test' in result['detail']
        assert result['completed_at'] == '2026-01-01'


class TestEvaluateStepRepository:
    def test_repository_done(self):
        comp = _make_comp(repository_url='https://github.com/org/repo')
        result = _evaluate_step('repository', comp, [], {}, [])
        assert result['status'] == 'done'
        assert result['detail'] == 'https://github.com/org/repo'

    def test_repository_blocked(self):
        comp = _make_comp(repository_url='')
        result = _evaluate_step('repository', comp, [], {}, [])
        assert result['status'] == 'blocked'
        assert 'fix' in result


class TestEvaluateStepBranch:
    def test_branch_done(self):
        comp = _make_comp(branch='rhoai-3.5')
        result = _evaluate_step('branch', comp, [], {}, [])
        assert result['status'] == 'done'
        assert result['detail'] == 'rhoai-3.5'

    def test_branch_blocked(self):
        comp = _make_comp(branch='')
        result = _evaluate_step('branch', comp, [], {}, [])
        assert result['status'] == 'blocked'
        assert 'fix' in result


class TestEvaluateStepDockerfile:
    def test_dockerfile_always_unknown(self):
        result = _evaluate_step('dockerfile', _make_comp(), [], {}, [])
        assert result['status'] == 'unknown'
        assert 'Cannot verify' in result['detail']
        assert 'fix' in result


class TestEvaluateStepContainerImage:
    def test_container_image_done(self):
        comp = _make_comp(container_image='quay.io/rhoai/comp')
        result = _evaluate_step('container_image', comp, [], {}, [])
        assert result['status'] == 'done'
        assert result['detail'] == 'quay.io/rhoai/comp'

    def test_container_image_blocked(self):
        comp = _make_comp(container_image='')
        result = _evaluate_step('container_image', comp, [], {}, [])
        assert result['status'] == 'blocked'
        assert 'fix' in result


class TestEvaluateStepPac:
    def test_pac_done_matching_repo(self):
        comp = _make_comp(repository_url='https://github.com/org/repo')
        pac_repos = [{'url': 'https://github.com/org/repo'}]
        result = _evaluate_step('pac', comp, pac_repos, {}, [])
        assert result['status'] == 'done'

    def test_pac_pending_no_match(self):
        comp = _make_comp(repository_url='https://github.com/org/repo')
        pac_repos = [{'url': 'https://github.com/org/other'}]
        result = _evaluate_step('pac', comp, pac_repos, {}, [])
        assert result['status'] == 'pending'
        assert 'fix' in result

    def test_pac_pending_empty_repos(self):
        result = _evaluate_step('pac', _make_comp(), [], {}, [])
        assert result['status'] == 'pending'


class TestEvaluateStepFirstBuild:
    def test_no_runs_no_commit(self):
        comp = _make_comp(name='comp-a', last_built_commit='')
        result = _evaluate_step('first_build', comp, [], {}, [])
        assert result['status'] == 'pending'
        assert 'fix' in result

    def test_no_runs_has_commit(self):
        comp = _make_comp(name='comp-a', last_built_commit='abcdef123456789')
        result = _evaluate_step('first_build', comp, [], {}, [])
        assert result['status'] == 'done'
        assert 'abcdef123456' in result['detail']

    def test_latest_succeeded(self):
        comp = _make_comp(name='comp-a')
        runs = [
            {'name': 'run-1', 'status': 'succeeded', 'created_at': '2026-06-01'},
        ]
        pipelineruns = {'comp-a': runs}
        result = _evaluate_step('first_build', comp, [], pipelineruns, [])
        assert result['status'] == 'done'
        assert result['total_runs'] == 1
        assert result['succeeded'] == 1
        assert result['failed'] == 0

    def test_latest_running(self):
        comp = _make_comp(name='comp-a')
        runs = [
            {'name': 'run-2', 'status': 'running', 'created_at': '2026-06-02'},
        ]
        pipelineruns = {'comp-a': runs}
        result = _evaluate_step('first_build', comp, [], pipelineruns, [])
        assert result['status'] == 'in_progress'
        assert 'started_at' in result

    def test_latest_failed_no_prior_success(self):
        comp = _make_comp(name='comp-a')
        runs = [
            {'name': 'run-3', 'status': 'failed', 'reason': 'OOMKilled',
             'created_at': '2026-06-03'},
        ]
        pipelineruns = {'comp-a': runs}
        result = _evaluate_step('first_build', comp, [], pipelineruns, [])
        assert result['status'] == 'failed'
        assert 'OOMKilled' in result['detail']
        assert 'fix' in result
        assert 'last_success_at' not in result

    def test_latest_failed_with_prior_success(self):
        comp = _make_comp(name='comp-a')
        runs = [
            {'name': 'run-4', 'status': 'failed', 'reason': '',
             'created_at': '2026-06-04'},
            {'name': 'run-3', 'status': 'succeeded',
             'created_at': '2026-06-03'},
        ]
        pipelineruns = {'comp-a': runs}
        result = _evaluate_step('first_build', comp, [], pipelineruns, [])
        assert result['status'] == 'failed'
        assert result['last_success_at'] == '2026-06-03'

    def test_latest_failed_no_reason(self):
        comp = _make_comp(name='comp-a')
        runs = [
            {'name': 'run-5', 'status': 'failed', 'created_at': '2026-06-05'},
        ]
        pipelineruns = {'comp-a': runs}
        result = _evaluate_step('first_build', comp, [], pipelineruns, [])
        assert result['status'] == 'failed'
        assert '()' not in result['detail']

    def test_latest_unknown_status(self):
        comp = _make_comp(name='comp-a')
        runs = [
            {'name': 'run-6', 'created_at': '2026-06-06'},
        ]
        pipelineruns = {'comp-a': runs}
        result = _evaluate_step('first_build', comp, [], pipelineruns, [])
        assert result['status'] == 'failed'
        assert result['latest_status'] == 'unknown'


class TestEvaluateStepNudges:
    def test_nudges_done(self):
        comp = _make_comp(nudges=['comp-a', 'comp-b'])
        result = _evaluate_step('nudges', comp, [], {}, [])
        assert result['status'] == 'done'
        assert '2 nudge(s)' in result['detail']
        assert result['nudge_refs'] == ['comp-a', 'comp-b']

    def test_nudges_pending(self):
        comp = _make_comp(nudges=[])
        result = _evaluate_step('nudges', comp, [], {}, [])
        assert result['status'] == 'pending'


class TestEvaluateStepReleasePlan:
    def test_release_plan_match_by_component_name(self):
        comp = _make_comp(name='my-comp')
        rps = [{'component': 'my-comp'}]
        result = _evaluate_step('release_plan', comp, [], {}, rps)
        assert result['status'] == 'done'

    def test_release_plan_match_by_empty_component(self):
        """ReleasePlan with no component filter covers all."""
        comp = _make_comp(name='my-comp')
        rps = [{'component': ''}]
        result = _evaluate_step('release_plan', comp, [], {}, rps)
        assert result['status'] == 'done'

    def test_release_plan_match_by_none_component(self):
        comp = _make_comp(name='my-comp')
        rps = [{}]
        result = _evaluate_step('release_plan', comp, [], {}, rps)
        assert result['status'] == 'done'

    def test_release_plan_no_match(self):
        comp = _make_comp(name='my-comp')
        rps = [{'component': 'other-comp'}]
        result = _evaluate_step('release_plan', comp, [], {}, rps)
        assert result['status'] == 'pending'
        assert 'fix' in result

    def test_release_plan_empty_list(self):
        comp = _make_comp(name='my-comp')
        result = _evaluate_step('release_plan', comp, [], {}, [])
        assert result['status'] == 'pending'


class TestEvaluateStepUnknownKey:
    def test_unknown_step_key(self):
        result = _evaluate_step('nonexistent_key', _make_comp(), [], {}, [])
        assert result['status'] == 'unknown'


# ═══════════════════════════════════════════════════════════════════════════
# _describe_component
# ═══════════════════════════════════════════════════════════════════════════


class TestDescribeComponent:
    def test_complete_rhoai_component(self):
        """All steps pass for a RHOAI component."""
        comp = _make_comp(
            name='rhoai-op',
            repository_url='https://github.com/red-hat-data-services/op',
            branch='rhoai-3.5',
            container_image='quay.io/rhoai/op',
            nudges=['dep-a'],
        )
        pac_repos = [{'url': 'https://github.com/red-hat-data-services/op'}]
        pipelineruns = {'rhoai-op': [
            {'name': 'run-1', 'status': 'succeeded', 'created_at': '2026-06-01'},
        ]}
        release_plans = [{'component': 'rhoai-op'}]

        result = _describe_component(comp, pac_repos, pipelineruns, release_plans)
        assert result['type'] == 'rhoai'
        assert result['phase'] == 'complete'
        assert result['progress'] == 100
        assert result['steps_done'] == result['steps_total']
        assert result['component'] == 'rhoai-op'
        assert result['blocked_at'] is None
        assert result['current_step'] is None

    def test_blocked_component(self):
        """Component with missing repo is blocked."""
        comp = _make_comp(name='rhoai-op', repository_url='')
        result = _describe_component(comp, [], {}, [])
        assert result['phase'] == 'blocked'
        assert result['blocked_at'] == 'repository'
        assert result['progress'] < 100

    def test_in_progress_component(self):
        """Component with pending steps is in_progress."""
        comp = _make_comp(
            name='rhoai-op',
            repository_url='https://github.com/red-hat-data-services/op',
            branch='rhoai-3.5',
            container_image='quay.io/rhoai/op',
        )
        # pac not configured, no builds => pending steps
        result = _describe_component(comp, [], {}, [])
        assert result['phase'] == 'in_progress'
        assert result['current_step'] is not None

    def test_odh_component_uses_odh_steps(self):
        """ODH component includes dockerfile step."""
        comp = _make_comp(
            name='odh-notebook',
            repository_url='https://github.com/opendatahub-io/notebooks',
        )
        result = _describe_component(comp, [], {}, [])
        assert result['type'] == 'odh'
        step_keys = [s['step'] for s in result['steps']]
        assert 'dockerfile' in step_keys
        assert result['steps_total'] == len(_ODH_STEPS)

    def test_rhoai_component_no_dockerfile_step(self):
        """RHOAI component does not include dockerfile step."""
        comp = _make_comp(
            name='rhoai-op',
            repository_url='https://github.com/red-hat-data-services/op',
        )
        result = _describe_component(comp, [], {}, [])
        step_keys = [s['step'] for s in result['steps']]
        assert 'dockerfile' not in step_keys
        assert result['steps_total'] == len(_RHOAI_STEPS)

    def test_timeline_sorted_by_date(self):
        comp = _make_comp(
            name='rhoai-op',
            repository_url='https://github.com/red-hat-data-services/op',
            branch='rhoai-3.5',
            container_image='quay.io/rhoai/op',
            created_at='2026-01-10T00:00:00Z',
        )
        pipelineruns = {'rhoai-op': [
            {'name': 'run-1', 'status': 'succeeded', 'created_at': '2026-02-01'},
        ]}
        result = _describe_component(comp, [], pipelineruns, [])
        dates = [t['date'] for t in result['timeline']]
        assert dates == sorted(dates)

    def test_step_labels_assigned(self):
        comp = _make_comp(name='rhoai-op',
                          repository_url='https://github.com/red-hat-data-services/op')
        result = _describe_component(comp, [], {}, [])
        for s in result['steps']:
            assert 'label' in s
            assert 'step' in s

    def test_not_started_phase(self):
        """A component where all steps return 'unknown' phase."""
        # Use an unknown-type component. For _RHOAI_STEPS (default),
        # component_cr is always 'done', so phase won't be 'not_started'
        # in normal flow. This tests the else branch.
        # We can achieve not_started if _evaluate_step returns only
        # 'unknown' or 'done' with done < total and no pending/in_progress.
        # Actually for any component, component_cr is 'done', so done > 0.
        # The 'not_started' branch needs done != total, no blocked, no current_step.
        # Since 'unknown' status doesn't set current_step, we need steps returning
        # only 'done' and 'unknown' but done < total.
        # ODH has a 'dockerfile' step that returns 'unknown'. If all other steps
        # are 'done', that leaves us with done < total, no blocked, no pending.
        comp = _make_comp(
            name='odh-comp',
            repository_url='https://github.com/opendatahub-io/comp',
            branch='rhoai-3.5',
            container_image='quay.io/rhoai/comp',
            nudges=['dep-a'],
        )
        pac_repos = [{'url': 'https://github.com/opendatahub-io/comp'}]
        pipelineruns = {'odh-comp': [
            {'name': 'run-1', 'status': 'succeeded', 'created_at': '2026-06-01'},
        ]}
        release_plans = [{'component': 'odh-comp'}]
        result = _describe_component(comp, pac_repos, pipelineruns, release_plans)
        # dockerfile returns 'unknown' => done < total, no blocked, no pending
        assert result['phase'] == 'not_started'

    def test_progress_zero_for_empty_comp(self):
        """Progress calculation when no steps done."""
        comp = _make_comp(name='rhoai-op', repository_url='', branch='',
                          container_image='')
        result = _describe_component(comp, [], {}, [])
        # component_cr is always done (1 step), rest blocked/pending
        assert result['steps_done'] >= 1
        assert result['progress'] > 0

    def test_application_in_result(self):
        comp = _make_comp(application='rhoai-v3-5')
        result = _describe_component(comp, [], {}, [])
        assert result['application'] == 'rhoai-v3-5'


# ═══════════════════════════════════════════════════════════════════════════
# _build_jira_map
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildJiraMap:
    @patch('cli.db.get_repo')
    def test_builds_map_from_triage(self, mock_get_repo):
        mock_triage = MagicMock()
        mock_triage.find_by_component.return_value = {
            'jira_key': 'RHOAIENG-123',
            'status': 'active',
        }
        mock_get_repo.return_value = mock_triage

        components = [
            {'name': 'comp-a', 'application': 'rhoai-v3-5'},
        ]
        result = _build_jira_map(components)
        assert 'comp-a' in result
        assert result['comp-a']['key'] == 'RHOAIENG-123'
        assert 'issues.redhat.com' in result['comp-a']['url']

    @patch('cli.db.get_repo')
    def test_skips_no_jira_key(self, mock_get_repo):
        mock_triage = MagicMock()
        mock_triage.find_by_component.return_value = {
            'jira_key': None,
            'status': 'active',
        }
        mock_get_repo.return_value = mock_triage

        components = [{'name': 'comp-a', 'application': 'rhoai-v3-5'}]
        result = _build_jira_map(components)
        assert 'comp-a' not in result

    @patch('cli.db.get_repo')
    def test_skips_no_triage_item(self, mock_get_repo):
        mock_triage = MagicMock()
        mock_triage.find_by_component.return_value = None
        mock_get_repo.return_value = mock_triage

        components = [{'name': 'comp-a', 'application': 'rhoai-v3-5'}]
        result = _build_jira_map(components)
        assert result == {}

    def test_handles_exception_gracefully(self):
        """Gracefully returns empty map if triage DB throws."""
        with patch('cli.db.get_repo',
                   side_effect=Exception('DB not available')):
            result = _build_jira_map([{'name': 'comp', 'application': 'app'}])
        assert result == {}

    def test_empty_components(self):
        result = _build_jira_map([])
        assert result == {}


# ═══════════════════════════════════════════════════════════════════════════
# _enrich_with_jira
# ═══════════════════════════════════════════════════════════════════════════


class TestEnrichWithJira:
    """Tests for _enrich_with_jira.

    All jira_analysis functions are imported inside _enrich_with_jira,
    so we patch them at 'onboarding.jira_analysis.<func>'.
    """

    @patch('onboarding.jira_analysis.get_jira_client_from_env')
    def test_no_jira_client(self, mock_jira_env):
        from api.routes.onboarding import _enrich_with_jira
        mock_jira_env.return_value = None
        result = {}
        _enrich_with_jira(result, 'comp-a')
        assert result['jira_available'] is False
        assert 'jira_notice' in result

    @patch('onboarding.jira_analysis.search_onboarding_jira')
    @patch('onboarding.jira_analysis.get_jira_client_from_env')
    def test_jira_no_tickets(self, mock_jira_env, mock_search):
        from api.routes.onboarding import _enrich_with_jira
        mock_jira_env.return_value = MagicMock()
        mock_search.return_value = []
        result = {}
        _enrich_with_jira(result, 'comp-a')
        assert result['jira_available'] is True
        assert result['jira_tickets'] == []

    @patch('onboarding.jira_analysis.build_onboarding_report')
    @patch('onboarding.jira_analysis.analyze_bot_comments')
    @patch('onboarding.jira_analysis.compute_heuristic_analysis')
    @patch('onboarding.jira_analysis.extract_pr_links')
    @patch('onboarding.jira_analysis.map_labels_to_steps')
    @patch('onboarding.jira_analysis.search_onboarding_jira')
    @patch('onboarding.jira_analysis.get_jira_client_from_env')
    def test_jira_with_rhoai_ticket(self, mock_jira_env, mock_search,
                                     mock_map_labels, mock_extract_pr,
                                     mock_heuristic, mock_bot,
                                     mock_report):
        from api.routes.onboarding import _enrich_with_jira
        mock_jira_env.return_value = MagicMock()
        mock_search.return_value = [
            {
                'key': 'RHOAIENG-100',
                'summary': 'Onboard comp-a',
                'status': 'In Progress',
                'type': 'rhoai',
                'url': 'https://issues.redhat.com/browse/RHOAIENG-100',
                'labels': ['yaml-attached'],
                'comments': [],
            }
        ]
        mock_map_labels.return_value = [
            {'key': 'yaml_attached', 'status': 'done'},
        ]
        mock_extract_pr.return_value = {}
        mock_heuristic.return_value = {'blockers': []}
        mock_bot.return_value = {'has_errors': False}
        mock_report.return_value = 'Report text'

        result = {'steps': []}
        _enrich_with_jira(result, 'comp-a')
        assert result['jira_available'] is True
        assert len(result['jira_tickets']) == 1
        assert result['jira_tickets'][0]['key'] == 'RHOAIENG-100'
        assert result['automation_progress'] == '1/1'
        assert result['report'] == 'Report text'
        assert 'bot_error_analysis' not in result

    @patch('onboarding.jira_analysis.build_onboarding_report')
    @patch('onboarding.jira_analysis.analyze_bot_comments')
    @patch('onboarding.jira_analysis.compute_heuristic_analysis')
    @patch('onboarding.jira_analysis.extract_pr_links')
    @patch('onboarding.jira_analysis.map_labels_to_steps')
    @patch('onboarding.jira_analysis.search_onboarding_jira')
    @patch('onboarding.jira_analysis.get_jira_client_from_env')
    def test_jira_with_bot_errors(self, mock_jira_env, mock_search,
                                   mock_map_labels, mock_extract_pr,
                                   mock_heuristic, mock_bot,
                                   mock_report):
        from api.routes.onboarding import _enrich_with_jira
        mock_jira_env.return_value = MagicMock()
        mock_search.return_value = [
            {
                'key': 'RHOAIENG-100',
                'summary': 'Onboard comp-a',
                'status': 'In Progress',
                'type': 'rhoai',
                'url': 'https://issues.redhat.com/browse/RHOAIENG-100',
                'labels': [],
                'comments': ['bot error: failed to create PR'],
            }
        ]
        mock_map_labels.return_value = []
        mock_extract_pr.return_value = {}
        mock_heuristic.return_value = {'blockers': []}
        mock_bot.return_value = {'has_errors': True, 'errors': ['Failed PR']}
        mock_report.return_value = 'Report'

        result = {'steps': []}
        _enrich_with_jira(result, 'comp-a')
        assert 'bot_error_analysis' in result
        assert result['bot_error_analysis']['has_errors'] is True

    @patch('onboarding.jira_analysis.analyze_bot_comments')
    @patch('onboarding.jira_analysis.extract_pr_links')
    @patch('onboarding.jira_analysis.search_onboarding_jira')
    @patch('onboarding.jira_analysis.get_jira_client_from_env')
    def test_jira_with_odh_ticket(self, mock_jira_env, mock_search,
                                   mock_extract_pr, mock_bot):
        from api.routes.onboarding import _enrich_with_jira
        mock_jira_env.return_value = MagicMock()
        mock_search.return_value = [
            {
                'key': 'ODH-500',
                'summary': 'ODH onboard',
                'status': 'Open',
                'type': 'odh',
                'url': 'https://issues.redhat.com/browse/ODH-500',
                'comments': ['some bot msg'],
            }
        ]
        mock_extract_pr.return_value = {'step_a': [{'url': 'https://github.com/pr/1'}]}
        mock_bot.return_value = {'has_errors': False}

        result = {}
        _enrich_with_jira(result, 'comp-a')
        assert 'odh_pr_links' in result
        assert 'step_a' in result['odh_pr_links']
        assert 'odh_bot_error_analysis' not in result

    @patch('onboarding.jira_analysis.analyze_bot_comments')
    @patch('onboarding.jira_analysis.extract_pr_links')
    @patch('onboarding.jira_analysis.search_onboarding_jira')
    @patch('onboarding.jira_analysis.get_jira_client_from_env')
    def test_jira_odh_with_bot_errors(self, mock_jira_env, mock_search,
                                       mock_extract_pr, mock_bot):
        from api.routes.onboarding import _enrich_with_jira
        mock_jira_env.return_value = MagicMock()
        mock_search.return_value = [
            {
                'key': 'ODH-600',
                'summary': 'ODH onboard',
                'status': 'Open',
                'type': 'odh',
                'url': 'https://issues.redhat.com/browse/ODH-600',
                'comments': [],
            }
        ]
        mock_extract_pr.return_value = {}
        mock_bot.return_value = {'has_errors': True, 'errors': ['Bad']}

        result = {}
        _enrich_with_jira(result, 'comp-a')
        assert 'odh_bot_error_analysis' in result

    @patch('onboarding.jira_analysis.build_onboarding_report')
    @patch('onboarding.jira_analysis.analyze_bot_comments')
    @patch('onboarding.jira_analysis.compute_heuristic_analysis')
    @patch('onboarding.jira_analysis.extract_pr_links')
    @patch('onboarding.jira_analysis.map_labels_to_steps')
    @patch('onboarding.jira_analysis.search_onboarding_jira')
    @patch('onboarding.jira_analysis.get_jira_client_from_env')
    def test_jira_automation_step_with_pr_links(self, mock_jira_env,
                                                 mock_search, mock_map_labels,
                                                 mock_extract_pr,
                                                 mock_heuristic, mock_bot,
                                                 mock_report):
        from api.routes.onboarding import _enrich_with_jira
        mock_jira_env.return_value = MagicMock()
        mock_search.return_value = [
            {
                'key': 'RHOAIENG-200',
                'summary': 'Onboard comp-b',
                'status': 'Done',
                'type': 'rhoai',
                'url': 'https://issues.redhat.com/browse/RHOAIENG-200',
                'labels': ['yaml-attached'],
                'comments': [],
            }
        ]
        mock_map_labels.return_value = [
            {'key': 'yaml_attached', 'status': 'done'},
            {'key': 'pac_configured', 'status': 'pending'},
        ]
        mock_extract_pr.return_value = {
            'yaml_attached': [{'url': 'https://github.com/pr/99'}],
        }
        mock_heuristic.return_value = {'blockers': []}
        mock_bot.return_value = {'has_errors': False}
        mock_report.return_value = 'Full report'

        result = {'steps': []}
        _enrich_with_jira(result, 'comp-b')
        assert len(result['automation_steps']) == 2
        assert result['automation_steps'][0]['pr_links'] == ['https://github.com/pr/99']
        assert 'pr_links' not in result['automation_steps'][1]
        assert result['automation_pr_links']['yaml_attached'] == ['https://github.com/pr/99']


# ═══════════════════════════════════════════════════════════════════════════
# _get_onboarding_sync
# ═══════════════════════════════════════════════════════════════════════════


class TestGetOnboardingSync:
    def test_no_namespace(self):
        with patch.dict(os.environ, {}, clear=True):
            result = _get_onboarding_sync('rhoai-v3-5')
        assert 'error' in result
        assert 'NAMESPACE' in result['error']

    @patch('api.routes.onboarding._build_jira_map')
    @patch('api.routes.onboarding._build_component_status')
    @patch('openshift_auth._ensure_k8s_config')
    @patch('clients.kubernetes.KubernetesClient')
    def test_success(self, mock_k8s_cls, mock_k8s_config,
                     mock_build_status, mock_jira_map):
        mock_k8s = MagicMock()
        mock_k8s.list_components.return_value = [
            {'name': 'comp-a', 'application': 'rhoai-v3-5'},
            {'name': 'comp-b', 'application': 'rhoai-v3-5'},
        ]
        mock_k8s.list_pac_repositories.return_value = []
        mock_k8s_cls.return_value = mock_k8s

        mock_build_status.side_effect = [
            {'component': 'comp-a', 'score': 80, 'overall': 'complete'},
            {'component': 'comp-b', 'score': 40, 'overall': 'partial'},
        ]
        mock_jira_map.return_value = {
            'comp-a': {
                'key': 'RHOAIENG-1',
                'status': 'Done',
                'url': 'https://issues.redhat.com/browse/RHOAIENG-1',
            },
        }

        with patch.dict(os.environ, {'NAMESPACE': 'test-ns'}):
            result = _get_onboarding_sync('rhoai-v3-5')

        assert result['application'] == 'rhoai-v3-5'
        assert result['total'] == 2
        assert result['complete'] == 1
        assert result['partial'] == 1
        assert result['incomplete'] == 0
        # sorted by score
        assert result['components'][0]['score'] == 40
        # jira info on comp-a
        comp_a = next(c for c in result['components'] if c['component'] == 'comp-a')
        assert comp_a['jira_key'] == 'RHOAIENG-1'

    @patch('openshift_auth._ensure_k8s_config')
    @patch('clients.kubernetes.KubernetesClient')
    def test_k8s_timeout(self, mock_k8s_cls, mock_k8s_config):
        mock_k8s = MagicMock()
        mock_k8s.list_components.side_effect = TimeoutError('timed out')
        mock_k8s_cls.return_value = mock_k8s

        with patch.dict(os.environ, {'NAMESPACE': 'test-ns'}):
            result = _get_onboarding_sync('rhoai-v3-5')
        assert 'error' in result
        assert 'K8s timeout' in result['error']


# ═══════════════════════════════════════════════════════════════════════════
# HTTP endpoints
# ═══════════════════════════════════════════════════════════════════════════


class TestGetComponentOnboardingEndpoint:
    @patch('api.routes.onboarding._enrich_with_jira')
    @patch('clients.kubernetes.KubernetesClient')
    def test_component_not_found(self, mock_k8s_cls, mock_enrich, client):
        mock_k8s = MagicMock()
        mock_k8s.list_components.return_value = [
            {'name': 'other-comp'},
        ]
        mock_k8s.list_pac_repositories.return_value = []
        mock_k8s_cls.return_value = mock_k8s

        resp = client.get('/api/v1/applications/rhoai-v3-5/onboarding/missing-comp')
        data = resp.json()
        assert 'error' in data
        assert 'not found' in data['error']

    def test_no_namespace(self, client):
        with patch.dict(os.environ, {'NAMESPACE': ''}, clear=False):
            resp = client.get('/api/v1/applications/rhoai-v3-5/onboarding/some-comp')
        data = resp.json()
        assert 'error' in data
        assert 'NAMESPACE' in data['error']

    @patch('api.routes.onboarding._enrich_with_jira')
    @patch('clients.konflux_client.KonfluxClient')
    @patch('clients.kubernetes.KubernetesClient')
    def test_component_found(self, mock_k8s_cls, mock_kfx_cls,
                              mock_enrich, client):
        mock_k8s = MagicMock()
        mock_k8s.list_components.return_value = [
            _make_comp(name='my-comp'),
        ]
        mock_k8s.list_pac_repositories.return_value = []
        mock_k8s.list_recent_pipelineruns.return_value = []
        mock_k8s_cls.return_value = mock_k8s

        mock_kfx = MagicMock()
        mock_kfx.get_release_plan_admissions.return_value = []
        mock_kfx.get_dependency_updates.return_value = []
        mock_kfx_cls.return_value = mock_kfx

        resp = client.get('/api/v1/applications/rhoai-v3-5/onboarding/my-comp')
        data = resp.json()
        assert resp.status_code == 200
        assert data.get('component') == 'my-comp'
        assert 'steps' in data

    @patch('api.routes.onboarding._enrich_with_jira')
    @patch('clients.konflux_client.KonfluxClient')
    @patch('clients.kubernetes.KubernetesClient')
    def test_with_nudge_prs(self, mock_k8s_cls, mock_kfx_cls,
                             mock_enrich, client):
        mock_k8s = MagicMock()
        mock_k8s.list_components.return_value = [_make_comp(name='my-comp')]
        mock_k8s.list_pac_repositories.return_value = []
        mock_k8s.list_recent_pipelineruns.return_value = []
        mock_k8s_cls.return_value = mock_k8s

        mock_kfx = MagicMock()
        mock_kfx.get_release_plan_admissions.return_value = []
        mock_kfx.get_dependency_updates.return_value = [
            {'pr_url': 'https://github.com/pr/1', 'package': 'dep-a',
             'from_version': '1.0', 'to_version': '1.1',
             'merged': True, 'created': '2026-06-01'},
        ]
        mock_kfx_cls.return_value = mock_kfx

        resp = client.get('/api/v1/applications/rhoai-v3-5/onboarding/my-comp')
        data = resp.json()
        assert 'nudge_prs' in data
        assert len(data['nudge_prs']) == 1
        assert data['nudge_prs'][0]['pr_url'] == 'https://github.com/pr/1'

    @patch('api.routes.onboarding._enrich_with_jira')
    @patch('clients.konflux_client.KonfluxClient')
    @patch('clients.kubernetes.KubernetesClient')
    def test_diff_param(self, mock_k8s_cls, mock_kfx_cls,
                         mock_enrich, client):
        mock_k8s = MagicMock()
        mock_k8s.list_components.return_value = [_make_comp(name='my-comp')]
        mock_k8s.list_pac_repositories.return_value = []
        mock_k8s.list_recent_pipelineruns.return_value = []
        mock_k8s_cls.return_value = mock_k8s

        mock_kfx = MagicMock()
        mock_kfx.get_release_plan_admissions.return_value = []
        mock_kfx.get_dependency_updates.return_value = []
        mock_kfx_cls.return_value = mock_kfx

        with patch('onboarding.jira_analysis.build_diff_data',
                   return_value={'diffs': []}) as mock_diff:
            resp = client.get(
                '/api/v1/applications/rhoai-v3-5/onboarding/my-comp?diff=true')
            data = resp.json()
            assert 'diff' in data

    @patch('api.routes.onboarding._enrich_with_jira')
    @patch('clients.kubernetes.KubernetesClient')
    def test_pipelinerun_exception(self, mock_k8s_cls, mock_enrich, client):
        """PipelineRun fetch failure is handled gracefully."""
        mock_k8s = MagicMock()
        mock_k8s.list_components.return_value = [_make_comp(name='my-comp')]
        mock_k8s.list_pac_repositories.return_value = []
        mock_k8s.list_recent_pipelineruns.side_effect = Exception('k8s error')
        mock_k8s_cls.return_value = mock_k8s

        # Also need to mock KonfluxClient since it's imported inside the function
        with patch('clients.konflux_client.KonfluxClient') as mock_kfx_cls:
            mock_kfx = MagicMock()
            mock_kfx.get_release_plan_admissions.return_value = []
            mock_kfx.get_dependency_updates.return_value = []
            mock_kfx_cls.return_value = mock_kfx

            resp = client.get('/api/v1/applications/rhoai-v3-5/onboarding/my-comp')
        data = resp.json()
        assert resp.status_code == 200
        assert 'error' not in data

    @patch('api.routes.onboarding._enrich_with_jira')
    @patch('clients.kubernetes.KubernetesClient')
    def test_konflux_client_exception(self, mock_k8s_cls, mock_enrich, client):
        """KonfluxClient failure is handled gracefully."""
        mock_k8s = MagicMock()
        mock_k8s.list_components.return_value = [_make_comp(name='my-comp')]
        mock_k8s.list_pac_repositories.return_value = []
        mock_k8s.list_recent_pipelineruns.return_value = []
        mock_k8s_cls.return_value = mock_k8s

        with patch('clients.konflux_client.KonfluxClient',
                   side_effect=Exception('import fail')):
            resp = client.get('/api/v1/applications/rhoai-v3-5/onboarding/my-comp')
        data = resp.json()
        assert resp.status_code == 200

    def test_invalid_application_name(self, client):
        resp = client.get('/api/v1/applications/INVALID_APP/onboarding/comp')
        assert resp.status_code == 422


class TestAnalyzeOnboardingEndpoint:
    @patch('api.routes.onboarding.get_component_onboarding')
    def test_analyze_returns_error_from_get(self, mock_get, client):
        mock_get.return_value = {'error': 'Component not found'}
        resp = client.post('/api/v1/applications/rhoai-v3-5/onboarding/bad-comp/analyze')
        data = resp.json()
        assert 'error' in data

    @patch('api.routes.onboarding.get_component_onboarding')
    def test_analyze_llm_not_configured(self, mock_get, client):
        mock_get.return_value = {'component': 'comp-a', 'steps': []}
        with patch('config.CollectorConfig') as mock_cfg_cls:
            mock_cfg = MagicMock()
            mock_cfg.llm = None
            mock_cfg_cls.from_env.return_value = mock_cfg
            resp = client.post(
                '/api/v1/applications/rhoai-v3-5/onboarding/comp-a/analyze')
        data = resp.json()
        assert 'error' in data
        assert 'LLM not configured' in data['error']

    @patch('api.routes.onboarding.get_component_onboarding')
    def test_analyze_success(self, mock_get, client):
        mock_get.return_value = {'component': 'comp-a', 'steps': []}
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('clients.llm_provider.create_llm_provider') as mock_llm_fn, \
             patch('analyzers.onboarding_analyzer.OnboardingAnalyzer') as mock_analyzer_cls:
            mock_cfg = MagicMock()
            mock_cfg.llm = MagicMock()
            mock_cfg_cls.from_env.return_value = mock_cfg

            mock_analyzer = MagicMock()
            mock_analyzer.analyze.return_value = {
                'root_cause': 'Missing webhook',
                'category': 'pac_issue',
            }
            mock_analyzer_cls.return_value = mock_analyzer

            resp = client.post(
                '/api/v1/applications/rhoai-v3-5/onboarding/comp-a/analyze')
        data = resp.json()
        assert data['component'] == 'comp-a'
        assert data['application'] == 'rhoai-v3-5'
        assert data['root_cause'] == 'Missing webhook'

    @patch('api.routes.onboarding.get_component_onboarding')
    def test_analyze_exception(self, mock_get, client):
        mock_get.return_value = {'component': 'comp-a', 'steps': []}
        with patch('config.CollectorConfig') as mock_cfg_cls:
            mock_cfg_cls.from_env.side_effect = Exception('Config broken')
            resp = client.post(
                '/api/v1/applications/rhoai-v3-5/onboarding/comp-a/analyze')
        data = resp.json()
        assert 'error' in data
        assert 'Analysis failed' in data['error']

    def test_analyze_invalid_application(self, client):
        resp = client.post('/api/v1/applications/BAD_APP/onboarding/comp/analyze')
        assert resp.status_code == 422


class TestGetOnboardingStatusEndpoint:
    @patch('api.routes.onboarding._get_onboarding_sync')
    def test_async_endpoint(self, mock_sync, client):
        mock_sync.return_value = {
            'application': 'rhoai-v3-5',
            'total': 1,
            'complete': 1,
            'partial': 0,
            'incomplete': 0,
            'components': [{'component': 'comp-a', 'score': 100, 'overall': 'complete'}],
        }
        resp = client.get('/api/v1/applications/rhoai-v3-5/onboarding')
        data = resp.json()
        assert data['application'] == 'rhoai-v3-5'
        assert data['total'] == 1

    def test_async_endpoint_invalid_app(self, client):
        resp = client.get('/api/v1/applications/BAD!/onboarding')
        assert resp.status_code == 422
