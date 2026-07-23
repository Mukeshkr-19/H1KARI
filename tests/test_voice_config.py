from __future__ import annotations

from core.voice_config import DEFAULT_TTS_RATE, tts_rate, tts_voice_name


def test_tts_rate_defaults_to_comfortable_speed(monkeypatch):
    monkeypatch.delenv("HIKARI_TTS_RATE", raising=False)
    assert tts_rate() == DEFAULT_TTS_RATE == 170


def test_tts_rate_is_bounded_and_invalid_values_fail_safe(monkeypatch):
    monkeypatch.setenv("HIKARI_TTS_RATE", "20")
    assert tts_rate() == 120
    monkeypatch.setenv("HIKARI_TTS_RATE", "900")
    assert tts_rate() == 220
    monkeypatch.setenv("HIKARI_TTS_RATE", "fast")
    assert tts_rate() == 170


def test_tts_voice_accepts_only_a_preset_name_not_a_path(monkeypatch):
    monkeypatch.setenv("HIKARI_TTS_VOICE", "alba")
    assert tts_voice_name() == "alba"
    monkeypatch.setenv("HIKARI_TTS_VOICE", "/private/voice.wav")
    assert tts_voice_name() == "alba"
