"""Bounded macOS Calendar read adapter for approved Phase 3 read actions.

Synchronous ``ActionAdapter``-compatible callable that queries Calendar.app for
events inside an approved local time range (optionally on one exact calendar)
via ``/usr/bin/osascript``. It never launches Calendar directly, never retries,
and never returns AppleScript, stdout/stderr, event content, paths, calendar
names, or exception text through errors, repr, or logs.

Construction and import perform no Calendar access.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Protocol

from core.productivity.action_inputs import CalendarReadAdapterInput
from core.productivity.action_results import (
    CALENDAR_LABEL_MAX,
    CALENDAR_LOCATION_MAX,
    CALENDAR_READ_EVENTS_MAX,
    CALENDAR_TITLE_MAX,
    ActionResultContractError,
    CalendarEventItem,
    CalendarReadResult,
)
from core.productivity.adapters.macos_actions import (
    OSASCRIPT_PATH,
    OSASCRIPT_TIMEOUT_SECONDS,
    _MAX_CAPTURE_BYTES,
    _applescript_date_block,
    _default_local_tz,
    escape_applescript_string,
)
from core.productivity.execution import AdapterResult, AdapterResultStatus

# Field and record separators are control characters that cannot appear in
# validated event content, so they remain unambiguous structural delimiters.
_FIELD_SEP = "\x1f"  # UNIT SEPARATOR
_RECORD_SEP = "\x1e"  # RECORD SEPARATOR

# Five fields per record: title, start ISO, end ISO, calendar label, location.
_EXPECTED_FIELDS = 5

# Decoded output is already byte-bounded by the runner; this is a defensive
# character ceiling derived from the event-count and per-field text bounds.
_MAX_OUTPUT_CHARS = CALENDAR_READ_EVENTS_MAX * (
    CALENDAR_TITLE_MAX
    + 2 * 32
    + CALENDAR_LABEL_MAX
    + CALENDAR_LOCATION_MAX
    + 16
)

_ISO_LOCAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

_FAILED = AdapterResult(AdapterResultStatus.FAILED, code="failed")


@dataclass(frozen=True)
class ReadCommandResult:
    """Bounded read command outcome. Stdout is never retained beyond parsing."""

    returncode: int
    stdout: bytes


class ReadCommandRunner(Protocol):
    """Injected runner for argv arrays. Production uses osascript; tests fake it."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
    ) -> ReadCommandResult: ...


def production_osascript_runner(
    argv: Sequence[str],
    *,
    timeout: float,
) -> ReadCommandResult:
    """Run ``argv`` without a shell. Capture is bounded and discarded.

    Invokes only ``/usr/bin/osascript`` with a single fixed timeout and bounded
    stdout/stderr capture. No environment secrets are passed.
    """
    if not isinstance(argv, (list, tuple)) or not argv:
        return ReadCommandResult(returncode=1, stdout=b"")
    if argv[0] != OSASCRIPT_PATH:
        return ReadCommandResult(returncode=1, stdout=b"")
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            shell=False,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ReadCommandResult(returncode=1, stdout=b"")
    out = (completed.stdout or b"")[:_MAX_CAPTURE_BYTES]
    code = completed.returncode
    if not isinstance(code, int):
        return ReadCommandResult(returncode=1, stdout=b"")
    return ReadCommandResult(returncode=code, stdout=out)


def _build_script(input: CalendarReadAdapterInput, local_tz: tzinfo) -> str | None:
    """Build the bounded Calendar.app read script for the approved range."""
    start_block = _applescript_date_block("startDate", input.start, local_tz)
    end_block = _applescript_date_block("endDate", input.end, local_tz)
    if start_block is None or end_block is None:
        return None

    if input.calendar_name is not None:
        # Exact calendar only; never fall back to another calendar.
        cal_ref = f"calendar {escape_applescript_string(input.calendar_name)}"
        cal_setup = (
            f"set theCal to {cal_ref}\n"
            'if writable of theCal is false then error "calendar not writable"\n'
            "set cals to {theCal}\n"
        )
    else:
        cal_setup = "set cals to every calendar\n"

    return (
        f"{start_block}\n"
        f"{end_block}\n"
        "on pad(n)\n"
        '  if n < 10 then return "0" & (n as text)\n'
        "  return n as text\n"
        "end pad\n"
        "on isoLocal(d)\n"
        "  set y to year of d\n"
        "  set mo to month of d as integer\n"
        "  set da to day of d\n"
        "  set h to hours of d\n"
        "  set mi to minutes of d\n"
        "  set s to seconds of d\n"
        '  return (y as text) & "-" & pad(mo) & "-" & pad(da) & "T" & pad(h) & ":" & pad(mi) & ":" & pad(s)\n'
        "end isoLocal\n"
        'tell application "Calendar"\n'
        f"{cal_setup}"
        '  set delim to character id 31\n'
        '  set recSep to character id 30\n'
        "  set outRecords to {}\n"
        "  repeat with theCal in cals\n"
        "    tell theCal\n"
        '      set evs to (every event whose start date >= startDate and start date <= endDate)\n'
        "      repeat with e in evs\n"
        '        set loc to ""\n'
        "        try\n"
        '          set loc to (location of e) as text\n'
        "        end try\n"
        '        set calLabel to (name of theCal) as text\n'
        '        set rec to (summary of e) & delim & isoLocal(start date of e) & delim & isoLocal(end date of e) & delim & calLabel & delim & loc\n'
        "        set end of outRecords to rec\n"
        "      end repeat\n"
        "    end tell\n"
        "  end repeat\n"
        "  set outText to \"\"\n"
        "  repeat with i from 1 to count of outRecords\n"
        "    if i > 1 then set outText to outText & recSep\n"
        "    set outText to outText & (item i of outRecords)\n"
        "  end repeat\n"
        "  return outText\n"
        "end tell\n"
    )


def _parse_local_iso(iso: str, local_tz: tzinfo) -> datetime | None:
    """Parse a local ISO instant and attach the injected local timezone.

    Returns ``None`` for malformed or non-parseable values so naive/invalid
    datetimes are rejected.
    """
    if not isinstance(iso, str) or not _ISO_LOCAL_RE.fullmatch(iso):
        return None
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    try:
        return dt.replace(tzinfo=local_tz)
    except Exception:
        return None


def _parse_records(
    text: str | bytes,
    local_tz: tzinfo,
    approved_calendar_name: str | None,
) -> tuple[CalendarEventItem, ...] | None:
    """Parse bounded delimited output into validated ``CalendarEventItem``s.

    Returns ``None`` on any malformed record, wrong field count, invalid
    datetime, unapproved calendar name, or contract violation.
    """
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
    if not isinstance(text, str):
        return None
    if len(text) > _MAX_OUTPUT_CHARS:
        return None
    if text == "":
        return ()

    records = text.split(_RECORD_SEP)
    items: list[CalendarEventItem] = []
    for rec in records:
        if rec == "":
            continue
        fields = rec.split(_FIELD_SEP)
        if len(fields) != _EXPECTED_FIELDS:
            return None
        title, start_iso, end_iso, cal_label, location = fields

        start = _parse_local_iso(start_iso, local_tz)
        end = _parse_local_iso(end_iso, local_tz)
        if start is None or end is None:
            return None
        if approved_calendar_name is not None and cal_label != approved_calendar_name:
            return None

        try:
            item = CalendarEventItem(
                title=title,
                start=start,
                end=end,
                calendar_label=cal_label,
                location=location or None,
            )
        except ActionResultContractError:
            return None

        items.append(item)
        if len(items) > CALENDAR_READ_EVENTS_MAX:
            return None

    return tuple(items)


def _decode(stdout: bytes) -> str:
    """Decode bounded stdout strictly; invalid bytes are rejected."""
    if not isinstance(stdout, (bytes, bytearray)):
        raise ValueError("stdout must be bytes")
    return bytes(stdout).decode("utf-8", errors="strict")


class CalendarReadMacAdapter:
    """Read approved Calendar.app events via injected runner and local timezone."""

    def __init__(
        self,
        runner: ReadCommandRunner | None = None,
        *,
        local_tz: tzinfo | None = None,
    ) -> None:
        self._runner = runner
        self._local_tz = local_tz

    def __call__(self, input: object) -> CalendarReadResult | AdapterResult:
        if not isinstance(input, CalendarReadAdapterInput):
            return _FAILED
        try:
            input.validate()
        except ValueError:
            return _FAILED

        local_tz = self._local_tz if self._local_tz is not None else _default_local_tz()
        script = _build_script(input, local_tz)
        if script is None:
            return _FAILED

        runner = (
            self._runner if self._runner is not None else production_osascript_runner
        )
        try:
            result = runner(
                (OSASCRIPT_PATH, "-e", script),
                timeout=OSASCRIPT_TIMEOUT_SECONDS,
            )
        except Exception:
            return _FAILED
        if not isinstance(result, ReadCommandResult):
            return _FAILED
        if result.returncode != 0:
            return _FAILED

        try:
            text = _decode(result.stdout)
            items = _parse_records(text, local_tz, input.calendar_name)
        except Exception:
            return _FAILED
        if items is None:
            return _FAILED

        try:
            return CalendarReadResult(
                events=items, calendar_label=input.calendar_name
            )
        except ActionResultContractError:
            return _FAILED
