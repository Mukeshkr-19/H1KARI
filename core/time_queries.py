"""Bounded time-of-day handling with offline common-place fallbacks."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Callable, Optional
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

TimezoneResolver = Callable[[str], Optional[tuple[str, str]]]


def _mentions_alias(text: str, alias: str) -> bool:
    return bool(re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE))


def _requested_location(text: str) -> Optional[tuple[str, str]]:
    lowered = (text or "").lower()
    for aliases, label, timezone_name in _LOCATION_ALIASES:
        if any(_mentions_alias(lowered, alias) for alias in aliases):
            return label, timezone_name
    return None


def _location_phrase(text: str, *, correction: bool) -> Optional[str]:
    if correction:
        match = re.search(r"\b(?:in|for)\s+(?P<place>.+)$", text, re.I)
    else:
        match = re.search(r"\btime\b.*?\b(?:in|for|at)\s+(?P<place>.+)$", text, re.I)
    if not match:
        return None
    place = re.sub(r"[?!.]+$", "", match.group("place")).strip()
    place = re.sub(
        r"\b(?:please|right\s+now|now|man|bro)\s*$",
        "",
        place,
        flags=re.I,
    ).strip()
    if not place or len(place) > 100:
        return None
    if any(
        ord(ch) < 32 or ord(ch) == 127 or unicodedata.category(ch) == "Cf"
        for ch in place
    ):
        return None
    return place


def _has_location_clause(text: str) -> bool:
    return bool(re.search(r"\b(?:in|for)\s+(?:the\s+)?[a-z]", text, re.IGNORECASE))


def _format_clock(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def answer_time_query(
    text: str,
    *,
    previous_was_time: bool = False,
    now: Optional[datetime] = None,
    resolve_timezone: Optional[TimezoneResolver] = None,
) -> Optional[str]:
    """Return a bounded time answer, or ``None`` when *text* is unrelated.

    A short correction such as ``"no, in India"`` is recognized only when the
    immediately preceding special-command intent was a time query.
    """

    raw = (text or "").strip()
    is_time_query = bool(_TIME_QUERY.search(raw))
    is_time_correction = previous_was_time and bool(_TIME_CORRECTION.search(raw))
    if not is_time_query and not is_time_correction:
        return None

    location = _requested_location(raw)
    place = _location_phrase(raw, correction=is_time_correction)
    if location is None and place and resolve_timezone is not None:
        try:
            location = resolve_timezone(place)
        except Exception:
            return "I couldn't reach the location service right now. Please try again."
    if location is None and _has_location_clause(raw):
        return "I couldn't find that location. Try a city, state, or country name."
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
