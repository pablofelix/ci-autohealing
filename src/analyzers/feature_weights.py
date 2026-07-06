"""Feature weight service for evidence-based confidence scoring.

Maintains learned weights per feature, updated incrementally from verdicts.
Computes weighted confidence scores from FeatureVector inputs.
"""

from dataclasses import dataclass

from logger import setup_logger

logger = setup_logger(__name__)

LEARNING_RATE = 0.1
MIN_WEIGHT = 0.0
MAX_WEIGHT = 2.0


@dataclass(frozen=True)
class FeatureWeight:
    feature_name: str
    weight: float
    sample_size: int


class FeatureWeightService:
    """Computes and learns feature weights for confidence scoring."""

    def __init__(self, db):
        self._db = db
        self._weights = {}
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        try:
            with self._db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT feature_name, weight, sample_size FROM feature_weights")
                for row in cursor.fetchall():
                    self._weights[row[0]] = FeatureWeight(
                        feature_name=row[0], weight=row[1], sample_size=row[2]
                    )
            self._loaded = True
        except Exception:
            logger.debug("Failed to load feature weights, using defaults", exc_info=True)
            self._loaded = True

    def compute_score(self, features):
        """Compute weighted score from a FeatureVector.

        Returns normalized score in [0.0, 1.0].
        """
        self._ensure_loaded()

        feature_dict = features.to_dict()
        total_weight = 0
        max_possible = 0

        for name, active in feature_dict.items():
            fw = self._weights.get(name)
            w = fw.weight if fw else 1.0
            max_possible += w
            if active:
                total_weight += w

        if max_possible <= 0:
            return 0.5

        return total_weight / max_possible

    def update_from_verdict(self, features, was_correct):
        """Update weights based on verdict outcome.

        Increments weights for active features if prediction was correct,
        decrements if incorrect. Uses exponential moving average.
        """
        self._ensure_loaded()
        feature_dict = features.to_dict()
        delta = LEARNING_RATE if was_correct else -LEARNING_RATE

        pending_updates = {}
        try:
            with self._db.connection() as conn:
                cursor = conn.cursor()
                for name, active in feature_dict.items():
                    if not active:
                        continue
                    fw = self._weights.get(name)
                    current = fw.weight if fw else 1.0
                    new_weight = max(MIN_WEIGHT, min(MAX_WEIGHT, current + delta))
                    new_sample = (fw.sample_size + 1) if fw else 1

                    cursor.execute("""
                        INSERT INTO feature_weights (feature_name, weight, sample_size, last_updated)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (feature_name) DO UPDATE SET
                            weight = %s,
                            sample_size = feature_weights.sample_size + 1,
                            last_updated = NOW()
                    """, (name, new_weight, new_sample, new_weight))

                    pending_updates[name] = FeatureWeight(
                        feature_name=name, weight=new_weight, sample_size=new_sample
                    )
                conn.commit()
                self._weights.update(pending_updates)
        except Exception:
            logger.warning("Failed to update feature weights", exc_info=True)

    def get_feature_importance(self):
        """Return features sorted by weight (highest first)."""
        self._ensure_loaded()
        return sorted(
            [{'name': fw.feature_name, 'weight': round(fw.weight, 2),
              'sample_size': fw.sample_size}
             for fw in self._weights.values()],
            key=lambda x: x['weight'],
            reverse=True,
        )
