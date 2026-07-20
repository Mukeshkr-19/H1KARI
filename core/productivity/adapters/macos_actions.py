"""Bounded macOS write adapters for approved Phase 3 productivity actions.

Synchronous ``ActionAdapter``-compatible callables that create Mail drafts,
Calendar events, and Reminders items via ``/usr/bin/osascript``. They never
send mail, never retry, and never return command text, AppleScript, paths,
stdout/stderr, exception text, or user content.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Protocol

from core.productivity.action_inputs import (
    CalendarDraftAdapterInput,
    EmailDraftAdapterInput,
    ReminderCreateAdapterInput,
)
from core.productivity.execution import (
    AdapterInput,
    AdapterResult,
    AdapterResultStatus,
)

OSASCRIPT_PATH = "/usr/bin/osascript"
OSASCRIPT_TIMEOUT_SECONDS = 15.0
_MAX_CAPTURE_BYTES = 4096

# AppleScript date objects are second-precision on macOS; adapter inputs still
# preserve microsecond-capable ISO strings through validation/conversion.
_FAILED = AdapterResult(AdapterResultStatus.FAILED, code="failed")
_SUCCESS = AdapterResult(AdapterResultStatus.SUCCESS)


@dataclass(frozen=True)
class CommandResult:
    """Bounded command outcome. Stdout/stderr are never retained here."""

    returncode: int


class CommandRunner(Protocol):
    """Injected runner for argv arrays. Production uses osascript; tests use fakes."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
    ) -> CommandResult: ...


def escape_applescript_string(value: str) -> str:
    """Return a quoted AppleScript string literal for ``value``.

    Escapes backslashes and quotes, and lifts CR/LF/tab out of the literal via
    AppleScript ``return`` / ``linefeed`` / ``tab`` so delimiter-like content
    cannot break out of the string. Unicode is preserved exactly.
    """
    if not isinstance(value, str):
        raise TypeError("AppleScript string value must be str")
    parts: list[str] = ['"']
    for char in value:
        if char == "\\":
            parts.append("\\\\")
        elif char == '"':
            parts.append('\\"')
        elif char == "\r":
            parts.append('" & return & "')
        elif char == "\n":
            parts.append('" & linefeed & "')
        elif char == "\t":
            parts.append('" & tab & "')
        else:
            parts.append(char)
    parts.append('"')
    return "".join(parts)


def production_osascript_runner(
    argv: Sequence[str],
    *,
    timeout: float,
) -> CommandResult:
    """Run ``argv`` without a shell. Capture is bounded and discarded."""
    if not isinstance(argv, (list, tuple)) or not argv:
        return CommandResult(returncode=1)
    if argv[0] != OSASCRIPT_PATH:
        return CommandResult(returncode=1)
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            shell=False,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return CommandResult(returncode=1)
    # Bound and discard captured bytes; never return them.
    _ = (completed.stdout or b"")[:_MAX_CAPTURE_BYTES]
    _ = (completed.stderr or b"")[:_MAX_CAPTURE_BYTES]
    code = completed.returncode
    if not isinstance(code, int):
        return CommandResult(returncode=1)
    return CommandResult(returncode=code)


def _default_local_tz() -> tzinfo:
    local = datetime.now().astimezone().tzinfo
    if local is None:
        from datetime import timezone

        return timezone.utc
    return local


def absolute_local_components(
    iso_value: str,
    local_tz: tzinfo,
) -> tuple[int, int, int, int, int, int, int] | None:
    """Convert an aware ISO instant to local wall-clock components.

    Returns ``(year, month, day, hour, minute, second, microsecond)`` for the
    same absolute instant expressed in ``local_tz``. Microseconds are preserved
    for callers; AppleScript date construction uses second precision.
    """
    if not isinstance(iso_value, str) or not iso_value:
        return None
    if not isinstance(local_tz, tzinfo):
        return None
    text = iso_value
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        instant = datetime.fromisoformat(text)
    except ValueError:
        return None
    if instant.tzinfo is None:
        return None
    try:
        local = instant.astimezone(local_tz)
    except Exception:
        return None
    return (
        local.year,
        local.month,
        local.day,
        local.hour,
        local.minute,
        local.second,
        local.microsecond,
    )


def _applescript_date_block(
    var_name: str,
    iso_value: str,
    local_tz: tzinfo,
) -> str | None:
    parts = absolute_local_components(iso_value, local_tz)
    if parts is None:
        return None
    year, month, day, hour, minute, second, _microsecond = parts
    return (
        f"set {var_name} to current date\n"
        "set monthList to {January, February, March, April, May, June, "
        "July, August, September, October, November, December}\n"
        f"set year of {var_name} to {year}\n"
        f"set month of {var_name} to item {month} of monthList\n"
        f"set day of {var_name} to {day}\n"
        f"set hours of {var_name} to {hour}\n"
        f"set minutes of {var_name} to {minute}\n"
        f"set seconds of {var_name} to {second}"
    )


def _run_script(
    runner: CommandRunner,
    script: str,
) -> AdapterResult:
    argv = (OSASCRIPT_PATH, "-e", script)
    try:
        result = runner(argv, timeout=OSASCRIPT_TIMEOUT_SECONDS)
    except Exception:
        return _FAILED
    if not isinstance(result, CommandResult):
        return _FAILED
    if result.returncode != 0:
        return _FAILED
    return _SUCCESS


class EmailDraftMacAdapter:
    """Create a visible Mail.app draft. Never transmits the message."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner

    def __call__(self, input: AdapterInput) -> AdapterResult:
        if not isinstance(input, EmailDraftAdapterInput):
            return _FAILED
        try:
            input.validate()
        except ValueError:
            return _FAILED
        recipient = escape_applescript_string(input.recipient)
        subject = escape_applescript_string(input.subject)
        body = escape_applescript_string(input.body)
        script = (
            'tell application "Mail"\n'
            "set newMessage to make new outgoing message with properties "
            f"{{visible:true, subject:{subject}, content:{body}}}\n"
            "tell newMessage\n"
            "make new to recipient at end of to recipients with properties "
            f"{{address:{recipient}}}\n"
            "end tell\n"
            "end tell"
        )
        runner = self._runner if self._runner is not None else production_osascript_runner
        return _run_script(runner, script)


class CalendarDraftMacAdapter:
    """Create the confirmed Calendar.app event on the exact named calendar."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        local_tz: tzinfo | None = None,
    ) -> None:
        self._runner = runner
        self._local_tz = local_tz

    def __call__(self, input: AdapterInput) -> AdapterResult:
        if not isinstance(input, CalendarDraftAdapterInput):
            return _FAILED
        try:
            input.validate()
        except ValueError:
            return _FAILED
        local_tz = self._local_tz if self._local_tz is not None else _default_local_tz()
        start_block = _applescript_date_block("startDate", input.start, local_tz)
        end_block = _applescript_date_block("endDate", input.end, local_tz)
        if start_block is None or end_block is None:
            return _FAILED
        title = escape_applescript_string(input.title)
        calendar_name = escape_applescript_string(input.calendar_name)
        props = [f"summary:{title}", "start date:startDate", "end date:endDate"]
        if input.location is not None:
            props.append(f"location:{escape_applescript_string(input.location)}")
        if input.notes is not None:
            props.append(f"description:{escape_applescript_string(input.notes)}")
        properties = ", ".join(props)
        script = (
            f"{start_block}\n"
            f"{end_block}\n"
            'tell application "Calendar"\n'
            f"set theCal to calendar {calendar_name}\n"
            "if writable of theCal is false then error \"calendar not writable\"\n"
            "tell theCal\n"
            f"make new event with properties {{{properties}}}\n"
            "end tell\n"
            "end tell"
        )
        runner = self._runner if self._runner is not None else production_osascript_runner
        return _run_script(runner, script)


class ReminderCreateMacAdapter:
    """Create the confirmed Reminders.app item."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        local_tz: tzinfo | None = None,
    ) -> None:
        self._runner = runner
        self._local_tz = local_tz

    def __call__(self, input: AdapterInput) -> AdapterResult:
        if not isinstance(input, ReminderCreateAdapterInput):
            return _FAILED
        try:
            input.validate()
        except ValueError:
            return _FAILED
        local_tz = self._local_tz if self._local_tz is not None else _default_local_tz()
        due_block = _applescript_date_block("dueDate", input.remind_at, local_tz)
        if due_block is None:
            return _FAILED
        title = escape_applescript_string(input.title)
        props = [f"name:{title}", "due date:dueDate"]
        if input.notes is not None:
            props.append(f"body:{escape_applescript_string(input.notes)}")
        properties = ", ".join(props)
        if input.list_name is not None:
            list_name = escape_applescript_string(input.list_name)
            body = (
                f"{due_block}\n"
                'tell application "Reminders"\n'
                f"tell list {list_name}\n"
                f"make new reminder with properties {{{properties}}}\n"
                "end tell\n"
                "end tell"
            )
        else:
            body = (
                f"{due_block}\n"
                'tell application "Reminders"\n'
                f"make new reminder with properties {{{properties}}}\n"
                "end tell"
            )
        runner = self._runner if self._runner is not None else production_osascript_runner
        return _run_script(runner, body)
