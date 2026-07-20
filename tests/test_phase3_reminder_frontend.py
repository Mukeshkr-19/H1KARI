"""Source contracts for Phase 3 reminder frontend primitives."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HELPERS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "reminderProposal.ts"
)
HELPER_TESTS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "reminderProposal.test.ts"
)
COMPONENT = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "ReminderProposalForm.tsx"
)
PROTOCOL = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "productivityProtocol.ts"
)
PAGE = REPO_ROOT / "hikari-frontend" / "src" / "app" / "page.tsx"
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


def _page() -> str:
    assert PAGE.is_file()
    return PAGE.read_text(encoding="utf-8")


def test_reminder_files_exist():
    assert HELPERS.is_file()
    assert HELPER_TESTS.is_file()
    assert COMPONENT.is_file()
    assert "encodeProductivityReminderPrepare" in PROTOCOL.read_text(encoding="utf-8")


def test_helpers_define_bounds_and_validation():
    text = _helpers()
    assert "REMINDER_TITLE_MAX = 500" in text
    assert "REMINDER_NOTES_MAX = 4000" in text
    assert "REMINDER_LIST_NAME_MAX = 200" in text
    assert "REMINDER_MAX_HORIZON_SECONDS = 366 * 24 * 3600" in text
    assert "export function validateReminderFields(" in text
    assert "export function reminderCodePointLength(" in text
    assert "export function isBlankReminderTitle(" in text
    assert "hasReminderUnicodeFormatChars" in text
    assert "parseCalendarInstantMicros" in text
    assert "remind_at_before_now" in text
    assert "remind_at_horizon_too_long" in text
    assert "remind_at_missing_timezone" in text
    assert "reduceReminderProposalClientState" in text
    assert "MICROS_PER_MILLISECOND = BigInt(1_000)" in text
    assert "MICROS_PER_SECOND = BigInt(1_000_000)" in text
    assert "BigInt(Date.now()) * MICROS_PER_MILLISECOND" in text
    assert "MAX_HORIZON_MICROS = BigInt(REMINDER_MAX_HORIZON_SECONDS) * MICROS_PER_SECOND" in text
    assert "Date.parse(" not in text
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
        "provider",
        "reminder access",
    ):
        assert banned not in text


def test_component_is_labelled_and_privacy_safe():
    text = _component()
    assert 'htmlFor={titleId}' in text
    assert 'htmlFor={remindAtId}' in text
    assert 'htmlFor={notesId}' in text
    assert 'htmlFor={listNameId}' in text
    assert "<textarea" in text
    assert 'type="button"' in text
    assert "disabled={locked" in text or "disabled={locked}" in text
    assert "validationMessageId" in text
    assert 'activeField === "title" ? validationMessageId' in text
    assert 'activeField === "remindAt" ? validationMessageId' in text
    assert "disabled={submitDisabled}" in text
    assert "disabled={pending}" in text
    assert 'role="alert"' in text
    assert "Prepare reminder" in text
    assert "Nothing is scheduled from" in text
    assert "autoFocus" not in text
    assert "localStorage" not in text
    assert "sessionStorage" not in text
    assert "addMessage" not in text
    assert "maxLength" not in text
    assert "onChange" in text
    assert "onSubmit" in text
    assert "onReset" in text


def test_encoder_emits_exact_prepare_type():
    text = PROTOCOL.read_text(encoding="utf-8")
    assert 'type: "productivity_reminder_prepare"' in text
    assert "export function encodeProductivityReminderPrepare(" in text
    assert "validateReminderFields" in text
    assert "isValidEmailDraftRequestId" in text


def test_page_wires_prepare_pending_clear_and_privacy():
    text = _page()
    assert "<ReminderProposalForm" in text
    assert "encodeProductivityReminderPrepare(" in text
    assert "submitReminderPrepare" in text
    assert "reminderPendingRef" in text
    assert "reminderRequestIdRef" in text
    assert "clearReminderForm" in text
    assert "clearReminderForm()" in text
    assert "productivityPreparePending" in text
    assert "reminderPendingRef.current" in text
    submit_start = text.index("const submitReminderPrepare")
    submit_end = text.index("const resetReminderForm")
    submit_block = text[submit_start:submit_end]
    assert "addMessage(" not in submit_block
    assert "localStorage" not in submit_block
    assert "sessionStorage" not in submit_block
    assert "JSON.stringify(encoded)" in submit_block
    assert "request_id: requestId" in submit_block
    apply_start = text.index("const applyProductivityMessage")
    apply_end = text.index("const confirmProductivityAction")
    apply_block = text[apply_start:apply_end]
    assert "reminderPendingRef.current" in apply_block
    assert "reminderPrepareMatch" in apply_block
    assert "setReminderPrepareError(message.code)" in apply_block


def test_page_blocks_cross_form_prepare_while_reminder_pending():
    text = _page()
    email_submit_start = text.index("const submitEmailDraftPrepare")
    email_submit_end = text.index("const resetEmailDraftForm")
    email_submit = text[email_submit_start:email_submit_end]
    research_submit_start = text.index("const submitResearchPrepare")
    research_submit_end = text.index("const resetResearchForm")
    research_submit = text[research_submit_start:research_submit_end]
    assert "if (isProductivityPreparePending())" in email_submit
    assert "if (isProductivityPreparePending())" in research_submit


def test_unit_tests_cover_required_behaviors():
    text = _tests()
    for needle in (
        "validates and freezes bounded reminder fields",
        "omits empty optional notes and list name without rewriting text",
        "rejects whitespace-only titles under Python strip parity",
        "rejects oversized and control-bearing title without truncation",
        "uses Unicode code-point length for text bounds including emoji",
        "rejects naive datetimes missing explicit timezone offsets",
        "accepts Zulu and offset datetimes without inventing timezones",
        "rejects impossible dates and year 0000 without Date.parse normalization",
        "orders by microsecond precision and accepts 1–6 fractional digits",
        "requires remind-at strictly after injected now",
        "enforces a maximum 366-day horizon",
        "allows newline and tab only in notes and rejects oversized notes",
        "rejects oversized and control-bearing list names",
        "rejects unknown fields and non-string values",
        "maps field-specific validation messages",
        "detects Unicode format characters without rewriting",
        "reducer blocks duplicate submit and clears pending explicitly",
        "converts epoch milliseconds to microseconds exactly",
        "accepts reminders one second after the production clock and rejects equal or past times",
    ):
        assert needle in text


def test_unit_script_includes_reminder_suite():
    package = PACKAGE.read_text(encoding="utf-8")
    assert "reminderProposal.test.ts" in package
    assert "reminderProposal.test.js" in package
