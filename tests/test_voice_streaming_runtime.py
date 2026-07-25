"""Tests for VoiceStreamingRuntime adapter and daemon integration boundaries."""

import pytest
from unittest.mock import MagicMock

from core.voice_streaming.contracts import (
    FinalTranscript,
    InterimTranscript,
    VoiceStreamState,
)
from core.voice_streaming.echo_policy import (
    EchoCapability,
    EchoMode,
    EchoPolicyContext,
)
from core.voice_streaming.frame_pipeline import AudioFrame, AudioFrameMetadata
from core.voice_streaming.runtime import (
    VoiceStreamingRuntime,
    VoiceStreamingRuntimeConfig,
    extract_wake_command,
    is_stop_command,
    is_wake_phrase,
)
from core.voice_streaming.vad import VADFrameMeasurement, VADState


def test_runtime_construction_has_no_io():
    """Verify runtime instantiation performs zero file/network/microphone I/O."""
    runtime = VoiceStreamingRuntime("test_stream")
    assert runtime.stream_id == "test_stream"
    assert runtime.state == VoiceStreamState.IDLE
    assert len(runtime.get_history()) == 0


def test_runtime_config_rejects_unbounded_history():
    with pytest.raises(ValueError, match="max_history"):
        VoiceStreamingRuntimeConfig(max_history=0)


def test_wake_phrase_matching_strictness():
    """Verify strict wake phrase matching without fuzzy or substring hallucinations."""
    assert is_wake_phrase("hikari") is True
    assert is_wake_phrase("hey hikari") is True
    assert is_wake_phrase("Hikari.") is True
    assert is_wake_phrase("Hey, HIKARI!") is True

    # Rejections
    assert is_wake_phrase("heck") is False
    assert is_wake_phrase("this mentions hikari later") is False
    assert is_wake_phrase("hikar") is False


def test_same_utterance_wake_command_extraction():
    """Verify same-utterance wake commands extract trailing phrase exactly."""
    assert extract_wake_command("Hikari, who won the game?") == "who won the game?"
    assert extract_wake_command("Hey Hikari tell me the weather") == "tell me the weather"
    assert extract_wake_command("Hikari") == ""
    assert extract_wake_command("this mentions hikari later") is None


def test_ordinary_sleeping_speech_ignored():
    """Verify speech without wake prefix is ignored while in passive wake-listening mode."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_wake_listening()
    assert runtime.is_wake_listening is True

    res = runtime.process_utterance("what is the weather today?", is_verified_speaker=True)
    assert res["action"] == "ignore"
    assert res["reason"] == "no_wake_prefix"
    assert runtime.is_wake_listening is True


def test_unauthenticated_wake_rejection():
    """Verify unauthenticated speaker saying wake word is ignored."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_wake_listening()

    res = runtime.process_utterance("hikari", is_verified_speaker=False)
    assert res["action"] == "ignore"
    assert res["reason"] == "unverified_speaker"
    assert runtime.is_wake_listening is True
    assert all("hikari" not in repr(event.details).casefold() for event in runtime.get_history())


def test_exact_wake_activation():
    """Verify bare wake activation transitions to ACTIVE_LISTENING and returns acknowledge action."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_wake_listening()

    res = runtime.process_utterance("hikari", is_verified_speaker=True)
    assert res["action"] == "acknowledge"
    assert res["response"] == "Yes?"
    assert runtime.state == VoiceStreamState.ACTIVE_LISTENING


def test_same_utterance_wake_command_activation():
    """Verify same-utterance wake command transitions to ACTIVE and returns process_command action."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_wake_listening()

    res = runtime.process_utterance("Hey Hikari, what time is it?", is_verified_speaker=True)
    assert res["action"] == "process_command"
    assert res["command"] == "what time is it?"
    assert runtime.state == VoiceStreamState.THINKING


def test_goodbye_is_silent_and_returns_to_wake_listening():
    """Verify goodbye command in active mode resets to passive wake listening silently."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_active_listening()
    assert runtime.is_active_listening is True

    res = runtime.process_utterance("goodbye", is_verified_speaker=True)
    assert res["action"] == "silent_goodbye"
    assert runtime.state == VoiceStreamState.WAKE_LISTENING

    # Active transcript state cleared
    assert runtime.accumulator.current_interim is None


def test_repeated_goodbye_while_sleeping_ignored():
    """Verify goodbye command while sleeping is ignored."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_wake_listening()

    res = runtime.process_utterance("goodbye", is_verified_speaker=True)
    assert res["action"] == "ignore"
    assert res["reason"] == "no_wake_prefix"
    assert runtime.is_wake_listening is True


def test_unauthenticated_barge_in_denied():
    """Verify unauthenticated barge-in request is rejected."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_active_listening()
    runtime.assistant_speaking_start()
    assert runtime.state == VoiceStreamState.ASSISTANT_SPEAKING

    # Unauthenticated interruption request -> False
    ok = runtime.request_interruption("req_1", is_authenticated=False)
    assert ok is False
    assert runtime.state == VoiceStreamState.ASSISTANT_SPEAKING


def test_confirmed_interruption_transition():
    """Verify authenticated interruption request + confirmation transitions state to INTERRUPTED."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_active_listening()
    runtime.assistant_speaking_start()

    ok_req = runtime.request_interruption("req_1", is_authenticated=True)
    assert ok_req is True
    assert runtime.state == VoiceStreamState.INTERRUPTING

    ok_conf = runtime.confirm_interruption("req_1", is_confirmed=True)
    assert ok_conf is True
    assert runtime.state == VoiceStreamState.INTERRUPTED


def test_stale_interruption_rejection():
    """Verify confirmation with mismatched request ID is rejected."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_active_listening()
    runtime.assistant_speaking_start()

    runtime.request_interruption("req_1", is_authenticated=True)
    assert runtime.state == VoiceStreamState.INTERRUPTING

    # Mismatched request_id -> False
    ok_stale = runtime.confirm_interruption("req_STALE", is_confirmed=True)
    assert ok_stale is False
    assert runtime.state == VoiceStreamState.INTERRUPTING


def test_echo_policy_evaluation():
    """Verify echo policy evaluator integration with unverified vs verified capabilities."""
    runtime = VoiceStreamingRuntime("stream_1")

    # Unverified AEC -> half-duplex / non-full-duplex
    runtime.set_echo_capability(EchoCapability(native_aec_available=True, native_aec_verified=False))
    decision = runtime.evaluate_echo_policy()
    assert decision.full_duplex_safe is False
    assert decision.selected_mode != EchoMode.NATIVE_AEC_ACTIVE

    # Verified AEC -> full-duplex safe
    runtime.set_echo_capability(EchoCapability(native_aec_available=True, native_aec_verified=True))
    decision_v = runtime.evaluate_echo_policy()
    assert decision_v.full_duplex_safe is True
    assert decision_v.selected_mode == EchoMode.NATIVE_AEC_ACTIVE


def test_raw_audio_redaction():
    """Verify raw frame payload is excluded from runtime repr/events."""
    runtime = VoiceStreamingRuntime("stream_1")
    payload = b"\x00\x01" * 320
    meta = AudioFrameMetadata("stream_1", 0, 1000, payload_bytes=640)
    frame = AudioFrame(meta, payload)

    runtime.process_audio_frame(frame)
    metrics = runtime.get_metrics()

    metrics_str = str(metrics)
    assert "stream_1" in metrics_str
    assert str(payload) not in metrics_str


def test_runtime_reset_and_close():
    """Verify reset and close restore runtime to clean passive state."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_active_listening()

    runtime.reset()
    assert runtime.state == VoiceStreamState.IDLE

    runtime.start_wake_listening()
    runtime.close()
    assert runtime.is_wake_listening is True
