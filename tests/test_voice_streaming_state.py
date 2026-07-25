"""Tests for streaming voice pipeline state machine."""

import pytest

from core.voice_streaming.contracts import (
    AECCapability,
    FinalTranscript,
    InterimTranscript,
    InterruptionConfirmation,
    InterruptionRequest,
    StreamingVoiceFailure,
    VADCapability,
    VADEvent,
    VerifiedWakeEvent,
    VoiceStreamState,
)
from core.voice_streaming.state import VoiceStreamStateMachine, is_valid_transition


def test_idle_to_wake_listening():
    """Test 1: Idle to wake-listening."""
    sm = VoiceStreamStateMachine("stream_1")
    assert sm.current_state == VoiceStreamState.IDLE

    assert sm.start_wake_listening(monotonic_ns=100) is True
    assert sm.current_state == VoiceStreamState.WAKE_LISTENING


def test_wake_listening_ignores_ordinary_command():
    """Test 2: Wake-listening ignores ordinary command event."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_wake_listening(monotonic_ns=100)
    assert sm.current_state == VoiceStreamState.WAKE_LISTENING

    # Attempt ordinary command interim transcript while in passive WAKE_LISTENING
    interim = InterimTranscript("stream_1", "turn off lights", monotonic_ns=200)
    assert sm.on_interim_transcript(interim) is False
    assert sm.current_state == VoiceStreamState.WAKE_LISTENING

    # Attempt ordinary final transcript
    final = FinalTranscript("stream_1", "turn off lights", 200, 300)
    assert sm.on_final_transcript(final) is False
    assert sm.current_state == VoiceStreamState.WAKE_LISTENING


def test_verified_wake_activation():
    """Test 3: Verified wake activation."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_wake_listening(monotonic_ns=100)

    # Unverified wake event rejected
    unverified = VerifiedWakeEvent("stream_1", "hey hikari", 0.9, 150, is_verified=False)
    assert sm.on_wake_event(unverified) is False
    assert sm.current_state == VoiceStreamState.WAKE_LISTENING

    # Verified wake event accepted -> ACTIVE_LISTENING
    verified = VerifiedWakeEvent("stream_1", "hey hikari", 0.95, 200, is_verified=True)
    assert sm.on_wake_event(verified) is True
    assert sm.current_state == VoiceStreamState.ACTIVE_LISTENING


def test_active_listening_to_user_speaking():
    """Test 4: Active-listening to user-speaking."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    assert sm.current_state == VoiceStreamState.ACTIVE_LISTENING

    vad = VADEvent("stream_1", is_speech=True, confidence=0.9, monotonic_ns=150)
    assert sm.on_vad_event(vad) is True
    assert sm.current_state == VoiceStreamState.USER_SPEAKING


def test_speech_end_to_finalizing():
    """Test 5: Speech-end to finalizing."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    sm.on_vad_event(VADEvent("stream_1", is_speech=True, confidence=0.9, monotonic_ns=150))
    assert sm.current_state == VoiceStreamState.USER_SPEAKING

    vad_end = VADEvent("stream_1", is_speech=False, confidence=0.9, monotonic_ns=250)
    assert sm.on_vad_event(vad_end) is True
    assert sm.current_state == VoiceStreamState.FINALIZING_USER_TURN


def test_thinking_to_assistant_speaking():
    """Test 6: Thinking to assistant-speaking."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    final = FinalTranscript("stream_1", "What is the weather?", 150, 250)
    assert sm.on_final_transcript(final) is True
    assert sm.current_state == VoiceStreamState.THINKING

    assert sm.assistant_speaking_start(monotonic_ns=300) is True
    assert sm.current_state == VoiceStreamState.ASSISTANT_SPEAKING


def test_silent_goodbye_to_wake_listening():
    """Test 7: Silent goodbye to wake-listening."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    sm.assistant_thinking(monotonic_ns=200)
    assert sm.current_state == VoiceStreamState.THINKING

    # Silent goodbye transitions directly to WAKE_LISTENING
    assert sm.silent_goodbye(monotonic_ns=300) is True
    assert sm.current_state == VoiceStreamState.WAKE_LISTENING


def test_invalid_transition_rejection():
    """Test 8: Invalid transition rejection."""
    sm = VoiceStreamStateMachine("stream_1")
    assert sm.current_state == VoiceStreamState.IDLE

    # Direct jump from IDLE to INTERRUPTED is invalid
    assert is_valid_transition(VoiceStreamState.IDLE, VoiceStreamState.INTERRUPTED) is False
    assert (
        sm.transition_to(
            VoiceStreamState.INTERRUPTED,
            event_type="invalid_jump",
            monotonic_ns=100,
            reason="invalid",
        )
        is False
    )
    assert sm.current_state == VoiceStreamState.IDLE


def test_backward_timestamp_rejection():
    """Test 9: Backward timestamp rejection."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=500)
    assert sm.last_monotonic_ns == 500

    # Event with earlier timestamp (400 < 500) rejected
    assert (
        sm.transition_to(
            VoiceStreamState.USER_SPEAKING,
            event_type="test",
            monotonic_ns=400,
            reason="backward_time",
        )
        is False
    )
    assert sm.current_state == VoiceStreamState.ACTIVE_LISTENING
    assert sm.last_monotonic_ns == 500


def test_authenticated_barge_in():
    """Test 10: Authenticated barge-in."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    sm.assistant_thinking(monotonic_ns=200)
    sm.assistant_speaking_start(monotonic_ns=300)
    assert sm.current_state == VoiceStreamState.ASSISTANT_SPEAKING

    # Authenticated interruption request accepted -> INTERRUPTING
    req = InterruptionRequest("stream_1", "req_1", monotonic_ns=350, is_authenticated=True)
    assert sm.request_interruption(req) is True
    assert sm.current_state == VoiceStreamState.INTERRUPTING


def test_unauthenticated_barge_in_rejection():
    """Test 11: Unauthenticated barge-in rejection."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    sm.assistant_thinking(monotonic_ns=200)
    sm.assistant_speaking_start(monotonic_ns=300)
    assert sm.current_state == VoiceStreamState.ASSISTANT_SPEAKING

    # Unauthenticated interruption request rejected!
    unauth_req = InterruptionRequest(
        "stream_1", "req_1", monotonic_ns=350, is_authenticated=False
    )
    assert sm.request_interruption(unauth_req) is False
    assert sm.current_state == VoiceStreamState.ASSISTANT_SPEAKING


def test_noise_rejection():
    """Test 12: Noise rejection."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    sm.assistant_thinking(monotonic_ns=200)
    sm.assistant_speaking_start(monotonic_ns=300)

    # Ambient noise does not trigger barge-in or state change
    assert sm.on_noise_event(monotonic_ns=350, db_level=85.0) is False
    assert sm.current_state == VoiceStreamState.ASSISTANT_SPEAKING


def test_interruption_request_vs_confirmation():
    """Test 13: Interruption request versus confirmation."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    sm.assistant_thinking(monotonic_ns=200)
    sm.assistant_speaking_start(monotonic_ns=300)

    # 1. Request interruption -> INTERRUPTING (does not claim physical playback stopped yet)
    req = InterruptionRequest("stream_1", "req_42", monotonic_ns=350, is_authenticated=True)
    assert sm.request_interruption(req) is True
    assert sm.current_state == VoiceStreamState.INTERRUPTING

    # 2. Confirmation arrives -> INTERRUPTED
    conf = InterruptionConfirmation("stream_1", "req_42", monotonic_ns=400, is_confirmed=True)
    assert sm.confirm_interruption(conf) is True
    assert sm.current_state == VoiceStreamState.INTERRUPTED


def test_error_transition():
    """Test 14: Error transition."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)

    fail = StreamingVoiceFailure("stream_1", "mic_error", "Microphone disconnected", 150)
    assert sm.fail(fail) is True
    assert sm.current_state == VoiceStreamState.ERROR
    assert sm.last_failure == fail


def test_explicit_reset():
    """Test 15: Explicit reset."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    sm.on_interim_transcript(InterimTranscript("stream_1", "hello", 150))
    assert sm.accumulator.current_interim is not None

    assert sm.reset(monotonic_ns=200) is True
    assert sm.current_state == VoiceStreamState.IDLE
    assert sm.accumulator.current_interim is None
    assert sm.active_interruption_request is None


def test_aec_unavailable():
    """Test 16: AEC unavailable."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.set_aec_capability(AECCapability(enabled=True, available=False, status_reason="no_aec_hardware"))
    assert sm.aec_capability.available is False

    acc = sm.get_accessibility_state()
    assert "Echo cancellation unavailable" in acc.announcement


def test_vad_unavailable():
    """Test 17: VAD unavailable."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.set_vad_capability(VADCapability(enabled=True, available=False, status_reason="no_vad_model"))
    assert sm.vad_capability.available is False

    acc = sm.get_accessibility_state()
    assert "Voice activity detection unavailable" in acc.announcement


def test_no_audio_persistence():
    """Test 26: No audio persistence."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    sm.on_interim_transcript(InterimTranscript("stream_1", "voice command", 150))

    # Inspect all attributes: ensure no byte arrays or raw audio buffers exist
    state_dict = vars(sm)
    for key, val in state_dict.items():
        assert not isinstance(val, (bytes, bytearray))


def test_deterministic_transition_records():
    """Test 27: Deterministic transition records."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_wake_listening(monotonic_ns=100)
    sm.on_wake_event(VerifiedWakeEvent("stream_1", "hey hikari", 0.9, 200, is_verified=True))

    history = sm.get_history()
    assert len(history) == 2
    assert history[0].old_state == VoiceStreamState.IDLE
    assert history[0].new_state == VoiceStreamState.WAKE_LISTENING
    assert history[0].monotonic_ns == 100

    assert history[1].old_state == VoiceStreamState.WAKE_LISTENING
    assert history[1].new_state == VoiceStreamState.ACTIVE_LISTENING
    assert history[1].monotonic_ns == 200


def test_passive_vs_active_listening_distinction():
    """Test 28: Passive versus active listening distinction."""
    sm = VoiceStreamStateMachine("stream_1")

    # In WAKE_LISTENING (passive wake mode)
    sm.start_wake_listening(100)
    assert sm.current_state == VoiceStreamState.WAKE_LISTENING
    # Commands rejected
    assert sm.on_interim_transcript(InterimTranscript("stream_1", "do work", 110)) is False
    assert sm.current_state == VoiceStreamState.WAKE_LISTENING

    # In ACTIVE_LISTENING (active command mode)
    sm.on_wake_event(VerifiedWakeEvent("stream_1", "hey hikari", 0.95, 120, is_verified=True))
    assert sm.current_state == VoiceStreamState.ACTIVE_LISTENING
    # Commands accepted
    assert sm.on_interim_transcript(InterimTranscript("stream_1", "do work", 130)) is True
    assert sm.current_state == VoiceStreamState.USER_SPEAKING


def test_synthetic_event_ordering():
    """Test 29: Synthetic event ordering."""
    sm = VoiceStreamStateMachine("stream_1")
    timestamps = [100, 200, 300, 400, 500]

    assert sm.start_wake_listening(timestamps[0]) is True
    assert sm.on_wake_event(VerifiedWakeEvent("stream_1", "wake", 0.9, timestamps[1])) is True
    assert sm.on_vad_event(VADEvent("stream_1", True, 0.9, timestamps[2])) is True
    assert sm.on_vad_event(VADEvent("stream_1", False, 0.9, timestamps[3])) is True
    assert sm.on_final_transcript(FinalTranscript("stream_1", "Hello", timestamps[3], timestamps[4])) is True

    assert sm.current_state == VoiceStreamState.THINKING
    assert sm.last_monotonic_ns == 500


def test_accessibility_state_exposure():
    """Test 30: Accessibility state exposure."""
    sm = VoiceStreamStateMachine("stream_1")
    sm.start_active_listening(monotonic_ns=100)
    acc1 = sm.get_accessibility_state()
    assert acc1.indicator == "listening"
    assert "Listening for user command" in acc1.announcement

    sm.on_interim_transcript(InterimTranscript("stream_1", "weather query", 150))
    acc2 = sm.get_accessibility_state()
    assert acc2.indicator == "listening"
    assert acc2.caption_text == "weather query"
    assert "User speaking" in acc2.announcement
    assert acc2.non_audio_fallback is True
    assert acc2.manual_stop_available is True


def test_rejected_stale_interim_does_not_change_state():
    sm = VoiceStreamStateMachine("stream_1")
    assert sm.start_active_listening(10)
    stale = InterimTranscript("stream_1", "hello", 9)
    assert sm.on_interim_transcript(stale) is False
    assert sm.current_state is VoiceStreamState.ACTIVE_LISTENING
    assert sm.accumulator.current_interim is None


def test_rejected_stale_final_is_not_stored():
    sm = VoiceStreamStateMachine("stream_1")
    assert sm.start_active_listening(20)
    stale = FinalTranscript("stream_1", "hello", 1, 19)
    assert sm.on_final_transcript(stale) is False
    assert sm.current_state is VoiceStreamState.ACTIVE_LISTENING
    assert sm.accumulator.get_segments() == ()
