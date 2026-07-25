"""Acoustic echo cancellation (AEC) capability selection and fallback policy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core.voice_streaming.contracts import (
    validate_confidence,
    validate_monotonic_ns,
    validate_stream_id,
)


class EchoMode(str, Enum):
    NATIVE_AEC_ACTIVE = "native_aec_active"
    SOFTWARE_AEC_ACTIVE = "software_aec_active"
    HEADPHONES_ACTIVE = "headphones_active"
    HALF_DUPLEX_FALLBACK = "half_duplex_fallback"
    PLAYBACK_SUPPRESSION = "playback_suppression"
    UNSUPPORTED_FAIL_CLOSED = "unsupported_fail_closed"


@dataclass(frozen=True)
class EchoCapability:
    """Caller-supplied acoustic echo cancellation capabilities and verification flags."""

    native_aec_available: bool = False
    native_aec_verified: bool = False
    software_aec_available: bool = False
    software_aec_verified: bool = False
    headphones_connected: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "native_aec_available",
            "native_aec_verified",
            "software_aec_available",
            "software_aec_verified",
            "headphones_connected",
        ):
            val = getattr(self, field_name)
            if not isinstance(val, bool):
                raise TypeError(f"{field_name} must be a boolean")


@dataclass(frozen=True)
class EchoPolicyContext:
    """Context for evaluating acoustic echo policy decisions."""

    stream_id: str
    capability: EchoCapability
    output_playback_active: bool = False
    input_capture_active: bool = False
    echo_confidence: float = 0.0
    interruption_requested: bool = False
    user_speaking: bool = False
    allow_half_duplex_fallback: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        if not isinstance(self.capability, EchoCapability):
            raise TypeError("capability must be EchoCapability")
        object.__setattr__(self, "echo_confidence", validate_confidence(self.echo_confidence))
        for field_name in (
            "output_playback_active",
            "input_capture_active",
            "interruption_requested",
            "user_speaking",
            "allow_half_duplex_fallback",
        ):
            val = getattr(self, field_name)
            if not isinstance(val, bool):
                raise TypeError(f"{field_name} must be a boolean")


@dataclass(frozen=True)
class EchoPolicyDecision:
    """Deterministic echo policy decision and action instructions."""

    stream_id: str
    selected_mode: EchoMode
    full_duplex_safe: bool
    mute_input: bool
    suppress_output: bool
    attenuation_db: float
    reason: str
    monotonic_ns: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        if isinstance(self.attenuation_db, bool) or not isinstance(self.attenuation_db, (int, float)):
            raise TypeError("attenuation_db must be a float")
        if not math.isfinite(self.attenuation_db) or self.attenuation_db > 0:
            raise ValueError("attenuation_db must be finite and non-positive")


class EchoPolicyEvaluator:
    """Evaluates echo capabilities and selects safe echo mitigation policies."""

    def evaluate(self, ctx: EchoPolicyContext, monotonic_ns: int) -> EchoPolicyDecision:
        """Select safe echo mode based on capability verification and active playback/capture context.

        Rules:
        1. Never claim AEC is active without verified capability evidence!
        2. Headphones -> HEADPHONES_ACTIVE (full_duplex_safe=True).
        3. Native AEC available AND verified -> NATIVE_AEC_ACTIVE (full_duplex_safe=True).
        4. Software AEC available AND verified -> SOFTWARE_AEC_ACTIVE (full_duplex_safe=True).
        5. Unverified AEC -> Full duplex is NOT safe. Fallback to half-duplex, playback suppression,
           or unsupported fail-closed.
        """
        if not isinstance(ctx, EchoPolicyContext):
            raise TypeError("Context must be an EchoPolicyContext")

        ts = validate_monotonic_ns(monotonic_ns)
        cap = ctx.capability

        # 1. Headphones connected (no acoustic echo path)
        if cap.headphones_connected:
            return EchoPolicyDecision(
                stream_id=ctx.stream_id,
                selected_mode=EchoMode.HEADPHONES_ACTIVE,
                full_duplex_safe=True,
                mute_input=False,
                suppress_output=False,
                attenuation_db=0.0,
                reason="Headphones connected; acoustic echo path eliminated",
                monotonic_ns=ts,
            )

        # High caller-supplied residual echo confidence while simultaneous
        # capture/playback is active overrides an otherwise verified AEC claim.
        if (
            ctx.output_playback_active
            and ctx.input_capture_active
            and ctx.echo_confidence >= 0.8
        ):
            return EchoPolicyDecision(
                stream_id=ctx.stream_id,
                selected_mode=EchoMode.PLAYBACK_SUPPRESSION,
                full_duplex_safe=False,
                mute_input=False,
                suppress_output=True,
                attenuation_db=-18.0,
                reason="High residual echo confidence; suppressing playback.",
                monotonic_ns=ts,
            )

        # 2. Native hardware/OS AEC (requires BOTH available and verified!)
        if cap.native_aec_available and cap.native_aec_verified:
            return EchoPolicyDecision(
                stream_id=ctx.stream_id,
                selected_mode=EchoMode.NATIVE_AEC_ACTIVE,
                full_duplex_safe=True,
                mute_input=False,
                suppress_output=False,
                attenuation_db=0.0,
                reason="Native hardware/OS AEC verified and active",
                monotonic_ns=ts,
            )

        # 3. Software DSP AEC (requires BOTH available and verified!)
        if cap.software_aec_available and cap.software_aec_verified:
            return EchoPolicyDecision(
                stream_id=ctx.stream_id,
                selected_mode=EchoMode.SOFTWARE_AEC_ACTIVE,
                full_duplex_safe=True,
                mute_input=False,
                suppress_output=False,
                attenuation_db=0.0,
                reason="Software DSP AEC verified and active",
                monotonic_ns=ts,
            )

        # 4. Unverified AEC or no AEC -> Full duplex is NOT safe!
        # Unverified AEC denial rule
        unverified = (cap.native_aec_available and not cap.native_aec_verified) or (
            cap.software_aec_available and not cap.software_aec_verified
        )
        base_reason = (
            "AEC capability unverified; full-duplex disallowed"
            if unverified
            else "No AEC capability available; full-duplex disallowed"
        )

        if not ctx.allow_half_duplex_fallback:
            return EchoPolicyDecision(
                stream_id=ctx.stream_id,
                selected_mode=EchoMode.UNSUPPORTED_FAIL_CLOSED,
                full_duplex_safe=False,
                mute_input=True,
                suppress_output=True,
                attenuation_db=-60.0,
                reason=f"{base_reason}. Fallback disabled; failing closed.",
                monotonic_ns=ts,
            )

        # Fallback modes when playback or speech is active
        if ctx.output_playback_active:
            if ctx.interruption_requested or ctx.user_speaking:
                # Interruption or user turn during playback -> suppress/duck playback
                return EchoPolicyDecision(
                    stream_id=ctx.stream_id,
                    selected_mode=EchoMode.PLAYBACK_SUPPRESSION,
                    full_duplex_safe=False,
                    mute_input=False,
                    suppress_output=True,
                    attenuation_db=-18.0,
                    reason=f"{base_reason}. User speaking during playback; suppressing playback.",
                    monotonic_ns=ts,
                )
            else:
                # Playback active without user speech -> half-duplex mute input to prevent feedback
                return EchoPolicyDecision(
                    stream_id=ctx.stream_id,
                    selected_mode=EchoMode.HALF_DUPLEX_FALLBACK,
                    full_duplex_safe=False,
                    mute_input=True,
                    suppress_output=False,
                    attenuation_db=0.0,
                    reason=f"{base_reason}. Playback active; muting input capture.",
                    monotonic_ns=ts,
                )

        # Inactive playback -> half-duplex idle
        return EchoPolicyDecision(
            stream_id=ctx.stream_id,
            selected_mode=EchoMode.HALF_DUPLEX_FALLBACK,
            full_duplex_safe=False,
            mute_input=False,
            suppress_output=False,
            attenuation_db=0.0,
            reason=f"{base_reason}. Half-duplex ready.",
            monotonic_ns=ts,
        )
