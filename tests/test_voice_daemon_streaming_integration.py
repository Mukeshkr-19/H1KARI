"""Daemon + canonical VoiceStreamingRuntime integration (no live mic)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
import time

import pytest

from core.streaming_voice import AecStatus, TurnState, TurnStateMachine
from core.voice_streaming.contracts import VoiceStreamState
from core.voice_streaming.live_audio import (
    AudioFrameSourceReason,
    AudioFrameSourceResult,
    AudioInputCapability,
    CaptureSourceCategory,
    LiveAudioFrame,
)
from core.voice_streaming.runtime import VoiceStreamingRuntime, extract_wake_command
from services import hikari_daemon as daemon


def _speech_module():
    module = MagicMock()
    module.WaitTimeoutError = TimeoutError
    module.UnknownValueError = ValueError
    return module


@pytest.fixture(autouse=True)
def _reset_daemon_runtime(monkeypatch):
    monkeypatch.setenv("HIKARI_VOICE_CAPTURE_BACKEND", "utterance-only")
    daemon._streaming_runtime = None
    daemon._time_sense_bridge = None
    daemon._voice_audio_loop = None
    daemon._frame_endpoint_gate = None
    daemon._barge_endpoint_gate = None
    daemon._playback_controller = None
    daemon._time_sense_coordinator = None
    daemon._utterance_seq = 0
    daemon._capture_mode = "utterance_only"
    daemon._active_last_activity_ns = 0
    daemon.daemon_running = True
    daemon.hikari_state = daemon.HikariState.LISTENING
    yield
    daemon._streaming_runtime = None
    daemon._time_sense_bridge = None
    daemon._voice_audio_loop = None
    daemon._frame_endpoint_gate = None
    daemon._barge_endpoint_gate = None
    daemon._playback_controller = None
    daemon._time_sense_coordinator = None
    daemon._utterance_seq = 0
    daemon._capture_mode = "utterance_only"
    daemon._active_last_activity_ns = 0
    daemon.daemon_running = True
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
    assert daemon._extract_wake_command("hickory, do something") == "do something"


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


def test_explicit_utterance_backend_stays_utterance_mode():
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


def test_unconfigured_capture_backend_selects_auto(monkeypatch):
    monkeypatch.delenv("HIKARI_VOICE_CAPTURE_BACKEND", raising=False)
    assert daemon._selected_capture_backend() == "auto"


def test_daemon_runtime_uses_host_monotonic_clock(monkeypatch):
    monkeypatch.setenv("HIKARI_VOICE_CAPTURE_BACKEND", "utterance-only")
    before = time.monotonic_ns()
    runtime = daemon._get_streaming_runtime()
    observed = runtime.now_ns()
    after = time.monotonic_ns()
    assert before <= observed <= after


def test_daemon_uses_public_set_input_capability():
    runtime = daemon._get_streaming_runtime()
    result = runtime.set_input_capability(
        __import__("core.voice_streaming.live_audio", fromlist=["AudioInputCapability"]).AudioInputCapability.FRAME_STREAM,
        frame_loop_open=False,
    )
    assert result["capability"] == "utterance_only"


def test_pcm_speaker_verification_missing_auth_fails_closed(monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_AUTH_AVAILABLE", False)
    assert daemon._verify_speaker_pcm(b"\x00\x00" * 8_000, utterance_id="u1") is False


def test_wake_activation_requires_stronger_owner_match(monkeypatch):
    class Auth:
        @staticmethod
        def is_enrolled():
            return True

        @staticmethod
        def verify_pcm16_mono(_pcm, *, utterance_id):
            assert utterance_id in {"wake-1", "active-1"}
            return SimpleNamespace(ok=True, score=0.30, threshold=0.25, reason="matched")

    monkeypatch.setattr(daemon, "SPEAKER_AUTH_AVAILABLE", True)
    monkeypatch.setattr(daemon, "_get_speaker_auth", lambda: Auth())
    monkeypatch.delenv("HIKARI_VOICE_WAKE_MIN_SCORE", raising=False)

    assert daemon._verify_speaker_pcm(
        b"\x00\x00" * 8_000,
        utterance_id="wake-1",
        wake_activation=True,
    ) is False
    assert daemon._verify_speaker_pcm(
        b"\x00\x00" * 8_000,
        utterance_id="active-1",
        wake_activation=False,
    ) is True


def test_active_conversation_sleeps_after_long_idle(monkeypatch):
    class Clock:
        now = 1_000_000_000

        def __call__(self):
            return self.now

    clock = Clock()
    runtime = VoiceStreamingRuntime("daemon_stream", clock=clock)
    runtime.start_wake_listening()
    runtime.start_active_listening()
    daemon.hikari_state = daemon.HikariState.ACTIVE
    monkeypatch.setenv("HIKARI_VOICE_ACTIVE_IDLE_SECONDS", "120")

    daemon._mark_active_voice_activity(runtime)
    clock.now += 119_000_000_000
    assert daemon._sleep_active_session_if_idle(runtime) is False
    assert daemon.hikari_state == daemon.HikariState.ACTIVE

    clock.now += 2_000_000_000
    assert daemon._sleep_active_session_if_idle(runtime) is True
    assert daemon.hikari_state == daemon.HikariState.LISTENING
    assert runtime.is_wake_listening


def test_frame_stream_barge_requests_before_physical_confirmation(monkeypatch):
    runtime = VoiceStreamingRuntime("daemon_stream")
    runtime.start_active_listening()
    runtime.process_utterance("tell me a story", is_verified_speaker=True, utterance_id="user-1")
    assert runtime.assistant_speaking_start()
    runtime.set_input_capability(AudioInputCapability.FRAME_STREAM, frame_loop_open=True)
    daemon._streaming_runtime = runtime
    daemon._capture_mode = "frame_stream"
    time.sleep(0.002)

    frame = LiveAudioFrame(
        stream_id="daemon_stream",
        frame_id="frame-1",
        sequence=1,
        monotonic_ns=runtime.now_ns(),
        sample_rate=16_000,
        channels=1,
        sample_width=2,
        pcm=b"\xff\x7f" * 8_000,
        capture_source=CaptureSourceCategory.MICROPHONE,
    )

    class Loop:
        def pull(self):
            return AudioFrameSourceResult(True, AudioFrameSourceReason.OK, frame)

    class Gate:
        def reset(self):
            return None

        def process_frame(self, *_args, **_kwargs):
            return SimpleNamespace(
                event=SimpleNamespace(value="finalized"),
                utterance_pcm=b"\xff\x7f" * 8_000,
            )

    process = MagicMock()
    process.poll.return_value = None
    daemon._voice_audio_loop = Loop()
    daemon._barge_endpoint_gate = Gate()
    monkeypatch.setattr(daemon, "_transcribe_pcm_utterance", lambda *_a, **_k: "stop")
    monkeypatch.setattr(daemon, "_verify_speaker_pcm", lambda *_a, **_k: True)

    pending = daemon._wait_for_frame_stream_owner_interrupt(process)

    assert pending is not None
    mode, interruption_id = pending
    assert mode == "stop"
    assert interruption_id.startswith("barge_in_")
    assert runtime.state == VoiceStreamState.INTERRUPTING
    assert runtime.confirm_interruption(interruption_id, is_confirmed=True)
    assert runtime.state == VoiceStreamState.INTERRUPTED
