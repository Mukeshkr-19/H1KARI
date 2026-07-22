"""Accessibility contracts for Phase 4 vision analysis frontend primitives."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = (
    ROOT / "hikari-frontend" / "src" / "components" / "VisionAnalysisPanel.tsx"
)
PRIMITIVES = (
    ROOT / "hikari-frontend" / "src" / "utils" / "phase4" / "visionAnalysis.ts"
)


def _component() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def _status_formatter(source: str) -> str:
    start = source.index("function formatStatusText")
    end = source.index("function formatErrorMessage", start)
    return source[start:end]


def _live_region(source: str) -> str:
    marker = 'role="status"'
    start = source.index(marker)
    opening = source.rfind("<div", 0, start)
    end = source.index("</div>", start) + len("</div>")
    return source[opening:end]


def test_capability_and_actions_use_labelled_native_keyboard_controls() -> None:
    source = _component()
    assert "<fieldset" in source
    assert "<legend" in source
    assert source.count('type="radio"') == 2
    assert 'value="ocr"' in source
    assert 'value="describe"' in source
    assert "OCR (Text Extraction)" in source
    assert "Describe Image" in source
    assert source.count('type="button"') >= 2
    assert "Start Analysis" in source
    assert "Cancel Analysis" in source
    assert "onClick={handleStart}" in source
    assert "onClick={handleCancel}" in source
    assert "onKeyDown" not in source


def test_progress_is_visible_cancellable_and_status_is_bounded() -> None:
    source = _component()
    primitives = PRIMITIVES.read_text(encoding="utf-8")
    assert 'status === "preparing"' in primitives
    assert 'status === "awaiting_image"' in primitives
    assert 'status === "analyzing"' in primitives
    assert "{isPending && (" in source
    assert "disabled={state.cancelPending}" in source
    assert "Cancellation requested..." in source

    status = _status_formatter(source)
    assert "state.observations" not in status
    assert "obs.text" not in status
    assert "`${" not in status
    literals = re.findall(r'return "([^"]+)"', status)
    assert literals
    assert all(0 < len(item) <= 80 for item in literals)


def test_live_region_never_contains_observation_content() -> None:
    source = _component()
    live = _live_region(source)
    assert 'role="status"' in live
    assert 'aria-live="polite"' in live
    assert 'aria-atomic="true"' in live
    assert "{formatStatusText(state)}" in live
    assert "obs.text" not in live
    assert "state.observations" not in live
    assert "dangerouslySetInnerHTML" not in source
    assert '<p className="whitespace-pre-wrap break-words text-gray-100">{obs.text}</p>' in source


def test_errors_and_uncertainty_are_visible_without_progress_focus_stealing() -> None:
    source = _component()
    assert 'role="alert"' in source
    assert "formatErrorMessage(state.errorCode)" in source
    assert "CONFIDENCE_UNCERTAINTY_THRESHOLD_MILLI" in source
    assert "Confidence unavailable" in source
    assert "(Uncertain)" in source
    assert ".focus(" not in source
    assert "autoFocus" not in source
    assert "document.activeElement" not in source


def test_component_has_no_capture_transport_storage_or_timer_side_effects() -> None:
    source = _component()
    forbidden = (
        "getUserMedia",
        "mediaDevices",
        "FileReader",
        "readAsDataURL",
        "readAsArrayBuffer",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "setTimeout",
        "setInterval",
        "fetch(",
        "XMLHttpRequest",
    )
    for needle in forbidden:
        assert needle not in source
