"""Deterministic conversational time interpretation.

The interpreter turns owner phrases such as "tomorrow morning", "in 20
minutes", "next Monday", "this afternoon", "after lunch", or "soon" into
structured :class:`TemporalInterpretation` records.

Design rules:

- A caller-supplied reference datetime (``reference_at``) is the only
  clock. ``datetime.now()`` is never called by this module.
- All output timestamps are timezone-aware. Mixing naive with aware raises
  ``ValueError``, never silently coerces.
- Negative durations and excessive future horizons are rejected.
- Vague expressions return ``TemporalPrecision.VAGUE`` and require
  clarification; the interpreter never invents an instant.
- DST transitions are handled with the :mod:`zoneinfo` database, so adding
  the resulting offset is correct even across the spring-forward gap.
- Midnight crossings are resolved by adding the offset implied by the
  target wall clock, not by adding hours naively.
- A resolved timestamp is kept distinct from a conversational time window
  (``WINDOW`` / ``VAGUE``); the former carries ``resolution`` and the
  latter carries a start/end ``window`` pair.
- The original phrase is always preserved for provenance.
- The interpreter claims no reminder or task was scheduled and never
  writes to any store; callers remain responsible for confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from core.time_sense.contracts import (
    DEFAULT_MAX_FUTURE_HORIZON,
    MIN_FUTURE_HORIZON,
    TemporalInterpretation,
    TemporalPrecision,
    TimeReference,
)


# ---------------------------------------------------------------------------
# Resolver policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InterpretPolicy:
    """Bounded overrides for the interpretive policy.

    ``lunch_window`` is the (start, end) pair of *minute-of-day* values used
    when the owner says "after lunch" or "before lunch". When None (the
    default), those phrases produce a ``WINDOW`` requiring clarification
    because Time Sense must never invent a meal time.
    """

    max_future_horizon: timedelta = DEFAULT_MAX_FUTURE_HORIZON
    min_future_horizon: timedelta = MIN_FUTURE_HORIZON
    lunch_window: Optional[Tuple[int, int]] = None
    afternoon_start_minute: int = 12 * 60          # 12:00 local
    evening_start_minute: int = 18 * 60            # 18:00 local
    morning_end_minute: int = 12 * 60              # exclusive end
    weekend_days: Tuple[int, ...] = (5, 6)         # 5=Saturday, 6=Sunday
    end_of_day_minute: int = 18 * 60               # default EOD window end

    def __post_init__(self) -> None:
        if not isinstance(self.max_future_horizon, timedelta):
            raise ValueError("max_future_horizon must be a timedelta")
        if self.max_future_horizon < self.min_future_horizon:
            raise ValueError(
                "max_future_horizon must be at least min_future_horizon"
            )
        if (
            self.afternoon_start_minute < 0
            or self.afternoon_start_minute >= 1440
        ):
            raise ValueError("afternoon_start_minute out of bounds")
        if self.evening_start_minute < 0 or self.evening_start_minute >= 1440:
            raise ValueError("evening_start_minute out of bounds")
        if self.morning_end_minute < 0 or self.morning_end_minute > 1440:
            raise ValueError("morning_end_minute out of bounds")
        if self.end_of_day_minute < 0 or self.end_of_day_minute > 1440:
            raise ValueError("end_of_day_minute out of bounds")
        if not self.weekend_days or any(
            not isinstance(day, int) or day < 0 or day > 6 for day in self.weekend_days
        ):
            raise ValueError("weekend_days must be 0..6 weekday integers")
        if self.lunch_window is not None:
            start, end = self.lunch_window
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end > 1440
                or end <= start
            ):
                raise ValueError("lunch_window must be a (start, end) window")


# ---------------------------------------------------------------------------
# Phrase parsing
# ---------------------------------------------------------------------------


_WEEKDAY_NAMES: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


_TIME_12H = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE
)
_TIME_24H = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_RELATIVE_DELTA = re.compile(
    r"\bin\s+(\d+)\s+(minute|minutes|min|hour|hours|hr|hrs|day|days|millisecond|milliseconds|ms|week|weeks|month|months)\b",
    re.IGNORECASE,
)


class TimeSenseError(ValueError):
    """Raised for invalid configuration or inputs to Time Sense."""


def _require_zone(ref: TimeReference) -> ZoneInfo:
    """Return a ``ZoneInfo`` for the reference clock, defaulting to UTC."""
    tz = ref.reference_at.tzinfo
    if tz is None:
        raise TimeSenseError("reference_at must be timezone-aware")
    if isinstance(tz, ZoneInfo):
        return tz
    # Convert offset-based zones into UTC; offset-only zones cannot resolve
    # names that don't carry an IANA key.
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        try:
            return ZoneInfo(key)
        except Exception:
            pass
    return ZoneInfo("UTC")


def _at_zone(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        raise TimeSenseError("datetime must be timezone-aware")
    return value.astimezone(zone)


def _combine(date, minute: int, zone: ZoneInfo) -> datetime:
    """Combine a date and minute-of-day into an aware datetime in ``zone``.

    Using ``datetime.combine`` plus a :class:`ZoneInfo` lets the standard
    library apply the right DST offset for the resulting instant, so adding
    60 minutes from ``23:30`` lands on the next calendar day at the right
    moment even on a spring-forward or fall-back night.
    """
    hour, minute = divmod(minute, 60)
    return datetime.combine(date, time(hour, minute), tzinfo=zone)


def _minute_to_clock(minute: int) -> str:
    hour, m = divmod(minute, 60)
    return f"{hour:02d}:{m:02d}"


def _normalise_phrase(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise TimeSenseError("phrase must be a non-empty string")
    return text


def _diff_to_target_weekday(
    observed: datetime, target_weekday: int, *, days_offset: int = 0
) -> int:
    """Return the day delta (>=1) to reach the next ``target_weekday``.

    ``days_offset`` shifts the starting weekday; e.g. ``days_offset=1``
    means "next Monday" (at least 7 days ahead depending on leap days),
    while ``days_offset=0`` means "Monday" (could be today).
    """
    current = observed.weekday()
    raw = (target_weekday - current) % 7
    if raw == 0:
        return 7 * days_offset if days_offset > 0 else 0
    return raw if days_offset == 0 else raw + 7


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _reject_negative_duration(delta: timedelta) -> None:
    if delta.total_seconds() < 0:
        raise TimeSenseError("duration must not be negative")


def _resolve_relative_delta(phrase: str) -> Optional[timedelta]:
    """Match ``in N unit`` phrases and turn them into a positive timedelta."""
    match = _RELATIVE_DELTA.search(phrase)
    if not match:
        return None
    amount = int(match.group(1))
    if amount < 0:
        raise TimeSenseError("duration must not be negative")
    unit = match.group(2).lower()
    if unit in {"minute", "minutes", "min"}:
        return timedelta(minutes=amount)
    if unit in {"hour", "hours", "hr", "hrs"}:
        return timedelta(hours=amount)
    if unit in {"day", "days"}:
        return timedelta(days=amount)
    if unit in {"week", "weeks"}:
        return timedelta(weeks=amount)
    if unit in {"month", "months"}:
        # Months are calendar-aware; we approximate with 30 days only after
        # the policy's max-future-horizon check.
        return timedelta(days=30 * amount)
    if unit in {"millisecond", "milliseconds", "ms"}:
        return timedelta(milliseconds=amount)
    return None


def _resolve_clock_value(phrase: str) -> Optional[int]:
    """Return minute-of-day if a 12h or 24h clock appears in ``phrase``."""
    match = _TIME_12H.search(phrase)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3).lower()
        if hour == 0 or hour > 12:
            raise TimeSenseError("clock hour outside 1..12")
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if minute >= 60:
            raise TimeSenseError("clock minute outside 0..59")
        return hour * 60 + minute
    match = _TIME_24H.search(phrase)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if minute >= 60:
            raise TimeSenseError("clock minute outside 0..59")
        return hour * 60 + minute
    return None


# ---------------------------------------------------------------------------
# Top-level interpretation
# ---------------------------------------------------------------------------


def interpret_time_phrase(
    phrase: str,
    *,
    reference_at: datetime,
    timezone_name: Optional[str] = None,
    policy: Optional[InterpretPolicy] = None,
) -> TemporalInterpretation:
    """Interpret one conversational time phrase deterministically.

    ``reference_at`` is the caller's "now" and must be timezone-aware. When
    ``timezone_name`` is supplied, the resolution is computed in that IANA
    zone (regardless of the reference's own zone). When ``None``, the
    reference's own zone is used.

    The interpreter returns a structured :class:`TemporalInterpretation`
    carrying either a resolved instant (``INSTANT``) or a bounded window
    (``WINDOW`` / ``VAGUE``). It never claims a reminder or task was
    scheduled; callers remain responsible for any confirmation step.
    """
    ref = TimeReference(reference_at=reference_at, phrase=_normalise_phrase(phrase))
    policy = policy or InterpretPolicy()

    name = timezone_name or ref.timezone_name
    if not name or name == "UTC":
        zone = ZoneInfo("UTC")
    else:
        try:
            zone = ZoneInfo(name)
        except Exception as exc:
            raise TimeSenseError(f"unknown timezone: {name!r}") from exc

    ref_local = _at_zone(ref.reference_at, zone)
    raw_lower = ref.phrase.lower().strip()

    notes = ("deterministic", f"zone={name}")

    instant, window, precision, confidence, extra_notes = _dispatch(
        raw_lower, ref=ref, ref_local=ref_local, zone=zone, policy=policy
    )

    all_notes: list[str] = list(notes)
    if extra_notes:
        all_notes.extend(extra_notes)

    requires_clarification = precision is TemporalPrecision.VAGUE

    if precision is TemporalPrecision.INSTANT:
        if instant is None:
            raise TimeSenseError("instant precision requires a resolution")
        delta = instant - ref_local
        if delta.total_seconds() < 0:
            # Past phrases are flagged rather than silently processed.
            return TemporalInterpretation(
                original_phrase=ref.phrase,
                precision=TemporalPrecision.WINDOW,
                confidence=0.0,
                reference=ref,
                window=(ref_local, ref_local),
                requires_clarification=True,
                notes=(*all_notes, "phrase_resolves_to_past"),
            )
        if delta > policy.max_future_horizon:
            raise TimeSenseError(
                "phrase exceeds configured future horizon"
            )
        if delta < policy.min_future_horizon and delta != timedelta(0):
            # ``min_future_horizon`` is informational; clamp to floor only
            # when the interpreter later wants to reject tiny windows. We
            # never fall below it for resolved instants.
            pass
        return TemporalInterpretation(
            original_phrase=ref.phrase,
            precision=TemporalPrecision.INSTANT,
            confidence=confidence,
            reference=ref,
            resolution=instant,
            notes=tuple(all_notes),
        )

    if window is None:
        raise TimeSenseError("non-instant precision requires a window")
    return TemporalInterpretation(
        original_phrase=ref.phrase,
        precision=precision,
        confidence=confidence,
        reference=ref,
        window=window,
        requires_clarification=requires_clarification,
        notes=tuple(all_notes),
    )


# ---------------------------------------------------------------------------
# Dispatch — pattern-specific handlers
# ---------------------------------------------------------------------------


def _dispatch(
    phrase: str,
    *,
    ref: TimeReference,
    ref_local: datetime,
    zone: ZoneInfo,
    policy: InterpretPolicy,
) -> Tuple[
    Optional[datetime],
    Optional[Tuple[datetime, datetime]],
    TemporalPrecision,
    float,
    Tuple[str, ...],
]:
    # 1. Relative durations: ``in 20 minutes``, ``in 2 hours``.
    delta = _resolve_relative_delta(phrase)
    if delta is not None:
        _reject_negative_duration(delta)
        if delta > policy.max_future_horizon:
            raise TimeSenseError("duration exceeds configured future horizon")
        resolution = _combine(
            ref_local.date(), ref_local.hour * 60 + ref_local.minute, zone
        ) + delta
        return resolution, None, TemporalPrecision.INSTANT, 0.95, ("relative",)

    # 2. Time-of-day with a clock: ``tomorrow at 3 PM``, ``Monday at 3 PM``.
    clock_minute = _resolve_clock_value(phrase)

    # 3. Named weekday: ``next Monday`` / ``Monday`` / ``Monday at 3 PM``.
    weekday_match = None
    is_next = "next" in phrase.split()
    for name, idx in _WEEKDAY_NAMES.items():
        if re.search(rf"\b{name}\b", phrase):
            weekday_match = idx
            break

    if weekday_match is not None:
        days_offset = 1 if is_next else 0
        delta_days = _diff_to_target_weekday(
            ref_local, weekday_match, days_offset=days_offset
        )
        target_date = ref_local.date() + timedelta(days=delta_days)
        minute = clock_minute if clock_minute is not None else 9 * 60
        instant = _combine(target_date, minute, zone)
        if "morning" in phrase:
            instant = _combine(target_date, policy.morning_end_minute // 2, zone)
        elif "evening" in phrase and clock_minute is None:
            instant = _combine(target_date, policy.evening_start_minute, zone)
        elif "afternoon" in phrase and clock_minute is None:
            instant = _combine(
                target_date, policy.afternoon_start_minute + 60, zone
            )
        return instant, None, TemporalPrecision.INSTANT, 0.9, ("named_weekday",)

    # 4. Today / tonight / tomorrow — with optional time-of-day.
    today_token = "today" in phrase or "tonight" in phrase
    tomorrow_token = "tomorrow" in phrase

    if tomorrow_token:
        base = ref_local.date() + timedelta(days=1)
        if "morning" in phrase or clock_minute == 9 * 60:
            minute = clock_minute if clock_minute is not None else 9 * 60
            instant = _combine(base, minute, zone)
            return instant, None, TemporalPrecision.INSTANT, 0.9, ("tomorrow",)
        if "afternoon" in phrase:
            minute = clock_minute if clock_minute is not None else (
                policy.afternoon_start_minute + 60
            )
            instant = _combine(base, minute, zone)
            return instant, None, TemporalPrecision.INSTANT, 0.9, ("tomorrow_afternoon",)
        if "evening" in phrase or "night" in phrase:
            minute = clock_minute if clock_minute is not None else (
                policy.evening_start_minute
            )
            instant = _combine(base, minute, zone)
            return instant, None, TemporalPrecision.INSTANT, 0.9, ("tomorrow_evening",)
        if clock_minute is not None:
            instant = _combine(base, clock_minute, zone)
            return instant, None, TemporalPrecision.INSTANT, 0.95, ("tomorrow_clock",)
        instant_floor = _combine(base, 9 * 60, zone)
        instant_ceil = _combine(base, policy.end_of_day_minute, zone)
        return None, (instant_floor, instant_ceil), TemporalPrecision.WINDOW, 0.6, ("tomorrow_window",)

    if today_token:
        base = ref_local.date()
        if "evening" in phrase or "tonight" in phrase:
            minute = clock_minute if clock_minute is not None else (
                policy.evening_start_minute
            )
            instant = _combine(base, minute, zone)
            if instant < ref_local:
                instant = _combine(
                    base + timedelta(days=1), minute, zone
                )
            return instant, None, TemporalPrecision.INSTANT, 0.85, ("today_evening",)
        if clock_minute is not None:
            instant = _combine(base, clock_minute, zone)
            if instant < ref_local:
                instant = _combine(
                    base + timedelta(days=1), clock_minute, zone
                )
            return instant, None, TemporalPrecision.INSTANT, 0.85, ("today_clock",)
        if "later" in phrase:
            floor = ref_local + timedelta(minutes=30)
            ceil_today = _combine(base, policy.end_of_day_minute, zone)
            return None, (floor, ceil_today), TemporalPrecision.WINDOW, 0.5, ("later_today",)
        return None, (
            ref_local,
            _combine(base, policy.end_of_day_minute, zone),
        ), TemporalPrecision.WINDOW, 0.4, ("today_window",)

    # 5. This afternoon / this evening — bound the spoken window.
    if "afternoon" in phrase:
        base = ref_local.date()
        start = _combine(base, policy.afternoon_start_minute, zone)
        end = _combine(base, policy.evening_start_minute, zone)
        return None, (start, end), TemporalPrecision.WINDOW, 0.7, ("afternoon_window",)
    if "evening" in phrase:
        base = ref_local.date()
        start = _combine(base, policy.evening_start_minute, zone)
        end = _combine(base + timedelta(days=1), 6 * 60, zone)
        return None, (start, end), TemporalPrecision.WINDOW, 0.7, ("evening_window",)

    # 6. After lunch / before dinner.
    if "after lunch" in phrase or "after lunch." in phrase:
        if policy.lunch_window is None:
            return None, _vague_window("after_lunch", ref_local), TemporalPrecision.VAGUE, 0.2, ("after_lunch_vague",)
        start_minute, end_minute = policy.lunch_window
        base = ref_local.date()
        start = _combine(base, end_minute, zone)
        if start < ref_local:
            base = base + timedelta(days=1)
        end = _combine(base + timedelta(days=1), 18 * 60, zone)
        return None, (start, end), TemporalPrecision.WINDOW, 0.6, ("after_lunch_window",)
    if "before dinner" in phrase:
        base = ref_local.date()
        start = _combine(base, 16 * 60, zone)
        end = _combine(base, 19 * 60, zone)
        return None, (start, end), TemporalPrecision.WINDOW, 0.6, ("before_dinner_window",)

    # 7. End of day.
    if "end of day" in phrase or "eod" in phrase:
        base = ref_local.date()
        start = max(ref_local, _combine(base, 16 * 60, zone))
        end = _combine(base, policy.end_of_day_minute, zone)
        if end <= start:
            end = _combine(base + timedelta(days=1), 18 * 60, zone)
        return None, (start, end), TemporalPrecision.WINDOW, 0.5, ("eod_window",)

    # 8. This weekend / next week.
    if "this weekend" in phrase:
        target = _next_weekend_start(ref_local, policy)
        end_date = target + timedelta(days=1)
        return None, (
            _combine(target, 9 * 60, zone),
            _combine(end_date, 18 * 60, zone),
        ), TemporalPrecision.WINDOW, 0.6, ("weekend_window",)
    if "next week" in phrase:
        today = ref_local.date()
        monday_offset = (7 - today.weekday()) % 7 or 7
        monday = today + timedelta(days=monday_offset)
        sunday = monday + timedelta(days=6)
        return None, (
            _combine(monday, 9 * 60, zone),
            _combine(sunday, 18 * 60, zone),
        ), TemporalPrecision.WINDOW, 0.6, ("next_week_window",)

    # 9. Bare clock ("3 PM"): today, then tomorrow if already past.
    if clock_minute is not None:
        base = ref_local.date()
        instant = _combine(base, clock_minute, zone)
        if instant < ref_local:
            instant = _combine(
                base + timedelta(days=1), clock_minute, zone
            )
        return instant, None, TemporalPrecision.INSTANT, 0.8, ("bare_clock",)

    # 10. Soon.
    if "soon" in phrase:
        floor = ref_local + timedelta(minutes=15)
        ceil = ref_local + timedelta(hours=2)
        return None, (floor, ceil), TemporalPrecision.VAGUE, 0.2, ("soon_vague",)

    # Nothing matched.
    return (
        None,
        (ref_local, ref_local + timedelta(hours=1)),
        TemporalPrecision.VAGUE,
        0.1,
        ("unmatched",),
    )


def _next_weekend_start(
    observed: datetime, policy: InterpretPolicy
) -> "datetime.date":
    weekday = observed.weekday()
    days_to_weekend = min(
        ((day - weekday) % 7) for day in policy.weekend_days
    )
    if days_to_weekend == 0 and observed.hour >= 18:
        # Use the next weekend once Saturday has already started evening.
        days_to_weekend = 7 - weekday + policy.weekend_days[0]
    return observed.date() + timedelta(days=days_to_weekend)


def _vague_window(reason: str, ref_local: datetime) -> Tuple[datetime, datetime]:
    return (
        ref_local + timedelta(hours=1),
        ref_local + timedelta(hours=4),
    )


__all__ = [
    "InterpretPolicy",
    "TimeSenseError",
    "interpret_time_phrase",
]
