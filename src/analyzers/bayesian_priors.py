"""Bayesian confidence adjustment using historical verdict data.

Computes P(correct | category) from accumulated verdicts, with bootstrapping
to global accuracy when sample size is too small.
"""

import time
from dataclasses import dataclass

from logger import setup_logger

logger = setup_logger(__name__)

MIN_SAMPLE_SIZE = 10
CACHE_TTL = 300  # 5 minutes
BOOTSTRAP_BLEND = 0.7  # weight for global prior when bootstrapping


@dataclass(frozen=True)
class CategoryPrior:
    category: str
    prior_correct: float
    global_correct: float
    sample_size: int
    is_bootstrapped: bool


class BayesianPriorService:
    """Provides category-specific Bayesian priors from historical verdicts."""

    def __init__(self, ai_repo):
        self._ai_repo = ai_repo
        self._cache = {}
        self._global_cache = None
        self._last_refresh = 0

    def get_prior(self, category, analyzer_type='build'):
        """Get Bayesian prior for a failure category.

        Returns CategoryPrior with P(correct | category) and metadata.
        Bootstraps to global accuracy when < MIN_SAMPLE_SIZE verdicts.
        """
        self._maybe_refresh(analyzer_type)

        global_correct = self._global_cache or 0.5
        cached = self._cache.get((category, analyzer_type))

        if cached and cached.sample_size >= MIN_SAMPLE_SIZE:
            return cached

        if cached:
            blended = (BOOTSTRAP_BLEND * global_correct +
                       (1 - BOOTSTRAP_BLEND) * cached.prior_correct)
            return CategoryPrior(
                category=category,
                prior_correct=blended,
                global_correct=global_correct,
                sample_size=cached.sample_size,
                is_bootstrapped=True,
            )

        return CategoryPrior(
            category=category,
            prior_correct=global_correct,
            global_correct=global_correct,
            sample_size=0,
            is_bootstrapped=True,
        )

    def adjust_confidence(self, llm_confidence, category, analyzer_type='build'):
        """Apply Bayesian adjustment to raw LLM confidence.

        Formula: adjusted = llm_conf * P(correct|category) / P(correct)
        Clamped to [0.1, 0.95].
        """
        prior = self.get_prior(category, analyzer_type)

        if prior.global_correct <= 0 or prior.sample_size == 0:
            return llm_confidence

        ratio = prior.prior_correct / prior.global_correct
        adjusted = llm_confidence * ratio
        return max(0.1, min(0.95, adjusted))

    def _maybe_refresh(self, analyzer_type):
        now = time.time()
        if now - self._last_refresh < CACHE_TTL:
            return

        try:
            stats = self._ai_repo.get_verdict_stats_by_category(analyzer_type)
            if not stats:
                return

            total_correct = 0
            total_judged = 0

            for row in stats:
                cat = row['category']
                correct = row.get('correct', 0) + row.get('partial', 0) * 0.5
                total = row.get('total', 0)
                if total > 0:
                    self._cache[(cat, analyzer_type)] = CategoryPrior(
                        category=cat,
                        prior_correct=correct / total,
                        global_correct=0,
                        sample_size=total,
                        is_bootstrapped=total < MIN_SAMPLE_SIZE,
                    )
                    total_correct += correct
                    total_judged += total

            self._global_cache = (total_correct / total_judged) if total_judged > 0 else 0.5

            for key, prior in self._cache.items():
                if key[1] == analyzer_type:
                    self._cache[key] = CategoryPrior(
                        category=prior.category,
                        prior_correct=prior.prior_correct,
                        global_correct=self._global_cache,
                        sample_size=prior.sample_size,
                        is_bootstrapped=prior.is_bootstrapped,
                    )

            self._last_refresh = now
            logger.debug("Bayesian priors refreshed: %d categories, global=%.2f",
                         len(stats), self._global_cache)
        except Exception:
            logger.debug("Failed to refresh Bayesian priors", exc_info=True)
