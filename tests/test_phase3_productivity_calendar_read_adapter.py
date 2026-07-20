"""Deterministic tests for the bounded Phase 3 macOS Calendar read adapter.

Tests use only fake runners; they never launch Calendar.app or osascript.
"""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo

import pytest

from core.productivity.action_inputs import CalendarReadAdapterInput
from core.productivity.action_results import (
    CALENDAR_LABEL_MAX,
    CALENDAR_LOCATION_MAX,
    CALENDAR_READ_EVENTS_MAX,
    CALENDAR_TITLE_MAX,
    CalendarEventItem,
    CalendarReadResult,
)
from core.productivity.adapters.calendar_read import (
    CalendarReadMacAdapter,
    ReadCommandResult,
    _build_script,
    _parse_local_iso,
    _parse_records,
    production_osascript_runner,
)
from core.productivity.execution import AdapterResult, AdapterResultStatus


# ---------------------------------------------------------------------------
# Fake runner
# ---------------------------------------------------------------------------


class FakeRunner:
    """Returns a fixed ``ReadCommandResult`` without touching the filesystem."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: bytes = b"",
        raise_error: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.raise_error = raise_error
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def __call__(self, argv: tuple[str, ...], *, timeout: float) -> ReadCommandResult:
        if self.raise_error:
            raise RuntimeError("boom")
        self.calls.append((tuple(argv), timeout))
        return ReadCommandResult(returncode=self.returncode, stdout=self.stdout)


def _input(start: str, end: str, calendar_name: str | None = None) -> CalendarReadAdapterInput:
    return CalendarReadAdapterInput(start=start, end=end, calendar_name=calendar_name)


def _record(
    title: str,
    start: str,
    end: str,
    label: str,
    location: str = "",
) -> bytes:
    return f"{title}\x1f{start}\x1f{end}\x1f{label}\x1f{location}".encode("utf-8")


# ---------------------------------------------------------------------------
# Construction / import isolation
# ---------------------------------------------------------------------------


def test_construction_performs_no_calendar_access() -> None:
    # Construction must not invoke a runner or touch Calendar.
    adapter = CalendarReadMacAdapter()
    assert adapter._runner is None
    assert adapter._local_tz is None


def test_import_performs_no_calendar_access() -> None:
    # Importing the module must not raise or access Calendar.
    import core.productivity.adapters.calendar_read as mod

    assert mod.CalendarReadMacAdapter is not None
    assert mod.production_osascript_runner is not None


# ---------------------------------------------------------------------------
# Acceptance criteria
# ---------------------------------------------------------------------------


def test_accepts_only_calendar_read_input() -> None:
    adapter = CalendarReadMacAdapter(FakeRunner())
    # Wrong input type must fail closed with a fixed AdapterResult.
    result = adapter(object())  # type: ignore[arg-type]
    assert isinstance(result, AdapterResult)
    assert result.status is AdapterResultStatus.FAILED


def test_invalid_input_fails_closed() -> None:
    adapter = CalendarReadMacAdapter(FakeRunner())
    # Naive (invalid) start datetime fails validation.
    bad = _input("2026-07-19T14:00:00", "2026-07-19T15:00:00Z", None)
    result = adapter(bad)
    assert isinstance(result, AdapterResult)
    assert result.status is AdapterResultStatus.FAILED


def test_successful_read_returns_calendar_read_result() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    out = _record("Planning", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work", "Room A")
    adapter = CalendarReadMacAdapter(FakeRunner(stdout=out))
    result = adapter(inp)
    assert isinstance(result, CalendarReadResult)
    assert len(result.events) == 1
    assert result.events[0].title == "Planning"
    assert result.events[0].calendar_label == "Work"
    assert result.events[0].location == "Room A"
    assert result.calendar_label == "Work"


def test_empty_output_returns_empty_result() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z")
    adapter = CalendarReadMacAdapter(FakeRunner(stdout=b""))
    result = adapter(inp)
    assert isinstance(result, CalendarReadResult)
    assert result.events == ()
    assert result.calendar_label is None


def test_runner_invokes_only_osascript_with_fixed_timeout() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    runner = FakeRunner(stdout=_record("T", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work"))
    adapter = CalendarReadMacAdapter(runner)
    adapter(inp)
    assert len(runner.calls) == 1
    argv, timeout = runner.calls[0]
    assert argv[0] == "/usr/bin/osascript"
    assert argv[1] == "-e"
    assert timeout == 15.0


def test_production_runner_rejects_non_osascript() -> None:
    result = production_osascript_runner(("/bin/echo", "x"), timeout=15.0)
    assert result.returncode == 1
    assert result.stdout == b""


def test_production_runner_rejects_empty_argv() -> None:
    result = production_osascript_runner((), timeout=15.0)
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# Requirement 5: no fallback from named calendar
# ---------------------------------------------------------------------------


def test_named_calendar_script_references_exact_calendar() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    script = _build_script(inp, timezone.utc)
    assert script is not None
    assert 'calendar "Work"' in script
    # Only the exact calendar is queried; no "every calendar" fallback.
    assert "every calendar" not in script


def test_unnamed_calendar_script_queries_every_calendar() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z")
    script = _build_script(inp, timezone.utc)
    assert script is not None
    assert "set cals to every calendar" in script


def test_unapproved_calendar_name_in_output_rejected() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    # Output reports a different calendar label than approved.
    out = _record("T", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Other")
    adapter = CalendarReadMacAdapter(FakeRunner(stdout=out))
    result = adapter(inp)
    assert isinstance(result, AdapterResult)
    assert result.status is AdapterResultStatus.FAILED


# ---------------------------------------------------------------------------
# Requirement 6: local instant conversion
# ---------------------------------------------------------------------------


def test_script_uses_local_date_blocks() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    script = _build_script(inp, timezone.utc)
    assert script is not None
    assert "set startDate to current date" in script
    assert "set endDate to current date" in script
    # Comparison happens in local Calendar time.
    assert "start date >= startDate and start date <= endDate" in script


def test_parse_local_iso_attaches_local_tz() -> None:
    tz = timezone.utc
    dt = _parse_local_iso("2026-07-19T10:00:00", tz)
    assert dt is not None
    assert dt.tzinfo is tz
    # Naive/invalid rejected.
    assert _parse_local_iso("not-a-date", tz) is None
    assert _parse_local_iso("2026-07-19T10:00:00Z", tz) is None  # has offset


# ---------------------------------------------------------------------------
# Requirement 8/9: bounds and rejection of malformed records
# ---------------------------------------------------------------------------


def test_rejects_wrong_field_count() -> None:
    items = _parse_records("a\x1fb\x1fc\x1fd", timezone.utc, None)
    assert items is None


def test_rejects_naive_datetime_in_output() -> None:
    # A record whose datetime cannot be made aware is rejected.
    out = "T\x1f2026-07-19T10:00:00Z\x1f2026-07-19T11:00:00\x1fWork\x1f"
    items = _parse_records(out, timezone.utc, None)
    assert items is None


def test_rejects_invalid_ordering() -> None:
    out = _record("T", "2026-07-19T11:00:00", "2026-07-19T10:00:00", "Work")
    items = _parse_records(out, timezone.utc, None)
    assert items is None


def test_rejects_control_char_in_content() -> None:
    out = _record("T\x01x", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work")
    items = _parse_records(out, timezone.utc, None)
    assert items is None


def test_rejects_unicode_cf_in_content() -> None:
    out = _record("T\u202ex", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work")
    items = _parse_records(out, timezone.utc, None)
    assert items is None


def test_rejects_overlong_title() -> None:
    long_title = "T" * (CALENDAR_TITLE_MAX + 1)
    out = _record(long_title, "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work")
    items = _parse_records(out, timezone.utc, None)
    assert items is None


def test_rejects_overlong_location() -> None:
    long_loc = "L" * (CALENDAR_LOCATION_MAX + 1)
    out = _record("T", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work", long_loc)
    items = _parse_records(out, timezone.utc, None)
    assert items is None


def test_rejects_overlong_calendar_label() -> None:
    long_label = "C" * (CALENDAR_LABEL_MAX + 1)
    out = _record("T", "2026-07-19T10:00:00", "2026-07-19T11:00:00", long_label)
    items = _parse_records(out, timezone.utc, None)
    assert items is None


def test_rejects_empty_title() -> None:
    out = _record("", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work")
    items = _parse_records(out, timezone.utc, None)
    assert items is None


def test_bounds_event_count() -> None:
    records = []
    for i in range(CALENDAR_READ_EVENTS_MAX + 1):
        records.append(
            f"T{i}\x1f2026-07-19T10:00:00\x1f2026-07-19T11:00:00\x1fWork\x1f"
        )
    out = "\x1e".join(records).encode("utf-8")
    items = _parse_records(out.decode("utf-8"), timezone.utc, None)
    assert items is None


def test_accepts_max_event_count() -> None:
    records = []
    for i in range(CALENDAR_READ_EVENTS_MAX):
        records.append(
            f"T{i}\x1f2026-07-19T10:00:00\x1f2026-07-19T11:00:00\x1fWork\x1f"
        )
    out = "\x1e".join(records).encode("utf-8")
    items = _parse_records(out.decode("utf-8"), timezone.utc, None)
    assert items is not None
    assert len(items) == CALENDAR_READ_EVENTS_MAX


def test_rejects_oversized_output() -> None:
    big = "x" * 1_000_000
    out = _record(big, "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work").decode("utf-8")
    items = _parse_records(out, timezone.utc, None)
    assert items is None


# ---------------------------------------------------------------------------
# Requirement 10/11: fail closed, no leaks
# ---------------------------------------------------------------------------


def test_runner_returncode_nonzero_fails_closed() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    adapter = CalendarReadMacAdapter(FakeRunner(returncode=1, stdout=b"err"))
    result = adapter(inp)
    assert isinstance(result, AdapterResult)
    assert result.status is AdapterResultStatus.FAILED
    # No error detail leaked.
    assert "err" not in str(result)


def test_runner_exception_fails_closed() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    adapter = CalendarReadMacAdapter(FakeRunner(raise_error=True))
    result = adapter(inp)
    assert isinstance(result, AdapterResult)
    assert result.status is AdapterResultStatus.FAILED


def test_runner_wrong_result_type_fails_closed() -> None:
    class BadResult:
        returncode = 0

    class BadRunner:
        def __call__(self, argv, *, timeout):
            return BadResult()  # type: ignore[return-value]

    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    adapter = CalendarReadMacAdapter(BadRunner())
    result = adapter(inp)
    assert isinstance(result, AdapterResult)
    assert result.status is AdapterResultStatus.FAILED


def test_invalid_utf8_stdout_fails_closed() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    adapter = CalendarReadMacAdapter(FakeRunner(stdout=b"\xff\xfe"))
    result = adapter(inp)
    assert isinstance(result, AdapterResult)
    assert result.status is AdapterResultStatus.FAILED


def test_failed_result_has_fixed_shape() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    adapter = CalendarReadMacAdapter(FakeRunner(returncode=1))
    result = adapter(inp)
    assert isinstance(result, AdapterResult)
    assert result.status is AdapterResultStatus.FAILED
    assert result.code == "failed"


def test_success_repr_does_not_leak_content() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    out = _record("Secret Title", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work", "Secret Loc")
    adapter = CalendarReadMacAdapter(FakeRunner(stdout=out))
    result = adapter(inp)
    assert isinstance(result, CalendarReadResult)
    text = repr(result)
    assert "Secret Title" not in text
    assert "Secret Loc" not in text
    assert "Work" not in text
    assert text == f"CalendarReadResult(events={len(result.events)})"


def test_event_item_repr_is_content_free() -> None:
    item = CalendarEventItem(
        title="Secret",
        start=datetime(2026, 7, 19, 10, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 7, 19, 11, 0, 0, tzinfo=timezone.utc),
        calendar_label="Work",
        location="Loc",
    )
    assert repr(item) == "CalendarEventItem(...)"


# ---------------------------------------------------------------------------
# Requirement 2: injected runner and timezone
# ---------------------------------------------------------------------------


def test_injected_local_tz_is_used() -> None:
    from zoneinfo import ZoneInfo

    fixed = ZoneInfo("America/New_York")
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    out = _record("T", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work")
    adapter = CalendarReadMacAdapter(FakeRunner(stdout=out), local_tz=fixed)
    result = adapter(inp)
    assert isinstance(result, CalendarReadResult)
    # Parsed event instants carry the injected timezone.
    assert result.events[0].start.tzinfo is fixed


def test_default_local_tz_when_none() -> None:
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    out = _record("T", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work")
    adapter = CalendarReadMacAdapter(FakeRunner(stdout=out))
    result = adapter(inp)
    assert isinstance(result, CalendarReadResult)
    assert result.events[0].start.tzinfo is not None


# ---------------------------------------------------------------------------
# Multiple events and location omission
# ---------------------------------------------------------------------------


def test_multiple_events_parsed() -> None:
    recs = [
        _record("A", "2026-07-19T10:00:00", "2026-07-19T10:30:00", "Work"),
        _record("B", "2026-07-19T11:00:00", "2026-07-19T11:30:00", "Work", "Loc B"),
    ]
    out = "\x1e".join(r.decode("utf-8") for r in recs).encode("utf-8")
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    adapter = CalendarReadMacAdapter(FakeRunner(stdout=out))
    result = adapter(inp)
    assert isinstance(result, CalendarReadResult)
    assert len(result.events) == 2
    assert result.events[0].title == "A"
    assert result.events[1].location == "Loc B"


def test_empty_location_becomes_none() -> None:
    out = _record("T", "2026-07-19T10:00:00", "2026-07-19T11:00:00", "Work", "")
    inp = _input("2026-07-19T14:00:00Z", "2026-07-19T15:30:00Z", "Work")
    adapter = CalendarReadMacAdapter(FakeRunner(stdout=out))
    result = adapter(inp)
    assert isinstance(result, CalendarReadResult)
    assert result.events[0].location is None
