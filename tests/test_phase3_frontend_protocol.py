"""Source contracts for the Phase 3 frontend productivity protocol parser."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "productivityProtocol.ts"
)
PROTOCOL_TEST = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "productivityProtocol.test.ts"
)
PACKAGE = REPO_ROOT / "hikari-frontend" / "package.json"

REQUIRED_ACTIONS = (
    "browser.research",
    "email.draft",
    "calendar.read",
    "calendar.draft",
    "reminder.create",
    "scheduled_job.manage",
    "skill.execute",
    "mcp.execute",
)


def _protocol() -> str:
    assert PROTOCOL.is_file()
    return PROTOCOL.read_text(encoding="utf-8")


def _tests() -> str:
    assert PROTOCOL_TEST.is_file()
    return PROTOCOL_TEST.read_text(encoding="utf-8")


def test_protocol_module_and_tests_exist():
    assert PROTOCOL.is_file()
    assert PROTOCOL_TEST.is_file()


def test_protocol_parses_required_server_message_types():
    text = _protocol()
    assert "export function parseProductivityServerMessage(" in text
    for message_type in (
        "productivity_confirmation_required",
        "productivity_update",
        "productivity_error",
        "productivity_research_result",
        "productivity_calendar_result",
    ):
        assert f'"{message_type}"' in text


def test_protocol_reuses_preview_lifecycle_and_scope_helpers():
    text = _protocol()
    assert 'from "./actionPreview"' in text
    assert 'from "./actionLifecycle"' in text
    assert 'from "./approvalScopes"' in text
    assert "boundPreviewLabel" in text
    assert "boundPreviewValue" in text
    assert "isValidProposalId" in text
    assert "parseAllowedApprovalScopes" in text
    assert "isApprovalScopeConfirmReady" in text
    assert "PREVIEW_ENTRY_MAX" in text


def test_protocol_actions_and_scope_constraints():
    text = _protocol()
    for action in REQUIRED_ACTIONS:
        assert f'"{action}"' in text
    assert len(REQUIRED_ACTIONS) == 8
    assert "allowed_scopes" in text
    assert "parseAllowedApprovalScopes" in text
    tests = _tests()
    assert "parses non-empty duplicate-free ordered allowed_scopes subsets" in tests
    assert "rejects empty allowed_scopes that were previously accepted" in tests
    assert "accepts empty allowed_scopes" not in tests


def test_protocol_preserves_exact_proposal_id_and_safe_errors():
    text = _protocol()
    assert "isValidProposalId" in text
    assert "isProductivityPreviewErrorCode" in text
    for code in (
        "confirm_failed",
        "cancel_failed",
        "proposal_expired",
        "proposal_invalid",
        "unavailable",
    ):
        assert code in _tests() or f'"{code}"' in text
    tests = _tests()
    assert "without trimming or truncation collisions" in tests
    assert "proposal:1" in tests
    assert "accepts only the five safe productivity error codes" in tests


def test_protocol_encodes_calendar_prepare_messages():
    text = _protocol()
    assert "export function encodeProductivityCalendarReadPrepare(" in text
    assert "export function encodeProductivityCalendarDraftPrepare(" in text
    assert "productivity_calendar_read_prepare" in text
    assert "productivity_calendar_draft_prepare" in text
    assert "validateCalendarReadFields" in text
    assert "validateCalendarDraftFields" in text
    tests = _tests()
    assert "encodeProductivityCalendarReadPrepare" in tests
    assert "encodeProductivityCalendarDraftPrepare" in tests


def test_protocol_encodes_research_prepare_messages():
    text = _protocol()
    assert "export function encodeProductivityResearchPrepare(" in text
    assert "productivity_research_prepare" in text
    assert "validateResearchFields" in text
    assert "isValidResearchRequestId" in text
    tests = _tests()
    assert "encodeProductivityResearchPrepare" in tests
    assert "encodes research prepare messages with exact protocol fields" in tests


def test_protocol_encodes_reminder_prepare_messages():
    text = _protocol()
    assert "export function encodeProductivityReminderPrepare(" in text
    assert "productivity_reminder_prepare" in text
    assert "validateReminderFields" in text
    assert "isValidEmailDraftRequestId" in text
    assert "remind_at" in text
    assert "list_name" in text
    tests = _tests()
    assert "encodeProductivityReminderPrepare" in tests
    assert "encodes reminder prepare messages with exact protocol fields" in tests


def test_protocol_encoders_emit_exact_scope_fields():
    text = _protocol()
    assert "export function encodeProductivityConfirm(" in text
    assert "scopeState: ApprovalScopeState" in text
    assert 'scope: "duration"' in text
    assert "duration_seconds" in text
    assert "acknowledged: true" in text
    assert "isValidProposalId(proposalId)" in text
    tests = _tests()
    assert "encodes exact confirm fields for every ready scope" in tests
    assert "duration_seconds" in tests
    assert "acknowledged: true" in tests
    assert "actor" in tests
    assert "session_id" in tests


def test_protocol_has_no_side_effects():
    text = _protocol()
    for banned in (
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "setInterval",
        "console.",
        "XMLHttpRequest",
        "navigator.",
        "window.",
        "document.",
    ):
        assert banned not in text


def test_protocol_rejects_unknown_fields_and_unsafe_errors():
    text = _protocol()
    assert "hasOnlyKeys" in text
    assert 'new Set(["type", "proposal_id", "code", "request_id"])' in text
    tests = _tests()
    assert "rejects unknown fields" in tests
    assert "never retains provider message detail" in tests
    assert "rejects non-finite expires_at" in tests
    assert "rejects malformed or oversized" in tests
    assert "rejects empty allowed_scopes that were previously accepted" in tests
    assert "parses optional request_id on confirmation and error" in tests


def test_unit_script_includes_protocol_and_existing_suites():
    package = PACKAGE.read_text(encoding="utf-8")
    assert "productivityProtocol.test.ts" in package
    assert "productivityProtocol.test.js" in package
    assert "approvalScopes.test.js" in package
    assert "actionLifecycle.test.js" in package
    assert "actionPreview.test.js" in package
    assert "voiceDocumentIntent.test.js" in package
    assert "speechOutput.test.js" in package
