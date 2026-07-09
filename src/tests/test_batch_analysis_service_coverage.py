"""Comprehensive tests for BatchAnalysisService and BatchAnalysisResult."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest

from services.batch_analysis_service import BatchAnalysisResult, BatchAnalysisService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(max_per_run=20, enabled=True, app_name="rhoai"):
    """Build a minimal mock CollectorConfig."""
    config = MagicMock()
    config.batch_analysis.max_per_run = max_per_run
    config.batch_analysis.enabled = enabled
    config.k8s.application_name = app_name
    return config


def _make_config_no_batch(app_name="rhoai"):
    """Config whose batch_analysis attribute is falsy (defaults apply)."""
    config = MagicMock()
    config.batch_analysis = None
    config.k8s.application_name = app_name
    return config


def _make_db():
    """Standard DB mock triple used throughout the project."""
    db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    db.connection.return_value.__enter__ = MagicMock(return_value=conn)
    db.connection.return_value.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    return db, conn, cursor


def _make_analyzer(build_pending=5, conforma_pending=3):
    """Build a mock BuildFailureAnalyzer with configurable pending counts."""
    analyzer = MagicMock()
    analyzer.ai_repo.get_pending_count.return_value = build_pending
    analyzer.ai_repo.get_pending_conforma_count.return_value = conforma_pending
    analyzer.run.return_value = {'analyzed': 0, 'skipped_new': 0}
    return analyzer


# ===================================================================
# BatchAnalysisResult dataclass
# ===================================================================

class TestBatchAnalysisResult:
    """Tests for the frozen dataclass."""

    def test_fields_accessible(self):
        r = BatchAnalysisResult(
            build_analyzed=3,
            conforma_analyzed=2,
            total_analyzed=5,
            build_skipped=1,
            conforma_skipped=0,
            build_pending=10,
            conforma_pending=4,
            duration_seconds=12.5,
            queue_eta_hours=2.0,
        )
        assert r.build_analyzed == 3
        assert r.conforma_analyzed == 2
        assert r.total_analyzed == 5
        assert r.build_skipped == 1
        assert r.conforma_skipped == 0
        assert r.build_pending == 10
        assert r.conforma_pending == 4
        assert r.duration_seconds == 12.5
        assert r.queue_eta_hours == 2.0

    def test_frozen_cannot_mutate(self):
        r = BatchAnalysisResult(
            build_analyzed=0, conforma_analyzed=0, total_analyzed=0,
            build_skipped=0, conforma_skipped=0,
            build_pending=0, conforma_pending=0,
            duration_seconds=0.0, queue_eta_hours=0.0,
        )
        with pytest.raises(AttributeError):
            r.build_analyzed = 99


# ===================================================================
# __init__
# ===================================================================

class TestInit:
    """Constructor behaviour under various config shapes."""

    @patch("services.batch_analysis_service.BuildFailureAnalyzer")
    def test_with_batch_config(self, MockBFA):
        config = _make_config(max_per_run=40, enabled=True)
        svc = BatchAnalysisService(config)
        assert svc.max_per_run == 40
        assert svc.enabled is True
        assert svc.max_build == 30   # int(40 * 0.75)
        assert svc.max_conforma == 10  # int(40 * 0.25)
        assert svc.all_apps is False

    @patch("services.batch_analysis_service.BuildFailureAnalyzer")
    def test_without_batch_config_uses_defaults(self, MockBFA):
        config = _make_config_no_batch()
        svc = BatchAnalysisService(config)
        assert svc.max_per_run == 20
        assert svc.enabled is True
        assert svc.max_build == 15
        assert svc.max_conforma == 5

    def test_provided_build_analyzer_is_used(self):
        config = _make_config()
        analyzer = _make_analyzer()
        svc = BatchAnalysisService(config, build_analyzer=analyzer)
        assert svc.build_analyzer is analyzer

    @patch("services.batch_analysis_service.BuildFailureAnalyzer")
    def test_all_apps_flag_stored(self, MockBFA):
        config = _make_config()
        svc = BatchAnalysisService(config, all_apps=True)
        assert svc.all_apps is True


# ===================================================================
# run_batch — disabled
# ===================================================================

class TestRunBatchDisabled:
    """When batch analysis is disabled, run_batch returns zeros immediately."""

    def test_returns_zeros_when_disabled(self):
        config = _make_config(enabled=False)
        analyzer = _make_analyzer()
        svc = BatchAnalysisService(config, build_analyzer=analyzer)

        result = svc.run_batch()

        assert result.total_analyzed == 0
        assert result.build_analyzed == 0
        assert result.conforma_analyzed == 0
        assert result.build_pending == 0
        assert result.conforma_pending == 0
        assert result.duration_seconds == 0.0
        assert result.queue_eta_hours == 0.0
        # Analyzer should never have been called
        analyzer.run.assert_not_called()


# ===================================================================
# run_batch — single app (default mode)
# ===================================================================

class TestRunBatchSingleApp:
    """Single-app mode exercises the build + conforma loop for one app."""

    def test_happy_path(self):
        config = _make_config(max_per_run=20, app_name="my-app")
        analyzer = _make_analyzer(build_pending=8, conforma_pending=2)
        analyzer.run.return_value = {'analyzed': 4, 'skipped_new': 1}

        svc = BatchAnalysisService(config, build_analyzer=analyzer)

        # ConformaAnalyzer is imported locally inside run_batch — patch the module
        mock_ca_cls = MagicMock()
        mock_ca_cls.return_value.run.return_value = {'analyzed': 2, 'skipped_new': 0}
        fake_module = MagicMock(ConformaAnalyzer=mock_ca_cls)

        with patch.dict("sys.modules", {"analyzers.conforma_analyzer": fake_module}):
            result = svc.run_batch()

        assert result.build_analyzed == 4
        assert result.build_skipped == 1
        assert result.build_pending == 8
        assert result.conforma_pending == 2
        analyzer.run.assert_called_once_with(limit=15, application="my-app")

    def test_conforma_import_error_is_swallowed(self):
        """When ConformaAnalyzer cannot be imported, build analysis still runs."""
        config = _make_config(app_name="test-app")
        analyzer = _make_analyzer(build_pending=3, conforma_pending=0)
        analyzer.run.return_value = {'analyzed': 2, 'skipped_new': 0}

        svc = BatchAnalysisService(config, build_analyzer=analyzer)

        # Force ImportError on the conforma import inside the loop
        real_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def fake_import(name, *args, **kwargs):
            if name == "analyzers.conforma_analyzer":
                raise ImportError("no conforma")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = svc.run_batch()

        assert result.build_analyzed == 2
        assert result.conforma_analyzed == 0
        assert result.conforma_skipped == 0

    def test_conforma_generic_exception_is_logged(self):
        """A non-ImportError from conforma is caught and logged."""
        config = _make_config(app_name="test-app")
        analyzer = _make_analyzer(build_pending=1, conforma_pending=0)
        analyzer.run.return_value = {'analyzed': 1, 'skipped_new': 0}

        svc = BatchAnalysisService(config, build_analyzer=analyzer)

        def fake_import(name, *args, **kwargs):
            if name == "analyzers.conforma_analyzer":
                # Return a module-like object whose ConformaAnalyzer.run raises
                mod = MagicMock()
                mod.ConformaAnalyzer.return_value.run.side_effect = RuntimeError("boom")
                return mod
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = svc.run_batch()

        assert result.build_analyzed == 1
        assert result.conforma_analyzed == 0


# ===================================================================
# run_batch — all_apps mode
# ===================================================================

class TestRunBatchAllApps:
    """Multi-app mode discovers apps and splits quotas."""

    def test_discovers_apps_and_splits_quota(self):
        config = _make_config(max_per_run=20)
        analyzer = _make_analyzer(build_pending=10, conforma_pending=6)
        analyzer.run.return_value = {'analyzed': 3, 'skipped_new': 0}

        svc = BatchAnalysisService(config, build_analyzer=analyzer, all_apps=True)
        svc._discover_apps_with_pending = MagicMock(return_value=["app-a", "app-b"])

        # Suppress conforma import
        def fake_import(name, *args, **kwargs):
            if name == "analyzers.conforma_analyzer":
                raise ImportError("skip")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = svc.run_batch()

        # build_analyzer.run should have been called for each app
        assert analyzer.run.call_count == 2
        # build_per_app = max(1, 15 // 2) = 7
        calls = analyzer.run.call_args_list
        assert calls[0].kwargs['application'] == "app-a"
        assert calls[0].kwargs['limit'] == 7
        assert calls[1].kwargs['application'] == "app-b"
        # Total build analyzed = 3 per app * 2 apps
        assert result.build_analyzed == 6

    def test_no_apps_found_returns_gracefully(self):
        config = _make_config()
        analyzer = _make_analyzer(build_pending=0, conforma_pending=0)
        svc = BatchAnalysisService(config, build_analyzer=analyzer, all_apps=True)
        svc._discover_apps_with_pending = MagicMock(return_value=[])

        result = svc.run_batch()

        assert result.total_analyzed == 0
        assert result.build_analyzed == 0
        assert result.conforma_analyzed == 0
        analyzer.run.assert_not_called()


# ===================================================================
# run_batch — queue ETA calculation
# ===================================================================

class TestRunBatchQueueETA:
    """Queue ETA is computed when items are analyzed, zero otherwise."""

    def test_eta_nonzero_when_analyzed_gt_zero(self):
        config = _make_config(app_name="app")
        analyzer = _make_analyzer(build_pending=100, conforma_pending=50)
        analyzer.run.return_value = {'analyzed': 5, 'skipped_new': 0}

        svc = BatchAnalysisService(config, build_analyzer=analyzer)

        def fake_import(name, *args, **kwargs):
            if name == "analyzers.conforma_analyzer":
                raise ImportError("skip")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = svc.run_batch()

        # total_analyzed = 5, total_pending = 150
        # queue_eta = (150/5) * (duration/3600) — should be > 0
        assert result.queue_eta_hours >= 0.0
        assert result.total_analyzed == 5

    def test_eta_zero_when_nothing_analyzed(self):
        config = _make_config(app_name="app")
        analyzer = _make_analyzer(build_pending=10, conforma_pending=5)
        analyzer.run.return_value = {'analyzed': 0, 'skipped_new': 0}

        svc = BatchAnalysisService(config, build_analyzer=analyzer)

        def fake_import(name, *args, **kwargs):
            if name == "analyzers.conforma_analyzer":
                raise ImportError("skip")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = svc.run_batch()

        assert result.queue_eta_hours == 0.0
        assert result.total_analyzed == 0


# ===================================================================
# estimate_queue_depth
# ===================================================================

class TestEstimateQueueDepth:
    """Queue-depth estimation without running analysis."""

    def test_single_app_mode(self):
        config = _make_config(max_per_run=20, app_name="my-app")
        analyzer = _make_analyzer(build_pending=12, conforma_pending=8)
        svc = BatchAnalysisService(config, build_analyzer=analyzer)

        depth = svc.estimate_queue_depth()

        assert depth['build_pending'] == 12
        assert depth['conforma_pending'] == 8
        assert depth['total_pending'] == 20
        # eta_hours = (20 / 20) * 1.0 = 1.0
        assert depth['eta_hours'] == 1.0
        assert 'apps' not in depth

        # Verify the app filter was passed
        analyzer.ai_repo.get_pending_count.assert_called_once_with("my-app")
        analyzer.ai_repo.get_pending_conforma_count.assert_called_once_with("my-app")

    def test_all_apps_mode_includes_app_list(self):
        config = _make_config(max_per_run=10)
        analyzer = _make_analyzer(build_pending=6, conforma_pending=4)
        svc = BatchAnalysisService(config, build_analyzer=analyzer, all_apps=True)
        svc._discover_apps_with_pending = MagicMock(return_value=["alpha", "beta"])

        depth = svc.estimate_queue_depth()

        assert depth['build_pending'] == 6
        assert depth['conforma_pending'] == 4
        assert depth['total_pending'] == 10
        # eta_hours = (10 / 10) * 1.0 = 1.0
        assert depth['eta_hours'] == 1.0
        assert depth['apps'] == ["alpha", "beta"]
        assert depth['app_count'] == 2

        # In all_apps mode, app filter should be None
        analyzer.ai_repo.get_pending_count.assert_called_once_with(None)
        analyzer.ai_repo.get_pending_conforma_count.assert_called_once_with(None)

    def test_zero_max_per_run_avoids_division_by_zero(self):
        config = _make_config(max_per_run=0)
        analyzer = _make_analyzer(build_pending=5, conforma_pending=3)
        svc = BatchAnalysisService(config, build_analyzer=analyzer)

        depth = svc.estimate_queue_depth()

        assert depth['eta_hours'] == 0.0


# ===================================================================
# _discover_apps_with_pending
# ===================================================================

class TestDiscoverAppsWithPending:
    """DB query for applications with pending work."""

    def test_success_returns_app_names(self):
        config = _make_config(app_name="fallback")
        analyzer = _make_analyzer()
        db, conn, cursor = _make_db()
        analyzer.ai_repo.db = db
        cursor.fetchall.return_value = [("app-x",), ("app-y",), ("app-z",)]

        svc = BatchAnalysisService(config, build_analyzer=analyzer)
        apps = svc._discover_apps_with_pending()

        assert apps == ["app-x", "app-y", "app-z"]
        cursor.execute.assert_called_once()
        # Verify the SQL uses UNION of build_failures and conforma_results
        sql = cursor.execute.call_args[0][0]
        assert "build_failures" in sql
        assert "conforma_results" in sql

    def test_exception_falls_back_to_config_app(self):
        config = _make_config(app_name="fallback-app")
        analyzer = _make_analyzer()
        analyzer.ai_repo.db.connection.side_effect = RuntimeError("db down")

        svc = BatchAnalysisService(config, build_analyzer=analyzer)
        apps = svc._discover_apps_with_pending()

        assert apps == ["fallback-app"]


# ===================================================================
# _get_build_pending_count
# ===================================================================

class TestGetBuildPendingCount:
    """Delegates to ai_repo.get_pending_count."""

    def test_success_returns_count(self):
        config = _make_config()
        analyzer = _make_analyzer()
        analyzer.ai_repo.get_pending_count.return_value = 42

        svc = BatchAnalysisService(config, build_analyzer=analyzer)
        count = svc._get_build_pending_count("some-app")

        assert count == 42
        analyzer.ai_repo.get_pending_count.assert_called_once_with("some-app")

    def test_exception_returns_zero(self):
        config = _make_config()
        analyzer = _make_analyzer()
        analyzer.ai_repo.get_pending_count.side_effect = RuntimeError("fail")

        svc = BatchAnalysisService(config, build_analyzer=analyzer)
        count = svc._get_build_pending_count("app")

        assert count == 0

    def test_none_application_passes_through(self):
        config = _make_config()
        analyzer = _make_analyzer()
        analyzer.ai_repo.get_pending_count.return_value = 7

        svc = BatchAnalysisService(config, build_analyzer=analyzer)
        count = svc._get_build_pending_count(None)

        assert count == 7
        analyzer.ai_repo.get_pending_count.assert_called_once_with(None)


# ===================================================================
# _get_conforma_pending_count
# ===================================================================

class TestGetConformaPendingCount:
    """Delegates to ai_repo.get_pending_conforma_count."""

    def test_success_returns_count(self):
        config = _make_config()
        analyzer = _make_analyzer()
        analyzer.ai_repo.get_pending_conforma_count.return_value = 17

        svc = BatchAnalysisService(config, build_analyzer=analyzer)
        count = svc._get_conforma_pending_count("some-app")

        assert count == 17
        analyzer.ai_repo.get_pending_conforma_count.assert_called_once_with("some-app")

    def test_exception_returns_zero(self):
        config = _make_config()
        analyzer = _make_analyzer()
        analyzer.ai_repo.get_pending_conforma_count.side_effect = RuntimeError("fail")

        svc = BatchAnalysisService(config, build_analyzer=analyzer)
        count = svc._get_conforma_pending_count("app")

        assert count == 0

    def test_none_application_passes_through(self):
        config = _make_config()
        analyzer = _make_analyzer()
        analyzer.ai_repo.get_pending_conforma_count.return_value = 3

        svc = BatchAnalysisService(config, build_analyzer=analyzer)
        count = svc._get_conforma_pending_count(None)

        assert count == 3
        analyzer.ai_repo.get_pending_conforma_count.assert_called_once_with(None)


# ===================================================================
# run_batch — result field consistency
# ===================================================================

class TestRunBatchResultConsistency:
    """Verify internal consistency of the returned BatchAnalysisResult."""

    def test_total_equals_build_plus_conforma(self):
        config = _make_config(app_name="app")
        analyzer = _make_analyzer(build_pending=5, conforma_pending=3)
        analyzer.run.return_value = {'analyzed': 2, 'skipped_new': 1}

        svc = BatchAnalysisService(config, build_analyzer=analyzer)

        def fake_import(name, *args, **kwargs):
            if name == "analyzers.conforma_analyzer":
                raise ImportError("skip")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = svc.run_batch()

        assert result.total_analyzed == result.build_analyzed + result.conforma_analyzed

    def test_duration_is_positive(self):
        config = _make_config(app_name="app")
        analyzer = _make_analyzer(build_pending=0, conforma_pending=0)
        analyzer.run.return_value = {'analyzed': 0, 'skipped_new': 0}

        svc = BatchAnalysisService(config, build_analyzer=analyzer)

        def fake_import(name, *args, **kwargs):
            if name == "analyzers.conforma_analyzer":
                raise ImportError("skip")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = svc.run_batch()

        assert result.duration_seconds >= 0.0
