"""Calibration curve computation for AI analysis confidence scoring.

Bins predictions into confidence buckets and computes actual accuracy
per bucket, detecting systematic over/underconfidence.
"""

from dataclasses import dataclass

from logger import setup_logger

logger = setup_logger(__name__)

BUCKETS = [(0.3, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


@dataclass(frozen=True)
class CalibrationBucket:
    bucket_min: float
    bucket_max: float
    predicted_confidence: float
    actual_accuracy: float
    sample_size: int


class CalibrationService:
    """Computes calibration curves from AI analysis verdicts."""

    def __init__(self, ai_repo):
        self._ai_repo = ai_repo

    def compute_calibration_curve(self, analyzer_type=None, category=None, days=90):
        """Compute predicted vs actual accuracy for confidence buckets.

        Returns list of CalibrationBucket, one per bucket with >= 1 sample.
        """
        data = self._ai_repo.get_calibration_data(analyzer_type, category, days)
        if not data:
            return []

        return _bin_predictions(data)

    def get_per_category_calibration(self, analyzer_type, days=90):
        """Calibration curves per failure category.

        Returns dict of {category: [CalibrationBucket, ...]}.
        Only includes categories with >= 5 verdicts.
        """
        data = self._ai_repo.get_calibration_data(analyzer_type, days=days)
        if not data:
            return {}

        by_category = {}
        for row in data:
            cat = row.get('category', 'unknown')
            by_category.setdefault(cat, []).append(row)

        result = {}
        for cat, rows in by_category.items():
            if len(rows) >= 5:
                buckets = _bin_predictions(rows)
                if buckets:
                    result[cat] = buckets
        return result

    def compute_calibration_score(self, analyzer_type=None, days=90):
        """Compute overall calibration score (0-1, higher = better calibrated).

        Uses mean absolute deviation between predicted and actual.
        1.0 = perfectly calibrated, 0.0 = maximally miscalibrated.
        """
        curve = self.compute_calibration_curve(analyzer_type, days=days)
        if not curve:
            return None

        total_deviation = 0
        total_samples = 0
        for bucket in curve:
            deviation = abs(bucket.predicted_confidence - bucket.actual_accuracy)
            total_deviation += deviation * bucket.sample_size
            total_samples += bucket.sample_size

        if total_samples == 0:
            return None

        mean_deviation = total_deviation / total_samples
        return round(max(0, 1.0 - mean_deviation * 2), 2)


def _bin_predictions(data):
    """Bin prediction data into calibration buckets.

    Args:
        data: List of dicts with 'confidence' and 'accuracy' (0/0.5/1) keys.

    Returns:
        List of CalibrationBucket.
    """
    bins = {(lo, hi): [] for lo, hi in BUCKETS}

    for row in data:
        conf = row.get('confidence', 0.5)
        accuracy = row.get('accuracy', 0)
        for (lo, hi) in BUCKETS:
            if lo <= conf < hi:
                bins[(lo, hi)].append(accuracy)
                break

    result = []
    for (lo, hi), accuracies in sorted(bins.items()):
        if not accuracies:
            continue
        predicted = (lo + min(hi, 1.0)) / 2
        actual = sum(accuracies) / len(accuracies)
        result.append(CalibrationBucket(
            bucket_min=lo,
            bucket_max=min(hi, 1.0),
            predicted_confidence=round(predicted, 2),
            actual_accuracy=round(actual, 2),
            sample_size=len(accuracies),
        ))
    return result
