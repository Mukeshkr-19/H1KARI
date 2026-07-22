"""Tests for pure Phase 4 vision-analysis frontend primitives and component contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "hikari-frontend"
UTILS = FRONTEND / "src" / "utils" / "phase4"
COMPONENTS = FRONTEND / "src" / "components"


def test_vision_analysis_files_exist():
    assert (UTILS / "visionAnalysis.ts").exists()
    assert (UTILS / "visionAnalysis.test.ts").exists()
    assert (COMPONENTS / "VisionAnalysisPanel.tsx").exists()


def test_vision_analysis_no_forbidden_apis():
    sources = [
        (UTILS / "visionAnalysis.ts").read_text(encoding="utf-8"),
        (UTILS / "visionAnalysis.test.ts").read_text(encoding="utf-8"),
        (COMPONENTS / "VisionAnalysisPanel.tsx").read_text(encoding="utf-8"),
    ]
    forbidden_terms = [
        "getUserMedia",
        "mediaDevices",
        "FileReader",
        "readAsDataURL",
        "readAsArrayBuffer",
        "base64",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "setTimeout",
        "setInterval",
        "dangerouslySetInnerHTML",
    ]
    for source in sources:
        for term in forbidden_terms:
            assert term not in source, f"Forbidden term {term} found in vision analysis frontend source"


def test_vision_analysis_component_accessibility_and_privacy_contracts():
    component = (COMPONENTS / "VisionAnalysisPanel.tsx").read_text(encoding="utf-8")

    assert 'aria-labelledby="vision-analysis-heading"' in component
    assert 'id="vision-analysis-heading"' in component
    assert 'ref={headingRef}' in component
    assert 'tabIndex={-1}' in component
    assert 'role="status"' in component
    assert 'aria-live="polite"' in component
    assert 'aria-atomic="true"' in component
    assert 'role="alert"' in component

    # Verify live status region text does not include observation text
    assert "{formatStatusText(state)}" in component
    status_fn_start = component.find("function formatStatusText")
    status_fn_end = component.find("function formatErrorMessage")
    status_fn_body = component[status_fn_start:status_fn_end]
    assert "observation" not in status_fn_body.lower()
    assert "obs.text" not in status_fn_body


def test_vision_analysis_reducer_contracts():
    ts_source = (UTILS / "visionAnalysis.ts").read_text(encoding="utf-8")

    assert 'type VisionAnalysisStatus' in ts_source
    assert '"idle"' in ts_source
    assert '"preparing"' in ts_source
    assert '"awaiting_image"' in ts_source
    assert '"analyzing"' in ts_source
    assert '"completed"' in ts_source
    assert '"cancelled"' in ts_source
    assert '"expired"' in ts_source
    assert '"failed"' in ts_source

    assert 'isVisionAnalysisPending' in ts_source
    assert 'isVisionAnalysisTerminal' in ts_source
    assert 'reduceVisionAnalysis' in ts_source
    assert 'validateObservation' in ts_source
    assert 'validateObservations' in ts_source
    assert 'CONFIDENCE_UNCERTAINTY_THRESHOLD_MILLI = 700' in ts_source
    assert 'MAX_OBSERVATIONS = 16' in ts_source
    assert 'MAX_TEXT_CODE_POINTS = 2000' in ts_source
