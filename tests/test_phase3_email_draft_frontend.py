"""Source contracts for Phase 3 email-draft frontend primitives."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HELPERS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "emailDraftProposal.ts"
)
HELPER_TESTS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "emailDraftProposal.test.ts"
)
PROTOCOL = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "productivityProtocol.ts"
)
COMPONENT = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "EmailDraftProposal.tsx"
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


def test_email_draft_files_exist():
    assert HELPERS.is_file()
    assert HELPER_TESTS.is_file()
    assert COMPONENT.is_file()
    assert "encodeProductivityEmailDraftPrepare" in PROTOCOL.read_text(encoding="utf-8")


def test_helpers_define_bounds_and_validation():
    text = _helpers()
    assert "EMAIL_DRAFT_RECIPIENT_MAX = 320" in text
    assert "EMAIL_DRAFT_SUBJECT_MAX = 998" in text
    assert "EMAIL_DRAFT_BODY_MAX = 20000" in text
    assert "export function validateEmailDraftFields(" in text
    assert "export function hasEmailDraftUnicodeFormatChars(" in text
    assert "\\p{Cf}" in text
    assert "recipient_too_long" in text
    assert "subject_too_long" in text
    assert "body_too_long" in text
    assert "reduceEmailDraftClientState" in text
    assert "emailDraftResponseMatchesRequest" in text


def test_helpers_exclude_side_effects_and_sensitive_sinks():
    text = _helpers()
    for banned in (
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "console.",
        "actorId",
        "sessionId",
        "provider",
    ):
        assert banned not in text
    assert "stripEmailDraftBidiControls" not in text


def test_encoder_emits_exact_prepare_type():
    text = PROTOCOL.read_text(encoding="utf-8")
    assert 'type: "productivity_email_draft_prepare"' in text
    assert "export function encodeProductivityEmailDraftPrepare(" in text
    assert "validateEmailDraftFields" in text
    assert "request_id" in text
    assert "isValidEmailDraftRequestId" in text


def test_component_is_labelled_and_privacy_safe():
    text = _component()
    assert 'htmlFor={recipientId}' in text
    assert 'htmlFor={subjectId}' in text
    assert 'htmlFor={bodyId}' in text
    assert "<textarea" in text
    assert 'type="button"' in text
    assert "disabled={locked" in text or "disabled={locked}" in text
    assert 'role="alert"' in text
    assert "validationMessageId" in text
    assert "aria-describedby={\n              validationField === \"recipient\" ? validationMessageId" in text or (
        'validationField === "recipient" ? validationMessageId' in text
    )
    assert "autoFocus" not in text
    assert "localStorage" not in text
    assert "sessionStorage" not in text
    assert "addMessage" not in text


def test_page_wires_prepare_pending_clear_and_privacy():
    text = _page()
    assert "<EmailDraftProposal" in text
    assert "encodeProductivityEmailDraftPrepare(" in text
    assert "submitEmailDraftPrepare" in text
    assert "emailDraftPendingRef" in text
    assert "emailDraftRequestIdRef" in text
    assert "emailDraftResponseMatchesRequest" in text
    assert "createEmailDraftRequestId" in text
    assert "clearEmailDraftForm" in text
    assert "clearEmailDraftForm()" in text
    assert "clearEmailDraftPendingWithLocalError" not in text
    submit_start = text.index("const submitEmailDraftPrepare")
    submit_end = text.index("const resetEmailDraftForm")
    submit_block = text[submit_start:submit_end]
    assert "addMessage(" not in submit_block
    assert "localStorage" not in submit_block
    assert "sessionStorage" not in submit_block
    assert "JSON.stringify(encoded)" in submit_block
    assert "request_id: requestId" in submit_block
    apply_start = text.index("const applyProductivityMessage")
    apply_end = text.index("const confirmProductivityAction")
    apply_block = text[apply_start:apply_end]
    assert "emailDraftPendingRef.current" in apply_block
    assert "emailDraftResponseMatchesRequest" in apply_block
    assert "setEmailDraftPrepareError(message.code)" in apply_block


def test_page_blocks_cross_form_prepare_while_email_pending():
    text = _page()
    assert "isProductivityPreparePending" in text
    assert "productivityPreparePending" in text
    calendar_submit_start = text.index("const submitCalendarPrepare")
    calendar_submit_end = text.index("const resetCalendarForm")
    calendar_submit = text[calendar_submit_start:calendar_submit_end]
    research_submit_start = text.index("const submitResearchPrepare")
    research_submit_end = text.index("const resetResearchForm")
    research_submit = text[research_submit_start:research_submit_end]
    assert "if (isProductivityPreparePending())" in calendar_submit
    assert "if (isProductivityPreparePending())" in research_submit
    assert "pending={productivityPreparePending}" in text


def test_page_drops_malformed_dedicated_messages_before_legacy_parser():
    text = _page()
    assert "STRICT_DEDICATED_SERVER_MESSAGE_TYPES" in text
    assert "isStrictDedicatedServerMessageType(" in text
    assert "parseWebSocketFrameType(" in text
    assert "productivity_error" in text
    assert "scheduled_job_error" in text
    onmessage_start = text.index("ws.onmessage = (event) => {")
    onmessage_end = text.index("ws.onclose = () => {", onmessage_start)
    onmessage_block = text[onmessage_start:onmessage_end]
    dedicated_idx = onmessage_block.index("isStrictDedicatedServerMessageType(frameType)")
    legacy_idx = onmessage_block.index("parseServerMessage(event.data)", dedicated_idx)
    assert dedicated_idx < legacy_idx
    assert "Array.isArray(spec)" in text
    assert "required.every(" in text


def test_page_legacy_errors_do_not_clear_email_pending():
    text = _page()
    onmessage_start = text.index("ws.onmessage = (event) => {")
    onmessage_end = text.index("ws.onclose = () => {", onmessage_start)
    onmessage_block = text[onmessage_start:onmessage_end]
    protocol_start = onmessage_block.index('data.type === "protocol_error"')
    error_start = onmessage_block.index('data.type === "error"', protocol_start)
    protocol_block = onmessage_block[protocol_start:error_start]
    error_block = onmessage_block[error_start:]
    assert "emailDraftPendingRef" not in protocol_block
    assert "clearEmailDraftForm" not in protocol_block
    assert 'setInterfaceError("Unsupported server protocol")' in protocol_block
    assert "stringField(data" not in protocol_block
    assert "emailDraftPendingRef" not in error_block
    assert "clearEmailDraftForm" not in error_block
    assert 'setInterfaceError("Server request failed")' in error_block
    assert 'boundedString(data, "message"' not in error_block
    close_start = text.index("ws.onclose = () => {")
    close_end = text.index("}, [serverUrl, pairingCode", close_start)
    assert "clearProductivityLifecycle()" in text[close_start:close_end]


def test_unit_tests_cover_required_behaviors():
    text = _tests()
    for needle in (
        "validates and freezes bounded draft fields",
        "rejects empty oversized and control-bearing recipient without truncation",
        "rejects oversized subject and body with explicit validation codes",
        "allows newline and tab only in body and rejects Unicode Cf without rewriting",
        "rejects unknown fields and non-string values",
        "maps only safe validation messages",
        "encodes exact prepare messages and rejects malformed input",
        "reducer clears pending on protocol rejection while preserving draft fields",
        "reducer ignores stale and mismatched response ids",
        "reducer blocks duplicate submit and accepts matching success and error",
        "maps specific accessible validation descriptions per field code",
    ):
        assert needle in text


def test_unit_script_includes_email_draft_suite():
    package = PACKAGE.read_text(encoding="utf-8")
    assert "emailDraftProposal.test.ts" in package
    assert "emailDraftProposal.test.js" in package
    assert "approvalScopes.test.js" in package
    assert "productivityProtocol.test.js" in package
