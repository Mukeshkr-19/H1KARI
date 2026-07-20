"""Immutable quiet-hours window and a pure quiet-check helper.

A quiet window is expressed as minutes from local midnight in a configured
IANA timezone. The window may cross midnight. Equal start and end means the
window is disabled (not all-day quiet).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MINUTES_PER_DAY = 1440


class QuietHoursError(ValueError):
    """Raised for invalid quiet-hours configuration."""


@dataclass(frozen=True)
class QuietHours:
    """Immutable quiet window in a single IANA timezone.

    ``start_minute`` is inclusive; ``end_minute`` is exclusive. Both are
    integers in ``0..1439`` measured from local midnight in ``timezone_name``.
    When ``start_minute == end_minute`` the window is disabled.
    """

    timezone_name: str
    start_minute: int
    end_minute: int

    def __post_init__(self) -> None:
        if not isinstance(self.timezone_name, str) or not self.timezone_name.strip():
            raise QuietHoursError("timezone_name must be a non-empty string")
        try:
            ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise QuietHoursError(
                f"unknown timezone name: {self.timezone_name!r}"
            ) from exc
        for name in ("start_minute", "end_minute"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise QuietHoursError(f"{name} must be an integer")
            if value < 0 or value >= MINUTES_PER_DAY:
                raise QuietHoursError(
                    f"{name} must be in 0..{MINUTES_PER_DAY - 1}"
                )

    @property
    def enabled(self) -> bool:
        """Equal start/end means disabled, not all-day."""
        return self.start_minute != self.end_minute


def _minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def is_quiet(now: datetime, quiet_hours: QuietHours) -> bool:
    """Return whether ``now`` falls inside the quiet window.

    ``now`` must be timezone-aware; it is converted into the configured
    timezone before comparison. No implicit environment timezone, no
    ``datetime.now``, no sleeping or background activity is used.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise QuietHoursError("now must be timezone-aware")
    if not isinstance(quiet_hours, QuietHours):
        raise QuietHoursError("quiet_hours must be a QuietHours instance")

    if not quiet_hours.enabled:
        return False

    local = now.astimezone(ZoneInfo(quiet_hours.timezone_name))
    minute = _minute_of_day(local)
    start = quiet_hours.start_minute
    end = quiet_hours.end_minute

    if start < end:
        # Same-day window, e.g. 22:00 -> 23:00.
        return start <= minute < end
    # Cross-midnight window, e.g. 23:00 -> 01:00.
    return minute >= start or minute < end
