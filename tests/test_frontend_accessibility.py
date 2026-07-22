"""Stable accessibility semantics for the representative HIKARI client flow."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "hikari-frontend" / "src" / "app" / "page.tsx"
SETTINGS = REPO_ROOT / "hikari-frontend" / "src" / "components" / "CompanionSettings.tsx"
OVERLAY = REPO_ROOT / "hikari-frontend" / "src" / "components" / "VoiceCompanionOverlay.tsx"
CSS = REPO_ROOT / "hikari-frontend" / "src" / "app" / "globals.css"
CHECKLIST = REPO_ROOT / "docs" / "ACCESSIBILITY_CHECKLIST.md"


def test_pairing_inputs_have_programmatic_labels_and_landmark():
    text = PAGE.read_text(encoding="utf-8")

    assert 'aria-labelledby="pairing-title"' in text
    assert 'id="pairing-title"' in text
    assert 'htmlFor="server-url"' in text and 'id="server-url"' in text
    assert 'htmlFor="pairing-code"' in text and 'id="pairing-code"' in text
    assert 'autoComplete="one-time-code"' in text


def test_icon_buttons_have_names_and_decorative_icons_are_hidden():
    text = PAGE.read_text(encoding="utf-8")

    assert 'microphoneCapturing ? "Stop listening" : "Start voice input"' in text
    assert 'aria-label="Send message"' in text
    assert text.count('aria-hidden="true" focusable="false"') >= 3


def test_microphone_stop_control_and_hit_target_are_accessible():
    text = PAGE.read_text(encoding="utf-8")

    assert "onClick={handleMicrophoneClick}" in text
    assert "const handleMicrophoneClick = () => {" in text
    handle_start = text.index("const handleMicrophoneClick = () => {")
    handle_end = text.index("const getOrbGradient = () => {", handle_start)
    handle_block = text[handle_start:handle_end]
    assert "cancelVoiceCapture()" in handle_block
    assert "min-h-11 min-w-11" in text
    assert "disabled={microphoneDisabled}" in text
    assert "aria-disabled={microphoneDisabled}" in text
    assert "const microphoneCapturing = isListening || recognitionCaptureActive" in text
    disabled_start = text.index("const microphoneDisabled =")
    disabled_end = text.index("const prepareDocument =", disabled_start)
    disabled_block = text[disabled_start:disabled_end]
    assert "voiceTurnActive" in disabled_block
    assert "microphoneCapturing" in disabled_block
    assert "|| isListening" not in disabled_block


def test_conversation_connection_and_voice_updates_are_announced():
    page = PAGE.read_text(encoding="utf-8")
    overlay = OVERLAY.read_text(encoding="utf-8")

    assert 'role="log"' in page
    assert 'aria-label="Conversation"' in page
    assert 'aria-relevant="additions text"' in page
    assert 'role="status" aria-live="polite"' in page
    assert 'aria-label="HIKARI is typing"' in page
    assert 'aria-live="polite"' in overlay
    assert 'aria-atomic="true"' in overlay


def test_navigation_and_companion_choices_match_keyboard_behavior():
    page = PAGE.read_text(encoding="utf-8")
    settings = SETTINGS.read_text(encoding="utf-8")

    assert '<nav aria-label="Primary"' in page
    assert 'aria-current={activeTab === tab.id ? "page" : undefined}' in page
    assert settings.count('role="group"') == 2
    assert settings.count("aria-pressed=") == 2
    assert 'role="radio"' not in settings


def test_focus_and_reduced_motion_contracts_are_global():
    css = CSS.read_text(encoding="utf-8")

    assert ":focus-visible" in css
    assert "outline: 3px solid" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "animation-iteration-count: 1 !important" in css
    assert "transition-duration: 0.01ms !important" in css


def test_manual_checklist_covers_representative_flow():
    checklist = CHECKLIST.read_text(encoding="utf-8").lower()

    for requirement in (
        "keyboard",
        "voiceover",
        "pairing",
        "message",
        "microphone",
        "200%",
        "reduced motion",
        "disconnect",
    ):
        assert requirement in checklist


def test_document_flow_uses_labeled_path_and_provider_controls():
    text = PAGE.read_text(encoding="utf-8")

    assert 'htmlFor="document-path"' in text
    assert 'id="document-path"' in text
    assert "Path on the HIKARI computer" in text
    assert 'htmlFor="document-provider"' in text
    assert 'htmlFor="document-fallback-provider"' in text
    assert 'type="file"' not in text


def test_document_confirmation_progress_errors_and_follow_up_are_accessible():
    text = PAGE.read_text(encoding="utf-8")

    assert 'id="document-confirmation-heading"' in text
    assert "ref={confirmationHeadingRef}" in text
    assert "ref={documentErrorHeadingRef}" in text
    assert 'role="alert"' in text
    assert '<progress' in text and 'htmlFor="document-progress"' in text
    assert 'role="status" aria-live="polite" aria-atomic="true"' in text
    assert 'htmlFor="document-follow-up"' in text
    assert "documentTaskId," in text


def test_document_flow_uses_protocol_and_only_persists_root_task_id():
    text = PAGE.read_text(encoding="utf-8")

    for message_type in (
        "document_prepare",
        "document_confirm",
        "document_follow_up",
        "document_cancel",
    ):
        assert f'sendDocumentMessage("{message_type}"' in text
    assert 'encodeClientMessage("task_status"' in text

    assert 'window.localStorage.setItem(ROOT_DOCUMENT_TASK_KEY, taskId)' in text
    assert text.count("window.localStorage.setItem(") == 1
    assert "documentFollowUpPendingRef" not in text
    assert "task_id: taskId" in text
    assert "task_id: documentConfirmation.taskId" in text
    assert 'rootTaskId === documentTaskIdRef.current' in text
    assert 'code === "task_not_found" || code === "actor_not_authorized"' in text
    assert "alert(" not in text


def test_document_confirmation_freezes_server_returned_request():
    text = PAGE.read_text(encoding="utf-8")

    assert "type DocumentConfirmation" in text
    assert "documentPreparePendingRef.current" in text
    assert "documentTaskIdsSeenRef.current.has(taskId)" in text
    assert "request.path !== path || request.provider !== provider" in text
    assert "disabled={documentRequestLocked}" in text
    assert "task_id: documentConfirmation.taskId" in text
    assert "provider: documentConfirmation.provider" in text


def test_document_prepare_arms_reply_guard_before_websocket_send():
    text = PAGE.read_text(encoding="utf-8")

    arm = text.index("documentPreparePendingRef.current = true;")
    send = text.index('sendDocumentMessage("document_prepare", fields)')
    assert arm < send


def test_document_cancel_remains_available_for_nonterminal_tasks():
    text = PAGE.read_text(encoding="utf-8")

    assert "TERMINAL_DOCUMENT_STATUSES" in text
    assert "const canCancelDocument = Boolean(documentTaskId)" in text
    assert "{canCancelDocument && (" in text
    assert "Cancel document task" in text


def test_document_events_have_bounded_typed_guards_and_root_correlation():
    text = PAGE.read_text(encoding="utf-8")

    assert "function boundedString(" in text
    assert "function documentStatusField(" in text
    assert "Number.isInteger(value) && value >= 0 && value <= 100" in text
    assert text.count('boundedString(data, "root_task_id", DOCUMENT_TASK_ID_MAX)') == 3
    assert text.count("rootTaskId === documentTaskIdRef.current") == 3


def test_document_prepare_failure_paths_clear_lock_and_reject_unsolicited():
    text = PAGE.read_text(encoding="utf-8")

    assert "failDocumentPrepare(" in text
    fail_start = text.index("const failDocumentPrepare = useCallback")
    fail_end = text.index("const scrollToBottom = useCallback")
    fail_block = text[fail_start:fail_end]
    assert "setDocumentAwaitingConfirmation(false)" in fail_block
    assert "setDocumentConfirmation(null)" in fail_block
    assert 'setDocumentStatusCode("failed")' in fail_block
    assert "setDocumentProgress(0)" in fail_block
    assert "forgetDocumentTask" not in fail_block
    confirm_start = text.index('} else if (data.type === "document_confirmation_required")')
    confirm_end = text.index('} else if (data.type === "task_update")', confirm_start)
    confirm_block = text[confirm_start:confirm_end]
    assert "if (!documentPreparePendingRef.current) return;" in confirm_block
    assert "failDocumentPrepare(" in confirm_block
    error_start = text.index('} else if (data.type === "document_error")')
    error_end = text.index('} else if (data.type === "companion_preferences_error")', error_start)
    error_block = text[error_start:error_end]
    assert "documentPreparePendingRef.current" in error_block
    assert "failDocumentPrepare(message)" in error_block
    pending_branch = error_block.split("if (documentPreparePendingRef.current)")[1].split(
        "if (rootTaskId === documentTaskIdRef.current)"
    )[0]
    assert "forgetDocumentTask()" not in pending_branch
    close_start = text.index("ws.onclose = () => {")
    close_block = text[close_start : close_start + 500]
    assert "failDocumentPrepare(" in close_block


def test_speech_recognition_aggregates_results_before_final_submit():
    text = PAGE.read_text(encoding="utf-8")

    assert "function aggregateSpeechRecognitionTranscript(" in text
    assert "resultIndex: number" in text
    voice_start = text.index("recognition.onresult")
    voice_end = text.index("recognition.onerror", voice_start)
    onresult = text[voice_start:voice_end]
    assert "aggregateSpeechRecognitionTranscript(event)" in onresult
    assert "captureSubmitted" in onresult
    assert "if (!complete)" in onresult
    assert "event.results[event.results.length - 1]" not in onresult


def test_speech_recognition_errors_announce_keyboard_fallback():
    text = PAGE.read_text(encoding="utf-8")

    assert "function speechRecognitionErrorMessage(errorCode: string)" in text
    assert "Type your message instead." in text
    assert "inputRef.current?.focus()" in text
    assert 'id="chat-input"' in text
    assert 'htmlFor="chat-input"' in text


def test_companion_preferences_error_is_surfaced_accessibly():
    text = PAGE.read_text(encoding="utf-8")

    assert 'data.type === "companion_preferences_error"' in text
    prefs_start = text.index('} else if (data.type === "companion_preferences_error")')
    prefs_block = text[prefs_start : prefs_start + 350]
    assert "setInterfaceError(" in prefs_block
    assert 'role="alert"' in text


def test_overlay_bounds_caption_display_for_interim_text():
    overlay = OVERLAY.read_text(encoding="utf-8")

    assert "CAPTION_DISPLAY_MAX = 500" in overlay
    assert "function displayCaptionText(text: string)" in overlay
    assert "displayCaptionText(caption.text)" in overlay
    assert "!caption.is_final" in overlay or "caption && !caption.is_final" in overlay
    assert "Live captions appear here during voice" in overlay
    assert "Stop speaking" in overlay


def test_companion_settings_speak_responses_is_opt_in_and_labelled():
    settings = SETTINGS.read_text(encoding="utf-8")

    assert 'id="speak-responses-label"' in settings
    assert 'role="switch"' in settings
    assert "aria-checked={speakResponses}" in settings
    assert "Speak responses" in settings
    assert "Off by default" in settings
    assert "Stop speaking" in settings
    assert "browser or vendor" in settings.lower() or "Browser or vendor" in settings


def test_voice_document_commands_keep_keyboard_fallback_and_frozen_confirmation():
    text = PAGE.read_text(encoding="utf-8")

    assert 'from "@/utils/companion/voiceDocumentIntent"' in text
    assert "parseVoiceDocumentIntent" in text
    assert "boundVoiceTranscript" in text
    assert 'intent.type === "reject"' in text
    assert "setDocumentError(intent.message)" in text
    assert "documentErrorHeadingRef" in text
    assert 'id="document-confirmation-heading"' in text
    assert "documentConfirmation.path" in text
    assert "documentConfirmation.provider" in text
    assert "prepareDocumentRequest(" in text
    assert "confirmDocumentRequest()" in text
    assert "failDocumentPrepare(" in text
    assert "parseSpeechControlIntent" in text
    assert "SpeechOutputController" in text
    submit_start = text.index("const submitVoiceRequest = useCallback")
    submit_end = text.index("const syncCompanionPrefs = useCallback")
    submit_block = text[submit_start:submit_end]
    assert "inputRef.current?.focus()" not in submit_block
    assert 'addMessage(trimmed, "user")' in submit_block
    assert submit_block.rindex('addMessage(trimmed, "user")') > submit_block.rindex(
        "const ws = wsRef.current;"
    )


def test_typed_document_actions_clear_voice_speech_origin():
    text = PAGE.read_text(encoding="utf-8")
    prepare_start = text.index("const prepareDocument = () => {")
    prepare_end = text.index("const confirmDocument = () => {", prepare_start)
    follow_start = text.index("const sendDocumentFollowUp = () => {")
    follow_end = text.index("const documentRequestLocked", follow_start)

    assert "documentTaskVoiceOriginRef.current = false" in text[prepare_start:prepare_end]
    assert "documentTaskVoiceOriginRef.current = false" in text[follow_start:follow_end]


def test_phase3_productivity_preview_focus_and_live_regions():
    page = PAGE.read_text(encoding="utf-8")
    preview = (
        Path(__file__).resolve().parent.parent
        / "hikari-frontend"
        / "src"
        / "components"
        / "ProductivityActionPreview.tsx"
    ).read_text(encoding="utf-8")

    assert "productivityHeadingRef" in page
    assert "headingRef={productivityHeadingRef}" in page
    assert "productivityLiveStatus(" in page
    assert "liveStatus={productivityLiveStatus" in page
    assert 'role="status"' in preview
    assert 'aria-live="polite"' in preview
    assert 'role="alert"' in preview
    assert "disabled={confirmDisabled}" in preview
    assert "disabled={cancelDisabled}" in preview
    assert 'tabIndex={-1}' in preview
    assert "mapPreviewErrorMessage" in preview


def test_phase3_approval_scope_selector_labelled_and_non_focus_stealing():
    page = PAGE.read_text(encoding="utf-8")
    selector = (
        Path(__file__).resolve().parent.parent
        / "hikari-frontend"
        / "src"
        / "components"
        / "ApprovalScopeSelector.tsx"
    ).read_text(encoding="utf-8")

    assert "<ApprovalScopeSelector" in page
    assert "disabled={productivityPending}" in page
    assert "isApprovalScopeConfirmReady(approvalScopeState)" in page
    assert "createApprovalScopeStateFromAllowed(message.allowed_scopes)" in page
    assert "resetApprovalScopeState()" in page
    assert 'type="radio"' in selector
    assert 'type="checkbox"' in selector
    assert 'role="radiogroup"' in selector
    assert "aria-describedby={descriptionId}" in selector
    assert "aria-describedby={ackWarningId}" in selector
    assert "htmlFor={ackId}" in selector
    assert "APPROVAL_SCOPE_BINDING_DESCRIPTION" in selector
    assert "autoFocus" not in selector
    assert ".focus(" not in selector
    assert 'role="status"' in selector
    assert 'aria-live="polite"' in selector


def test_phase3_calendar_form_labels_errors_and_privacy():
    page = PAGE.read_text(encoding="utf-8")
    form = (
        Path(__file__).resolve().parent.parent
        / "hikari-frontend"
        / "src"
        / "components"
        / "CalendarProposalForm.tsx"
    ).read_text(encoding="utf-8")

    assert "<CalendarProposalForm" in page
    assert "submitCalendarPrepare" in page
    assert "clearCalendarForm" in page
    assert "calendarPending" in page
    assert "productivityPreparePending" in page
    assert "pending={productivityPreparePending}" in page
    assert 'htmlFor={readStartId}' in form
    assert 'htmlFor={draftTitleId}' in form
    assert 'htmlFor={draftCalendarNameId}' in form
    assert 'activeField === "calendarName" ? validationMessageId' in form
    assert "<textarea" in form
    assert 'role="alert"' in form
    assert "aria-invalid=" in form
    assert "validationMessageId" in form
    assert "tabIndex={-1}" in form
    assert "autoFocus" not in form
    assert "localStorage" not in form
    assert "sessionStorage" not in form
    assert "aria-live" not in form
    assert "calendarRequestIdRef" in page
    assert "mapPreviewErrorMessage(calendarPrepareError)" in page
    onmessage_start = page.index("ws.onmessage = (event) => {")
    onmessage_end = page.index("ws.onclose = () => {", onmessage_start)
    onmessage_block = page[onmessage_start:onmessage_end]
    protocol_start = onmessage_block.index('data.type === "protocol_error"')
    error_start = onmessage_block.index('data.type === "error"', protocol_start)
    protocol_block = onmessage_block[protocol_start:error_start]
    error_block = onmessage_block[error_start:]
    assert "calendarPendingRef" not in protocol_block
    assert "calendarPendingRef" not in error_block


def test_phase3_research_form_labels_errors_and_privacy():
    page = PAGE.read_text(encoding="utf-8")
    form = (
        Path(__file__).resolve().parent.parent
        / "hikari-frontend"
        / "src"
        / "components"
        / "ResearchProposalForm.tsx"
    ).read_text(encoding="utf-8")

    assert "<ResearchProposalForm" in page
    assert "submitResearchPrepare" in page
    assert "clearResearchForm" in page
    assert "researchPending" in page
    assert "productivityPreparePending" in page
    assert 'htmlFor={queryId}' in form
    assert 'htmlFor={domainsId}' in form
    assert 'htmlFor={maxResultsId}' in form
    assert "<textarea" in form
    assert 'role="alert"' in form
    assert "aria-invalid=" in form
    assert "validationMessageId" in form
    assert "tabIndex={-1}" in form
    assert "autoFocus" not in form
    assert "localStorage" not in form
    assert "sessionStorage" not in form
    assert "aria-live" not in form
    assert "researchRequestIdRef" in page
    assert "mapPreviewErrorMessage(researchPrepareError)" in page
    onmessage_start = page.index("ws.onmessage = (event) => {")
    onmessage_end = page.index("ws.onclose = () => {", onmessage_start)
    onmessage_block = page[onmessage_start:onmessage_end]
    protocol_start = onmessage_block.index('data.type === "protocol_error"')
    error_start = onmessage_block.index('data.type === "error"', protocol_start)
    protocol_block = onmessage_block[protocol_start:error_start]
    error_block = onmessage_block[error_start:]
    assert "researchPendingRef" not in protocol_block
    assert "researchPendingRef" not in error_block


def test_phase3_research_and_calendar_results_are_accessible_and_private():
    page = PAGE.read_text(encoding="utf-8")
    assert 'id="productivity-research-result-heading"' in page
    assert 'id="productivity-calendar-result-heading"' in page
    assert 'aria-labelledby="productivity-research-result-heading"' in page
    assert 'aria-labelledby="productivity-calendar-result-heading"' in page
    assert 'role="status"' in page
    assert 'aria-live="polite"' in page
    assert "tabIndex={-1}" in page
    assert "rel=\"noopener noreferrer\"" in page
    assert "target=\"_blank\"" in page
    apply_start = page.index("const applyProductivityMessage")
    apply_end = page.index("const confirmProductivityAction")
    apply_block = page[apply_start:apply_end]
    assert "addMessage(" not in apply_block
    assert "localStorage" not in apply_block
    assert "sessionStorage" not in apply_block
    assert "setProductivityResearchResult(null)" in page
    assert "setProductivityCalendarResult(null)" in page
    clear_start = page.index("const clearProductivityLifecycle")
    clear_end = page.index("const clearScheduledJobsState")
    clear_block = page[clear_start:clear_end]
    assert "setProductivityResearchResult(null)" in clear_block
    assert "setProductivityCalendarResult(null)" in clear_block


def test_phase3_reminder_form_labels_errors_and_privacy():
    page = PAGE.read_text(encoding="utf-8")
    form = (
        Path(__file__).resolve().parent.parent
        / "hikari-frontend"
        / "src"
        / "components"
        / "ReminderProposalForm.tsx"
    ).read_text(encoding="utf-8")

    assert "<ReminderProposalForm" in page
    assert "submitReminderPrepare" in page
    assert "clearReminderForm" in page
    assert "reminderPending" in page
    assert "productivityPreparePending" in page
    assert 'htmlFor={titleId}' in form
    assert 'htmlFor={remindAtId}' in form
    assert 'htmlFor={notesId}' in form
    assert 'htmlFor={listNameId}' in form
    assert "<textarea" in form
    assert 'role="alert"' in form
    assert "aria-invalid=" in form
    assert "validationMessageId" in form
    assert 'activeField === "title" ? validationMessageId' in form
    assert 'activeField === "remindAt" ? validationMessageId' in form
    assert "tabIndex={-1}" in form
    assert "autoFocus" not in form
    assert "localStorage" not in form
    assert "sessionStorage" not in form
    assert "aria-live" not in form
    assert "reminderRequestIdRef" in page
    assert "mapPreviewErrorMessage(reminderPrepareError)" in page
    onmessage_start = page.index("ws.onmessage = (event) => {")
    onmessage_end = page.index("ws.onclose = () => {", onmessage_start)
    onmessage_block = page[onmessage_start:onmessage_end]
    protocol_start = onmessage_block.index('data.type === "protocol_error"')
    error_start = onmessage_block.index('data.type === "error"', protocol_start)
    protocol_block = onmessage_block[protocol_start:error_start]
    error_block = onmessage_block[error_start:]
    assert "reminderPendingRef" not in protocol_block
    assert "reminderPendingRef" not in error_block


def test_phase3_email_draft_form_labels_errors_and_privacy():
    page = PAGE.read_text(encoding="utf-8")
    form = (
        Path(__file__).resolve().parent.parent
        / "hikari-frontend"
        / "src"
        / "components"
        / "EmailDraftProposal.tsx"
    ).read_text(encoding="utf-8")

    assert "<EmailDraftProposal" in page
    assert "submitEmailDraftPrepare" in page
    assert "clearEmailDraftForm" in page
    assert "emailDraftPending" in page
    assert "productivityPreparePending" in page
    assert 'htmlFor={recipientId}' in form
    assert 'htmlFor={subjectId}' in form
    assert 'htmlFor={bodyId}' in form
    assert "<textarea" in form
    assert 'role="alert"' in form
    assert "aria-invalid=" in form
    assert "validationMessageId" in form
    assert 'validationField === "recipient" ? validationMessageId' in form
    assert 'validationField === "subject" ? validationMessageId' in form
    assert 'validationField === "body" ? validationMessageId' in form
    assert "id={validationMessageId}" in form
    assert "tabIndex={-1}" in form
    assert "autoFocus" not in form
    assert "localStorage" not in form
    assert "sessionStorage" not in form
    assert "aria-live" not in form
    assert "emailDraftRequestIdRef" in page
    assert "clearEmailDraftPendingWithLocalError" not in page
    onmessage_start = page.index("ws.onmessage = (event) => {")
    onmessage_end = page.index("ws.onclose = () => {", onmessage_start)
    onmessage_block = page[onmessage_start:onmessage_end]
    protocol_start = onmessage_block.index('data.type === "protocol_error"')
    error_start = onmessage_block.index('data.type === "error"', protocol_start)
    protocol_block = onmessage_block[protocol_start:error_start]
    error_block = onmessage_block[error_start:]
    assert "emailDraftPendingRef" not in protocol_block
    assert 'setInterfaceError("Unsupported server protocol")' in protocol_block
    assert "emailDraftPendingRef" not in error_block
    assert 'setInterfaceError("Server request failed")' in error_block


def test_phase3_scheduled_jobs_panel_live_regions_and_labelled_controls():
    page = PAGE.read_text(encoding="utf-8")
    panel = (
        Path(__file__).resolve().parent.parent
        / "hikari-frontend"
        / "src"
        / "components"
        / "ScheduledJobsPanel.tsx"
    ).read_text(encoding="utf-8")

    assert "<ScheduledJobsPanel" in page
    assert "onPause={pauseScheduledJob}" in page
    assert "onResume={resumeScheduledJob}" in page
    assert "onCancel={cancelScheduledJob}" in page
    assert "error={scheduledJobsError}" in page
    assert "statusMessage={scheduledJobsStatus}" in page
    assert 'setScheduledJobsStatus("List loaded.")' in page
    assert 'setScheduledJobsStatus("Correlated update received.")' in page
    assert '"Pause requested."' in page
    assert '"Resume requested."' in page
    assert '"Cancel requested."' in page
    assert 'aria-labelledby={headingId}' in panel
    assert 'aria-label="Scheduled job list"' in panel
    assert 'role="status"' in panel
    assert 'aria-live="polite"' in panel
    assert 'role="alert"' in panel
    assert 'type="button"' in panel
    assert "disabled={pending}" in panel
    assert "aria-label={`Pause ${job.actionLabel" in panel
    assert "aria-label={`Resume ${job.actionLabel" in panel
    assert "aria-label={`Cancel ${job.actionLabel" in panel
    assert "mapScheduledJobErrorMessage" in panel


def test_phase4_frontend_integration_accessibility():
    page = PAGE.read_text(encoding="utf-8")
    assert "<Phase4PairingPanel" in page
    assert "<HandoffOfferPanel" in page
    assert "<VisualTransferPanel" in page
    assert "headingRef={pairingHeadingRef}" in page
    assert "headingRef={handoffHeadingRef}" in page
    assert "headingRef={visualTransferHeadingRef}" in page
