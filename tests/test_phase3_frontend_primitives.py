"""Source contracts for the Phase 3 productivity action preview primitive."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "ProductivityActionPreview.tsx"
)
HELPERS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "actionPreview.ts"
)
HELPER_TESTS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "actionPreview.test.ts"
)
PACKAGE = REPO_ROOT / "hikari-frontend" / "package.json"


def _component() -> str:
    assert COMPONENT.is_file()
    return COMPONENT.read_text(encoding="utf-8")


def _helpers() -> str:
    assert HELPERS.is_file()
    return HELPERS.read_text(encoding="utf-8")


def test_productivity_action_preview_component_exists():
    assert COMPONENT.is_file()
    assert HELPERS.is_file()
    assert HELPER_TESTS.is_file()


def test_preview_uses_labelled_section_and_visible_heading():
    text = _component()
    assert "aria-labelledby={headingId}" in text
    assert "id={headingId}" in text
    assert "useId()" in text
    assert "<h3" in text
    assert "headingRef" in text
    assert 'tabIndex={-1}' in text


def test_preview_uses_description_lists_for_destination_and_payload():
    text = _component()
    assert 'title="Destination"' in text or ">Destination<" in text
    assert 'title="Payload preview"' in text or ">Payload preview<" in text
    assert "<dl" in text
    assert "<dt" in text
    assert "<dd" in text
    assert "listId={destinationId}" in text
    assert "listId={payloadId}" in text


def test_preview_confirm_and_cancel_are_native_buttons():
    text = _component()
    assert 'type="button"' in text
    assert text.count('type="button"') >= 2
    assert "disabled={confirmDisabled}" in text
    assert "disabled={cancelDisabled}" in text
    assert "onClick={onConfirm}" in text
    assert "onClick={onCancel}" in text
    confirm_start = text.rindex("<button", 0, text.index("onClick={onConfirm}"))
    confirm_end = text.index("</button>", text.index("onClick={onConfirm}"))
    cancel_start = text.rindex("<button", 0, text.index("onClick={onCancel}"))
    cancel_end = text.index("</button>", text.index("onClick={onCancel}"))
    confirm_block = text[confirm_start:confirm_end]
    cancel_block = text[cancel_start:cancel_end]
    assert 'type="button"' in confirm_block
    assert 'type="button"' in cancel_block
    assert "disabled={confirmDisabled}" in confirm_block
    assert "disabled={cancelDisabled}" in cancel_block
    assert "Confirm" in confirm_block
    assert "Cancel" in cancel_block


def test_preview_announces_pending_and_errors():
    text = _component()
    assert 'role="status"' in text
    assert 'aria-live="polite"' in text
    assert 'role="alert"' in text
    assert "Waiting for confirmation" in text
    assert "mapPreviewErrorMessage(error)" in text


def test_preview_bounding_and_sanitization_live_in_helper_module():
    helpers = _helpers()
    component = _component()
    assert "PREVIEW_LABEL_MAX = 120" in helpers
    assert "PREVIEW_VALUE_MAX = 2000" in helpers
    assert "export function sanitizePreviewText(" in helpers
    assert "export function boundPreviewLabel(" in helpers
    assert "export function boundPreviewValue(" in helpers
    assert "export function mapPreviewErrorMessage(" in helpers
    assert "\\u061C" in helpers
    assert "\\u200E" in helpers
    assert "\\u202A" in helpers
    assert "\\u2066" in helpers
    assert "\\uFEFF" in helpers
    assert "Preview shortened" in component
    assert "from \"@/utils/productivity/actionPreview\"" in component


def test_preview_uses_bounded_error_code_union_not_raw_strings():
    helpers = _helpers()
    component = _component()
    assert "export type ProductivityPreviewErrorCode" in helpers
    assert 'error?: ProductivityPreviewErrorCode' in component
    assert "error?: string" not in component
    assert "GENERIC_PREVIEW_ERROR_MESSAGE" in helpers
    assert "The action could not be completed." in helpers
    for code in (
        "confirm_failed",
        "cancel_failed",
        "proposal_expired",
        "proposal_invalid",
        "unavailable",
    ):
        assert f'"{code}"' in helpers


def test_preview_public_model_excludes_internal_identity_fields():
    text = _component()
    model_start = text.index("export type ProductivityActionPreviewModel")
    model_end = text.index("export type ProductivityActionPreviewProps")
    model = text[model_start:model_end]
    for banned in ("actorId", "actor_id", "sessionId", "session_id", "grantId", "grant_id"):
        assert banned not in model
    assert "proposalId: string" in model
    assert "targets: readonly PreviewEntry[]" in model
    assert "payload: readonly PreviewEntry[]" in model


def test_preview_has_no_dangerous_side_channels_or_transport():
    text = _component() + "\n" + _helpers()
    assert "dangerouslySetInnerHTML" not in text
    assert "localStorage" not in text
    assert "sessionStorage" not in text
    assert "fetch(" not in text
    assert "WebSocket" not in text
    assert "setInterval" not in text
    assert "setTimeout" not in text
    assert "send email" not in text.lower()
    assert "email-send" not in text.lower()
    assert "mailto:" not in text.lower()
    assert "useEffect" not in _component()


def test_preview_does_not_auto_confirm():
    text = _component()
    assert "onConfirm()" not in text.replace("onConfirm: () => void", "")
    assert "onClick={onConfirm}" in text
    assert "onClick={onCancel}" in text


def test_unit_script_runs_productivity_and_companion_tests():
    package = PACKAGE.read_text(encoding="utf-8")
    assert "actionPreview.test.ts" in package
    assert "actionPreview.test.js" in package
    assert "voiceDocumentIntent.test.js" in package
    assert "speechOutput.test.js" in package
    assert '"test:unit"' in package
