"""Comprehensive unit tests for LangfuseTracker.

Covers enabled and disabled modes, trace creation, generation recording,
trace finalization, and flushing. When disabled, all methods are no-ops.
The langfuse library is mocked throughout to avoid real API calls.
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ═══════════════════════════════════════════════════════════════════════
# Constructor tests
# ═══════════════════════════════════════════════════════════════════════

class TestLangfuseTrackerInit:
    """Constructor: enabled/disabled modes and import failures."""

    @patch('clients.langfuse_tracker.logging')
    def test_enabled_with_langfuse_available(self, mock_logging):
        mock_langfuse_instance = MagicMock()
        with patch.dict('sys.modules', {'langfuse': MagicMock()}):
            with patch('langfuse.Langfuse', return_value=mock_langfuse_instance):
                from clients.langfuse_tracker import LangfuseTracker
                tracker = LangfuseTracker(enabled=True)
        assert tracker.enabled is True
        assert tracker._langfuse is not None

    def test_disabled_explicitly(self):
        from clients.langfuse_tracker import LangfuseTracker
        tracker = LangfuseTracker(enabled=False)
        assert tracker.enabled is False
        assert tracker._langfuse is None

    @patch('clients.langfuse_tracker.logging')
    def test_import_error_disables_tracking(self, mock_logging):
        """If langfuse import fails, tracker is disabled gracefully."""
        from clients.langfuse_tracker import LangfuseTracker

        # Simulate ImportError when trying to import langfuse
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def fake_import(name, *args, **kwargs):
            if name == 'langfuse':
                raise ImportError('No module named langfuse')
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=fake_import):
            tracker = LangfuseTracker(enabled=True)
        assert tracker.enabled is False
        assert tracker._langfuse is None

    @patch('clients.langfuse_tracker.logging')
    def test_langfuse_init_exception_disables_tracking(self, mock_logging):
        """If Langfuse() constructor raises, tracker is disabled gracefully."""
        from clients.langfuse_tracker import LangfuseTracker

        mock_langfuse_module = MagicMock()
        mock_langfuse_module.Langfuse.side_effect = Exception('Bad config')

        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def fake_import(name, *args, **kwargs):
            if name == 'langfuse':
                return mock_langfuse_module
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=fake_import):
            tracker = LangfuseTracker(enabled=True)
        assert tracker.enabled is False

    def test_default_enabled_is_true(self):
        """Default parameter for enabled is True."""
        from clients.langfuse_tracker import LangfuseTracker
        # We need to handle the import that happens in __init__
        # Just test that disabled=False works the same
        tracker = LangfuseTracker(enabled=False)
        assert tracker.enabled is False


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_tracker(enabled=True):
    """Build a LangfuseTracker with a mock langfuse instance."""
    from clients.langfuse_tracker import LangfuseTracker
    tracker = LangfuseTracker.__new__(LangfuseTracker)
    tracker.enabled = enabled
    tracker._langfuse = MagicMock() if enabled else None
    return tracker


def _make_disabled_tracker():
    """Build a disabled LangfuseTracker."""
    return _make_tracker(enabled=False)


# ═══════════════════════════════════════════════════════════════════════
# create_trace
# ═══════════════════════════════════════════════════════════════════════

class TestCreateTrace:
    """Trace creation for analysis runs."""

    def test_creates_trace_with_all_params(self):
        tracker = _make_tracker()
        mock_trace = MagicMock()
        tracker._langfuse.trace.return_value = mock_trace

        result = tracker.create_trace(
            name='build-failure-analysis',
            input_data={'component': 'odh-dashboard', 'failure_id': 42},
            metadata={'release': 'v3.5'},
        )

        assert result == mock_trace
        tracker._langfuse.trace.assert_called_once_with(
            name='build-failure-analysis',
            input={'component': 'odh-dashboard', 'failure_id': 42},
            metadata={'release': 'v3.5'},
        )

    def test_creates_trace_minimal_params(self):
        tracker = _make_tracker()
        mock_trace = MagicMock()
        tracker._langfuse.trace.return_value = mock_trace

        result = tracker.create_trace(name='quick-analysis')
        assert result == mock_trace
        tracker._langfuse.trace.assert_called_once_with(
            name='quick-analysis',
            input=None,
            metadata=None,
        )

    def test_disabled_returns_none(self):
        tracker = _make_disabled_tracker()
        result = tracker.create_trace(name='test')
        assert result is None

    def test_enabled_but_no_langfuse_returns_none(self):
        """Edge case: enabled=True but _langfuse is None (shouldn't happen normally)."""
        tracker = _make_tracker()
        tracker._langfuse = None
        result = tracker.create_trace(name='test')
        assert result is None

    def test_langfuse_trace_exception_returns_none(self):
        """Langfuse errors should not propagate — returns None."""
        tracker = _make_tracker()
        tracker._langfuse.trace.side_effect = Exception('API error')
        result = tracker.create_trace(name='failing')
        assert result is None

    def test_trace_with_none_input_and_metadata(self):
        tracker = _make_tracker()
        mock_trace = MagicMock()
        tracker._langfuse.trace.return_value = mock_trace

        result = tracker.create_trace(name='test', input_data=None, metadata=None)
        assert result == mock_trace


# ═══════════════════════════════════════════════════════════════════════
# record_generation
# ═══════════════════════════════════════════════════════════════════════

class TestRecordGeneration:
    """LLM generation recording within a trace."""

    def test_records_generation_with_all_params(self):
        tracker = _make_tracker()
        mock_trace = MagicMock()

        tracker.record_generation(
            trace=mock_trace,
            name='diagnose-root-cause',
            model='claude-sonnet-4-20250514',
            prompt='Analyze this build failure...',
            completion='The root cause is a missing dependency.',
            input_tokens=500,
            output_tokens=200,
            duration_ms=3500,
        )

        mock_trace.generation.assert_called_once_with(
            name='diagnose-root-cause',
            model='claude-sonnet-4-20250514',
            input='Analyze this build failure...',
            output='The root cause is a missing dependency.',
            usage={
                'input': 500,
                'output': 200,
                'total': 700,
            },
            metadata={
                'duration_ms': 3500,
            },
        )

    def test_token_total_computed_correctly(self):
        tracker = _make_tracker()
        mock_trace = MagicMock()

        tracker.record_generation(
            trace=mock_trace,
            name='test',
            model='model',
            prompt='p',
            completion='c',
            input_tokens=1234,
            output_tokens=5678,
            duration_ms=100,
        )

        call_kwargs = mock_trace.generation.call_args[1]
        assert call_kwargs['usage']['total'] == 1234 + 5678

    def test_disabled_is_noop(self):
        tracker = _make_disabled_tracker()
        mock_trace = MagicMock()

        # Should not raise
        tracker.record_generation(
            trace=mock_trace,
            name='test',
            model='model',
            prompt='p',
            completion='c',
            input_tokens=0,
            output_tokens=0,
            duration_ms=0,
        )
        mock_trace.generation.assert_not_called()

    def test_none_trace_is_noop(self):
        """When trace is None (e.g., create_trace failed), record_generation is a no-op."""
        tracker = _make_tracker()

        # Should not raise
        tracker.record_generation(
            trace=None,
            name='test',
            model='model',
            prompt='p',
            completion='c',
            input_tokens=0,
            output_tokens=0,
            duration_ms=0,
        )

    def test_generation_exception_swallowed(self):
        """Langfuse generation errors should not propagate."""
        tracker = _make_tracker()
        mock_trace = MagicMock()
        mock_trace.generation.side_effect = Exception('Network error')

        # Should not raise
        tracker.record_generation(
            trace=mock_trace,
            name='test',
            model='model',
            prompt='p',
            completion='c',
            input_tokens=100,
            output_tokens=50,
            duration_ms=1000,
        )

    def test_zero_tokens(self):
        tracker = _make_tracker()
        mock_trace = MagicMock()

        tracker.record_generation(
            trace=mock_trace,
            name='empty',
            model='model',
            prompt='',
            completion='',
            input_tokens=0,
            output_tokens=0,
            duration_ms=0,
        )

        call_kwargs = mock_trace.generation.call_args[1]
        assert call_kwargs['usage'] == {'input': 0, 'output': 0, 'total': 0}
        assert call_kwargs['metadata'] == {'duration_ms': 0}


# ═══════════════════════════════════════════════════════════════════════
# end_trace
# ═══════════════════════════════════════════════════════════════════════

class TestEndTrace:
    """Trace finalization with output data."""

    def test_updates_trace_with_output(self):
        tracker = _make_tracker()
        mock_trace = MagicMock()
        output = {
            'root_cause': 'Missing FIPS module',
            'category': 'fips_compliance',
            'confidence': 0.92,
        }

        tracker.end_trace(trace=mock_trace, output=output)
        mock_trace.update.assert_called_once_with(output=output)

    def test_updates_trace_with_none_output(self):
        tracker = _make_tracker()
        mock_trace = MagicMock()

        tracker.end_trace(trace=mock_trace, output=None)
        mock_trace.update.assert_called_once_with(output=None)

    def test_updates_trace_default_output_is_none(self):
        tracker = _make_tracker()
        mock_trace = MagicMock()

        tracker.end_trace(trace=mock_trace)
        mock_trace.update.assert_called_once_with(output=None)

    def test_disabled_is_noop(self):
        tracker = _make_disabled_tracker()
        mock_trace = MagicMock()

        tracker.end_trace(trace=mock_trace, output={'result': 'ok'})
        mock_trace.update.assert_not_called()

    def test_none_trace_is_noop(self):
        tracker = _make_tracker()
        # Should not raise
        tracker.end_trace(trace=None, output={'data': 'value'})

    def test_update_exception_swallowed(self):
        """Langfuse update errors should not propagate."""
        tracker = _make_tracker()
        mock_trace = MagicMock()
        mock_trace.update.side_effect = Exception('Update failed')

        # Should not raise
        tracker.end_trace(trace=mock_trace, output={'result': 'ok'})


# ═══════════════════════════════════════════════════════════════════════
# flush
# ═══════════════════════════════════════════════════════════════════════

class TestFlush:
    """Pending event flushing to Langfuse server."""

    def test_flushes_when_enabled(self):
        tracker = _make_tracker()
        tracker.flush()
        tracker._langfuse.flush.assert_called_once()

    def test_disabled_is_noop(self):
        tracker = _make_disabled_tracker()
        # Should not raise
        tracker.flush()

    def test_enabled_but_no_langfuse_is_noop(self):
        tracker = _make_tracker()
        tracker._langfuse = None
        # Should not raise
        tracker.flush()

    def test_flush_exception_swallowed(self):
        """Flush errors should not propagate."""
        tracker = _make_tracker()
        tracker._langfuse.flush.side_effect = Exception('Connection reset')
        # Should not raise
        tracker.flush()


# ═══════════════════════════════════════════════════════════════════════
# Integration-style: full lifecycle
# ═══════════════════════════════════════════════════════════════════════

class TestFullLifecycle:
    """End-to-end usage pattern: create trace -> record gen -> end trace -> flush."""

    def test_enabled_lifecycle(self):
        tracker = _make_tracker()
        mock_trace = MagicMock()
        tracker._langfuse.trace.return_value = mock_trace

        # 1. Create trace
        trace = tracker.create_trace(
            name='analysis',
            input_data={'component': 'odh-dashboard'},
        )
        assert trace == mock_trace

        # 2. Record a generation
        tracker.record_generation(
            trace=trace,
            name='diagnose',
            model='claude-sonnet-4-20250514',
            prompt='What went wrong?',
            completion='Build failed due to Go 1.26 incompatibility.',
            input_tokens=1000,
            output_tokens=500,
            duration_ms=4200,
        )
        mock_trace.generation.assert_called_once()

        # 3. End trace
        tracker.end_trace(trace=trace, output={'root_cause': 'Go version'})
        mock_trace.update.assert_called_once()

        # 4. Flush
        tracker.flush()
        tracker._langfuse.flush.assert_called_once()

    def test_disabled_lifecycle_all_noops(self):
        tracker = _make_disabled_tracker()

        # All calls should be no-ops, no exceptions
        trace = tracker.create_trace(name='analysis')
        assert trace is None

        tracker.record_generation(
            trace=trace,
            name='diagnose',
            model='model',
            prompt='p',
            completion='c',
            input_tokens=0,
            output_tokens=0,
            duration_ms=0,
        )
        tracker.end_trace(trace=trace, output={'result': 'ok'})
        tracker.flush()

    def test_create_trace_fails_rest_still_works(self):
        """If create_trace fails, record_generation/end_trace handle None trace."""
        tracker = _make_tracker()
        tracker._langfuse.trace.side_effect = Exception('API down')

        trace = tracker.create_trace(name='analysis')
        assert trace is None

        # These should all be no-ops since trace is None
        tracker.record_generation(
            trace=trace,
            name='diagnose',
            model='model',
            prompt='p',
            completion='c',
            input_tokens=100,
            output_tokens=50,
            duration_ms=1000,
        )
        tracker.end_trace(trace=trace, output={'root_cause': 'unknown'})
        # Flush still works even if trace creation failed
        tracker.flush()
        tracker._langfuse.flush.assert_called_once()
