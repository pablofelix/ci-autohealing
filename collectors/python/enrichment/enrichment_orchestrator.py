"""Enrichment orchestrator - coordinates multiple context sources.

Manages the enrichment pipeline:
1. Register context sources
2. Fetch enrichment data in parallel
3. Aggregate results (partial success OK)
4. Update database with enriched context
"""

import time
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from config import CollectorConfig
from enrichment.context_source import ContextSource, EnrichmentResult
from repositories.connection import DatabaseConnection
from repositories.context_enrichment_repository import ContextEnrichmentRepository
from logger import setup_logger

logger = setup_logger(__name__)


@dataclass(frozen=True)
class OrchestrationResult:
    """Result of enriching one failure.

    Attributes:
        failure_id: Database ID
        success: Whether any sources succeeded
        sources_attempted: Number of sources run
        sources_succeeded: Number that returned data
        sources_failed: Number that failed or timed out
        enrichment_data: Merged data from all sources
        errors: List of error messages from failed sources
        duration_seconds: Total time for all sources
    """
    failure_id: int
    success: bool
    sources_attempted: int
    sources_succeeded: int
    sources_failed: int
    enrichment_data: Dict[str, Any]
    errors: List[str]
    duration_seconds: float


class EnrichmentOrchestrator:
    """Coordinates multiple context sources with resilience and parallelism.

    Design patterns:
    - Parallel execution (ThreadPoolExecutor) for I/O-bound sources
    - Partial success model (some sources can fail)
    - Circuit breaker (max retry attempts per failure)
    - Graceful degradation (continue with available data)
    """

    MAX_ENRICHMENT_ATTEMPTS = 3
    MAX_PARALLEL_SOURCES = 3

    def __init__(
        self,
        config: CollectorConfig,
        db: Optional[DatabaseConnection] = None,
        enrichment_repo: Optional[ContextEnrichmentRepository] = None
    ):
        """Initialize enrichment orchestrator.

        Args:
            config: Collector configuration
            db: Database connection (created if None)
            enrichment_repo: Enrichment repository (created if None)
        """
        self.config = config
        self.db = db or DatabaseConnection(config.db)
        self.enrichment_repo = enrichment_repo or ContextEnrichmentRepository(self.db)
        self.sources: List[ContextSource] = []

    def register_source(self, source: ContextSource) -> None:
        """Add a context source to the enrichment pipeline.

        Sources are executed in registration order.

        Args:
            source: Context source instance
        """
        self.sources.append(source)
        logger.info("Registered context source: %s", source.source_name())

    def enrich_failure(self, failure: Dict[str, Any]) -> OrchestrationResult:
        """Enrich one failure with all registered sources.

        Args:
            failure: Failure dict to enrich (must include 'id' key)

        Returns:
            OrchestrationResult with aggregated data and stats
        """
        start_time = time.time()
        failure_id = failure['id']

        # Check circuit breaker
        attempts = self.enrichment_repo.increment_enrichment_attempts(failure_id)
        if attempts > self.MAX_ENRICHMENT_ATTEMPTS:
            logger.warning("Failure %d exceeded max enrichment attempts (%d)",
                         failure_id, self.MAX_ENRICHMENT_ATTEMPTS)
            self.enrichment_repo.mark_enrichment_failed(
                failure_id,
                f"Exceeded max attempts ({self.MAX_ENRICHMENT_ATTEMPTS})"
            )
            return OrchestrationResult(
                failure_id=failure_id,
                success=False,
                sources_attempted=0,
                sources_succeeded=0,
                sources_failed=0,
                enrichment_data={},
                errors=[f"Exceeded max attempts ({self.MAX_ENRICHMENT_ATTEMPTS})"],
                duration_seconds=time.time() - start_time
            )

        # Execute sources in parallel
        source_results = self._execute_sources_parallel(failure)

        # Aggregate results
        enrichment_data = {'sources': {}}
        errors = []

        for result in source_results:
            if result.success and result.data:
                # Merge source data with conflict detection
                for key, value in result.data.items():
                    if key in enrichment_data and key != 'sources':
                        logger.warning(
                            "Source %s returned duplicate key '%s' (overwriting previous value)",
                            result.source_name, key
                        )
                    enrichment_data[key] = value
                enrichment_data['sources'][result.source_name] = True
            else:
                enrichment_data['sources'][result.source_name] = False
                if result.error:
                    errors.append(f"{result.source_name}: {result.error}")

        # Add timestamp
        enrichment_data['sources']['enriched_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Count successes
        sources_succeeded = sum(1 for r in source_results if r.success)
        sources_failed = len(source_results) - sources_succeeded

        # Store enrichment (even if partial)
        if sources_succeeded > 0:
            self.enrichment_repo.update_enriched_context(failure_id, enrichment_data)
            logger.info("Enriched failure %d: %d/%d sources succeeded",
                       failure_id, sources_succeeded, len(source_results))
        else:
            # All sources failed - mark as failed
            error_msg = "; ".join(errors) if errors else "All sources failed"
            self.enrichment_repo.mark_enrichment_failed(failure_id, error_msg)
            logger.error("Failed to enrich failure %d: %s", failure_id, error_msg)

        duration = time.time() - start_time

        return OrchestrationResult(
            failure_id=failure_id,
            success=sources_succeeded > 0,
            sources_attempted=len(source_results),
            sources_succeeded=sources_succeeded,
            sources_failed=sources_failed,
            enrichment_data=enrichment_data,
            errors=errors,
            duration_seconds=duration
        )

    def _execute_sources_parallel(
        self,
        failure: Dict[str, Any]
    ) -> List[EnrichmentResult]:
        """Execute all sources in parallel with timeout.

        Args:
            failure: Failure dict to enrich

        Returns:
            List of EnrichmentResults (one per source)
        """
        results = []

        with ThreadPoolExecutor(max_workers=self.MAX_PARALLEL_SOURCES) as executor:
            # Submit all sources
            future_to_source = {
                executor.submit(self._execute_source, source, failure): source
                for source in self.sources
            }

            # Collect results as they complete
            for future in as_completed(future_to_source):
                source = future_to_source[future]
                try:
                    result = future.result(timeout=source.timeout_seconds)
                    results.append(result)
                except Exception as e:
                    # Source raised exception or timed out
                    logger.error("Source %s failed: %s", source.source_name(), e)
                    results.append(EnrichmentResult(
                        source_name=source.source_name(),
                        success=False,
                        data=None,
                        error=str(e),
                        duration_seconds=0.0
                    ))

        return results

    def _execute_source(
        self,
        source: ContextSource,
        failure: Dict[str, Any]
    ) -> EnrichmentResult:
        """Execute one context source.

        Args:
            source: Context source to execute
            failure: Failure data

        Returns:
            EnrichmentResult with data or error
        """
        start_time = time.time()

        try:
            data = source.fetch(failure)
            duration = time.time() - start_time

            if data:
                return EnrichmentResult(
                    source_name=source.source_name(),
                    success=True,
                    data=data,
                    error=None,
                    duration_seconds=duration
                )
            else:
                return EnrichmentResult(
                    source_name=source.source_name(),
                    success=False,
                    data=None,
                    error="No data returned",
                    duration_seconds=duration
                )

        except Exception as e:
            duration = time.time() - start_time
            logger.error("Source %s raised exception: %s", source.source_name(), e)
            return EnrichmentResult(
                source_name=source.source_name(),
                success=False,
                data=None,
                error=str(e),
                duration_seconds=duration
            )

    def enrich_batch(self, limit: int = 20) -> Dict[str, Any]:
        """Process a batch of pending enrichments.

        Args:
            limit: Maximum failures to process

        Returns:
            Stats dict:
            {
                'enriched': int,
                'failed': int,
                'skipped': int,
                'duration': float,
                'coverage_pct': float
            }
        """
        start_time = time.time()

        logger.info("=" * 70)
        logger.info("Context Enrichment Batch")
        logger.info("Application: %s", self.config.k8s.application_name)
        logger.info("=" * 70)

        # Get pending enrichments
        pending = self.enrichment_repo.get_pending_enrichments(
            self.config.k8s.application_name,
            limit=limit
        )

        if not pending:
            logger.info("No failures need enrichment")
            # Get coverage stats
            coverage = self.enrichment_repo.get_enrichment_coverage(
                self.config.k8s.application_name
            )
            return {
                'enriched': 0,
                'failed': 0,
                'skipped': 0,
                'duration': 0.0,
                'coverage_pct': coverage['coverage_pct']
            }

        logger.info("Found %d failures needing enrichment", len(pending))

        enriched = 0
        failed = 0

        for i, failure in enumerate(pending, 1):
            logger.info("[%d/%d] %s (id=%d)",
                       i, len(pending), failure['component_name'], failure['id'])

            result = self.enrich_failure(failure)

            if result.success:
                enriched += 1
                logger.info("  SUCCESS: %d/%d sources succeeded",
                           result.sources_succeeded, result.sources_attempted)
            else:
                failed += 1
                logger.error("  FAILED: Enrichment failed: %s",
                           "; ".join(result.errors))

        duration = time.time() - start_time

        # Get final coverage stats
        coverage = self.enrichment_repo.get_enrichment_coverage(
            self.config.k8s.application_name
        )

        logger.info("=" * 70)
        logger.info("Enrichment Complete")
        logger.info("Enriched: %d, Failed: %d", enriched, failed)
        logger.info("Coverage: %.1f%%", coverage['coverage_pct'])
        logger.info("Duration: %.1fs", duration)
        logger.info("=" * 70)

        return {
            'enriched': enriched,
            'failed': failed,
            'skipped': 0,
            'duration': duration,
            'coverage_pct': coverage['coverage_pct']
        }
