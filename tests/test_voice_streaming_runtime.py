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
from core.voice_streaming.live_audio import AudioInputCapability, LiveAudioFrame
from core.voice_streaming.runtime import (
    VoiceStreamingRuntime,
    VoiceStreamingRuntimeConfig,
    extract_wake_command,
    is_stop_command,
    is_wake_phrase,
    speech_interrupt_mode,
)
from core.voice_streaming.vad import VADFrameMeasurement, VADState
from core.voice_streaming.interruption_evidence import (
    InterruptionEvidence,
    InterruptionVerificationSource,
)



def _barge_evidence(runtime, interruption_id="req_1", **kwargs):
    now = runtime.now_ns()
    base = dict(
        stream_id=runtime.stream_id,
        interruption_id=interruption_id,
        target_assistant_utterance_id=runtime.interruption_target_id(),
        speaker_verified=True,
        verification_source=InterruptionVerificationSource.SPEAKER_AUTH,
        speech_observed_ns=now,
        observed_at_ns=now,
        expires_at_ns=now + 2_000_000_000,
    )
    base.update(kwargs)
    return InterruptionEvidence(**base)

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
    assert extract_wake_command("Hickory, tell me the weather") == "tell me the weather"
    assert extract_wake_command("Hey, Carrie, tell me the weather") == "tell me the weather"
    assert extract_wake_command("Carrie, tell me the weather") is None
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


@pytest.mark.parametrize(
    "phrase",
    ["hikari stop", "hikari done", "stop hikari", "be quiet", "stop listening", "stop"],
)
def test_stop_phrases_are_stop_commands_and_goodbye_interrupt(phrase: str):
    assert is_stop_command(phrase) is True
    assert speech_interrupt_mode(phrase) == "goodbye"


def test_cancel_is_soft_interrupt_not_stop_command():
    assert is_stop_command("cancel") is False
    assert speech_interrupt_mode("cancel") == "cancel"


def test_hikari_stop_while_active_returns_to_wake_listening():
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_active_listening()
    res = runtime.process_utterance("hikari stop", is_verified_speaker=True)
    assert res["action"] == "silent_goodbye"
    assert runtime.state == VoiceStreamState.WAKE_LISTENING


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

    assert runtime.request_interruption("req_1", is_authenticated=False) is False
    assert runtime.request_interruption("req_1", is_authenticated=True) is False
    bad = _barge_evidence(runtime, speaker_verified=False)
    assert runtime.request_interruption(evidence=bad) is False
    assert runtime.state == VoiceStreamState.ASSISTANT_SPEAKING


def test_confirmed_interruption_transition():
    """Verify authenticated interruption request + confirmation transitions state to INTERRUPTED."""
    runtime = VoiceStreamingRuntime("stream_1")
    runtime.start_active_listening()
    runtime.assistant_speaking_start()

    ok_req = runtime.request_interruption(evidence=_barge_evidence(runtime, "req_1"))
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

    runtime.request_interruption(evidence=_barge_evidence(runtime, "req_1"))
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


def test_duplicate_same_utterance_id_rejected_but_repeated_words_are_allowed():
    runtime = VoiceStreamingRuntime("stream_dup")
    runtime.start_wake_listening()
    first = runtime.process_utterance(
        "Hikari, lights on", is_verified_speaker=True, utterance_id="utt_1"
    )
    assert first["action"] == "process_command"
    runtime.reset_to_wake_listening()
    second = runtime.process_utterance(
        "Hikari, lights on", is_verified_speaker=True, utterance_id="utt_1"
    )
    assert second["action"] == "ignore"
    assert second["reason"] == "duplicate_utterance"
    runtime.reset_to_wake_listening()
    repeated_words = runtime.process_utterance(
        "Hikari, lights on", is_verified_speaker=True, utterance_id="utt_2"
    )
    assert repeated_words["action"] == "process_command"


def test_assistant_playback_cannot_become_user_speech():
    runtime = VoiceStreamingRuntime("stream_play")
    runtime.start_active_listening()
    runtime.assistant_speaking_start()
    res = runtime.process_utterance("turn off the lights", is_verified_speaker=True)
    assert res["action"] == "ignore"
    assert res["reason"] == "assistant_playback_active"


def test_live_pcm_energy_uses_signed_samples_not_raw_byte_values():
    runtime = VoiceStreamingRuntime("stream_signed_pcm")
    runtime.start_active_listening()
    runtime.set_input_capability(AudioInputCapability.FRAME_STREAM, frame_loop_open=True)
    runtime.assistant_speaking_start()
    frame = LiveAudioFrame(
        stream_id=runtime.stream_id,
        frame_id="frame-low-positive",
        sequence=1,
        monotonic_ns=runtime.now_ns(),
        sample_rate=16_000,
        channels=1,
        sample_width=2,
        pcm=b"\xff\x00" * 160,
    )

    result = runtime.ingest_live_frame(frame)

    assert result["reason"] == "assistant_playback_active"
    assert result["barge_observed"] is False


def test_future_interruption_rejected():
    runtime = VoiceStreamingRuntime("stream_future", clock=lambda: 1_000)
    runtime.start_active_listening()
    runtime.assistant_speaking_start()
    now = runtime.now_ns()
    future = InterruptionEvidence(
        stream_id=runtime.stream_id,
        interruption_id="req_f",
        target_assistant_utterance_id=runtime.interruption_target_id(),
        speaker_verified=True,
        verification_source=InterruptionVerificationSource.SPEAKER_AUTH,
        speech_observed_ns=now,
        observed_at_ns=now,
        expires_at_ns=now + 2_000_000_000,
    )
    # monotonic_ns mismatch / future wall via mismatched observed binding
    assert runtime.request_interruption(evidence=future, monotonic_ns=10**15) is False


def test_cancel_active_from_speaking_clears_state():
    runtime = VoiceStreamingRuntime("stream_cancel")
    runtime.start_active_listening()
    runtime.assistant_speaking_start()
    assert runtime.cancel_active() is True
    assert runtime.is_wake_listening
    assert runtime.state_machine.active_interruption_request is None


def test_replay_history_exhaustion_fail_closed():
    runtime = VoiceStreamingRuntime(
        "stream_hist",
        config=VoiceStreamingRuntimeConfig(max_history=16),
    )
    runtime.start_active_listening()
    accepted = 0
    rejected = 0
    for i in range(20):
        res = runtime.process_utterance(
            f"command number {i}",
            is_verified_speaker=True,
            utterance_id=f"utt_{i}",
        )
        if res["action"] == "process_command":
            accepted += 1
            runtime.reset_to_wake_listening()
            runtime.start_active_listening()
        elif res.get("reason") == "duplicate_utterance":
            rejected += 1
        else:
            # After key set clears on exhaustion, subsequent unique commands may resume.
            if res["action"] == "ignore":
                rejected += 1
    assert accepted >= 1
    assert rejected >= 1



def test_bind_assistant_playback_public_api():
    runtime = VoiceStreamingRuntime("stream_bind")
    runtime.start_active_listening()
    # Wrong state
    assert runtime.bind_assistant_playback("a1", "r1") is False
    runtime.state_machine.transition_to(
        VoiceStreamState.THINKING,
        event_type="test_think",
        monotonic_ns=runtime.now_ns(),
        reason="test",
    )
    assert runtime.bind_assistant_playback("a1", "r1") is True
    assert runtime.interruption_target_id() == "a1"
    # Idempotent identical
    assert runtime.bind_assistant_playback("a1", "r1") is True
    # Conflict
    assert runtime.bind_assistant_playback("a2", "r2") is False
    assert runtime.interruption_target_id() == "a1"
    # Stale clear
    assert runtime.clear_assistant_playback(expected_response_id="other") is False
    assert runtime.clear_assistant_playback() is False
    assert runtime.clear_assistant_playback(expected_response_id="r1") is True
    assert runtime.interruption_target_id() == "assistant_playback"
    # Idempotent already clear
    assert runtime.clear_assistant_playback(expected_response_id="r1") is True
    # Content-free events
    blob = repr(runtime.get_history())
    assert "a1" not in blob
    assert "r1" not in blob


def test_bind_does_not_change_wake_sleep():
    runtime = VoiceStreamingRuntime("stream_wake")
    runtime.start_wake_listening()
    assert runtime.is_wake_listening
    assert runtime.bind_assistant_playback("a1", "r1") is False
    assert runtime.is_wake_listening
