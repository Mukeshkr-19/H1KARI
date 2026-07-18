"""Daemon recognition uses the bounded adapter boundary and preserves privacy."""

from __future__ import annotations

from unittest.mock import MagicMock

from services import hikari_daemon as daemon


def test_recognize_audio_does_not_print_transcript(capsys, monkeypatch):
    """Daemon recognize_audio must not print the recognized text."""
    fake_adapter = MagicMock()
    fake_adapter.transcribe.return_value = "secret command"
    monkeypatch.setattr(daemon, "stt_adapter", fake_adapter)

    class FakeAudio:
        def get_raw_data(self):
            return b"\x00\x01" * 8

        sample_rate = 16000
        sample_width = 2

    audio = FakeAudio()
    result = daemon.recognize_audio(audio)

    out, _err = capsys.readouterr()
    assert result == "secret command"
    assert "secret command" not in out
    assert "Recognition succeeded" in out


def test_recognize_audio_returns_empty_on_adapter_failure(capsys, monkeypatch):
    """Adapter failure must return empty string, never fall back to Google."""
    from core.speech_adapters import SpeechBackendUnavailable

    fake_adapter = MagicMock()
    fake_adapter.transcribe.side_effect = SpeechBackendUnavailable("model missing")
    monkeypatch.setattr(daemon, "stt_adapter", fake_adapter)

    class FakeAudio:
        def get_raw_data(self):
            return b"\x00\x01" * 8

        sample_rate = 16000
        sample_width = 2

    audio = FakeAudio()
    result = daemon.recognize_audio(audio)

    out, _err = capsys.readouterr()
    assert result == ""
    assert "falling back to text" in out
    fake_adapter.transcribe.assert_called_once()


def test_recognize_audio_uses_configured_backend(monkeypatch):
    """Daemon builds the adapter from runtime configuration."""
    captured = {}

    def fake_build(backend_name):
        captured["backend"] = backend_name
        return MagicMock(transcribe=lambda _audio: "hello")

    monkeypatch.setattr(daemon, "build_stt_adapter", fake_build)
    monkeypatch.setattr(daemon, "_get_configured_stt_backend", lambda: "faster-whisper")

    class FakeAudio:
        def get_raw_data(self):
            return b"\x00\x01" * 8

        sample_rate = 16000
        sample_width = 2

    # Trigger adapter construction via the daemon's initializer path.
    daemon.initialize_audio_backends()
    assert captured.get("backend") == "faster-whisper"


def test_conversation_log_never_persists_voice_content(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "maybe_rotate_daily_log", lambda *_args: tmp_path / "voice.log")

    daemon.log_convo("private spoken request", "private assistant response")

    content = (tmp_path / "voice.log").read_text(encoding="utf-8")
    assert "private spoken request" not in content
    assert "private assistant response" not in content
    assert "voice_turn=response" in content
