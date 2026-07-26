"""Adversarial VAD state-machine tests."""

from __future__ import annotations

import pytest

from core.streaming_voice import (
    AudioFrameMeta,
    StreamingReason,
    VadConfig,
    VadState,
    VadStateMachine,
)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def frame(seq: int, energy: int, mono: float, fid: str | None = None) -> AudioFrameMeta:
    return AudioFrameMeta(
        frame_id=fid or f"f{seq}",
        session_id="sess-1",
        captured_at_mono=mono,
        sequence=seq,
        duration_ms=20,
        energy_bucket=energy,
    )


def test_speech_path_to_complete_and_boundaries():
    clock = Clock()
    vad = VadStateMachine(clock, VadConfig(debounce_ms=40, min_speech_ms=40, silence_end_ms=60, max_utterance_ms=200))
    assert vad.state == VadState.IDLE
    # possible
    assert vad.ingest(frame(0, 5, 0.0)).accepted
    assert vad.state == VadState.POSSIBLE_SPEECH
    assert vad.ingest(frame(1, 5, 0.02)).accepted
    assert vad.state == VadState.SPEAKING
    # silence into ending then complete
    assert vad.ingest(frame(2, 0, 0.04)).accepted
    assert vad.state in (VadState.SPEAKING, VadState.ENDING)
    for seq, mono in ((3, 0.06), (4, 0.08), (5, 0.10), (6, 0.12)):
        decision = vad.ingest(frame(seq, 0, mono))
        if vad.state == VadState.COMPLETE:
            assert decision.accepted or decision.reason == StreamingReason.CLOSED
            break
        assert decision.accepted
    assert vad.state == VadState.COMPLETE


def test_duplicate_replay_out_of_order_stale():
    clock = Clock()
    vad = VadStateMachine(clock)
    assert vad.ingest(frame(1, 5, 1.0)).accepted
    assert vad.ingest(frame(1, 5, 1.1, fid="f1b")).reason == StreamingReason.REPLAYED
    assert vad.ingest(frame(0, 5, 1.2)).reason == StreamingReason.OUT_OF_ORDER
    assert vad.ingest(frame(2, 5, 0.5)).reason == StreamingReason.STALE_FRAME
    assert vad.ingest(frame(2, 5, 1.3, fid="f1")).reason == StreamingReason.DUPLICATE


def test_max_utterance_via_tick():
    clock = Clock()
    vad = VadStateMachine(clock, VadConfig(debounce_ms=20, min_speech_ms=20, max_utterance_ms=50))
    vad.ingest(frame(0, 5, 0.0))
    vad.ingest(frame(1, 5, 0.02))
    assert vad.state == VadState.SPEAKING
    clock.advance(0.1)
    decision = vad.tick()
    assert decision.reason == StreamingReason.MAX_DURATION
    assert vad.state == VadState.COMPLETE


def test_cancel_every_active_state():
    for setup in (
        lambda v: None,  # IDLE
        lambda v: (v.ingest(frame(0, 5, 0.0)),),
        lambda v: (v.ingest(frame(0, 5, 0.0)), v.ingest(frame(1, 5, 0.02))),
    ):
        clock = Clock()
        vad = VadStateMachine(clock, VadConfig(debounce_ms=20))
        setup(vad)
        snap = vad.cancel()
        assert snap.state == VadState.CANCELLED
        assert vad.ingest(frame(9, 5, 9.0, fid="late")).reason == StreamingReason.CANCELLED
