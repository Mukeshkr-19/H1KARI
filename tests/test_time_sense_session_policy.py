"""Adversarial tests for conversation timing policy."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.time_sense import (
    ConversationTimingObservation,
    QuietHoursContext,
    TimingAction,
    TimingReason,
    evaluate_conversation_timing,
)


NOW = datetime(2026, 7, 26, 15, 0, tzinfo=timezone.utc)


def obs(**kwargs):
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


def test_rejects_nan_inf_negative_ages():
    for bad in (float("nan"), float("inf"), -1.0):
        with pytest.raises(ValueError):
            obs(pause_age_seconds=bad)


def test_suppress_quiet_hours_child_sleep_speech_dismissal():
    assert evaluate_conversation_timing(obs(quiet_hours=True)).action == TimingAction.SUPPRESS
    assert evaluate_conversation_timing(obs(child_mode=True)).reason == TimingReason.CHILD_MODE
    assert evaluate_conversation_timing(obs(sleeping=True)).reason == TimingReason.SLEEPING
    assert evaluate_conversation_timing(obs(user_speaking=True)).reason == TimingReason.ACTIVE_SPEECH
    assert evaluate_conversation_timing(obs(recent_dismissal=True)).reason == TimingReason.RECENT_DISMISSAL
    qh = QuietHoursContext(
        timezone_name="UTC",
        active=True,
        start_minute=22 * 60,
        end_minute=7 * 60,
        suppressed=True,
    )
    assert evaluate_conversation_timing(obs(), quiet_hours_context=qh).reason == TimingReason.QUIET_HOURS


def test_wait_respond_check_in_summarize():
    assert evaluate_conversation_timing(obs(pause_age_seconds=0.5)).action == TimingAction.WAIT
    assert evaluate_conversation_timing(obs(pause_age_seconds=2.0)).action == TimingAction.RESPOND
    assert evaluate_conversation_timing(obs(pause_age_seconds=50.0)).action == TimingAction.CHECK_IN
    assert evaluate_conversation_timing(obs(pause_age_seconds=200.0)).action == TimingAction.SUMMARIZE


def test_content_free_repr():
    o = obs()
    assert "pause_age" not in repr(o) or "sess-1" in repr(o)
    d = evaluate_conversation_timing(o)
    assert "RESPOND" not in repr(d)  # uses value strings lowercase
    assert d.action.value in repr(d)
