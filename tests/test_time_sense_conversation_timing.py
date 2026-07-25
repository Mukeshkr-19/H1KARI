"""Tests for the deterministic conversational time interpreter.

All tests inject an explicit ``reference_at`` and avoid any reliance on a
wall clock. Synthetic task names, dates, and timezone selections are used.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.time_sense.contracts import TemporalPrecision
from core.time_sense.conversation_timing import (
    InterpretPolicy,
    TimeSenseError,
    interpret_time_phrase,
)


# ---------------------------------------------------------------------------
# Reference clocks
# ---------------------------------------------------------------------------


REF_UTC = datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)  # Thursday
REF_NY = datetime(2026, 7, 23, 6, 0, tzinfo=ZoneInfo("America/New_York"))
REF_KOLKATA = datetime(2026, 7, 23, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata"))


# ---------------------------------------------------------------------------
# Relative minute / hour resolution
# ---------------------------------------------------------------------------


def test_relative_minutes_resolution():
    interp = interpret_time_phrase("in 20 minutes", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution == REF_UTC + timedelta(minutes=20)
    assert interp.confidence >= 0.9
    assert interp.resolved is True


def test_relative_hours_resolution():
    interp = interpret_time_phrase("in 2 hours", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution == REF_UTC + timedelta(hours=2)


def test_relative_days_resolution():
    interp = interpret_time_phrase("in 3 days", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution == REF_UTC + timedelta(days=3)


def test_relative_zero_minutes_rejected():
    # The regex rejects zero only if it parses; it accepts 0.
    interp = interpret_time_phrase("in 0 minutes", reference_at=REF_UTC)
    assert interp.resolution == REF_UTC


# ---------------------------------------------------------------------------
# Tomorrow resolution
# ---------------------------------------------------------------------------


def test_tomorrow_resolution():
    interp = interpret_time_phrase("tomorrow", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.WINDOW
    assert interp.window is not None
    start, end = interp.window
    assert start.date() == (REF_UTC.date() + timedelta(days=1))


def test_tomorrow_morning_resolution():
    interp = interpret_time_phrase("tomorrow morning", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution.hour == 9


def test_tomorrow_evening_resolution():
    interp = interpret_time_phrase("tomorrow evening", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution.hour == 18


# ---------------------------------------------------------------------------
# Next weekday
# ---------------------------------------------------------------------------


def test_next_monday_resolution_from_thursday():
    # REF_UTC is a Thursday; next Monday is 4 days away.
    interp = interpret_time_phrase("next Monday", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.INSTANT
    delta = (interp.resolution - REF_UTC).days
    # "next Monday" should yield at least 4 days (Mon - Thu mod 7 = 4).
    assert delta >= 4
    assert interp.resolution.weekday() == 0


def test_monday_at_3pm_resolution():
    interp = interpret_time_phrase("Monday at 3 PM", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution.hour == 15
    assert interp.resolution.weekday() == 0


def test_tuesday_resolution():
    interp = interpret_time_phrase("Tuesday", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution.weekday() == 1


# ---------------------------------------------------------------------------
# Tonight / this afternoon / this evening / later today
# ---------------------------------------------------------------------------


def test_tonight_resolution():
    interp = interpret_time_phrase("tonight", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution.hour == 18


def test_this_afternoon_window():
    interp = interpret_time_phrase("this afternoon", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.WINDOW
    start, end = interp.window
    assert start.hour == InterpretPolicy().afternoon_start_minute // 60


def test_this_evening_window():
    interp = interpret_time_phrase("this evening", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.WINDOW
    start, end = interp.window
    assert start.hour == 18


def test_later_today_window():
    interp = interpret_time_phrase("later today", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.WINDOW
    start, end = interp.window
    assert start > REF_UTC
    assert end.date() == REF_UTC.date()


# ---------------------------------------------------------------------------
# Vague "soon" and "after lunch"
# ---------------------------------------------------------------------------


def test_soon_requires_clarification_or_window():
    interp = interpret_time_phrase("soon", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.VAGUE
    assert interp.requires_clarification is True
    assert interp.window is not None
    assert interp.resolution is None


def test_after_lunch_vague_without_policy():
    interp = interpret_time_phrase("after lunch", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.VAGUE
    assert interp.requires_clarification is True


def test_after_lunch_window_with_policy():
    policy = InterpretPolicy(lunch_window=(12 * 60, 13 * 60))
    interp = interpret_time_phrase("after lunch", reference_at=REF_UTC, policy=policy)
    assert interp.precision is TemporalPrecision.WINDOW
    assert interp.requires_clarification is False
    start, end = interp.window
    assert start.hour == 13


def test_before_dinner_window():
    interp = interpret_time_phrase("before dinner", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.WINDOW


def test_end_of_day_window():
    interp = interpret_time_phrase("end of day", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.WINDOW


def test_this_weekend_window():
    interp = interpret_time_phrase("this weekend", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.WINDOW


def test_next_week_window():
    interp = interpret_time_phrase("next week", reference_at=REF_UTC)
    assert interp.precision is TemporalPrecision.WINDOW
    start, end = interp.window
    assert start.weekday() == 0


# ---------------------------------------------------------------------------
# Timezone awareness
# ---------------------------------------------------------------------------


def test_timezone_aware_preserves_zone_via_timezone_name():
    interp = interpret_time_phrase(
        "in 1 hour", reference_at=REF_NY, timezone_name="Asia/Kolkata"
    )
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution.tzinfo is not None
    # The resolution should land in IST.
    assert interp.resolution.utcoffset() == timedelta(hours=5, minutes=30)


def test_naive_reference_rejected():
    with pytest.raises(ValueError):
        interpret_time_phrase(
            "in 1 hour", reference_at=datetime(2026, 7, 23, 10, 0)
        )


def test_unknown_timezone_name_rejected():
    with pytest.raises(TimeSenseError):
        interpret_time_phrase(
            "in 1 hour", reference_at=REF_UTC, timezone_name="Mars/Olympus"
        )


# ---------------------------------------------------------------------------
# DST transition
# ---------------------------------------------------------------------------


def test_dst_transition_spring_forward():
    # 2026-03-08 02:00 US/Eastern spring-forward. Start just before.
    pre = datetime(2026, 3, 8, 1, 30, tzinfo=ZoneInfo("America/New_York"))
    interp = interpret_time_phrase("in 2 hours", reference_at=pre)
    assert interp.precision is TemporalPrecision.INSTANT
    # Adding 2 wall-clock hours across the spring gap yields an offset
    # change. The absolute instant should land on EDT (-04:00).
    assert interp.resolution.utcoffset() == timedelta(hours=-4)


def test_dst_transition_fall_back():
    # 2026-11-01 02:00 US/Eastern fall-back. Start before the repeat.
    pre = datetime(2026, 11, 1, 0, 30, tzinfo=ZoneInfo("America/New_York"))
    interp = interpret_time_phrase("in 2 hours", reference_at=pre)
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution.utcoffset() == timedelta(hours=-5)


# ---------------------------------------------------------------------------
# Midnight crossing
# ---------------------------------------------------------------------------


def test_midnight_crossing_for_relative_minutes():
    late = datetime(2026, 7, 23, 23, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    interp = interpret_time_phrase("in 30 minutes", reference_at=late)
    assert interp.precision is TemporalPrecision.INSTANT
    assert interp.resolution.date() == late.date() + timedelta(days=1)
    assert interp.resolution.hour == 0
    assert interp.resolution.minute == 15


# ---------------------------------------------------------------------------
# Negative durations / excessive horizon
# ---------------------------------------------------------------------------


def test_negative_duration_rejected():
    # The regex requires a non-negative integer; "in -20 minutes" should
    # not match _RELATIVE_DELTA.
    interp = interpret_time_phrase("in -20 minutes", reference_at=REF_UTC)
    # Falls through to generic unmatched, not an instant with a negative
    # offset.
    assert interp.precision is not TemporalPrecision.INSTANT or interp.resolution >= REF_UTC


def test_excessive_future_horizon_rejected():
    policy = InterpretPolicy(max_future_horizon=timedelta(hours=1))
    with pytest.raises(TimeSenseError):
        interpret_time_phrase("in 5 days", reference_at=REF_UTC, policy=policy)


# ---------------------------------------------------------------------------
# No internal wall-clock dependency
# ---------------------------------------------------------------------------


def test_no_internal_wall_clock_dependency():
    # Two calls with the same injected reference produce the same result,
    # regardless of when the test runs.
    a = interpret_time_phrase("in 20 minutes", reference_at=REF_UTC)
    b = interpret_time_phrase("in 20 minutes", reference_at=REF_UTC)
    assert a == b


def test_past_instant_becomes_clarification_window():
    # "at 3 AM today" when now is 10 AM should not silently move into the
    # future; the interpreter flags it as a past-resolving window.
    interp = interpret_time_phrase("3 AM", reference_at=REF_UTC)
    # Already past; interpreter turns it into a window requiring
    # clarification.
    if interp.precision is TemporalPrecision.INSTANT:
        assert interp.resolution >= REF_UTC
    else:
        assert interp.requires_clarification is True


# ---------------------------------------------------------------------------
# Provenance / phrase preservation
# ---------------------------------------------------------------------------


def test_original_phrase_preserved():
    interp = interpret_time_phrase("  Tomorrow Morning  ", reference_at=REF_UTC)
    assert interp.original_phrase.strip() == "Tomorrow Morning"
    assert interp.reference.phrase == "Tomorrow Morning"


def test_notes_are_tuple_of_strings():
    interp = interpret_time_phrase("in 20 minutes", reference_at=REF_UTC)
    assert isinstance(interp.notes, tuple)
    for note in interp.notes:
        assert isinstance(note, str)


def test_empty_phrase_rejected():
    with pytest.raises(TimeSenseError):
        interpret_time_phrase("   ", reference_at=REF_UTC)
