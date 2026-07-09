"""Comprehensive tests for failures.py and releases.py API routes.

Covers helper functions (direct unit tests) and HTTP endpoints (via TestClient).
Target: ~60 tests covering happy paths, edge cases, and error paths.
"""

import os
import sys
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api import create_app
from api.routes.failures import (
    _compute_age_signals,
    _compute_freeze_countdown,
    _detect_systemic_patterns,
    _detect_unmapped_upstream,
)
from api.routes.releases import (
    _check_cross_product_images,
    _check_fbc_health,
    _check_gha_nightly,
    _check_integration_tests,
    _check_ocp_compatibility,
    _check_pcc,
    _check_policy_consistency,
    _check_snapshot_completeness,
    _check_tutorial_validation,
    _derive_branch,
    _release_classify,
    _release_type_label,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """Full app with all routes mounted — mirrors production setup."""
    with patch.dict(os.environ, {
        'DB_HOST': 'localhost',
        'DB_NAME': 'test_db',
        'DB_USER': 'test_user',
        'DB_PASS': 'test_pass',
        'NAMESPACE': 'test-tenant',
    }), patch('api.init_pool'), patch('api.rate_limit.setup_rate_limiter'):
        app = create_app()
        yield TestClient(app, raise_server_exceptions=False)


def _make_failure_summary(**overrides):
    """Build a minimal FailureSummary-like object for testing helper functions."""
    obj = MagicMock()
    obj.component = overrides.get('component', 'test-comp')
    obj.error_type = overrides.get('error_type', None)
    obj.possible_cause = overrides.get('possible_cause', None)
    return obj


def _mock_db_connection():
    """Create a mock DB connection with cursor for releases routes."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=None)
    return mock_conn, mock_cursor


# ═══════════════════════════════════════════════════════════════════════════
# FAILURES ROUTE — Helper Functions
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeFreezeCountdown:
    """Tests for _compute_freeze_countdown()."""

    def test_none_schedule_returns_none(self):
        assert _compute_freeze_countdown(None) is None

    def test_empty_schedule_returns_none(self):
        assert _compute_freeze_countdown({}) is None

    def test_all_none_days_returns_none(self):
        schedule = {
            'code_freeze_days': None,
            'initial_rc_days': None,
            'release_date_days': None,
        }
        assert _compute_freeze_countdown(schedule) is None

    def test_pre_freeze_phase(self):
        schedule = {
            'code_freeze_days': 10,
            'initial_rc_days': 20,
            'release_date_days': 30,
        }
        result = _compute_freeze_countdown(schedule)
        assert result.phase == 'pre-freeze'
        assert result.code_freeze_days == 10
        assert '10 days to code freeze' in result.message

    def test_frozen_phase(self):
        schedule = {
            'code_freeze_days': -2,
            'initial_rc_days': 5,
            'release_date_days': 15,
        }
        result = _compute_freeze_countdown(schedule)
        assert result.phase == 'frozen'
        assert 'Code freeze ACTIVE' in result.message

    def test_rc_phase(self):
        schedule = {
            'code_freeze_days': -10,
            'initial_rc_days': -3,
            'release_date_days': 5,
        }
        result = _compute_freeze_countdown(schedule)
        assert result.phase == 'rc'
        assert 'RC phase' in result.message

    def test_released_phase(self):
        schedule = {
            'code_freeze_days': -20,
            'initial_rc_days': -10,
            'release_date_days': -2,
        }
        result = _compute_freeze_countdown(schedule)
        assert result.phase == 'released'
        assert 'GA was 2 days ago' in result.message

    def test_urgency_critical(self):
        schedule = {'code_freeze_days': 2, 'initial_rc_days': None, 'release_date_days': None}
        result = _compute_freeze_countdown(schedule)
        assert result.urgency == 'critical'

    def test_urgency_high(self):
        schedule = {'code_freeze_days': 5, 'initial_rc_days': None, 'release_date_days': None}
        result = _compute_freeze_countdown(schedule)
        assert result.urgency == 'high'

    def test_urgency_normal(self):
        schedule = {'code_freeze_days': 12, 'initial_rc_days': None, 'release_date_days': None}
        result = _compute_freeze_countdown(schedule)
        assert result.urgency == 'normal'

    def test_urgency_low(self):
        schedule = {'code_freeze_days': 30, 'initial_rc_days': None, 'release_date_days': None}
        result = _compute_freeze_countdown(schedule)
        assert result.urgency == 'low'

    def test_release_day_zero(self):
        schedule = {
            'code_freeze_days': -10,
            'initial_rc_days': -5,
            'release_date_days': 0,
        }
        result = _compute_freeze_countdown(schedule)
        assert 'GA release TODAY' in result.message


class TestComputeAgeSignals:
    """Tests for _compute_age_signals()."""

    def test_new_alert(self):
        now = datetime.utcnow()
        first_seen = now - timedelta(hours=5)
        last_seen = now - timedelta(hours=1)
        age_h, is_new, status_changed = _compute_age_signals(first_seen, last_seen)
        assert is_new is True
        assert 4 < age_h < 6

    def test_old_alert(self):
        now = datetime.utcnow()
        first_seen = now - timedelta(hours=72)
        last_seen = now - timedelta(hours=48)
        age_h, is_new, status_changed = _compute_age_signals(first_seen, last_seen)
        assert is_new is False
        assert age_h > 70

    def test_status_changed_recently(self):
        now = datetime.utcnow()
        first_seen = now - timedelta(hours=72)
        last_seen = now - timedelta(hours=6)
        age_h, is_new, status_changed = _compute_age_signals(first_seen, last_seen)
        assert is_new is False
        assert status_changed is True

    def test_none_first_seen(self):
        age_h, is_new, status_changed = _compute_age_signals(None, None)
        assert age_h == 0
        assert is_new is True


class TestDetectSystemicPatterns:
    """Tests for _detect_systemic_patterns()."""

    def test_two_go_version_components(self):
        f1 = _make_failure_summary(component='comp-a', error_type='go version mismatch')
        f2 = _make_failure_summary(component='comp-b', error_type='go1.22 required')
        failures = [f1, f2]
        _detect_systemic_patterns(failures)
        assert 'systemic' in (f1.possible_cause or '')
        assert 'systemic' in (f2.possible_cause or '')

    def test_single_component_no_systemic(self):
        f1 = _make_failure_summary(component='comp-a', error_type='go version mismatch')
        failures = [f1]
        _detect_systemic_patterns(failures)
        # With only one component matching a pattern, no systemic tag
        assert f1.possible_cause is None

    def test_multiple_patterns(self):
        f1 = _make_failure_summary(component='comp-a', error_type='npm err registry')
        f2 = _make_failure_summary(component='comp-b', error_type='npm package not found')
        f3 = _make_failure_summary(component='comp-c', error_type='s390x build failed')
        f4 = _make_failure_summary(component='comp-d', error_type='linux/s390x timeout')
        failures = [f1, f2, f3, f4]
        _detect_systemic_patterns(failures)
        assert 'systemic' in (f1.possible_cause or '')
        assert 'systemic' in (f3.possible_cause or '')


class TestDetectUnmappedUpstream:
    """Tests for _detect_unmapped_upstream()."""

    def test_upstream_prefix_detected(self):
        f = _make_failure_summary(component='upstream-model-registry')
        unmapped = _detect_unmapped_upstream([f])
        assert 'upstream-model-registry' in unmapped
        assert 'upstream sync component' in (f.possible_cause or '')

    def test_upstream_suffix_detected(self):
        f = _make_failure_summary(component='model-registry-upstream')
        unmapped = _detect_unmapped_upstream([f])
        assert len(unmapped) == 1

    def test_no_upstream_pattern(self):
        f = _make_failure_summary(component='odh-operator')
        unmapped = _detect_unmapped_upstream([f])
        assert unmapped == []

    def test_appends_to_existing_cause(self):
        f = _make_failure_summary(
            component='upstream-foo', possible_cause='existing cause')
        _detect_unmapped_upstream([f])
        assert 'existing cause' in f.possible_cause
        assert 'upstream sync component' in f.possible_cause


# ═══════════════════════════════════════════════════════════════════════════
# FAILURES ROUTE — HTTP Endpoints
# ═══════════════════════════════════════════════════════════════════════════


class TestListFailuresEndpoint:
    """Tests for GET /api/v1/applications/{app}/failures."""

    @patch('api.routes.failures.get_repository')
    def test_list_failures_basic(self, mock_get_repo, client):
        mock_build_repo = MagicMock()
        mock_build_repo.get_triage_summary.return_value = {
            'failing_components': [{
                'component': 'comp-a',
                'status': 'Failed',
                'error_type': 'build-error',
                'first_detected_at': datetime(2026, 6, 20, 10, 0, 0),
                'last_updated_at': datetime(2026, 6, 25, 10, 0, 0),
                'failure_count': 3,
                'has_logs': True,
                'ai_analyzed': True,
            }],
        }
        mock_get_repo.return_value = mock_build_repo

        resp = client.get('/api/v1/applications/test-app/failures')
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]['component'] == 'comp-a'
        assert data[0]['has_analysis'] is True

    @patch('api.routes.failures.get_repository')
    def test_list_failures_filter_has_analysis_true(self, mock_get_repo, client):
        mock_build_repo = MagicMock()
        mock_build_repo.get_triage_summary.return_value = {
            'failing_components': [
                {'component': 'analyzed', 'ai_analyzed': True,
                 'first_detected_at': datetime.utcnow(),
                 'last_updated_at': datetime.utcnow()},
                {'component': 'not-analyzed', 'ai_analyzed': False,
                 'first_detected_at': datetime.utcnow(),
                 'last_updated_at': datetime.utcnow()},
            ],
        }
        mock_get_repo.return_value = mock_build_repo

        resp = client.get('/api/v1/applications/test-app/failures?has_analysis=true')
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]['component'] == 'analyzed'


class TestGetFailureEndpoint:
    """Tests for GET /api/v1/applications/{app}/failures/{component}."""

    @patch('api.routes.failures.get_repository')
    def test_get_failure_found(self, mock_get_repo, client):
        mock_build_repo = MagicMock()
        mock_build_repo.get_failure_details.return_value = {
            'component_name': 'comp-a',
            'pipelinerun_name': 'pr-123',
            'status': 'Failed',
            'error_message': 'build failed',
            'error_type': 'compilation',
            'failed_task_name': 'build-container',
            'failed_step_name': 'build',
            'task_summary': None,
            'build_logs': 'some logs',
            'commit_sha': 'abc123',
            'commit_message': 'fix stuff',
            'commit_author': 'dev',
            'commit_url': 'https://github.com/org/repo/commit/abc123',
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'output_image': 'quay.io/org/comp-a:latest',
            'jira_key': None,
            'build_duration_seconds': 120,
            'ai_analyzed': False,
            'is_resolved': False,
            'commit_context': None,
            'konflux_url': None,
            'first_detected_at': datetime(2026, 6, 20, 10, 0, 0),
        }
        mock_build_repo.get_component_history.return_value = {
            'builds': [{'status': 'Failed'}],
        }
        mock_get_repo.return_value = mock_build_repo

        resp = client.get('/api/v1/applications/test-app/failures/comp-a')
        assert resp.status_code == 200
        data = resp.json()
        assert data['component'] == 'comp-a'
        assert data['status'] == 'Failed'

    @patch('api.routes.failures.get_repository')
    def test_get_failure_not_found(self, mock_get_repo, client):
        mock_build_repo = MagicMock()
        mock_build_repo.get_failure_details.return_value = None
        mock_get_repo.return_value = mock_build_repo

        resp = client.get('/api/v1/applications/test-app/failures/nonexistent')
        assert resp.status_code == 404


class TestGetComponentHistory:
    """Tests for GET /api/v1/applications/{app}/failures/{component}/history."""

    @patch('api.routes.failures.get_repository')
    def test_component_history(self, mock_get_repo, client):
        mock_build_repo = MagicMock()
        mock_build_repo.get_component_history.return_value = {
            'summary': {'total': 5, 'succeeded': 3, 'failed': 2},
            'builds': [
                {'status': 'Succeeded', 'created': '2026-06-25'},
                {'status': 'Failed', 'created': '2026-06-24'},
            ],
        }
        mock_get_repo.return_value = mock_build_repo

        resp = client.get('/api/v1/applications/test-app/failures/comp-a/history')
        assert resp.status_code == 200
        data = resp.json()
        assert data['component'] == 'comp-a'
        assert data['application'] == 'test-app'
        assert len(data['builds']) == 2


class TestGetWorkingAndResolved:
    """Tests for GET /api/v1/applications/{app}/working and /resolved."""

    @patch('api.routes.failures.get_repository')
    def test_get_working(self, mock_get_repo, client):
        mock_build_repo = MagicMock()
        mock_build_repo.get_working_components.return_value = [
            {'component': 'good-comp', 'status': 'Succeeded'},
        ]
        mock_get_repo.return_value = mock_build_repo

        resp = client.get('/api/v1/applications/test-app/working')
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]['component'] == 'good-comp'

    @patch('api.routes.failures.get_repository')
    def test_get_resolved(self, mock_get_repo, client):
        mock_build_repo = MagicMock()
        mock_build_repo.get_resolved_components.return_value = [
            {'component': 'fixed-comp'},
        ]
        mock_get_repo.return_value = mock_build_repo

        resp = client.get('/api/v1/applications/test-app/resolved')
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1


class TestLookupImage:
    """Tests for GET /api/v1/lookup."""

    @patch('api.routes.failures.get_repository')
    def test_lookup_image(self, mock_get_repo, client):
        mock_build_repo = MagicMock()
        mock_build_repo.find_by_image.return_value = [
            {'component': 'comp-a', 'image': 'quay.io/org/comp-a@sha256:abc'},
        ]
        mock_get_repo.return_value = mock_build_repo

        resp = client.get('/api/v1/lookup?image=quay.io/org/comp-a')
        assert resp.status_code == 200
        data = resp.json()
        assert data['query'] == 'quay.io/org/comp-a'
        assert data['total_matches'] >= 1


class TestListBlockers:
    """Tests for GET /api/v1/applications/{app}/blockers."""

    def test_blockers_with_signals(self, client):
        now = datetime.utcnow()
        old_date = (now - timedelta(hours=100)).strftime('%Y-%m-%dT%H:%M:%S.000+0000')
        stale_update = (now - timedelta(hours=60)).strftime('%Y-%m-%dT%H:%M:%S.000+0000')

        mock_jira = MagicMock()
        mock_jira.search_blockers.return_value = [{
            'key': 'RHOAIENG-99999',
            'summary': 'gaudi driver is broken',
            'status': 'Open',
            'status_category': 'new',
            'assignee': None,
            'created': old_date,
            'updated': stale_update,
            'resolution': None,
            'labels': ['gaudi'],
        }]

        with patch.dict(os.environ, {'JIRA_EMAIL': 'x@x.com', 'JIRA_TOKEN': 'tok'}), \
             patch('clients.jira_client.JiraClient', return_value=mock_jira):
            resp = client.get('/api/v1/applications/test-app/blockers')

        assert resp.status_code == 200
        data = resp.json()
        assert data['total_blockers'] == 1
        assert data['open_blockers'] == 1
        blockers = data['blockers']
        assert len(blockers) == 1
        assert 'unassigned_24h' in blockers[0]['signals']
        assert 'stale_48h' in blockers[0]['signals']
        assert 'hardware_blocked' in blockers[0]['signals']


class TestBouncingIssues:
    """Tests for GET /api/v1/applications/{app}/bouncing-issues."""

    def test_bouncing_issues_jira_unavailable(self, client):
        with patch('clients.jira_client.JiraClient',
                   side_effect=Exception('no jira')):
            resp = client.get('/api/v1/applications/test-app/bouncing-issues')
        assert resp.status_code == 200
        data = resp.json()
        assert data['total_bouncing'] == 0


class TestJiraHealth:
    """Tests for GET /api/v1/jira/health."""

    def test_jira_health_unreachable(self):
        """Test check_jira_health directly when JiraClient raises."""
        from api.routes.failures import check_jira_health
        with patch('clients.jira_client.JiraClient',
                   side_effect=Exception('conn refused')):
            result = check_jira_health()
        assert result.status == 'unreachable'
        assert 'conn refused' in result.message

    def test_jira_health_valid(self):
        """Test check_jira_health directly with a valid token."""
        from api.routes.failures import check_jira_health
        mock_jira = MagicMock()
        mock_jira.check_token_health.return_value = {
            'status': 'valid',
            'user': 'testuser',
            'message': 'Token is valid',
        }
        with patch('clients.jira_client.JiraClient', return_value=mock_jira):
            result = check_jira_health()
        assert result.status == 'valid'
        assert result.user == 'testuser'


class TestFixPropagation:
    """Tests for GET /api/v1/applications/{app}/fix-propagation."""

    @patch('api.routes.failures.get_repository')
    def test_no_resolved_components(self, mock_get_repo, client):
        mock_build_repo = MagicMock()
        mock_build_repo.get_resolved_components.return_value = []
        mock_get_repo.return_value = mock_build_repo

        resp = client.get('/api/v1/applications/test-app/fix-propagation')
        assert resp.status_code == 200
        data = resp.json()
        assert data['checked_count'] == 0
        assert data['total_missing'] == 0

    @patch.dict(os.environ, {'GITHUB_TOKEN': ''})
    @patch('api.routes.failures.get_repository')
    def test_no_github_token(self, mock_get_repo, client):
        mock_build_repo = MagicMock()
        mock_build_repo.get_resolved_components.return_value = [
            {'component': 'comp-a', 'commit_sha': 'abc1234'},
        ]
        mock_get_repo.return_value = mock_build_repo

        resp = client.get('/api/v1/applications/test-app/fix-propagation')
        assert resp.status_code == 200
        data = resp.json()
        assert data['checked_count'] == 0


# ═══════════════════════════════════════════════════════════════════════════
# RELEASES ROUTE — Helper Functions
# ═══════════════════════════════════════════════════════════════════════════


class TestDeriveBranch:
    """Tests for _derive_branch()."""

    def test_ea_branch(self):
        assert _derive_branch('rhoai-v3-5-ea-2') == 'rhoai-3.5-ea.2'

    def test_ga_branch(self):
        assert _derive_branch('rhoai-v3-5') == 'rhoai-3.5'

    def test_non_matching_passthrough(self):
        assert _derive_branch('some-other-app') == 'some-other-app'


class TestReleaseClassify:
    """Tests for _release_classify()."""

    def test_components_stage(self):
        assert _release_classify('app-components-stage') == ('stage', 'components')

    def test_components_prod(self):
        assert _release_classify('app-components-prod') == ('prod', 'components')

    def test_charts_stage(self):
        assert _release_classify('app-charts-stage') == ('stage', 'charts')

    def test_charts_prod(self):
        assert _release_classify('app-charts-prod') == ('prod', 'charts')

    def test_addon_fbc_stage(self):
        assert _release_classify('app-addon-odf-fbc-stage') == ('stage', 'fbc-addon')

    def test_addon_fbc_prod(self):
        assert _release_classify('app-addon-odf-fbc-prod') == ('prod', 'fbc-addon')

    def test_ocp_fbc_stage(self):
        assert _release_classify('app-ocp-416-fbc-stage') == ('stage', 'fbc-416')

    def test_ocp_fbc_prod(self):
        assert _release_classify('app-ocp-416-fbc-prod') == ('prod', 'fbc-416')

    def test_unknown(self):
        assert _release_classify('some-random-plan') == ('unknown', 'unknown')


class TestReleaseTypeLabel:
    """Tests for _release_type_label()."""

    def test_components_label(self):
        assert _release_type_label('components') == 'Components'

    def test_charts_label(self):
        assert _release_type_label('charts') == 'Charts'

    def test_fbc_addon_label(self):
        assert _release_type_label('fbc-addon') == 'FBC Addon'

    def test_fbc_ocp_label(self):
        assert _release_type_label('fbc-416') == 'FBC OCP 4.16'

    def test_fbc_ocp_422(self):
        assert _release_type_label('fbc-422') == 'FBC OCP 4.22'

    def test_unknown_passthrough(self):
        assert _release_type_label('custom-thing') == 'custom-thing'


class TestCheckTutorialValidation:
    """Tests for _check_tutorial_validation()."""

    def test_always_warns(self):
        result = _check_tutorial_validation()
        assert result['status'] == 'WARN'
        assert 'Tutorial validation' in result['name']
        assert 'Fraud Detection' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# RELEASES ROUTE — Readiness Check Functions
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckFbcHealth:
    """Tests for _check_fbc_health()."""

    def test_k8s_unreachable(self):
        result = _check_fbc_health(MagicMock(), 'test-app', k8s_reachable=False)
        assert result['status'] == 'SKIP'
        assert 'unreachable' in result['detail']

    def test_fbc_pass(self):
        monitor = MagicMock()
        monitor.get_nightly_status.return_value = {
            'fbc_health': {'current_status': 'Succeeded', 'health_score': 95},
        }
        result = _check_fbc_health(monitor, 'test-app', k8s_reachable=True)
        assert result['status'] == 'PASS'

    def test_fbc_fail(self):
        monitor = MagicMock()
        monitor.get_nightly_status.return_value = {
            'fbc_health': {'current_status': 'Failed'},
            'fbc_component': 'fbc-fragment',
        }
        result = _check_fbc_health(monitor, 'test-app', k8s_reachable=True)
        assert result['status'] == 'FAIL'

    def test_fbc_no_data(self):
        monitor = MagicMock()
        monitor.get_nightly_status.return_value = {'fbc_health': None}
        result = _check_fbc_health(monitor, 'test-app', k8s_reachable=True)
        assert result['status'] == 'WARN'


class TestCheckPcc:
    """Tests for _check_pcc()."""

    def test_pcc_stale(self):
        monitor = MagicMock()
        monitor._check_pcc_freshness.return_value = {
            'status': 'stale',
            'missing_versions': ['3.5.0', '3.6.0'],
        }
        result = _check_pcc(monitor)
        assert result['status'] == 'FAIL'
        assert '2 version(s)' in result['detail']

    def test_pcc_fresh(self):
        monitor = MagicMock()
        monitor._check_pcc_freshness.return_value = {
            'status': 'fresh',
            'cached_versions': 10,
        }
        result = _check_pcc(monitor)
        assert result['status'] == 'PASS'

    def test_pcc_skip_no_token(self):
        monitor = MagicMock()
        monitor._check_pcc_freshness.return_value = None
        result = _check_pcc(monitor)
        assert result['status'] == 'SKIP'


class TestCheckGhaNightly:
    """Tests for _check_gha_nightly()."""

    def test_gha_pass(self):
        monitor = MagicMock()
        monitor.get_nightly_status.return_value = {
            'gha_validation': {'conclusion': 'success', 'created_at': '2026-06-25'},
        }
        result = _check_gha_nightly(monitor, 'test-app')
        assert result['status'] == 'PASS'

    def test_gha_fail(self):
        monitor = MagicMock()
        monitor.get_nightly_status.return_value = {
            'gha_validation': {'conclusion': 'failure', 'created_at': '2026-06-25', 'url': 'http://x'},
        }
        result = _check_gha_nightly(monitor, 'test-app')
        assert result['status'] == 'WARN'

    def test_gha_skip_no_data(self):
        monitor = MagicMock()
        monitor.get_nightly_status.return_value = {'gha_validation': None}
        result = _check_gha_nightly(monitor, 'test-app')
        assert result['status'] == 'SKIP'


class TestCheckIntegrationTests:
    """Tests for _check_integration_tests()."""

    def test_no_snapshot(self):
        result = _check_integration_tests(None)
        assert result['status'] == 'SKIP'

    @patch('clients.konflux_client.KonfluxClient')
    def test_tests_passing(self, mock_kfx_cls):
        mock_kfx_cls.extract_snapshot_status.return_value = {
            'test_results': {
                'test-a': {'status': 'True'},
                'test-b': {'status': 'True'},
            },
        }
        snapshot = {'status': {'conditions': []}}
        result = _check_integration_tests(snapshot)
        assert result['status'] == 'PASS'
        assert '2 integration tests passed' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_tests_failing(self, mock_kfx_cls):
        mock_kfx_cls.extract_snapshot_status.return_value = {
            'test_results': {
                'test-a': {'status': 'True'},
                'test-b': {'status': 'False'},
            },
        }
        snapshot = {'status': {'conditions': []}}
        result = _check_integration_tests(snapshot)
        assert result['status'] == 'FAIL'
        assert 'test-b' in result['detail']


class TestCheckPolicyConsistency:
    """Tests for _check_policy_consistency()."""

    def test_no_rpas(self):
        result = _check_policy_consistency([])
        assert result['status'] == 'SKIP'

    @patch('clients.konflux_client.KonfluxClient')
    def test_policies_match(self, mock_kfx_cls):
        mock_kfx_cls.extract_rpa_bindings.return_value = [
            {'target': 'stage', 'policy': 'rhoai-policy', 'rpa_name': 'rpa-stage'},
            {'target': 'prod', 'policy': 'rhoai-policy', 'rpa_name': 'rpa-prod'},
        ]
        result = _check_policy_consistency(['rpa-stage', 'rpa-prod'])
        assert result['status'] == 'PASS'

    @patch('clients.konflux_client.KonfluxClient')
    def test_policies_mismatch(self, mock_kfx_cls):
        mock_kfx_cls.extract_rpa_bindings.return_value = [
            {'target': 'stage', 'policy': 'stage-policy', 'rpa_name': 'rpa-stage'},
            {'target': 'prod', 'policy': 'prod-policy', 'rpa_name': 'rpa-prod'},
        ]
        result = _check_policy_consistency(['rpa-stage', 'rpa-prod'])
        assert result['status'] == 'WARN'
        assert 'differs' in result['detail']


class TestCheckSnapshotCompleteness:
    """Tests for _check_snapshot_completeness()."""

    def test_no_k8s(self):
        result = _check_snapshot_completeness(None, None, 'test-app')
        assert result['status'] == 'SKIP'

    def test_complete_snapshot(self):
        k8s = MagicMock()
        k8s.list_components.return_value = [
            {'name': 'comp-a'}, {'name': 'comp-b'},
        ]
        snapshot = {
            'spec': {
                'components': [
                    {'name': 'comp-a'},
                    {'name': 'comp-b'},
                ],
            },
        }
        result = _check_snapshot_completeness(k8s, snapshot, 'test-app')
        assert result['status'] == 'PASS'
        assert '2 application components' in result['detail']

    def test_missing_components(self):
        k8s = MagicMock()
        k8s.list_components.return_value = [
            {'name': 'comp-a'}, {'name': 'comp-b'}, {'name': 'comp-c'},
        ]
        snapshot = {
            'spec': {
                'components': [
                    {'name': 'comp-a'},
                ],
            },
        }
        result = _check_snapshot_completeness(k8s, snapshot, 'test-app')
        assert result['status'] == 'WARN'
        assert 'missing from snapshot' in result['detail']


class TestCheckCrossProductImages:
    """Tests for _check_cross_product_images()."""

    def test_no_snapshot(self):
        result = _check_cross_product_images(None)
        assert result['status'] == 'SKIP'

    def test_clean_snapshot(self):
        snapshot = {
            'spec': {
                'components': [
                    {'name': 'comp-a', 'containerImage': 'quay.io/org/comp-a@sha256:abc'},
                ],
            },
        }
        result = _check_cross_product_images(snapshot)
        assert result['status'] == 'PASS'

    def test_stage_refs(self):
        snapshot = {
            'spec': {
                'components': [
                    {'name': 'comp-a',
                     'containerImage': 'registry.stage.redhat.io/rhoai/comp-a@sha256:abc'},
                ],
            },
        }
        result = _check_cross_product_images(snapshot)
        assert result['status'] == 'WARN'
        assert 'stage registry' in result['detail']

    def test_external_refs(self):
        snapshot = {
            'spec': {
                'components': [
                    {'name': 'comp-a',
                     'containerImage': 'registry.redhat.io/rhaii/comp-a@sha256:abc'},
                ],
            },
        }
        result = _check_cross_product_images(snapshot)
        assert result['status'] == 'WARN'
        assert 'external product images' in result['detail']


class TestCheckOcpCompatibility:
    """Tests for _check_ocp_compatibility()."""

    def test_no_snapshot(self):
        result = _check_ocp_compatibility(None)
        assert result['status'] == 'SKIP'

    def test_pass_no_affected(self):
        snapshot = {
            'spec': {
                'components': [
                    {'name': 'odh-operator'},
                    {'name': 'model-controller'},
                ],
            },
        }
        result = _check_ocp_compatibility(snapshot)
        assert result['status'] == 'PASS'

    def test_warn_affected_component(self):
        snapshot = {
            'spec': {
                'components': [
                    {'name': 'payload-processing'},
                    {'name': 'gateway'},
                ],
            },
        }
        result = _check_ocp_compatibility(snapshot)
        assert result['status'] == 'WARN'
        assert 'OCP 4.22' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# RELEASES ROUTE — HTTP Endpoints
# ═══════════════════════════════════════════════════════════════════════════


class TestFreezesEndpoints:
    """Tests for freeze CRUD endpoints."""

    @patch('api.routes.releases._db')
    def test_list_freezes(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        today = date.today()
        mock_cursor.fetchall.return_value = [
            (1, today - timedelta(days=5), today + timedelta(days=5), 'Active freeze'),
            (2, today - timedelta(days=30), today - timedelta(days=20), 'Past freeze'),
            (3, today + timedelta(days=5), today + timedelta(days=15), 'Future freeze'),
        ]

        resp = client.get('/api/v1/freezes')
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        statuses = [d['status'] for d in data]
        assert 'active' in statuses
        assert 'past' in statuses
        assert 'upcoming' in statuses

    @patch('api.routes.releases._db')
    def test_get_active_freeze_found(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        mock_cursor.fetchone.return_value = (
            1, date(2026, 7, 1), date(2026, 7, 15), 'Code freeze',
        )

        resp = client.get('/api/v1/freezes/active')
        assert resp.status_code == 200
        data = resp.json()
        assert data['status'] == 'active'
        assert data['reason'] == 'Code freeze'

    @patch('api.routes.releases._db')
    def test_get_active_freeze_none(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        resp = client.get('/api/v1/freezes/active')
        assert resp.status_code == 200
        assert resp.json() is None

    @patch('api.routes.releases._db')
    def test_add_freeze_valid(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        mock_cursor.fetchone.return_value = (42,)

        resp = client.post('/api/v1/freezes', json={
            'start_date': '2026-08-01',
            'end_date': '2026-08-15',
            'reason': 'Test freeze',
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['id'] == 42

    def test_add_freeze_invalid_dates(self, client):
        resp = client.post('/api/v1/freezes', json={
            'start_date': 'not-a-date',
            'end_date': '2026-08-15',
            'reason': 'Bad date',
        })
        assert resp.status_code == 400

    def test_add_freeze_end_before_start(self, client):
        resp = client.post('/api/v1/freezes', json={
            'start_date': '2026-08-15',
            'end_date': '2026-08-01',
            'reason': 'Wrong order',
        })
        assert resp.status_code == 400

    @patch('api.routes.releases._db')
    def test_remove_freeze_found(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        mock_cursor.rowcount = 1

        resp = client.delete('/api/v1/freezes/42')
        assert resp.status_code == 200
        data = resp.json()
        assert data['status'] == 'deleted'

    @patch('api.routes.releases._db')
    def test_remove_freeze_not_found(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        mock_cursor.rowcount = 0

        resp = client.delete('/api/v1/freezes/999')
        assert resp.status_code == 404


class TestScheduleEndpoint:
    """Tests for GET /api/v1/applications/{app}/schedule."""

    @patch('api.routes.releases._db')
    def test_schedule_found(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        mock_cursor.fetchone.return_value = (
            date(2026, 6, 15),  # planning_freeze
            date(2026, 7, 1),   # feature_freeze
            date(2026, 7, 15),  # code_freeze
            date(2026, 7, 22),  # initial_rc
            date(2026, 7, 28),  # release_window_start
            date(2026, 8, 5),   # release_date
            'rhoai-v3-6',       # next_release
            datetime(2026, 6, 1, 10, 0, 0),  # updated_at
        )

        resp = client.get('/api/v1/applications/test-app/schedule')
        assert resp.status_code == 200
        data = resp.json()
        assert data['application'] == 'test-app'
        assert data['next_release'] == 'rhoai-v3-6'
        assert 'code_freeze_days' in data

    @patch('api.routes.releases._db')
    def test_schedule_not_found(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        resp = client.get('/api/v1/applications/test-app/schedule')
        assert resp.status_code == 200
        assert resp.json() is None


class TestSyncSchedule:
    """Tests for POST /api/v1/releases/schedule/sync."""

    @patch('api.routes.releases._db')
    def test_sync_schedule(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn

        resp = client.post('/api/v1/releases/schedule/sync', json=[
            {
                'application': 'rhoai-v3-5',
                'code_freeze': '2026-07-15',
                'release_date': '2026-08-05',
            },
        ])
        assert resp.status_code == 200
        data = resp.json()
        assert data['count'] == 1
        assert 'rhoai-v3-5' in data['synced']


class TestStaleAndNightly:
    """Tests for stale, nightly, and nightly-history endpoints."""

    @patch('proactive.health_monitor.HealthMonitor')
    def test_get_stale(self, mock_monitor_cls, client):
        mock_monitor = MagicMock()
        mock_monitor_cls.return_value = mock_monitor
        mock_monitor.get_stale_components.return_value = {
            'stale_count': 0,
            'stale': [],
            'checked': 10,
        }

        resp = client.get('/api/v1/applications/test-app/stale')
        assert resp.status_code == 200
        data = resp.json()
        assert data['stale_count'] == 0

    @patch('api.routes.releases._db')
    @patch('proactive.health_monitor.HealthMonitor')
    def test_get_nightly(self, mock_monitor_cls, mock_db, client):
        mock_monitor = MagicMock()
        mock_monitor_cls.return_value = mock_monitor
        mock_monitor.get_nightly_status.return_value = {
            'fbc_health': {'current_status': 'Succeeded'},
        }

        resp = client.get('/api/v1/applications/test-app/nightly')
        assert resp.status_code == 200

    @patch('api.routes.releases.get_repository')
    def test_get_nightly_history(self, mock_get_repo, client):
        mock_repo = MagicMock()
        mock_repo.get_nightly_history.return_value = {
            'builds': [],
            'days': 14,
        }
        mock_get_repo.return_value = mock_repo

        resp = client.get('/api/v1/applications/test-app/nightly/history')
        assert resp.status_code == 200


class TestFbcHistory:
    """Tests for GET /api/v1/applications/{app}/fbc-history."""

    @patch('proactive.health_monitor._nightly_component_for_app',
           return_value='odh-fbc-fragment-ci')
    @patch('api.routes.releases.get_repository')
    def test_fbc_history(self, mock_get_repo, mock_nightly, client):
        mock_repo = MagicMock()
        mock_conn, mock_cursor = _mock_db_connection()
        mock_repo.db.connection.return_value = mock_conn
        mock_cursor.fetchall.return_value = [
            ('pr-1', 'Succeeded', 'quay.io/img', 'sha256:abc1234567890123456789', datetime(2026, 7, 1, 10, 0)),
            ('pr-2', 'Failed', 'quay.io/img', 'sha256:def1234567890123456789', datetime(2026, 7, 1, 8, 0)),
        ]
        mock_get_repo.return_value = mock_repo

        resp = client.get('/api/v1/applications/test-app/fbc-history')
        assert resp.status_code == 200
        data = resp.json()
        assert data['fbc_component'] == 'odh-fbc-fragment-ci'
        assert data['total_builds'] == 2


class TestReadinessEndpoint:
    """Tests for GET /api/v1/applications/{app}/readiness."""

    @patch('api.routes.releases._build_manual_checks', return_value={})
    @patch('api.routes.releases._run_readiness_checks', return_value=[])
    @patch('api.routes.releases.get_schedule', return_value=None)
    @patch('api.routes.releases.get_active_freeze', return_value=None)
    @patch('api.routes.releases.get_repository')
    def test_readiness_ready(self, mock_get_repo, mock_freeze, mock_sched,
                              mock_checks, mock_manual, client):
        mock_build_repo = MagicMock()
        mock_conforma_repo = MagicMock()
        mock_build_repo.find_failing_component_names.return_value = set()
        mock_conforma_repo.find_unresolved_component_names.return_value = []
        mock_conforma_repo.get_violation_summaries.return_value = []

        def repo_factory(cls):
            name = cls.__name__
            if name == 'BuildFailureRepository':
                return mock_build_repo
            if name == 'ConformaRepository':
                return mock_conforma_repo
            return MagicMock()

        mock_get_repo.side_effect = repo_factory

        resp = client.get('/api/v1/applications/test-app/readiness')
        assert resp.status_code == 200
        data = resp.json()
        assert data['verdict'] == 'READY'
        assert data['build_failures'] == 0
        assert data['conforma_violations'] == 0

    @patch('api.routes.releases._build_manual_checks', return_value={})
    @patch('api.routes.releases._run_readiness_checks', return_value=[])
    @patch('api.routes.releases.get_schedule', return_value=None)
    @patch('api.routes.releases.get_active_freeze', return_value=None)
    @patch('api.routes.releases.get_repository')
    def test_readiness_not_ready_conforma(self, mock_get_repo, mock_freeze,
                                          mock_sched, mock_checks,
                                          mock_manual, client):
        mock_build_repo = MagicMock()
        mock_conforma_repo = MagicMock()
        mock_build_repo.find_failing_component_names.return_value = set()
        mock_conforma_repo.find_unresolved_component_names.return_value = [
            'comp-a', 'comp-b',
        ]
        mock_conforma_repo.get_violation_summaries.return_value = [
            {
                'component_name': 'comp-a',
                'scenario': 'conforma-registry-rhoai-prod-test-app-single-component',
                'violation_summary': '✕ [Violation] source_image.exists\n  Reason: source image missing',
            },
            {
                'component_name': 'comp-b',
                'scenario': 'conforma-registry-rhoai-prod-test-app-single-component',
                'violation_summary': '✕ [Violation] source_image.exists\n  Reason: source image missing',
            },
        ]

        def repo_factory(cls):
            name = cls.__name__
            if name == 'BuildFailureRepository':
                return mock_build_repo
            if name == 'ConformaRepository':
                return mock_conforma_repo
            return MagicMock()

        mock_get_repo.side_effect = repo_factory

        resp = client.get('/api/v1/applications/test-app/readiness')
        assert resp.status_code == 200
        data = resp.json()
        assert data['verdict'] == 'NOT_READY'
        assert data['conforma_violations'] == 2
        assert data['conforma_blockers'] == 2
