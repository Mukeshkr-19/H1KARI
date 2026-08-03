"""Event models for VoiceSessionCoordinator with privacy-preserving representations.

All events carry mandatory, validated correlation IDs and timestamps. Reprs omit raw text,
audio data, speaker IDs, and sensitive device metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from core.voice_session.contracts import (
    validate_generation,
    validate_monotonic_ns,
    validate_playback_id,
    validate_response_id,
    validate_sequence,
    validate_session_id,
    validate_utterance_id,
)


@dataclass(frozen=True, repr=False)
class VoiceSessionEvent:
    """Base session event carrying authoritative correlation context."""

    session_id: str
    utterance_id: str
    response_id: str
    playback_id: str
    event_sequence: int
    monotonic_ns: int
    cancellation_generation: int
    event_type: str = "base"

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", validate_session_id(self.session_id))
        object.__setattr__(self, "utterance_id", validate_utterance_id(self.utterance_id))
        object.__setattr__(self, "response_id", validate_response_id(self.response_id))
        object.__setattr__(self, "playback_id", validate_playback_id(self.playback_id))
        object.__setattr__(self, "event_sequence", validate_sequence(self.event_sequence))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        object.__setattr__(
            self, "cancellation_generation", validate_generation(self.cancellation_generation)
        )
        if not isinstance(self.event_type, str) or not self.event_type:
            raise ValueError("event_type must be a non-empty string")

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} type={self.event_type!r} "
            f"seq={self.event_sequence} gen={self.cancellation_generation}>"
        )


@dataclass(frozen=True, repr=False)
class StateChangeEvent(VoiceSessionEvent):
    """Authoritative state machine transition event."""

    old_state: str = "idle"
    new_state: str = "idle"
    reason: str = "ok"

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "event_type", "state_change")
        if not isinstance(self.old_state, str) or not self.old_state:
            raise ValueError("old_state must be a non-empty string")
        if not isinstance(self.new_state, str) or not self.new_state:
            raise ValueError("new_state must be a non-empty string")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")

    def __repr__(self) -> str:
        return (
            f"<StateChangeEvent {self.old_state}->{self.new_state} "
            f"seq={self.event_sequence} gen={self.cancellation_generation}>"
        )


@dataclass(frozen=True, repr=False)
class DegradedStateEvent(VoiceSessionEvent):
    """Event emitted when AEC proof is lost or pipeline downgrades to half-duplex."""

    reason: str = "aec_evidence_absent"
    is_full_duplex: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "event_type", "degraded_state")
        if not isinstance(self.is_full_duplex, bool):
            raise TypeError("is_full_duplex must be a boolean")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")

    def __repr__(self) -> str:
        return (
            f"<DegradedStateEvent full_duplex={self.is_full_duplex} "
            f"reason={self.reason!r} seq={self.event_sequence}>"
        )


@dataclass(frozen=True, repr=False)
class TranscriptEvent(VoiceSessionEvent):
    """Content-free transcript event indicating partial/final caption status."""

    is_final: bool = False
    text_length: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "event_type", "transcript")
        if not isinstance(self.is_final, bool):
            raise TypeError("is_final must be a boolean")
        if isinstance(self.text_length, bool) or not isinstance(self.text_length, int) or self.text_length < 0:
            raise ValueError("text_length must be a non-negative integer")

    def __repr__(self) -> str:
        return f"<TranscriptEvent final={self.is_final} len={self.text_length} seq={self.event_sequence}>"


@dataclass(frozen=True, repr=False)
class PlaybackEvent(VoiceSessionEvent):
    """Playback status or command event."""

    action: str = "play"
    position_ms: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "event_type", "playback")
        if not isinstance(self.action, str) or not self.action:
            raise ValueError("action must be a non-empty string")
        if isinstance(self.position_ms, bool) or not isinstance(self.position_ms, int) or self.position_ms < 0:
            raise ValueError("position_ms must be a non-negative integer")

    def __repr__(self) -> str:
        return f"<PlaybackEvent action={self.action!r} pos={self.position_ms}ms seq={self.event_sequence}>"


@dataclass(frozen=True, repr=False)
class BargeInEvent(VoiceSessionEvent):
    """Barge-in detection or action event."""

    action: str = "paused"
    reason: str = "probable_speech"

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "event_type", "barge_in")
        if not isinstance(self.action, str) or not self.action:
            raise ValueError("action must be a non-empty string")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")

    def __repr__(self) -> str:
        return f"<BargeInEvent action={self.action!r} reason={self.reason!r} seq={self.event_sequence}>"


__all__ = [
    "BargeInEvent",
    "DegradedStateEvent",
    "PlaybackEvent",
    "StateChangeEvent",
    "TranscriptEvent",
    "VoiceSessionEvent",
]
