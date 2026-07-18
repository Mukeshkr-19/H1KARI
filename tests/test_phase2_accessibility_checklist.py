"""Tests verifying that Phase 2 voice-companion and document-workflow accessibility checklist items are documented."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKLIST_PATH = REPO_ROOT / "docs" / "ACCESSIBILITY_CHECKLIST.md"


def test_phase2_accessibility_checklist_headings_present():
    """Verify that headings for Phase 2 accessibility checklist are present."""
    assert CHECKLIST_PATH.exists(), f"Checklist not found at {CHECKLIST_PATH}"
    content = CHECKLIST_PATH.read_text(encoding="utf-8")

    assert "## Phase 2 voice-companion and document-workflow" in content
    assert "### Automated Checks" in content
    assert "### Manual Checks" in content


def test_phase2_accessibility_checklist_requirements_present():
    """Verify that all Phase 2 accessibility checklist requirements are documented."""
    assert CHECKLIST_PATH.exists()
    content = CHECKLIST_PATH.read_text(encoding="utf-8")

    required_phrases = [
        "visible microphone inactive/listening/stopped states",
        "browser permission denial",
        "clear interim and final captions",
        "captions not relying only on color",
        "keyboard fallback after voice failure",
        "keyboard-only document prepare, confirm, cancel, and follow-up",
        "explicit confirmation wording",
        "confirmation focus placement",
        "cancellation feedback",
        "preserved task state after recoverable failure",
        "screen-reader announcement expectations",
        "VoiceOver manual checks",
        "200% browser zoom",
        "reduced-motion behavior",
        "focus visibility",
        "touch-target sizing",
        "understandable error text",
        "no indefinite “listening” state after disconnect or failure",
    ]

    for phrase in required_phrases:
        assert phrase in content, f"Expected requirement phrase not found in checklist: {phrase}"
