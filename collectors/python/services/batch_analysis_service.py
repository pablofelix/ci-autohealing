"""Batch analysis service - automated AI analysis of pending failures.

Orchestrates analysis of build failures and Conforma violations in batches.
Supports single-app (default) and multi-app (--all) modes.
"""

import time
from typing import Any, Dict, List, Optional
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
        build_analyzer: Optional[BuildFailureAnalyzer] = None,
        all_apps: bool = False
    ):
        """Initialize batch analysis service.

        Args:
            config: Collector configuration
            build_analyzer: Build failure analyzer (created if None)
            all_apps: If True, analyze failures across all applications
        """
        self.config = config
        self.build_analyzer = build_analyzer or BuildFailureAnalyzer(config)
        self.all_apps = all_apps

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

        In all_apps mode, discovers apps with pending failures and distributes
        the batch quota proportionally by queue depth.

        Returns:
            BatchAnalysisResult with stats and queue depth
        """
        if not self.enabled:
            logger.warning("Batch analysis is disabled in config")
            return BatchAnalysisResult(
                build_analyzed=0, conforma_analyzed=0, total_analyzed=0,
                build_skipped=0, conforma_skipped=0,
                build_pending=0, conforma_pending=0,
                duration_seconds=0.0, queue_eta_hours=0.0
            )

        start_time = time.time()

        if self.all_apps:
            apps = self._discover_apps_with_pending()
            mode_label = "Multi-App ({} apps)".format(len(apps))
        else:
            apps = [self.config.k8s.application_name]
            mode_label = self.config.k8s.application_name

        logger.info("=" * 70)
        logger.info("Batch AI Analysis")
        logger.info("Mode: %s", mode_label)
        logger.info("Max per batch: %d build + %d conforma = %d total",
                   self.max_build, self.max_conforma, self.max_per_run)
        if self.all_apps and apps:
            logger.info("Apps: %s", ', '.join(apps))
        logger.info("=" * 70)

        total_build_analyzed = 0
        total_conforma_analyzed = 0
        total_build_skipped = 0
        total_conforma_skipped = 0

        if not apps:
            logger.info("No applications with pending failures")
        else:
            build_per_app = max(1, self.max_build // len(apps))
            conforma_per_app = max(1, self.max_conforma // len(apps))

            for app in apps:
                logger.info("--- %s ---", app)
                build_result = self.build_analyzer.run(
                    limit=build_per_app, application=app
                )
                total_build_analyzed += build_result['analyzed']
                total_build_skipped += build_result.get('skipped_new', 0)

                try:
                    from analyzers.conforma_analyzer import ConformaAnalyzer
                    conforma_analyzer = ConformaAnalyzer(self.config)
                    conforma_result = conforma_analyzer.run(
                        limit=conforma_per_app, application=app
                    )
                    total_conforma_analyzed += conforma_result['analyzed']
                    total_conforma_skipped += conforma_result.get('skipped_new', 0)
                except ImportError:
                    pass
                except Exception as e:
                    logger.error("Conforma analysis failed for %s: %s", app, e)

        app_filter = None if self.all_apps else self.config.k8s.application_name
        build_pending = self._get_build_pending_count(app_filter)
        conforma_pending = self._get_conforma_pending_count(app_filter)

        total_analyzed = total_build_analyzed + total_conforma_analyzed
        total_pending = build_pending + conforma_pending

        if total_analyzed > 0:
            hours_per_batch = (time.time() - start_time) / 3600.0
            batches_needed = total_pending / total_analyzed
            queue_eta = batches_needed * hours_per_batch
        else:
            queue_eta = 0.0

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Batch Analysis Complete")
        logger.info("Build: %d analyzed, %d skipped, %d pending",
                   total_build_analyzed, total_build_skipped, build_pending)
        logger.info("Conforma: %d analyzed, %d skipped, %d pending",
                   total_conforma_analyzed, total_conforma_skipped, conforma_pending)
        logger.info("Total: %d analyzed, %d pending", total_analyzed, total_pending)
        logger.info("Queue ETA: %.1f hours at current rate", queue_eta)
        logger.info("Duration: %.1fs", duration)
        logger.info("=" * 70)

        return BatchAnalysisResult(
            build_analyzed=total_build_analyzed,
            conforma_analyzed=total_conforma_analyzed,
            total_analyzed=total_analyzed,
            build_skipped=total_build_skipped,
            conforma_skipped=total_conforma_skipped,
            build_pending=build_pending,
            conforma_pending=conforma_pending,
            duration_seconds=duration,
            queue_eta_hours=queue_eta
        )

    def estimate_queue_depth(self) -> Dict[str, Any]:
        """Get current queue depth without analyzing.

        Returns:
            Dict with build_pending, conforma_pending, total_pending, eta_hours.
            In all_apps mode, also includes per_app breakdown.
        """
        app_filter = None if self.all_apps else self.config.k8s.application_name
        build_pending = self._get_build_pending_count(app_filter)
        conforma_pending = self._get_conforma_pending_count(app_filter)
        total_pending = build_pending + conforma_pending

        batches_needed = (total_pending / self.max_per_run) if self.max_per_run > 0 else 0
        eta_hours = batches_needed * 1.0

        result = {
            'build_pending': build_pending,
            'conforma_pending': conforma_pending,
            'total_pending': total_pending,
            'eta_hours': round(eta_hours, 1)
        }  # type: Dict[str, Any]

        if self.all_apps:
            apps = self._discover_apps_with_pending()
            result['apps'] = apps
            result['app_count'] = len(apps)

        return result

    def _discover_apps_with_pending(self) -> List[str]:
        """Find all applications that have pending failures or violations."""
        try:
            from repositories.connection import DatabaseConnection
            db = self.build_analyzer.ai_repo.db
            with db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT application FROM (
                        SELECT application FROM build_failures
                        WHERE ai_analyzed = FALSE AND is_resolved = FALSE
                          AND build_logs IS NOT NULL AND ai_skip_reason IS NULL
                        UNION
                        SELECT application FROM conforma_results
                        WHERE ai_analyzed = FALSE AND is_resolved = FALSE
                          AND violation_summary IS NOT NULL AND ai_skip_reason IS NULL
                    ) apps
                    ORDER BY application
                """)
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to discover apps: %s", e)
            return [self.config.k8s.application_name]

    def _get_build_pending_count(self, application=None) -> int:
        """Get count of build failures awaiting analysis."""
        try:
            return self.build_analyzer.ai_repo.get_pending_count(application)
        except Exception as e:
            logger.error("Failed to get build pending count: %s", e)
            return 0

    def _get_conforma_pending_count(self, application=None) -> int:
        """Get count of conforma violations awaiting analysis."""
        try:
            return self.build_analyzer.ai_repo.get_pending_conforma_count(application)
        except Exception as e:
            logger.error("Failed to get conforma pending count: %s", e)
            return 0
