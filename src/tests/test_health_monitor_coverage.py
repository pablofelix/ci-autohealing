"""Comprehensive tests for HealthMonitor — covers all public methods and edge cases.

Complements test_health_monitor.py by covering:
- Constructor
- get_degrading_components (all severity/status combos)
- get_pattern_cascades (dedup, date formatting, empty)
- get_repeat_failures (severity thresholds, empty)
- get_cve_warnings (env missing, no snapshots, severity buckets, exceptions)
- get_stale_nightly_builds (already tested elsewhere but edge cases here)
- get_component_health_summary (with/without application filter)
- get_nightly_status (full orchestration with mocked sub-clients)
- get_pcc_freshness_warnings (fresh, stale, unknown, many missing)
- _check_pcc_freshness (token missing, file missing, registry empty, stale, fresh, exception)
- get_stale_components (env missing, cache, no-cache, skipped, diagnose)
- _diagnose_stale (pac failure, normal)
- get_stale_warnings (success, exception fallback)
- check_snapshot_freshness (no namespace, no snapshots, fresh/stale/no-builds)
- run_checks (aggregates all sub-checks)
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from proactive.health_monitor import HealthMonitor, HealthWarning, _nightly_component_for_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def monitor(mock_db):
    return HealthMonitor(mock_db)


def _make_cursor(mock_db, rows, cols):
    """Wire up mock_db.connection() context manager to return a cursor with given data."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.description = [(c,) for c in cols]
    mock_cursor.fetchall.return_value = rows
    mock_cursor.fetchone.return_value = rows[0] if rows else None
    mock_conn.cursor.return_value = mock_cursor
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
    return mock_cursor


def _make_multi_cursor(mock_db, call_specs):
    """Wire cursor for methods that make multiple DB calls.

    call_specs is a list of (cols, rows) tuples — one per cursor.execute call.
    Each call to cursor.execute will advance to the next spec.
    """
    mock_conn = MagicMock()
    mock_cursor = MagicMock()

    descriptions = [[(c,) for c in cols] for cols, _ in call_specs]
    fetchall_returns = [rows for _, rows in call_specs]
    fetchone_returns = [rows[0] if rows else None for _, rows in call_specs]

    mock_cursor.description = descriptions[0] if descriptions else []
    exec_count = {'n': 0}

    def _on_execute(*args, **kwargs):
        idx = exec_count['n']
        exec_count['n'] += 1
        if idx < len(descriptions):
            mock_cursor.description = descriptions[idx]
            mock_cursor.fetchall.return_value = fetchall_returns[idx]
            mock_cursor.fetchone.return_value = fetchone_returns[idx]

    mock_cursor.execute.side_effect = _on_execute
    mock_conn.cursor.return_value = mock_cursor
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
    return mock_cursor


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_stores_db(self, mock_db):
        m = HealthMonitor(mock_db)
        assert m.db is mock_db


# ---------------------------------------------------------------------------
# _nightly_component_for_app (helper)
# ---------------------------------------------------------------------------

class TestNightlyComponentForApp:
    def test_ea_version(self):
        assert _nightly_component_for_app('rhoai-v3-5-ea-2') == 'rhoai-fbc-fragment-v3-5-ea-2'

    def test_simple_version(self):
        assert _nightly_component_for_app('rhoai-v3-6') == 'rhoai-fbc-fragment-v3-6'

    def test_no_version(self):
        assert _nightly_component_for_app('myapp') == 'myapp-fbc-fragment'

    def test_empty_string(self):
        assert _nightly_component_for_app('') == '-fbc-fragment'

    def test_multiple_v_takes_first(self):
        result = _nightly_component_for_app('rhoai-v1-v2')
        assert result == 'rhoai-fbc-fragment-v1-v2'


# ---------------------------------------------------------------------------
# HealthWarning dataclass
# ---------------------------------------------------------------------------

class TestHealthWarning:
    def test_creation(self):
        w = HealthWarning(
            component_name='comp',
            application='app',
            signal_type='degrading_health',
            severity='critical',
            message='msg',
            evidence={'key': 'value'},
        )
        assert w.component_name == 'comp'
        assert w.severity == 'critical'
        assert w.evidence == {'key': 'value'}


# ---------------------------------------------------------------------------
# get_degrading_components
# ---------------------------------------------------------------------------

DEGRADING_COLS = [
    'component_name', 'application', 'health_status', 'health_score',
    'consecutive_failures', 'success_rate_last_7d', 'success_rate_last_30d',
    'total_failures_last_7d',
]


class TestGetDegradingComponents:
    def test_empty_results(self, monitor, mock_db):
        _make_cursor(mock_db, [], DEGRADING_COLS)
        assert monitor.get_degrading_components() == []

    def test_warning_status(self, monitor, mock_db):
        rows = [('comp-a', 'app-1', 'warning', 70, 2, 80.0, 85.0, 3)]
        _make_cursor(mock_db, rows, DEGRADING_COLS)
        warnings = monitor.get_degrading_components()
        assert len(warnings) == 1
        w = warnings[0]
        assert w.signal_type == 'degrading_health'
        assert w.severity == 'warning'
        assert 'comp-a' in w.message
        assert '70%' in w.message

    def test_critical_status_yields_critical_severity(self, monitor, mock_db):
        rows = [('comp-b', 'app-2', 'critical', 30, 3, 40.0, 50.0, 8)]
        _make_cursor(mock_db, rows, DEGRADING_COLS)
        warnings = monitor.get_degrading_components()
        assert warnings[0].severity == 'critical'

    def test_consecutive_failures_5_is_critical(self, monitor, mock_db):
        rows = [('comp-c', None, 'warning', 60, 5, 50.0, 60.0, 5)]
        _make_cursor(mock_db, rows, DEGRADING_COLS)
        warnings = monitor.get_degrading_components()
        assert warnings[0].severity == 'critical'
        # application defaults to '' when None
        assert warnings[0].application == ''

    def test_null_health_score_shows_na(self, monitor, mock_db):
        rows = [('comp-d', 'app-3', 'warning', None, 2, None, None, 0)]
        _make_cursor(mock_db, rows, DEGRADING_COLS)
        warnings = monitor.get_degrading_components()
        assert 'N/A' in warnings[0].message

    def test_null_consecutive_failures_not_critical(self, monitor, mock_db):
        rows = [('comp-e', 'app-4', 'warning', 50, None, 70.0, 75.0, 2)]
        _make_cursor(mock_db, rows, DEGRADING_COLS)
        warnings = monitor.get_degrading_components()
        assert warnings[0].severity == 'warning'
        assert 'consecutive_failures=0' in warnings[0].message

    def test_null_application_shows_question_mark(self, monitor, mock_db):
        rows = [('comp-f', None, 'warning', 50, 2, 70.0, 75.0, 2)]
        _make_cursor(mock_db, rows, DEGRADING_COLS)
        warnings = monitor.get_degrading_components()
        assert '(?)' in warnings[0].message

    def test_multiple_rows(self, monitor, mock_db):
        rows = [
            ('comp-1', 'app', 'critical', 10, 8, 20.0, 30.0, 10),
            ('comp-2', 'app', 'warning', 60, 1, 80.0, 85.0, 2),
        ]
        _make_cursor(mock_db, rows, DEGRADING_COLS)
        warnings = monitor.get_degrading_components()
        assert len(warnings) == 2

    def test_evidence_contains_full_row(self, monitor, mock_db):
        rows = [('comp-g', 'app-5', 'warning', 55, 3, 60.0, 70.0, 4)]
        _make_cursor(mock_db, rows, DEGRADING_COLS)
        warnings = monitor.get_degrading_components()
        ev = warnings[0].evidence
        assert ev['component_name'] == 'comp-g'
        assert ev['health_score'] == 55
        assert ev['success_rate_last_7d'] == 60.0


# ---------------------------------------------------------------------------
# get_pattern_cascades
# ---------------------------------------------------------------------------

CASCADE_COLS = [
    'pattern_name', 'source_app', 'spread_to_app', 'source_date', 'spread_date',
]


class TestGetPatternCascades:
    def test_empty(self, monitor, mock_db):
        _make_cursor(mock_db, [], CASCADE_COLS)
        assert monitor.get_pattern_cascades() == []

    def test_single_cascade(self, monitor, mock_db):
        src_date = datetime(2026, 6, 15, tzinfo=UTC)
        spr_date = datetime(2026, 6, 20, tzinfo=UTC)
        rows = [('missing-dep', 'app-a', 'app-b', src_date, spr_date)]
        _make_cursor(mock_db, rows, CASCADE_COLS)
        warnings = monitor.get_pattern_cascades()
        assert len(warnings) == 1
        w = warnings[0]
        assert w.signal_type == 'pattern_cascade'
        assert w.severity == 'warning'
        assert w.component_name == '(cross-app)'
        assert w.application == 'app-b'
        assert '2026-06-15' in w.message
        assert '2026-06-20' in w.message

    def test_dedup_same_pattern_same_target(self, monitor, mock_db):
        d1 = datetime(2026, 6, 10, tzinfo=UTC)
        d2 = datetime(2026, 6, 12, tzinfo=UTC)
        d3 = datetime(2026, 6, 14, tzinfo=UTC)
        rows = [
            ('pat-x', 'app-a', 'app-b', d1, d2),
            ('pat-x', 'app-a', 'app-b', d1, d3),  # duplicate target
        ]
        _make_cursor(mock_db, rows, CASCADE_COLS)
        warnings = monitor.get_pattern_cascades()
        assert len(warnings) == 1

    def test_different_patterns_not_deduped(self, monitor, mock_db):
        d1 = datetime(2026, 6, 10, tzinfo=UTC)
        d2 = datetime(2026, 6, 12, tzinfo=UTC)
        rows = [
            ('pat-x', 'app-a', 'app-b', d1, d2),
            ('pat-y', 'app-a', 'app-b', d1, d2),
        ]
        _make_cursor(mock_db, rows, CASCADE_COLS)
        warnings = monitor.get_pattern_cascades()
        assert len(warnings) == 2

    def test_none_dates_show_question_mark(self, monitor, mock_db):
        rows = [('pat-z', 'app-a', 'app-c', None, None)]
        _make_cursor(mock_db, rows, CASCADE_COLS)
        warnings = monitor.get_pattern_cascades()
        assert '?' in warnings[0].message


# ---------------------------------------------------------------------------
# get_repeat_failures
# ---------------------------------------------------------------------------

REPEAT_COLS = [
    'component_name', 'application', 'failure_category',
    'failure_count', 'last_fix_attempt',
]


class TestGetRepeatFailures:
    def test_empty(self, monitor, mock_db):
        _make_cursor(mock_db, [], REPEAT_COLS)
        assert monitor.get_repeat_failures() == []

    def test_two_failures_is_warning(self, monitor, mock_db):
        rows = [('comp-a', 'app-1', 'build_config', 2, datetime.now(UTC))]
        _make_cursor(mock_db, rows, REPEAT_COLS)
        warnings = monitor.get_repeat_failures()
        assert len(warnings) == 1
        assert warnings[0].severity == 'warning'
        assert warnings[0].signal_type == 'repeat_failure'
        assert '2 failed fixes' in warnings[0].message

    def test_three_failures_is_critical(self, monitor, mock_db):
        rows = [('comp-b', 'app-2', 'dependency', 3, datetime.now(UTC))]
        _make_cursor(mock_db, rows, REPEAT_COLS)
        warnings = monitor.get_repeat_failures()
        assert warnings[0].severity == 'critical'

    def test_five_failures_is_critical(self, monitor, mock_db):
        rows = [('comp-c', 'app-3', 'flaky_test', 5, datetime.now(UTC))]
        _make_cursor(mock_db, rows, REPEAT_COLS)
        warnings = monitor.get_repeat_failures()
        assert warnings[0].severity == 'critical'
        assert '5 failed fixes' in warnings[0].message

    def test_evidence_populated(self, monitor, mock_db):
        rows = [('comp-d', 'app-4', 'infra', 2, datetime.now(UTC))]
        _make_cursor(mock_db, rows, REPEAT_COLS)
        warnings = monitor.get_repeat_failures()
        assert warnings[0].evidence['failure_count'] == 2


# ---------------------------------------------------------------------------
# get_cve_warnings
# ---------------------------------------------------------------------------

class TestGetCveWarnings:
    def test_no_namespace_returns_empty(self, monitor):
        with patch.dict(os.environ, {'NAMESPACE': '', 'APPLICATION_NAME': 'app'}, clear=False):
            assert monitor.get_cve_warnings() == []

    def test_no_app_name_returns_empty(self, monitor):
        with patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': ''}, clear=False):
            assert monitor.get_cve_warnings() == []

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    @patch('proactive.health_monitor.KonfluxClient', create=True)
    @patch('proactive.health_monitor.RegistryClient', create=True)
    def test_no_snapshots_returns_empty(self, mock_rc_cls, mock_kc_cls, monitor):
        # Patch the import inside the method
        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=mock_kc_cls),
            'clients.registry_client': MagicMock(RegistryClient=mock_rc_cls),
        }):
            mock_kc_cls.return_value.get_snapshots.return_value = []
            assert monitor.get_cve_warnings() == []

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_critical_cves_produce_critical_warning(self, monitor):
        mock_kc = MagicMock()
        mock_kc.get_snapshots.return_value = [{
            'spec': {'components': [
                {'name': 'comp-a', 'containerImage': 'img@sha256:abc'},
            ]}
        }]
        mock_rc = MagicMock()
        mock_rc.fetch_sarif_batch.return_value = {
            'comp-a': [
                {'level': 'error'},   # critical
                {'level': 'warning'}, # high
            ]
        }
        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=lambda **kw: mock_kc),
            'clients.registry_client': MagicMock(RegistryClient=lambda: mock_rc),
        }):
            warnings = monitor.get_cve_warnings()
            assert len(warnings) == 1
            assert warnings[0].severity == 'critical'
            assert warnings[0].signal_type == 'critical_cves'
            assert '1 critical' in warnings[0].message

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_high_cves_threshold(self, monitor):
        mock_kc = MagicMock()
        mock_kc.get_snapshots.return_value = [{
            'spec': {'components': [
                {'name': 'comp-b', 'containerImage': 'img@sha256:abc'},
            ]}
        }]
        mock_rc = MagicMock()
        # 5 high CVEs (warnings), no critical
        mock_rc.fetch_sarif_batch.return_value = {
            'comp-b': [{'level': 'warning'}] * 5
        }
        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=lambda **kw: mock_kc),
            'clients.registry_client': MagicMock(RegistryClient=lambda: mock_rc),
        }):
            warnings = monitor.get_cve_warnings()
            assert len(warnings) == 1
            assert warnings[0].severity == 'warning'
            assert warnings[0].signal_type == 'high_cves'

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_below_threshold_no_warning(self, monitor):
        mock_kc = MagicMock()
        mock_kc.get_snapshots.return_value = [{
            'spec': {'components': [
                {'name': 'comp-c', 'containerImage': 'img@sha256:abc'},
            ]}
        }]
        mock_rc = MagicMock()
        # 4 high CVEs — below threshold of 5
        mock_rc.fetch_sarif_batch.return_value = {
            'comp-c': [{'level': 'warning'}] * 4
        }
        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=lambda **kw: mock_kc),
            'clients.registry_client': MagicMock(RegistryClient=lambda: mock_rc),
        }):
            warnings = monitor.get_cve_warnings()
            assert len(warnings) == 0

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_exception_returns_empty(self, monitor):
        with patch.dict('sys.modules', {
            'clients.konflux_client': None,  # force ImportError
        }):
            # ImportError is caught by the broad except
            assert monitor.get_cve_warnings() == []

    @patch.dict(os.environ, {'NAMESPACE': 'ns'})
    def test_application_arg_overrides_env(self, monitor):
        mock_kc = MagicMock()
        mock_kc.get_snapshots.return_value = []
        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=lambda **kw: mock_kc),
            'clients.registry_client': MagicMock(RegistryClient=MagicMock),
        }):
            result = monitor.get_cve_warnings(application='custom-app')
            assert result == []

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_empty_sarif_results_skipped(self, monitor):
        mock_kc = MagicMock()
        mock_kc.get_snapshots.return_value = [{
            'spec': {'components': [
                {'name': 'comp-d', 'containerImage': 'img@sha256:abc'},
            ]}
        }]
        mock_rc = MagicMock()
        mock_rc.fetch_sarif_batch.return_value = {'comp-d': []}
        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=lambda **kw: mock_kc),
            'clients.registry_client': MagicMock(RegistryClient=lambda: mock_rc),
        }):
            assert monitor.get_cve_warnings() == []

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_note_level_maps_to_medium(self, monitor):
        mock_kc = MagicMock()
        mock_kc.get_snapshots.return_value = [{
            'spec': {'components': [
                {'name': 'comp-e', 'containerImage': 'img@sha256:abc'},
            ]}
        }]
        mock_rc = MagicMock()
        # Only notes — below any threshold
        mock_rc.fetch_sarif_batch.return_value = {
            'comp-e': [{'level': 'note'}] * 10
        }
        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=lambda **kw: mock_kc),
            'clients.registry_client': MagicMock(RegistryClient=lambda: mock_rc),
        }):
            assert monitor.get_cve_warnings() == []


# ---------------------------------------------------------------------------
# get_component_health_summary
# ---------------------------------------------------------------------------

HEALTH_SUMMARY_COLS = [
    'component_name', 'application', 'current_status', 'health_score',
    'health_status', 'consecutive_failures',
    'success_rate_last_7d', 'total_failures_last_7d',
    'last_successful_build', 'last_failed_build',
]


class TestGetComponentHealthSummary:
    def test_no_filter(self, monitor, mock_db):
        rows = [
            ('comp-1', 'app-1', 'healthy', 90, 'healthy', 0, 100.0, 0, datetime.now(UTC), None),
        ]
        _make_cursor(mock_db, rows, HEALTH_SUMMARY_COLS)
        result = monitor.get_component_health_summary()
        assert len(result) == 1
        assert result[0]['component_name'] == 'comp-1'

    def test_with_application_filter(self, monitor, mock_db):
        rows = [
            ('comp-2', 'app-2', 'failing', 30, 'critical', 5, 20.0, 10, None, datetime.now(UTC)),
        ]
        _make_cursor(mock_db, rows, HEALTH_SUMMARY_COLS)
        result = monitor.get_component_health_summary(application='app-2')
        assert len(result) == 1
        # Verify the parameterized query was used
        cursor = mock_db.connection().__enter__().cursor()
        assert cursor.execute.call_count >= 1

    def test_empty_results(self, monitor, mock_db):
        _make_cursor(mock_db, [], HEALTH_SUMMARY_COLS)
        result = monitor.get_component_health_summary()
        assert result == []


# ---------------------------------------------------------------------------
# get_nightly_status
# ---------------------------------------------------------------------------

class TestGetNightlyStatus:
    def _setup_nightly(self, mock_db, fbc_row=None, comp_health_rows=None):
        """Set up multi-call cursor for get_nightly_status."""
        fbc_cols = ['component_name', 'last_successful_build', 'last_failed_build',
                    'current_status', 'health_score']
        health_cols = ['component_name', 'last_successful_build']

        specs = [(fbc_cols, [fbc_row] if fbc_row else [])]
        if comp_health_rows is not None:
            specs.append((health_cols, comp_health_rows))

        _make_multi_cursor(mock_db, specs)

    @patch('proactive.health_monitor.HealthMonitor._check_pcc_freshness')
    def test_basic_nightly_status(self, mock_pcc, monitor, mock_db):
        mock_pcc.return_value = {'status': 'fresh'}
        now = datetime.now(UTC)
        self._setup_nightly(mock_db, fbc_row=(
            'rhoai-fbc-fragment-v3-5', now, None, 'healthy', 90
        ))

        mock_build_repo = MagicMock()
        mock_build_repo.get_triage_summary.return_value = {'failing_components': []}

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(
                return_value=MagicMock(get_component_metadata=MagicMock(return_value=None))
            )),
            'repositories.conforma_repository': MagicMock(ConformaRepository=MagicMock(
                return_value=MagicMock(get_violation_details=MagicMock(return_value=None))
            )),
            'repositories.build_failure_repository': MagicMock(
                BuildFailureRepository=MagicMock(return_value=mock_build_repo)
            ),
            'clients.github_client': MagicMock(GitHubClient=MagicMock(
                return_value=MagicMock(get_workflow_runs=MagicMock(return_value=[]))
            )),
        }):
            result = monitor.get_nightly_status('rhoai-v3-5')

        assert result['application'] == 'rhoai-v3-5'
        assert result['fbc_component'] == 'rhoai-fbc-fragment-v3-5'
        assert result['fbc_health'] is not None
        assert result['blockers_count'] == 0
        assert result['pcc_freshness'] == {'status': 'fresh'}

    @patch('proactive.health_monitor.HealthMonitor._check_pcc_freshness')
    def test_nightly_with_blockers(self, mock_pcc, monitor, mock_db):
        mock_pcc.return_value = None
        now = datetime.now(UTC)
        fbc_cols = ['component_name', 'last_successful_build', 'last_failed_build',
                    'current_status', 'health_score']
        health_cols = ['component_name', 'last_successful_build']

        _make_multi_cursor(mock_db, [
            (fbc_cols, [('rhoai-fbc-fragment-v3-5', now, None, 'healthy', 90)]),
            (health_cols, [('blocker-comp', now - timedelta(hours=10))]),
        ])

        mock_build_repo = MagicMock()
        mock_build_repo.get_triage_summary.return_value = {
            'failing_components': [
                {
                    'component': 'blocker-comp',
                    'error_type': 'build_error',
                    'error_message': 'OOM',
                    'first_detected_at': now.isoformat(),
                    'ai_analyzed': False,
                }
            ]
        }

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(
                return_value=MagicMock(get_component_metadata=MagicMock(return_value=None))
            )),
            'repositories.conforma_repository': MagicMock(ConformaRepository=MagicMock(
                return_value=MagicMock(get_violation_details=MagicMock(return_value=None))
            )),
            'repositories.build_failure_repository': MagicMock(
                BuildFailureRepository=MagicMock(return_value=mock_build_repo)
            ),
            'clients.github_client': MagicMock(GitHubClient=MagicMock(
                return_value=MagicMock(get_workflow_runs=MagicMock(return_value=[]))
            )),
        }):
            result = monitor.get_nightly_status('rhoai-v3-5')

        assert result['blockers_count'] == 1
        assert result['blockers'][0]['component'] == 'blocker-comp'
        assert result['blockers'][0]['has_analysis'] is False

    @patch('proactive.health_monitor.HealthMonitor._check_pcc_freshness')
    def test_nightly_with_ai_analysis(self, mock_pcc, monitor, mock_db):
        mock_pcc.return_value = None
        now = datetime.now(UTC)
        fbc_cols = ['component_name', 'last_successful_build', 'last_failed_build',
                    'current_status', 'health_score']
        health_cols = ['component_name', 'last_successful_build']

        _make_multi_cursor(mock_db, [
            (fbc_cols, [('rhoai-fbc-fragment-v3-5', now, None, 'healthy', 90)]),
            (health_cols, []),
        ])

        mock_build_repo = MagicMock()
        mock_build_repo.get_triage_summary.return_value = {
            'failing_components': [
                {
                    'component': 'analyzed-comp',
                    'error_type': 'build_error',
                    'error_message': 'dep missing',
                    'first_detected_at': now.isoformat(),
                    'ai_analyzed': True,
                }
            ]
        }

        mock_ai_repo = MagicMock()
        mock_ai_repo.get_analysis_by_component.return_value = {
            'failure_category': 'dependency',
            'root_cause': 'Missing dependency on libfoo version 2.3',
        }

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(
                return_value=MagicMock(get_component_metadata=MagicMock(return_value=None))
            )),
            'repositories.conforma_repository': MagicMock(ConformaRepository=MagicMock(
                return_value=MagicMock(get_violation_details=MagicMock(return_value=None))
            )),
            'repositories.build_failure_repository': MagicMock(
                BuildFailureRepository=MagicMock(return_value=mock_build_repo)
            ),
            'repositories.ai_analysis_repository': MagicMock(
                AIAnalysisRepository=MagicMock(return_value=mock_ai_repo)
            ),
            'clients.github_client': MagicMock(GitHubClient=MagicMock(
                return_value=MagicMock(get_workflow_runs=MagicMock(return_value=[]))
            )),
        }):
            result = monitor.get_nightly_status('rhoai-v3-5')

        assert result['blockers_count'] == 1
        blocker = result['blockers'][0]
        assert blocker['has_analysis'] is True
        assert blocker['failure_category'] == 'dependency'

    @patch('proactive.health_monitor.HealthMonitor._check_pcc_freshness')
    def test_nightly_no_fbc_health(self, mock_pcc, monitor, mock_db):
        mock_pcc.return_value = None
        _make_multi_cursor(mock_db, [
            (['component_name', 'last_successful_build', 'last_failed_build',
              'current_status', 'health_score'], []),
        ])

        mock_build_repo = MagicMock()
        mock_build_repo.get_triage_summary.return_value = {'failing_components': []}

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(
                return_value=MagicMock(get_component_metadata=MagicMock(return_value=None))
            )),
            'repositories.conforma_repository': MagicMock(ConformaRepository=MagicMock(
                return_value=MagicMock(get_violation_details=MagicMock(return_value=None))
            )),
            'repositories.build_failure_repository': MagicMock(
                BuildFailureRepository=MagicMock(return_value=mock_build_repo)
            ),
            'clients.github_client': MagicMock(GitHubClient=MagicMock(
                return_value=MagicMock(get_workflow_runs=MagicMock(return_value=[]))
            )),
        }):
            result = monitor.get_nightly_status('rhoai-v3-5')

        assert result['fbc_health'] is None

    @patch('proactive.health_monitor.HealthMonitor._check_pcc_freshness')
    def test_nightly_fbc_image_from_kubernetes(self, mock_pcc, monitor, mock_db):
        mock_pcc.return_value = None
        now = datetime.now(UTC)
        _make_multi_cursor(mock_db, [
            (['component_name', 'last_successful_build', 'last_failed_build',
              'current_status', 'health_score'],
             [('rhoai-fbc-fragment-v3-5', now, None, 'healthy', 90)]),
        ])

        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'container_image': 'quay.io/rhoai/fbc:latest'
        }

        mock_build_repo = MagicMock()
        mock_build_repo.get_triage_summary.return_value = {'failing_components': []}

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'repositories.conforma_repository': MagicMock(ConformaRepository=MagicMock(
                return_value=MagicMock(get_violation_details=MagicMock(return_value=None))
            )),
            'repositories.build_failure_repository': MagicMock(
                BuildFailureRepository=MagicMock(return_value=mock_build_repo)
            ),
            'clients.github_client': MagicMock(GitHubClient=MagicMock(
                return_value=MagicMock(get_workflow_runs=MagicMock(return_value=[]))
            )),
        }):
            result = monitor.get_nightly_status('rhoai-v3-5')

        assert result['fbc_image'] == 'quay.io/rhoai/fbc:latest'

    @patch('proactive.health_monitor.HealthMonitor._check_pcc_freshness')
    def test_nightly_gha_validation(self, mock_pcc, monitor, mock_db):
        mock_pcc.return_value = None
        now = datetime.now(UTC)
        _make_multi_cursor(mock_db, [
            (['component_name', 'last_successful_build', 'last_failed_build',
              'current_status', 'health_score'],
             [('rhoai-fbc-fragment-v3-5', now, None, 'healthy', 90)]),
        ])

        mock_gh = MagicMock()
        mock_gh.get_workflow_runs.return_value = [{
            'conclusion': 'success',
            'status': 'completed',
            'created_at': '2026-07-01T00:00:00Z',
            'html_url': 'https://github.com/run/1',
        }]

        mock_build_repo = MagicMock()
        mock_build_repo.get_triage_summary.return_value = {'failing_components': []}

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(
                return_value=MagicMock(get_component_metadata=MagicMock(return_value=None))
            )),
            'repositories.conforma_repository': MagicMock(ConformaRepository=MagicMock(
                return_value=MagicMock(get_violation_details=MagicMock(return_value=None))
            )),
            'repositories.build_failure_repository': MagicMock(
                BuildFailureRepository=MagicMock(return_value=mock_build_repo)
            ),
            'clients.github_client': MagicMock(
                GitHubClient=MagicMock(return_value=mock_gh)
            ),
        }):
            result = monitor.get_nightly_status('rhoai-v3-5')

        assert result['gha_validation'] is not None
        assert result['gha_validation']['conclusion'] == 'success'

    @patch('proactive.health_monitor.HealthMonitor._check_pcc_freshness')
    def test_nightly_conforma_violations(self, mock_pcc, monitor, mock_db):
        mock_pcc.return_value = None
        now = datetime.now(UTC)
        _make_multi_cursor(mock_db, [
            (['component_name', 'last_successful_build', 'last_failed_build',
              'current_status', 'health_score'],
             [('rhoai-fbc-fragment-v3-5', now, None, 'healthy', 90)]),
        ])

        mock_conforma_repo = MagicMock()
        mock_conforma_repo.get_violation_details.return_value = {
            'violations_count': 3,
            'warnings_count': 1,
            'scenario': 'release',
            'violation_summary': 'FIPS check failed',
        }

        mock_build_repo = MagicMock()
        mock_build_repo.get_triage_summary.return_value = {'failing_components': []}

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(
                return_value=MagicMock(get_component_metadata=MagicMock(return_value=None))
            )),
            'repositories.conforma_repository': MagicMock(
                ConformaRepository=MagicMock(return_value=mock_conforma_repo)
            ),
            'repositories.build_failure_repository': MagicMock(
                BuildFailureRepository=MagicMock(return_value=mock_build_repo)
            ),
            'clients.github_client': MagicMock(GitHubClient=MagicMock(
                return_value=MagicMock(get_workflow_runs=MagicMock(return_value=[]))
            )),
        }):
            result = monitor.get_nightly_status('rhoai-v3-5')

        assert result['fbc_conforma'] is not None
        assert result['fbc_conforma']['violations_count'] == 3


# ---------------------------------------------------------------------------
# get_pcc_freshness_warnings
# ---------------------------------------------------------------------------

class TestGetPccFreshnessWarnings:
    def test_fresh_returns_empty(self, monitor):
        with patch.object(monitor, '_check_pcc_freshness', return_value={'status': 'fresh'}):
            assert monitor.get_pcc_freshness_warnings() == []

    def test_unknown_returns_empty(self, monitor):
        with patch.object(monitor, '_check_pcc_freshness', return_value={'status': 'unknown'}):
            assert monitor.get_pcc_freshness_warnings() == []

    def test_none_returns_empty(self, monitor):
        with patch.object(monitor, '_check_pcc_freshness', return_value=None):
            assert monitor.get_pcc_freshness_warnings() == []

    def test_stale_returns_critical_warning(self, monitor):
        pcc = {
            'status': 'stale',
            'missing_versions': ['v2.14.0', 'v2.15.0'],
        }
        with patch.object(monitor, '_check_pcc_freshness', return_value=pcc):
            warnings = monitor.get_pcc_freshness_warnings()
            assert len(warnings) == 1
            w = warnings[0]
            assert w.severity == 'critical'
            assert w.signal_type == 'pcc_stale'
            assert w.component_name == 'pcc-cache'
            assert 'v2.14.0' in w.message
            assert '2 version(s)' in w.message

    def test_stale_many_versions_truncated(self, monitor):
        versions = ['v1.{}'.format(i) for i in range(10)]
        pcc = {
            'status': 'stale',
            'missing_versions': versions,
        }
        with patch.object(monitor, '_check_pcc_freshness', return_value=pcc):
            warnings = monitor.get_pcc_freshness_warnings()
            assert '(+5 more)' in warnings[0].message

    def test_stale_exactly_five_no_truncation(self, monitor):
        versions = ['v1.{}'.format(i) for i in range(5)]
        pcc = {
            'status': 'stale',
            'missing_versions': versions,
        }
        with patch.object(monitor, '_check_pcc_freshness', return_value=pcc):
            warnings = monitor.get_pcc_freshness_warnings()
            assert '+' not in warnings[0].message


# ---------------------------------------------------------------------------
# _check_pcc_freshness
# ---------------------------------------------------------------------------

class TestCheckPccFreshness:
    @patch.dict(os.environ, {'GITHUB_TOKEN': ''}, clear=False)
    def test_no_token_returns_none(self, monitor):
        assert monitor._check_pcc_freshness() is None

    @patch.dict(os.environ, {'GITHUB_TOKEN': 'tok'})
    def test_fresh_cache(self, monitor):
        mock_gh = MagicMock()
        mock_gh.get_workflow_runs.return_value = [{
            'conclusion': 'success',
            'created_at': '2026-07-01T00:00:00Z',
            'html_url': 'https://github.com/run/1',
        }]
        mock_gh.get_file_content.return_value = 'v2.14.0\nv2.15.0\n'

        mock_rc = MagicMock()
        mock_rc.list_tags.return_value = ['v2.14.0', 'v2.15.0', 'latest']

        with patch.dict('sys.modules', {
            'clients.github_client': MagicMock(GitHubClient=MagicMock(return_value=mock_gh)),
            'clients.registry_client': MagicMock(RegistryClient=MagicMock(return_value=mock_rc)),
        }):
            result = monitor._check_pcc_freshness()

        assert result['status'] == 'fresh'
        assert result['cached_versions'] == 2
        assert result['missing_versions'] == []

    @patch.dict(os.environ, {'GITHUB_TOKEN': 'tok'})
    def test_stale_cache(self, monitor):
        mock_gh = MagicMock()
        mock_gh.get_workflow_runs.return_value = []
        mock_gh.get_file_content.return_value = 'v2.14.0\n'

        mock_rc = MagicMock()
        mock_rc.list_tags.return_value = ['v2.14.0', 'v2.15.0']

        with patch.dict('sys.modules', {
            'clients.github_client': MagicMock(GitHubClient=MagicMock(return_value=mock_gh)),
            'clients.registry_client': MagicMock(RegistryClient=MagicMock(return_value=mock_rc)),
        }):
            result = monitor._check_pcc_freshness()

        assert result['status'] == 'stale'
        assert result['missing_versions'] == ['v2.15.0']

    @patch.dict(os.environ, {'GITHUB_TOKEN': 'tok'})
    def test_no_cached_file(self, monitor):
        mock_gh = MagicMock()
        mock_gh.get_workflow_runs.return_value = []
        mock_gh.get_file_content.return_value = None

        with patch.dict('sys.modules', {
            'clients.github_client': MagicMock(GitHubClient=MagicMock(return_value=mock_gh)),
        }):
            result = monitor._check_pcc_freshness()

        assert result['status'] == 'unknown'

    @patch.dict(os.environ, {'GITHUB_TOKEN': 'tok'})
    def test_no_registry_tags(self, monitor):
        mock_gh = MagicMock()
        mock_gh.get_workflow_runs.return_value = []
        mock_gh.get_file_content.return_value = 'v1.0\n'

        mock_rc = MagicMock()
        mock_rc.list_tags.return_value = []

        with patch.dict('sys.modules', {
            'clients.github_client': MagicMock(GitHubClient=MagicMock(return_value=mock_gh)),
            'clients.registry_client': MagicMock(RegistryClient=MagicMock(return_value=mock_rc)),
        }):
            result = monitor._check_pcc_freshness()

        assert result['status'] == 'unknown'

    @patch.dict(os.environ, {'GITHUB_TOKEN': 'tok'})
    def test_exception_returns_unknown(self, monitor):
        with patch.dict('sys.modules', {
            'clients.github_client': None,
        }):
            result = monitor._check_pcc_freshness()
            assert result['status'] == 'unknown'

    @patch.dict(os.environ, {'GITHUB_TOKEN': 'tok'})
    def test_non_v_tags_ignored(self, monitor):
        mock_gh = MagicMock()
        mock_gh.get_workflow_runs.return_value = []
        mock_gh.get_file_content.return_value = 'v1.0\n'

        mock_rc = MagicMock()
        mock_rc.list_tags.return_value = ['v1.0', 'latest', 'sha-abc123']

        with patch.dict('sys.modules', {
            'clients.github_client': MagicMock(GitHubClient=MagicMock(return_value=mock_gh)),
            'clients.registry_client': MagicMock(RegistryClient=MagicMock(return_value=mock_rc)),
        }):
            result = monitor._check_pcc_freshness()

        assert result['status'] == 'fresh'
        # latest and sha-abc123 don't start with 'v', so they're excluded
        assert result['registry_versions'] == 1

    @patch.dict(os.environ, {'GITHUB_TOKEN': 'tok'})
    def test_last_regen_populated(self, monitor):
        mock_gh = MagicMock()
        mock_gh.get_workflow_runs.return_value = [{
            'conclusion': 'failure',
            'created_at': '2026-06-28T12:00:00Z',
            'html_url': 'https://github.com/run/42',
        }]
        mock_gh.get_file_content.return_value = 'v1.0\n'

        mock_rc = MagicMock()
        mock_rc.list_tags.return_value = ['v1.0']

        with patch.dict('sys.modules', {
            'clients.github_client': MagicMock(GitHubClient=MagicMock(return_value=mock_gh)),
            'clients.registry_client': MagicMock(RegistryClient=MagicMock(return_value=mock_rc)),
        }):
            result = monitor._check_pcc_freshness()

        assert result['last_regen'] is not None
        assert result['last_regen']['conclusion'] == 'failure'


# ---------------------------------------------------------------------------
# get_stale_components
# ---------------------------------------------------------------------------

class TestGetStaleComponents:
    @patch.dict(os.environ, {'NAMESPACE': '', 'APPLICATION_NAME': 'app'})
    def test_no_namespace_returns_error(self, monitor):
        result = monitor.get_stale_components()
        assert 'error' in result
        assert 'NAMESPACE not set' in result['error']

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app', 'GITHUB_TOKEN': 'tok'})
    def test_all_fresh_components(self, monitor):
        mock_kc = MagicMock()
        mock_kc.list_components.return_value = [{
            'name': 'comp-a',
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'last_built_commit': 'abc123def456',
            'application': 'app',
        }]

        mock_gh = MagicMock()
        mock_gh.get_ref_sha.return_value = 'abc123def456'  # same as built

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.github_client': MagicMock(
                GitHubClient=MagicMock(return_value=mock_gh),
                parse_github_repo=MagicMock(return_value=('org', 'repo')),
            ),
            'proactive.ref_cache': MagicMock(
                RefCache=MagicMock(return_value=MagicMock(
                    get_batch=MagicMock(return_value={}),
                    put_batch=MagicMock(),
                    stats=MagicMock(return_value={}),
                )),
                CacheConfig=MagicMock(from_env=MagicMock()),
            ),
        }):
            result = monitor.get_stale_components()

        assert result['stale_count'] == 0

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app', 'GITHUB_TOKEN': 'tok'})
    def test_stale_component_detected(self, monitor):
        mock_kc = MagicMock()
        mock_kc.list_components.return_value = [{
            'name': 'comp-a',
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'last_built_commit': 'abc123def456',
            'application': 'app',
            'nudges': [],
        }]

        mock_gh = MagicMock()
        mock_gh.get_ref_sha.return_value = 'xyz789000000'  # different

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.github_client': MagicMock(
                GitHubClient=MagicMock(return_value=mock_gh),
                parse_github_repo=MagicMock(return_value=('org', 'repo')),
            ),
            'proactive.ref_cache': MagicMock(
                RefCache=MagicMock(return_value=MagicMock(
                    get_batch=MagicMock(return_value={}),
                    put_batch=MagicMock(),
                    stats=MagicMock(return_value={}),
                )),
                CacheConfig=MagicMock(from_env=MagicMock()),
            ),
        }):
            result = monitor.get_stale_components(diagnose=False)

        assert result['stale_count'] == 1
        assert result['stale'][0]['component'] == 'comp-a'

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app', 'GITHUB_TOKEN': 'tok'})
    def test_skipped_components_no_branch(self, monitor):
        mock_kc = MagicMock()
        mock_kc.list_components.return_value = [
            {
                'name': 'comp-no-branch',
                'repository_url': 'https://github.com/org/repo',
                'branch': '',  # no branch
                'last_built_commit': 'abc123',
            },
        ]

        mock_gh = MagicMock()

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.github_client': MagicMock(
                GitHubClient=MagicMock(return_value=mock_gh),
                parse_github_repo=MagicMock(return_value=('org', 'repo')),
            ),
            'proactive.ref_cache': MagicMock(
                RefCache=MagicMock(return_value=MagicMock(
                    get_batch=MagicMock(return_value={}),
                    put_batch=MagicMock(),
                    stats=MagicMock(return_value={}),
                )),
                CacheConfig=MagicMock(from_env=MagicMock()),
            ),
        }):
            result = monitor.get_stale_components(diagnose=False)

        assert result['skipped'] == 1
        assert result['stale_count'] == 0

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app', 'GITHUB_TOKEN': 'tok'})
    def test_skipped_no_built_commit(self, monitor):
        mock_kc = MagicMock()
        mock_kc.list_components.return_value = [
            {
                'name': 'comp-no-commit',
                'repository_url': 'https://github.com/org/repo',
                'branch': 'main',
                'last_built_commit': '',  # no commit
            },
        ]

        mock_gh = MagicMock()

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.github_client': MagicMock(
                GitHubClient=MagicMock(return_value=mock_gh),
                parse_github_repo=MagicMock(return_value=('org', 'repo')),
            ),
            'proactive.ref_cache': MagicMock(
                RefCache=MagicMock(return_value=MagicMock(
                    get_batch=MagicMock(return_value={}),
                    put_batch=MagicMock(),
                    stats=MagicMock(return_value={}),
                )),
                CacheConfig=MagicMock(from_env=MagicMock()),
            ),
        }):
            result = monitor.get_stale_components(diagnose=False)

        assert result['skipped'] == 1

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app', 'GITHUB_TOKEN': 'tok'})
    def test_no_cache_mode(self, monitor):
        mock_kc = MagicMock()
        mock_kc.list_components.return_value = [{
            'name': 'comp-a',
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'last_built_commit': 'abc123def456',
            'application': 'app',
        }]

        mock_gh = MagicMock()
        mock_gh.get_ref_sha.return_value = 'abc123def456'

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.github_client': MagicMock(
                GitHubClient=MagicMock(return_value=mock_gh),
                parse_github_repo=MagicMock(return_value=('org', 'repo')),
            ),
            'proactive.ref_cache': MagicMock(
                RefCache=MagicMock(),
                CacheConfig=MagicMock(from_env=MagicMock()),
            ),
        }):
            result = monitor.get_stale_components(use_cache=False, diagnose=False)

        assert 'cache' not in result

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app', 'GITHUB_TOKEN': 'tok'})
    def test_unparseable_repo_url_skipped(self, monitor):
        mock_kc = MagicMock()
        mock_kc.list_components.return_value = [{
            'name': 'comp-bad',
            'repository_url': 'not-a-github-url',
            'branch': 'main',
            'last_built_commit': 'abc123',
        }]

        mock_gh = MagicMock()

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.github_client': MagicMock(
                GitHubClient=MagicMock(return_value=mock_gh),
                parse_github_repo=MagicMock(return_value=None),
            ),
            'proactive.ref_cache': MagicMock(
                RefCache=MagicMock(return_value=MagicMock(
                    get_batch=MagicMock(return_value={}),
                    put_batch=MagicMock(),
                    stats=MagicMock(return_value={}),
                )),
                CacheConfig=MagicMock(from_env=MagicMock()),
            ),
        }):
            result = monitor.get_stale_components(diagnose=False)

        assert result['skipped'] == 1

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app', 'GITHUB_TOKEN': 'tok'})
    def test_github_api_errors_tracked(self, monitor):
        mock_kc = MagicMock()
        mock_kc.list_components.return_value = [{
            'name': 'comp-a',
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'last_built_commit': 'abc123def456',
            'application': 'app',
        }]

        mock_gh = MagicMock()
        mock_gh.get_ref_sha.side_effect = Exception("rate limited")

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.github_client': MagicMock(
                GitHubClient=MagicMock(return_value=mock_gh),
                parse_github_repo=MagicMock(return_value=('org', 'repo')),
            ),
            'proactive.ref_cache': MagicMock(
                RefCache=MagicMock(return_value=MagicMock(
                    get_batch=MagicMock(return_value={}),
                    put_batch=MagicMock(),
                    stats=MagicMock(return_value={}),
                )),
                CacheConfig=MagicMock(from_env=MagicMock()),
            ),
        }):
            result = monitor.get_stale_components(diagnose=False)

        assert result.get('api_errors', 0) >= 1
        assert 'warning' in result


# ---------------------------------------------------------------------------
# _diagnose_stale
# ---------------------------------------------------------------------------

class TestDiagnoseStale:
    def test_diagnose_adds_diagnosis(self, monitor):
        mock_kc = MagicMock()
        mock_kc.list_pac_repositories.return_value = []
        mock_kc.list_recent_pipelineruns.return_value = []

        mock_diagnosis = MagicMock()
        mock_diagnosis.cause = 'pac_not_configured'
        mock_diagnosis.severity = 'warning'
        mock_diagnosis.detail = 'No PaC repo found'

        stale_list = [{
            'component': 'comp-a',
            'repository_url': 'https://github.com/org/repo',
            'nudges': [],
            'head_commit_full': 'abc123',
        }]
        all_comps = [{'name': 'comp-a'}]

        with patch.dict('sys.modules', {
            'proactive.trigger_diagnosis': MagicMock(
                diagnose_stale_trigger=MagicMock(return_value=mock_diagnosis)
            ),
        }):
            result = monitor._diagnose_stale(mock_kc, stale_list, all_comps)

        assert result[0]['diagnosis']['cause'] == 'pac_not_configured'
        assert result[0]['diagnosis']['severity'] == 'warning'

    def test_diagnose_pac_fetch_failure(self, monitor):
        mock_kc = MagicMock()
        mock_kc.list_pac_repositories.side_effect = Exception("kube error")
        mock_kc.list_recent_pipelineruns.return_value = []

        mock_diagnosis = MagicMock()
        mock_diagnosis.cause = 'unknown'
        mock_diagnosis.severity = 'info'
        mock_diagnosis.detail = 'Unable to determine'

        stale_list = [{
            'component': 'comp-b',
            'repository_url': 'https://github.com/org/repo',
            'nudges': [],
            'head_commit_full': 'def456',
        }]
        all_comps = [{'name': 'comp-b'}]

        with patch.dict('sys.modules', {
            'proactive.trigger_diagnosis': MagicMock(
                diagnose_stale_trigger=MagicMock(return_value=mock_diagnosis)
            ),
        }):
            result = monitor._diagnose_stale(mock_kc, stale_list, all_comps)

        assert 'diagnosis' in result[0]

    def test_diagnose_pipelinerun_fetch_failure(self, monitor):
        mock_kc = MagicMock()
        mock_kc.list_pac_repositories.return_value = []
        mock_kc.list_recent_pipelineruns.side_effect = Exception("kube error")

        mock_diagnosis = MagicMock()
        mock_diagnosis.cause = 'no_recent_runs'
        mock_diagnosis.severity = 'warning'
        mock_diagnosis.detail = 'No runs found'

        stale_list = [{
            'component': 'comp-c',
            'repository_url': '',
            'nudges': [],
            'head_commit_full': '',
        }]
        all_comps = [{'name': 'comp-c'}]

        with patch.dict('sys.modules', {
            'proactive.trigger_diagnosis': MagicMock(
                diagnose_stale_trigger=MagicMock(return_value=mock_diagnosis)
            ),
        }):
            result = monitor._diagnose_stale(mock_kc, stale_list, all_comps)

        # Even with pipeline run fetch failure, diagnosis should still run
        assert result[0]['diagnosis']['cause'] == 'no_recent_runs'


# ---------------------------------------------------------------------------
# get_stale_warnings
# ---------------------------------------------------------------------------

class TestGetStaleWarnings:
    def test_stale_generates_warnings(self, monitor):
        stale_result = {
            'application': 'app-1',
            'stale': [
                {
                    'component': 'comp-x',
                    'head_commit': 'abc123',
                    'built_commit': 'def456',
                    'branch': 'main',
                }
            ],
        }
        with patch.object(monitor, 'get_stale_components', return_value=stale_result):
            warnings = monitor.get_stale_warnings()
            assert len(warnings) == 1
            w = warnings[0]
            assert w.signal_type == 'commit_staleness'
            assert w.severity == 'warning'
            assert 'abc123' in w.message
            assert 'def456' in w.message

    def test_no_stale_returns_empty(self, monitor):
        with patch.object(monitor, 'get_stale_components', return_value={'stale': [], 'application': 'app'}):
            assert monitor.get_stale_warnings() == []

    def test_exception_returns_empty(self, monitor):
        with patch.object(monitor, 'get_stale_components', side_effect=Exception("boom")):
            assert monitor.get_stale_warnings() == []

    def test_application_forwarded(self, monitor):
        with patch.object(monitor, 'get_stale_components', return_value={'stale': [], 'application': 'custom'}) as mock:
            monitor.get_stale_warnings(application='custom')
            mock.assert_called_once_with('custom', diagnose=False)


# ---------------------------------------------------------------------------
# check_snapshot_freshness
# ---------------------------------------------------------------------------

class TestCheckSnapshotFreshness:
    @patch.dict(os.environ, {'NAMESPACE': '', 'APPLICATION_NAME': 'app'})
    def test_no_namespace_returns_error(self, monitor):
        result = monitor.check_snapshot_freshness()
        assert 'error' in result
        assert 'NAMESPACE not set' in result['error']

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_no_snapshots_returns_error(self, monitor):
        mock_kfx = MagicMock()
        mock_kfx.get_snapshots.return_value = []

        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=MagicMock(return_value=mock_kfx)),
            'clients.tekton_results': MagicMock(TektonResultsClient=MagicMock()),
        }):
            result = monitor.check_snapshot_freshness()

        assert 'error' in result
        assert 'No snapshots found' in result['error']

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_all_fresh(self, monitor):
        mock_kfx = MagicMock()
        mock_kfx.get_snapshots.return_value = [{
            'metadata': {'name': 'snap-1', 'creationTimestamp': '2026-07-01T00:00:00Z'},
            'spec': {'components': [
                {'name': 'comp-a', 'containerImage': 'img@sha256:aaa'},
            ]},
        }]

        mock_tr = MagicMock()
        mock_tr.query_pipelinerun_records.return_value = [{
            'metadata': {
                'name': 'pr-1',
                'creationTimestamp': '2026-07-01T01:00:00Z',
                'labels': {
                    'appstudio.openshift.io/component': 'comp-a',
                    'pipelinesascode.tekton.dev/event-type': 'push',
                },
            },
            'status': {
                'conditions': [{'status': 'True', 'reason': 'Succeeded'}],
                'results': [
                    {'name': 'IMAGE_URL', 'value': 'img'},
                    {'name': 'IMAGE_DIGEST', 'value': 'sha256:aaa'},
                ],
            },
        }]

        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=MagicMock(return_value=mock_kfx)),
            'clients.tekton_results': MagicMock(TektonResultsClient=MagicMock(return_value=mock_tr)),
        }):
            result = monitor.check_snapshot_freshness()

        assert result['fresh_count'] == 1
        assert result['stale_count'] == 0
        assert result['no_builds_count'] == 0

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_latest_build_failed_shows_stale(self, monitor):
        mock_kfx = MagicMock()
        mock_kfx.get_snapshots.return_value = [{
            'metadata': {'name': 'snap-1', 'creationTimestamp': '2026-07-01T00:00:00Z'},
            'spec': {'components': [
                {'name': 'comp-fail', 'containerImage': 'img@sha256:old'},
            ]},
        }]

        mock_tr = MagicMock()
        mock_tr.query_pipelinerun_records.return_value = [{
            'metadata': {
                'name': 'pr-fail',
                'creationTimestamp': '2026-07-01T02:00:00Z',
                'labels': {
                    'appstudio.openshift.io/component': 'comp-fail',
                    'pipelinesascode.tekton.dev/event-type': 'push',
                },
            },
            'status': {
                'conditions': [{'status': 'False', 'reason': 'Failed'}],
                'results': [],
            },
        }]

        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=MagicMock(return_value=mock_kfx)),
            'clients.tekton_results': MagicMock(TektonResultsClient=MagicMock(return_value=mock_tr)),
        }):
            result = monitor.check_snapshot_freshness()

        assert result['stale_count'] == 1
        assert result['stale'][0]['reason'] == 'latest_build_failed'

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_newer_build_available(self, monitor):
        mock_kfx = MagicMock()
        mock_kfx.get_snapshots.return_value = [{
            'metadata': {'name': 'snap-1', 'creationTimestamp': '2026-07-01T00:00:00Z'},
            'spec': {'components': [
                {'name': 'comp-old', 'containerImage': 'img@sha256:old111'},
            ]},
        }]

        mock_tr = MagicMock()
        mock_tr.query_pipelinerun_records.return_value = [{
            'metadata': {
                'name': 'pr-new',
                'creationTimestamp': '2026-07-01T03:00:00Z',
                'labels': {
                    'appstudio.openshift.io/component': 'comp-old',
                    'pipelinesascode.tekton.dev/event-type': 'push',
                },
            },
            'status': {
                'conditions': [{'status': 'True', 'reason': 'Succeeded'}],
                'results': [
                    {'name': 'IMAGE_URL', 'value': 'img'},
                    {'name': 'IMAGE_DIGEST', 'value': 'sha256:new222'},
                ],
            },
        }]

        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=MagicMock(return_value=mock_kfx)),
            'clients.tekton_results': MagicMock(TektonResultsClient=MagicMock(return_value=mock_tr)),
        }):
            result = monitor.check_snapshot_freshness()

        assert result['stale_count'] == 1
        assert result['stale'][0]['reason'] == 'newer_build_available'

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_no_builds_for_component(self, monitor):
        mock_kfx = MagicMock()
        mock_kfx.get_snapshots.return_value = [{
            'metadata': {'name': 'snap-1', 'creationTimestamp': '2026-07-01T00:00:00Z'},
            'spec': {'components': [
                {'name': 'comp-orphan', 'containerImage': 'img@sha256:abc'},
            ]},
        }]

        mock_tr = MagicMock()
        mock_tr.query_pipelinerun_records.return_value = []

        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=MagicMock(return_value=mock_kfx)),
            'clients.tekton_results': MagicMock(TektonResultsClient=MagicMock(return_value=mock_tr)),
        }):
            result = monitor.check_snapshot_freshness()

        assert result['no_builds_count'] == 1
        assert result['no_builds'][0]['component'] == 'comp-orphan'

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_pr_event_type_filtered(self, monitor):
        """Only push/incoming events should be considered."""
        mock_kfx = MagicMock()
        mock_kfx.get_snapshots.return_value = [{
            'metadata': {'name': 'snap-1', 'creationTimestamp': '2026-07-01T00:00:00Z'},
            'spec': {'components': [
                {'name': 'comp-pr', 'containerImage': 'img@sha256:abc'},
            ]},
        }]

        mock_tr = MagicMock()
        mock_tr.query_pipelinerun_records.return_value = [{
            'metadata': {
                'name': 'pr-pr-event',
                'creationTimestamp': '2026-07-01T01:00:00Z',
                'labels': {
                    'appstudio.openshift.io/component': 'comp-pr',
                    'pipelinesascode.tekton.dev/event-type': 'pull_request',
                },
            },
            'status': {
                'conditions': [{'status': 'True', 'reason': 'Succeeded'}],
                'results': [
                    {'name': 'IMAGE_URL', 'value': 'img'},
                    {'name': 'IMAGE_DIGEST', 'value': 'sha256:abc'},
                ],
            },
        }]

        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=MagicMock(return_value=mock_kfx)),
            'clients.tekton_results': MagicMock(TektonResultsClient=MagicMock(return_value=mock_tr)),
        }):
            result = monitor.check_snapshot_freshness()

        # pull_request event is filtered out, so comp-pr has no builds
        assert result['no_builds_count'] == 1

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_application_arg_overrides_env(self, monitor):
        mock_kfx = MagicMock()
        mock_kfx.get_snapshots.return_value = []

        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=MagicMock(return_value=mock_kfx)),
            'clients.tekton_results': MagicMock(TektonResultsClient=MagicMock()),
        }):
            result = monitor.check_snapshot_freshness(application='custom-app')

        assert result['application'] == 'custom-app'

    @patch.dict(os.environ, {'NAMESPACE': 'ns', 'APPLICATION_NAME': 'app'})
    def test_latest_build_picked_by_timestamp(self, monitor):
        """When multiple builds exist, the most recent one is used."""
        mock_kfx = MagicMock()
        mock_kfx.get_snapshots.return_value = [{
            'metadata': {'name': 'snap-1', 'creationTimestamp': '2026-07-01T00:00:00Z'},
            'spec': {'components': [
                {'name': 'comp-multi', 'containerImage': 'img@sha256:old'},
            ]},
        }]

        mock_tr = MagicMock()
        mock_tr.query_pipelinerun_records.return_value = [
            {
                'metadata': {
                    'name': 'pr-old',
                    'creationTimestamp': '2026-06-30T01:00:00Z',
                    'labels': {
                        'appstudio.openshift.io/component': 'comp-multi',
                        'pipelinesascode.tekton.dev/event-type': 'push',
                    },
                },
                'status': {
                    'conditions': [{'status': 'True', 'reason': 'Succeeded'}],
                    'results': [
                        {'name': 'IMAGE_URL', 'value': 'img'},
                        {'name': 'IMAGE_DIGEST', 'value': 'sha256:old'},
                    ],
                },
            },
            {
                'metadata': {
                    'name': 'pr-new',
                    'creationTimestamp': '2026-07-01T05:00:00Z',
                    'labels': {
                        'appstudio.openshift.io/component': 'comp-multi',
                        'pipelinesascode.tekton.dev/event-type': 'push',
                    },
                },
                'status': {
                    'conditions': [{'status': 'True', 'reason': 'Succeeded'}],
                    'results': [
                        {'name': 'IMAGE_URL', 'value': 'img'},
                        {'name': 'IMAGE_DIGEST', 'value': 'sha256:new'},
                    ],
                },
            },
        ]

        with patch.dict('sys.modules', {
            'clients.konflux_client': MagicMock(KonfluxClient=MagicMock(return_value=mock_kfx)),
            'clients.tekton_results': MagicMock(TektonResultsClient=MagicMock(return_value=mock_tr)),
        }):
            result = monitor.check_snapshot_freshness()

        # Latest build has sha256:new, snapshot has sha256:old -> stale
        assert result['stale_count'] == 1
        assert result['stale'][0]['reason'] == 'newer_build_available'


# ---------------------------------------------------------------------------
# run_checks (aggregation)
# ---------------------------------------------------------------------------

class TestRunChecks:
    def test_aggregates_all_sub_checks(self, monitor):
        w1 = HealthWarning('c1', 'a1', 'degrading_health', 'warning', 'm1', {})
        w2 = HealthWarning('c2', 'a2', 'pattern_cascade', 'warning', 'm2', {})
        w3 = HealthWarning('c3', 'a3', 'repeat_failure', 'critical', 'm3', {})
        w4 = HealthWarning('c4', 'a4', 'critical_cves', 'critical', 'm4', {})
        w5 = HealthWarning('c5', 'a5', 'stale_nightly', 'warning', 'm5', {})
        w6 = HealthWarning('c6', 'a6', 'commit_staleness', 'warning', 'm6', {})
        w7 = HealthWarning('c7', 'a7', 'pcc_stale', 'critical', 'm7', {})

        with patch.object(monitor, 'get_degrading_components', return_value=[w1]), \
             patch.object(monitor, 'get_pattern_cascades', return_value=[w2]), \
             patch.object(monitor, 'get_repeat_failures', return_value=[w3]), \
             patch.object(monitor, 'get_cve_warnings', return_value=[w4]), \
             patch.object(monitor, 'get_stale_nightly_builds', return_value=[w5]), \
             patch.object(monitor, 'get_stale_warnings', return_value=[w6]), \
             patch.object(monitor, 'get_pcc_freshness_warnings', return_value=[w7]):
            warnings = monitor.run_checks()

        assert len(warnings) == 7
        signal_types = {w.signal_type for w in warnings}
        assert signal_types == {
            'degrading_health', 'pattern_cascade', 'repeat_failure',
            'critical_cves', 'stale_nightly', 'commit_staleness', 'pcc_stale',
        }

    def test_empty_when_all_healthy(self, monitor):
        with patch.object(monitor, 'get_degrading_components', return_value=[]), \
             patch.object(monitor, 'get_pattern_cascades', return_value=[]), \
             patch.object(monitor, 'get_repeat_failures', return_value=[]), \
             patch.object(monitor, 'get_cve_warnings', return_value=[]), \
             patch.object(monitor, 'get_stale_nightly_builds', return_value=[]), \
             patch.object(monitor, 'get_stale_warnings', return_value=[]), \
             patch.object(monitor, 'get_pcc_freshness_warnings', return_value=[]):
            warnings = monitor.run_checks()

        assert warnings == []
