"""Full-duplex turn state machine with exact transition graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Optional, Set, Tuple

from .barge_in import BargeInController, BargeInResult
from .contracts import (
    InterruptionEvent,
    StreamingDecision,
    StreamingReason,
    TurnState,
    WakeEvidence,
    validate_id,
)
from .wake_sleep import WakeSleepAuthority

MonoClock = Callable[[], float]

# Exact allowed transitions
_TRANSITIONS: Dict[TurnState, FrozenSet[TurnState]] = {
    TurnState.SLEEPING: frozenset({TurnState.WAKE_CANDIDATE, TurnState.CLOSED}),
    TurnState.WAKE_CANDIDATE: frozenset(
        {TurnState.LISTENING, TurnState.SLEEPING, TurnState.CLOSED}
    ),
    TurnState.LISTENING: frozenset(
        {
            TurnState.USER_SPEAKING,
            TurnState.ASSISTANT_THINKING,
            TurnState.SLEEPING,
            TurnState.CLOSED,
        }
    ),
    TurnState.USER_SPEAKING: frozenset(
        {
            TurnState.LISTENING,
            TurnState.ASSISTANT_THINKING,
            TurnState.SLEEPING,
            TurnState.CLOSED,
        }
    ),
    TurnState.ASSISTANT_THINKING: frozenset(
        {
            TurnState.ASSISTANT_SPEAKING,
            TurnState.INTERRUPTED,
            TurnState.LISTENING,
            TurnState.SLEEPING,
            TurnState.CLOSED,
        }
    ),
    TurnState.ASSISTANT_SPEAKING: frozenset(
        {
            TurnState.LISTENING,
            TurnState.INTERRUPTED,
            TurnState.DRAINING,
            TurnState.SLEEPING,
            TurnState.CLOSED,
        }
    ),
    TurnState.INTERRUPTED: frozenset(
        {TurnState.DRAINING, TurnState.LISTENING, TurnState.SLEEPING, TurnState.CLOSED}
    ),
    TurnState.DRAINING: frozenset(
        {TurnState.LISTENING, TurnState.SLEEPING, TurnState.CLOSED, TurnState.INTERRUPTED}
    ),
    TurnState.CLOSED: frozenset(),
}


@dataclass(frozen=True, repr=False)
class TurnSnapshot:
    state: TurnState
    session_id: str
    active_user_utterance_id: Optional[str]
    active_assistant_utterance_id: Optional[str]
    active_response_id: Optional[str]

    def __repr__(self) -> str:
        return f"TurnSnapshot(state={self.state.value!r})"


class TurnStateMachine:
    """Full-duplex turn control. Injected clock; no I/O."""

    def __init__(self, session_id: str, clock: MonoClock) -> None:
        self._session_id = validate_id(session_id, "session_id")
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        self._clock = clock
        self._state = TurnState.SLEEPING
        self._wake = WakeSleepAuthority()
        self._barge = BargeInController()
        self._user_utt: Optional[str] = None
        self._assistant_utt: Optional[str] = None
        self._response_id: Optional[str] = None
        self._seen_utt: Set[str] = set()

    @property
    def state(self) -> TurnState:
        return self._state

    @property
    def wake(self) -> WakeSleepAuthority:
        return self._wake

    @property
    def barge(self) -> BargeInController:
        return self._barge

    def snapshot(self) -> TurnSnapshot:
        return TurnSnapshot(
            state=self._state,
            session_id=self._session_id,
            active_user_utterance_id=self._user_utt,
            active_assistant_utterance_id=self._assistant_utt,
            active_response_id=self._response_id,
        )

    def allowed_targets(self) -> FrozenSet[TurnState]:
        return _TRANSITIONS[self._state]

    def _transition(self, target: TurnState) -> StreamingDecision:
        if target not in _TRANSITIONS[self._state]:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self._state.value)
        self._state = target
        self._wake.bind_listening_state(target)
        return StreamingDecision(True, StreamingReason.OK, self._state.value)

    def cancel(self) -> StreamingDecision:
        """Cancel active turn work; reachable from every non-closed state via CLOSE path."""
        if self._state == TurnState.CLOSED:
            return StreamingDecision(False, StreamingReason.CLOSED, self._state.value)
        # Force close (allowed from every state)
        if TurnState.CLOSED in _TRANSITIONS[self._state] or self._state != TurnState.CLOSED:
            # CLOSED is in all non-closed transition sets
            self._state = TurnState.CLOSED
            self._user_utt = None
            self._assistant_utt = None
            self._response_id = None
            self._barge.set_active_utterance(None)
            return StreamingDecision(True, StreamingReason.CANCELLED, self._state.value)
        return StreamingDecision(False, StreamingReason.CLOSED, self._state.value)

    def observe_sleeping_audio(self) -> StreamingDecision:
        decision = self._wake.observe_sleeping_audio()
        if decision.accepted and self._state == TurnState.SLEEPING:
            return self._transition(TurnState.WAKE_CANDIDATE)
        return decision

    def submit_wake(self, evidence: WakeEvidence) -> StreamingDecision:
        if evidence.session_id != self._session_id:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self._state.value)
        decision = self._wake.submit_wake_evidence(evidence)
        if not decision.accepted:
            if self._state == TurnState.WAKE_CANDIDATE:
                self._transition(TurnState.SLEEPING)
            return decision
        if self._state == TurnState.SLEEPING:
            candidate = self._transition(TurnState.WAKE_CANDIDATE)
            if not candidate.accepted:
                return candidate
        if self._state == TurnState.WAKE_CANDIDATE:
            return self._transition(TurnState.LISTENING)
        return decision

    def goodbye(self) -> StreamingDecision:
        decision = self._wake.goodbye()
        if not decision.accepted:
            return decision
        if TurnState.SLEEPING in _TRANSITIONS[self._state] or self._state != TurnState.CLOSED:
            self._state = TurnState.SLEEPING
            self._user_utt = None
            self._assistant_utt = None
            self._response_id = None
            self._barge.set_active_utterance(None)
            return StreamingDecision(True, StreamingReason.OK, self._state.value)
        return StreamingDecision(False, StreamingReason.CLOSED, self._state.value)

    def begin_user_speech(self, utterance_id: str) -> StreamingDecision:
        uid = validate_id(utterance_id, "utterance_id")
        if self._wake.snapshot().responses_suppressed:
            return StreamingDecision(False, StreamingReason.SLEEPING_SUPPRESSED, self._state.value)
        if uid in self._seen_utt:
            return StreamingDecision(False, StreamingReason.DUPLICATE, self._state.value)
        if len(self._seen_utt) >= 4096:
            return StreamingDecision(False, StreamingReason.BUFFER_EXHAUSTED, self._state.value)
        decision = self._transition(TurnState.USER_SPEAKING)
        if decision.accepted:
            self._seen_utt.add(uid)
            self._user_utt = uid
        return decision

    def end_user_speech(self) -> StreamingDecision:
        if self._state != TurnState.USER_SPEAKING:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self._state.value)
        return self._transition(TurnState.ASSISTANT_THINKING)

    def begin_assistant_response(self, *, utterance_id: str, response_id: str) -> StreamingDecision:
        uid = validate_id(utterance_id, "utterance_id")
        rid = validate_id(response_id, "response_id")
        allow = self._wake.allow_response()
        if not allow.accepted:
            return allow
        if self._state != TurnState.ASSISTANT_THINKING:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self._state.value)
        if uid in self._seen_utt:
            return StreamingDecision(False, StreamingReason.DUPLICATE, self._state.value)
        if len(self._seen_utt) >= 4096:
            return StreamingDecision(False, StreamingReason.BUFFER_EXHAUSTED, self._state.value)
        decision = self._transition(TurnState.ASSISTANT_SPEAKING)
        if decision.accepted:
            self._seen_utt.add(uid)
            self._assistant_utt = uid
            self._response_id = rid
            self._barge.set_active_utterance(uid)
        return decision

    def complete_assistant_response(self, *, response_id: str) -> StreamingDecision:
        rid = validate_id(response_id, "response_id")
        if self._response_id is not None and rid != self._response_id:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self._state.value)
        if self._state not in (TurnState.ASSISTANT_SPEAKING, TurnState.DRAINING):
            return StreamingDecision(False, StreamingReason.STALE_INTERRUPTION, self._state.value)
        decision = self._transition(TurnState.LISTENING)
        if decision.accepted:
            self._assistant_utt = None
            self._response_id = None
            self._barge.set_active_utterance(None)
        return decision

    def interrupt(self, event: InterruptionEvent) -> BargeInResult:
        if event.session_id != self._session_id:
            return BargeInResult(False, StreamingReason.CORRELATION_MISMATCH, None, False)
        try:
            now = float(self._clock())
        except Exception:
            return BargeInResult(False, StreamingReason.INVALID_INPUT, None, False)
        if event.observed_at_mono > now + 1.0 or now - event.observed_at_mono > 30.0:
            return BargeInResult(False, StreamingReason.STALE_INTERRUPTION, None, False)
        result = self._barge.handle(event, turn_state=self._state)
        if not result.accepted:
            return result
        # Move to INTERRUPTED then DRAINING
        if TurnState.INTERRUPTED in _TRANSITIONS[self._state]:
            self._state = TurnState.INTERRUPTED
            self._wake.bind_listening_state(TurnState.INTERRUPTED)
        if TurnState.DRAINING in _TRANSITIONS[self._state]:
            self._state = TurnState.DRAINING
            self._wake.bind_listening_state(TurnState.DRAINING)
        return result

    def finish_drain(self) -> StreamingDecision:
        drain = self._barge.complete_drain()
        if not drain.accepted:
            return drain
        self._assistant_utt = None
        self._response_id = None
        if self._state == TurnState.DRAINING:
            return self._transition(TurnState.LISTENING)
        return StreamingDecision(True, StreamingReason.OK, self._state.value)

    def reject_stale_utterance(self, utterance_id: str) -> StreamingDecision:
        uid = validate_id(utterance_id, "utterance_id")
        if self._user_utt is None and self._assistant_utt is None:
            return StreamingDecision(False, StreamingReason.STALE_INTERRUPTION, self._state.value)
        if self._user_utt is not None and uid != self._user_utt and self._assistant_utt != uid:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self._state.value)
        if self._assistant_utt is not None and uid != self._assistant_utt and uid != self._user_utt:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self._state.value)
        return StreamingDecision(True, StreamingReason.OK, self._state.value)


def transition_table() -> Dict[TurnState, FrozenSet[TurnState]]:
    return dict(_TRANSITIONS)


__all__ = ["TurnSnapshot", "TurnStateMachine", "transition_table"]
