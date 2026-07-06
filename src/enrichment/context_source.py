"""Abstract base class for context sources.

Context sources are pluggable components that fetch additional information
about build failures to enrich AI analysis prompts.

Design Pattern: Strategy pattern with Template Method
- ContextSource defines the contract
- Implementations provide specific enrichment strategies
- Orchestrator coordinates multiple sources
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from config import CollectorConfig


@dataclass(frozen=True)
class EnrichmentResult:
    """Result of a context source enrichment attempt.

    Attributes:
        source_name: Identifier for this source
        success: Whether enrichment succeeded
        data: Enrichment data (None on failure)
        error: Error message if failed
        duration_seconds: How long the fetch took
    """
    source_name: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0


class ContextSource(ABC):
    """Abstract interface for pluggable context sources.

    Each context source is responsible for fetching one type of enrichment
    data (e.g., dependency changes, related failures, policy updates).

    Implementations must be:
    - Idempotent: Safe to call multiple times for same failure
    - Resilient: Gracefully handle errors without raising
    - Fast: Complete within timeout_seconds or return partial results

    Example:
        class DependencyContextSource(ContextSource):
            def fetch(self, failure):
                return {'dependency_changes': [...]}
    """

    def __init__(self, config: CollectorConfig):
        """Initialize context source with configuration.

        Args:
            config: Collector configuration with API tokens, timeouts, etc.
        """
        self.config = config

    @abstractmethod
    def fetch(self, failure: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch enrichment context for a failure.

        Args:
            failure: Failure dict from database with at minimum:
                - id: Database ID
                - component_name: Component that failed
                - error_type: Type of error
                - commit_sha: Git commit SHA (if available)
                - repository_url: Git repository URL (if available)

        Returns:
            Dict with enrichment data, or None if enrichment not possible.
            Return value will be merged into failure's enriched_context JSONB.

        Raises:
            Should not raise - catch exceptions and return None instead.
            Exceptions indicate a programming error, not expected failure.
        """
        pass

    @abstractmethod
    def source_name(self) -> str:
        """Unique identifier for this source.

        Used for logging, metrics, and tracking which sources succeeded/failed.

        Returns:
            Short lowercase identifier (e.g., 'dependency_changes', 'related_failures')
        """
        pass

    @property
    def timeout_seconds(self) -> int:
        """Maximum time this source can take before timing out.

        Orchestrator may enforce this timeout to prevent slow sources from
        blocking the pipeline. Default is 30 seconds.

        Override in subclasses if needed:
            @property
            def timeout_seconds(self):
                return 60  # GitHub API can be slow

        Returns:
            Timeout in seconds (default: 30)
        """
        return 30

    @property
    def requires_external_api(self) -> bool:
        """Whether this source makes external API calls.

        Used by orchestrator to decide whether to check rate limits,
        run in parallel, etc.

        Returns:
            True if source calls GitHub/Slack/etc, False if DB-only
        """
        return False
