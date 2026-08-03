"""Contracts, protocols, and validated correlation types for VoiceSessionCoordinator.

Zero side-effects on import. Strict validation, privacy-preserving reprs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Optional, Protocol, runtime_checkable

_MAX_ID_LEN = 128


def validate_non_empty_str(value: object, name: str) -> str:
    """Validate a non-empty string identifier under bounded length."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} cannot be empty or whitespace-only")
    if len(cleaned) > _MAX_ID_LEN:
        raise ValueError(f"{name} exceeds maximum length of {_MAX_ID_LEN}")
    return cleaned


def validate_session_id(session_id: object) -> str:
    return validate_non_empty_str(session_id, "session_id")


def validate_utterance_id(utterance_id: object) -> str:
    return validate_non_empty_str(utterance_id, "utterance_id")


def validate_response_id(response_id: object) -> str:
    return validate_non_empty_str(response_id, "response_id")


def validate_playback_id(playback_id: object) -> str:
    return validate_non_empty_str(playback_id, "playback_id")


def validate_sequence(sequence: object, name: str = "event_sequence") -> int:
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise TypeError(f"{name} must be an integer")
    if sequence < 0:
        raise ValueError(f"{name} cannot be negative")
    return sequence


def validate_monotonic_ns(ts_ns: object, name: str = "monotonic_ns") -> int:
    if isinstance(ts_ns, bool) or not isinstance(ts_ns, int):
        raise TypeError(f"{name} must be an integer (nanoseconds)")
    if ts_ns < 0:
        raise ValueError(f"{name} cannot be negative")
    return ts_ns


def validate_generation(generation: object, name: str = "cancellation_generation") -> int:
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise TypeError(f"{name} must be an integer")
    if generation < 0:
        raise ValueError(f"{name} cannot be negative")
    return generation


def validate_capture_frame_bound(value: object, name: str = "max_capture_frames") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0 or value > 1000:
        raise ValueError(f"{name} must be between 1 and 1000")
    return value


def validate_capture_duration_bound(value: object, name: str = "max_capture_duration_s") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    val = float(value)
    import math
    if math.isnan(val) or math.isinf(val) or val <= 0.0 or val > 60.0:
        raise ValueError(f"{name} must be a finite number between 0.0 and 60.0")
    return val


@dataclass(frozen=True, repr=False)
class AudioFrame:
    """Raw audio frame item with timestamp."""

    data: bytes
    sample_rate: int
    channels: int
    monotonic_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("data must be bytes")
        if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, int) or self.sample_rate <= 0:
            raise ValueError("sample_rate must be a positive integer")
        if isinstance(self.channels, bool) or not isinstance(self.channels, int) or self.channels <= 0:
            raise ValueError("channels must be a positive integer")
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))

    def __repr__(self) -> str:
        return f"<AudioFrame bytes={len(self.data)} rate={self.sample_rate} ch={self.channels}>"


@dataclass(frozen=True, repr=False)
class OwnerVerificationResult:
    """Speaker identity verification decision."""

    is_owner: bool
    confidence: float
    speaker_id: Optional[str] = None
    reason: str = "ok"

    def __post_init__(self) -> None:
        if not isinstance(self.is_owner, bool):
            raise TypeError("is_owner must be a boolean")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence must be a float")
        conf = float(self.confidence)
        if not (0.0 <= conf <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", conf)

    def __repr__(self) -> str:
        return f"<OwnerVerificationResult is_owner={self.is_owner} confidence={self.confidence:.2f}>"


@dataclass(frozen=True, repr=False)
class EchoNoiseResult:
    """Echo and noise rejection decision."""

    is_echo: bool
    is_noise: bool
    confidence: float = 1.0
    reason: str = "ok"

    def __post_init__(self) -> None:
        if not isinstance(self.is_echo, bool):
            raise TypeError("is_echo must be a boolean")
        if not isinstance(self.is_noise, bool):
            raise TypeError("is_noise must be a boolean")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence must be a float")
        conf = float(self.confidence)
        if not (0.0 <= conf <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", conf)

    def __repr__(self) -> str:
        return f"<EchoNoiseResult is_echo={self.is_echo} is_noise={self.is_noise}>"


@dataclass(frozen=True, repr=False)
class SessionContext:
    """Correlation container carrying session IDs and cancellation generation."""

    session_id: str
    utterance_id: str
    response_id: str
    playback_id: str
    event_sequence: int
    cancellation_generation: int
    monotonic_ns: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", validate_session_id(self.session_id))
        object.__setattr__(self, "utterance_id", validate_utterance_id(self.utterance_id))
        object.__setattr__(self, "response_id", validate_response_id(self.response_id))
        object.__setattr__(self, "playback_id", validate_playback_id(self.playback_id))
        object.__setattr__(self, "event_sequence", validate_sequence(self.event_sequence))
        object.__setattr__(
            self, "cancellation_generation", validate_generation(self.cancellation_generation)
        )
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))

    def __repr__(self) -> str:
        return (
            f"<SessionContext seq={self.event_sequence} gen={self.cancellation_generation}>"
        )


@runtime_checkable
class FrameSourceProtocol(Protocol):
    async def get_frame(self) -> Optional[AudioFrame]: ...


@runtime_checkable
class VADSourceProtocol(Protocol):
    async def observe(self) -> Optional[tuple[bool, float, int]]: ...


@runtime_checkable
class TranscriberProtocol(Protocol):
    async def transcribe_partial(self, frame: AudioFrame) -> Optional[str]: ...
    async def transcribe_final(self, frames: list[AudioFrame]) -> Optional[str]: ...


@runtime_checkable
class OwnerVerifierProtocol(Protocol):
    def verify_owner(self, frames: list[AudioFrame]) -> OwnerVerificationResult: ...


@runtime_checkable
class EchoNoiseRejectorProtocol(Protocol):
    def evaluate_echo_noise(self, frames: list[AudioFrame]) -> EchoNoiseResult: ...


@runtime_checkable
class GenerationStreamProtocol(Protocol):
    def generate(self, prompt_text: str) -> AsyncIterator[str]: ...


@runtime_checkable
class TTSRendererProtocol(Protocol):
    async def render(self, text: str) -> bytes:
        """Render sentence text to audio bytes (maximum 10MB per rendered chunk)."""
        ...


@runtime_checkable
class PlaybackControllerProtocol(Protocol):
    async def play(self, audio_data: bytes, playback_id: str) -> bool:
        """Play audio bytes for playback_id.

        Contract: play() awaits audible chunk playback completion before returning True.
        """
        ...
    async def pause(self) -> None: ...
    async def stop(self) -> None: ...
    async def resume(self) -> None: ...


@runtime_checkable
class MonotonicClockProtocol(Protocol):
    def now_ns(self) -> int: ...


@runtime_checkable
class StateEventSinkProtocol(Protocol):
    async def emit_event(self, event: object) -> None: ...


@runtime_checkable
class TurnSinkProtocol(Protocol):
    async def on_turn(
        self, text: str, session_id: str, utterance_id: str, start_ns: int, end_ns: int
    ) -> None: ...


@runtime_checkable
class ResumePolicyProtocol(Protocol):
    def should_resume(self, previous_response_id: str, rejected_reason: str) -> bool: ...


__all__ = [
    "AudioFrame",
    "EchoNoiseRejectorProtocol",
    "EchoNoiseResult",
    "FrameSourceProtocol",
    "GenerationStreamProtocol",
    "MonotonicClockProtocol",
    "OwnerVerificationResult",
    "OwnerVerifierProtocol",
    "PlaybackControllerProtocol",
    "ResumePolicyProtocol",
    "SessionContext",
    "StateEventSinkProtocol",
    "TTSRendererProtocol",
    "TranscriberProtocol",
    "TurnSinkProtocol",
    "VADSourceProtocol",
    "validate_capture_duration_bound",
    "validate_capture_frame_bound",
    "validate_generation",
    "validate_monotonic_ns",
    "validate_playback_id",
    "validate_response_id",
    "validate_sequence",
    "validate_session_id",
    "validate_utterance_id",
]
