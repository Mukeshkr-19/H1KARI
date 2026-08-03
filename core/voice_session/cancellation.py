"""Cancellation tracking and epoch generation management.

Supports idempotent cancellation, separate cancellation requests and physical stop
acknowledgments, and stale work invalidation via generation epochs.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Optional

from core.voice_session.contracts import (
    validate_generation,
    validate_monotonic_ns,
    validate_playback_id,
    validate_response_id,
    validate_session_id,
    validate_utterance_id,
)


@dataclass(frozen=True, repr=False)
class InterruptionRequest:
    """Interruption or barge-in cancellation request."""

    request_id: str
    session_id: str
    utterance_id: str
    response_id: str
    playback_id: str
    cancellation_generation: int
    monotonic_ns: int
    reason: str = "barge_in"

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", validate_session_id(self.request_id))
        object.__setattr__(self, "session_id", validate_session_id(self.session_id))
        object.__setattr__(self, "utterance_id", validate_utterance_id(self.utterance_id))
        object.__setattr__(self, "response_id", validate_response_id(self.response_id))
        object.__setattr__(self, "playback_id", validate_playback_id(self.playback_id))
        object.__setattr__(
            self, "cancellation_generation", validate_generation(self.cancellation_generation)
        )
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))

    def __repr__(self) -> str:
        return f"<InterruptionRequest gen={self.cancellation_generation} reason={self.reason!r}>"


@dataclass(frozen=True, repr=False)
class InterruptionConfirmation:
    """Physical playback stop acknowledgment."""

    request_id: str
    session_id: str
    playback_id: str
    confirmed: bool
    monotonic_ns: int
    bytes_played: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", validate_session_id(self.request_id))
        object.__setattr__(self, "session_id", validate_session_id(self.session_id))
        object.__setattr__(self, "playback_id", validate_playback_id(self.playback_id))
        if not isinstance(self.confirmed, bool):
            raise TypeError("confirmed must be a boolean")
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        if isinstance(self.bytes_played, bool) or not isinstance(self.bytes_played, int) or self.bytes_played < 0:
            raise ValueError("bytes_played must be a non-negative integer")

    def __repr__(self) -> str:
        return f"<InterruptionConfirmation confirmed={self.confirmed}>"


class CancellationTracker:
    """Thread-safe cancellation generation tracker."""

    def __init__(self, initial_generation: int = 0) -> None:
        self._lock = threading.Lock()
        self._generation = validate_generation(initial_generation)
        self._last_request: Optional[InterruptionRequest] = None
        self._last_confirmation: Optional[InterruptionConfirmation] = None

    @property
    def current_generation(self) -> int:
        with self._lock:
            return self._generation

    def cancel(self, reason: str = "user_cancel") -> int:
        """Idempotently bump cancellation generation epoch."""
        with self._lock:
            self._generation += 1
            return self._generation

    def is_stale(self, generation: int) -> bool:
        """Return True if specified generation is older than current generation."""
        val = validate_generation(generation)
        with self._lock:
            return val < self._generation

    def record_request(self, request: InterruptionRequest) -> None:
        if not isinstance(request, InterruptionRequest):
            raise TypeError("request must be an InterruptionRequest")
        with self._lock:
            self._last_request = request

    def record_confirmation(self, confirmation: InterruptionConfirmation) -> None:
        if not isinstance(confirmation, InterruptionConfirmation):
            raise TypeError("confirmation must be an InterruptionConfirmation")
        with self._lock:
            self._last_confirmation = confirmation

    @property
    def last_request(self) -> Optional[InterruptionRequest]:
        with self._lock:
            return self._last_request

    @property
    def last_confirmation(self) -> Optional[InterruptionConfirmation]:
        with self._lock:
            return self._last_confirmation

    def __repr__(self) -> str:
        with self._lock:
            gen = self._generation
        return f"<CancellationTracker generation={gen}>"


__all__ = [
    "CancellationTracker",
    "InterruptionConfirmation",
    "InterruptionRequest",
]
