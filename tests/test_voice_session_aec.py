"""Tests for AEC policy, platform evidence binding, and safe half-duplex fallback."""

from __future__ import annotations

from core.voice_session.aec_policy import AecPolicy, AecPolicyDecision
from core.voice_streaming.aec_evidence import PlatformAecEvidence


def test_aec_policy_default_half_duplex() -> None:
    policy = AecPolicy()
    assert policy.is_full_duplex is False

    # Evidence absent -> half-duplex
    decision = policy.evaluate(
        evidence=None,
        has_echo_reference=True,
        headphones_active=False,
        now_ns=1000,
        active_stream_id="sess_1",
        active_device_id="dev_1",
    )
    assert decision.is_full_duplex is False
    assert decision.reason == "aec_evidence_absent"
    assert policy.is_full_duplex is False

    # Privacy check: repr hides secrets/device IDs
    assert "sess_1" not in repr(decision)
    assert "dev_1" not in repr(decision)


def test_aec_policy_valid_full_duplex() -> None:
    policy = AecPolicy()
    now = 1_000_000_000

    evidence = PlatformAecEvidence(
        stream_id="sess_1",
        device_id="dev_1",
        available=True,
        enabled=True,
        verified=True,
        observed_at_ns=now,
    )

    decision = policy.evaluate(
        evidence=evidence,
        has_echo_reference=True,
        headphones_active=False,
        now_ns=now,
        active_stream_id="sess_1",
        active_device_id="dev_1",
    )

    assert decision.is_full_duplex is True
    assert decision.reason == "ok"
    assert policy.is_full_duplex is True


def test_aec_policy_missing_echo_reference() -> None:
    policy = AecPolicy()
    now = 1_000_000_000

    evidence = PlatformAecEvidence(
        stream_id="sess_1",
        device_id="dev_1",
        available=True,
        enabled=True,
        verified=True,
        observed_at_ns=now,
    )

    # Echo reference missing -> must return to half-duplex
    decision = policy.evaluate(
        evidence=evidence,
        has_echo_reference=False,
        headphones_active=False,
        now_ns=now,
        active_stream_id="sess_1",
        active_device_id="dev_1",
    )

    assert decision.is_full_duplex is False
    assert decision.reason == "missing_echo_reference"
    assert policy.is_full_duplex is False


def test_aec_policy_cross_stream_cross_device() -> None:
    policy = AecPolicy()
    now = 1_000_000_000

    evidence = PlatformAecEvidence(
        stream_id="sess_OTHER",
        device_id="dev_1",
        available=True,
        enabled=True,
        verified=True,
        observed_at_ns=now,
    )

    # Cross stream
    decision1 = policy.evaluate(
        evidence=evidence,
        has_echo_reference=True,
        now_ns=now,
        active_stream_id="sess_1",
        active_device_id="dev_1",
    )
    assert decision1.is_full_duplex is False
    assert "cross_stream" in decision1.reason

    # Cross device
    evidence2 = PlatformAecEvidence(
        stream_id="sess_1",
        device_id="dev_OTHER",
        available=True,
        enabled=True,
        verified=True,
        observed_at_ns=now,
    )
    decision2 = policy.evaluate(
        evidence=evidence2,
        has_echo_reference=True,
        now_ns=now,
        active_stream_id="sess_1",
        active_device_id="dev_1",
    )
    assert decision2.is_full_duplex is False
    assert "cross_device" in decision2.reason


def test_aec_policy_headphones_not_fabricated_proof() -> None:
    policy = AecPolicy()
    now = 1_000_000_000

    # Headphones alone with NO platform AEC evidence -> half-duplex
    decision = policy.evaluate(
        evidence=None,
        has_echo_reference=True,
        headphones_active=True,
        now_ns=now,
        active_stream_id="sess_1",
        active_device_id="dev_1",
    )
    assert decision.is_full_duplex is False
    assert decision.headphones_active is True


def test_aec_policy_mark_lost() -> None:
    policy = AecPolicy()
    now = 1_000_000_000

    evidence = PlatformAecEvidence(
        stream_id="sess_1",
        device_id="dev_1",
        available=True,
        enabled=True,
        verified=True,
        observed_at_ns=now,
    )
    policy.evaluate(
        evidence=evidence,
        has_echo_reference=True,
        now_ns=now,
        active_stream_id="sess_1",
        active_device_id="dev_1",
    )
    assert policy.is_full_duplex is True

    lost_dec = policy.mark_lost()
    assert lost_dec.is_full_duplex is False
    assert lost_dec.reason == "aec_evidence_lost"
    assert policy.is_full_duplex is False
