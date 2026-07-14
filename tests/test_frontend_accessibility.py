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
