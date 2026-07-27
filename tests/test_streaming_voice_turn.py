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
from core.voice_streaming.interruption_evidence import (
    InterruptionEvidence,
    InterruptionVerificationSource,
)
from core.voice_streaming.contracts import VoiceStreamState


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


def arm_speaking(tm: TurnStateMachine, mono=None) -> None:
    assert tm.observe_sleeping_audio().accepted
    assert tm.submit_wake(wake()).accepted
    assert tm.begin_user_speech("u1").accepted
    assert tm.end_user_speech().accepted
    assert tm.begin_assistant_response(utterance_id="a1", response_id="r1").accepted
    assert tm.state == TurnState.ASSISTANT_SPEAKING
    if mono is not None:
        mono.advance(0.05)  # speech observation after playback began


def _event(
    *,
    iid="i2",
    session="sess-1",
    assistant="a1",
    mono=2.25,
    speaker=SpeakerCategory.OWNER,
    is_noise=False,
):
    return InterruptionEvent(
        interruption_id=iid,
        session_id=session,
        assistant_utterance_id=assistant,
        observed_at_mono=mono,
        speaker=speaker,
        is_noise=is_noise,
    )


def _evidence(
    event: InterruptionEvent,
    *,
    speaker_verified=True,
    speech_ns=None,
    target=None,
    interruption_id=None,
    stream_id=None,
    expires_delta_ns=2_000_000_000,
    observed_ns=None,
):
    obs = observed_ns if observed_ns is not None else int(event.observed_at_mono * 1_000_000_000)
    speech = speech_ns if speech_ns is not None else obs
    return InterruptionEvidence(
        stream_id=stream_id if stream_id is not None else event.session_id,
        interruption_id=interruption_id if interruption_id is not None else event.interruption_id,
        target_assistant_utterance_id=target if target is not None else event.assistant_utterance_id,
        speaker_verified=speaker_verified,
        verification_source=InterruptionVerificationSource.SPEAKER_AUTH,
        speech_observed_ns=speech,
        observed_at_ns=obs,
        expires_at_ns=obs + expires_delta_ns,
    )


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
    assert tm.wake.attempt_orchestration_while_sleeping().reason == StreamingReason.SLEEPING_SUPPRESSED
    assert tm.observe_sleeping_audio().accepted
    assert tm.state == TurnState.WAKE_CANDIDATE
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
    arm_speaking(tm, mono)
    noise = _event(iid="i0", mono=mono(), speaker=SpeakerCategory.NOISE, is_noise=True)
    assert tm.interrupt(noise, evidence=_evidence(noise)).reason == StreamingReason.NOISE_REJECTED
    stale = _event(iid="i1", assistant="old-a", mono=mono())
    assert tm.interrupt(stale, evidence=_evidence(stale)).reason == StreamingReason.CORRELATION_MISMATCH
    ok = _event(iid="i2", mono=mono())
    result = tm.interrupt(ok, evidence=_evidence(ok))
    assert result.accepted and result.drain
    assert tm.state == TurnState.DRAINING
    assert tm.canonical_runtime.state == VoiceStreamState.INTERRUPTING
    assert tm.notify_playback_stopped("i2").accepted
    assert tm.canonical_runtime.state == VoiceStreamState.INTERRUPTED
    dup = _event(iid="i2", mono=mono() + 0.1)
    assert tm.interrupt(dup, evidence=_evidence(dup)).reason == StreamingReason.DUPLICATE
    assert tm.finish_drain().accepted
    assert tm.state == TurnState.LISTENING


def test_facade_interrupt_requires_evidence_and_ignores_boolean():
    mono = clock()
    mono.advance(3.0)
    tm = TurnStateMachine("sess-1", mono)
    arm_speaking(tm, mono)
    event = _event(mono=mono())
    # Missing evidence denied; no drain.
    missing = tm.interrupt(event)
    assert missing.accepted is False
    assert missing.reason == StreamingReason.INVALID_INPUT
    assert tm.state == TurnState.ASSISTANT_SPEAKING
    assert tm.canonical_runtime.state == VoiceStreamState.ASSISTANT_SPEAKING
    # Caller boolean cannot authenticate.
    bool_only = tm.interrupt(event, is_authenticated=True)
    assert bool_only.accepted is False
    assert bool_only.reason == StreamingReason.INVALID_INPUT
    assert tm.state == TurnState.ASSISTANT_SPEAKING


def test_facade_interrupt_adversarial_evidence_matrix():
    mono = clock()
    mono.advance(4.0)
    tm = TurnStateMachine("sess-1", mono)
    arm_speaking(tm, mono)
    playback_ns = tm.canonical_runtime._playback_started_ns
    base = _event(iid="base", mono=mono())

    wrong_id = _evidence(base, interruption_id="other")
    assert tm.interrupt(base, evidence=wrong_id).reason == StreamingReason.CORRELATION_MISMATCH

    wrong_stream = _evidence(base, stream_id="other")
    assert tm.interrupt(base, evidence=wrong_stream).reason == StreamingReason.CORRELATION_MISMATCH

    future_event = _event(iid="fut", mono=mono() + 5.0)
    # event itself is future vs clock → stale at facade gate
    assert tm.interrupt(future_event, evidence=_evidence(future_event)).reason == StreamingReason.STALE_INTERRUPTION

    # Stale evidence expiry relative to runtime now: build expired evidence with matching obs ns
    stale_ev = _event(iid="stale", mono=mono())
    obs = int(stale_ev.observed_at_mono * 1_000_000_000)
    expired = InterruptionEvidence(
        stream_id="sess-1",
        interruption_id="stale",
        target_assistant_utterance_id="a1",
        speaker_verified=True,
        verification_source=InterruptionVerificationSource.SPEAKER_AUTH,
        speech_observed_ns=obs,
        observed_at_ns=obs,
        expires_at_ns=obs,  # expires immediately; runtime now advances past
    )
    # Advance clock so runtime now > expires
    mono.advance(0.01)
    denied_stale = tm.interrupt(stale_ev, evidence=expired)
    assert denied_stale.accepted is False
    assert tm.state == TurnState.ASSISTANT_SPEAKING

    # Speech before playback
    pre = _event(iid="pre", mono=mono())
    pre_ev = _evidence(pre, speech_ns=max(0, playback_ns - 100))
    assert tm.interrupt(pre, evidence=pre_ev).accepted is False
    assert tm.state == TurnState.ASSISTANT_SPEAKING

    # Wrong assistant utterance target on evidence
    wrong_utt = _evidence(base, target="nope", interruption_id="wu")
    wrong_event = _event(iid="wu", mono=mono())
    assert tm.interrupt(wrong_event, evidence=wrong_utt).reason == StreamingReason.CORRELATION_MISMATCH

    # Unverified speaker evidence
    unver = _event(iid="un", mono=mono())
    assert tm.interrupt(unver, evidence=_evidence(unver, speaker_verified=False)).reason == (
        StreamingReason.SPEAKER_DENIED
    )


def test_verified_evidence_drains_only_after_runtime_accept():
    mono = clock()
    mono.advance(5.0)
    tm = TurnStateMachine("sess-1", mono)
    arm_speaking(tm, mono)
    event = _event(iid="ok", mono=mono())
    evidence = _evidence(event)
    result = tm.interrupt(event, evidence=evidence)
    assert result.accepted is True
    assert result.drain is True
    assert tm.state == TurnState.DRAINING
    assert tm.canonical_runtime.state == VoiceStreamState.INTERRUPTING
    # Confirmation is not implied by request acceptance.
    assert tm.canonical_runtime.state != VoiceStreamState.INTERRUPTED
    # finish_drain before playback stop is denied
    assert tm.finish_drain().reason == StreamingReason.INVALID_INPUT
    assert tm.notify_playback_stopped("ok").accepted
    assert tm.canonical_runtime.state == VoiceStreamState.INTERRUPTED
    assert tm.finish_drain().accepted
    assert tm.state == TurnState.LISTENING


def test_failed_runtime_request_does_not_enter_draining():
    mono = clock()
    mono.advance(6.0)
    tm = TurnStateMachine("sess-1", mono)
    arm_speaking(tm, mono)
    event = _event(iid="fail", mono=mono())
    # Force runtime rejection by using speech before playback while facade correlation passes.
    playback_ns = tm.canonical_runtime._playback_started_ns
    evidence = _evidence(event, speech_ns=max(0, playback_ns - 50))
    # speech_observed <= observed is required by evidence ctor; keep speech <= obs.
    result = tm.interrupt(event, evidence=evidence)
    assert result.accepted is False
    assert result.drain is False
    assert tm.state == TurnState.ASSISTANT_SPEAKING
    assert tm.canonical_runtime.state == VoiceStreamState.ASSISTANT_SPEAKING


def test_confirm_only_via_playback_stopped_path():
    mono = clock()
    mono.advance(7.0)
    tm = TurnStateMachine("sess-1", mono)
    arm_speaking(tm, mono)
    event = _event(iid="c1", mono=mono())
    assert tm.interrupt(event, evidence=_evidence(event)).accepted
    # Wrong id
    assert tm.notify_playback_stopped("other").reason == StreamingReason.CORRELATION_MISMATCH
    assert tm.canonical_runtime.state == VoiceStreamState.INTERRUPTING
    assert tm.notify_playback_stopped("c1", bytes_played_before_stop=12).accepted
    assert tm.canonical_runtime.state == VoiceStreamState.INTERRUPTED


def test_interrupt_repr_and_events_omit_identifiers():
    mono = clock()
    mono.advance(8.0)
    tm = TurnStateMachine("sess-1", mono)
    arm_speaking(tm, mono)
    event = _event(iid="secret_interrupt", mono=mono())
    evidence = _evidence(event)
    assert "secret_interrupt" not in repr(event)
    assert "speaker_id" not in repr(evidence)
    tm.interrupt(event, evidence=evidence)
    blob = repr(tm.canonical_runtime.get_history()) + repr(tm.snapshot())
    assert "secret_interrupt" not in blob
    assert "transcript" not in blob.lower()


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
    assert buf.push("c", key="k3", enqueued_mono=0.2, byte_estimate=10).accepted
    assert buf.dropped >= 1
    assert buf.size == 2
    big = buf.push("z", key="k4", enqueued_mono=0.3, byte_estimate=10_000)
    assert big.reason == StreamingReason.BUFFER_EXHAUSTED
    summary = buf.summary(1.0)
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



def test_facade_source_has_no_private_active_assigns():
    from pathlib import Path
    source = Path("core/streaming_voice/turn.py").read_text(encoding="utf-8")
    assert "_runtime._active_utterance_id" not in source
    assert "_runtime._active_response_id" not in source


def test_facade_uses_public_playback_bind_api():
    mono = clock()
    mono.advance(1.0)
    tm = TurnStateMachine("sess-1", mono)
    arm_speaking(tm, mono)
    assert tm.canonical_runtime.interruption_target_id() == "a1"
    # Identical begin would conflict at facade seen_utt; bind idempotent at runtime:
    assert tm.canonical_runtime.bind_assistant_playback("a1", "r1") is True
    # Conflicting bind fails and does not change target
    assert tm.canonical_runtime.bind_assistant_playback("aX", "rX") is False
    assert tm.canonical_runtime.interruption_target_id() == "a1"
    event = _event(iid="bind-ok", mono=mono())
    assert tm.interrupt(event, evidence=_evidence(event)).accepted
    assert tm.notify_playback_stopped("bind-ok").accepted
    assert tm.finish_drain().accepted
    assert tm.canonical_runtime.interruption_target_id() == "assistant_playback"


def test_cancel_and_goodbye_clear_playback_correlation():
    mono = clock()
    mono.advance(2.0)
    tm = TurnStateMachine("sess-1", mono)
    arm_speaking(tm, mono)
    assert tm.canonical_runtime.interruption_target_id() == "a1"
    assert tm.cancel().accepted
    assert tm.canonical_runtime.interruption_target_id() == "assistant_playback"

    tm2 = TurnStateMachine("sess-1", mono)
    arm_speaking(tm2, mono)
    assert tm2.goodbye().accepted
    assert tm2.canonical_runtime.interruption_target_id() == "assistant_playback"


def test_wrong_response_complete_cannot_clear_correlation():
    mono = clock()
    mono.advance(3.0)
    tm = TurnStateMachine("sess-1", mono)
    arm_speaking(tm, mono)
    bad = tm.complete_assistant_response(response_id="wrong")
    assert bad.reason == StreamingReason.CORRELATION_MISMATCH
    assert tm.canonical_runtime.interruption_target_id() == "a1"


def test_failed_bind_does_not_update_facade_ids(monkeypatch):
    mono = clock()
    mono.advance(4.0)
    tm = TurnStateMachine("sess-1", mono)
    assert tm.observe_sleeping_audio().accepted
    assert tm.submit_wake(wake()).accepted
    assert tm.begin_user_speech("u1").accepted
    assert tm.end_user_speech().accepted
    monkeypatch.setattr(tm.canonical_runtime, "bind_assistant_playback", lambda *a, **k: False)
    denied = tm.begin_assistant_response(utterance_id="a9", response_id="r9")
    assert denied.accepted is False
    assert tm._assistant_utt is None
    assert tm._response_id is None
    assert tm.state == TurnState.ASSISTANT_THINKING
