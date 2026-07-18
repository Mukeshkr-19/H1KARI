"""Shipped voice entrypoints must not print captured or generated content."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_voice_entrypoints_do_not_print_transcripts_or_responses():
    simple = (REPO_ROOT / "services" / "hikari_simple.py").read_text(encoding="utf-8")
    service = (REPO_ROOT / "services" / "hikari_service.py").read_text(encoding="utf-8")

    for forbidden in (
        'print(f"📝 \'{text}\'")',
        'print(f"You: {text}")',
        'print(f"HIKARI: {resp}")',
        'print(f"[YOU] {text}")',
        'print(f"[HIKARI] {response}")',
    ):
        assert forbidden not in simple
        assert forbidden not in service


def test_legacy_voice_entrypoints_do_not_render_exception_details():
    simple = (REPO_ROOT / "services" / "hikari_simple.py").read_text(encoding="utf-8")
    service = (REPO_ROOT / "services" / "hikari_service.py").read_text(encoding="utf-8")

    assert 'return f"Oops: {e}"' not in simple
    assert 'print(f"Error: {e}")' not in simple
    assert 'print(f"[TTS Error] {e}")' not in service
    assert 'print(f"\\n[HIKARI] Error: {e}")' not in service
