"""Pattern matching service - orchestrates pattern matching and confidence boosting.

Responsibilities:
1. Find matching patterns using injected matcher
2. Apply confidence boost formula
3. Format pattern context for AI prompts
4. Track pattern usage statistics
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from logger import setup_logger
from patterns.pattern_matcher import PatternMatch, PatternMatcher
from repositories.error_pattern_repository import ErrorPatternRepository

logger = setup_logger(__name__)

# Truncation limits for pattern context
MAX_DOC_CONTEXT_LENGTH = 1500  # ~1-2 paragraphs of documentation


@dataclass(frozen=True)
class AnalysisEnhancement:
    """Enhanced analysis with pattern matching.

    Attributes:
        original_confidence: LLM's raw confidence score
        boosted_confidence: Confidence after pattern boost
        boost_applied: Whether boost was applied
        boost_amount: How much was added
        matched_patterns: List of patterns that matched
        pattern_context: Formatted text for prompt injection
    """
    original_confidence: float
    boosted_confidence: float
    boost_applied: bool
    boost_amount: float
    matched_patterns: List[PatternMatch]
    pattern_context: str


class PatternMatchingService:
    """Coordinates pattern matching and confidence boosting.

    Design:
    - Strategy pattern: Injected matcher can be swapped
    - Single responsibility: Only pattern matching logic
    - Transparency: Logs all boosts for auditing
    """

    BOOST_FACTOR = 0.15  # Pattern confidence multiplier
    MAX_CONFIDENCE = 0.95  # Cap to avoid overconfidence

    def __init__(
        self,
        matcher: PatternMatcher,
        pattern_repo: ErrorPatternRepository
    ):
        """Initialize pattern matching service.

        Args:
            matcher: Pattern matcher strategy
            pattern_repo: Repository for updating pattern stats
        """
        self.matcher = matcher
        self.pattern_repo = pattern_repo

    def enhance_analysis(
        self,
        failure: Dict[str, Any],
        llm_confidence: float,
        llm_category: str
    ) -> AnalysisEnhancement:
        """Find patterns and boost confidence if match found.

        Args:
            failure: Failure dict for pattern matching
            llm_confidence: AI's raw confidence score (0.0-1.0)
            llm_category: AI's determined failure category

        Returns:
            AnalysisEnhancement with boosted confidence and pattern context
        """
        # Find matching patterns
        matches = self.matcher.find_matches(failure, top_n=3)

        if not matches:
            # No patterns found - no boost
            return AnalysisEnhancement(
                original_confidence=llm_confidence,
                boosted_confidence=llm_confidence,
                boost_applied=False,
                boost_amount=0.0,
                matched_patterns=[],
                pattern_context=""
            )

        # Check if any pattern matches LLM's category
        matching_pattern = self._find_best_category_match(matches, llm_category)

        if matching_pattern:
            # Apply confidence boost
            boost = matching_pattern.confidence * self.BOOST_FACTOR
            boosted = min(self.MAX_CONFIDENCE, llm_confidence + boost)

            logger.info(
                "Pattern boost: %.2f → %.2f (pattern: %s, avg_conf: %.2f, boost: +%.2f)",
                llm_confidence,
                boosted,
                matching_pattern.pattern_name,
                matching_pattern.confidence,
                boost
            )

            # Update pattern usage stats
            self.pattern_repo.record_occurrence(
                matching_pattern.pattern_id,
                llm_confidence
            )

            return AnalysisEnhancement(
                original_confidence=llm_confidence,
                boosted_confidence=boosted,
                boost_applied=True,
                boost_amount=boost,
                matched_patterns=matches,
                pattern_context=self._format_pattern_context(matches)
            )

        else:
            # Patterns found but none match LLM's category - no boost
            logger.info("Patterns found but no category match (LLM chose: %s)", llm_category)
            return AnalysisEnhancement(
                original_confidence=llm_confidence,
                boosted_confidence=llm_confidence,
                boost_applied=False,
                boost_amount=0.0,
                matched_patterns=matches,
                pattern_context=self._format_pattern_context(matches)
            )

    def _find_best_category_match(
        self,
        matches: List[PatternMatch],
        llm_category: str
    ) -> Optional[PatternMatch]:
        """Find pattern that matches LLM's chosen category.

        Args:
            matches: List of pattern matches
            llm_category: Category chosen by LLM

        Returns:
            Best matching pattern, or None if no category match
        """
        for match in matches:
            if match.failure_category == llm_category:
                return match
        return None

    def _format_pattern_context(self, matches: List[PatternMatch]) -> str:
        """Format patterns into prompt section.

        Args:
            matches: List of pattern matches

        Returns:
            Formatted markdown text for injection into AI prompt
        """
        if not matches:
            return ""

        lines = ["\n## Known Patterns (Institutional Memory)\n"]
        lines.append(
            "The following patterns have been seen before. "
            "Use them as reference but verify against current evidence.\n"
        )

        for i, match in enumerate(matches, 1):
            lines.append(
                f"\n### Pattern {i}: {match.pattern_name} "
                f"({match.occurrence_count} occurrences, "
                f"avg confidence: {match.confidence:.2f})"
            )

            if match.typical_fix:
                lines.append(f"\n**Previous Solution:**\n{match.typical_fix}")

            if match.doc_context:
                # Truncate doc context
                doc = match.doc_context[:MAX_DOC_CONTEXT_LENGTH]
                lines.append(f"\n**Relevant Documentation:**\n{doc}")

        return '\n'.join(lines) + '\n'

    def get_matches_for_prompt(self, failure: Dict[str, Any]) -> str:
        """Get formatted pattern context for AI prompt.

        Convenience method for BuildFailureAnalyzer integration.

        Args:
            failure: Failure dict for pattern matching

        Returns:
            Formatted pattern section for prompt
        """
        matches = self.matcher.find_matches(failure, top_n=3)
        return self._format_pattern_context(matches)
