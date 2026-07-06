"""Category-based pattern matcher.

Matches patterns by exact failure_category with fuzzy fallback on error_type.

Matching strategy:
1. Exact: Pattern's failure_category == failure's previous AI category (score: 1.0)
2. Fuzzy: Pattern's category inferred from error_type keywords (score: 0.7-0.9)
3. Ranked by: match_score DESC, occurrence_count DESC, avg_confidence DESC

Example:
    Failure with error_type='ModuleNotFoundError' and no previous category
    → Fuzzy match to 'dependency_issue' patterns (score: 0.8)
"""

from typing import Any, Dict, List

from logger import setup_logger
from patterns.pattern_matcher import PatternMatch, PatternMatcher
from repositories.error_pattern_repository import ErrorPatternRepository

logger = setup_logger(__name__)


class CategoryBasedMatcher(PatternMatcher):
    """Matches patterns based on failure category with fuzzy fallback."""

    # Fuzzy matching rules: error_type keyword → category mapping
    FUZZY_RULES = {
        'dependency_issue': ['dependency', 'module', 'import', 'package', 'requirement'],
        'build_error': ['syntax', 'compile', 'build', 'makefile'],
        'test_failure': ['test', 'pytest', 'unittest', 'assert'],
        'resource_limit': ['oom', 'memory', 'timeout', 'killed'],
        'config_error': ['config', 'yaml', 'json', 'env', 'variable'],
        'git_sync_issue': ['git', 'clone', 'fetch', 'pull', 'merge'],
    }

    def __init__(self, pattern_repo: ErrorPatternRepository):
        """Initialize category-based matcher.

        Args:
            pattern_repo: Repository for querying error patterns
        """
        self.pattern_repo = pattern_repo

    def find_matches(
        self,
        failure: Dict[str, Any],
        top_n: int = 3
    ) -> List[PatternMatch]:
        """Find top N patterns by category matching.

        Args:
            failure: Failure dict with error_type, failure_category (optional)
            top_n: Number of patterns to return

        Returns:
            List of PatternMatch objects
        """
        try:
            error_type = failure.get('error_type', '').lower()
            prev_category = failure.get('failure_category')

            # Build match candidates
            candidates = []
            seen_pattern_ids = set()  # O(1) lookup for duplicate detection

            # Strategy 1: Exact match on previous AI category
            if prev_category:
                exact_pattern = self.pattern_repo.get_by_category('build', prev_category)
                if exact_pattern:
                    candidates.append((exact_pattern, 1.0))  # Perfect match
                    seen_pattern_ids.add(exact_pattern['id'])

            # Strategy 2: Fuzzy match on error_type keywords
            for category, keywords in self.FUZZY_RULES.items():
                if any(keyword in error_type for keyword in keywords):
                    fuzzy_pattern = self.pattern_repo.get_by_category('build', category)
                    if fuzzy_pattern and fuzzy_pattern['id'] not in seen_pattern_ids:
                        # Score based on keyword specificity
                        score = 0.9 if len(keywords) <= 3 else 0.7
                        candidates.append((fuzzy_pattern, score))
                        seen_pattern_ids.add(fuzzy_pattern['id'])

            if not candidates:
                return []

            # Rank candidates
            ranked = self._rank_candidates(candidates, top_n)

            # Convert to PatternMatch objects
            matches = []
            for pattern, match_score in ranked:
                matches.append(PatternMatch(
                    pattern_id=pattern['id'],
                    pattern_name=pattern['pattern_name'],
                    failure_category=pattern['failure_category'],
                    confidence=pattern.get('avg_confidence') or 0.0,
                    occurrence_count=pattern.get('occurrence_count') or 0,
                    match_score=match_score,
                    typical_fix=pattern.get('typical_fix'),
                    doc_context=pattern.get('doc_context'),
                ))

            logger.info("Found %d pattern matches (strategy: %s)",
                       len(matches), self.strategy_name)

            return matches

        except Exception as e:
            logger.error("CategoryBasedMatcher failed: %s", e)
            return []

    def _rank_candidates(
        self,
        candidates: List[tuple],
        top_n: int
    ) -> List[tuple]:
        """Rank pattern candidates by match quality.

        Ranking criteria (in order):
        1. Match score (exact > fuzzy)
        2. Occurrence count (common patterns first)
        3. Average confidence (high confidence first)

        Args:
            candidates: List of (pattern_dict, match_score) tuples
            top_n: Number to return

        Returns:
            Top N ranked candidates
        """
        def rank_key(item):
            pattern, match_score = item
            return (
                -match_score,  # Negative for DESC (exact first)
                -pattern.get('occurrence_count', 0),  # Most common first
                -pattern.get('avg_confidence', 0.0),  # Highest confidence first
            )

        ranked = sorted(candidates, key=rank_key)
        return ranked[:top_n]

    @property
    def strategy_name(self) -> str:
        return 'category'
