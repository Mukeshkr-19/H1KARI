"""Tests for bounded, caller-supplied background-work awareness."""

from datetime import datetime, timedelta, timezone

from core.time_sense.background_awareness import (
    AwarenessBuilder,
    build_awareness,
    recommend_notification,
)
from core.time_sense.contracts import (
    BackgroundActivity,
    NotificationAdvice,
    QuietHoursContext,
    TaskProgressObservation,
    TaskProgressState,
)


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def _observation(**overrides):
    values = {
        "task_id": "task-1",
        "kind": "build",
        "state": TaskProgressState.MAKING_PROGRESS,
        "observed_at": NOW,
    }
    values.update(overrides)
    return TaskProgressObservation(**values)


def _activity(item_id="done-1"):
    return BackgroundActivity(
        item_id=item_id,
        kind="build",
        status="completed",
        observed_at=NOW,
    )


def _quiet_hours(*, active=True, suppressed=False):
    return QuietHoursContext(
        timezone_name="UTC",
        active=active,
        start_minute=1320,
        end_minute=420,
        suppressed=suppressed,
    )


def test_generator_input_is_preserved_for_summary_validation():
    observations = (_observation() for _ in range(1))
    snapshot = build_awareness(
        now=NOW,
        observations=observations,
        summary="I am still working on the build.",
    )
    assert len(snapshot.active_tasks) == 1
    assert snapshot.summary == "I am still working on the build."


def test_unverified_active_claim_is_removed():
    snapshot = build_awareness(
        now=NOW,
        observations=(_observation(state=TaskProgressState.UNKNOWN),),
        summary="I am still working on the build.",
    )
    assert snapshot.summary is None


def test_unverified_all_completed_claim_is_removed():
    snapshot = build_awareness(
        now=NOW,
        observations=(),
        summary="All tasks completed.",
    )
    assert snapshot.summary is None


def test_completed_claim_requires_completed_evidence():
    snapshot = build_awareness(
        now=NOW,
        observations=(),
        recently_completed=(_activity(),),
        summary="All supplied tasks completed.",
    )
    assert snapshot.summary == "All supplied tasks completed."


def test_stuck_reason_hint_is_private_by_default():
    observation = _observation(
        state=TaskProgressState.NO_PROGRESS,
        last_progress_at=NOW - timedelta(hours=2),
    )
    snapshot = build_awareness(now=NOW, observations=(observation,))
    assert snapshot.stuck_candidates[0].payload_hint is None


def test_stuck_reason_hint_requires_explicit_opt_in():
    observation = _observation(
        state=TaskProgressState.NO_PROGRESS,
        last_progress_at=NOW - timedelta(hours=2),
    )
    snapshot = build_awareness(
        now=NOW,
        observations=(observation,),
        include_payload_hint=True,
    )
    assert snapshot.stuck_candidates[0].payload_hint == "no_progress_delayed"


def test_external_wait_is_not_reported_as_active_or_stuck():
    observation = _observation(
        state=TaskProgressState.WAITING_FOR_APPROVAL,
        waiting_for_approval=True,
    )
    snapshot = build_awareness(now=NOW, observations=(observation,))
    assert len(snapshot.approval_blocked) == 1
    assert not snapshot.active_tasks
    assert not snapshot.stuck_candidates


def test_quiet_hours_delivery_failure_is_suppressed_not_stuck():
    observation = _observation(
        state=TaskProgressState.DELIVERY_FAILED,
        delivery_failed=True,
        quiet_hours_active=True,
    )
    snapshot = build_awareness(now=NOW, observations=(observation,))
    assert len(snapshot.quiet_hours_suppressed) == 1
    assert not snapshot.stuck_candidates


def test_bucket_cap_is_deterministic():
    observations = tuple(
        _observation(task_id=f"task-{index}") for index in range(4)
    )
    snapshot = AwarenessBuilder(max_items_per_bucket=2).build(
        now=NOW,
        observations=observations,
    )
    assert tuple(item.item_id for item in snapshot.active_tasks) == (
        "task-0",
        "task-1",
    )


def test_notification_advice_respects_quiet_hours():
    deferred = recommend_notification(quiet_hours=_quiet_hours(active=True))
    assert deferred.advice is NotificationAdvice.DEFER
    suppressed = recommend_notification(
        quiet_hours=_quiet_hours(active=False, suppressed=True)
    )
    assert suppressed.advice is NotificationAdvice.SUPPRESS
    assert suppressed.delivered is False
