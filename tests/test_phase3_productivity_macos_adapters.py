"""Deterministic tests for bounded macOS productivity write adapters.

Uses fake command runners only. Never invokes live AppleScript.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.productivity.action_inputs import (
    BrowserResearchAdapterInput,
    CalendarDraftAdapterInput,
    EmailDraftAdapterInput,
    ReminderCreateAdapterInput,
)
from core.productivity.adapters.macos_actions import (
    OSASCRIPT_PATH,
    OSASCRIPT_TIMEOUT_SECONDS,
    CalendarDraftMacAdapter,
    CommandResult,
    EmailDraftMacAdapter,
    ReminderCreateMacAdapter,
    escape_applescript_string,
    production_osascript_runner,
)
from core.productivity.execution import AdapterResultStatus


class FakeRunner:
    def __init__(self, returncode: int = 0, *, raise_exc: BaseException | None = None) -> None:
        self.returncode = returncode
        self.raise_exc = raise_exc
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def __call__(self, argv, *, timeout: float) -> CommandResult:
        self.calls.append((tuple(argv), timeout))
        if self.raise_exc is not None:
            raise self.raise_exc
        return CommandResult(returncode=self.returncode)


def _script_from_call(runner: FakeRunner) -> str:
    assert len(runner.calls) == 1
    argv, timeout = runner.calls[0]
    assert timeout == OSASCRIPT_TIMEOUT_SECONDS
    assert argv[0] == OSASCRIPT_PATH
    assert argv[1] == "-e"
    assert len(argv) == 3
    return argv[2]


def _structural_script(script: str) -> str:
    """Strip AppleScript string literals so content cannot mask command verbs."""
    return re.sub(r'"((?:\\.|[^"\\])*)"', '""', script)


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


def test_escape_applescript_quotes_backslashes_and_line_breaks() -> None:
    assert escape_applescript_string('say "hi"') == '"say \\"hi\\""'
    assert escape_applescript_string("a\\b") == '"a\\\\b"'
    assert escape_applescript_string("a\nb") == '"a" & linefeed & "b"'
    assert escape_applescript_string("a\rb") == '"a" & return & "b"'
    assert escape_applescript_string("a\tb") == '"a" & tab & "b"'
    assert escape_applescript_string("café 📌") == '"café 📌"'
    assert 'end tell' in escape_applescript_string('"; end tell --')


# ---------------------------------------------------------------------------
# Construction / import side effects
# ---------------------------------------------------------------------------


def test_constructing_adapters_does_not_invoke_runner() -> None:
    runner = FakeRunner()
    EmailDraftMacAdapter(runner)
    CalendarDraftMacAdapter(runner)
    ReminderCreateMacAdapter(runner)
    assert runner.calls == []


def test_importing_module_does_not_execute_commands() -> None:
    source = (
        Path(__file__).resolve().parent.parent
        / "core"
        / "productivity"
        / "adapters"
        / "macos_actions.py"
    ).read_text(encoding="utf-8")
    body_before_defs = source.split("def production_osascript_runner", 1)[0]
    assert "subprocess.run(" not in body_before_defs
    assert "Popen(" not in body_before_defs


# ---------------------------------------------------------------------------
# Email draft
# ---------------------------------------------------------------------------


def test_email_draft_builds_visible_draft_without_send() -> None:
    runner = FakeRunner()
    adapter = EmailDraftMacAdapter(runner)
    result = adapter(
        EmailDraftAdapterInput(
            'user@example.com',
            'Hello "friend"',
            "Line 1\nLine 2",
        )
    )
    assert result.status is AdapterResultStatus.SUCCESS
    script = _script_from_call(runner)
    assert 'tell application "Mail"' in script
    assert "visible:true" in script
    assert "make new outgoing message" in script
    assert "make new to recipient" in script
    assert escape_applescript_string("user@example.com") in script
    assert escape_applescript_string('Hello "friend"') in script
    assert escape_applescript_string("Line 1\nLine 2") in script
    structural = _structural_script(script)
    assert re.search(r"\bsend\b", structural, flags=re.IGNORECASE) is None
    assert "save" not in structural.lower()


def test_email_draft_rejects_wrong_input_without_running() -> None:
    runner = FakeRunner()
    adapter = EmailDraftMacAdapter(runner)
    result = adapter(BrowserResearchAdapterInput("query", ("example.com",), 5))
    assert result.status is AdapterResultStatus.FAILED
    assert result.code == "failed"
    assert runner.calls == []


def test_email_draft_runner_failure_modes() -> None:
    for runner in (
        FakeRunner(returncode=1),
        FakeRunner(raise_exc=TimeoutError("private")),
        FakeRunner(raise_exc=OSError("missing")),
    ):
        adapter = EmailDraftMacAdapter(runner)
        result = adapter(EmailDraftAdapterInput("a@b.co", "S", "B"))
        assert result == result  # fixed shape
        assert result.status is AdapterResultStatus.FAILED
        assert result.code == "failed"
        assert "private" not in str(result)
        assert "missing" not in str(result)
        assert len(runner.calls) == 1


# ---------------------------------------------------------------------------
# Calendar draft
# ---------------------------------------------------------------------------


def test_calendar_draft_targets_exact_named_calendar_only() -> None:
    runner = FakeRunner()
    adapter = CalendarDraftMacAdapter(runner)
    result = adapter(
        CalendarDraftAdapterInput(
            "Planning sync",
            "2026-07-21T13:00:00.111111Z",
            "2026-07-21T14:30:00Z",
            "Work",
            None,
            None,
        )
    )
    assert result.status is AdapterResultStatus.SUCCESS
    script = _script_from_call(runner)
    assert 'tell application "Calendar"' in script
    assert f"set theCal to calendar {escape_applescript_string('Work')}" in script
    assert "if writable of theCal is false then error" in script
    assert "first calendar whose writable" not in script
    assert "make new event" in script
    assert escape_applescript_string("Planning sync") in script
    assert "location:" not in script
    assert "description:" not in script


def test_calendar_draft_includes_optional_location_and_notes() -> None:
    runner = FakeRunner()
    adapter = CalendarDraftMacAdapter(runner)
    result = adapter(
        CalendarDraftAdapterInput(
            "Title",
            "2026-07-21T13:00:00-04:00",
            "2026-07-21T14:00:00-04:00",
            "Personal",
            'Room "A"\nEast',
            "Bring\tnotes",
        )
    )
    assert result.status is AdapterResultStatus.SUCCESS
    script = _script_from_call(runner)
    assert f"set theCal to calendar {escape_applescript_string('Personal')}" in script
    assert escape_applescript_string('Room "A"\nEast') in script
    assert escape_applescript_string("Bring\tnotes") in script
    assert "location:" in script
    assert "description:" in script


def test_calendar_draft_converts_plus0530_to_injected_minus0400_local() -> None:
    from datetime import timedelta, timezone

    runner = FakeRunner()
    local_tz = timezone(timedelta(hours=-4))
    adapter = CalendarDraftMacAdapter(runner, local_tz=local_tz)
    # 18:30 +05:30 == 13:00 UTC == 09:00 -04:00
    result = adapter(
        CalendarDraftAdapterInput(
            "Title",
            "2026-07-21T18:30:00.123456+05:30",
            "2026-07-21T19:30:00+05:30",
            "Work",
            None,
            None,
        )
    )
    assert result.status is AdapterResultStatus.SUCCESS
    script = _script_from_call(runner)
    assert "set year of startDate to 2026" in script
    assert "set day of startDate to 21" in script
    assert "set hours of startDate to 9" in script
    assert "set minutes of startDate to 0" in script
    assert "set seconds of startDate to 0" in script
    assert "set hours of endDate to 10" in script


def test_calendar_draft_utc_crossing_local_calendar_day_boundary() -> None:
    from datetime import timedelta, timezone

    runner = FakeRunner()
    local_tz = timezone(timedelta(hours=-4))
    adapter = CalendarDraftMacAdapter(runner, local_tz=local_tz)
    result = adapter(
        CalendarDraftAdapterInput(
            "Title",
            "2026-07-22T02:00:00Z",
            "2026-07-22T03:00:00Z",
            "Work",
            None,
            None,
        )
    )
    assert result.status is AdapterResultStatus.SUCCESS
    script = _script_from_call(runner)
    assert "set day of startDate to 21" in script
    assert "set hours of startDate to 22" in script
    assert "set day of endDate to 21" in script
    assert "set hours of endDate to 23" in script


def test_calendar_draft_unknown_destination_fails_without_fallback() -> None:
    runner = FakeRunner(returncode=1)
    adapter = CalendarDraftMacAdapter(runner)
    result = adapter(
        CalendarDraftAdapterInput(
            "Title",
            "2026-07-21T13:00:00Z",
            "2026-07-21T14:00:00Z",
            "Missing Calendar",
            None,
            None,
        )
    )
    assert result.status is AdapterResultStatus.FAILED
    script = _script_from_call(runner)
    assert "first calendar whose writable" not in script
    assert (
        f"set theCal to calendar {escape_applescript_string('Missing Calendar')}"
        in script
    )


def test_calendar_draft_rejects_wrong_input_and_empty_calendar_name() -> None:
    runner = FakeRunner()
    adapter = CalendarDraftMacAdapter(runner)
    wrong = adapter(EmailDraftAdapterInput("a@b.co", "S", "B"))
    assert wrong.status is AdapterResultStatus.FAILED
    assert runner.calls == []

    with pytest.raises(ValueError):
        CalendarDraftAdapterInput(
            "Title",
            "2026-07-21T13:00:00Z",
            "2026-07-21T14:00:00Z",
            "",
            None,
            None,
        ).validate()


def test_absolute_local_components_preserves_microseconds() -> None:
    from datetime import timedelta, timezone

    from core.productivity.adapters.macos_actions import absolute_local_components

    local_tz = timezone(timedelta(hours=-4))
    parts = absolute_local_components("2026-07-21T18:30:00.123456+05:30", local_tz)
    assert parts == (2026, 7, 21, 9, 0, 0, 123456)


# ---------------------------------------------------------------------------
# Reminder create
# ---------------------------------------------------------------------------


def test_reminder_create_uses_list_when_present() -> None:
    from datetime import timezone

    runner = FakeRunner()
    adapter = ReminderCreateMacAdapter(runner, local_tz=timezone.utc)
    result = adapter(
        ReminderCreateAdapterInput(
            "Pick up package",
            "2026-08-01T09:00:00-04:00",
            "Bring ID",
            "Errands",
        )
    )
    assert result.status is AdapterResultStatus.SUCCESS
    script = _script_from_call(runner)
    assert 'tell application "Reminders"' in script
    assert f"tell list {escape_applescript_string('Errands')}" in script
    assert escape_applescript_string("Pick up package") in script
    assert escape_applescript_string("Bring ID") in script
    # 09:00-04:00 == 13:00 UTC
    assert "set hours of dueDate to 13" in script
    assert "make new reminder" in script


def test_reminder_timezone_conversion_with_injected_local_tz() -> None:
    from datetime import timedelta, timezone

    runner = FakeRunner()
    local_tz = timezone(timedelta(hours=-4))
    adapter = ReminderCreateMacAdapter(runner, local_tz=local_tz)
    result = adapter(
        ReminderCreateAdapterInput(
            "Title",
            "2026-08-01T18:30:00+05:30",
            None,
            None,
        )
    )
    assert result.status is AdapterResultStatus.SUCCESS
    script = _script_from_call(runner)
    assert "set day of dueDate to 1" in script
    assert "set hours of dueDate to 9" in script
    assert "set minutes of dueDate to 0" in script


def test_reminder_create_omits_optional_list_and_notes() -> None:
    from datetime import timezone

    runner = FakeRunner()
    adapter = ReminderCreateMacAdapter(runner, local_tz=timezone.utc)
    result = adapter(
        ReminderCreateAdapterInput(
            "Title",
            "2026-08-02T12:00:00Z",
            None,
            None,
        )
    )
    assert result.status is AdapterResultStatus.SUCCESS
    script = _script_from_call(runner)
    assert "tell list" not in script
    assert "body:" not in script
    assert "make new reminder with properties" in script
    assert "set hours of dueDate to 12" in script


def test_reminder_rejects_wrong_input_without_running() -> None:
    runner = FakeRunner()
    result = ReminderCreateMacAdapter(runner)(
        CalendarDraftAdapterInput(
            "Title",
            "2026-07-21T13:00:00Z",
            "2026-07-21T14:00:00Z",
            "Work",
            None,
            None,
        )
    )
    assert result.status is AdapterResultStatus.FAILED
    assert runner.calls == []


# ---------------------------------------------------------------------------
# Shared failure / privacy / runner contracts
# ---------------------------------------------------------------------------


def test_nonzero_timeout_and_exception_return_fixed_failed_result() -> None:
    adapters = (
        (
            EmailDraftMacAdapter,
            EmailDraftAdapterInput("a@b.co", "S", "B"),
        ),
        (
            CalendarDraftMacAdapter,
            CalendarDraftAdapterInput(
                "T",
                "2026-07-21T13:00:00Z",
                "2026-07-21T14:00:00Z",
                "Work",
                None,
                None,
            ),
        ),
        (
            ReminderCreateMacAdapter,
            ReminderCreateAdapterInput("T", "2026-08-01T09:00:00Z", None, None),
        ),
    )
    for factory, payload in adapters:
        runner = FakeRunner(returncode=7)
        result = factory(runner)(payload)
        assert result.status is AdapterResultStatus.FAILED
        assert result.code == "failed"
        assert len(runner.calls) == 1
        assert runner.calls[0][0][0] == OSASCRIPT_PATH
        assert runner.calls[0][1] == OSASCRIPT_TIMEOUT_SECONDS


def test_malformed_runner_result_fails_closed() -> None:
    class BadRunner:
        def __call__(self, argv, *, timeout: float):
            return {"returncode": 0}

    result = EmailDraftMacAdapter(BadRunner())(  # type: ignore[arg-type]
        EmailDraftAdapterInput("a@b.co", "S", "B")
    )
    assert result.status is AdapterResultStatus.FAILED
    assert result.code == "failed"


def test_adapter_results_are_content_free() -> None:
    runner = FakeRunner(returncode=1)
    result = EmailDraftMacAdapter(runner)(
        EmailDraftAdapterInput("secret@example.com", "Private", "Body")
    )
    text = repr(result)
    assert "secret@example.com" not in text
    assert "Private" not in text
    assert "Body" not in text
    assert str(result.status) == "AdapterResultStatus.FAILED" or result.status.value == "failed"


def test_production_runner_rejects_non_osascript_argv_without_shell() -> None:
    source = (
        Path(__file__).resolve().parent.parent
        / "core"
        / "productivity"
        / "adapters"
        / "macos_actions.py"
    ).read_text(encoding="utf-8")
    assert "shell=False" in source or "shell=True" not in source
    assert 'shell=True' not in source
    assert OSASCRIPT_PATH in source
    # Direct unit check: wrong binary path fails closed without executing shell.
    result = production_osascript_runner(["/bin/echo", "nope"], timeout=1.0)
    assert result.returncode != 0


def test_source_has_no_forbidden_side_effects() -> None:
    source = (
        Path(__file__).resolve().parent.parent
        / "core"
        / "productivity"
        / "adapters"
        / "macos_actions.py"
    ).read_text(encoding="utf-8")
    for banned in (
        "import logging",
        "from logging",
        "requests",
        "urllib",
        "browser_automation",
        "mac_integration",
        "smtplib",
        "sqlite3",
        "random",
        "time.sleep",
        "shell=True",
    ):
        assert banned not in source
    email_class = source.split("class EmailDraftMacAdapter", 1)[1].split(
        "class CalendarDraftMacAdapter", 1
    )[0]
    assert re.search(r"\bsend\b", email_class, flags=re.IGNORECASE) is None
