"""Batch analysis service - automated AI analysis of pending failures.

Orchestrates analysis of build failures and Conforma violations in batches.
Designed for cron jobs to achieve 100% analysis coverage within 24-48 hours.
"""

import time
from typing import Any, Dict, Optional
from dataclasses import dataclass

from config import CollectorConfig
from analyzers.build_failure_analyzer import BuildFailureAnalyzer
from logger import setup_logger

logger = setup_logger(__name__)


@dataclass(frozen=True)
class BatchAnalysisResult:
    """Result of a batch analysis run.

    Attributes:
        build_analyzed: Number of build failures analyzed
        conforma_analyzed: Number of conforma violations analyzed
        total_analyzed: Total analyzed
        build_skipped: Build failures skipped (errors)
        conforma_skipped: Conforma skipped (errors)
        build_pending: Build failures remaining in queue
        conforma_pending: Conforma violations remaining in queue
        duration_seconds: Total batch duration
        queue_eta_hours: Estimated hours to clear queue at current rate
    """
    build_analyzed: int
    conforma_analyzed: int
    total_analyzed: int
    build_skipped: int
    conforma_skipped: int
    build_pending: int
    conforma_pending: int
    duration_seconds: float
    queue_eta_hours: float


class BatchAnalysisService:
    """Automated batch analysis with queue management.

    Design principles:
    - FIFO queue discipline (oldest first)
    - Separate limits for build vs conforma
    - No auto-Jira creation (P0 - analyze only)
    - Queue depth tracking for monitoring
    """

    # Batch split ratios (build failures are more common than conforma)
    BUILD_BATCH_RATIO = 0.75   # 75% of batch capacity
    CONFORMA_BATCH_RATIO = 0.25  # 25% of batch capacity

    def __init__(
        self,
        config: CollectorConfig,
        build_analyzer: Optional[BuildFailureAnalyzer] = None
    ):
        """Initialize batch analysis service.

        Args:
            config: Collector configuration
            build_analyzer: Build failure analyzer (created if None)
        """
        self.config = config
        self.build_analyzer = build_analyzer or BuildFailureAnalyzer(config)

        # Get batch limits from config
        batch_config = config.batch_analysis
        if batch_config:
            self.max_per_run = batch_config.max_per_run
            self.enabled = batch_config.enabled
        else:
            self.max_per_run = 20
            self.enabled = True

        # Split limit based on ratios
        self.max_build = int(self.max_per_run * self.BUILD_BATCH_RATIO)
        self.max_conforma = int(self.max_per_run * self.CONFORMA_BATCH_RATIO)

    def run_batch(self) -> BatchAnalysisResult:
        """Process one batch of pending analyses.

        Returns:
            BatchAnalysisResult with stats and queue depth
        """
        if not self.enabled:
            logger.warning("Batch analysis is disabled in config")
            return BatchAnalysisResult(
                build_analyzed=0,
                conforma_analyzed=0,
                total_analyzed=0,
                build_skipped=0,
                conforma_skipped=0,
                build_pending=0,
                conforma_pending=0,
                duration_seconds=0.0,
                queue_eta_hours=0.0
            )

        start_time = time.time()

        logger.info("=" * 70)
        logger.info("Batch AI Analysis (Automated)")
        logger.info("Application: %s", self.config.k8s.application_name)
        logger.info("Max per batch: %d build + %d conforma = %d total",
                   self.max_build, self.max_conforma, self.max_per_run)
        logger.info("=" * 70)

        # Analyze build failures
        build_result = self.build_analyzer.run(limit=self.max_build)

        # Analyze conforma violations (if analyzer available)
        conforma_result = {'analyzed': 0, 'skipped_new': 0}
        try:
            from analyzers.conforma_analyzer import ConformaAnalyzer
            conforma_analyzer = ConformaAnalyzer(self.config)
            conforma_result = conforma_analyzer.run(limit=self.max_conforma)
        except ImportError:
            logger.info("ConformaAnalyzer not available - skipping conforma analysis")
        except Exception as e:
            logger.error("Conforma analysis failed: %s", e)

        # Get queue depths
        build_pending = self._get_build_pending_count()
        conforma_pending = self._get_conforma_pending_count()

        # Calculate ETA
        total_analyzed = build_result['analyzed'] + conforma_result['analyzed']
        total_pending = build_pending + conforma_pending

        if total_analyzed > 0:
            # At current rate, how long to clear queue?
            hours_per_batch = (time.time() - start_time) / 3600.0
            batches_needed = (total_pending / total_analyzed) if total_analyzed > 0 else 0
            queue_eta = batches_needed * hours_per_batch
        else:
            queue_eta = 0.0

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Batch Analysis Complete")
        logger.info("Build: %d analyzed, %d skipped, %d pending",
                   build_result['analyzed'],
                   build_result.get('skipped_new', 0),
                   build_pending)
        logger.info("Conforma: %d analyzed, %d skipped, %d pending",
                   conforma_result['analyzed'],
                   conforma_result.get('skipped_new', 0),
                   conforma_pending)
        logger.info("Total: %d analyzed, %d pending",
                   total_analyzed, total_pending)
        logger.info("Queue ETA: %.1f hours at current rate", queue_eta)
        logger.info("Duration: %.1fs", duration)
        logger.info("=" * 70)

        return BatchAnalysisResult(
            build_analyzed=build_result['analyzed'],
            conforma_analyzed=conforma_result['analyzed'],
            total_analyzed=total_analyzed,
            build_skipped=build_result.get('skipped_new', 0),
            conforma_skipped=conforma_result.get('skipped_new', 0),
            build_pending=build_pending,
            conforma_pending=conforma_pending,
            duration_seconds=duration,
            queue_eta_hours=queue_eta
        )

    def estimate_queue_depth(self) -> Dict[str, Any]:
        """Get current queue depth without analyzing.

        Returns:
            Dict with:
            - build_pending: Build failures awaiting analysis
            - conforma_pending: Conforma violations awaiting analysis
            - total_pending: Total pending
            - eta_hours: Estimated hours to clear at current batch rate
        """
        build_pending = self._get_build_pending_count()
        conforma_pending = self._get_conforma_pending_count()
        total_pending = build_pending + conforma_pending

        # Estimate ETA (assumes hourly cron)
        batches_needed = (total_pending / self.max_per_run) if self.max_per_run > 0 else 0
        eta_hours = batches_needed * 1.0  # 1 hour between batches

        return {
            'build_pending': build_pending,
            'conforma_pending': conforma_pending,
            'total_pending': total_pending,
            'eta_hours': round(eta_hours, 1)
        }

    def _get_build_pending_count(self) -> int:
        """Get count of build failures awaiting analysis."""
        try:
            return self.build_analyzer.ai_repo.get_pending_count(
                self.config.k8s.application_name
            )
        except Exception as e:
            logger.error("Failed to get build pending count: %s", e)
            return 0

    def _get_conforma_pending_count(self) -> int:
        """Get count of conforma violations awaiting analysis."""
        try:
            return self.build_analyzer.ai_repo.get_pending_conforma_count(
                self.config.k8s.application_name
            )
        except Exception as e:
            logger.error("Failed to get conforma pending count: %s", e)
            return 0
