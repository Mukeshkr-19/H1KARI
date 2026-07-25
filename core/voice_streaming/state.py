"""Deterministic state machine for streaming voice pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from core.voice_streaming.contracts import (
    AECCapability,
    AccessibilityState,
    AuthDecision,
    CaptureState,
    FinalTranscript,
    InterimTranscript,
    InterruptionConfirmation,
    InterruptionRequest,
    PlaybackState,
    StateTransitionRecord,
    StreamingVoiceFailure,
    VADCapability,
    VADEvent,
    VerifiedWakeEvent,
    VoiceStreamState,
    validate_monotonic_ns,
    validate_stream_id,
)
from core.voice_streaming.transcript import StreamingTranscriptAccumulator


_VALID_TRANSITIONS: Dict[VoiceStreamState, Set[VoiceStreamState]] = {
    VoiceStreamState.IDLE: {
        VoiceStreamState.WAKE_LISTENING,
        VoiceStreamState.ACTIVE_LISTENING,
        VoiceStreamState.STOPPING,
        VoiceStreamState.ERROR,
    },
    VoiceStreamState.WAKE_LISTENING: {
        VoiceStreamState.ACTIVE_LISTENING,
        VoiceStreamState.IDLE,
        VoiceStreamState.STOPPING,
        VoiceStreamState.ERROR,
    },
    VoiceStreamState.ACTIVE_LISTENING: {
        VoiceStreamState.USER_SPEAKING,
        VoiceStreamState.WAKE_LISTENING,
        VoiceStreamState.THINKING,
        VoiceStreamState.IDLE,
        VoiceStreamState.STOPPING,
        VoiceStreamState.ERROR,
    },
    VoiceStreamState.USER_SPEAKING: {
        VoiceStreamState.FINALIZING_USER_TURN,
        VoiceStreamState.ACTIVE_LISTENING,
        VoiceStreamState.WAKE_LISTENING,
        VoiceStreamState.IDLE,
        VoiceStreamState.STOPPING,
        VoiceStreamState.ERROR,
    },
    VoiceStreamState.FINALIZING_USER_TURN: {
        VoiceStreamState.THINKING,
        VoiceStreamState.ACTIVE_LISTENING,
        VoiceStreamState.WAKE_LISTENING,
        VoiceStreamState.IDLE,
        VoiceStreamState.STOPPING,
        VoiceStreamState.ERROR,
    },
    VoiceStreamState.THINKING: {
        VoiceStreamState.ASSISTANT_SPEAKING,
        VoiceStreamState.WAKE_LISTENING,
        VoiceStreamState.ACTIVE_LISTENING,
        VoiceStreamState.IDLE,
        VoiceStreamState.STOPPING,
        VoiceStreamState.ERROR,
    },
    VoiceStreamState.ASSISTANT_SPEAKING: {
        VoiceStreamState.ACTIVE_LISTENING,
        VoiceStreamState.WAKE_LISTENING,
        VoiceStreamState.INTERRUPTING,
        VoiceStreamState.IDLE,
        VoiceStreamState.STOPPING,
        VoiceStreamState.ERROR,
    },
    VoiceStreamState.INTERRUPTING: {
        VoiceStreamState.INTERRUPTED,
        VoiceStreamState.ASSISTANT_SPEAKING,
        VoiceStreamState.STOPPING,
        VoiceStreamState.ERROR,
    },
    VoiceStreamState.INTERRUPTED: {
        VoiceStreamState.USER_SPEAKING,
        VoiceStreamState.ACTIVE_LISTENING,
        VoiceStreamState.WAKE_LISTENING,
        VoiceStreamState.THINKING,
        VoiceStreamState.IDLE,
        VoiceStreamState.STOPPING,
        VoiceStreamState.ERROR,
    },
    VoiceStreamState.STOPPING: {
        VoiceStreamState.IDLE,
        VoiceStreamState.WAKE_LISTENING,
        VoiceStreamState.ERROR,
    },
    VoiceStreamState.ERROR: {
        VoiceStreamState.IDLE,
        VoiceStreamState.WAKE_LISTENING,
        VoiceStreamState.ACTIVE_LISTENING,
    },
}


def is_valid_transition(
    from_state: VoiceStreamState, to_state: VoiceStreamState
) -> bool:
    """Check if transitioning from ``from_state`` to ``to_state`` is allowed."""
    if from_state == to_state:
        return True
    return to_state in _VALID_TRANSITIONS.get(from_state, set())


class VoiceStreamStateMachine:
    """Deterministic state machine for voice streaming lifecycle."""

    def __init__(
        self,
        stream_id: str,
        *,
        max_history: int = 100,
        aec_capability: Optional[AECCapability] = None,
        vad_capability: Optional[VADCapability] = None,
    ) -> None:
        self.stream_id = validate_stream_id(stream_id)
        if isinstance(max_history, bool) or not isinstance(max_history, int):
            raise ValueError("max_history must be an integer")
        if max_history <= 0:
            raise ValueError("max_history must be positive")
        self.max_history = max_history
        self._current_state: VoiceStreamState = VoiceStreamState.IDLE
        self._last_monotonic_ns: int = 0
        self._history: List[StateTransitionRecord] = []

        self.aec_capability: AECCapability = aec_capability or AECCapability(
            enabled=True, available=True
        )
        self.vad_capability: VADCapability = vad_capability or VADCapability(
            enabled=True, available=True
        )

        self.accumulator: StreamingTranscriptAccumulator = StreamingTranscriptAccumulator(
            self.stream_id
        )
        self.active_interruption_request: Optional[InterruptionRequest] = None
        self.last_failure: Optional[StreamingVoiceFailure] = None
        self.reduced_motion: bool = False
        self.non_audio_fallback: bool = True

    @property
    def current_state(self) -> VoiceStreamState:
        return self._current_state

    @property
    def last_monotonic_ns(self) -> int:
        return self._last_monotonic_ns

    def get_history(self) -> Tuple[StateTransitionRecord, ...]:
        """Return transition audit history as an immutable tuple."""
        return tuple(self._history)

    def transition_to(
        self,
        new_state: VoiceStreamState,
        *,
        event_type: str,
        monotonic_ns: int,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Attempt state transition enforcing monotonic time and valid state matrix."""
        try:
            ts = validate_monotonic_ns(monotonic_ns)
        except (ValueError, TypeError):
            return False

        if ts < self._last_monotonic_ns:
            # Monotonic time cannot move backward!
            return False

        if not is_valid_transition(self._current_state, new_state):
            # Invalid transition fails closed!
            return False

        old_state = self._current_state
        self._current_state = new_state
        self._last_monotonic_ns = ts

        record = StateTransitionRecord(
            old_state=old_state,
            new_state=new_state,
            event_type=event_type,
            monotonic_ns=ts,
            reason=reason,
            details=details or {},
        )
        self._history.append(record)
        if len(self._history) > self.max_history:
            self._history.pop(0)

        return True

    def start_wake_listening(self, monotonic_ns: int) -> bool:
        """IDLE -> WAKE_LISTENING (Passive wake listening)."""
        return self.transition_to(
            VoiceStreamState.WAKE_LISTENING,
            event_type="start_wake_listening",
            monotonic_ns=monotonic_ns,
            reason="Listening for verified wake word",
        )

    def start_active_listening(self, monotonic_ns: int) -> bool:
        """IDLE -> ACTIVE_LISTENING (Active command mode)."""
        return self.transition_to(
            VoiceStreamState.ACTIVE_LISTENING,
            event_type="start_active_listening",
            monotonic_ns=monotonic_ns,
            reason="Armed for active command listening",
        )

    def on_wake_event(self, event: VerifiedWakeEvent) -> bool:
        """Process a wake word event.

        Requires verified wake event to move from WAKE_LISTENING to ACTIVE_LISTENING.
        """
        if not isinstance(event, VerifiedWakeEvent):
            return False
        if event.stream_id != self.stream_id:
            return False
        if self._current_state != VoiceStreamState.WAKE_LISTENING:
            return False
        if not event.is_verified:
            # Unverified wake event rejected!
            return False

        return self.transition_to(
            VoiceStreamState.ACTIVE_LISTENING,
            event_type="wake_word_verified",
            monotonic_ns=event.monotonic_ns,
            reason=f"Wake word '{event.wake_word}' verified",
            details={"wake_word": event.wake_word, "speaker_id": event.speaker_id},
        )

    def on_vad_event(self, event: VADEvent) -> bool:
        """Process Voice Activity Detection event."""
        if not isinstance(event, VADEvent):
            return False
        if event.stream_id != self.stream_id:
            return False

        # PASSIVE WAKE vs ACTIVE LISTENING GUARANTEE:
        # Ordinary VAD speech events CANNOT trigger active listening from WAKE_LISTENING!
        if self._current_state == VoiceStreamState.WAKE_LISTENING:
            return False

        if self._current_state == VoiceStreamState.ACTIVE_LISTENING and event.is_speech:
            return self.transition_to(
                VoiceStreamState.USER_SPEAKING,
                event_type="vad_speech_start",
                monotonic_ns=event.monotonic_ns,
                reason="VAD detected speech start",
            )

        if self._current_state == VoiceStreamState.USER_SPEAKING and not event.is_speech:
            return self.transition_to(
                VoiceStreamState.FINALIZING_USER_TURN,
                event_type="vad_speech_end",
                monotonic_ns=event.monotonic_ns,
                reason="VAD detected speech end",
            )

        return False

    def on_interim_transcript(self, transcript: InterimTranscript) -> bool:
        """Process interim transcript.

        Fails closed if in WAKE_LISTENING mode or if cross-session/stale.
        """
        if not isinstance(transcript, InterimTranscript):
            return False
        if transcript.stream_id != self.stream_id:
            return False

        # PASSIVE WAKE vs ACTIVE LISTENING GUARANTEE:
        # Passive wake-listening CANNOT accept ordinary command transcripts!
        if self._current_state == VoiceStreamState.WAKE_LISTENING:
            return False

        if transcript.monotonic_ns < self._last_monotonic_ns:
            return False

        if self._current_state == VoiceStreamState.ACTIVE_LISTENING:
            if not self.accumulator.update_interim(transcript):
                return False
            if not self.transition_to(
                VoiceStreamState.USER_SPEAKING,
                event_type="interim_transcript_start",
                monotonic_ns=transcript.monotonic_ns,
                reason="Interim user transcript received",
            ):
                return False

        if self._current_state == VoiceStreamState.USER_SPEAKING:
            if self.accumulator.current_interim is transcript:
                return True
            return self.accumulator.update_interim(transcript)

        return False

    def on_final_transcript(self, transcript: FinalTranscript) -> bool:
        """Process final user transcript segment."""
        if not isinstance(transcript, FinalTranscript):
            return False
        if transcript.stream_id != self.stream_id:
            return False

        # PASSIVE WAKE vs ACTIVE LISTENING GUARANTEE:
        if self._current_state == VoiceStreamState.WAKE_LISTENING:
            return False

        if self._current_state in (
            VoiceStreamState.ACTIVE_LISTENING,
            VoiceStreamState.USER_SPEAKING,
            VoiceStreamState.FINALIZING_USER_TURN,
        ):
            if transcript.end_monotonic_ns < self._last_monotonic_ns:
                return False
            if not self.accumulator.add_final(transcript):
                return False

            return self.transition_to(
                VoiceStreamState.THINKING,
                event_type="final_transcript_received",
                monotonic_ns=transcript.end_monotonic_ns,
                reason="User turn finalized with final transcript",
            )

        return False

    def assistant_thinking(self, monotonic_ns: int) -> bool:
        """Transition to THINKING state."""
        return self.transition_to(
            VoiceStreamState.THINKING,
            event_type="assistant_thinking",
            monotonic_ns=monotonic_ns,
            reason="Assistant processing request",
        )

    def assistant_speaking_start(self, monotonic_ns: int) -> bool:
        """THINKING -> ASSISTANT_SPEAKING."""
        return self.transition_to(
            VoiceStreamState.ASSISTANT_SPEAKING,
            event_type="assistant_speaking_start",
            monotonic_ns=monotonic_ns,
            reason="Assistant audio playback started",
        )

    def add_assistant_segment(self, transcript: FinalTranscript) -> bool:
        """Record final assistant speech segment and transition to ACTIVE_LISTENING."""
        if not isinstance(transcript, FinalTranscript):
            return False
        if transcript.role != "assistant":
            return False
        if not self.accumulator.add_final(transcript):
            return False

        if self._current_state == VoiceStreamState.ASSISTANT_SPEAKING:
            return self.transition_to(
                VoiceStreamState.ACTIVE_LISTENING,
                event_type="assistant_speaking_end",
                monotonic_ns=transcript.end_monotonic_ns,
                reason="Assistant speech complete; returning to active listening",
            )
        return True

    def silent_goodbye(self, monotonic_ns: int) -> bool:
        """Silent goodbye/sleep transition returning directly to WAKE_LISTENING."""
        if self._current_state in (
            VoiceStreamState.THINKING,
            VoiceStreamState.ASSISTANT_SPEAKING,
            VoiceStreamState.ACTIVE_LISTENING,
            VoiceStreamState.USER_SPEAKING,
            VoiceStreamState.FINALIZING_USER_TURN,
        ):
            return self.transition_to(
                VoiceStreamState.WAKE_LISTENING,
                event_type="silent_goodbye",
                monotonic_ns=monotonic_ns,
                reason="Silent goodbye requested; returning to passive wake listening",
            )
        return False

    def request_interruption(self, req: InterruptionRequest) -> bool:
        """Request barge-in during assistant speaking.

        BARGE-IN REQUIRES EXPLICIT AUTHENTICATED INTERRUPTION EVENT!
        Noise or unauthenticated requests CANNOT trigger interruption.
        """
        if not isinstance(req, InterruptionRequest):
            return False
        if req.stream_id != self.stream_id:
            return False
        if self._current_state != VoiceStreamState.ASSISTANT_SPEAKING:
            return False

        if not req.is_authenticated:
            # Unauthenticated barge-in REJECTED!
            return False

        if self.transition_to(
            VoiceStreamState.INTERRUPTING,
            event_type="interruption_requested",
            monotonic_ns=req.monotonic_ns,
            reason="Authenticated barge-in requested",
            details={"request_id": req.request_id, "speaker_id": req.speaker_id},
        ):
            self.active_interruption_request = req
            return True
        return False

    def confirm_interruption(self, conf: InterruptionConfirmation) -> bool:
        """Confirm physical playback stop following interruption request."""
        if not isinstance(conf, InterruptionConfirmation):
            return False
        if conf.stream_id != self.stream_id:
            return False
        if self._current_state != VoiceStreamState.INTERRUPTING:
            return False

        if (
            self.active_interruption_request
            and conf.request_id != self.active_interruption_request.request_id
        ):
            return False

        if conf.is_confirmed:
            return self.transition_to(
                VoiceStreamState.INTERRUPTED,
                event_type="interruption_confirmed",
                monotonic_ns=conf.monotonic_ns,
                reason="Physical playback stop confirmed",
                details={"bytes_played": conf.bytes_played_before_stop},
            )
        else:
            # Interruption failed or cancelled -> return to ASSISTANT_SPEAKING
            return self.transition_to(
                VoiceStreamState.ASSISTANT_SPEAKING,
                event_type="interruption_failed",
                monotonic_ns=conf.monotonic_ns,
                reason="Interruption not confirmed; resuming playback",
            )

    def on_noise_event(self, monotonic_ns: int, db_level: float) -> bool:
        """Noise events CANNOT grant interruption authority."""
        # Intentionally no state change for noise!
        return False

    def set_aec_capability(self, aec: AECCapability) -> None:
        if isinstance(aec, AECCapability):
            self.aec_capability = aec

    def set_vad_capability(self, vad: VADCapability) -> None:
        if isinstance(vad, VADCapability):
            self.vad_capability = vad

    def fail(self, failure: StreamingVoiceFailure) -> bool:
        """Transition to ERROR state on failure."""
        if not isinstance(failure, StreamingVoiceFailure):
            return False
        if failure.stream_id != self.stream_id:
            return False

        self.last_failure = failure
        return self.transition_to(
            VoiceStreamState.ERROR,
            event_type="failure_event",
            monotonic_ns=failure.monotonic_ns,
            reason=f"Error: [{failure.error_code}] {failure.message}",
            details={"error_code": failure.error_code, "recoverable": failure.recoverable},
        )

    def reset(self, monotonic_ns: int = 0) -> bool:
        """Reset volatile session state and return to IDLE."""
        ts = max(monotonic_ns, self._last_monotonic_ns)
        old_state = self._current_state
        self._current_state = VoiceStreamState.IDLE
        self._last_monotonic_ns = ts
        self.accumulator.reset()
        self.active_interruption_request = None
        self.last_failure = None

        record = StateTransitionRecord(
            old_state=old_state,
            new_state=VoiceStreamState.IDLE,
            event_type="session_reset",
            monotonic_ns=ts,
            reason="Explicit session reset",
            details={},
        )
        self._history.append(record)
        if len(self._history) > self.max_history:
            self._history.pop(0)
        return True

    def get_accessibility_state(self) -> AccessibilityState:
        """Expose current state view for accessibility indicators and screen readers."""
        state = self._current_state

        indicator_map = {
            VoiceStreamState.IDLE: "idle",
            VoiceStreamState.WAKE_LISTENING: "listening",
            VoiceStreamState.ACTIVE_LISTENING: "listening",
            VoiceStreamState.USER_SPEAKING: "listening",
            VoiceStreamState.FINALIZING_USER_TURN: "thinking",
            VoiceStreamState.THINKING: "thinking",
            VoiceStreamState.ASSISTANT_SPEAKING: "speaking",
            VoiceStreamState.INTERRUPTING: "interrupted",
            VoiceStreamState.INTERRUPTED: "interrupted",
            VoiceStreamState.STOPPING: "idle",
            VoiceStreamState.ERROR: "error",
        }
        indicator = indicator_map.get(state, "idle")

        announcement_map = {
            VoiceStreamState.IDLE: "Voice session idle.",
            VoiceStreamState.WAKE_LISTENING: "Listening for wake word.",
            VoiceStreamState.ACTIVE_LISTENING: "Listening for user command.",
            VoiceStreamState.USER_SPEAKING: "User speaking...",
            VoiceStreamState.FINALIZING_USER_TURN: "Finalizing user input.",
            VoiceStreamState.THINKING: "Assistant thinking...",
            VoiceStreamState.ASSISTANT_SPEAKING: "Assistant speaking...",
            VoiceStreamState.INTERRUPTING: "Interruption requested.",
            VoiceStreamState.INTERRUPTED: "Assistant playback interrupted.",
            VoiceStreamState.STOPPING: "Stopping voice session.",
            VoiceStreamState.ERROR: f"Voice error: {self.last_failure.message if self.last_failure else 'Unknown error'}",
        }
        announcement = announcement_map.get(state, "Voice status updated.")

        # Status warnings if AEC or VAD unavailable
        if not self.aec_capability.available:
            announcement += " Warning: Echo cancellation unavailable."
        if not self.vad_capability.available:
            announcement += " Warning: Voice activity detection unavailable."

        caption_text = ""
        interim = self.accumulator.current_interim
        recent = self.accumulator.get_recent_segments(1)
        if interim and interim.text:
            caption_text = interim.text
        elif recent:
            caption_text = recent[0].text

        return AccessibilityState(
            indicator=indicator,
            caption_text=caption_text,
            announcement=announcement,
            non_audio_fallback=self.non_audio_fallback,
            manual_stop_available=True,
            reduced_motion=self.reduced_motion,
            error_message=self.last_failure.message if (state == VoiceStreamState.ERROR and self.last_failure) else None,
        )
