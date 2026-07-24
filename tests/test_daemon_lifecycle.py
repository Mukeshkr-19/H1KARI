"""Behavioral coverage for the always-on daemon lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from services import hikari_daemon as daemon


class WaitTimeoutError(Exception):
    pass


class UnknownValueError(Exception):
    pass


class Microphone:
    def __enter__(self):
        return object()

    def __exit__(self, *_args):
        return False


def _speech_module():
    return SimpleNamespace(
        Microphone=Microphone,
        WaitTimeoutError=WaitTimeoutError,
        UnknownValueError=UnknownValueError,
    )


def test_import_does_not_load_audio_models(tmp_path: Path):
    marker = tmp_path / "model-imported"
    module_source = (
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['HIKARI_TEST_MODEL_MARKER']).write_text('imported')\n"
    )
    (tmp_path / "faster_whisper.py").write_text(module_source, encoding="utf-8")
    (tmp_path / "whisper.py").write_text(module_source, encoding="utf-8")
    env = os.environ.copy()
    env["HIKARI_TEST_MODEL_MARKER"] = str(marker)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(tmp_path), str(Path(__file__).parents[1])) if part
    )

    result = subprocess.run(
        [sys.executable, "-c", "import services.hikari_daemon"],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_main_fails_cleanly_when_speech_recognition_is_unavailable(monkeypatch):
    monkeypatch.setattr(daemon, "initialize_audio_backends", lambda: False)
    listen = MagicMock()
    monkeypatch.setattr(daemon, "listen_always", listen)

    assert daemon.main() == 1
    listen.assert_not_called()


def test_shutdown_request_stops_owned_loop():
    daemon.daemon_running = True

    daemon.request_shutdown(signal.SIGTERM, None)

    assert daemon.daemon_running is False


def test_listener_dispatches_wake_then_active_and_stops(monkeypatch):
    calls = []
    daemon.sr = _speech_module()
    daemon.r = object()
    daemon.daemon_running = True
    daemon.hikari_state = daemon.HikariState.LISTENING

    def wake():
        calls.append("wake")
        daemon.hikari_state = daemon.HikariState.ACTIVE

    def active():
        calls.append("active")
        daemon.request_shutdown()

    monkeypatch.setattr(daemon, "_listen_for_wake_word", wake)
    monkeypatch.setattr(daemon, "_listen_for_active_command", active)

    daemon.listen_always()

    assert calls == ["wake", "active"]


def test_listener_continues_after_wait_timeout(monkeypatch):
    attempts = 0
    daemon.sr = _speech_module()
    daemon.r = object()
    daemon.daemon_running = True
    daemon.hikari_state = daemon.HikariState.LISTENING

    def wake():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise WaitTimeoutError
        daemon.request_shutdown()

    monkeypatch.setattr(daemon, "_listen_for_wake_word", wake)

    daemon.listen_always()

    assert attempts == 2


def test_verified_wake_phrase_enters_active_state(monkeypatch):
    daemon.sr = _speech_module()
    daemon.r = MagicMock()
    daemon.hikari_state = daemon.HikariState.LISTENING
    monkeypatch.setattr(
        daemon,
        "recognize_audio",
        lambda _audio, *, short_utterance=False: "hikari",
    )
    monkeypatch.setattr(daemon, "verify_speaker", lambda _audio: True)
    speak = MagicMock()
    monkeypatch.setattr(daemon, "speak", speak)

    daemon._listen_for_wake_word()

    assert daemon.hikari_state == daemon.HikariState.ACTIVE
    speak.assert_called_once_with("Go ahead!")


def test_verified_owner_can_interrupt_speech_immediately(monkeypatch):
    daemon.sr = _speech_module()
    daemon.r = MagicMock()
    daemon.daemon_running = True
    daemon.hikari_state = daemon.HikariState.ACTIVE
    monkeypatch.setattr(
        daemon,
        "recognize_audio",
        lambda _audio, *, short_utterance=False: "stop talking",
    )
    verify = MagicMock(return_value=False)
    monkeypatch.setattr(daemon, "verify_speaker", verify)

    class SpeechProcess:
        def __init__(self):
            self.running = True
            self.terminated = False

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.terminated = True
            self.running = False

        def wait(self, timeout=None):
            self.running = False
            return 0

        def kill(self):
            self.running = False

    process = SpeechProcess()
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *_args, **_kwargs: process)

    completed = daemon.speak("This response should be interrupted.")

    assert completed is False
    assert process.terminated is True
    assert daemon.hikari_state == daemon.HikariState.ACTIVE
    verify.assert_not_called()


def test_non_interrupt_follow_up_does_not_cut_off_active_speech(monkeypatch):
    daemon.sr = _speech_module()
    daemon.r = MagicMock()
    daemon.daemon_running = True
    monkeypatch.setattr(
        daemon,
        "recognize_audio",
        lambda _audio, *, short_utterance=False: "actually tell me the weather",
    )
    verify = MagicMock(return_value=True)
    monkeypatch.setattr(daemon, "verify_speaker", verify)

    process = MagicMock()
    process.poll.side_effect = [None, 0]

    assert daemon._wait_for_speech_or_owner_interrupt(process) is False
    process.terminate.assert_not_called()
    verify.assert_not_called()


def test_pocket_tts_process_uses_temporary_wav_and_cleans_it(monkeypatch, tmp_path):
    monkeypatch.setenv("HIKARI_TTS_BACKEND", "pocket-tts")
    monkeypatch.setenv("HIKARI_TTS_RATE", "185")

    adapter = daemon.PocketTTSAdapter()

    def render_wav(_text, output):
        Path(output).write_bytes(b"RIFF-test")

    monkeypatch.setattr(adapter, "render_wav", render_wav)
    monkeypatch.setattr(daemon, "_local_tts_adapter", adapter)
    process = MagicMock()
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(daemon.subprocess, "Popen", popen)

    returned, cleanup = daemon._start_speech_process("Hello, Sanjay.")

    assert returned is process
    argv = popen.call_args.args[0]
    assert argv[:3] == ["/usr/bin/afplay", "-r", "1.000"]
    output = Path(argv[3])
    assert output.read_bytes() == b"RIFF-test"

    cleanup()
    assert not output.exists()


def test_explicit_stop_does_not_wait_for_speaker_verification(monkeypatch):
    daemon.sr = _speech_module()
    daemon.r = MagicMock()
    daemon.daemon_running = True
    daemon.hikari_state = daemon.HikariState.ACTIVE
    monkeypatch.setattr(
        daemon,
        "recognize_audio",
        lambda _audio, *, short_utterance=False: "stop",
    )
    verify = MagicMock(return_value=False)
    monkeypatch.setattr(daemon, "verify_speaker", verify)

    class SpeechProcess:
        def __init__(self):
            self.polls = 0
            self.terminated = False

        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else 0

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    process = SpeechProcess()

    assert daemon._wait_for_speech_or_owner_interrupt(process) is True
    assert process.terminated is True
    verify.assert_not_called()


def test_stop_command_returns_active_daemon_to_listening(monkeypatch):
    daemon.sr = _speech_module()
    daemon.r = MagicMock()
    daemon.hikari_state = daemon.HikariState.ACTIVE
    monkeypatch.setattr(daemon, "verify_speaker", lambda _audio: True)
    monkeypatch.setattr(daemon, "recognize_audio", lambda _audio: "bye")
    speak = MagicMock()
    monkeypatch.setattr(daemon, "speak", speak)

    daemon._listen_for_active_command()

    assert daemon.hikari_state == daemon.HikariState.LISTENING
    speak.assert_called_once_with("Talk to you later!")


def test_main_registers_shutdown_signals_before_listening(monkeypatch):
    registered = {}
    monkeypatch.setattr(daemon, "initialize_audio_backends", lambda: True)
    monkeypatch.setattr(daemon, "SPEAKER_AUTH_AVAILABLE", True)
    auth = SimpleNamespace(is_enrolled=lambda: True, available=lambda: True)
    monkeypatch.setattr(daemon, "_get_speaker_auth", lambda: auth)
    monkeypatch.setattr(
        daemon.signal,
        "signal",
        lambda signum, handler: registered.__setitem__(signum, handler),
    )
    listen = MagicMock()
    monkeypatch.setattr(daemon, "listen_always", listen)
    monkeypatch.setattr(daemon.sys, "argv", ["hikari_daemon.py"])

    assert daemon.main() == 0
    assert registered == {
        signal.SIGINT: daemon.request_shutdown,
        signal.SIGTERM: daemon.request_shutdown,
    }
    listen.assert_called_once_with()


def test_unavailable_speaker_verification_never_starts_listener(monkeypatch):
    monkeypatch.setattr(daemon, "initialize_audio_backends", lambda: True)
    monkeypatch.setattr(daemon, "SPEAKER_AUTH_AVAILABLE", False)
    listen = MagicMock()
    monkeypatch.setattr(daemon, "listen_always", listen)
    monkeypatch.setattr(daemon.sys, "argv", ["hikari_daemon.py"])

    assert daemon.main() == 1
    listen.assert_not_called()


def test_missing_owner_enrollment_never_starts_listener(monkeypatch):
    monkeypatch.setattr(daemon, "initialize_audio_backends", lambda: True)
    monkeypatch.setattr(daemon, "SPEAKER_AUTH_AVAILABLE", True)
    auth = SimpleNamespace(is_enrolled=lambda: False, available=lambda: True)
    monkeypatch.setattr(daemon, "_get_speaker_auth", lambda: auth)
    listen = MagicMock()
    monkeypatch.setattr(daemon, "listen_always", listen)
    monkeypatch.setattr(daemon.sys, "argv", ["hikari_daemon.py"])

    assert daemon.main() == 2
    listen.assert_not_called()


def test_speaker_verification_fails_closed_without_enrollment(monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_AUTH_AVAILABLE", True)
    auth = SimpleNamespace(is_enrolled=lambda: False)
    monkeypatch.setattr(daemon, "_get_speaker_auth", lambda: auth)

    assert daemon.verify_speaker(object()) is False


def test_enrollment_checks_model_before_requesting_audio(monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_AUTH_AVAILABLE", True)
    monkeypatch.setattr(
        daemon,
        "SpeakerAuth",
        lambda: SimpleNamespace(available=lambda: False),
    )
    daemon.sr = _speech_module()
    microphone = MagicMock()
    daemon.sr.Microphone = microphone

    assert daemon.enroll_voice() is False
    microphone.assert_not_called()


def test_check_enrollment_does_not_initialize_microphone(monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_AUTH_AVAILABLE", True)
    monkeypatch.setattr(
        daemon,
        "_get_speaker_auth",
        lambda: SimpleNamespace(is_enrolled=lambda: True),
    )
    initialize = MagicMock()
    monkeypatch.setattr(daemon, "initialize_audio_backends", initialize)
    monkeypatch.setattr(daemon.sys, "argv", ["hikari_daemon.py", "--check-enrollment"])

    assert daemon.main() == 0
    initialize.assert_not_called()


def test_wake_phrase_requires_explicit_hikari_form():
    assert daemon._is_wake_phrase("hikari") is True
    assert daemon._is_wake_phrase("hey hikari") is True
    assert daemon._is_wake_phrase("Hikari.") is True
    assert daemon._is_wake_phrase("Hey, HIKARI!") is True
    assert daemon._is_wake_phrase("heck") is False
    assert daemon._is_wake_phrase("this has hikar somewhere") is False


def test_speech_interrupt_accepts_short_explicit_variants_only():
    for phrase in (
        "stop",
        "please stop",
        "stop talking",
        "Hikari, please stop talking",
        "be quiet please",
    ):
        assert daemon._is_speech_interrupt(phrase) is True

    for phrase in (
        "do not stop the timer",
        "tell me about stop motion",
        "actually answer the weather question",
    ):
        assert daemon._is_speech_interrupt(phrase) is False


def test_check_enrollment_is_silent(monkeypatch, capsys):
    monkeypatch.setattr(daemon, "SPEAKER_AUTH_AVAILABLE", True)
    monkeypatch.setattr(
        daemon,
        "_get_speaker_auth",
        lambda: SimpleNamespace(is_enrolled=lambda: True),
    )
    monkeypatch.setattr(daemon.sys, "argv", ["hikari_daemon.py", "--check-enrollment"])

    assert daemon.main() == 0
    assert capsys.readouterr().out == ""
