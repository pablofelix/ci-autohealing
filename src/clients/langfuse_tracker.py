"""Langfuse tracking for LLM calls.

Wraps the Langfuse SDK to track all AI analysis operations: traces per
analysis run, generations per LLM call, token usage, cost, duration.
"""

import logging


class LangfuseTracker:
    """Wraps Langfuse SDK for LLM call tracking.

    Tracks: model, tokens, cost, latency, trace ID. Links trace IDs to
    database records (ai_analysis.langfuse_trace_id).

    If Langfuse is not configured (missing LANGFUSE_PUBLIC_KEY), tracking is
    disabled but analysis still works - graceful degradation.
    """

    def __init__(self, enabled=True):
        self.enabled = enabled
        self._langfuse = None

        if enabled:
            try:
                logging.getLogger('langfuse').setLevel(logging.CRITICAL)
                from langfuse import Langfuse
                self._langfuse = Langfuse()
            except (ImportError, Exception):
                self.enabled = False

    def create_trace(self, name, input_data=None, metadata=None):
        """Create a trace for an analysis run.

        Args:
            name: Trace name (e.g., 'build-failure-analysis')
            input_data: Input data to log (component, failure_id, etc.)
            metadata: Additional metadata

        Returns:
            Trace object or None if tracking disabled
        """
        if not self.enabled or not self._langfuse:
            return None

        try:
            trace = self._langfuse.trace(
                name=name,
                input=input_data,
                metadata=metadata,
            )
            return trace
        except Exception:
            # Langfuse errors shouldn't break analysis
            return None

    def record_generation(self, trace, name, model, prompt, completion,
                         input_tokens, output_tokens, duration_ms):
        """Record an LLM generation within a trace.

        Args:
            trace: Trace object from create_trace()
            name: Generation name (e.g., 'diagnose-root-cause')
            model: Model identifier
            prompt: Input prompt text
            completion: LLM response text
            input_tokens: Token count for input
            output_tokens: Token count for output
            duration_ms: Generation duration in milliseconds
        """
        if not self.enabled or not trace:
            return

        try:
            trace.generation(
                name=name,
                model=model,
                input=prompt,
                output=completion,
                usage={
                    'input': input_tokens,
                    'output': output_tokens,
                    'total': input_tokens + output_tokens,
                },
                metadata={
                    'duration_ms': duration_ms,
                }
            )
        except Exception:
            # Langfuse errors shouldn't break analysis
            pass

    def end_trace(self, trace, output=None):
        """Finalize trace with output data.

        Args:
            trace: Trace object from create_trace()
            output: Analysis results (root_cause, category, confidence, etc.)
        """
        if not self.enabled or not trace:
            return

        try:
            trace.update(output=output)
        except Exception:
            pass

    def flush(self):
        """Flush pending events to Langfuse server.

        Should be called at the end of an analysis run to ensure all events
        are sent before the process exits.
        """
        if not self.enabled or not self._langfuse:
            return

        try:
            self._langfuse.flush()
        except Exception:
            pass
