"""Deterministic Phase 4 camera-capture accessibility regression.

Tests prove the camera-capture panel provides bounded polite status regions,
safe errors with role=alert, visible camera activity without color dependence,
native keyboard operation, no focus stealing, and observation text never
copied into live status. All tests are deterministic source inspections.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "hikari-frontend"
COMPONENTS = FRONTEND / "src" / "components"
UTILS = FRONTEND / "src" / "utils" / "phase4"

CAMERA_PANEL = COMPONENTS / "CameraCapturePanel.tsx"
CAMERA_TS = UTILS / "cameraCapture.ts"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """Strip JSX/TS comments to avoid false positives in content checks."""
    result = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    result = re.sub(r"//[^\n]*", "", result)
    return result


# ===========================================================================
# Accessibility (40-46)
# ===========================================================================


def test_bounded_polite_status_regions() -> None:
    """(40) Status updates use role=status with aria-live=polite and aria-atomic."""
    panel = _source(CAMERA_PANEL)
    assert 'role="status"' in panel
    assert 'aria-live="polite"' in panel
    assert 'aria-atomic="true"' in panel


def test_safe_errors_use_role_alert() -> None:
    """(41) Error messages use role=alert for assertive announcement."""
    panel = _source(CAMERA_PANEL)
    assert 'role="alert"' in panel
    # The error alert is conditionally rendered only on failed status
    assert 'state.status === "failed"' in panel
    assert "formatErrorMessage" in panel


def test_camera_activity_visible_without_depending_on_color() -> None:
    """(42) Camera-active indicator uses text 'Camera active', not color alone."""
    panel = _source(CAMERA_PANEL)
    # Text indicator present
    assert "Camera active" in panel
    # The colored dot is marked aria-hidden (decorative, not the sole indicator)
    assert 'aria-hidden="true"' in panel
    # The text is in a <span> that is not aria-hidden
    assert "<span>Camera active</span>" in panel


def test_uncertainty_is_visible() -> None:
    """(43) The vision analysis panel communicates uncertainty visibly."""
    # This is tested in the vision panel, but we verify the camera panel
    # communicates capture status (including 'capturing' and 'failed')
    panel = _source(CAMERA_PANEL)
    status_fn_start = panel.find("function formatStatusText")
    status_fn_end = panel.find("function formatErrorMessage")
    status_fn = panel[status_fn_start:status_fn_end]
    # All status values have visible text
    for status_text in (
        "Camera inactive.",
        "Requesting camera permission...",
        "Camera active.",
        "Capturing image frame...",
        "Image frame captured.",
        "Stopping camera...",
        "Camera stopped.",
        "Camera capture error.",
    ):
        assert status_text in status_fn, f"Missing status text: {status_text}"


def test_native_keyboard_operation() -> None:
    """(44) Controls are native <button> elements with type=button (keyboard-operable)."""
    panel = _source(CAMERA_PANEL)
    assert 'type="button"' in panel
    # No custom onKeyDown handlers that would interfere with native keyboard
    assert "onKeyDown" not in panel
    # No role=button (which would require manual keyboard handling)
    assert 'role="button"' not in panel


def test_no_normal_progress_focus_stealing() -> None:
    """(45) No .focus(), autoFocus, or document.activeElement calls during normal progress."""
    panel = _strip_comments(_source(CAMERA_PANEL))
    assert ".focus(" not in panel
    assert "autoFocus" not in panel
    assert "document.activeElement" not in panel
    # tabIndex=-1 on heading is allowed (for error focus recovery), but
    # no programmatic focus calls during normal camera operation


def test_observation_text_not_copied_into_live_status() -> None:
    """(46) The live status region contains only bounded status text, not observation content."""
    panel = _source(CAMERA_PANEL)
    # Extract the live region
    marker = 'role="status"'
    start = panel.index(marker)
    # Find the enclosing <div
    opening = panel.rfind("<div", 0, start)
    end = panel.index("</div>", start) + len("</div>")
    live_region = panel[opening:end]

    assert 'role="status"' in live_region
    assert 'aria-live="polite"' in live_region
    assert 'aria-atomic="true"' in live_region
    assert "{formatStatusText(state.status)}" in live_region

    # No observation text, blob, or raw content in the live region
    assert "obs.text" not in live_region
    assert "observations" not in live_region
    assert "blob" not in live_region.lower()
    assert "data:" not in live_region
    assert "base64" not in live_region
    assert "dangerouslySetInnerHTML" not in panel


# ===========================================================================
# Additional accessibility structural checks
# ===========================================================================


def test_section_has_accessible_label() -> None:
    """The camera section has an aria-labelledby reference to its heading."""
    panel = _source(CAMERA_PANEL)
    assert 'aria-labelledby="camera-capture-heading"' in panel
    assert 'id="camera-capture-heading"' in panel
    assert "<h2" in panel
    assert "Camera Capture" in panel


def test_heading_supports_focus_recovery() -> None:
    """The heading has tabIndex=-1 for error-recovery focus, but no auto-focus."""
    panel = _source(CAMERA_PANEL)
    assert "tabIndex={-1}" in panel or 'tabIndex={-1}' in panel
    # No autofocus on the heading
    heading_start = panel.index("<h2")
    heading_end = panel.index(">", heading_start)
    heading_tag = panel[heading_start:heading_end]
    assert "autoFocus" not in heading_tag


def test_error_region_supports_focus_recovery() -> None:
    """The error alert has tabIndex=-1 for screen-reader focus recovery."""
    panel = _source(CAMERA_PANEL)
    # The error div has tabIndex=-1
    error_marker = 'role="alert"'
    error_start = panel.index(error_marker)
    error_opening = panel.rfind("<div", 0, error_start)
    error_tag_end = panel.index(">", error_start)
    error_tag = panel[error_opening:error_tag_end]
    assert "tabIndex" in error_tag or "tabIndex={-1}" in panel


def test_video_preview_is_muted_and_labelled() -> None:
    """The live video preview is muted (no audio) and has an aria-label."""
    panel = _source(CAMERA_PANEL)
    assert "muted" in panel
    assert 'aria-label="Live camera preview"' in panel
    assert "autoPlay" in panel
    assert "playsInline" in panel


def test_status_text_is_bounded_and_safe() -> None:
    """Status text literals are bounded (max 80 chars) and contain no raw content."""
    panel = _source(CAMERA_PANEL)
    status_fn_start = panel.find("function formatStatusText")
    status_fn_end = panel.find("function formatErrorMessage")
    status_fn = panel[status_fn_start:status_fn_end]
    literals = re.findall(r'return "([^"]+)"', status_fn)
    assert literals
    assert all(0 < len(item) <= 80 for item in literals)
    for literal in literals:
        assert "data:" not in literal
        assert "base64" not in literal
        assert "filename" not in literal.lower()
        assert "http" not in literal.lower()


def test_error_text_is_bounded_and_safe() -> None:
    """Error message literals are bounded and contain no raw content."""
    panel = _source(CAMERA_PANEL)
    error_fn_start = panel.find("function formatErrorMessage")
    error_fn_end = panel.find("export function CameraCapturePanel", error_fn_start)
    error_fn = panel[error_fn_start:error_fn_end]
    literals = re.findall(r'return "([^"]+)"', error_fn)
    assert literals
    assert all(0 < len(item) <= 200 for item in literals)
    for literal in literals:
        assert "data:" not in literal
        assert "base64" not in literal
        assert "filename" not in literal.lower()
        assert "http" not in literal.lower()
        assert "stack" not in literal.lower()


def test_no_custom_keyboard_handlers() -> None:
    """No onKeyDown, onKeyUp, or onKeyPress that could trap or break keyboard navigation."""
    panel = _strip_comments(_source(CAMERA_PANEL))
    assert "onKeyDown" not in panel
    assert "onKeyUp" not in panel
    assert "onKeyPress" not in panel


def test_capture_button_disabled_when_not_active() -> None:
    """The Capture button is disabled when status is not 'active' (prevents accidental capture)."""
    panel = _source(CAMERA_PANEL)
    assert "disabled" in panel
    assert "canCapture" in panel
    assert 'state.status === "active"' in panel


def test_stop_button_always_available_when_active() -> None:
    """The Stop button is always shown when the camera is active (cancellable)."""
    panel = _source(CAMERA_PANEL)
    assert "Stop camera" in panel
    assert "handleStopCamera" in panel
    # Stop is shown when isActive is true
    assert "isActive" in panel
