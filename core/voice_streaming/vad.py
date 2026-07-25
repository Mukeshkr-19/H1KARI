"""Deterministic Voice Activity Detection (VAD) state engine and hysteresis logic."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from core.voice_streaming.contracts import (
    validate_confidence,
    validate_monotonic_ns,
    validate_stream_id,
)


class VADState(str, Enum):
    SILENCE = "silence"
    POSSIBLE_SPEECH = "possible_speech"
    CONFIRMED_SPEECH = "confirmed_speech"
    POSSIBLE_END = "possible_end"
    CONFIRMED_END = "confirmed_end"
    INTERRUPTION_CANDIDATE = "interruption_candidate"
    CLOSED = "closed"


@dataclass(frozen=True)
class VADConfig:
    """Configuration for VAD thresholds, frame counters, and duration bounds."""

    speech_start_threshold: float = 0.6
    speech_stop_threshold: float = 0.3
    interruption_threshold: float = 0.85
    min_speech_frames: int = 3
    min_silence_frames: int = 5
    min_interruption_frames: int = 2
    max_utterance_duration_ms: float = 30000.0
    pre_roll_ms: float = 200.0
    post_roll_ms: float = 300.0
    max_history_events: int = 256

    def __post_init__(self) -> None:
        object.__setattr__(self, "speech_start_threshold", validate_confidence(self.speech_start_threshold))
        object.__setattr__(self, "speech_stop_threshold", validate_confidence(self.speech_stop_threshold))
        object.__setattr__(self, "interruption_threshold", validate_confidence(self.interruption_threshold))

        if self.speech_stop_threshold > self.speech_start_threshold:
            raise ValueError("speech_stop_threshold cannot exceed speech_start_threshold")

        if isinstance(self.min_speech_frames, bool) or not isinstance(self.min_speech_frames, int) or self.min_speech_frames <= 0:
            raise ValueError("min_speech_frames must be a positive integer")

        if isinstance(self.min_silence_frames, bool) or not isinstance(self.min_silence_frames, int) or self.min_silence_frames <= 0:
            raise ValueError("min_silence_frames must be a positive integer")

        if isinstance(self.min_interruption_frames, bool) or not isinstance(self.min_interruption_frames, int) or self.min_interruption_frames <= 0:
            raise ValueError("min_interruption_frames must be a positive integer")

        if isinstance(self.max_utterance_duration_ms, bool) or not isinstance(self.max_utterance_duration_ms, (int, float)) or self.max_utterance_duration_ms <= 0:
            raise ValueError("max_utterance_duration_ms must be a positive number")
        if not math.isfinite(self.max_utterance_duration_ms):
            raise ValueError("max_utterance_duration_ms must be finite")
        for name in ("pre_roll_ms", "post_roll_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative finite number")
        if isinstance(self.max_history_events, bool) or not isinstance(self.max_history_events, int) or self.max_history_events <= 0:
            raise ValueError("max_history_events must be a positive integer")


@dataclass(frozen=True)
class VADFrameMeasurement:
    """Caller-supplied energy and probability measurement for one audio frame."""

    sequence_id: int
    monotonic_ns: int
    speech_probability: float
    energy_db: float = -60.0
    frame_duration_ms: float = 20.0

    def __post_init__(self) -> None:
        if isinstance(self.sequence_id, bool) or not isinstance(self.sequence_id, int) or self.sequence_id < 0:
            raise ValueError("sequence_id must be a non-negative integer")
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        object.__setattr__(self, "speech_probability", validate_confidence(self.speech_probability))
        if isinstance(self.energy_db, bool) or not isinstance(self.energy_db, (int, float)):
            raise TypeError("energy_db must be a float")
        if not math.isfinite(self.energy_db):
            raise ValueError("energy_db must be finite")
        if isinstance(self.frame_duration_ms, bool) or not isinstance(self.frame_duration_ms, (int, float)) or not math.isfinite(self.frame_duration_ms) or self.frame_duration_ms <= 0:
            raise ValueError("frame_duration_ms must be a positive number")


@dataclass(frozen=True)
class VADStateTransitionEvent:
    """Immutable record of a VAD state transition."""

    stream_id: str
    old_state: VADState
    new_state: VADState
    measurement: VADFrameMeasurement
    monotonic_ns: int
    reason: str
    utterance_duration_ms: float = 0.0


class VADEngineState:
    """Deterministic VAD state machine enforcing hysteresis and counter rules."""

    def __init__(self, stream_id: str, config: Optional[VADConfig] = None) -> None:
        self.stream_id = validate_stream_id(stream_id)
        self.config = config or VADConfig()

        self._state: VADState = VADState.SILENCE
        self._last_sequence_id: Optional[int] = None
        self._last_monotonic_ns: Optional[int] = None

        self._consecutive_speech_frames: int = 0
        self._consecutive_silence_frames: int = 0
        self._consecutive_interruption_frames: int = 0

        self._speech_start_monotonic_ns: Optional[int] = None
        self._current_utterance_duration_ms: float = 0.0
        self._transition_history: List[VADStateTransitionEvent] = []

    @property
    def current_state(self) -> VADState:
        return self._state

    @property
    def utterance_duration_ms(self) -> float:
        return self._current_utterance_duration_ms

    def get_history(self) -> Tuple[VADStateTransitionEvent, ...]:
        return tuple(self._transition_history)

    def process_measurement(
        self,
        measurement: VADFrameMeasurement,
        *,
        assistant_speaking: bool = False,
    ) -> Tuple[VADState, Optional[VADStateTransitionEvent]]:
        """Process one caller-supplied VAD measurement frame.

        Enforces non-decreasing sequence IDs, monotonic timestamps, hysteresis bounds,
        false-start detection, maximum utterance limits, and interruption candidates.
        """
        if self._state == VADState.CLOSED:
            return VADState.CLOSED, None

        if not isinstance(measurement, VADFrameMeasurement):
            return self._state, None
        if not isinstance(assistant_speaking, bool):
            return self._state, None

        # Out-of-order or duplicate frame rejection
        if self._last_sequence_id is not None:
            if measurement.sequence_id <= self._last_sequence_id:
                return self._state, None
            if self._last_monotonic_ns is not None and measurement.monotonic_ns < self._last_monotonic_ns:
                return self._state, None

        self._last_sequence_id = measurement.sequence_id
        self._last_monotonic_ns = measurement.monotonic_ns

        old_state = self._state
        prob = measurement.speech_probability
        dur = measurement.frame_duration_ms

        transition_event: Optional[VADStateTransitionEvent] = None

        # State transition logic
        if self._state == VADState.SILENCE:
            if prob >= self.config.speech_start_threshold:
                self._consecutive_speech_frames = 1
                self._consecutive_silence_frames = 0
                self._speech_start_monotonic_ns = measurement.monotonic_ns
                self._current_utterance_duration_ms = dur
                if self._consecutive_speech_frames >= self.config.min_speech_frames:
                    self._state = VADState.CONFIRMED_SPEECH
                    transition_event = self._record_transition(
                        old_state, self._state, measurement, "min_speech_frames_reached"
                    )
                else:
                    self._state = VADState.POSSIBLE_SPEECH
                    transition_event = self._record_transition(
                        old_state, self._state, measurement, "speech_probability_above_start_threshold"
                    )
            else:
                self._consecutive_silence_frames += 1

        elif self._state == VADState.POSSIBLE_SPEECH:
            if prob >= self.config.speech_start_threshold:
                self._consecutive_speech_frames += 1
                self._current_utterance_duration_ms += dur
                if self._consecutive_speech_frames >= self.config.min_speech_frames:
                    self._state = VADState.CONFIRMED_SPEECH
                    transition_event = self._record_transition(
                        old_state, self._state, measurement, "min_speech_frames_reached"
                    )
                elif self._current_utterance_duration_ms >= self.config.max_utterance_duration_ms:
                    self._state = VADState.CONFIRMED_END
                    transition_event = self._record_transition(
                        old_state, self._state, measurement, "max_utterance_duration_exceeded"
                    )
            else:
                # False start handling!
                self._consecutive_speech_frames = 0
                self._consecutive_silence_frames = 1
                self._current_utterance_duration_ms = 0.0
                self._speech_start_monotonic_ns = None
                self._state = VADState.SILENCE
                transition_event = self._record_transition(
                    old_state, self._state, measurement, "false_start_dropped_below_threshold"
                )

        elif self._state == VADState.CONFIRMED_SPEECH:
            self._current_utterance_duration_ms += dur

            # Interruption candidate check if assistant is speaking
            if assistant_speaking and prob >= self.config.interruption_threshold:
                self._consecutive_interruption_frames += 1
                if self._consecutive_interruption_frames >= self.config.min_interruption_frames:
                    self._state = VADState.INTERRUPTION_CANDIDATE
                    transition_event = self._record_transition(
                        old_state, self._state, measurement, "interruption_threshold_reached"
                    )
                    return self._state, transition_event
            else:
                self._consecutive_interruption_frames = 0

            # Max utterance duration limit check
            if self._current_utterance_duration_ms >= self.config.max_utterance_duration_ms:
                self._state = VADState.CONFIRMED_END
                transition_event = self._record_transition(
                    old_state, self._state, measurement, "max_utterance_duration_exceeded"
                )
            elif prob < self.config.speech_stop_threshold:
                self._consecutive_silence_frames = 1
                self._state = VADState.POSSIBLE_END
                transition_event = self._record_transition(
                    old_state, self._state, measurement, "speech_probability_below_stop_threshold"
                )

        elif self._state == VADState.POSSIBLE_END:
            self._current_utterance_duration_ms += dur
            if self._current_utterance_duration_ms >= self.config.max_utterance_duration_ms:
                self._state = VADState.CONFIRMED_END
                transition_event = self._record_transition(
                    old_state, self._state, measurement, "max_utterance_duration_exceeded"
                )
            elif prob < self.config.speech_stop_threshold:
                self._consecutive_silence_frames += 1
                if self._consecutive_silence_frames >= self.config.min_silence_frames:
                    self._state = VADState.CONFIRMED_END
                    transition_event = self._record_transition(
                        old_state, self._state, measurement, "min_silence_frames_reached"
                    )
            else:
                # Speech resumed before confirmed end -> return to CONFIRMED_SPEECH
                self._consecutive_silence_frames = 0
                self._state = VADState.CONFIRMED_SPEECH
                transition_event = self._record_transition(
                    old_state, self._state, measurement, "speech_resumed_during_possible_end"
                )

        elif self._state == VADState.INTERRUPTION_CANDIDATE:
            # Transitions out of interruption candidate back to speech or end
            if prob < self.config.speech_stop_threshold:
                self._state = VADState.POSSIBLE_END
                transition_event = self._record_transition(
                    old_state, self._state, measurement, "interruption_candidate_silence"
                )
            else:
                self._state = VADState.CONFIRMED_SPEECH
                transition_event = self._record_transition(
                    old_state, self._state, measurement, "interruption_candidate_continues_as_speech"
                )

        elif self._state == VADState.CONFIRMED_END:
            # New utterance after confirmed end
            if prob >= self.config.speech_start_threshold:
                self._consecutive_speech_frames = 1
                self._consecutive_silence_frames = 0
                self._speech_start_monotonic_ns = measurement.monotonic_ns
                self._current_utterance_duration_ms = dur
                if self._consecutive_speech_frames >= self.config.min_speech_frames:
                    self._state = VADState.CONFIRMED_SPEECH
                    transition_event = self._record_transition(
                        old_state, self._state, measurement, "min_speech_frames_reached"
                    )
                else:
                    self._state = VADState.POSSIBLE_SPEECH
                    transition_event = self._record_transition(
                        old_state, self._state, measurement, "new_utterance_after_confirmed_end"
                    )

        return self._state, transition_event

    def reset(self) -> None:
        """Reset internal state machine to SILENCE."""
        self._state = VADState.SILENCE
        self._last_sequence_id = None
        self._last_monotonic_ns = None
        self._consecutive_speech_frames = 0
        self._consecutive_silence_frames = 0
        self._consecutive_interruption_frames = 0
        self._speech_start_monotonic_ns = None
        self._current_utterance_duration_ms = 0.0
        self._transition_history.clear()

    def close(self) -> None:
        """Close the VAD engine."""
        self._state = VADState.CLOSED

    def _record_transition(
        self,
        old_state: VADState,
        new_state: VADState,
        measurement: VADFrameMeasurement,
        reason: str,
    ) -> VADStateTransitionEvent:
        event = VADStateTransitionEvent(
            stream_id=self.stream_id,
            old_state=old_state,
            new_state=new_state,
            measurement=measurement,
            monotonic_ns=measurement.monotonic_ns,
            reason=reason,
            utterance_duration_ms=self._current_utterance_duration_ms,
        )
        self._transition_history.append(event)
        if len(self._transition_history) > self.config.max_history_events:
            del self._transition_history[:-self.config.max_history_events]
        return event
