"""Interruption / barge-in correlation with fail-closed rejection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Set

from .contracts import (
    InterruptionEvent,
    SpeakerCategory,
    StreamingDecision,
    StreamingReason,
    TurnState,
)


@dataclass(frozen=True, repr=False)
class BargeInResult:
    accepted: bool
    reason: StreamingReason
    cancel_utterance_id: Optional[str]
    drain: bool

    def __repr__(self) -> str:
        return f"BargeInResult(accepted={self.accepted}, reason={self.reason.value!r})"


class BargeInController:
    """Correlate barge-in to the active assistant utterance only."""

    def __init__(self, *, max_seen: int = 256) -> None:
        if isinstance(max_seen, bool) or not isinstance(max_seen, int) or max_seen < 1:
            raise ValueError("invalid_max_seen")
        self._max_seen = max_seen
        self._seen: Set[str] = set()
        self._active_utterance: Optional[str] = None
        self._draining = False

    def set_active_utterance(self, utterance_id: Optional[str]) -> None:
        from .contracts import validate_id

        if utterance_id is None:
            self._active_utterance = None
            self._draining = False
            return
        self._active_utterance = validate_id(utterance_id, "utterance_id")
        self._draining = False

    @property
    def active_utterance_id(self) -> Optional[str]:
        return self._active_utterance

    @property
    def is_draining(self) -> bool:
        return self._draining

    def handle(
        self,
        event: InterruptionEvent,
        *,
        turn_state: TurnState,
    ) -> BargeInResult:
        if not isinstance(event, InterruptionEvent):
            return BargeInResult(False, StreamingReason.INVALID_INPUT, None, False)
        if event.interruption_id in self._seen:
            return BargeInResult(False, StreamingReason.DUPLICATE, None, False)
        if len(self._seen) >= self._max_seen:
            self._seen.clear()
        self._seen.add(event.interruption_id)

        if event.is_noise or event.speaker == SpeakerCategory.NOISE:
            return BargeInResult(False, StreamingReason.NOISE_REJECTED, None, False)

        if turn_state not in (TurnState.ASSISTANT_SPEAKING, TurnState.ASSISTANT_THINKING):
            return BargeInResult(False, StreamingReason.STALE_INTERRUPTION, None, False)

        if self._active_utterance is None:
            return BargeInResult(False, StreamingReason.STALE_INTERRUPTION, None, False)

        if event.assistant_utterance_id != self._active_utterance:
            return BargeInResult(False, StreamingReason.CORRELATION_MISMATCH, None, False)

        # Cancellation affects only the active utterance; enter bounded drain
        cancelled = self._active_utterance
        self._draining = True
        return BargeInResult(True, StreamingReason.OK, cancelled, True)

    def complete_drain(self) -> StreamingDecision:
        if not self._draining:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT)
        self._draining = False
        self._active_utterance = None
        return StreamingDecision(True, StreamingReason.OK, TurnState.INTERRUPTED.value)


__all__ = ["BargeInController", "BargeInResult"]
