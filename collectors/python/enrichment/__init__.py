"""Context enrichment pipeline for CI build failures.

This package provides pluggable context sources that enrich failure data
with additional information for more accurate AI analysis.
"""

from enrichment.context_source import ContextSource, EnrichmentResult
from enrichment.enrichment_orchestrator import EnrichmentOrchestrator

__all__ = ['ContextSource', 'EnrichmentResult', 'EnrichmentOrchestrator']
