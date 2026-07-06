"""Abstract pattern matcher interface.

Defines the contract for pattern matching strategies. Implementations can use:
- Category-based matching (current, simple)
- Embedding-based similarity search (future, ML-powered)
- LLM-based semantic matching (future, expensive but accurate)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class PatternMatch:
    """A matched pattern with its metadata.

    Attributes:
        pattern_id: Database ID of the pattern
        pattern_name: Human-readable pattern name
        failure_category: Category (e.g., 'dependency_issue')
        confidence: Pattern's historical avg confidence (0.0-1.0)
        occurrence_count: How many times this pattern was seen
        match_score: How well this pattern matches (1.0 = exact, <1.0 = fuzzy)
        typical_fix: Solution that worked before (optional)
        doc_context: Relevant documentation (optional)
    """
    pattern_id: int
    pattern_name: str
    failure_category: str
    confidence: float
    occurrence_count: int
    match_score: float
    typical_fix: str = None
    doc_context: str = None


class PatternMatcher(ABC):
    """Abstract interface for pattern matching strategies.

    Enables swapping matching algorithms without changing consumers.

    Example implementations:
    - CategoryBasedMatcher: Match on exact failure_category
    - EmbeddingMatcher: Cosine similarity on error message embeddings
    - HybridMatcher: Combine multiple strategies

    Future-proofing:
    - Add EmbeddingMatcher when we have enough patterns (P1)
    - Add LLMBasedMatcher for semantic understanding (P2)
    - Keep CategoryBasedMatcher as fast fallback
    """

    @abstractmethod
    def find_matches(
        self,
        failure: Dict[str, Any],
        top_n: int = 3
    ) -> List[PatternMatch]:
        """Find top N matching patterns for a failure.

        Args:
            failure: Failure dict from database with at minimum:
                - error_type: Type of error
                - error_message: Error message text
                - failure_category: AI's previous category (if re-analyzing)
            top_n: Number of patterns to return (default: 3)

        Returns:
            List of PatternMatch objects, ranked by relevance.
            Empty list if no matches found.

        Design note:
            Implementations should rank by:
            1. Match quality (exact > fuzzy)
            2. Occurrence count (common patterns first)
            3. Historical confidence (high confidence patterns first)
        """
        pass

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """Identifier for this matching strategy.

        Returns:
            Short name (e.g., 'category', 'embedding', 'llm')
        """
        pass
