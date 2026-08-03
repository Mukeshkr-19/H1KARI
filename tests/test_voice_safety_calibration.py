"""Focused tests for pure wake-confidence calibration.

These tests perform no external I/O: no microphone, filesystem, subprocess,
model, environment, or network access.  Calibration operates only on synthetic
labeled score observations supplied in-process.
"""

from __future__ import annotations

import pytest

from core.voice_safety.calibration import CalibrationReport, LabeledScore, calibrate_threshold
from core.voice_safety.contracts import BoundedConfidence


def _obs(score: float, is_owner: bool) -> LabeledScore:
    return LabeledScore(confidence=BoundedConfidence(score), is_owner=is_owner)


def _clean_owners() -> list:
    return [_obs(s, True) for s in (0.92, 0.95, 0.88, 0.97, 0.9)]


def _clean_impostors() -> list:
    return [_obs(s, False) for s in (0.2, 0.3, 0.25, 0.4, 0.35)]


# ---------------------------------------------------------------------------
# Validation (fail-closed on malformed input)
# ---------------------------------------------------------------------------


def test_non_finite_scores_are_rejected() -> None:
    # Non-finite scores fail closed at the bounded-contract boundary.
    with pytest.raises(ValueError):
        _obs(float("nan"), False)
    with pytest.raises(ValueError):
        _obs(float("inf"), False)
    # Calibration itself rejects non-finite input if it ever arrives.
    bypass = object.__new__(LabeledScore)
    object.__setattr__(bypass, "confidence", BoundedConfidence(0.5))
    object.__setattr__(bypass, "is_owner", False)
    bad = object.__new__(LabeledScore)
    bad_conf = object.__new__(BoundedConfidence)
    object.__setattr__(bad_conf, "value", float("nan"))
    object.__setattr__(bad, "confidence", bad_conf)
    object.__setattr__(bad, "is_owner", False)
    with pytest.raises(ValueError):
        calibrate_threshold([bypass, bad], max_false_accept_rate=0.01)


def test_out_of_bounds_scores_are_rejected() -> None:
    with pytest.raises(ValueError):
        BoundedConfidence(1.5)
    with pytest.raises(ValueError):
        BoundedConfidence(-0.1)


def test_invalid_ceiling_is_rejected() -> None:
    with pytest.raises(ValueError):
        calibrate_threshold([_obs(0.5, True)], max_false_accept_rate=1.5)


def test_sample_requirements_must_be_positive_integers() -> None:
    observations = _clean_owners() + _clean_impostors()
    with pytest.raises(ValueError):
        calibrate_threshold(observations, max_false_accept_rate=0.1, min_owner_samples=0)
    with pytest.raises(ValueError):
        calibrate_threshold(observations, max_false_accept_rate=0.1, min_impostor_samples=2.5)
    with pytest.raises(ValueError):
        calibrate_threshold(observations, max_false_accept_rate=0.1, min_total_samples=-1)


def test_reason_is_a_stable_enum_code() -> None:
    from core.voice_safety.calibration import CalibrationReason

    observations = _clean_owners() + _clean_impostors()
    report = calibrate_threshold(observations, max_false_accept_rate=0.2)
    assert isinstance(report.reason, CalibrationReason)
    assert report.reason == CalibrationReason.THRESHOLD_SELECTED


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_insufficient_coverage_returns_not_calibrated() -> None:
    observations = [_obs(0.9, True), _obs(0.95, True)]
    report = calibrate_threshold(
        observations, max_false_accept_rate=0.01, min_owner_samples=3, min_impostor_samples=3
    )
    assert report.calibrated is False
    assert report.not_calibrated is True
    assert report.threshold is None
    assert report.reason == "insufficient_coverage"


def test_missing_impostor_samples_returns_not_calibrated() -> None:
    observations = _clean_owners() + [_obs(0.3, False)]
    report = calibrate_threshold(
        observations, max_false_accept_rate=0.01, min_impostor_samples=3
    )
    assert report.calibrated is False
    assert report.threshold is None


# ---------------------------------------------------------------------------
# Threshold selection
# ---------------------------------------------------------------------------


def test_chooses_threshold_within_false_accept_ceiling() -> None:
    observations = _clean_owners() + _clean_impostors()
    report = calibrate_threshold(observations, max_false_accept_rate=0.2)
    assert report.calibrated is True
    assert report.threshold is not None
    assert report.false_accept_rate <= 0.2
    assert 0.0 <= report.threshold <= 1.0
    assert report.true_accept_rate == 1.0 - report.false_reject_rate


def test_no_acceptable_threshold_fails_closed() -> None:
    # Impostor scores overlap owners so tightly that a 0% ceiling cannot be met.
    observations = [
        _obs(0.9, True),
        _obs(0.91, True),
        _obs(0.92, True),
        _obs(0.91, False),
        _obs(0.92, False),
        _obs(0.9, False),
    ]
    report = calibrate_threshold(observations, max_false_accept_rate=0.0)
    assert report.calibrated is False
    assert report.threshold is None
    assert report.reason == "no_acceptable_threshold"


def test_never_selects_a_permissive_fallback() -> None:
    # With an impossible ceiling the calibration must NOT relax the ceiling to
    # produce a threshold; it reports not calibrated.
    observations = [
        _obs(0.9, True),
        _obs(0.91, True),
        _obs(0.92, True),
        _obs(0.91, False),
        _obs(0.92, False),
        _obs(0.9, False),
    ]
    strict = calibrate_threshold(observations, max_false_accept_rate=0.0)
    loose = calibrate_threshold(observations, max_false_accept_rate=0.5)
    assert strict.calibrated is False
    assert strict.threshold is None
    assert loose.calibrated is True
    assert loose.false_accept_rate <= 0.5


def test_threshold_prefers_lower_false_reject_then_higher_threshold() -> None:
    observations = (
        _clean_owners()
        + _clean_impostors()
        + [_obs(0.85, True), _obs(0.83, True), _obs(0.82, True)]
    )
    report = calibrate_threshold(observations, max_false_accept_rate=0.2)
    assert report.calibrated is True
    assert report.threshold is not None
    # Chosen threshold must accept the true owners it was trained to accept.
    assert report.true_accept_rate >= 0.8


# ---------------------------------------------------------------------------
# Aggregate reporting (content-free)
# ---------------------------------------------------------------------------


def test_report_contains_aggregate_counts_only() -> None:
    observations = _clean_owners() + _clean_impostors()
    report = calibrate_threshold(observations, max_false_accept_rate=0.2)
    assert isinstance(report, CalibrationReport)
    assert report.owner_sample_count == 5
    assert report.impostor_sample_count == 5
    assert report.threshold is not None
    assert 0.0 <= report.false_accept_rate <= 1.0
    assert 0.0 <= report.false_reject_rate <= 1.0
    assert 0.0 <= report.true_accept_rate <= 1.0


def test_aggregate_counts_on_not_calibrated_report() -> None:
    observations = [_obs(0.9, True), _obs(0.95, True)]
    report = calibrate_threshold(observations, max_false_accept_rate=0.01)
    assert report.calibrated is False
    assert report.owner_sample_count == 2
    assert report.impostor_sample_count == 0


def test_calibration_is_deterministic() -> None:
    observations = _clean_owners() + _clean_impostors()
    first = calibrate_threshold(observations, max_false_accept_rate=0.2)
    second = calibrate_threshold(observations, max_false_accept_rate=0.2)
    assert first == second
    assert first.threshold == second.threshold


# ---------------------------------------------------------------------------
# Strict validation and fail-closed guarantees
# ---------------------------------------------------------------------------


def test_sample_requirements_reject_booleans() -> None:
    observations = _clean_owners() + _clean_impostors()
    with pytest.raises(ValueError):
        calibrate_threshold(observations, max_false_accept_rate=0.1, min_owner_samples=True)
    with pytest.raises(ValueError):
        calibrate_threshold(observations, max_false_accept_rate=0.1, min_total_samples=False)


def test_ceiling_rejects_boolean() -> None:
    with pytest.raises(TypeError):
        calibrate_threshold([_obs(0.5, True)], max_false_accept_rate=True)


def test_no_division_by_zero_path() -> None:
    # Owner-only and impostor-only data sets fail closed on coverage without
    # ever reaching a rate computation.
    only_impostors = calibrate_threshold(
        [_obs(0.4, False), _obs(0.5, False), _obs(0.6, False)],
        max_false_accept_rate=0.1,
    )
    assert only_impostors.calibrated is False
    assert only_impostors.reason == "insufficient_coverage"
    only_owners = calibrate_threshold(
        [_obs(0.9, True), _obs(0.95, True), _obs(0.8, True)],
        max_false_accept_rate=0.1,
    )
    assert only_owners.calibrated is False
    assert only_owners.reason == "insufficient_coverage"


def test_calibrated_threshold_never_rejects_every_owner() -> None:
    observations = _clean_owners() + _clean_impostors()
    report = calibrate_threshold(observations, max_false_accept_rate=0.2)
    assert report.calibrated is True
    # A calibrated threshold must accept at least one owner sample; a
    # "reject every owner" threshold is never a valid calibration.
    assert report.true_accept_rate > 0.0


def test_overlarge_sample_requirements_fail_closed_without_crashing() -> None:
    observations = _clean_owners() + _clean_impostors()
    report = calibrate_threshold(
        observations, max_false_accept_rate=0.2, min_total_samples=10**12
    )
    assert report.calibrated is False
    assert report.reason == "insufficient_coverage"
    assert report.threshold is None
