"""Wake/sleep authority for streaming voice sessions.

Sleeping audio may only create a bounded wake candidate. Ordinary speech while
sleeping never reaches orchestration. Wake grants no tool or memory authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .contracts import StreamingDecision, StreamingReason, TurnState, WakeEvidence


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
    def __init__(self) -> None:
        self._state = TurnState.SLEEPING
        self._responses_suppressed = True
        self._wake_detector_available = True
        self._last_wake_id: Optional[str] = None
        self._candidate: Optional[WakeEvidence] = None

    @property
    def state(self) -> TurnState:
        return self._state

    def snapshot(self) -> WakeSleepSnapshot:
        return WakeSleepSnapshot(
            state=self._state,
            responses_suppressed=self._responses_suppressed,
            wake_detector_available=self._wake_detector_available,
            last_wake_id=self._last_wake_id,
        )

    def observe_sleeping_audio(self) -> StreamingDecision:
        """Ordinary speech while sleeping creates at most a wake candidate slot."""
        if self._state == TurnState.CLOSED:
            return StreamingDecision(False, StreamingReason.CLOSED, self._state.value)
        if self._state != TurnState.SLEEPING:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self._state.value)
        self._state = TurnState.WAKE_CANDIDATE
        # Still suppressed — candidate only, no orchestration
        self._responses_suppressed = True
        return StreamingDecision(True, StreamingReason.OK, self._state.value)

    def attempt_orchestration_while_sleeping(self) -> StreamingDecision:
        """Fail-closed: sleeping ordinary speech cannot reach orchestration."""
        if self._state in (TurnState.SLEEPING, TurnState.WAKE_CANDIDATE) or self._responses_suppressed:
            return StreamingDecision(False, StreamingReason.SLEEPING_SUPPRESSED, self._state.value)
        return StreamingDecision(True, StreamingReason.OK, self._state.value)

    def submit_wake_evidence(self, evidence: WakeEvidence) -> StreamingDecision:
        if not isinstance(evidence, WakeEvidence):
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self._state.value)
        if self._state not in (TurnState.SLEEPING, TurnState.WAKE_CANDIDATE):
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self._state.value)
        self._candidate = evidence
        if not evidence.wake_verified:
            self._state = TurnState.SLEEPING
            self._responses_suppressed = True
            return StreamingDecision(False, StreamingReason.WAKE_REQUIRED, self._state.value)
        if not evidence.speaker_verified:
            self._state = TurnState.SLEEPING
            self._responses_suppressed = True
            return StreamingDecision(False, StreamingReason.SPEAKER_DENIED, self._state.value)
        self._last_wake_id = evidence.wake_id
        self._state = TurnState.LISTENING
        self._responses_suppressed = False
        self._wake_detector_available = True
        return StreamingDecision(True, StreamingReason.OK, self._state.value)

    def goodbye(self) -> StreamingDecision:
        """Return conversation to SLEEPING; wake detector remains available."""
        if self._state == TurnState.CLOSED:
            return StreamingDecision(False, StreamingReason.CLOSED, self._state.value)
        self._state = TurnState.SLEEPING
        self._responses_suppressed = True
        self._wake_detector_available = True
        self._candidate = None
        return StreamingDecision(True, StreamingReason.OK, self._state.value)

    def allow_response(self) -> StreamingDecision:
        if self._responses_suppressed or self._state in (
            TurnState.SLEEPING,
            TurnState.WAKE_CANDIDATE,
            TurnState.CLOSED,
        ):
            return StreamingDecision(False, StreamingReason.SLEEPING_SUPPRESSED, self._state.value)
        return StreamingDecision(True, StreamingReason.OK, self._state.value)

    def bind_listening_state(self, state: TurnState) -> None:
        """Allow turn machine to mirror listening-family states after valid wake."""
        if self._responses_suppressed:
            return
        if state in (
            TurnState.LISTENING,
            TurnState.USER_SPEAKING,
            TurnState.ASSISTANT_THINKING,
            TurnState.ASSISTANT_SPEAKING,
            TurnState.INTERRUPTED,
            TurnState.DRAINING,
        ):
            self._state = state


__all__ = ["WakeSleepAuthority", "WakeSleepSnapshot"]
