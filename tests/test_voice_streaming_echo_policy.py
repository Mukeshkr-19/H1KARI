"""Tests for acoustic echo cancellation capability selection and fallback policy."""

import pytest

from core.voice_streaming.echo_policy import (
    EchoCapability,
    EchoMode,
    EchoPolicyContext,
    EchoPolicyDecision,
    EchoPolicyEvaluator,
)


def test_unverified_aec_denial():
    """Verify available BUT unverified AEC is denied full-duplex operation."""
    evaluator = EchoPolicyEvaluator()

    # Native AEC available but NOT verified
    cap_unverified = EchoCapability(native_aec_available=True, native_aec_verified=False)
    ctx = EchoPolicyContext(stream_id="s1", capability=cap_unverified)

    decision = evaluator.evaluate(ctx, monotonic_ns=100)
    assert decision.full_duplex_safe is False
    assert decision.selected_mode != EchoMode.NATIVE_AEC_ACTIVE
    assert "AEC capability unverified" in decision.reason


def test_verified_native_aec():
    """Verify native AEC available AND verified grants full-duplex operation."""
    evaluator = EchoPolicyEvaluator()

    cap = EchoCapability(native_aec_available=True, native_aec_verified=True)
    ctx = EchoPolicyContext(stream_id="s1", capability=cap)

    decision = evaluator.evaluate(ctx, monotonic_ns=100)
    assert decision.full_duplex_safe is True
    assert decision.selected_mode == EchoMode.NATIVE_AEC_ACTIVE
    assert decision.mute_input is False
    assert decision.suppress_output is False


def test_high_residual_echo_overrides_verified_aec():
    evaluator = EchoPolicyEvaluator()
    ctx = EchoPolicyContext(
        stream_id="s1",
        capability=EchoCapability(native_aec_available=True, native_aec_verified=True),
        output_playback_active=True,
        input_capture_active=True,
        echo_confidence=0.95,
    )
    decision = evaluator.evaluate(ctx, monotonic_ns=100)
    assert decision.full_duplex_safe is False
    assert decision.selected_mode == EchoMode.PLAYBACK_SUPPRESSION


def test_verified_software_aec():
    """Verify software DSP AEC available AND verified grants full-duplex operation."""
    evaluator = EchoPolicyEvaluator()

    cap = EchoCapability(software_aec_available=True, software_aec_verified=True)
    ctx = EchoPolicyContext(stream_id="s1", capability=cap)

    decision = evaluator.evaluate(ctx, monotonic_ns=100)
    assert decision.full_duplex_safe is True
    assert decision.selected_mode == EchoMode.SOFTWARE_AEC_ACTIVE
    assert decision.mute_input is False


def test_headphones_connected_mode():
    """Verify connected headphones bypass acoustic echo path and grant full-duplex."""
    evaluator = EchoPolicyEvaluator()

    cap = EchoCapability(headphones_connected=True)
    ctx = EchoPolicyContext(stream_id="s1", capability=cap)

    decision = evaluator.evaluate(ctx, monotonic_ns=100)
    assert decision.full_duplex_safe is True
    assert decision.selected_mode == EchoMode.HEADPHONES_ACTIVE
    assert decision.mute_input is False


def test_half_duplex_fallback_during_playback():
    """Verify unverified AEC during active playback falls back to muting input capture."""
    evaluator = EchoPolicyEvaluator()

    cap = EchoCapability()  # No AEC, no headphones
    ctx = EchoPolicyContext(
        stream_id="s1",
        capability=cap,
        output_playback_active=True,
        allow_half_duplex_fallback=True,
    )

    decision = evaluator.evaluate(ctx, monotonic_ns=100)
    assert decision.full_duplex_safe is False
    assert decision.selected_mode == EchoMode.HALF_DUPLEX_FALLBACK
    assert decision.mute_input is True
    assert decision.suppress_output is False


def test_playback_suppression_during_user_speech():
    """Verify user speaking during playback without verified AEC suppresses/ducks playback."""
    evaluator = EchoPolicyEvaluator()

    cap = EchoCapability()  # No AEC
    ctx = EchoPolicyContext(
        stream_id="s1",
        capability=cap,
        output_playback_active=True,
        user_speaking=True,
        allow_half_duplex_fallback=True,
    )

    decision = evaluator.evaluate(ctx, monotonic_ns=100)
    assert decision.full_duplex_safe is False
    assert decision.selected_mode == EchoMode.PLAYBACK_SUPPRESSION
    assert decision.mute_input is False
    assert decision.suppress_output is True
    assert decision.attenuation_db == -18.0


def test_unsupported_fail_closed_mode():
    """Verify unverified AEC with fallback disabled fails closed."""
    evaluator = EchoPolicyEvaluator()

    cap = EchoCapability()
    ctx = EchoPolicyContext(
        stream_id="s1",
        capability=cap,
        allow_half_duplex_fallback=False,
    )

    decision = evaluator.evaluate(ctx, monotonic_ns=100)
    assert decision.full_duplex_safe is False
    assert decision.selected_mode == EchoMode.UNSUPPORTED_FAIL_CLOSED
    assert decision.mute_input is True
    assert decision.suppress_output is True
