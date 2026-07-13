"""Context source implementations.

Each module implements a specific enrichment strategy.
"""

from enrichment.sources.component_health import ComponentHealthSource
from enrichment.sources.dependency_context import DependencyContextSource
from enrichment.sources.open_prs import OpenPRsSource
from enrichment.sources.related_failures import RelatedFailuresSource

__all__ = ['ComponentHealthSource', 'DependencyContextSource', 'RelatedFailuresSource', 'OpenPRsSource']
