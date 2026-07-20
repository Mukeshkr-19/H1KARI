"""Source contracts for isolated Phase 3 scheduled-job creation primitives."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HELPERS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "scheduleProposal.ts"
)
HELPER_TESTS = HELPERS.with_name("scheduleProposal.test.ts")
COMPONENT = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "ScheduledJobCreateForm.tsx"
)


def _read(path: Path) -> str:
    assert path.is_file()
    return path.read_text(encoding="utf-8")


def test_creation_primitive_files_exist():
    assert HELPERS.is_file()
    assert HELPER_TESTS.is_file()
    assert COMPONENT.is_file()


def test_helpers_limit_actions_attempts_and_horizon():
    text = _read(HELPERS)
    assert '"browser.research"' in text
    assert '"calendar.read"' in text
    assert '"email.draft"' not in text
    assert "SCHEDULE_MAX_HORIZON_SECONDS = 365 * 24 * 3600" in text
    assert "SCHEDULE_MIN_ATTEMPTS = 1" in text
    assert "SCHEDULE_MAX_ATTEMPTS = 5" in text
    assert "QUIET_HOURS_MINUTE_MAX = 1439" in text
    assert "parseCalendarInstantMicros" in text
    assert "next_run_not_future" in text
    assert "next_run_horizon_too_long" in text
    assert "reduceScheduleProposalClientState" in text


def test_validation_is_exact_control_safe_and_clock_injected():
    text = _read(HELPERS)
    assert "export type ScheduleClock = () => bigint" in text
    assert "clock: ScheduleClock" in text
    assert "nowMicros = clock()" in text
    assert "hasCalendarUnicodeFormatChars" in text
    assert "hasAsciiControls" in text
    assert "hasOnlyKnownFields" in text
    assert "isValidScheduleTimezone" in text
    assert "Intl.DateTimeFormat" in text
    assert "Date.now" not in text
    assert "Date.parse" not in text
    assert ".trim()" not in text


def test_helpers_have_no_transport_storage_timer_or_execution_side_effects():
    text = _read(HELPERS)
    for banned in (
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "setInterval",
        "console.",
        "child_process",
        "osascript",
        "execute(",
    ):
        assert banned not in text


def test_form_is_callback_only_labelled_and_keyboard_native():
    text = _read(COMPONENT)
    for label in (
        "htmlFor={actionId}",
        "htmlFor={nextRunAtId}",
        "htmlFor={maxAttemptsId}",
        "htmlFor={quietEnabledId}",
        "htmlFor={quietStartId}",
        "htmlFor={quietEndId}",
        "htmlFor={quietTimezoneId}",
    ):
        assert label in text
    assert "<form" in text
    assert "onSubmit={handleSubmit}" in text
    assert 'type="checkbox"' in text
    assert "<fieldset" in text
    assert "<legend" in text
    assert 'role="alert"' in text
    assert "validationId" in text
    assert "aria-describedby" in text
    assert "disabled={locked}" in text
    assert "Prepare scheduled job" in text
    assert "Nothing is" in text and "scheduled from this form" in text
    assert "autoFocus" not in text
    for banned in (
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "setInterval",
        "console.",
    ):
        assert banned not in text


def test_unit_tests_cover_boundaries_and_privacy_rules():
    text = _read(HELPER_TESTS)
    for needle in (
        "validates both supported one-shot actions and freezes output",
        "rejects unknown actions, unknown fields, and malformed field types",
        "requires an explicit timezone and rejects impossible instants",
        "preserves microsecond precision at the strict future boundary",
        "accepts the exact 365-day horizon and rejects one microsecond beyond",
        "calls the injected clock once and fails safely for invalid clocks",
        "enforces integer maximum attempts from one through five",
        "requires disabled quiet-hours fields to remain empty",
        "validates and deeply freezes an enabled cross-midnight quiet window",
        "rejects missing, unbounded, and empty enabled quiet windows",
        "validates IANA timezones and rejects controls without rewriting",
        "rejects control and Unicode format characters in structural fields",
        "maps only fixed validation messages",
        "reducer blocks duplicate submit and clears state deterministically",
    ):
        assert needle in text
