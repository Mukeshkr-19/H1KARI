"""Compatibility turn facade over canonical VoiceStreamingRuntime.

Production wake/sleep/turn authority lives in
``core.voice_streaming.runtime.VoiceStreamingRuntime``. This module maps the
bounded ``TurnState`` API onto that runtime so the two packages cannot disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Optional, Set

from core.voice_streaming.contracts import VoiceStreamState

from core.voice_streaming.interruption_evidence import InterruptionEvidence

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

# Documented compatibility graph (facade API). Canonical transitions are enforced
# by VoiceStreamStateMachine inside VoiceStreamingRuntime.
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


def _ns_clock(clock: MonoClock) -> Callable[[], int]:
    def _inner() -> int:
        value = float(clock())
        if value != value or value in (float("inf"), float("-inf")) or value < 0.0:
            return 0
        return int(value * 1_000_000_000)

    return _inner


class TurnStateMachine:
    """Facade turn control. Canonical mutable authority is VoiceStreamingRuntime."""

    def __init__(self, session_id: str, clock: MonoClock) -> None:
        self._session_id = validate_id(session_id, "session_id")
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        self._clock = clock
        from core.voice_streaming.runtime import VoiceStreamingRuntime
        self._runtime = VoiceStreamingRuntime(self._session_id, clock=_ns_clock(clock))
        self._runtime.start_wake_listening()
        self._wake = WakeSleepAuthority(self)
        self._barge = BargeInController()
        self._user_utt: Optional[str] = None
        self._assistant_utt: Optional[str] = None
        self._response_id: Optional[str] = None
        self._seen_utt: Set[str] = set()
        self._wake_candidate = False
        self._closed = False
        self._last_wake_id: Optional[str] = None
        self._draining = False
        self._pending_interrupt_id: Optional[str] = None
        self._playback_stop_confirmed = False

    @property
    def canonical_runtime(self):
        return self._runtime

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def last_wake_id(self) -> Optional[str]:
        return self._last_wake_id

    @property
    def responses_suppressed(self) -> bool:
        if self._closed:
            return True
        return self._runtime.is_wake_listening or self._wake_candidate

    def _map_state(self) -> TurnState:
        if self._closed:
            return TurnState.CLOSED
        if self._draining:
            return TurnState.DRAINING
        vs = self._runtime.state
        if vs in (VoiceStreamState.IDLE, VoiceStreamState.WAKE_LISTENING):
            return TurnState.WAKE_CANDIDATE if self._wake_candidate else TurnState.SLEEPING
        if vs == VoiceStreamState.ACTIVE_LISTENING:
            return TurnState.LISTENING
        if vs in (VoiceStreamState.USER_SPEAKING, VoiceStreamState.FINALIZING_USER_TURN):
            return TurnState.USER_SPEAKING
        if vs == VoiceStreamState.THINKING:
            return TurnState.ASSISTANT_THINKING
        if vs == VoiceStreamState.ASSISTANT_SPEAKING:
            return TurnState.ASSISTANT_SPEAKING
        if vs == VoiceStreamState.INTERRUPTING:
            return TurnState.DRAINING
        if vs == VoiceStreamState.INTERRUPTED:
            return TurnState.INTERRUPTED
        if vs in (VoiceStreamState.STOPPING, VoiceStreamState.ERROR):
            return TurnState.CLOSED
        return TurnState.SLEEPING

    @property
    def state(self) -> TurnState:
        return self._map_state()

    @property
    def wake(self) -> WakeSleepAuthority:
        return self._wake

    @property
    def barge(self) -> BargeInController:
        return self._barge

    def snapshot(self) -> TurnSnapshot:
        return TurnSnapshot(
            state=self.state,
            session_id=self._session_id,
            active_user_utterance_id=self._user_utt,
            active_assistant_utterance_id=self._assistant_utt,
            active_response_id=self._response_id,
        )

    def allowed_targets(self) -> FrozenSet[TurnState]:
        return _TRANSITIONS[self.state]

    def cancel(self) -> StreamingDecision:
        if self._closed:
            return StreamingDecision(False, StreamingReason.CLOSED, TurnState.CLOSED.value)
        response_id = self._response_id
        if response_id is not None:
            self._runtime.clear_assistant_playback(expected_response_id=response_id)
        self._runtime.cancel_active()
        self._closed = True
        self._wake_candidate = False
        self._draining = False
        self._pending_interrupt_id = None
        self._playback_stop_confirmed = False
        self._user_utt = None
        self._assistant_utt = None
        self._response_id = None
        self._barge.set_active_utterance(None)
        return StreamingDecision(True, StreamingReason.CANCELLED, TurnState.CLOSED.value)

    def observe_sleeping_audio(self) -> StreamingDecision:
        if self._closed:
            return StreamingDecision(False, StreamingReason.CLOSED, TurnState.CLOSED.value)
        if not self._runtime.is_wake_listening:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        self._wake_candidate = True
        return StreamingDecision(True, StreamingReason.OK, TurnState.WAKE_CANDIDATE.value)

    def submit_wake(self, evidence: WakeEvidence) -> StreamingDecision:
        if self._closed:
            return StreamingDecision(False, StreamingReason.CLOSED, TurnState.CLOSED.value)
        if not isinstance(evidence, WakeEvidence):
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        if evidence.session_id != self._session_id:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self.state.value)
        if not self._runtime.is_wake_listening and not self._wake_candidate:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        if not evidence.wake_verified:
            self._wake_candidate = False
            self._runtime.reset_to_wake_listening()
            return StreamingDecision(False, StreamingReason.WAKE_REQUIRED, TurnState.SLEEPING.value)
        if not evidence.speaker_verified:
            self._wake_candidate = False
            self._runtime.reset_to_wake_listening()
            return StreamingDecision(False, StreamingReason.SPEAKER_DENIED, TurnState.SLEEPING.value)
        ok = self._runtime.process_wake_event(
            "hikari",
            is_verified=True,
            speaker_id="owner",
        )
        if not ok:
            if self._runtime.is_wake_listening:
                self._runtime.start_active_listening()
            if self._runtime.state != VoiceStreamState.ACTIVE_LISTENING:
                self._wake_candidate = False
                return StreamingDecision(False, StreamingReason.WAKE_REQUIRED, self.state.value)
        self._wake_candidate = False
        self._last_wake_id = evidence.wake_id
        return StreamingDecision(True, StreamingReason.OK, TurnState.LISTENING.value)

    def goodbye(self) -> StreamingDecision:
        if self._closed:
            return StreamingDecision(False, StreamingReason.CLOSED, TurnState.CLOSED.value)
        response_id = self._response_id
        if response_id is not None:
            self._runtime.clear_assistant_playback(expected_response_id=response_id)
        self._runtime.reset_to_wake_listening()
        self._wake_candidate = False
        self._draining = False
        self._pending_interrupt_id = None
        self._playback_stop_confirmed = False
        self._user_utt = None
        self._assistant_utt = None
        self._response_id = None
        self._barge.set_active_utterance(None)
        return StreamingDecision(True, StreamingReason.OK, TurnState.SLEEPING.value)

    def begin_user_speech(self, utterance_id: str) -> StreamingDecision:
        uid = validate_id(utterance_id, "utterance_id")
        if self.responses_suppressed:
            return StreamingDecision(False, StreamingReason.SLEEPING_SUPPRESSED, self.state.value)
        if uid in self._seen_utt:
            return StreamingDecision(False, StreamingReason.DUPLICATE, self.state.value)
        if len(self._seen_utt) >= 4096:
            return StreamingDecision(False, StreamingReason.BUFFER_EXHAUSTED, self.state.value)
        sm = self._runtime.state_machine
        ok = sm.transition_to(
            VoiceStreamState.USER_SPEAKING,
            event_type="facade_user_speech",
            monotonic_ns=self._runtime.now_ns(),
            reason="Facade user speech begin",
        )
        if not ok:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        self._seen_utt.add(uid)
        self._user_utt = uid
        return StreamingDecision(True, StreamingReason.OK, TurnState.USER_SPEAKING.value)

    def end_user_speech(self) -> StreamingDecision:
        if self.state != TurnState.USER_SPEAKING:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        sm = self._runtime.state_machine
        ok = sm.transition_to(
            VoiceStreamState.FINALIZING_USER_TURN,
            event_type="facade_finalize",
            monotonic_ns=self._runtime.now_ns(),
            reason="Facade finalize user speech",
        )
        if not ok:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        ok = sm.transition_to(
            VoiceStreamState.THINKING,
            event_type="facade_thinking",
            monotonic_ns=self._runtime.now_ns(),
            reason="Facade end user speech",
        )
        if not ok:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        return StreamingDecision(True, StreamingReason.OK, TurnState.ASSISTANT_THINKING.value)

    def begin_assistant_response(self, *, utterance_id: str, response_id: str) -> StreamingDecision:
        uid = validate_id(utterance_id, "utterance_id")
        rid = validate_id(response_id, "response_id")
        if self.responses_suppressed:
            return StreamingDecision(False, StreamingReason.SLEEPING_SUPPRESSED, self.state.value)
        if self.state != TurnState.ASSISTANT_THINKING:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        if uid in self._seen_utt:
            return StreamingDecision(False, StreamingReason.DUPLICATE, self.state.value)
        if len(self._seen_utt) >= 4096:
            return StreamingDecision(False, StreamingReason.BUFFER_EXHAUSTED, self.state.value)
        # Bind while THINKING so failed bind never mutates facade utterance/response.
        if not self._runtime.bind_assistant_playback(uid, rid):
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        ok = self._runtime.assistant_speaking_start()
        if not ok:
            self._runtime.clear_assistant_playback(expected_response_id=rid)
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        self._seen_utt.add(uid)
        self._assistant_utt = uid
        self._response_id = rid
        self._barge.set_active_utterance(uid)
        self._pending_interrupt_id = None
        self._playback_stop_confirmed = False
        return StreamingDecision(True, StreamingReason.OK, TurnState.ASSISTANT_SPEAKING.value)

    def complete_assistant_response(self, *, response_id: str) -> StreamingDecision:
        rid = validate_id(response_id, "response_id")
        if self._response_id is not None and rid != self._response_id:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self.state.value)
        if self.state not in (TurnState.ASSISTANT_SPEAKING, TurnState.DRAINING):
            return StreamingDecision(False, StreamingReason.STALE_INTERRUPTION, self.state.value)
        ok = self._runtime.state_machine.transition_to(
            VoiceStreamState.ACTIVE_LISTENING,
            event_type="facade_assistant_done",
            monotonic_ns=self._runtime.now_ns(),
            reason="Facade assistant complete",
        )
        if not ok:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        if not self._runtime.clear_assistant_playback(expected_response_id=rid):
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self.state.value)
        self._assistant_utt = None
        self._response_id = None
        self._draining = False
        self._pending_interrupt_id = None
        self._playback_stop_confirmed = False
        self._barge.set_active_utterance(None)
        return StreamingDecision(True, StreamingReason.OK, TurnState.LISTENING.value)

    def _event_observed_ns(self, event: InterruptionEvent) -> int:
        return int(float(event.observed_at_mono) * 1_000_000_000)

    def _deny_interrupt(self, reason: StreamingReason) -> BargeInResult:
        return BargeInResult(False, reason, None, False)

    def _correlate_interruption_evidence(
        self,
        event: InterruptionEvent,
        evidence: InterruptionEvidence,
    ) -> Optional[BargeInResult]:
        """Fail closed before any drain mutation when evidence cannot be trusted."""
        if not isinstance(event, InterruptionEvent):
            return self._deny_interrupt(StreamingReason.INVALID_INPUT)
        if not isinstance(evidence, InterruptionEvidence):
            return self._deny_interrupt(StreamingReason.INVALID_INPUT)
        if evidence.stream_id != self._session_id or event.session_id != self._session_id:
            return self._deny_interrupt(StreamingReason.CORRELATION_MISMATCH)
        if evidence.interruption_id != event.interruption_id:
            return self._deny_interrupt(StreamingReason.CORRELATION_MISMATCH)
        if evidence.observed_at_ns != self._event_observed_ns(event):
            return self._deny_interrupt(StreamingReason.CORRELATION_MISMATCH)
        if not evidence.speaker_verified:
            return self._deny_interrupt(StreamingReason.SPEAKER_DENIED)
        # Active assistant utterance/response must match both event and evidence.
        if self._assistant_utt is None:
            return self._deny_interrupt(StreamingReason.STALE_INTERRUPTION)
        if event.assistant_utterance_id != self._assistant_utt:
            return self._deny_interrupt(StreamingReason.CORRELATION_MISMATCH)
        allowed_targets = {self._assistant_utt}
        if self._response_id is not None:
            allowed_targets.add(self._response_id)
        allowed_targets.add(self._runtime.interruption_target_id())
        if evidence.target_assistant_utterance_id not in allowed_targets:
            return self._deny_interrupt(StreamingReason.CORRELATION_MISMATCH)
        return None

    def interrupt(
        self,
        event: InterruptionEvent,
        *,
        evidence: Optional[InterruptionEvidence] = None,
        is_authenticated: Optional[bool] = None,
    ) -> BargeInResult:
        """Request barge-in via canonical runtime.

        ``is_authenticated`` is ignored and grants no authority. Callers must
        supply correlated ``InterruptionEvidence``. Drain begins only after the
        runtime accepts the request. Playback stop confirmation is separate.
        """
        del is_authenticated  # never trusted
        if evidence is None:
            return self._deny_interrupt(StreamingReason.INVALID_INPUT)

        correlated = self._correlate_interruption_evidence(event, evidence)
        if correlated is not None:
            return correlated

        if event.session_id != self._session_id:
            return self._deny_interrupt(StreamingReason.CORRELATION_MISMATCH)
        try:
            now = float(self._clock())
        except Exception:
            return self._deny_interrupt(StreamingReason.INVALID_INPUT)
        if event.observed_at_mono > now + 1.0 or now - event.observed_at_mono > 30.0:
            return self._deny_interrupt(StreamingReason.STALE_INTERRUPTION)

        # Facade policy check first; do not mark turn draining until runtime accepts.
        result = self._barge.handle(event, turn_state=self.state)
        if not result.accepted:
            return result

        req_ok = self._runtime.request_interruption(
            evidence.interruption_id,
            evidence=evidence,
            monotonic_ns=evidence.observed_at_ns,
        )
        if not req_ok:
            # Roll back controller drain flag; keep active utterance for replay/fail-closed.
            if self._assistant_utt is not None:
                self._barge.set_active_utterance(self._assistant_utt)
            return self._deny_interrupt(StreamingReason.STALE_INTERRUPTION)

        self._draining = True
        self._pending_interrupt_id = evidence.interruption_id
        self._playback_stop_confirmed = False
        return BargeInResult(True, StreamingReason.OK, result.cancel_utterance_id, True)

    def notify_playback_stopped(
        self,
        interruption_id: str,
        *,
        bytes_played_before_stop: int = 0,
    ) -> StreamingDecision:
        """Confirm physical playback termination after an accepted interruption."""
        if self._closed:
            return StreamingDecision(False, StreamingReason.CLOSED, TurnState.CLOSED.value)
        if not self._draining or self._pending_interrupt_id is None:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        try:
            iid = validate_id(interruption_id, "interruption_id")
        except ValueError:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        if iid != self._pending_interrupt_id:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self.state.value)
        if isinstance(bytes_played_before_stop, bool) or not isinstance(bytes_played_before_stop, int):
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        if bytes_played_before_stop < 0:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        ok = self._runtime.confirm_interruption(
            iid,
            is_confirmed=True,
            bytes_played_before_stop=bytes_played_before_stop,
            monotonic_ns=self._runtime.now_ns(),
        )
        if not ok:
            return StreamingDecision(False, StreamingReason.STALE_INTERRUPTION, self.state.value)
        self._playback_stop_confirmed = True
        return StreamingDecision(True, StreamingReason.OK, self.state.value)

    def finish_drain(self) -> StreamingDecision:
        if not self._playback_stop_confirmed:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT, self.state.value)
        drain = self._barge.complete_drain()
        if not drain.accepted:
            return drain
        if self._response_id is None:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self.state.value)
        if not self._runtime.clear_assistant_playback(expected_response_id=self._response_id):
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self.state.value)
        self._assistant_utt = None
        self._response_id = None
        self._draining = False
        self._pending_interrupt_id = None
        self._playback_stop_confirmed = False
        self._barge.set_active_utterance(None)
        self._runtime.state_machine.transition_to(
            VoiceStreamState.ACTIVE_LISTENING,
            event_type="facade_drain_complete",
            monotonic_ns=self._runtime.now_ns(),
            reason="Facade drain complete",
        )
        return StreamingDecision(True, StreamingReason.OK, TurnState.LISTENING.value)

    def reject_stale_utterance(self, utterance_id: str) -> StreamingDecision:
        uid = validate_id(utterance_id, "utterance_id")
        if self._user_utt is None and self._assistant_utt is None:
            return StreamingDecision(False, StreamingReason.STALE_INTERRUPTION, self.state.value)
        if self._user_utt is not None and uid != self._user_utt and self._assistant_utt != uid:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self.state.value)
        if self._assistant_utt is not None and uid != self._assistant_utt and uid != self._user_utt:
            return StreamingDecision(False, StreamingReason.CORRELATION_MISMATCH, self.state.value)
        return StreamingDecision(True, StreamingReason.OK, self.state.value)


def transition_table() -> Dict[TurnState, FrozenSet[TurnState]]:
    return dict(_TRANSITIONS)


__all__ = ["TurnSnapshot", "TurnStateMachine", "transition_table"]
