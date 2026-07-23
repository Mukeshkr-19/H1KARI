"""Foreground voice mode uses bounded adapters and deterministic lifecycle."""

from __future__ import annotations

import inspect
import sys

import pytest

from core.voice import run_voice_session


class FakeVoice:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.warmed = 0
        self.spoken = []

    def warmup(self):
        self.warmed += 1

    def listen(self):
        return next(self.turns)

    def speak(self, text):
        self.spoken.append(text)


class FakeOrchestrator:
    def __init__(self):
        self.calls = []
        self.finalized = 0

    def process_input(self, text, source="text"):
        self.calls.append((text, source))
        return "bounded response"

    def finalize_session(self):
        self.finalized += 1


def test_foreground_voice_routes_transcript_and_stops_without_persisting_audio(capsys):
    voice = FakeVoice(["weather in Sample City", "exit"])
    orchestrator = FakeOrchestrator()

    result = run_voice_session(
        orchestrator,
        backend="google-speech",
        voice_system=voice,
    )

    assert result == 0
    assert voice.warmed == 1
    assert voice.spoken == ["bounded response"]
    assert orchestrator.calls == [("weather in Sample City", "voice")]
    assert orchestrator.finalized == 1
    output = capsys.readouterr().out
    assert "weather in Sample City" in output
    assert "bounded response" in output


def test_foreground_voice_ignores_empty_transcript_and_stops_on_keyboard_interrupt(capsys):
    class InterruptingVoice(FakeVoice):
        def listen(self):
            if self.warmed == 1:
                self.warmed += 1
                return ""
            raise KeyboardInterrupt

    voice = InterruptingVoice([])
    orchestrator = FakeOrchestrator()

    assert run_voice_session(
        orchestrator,
        backend="openai-whisper",
        voice_system=voice,
    ) == 0
    assert orchestrator.calls == []
    assert orchestrator.finalized == 1
    assert "Voice mode stopped" in capsys.readouterr().out


def test_cli_accepts_explicit_foreground_voice_backend(monkeypatch):
    import hikari

    calls = []
    monkeypatch.setattr(hikari, "run_voice", lambda backend: calls.append(backend) or 0)
    monkeypatch.setattr(
        sys,
        "argv",
        ["hikari.py", "--voice", "--voice-backend", "google-speech"],
    )

    with pytest.raises(SystemExit) as stopped:
        hikari.main()

    assert stopped.value.code == 0
    assert calls == ["google-speech"]


def test_text_mode_enables_nonpersistent_terminal_line_editing():
    import hikari

    source = inspect.getsource(hikari.run_interactive)
    assert "import readline" in source
    assert "write_history_file" not in source
