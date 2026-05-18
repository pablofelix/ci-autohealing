"""Context source implementations.

Each module implements a specific enrichment strategy.
"""

from enrichment.sources.dependency_context import DependencyContextSource
from enrichment.sources.related_failures import RelatedFailuresSource

__all__ = ['DependencyContextSource', 'RelatedFailuresSource']
