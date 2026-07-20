"""Source contracts for Phase 3 approval-scope frontend primitives."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HELPERS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "approvalScopes.ts"
)
HELPER_TESTS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "approvalScopes.test.ts"
)
SELECTOR = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "ApprovalScopeSelector.tsx"
)
PACKAGE = REPO_ROOT / "hikari-frontend" / "package.json"

REQUIRED_SCOPES = (
    "once",
    "session",
    "duration",
    "precise_persistent",
)

REQUIRED_DURATIONS = (
    "15_minutes",
    "1_hour",
    "8_hours",
)

BANNED_PHRASES = (
    "remember everything",
    "wildcard",
    "global",
    "forever",
    "unrestricted",
    "implicit consent",
)


def _helpers() -> str:
    assert HELPERS.is_file()
    return HELPERS.read_text(encoding="utf-8")


def _tests() -> str:
    assert HELPER_TESTS.is_file()
    return HELPER_TESTS.read_text(encoding="utf-8")


def _selector() -> str:
    assert SELECTOR.is_file()
    return SELECTOR.read_text(encoding="utf-8")


def test_approval_scope_files_exist():
    assert HELPERS.is_file()
    assert HELPER_TESTS.is_file()
    assert SELECTOR.is_file()


def test_helpers_define_exact_scopes_default_and_durations():
    text = _helpers()
    assert 'DEFAULT_APPROVAL_SCOPE: ApprovalScopeKind = "once"' in text
    for scope in REQUIRED_SCOPES:
        assert f'"{scope}"' in text
    for duration in REQUIRED_DURATIONS:
        assert f'"{duration}"' in text
    assert "900" in text
    assert "3600" in text
    assert "28800" in text
    assert "export function createApprovalScopeStateFromAllowed(" in text
    assert "export function parseAllowedApprovalScopes(" in text
    assert "export function isApprovalScopeConfirmReady(" in text
    assert "exact action and destination" in text.lower()


def test_helpers_exclude_sensitive_fields_and_side_effects():
    text = _helpers()
    for banned in (
        "actorId",
        "sessionId",
        "approvalId",
        "provider",
        "payload",
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "setInterval",
        "console.",
        "JSON.stringify",
    ):
        assert banned not in text
    for phrase in BANNED_PHRASES:
        assert phrase not in text.lower()


def test_selector_uses_native_labelled_controls_without_stealing_focus():
    text = _selector()
    assert 'type="radio"' in text
    assert 'type="checkbox"' in text
    assert 'role="radiogroup"' in text
    assert "htmlFor={optionId}" in text
    assert "htmlFor={ackId}" in text
    assert "aria-describedby={ackWarningId}" in text
    assert "state.allowedScopes.map" in text
    assert "APPROVAL_SCOPE_BINDING_DESCRIPTION" in text
    assert "APPROVAL_PERSISTENT_WARNING" in text
    assert "autoFocus" not in text
    assert ".focus(" not in text
    assert "onKeyDown" not in text
    for banned in (
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "encodeProductivity",
    ):
        assert banned not in text
    for phrase in BANNED_PHRASES:
        assert phrase not in text.lower()


def test_unit_tests_cover_required_behaviors():
    text = _tests()
    for needle in (
        "defaults to once with no duration or acknowledgement",
        "defaults to once when advertised otherwise first advertised scope",
        "covers every scope state and confirm readiness",
        "rejects invalid scope duration and acknowledgement inputs",
        "rejects invalid server advertisements for allowed scopes",
        "clears duration and acknowledgement when the selected scope changes",
        "bounds duration to the three allowed second choices only",
        "resets to the default once state",
        "returns immutable frozen states",
        "exposes bounded human labels and binding description",
        "supplies keyboard-native control labels for every radio and duration choice",
        "has no side-effectful imports or transport helpers",
    ):
        assert needle in text


def test_unit_script_includes_approval_scopes_and_existing_suites():
    package = PACKAGE.read_text(encoding="utf-8")
    assert "approvalScopes.test.ts" in package
    assert "approvalScopes.test.js" in package
    assert "productivityProtocol.test.js" in package
    assert "scheduledJobsProtocol.test.js" in package
