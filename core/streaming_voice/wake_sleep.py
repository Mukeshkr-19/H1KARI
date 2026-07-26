"""Wake/sleep projection over the canonical VoiceStreamingRuntime.

This module does not own independent wake/sleep authority. All orchestration
gates are answered from the bound VoiceStreamingRuntime. Projection flags such
as WAKE_CANDIDATE are display/API compatibility only and never grant tools,
memory, or action authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from .contracts import StreamingDecision, StreamingReason, TurnState, WakeEvidence

if TYPE_CHECKING:
    from .turn import TurnStateMachine


@dataclass(frozen=True, repr=False)
class WakeSleepSnapshot:
    state: TurnState
    responses_suppressed: bool
    wake_detector_available: bool
    last_wake_id: Optional[str]

    def __repr__(self) -> str:
        return (
            f"WakeSleepSnapshot(state={self.state.value!r}, "
            f"suppressed={self.responses_suppressed})"
        )


class WakeSleepAuthority:
    """Compatibility facade; mutable authority lives on VoiceStreamingRuntime."""

    def __init__(self, owner: "TurnStateMachine") -> None:
        self._owner = owner

    @property
    def state(self) -> TurnState:
        return self._owner.state

    def snapshot(self) -> WakeSleepSnapshot:
        return WakeSleepSnapshot(
            state=self._owner.state,
            responses_suppressed=self._owner.responses_suppressed,
            wake_detector_available=not self._owner.is_closed,
            last_wake_id=self._owner.last_wake_id,
        )

    def observe_sleeping_audio(self) -> StreamingDecision:
        return self._owner.observe_sleeping_audio()

    def attempt_orchestration_while_sleeping(self) -> StreamingDecision:
        if self._owner.responses_suppressed or self._owner.is_closed:
            return StreamingDecision(
                False, StreamingReason.SLEEPING_SUPPRESSED, self._owner.state.value
            )
        return StreamingDecision(True, StreamingReason.OK, self._owner.state.value)

    def submit_wake_evidence(self, evidence: WakeEvidence) -> StreamingDecision:
        return self._owner.submit_wake(evidence)

    def goodbye(self) -> StreamingDecision:
        return self._owner.goodbye()

    def allow_response(self) -> StreamingDecision:
        if self._owner.responses_suppressed or self._owner.is_closed:
            return StreamingDecision(
                False, StreamingReason.SLEEPING_SUPPRESSED, self._owner.state.value
            )
        return StreamingDecision(True, StreamingReason.OK, self._owner.state.value)

    def bind_listening_state(self, state: TurnState) -> None:
        # No-op: canonical runtime owns listening-family transitions.
        return None


__all__ = ["WakeSleepAuthority", "WakeSleepSnapshot"]
