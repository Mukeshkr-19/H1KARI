"""Source contracts for Phase 3 calendar frontend primitives."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HELPERS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "calendarProposal.ts"
)
HELPER_TESTS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "calendarProposal.test.ts"
)
COMPONENT = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "CalendarProposalForm.tsx"
)
PACKAGE = REPO_ROOT / "hikari-frontend" / "package.json"


def _helpers() -> str:
    assert HELPERS.is_file()
    return HELPERS.read_text(encoding="utf-8")


def _tests() -> str:
    assert HELPER_TESTS.is_file()
    return HELPER_TESTS.read_text(encoding="utf-8")


def _component() -> str:
    assert COMPONENT.is_file()
    return COMPONENT.read_text(encoding="utf-8")


def test_calendar_files_exist():
    assert HELPERS.is_file()
    assert HELPER_TESTS.is_file()
    assert COMPONENT.is_file()


def test_page_wires_calendar_prepare_on_files_tab():
    page = (
        REPO_ROOT / "hikari-frontend" / "src" / "app" / "page.tsx"
    ).read_text(encoding="utf-8")
    assert "<CalendarProposalForm" in page
    assert "submitCalendarPrepare" in page
    assert "calendarRequestIdRef" in page
    assert "isProductivityPreparePending" in page
    assert "pending={productivityPreparePending}" in page
    email_submit_start = page.index("const submitEmailDraftPrepare")
    email_submit_end = page.index("const resetEmailDraftForm")
    email_submit = page[email_submit_start:email_submit_end]
    assert "if (isProductivityPreparePending())" in email_submit


def test_helpers_define_bounds_and_validation():
    text = _helpers()
    assert "CALENDAR_NAME_MAX = 200" in text
    assert "CALENDAR_TITLE_MAX = 500" in text
    assert "CALENDAR_LOCATION_MAX = 500" in text
    assert "CALENDAR_NOTES_MAX = 4000" in text
    assert "CALENDAR_MAX_RANGE_SECONDS = 31 * 24 * 3600" in text
    assert "export function validateCalendarReadFields(" in text
    assert "export function validateCalendarDraftFields(" in text
    assert "export function parseCalendarInstantMicros(" in text
    assert "export function calendarCodePointLength(" in text
    assert "export function isBlankCalendarTitle(" in text
    assert "hasCalendarUnicodeFormatChars" in text
    assert "\\p{Cf}" in text
    assert "\\p{White_Space}" in text
    assert "year < 1 || year > 9999" in text
    assert "start_missing_timezone" in text
    assert "range_too_long" in text
    assert "reduceCalendarProposalClientState" in text
    assert "Date.parse(" not in text
    assert "\\d{1,6}" in text
    assert ".trim()" not in text


def test_helpers_exclude_side_effects_and_sensitive_sinks():
    text = _helpers()
    for banned in (
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "console.",
        "EventKit",
        "provider",
        "calendar access",
    ):
        assert banned not in text


def test_component_is_labelled_and_privacy_safe():
    text = _component()
    assert 'htmlFor={readStartId}' in text
    assert 'htmlFor={readEndId}' in text
    assert 'htmlFor={readCalendarNameId}' in text
    assert 'htmlFor={draftTitleId}' in text
    assert 'htmlFor={draftCalendarNameId}' in text
    assert 'htmlFor={draftLocationId}' in text
    assert 'htmlFor={draftNotesId}' in text
    assert "Calendar name" in text
    assert 'activeField === "calendarName" ? validationMessageId' in text
    assert "<textarea" in text
    assert 'type="button"' in text
    assert "disabled={locked" in text or "disabled={locked}" in text
    assert "validationMessageId" in text
    assert 'activeField === "start" ? validationMessageId' in text
    assert 'activeField === "title" ? validationMessageId' in text
    assert "disabled={submitDisabled}" in text
    assert "disabled={pending}" in text
    assert 'role="alert"' in text
    assert "autoFocus" not in text
    assert "localStorage" not in text
    assert "sessionStorage" not in text
    assert "addMessage" not in text
    assert "onSubmit" in text
    assert "onModeChange" in text
    assert "maxLength" not in text


def test_unit_tests_cover_required_behaviors():
    text = _tests()
    for needle in (
        "validates and freezes bounded calendar read fields",
        "rejects naive datetimes missing explicit timezone offsets",
        "accepts Zulu and offset datetimes without inventing timezones",
        "rejects impossible calendar dates without Date.parse normalization",
        "rejects year 0000 and accepts Python datetime year bounds",
        "orders by microsecond precision and accepts 1–6 fractional digits",
        "compares offset-aware ordering without inventing local timezones",
        "rejects invalid ordering and excessive ranges",
        "rejects malformed dates bounds controls and Unicode Cf without rewriting",
        "uses Unicode code-point length for text bounds including emoji",
        "preserves surrounding title whitespace and rejects overlong untrimmed titles",
        "rejects U+0085-only titles as blank under Python strip parity",
        "validates draft fields with required calendar name and optional location notes",
        "rejects missing oversized and control-bearing draft calendar names",
        "rejects empty oversized and control-bearing draft text",
        "maps field-specific validation messages",
        "reducer blocks duplicate submit and clears pending explicitly",
        "rejects unknown fields and non-string values",
    ):
        assert needle in text


def test_unit_script_includes_calendar_suite():
    package = PACKAGE.read_text(encoding="utf-8")
    assert "calendarProposal.test.ts" in package
    assert "calendarProposal.test.js" in package
