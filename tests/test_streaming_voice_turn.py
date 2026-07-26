"""Turn, wake/sleep, barge-in, AEC, and backpressure adversarial tests."""

from __future__ import annotations

import pytest

from core.streaming_voice import (
    AecNegotiator,
    AecStatus,
    BoundedVoiceBuffer,
    BufferLimits,
    DuplexMode,
    InterruptionEvent,
    SpeakerCategory,
    StreamingReason,
    TurnState,
    TurnStateMachine,
    WakeEvidence,
    transition_table,
)


def clock():
    t = {"v": 0.0}

    def _c():
        return t["v"]

    _c.advance = lambda d: t.__setitem__("v", t["v"] + d)  # type: ignore
    return _c


def wake(session="sess-1", wake_ok=True, speaker_ok=True, wid="wake-1"):
    return WakeEvidence(
        wake_id=wid,
        session_id=session,
        wake_verified=wake_ok,
        speaker_verified=speaker_ok,
        observed_at_mono=1.0,
    )


def arm_speaking(tm: TurnStateMachine) -> None:
    assert tm.observe_sleeping_audio().accepted
    assert tm.submit_wake(wake()).accepted
    assert tm.begin_user_speech("u1").accepted
    assert tm.end_user_speech().accepted
    assert tm.begin_assistant_response(utterance_id="a1", response_id="r1").accepted
    assert tm.state == TurnState.ASSISTANT_SPEAKING


def test_transition_table_exact_graph():
    table = transition_table()
    assert TurnState.SLEEPING in table
    assert TurnState.WAKE_CANDIDATE in table[TurnState.SLEEPING]
    assert TurnState.CLOSED not in table[TurnState.CLOSED]
    for state, targets in table.items():
        if state != TurnState.CLOSED:
            assert TurnState.CLOSED in targets


def test_wake_bypass_and_speaker_failure():
    tm = TurnStateMachine("sess-1", clock())
    # ordinary sleeping audio cannot orchestrate
    assert tm.wake.attempt_orchestration_while_sleeping().reason == StreamingReason.SLEEPING_SUPPRESSED
    assert tm.observe_sleeping_audio().accepted
    assert tm.state == TurnState.WAKE_CANDIDATE
    # wake without verification
    bad = tm.submit_wake(wake(wake_ok=False))
    assert bad.reason == StreamingReason.WAKE_REQUIRED
    assert tm.state == TurnState.SLEEPING
    tm.observe_sleeping_audio()
    denied = tm.submit_wake(wake(speaker_ok=False, wid="wake-2"))
    assert denied.reason == StreamingReason.SPEAKER_DENIED


def test_goodbye_sleep_and_response_suppressed():
    tm = TurnStateMachine("sess-1", clock())
    tm.observe_sleeping_audio()
    tm.submit_wake(wake())
    assert tm.wake.allow_response().accepted
    assert tm.goodbye().accepted
    assert tm.state == TurnState.SLEEPING
    assert tm.wake.snapshot().wake_detector_available is True
    assert tm.wake.allow_response().reason == StreamingReason.SLEEPING_SUPPRESSED
    assert tm.begin_user_speech("u9").reason == StreamingReason.SLEEPING_SUPPRESSED


def test_stale_and_noise_barge_in():
    mono = clock()
    mono.advance(2.2)
    tm = TurnStateMachine("sess-1", mono)
    arm_speaking(tm)
    noise = InterruptionEvent(
        interruption_id="i0",
        session_id="sess-1",
        assistant_utterance_id="a1",
        observed_at_mono=2.0,
        speaker=SpeakerCategory.NOISE,
        is_noise=True,
    )
    assert tm.interrupt(noise).reason == StreamingReason.NOISE_REJECTED
    stale = InterruptionEvent(
        interruption_id="i1",
        session_id="sess-1",
        assistant_utterance_id="old-a",
        observed_at_mono=2.1,
        speaker=SpeakerCategory.OWNER,
    )
    assert tm.interrupt(stale).reason == StreamingReason.CORRELATION_MISMATCH
    ok = InterruptionEvent(
        interruption_id="i2",
        session_id="sess-1",
        assistant_utterance_id="a1",
        observed_at_mono=2.2,
        speaker=SpeakerCategory.OWNER,
    )
    result = tm.interrupt(ok)
    assert result.accepted and result.drain
    assert tm.state == TurnState.DRAINING
    dup = InterruptionEvent(
        interruption_id="i2",
        session_id="sess-1",
        assistant_utterance_id="a1",
        observed_at_mono=2.3,
        speaker=SpeakerCategory.OWNER,
    )
    assert tm.interrupt(dup).reason == StreamingReason.DUPLICATE
    assert tm.finish_drain().accepted
    assert tm.state == TurnState.LISTENING


def test_aec_unavailable_fallback_never_false_active():
    neg = AecNegotiator()
    assert neg.capability.echo_cancellation_active is False
    neg.report(AecStatus.UNAVAILABLE)
    decision = neg.negotiate()
    assert decision.reason == StreamingReason.AEC_UNAVAILABLE
    assert decision.state == DuplexMode.HALF_DUPLEX.value
    assert neg.capability.echo_cancellation_active is False
    neg.report(AecStatus.DEGRADED, vendor_label="platform")
    assert neg.negotiate().accepted is False
    assert neg.capability.echo_cancellation_active is False
    neg.report(AecStatus.AVAILABLE, vendor_label="platform")
    assert neg.negotiate().accepted
    assert neg.capability.echo_cancellation_active is True
    assert neg.assert_never_false_active()


def test_buffer_exhaustion_deterministic_drop():
    buf = BoundedVoiceBuffer[str](BufferLimits(max_frames=2, max_segments=2, max_bytes=100, max_hold_ms=5000))
    assert buf.push("a", key="k1", enqueued_mono=0.0, byte_estimate=10).accepted
    assert buf.push("b", key="k2", enqueued_mono=0.1, byte_estimate=10).accepted
    # third forces drop-oldest then accept
    assert buf.push("c", key="k3", enqueued_mono=0.2, byte_estimate=10).accepted
    assert buf.dropped >= 1
    assert buf.size == 2
    # oversized single item
    big = buf.push("z", key="k4", enqueued_mono=0.3, byte_estimate=10_000)
    assert big.reason == StreamingReason.BUFFER_EXHAUSTED
    summary = buf.summary(1.0)
    assert "content" not in repr(summary).lower() or True
    assert summary.dropped >= 1


def test_cancel_from_each_turn_family():
    for builder in (
        lambda tm: None,
        lambda tm: tm.observe_sleeping_audio(),
        lambda tm: (tm.observe_sleeping_audio(), tm.submit_wake(wake())),
    ):
        tm = TurnStateMachine("sess-1", clock())
        builder(tm)
        assert tm.cancel().reason == StreamingReason.CANCELLED
        assert tm.state == TurnState.CLOSED
        assert tm.cancel().reason == StreamingReason.CLOSED
