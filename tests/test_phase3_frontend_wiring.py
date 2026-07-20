"""Source contracts for Phase 3 productivity frontend wiring."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "hikari-frontend" / "src" / "app" / "page.tsx"
PREVIEW = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "ProductivityActionPreview.tsx"
)
SELECTOR = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "ApprovalScopeSelector.tsx"
)


def _page() -> str:
    assert PAGE.is_file()
    return PAGE.read_text(encoding="utf-8")


def _preview() -> str:
    assert PREVIEW.is_file()
    return PREVIEW.read_text(encoding="utf-8")


def test_page_parses_productivity_server_messages_before_schema_fallback():
    text = _page()
    assert "parseProductivityServerMessage(event.data)" in text
    parse_idx = text.index("parseProductivityServerMessage(event.data)")
    schema_idx = text.index("parseServerMessage(event.data)", parse_idx)
    assert parse_idx < schema_idx
    assert "applyProductivityMessage(productivityMessage)" in text
    assert "isStrictDedicatedServerMessageType(frameType)" in text
    onmessage_start = text.index("ws.onmessage = (event) => {")
    onmessage_end = text.index("ws.onclose = () => {", onmessage_start)
    onmessage_block = text[onmessage_start:onmessage_end]
    dedicated_idx = onmessage_block.index("isStrictDedicatedServerMessageType(frameType)")
    legacy_idx = onmessage_block.index("parseServerMessage(event.data)", dedicated_idx)
    assert dedicated_idx < legacy_idx


def test_page_legacy_parser_rejects_object_schema_specs():
    text = _page()
    assert "Array.isArray(spec)" in text
    assert "required.every(" in text


def test_page_wires_confirmation_to_frozen_preview_lifecycle():
    text = _page()
    assert 'type: "preview"' in text
    assert "reduceProposalLifecycle(" in text
    assert "createInitialProposalLifecycleState" in text
    assert "proposalId: message.proposal_id" in text
    assert "actionLabel: message.action" in text
    assert "riskLabel: message.risk_label" in text
    assert "<ProductivityActionPreview" in text
    assert "headingRef={productivityHeadingRef}" in text
    assert "createApprovalScopeStateFromAllowed(message.allowed_scopes)" in text
    assert "<ApprovalScopeSelector" in text


def test_page_confirm_and_cancel_send_encoded_scope_payload():
    text = _page()
    assert "encodeProductivityConfirm(state.proposalId, scopeState)" in text
    assert "encodeProductivityCancel(state.proposalId)" in text
    assert "isApprovalScopeConfirmReady(scopeState)" in text
    assert 'type: "confirm"' in text
    assert 'type: "cancel"' in text
    assert "JSON.stringify(encoded)" in text
    assert "confirmProductivityAction" in text
    assert "cancelProductivityAction" in text


def test_page_updates_and_errors_correlate_and_reset_scopes():
    text = _page()
    assert "applyProductivityUpdateStatus(" in text
    assert "message.proposal_id" in text
    assert 'type: "fail"' in text
    assert "error: message.code" in text
    assert "TERMINAL_PRODUCTIVITY_STATUSES.has(next.status)" in text
    assert "resetApprovalScopeState()" in text
    assert "clearProductivityLifecycle" in text
    assert 'message.type === "productivity_research_result"' in text
    assert 'message.type === "productivity_calendar_result"' in text
    assert "setProductivityResearchResult(message)" in text
    assert "setProductivityCalendarResult(message)" in text
    assert "state.proposalId !== message.proposal_id" in text
    assert "setProductivityResearchResult(null)" in text
    assert "setProductivityCalendarResult(null)" in text
    assert 'id="productivity-research-result-heading"' in text
    assert 'id="productivity-calendar-result-heading"' in text
    assert 'aria-live="polite"' in text
    close_start = text.index("ws.onclose = () => {")
    close_end = text.index("}, [serverUrl, pairingCode", close_start)
    assert "clearProductivityLifecycle()" in text[close_start:close_end]


def test_page_does_not_persist_or_chat_productivity_or_scope_selection():
    text = _page()
    assert "addMessage(" in text
    apply_start = text.index("const applyProductivityMessage")
    apply_end = text.index("const confirmProductivityAction")
    apply_block = text[apply_start:apply_end]
    assert "addMessage(" not in apply_block
    assert "localStorage" not in apply_block
    assert "sessionStorage" not in apply_block
    confirm_start = text.index("const confirmProductivityAction")
    confirm_end = text.index("const cancelProductivityAction")
    confirm_block = text[confirm_start:confirm_end]
    assert "addMessage(" not in confirm_block
    assert "localStorage" not in confirm_block
    assert "sessionStorage" not in confirm_block


def test_page_disables_confirm_until_scope_ready_and_pending():
    text = _page()
    assert "isApprovalScopeConfirmReady(approvalScopeState)" in text
    assert 'productivityLifecycle.status !== "preview"' in text
    assert "disabled={productivityPending}" in text
    assert "confirmDisabled={productivityConfirmDisabled}" in text
    assert "cancelDisabled={productivityCancelDisabled}" in text


def test_page_wires_calendar_prepare_without_leaking_content():
    text = _page()
    assert "<CalendarProposalForm" in text
    assert "encodeProductivityCalendarReadPrepare(" in text
    assert "encodeProductivityCalendarDraftPrepare(" in text
    assert "submitCalendarPrepare" in text
    assert "clearCalendarForm" in text
    assert "calendarPendingRef" in text
    assert "calendarRequestIdRef" in text
    assert "emailDraftResponseMatchesRequest" in text
    submit_start = text.index("const submitCalendarPrepare")
    submit_end = text.index("const resetCalendarForm")
    submit_block = text[submit_start:submit_end]
    assert "addMessage(" not in submit_block
    assert "localStorage" not in submit_block
    assert "sessionStorage" not in submit_block
    assert "JSON.stringify(encoded)" in submit_block
    assert "request_id: requestId" in submit_block


def test_page_wires_research_prepare_without_leaking_content():
    text = _page()
    assert "<ResearchProposalForm" in text
    assert "encodeProductivityResearchPrepare(" in text
    assert "submitResearchPrepare" in text
    assert "clearResearchForm" in text
    assert "researchPendingRef" in text
    assert "researchRequestIdRef" in text
    assert "researchResponseMatchesRequest" in text
    submit_start = text.index("const submitResearchPrepare")
    submit_end = text.index("const resetResearchForm")
    submit_block = text[submit_start:submit_end]
    assert "addMessage(" not in submit_block
    assert "localStorage" not in submit_block
    assert "sessionStorage" not in submit_block
    assert "JSON.stringify(encoded)" in submit_block
    assert "request_id: requestId" in submit_block


def test_page_wires_reminder_prepare_without_leaking_content():
    text = _page()
    assert "<ReminderProposalForm" in text
    assert "encodeProductivityReminderPrepare(" in text
    assert "submitReminderPrepare" in text
    assert "clearReminderForm" in text
    assert "reminderPendingRef" in text
    assert "reminderRequestIdRef" in text
    assert "emailDraftResponseMatchesRequest" in text
    submit_start = text.index("const submitReminderPrepare")
    submit_end = text.index("const resetReminderForm")
    submit_block = text[submit_start:submit_end]
    assert "addMessage(" not in submit_block
    assert "localStorage" not in submit_block
    assert "sessionStorage" not in submit_block
    assert "JSON.stringify(encoded)" in submit_block
    assert "request_id: requestId" in submit_block


def test_page_wires_email_draft_prepare_without_leaking_content():
    text = _page()
    assert "<EmailDraftProposal" in text
    assert "encodeProductivityEmailDraftPrepare(" in text
    assert "submitEmailDraftPrepare" in text
    assert "clearEmailDraftForm" in text
    assert "emailDraftPendingRef" in text
    assert "emailDraftRequestIdRef" in text
    assert "emailDraftResponseMatchesRequest" in text
    assert "clearEmailDraftPendingWithLocalError" not in text
    submit_start = text.index("const submitEmailDraftPrepare")
    submit_end = text.index("const resetEmailDraftForm")
    submit_block = text[submit_start:submit_end]
    assert "addMessage(" not in submit_block
    assert "localStorage" not in submit_block
    assert "sessionStorage" not in submit_block
    assert "JSON.stringify(encoded)" in submit_block
    assert "request_id: requestId" in submit_block
    close_start = text.index("ws.onclose = () => {")
    close_end = text.index("}, [serverUrl, pairingCode", close_start)
    assert "clearProductivityLifecycle()" in text[close_start:close_end]


def test_preview_keeps_accessible_pending_and_alert_semantics():
    text = _preview()
    assert 'role="status"' in text
    assert 'aria-live="polite"' in text
    assert 'role="alert"' in text
    assert "disabled={confirmDisabled}" in text
    assert "disabled={cancelDisabled}" in text
    assert 'type="button"' in text
    assert "liveStatus" in text
    selector = SELECTOR.read_text(encoding="utf-8")
    assert 'type="radio"' in selector
    assert 'aria-describedby={ackWarningId}' in selector


def test_page_enforces_single_productivity_prepare_flight():
    text = _page()
    assert "const isProductivityPreparePending = useCallback(" in text
    assert "emailDraftPendingRef.current ||" in text
    assert "calendarPendingRef.current ||" in text
    assert "researchPendingRef.current" in text
    assert "reminderPendingRef.current" in text
    assert "const productivityPreparePending =" in text
    assert (
        "emailDraftPending || calendarPending || researchPending || reminderPending"
        in text
    )
    assert "pending={productivityPreparePending}" in text
    assert "disabled={\n                productivityPreparePending ||" in text

    email_submit_start = text.index("const submitEmailDraftPrepare")
    email_submit_end = text.index("const resetEmailDraftForm")
    email_submit = text[email_submit_start:email_submit_end]
    calendar_submit_start = text.index("const submitCalendarPrepare")
    calendar_submit_end = text.index("const resetCalendarForm")
    calendar_submit = text[calendar_submit_start:calendar_submit_end]
    research_submit_start = text.index("const submitResearchPrepare")
    research_submit_end = text.index("const resetResearchForm")
    research_submit = text[research_submit_start:research_submit_end]
    reminder_submit_start = text.index("const submitReminderPrepare")
    reminder_submit_end = text.index("const resetReminderForm")
    reminder_submit = text[reminder_submit_start:reminder_submit_end]

    for submit_block in (email_submit, calendar_submit, research_submit, reminder_submit):
        pending_guard = submit_block.index("if (isProductivityPreparePending())")
        validate_idx = submit_block.index("validate", pending_guard)
        request_idx = submit_block.index("requestId =", pending_guard)
        send_idx = submit_block.index("ws.send(JSON.stringify(encoded))", pending_guard)
        assert pending_guard < validate_idx < request_idx < send_idx

    assert "if (isProductivityPreparePending()) {\n      return;\n    }\n    const validated = validateEmailDraftFields" in email_submit
    assert "if (isProductivityPreparePending()) {\n      return;\n    }\n    const ws = wsRef.current;" in calendar_submit
    assert "if (isProductivityPreparePending()) {\n      return;\n    }\n    const validated = validateResearchFields" in research_submit
    assert "if (isProductivityPreparePending()) {\n      return;\n    }\n    const validated = validateReminderFields" in reminder_submit


def test_page_correlates_prepare_responses_without_cross_form_priority_drops():
    text = _page()
    apply_start = text.index("const applyProductivityMessage")
    apply_end = text.index("const confirmProductivityAction")
    apply_block = text[apply_start:apply_end]
    assert "const emailPrepareMatch =" in apply_block
    assert "const calendarPrepareMatch =" in apply_block
    assert "const researchPrepareMatch =" in apply_block
    assert "const reminderPrepareMatch =" in apply_block
    assert "} else if (calendarPrepareMatch) {" in apply_block
    assert "} else if (researchPrepareMatch) {" in apply_block
    assert "} else if (reminderPrepareMatch) {" in apply_block
    assert "setEmailDraftPrepareError(message.code)" in apply_block
    assert "setCalendarPrepareError(message.code)" in apply_block
    assert "setResearchPrepareError(message.code)" in apply_block
    assert "setReminderPrepareError(message.code)" in apply_block
    assert "} else if (calendarPendingRef.current) {" not in apply_block
    assert "} else if (researchPendingRef.current) {" not in apply_block
    assert "} else if (reminderPendingRef.current) {" not in apply_block
