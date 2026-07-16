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

    assert 'aria-label={isListening ? "Listening for voice input" : "Start voice input"}' in text
    assert 'aria-label="Send message"' in text
    assert text.count('aria-hidden="true" focusable="false"') >= 3


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
    assert "task_id: documentTaskId" in text
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
