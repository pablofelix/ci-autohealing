"""Comprehensive tests for releases.py API routes.

Covers helper functions (direct unit tests) and HTTP endpoints (via TestClient).
Target: ~60-80 tests covering happy paths, edge cases, and error paths for
all uncovered functions in releases.py.
"""

import os
import sys
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api.routes.releases import (
    _build_manual_checks,
    _build_release_details,
    _check_all_artifacts,
    _check_chains_signing,
    _check_fbc_artifacts,
    _check_fbc_health,
    _check_fbc_prune,
    _check_multiarch_coverage,
    _check_nudge_propagation,
    _check_pcc,
    _check_pcc_post_push,
    _check_prod_rpa_exists,
    _check_release_pipeline_completeness,
    _check_rpm_drift,
    _check_selector_label_changes,
    _check_snapshot_drift,
    _check_snapshot_freshness,
    _check_stage_release_health,
    _check_stale_components,
    _check_test_coverage_regression,
    _derive_branch,
    _release_classify,
    _release_type_label,
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
        from fastapi.testclient import TestClient

        from api import create_app
        app = create_app()
        yield TestClient(app, raise_server_exceptions=False)


def _mock_db_connection():
    """Create a mock DB connection with cursor for releases routes."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=None)
    return mock_conn, mock_cursor


# ═══════════════════════════════════════════════════════════════════════════
# _check_stale_components
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckStaleComponents:

    def test_k8s_unreachable(self):
        monitor = MagicMock()
        result = _check_stale_components(monitor, 'app', k8s_reachable=False)
        assert result['status'] == 'SKIP'
        assert 'K8s cluster unreachable' in result['detail']

    def test_stale_detected(self):
        monitor = MagicMock()
        monitor.get_stale_components.return_value = {
            'stale_count': 2,
            'stale': [
                {'component': 'comp-a'},
                {'component': 'comp-b'},
            ],
        }
        result = _check_stale_components(monitor, 'app', k8s_reachable=True)
        assert result['status'] == 'WARN'
        assert '2 component(s)' in result['detail']
        assert 'comp-a' in result['detail']

    def test_no_stale(self):
        monitor = MagicMock()
        monitor.get_stale_components.return_value = {
            'stale_count': 0,
            'checked': 10,
        }
        result = _check_stale_components(monitor, 'app', k8s_reachable=True)
        assert result['status'] == 'PASS'
        assert '10 checked' in result['detail']

    def test_exception(self):
        monitor = MagicMock()
        monitor.get_stale_components.side_effect = RuntimeError('boom')
        result = _check_stale_components(monitor, 'app', k8s_reachable=True)
        assert result['status'] == 'SKIP'
        assert 'boom' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# _check_snapshot_freshness
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckSnapshotFreshness:

    def test_k8s_unreachable(self):
        monitor = MagicMock()
        result = _check_snapshot_freshness(monitor, 'app', k8s_reachable=False)
        assert result['status'] == 'SKIP'
        assert 'K8s cluster unreachable' in result['detail']

    def test_stale_snapshots(self):
        monitor = MagicMock()
        monitor.check_snapshot_freshness.return_value = {
            'stale_count': 3,
            'stale': [
                {'component': 'c1'},
                {'component': 'c2'},
                {'component': 'c3'},
            ],
        }
        result = _check_snapshot_freshness(monitor, 'app', k8s_reachable=True)
        assert result['status'] == 'WARN'
        assert '3 component(s)' in result['detail']

    def test_fresh_snapshots(self):
        monitor = MagicMock()
        monitor.check_snapshot_freshness.return_value = {
            'stale_count': 0,
            'fresh_count': 15,
        }
        result = _check_snapshot_freshness(monitor, 'app', k8s_reachable=True)
        assert result['status'] == 'PASS'
        assert '15 components' in result['detail']

    def test_exception(self):
        monitor = MagicMock()
        monitor.check_snapshot_freshness.side_effect = ValueError('fail')
        result = _check_snapshot_freshness(monitor, 'app', k8s_reachable=True)
        assert result['status'] == 'SKIP'
        assert 'fail' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# _check_fbc_artifacts
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckFBCArtifacts:

    @patch.dict(os.environ, {'NAMESPACE': ''})
    def test_no_namespace(self):
        result = _check_fbc_artifacts('app')
        assert result['status'] == 'SKIP'
        assert 'NAMESPACE not set' in result['detail']

    @patch.dict(os.environ, {'NAMESPACE': 'test-ns'})
    @patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc-comp')
    def test_no_snapshot_no_kfx(self, mock_nightly):
        with patch('clients.konflux_client.KonfluxClient') as mock_kfx_cls:
            mock_kfx = MagicMock()
            mock_kfx.get_snapshots.return_value = []
            mock_kfx_cls.return_value = mock_kfx
            result = _check_fbc_artifacts('app', snapshot=None)
        assert result['status'] == 'SKIP'
        assert 'No snapshot found' in result['detail']

    @patch.dict(os.environ, {'NAMESPACE': 'test-ns'})
    @patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc-comp')
    def test_fbc_not_in_snapshot(self, mock_nightly):
        snapshot = {'spec': {'components': [{'name': 'other-comp'}]}}
        result = _check_fbc_artifacts('app', snapshot=snapshot)
        assert result['status'] == 'SKIP'
        assert 'fbc-comp not in snapshot' in result['detail']

    @patch.dict(os.environ, {'NAMESPACE': 'test-ns'})
    @patch('clients.registry_client.RegistryClient')
    @patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc-comp')
    def test_unhealthy_artifact(self, mock_nightly, mock_rc_cls):
        mock_rc = MagicMock()
        mock_rc.check_artifact_health_batch.return_value = {
            'fbc-comp': {'healthy': False, 'missing': ['src']},
        }
        mock_rc_cls.return_value = mock_rc
        snapshot = {'spec': {'components': [
            {'name': 'fbc-comp', 'containerImage': 'quay.io/test@sha256:abc'},
        ]}}
        result = _check_fbc_artifacts('app', snapshot=snapshot)
        assert result['status'] == 'FAIL'
        assert 'missing artifacts' in result['detail']

    @patch.dict(os.environ, {'NAMESPACE': 'test-ns'})
    @patch('clients.registry_client.RegistryClient')
    @patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc-comp')
    def test_healthy_artifact(self, mock_nightly, mock_rc_cls):
        mock_rc = MagicMock()
        mock_rc.check_artifact_health_batch.return_value = {
            'fbc-comp': {'healthy': True},
        }
        mock_rc_cls.return_value = mock_rc
        snapshot = {'spec': {'components': [
            {'name': 'fbc-comp', 'containerImage': 'quay.io/test@sha256:abc'},
        ]}}
        result = _check_fbc_artifacts('app', snapshot=snapshot)
        assert result['status'] == 'PASS'
        assert 'all OCI artifacts' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# _check_chains_signing
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckChainsSigning:

    def test_no_k8s(self):
        result = _check_chains_signing(None, 'app')
        assert result['status'] == 'SKIP'
        assert 'NAMESPACE not set' in result['detail']

    def test_no_components(self):
        k8s = MagicMock()
        k8s.list_components.return_value = []
        result = _check_chains_signing(k8s, 'app')
        assert result['status'] == 'SKIP'
        assert 'No components found' in result['detail']

    def test_failed_signing(self):
        k8s = MagicMock()
        k8s.list_components.return_value = [
            {'name': 'comp-a'},
            {'name': 'comp-b'},
        ]
        k8s.list_recent_pipelineruns.side_effect = [
            [{'chains_signed': 'failed'}],
            [{'chains_signed': 'true'}],
        ]
        result = _check_chains_signing(k8s, 'app')
        assert result['status'] == 'FAIL'
        assert 'comp-a' in result['detail']
        assert '1 component(s)' in result['detail']

    def test_all_signed(self):
        k8s = MagicMock()
        k8s.list_components.return_value = [
            {'name': 'comp-a'},
            {'name': 'comp-b'},
        ]
        k8s.list_recent_pipelineruns.return_value = [
            {'chains_signed': 'true'},
        ]
        result = _check_chains_signing(k8s, 'app')
        assert result['status'] == 'PASS'
        assert 'All 2 components' in result['detail']

    def test_exception(self):
        k8s = MagicMock()
        k8s.list_components.side_effect = RuntimeError('cluster down')
        result = _check_chains_signing(k8s, 'app')
        assert result['status'] == 'SKIP'
        assert 'cluster down' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# _check_nudge_propagation
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckNudgePropagation:

    @patch.dict(os.environ, {'GITHUB_TOKEN': ''})
    def test_no_github_token(self):
        result = _check_nudge_propagation('app')
        assert result['status'] == 'SKIP'
        assert 'No GITHUB_TOKEN' in result['detail']

    @patch.dict(os.environ, {
        'GITHUB_TOKEN': 'ghp_test',
        'GITHUB_OPERATOR_OWNER': 'test-org',
        'GITHUB_OPERATOR_REPO': 'test-repo',
    })
    @patch('clients.github_client.GitHubClient')
    def test_no_nudge_prs(self, mock_gh_cls):
        mock_gh = MagicMock()
        mock_gh.list_pull_requests.return_value = [
            {'title': 'Fix bug'},
            {'title': 'Add feature'},
        ]
        mock_gh_cls.return_value = mock_gh
        result = _check_nudge_propagation('rhoai-v3-5-ea-2')
        assert result['status'] == 'PASS'
        assert 'No open nudge PRs' in result['detail']

    @patch.dict(os.environ, {
        'GITHUB_TOKEN': 'ghp_test',
        'GITHUB_OPERATOR_OWNER': 'test-org',
        'GITHUB_OPERATOR_REPO': 'test-repo',
    })
    @patch('clients.github_client.GitHubClient')
    def test_stale_nudge_prs(self, mock_gh_cls):
        mock_gh = MagicMock()
        old_time = (datetime.utcnow() - timedelta(hours=72)).strftime('%Y-%m-%dT%H:%M:%SZ')
        mock_gh.list_pull_requests.return_value = [
            {'title': 'Nudge update for comp-a', 'created_at': old_time},
        ]
        mock_gh_cls.return_value = mock_gh
        result = _check_nudge_propagation('rhoai-v3-5-ea-2')
        assert result['status'] == 'WARN'
        assert 'nudge PR(s) open >48h' in result['detail']

    @patch.dict(os.environ, {
        'GITHUB_TOKEN': 'ghp_test',
        'GITHUB_OPERATOR_OWNER': 'test-org',
        'GITHUB_OPERATOR_REPO': 'test-repo',
    })
    @patch('clients.github_client.GitHubClient')
    def test_recent_nudge_prs(self, mock_gh_cls):
        mock_gh = MagicMock()
        recent = (datetime.utcnow() - timedelta(hours=12)).strftime('%Y-%m-%dT%H:%M:%SZ')
        mock_gh.list_pull_requests.return_value = [
            {'title': 'Nudge update for comp-a', 'created_at': recent},
        ]
        mock_gh_cls.return_value = mock_gh
        result = _check_nudge_propagation('rhoai-v3-5-ea-2')
        assert result['status'] == 'PASS'
        assert '1 open nudge PR(s), all recent' in result['detail']

    @patch.dict(os.environ, {'GITHUB_TOKEN': 'ghp_test'})
    @patch('clients.github_client.GitHubClient')
    def test_exception(self, mock_gh_cls):
        mock_gh_cls.side_effect = ImportError('no module')
        result = _check_nudge_propagation('rhoai-v3-5-ea-2')
        assert result['status'] == 'SKIP'
        assert 'no module' in result['detail']

    @patch.dict(os.environ, {
        'GITHUB_TOKEN': 'ghp_test',
        'GITHUB_OPERATOR_OWNER': 'test-org',
        'GITHUB_OPERATOR_REPO': 'test-repo',
    })
    @patch('clients.github_client.GitHubClient')
    def test_nudge_with_invalid_date(self, mock_gh_cls):
        mock_gh = MagicMock()
        mock_gh.list_pull_requests.return_value = [
            {'title': 'Nudge update for comp-a', 'created_at': 'invalid-date'},
        ]
        mock_gh_cls.return_value = mock_gh
        result = _check_nudge_propagation('rhoai-v3-5-ea-2')
        assert result['status'] == 'WARN'
        assert 'nudge PR(s) open >48h' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# _check_test_coverage_regression
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckTestCoverageRegression:

    def test_no_kfx(self):
        result = _check_test_coverage_regression(None, None, 'app')
        assert result['status'] == 'SKIP'

    @patch('clients.konflux_client.KonfluxClient')
    def test_no_tests_in_current(self, mock_kfx_cls):
        mock_kfx_cls.extract_snapshot_status.return_value = {'test_results': {}}
        snapshot = {'metadata': {'name': 'snap-1'}}
        kfx = MagicMock()
        result = _check_test_coverage_regression(kfx, snapshot, 'app')
        assert result['status'] == 'SKIP'
        assert 'No tests in current snapshot' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_no_previous_snapshot(self, mock_kfx_cls):
        mock_kfx_cls.extract_snapshot_status.return_value = {
            'test_results': {'test-a': {'status': 'True'}},
        }
        kfx = MagicMock()
        kfx.get_snapshots.return_value = [
            {'metadata': {'name': 'snap-1'}},
        ]
        snapshot = {'metadata': {'name': 'snap-1'}}
        result = _check_test_coverage_regression(kfx, snapshot, 'app')
        assert result['status'] == 'SKIP'
        assert 'No previous snapshot' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_disappeared_tests(self, mock_kfx_cls):
        def _mock_status(snap):
            name = snap.get('metadata', {}).get('name', '')
            if name == 'snap-1':
                return {'test_results': {'test-a': {'status': 'True'}}}
            else:
                return {'test_results': {
                    'test-a': {'status': 'True'},
                    'test-b': {'status': 'True'},
                }}

        mock_kfx_cls.extract_snapshot_status.side_effect = _mock_status
        kfx = MagicMock()
        kfx.get_snapshots.return_value = [
            {'metadata': {'name': 'snap-1'}},
            {'metadata': {'name': 'snap-0'}},
        ]
        snapshot = {'metadata': {'name': 'snap-1'}}
        result = _check_test_coverage_regression(kfx, snapshot, 'app')
        assert result['status'] == 'WARN'
        assert 'test-b' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_no_regression_with_new_tests(self, mock_kfx_cls):
        def _mock_status(snap):
            name = snap.get('metadata', {}).get('name', '')
            if name == 'snap-1':
                return {'test_results': {
                    'test-a': {'status': 'True'},
                    'test-c': {'status': 'True'},
                }}
            else:
                return {'test_results': {
                    'test-a': {'status': 'True'},
                }}

        mock_kfx_cls.extract_snapshot_status.side_effect = _mock_status
        kfx = MagicMock()
        kfx.get_snapshots.return_value = [
            {'metadata': {'name': 'snap-1'}},
            {'metadata': {'name': 'snap-0'}},
        ]
        snapshot = {'metadata': {'name': 'snap-1'}}
        result = _check_test_coverage_regression(kfx, snapshot, 'app')
        assert result['status'] == 'PASS'
        assert '1 new' in result['detail']
        assert 'test-c' in result['detail']

    def test_exception(self):
        kfx = MagicMock()
        snapshot = {'metadata': {'name': 'snap-1'}}
        with patch('clients.konflux_client.KonfluxClient') as mock_cls:
            mock_cls.extract_snapshot_status.side_effect = RuntimeError('bad')
            result = _check_test_coverage_regression(kfx, snapshot, 'app')
        assert result['status'] == 'SKIP'


# ═══════════════════════════════════════════════════════════════════════════
# _check_multiarch_coverage
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckMultiarchCoverage:

    def test_no_snapshot(self):
        result = _check_multiarch_coverage(None)
        assert result['status'] == 'SKIP'
        assert 'No snapshot available' in result['detail']

    def test_no_components(self):
        result = _check_multiarch_coverage({'spec': {'components': []}})
        assert result['status'] == 'SKIP'
        assert 'No components in snapshot' in result['detail']

    @patch('clients.registry_client.RegistryClient')
    def test_missing_arches(self, mock_rc_cls):
        mock_rc = MagicMock()
        mock_rc.get_manifest.return_value = {
            'manifests': [
                {'platform': {'architecture': 'amd64'}},
                {'platform': {'architecture': 'arm64'}},
            ]
        }
        mock_rc_cls.return_value = mock_rc
        snapshot = {'spec': {'components': [
            {'name': 'comp-a', 'containerImage': 'quay.io/repo/img@sha256:abc123'},
        ]}}
        result = _check_multiarch_coverage(snapshot)
        assert result['status'] == 'WARN'
        assert 'comp-a' in result['detail']
        assert 'missing' in result['detail']

    @patch('clients.registry_client.RegistryClient')
    def test_all_arches_present(self, mock_rc_cls):
        mock_rc = MagicMock()
        mock_rc.get_manifest.return_value = {
            'manifests': [
                {'platform': {'architecture': 'amd64'}},
                {'platform': {'architecture': 'arm64'}},
                {'platform': {'architecture': 's390x'}},
                {'platform': {'architecture': 'ppc64le'}},
            ]
        }
        mock_rc_cls.return_value = mock_rc
        snapshot = {'spec': {'components': [
            {'name': 'comp-a', 'containerImage': 'quay.io/repo/img@sha256:abc123'},
        ]}}
        result = _check_multiarch_coverage(snapshot)
        assert result['status'] == 'PASS'
        assert 'All 1 checked' in result['detail']

    @patch('clients.registry_client.RegistryClient')
    def test_no_manifests_checked(self, mock_rc_cls):
        mock_rc = MagicMock()
        mock_rc.get_manifest.return_value = None
        mock_rc_cls.return_value = mock_rc
        snapshot = {'spec': {'components': [
            {'name': 'comp-a', 'containerImage': 'quay.io/repo/img@sha256:abc123'},
        ]}}
        result = _check_multiarch_coverage(snapshot)
        assert result['status'] == 'SKIP'
        assert 'Could not check' in result['detail']

    @patch('clients.registry_client.RegistryClient')
    def test_image_without_digest_skipped(self, mock_rc_cls):
        mock_rc = MagicMock()
        mock_rc_cls.return_value = mock_rc
        snapshot = {'spec': {'components': [
            {'name': 'comp-a', 'containerImage': 'quay.io/repo/img:latest'},
        ]}}
        result = _check_multiarch_coverage(snapshot)
        assert result['status'] == 'SKIP'

    def test_exception(self):
        snapshot = {'spec': {'components': [
            {'name': 'comp-a', 'containerImage': 'quay.io/repo/img@sha256:abc'},
        ]}}
        with patch('clients.registry_client.RegistryClient', side_effect=RuntimeError('fail')):
            result = _check_multiarch_coverage(snapshot)
        assert result['status'] == 'SKIP'


# ═══════════════════════════════════════════════════════════════════════════
# _check_stage_release_health
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckStageReleaseHealth:

    def test_no_kfx(self):
        result = _check_stage_release_health(None, 'app')
        assert result['status'] == 'SKIP'
        assert 'NAMESPACE not set' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_no_releases(self, mock_kfx_cls):
        kfx = MagicMock()
        kfx.get_releases.return_value = []
        result = _check_stage_release_health(kfx, 'app')
        assert result['status'] == 'SKIP'
        assert 'No releases found' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_no_stage_release(self, mock_kfx_cls):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {'spec': {'releasePlan': 'prod-plan'}},
        ]
        result = _check_stage_release_health(kfx, 'app')
        assert result['status'] == 'SKIP'
        assert 'No stage release found' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_post_validation_failed(self, mock_kfx_cls):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {'spec': {'releasePlan': 'my-stage-plan'}},
        ]
        mock_kfx_cls.extract_release_status.return_value = {
            'name': 'rel-123',
            'post_validation_failed': True,
            'status': 'False',
        }
        result = _check_stage_release_health(kfx, 'app')
        assert result['status'] == 'FAIL'
        assert 'Post-validation failed' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_release_not_completed(self, mock_kfx_cls):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {'spec': {'releasePlan': 'my-stage-plan'}},
        ]
        mock_kfx_cls.extract_release_status.return_value = {
            'name': 'rel-123',
            'post_validation_failed': False,
            'status': 'False',
            'message': 'Pipeline still running',
        }
        result = _check_stage_release_health(kfx, 'app')
        assert result['status'] == 'FAIL'
        assert 'not completed' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_release_completed(self, mock_kfx_cls):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {'spec': {'releasePlan': 'my-stage-plan'}},
        ]
        mock_kfx_cls.extract_release_status.return_value = {
            'name': 'rel-123',
            'post_validation_failed': False,
            'status': 'True',
        }
        result = _check_stage_release_health(kfx, 'app')
        assert result['status'] == 'PASS'
        assert 'rel-123 completed successfully' in result['detail']

    def test_exception(self):
        kfx = MagicMock()
        kfx.get_releases.side_effect = RuntimeError('boom')
        result = _check_stage_release_health(kfx, 'app')
        assert result['status'] == 'SKIP'
        assert 'boom' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# _check_prod_rpa_exists
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckProdRPAExists:

    def test_no_rpas(self):
        result = _check_prod_rpa_exists([])
        assert result['status'] == 'SKIP'

    @patch('clients.konflux_client.KonfluxClient')
    def test_no_prod_rpa(self, mock_kfx_cls):
        mock_kfx_cls.extract_rpa_bindings.return_value = [
            {'target': 'stage', 'policy': 'policy-a', 'rpa_name': 'stage-rpa'},
        ]
        result = _check_prod_rpa_exists(['rpa-1'])
        assert result['status'] == 'FAIL'
        assert 'No prod ReleasePlanAdmission' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_prod_rpa_missing_policy(self, mock_kfx_cls):
        mock_kfx_cls.extract_rpa_bindings.return_value = [
            {'target': 'prod', 'policy': '', 'rpa_name': 'prod-rpa'},
        ]
        result = _check_prod_rpa_exists(['rpa-1'])
        assert result['status'] == 'WARN'
        assert 'no EC policy' in result['detail']

    @patch('clients.konflux_client.KonfluxClient')
    def test_prod_rpa_with_policy(self, mock_kfx_cls):
        mock_kfx_cls.extract_rpa_bindings.return_value = [
            {'target': 'prod', 'policy': 'policy-a', 'rpa_name': 'prod-rpa'},
        ]
        result = _check_prod_rpa_exists(['rpa-1'])
        assert result['status'] == 'PASS'
        assert '1 prod RPA(s)' in result['detail']

    def test_exception(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_cls:
            mock_cls.extract_rpa_bindings.side_effect = RuntimeError('x')
            result = _check_prod_rpa_exists(['rpa-1'])
        assert result['status'] == 'SKIP'


# ═══════════════════════════════════════════════════════════════════════════
# _check_snapshot_drift
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckSnapshotDrift:

    def test_no_kfx(self):
        result = _check_snapshot_drift(None, None, 'app')
        assert result['status'] == 'SKIP'
        assert 'NAMESPACE not set' in result['detail']

    def test_no_stage_release(self):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {'spec': {'releasePlan': 'prod-plan'}},
        ]
        snapshot = {'metadata': {'name': 'snap-1'}}
        result = _check_snapshot_drift(kfx, snapshot, 'app')
        assert result['status'] == 'SKIP'

    def test_snapshot_matches(self):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {'spec': {'releasePlan': 'my-stage-plan', 'snapshot': 'snap-1'}},
        ]
        snapshot = {'metadata': {'name': 'snap-1'}}
        result = _check_snapshot_drift(kfx, snapshot, 'app')
        assert result['status'] == 'PASS'
        assert 'matches current' in result['detail']

    def test_snapshot_drifted(self):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {'spec': {'releasePlan': 'my-stage-plan', 'snapshot': 'snap-old'}},
        ]
        snapshot = {'metadata': {'name': 'snap-new'}}
        result = _check_snapshot_drift(kfx, snapshot, 'app')
        assert result['status'] == 'WARN'
        assert 'snap-old' in result['detail']
        assert 'snap-new' in result['detail']

    def test_exception(self):
        kfx = MagicMock()
        kfx.get_releases.side_effect = RuntimeError('x')
        result = _check_snapshot_drift(kfx, None, 'app')
        assert result['status'] == 'SKIP'


# ═══════════════════════════════════════════════════════════════════════════
# _check_release_pipeline_completeness
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckReleasePipelineCompleteness:

    def test_no_kfx(self):
        result = _check_release_pipeline_completeness(None, 'app')
        assert result['status'] == 'SKIP'

    def test_no_stage_release(self):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {'spec': {'releasePlan': 'prod-plan'}},
        ]
        result = _check_release_pipeline_completeness(kfx, 'app')
        assert result['status'] == 'SKIP'
        assert 'No stage release found' in result['detail']

    def test_no_conditions(self):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {
                'spec': {'releasePlan': 'my-stage-plan'},
                'status': {'conditions': []},
            },
        ]
        result = _check_release_pipeline_completeness(kfx, 'app')
        assert result['status'] == 'SKIP'
        assert 'No conditions' in result['detail']

    def test_incomplete_conditions(self):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {
                'spec': {'releasePlan': 'my-stage-plan'},
                'status': {
                    'conditions': [
                        {'type': 'Released', 'status': 'True', 'message': ''},
                        {'type': 'Validated', 'status': 'False', 'message': 'Not yet'},
                    ],
                },
                'metadata': {'name': 'rel-1'},
            },
        ]
        result = _check_release_pipeline_completeness(kfx, 'app')
        assert result['status'] == 'WARN'
        assert '1 incomplete condition(s)' in result['detail']

    def test_all_complete(self):
        kfx = MagicMock()
        kfx.get_releases.return_value = [
            {
                'spec': {'releasePlan': 'my-stage-plan'},
                'status': {
                    'conditions': [
                        {'type': 'Released', 'status': 'True'},
                        {'type': 'Validated', 'status': 'True'},
                    ],
                },
            },
        ]
        result = _check_release_pipeline_completeness(kfx, 'app')
        assert result['status'] == 'PASS'
        assert 'All 2 release conditions' in result['detail']

    def test_exception(self):
        kfx = MagicMock()
        kfx.get_releases.side_effect = RuntimeError('x')
        result = _check_release_pipeline_completeness(kfx, 'app')
        assert result['status'] == 'SKIP'


# ═══════════════════════════════════════════════════════════════════════════
# _check_pcc_post_push
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckPCCPostPush:

    def test_no_pcc_data(self):
        monitor = MagicMock()
        monitor._check_pcc_freshness.return_value = None
        result = _check_pcc_post_push(monitor)
        assert result['status'] == 'SKIP'
        assert 'no GitHub token' in result['detail']

    def test_stale(self):
        monitor = MagicMock()
        monitor._check_pcc_freshness.return_value = {
            'status': 'stale',
            'missing_versions': ['v2.0', 'v2.1'],
        }
        result = _check_pcc_post_push(monitor)
        assert result['status'] == 'FAIL'
        assert 'stale after stage push' in result['detail']
        assert 'v2.0' in result['detail']

    def test_fresh(self):
        monitor = MagicMock()
        monitor._check_pcc_freshness.return_value = {
            'status': 'fresh',
            'cached_versions': 15,
        }
        result = _check_pcc_post_push(monitor)
        assert result['status'] == 'PASS'
        assert '15 versions' in result['detail']

    def test_exception(self):
        monitor = MagicMock()
        monitor._check_pcc_freshness.side_effect = RuntimeError('err')
        result = _check_pcc_post_push(monitor)
        assert result['status'] == 'SKIP'


# ═══════════════════════════════════════════════════════════════════════════
# _check_all_artifacts (slow/full)
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckAllArtifacts:

    @patch.dict(os.environ, {'NAMESPACE': ''})
    def test_no_namespace(self):
        result = _check_all_artifacts('app')
        assert result['status'] == 'SKIP'
        assert 'NAMESPACE not set' in result['detail']

    @patch.dict(os.environ, {'NAMESPACE': 'ns'})
    def test_no_snapshot_no_kfx_snapshots(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_cls:
            mock_kfx = MagicMock()
            mock_kfx.get_snapshots.return_value = []
            mock_cls.return_value = mock_kfx
            result = _check_all_artifacts('app', snapshot=None)
        assert result['status'] == 'SKIP'
        assert 'No snapshot found' in result['detail']

    @patch.dict(os.environ, {'NAMESPACE': 'ns'})
    @patch('clients.registry_client.RegistryClient')
    def test_unhealthy_components(self, mock_rc_cls):
        mock_rc = MagicMock()
        mock_rc.check_artifact_health_batch.return_value = {
            'comp-a': {'healthy': False, 'missing': ['src']},
            'comp-b': {'healthy': True},
        }
        mock_rc_cls.return_value = mock_rc
        snapshot = {'spec': {'components': [
            {'name': 'comp-a', 'containerImage': 'img@sha'},
            {'name': 'comp-b', 'containerImage': 'img2@sha'},
        ]}}
        result = _check_all_artifacts('app', snapshot=snapshot)
        assert result['status'] == 'WARN'
        assert 'comp-a' in result['detail']
        assert result.get('unhealthy')

    @patch.dict(os.environ, {'NAMESPACE': 'ns'})
    @patch('clients.registry_client.RegistryClient')
    def test_all_healthy(self, mock_rc_cls):
        mock_rc = MagicMock()
        mock_rc.check_artifact_health_batch.return_value = {
            'comp-a': {'healthy': True},
        }
        mock_rc_cls.return_value = mock_rc
        snapshot = {'spec': {'components': [
            {'name': 'comp-a', 'containerImage': 'img@sha'},
        ]}}
        result = _check_all_artifacts('app', snapshot=snapshot)
        assert result['status'] == 'PASS'

    @patch.dict(os.environ, {'NAMESPACE': 'ns'})
    def test_exception(self):
        snapshot = {'spec': {'components': [{'name': 'c', 'containerImage': 'i@s'}]}}
        with patch('clients.registry_client.RegistryClient', side_effect=RuntimeError('x')):
            result = _check_all_artifacts('app', snapshot=snapshot)
        assert result['status'] == 'SKIP'


# ═══════════════════════════════════════════════════════════════════════════
# _check_fbc_prune
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckFBCPrune:

    def test_no_k8s(self):
        result = _check_fbc_prune(None, 'app')
        assert result['status'] == 'SKIP'
        assert 'NAMESPACE not set' in result['detail']

    @patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc')
    def test_no_pipelineruns(self, mock_nightly):
        k8s = MagicMock()
        k8s.list_recent_pipelineruns.return_value = []
        result = _check_fbc_prune(k8s, 'app')
        assert result['status'] == 'SKIP'
        assert 'No FBC PipelineRun found' in result['detail']

    @patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc')
    def test_prune_detected(self, mock_nightly):
        k8s = MagicMock()
        k8s.list_recent_pipelineruns.return_value = [{'name': 'pr-1'}]
        mock_get_logs = MagicMock(return_value={
            'logs': 'Step pruned version 1.0\nRemoving old channel alpha',
        })
        import sys
        mock_module = MagicMock()
        mock_module.get_logs_complete = mock_get_logs
        with patch.dict(sys.modules, {'clients.unified': mock_module}):
            result = _check_fbc_prune(k8s, 'app')
        assert result['status'] == 'FAIL'
        assert 'pruned content' in result['detail']

    @patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc')
    def test_no_prune(self, mock_nightly):
        k8s = MagicMock()
        k8s.list_recent_pipelineruns.return_value = [{'name': 'pr-1'}]
        mock_get_logs = MagicMock(return_value={
            'logs': 'Build succeeded\nAll images processed',
        })
        import sys
        mock_module = MagicMock()
        mock_module.get_logs_complete = mock_get_logs
        with patch.dict(sys.modules, {'clients.unified': mock_module}):
            result = _check_fbc_prune(k8s, 'app')
        assert result['status'] == 'PASS'

    @patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc')
    def test_no_logs(self, mock_nightly):
        k8s = MagicMock()
        k8s.list_recent_pipelineruns.return_value = [{'name': 'pr-1'}]
        mock_get_logs = MagicMock(return_value=None)
        import sys
        mock_module = MagicMock()
        mock_module.get_logs_complete = mock_get_logs
        with patch.dict(sys.modules, {'clients.unified': mock_module}):
            result = _check_fbc_prune(k8s, 'app')
        assert result['status'] == 'SKIP'
        assert 'Could not fetch' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# _check_rpm_drift
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckRpmDrift:

    def test_not_implemented(self):
        result = _check_rpm_drift(None)
        assert result['status'] == 'SKIP'
        assert 'Not implemented' in result['detail']
        assert result['phase'] == 'build'

    def test_with_snapshot_still_skip(self):
        snapshot = {'spec': {'components': [
            {'name': 'comp-a', 'containerImage': 'img@sha256:abc'},
        ]}}
        result = _check_rpm_drift(snapshot)
        assert result['status'] == 'SKIP'
        assert 'Not implemented' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# _check_selector_label_changes
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckSelectorLabelChanges:

    @patch('repositories.repository_factory.get_repository')
    def test_no_flagged_components(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_repo.get_resolved_components.return_value = []
        mock_repo.get_working_components.return_value = []
        mock_get_repo.return_value = mock_repo
        result = _check_selector_label_changes('app')
        assert result['status'] == 'PASS'
        assert 'No selector label changes' in result['detail']

    @patch('repositories.repository_factory.get_repository')
    def test_flagged_component(self, mock_get_repo):
        mock_repo = MagicMock()
        mock_repo.get_resolved_components.return_value = [
            {'component': 'comp-a'},
        ]
        mock_repo.get_working_components.return_value = []

        # The function accesses repo.db.connection() as a context manager
        # and then cursor.fetchone() to get the commit_context
        diff_text = '+  matchLabels:\n+    app: new-label\n-  matchLabels:\n-    app: old-label'
        context_dict = {'diff': diff_text}

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (context_dict,)
        mock_conn_cm = MagicMock()
        mock_conn_cm.__enter__ = MagicMock(return_value=mock_conn_cm)
        mock_conn_cm.__exit__ = MagicMock(return_value=None)
        mock_conn_cm.cursor.return_value = mock_cursor
        mock_repo.db.connection.return_value = mock_conn_cm

        mock_get_repo.return_value = mock_repo
        result = _check_selector_label_changes('app')
        assert result['status'] == 'WARN'
        assert 'comp-a' in result['detail']

    @patch('repositories.repository_factory.get_repository')
    def test_exception(self, mock_get_repo):
        mock_get_repo.side_effect = RuntimeError('db down')
        result = _check_selector_label_changes('app')
        assert result['status'] == 'SKIP'


# ═══════════════════════════════════════════════════════════════════════════
# _build_manual_checks
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildManualChecks:

    @patch.dict(os.environ, {'NAMESPACE': 'my-ns'})
    def test_returns_both_phases(self):
        result = _build_manual_checks('test-app')
        assert 'pre_release' in result
        assert 'post_stage' in result
        assert len(result['pre_release']) >= 5
        assert len(result['post_stage']) >= 4

    @patch.dict(os.environ, {'NAMESPACE': 'my-ns'})
    def test_includes_application_in_commands(self):
        result = _build_manual_checks('rhoai-v3-5')
        all_commands = []
        for check in result['pre_release']:
            if 'command' in check:
                all_commands.append(check['command'])
        combined = ' '.join(all_commands)
        assert 'rhoai-v3-5' in combined


# ═══════════════════════════════════════════════════════════════════════════
# _build_release_details
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildReleaseDetails:

    @patch('api.routes.releases._fetch_snapshot_components')
    @patch('api.routes.releases._db')
    def test_basic_release_details(self, mock_db_fn, mock_snap_comps):
        mock_snap_comps.return_value = [
            {'name': 'comp-a', 'containerImage': 'img@sha'},
        ]
        mock_db = MagicMock()
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.connection.return_value = mock_conn
        mock_cursor.fetchone.return_value = None
        mock_db_fn.return_value = mock_db

        rel_json = {
            'metadata': {
                'name': 'managed-abc',
                'creationTimestamp': '2026-07-01T10:00:00Z',
                'labels': {'appstudio.openshift.io/application': 'test-app'},
            },
            'spec': {
                'releasePlan': 'test-components-stage',
                'snapshot': 'snap-1',
                'data': {'releaseNotes': {}},
            },
            'status': {
                'conditions': [
                    {'type': 'Released', 'status': 'True', 'reason': 'Succeeded', 'message': 'ok'},
                ],
                'managedProcessing': {
                    'pipelineRun': 'pr-123',
                    'startTime': '2026-07-01T10:00:00Z',
                    'completionTime': '2026-07-01T10:05:00Z',
                },
            },
        }
        result = _build_release_details(rel_json, 'test-ns')
        assert result['name'] == 'managed-abc'
        assert result['target'] == 'stage'
        assert result['release_type'] == 'components'
        assert result['type_label'] == 'Components'
        assert result['component_count'] == 1
        assert result['duration_seconds'] == 300
        assert result['is_failed'] is False
        assert result['pipeline_ref'] == 'pr-123'
        assert result['pipeline_ui_url'] is not None

    @patch('api.routes.releases._fetch_snapshot_components')
    @patch('api.routes.releases._db')
    def test_failed_release_details(self, mock_db_fn, mock_snap_comps):
        mock_snap_comps.return_value = []
        mock_db = MagicMock()
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.connection.return_value = mock_conn
        mock_cursor.fetchone.return_value = None
        mock_db_fn.return_value = mock_db

        rel_json = {
            'metadata': {
                'name': 'managed-xyz',
                'creationTimestamp': '2026-07-01T10:00:00Z',
                'labels': {},
            },
            'spec': {
                'releasePlan': 'test-components-prod',
                'snapshot': 'snap-2',
                'data': {'releaseNotes': {
                    'type': 'RHBA',
                    'issues': {'fixed': [{'id': 'RHOAI-123'}]},
                    'cves': [{'key': 'CVE-2026-001', 'component': 'openssl'}],
                }},
            },
            'status': {
                'conditions': [
                    {'type': 'Released', 'status': 'False', 'reason': 'Failed',
                     'message': 'Pipeline failed at task verify-conforma'},
                ],
                'managedProcessing': {},
            },
        }
        result = _build_release_details(rel_json, 'test-ns')
        assert result['is_failed'] is True
        assert result['failed_task'] == 'verify-conforma'
        assert result['target'] == 'prod'
        assert result['advisory_type'] == 'RHBA'
        assert 'RHOAI-123' in result['fixed_issues']
        assert result['cves'][0]['key'] == 'CVE-2026-001'

    @patch('api.routes.releases._fetch_snapshot_components')
    @patch('api.routes.releases._db')
    def test_progressing_release(self, mock_db_fn, mock_snap_comps):
        mock_snap_comps.return_value = []
        mock_db = MagicMock()
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.connection.return_value = mock_conn
        mock_cursor.fetchone.return_value = None
        mock_db_fn.return_value = mock_db

        rel_json = {
            'metadata': {
                'name': 'managed-prog',
                'creationTimestamp': '2026-07-01T10:00:00Z',
                'labels': {},
            },
            'spec': {
                'releasePlan': 'unknown-plan',
                'snapshot': 'snap-3',
                'data': {'releaseNotes': {}},
            },
            'status': {
                'conditions': [
                    {'type': 'Released', 'status': 'False', 'reason': 'Progressing',
                     'message': 'Pipeline running'},
                ],
                'managedProcessing': {},
            },
        }
        result = _build_release_details(rel_json, 'test-ns')
        assert result['is_progressing'] is True
        assert result['is_failed'] is False

    @patch('api.routes.releases._fetch_snapshot_components')
    @patch('api.routes.releases._db')
    def test_with_ai_analysis(self, mock_db_fn, mock_snap_comps):
        mock_snap_comps.return_value = []
        mock_db = MagicMock()
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.connection.return_value = mock_conn
        mock_cursor.fetchone.return_value = ({'summary': 'analysis data'},)
        mock_db_fn.return_value = mock_db

        rel_json = {
            'metadata': {'name': 'rel-1', 'creationTimestamp': '', 'labels': {}},
            'spec': {'releasePlan': 'p', 'snapshot': 's', 'data': {'releaseNotes': {}}},
            'status': {'conditions': [], 'managedProcessing': {}},
        }
        result = _build_release_details(rel_json, 'test-ns')
        assert result['ai_analysis'] is not None

    @patch('api.routes.releases._fetch_snapshot_components')
    @patch('api.routes.releases._db')
    def test_no_duration_without_times(self, mock_db_fn, mock_snap_comps):
        mock_snap_comps.return_value = []
        mock_db = MagicMock()
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.connection.return_value = mock_conn
        mock_cursor.fetchone.return_value = None
        mock_db_fn.return_value = mock_db

        rel_json = {
            'metadata': {'name': 'r', 'creationTimestamp': '', 'labels': {}},
            'spec': {'releasePlan': 'p', 'snapshot': 's', 'data': {'releaseNotes': {}}},
            'status': {'conditions': [], 'managedProcessing': {}},
        }
        result = _build_release_details(rel_json, 'ns')
        assert result['duration_seconds'] is None


# ═══════════════════════════════════════════════════════════════════════════
# describe_release endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestDescribeReleaseEndpoint:

    @patch.dict(os.environ, {'NAMESPACE': ''})
    def test_no_namespace(self, client):
        resp = client.get('/api/v1/applications/test-app/releases/managed-abc')
        assert resp.status_code == 500

    @patch('api.routes.releases._fetch_release_cr')
    @patch('api.routes.releases._build_release_details')
    @patch.dict(os.environ, {'NAMESPACE': 'test-ns'})
    def test_success(self, mock_build, mock_fetch, client):
        mock_fetch.return_value = {'metadata': {'name': 'managed-abc'}}
        mock_build.return_value = {'name': 'managed-abc', 'target': 'stage'}
        resp = client.get('/api/v1/applications/test-app/releases/managed-abc')
        assert resp.status_code == 200
        assert resp.json()['name'] == 'managed-abc'

    @patch('api.routes.releases._fetch_release_cr')
    @patch.dict(os.environ, {'NAMESPACE': 'test-ns'})
    def test_not_found(self, mock_fetch, client):
        mock_fetch.side_effect = Exception('404 Not Found')
        resp = client.get('/api/v1/applications/test-app/releases/managed-abc')
        assert resp.status_code == 404

    @patch('api.routes.releases._fetch_release_cr')
    @patch.dict(os.environ, {'NAMESPACE': 'test-ns'})
    def test_k8s_error(self, mock_fetch, client):
        mock_fetch.side_effect = Exception('Connection refused')
        resp = client.get('/api/v1/applications/test-app/releases/managed-abc')
        assert resp.status_code == 502

    def test_invalid_app_name(self, client):
        resp = client.get('/api/v1/applications/INVALID_APP/releases/managed-abc')
        assert resp.status_code == 422

    def test_invalid_release_name(self, client):
        resp = client.get('/api/v1/applications/test-app/releases/INVALID_RELEASE!')
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# get_pp_schedule endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestGetPPScheduleEndpoint:

    @patch('subprocess.run')
    @patch.dict(os.environ, {'PRODUCT_PAGES_PROD_TOKEN': 'test-token'})
    def test_success(self, mock_run, client):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"name": "GA", "date": "2026-08-01"}]',
        )
        resp = client.get('/api/v1/product-pages/schedule/12345')
        assert resp.status_code == 200
        assert resp.json()[0]['name'] == 'GA'

    @patch('subprocess.run')
    @patch.dict(os.environ, {'PRODUCT_PAGES_PROD_TOKEN': 'test-token'})
    def test_curl_failure(self, mock_run, client):
        mock_run.return_value = MagicMock(returncode=1, stdout='')
        resp = client.get('/api/v1/product-pages/schedule/12345')
        assert resp.status_code == 502

    @patch('subprocess.run')
    def test_exception(self, mock_run, client):
        mock_run.side_effect = Exception('timeout')
        resp = client.get('/api/v1/product-pages/schedule/12345')
        assert resp.status_code == 502

    @patch('subprocess.run')
    @patch.dict(os.environ, {'PRODUCT_PAGES_PROD_TOKEN': 'test-token'})
    def test_empty_response(self, mock_run, client):
        mock_run.return_value = MagicMock(returncode=0, stdout='   ')
        resp = client.get('/api/v1/product-pages/schedule/12345')
        assert resp.status_code == 502


# ═══════════════════════════════════════════════════════════════════════════
# Readiness verdict integration (AT_RISK, FAIL checks, freeze)
# ═══════════════════════════════════════════════════════════════════════════


class TestReadinessVerdicts:

    @patch('api.routes.releases._build_manual_checks', return_value={})
    @patch('api.routes.releases._run_readiness_checks')
    @patch('api.routes.releases.get_schedule', return_value=None)
    @patch('api.routes.releases.get_active_freeze', return_value=None)
    @patch('api.routes.releases.get_repository')
    def test_at_risk_with_build_failures(self, mock_get_repo, mock_freeze,
                                          mock_sched, mock_checks,
                                          mock_manual, client):
        mock_checks.return_value = []
        mock_build_repo = MagicMock()
        mock_conforma_repo = MagicMock()
        mock_build_repo.find_failing_component_names.return_value = {'comp-a'}
        mock_conforma_repo.find_unresolved_component_names.return_value = []

        def repo_factory(cls):
            if cls.__name__ == 'BuildFailureRepository':
                return mock_build_repo
            return mock_conforma_repo

        mock_get_repo.side_effect = repo_factory
        resp = client.get('/api/v1/applications/test-app/readiness')
        assert resp.status_code == 200
        data = resp.json()
        assert data['verdict'] == 'AT_RISK'
        assert data['build_failures'] == 1

    @patch('api.routes.releases._build_manual_checks', return_value={})
    @patch('api.routes.releases._run_readiness_checks')
    @patch('api.routes.releases.get_schedule', return_value=None)
    @patch('api.routes.releases.get_active_freeze')
    @patch('api.routes.releases.get_repository')
    def test_not_ready_with_freeze(self, mock_get_repo, mock_freeze,
                                    mock_sched, mock_checks,
                                    mock_manual, client):
        mock_checks.return_value = []
        mock_freeze.return_value = {
            'end_date': '2026-08-01',
            'reason': 'code freeze',
        }
        mock_build_repo = MagicMock()
        mock_conforma_repo = MagicMock()
        mock_build_repo.find_failing_component_names.return_value = set()
        mock_conforma_repo.find_unresolved_component_names.return_value = []

        def repo_factory(cls):
            if cls.__name__ == 'BuildFailureRepository':
                return mock_build_repo
            return mock_conforma_repo

        mock_get_repo.side_effect = repo_factory
        resp = client.get('/api/v1/applications/test-app/readiness')
        data = resp.json()
        assert data['verdict'] == 'NOT_READY'
        assert 'frozen' in data['blockers'][0].lower() or 'Pipeline frozen' in data['blockers'][0]

    @patch('api.routes.releases._build_manual_checks', return_value={})
    @patch('api.routes.releases._run_readiness_checks')
    @patch('api.routes.releases.get_schedule', return_value=None)
    @patch('api.routes.releases.get_active_freeze', return_value=None)
    @patch('api.routes.releases.get_repository')
    def test_not_ready_with_failing_checks(self, mock_get_repo, mock_freeze,
                                            mock_sched, mock_checks,
                                            mock_manual, client):
        mock_checks.return_value = [
            {'name': 'FBC', 'status': 'FAIL', 'detail': 'build failed'},
        ]
        mock_build_repo = MagicMock()
        mock_conforma_repo = MagicMock()
        mock_build_repo.find_failing_component_names.return_value = set()
        mock_conforma_repo.find_unresolved_component_names.return_value = []

        def repo_factory(cls):
            if cls.__name__ == 'BuildFailureRepository':
                return mock_build_repo
            return mock_conforma_repo

        mock_get_repo.side_effect = repo_factory
        resp = client.get('/api/v1/applications/test-app/readiness')
        data = resp.json()
        assert data['verdict'] == 'NOT_READY'
        assert any('FBC' in b for b in data['blockers'])

    @patch('api.routes.releases._build_manual_checks', return_value={})
    @patch('api.routes.releases._run_readiness_checks')
    @patch('api.routes.releases.get_schedule', return_value=None)
    @patch('api.routes.releases.get_active_freeze', return_value=None)
    @patch('api.routes.releases.get_repository')
    def test_at_risk_with_warn_checks(self, mock_get_repo, mock_freeze,
                                       mock_sched, mock_checks,
                                       mock_manual, client):
        mock_checks.return_value = [
            {'name': 'Stale components', 'status': 'WARN', 'detail': '2 stale'},
        ]
        mock_build_repo = MagicMock()
        mock_conforma_repo = MagicMock()
        mock_build_repo.find_failing_component_names.return_value = set()
        mock_conforma_repo.find_unresolved_component_names.return_value = []

        def repo_factory(cls):
            if cls.__name__ == 'BuildFailureRepository':
                return mock_build_repo
            return mock_conforma_repo

        mock_get_repo.side_effect = repo_factory
        resp = client.get('/api/v1/applications/test-app/readiness')
        data = resp.json()
        assert data['verdict'] == 'AT_RISK'
        assert any('Stale' in r for r in data['risks'])


# ═══════════════════════════════════════════════════════════════════════════
# _run_readiness_checks (parallel executor)
# ═══════════════════════════════════════════════════════════════════════════


class TestRunReadinessChecks:

    @patch.dict(os.environ, {'NAMESPACE': '', 'RELENG_NAMESPACE': ''})
    @patch('api.routes.releases.CollectorConfig')
    @patch('api.routes.releases.DatabaseConnection')
    def test_no_namespace_skips_k8s(self, mock_db_conn, mock_cfg):
        from api.routes.releases import _run_readiness_checks

        mock_cfg.from_env.return_value = MagicMock()
        mock_db_conn.return_value = MagicMock()

        with patch('proactive.health_monitor.HealthMonitor') as mock_hm_cls:
            mock_hm = MagicMock()
            mock_hm_cls.return_value = mock_hm
            mock_hm.get_nightly_status.return_value = {}
            mock_hm._check_pcc_freshness.return_value = None
            mock_hm.get_stale_components.return_value = {'stale_count': 0, 'checked': 0}
            mock_hm.check_snapshot_freshness.return_value = {'stale_count': 0, 'fresh_count': 0}

            checks = _run_readiness_checks('test-app', full=False)

        assert isinstance(checks, list)
        assert len(checks) >= 16  # At least 16 standard checks + 5 post-stage


# ═══════════════════════════════════════════════════════════════════════════
# _fetch_release_cr and _fetch_snapshot_components
# ═══════════════════════════════════════════════════════════════════════════


class TestFetchHelpers:

    @patch('openshift_auth._ensure_k8s_config')
    @patch('kubernetes.client')
    def test_fetch_release_cr(self, mock_k8s, mock_ensure):
        from api.routes.releases import _fetch_release_cr
        mock_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = mock_api
        mock_api.get_namespaced_custom_object.return_value = {'metadata': {'name': 'rel-1'}}

        result = _fetch_release_cr('rel-1', 'test-ns')
        assert result['metadata']['name'] == 'rel-1'
        mock_api.get_namespaced_custom_object.assert_called_once_with(
            group='appstudio.redhat.com',
            version='v1alpha1',
            namespace='test-ns',
            plural='releases',
            name='rel-1',
            _request_timeout=15,
        )

    @patch('openshift_auth._ensure_k8s_config')
    @patch('kubernetes.client')
    def test_fetch_snapshot_components_success(self, mock_k8s, mock_ensure):
        from api.routes.releases import _fetch_snapshot_components
        mock_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = mock_api
        mock_api.get_namespaced_custom_object.return_value = {
            'spec': {'components': [{'name': 'comp-a'}]},
        }
        result = _fetch_snapshot_components('snap-1', 'ns')
        assert len(result) == 1
        assert result[0]['name'] == 'comp-a'

    @patch('openshift_auth._ensure_k8s_config')
    @patch('kubernetes.client')
    def test_fetch_snapshot_components_error(self, mock_k8s, mock_ensure):
        from api.routes.releases import _fetch_snapshot_components
        mock_k8s.CustomObjectsApi.side_effect = RuntimeError('fail')
        result = _fetch_snapshot_components('snap-1', 'ns')
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════
# Additional _derive_branch edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestDeriveBranchExtended:

    def test_ea_version(self):
        assert _derive_branch('rhoai-v3-5-ea-2') == 'rhoai-3.5-ea.2'

    def test_ga_version(self):
        assert _derive_branch('rhoai-v3-5') == 'rhoai-3.5'

    def test_different_prefix(self):
        assert _derive_branch('odh-v2-10-ea-1') == 'odh-2.10-ea.1'

    def test_non_matching(self):
        assert _derive_branch('some-random-app') == 'some-random-app'


# ═══════════════════════════════════════════════════════════════════════════
# Additional _release_classify edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestReleaseClassifyExtended:

    def test_addon_fbc_stage(self):
        assert _release_classify('rhoai-addon-foo-fbc-stage') == ('stage', 'fbc-addon')

    def test_addon_fbc_prod(self):
        assert _release_classify('rhoai-addon-bar-fbc-prod') == ('prod', 'fbc-addon')

    def test_ocp_fbc_stage_422(self):
        target, rtype = _release_classify('rhoai-ocp-422-fbc-stage')
        assert target == 'stage'
        assert rtype == 'fbc-422'

    def test_ocp_fbc_prod_416(self):
        target, rtype = _release_classify('rhoai-ocp-416-fbc-prod')
        assert target == 'prod'
        assert rtype == 'fbc-416'

    def test_completely_unknown(self):
        assert _release_classify('random-plan') == ('unknown', 'unknown')


# ═══════════════════════════════════════════════════════════════════════════
# Additional _release_type_label edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestReleaseTypeLabelExtended:

    def test_fbc_ocp_version(self):
        assert _release_type_label('fbc-422') == 'FBC OCP 4.22'

    def test_fbc_ocp_416(self):
        assert _release_type_label('fbc-416') == 'FBC OCP 4.16'

    def test_unknown_passthrough(self):
        assert _release_type_label('custom-type') == 'custom-type'


# ═══════════════════════════════════════════════════════════════════════════
# Freeze endpoints (additional edge cases)
# ═══════════════════════════════════════════════════════════════════════════


class TestFreezeEndpointsExtended:

    @patch('api.routes.releases._db')
    def test_list_freezes_status_active(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        today = date.today()
        mock_cursor.fetchall.return_value = [
            (1, today - timedelta(days=1), today + timedelta(days=1), 'Active freeze'),
        ]
        resp = client.get('/api/v1/freezes')
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]['status'] == 'active'

    @patch('api.routes.releases._db')
    def test_list_freezes_status_past(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        today = date.today()
        mock_cursor.fetchall.return_value = [
            (2, today - timedelta(days=10), today - timedelta(days=5), 'Old freeze'),
        ]
        resp = client.get('/api/v1/freezes')
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]['status'] == 'past'

    @patch('api.routes.releases._db')
    def test_list_freezes_status_upcoming(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        today = date.today()
        mock_cursor.fetchall.return_value = [
            (3, today + timedelta(days=5), today + timedelta(days=10), 'Future freeze'),
        ]
        resp = client.get('/api/v1/freezes')
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]['status'] == 'upcoming'

    def test_add_freeze_missing_reason(self, client):
        resp = client.post('/api/v1/freezes', json={
            'start_date': '2026-07-01',
            'end_date': '2026-07-10',
        })
        assert resp.status_code == 422

    def test_add_freeze_empty_reason(self, client):
        resp = client.post('/api/v1/freezes', json={
            'start_date': '2026-07-01',
            'end_date': '2026-07-10',
            'reason': '',
        })
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# Schedule endpoint edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestScheduleEndpointExtended:

    @patch('api.routes.releases._db')
    def test_schedule_with_dates_and_days(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn
        future = date.today() + timedelta(days=30)
        mock_cursor.fetchone.return_value = (
            future,   # planning_freeze
            future,   # feature_freeze
            None,     # code_freeze
            None,     # initial_rc
            None,     # release_window_start
            future,   # release_date
            'v3.6',   # next_release
            datetime(2026, 7, 1),  # updated_at
        )
        resp = client.get('/api/v1/applications/test-app/schedule')
        assert resp.status_code == 200
        data = resp.json()
        assert data['application'] == 'test-app'
        assert 'planning_freeze_days' in data
        assert data['next_release'] == 'v3.6'
        assert data['code_freeze'] is None


# ═══════════════════════════════════════════════════════════════════════════
# Sync schedule endpoint edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestSyncScheduleExtended:

    @patch('api.routes.releases._db')
    def test_sync_multiple_entries(self, mock_db, client):
        mock_conn, mock_cursor = _mock_db_connection()
        mock_db.return_value.connection.return_value = mock_conn

        entries = [
            {'application': 'app-a', 'planning_freeze': '2026-08-01'},
            {'application': 'app-b', 'release_date': '2026-09-01'},
        ]
        resp = client.post('/api/v1/releases/schedule/sync', json=entries)
        assert resp.status_code == 200
        data = resp.json()
        assert data['count'] == 2
        assert 'app-a' in data['synced']
        assert 'app-b' in data['synced']


# ═══════════════════════════════════════════════════════════════════════════
# _check_fbc_health edge cases (exception path)
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckFBCHealthExtended:

    def test_exception_in_monitor(self):
        monitor = MagicMock()
        monitor.get_nightly_status.side_effect = RuntimeError('cluster error')
        result = _check_fbc_health(monitor, 'app', k8s_reachable=True)
        assert result['status'] == 'SKIP'
        assert 'cluster error' in result['detail']


# ═══════════════════════════════════════════════════════════════════════════
# _check_pcc edge cases (inconclusive)
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckPCCExtended:

    def test_inconclusive_status(self):
        monitor = MagicMock()
        monitor._check_pcc_freshness.return_value = {
            'status': 'unknown',
        }
        result = _check_pcc(monitor)
        assert result['status'] == 'SKIP'
        assert 'inconclusive' in result['detail']

    def test_exception(self):
        monitor = MagicMock()
        monitor._check_pcc_freshness.side_effect = RuntimeError('api down')
        result = _check_pcc(monitor)
        assert result['status'] == 'SKIP'
        assert 'api down' in result['detail']
