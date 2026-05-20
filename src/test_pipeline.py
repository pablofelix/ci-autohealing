#!/usr/bin/env python3
"""Pipeline integration test - validates P0 improvements.

Tests:
1. Configuration loading
2. Database connectivity
3. Context enrichment (dependency + related failures)
4. Pattern matching with confidence boost
5. Batch analysis service
"""

import sys

from config import CollectorConfig
from enrichment.enrichment_orchestrator import EnrichmentOrchestrator
from enrichment.sources.dependency_context import DependencyContextSource
from enrichment.sources.related_failures import RelatedFailuresSource
from patterns.category_matcher import CategoryBasedMatcher
from patterns.pattern_matching_service import PatternMatchingService
from repositories.connection import DatabaseConnection
from repositories.context_enrichment_repository import ContextEnrichmentRepository
from repositories.error_pattern_repository import ErrorPatternRepository
from services.batch_analysis_service import BatchAnalysisService
from logger import setup_logger

logger = setup_logger(__name__)


def test_configuration():
    """Test 1: Configuration loads correctly."""
    logger.info("Test 1: Configuration Loading")
    try:
        config = CollectorConfig.from_env()
        assert config.db is not None, "Database config missing"
        assert config.k8s is not None, "Kubernetes config missing"
        assert config.batch_analysis is not None, "Batch analysis config missing"
        logger.info("✓ Configuration loaded successfully")
        logger.info("  - Application: %s", config.k8s.application_name)
        logger.info("  - Batch size: %d", config.batch_analysis.max_per_run)
        return config
    except Exception as e:
        logger.error("✗ Configuration failed: %s", e)
        raise


def test_database(config):
    """Test 2: Database connectivity."""
    logger.info("\nTest 2: Database Connectivity")
    try:
        db = DatabaseConnection(config.db)
        repo = ContextEnrichmentRepository(db)
        pending = repo.get_pending_enrichments(
            application=config.k8s.application_name,
            limit=1
        )
        logger.info("✓ Database connection successful")
        logger.info("  - Pending enrichments: %d", len(pending))
        return db, repo
    except Exception as e:
        logger.error("✗ Database connection failed: %s", e)
        raise


def test_enrichment(config, db, enrichment_repo):
    """Test 3: Context enrichment sources."""
    logger.info("\nTest 3: Context Enrichment")
    try:
        # Create sources
        dep_source = DependencyContextSource(config)
        related_source = RelatedFailuresSource(config)

        # Create orchestrator
        orchestrator = EnrichmentOrchestrator(
            config=config,
            db=db,
            enrichment_repo=enrichment_repo
        )
        orchestrator.register_source(dep_source)
        orchestrator.register_source(related_source)

        logger.info("✓ Enrichment orchestrator created")
        logger.info("  - Sources: %d", len(orchestrator.sources))
        logger.info("  - Max parallel: %d", orchestrator.MAX_PARALLEL_SOURCES)

        # Check enrichment coverage
        coverage = enrichment_repo.get_enrichment_coverage(config.k8s.application_name)
        logger.info("  - Enrichment coverage: %.1f%%", coverage['coverage_pct'])
        logger.info("  - Enrichment queue: %d pending", coverage['pending'])

        return orchestrator
    except Exception as e:
        logger.error("✗ Enrichment setup failed: %s", e)
        raise


def test_pattern_matching(config, db):
    """Test 4: Pattern matching with confidence boost."""
    logger.info("\nTest 4: Pattern Matching")
    try:
        # Create pattern components
        pattern_repo = ErrorPatternRepository(db)
        matcher = CategoryBasedMatcher(pattern_repo)
        service = PatternMatchingService(matcher, pattern_repo)

        logger.info("✓ Pattern matching service created")
        logger.info("  - Boost factor: %.2f", service.BOOST_FACTOR)
        logger.info("  - Max confidence: %.2f", service.MAX_CONFIDENCE)

        return service
    except Exception as e:
        logger.error("✗ Pattern matching setup failed: %s", e)
        raise


def test_batch_analysis(config):
    """Test 5: Batch analysis service."""
    logger.info("\nTest 5: Batch Analysis Service")
    try:
        # Check if LLM is configured
        if not config.llm:
            logger.warning("⚠ LLM not configured - skipping batch analysis test")
            logger.warning("  Set ANTHROPIC_VERTEX_PROJECT_ID or ANTHROPIC_API_KEY to test")
            return None

        try:
            service = BatchAnalysisService(config)
        except ImportError:
            logger.warning("⚠ Anthropic module not installed - skipping batch analysis test")
            logger.warning("  Run: pip install anthropic")
            return None

        logger.info("✓ Batch analysis service created")
        logger.info("  - Enabled: %s", service.enabled)
        logger.info("  - Max per run: %d", service.max_per_run)
        logger.info("  - Build limit: %d (75%%)", service.max_build)
        logger.info("  - Conforma limit: %d (25%%)", service.max_conforma)

        # Estimate queue
        estimate = service.estimate_queue_depth()
        logger.info("  - Build pending: %d", estimate['build_pending'])
        logger.info("  - Conforma pending: %d", estimate['conforma_pending'])
        logger.info("  - Queue ETA: %.1f hours", estimate['eta_hours'])

        return service
    except Exception as e:
        logger.error("✗ Batch analysis setup failed: %s", e)
        raise


def main():
    """Run all pipeline tests."""
    logger.info("=" * 70)
    logger.info("CI Auto-Healing Pipeline Integration Test")
    logger.info("=" * 70)

    try:
        # Test 1: Configuration
        config = test_configuration()

        # Test 2: Database
        db, enrichment_repo = test_database(config)

        # Test 3: Enrichment
        orchestrator = test_enrichment(config, db, enrichment_repo)

        # Test 4: Pattern Matching
        pattern_service = test_pattern_matching(config, db)

        # Test 5: Batch Analysis
        batch_service = test_batch_analysis(config)

        # Summary
        logger.info("\n" + "=" * 70)
        logger.info("All Tests Passed ✓")
        logger.info("=" * 70)
        logger.info("\nPipeline Ready:")
        logger.info("  - Context enrichment: %d sources",
                   len(orchestrator.sources))
        logger.info("  - Pattern matching: %.1f%% confidence boost",
                   pattern_service.BOOST_FACTOR * 100)
        if batch_service:
            logger.info("  - Batch analysis: %d per hour",
                       batch_service.max_per_run)
        else:
            logger.info("  - Batch analysis: Not tested (install anthropic module)")
        logger.info("\nNext Steps:")
        logger.info("  1. Set up cron jobs (see cron/README.md)")
        logger.info("  2. Monitor queue depth: python3 analyze_batch.py --estimate")
        logger.info("  3. View logs: /tmp/ci-autohealing/")

        return 0

    except Exception as e:
        logger.error("\n" + "=" * 70)
        logger.error("Tests Failed ✗")
        logger.error("=" * 70)
        logger.error("Error: %s", e)
        return 1


if __name__ == '__main__':
    sys.exit(main())
