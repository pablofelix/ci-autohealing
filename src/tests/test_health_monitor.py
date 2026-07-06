"""Tests for HealthMonitor stale nightly build detection."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from proactive.health_monitor import HealthMonitor, _nightly_component_for_app


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def monitor(mock_db):
    return HealthMonitor(mock_db)


def _setup_cursor(mock_db, rows, cols=None):
    if cols is None:
        cols = ['component_name', 'application', 'last_successful_build',
                'last_failed_build', 'current_status']
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.description = [(c,) for c in cols]
    mock_cursor.fetchall.return_value = rows
    mock_conn.cursor.return_value = mock_cursor
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
    return mock_cursor


class TestNightlyComponentDerivation:
    def test_standard_app(self):
        assert _nightly_component_for_app('rhoai-v3-5-ea-2') == 'rhoai-fbc-fragment-v3-5-ea-2'

    def test_simple_version(self):
        assert _nightly_component_for_app('rhoai-v3-6') == 'rhoai-fbc-fragment-v3-6'

    def test_no_version_suffix(self):
        assert _nightly_component_for_app('myapp') == 'myapp-fbc-fragment'

    def test_different_prefix(self):
        assert _nightly_component_for_app('odh-v2-0') == 'odh-fbc-fragment-v2-0'


class TestStaleNightlyBuilds:
    def test_stale_build_returns_warning(self, monitor, mock_db):
        stale_time = datetime.now(UTC) - timedelta(hours=30)
        _setup_cursor(mock_db, [
            ('rhoai-fbc-fragment-v3-5-ea-2', 'rhoai-v3-5-ea-2',
             stale_time, None, 'healthy'),
        ])
        warnings = monitor.get_stale_nightly_builds()
        assert len(warnings) == 1
        assert warnings[0].signal_type == 'stale_nightly'
        assert warnings[0].severity == 'warning'
        assert '30h ago' in warnings[0].message

    def test_fresh_build_no_warning(self, monitor, mock_db):
        _setup_cursor(mock_db, [])
        warnings = monitor.get_stale_nightly_builds()
        assert len(warnings) == 0

    def test_no_build_ever_returns_critical(self, monitor, mock_db):
        _setup_cursor(mock_db, [
            ('rhoai-fbc-fragment-v3-5-ea-2', 'rhoai-v3-5-ea-2',
             None, None, 'unknown'),
        ])
        warnings = monitor.get_stale_nightly_builds()
        assert len(warnings) == 1
        assert warnings[0].severity == 'critical'
        assert 'no successful build on record' in warnings[0].message

    def test_very_stale_is_critical(self, monitor, mock_db):
        stale_time = datetime.now(UTC) - timedelta(hours=50)
        _setup_cursor(mock_db, [
            ('rhoai-fbc-fragment-v3-5-ea-2', 'rhoai-v3-5-ea-2',
             stale_time, None, 'failing'),
        ])
        warnings = monitor.get_stale_nightly_builds()
        assert len(warnings) == 1
        assert warnings[0].severity == 'critical'

    @patch.dict('os.environ', {'NIGHTLY_STALENESS_HOURS': '36'})
    def test_custom_threshold(self, monitor, mock_db):
        cursor = _setup_cursor(mock_db, [])
        monitor.get_stale_nightly_builds()
        query = cursor.execute.call_args[0][0]
        assert "INTERVAL" in query
        assert cursor.execute.call_args[0][1] == (36,)

    def test_non_fbc_components_not_returned(self, monitor, mock_db):
        _setup_cursor(mock_db, [])
        warnings = monitor.get_stale_nightly_builds()
        assert len(warnings) == 0
        cursor_call = mock_db.connection().__enter__().cursor()
        query = cursor_call.execute.call_args[0][0]
        assert 'fbc-fragment' in query

    def test_naive_datetime_handled(self, monitor, mock_db):
        stale_time = datetime.now() - timedelta(hours=30)
        _setup_cursor(mock_db, [
            ('rhoai-fbc-fragment-v3-5-ea-2', 'rhoai-v3-5-ea-2',
             stale_time, None, 'healthy'),
        ])
        warnings = monitor.get_stale_nightly_builds()
        assert len(warnings) == 1
        assert warnings[0].severity == 'warning'

    def test_evidence_contains_row_data(self, monitor, mock_db):
        stale_time = datetime.now(UTC) - timedelta(hours=30)
        _setup_cursor(mock_db, [
            ('rhoai-fbc-fragment-v3-5-ea-2', 'rhoai-v3-5-ea-2',
             stale_time, None, 'healthy'),
        ])
        warnings = monitor.get_stale_nightly_builds()
        assert warnings[0].evidence['component_name'] == 'rhoai-fbc-fragment-v3-5-ea-2'
        assert warnings[0].evidence['application'] == 'rhoai-v3-5-ea-2'

    def test_multiple_stale_components(self, monitor, mock_db):
        stale_time = datetime.now(UTC) - timedelta(hours=30)
        _setup_cursor(mock_db, [
            ('rhoai-fbc-fragment-v3-5-ea-2', 'rhoai-v3-5-ea-2',
             stale_time, None, 'healthy'),
            ('odh-fbc-fragment-ci', 'odh-v2-0',
             None, None, 'unknown'),
        ])
        warnings = monitor.get_stale_nightly_builds()
        assert len(warnings) == 2
        severities = {w.severity for w in warnings}
        assert 'warning' in severities
        assert 'critical' in severities
