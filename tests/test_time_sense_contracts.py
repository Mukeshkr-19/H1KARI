"""Tests for the immutable Time Sense contracts.

These cover construction validation, immutability, timezone-awareness
enforcement, and the bounded-text / evidence-code helpers.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.time_sense.contracts import (
    AwarenessSnapshot,
    BackgroundActivity,
    DEFAULT_MAX_FUTURE_HORIZON,
    MAX_IDENTIFIER_LENGTH,
    MAX_PHRASE_CHARS,
    MAX_SNAPSHOT_ITEMS,
    MIN_FUTURE_HORIZON,
    MINUTES_PER_DAY,
    NotificationAdvice,
    NotificationRecommendation,
    QuietHoursContext,
    StuckAssessment,
    StuckReason,
    TaskProgressObservation,
    TaskProgressState,
    TemporalInterpretation,
    TemporalPrecision,
    TimeReference,
)


NOW_AWARE = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 7, 23, 12, 0)


# ---------------------------------------------------------------------------
# TimeReference / TemporalInterpretation
# ---------------------------------------------------------------------------


def test_time_reference_requires_aware_datetime():
    with pytest.raises(ValueError):
        TimeReference(reference_at=NAIVE, phrase="soon")


def test_time_reference_requires_non_empty_phrase():
    with pytest.raises(ValueError):
        TimeReference(reference_at=NOW_AWARE, phrase="   ")


def test_time_reference_strips_phrase():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="  soon  ")
    assert ref.phrase == "soon"


def test_time_reference_is_frozen():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="soon")
    with pytest.raises(FrozenInstanceError):
        ref.reference_at = NOW_AWARE + timedelta(hours=1)  # type: ignore[misc]


def test_time_reference_timezone_name_for_zoneinfo():
    ref = TimeReference(
        reference_at=datetime(2026, 7, 23, 12, tzinfo=ZoneInfo("Asia/Kolkata")),
        phrase="soon",
    )
    assert ref.timezone_name == "Asia/Kolkata"


def test_temporal_interpretation_instant_requires_resolution():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="in 20 minutes")
    with pytest.raises(ValueError):
        TemporalInterpretation(
            original_phrase="in 20 minutes",
            precision=TemporalPrecision.INSTANT,
            confidence=0.5,
            reference=ref,
        )


def test_temporal_interpretation_instant_rejects_window():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="in 20 minutes")
    with pytest.raises(ValueError):
        TemporalInterpretation(
            original_phrase="in 20 minutes",
            precision=TemporalPrecision.INSTANT,
            confidence=0.5,
            reference=ref,
            resolution=NOW_AWARE + timedelta(minutes=20),
            window=(NOW_AWARE, NOW_AWARE + timedelta(hours=1)),
        )


def test_temporal_interpretation_instant_rejects_naive_resolution():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="in 20 minutes")
    with pytest.raises(ValueError):
        TemporalInterpretation(
            original_phrase="in 20 minutes",
            precision=TemporalPrecision.INSTANT,
            confidence=0.5,
            reference=ref,
            resolution=NAIVE,
        )


def test_temporal_interpretation_window_requires_window():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="this afternoon")
    with pytest.raises(ValueError):
        TemporalInterpretation(
            original_phrase="this afternoon",
            precision=TemporalPrecision.WINDOW,
            confidence=0.7,
            reference=ref,
        )


def test_temporal_interpretation_window_rejects_resolution():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="this afternoon")
    with pytest.raises(ValueError):
        TemporalInterpretation(
            original_phrase="this afternoon",
            precision=TemporalPrecision.WINDOW,
            confidence=0.7,
            reference=ref,
            resolution=NOW_AWARE,
            window=(NOW_AWARE, NOW_AWARE + timedelta(hours=1)),
        )


def test_temporal_interpretation_vague_requires_clarification():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="soon")
    with pytest.raises(ValueError):
        TemporalInterpretation(
            original_phrase="soon",
            precision=TemporalPrecision.VAGUE,
            confidence=0.2,
            reference=ref,
            window=(NOW_AWARE, NOW_AWARE + timedelta(hours=1)),
            requires_clarification=False,
        )


def test_temporal_interpretation_confidence_must_be_in_unit_range():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="in 20 minutes")
    with pytest.raises(ValueError):
        TemporalInterpretation(
            original_phrase="in 20 minutes",
            precision=TemporalPrecision.INSTANT,
            confidence=1.5,
            reference=ref,
            resolution=NOW_AWARE + timedelta(minutes=20),
        )


def test_temporal_interpretation_window_end_must_not_precede_start():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="this afternoon")
    with pytest.raises(ValueError):
        TemporalInterpretation(
            original_phrase="this afternoon",
            precision=TemporalPrecision.WINDOW,
            confidence=0.5,
            reference=ref,
            window=(NOW_AWARE + timedelta(hours=2), NOW_AWARE),
        )


def test_temporal_interpretation_is_frozen():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="in 20 minutes")
    interp = TemporalInterpretation(
        original_phrase="in 20 minutes",
        precision=TemporalPrecision.INSTANT,
        confidence=0.95,
        reference=ref,
        resolution=NOW_AWARE + timedelta(minutes=20),
    )
    with pytest.raises(FrozenInstanceError):
        interp.confidence = 0.1  # type: ignore[misc]


def test_temporal_interpretation_resolved_property():
    ref = TimeReference(reference_at=NOW_AWARE, phrase="in 20 minutes")
    instant = TemporalInterpretation(
        original_phrase="in 20 minutes",
        precision=TemporalPrecision.INSTANT,
        confidence=0.95,
        reference=ref,
        resolution=NOW_AWARE + timedelta(minutes=20),
    )
    assert instant.resolved is True
    window = TemporalInterpretation(
        original_phrase="this afternoon",
        precision=TemporalPrecision.WINDOW,
        confidence=0.7,
        reference=ref,
        window=(NOW_AWARE, NOW_AWARE + timedelta(hours=1)),
    )
    assert window.resolved is False


# ---------------------------------------------------------------------------
# TaskProgressObservation / StuckAssessment
# ---------------------------------------------------------------------------


def _observation(**overrides) -> TaskProgressObservation:
    base = dict(
        task_id="task-aurora-1",
        kind="deploy",
        state=TaskProgressState.MAKING_PROGRESS,
        observed_at=NOW_AWARE,
        evidence_codes=("e1", "e2"),
    )
    base.update(overrides)
    return TaskProgressObservation(**base)


def test_observation_requires_aware_observed_at():
    with pytest.raises(ValueError):
        _observation(observed_at=NAIVE)


def test_observation_rejects_negative_counts():
    with pytest.raises(ValueError):
        _observation(last_error_count=-1)


def test_observation_rejects_bool_counts():
    with pytest.raises(ValueError):
        _observation(attempt_count=True)


def test_observation_rejects_naive_parsed_at():
    with pytest.raises(ValueError):
        _observation(parsed_at=NAIVE)


def test_observation_rejects_naive_last_progress_at():
    with pytest.raises(ValueError):
        _observation(last_progress_at=NAIVE)


def test_observation_rejects_non_bool_overdue():
    with pytest.raises(ValueError):
        _observation(overdue="yes")  # type: ignore[arg-type]


def test_observation_rejects_invalid_evidence_code_chars():
    with pytest.raises(ValueError):
        _observation(evidence_codes=("bad code with space",))


def test_observation_rejects_oversize_evidence_code():
    with pytest.raises(ValueError):
        _observation(evidence_codes=("x" * 100,))


def test_observation_is_external_block_for_approval():
    obs = _observation(
        waiting_for_approval=True,
        state=TaskProgressState.WAITING_FOR_APPROVAL,
    )
    assert obs.is_external_block is True


def test_observation_is_external_block_for_user_input():
    obs = _observation(
        waiting_for_user_input=True,
        state=TaskProgressState.WAITING_FOR_USER_INPUT,
    )
    assert obs.is_external_block is True


def test_observation_is_external_block_false_when_neither():
    obs = _observation()
    assert obs.is_external_block is False


def test_observation_is_frozen():
    obs = _observation()
    with pytest.raises(FrozenInstanceError):
        obs.state = TaskProgressState.UNKNOWN  # type: ignore[misc]


def test_stuck_assessment_non_stuck_must_use_not_stuck_reason():
    with pytest.raises(ValueError):
        StuckAssessment(
            task_id="t1",
            stuck=False,
            reason=StuckReason.OVERDUE,
            severity=0.0,
            confidence=0.1,
        )


def test_stuck_assessment_stuck_must_not_use_not_stuck_reason():
    with pytest.raises(ValueError):
        StuckAssessment(
            task_id="t1",
            stuck=True,
            reason=StuckReason.NOT_STUCK,
            severity=0.5,
            confidence=0.5,
        )


def test_stuck_assessment_severity_must_be_in_unit_range():
    with pytest.raises(ValueError):
        StuckAssessment(
            task_id="t1",
            stuck=True,
            reason=StuckReason.OVERDUE,
            severity=1.2,
            confidence=0.5,
        )


def test_stuck_assessment_confidence_must_be_in_unit_range():
    with pytest.raises(ValueError):
        StuckAssessment(
            task_id="t1",
            stuck=True,
            reason=StuckReason.OVERDUE,
            severity=0.5,
            confidence=-0.1,
        )


def test_stuck_assessment_rejects_invalid_evidence_code():
    with pytest.raises(ValueError):
        StuckAssessment(
            task_id="t1",
            stuck=True,
            reason=StuckReason.OVERDUE,
            severity=0.5,
            confidence=0.5,
            evidence_codes=("bad code!",),
        )


def test_stuck_assessment_is_frozen():
    assessment = StuckAssessment(
        task_id="t1",
        stuck=True,
        reason=StuckReason.OVERDUE,
        severity=0.5,
        confidence=0.5,
    )
    with pytest.raises(FrozenInstanceError):
        assessment.stuck = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# QuietHoursContext / NotificationRecommendation / BackgroundActivity
# ---------------------------------------------------------------------------


def test_quiet_hours_rejects_out_of_range_minute():
    with pytest.raises(ValueError):
        QuietHoursContext(
            timezone_name="UTC",
            active=True,
            start_minute=0,
            end_minute=MINUTES_PER_DAY,
            suppressed=False,
        )


def test_quiet_hours_rejects_empty_timezone_name():
    with pytest.raises(ValueError):
        QuietHoursContext(
            timezone_name="   ",
            active=True,
            start_minute=0,
            end_minute=60,
            suppressed=False,
        )


def test_quiet_hours_rejects_non_bool_active():
    with pytest.raises(ValueError):
        QuietHoursContext(
            timezone_name="UTC",
            active="yes",  # type: ignore[arg-type]
            start_minute=0,
            end_minute=60,
            suppressed=False,
        )


def test_notification_recommendation_delivered_must_not_be_suppressed():
    with pytest.raises(ValueError):
        NotificationRecommendation(
            advice=NotificationAdvice.SUPPRESS,
            delivered=True,
        )


def test_notification_recommendation_must_not_deliver_during_active_quiet_hours():
    quiet = QuietHoursContext(
        timezone_name="UTC",
        active=True,
        start_minute=0,
        end_minute=360,
        suppressed=False,
    )
    with pytest.raises(ValueError):
        NotificationRecommendation(
            advice=NotificationAdvice.DELIVER,
            quiet_hours=quiet,
            delivered=False,
        )


def test_background_activity_requires_aware_observed_at():
    with pytest.raises(ValueError):
        BackgroundActivity(
            item_id="i1",
            kind="job",
            status="running",
            observed_at=NAIVE,
        )


def test_background_activity_is_frozen():
    activity = BackgroundActivity(
        item_id="i1",
        kind="job",
        status="running",
        observed_at=NOW_AWARE,
    )
    with pytest.raises(FrozenInstanceError):
        activity.kind = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AwarenessSnapshot
# ---------------------------------------------------------------------------


def _activity(name: str) -> BackgroundActivity:
    return BackgroundActivity(
        item_id=name,
        kind="job",
        status="running",
        observed_at=NOW_AWARE,
    )


def test_awareness_snapshot_requires_aware_reference_at():
    with pytest.raises(ValueError):
        AwarenessSnapshot(reference_at=NAIVE)


def test_awareness_snapshot_rejects_non_background_activity_items():
    with pytest.raises(ValueError):
        AwarenessSnapshot(
            reference_at=NOW_AWARE,
            active_tasks=("not-an-activity",),  # type: ignore[arg-type]
        )


def test_awareness_snapshot_rejects_oversize_bucket():
    too_many = tuple(_activity(f"i{i}") for i in range(MAX_SNAPSHOT_ITEMS + 1))
    with pytest.raises(ValueError):
        AwarenessSnapshot(
            reference_at=NOW_AWARE,
            active_tasks=too_many,
        )


def test_awareness_snapshot_emptiness_true_when_all_buckets_empty():
    snap = AwarenessSnapshot(reference_at=NOW_AWARE)
    assert snap.emptiness is True


def test_awareness_snapshot_emptiness_false_when_any_bucket_filled():
    snap = AwarenessSnapshot(
        reference_at=NOW_AWARE,
        active_tasks=(_activity("i1"),),
    )
    assert snap.emptiness is False


def test_awareness_snapshot_is_frozen():
    snap = AwarenessSnapshot(reference_at=NOW_AWARE)
    with pytest.raises(FrozenInstanceError):
        snap.active_tasks = (_activity("i1"),)  # type: ignore[misc]
