"""Tests for Time Sense observation coordinator and static adapters."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.time_sense import (
    ConversationTimingObservation,
    QuietHoursContext,
    StaticConversationSessionSource,
    StaticStreamingVoiceSource,
    TimeSenseObservationCoordinator,
    TimingAction,
)
from core.time_sense.contracts import TaskProgressObservation, TaskProgressState
from core.time_sense.observation_coordinator import ObservationCoordinatorConfig


NOW = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)


def _obs(**kwargs):
    base = dict(
        session_id="sess-1",
        observed_at=NOW,
        pause_age_seconds=2.0,
        last_user_speech_age_seconds=2.0,
        last_assistant_response_age_seconds=5.0,
        conversation_active=True,
        sleeping=False,
        quiet_hours=False,
        recent_dismissal=False,
        child_mode=False,
        privacy_suppression=False,
        user_speaking=False,
        assistant_speaking=False,
    )
    base.update(kwargs)
    return ConversationTimingObservation(**base)


def _quiet(active=False):
    return QuietHoursContext(
        timezone_name="UTC",
        active=active,
        start_minute=22 * 60,
        end_minute=7 * 60,
        suppressed=active,
    )


def test_coordinator_suppresses_sleep_quiet_child_speech_dismissal():
    for kwargs in (
        {"sleeping": True},
        {"quiet_hours": True},
        {"child_mode": True},
        {"user_speaking": True},
        {"recent_dismissal": True},
        {"privacy_suppression": True},
    ):
        voice = StaticStreamingVoiceSource(_obs(**kwargs))
        coord = TimeSenseObservationCoordinator(lambda: NOW, voice=voice)
        snap = coord.tick()
        assert snap.suppressed is True
        assert snap.advisories[-1].action == TimingAction.SUPPRESS


def test_stale_observation_and_bounds():
    voice = StaticStreamingVoiceSource(_obs(observed_at=NOW - timedelta(hours=2)))
    coord = TimeSenseObservationCoordinator(lambda: NOW, voice=voice)
    snap = coord.tick()
    assert snap.reason == "stale_observation"
    content = coord.content_free_snapshot()
    assert "transcript" not in content


def test_conversation_adapter_feeds_bridge():
    session = StaticConversationSessionSource(_obs(), _quiet(False))
    coord = TimeSenseObservationCoordinator(lambda: NOW, conversation=session)
    snap = coord.tick()
    assert snap.suppressed is False
    assert snap.advisories[-1].action == TimingAction.RESPOND


def test_coordinator_config_rejects_bool_nan_negative():
    with pytest.raises(ValueError):
        ObservationCoordinatorConfig(max_jobs=True)
    with pytest.raises(ValueError):
        ObservationCoordinatorConfig(max_jobs=0)
    with pytest.raises(ValueError):
        ObservationCoordinatorConfig(max_observation_age_seconds=float("nan"))
    with pytest.raises(ValueError):
        ObservationCoordinatorConfig(max_future_skew_seconds=float("inf"))


def test_future_observation_classified_separately():
    voice = StaticStreamingVoiceSource(_obs(observed_at=NOW + timedelta(seconds=30)))
    coord = TimeSenseObservationCoordinator(lambda: NOW, voice=voice)
    snap = coord.tick()
    assert snap.reason == "future_observation"


def test_unbounded_task_source_stops_at_cap():
    def endless(*, now, limit):
        i = 0
        while True:
            i += 1
            yield TaskProgressObservation(
                task_id=f"task{i}",
                kind="generic",
                state=TaskProgressState.NO_PROGRESS,
                observed_at=now,
                last_progress_at=now - timedelta(seconds=120),
                overdue=True,
                evidence_codes=("stalled",),
            )

    class EndlessTasks:
        def list_task_progress(self, *, now, limit=32):
            return endless(now=now, limit=limit)

    coord = TimeSenseObservationCoordinator(
        lambda: NOW,
        voice=StaticStreamingVoiceSource(_obs()),
        tasks=EndlessTasks(),
        config=ObservationCoordinatorConfig(max_tasks=5),
    )
    snap = coord.tick()
    assert len(snap.stuck_task_ids) <= 5


def test_coordinator_cancel_is_idempotent():
    coord = TimeSenseObservationCoordinator(lambda: NOW, voice=StaticStreamingVoiceSource(_obs()))
    coord.cancel()
    assert coord.tick().reason == "cancelled"
    coord.cancel()
    assert coord.tick().reason == "cancelled"
