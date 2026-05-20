"""Pattern matching for failure analysis.

This package provides abstractions for matching failures to known patterns,
enabling AI analysis to benefit from institutional memory.
"""

from patterns.pattern_matcher import PatternMatcher, PatternMatch
from patterns.category_matcher import CategoryBasedMatcher
from patterns.pattern_matching_service import PatternMatchingService

__all__ = [
    'PatternMatcher',
    'PatternMatch',
    'CategoryBasedMatcher',
    'PatternMatchingService',
]
