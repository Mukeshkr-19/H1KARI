"""Source contracts for the Phase 3 scheduled-jobs frontend primitive."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HELPERS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "scheduledJobs.ts"
)
HELPER_TESTS = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "utils"
    / "productivity"
    / "scheduledJobs.test.ts"
)
PANEL = (
    REPO_ROOT
    / "hikari-frontend"
    / "src"
    / "components"
    / "ScheduledJobsPanel.tsx"
)
PACKAGE = REPO_ROOT / "hikari-frontend" / "package.json"

REQUIRED_STATES = (
    "scheduled",
    "paused",
    "running",
    "interrupted",
    "completed",
    "failed",
    "cancelled",
)


def _helpers() -> str:
    assert HELPERS.is_file()
    return HELPERS.read_text(encoding="utf-8")


def _panel() -> str:
    assert PANEL.is_file()
    return PANEL.read_text(encoding="utf-8")


def _tests() -> str:
    assert HELPER_TESTS.is_file()
    return HELPER_TESTS.read_text(encoding="utf-8")


def test_scheduled_jobs_files_exist():
    assert HELPERS.is_file()
    assert HELPER_TESTS.is_file()
    assert PANEL.is_file()


def test_helpers_define_bounded_view_and_states():
    text = _helpers()
    assert "export type ScheduledJobView" in text
    assert 'ownershipLabel: typeof SCHEDULED_JOB_OWNERSHIP_LABEL' in text
    assert 'SCHEDULED_JOB_OWNERSHIP_LABEL = "Current session"' in text
    for state in REQUIRED_STATES:
        assert f'"{state}"' in text
    assert "export function parseScheduledJobView(" in text
    assert "export function availableScheduledJobControls(" in text
    assert "isValidJobId" in text
    assert "boundPreviewLabel" in text


def test_helpers_exclude_sensitive_fields_and_side_effects():
    text = _helpers()
    for banned in (
        "actorId",
        "sessionId",
        "proposalPayload",
        "emailBody",
        "calendarContent",
        "providerResponse",
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "setInterval",
        "console.",
    ):
        assert banned not in text


def test_panel_is_accessible_and_transport_free():
    text = _panel()
    assert 'aria-labelledby={headingId}' in text
    assert 'aria-label="Scheduled job list"' in text
    assert 'role="status"' in text
    assert 'aria-live="polite"' in text
    assert 'role="alert"' in text
    assert 'type="button"' in text
    assert "aria-label={`Pause ${job.actionLabel" in text
    assert "aria-label={`Resume ${job.actionLabel" in text
    assert "aria-label={`Cancel ${job.actionLabel" in text
    assert "disabled={pending}" in text
    for banned in (
        "fetch(",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "setTimeout",
        "setInterval",
        "console.",
        "useEffect",
    ):
        assert banned not in text


def test_unit_tests_cover_required_behaviors():
    text = _tests()
    for needle in (
        "every job state",
        "correct controls for each state",
        "without trimming or truncation",
        "privacy-sensitive keys",
        "boolean negative inconsistent and overflow",
        "pending controls that are unavailable",
        "oversized job lists",
        "bounds and sanitizes display text",
        "rejects stale ids",
        "safe error codes",
    ):
        assert needle in text


def test_unit_script_includes_scheduled_jobs_and_existing_suites():
    package = PACKAGE.read_text(encoding="utf-8")
    assert "scheduledJobs.test.ts" in package
    assert "scheduledJobs.test.js" in package
    assert "productivityProtocol.test.js" in package
    assert "actionLifecycle.test.js" in package
    assert "actionPreview.test.js" in package
    assert "voiceDocumentIntent.test.js" in package
    assert "speechOutput.test.js" in package
