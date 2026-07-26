"""Immutable streaming-voice contracts.

Pure value objects only. No microphone, filesystem, network, model, process,
or database side effects. Injected clocks and backends remain outside this
module.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_MAX_TEXT = 2048
_UNICODE_FORMAT_RE = re.compile(r"[\u200e\u200f\u202a-\u202e\u2060-\u2069\ufeff]")


class SpeakerCategory(StrEnum):
    OWNER = "owner"
    GUEST = "guest"
    ASSISTANT = "assistant"
    UNKNOWN = "unknown"
    NOISE = "noise"


class SegmentStatus(StrEnum):
    PARTIAL = "partial"
    FINAL = "final"


class ConfidenceCategory(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


class VadState(StrEnum):
    IDLE = "idle"
    POSSIBLE_SPEECH = "possible_speech"
    SPEAKING = "speaking"
    ENDING = "ending"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class TurnState(StrEnum):
    SLEEPING = "sleeping"
    WAKE_CANDIDATE = "wake_candidate"
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    ASSISTANT_THINKING = "assistant_thinking"
    ASSISTANT_SPEAKING = "assistant_speaking"
    INTERRUPTED = "interrupted"
    DRAINING = "draining"
    CLOSED = "closed"


class AecStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class DuplexMode(StrEnum):
    FULL_DUPLEX = "full_duplex"
    HALF_DUPLEX = "half_duplex"


class StreamingReason(StrEnum):
    OK = "ok"
    INVALID_INPUT = "invalid_input"
    STALE_FRAME = "stale_frame"
    OUT_OF_ORDER = "out_of_order"
    DUPLICATE = "duplicate"
    REPLAYED = "replayed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    MAX_DURATION = "max_duration"
    BUFFER_EXHAUSTED = "buffer_exhausted"
    DROPPED = "dropped"
    WAKE_REQUIRED = "wake_required"
    SPEAKER_DENIED = "speaker_denied"
    SLEEPING_SUPPRESSED = "sleeping_suppressed"
    STALE_INTERRUPTION = "stale_interruption"
    NOISE_REJECTED = "noise_rejected"
    AEC_UNAVAILABLE = "aec_unavailable"
    CORRELATION_MISMATCH = "correlation_mismatch"
    CLOSED = "closed"


def validate_id(value: object, field: str = "id") -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid_{field}")
    return value


def validate_mono(value: object, field: str = "timestamp") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_{field}")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"invalid_{field}")
    return number


def validate_text(value: object, *, field: str = "text", max_length: int = _MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid_{field}")
    if any(ord(ch) < 32 and ch not in "\t\n" or ord(ch) == 127 for ch in value):
        raise ValueError(f"invalid_{field}")
    if _UNICODE_FORMAT_RE.search(value):
        raise ValueError(f"invalid_{field}")
    if len(value) > max_length:
        raise ValueError(f"invalid_{field}")
    return value


@dataclass(frozen=True, repr=False)
class TranscriptSegment:
    """Timestamped transcript segment. Never stores raw audio."""

    segment_id: str
    utterance_id: str
    session_id: str
    speaker: SpeakerCategory
    start_mono: float
    end_mono: float
    status: SegmentStatus
    confidence: ConfidenceCategory
    text: str
    sequence: int

    def __post_init__(self) -> None:
        validate_id(self.segment_id, "segment_id")
        validate_id(self.utterance_id, "utterance_id")
        validate_id(self.session_id, "session_id")
        if not isinstance(self.speaker, SpeakerCategory):
            raise ValueError("invalid_speaker")
        start = validate_mono(self.start_mono, "start_mono")
        end = validate_mono(self.end_mono, "end_mono")
        if end < start:
            raise ValueError("invalid_timestamp_order")
        if not isinstance(self.status, SegmentStatus):
            raise ValueError("invalid_status")
        if not isinstance(self.confidence, ConfidenceCategory):
            raise ValueError("invalid_confidence")
        validate_text(self.text, field="text", max_length=_MAX_TEXT)
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("invalid_sequence")

    def __repr__(self) -> str:
        return f"TranscriptSegment(status={self.status.value!r}, speaker={self.speaker.value!r})"


@dataclass(frozen=True, repr=False)
class AudioFrameMeta:
    """Metadata about one audio frame. No PCM/samples stored."""

    frame_id: str
    session_id: str
    captured_at_mono: float
    sequence: int
    duration_ms: int
    energy_bucket: int  # bounded 0..10 discrete energy, not raw samples

    def __post_init__(self) -> None:
        validate_id(self.frame_id, "frame_id")
        validate_id(self.session_id, "session_id")
        validate_mono(self.captured_at_mono, "captured_at_mono")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("invalid_sequence")
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, int):
            raise ValueError("invalid_duration_ms")
        if self.duration_ms < 1 or self.duration_ms > 1000:
            raise ValueError("invalid_duration_ms")
        if isinstance(self.energy_bucket, bool) or not isinstance(self.energy_bucket, int):
            raise ValueError("invalid_energy_bucket")
        if self.energy_bucket < 0 or self.energy_bucket > 10:
            raise ValueError("invalid_energy_bucket")

    def __repr__(self) -> str:
        return f"AudioFrameMeta(seq={self.sequence})"


@dataclass(frozen=True, repr=False)
class WakeEvidence:
    """Caller-supplied wake + speaker-policy evidence. Grants no tool authority."""

    wake_id: str
    session_id: str
    wake_verified: bool
    speaker_verified: bool
    observed_at_mono: float
    phrase_category: str = "wake"

    def __post_init__(self) -> None:
        validate_id(self.wake_id, "wake_id")
        validate_id(self.session_id, "session_id")
        if not isinstance(self.wake_verified, bool) or not isinstance(self.speaker_verified, bool):
            raise ValueError("invalid_wake_flags")
        validate_mono(self.observed_at_mono, "observed_at_mono")
        validate_id(self.phrase_category, "phrase_category")

    @property
    def is_valid_wake(self) -> bool:
        return self.wake_verified and self.speaker_verified

    def __repr__(self) -> str:
        return f"WakeEvidence(valid={self.is_valid_wake})"


@dataclass(frozen=True, repr=False)
class InterruptionEvent:
    interruption_id: str
    session_id: str
    assistant_utterance_id: str
    observed_at_mono: float
    speaker: SpeakerCategory
    is_noise: bool = False

    def __post_init__(self) -> None:
        validate_id(self.interruption_id, "interruption_id")
        validate_id(self.session_id, "session_id")
        validate_id(self.assistant_utterance_id, "assistant_utterance_id")
        validate_mono(self.observed_at_mono, "observed_at_mono")
        if not isinstance(self.speaker, SpeakerCategory):
            raise ValueError("invalid_speaker")
        if not isinstance(self.is_noise, bool):
            raise ValueError("invalid_is_noise")

    def __repr__(self) -> str:
        return f"InterruptionEvent(noise={self.is_noise})"


@dataclass(frozen=True, repr=False)
class StreamingDecision:
    accepted: bool
    reason: StreamingReason
    state: Optional[str] = None

    def __repr__(self) -> str:
        return f"StreamingDecision(accepted={self.accepted}, reason={self.reason.value!r})"


__all__ = [
    "AecStatus",
    "AudioFrameMeta",
    "ConfidenceCategory",
    "DuplexMode",
    "InterruptionEvent",
    "SegmentStatus",
    "SpeakerCategory",
    "StreamingDecision",
    "StreamingReason",
    "TranscriptSegment",
    "TurnState",
    "VadState",
    "WakeEvidence",
    "validate_id",
    "validate_mono",
    "validate_text",
]
