"""Bounded, offline handling for common time-of-day questions."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


_TIME_QUERY = re.compile(r"\btime\b", re.IGNORECASE)
_TIME_CORRECTION = re.compile(
    r"^\s*(?:no|nope|actually|i\s+meant|not\s+that)\b", re.IGNORECASE
)

# This is deliberately a small offline allowlist. Unknown locations fail clearly
# instead of silently returning the computer's local time.
_LOCATION_ALIASES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("new delhi", "delhi", "mumbai", "kolkata", "calcutta", "india"), "India", "Asia/Kolkata"),
    (("new york", "eastern time"), "New York", "America/New_York"),
    (("los angeles", "pacific time"), "Los Angeles", "America/Los_Angeles"),
    (("chicago", "central time"), "Chicago", "America/Chicago"),
    (("denver", "mountain time"), "Denver", "America/Denver"),
    (("london", "united kingdom", "uk"), "London", "Europe/London"),
    (("tokyo", "japan"), "Tokyo", "Asia/Tokyo"),
    (("utc", "gmt"), "UTC", "UTC"),
)


def _mentions_alias(text: str, alias: str) -> bool:
    return bool(re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE))


def _requested_location(text: str) -> Optional[tuple[str, str]]:
    lowered = (text or "").lower()
    for aliases, label, timezone_name in _LOCATION_ALIASES:
        if any(_mentions_alias(lowered, alias) for alias in aliases):
            return label, timezone_name
    return None


def _has_location_clause(text: str) -> bool:
    return bool(re.search(r"\b(?:in|for)\s+(?:the\s+)?[a-z]", text, re.IGNORECASE))


def _format_clock(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def answer_time_query(
    text: str,
    *,
    previous_was_time: bool = False,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Return an offline time answer, or ``None`` when *text* is unrelated.

    A short correction such as ``"no, in India"`` is recognized only when the
    immediately preceding special-command intent was a time query.
    """

    raw = (text or "").strip()
    is_time_query = bool(_TIME_QUERY.search(raw))
    is_time_correction = previous_was_time and bool(_TIME_CORRECTION.search(raw))
    if not is_time_query and not is_time_correction:
        return None

    location = _requested_location(raw)
    if location is None and _has_location_clause(raw):
        return (
            "I don't recognize that location for an offline time lookup. "
            "Try India, London, New York, Los Angeles, Chicago, Denver, Tokyo, or UTC."
        )
    if location is None:
        if is_time_correction:
            return None
        local_now = now.astimezone() if now is not None else datetime.now().astimezone()
        return f"The local time is {_format_clock(local_now)}."

    label, timezone_name = location
    zone = ZoneInfo(timezone_name)
    zoned_now = now.astimezone(zone) if now is not None else datetime.now(zone)
    abbreviation = zoned_now.tzname() or timezone_name
    return f"The current time in {label} is {_format_clock(zoned_now)} {abbreviation}."
