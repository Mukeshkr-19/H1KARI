"""Tests for voice session contracts, validation logic, and privacy reprs."""

from __future__ import annotations

import pytest

from core.voice_session.contracts import (
    AudioFrame,
    EchoNoiseResult,
    OwnerVerificationResult,
    SessionContext,
    validate_generation,
    validate_monotonic_ns,
    validate_playback_id,
    validate_response_id,
    validate_sequence,
    validate_session_id,
    validate_utterance_id,
)


def test_id_validations() -> None:
    assert validate_session_id("sess_123") == "sess_123"
    assert validate_utterance_id("utt_456") == "utt_456"
    assert validate_response_id("resp_789") == "resp_789"
    assert validate_playback_id("pb_abc") == "pb_abc"

    with pytest.raises(TypeError):
        validate_session_id(123)

    with pytest.raises(ValueError):
        validate_session_id("   ")

    with pytest.raises(ValueError):
        validate_session_id("x" * 200)


def test_numeric_validations() -> None:
    assert validate_sequence(0) == 0
    assert validate_sequence(42) == 42
    assert validate_monotonic_ns(1000) == 1000
    assert validate_generation(1) == 1

    with pytest.raises(TypeError):
        validate_sequence("10")
    with pytest.raises(TypeError):
        validate_sequence(True)
    with pytest.raises(ValueError):
        validate_sequence(-1)

    with pytest.raises(TypeError):
        validate_monotonic_ns(10.5)
    with pytest.raises(ValueError):
        validate_monotonic_ns(-5)


def test_audio_frame_contracts() -> None:
    frame = AudioFrame(data=b"\x00\x01\x02\x03", sample_rate=16000, channels=1, monotonic_ns=100)
    assert frame.data == b"\x00\x01\x02\x03"
    assert frame.sample_rate == 16000
    assert frame.monotonic_ns == 100

    # Privacy repr check - no raw data in repr
    rep = repr(frame)
    assert "bytes=4" in rep
    assert "b'\\x00\\x01\\x02\\x03'" not in rep

    with pytest.raises(TypeError):
        AudioFrame(data="not_bytes", sample_rate=16000, channels=1, monotonic_ns=100)  # type: ignore


def test_owner_verification_contracts() -> None:
    res = OwnerVerificationResult(
        is_owner=True, confidence=0.95, speaker_id="sensitive_speaker_123"
    )
    assert res.is_owner is True
    assert res.confidence == 0.95

    # Privacy repr check - speaker_id MUST NOT be printed in repr!
    rep = repr(res)
    assert "sensitive_speaker_123" not in rep
    assert "is_owner=True" in rep
    assert "confidence=0.95" in rep


def test_echo_noise_contracts() -> None:
    res = EchoNoiseResult(is_echo=False, is_noise=True, confidence=0.88)
    assert res.is_echo is False
    assert res.is_noise is True
    rep = repr(res)
    assert "is_echo=False" in rep
    assert "is_noise=True" in rep


def test_session_context_contracts() -> None:
    ctx = SessionContext(
        session_id="sess_1",
        utterance_id="utt_1",
        response_id="resp_1",
        playback_id="pb_1",
        event_sequence=5,
        cancellation_generation=2,
        monotonic_ns=1000,
    )
    assert ctx.session_id == "sess_1"
    assert ctx.cancellation_generation == 2

    # Privacy repr check - raw ID strings omitted
    rep = repr(ctx)
    assert "sess_1" not in rep
    assert "seq=5" in rep
    assert "gen=2" in rep
