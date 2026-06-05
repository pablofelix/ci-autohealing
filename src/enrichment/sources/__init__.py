"""Context source implementations.

Each module implements a specific enrichment strategy.
"""

from enrichment.sources.dependency_context import DependencyContextSource
from enrichment.sources.related_failures import RelatedFailuresSource
from enrichment.sources.open_prs import OpenPRsSource

__all__ = ['DependencyContextSource', 'RelatedFailuresSource', 'OpenPRsSource']
