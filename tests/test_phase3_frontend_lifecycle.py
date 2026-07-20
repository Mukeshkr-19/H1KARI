"""Source contracts for the Phase 3 frontend proposal lifecycle reducer."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LIFECYCLE = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "actionLifecycle.ts"
)
LIFECYCLE_TEST = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "actionLifecycle.test.ts"
)
PACKAGE = REPO_ROOT / "hikari-frontend" / "package.json"

REQUIRED_STATUSES = (
    "idle",
    "preview",
    "confirming",
    "approved",
    "executing",
    "completed",
    "failed",
    "cancelling",
    "cancelled",
)

REQUIRED_EVENTS = (
    "preview",
    "confirm",
    "approve",
    "execute",
    "complete",
    "fail",
    "cancel",
    "cancelled",
)


def _lifecycle() -> str:
    assert LIFECYCLE.is_file()
    return LIFECYCLE.read_text(encoding="utf-8")


def _tests() -> str:
    assert LIFECYCLE_TEST.is_file()
    return LIFECYCLE_TEST.read_text(encoding="utf-8")


def test_lifecycle_module_and_tests_exist():
    assert LIFECYCLE.is_file()
    assert LIFECYCLE_TEST.is_file()


def test_lifecycle_exports_required_statuses_and_events():
    text = _lifecycle()
    assert "export type ProposalLifecycleStatus" in text
    assert "export type ProposalLifecycleEvent" in text
    assert "export function reduceProposalLifecycle(" in text
    assert "export function createInitialProposalLifecycleState(" in text
    assert "export function freezeProposalSnapshot(" in text
    for status in REQUIRED_STATUSES:
        assert f'"{status}"' in text
    for event in REQUIRED_EVENTS:
        assert f'type: "{event}"' in text or f'"type": "{event}"' in text


def test_lifecycle_proposal_ids_are_exact_correlation_identifiers():
    text = _lifecycle()
    assert "PROPOSAL_ID_PATTERN" in text
    assert "^[a-z0-9][a-z0-9_.-]{0,79}$" in text
    assert "export function isValidProposalId(" in text
    assert "isValidProposalId(record.proposalId)" in text
    assert "proposalId === state.proposalId" in text
    assert ".slice(" not in text
    assert "sanitizePreviewText" not in text


def test_lifecycle_fail_closed_snapshot_and_entry_bounds():
    text = _lifecycle()
    assert "PREVIEW_ENTRY_MAX = 32" in text
    assert 'typeof input !== "object"' in text
    assert "Array.isArray(input)" in text
    assert "if (!Array.isArray(entries))" in text
    assert "entries.length > PREVIEW_ENTRY_MAX" in text
    assert "boundPreviewLabel" in text
    assert "boundPreviewValue" in text
    assert "Object.freeze(snapshot)" in text
    assert "return Object.freeze(frozen)" in text


def test_lifecycle_errors_are_safe_codes_only():
    text = _lifecycle()
    assert "ProductivityPreviewErrorCode" in text
    assert "resolveLifecycleErrorCode" in text
    assert 'return "unavailable"' in text
    assert "error?: string" not in text
    assert "error: string" not in text.replace("error: ProductivityPreviewErrorCode", "")


def test_lifecycle_has_no_side_effects():
    text = _lifecycle()
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
    assert "onConfirm()" not in text


def test_lifecycle_unit_tests_cover_required_behaviors():
    text = _tests()
    for needle in (
        "happy-path",
        "cancel transition",
        "fail transitions",
        "confirmation is only accepted from preview",
        "duplicate confirm",
        "duplicate cancel",
        "stale or mismatched",
        "idle or terminal",
        "cannot return to executing",
        "safe error codes",
        "invalid transitions",
        "nested immutability",
        "shared-prefix overlong",
        "Unicode and control characters",
        "malformed preview objects",
        "exceed destination or payload entry maxima",
        "bounds and sanitizes snapshot text",
    ):
        assert needle in text


def test_unit_script_includes_lifecycle_and_existing_suites():
    package = PACKAGE.read_text(encoding="utf-8")
    assert "actionLifecycle.test.ts" in package
    assert "actionLifecycle.test.js" in package
    assert "actionPreview.test.js" in package
    assert "voiceDocumentIntent.test.js" in package
    assert "speechOutput.test.js" in package
