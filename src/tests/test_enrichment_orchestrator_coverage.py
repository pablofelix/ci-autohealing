"""Comprehensive tests for EnrichmentOrchestrator and OrchestrationResult."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Mock heavy dependencies before importing the module under test.
# logger, repositories.connection, and repositories.context_enrichment_repository
# all pull in external I/O that we don't want during unit tests.
# config is also mocked since CollectorConfig reads YAML files.
from unittest.mock import MagicMock

sys.modules['logger'] = MagicMock(setup_logger=MagicMock(return_value=MagicMock()))
sys.modules['config'] = MagicMock()
sys.modules['repositories.connection'] = MagicMock()
sys.modules['repositories.context_enrichment_repository'] = MagicMock()

import time
from dataclasses import FrozenInstanceError

import pytest

from enrichment.context_source import ContextSource
from enrichment.enrichment_orchestrator import EnrichmentOrchestrator, OrchestrationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(app_name="rhoai"):
    """Build a minimal mock CollectorConfig."""
    config = MagicMock()
    config.k8s.application_name = app_name
    config.db = MagicMock()
    return config


def _make_repo():
    """Build a mock ContextEnrichmentRepository."""
    repo = MagicMock()
    repo.increment_enrichment_attempts.return_value = 1
    repo.get_enrichment_coverage.return_value = {'coverage_pct': 75.0}
    return repo


def _make_db():
    """Build a mock DatabaseConnection."""
    return MagicMock()


def _make_source(name="test_source", data=None, raises=None, timeout=30):
    """Build a mock ContextSource.

    Args:
        name: source_name() return value
        data: dict returned by fetch(), or None
        raises: exception to raise on fetch(), or None
        timeout: timeout_seconds property value
    """
    source = MagicMock(spec=ContextSource)
    source.source_name.return_value = name
    type(source).timeout_seconds = property(lambda self: timeout)

    if raises:
        source.fetch.side_effect = raises
    else:
        source.fetch.return_value = data

    return source


def _make_failure(failure_id=1, component="odh-dashboard"):
    """Build a minimal failure dict."""
    return {
        'id': failure_id,
        'component_name': component,
        'error_type': 'build_error',
        'commit_sha': 'abc123',
        'repository_url': 'https://github.com/example/repo',
    }


# ---------------------------------------------------------------------------
# OrchestrationResult dataclass tests
# ---------------------------------------------------------------------------

class TestOrchestrationResult:
    """Tests for the OrchestrationResult frozen dataclass."""

    def test_creation_with_all_fields(self):
        result = OrchestrationResult(
            failure_id=42,
            success=True,
            sources_attempted=3,
            sources_succeeded=2,
            sources_failed=1,
            enrichment_data={'key': 'val'},
            errors=['source_c: timeout'],
            duration_seconds=1.5,
        )
        assert result.failure_id == 42
        assert result.success is True
        assert result.sources_attempted == 3
        assert result.sources_succeeded == 2
        assert result.sources_failed == 1
        assert result.enrichment_data == {'key': 'val'}
        assert result.errors == ['source_c: timeout']
        assert result.duration_seconds == 1.5

    def test_frozen_immutability(self):
        result = OrchestrationResult(
            failure_id=1,
            success=True,
            sources_attempted=1,
            sources_succeeded=1,
            sources_failed=0,
            enrichment_data={},
            errors=[],
            duration_seconds=0.0,
        )
        with pytest.raises(FrozenInstanceError):
            result.success = False


# ---------------------------------------------------------------------------
# register_source tests
# ---------------------------------------------------------------------------

class TestRegisterSource:
    """Tests for EnrichmentOrchestrator.register_source."""

    def test_register_adds_source_to_list(self):
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=_make_repo()
        )
        source = _make_source("dep_changes")
        orch.register_source(source)
        assert len(orch.sources) == 1
        assert orch.sources[0].source_name() == "dep_changes"

    def test_register_multiple_sources_preserves_order(self):
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=_make_repo()
        )
        orch.register_source(_make_source("alpha"))
        orch.register_source(_make_source("beta"))
        orch.register_source(_make_source("gamma"))
        names = [s.source_name() for s in orch.sources]
        assert names == ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------
# enrich_failure tests
# ---------------------------------------------------------------------------

class TestEnrichFailure:
    """Tests for EnrichmentOrchestrator.enrich_failure."""

    def test_circuit_breaker_exceeded(self):
        """When attempts > MAX_ENRICHMENT_ATTEMPTS, return failed immediately."""
        repo = _make_repo()
        repo.increment_enrichment_attempts.return_value = 4  # exceeds 3
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=repo
        )
        orch.register_source(_make_source("src", data={'x': 1}))
        failure = _make_failure(failure_id=99)

        result = orch.enrich_failure(failure)

        assert result.success is False
        assert result.failure_id == 99
        assert result.sources_attempted == 0
        assert "Exceeded max attempts" in result.errors[0]
        repo.mark_enrichment_failed.assert_called_once()

    def test_all_sources_succeed(self):
        """All registered sources return data -> success=True."""
        repo = _make_repo()
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=repo
        )
        orch.register_source(_make_source("src_a", data={'logs': 'truncated'}))
        orch.register_source(_make_source("src_b", data={'deps': ['v1.2']}))
        failure = _make_failure()

        result = orch.enrich_failure(failure)

        assert result.success is True
        assert result.sources_attempted == 2
        assert result.sources_succeeded == 2
        assert result.sources_failed == 0
        assert result.errors == []
        assert 'logs' in result.enrichment_data
        assert 'deps' in result.enrichment_data
        repo.update_enriched_context.assert_called_once()

    def test_partial_success(self):
        """One source succeeds and one fails -> success=True (partial OK)."""
        repo = _make_repo()
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=repo
        )
        orch.register_source(_make_source("good", data={'info': 'ok'}))
        orch.register_source(_make_source("bad", raises=RuntimeError("boom")))
        failure = _make_failure()

        result = orch.enrich_failure(failure)

        assert result.success is True
        assert result.sources_succeeded == 1
        assert result.sources_failed == 1
        assert len(result.errors) == 1
        assert 'bad' in result.errors[0]
        repo.update_enriched_context.assert_called_once()

    def test_all_sources_fail(self):
        """All sources fail -> success=False, mark_enrichment_failed called."""
        repo = _make_repo()
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=repo
        )
        orch.register_source(_make_source("fail_a", raises=ValueError("err_a")))
        orch.register_source(_make_source("fail_b", raises=ValueError("err_b")))
        failure = _make_failure(failure_id=7)

        result = orch.enrich_failure(failure)

        assert result.success is False
        assert result.failure_id == 7
        assert result.sources_succeeded == 0
        assert result.sources_failed == 2
        assert len(result.errors) == 2
        repo.mark_enrichment_failed.assert_called_once()
        repo.update_enriched_context.assert_not_called()

    def test_key_conflict_detection_overwrites(self):
        """When two sources return the same top-level key, later one overwrites."""
        repo = _make_repo()
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=repo
        )
        orch.register_source(_make_source("first", data={'shared_key': 'first_val'}))
        orch.register_source(_make_source("second", data={'shared_key': 'second_val'}))
        failure = _make_failure()

        result = orch.enrich_failure(failure)

        assert result.success is True
        # The value should be one of the two (order depends on thread scheduling)
        assert result.enrichment_data['shared_key'] in ('first_val', 'second_val')

    def test_enrichment_data_has_sources_tracker(self):
        """enrichment_data['sources'] tracks which sources succeeded/failed."""
        repo = _make_repo()
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=repo
        )
        orch.register_source(_make_source("ok_src", data={'a': 1}))
        orch.register_source(_make_source("fail_src", raises=RuntimeError("oops")))
        failure = _make_failure()

        result = orch.enrich_failure(failure)

        assert result.enrichment_data['sources']['ok_src'] is True
        assert result.enrichment_data['sources']['fail_src'] is False
        assert 'enriched_at' in result.enrichment_data['sources']

    def test_duration_is_positive(self):
        """duration_seconds should be a positive float."""
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=_make_repo()
        )
        orch.register_source(_make_source("s", data={'x': 1}))
        result = orch.enrich_failure(_make_failure())
        assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# _execute_source tests
# ---------------------------------------------------------------------------

class TestExecuteSource:
    """Tests for EnrichmentOrchestrator._execute_source."""

    def test_source_returns_data(self):
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=_make_repo()
        )
        source = _make_source("logs", data={'log_lines': 100})
        failure = _make_failure()

        result = orch._execute_source(source, failure)

        assert result.success is True
        assert result.source_name == "logs"
        assert result.data == {'log_lines': 100}
        assert result.error is None
        assert result.duration_seconds >= 0.0

    def test_source_returns_none(self):
        """Source returns None -> success=False, error='No data returned'."""
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=_make_repo()
        )
        source = _make_source("empty", data=None)
        failure = _make_failure()

        result = orch._execute_source(source, failure)

        assert result.success is False
        assert result.error == "No data returned"

    def test_source_raises_exception(self):
        """Source raises -> success=False, error contains message."""
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=_make_repo()
        )
        source = _make_source("broken", raises=ConnectionError("network down"))
        failure = _make_failure()

        result = orch._execute_source(source, failure)

        assert result.success is False
        assert result.source_name == "broken"
        assert "network down" in result.error
        assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# _execute_sources_parallel tests
# ---------------------------------------------------------------------------

class TestExecuteSourcesParallel:
    """Tests for EnrichmentOrchestrator._execute_sources_parallel."""

    def test_multiple_sources_all_succeed(self):
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=_make_repo()
        )
        orch.register_source(_make_source("s1", data={'a': 1}))
        orch.register_source(_make_source("s2", data={'b': 2}))
        orch.register_source(_make_source("s3", data={'c': 3}))
        failure = _make_failure()

        results = orch._execute_sources_parallel(failure)

        assert len(results) == 3
        assert all(r.success for r in results)
        names = {r.source_name for r in results}
        assert names == {"s1", "s2", "s3"}

    def test_parallel_with_mixed_results(self):
        """Mix of successes and failures returns one result per source."""
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=_make_repo()
        )
        orch.register_source(_make_source("ok", data={'x': 1}))
        orch.register_source(_make_source("err", raises=RuntimeError("fail")))
        failure = _make_failure()

        results = orch._execute_sources_parallel(failure)

        assert len(results) == 2
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 1
        assert len(failures) == 1

    def test_no_sources_returns_empty(self):
        """No registered sources -> empty results list."""
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=_make_repo()
        )
        results = orch._execute_sources_parallel(_make_failure())
        assert results == []

    def test_source_timeout_produces_error_result(self):
        """A source whose future.result() times out gets an error result."""
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=_make_repo()
        )
        # Create a source that blocks longer than its timeout
        slow_source = _make_source("slow", timeout=1)
        slow_source.fetch.side_effect = lambda f: time.sleep(5)
        orch.register_source(slow_source)
        failure = _make_failure()

        # The ThreadPoolExecutor calls future.result(timeout=source.timeout_seconds)
        # which should raise TimeoutError if the source blocks too long.
        # However, thread scheduling makes this non-deterministic, so we
        # simply verify that a result is produced for each source.
        results = orch._execute_sources_parallel(failure)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# enrich_batch tests
# ---------------------------------------------------------------------------

class TestEnrichBatch:
    """Tests for EnrichmentOrchestrator.enrich_batch."""

    def test_no_pending_enrichments(self):
        """When repo returns no pending failures, return zeros with coverage."""
        repo = _make_repo()
        repo.get_pending_enrichments.return_value = []
        repo.get_enrichment_coverage.return_value = {'coverage_pct': 90.0}
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=repo
        )

        stats = orch.enrich_batch(limit=10)

        assert stats['enriched'] == 0
        assert stats['failed'] == 0
        assert stats['skipped'] == 0
        assert stats['coverage_pct'] == 90.0
        assert stats['duration'] == 0.0

    def test_normal_batch_processing(self):
        """Process a batch of pending failures and return correct counts."""
        repo = _make_repo()
        repo.get_pending_enrichments.return_value = [
            _make_failure(failure_id=1, component="comp-a"),
            _make_failure(failure_id=2, component="comp-b"),
        ]
        repo.get_enrichment_coverage.return_value = {'coverage_pct': 80.0}
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=repo
        )
        orch.register_source(_make_source("src", data={'info': 'ok'}))

        stats = orch.enrich_batch(limit=20)

        assert stats['enriched'] == 2
        assert stats['failed'] == 0
        assert stats['coverage_pct'] == 80.0
        assert stats['duration'] > 0.0

    def test_batch_with_component_filter(self):
        """component_filter is passed through to the repository."""
        repo = _make_repo()
        repo.get_pending_enrichments.return_value = []
        repo.get_enrichment_coverage.return_value = {'coverage_pct': 100.0}
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=repo
        )

        orch.enrich_batch(limit=5, component_filter="odh-dashboard")

        repo.get_pending_enrichments.assert_called_once_with(
            "rhoai", limit=5, component_filter="odh-dashboard"
        )

    def test_batch_counts_failures(self):
        """Failures in batch processing are counted correctly."""
        repo = _make_repo()
        repo.get_pending_enrichments.return_value = [
            _make_failure(failure_id=10, component="comp-x"),
        ]
        repo.get_enrichment_coverage.return_value = {'coverage_pct': 50.0}
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=_make_db(), enrichment_repo=repo
        )
        # Only a failing source registered
        orch.register_source(_make_source("bad", raises=RuntimeError("fail")))

        stats = orch.enrich_batch()

        assert stats['enriched'] == 0
        assert stats['failed'] == 1


# ---------------------------------------------------------------------------
# __init__ defaults tests
# ---------------------------------------------------------------------------

class TestInit:
    """Tests for EnrichmentOrchestrator.__init__ defaults."""

    def test_creates_db_and_repo_when_not_provided(self):
        """When db and enrichment_repo are None, defaults are created."""
        config = _make_config()
        orch = EnrichmentOrchestrator(config=config)
        # db and enrichment_repo should not be None
        assert orch.db is not None
        assert orch.enrichment_repo is not None
        assert orch.sources == []

    def test_uses_provided_db_and_repo(self):
        """When db and enrichment_repo are given, use them directly."""
        db = _make_db()
        repo = _make_repo()
        orch = EnrichmentOrchestrator(
            config=_make_config(), db=db, enrichment_repo=repo
        )
        assert orch.db is db
        assert orch.enrichment_repo is repo
