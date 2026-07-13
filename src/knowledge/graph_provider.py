"""Graph context provider abstraction for AI analyzers.

Analyzers import from this module, never from graph_context directly.
Today's DirectGraphProvider queries Neo4j; a future HttpGraphProvider
can call a map API endpoint without changing analyzer code.
"""

import logging
import os
import threading
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Prevents cascading failures when Neo4j is slow or down.

    States: CLOSED (normal) → OPEN (skip requests) → HALF_OPEN (probe one).
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: int = 300):
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    def allow_request(self) -> bool:
        with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if time.monotonic() - self._last_failure_time >= self._cooldown:
                    self._state = self.HALF_OPEN
                    return True
                return False
            # HALF_OPEN — already probing
            return True

    def record_success(self):
        with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED

    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == self.HALF_OPEN or self._failure_count >= self._threshold:
                self._state = self.OPEN


class GraphProvider(ABC):
    """Abstract interface for graph context providers."""

    @abstractmethod
    def policy_rules(self, rule_names: list[str]) -> str: ...

    @abstractmethod
    def failure_pattern(self, category: str) -> str: ...

    @abstractmethod
    def component_app(self, component_name: str) -> str: ...

    @abstractmethod
    def domain_concepts(self, concept_names: list[str]) -> str: ...

    @abstractmethod
    def build_context(self, failure: dict) -> str: ...

    @abstractmethod
    def conforma_context(self, violation: dict) -> str: ...

    @abstractmethod
    def release_context(self, context: dict) -> str: ...

    @abstractmethod
    def available(self) -> bool: ...


_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=300)


class DirectGraphProvider(GraphProvider):
    """Queries Neo4j directly via graph_context module."""

    def __init__(self):
        self._breaker = _breaker

    def _guarded_call(self, fn, *args):
        if not self._breaker.allow_request():
            return ""
        try:
            result = fn(*args)
            self._breaker.record_success()
            return result or ""
        except Exception:
            self._breaker.record_failure()
            return ""

    def policy_rules(self, rule_names):
        from knowledge.graph_context import policy_rules_context
        return self._guarded_call(policy_rules_context, rule_names)

    def failure_pattern(self, category):
        from knowledge.graph_context import failure_pattern_context
        return self._guarded_call(failure_pattern_context, category)

    def component_app(self, component_name):
        from knowledge.graph_context import component_context
        return self._guarded_call(component_context, component_name)

    def domain_concepts(self, concept_names):
        from knowledge.graph_context import domain_concepts_context
        return self._guarded_call(domain_concepts_context, concept_names)

    def build_context(self, failure):
        from knowledge.graph_context import build_context
        return self._guarded_call(build_context, failure)

    def conforma_context(self, violation):
        from knowledge.graph_context import conforma_context
        return self._guarded_call(conforma_context, violation)

    def release_context(self, context):
        from knowledge.graph_context import release_context
        return self._guarded_call(release_context, context)

    def available(self):
        if not self._breaker.allow_request():
            return False
        try:
            from knowledge.graph_context import _get_driver
            return _get_driver() is not None
        except Exception:
            return False


_provider_instance = None


def get_provider() -> GraphProvider:
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance
    mode = os.environ.get("GRAPH_PROVIDER", "direct")
    if mode == "direct":
        _provider_instance = DirectGraphProvider()
    else:
        logger.warning("Unknown GRAPH_PROVIDER=%s, using direct", mode)
        _provider_instance = DirectGraphProvider()
    return _provider_instance
