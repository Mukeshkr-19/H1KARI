"""Streaming voice pipeline contracts and data types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Literal, Optional, Tuple


class VoiceStreamState(str, Enum):
    IDLE = "idle"
    WAKE_LISTENING = "wake_listening"
    ACTIVE_LISTENING = "active_listening"
    USER_SPEAKING = "user_speaking"
    FINALIZING_USER_TURN = "finalizing_user_turn"
    THINKING = "thinking"
    ASSISTANT_SPEAKING = "assistant_speaking"
    INTERRUPTING = "interrupting"
    INTERRUPTED = "interrupted"
    STOPPING = "stopping"
    ERROR = "error"


CaptionRole = Literal["user", "assistant", "system"]
ALLOWED_ROLES: Tuple[CaptionRole, ...] = ("user", "assistant", "system")


def sanitize_text(text: str, *, allow_empty: bool = False) -> str:
    """Sanitize transcript text, stripping controls except newlines/tabs."""
    if not isinstance(text, str):
        raise TypeError("Text must be a string")
    cleaned = "".join(
        ch for ch in text if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    ).strip()
    if not cleaned and not allow_empty:
        raise ValueError("Text cannot be empty or whitespace-only")
    return cleaned


def validate_confidence(confidence: float) -> float:
    """Ensure confidence is bounded between 0.0 and 1.0."""
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise TypeError("Confidence must be a float")
    val = float(confidence)
    if not (0.0 <= val <= 1.0):
        raise ValueError(f"Confidence {val} out of bounds [0.0, 1.0]")
    return val


def validate_monotonic_ns(ts_ns: int) -> int:
    """Ensure monotonic timestamp is a non-negative integer."""
    if isinstance(ts_ns, bool) or not isinstance(ts_ns, int):
        raise TypeError("Monotonic timestamp must be an integer (nanoseconds)")
    if ts_ns < 0:
        raise ValueError("Monotonic timestamp cannot be negative")
    return ts_ns


def validate_stream_id(stream_id: str) -> str:
    """Ensure stream identifier is valid and non-empty."""
    if not isinstance(stream_id, str) or not stream_id.strip():
        raise ValueError("Stream identifier must be a non-empty string")
    return stream_id.strip()


@dataclass(frozen=True)
class VADEvent:
    """Voice Activity Detection event."""

    stream_id: str
    is_speech: bool
    confidence: float
    monotonic_ns: int
    speech_duration_ms: float = 0.0
    source_id: str = "default_mic"

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        if not isinstance(self.is_speech, bool):
            raise TypeError("is_speech must be a boolean")


@dataclass(frozen=True)
class VerifiedWakeEvent:
    """Caller-supplied verified wake word activation event."""

    stream_id: str
    wake_word: str
    confidence: float
    monotonic_ns: int
    speaker_id: Optional[str] = None
    is_verified: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        object.__setattr__(self, "wake_word", sanitize_text(self.wake_word))
        if not isinstance(self.is_verified, bool):
            raise TypeError("is_verified must be a boolean")


@dataclass(frozen=True)
class InterimTranscript:
    """Ordered interim transcript revision."""

    stream_id: str
    text: str
    monotonic_ns: int
    role: CaptionRole = "user"
    confidence: float = 1.0
    sequence_number: int = 0
    source_id: str = "mic"

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        object.__setattr__(self, "text", sanitize_text(self.text, allow_empty=True))
        if self.role not in ALLOWED_ROLES:
            raise ValueError(f"Invalid role {self.role!r}")
        if isinstance(self.sequence_number, bool) or not isinstance(
            self.sequence_number, int
        ) or self.sequence_number < 0:
            raise ValueError("sequence_number must be a non-negative integer")


@dataclass(frozen=True)
class FinalTranscript:
    """Immutable final transcript segment."""

    stream_id: str
    text: str
    start_monotonic_ns: int
    end_monotonic_ns: int
    role: CaptionRole = "user"
    confidence: float = 1.0
    sequence_number: int = 0
    source_id: str = "mic"

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        object.__setattr__(
            self, "start_monotonic_ns", validate_monotonic_ns(self.start_monotonic_ns)
        )
        object.__setattr__(
            self, "end_monotonic_ns", validate_monotonic_ns(self.end_monotonic_ns)
        )
        if self.end_monotonic_ns < self.start_monotonic_ns:
            raise ValueError("End timestamp cannot be earlier than start timestamp")
        object.__setattr__(self, "text", sanitize_text(self.text, allow_empty=False))
        if self.role not in ALLOWED_ROLES:
            raise ValueError(f"Invalid role {self.role!r}")
        if isinstance(self.sequence_number, bool) or not isinstance(
            self.sequence_number, int
        ) or self.sequence_number < 0:
            raise ValueError("sequence_number must be a non-negative integer")


@dataclass(frozen=True)
class InterruptionRequest:
    """User barge-in or interruption request."""

    stream_id: str
    request_id: str
    monotonic_ns: int
    is_authenticated: bool
    speaker_id: Optional[str] = None
    reason: str = "barge_in"

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        object.__setattr__(self, "request_id", validate_stream_id(self.request_id))
        if not isinstance(self.is_authenticated, bool):
            raise TypeError("is_authenticated must be a boolean")


@dataclass(frozen=True)
class InterruptionConfirmation:
    """Physical playback stop confirmation."""

    stream_id: str
    request_id: str
    monotonic_ns: int
    is_confirmed: bool = True
    bytes_played_before_stop: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        object.__setattr__(self, "request_id", validate_stream_id(self.request_id))
        if not isinstance(self.is_confirmed, bool):
            raise TypeError("is_confirmed must be a boolean")


@dataclass(frozen=True)
class PlaybackState:
    """Assistant playback status."""

    stream_id: str
    is_playing: bool
    current_position_ms: int = 0
    monotonic_ns: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))


@dataclass(frozen=True)
class CaptureState:
    """Microphone audio capture status."""

    stream_id: str
    is_capturing: bool
    sample_rate: int = 16000
    channels: int = 1
    source_id: str = "mic"

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))


@dataclass(frozen=True)
class AuthDecision:
    """Speaker verification decision supplied by external authentication subsystem."""

    speaker_id: Optional[str]
    is_authenticated: bool
    confidence: float
    reason: str
    monotonic_ns: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))


@dataclass(frozen=True)
class AECCapability:
    """Acoustic Echo Cancellation capability and status."""

    enabled: bool
    available: bool
    is_hardware: bool = False
    status_reason: str = "ok"


@dataclass(frozen=True)
class VADCapability:
    """Voice Activity Detection capability and status."""

    enabled: bool
    available: bool
    algorithm: str = "energy_spectral"
    status_reason: str = "ok"


@dataclass(frozen=True)
class StreamingVoiceFailure:
    """Voice streaming error or failure event."""

    stream_id: str
    error_code: str
    message: str
    monotonic_ns: int
    recoverable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))


@dataclass(frozen=True)
class StateTransitionRecord:
    """Immutable transition audit record."""

    old_state: VoiceStreamState
    new_state: VoiceStreamState
    event_type: str
    monotonic_ns: int
    reason: str
    details: Dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))


@dataclass(frozen=True)
class AccessibilityState:
    """Accessible UI state view."""

    indicator: str
    caption_text: str
    announcement: str
    non_audio_fallback: bool = True
    manual_stop_available: bool = True
    reduced_motion: bool = False
    error_message: Optional[str] = None
