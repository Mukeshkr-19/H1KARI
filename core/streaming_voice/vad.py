"""Deterministic VAD state machine with injected monotonic clock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Set

from .contracts import (
    AudioFrameMeta,
    StreamingDecision,
    StreamingReason,
    VadState,
    validate_mono,
)

MonoClock = Callable[[], float]

# Speech energy threshold bucket (discrete, not DSP)
_SPEECH_ENERGY_MIN = 3


@dataclass(frozen=True)
class VadConfig:
    debounce_ms: int = 80
    min_speech_ms: int = 200
    silence_end_ms: int = 600
    max_utterance_ms: int = 30_000
    pre_roll_frames: int = 5

    def __post_init__(self) -> None:
        for name in ("debounce_ms", "min_speech_ms", "silence_end_ms", "max_utterance_ms", "pre_roll_frames"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"invalid_{name}")
        if self.pre_roll_frames > 32:
            raise ValueError("invalid_pre_roll_frames")
        if self.debounce_ms > 5_000 or self.min_speech_ms > 10_000 or self.silence_end_ms > 10_000 or not 1 <= self.max_utterance_ms <= 300_000:
            raise ValueError("voice_duration_bound_exceeded")


@dataclass(frozen=True, repr=False)
class VadSnapshot:
    state: VadState
    utterance_started_mono: Optional[float]
    speech_ms: int
    silence_ms: int
    pre_roll_count: int
    last_frame_sequence: int
    reason: StreamingReason

    def __repr__(self) -> str:
        return f"VadSnapshot(state={self.state.value!r}, reason={self.reason.value!r})"


class VadStateMachine:
    """Deterministic VAD. No audio I/O. Frames are metadata only."""

    def __init__(self, clock: MonoClock, config: Optional[VadConfig] = None) -> None:
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        self._clock = clock
        self._config = config or VadConfig()
        self._state = VadState.IDLE
        self._utterance_started: Optional[float] = None
        self._speech_ms = 0
        self._silence_ms = 0
        self._possible_ms = 0
        self._pre_roll = 0
        self._last_seq = -1
        self._last_mono = -1.0
        self._seen_frames: Set[str] = set()
        self._cancelled = False
        self._session_id: Optional[str] = None

    @property
    def state(self) -> VadState:
        return self._state

    def snapshot(self) -> VadSnapshot:
        return VadSnapshot(
            state=self._state,
            utterance_started_mono=self._utterance_started,
            speech_ms=self._speech_ms,
            silence_ms=self._silence_ms,
            pre_roll_count=self._pre_roll,
            last_frame_sequence=self._last_seq,
            reason=StreamingReason.CANCELLED if self._cancelled else StreamingReason.OK,
        )

    def cancel(self, *, reason: StreamingReason = StreamingReason.CANCELLED) -> VadSnapshot:
        self._cancelled = True
        self._state = VadState.CANCELLED
        snap = self.snapshot()
        # reason reflected via CANCELLED state
        return VadSnapshot(
            state=VadState.CANCELLED,
            utterance_started_mono=self._utterance_started,
            speech_ms=self._speech_ms,
            silence_ms=self._silence_ms,
            pre_roll_count=self._pre_roll,
            last_frame_sequence=self._last_seq,
            reason=reason if reason != StreamingReason.OK else StreamingReason.CANCELLED,
        )

    def reset(self) -> None:
        self._state = VadState.IDLE
        self._utterance_started = None
        self._speech_ms = 0
        self._silence_ms = 0
        self._possible_ms = 0
        self._pre_roll = 0
        self._last_seq = -1
        self._last_mono = -1.0
        self._seen_frames.clear()
        self._cancelled = False
        self._session_id = None

    def ingest(self, frame: AudioFrameMeta) -> StreamingDecision:
        if self._state == VadState.CANCELLED:
            return StreamingDecision(False, StreamingReason.CANCELLED, self._state.value)
        if self._state == VadState.COMPLETE:
            return StreamingDecision(False, StreamingReason.CLOSED, self._state.value)

        if not isinstance(frame, AudioFrameMeta):
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self._state.value)

        if self._cancelled or self._state == VadState.CANCELLED:
            return StreamingDecision(False, StreamingReason.CANCELLED, VadState.CANCELLED.value)

        if self._session_id is None:
            self._session_id = frame.session_id
        elif frame.session_id != self._session_id:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self._state.value)

        if frame.frame_id in self._seen_frames:
            return StreamingDecision(False, StreamingReason.DUPLICATE, self._state.value)
        if self._last_seq >= 0 and frame.sequence < self._last_seq:
            return StreamingDecision(False, StreamingReason.OUT_OF_ORDER, self._state.value)
        if self._last_seq >= 0 and frame.sequence == self._last_seq:
            return StreamingDecision(False, StreamingReason.REPLAYED, self._state.value)
        if self._last_mono >= 0 and frame.captured_at_mono < self._last_mono:
            return StreamingDecision(False, StreamingReason.STALE_FRAME, self._state.value)

        # Bound seen set
        if len(self._seen_frames) >= 4096:
            return StreamingDecision(False, StreamingReason.BUFFER_EXHAUSTED, self._state.value)
        self._seen_frames.add(frame.frame_id)
        self._last_seq = frame.sequence
        self._last_mono = frame.captured_at_mono

        is_speech = frame.energy_bucket >= _SPEECH_ENERGY_MIN
        dur = frame.duration_ms

        if self._state == VadState.IDLE:
            if is_speech:
                self._possible_ms = dur
                self._pre_roll = min(1, self._config.pre_roll_frames)
                self._state = VadState.POSSIBLE_SPEECH
            else:
                if self._config.pre_roll_frames > 0:
                    self._pre_roll = min(self._pre_roll + 1, self._config.pre_roll_frames)
            return StreamingDecision(True, StreamingReason.OK, self._state.value)

        if self._state == VadState.POSSIBLE_SPEECH:
            if is_speech:
                self._possible_ms += dur
                if self._possible_ms >= self._config.debounce_ms:
                    self._state = VadState.SPEAKING
                    self._utterance_started = frame.captured_at_mono
                    self._speech_ms = self._possible_ms
                    self._silence_ms = 0
            else:
                self._state = VadState.IDLE
                self._possible_ms = 0
            return StreamingDecision(True, StreamingReason.OK, self._state.value)

        if self._state == VadState.SPEAKING:
            if is_speech:
                self._speech_ms += dur
                self._silence_ms = 0
            else:
                self._silence_ms += dur
                if self._silence_ms >= max(1, self._config.silence_end_ms // 3):
                    self._state = VadState.ENDING
            if self._speech_ms >= self._config.max_utterance_ms:
                self._state = VadState.COMPLETE
                return StreamingDecision(True, StreamingReason.MAX_DURATION, self._state.value)
            return StreamingDecision(True, StreamingReason.OK, self._state.value)

        if self._state == VadState.ENDING:
            if is_speech:
                self._state = VadState.SPEAKING
                self._speech_ms += dur
                self._silence_ms = 0
            else:
                self._silence_ms += dur
                if self._silence_ms >= self._config.silence_end_ms:
                    if self._speech_ms < self._config.min_speech_ms:
                        self._state = VadState.IDLE
                        self._speech_ms = 0
                        self._silence_ms = 0
                        self._utterance_started = None
                        return StreamingDecision(True, StreamingReason.OK, self._state.value)
                    self._state = VadState.COMPLETE
                    return StreamingDecision(True, StreamingReason.OK, self._state.value)
            if self._speech_ms >= self._config.max_utterance_ms:
                self._state = VadState.COMPLETE
                return StreamingDecision(True, StreamingReason.MAX_DURATION, self._state.value)
            return StreamingDecision(True, StreamingReason.OK, self._state.value)

        return StreamingDecision(False, StreamingReason.INVALID_INPUT, self._state.value)

    def tick(self) -> StreamingDecision:
        """Advance time-based timeout checks using injected clock."""
        if self._state in (VadState.COMPLETE, VadState.CANCELLED, VadState.IDLE):
            return StreamingDecision(True, StreamingReason.OK, self._state.value)
        now = validate_mono(self._clock(), "clock")
        if self._last_mono >= 0 and now < self._last_mono:
            return StreamingDecision(False, StreamingReason.STALE_FRAME, self._state.value)
        if self._utterance_started is not None:
            elapsed_ms = int((now - self._utterance_started) * 1000)
            if elapsed_ms >= self._config.max_utterance_ms and self._state in (
                VadState.SPEAKING,
                VadState.ENDING,
                VadState.POSSIBLE_SPEECH,
            ):
                self._state = VadState.COMPLETE
                return StreamingDecision(True, StreamingReason.MAX_DURATION, self._state.value)
        return StreamingDecision(True, StreamingReason.OK, self._state.value)


__all__ = ["VadConfig", "VadSnapshot", "VadStateMachine", "MonoClock"]
