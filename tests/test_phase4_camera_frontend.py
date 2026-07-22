"""Tests for pure Phase 4 browser-camera primitives and component contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "hikari-frontend"
UTILS = FRONTEND / "src" / "utils" / "phase4"
COMPONENTS = FRONTEND / "src" / "components"
PAGE = FRONTEND / "src" / "app" / "page.tsx"
VISUAL_TRANSFER = UTILS / "visualTransfer.ts"


def test_camera_capture_files_exist():
    assert (UTILS / "cameraCapture.ts").exists()
    assert (UTILS / "cameraCapture.test.ts").exists()
    assert (COMPONENTS / "CameraCapturePanel.tsx").exists()


def test_camera_capture_no_forbidden_apis():
    sources = [
        (UTILS / "cameraCapture.ts").read_text(encoding="utf-8"),
        (UTILS / "cameraCapture.test.ts").read_text(encoding="utf-8"),
        (COMPONENTS / "CameraCapturePanel.tsx").read_text(encoding="utf-8"),
    ]
    forbidden_terms = [
        "getDisplayMedia",
        "toDataURL",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "fetch",
        "XMLHttpRequest",
        "setTimeout",
        "setInterval",
        "dangerouslySetInnerHTML",
        "enumerateDevices",
        "AppleScript",
    ]
    for source in sources:
        for term in forbidden_terms:
            assert term not in source, f"Forbidden term {term} found in camera capture frontend source"


def test_camera_capture_consent_and_constraints_contracts():
    component = (COMPONENTS / "CameraCapturePanel.tsx").read_text(encoding="utf-8")

    # Explicit getUserMedia with video facingMode environment and audio false
    assert 'getUserMedia({ video: { facingMode: "environment" }, audio: false })' in component
    assert 'audio: false' in component

    # Uses toBlob instead of toDataURL
    assert 'toBlob(' in component
    assert 'toDataURL' not in component

    # Native buttons with exact accessible names
    assert "Start camera" in component
    assert "Capture image" in component
    assert "Stop camera" in component

    # Visible active text indicator (not color alone)
    assert "Camera active" in component


def test_camera_capture_accessibility_and_privacy_contracts():
    component = (COMPONENTS / "CameraCapturePanel.tsx").read_text(encoding="utf-8")

    assert 'aria-labelledby="camera-capture-heading"' in component
    assert 'id="camera-capture-heading"' in component
    assert 'ref={headingRef}' in component
    assert 'tabIndex={-1}' in component
    assert 'role="status"' in component
    assert 'aria-live="polite"' in component
    assert 'aria-atomic="true"' in component
    assert 'role="alert"' in component

    # The component itself must not invoke focus()
    assert ".focus()" not in component

    # Format status text contains safe status phrases only
    status_fn_start = component.find("function formatStatusText")
    status_fn_end = component.find("function formatErrorMessage")
    status_fn_body = component[status_fn_start:status_fn_end]
    assert "data:" not in status_fn_body
    assert "base64" not in status_fn_body
    assert "filename" not in status_fn_body
    assert "err." not in status_fn_body


def test_camera_capture_reducer_and_state_contracts():
    ts_source = (UTILS / "cameraCapture.ts").read_text(encoding="utf-8")

    assert 'type CameraCaptureStatus' in ts_source
    assert '"idle"' in ts_source
    assert '"requesting"' in ts_source
    assert '"active"' in ts_source
    assert '"capturing"' in ts_source
    assert '"captured"' in ts_source
    assert '"stopping"' in ts_source
    assert '"stopped"' in ts_source
    assert '"failed"' in ts_source

    assert 'type CameraCaptureErrorCode' in ts_source
    assert '"camera_unavailable"' in ts_source
    assert '"permission_denied"' in ts_source
    assert '"capture_failed"' in ts_source
    assert '"image_too_large"' in ts_source
    assert '"dimensions_exceeded"' in ts_source

    assert 'stopStreamTracks' in ts_source
    assert 'MAX_FRAME_BYTES = 1048576' in ts_source
    assert 'MAX_FRAME_DIMENSION = 4096' in ts_source


def test_camera_capture_is_wired_only_to_an_accepted_analysis() -> None:
    page = PAGE.read_text(encoding="utf-8")
    visual = VISUAL_TRANSFER.read_text(encoding="utf-8")

    assert 'import { CameraCapturePanel }' in page
    assert 'handoffState.status === "accepted"' in page
    assert 'visionAnalysisState.status === "awaiting_image"' in page
    assert 'onFrameCaptured={selectVisualTransferFileAction}' in page
    assert 'selectVisualTransferFileAction = useCallback((file: Blob)' in page
    assert 'beginVisualTransferAction = useCallback(async (file: Blob)' in page
    assert 'readonly fileRef: Blob | null' in visual


def test_pending_permission_and_unmount_invalidate_late_camera_results() -> None:
    component = (COMPONENTS / "CameraCapturePanel.tsx").read_text(encoding="utf-8")

    assert 'state.status === "requesting" ? "Cancel camera request"' in component
    assert "mountedRef.current = false" in component
    assert "tokenRef.current += 1" in component
    assert "stopStreamTracks(currentStreamRef.current)" in component
    assert 'dispatch({ type: "CLEAR_FRAME" })' in component
