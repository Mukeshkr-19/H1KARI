"""Tests for streaming voice pipeline contracts and data types."""

import pytest
from dataclasses import FrozenInstanceError

from core.voice_streaming.contracts import (
    AECCapability,
    AccessibilityState,
    AuthDecision,
    CaptureState,
    FinalTranscript,
    InterimTranscript,
    InterruptionConfirmation,
    InterruptionRequest,
    PlaybackState,
    StateTransitionRecord,
    StreamingVoiceFailure,
    VADCapability,
    VADEvent,
    VerifiedWakeEvent,
    VoiceStreamState,
    sanitize_text,
    validate_confidence,
    validate_monotonic_ns,
    validate_stream_id,
)


def test_contract_dataclasses_are_frozen():
    """Verify dataclasses are immutable."""
    event = VADEvent(
        stream_id="s1",
        is_speech=True,
        confidence=0.9,
        monotonic_ns=100,
    )
    with pytest.raises(FrozenInstanceError):
        event.confidence = 0.5  # type: ignore[misc]

    interim = InterimTranscript(
        stream_id="s1",
        text="hello",
        monotonic_ns=100,
    )
    with pytest.raises(FrozenInstanceError):
        interim.text = "change"  # type: ignore[misc]

    final = FinalTranscript(
        stream_id="s1",
        text="hello world",
        start_monotonic_ns=100,
        end_monotonic_ns=200,
    )
    with pytest.raises(FrozenInstanceError):
        final.text = "other"  # type: ignore[misc]


def test_empty_final_rejection():
    """Test 20: Empty final rejection."""
    with pytest.raises(ValueError, match="empty"):
        FinalTranscript(
            stream_id="s1",
            text="   \n\t  ",
            start_monotonic_ns=100,
            end_monotonic_ns=200,
        )

    with pytest.raises(ValueError, match="empty"):
        FinalTranscript(
            stream_id="s1",
            text="",
            start_monotonic_ns=100,
            end_monotonic_ns=200,
        )


def test_invalid_confidence():
    """Test 21: Invalid confidence rejection."""
    with pytest.raises(ValueError, match="out of bounds"):
        validate_confidence(1.5)

    with pytest.raises(ValueError, match="out of bounds"):
        validate_confidence(-0.1)

    with pytest.raises(TypeError):
        validate_confidence("high")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        validate_confidence(True)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        VADEvent(stream_id="s1", is_speech=True, confidence=2.0, monotonic_ns=100)


def test_end_before_start_rejection():
    """Test 22: End-before-start rejection."""
    with pytest.raises(ValueError, match="earlier than start"):
        FinalTranscript(
            stream_id="s1",
            text="valid text",
            start_monotonic_ns=200,
            end_monotonic_ns=100,
        )


def test_text_sanitization_and_controls():
    """Verify ASCII control characters are stripped while preserving text and tabs/newlines."""
    raw = "Hello\x07 World\nLine\t2\x7f"
    cleaned = sanitize_text(raw)
    assert cleaned == "Hello World\nLine\t2"


def test_stream_id_validation():
    with pytest.raises(ValueError):
        validate_stream_id("")
    with pytest.raises(ValueError):
        validate_stream_id("   ")
    assert validate_stream_id("  stream_123  ") == "stream_123"


def test_monotonic_ns_validation():
    with pytest.raises(ValueError):
        validate_monotonic_ns(-5)
    with pytest.raises(TypeError):
        validate_monotonic_ns(1.5)  # type: ignore[arg-type]
    assert validate_monotonic_ns(0) == 0
    assert validate_monotonic_ns(1000) == 1000


def test_additional_contracts_instantiation():
    req = InterruptionRequest(
        stream_id="s1", request_id="r1", monotonic_ns=100, is_authenticated=True
    )
    assert req.is_authenticated is True

    conf = InterruptionConfirmation(
        stream_id="s1", request_id="r1", monotonic_ns=150, is_confirmed=True
    )
    assert conf.is_confirmed is True

    pb = PlaybackState(stream_id="s1", is_playing=True, current_position_ms=500)
    assert pb.is_playing is True

    cap = CaptureState(stream_id="s1", is_capturing=True)
    assert cap.is_capturing is True

    auth = AuthDecision(
        speaker_id="user_1", is_authenticated=True, confidence=0.95, reason="verified"
    )
    assert auth.is_authenticated is True

    aec = AECCapability(enabled=True, available=True, is_hardware=False)
    assert aec.available is True

    vad = VADCapability(enabled=True, available=False, status_reason="no_engine")
    assert vad.available is False

    fail = StreamingVoiceFailure(
        stream_id="s1", error_code="timeout", message="Request timed out", monotonic_ns=200
    )
    assert fail.recoverable is True

    rec = StateTransitionRecord(
        old_state=VoiceStreamState.IDLE,
        new_state=VoiceStreamState.WAKE_LISTENING,
        event_type="test",
        monotonic_ns=10,
        reason="test_start",
        details={},
    )
    assert rec.new_state == VoiceStreamState.WAKE_LISTENING

    acc = AccessibilityState(
        indicator="listening",
        caption_text="hello",
        announcement="Listening...",
    )
    assert acc.indicator == "listening"
