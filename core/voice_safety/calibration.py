"""Pure, deterministic wake-confidence calibration.

Calibration here is a pure function over synthetic labeled score
observations.  It performs no microphone access, filesystem access,
subprocesses, model loading, environment reads, or network access.  It:

- validates that observations are finite and bounded,
- accepts a caller-supplied maximum false-accept ceiling,
- selects a threshold only when sample coverage and safety constraints hold,
- returns ``not calibrated`` when no acceptable threshold exists, and
- never selects a permissive fallback.

Only aggregate counts and rates are reported — never individual scores.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import List, Optional, Sequence, Tuple

from core.voice_safety.contracts import BoundedConfidence


class CalibrationReason(StrEnum):
    """Stable, content-free calibration outcome codes."""

    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    NO_ACCEPTABLE_THRESHOLD = "no_acceptable_threshold"
    THRESHOLD_SELECTED = "threshold_selected"


@dataclass(frozen=True)
class LabeledScore:
    """One synthetic, labeled calibration observation.

    ``confidence`` is a bounded score in [0.0, 1.0]; ``is_owner`` labels the
    score as a true owner utterance (True) or an impostor (False).
    """

    confidence: BoundedConfidence
    is_owner: bool

    def __post_init__(self) -> None:
        if not isinstance(self.confidence, BoundedConfidence):
            raise TypeError("confidence must be a BoundedConfidence")
        if not isinstance(self.is_owner, bool):
            raise TypeError("is_owner must be a boolean")


@dataclass(frozen=True)
class CalibrationReport:
    """Aggregate-only calibration outcome (content-free)."""

    calibrated: bool
    threshold: Optional[float]
    false_accept_rate: float
    false_reject_rate: float
    true_accept_rate: float
    owner_sample_count: int
    impostor_sample_count: int
    reason: CalibrationReason

    @property
    def not_calibrated(self) -> bool:
        return not self.calibrated


def calibrate_threshold(
    observations: Sequence[LabeledScore],
    *,
    max_false_accept_rate: float,
    min_owner_samples: int = 3,
    min_impostor_samples: int = 3,
    min_total_samples: int = 6,
) -> CalibrationReport:
    """Select a wake confidence threshold from labeled observations.

    Args:
        observations: Synthetic labeled score observations (pure input).
        max_false_accept_rate: Maximum acceptable false-accept ceiling in
            [0.0, 1.0].  A threshold is only chosen when its empirical
            false-accept rate is at or below this ceiling.
        min_owner_samples: Minimum owner samples required for coverage.
        min_impostor_samples: Minimum impostor samples required for coverage.
        min_total_samples: Minimum total sample count required.

    Returns:
        A ``CalibrationReport``.  ``calibrated`` is False (and ``threshold`` is
        None) when coverage or safety constraints cannot be satisfied.

    Raises:
        ValueError: If the ceiling is out of bounds or observations contain
            non-finite/out-of-range scores (validation is fail-closed).
    """
    if isinstance(max_false_accept_rate, bool) or not isinstance(max_false_accept_rate, (int, float)):
        raise TypeError("max_false_accept_rate must be a number")
    ceiling = float(max_false_accept_rate)
    if not math.isfinite(ceiling) or not 0.0 <= ceiling <= 1.0:
        raise ValueError("max_false_accept_rate must be within [0.0, 1.0]")
    # Sample requirements must be positive integers (strict validation).
    for label, value in (
        ("min_owner_samples", min_owner_samples),
        ("min_impostor_samples", min_impostor_samples),
        ("min_total_samples", min_total_samples),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{label} must be a positive integer")

    # Validate every observation up front (fail-closed on malformed input).
    owner_scores: List[float] = []
    impostor_scores: List[float] = []
    for obs in observations:
        if not isinstance(obs, LabeledScore):
            raise TypeError("observations must contain LabeledScore values")
        score = obs.confidence.value
        if not math.isfinite(score):
            raise ValueError("observation scores must be finite")
        if not 0.0 <= score <= 1.0:
            raise ValueError("observation scores must be within [0.0, 1.0]")
        (owner_scores if obs.is_owner else impostor_scores).append(score)

    if (
        len(owner_scores) < min_owner_samples
        or len(impostor_scores) < min_impostor_samples
        or len(observations) < min_total_samples
    ):
        return CalibrationReport(
            calibrated=False,
            threshold=None,
            false_accept_rate=0.0,
            false_reject_rate=0.0,
            true_accept_rate=0.0,
            owner_sample_count=len(owner_scores),
            impostor_sample_count=len(impostor_scores),
            reason=CalibrationReason.INSUFFICIENT_COVERAGE,
        )

    # Candidate thresholds: every distinct observed score, swept in ascending
    # order so the selection is exhaustive and deterministic.  A threshold
    # above every score is intentionally not considered: it would reject every
    # owner, which is never a valid calibration (the ``frr >= 1.0`` guard below
    # additionally rejects any such degenerate candidate).
    candidate_thresholds = sorted(set(owner_scores) | set(impostor_scores))

    best: Optional[Tuple[float, float, float]] = None  # (threshold, far, frr)
    for threshold in candidate_thresholds:
        false_accepts = sum(1 for s in impostor_scores if s >= threshold)
        false_rejects = sum(1 for s in owner_scores if s < threshold)
        far = false_accepts / len(impostor_scores)
        frr = false_rejects / len(owner_scores)
        # A threshold that rejects every owner is a degenerate "reject all"
        # result, not a calibration; it is never selected.
        if frr >= 1.0:
            continue
        if far <= ceiling:
            # Prefer the lowest false-reject rate; tie-break toward the higher
            # (more conservative) threshold.
            if best is None or frr < best[2] or (frr == best[2] and threshold > best[0]):
                best = (threshold, far, frr)

    if best is None:
        return CalibrationReport(
            calibrated=False,
            threshold=None,
            false_accept_rate=0.0,
            false_reject_rate=0.0,
            true_accept_rate=0.0,
            owner_sample_count=len(owner_scores),
            impostor_sample_count=len(impostor_scores),
            reason=CalibrationReason.NO_ACCEPTABLE_THRESHOLD,
        )

    threshold, far, frr = best
    return CalibrationReport(
        calibrated=True,
        threshold=threshold,
        false_accept_rate=far,
        false_reject_rate=frr,
        true_accept_rate=1.0 - frr,
        owner_sample_count=len(owner_scores),
        impostor_sample_count=len(impostor_scores),
        reason=CalibrationReason.THRESHOLD_SELECTED,
    )
