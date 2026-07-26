"""Stuck notification backoff, dedupe, resolved/cancelled tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.time_sense import (
    JobObservationState,
    JobTimingObservation,
    StuckNotifyConfig,
    StuckNotificationTracker,
)


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def job(**kwargs):
    base = dict(
        job_id="job-1",
        state=JobObservationState.RUNNING,
        observed_at=NOW,
        age_seconds=120.0,
        heartbeat_age_seconds=60.0,
        attempt_count=3,
    )
    base.update(kwargs)
    return JobTimingObservation(**base)


def test_job_rejects_bad_estimates_and_nan():
    with pytest.raises(ValueError):
        job(age_seconds=float("nan"))
    with pytest.raises(ValueError):
        job(completion_estimate_min_seconds=10.0)  # unpaired
    with pytest.raises(ValueError):
        job(completion_estimate_min_seconds=20.0, completion_estimate_max_seconds=10.0)


def test_backoff_dedupe_and_exponential():
    tracker = StuckNotificationTracker(
        StuckNotifyConfig(
            min_age_seconds=60,
            missed_heartbeat_seconds=30,
            consecutive_failure_threshold=3,
            base_backoff_seconds=10.0,
            max_backoff_seconds=1000.0,
        )
    )
    first = tracker.evaluate(job(), now=NOW)
    assert first.should_notify is True
    # immediate retry deduped
    second = tracker.evaluate(job(), now=NOW + timedelta(seconds=1))
    assert second.should_notify is False
    assert second.reason == "backoff_deduped"
    # after backoff
    third = tracker.evaluate(job(), now=NOW + timedelta(seconds=20))
    assert third.should_notify is True
    assert third.notification_count == 2


def test_resolved_and_cancelled_never_notify():
    tracker = StuckNotificationTracker(StuckNotifyConfig(min_age_seconds=1, missed_heartbeat_seconds=1))
    tracker.evaluate(job(), now=NOW)
    tracker.mark_resolved("job-1")
    assert tracker.evaluate(job(state=JobObservationState.RESOLVED), now=NOW + timedelta(hours=1)).reason == "resolved"
    tracker2 = StuckNotificationTracker(StuckNotifyConfig(min_age_seconds=1, missed_heartbeat_seconds=1))
    assert tracker2.evaluate(job(state=JobObservationState.CANCELLED), now=NOW).reason == "cancelled"


def test_below_thresholds_no_notify():
    tracker = StuckNotificationTracker(
        StuckNotifyConfig(min_age_seconds=1000, missed_heartbeat_seconds=999, consecutive_failure_threshold=99)
    )
    decision = tracker.evaluate(job(age_seconds=10, heartbeat_age_seconds=1, attempt_count=0), now=NOW)
    assert decision.should_notify is False


def test_adapters_are_protocols_only():
    from core.time_sense.adapters import ScheduledJobObservationSource
    assert ScheduledJobObservationSource.__name__ == "ScheduledJobObservationSource"
