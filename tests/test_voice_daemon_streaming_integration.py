"""Daemon + canonical VoiceStreamingRuntime integration (no live mic)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.streaming_voice import AecStatus, TurnState, TurnStateMachine
from core.voice_streaming.contracts import VoiceStreamState
from core.voice_streaming.runtime import VoiceStreamingRuntime, extract_wake_command
from services import hikari_daemon as daemon


def _speech_module():
    module = MagicMock()
    module.WaitTimeoutError = TimeoutError
    module.UnknownValueError = ValueError
    return module


@pytest.fixture(autouse=True)
def _reset_daemon_runtime(monkeypatch):
    daemon._streaming_runtime = None
    daemon._time_sense_bridge = None
    daemon._voice_audio_loop = None
    daemon._time_sense_coordinator = None
    daemon._utterance_seq = 0
    daemon._capture_mode = "utterance_only"
    daemon.hikari_state = daemon.HikariState.LISTENING
    yield
    daemon._streaming_runtime = None
    daemon._time_sense_bridge = None
    daemon._voice_audio_loop = None
    daemon._time_sense_coordinator = None
    daemon._utterance_seq = 0
    daemon._capture_mode = "utterance_only"
    daemon.hikari_state = daemon.HikariState.LISTENING


def test_ordinary_sleeping_speech_never_reaches_process(monkeypatch):
    daemon.sr = _speech_module()
    daemon.r = MagicMock()
    monkeypatch.setattr(
        daemon, "recognize_audio", lambda _a, *, short_utterance=False: "what time is it"
    )
    verify = MagicMock(return_value=True)
    process = MagicMock(return_value="should-not-run")
    speak = MagicMock()
    monkeypatch.setattr(daemon, "verify_speaker", verify)
    monkeypatch.setattr(daemon, "process", process)
    monkeypatch.setattr(daemon, "speak", speak)

    daemon._listen_for_wake_word()

    verify.assert_not_called()
    process.assert_not_called()
    speak.assert_not_called()
    assert daemon.hikari_state == daemon.HikariState.LISTENING


def test_unverified_speaker_cannot_wake(monkeypatch):
    daemon.sr = _speech_module()
    daemon.r = MagicMock()
    monkeypatch.setattr(
        daemon, "recognize_audio", lambda _a, *, short_utterance=False: "hikari"
    )
    monkeypatch.setattr(daemon, "verify_speaker", lambda _a: False)
    process = MagicMock()
    speak = MagicMock()
    monkeypatch.setattr(daemon, "process", process)
    monkeypatch.setattr(daemon, "speak", speak)

    daemon._listen_for_wake_word()

    process.assert_not_called()
    speak.assert_not_called()
    assert daemon._get_streaming_runtime().is_wake_listening


def test_bare_verified_wake_activates_without_process(monkeypatch):
    daemon.sr = _speech_module()
    daemon.r = MagicMock()
    monkeypatch.setattr(
        daemon, "recognize_audio", lambda _a, *, short_utterance=False: "hikari"
    )
    monkeypatch.setattr(daemon, "verify_speaker", lambda _a: True)
    process = MagicMock()
    speak = MagicMock()
    monkeypatch.setattr(daemon, "process", process)
    monkeypatch.setattr(daemon, "speak", speak)

    daemon._listen_for_wake_word()

    process.assert_not_called()
    speak.assert_called_once_with("Yes?", allow_interrupt=False)
    assert daemon.hikari_state == daemon.HikariState.ACTIVE
    assert daemon._get_streaming_runtime().state == VoiceStreamState.ACTIVE_LISTENING


def test_same_utterance_wake_command_executes_once(monkeypatch):
    daemon.sr = _speech_module()
    daemon.r = MagicMock()
    monkeypatch.setattr(
        daemon,
        "recognize_audio",
        lambda _a, *, short_utterance=False: "Hikari, what time is it?",
    )
    monkeypatch.setattr(daemon, "verify_speaker", lambda _a: True)
    process = MagicMock(return_value="3pm")
    speak = MagicMock()
    log = MagicMock()
    monkeypatch.setattr(daemon, "process", process)
    monkeypatch.setattr(daemon, "speak", speak)
    monkeypatch.setattr(daemon, "log_convo", log)

    daemon._listen_for_wake_word()

    process.assert_called_once_with("what time is it?")
    speak.assert_called_once_with("3pm")
    log.assert_called_once()


def test_goodbye_returns_sleeping_without_process_or_speak(monkeypatch):
    daemon.sr = _speech_module()
    daemon.r = MagicMock()
    daemon.hikari_state = daemon.HikariState.ACTIVE
    runtime = daemon._get_streaming_runtime()
    runtime.start_active_listening()
    monkeypatch.setattr(daemon, "verify_speaker", lambda _a: True)
    monkeypatch.setattr(daemon, "recognize_audio", lambda _a: "goodbye")
    process = MagicMock()
    speak = MagicMock()
    monkeypatch.setattr(daemon, "process", process)
    monkeypatch.setattr(daemon, "speak", speak)

    daemon._listen_for_active_command()

    process.assert_not_called()
    speak.assert_not_called()
    assert daemon.hikari_state == daemon.HikariState.LISTENING
    assert runtime.is_wake_listening


def test_wake_possible_after_goodbye(monkeypatch):
    runtime = daemon._get_streaming_runtime()
    runtime.start_active_listening()
    assert runtime.process_utterance("bye", is_verified_speaker=True)["action"] == "silent_goodbye"
    assert runtime.is_wake_listening
    res = runtime.process_utterance("hikari", is_verified_speaker=True)
    assert res["action"] == "acknowledge"


def test_hickory_alias_characterization_unchanged():
    assert daemon._extract_wake_command("hickory") == ""
    assert daemon._extract_wake_command("Hickory") == ""
    assert extract_wake_command("hickory") == ""
    assert daemon._extract_wake_command("hickory, do something") is None


def test_facade_shares_canonical_runtime_authority():
    tm = TurnStateMachine("daemon_stream", lambda: 1.0)
    assert isinstance(tm.canonical_runtime, VoiceStreamingRuntime)
    assert tm.state == TurnState.SLEEPING
    assert tm.canonical_runtime.is_wake_listening


def test_aec_unavailable_half_duplex():
    runtime = VoiceStreamingRuntime("s1")
    runtime.report_aec_status(AecStatus.UNAVAILABLE)
    assert runtime.duplex_mode() == "half_duplex"
    assert runtime.echo_cancellation_active() is False
    runtime.report_aec_status(AecStatus.DEGRADED, vendor_label="platform")
    assert runtime.duplex_mode() == "half_duplex"
    assert runtime.echo_cancellation_active() is False


def test_content_free_summary_has_no_transcript():
    runtime = VoiceStreamingRuntime("s1")
    runtime.start_active_listening()
    runtime.process_utterance("set a timer", is_verified_speaker=True)
    summary = runtime.content_free_summary()
    blob = repr(summary)
    assert "set a timer" not in blob
    assert "transcript" not in blob


def test_shutdown_cancels_and_clears(monkeypatch):
    runtime = daemon._get_streaming_runtime()
    runtime.start_active_listening()
    daemon.request_shutdown()
    assert daemon._streaming_runtime is None


def test_daemon_defaults_to_utterance_mode():
    runtime = daemon._get_streaming_runtime()
    assert daemon.get_voice_capture_mode() == "utterance_only"
    assert runtime.input_capability.value == "utterance_only"


def test_shutdown_clears_utterance_and_timing_state():
    daemon._get_streaming_runtime()
    daemon._next_utterance_id()
    daemon._get_time_sense_coordinator()
    daemon.request_shutdown()
    assert daemon._streaming_runtime is None
    assert daemon._utterance_seq == 0
    assert daemon._capture_mode == "utterance_only"


def test_package_presence_cannot_force_frame_stream(monkeypatch):
    """Installed audio package must not flip capture mode to frame_stream."""
    class FakeFrameSource:
        @property
        def capability(self):
            from core.voice_streaming.live_audio import AudioInputCapability
            return AudioInputCapability.FRAME_STREAM

    monkeypatch.setattr(
        "core.voice_streaming.live_audio.try_create_pyaudio_source",
        lambda **kwargs: FakeFrameSource(),
    )
    # Also patch where daemon imports from.
    import core.voice_streaming.live_audio as la
    monkeypatch.setattr(la, "try_create_pyaudio_source", lambda **kwargs: FakeFrameSource())
    runtime = daemon._get_streaming_runtime()
    mode = daemon._resolve_capture_mode(runtime)
    assert mode == "utterance_only"
    assert runtime.input_capability.value == "utterance_only"
    assert daemon.get_voice_capture_mode() == "utterance_only"


def test_daemon_uses_public_set_input_capability():
    runtime = daemon._get_streaming_runtime()
    result = runtime.set_input_capability(
        __import__("core.voice_streaming.live_audio", fromlist=["AudioInputCapability"]).AudioInputCapability.FRAME_STREAM,
        frame_loop_open=False,
    )
    assert result["capability"] == "utterance_only"
