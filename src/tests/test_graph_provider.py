"""Tests for GraphProvider abstraction layer."""
import os
import sys
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))



class TestCircuitBreaker:
    def test_starts_closed(self):
        from knowledge.graph_provider import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=300)
        assert cb.allow_request() is True

    def test_opens_after_threshold_failures(self):
        from knowledge.graph_provider import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=300)
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is True
        cb.record_failure()
        assert cb.allow_request() is False

    def test_half_open_after_cooldown(self):
        from knowledge.graph_provider import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is False
        time.sleep(1.1)
        assert cb.allow_request() is True  # HALF_OPEN: allows one probe

    def test_closes_on_success_after_half_open(self):
        from knowledge.graph_provider import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        time.sleep(1.1)
        cb.allow_request()  # enters HALF_OPEN
        cb.record_success()
        assert cb.allow_request() is True  # back to CLOSED

    def test_reopens_on_failure_in_half_open(self):
        from knowledge.graph_provider import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        time.sleep(1.1)
        cb.allow_request()  # enters HALF_OPEN
        cb.record_failure()
        assert cb.allow_request() is False  # back to OPEN

    def test_success_resets_failure_count(self):
        from knowledge.graph_provider import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=300)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is True  # only 2 consecutive, not 3


class TestDirectGraphProvider:
    def test_available_false_when_neo4j_not_configured(self):
        from knowledge.graph_provider import DirectGraphProvider
        with patch.dict(os.environ, {}, clear=True):
            provider = DirectGraphProvider.__new__(DirectGraphProvider)
            provider._breaker = MagicMock()
            provider._breaker.allow_request.return_value = True
            # No SLK_NEO4J_PASSWORD → driver returns None
            assert provider.available() is False

    def test_build_context_returns_empty_when_circuit_open(self):
        from knowledge.graph_provider import DirectGraphProvider
        provider = DirectGraphProvider.__new__(DirectGraphProvider)
        provider._breaker = MagicMock()
        provider._breaker.allow_request.return_value = False
        result = provider.build_context({"component_name": "test"})
        assert result == ""

    def test_build_context_delegates_to_graph_context(self):
        from knowledge.graph_provider import DirectGraphProvider
        provider = DirectGraphProvider.__new__(DirectGraphProvider)
        provider._breaker = MagicMock()
        provider._breaker.allow_request.return_value = True
        with patch('knowledge.graph_context.build_context', return_value="graph data"):
            result = provider.build_context({"component_name": "test"})
        assert result == "graph data"
        provider._breaker.record_success.assert_called_once()

    def test_build_context_records_failure_on_exception(self):
        from knowledge.graph_provider import DirectGraphProvider
        provider = DirectGraphProvider.__new__(DirectGraphProvider)
        provider._breaker = MagicMock()
        provider._breaker.allow_request.return_value = True
        with patch('knowledge.graph_context.build_context', side_effect=Exception("timeout")):
            result = provider.build_context({"component_name": "test"})
        assert result == ""
        provider._breaker.record_failure.assert_called_once()


class TestGetProvider:
    def test_default_returns_direct(self):
        from knowledge.graph_provider import DirectGraphProvider, get_provider
        provider = get_provider()
        assert isinstance(provider, DirectGraphProvider)

    def test_env_var_selects_provider(self):
        from knowledge.graph_provider import DirectGraphProvider, get_provider
        with patch.dict(os.environ, {"GRAPH_PROVIDER": "direct"}):
            provider = get_provider()
        assert isinstance(provider, DirectGraphProvider)
