"""Utterance endpointing gate — adapted architecture from Omi vad_gate.py.

Aligns with HIKARI VADEngineState contracts. Does not wake, authorize, or
execute commands. Hangover is assistant-short (not Omi meeting multi-second).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Deque, Optional, Tuple

from core.voice_capture.vad_backend import SpeechProbabilityBackend, UnavailableVadBackend
from core.voice_streaming.vad import VADConfig, VADEngineState, VADFrameMeasurement, VADState


class EndpointEvent(StrEnum):
    NONE = "none"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    FINALIZED = "finalized"
    CANCELLED = "cancelled"
    FAILED = "failed"
    MAX_DURATION = "max_duration"


@dataclass(frozen=True, repr=False)
class EndpointTickResult:
    state: VADState
    event: EndpointEvent
    speech_probability: float
    utterance_pcm: bytes = b""
    available: bool = True

    def __repr__(self) -> str:
        return (
            f"EndpointTickResult(state={self.state.value!r}, event={self.event.value!r}, "
            f"bytes={len(self.utterance_pcm)}, available={self.available})"
        )


class UtteranceEndpointGate:
    """Buffers PCM around VAD confirmed speech and finalizes on silence."""

    def __init__(
        self,
        *,
        stream_id: str,
        backend: Optional[SpeechProbabilityBackend] = None,
        vad_config: Optional[VADConfig] = None,
        pre_roll_ms: float = 300.0,
        hangover_ms: float = 450.0,
        max_utterance_bytes: int = 480_000,
    ) -> None:
        if isinstance(pre_roll_ms, bool) or not isinstance(pre_roll_ms, (int, float)):
            raise ValueError("invalid_pre_roll_ms")
        if not math.isfinite(float(pre_roll_ms)) or pre_roll_ms < 0 or pre_roll_ms > 2_000:
            raise ValueError("invalid_pre_roll_ms")
        if isinstance(hangover_ms, bool) or not isinstance(hangover_ms, (int, float)):
            raise ValueError("invalid_hangover_ms")
        if not math.isfinite(float(hangover_ms)) or hangover_ms < 40 or hangover_ms > 5_000:
            raise ValueError("invalid_hangover_ms")
        if isinstance(max_utterance_bytes, bool) or not isinstance(max_utterance_bytes, int):
            raise ValueError("invalid_max_utterance_bytes")
        if max_utterance_bytes < 3_200 or max_utterance_bytes > 1_920_000:
            raise ValueError("invalid_max_utterance_bytes")
        self._stream_id = stream_id
        self._backend = backend or UnavailableVadBackend()
        # Assistant hangover: shorter than Omi meeting default (4000ms).
        cfg = vad_config or VADConfig(
            speech_start_threshold=0.5,
            speech_stop_threshold=0.4,
            min_speech_frames=2,
            pre_roll_ms=pre_roll_ms,
            post_roll_ms=hangover_ms,
            min_silence_frames=max(3, int(hangover_ms / 32)),
        )
        self._engine = VADEngineState(stream_id, cfg)
        self._pre_roll: Deque[bytes] = deque()
        self._pre_roll_ms = 0.0
        self._target_pre_roll_ms = float(cfg.pre_roll_ms)
        self._target_energy_silence_ms = float(hangover_ms)
        self._energy_silence_ms = 0.0
        self._utterance = bytearray()
        self._capturing = False
        self._sequence = 0
        self._max_bytes = max_utterance_bytes
        self._pcm_remainder = b""
        self._cancelled = False

    @property
    def available(self) -> bool:
        return bool(getattr(self._backend, "available", False))

    @property
    def state(self) -> VADState:
        return self._engine.current_state

    def reset(self) -> None:
        self._engine.reset()
        self._backend.reset()
        self._pre_roll.clear()
        self._pre_roll_ms = 0.0
        self._utterance.clear()
        self._capturing = False
        self._pcm_remainder = b""
        self._cancelled = False
        self._energy_silence_ms = 0.0

    def cancel(self) -> EndpointTickResult:
        self._cancelled = True
        self._utterance.clear()
        self._capturing = False
        self._pre_roll.clear()
        self._pcm_remainder = b""
        self._energy_silence_ms = 0.0
        return EndpointTickResult(self._engine.current_state, EndpointEvent.CANCELLED, 0.0, b"", self.available)

    def process_frame(
        self,
        pcm: bytes,
        *,
        monotonic_ns: int,
        sample_rate: int = 16_000,
        frame_duration_ms: Optional[float] = None,
    ) -> EndpointTickResult:
        if self._cancelled:
            return EndpointTickResult(VADState.CLOSED, EndpointEvent.CANCELLED, 0.0, b"", self.available)
        if not self.available:
            return EndpointTickResult(VADState.SILENCE, EndpointEvent.FAILED, 0.0, b"", False)

        # Remainder handling for odd splits.
        data = self._pcm_remainder + pcm
        usable = len(data) - (len(data) % 2)
        self._pcm_remainder = data[usable:]
        pcm = data[:usable]
        if not pcm:
            return EndpointTickResult(self._engine.current_state, EndpointEvent.NONE, 0.0, b"", True)
        if sample_rate != 16_000:
            return EndpointTickResult(self._engine.current_state, EndpointEvent.FAILED, 0.0, b"", False)
        derived_duration_ms = (len(pcm) / (sample_rate * 2)) * 1000.0
        import array

        samples = array.array("h")
        samples.frombytes(pcm)
        rms = math.sqrt(sum(int(sample) ** 2 for sample in samples) / len(samples)) / 32768.0
        if frame_duration_ms is not None:
            if (
                isinstance(frame_duration_ms, bool)
                or not isinstance(frame_duration_ms, (int, float))
                or not math.isfinite(float(frame_duration_ms))
                or abs(float(frame_duration_ms) - derived_duration_ms) > 0.01
            ):
                return EndpointTickResult(self._engine.current_state, EndpointEvent.FAILED, 0.0, b"", False)
        frame_duration_ms = derived_duration_ms

        result = self._backend.process_pcm16_mono(pcm, sample_rate=sample_rate)
        if not result.available:
            return EndpointTickResult(self._engine.current_state, EndpointEvent.FAILED, 0.0, b"", False)

        if not result.measurement_ready:
            if self._capturing:
                self._utterance.extend(pcm)
                if rms <= 0.025:
                    self._energy_silence_ms += derived_duration_ms
                else:
                    self._energy_silence_ms = 0.0
            else:
                self._pre_roll.append(pcm)
                self._pre_roll_ms += derived_duration_ms
                while self._pre_roll_ms > self._target_pre_roll_ms and self._pre_roll:
                    dropped = self._pre_roll.popleft()
                    self._pre_roll_ms -= (len(dropped) / (sample_rate * 2)) * 1000.0
            if self._capturing and self._energy_silence_ms >= self._target_energy_silence_ms:
                out_pcm = bytes(self._utterance[: self._max_bytes])
                self._utterance.clear()
                self._capturing = False
                self._energy_silence_ms = 0.0
                return EndpointTickResult(
                    self._engine.current_state,
                    EndpointEvent.FINALIZED,
                    result.speech_probability,
                    out_pcm,
                    True,
                )
            if len(self._utterance) > self._max_bytes:
                out_pcm = bytes(self._utterance[: self._max_bytes])
                self._utterance.clear()
                self._capturing = False
                return EndpointTickResult(
                    self._engine.current_state,
                    EndpointEvent.MAX_DURATION,
                    result.speech_probability,
                    out_pcm,
                    True,
                )
            return EndpointTickResult(
                self._engine.current_state,
                EndpointEvent.NONE,
                result.speech_probability,
                b"",
                True,
            )

        self._sequence += 1
        measurement = VADFrameMeasurement(
            sequence_id=self._sequence,
            monotonic_ns=monotonic_ns,
            speech_probability=result.speech_probability,
            frame_duration_ms=result.measurement_duration_ms or frame_duration_ms,
        )
        old = self._engine.current_state
        new_state, transition = self._engine.process_measurement(measurement)
        event = EndpointEvent.NONE
        out_pcm = b""

        # Maintain pre-roll while silent / possible.
        chunk_ms = frame_duration_ms
        if not self._capturing:
            self._pre_roll.append(pcm)
            self._pre_roll_ms += chunk_ms
            while self._pre_roll_ms > self._target_pre_roll_ms and self._pre_roll:
                dropped = self._pre_roll.popleft()
                self._pre_roll_ms -= (len(dropped) / (sample_rate * 2)) * 1000.0

        if transition is not None:
            if new_state == VADState.CONFIRMED_SPEECH and old in {
                VADState.SILENCE,
                VADState.POSSIBLE_SPEECH,
            }:
                self._capturing = True
                self._energy_silence_ms = 0.0
                for preroll in self._pre_roll:
                    self._utterance.extend(preroll)
                self._pre_roll.clear()
                self._pre_roll_ms = 0.0
                event = EndpointEvent.SPEECH_START
            elif new_state == VADState.CONFIRMED_END:
                if self._capturing:
                    self._utterance.extend(pcm)
                out_pcm = bytes(self._utterance)
                self._utterance.clear()
                self._capturing = False
                event = EndpointEvent.FINALIZED
            elif new_state == VADState.POSSIBLE_END:
                event = EndpointEvent.SPEECH_END
                if self._capturing:
                    self._utterance.extend(pcm)
            elif self._capturing:
                self._utterance.extend(pcm)
        elif self._capturing:
            self._utterance.extend(pcm)

        if self._capturing:
            if rms <= 0.025:
                self._energy_silence_ms += derived_duration_ms
            else:
                self._energy_silence_ms = 0.0
            if self._energy_silence_ms >= self._target_energy_silence_ms:
                out_pcm = bytes(self._utterance[: self._max_bytes])
                self._utterance.clear()
                self._capturing = False
                self._energy_silence_ms = 0.0
                event = EndpointEvent.FINALIZED

        if len(self._utterance) > self._max_bytes:
            out_pcm = bytes(self._utterance[: self._max_bytes])
            self._utterance.clear()
            self._capturing = False
            event = EndpointEvent.MAX_DURATION

        return EndpointTickResult(new_state, event, result.speech_probability, out_pcm, True)
