"""Production streaming-voice runtime adapter for H1KARI daemon.

Composes frame processing, VAD engine, echo policy evaluation, state machine transitions,
and transcript accumulator into a typed, deterministic, pure in-memory runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from core.voice_streaming.contracts import (
    AECCapability,
    AccessibilityState,
    AuthDecision,
    FinalTranscript,
    InterimTranscript,
    InterruptionConfirmation,
    InterruptionRequest,
    StreamingVoiceFailure,
    VADCapability,
    VerifiedWakeEvent,
    VoiceStreamState,
    validate_monotonic_ns,
    validate_stream_id,
)
from core.voice_streaming.echo_policy import (
    EchoCapability,
    EchoMode,
    EchoPolicyContext,
    EchoPolicyDecision,
    EchoPolicyEvaluator,
)
from core.voice_streaming.frame_pipeline import (
    AudioFrame,
    AudioFramePipeline,
    FramePipelineConfig,
    FramePipelineMetrics,
)
from core.voice_streaming.state import VoiceStreamStateMachine
from core.voice_streaming.transcript import StreamingTranscriptAccumulator
from core.voice_streaming.vad import (
    VADConfig,
    VADEngineState,
    VADFrameMeasurement,
    VADState,
    VADStateTransitionEvent,
)

STOP_WORDS = {
    "bye",
    "goodbye",
    "good bye",
    "exit",
    "stop",
    "go to sleep",
    "sleep",
    "that's all",
    "that's it",
    "nothing else",
    "done",
    "thank you",
    "thanks",
    "okay goodbye",
    "see you later",
}


def extract_wake_command(text: str) -> Optional[str]:
    """Extract trailing command after explicit HIKARI wake prefix.

    Returns:
      - "" for bare wake activation (e.g. "Hikari")
      - trailing command for same-utterance wake (e.g. "Hey Hikari, what time is it?" -> "what time is it?")
      - None if no wake word match
    """
    if not isinstance(text, str):
        return None
    match = re.fullmatch(
        r"\s*(?:(?:hey|okay|hi)[\s,]+)?hikari\b[\s,.:;!?-]*(.*?)\s*",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    return match.group(1).strip()


def is_wake_phrase(text: str) -> bool:
    """Return True only if text is a bare HIKARI wake phrase."""
    return extract_wake_command(text) == ""


def is_stop_command(text: str) -> bool:
    """Return True only for an explicit command to resume wake-word listening."""
    if not isinstance(text, str):
        return False
    normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())
    return normalized in STOP_WORDS


def is_speech_interrupt_command(text: str) -> bool:
    """Classify only explicit stop commands during playback."""
    if not isinstance(text, str):
        return False
    normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())
    return normalized in {"hikari stop", "hikari done", "stop hikari"}


@dataclass(frozen=True)
class VoiceStreamingRuntimeConfig:
    """Configuration for VoiceStreamingRuntime."""

    max_history: int = 100
    frame_config: FramePipelineConfig = field(default_factory=FramePipelineConfig)
    vad_config: VADConfig = field(default_factory=VADConfig)
    echo_capability: EchoCapability = field(default_factory=EchoCapability)
    allow_half_duplex_fallback: bool = True
    sample_rate: int = 16000
    channels: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.max_history, bool) or not isinstance(self.max_history, int) or self.max_history < 1:
            raise ValueError("max_history must be a positive integer")
        if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, int) or self.sample_rate < 1:
            raise ValueError("sample_rate must be a positive integer")
        if isinstance(self.channels, bool) or not isinstance(self.channels, int) or self.channels < 1:
            raise ValueError("channels must be a positive integer")
        if not isinstance(self.allow_half_duplex_fallback, bool):
            raise ValueError("allow_half_duplex_fallback must be boolean")


@dataclass(frozen=True)
class VoiceRuntimeEvent:
    """Immutable event emitted by VoiceStreamingRuntime."""

    stream_id: str
    event_type: str
    state: VoiceStreamState
    monotonic_ns: int
    details: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


class VoiceStreamingRuntime:
    """Production streaming-voice runtime composing frame pipeline, VAD, state machine, and AEC policy."""

    def __init__(
        self,
        stream_id: str,
        *,
        config: Optional[VoiceStreamingRuntimeConfig] = None,
        clock: Optional[Callable[[], int]] = None,
        on_event: Optional[Callable[[VoiceRuntimeEvent], None]] = None,
    ) -> None:
        self.stream_id = validate_stream_id(stream_id)
        self.config = config or VoiceStreamingRuntimeConfig()
        self._clock: Callable[[], int] = clock or (lambda: 0)
        self._on_event = on_event

        self.pipeline = AudioFramePipeline(
            self.stream_id,
            config=self.config.frame_config,
            clock=self._clock,
        )
        self.vad_engine = VADEngineState(
            self.stream_id,
            config=self.config.vad_config,
        )
        self.state_machine = VoiceStreamStateMachine(
            self.stream_id,
            max_history=self.config.max_history,
        )
        self.echo_evaluator = EchoPolicyEvaluator()

        self._echo_capability: EchoCapability = self.config.echo_capability
        self._last_monotonic_ns: int = 0
        self._runtime_events: List[VoiceRuntimeEvent] = []

    @property
    def state(self) -> VoiceStreamState:
        return self.state_machine.current_state

    @property
    def is_wake_listening(self) -> bool:
        return self.state in (VoiceStreamState.WAKE_LISTENING, VoiceStreamState.IDLE)

    @property
    def is_active_listening(self) -> bool:
        return self.state in (
            VoiceStreamState.ACTIVE_LISTENING,
            VoiceStreamState.USER_SPEAKING,
            VoiceStreamState.FINALIZING_USER_TURN,
            VoiceStreamState.THINKING,
        )

    @property
    def accumulator(self) -> StreamingTranscriptAccumulator:
        return self.state_machine.accumulator

    def now_ns(self) -> int:
        ts = self._clock()
        if ts <= self._last_monotonic_ns:
            self._last_monotonic_ns += 10
            return self._last_monotonic_ns
        self._last_monotonic_ns = ts
        return ts

    def get_history(self) -> Tuple[VoiceRuntimeEvent, ...]:
        return tuple(self._runtime_events)

    def _emit_event(self, event_type: str, details: Dict[str, Any], ts_ns: int) -> None:
        event = VoiceRuntimeEvent(
            stream_id=self.stream_id,
            event_type=event_type,
            state=self.state,
            monotonic_ns=ts_ns,
            details=details,
        )
        self._runtime_events.append(event)
        if len(self._runtime_events) > self.config.max_history:
            self._runtime_events.pop(0)
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                pass

    def start_wake_listening(self, monotonic_ns: Optional[int] = None) -> bool:
        """Initialize or transition to passive WAKE_LISTENING state."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        ok = self.state_machine.start_wake_listening(ts)
        if ok:
            self._emit_event("start_wake_listening", {}, ts)
        return ok

    def start_active_listening(self, monotonic_ns: Optional[int] = None) -> bool:
        """Arm system for ACTIVE_LISTENING command processing."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        ok = self.state_machine.start_active_listening(ts)
        if ok:
            self._emit_event("start_active_listening", {}, ts)
        return ok

    def set_echo_capability(self, capability: EchoCapability) -> None:
        """Update caller-supplied echo capabilities."""
        if isinstance(capability, EchoCapability):
            self._echo_capability = capability
            if capability.native_aec_available and capability.native_aec_verified:
                self.state_machine.set_aec_capability(AECCapability(enabled=True, available=True, is_hardware=True))
            elif capability.software_aec_available and capability.software_aec_verified:
                self.state_machine.set_aec_capability(AECCapability(enabled=True, available=True, is_hardware=False))
            else:
                self.state_machine.set_aec_capability(AECCapability(enabled=True, available=False))

    def evaluate_echo_policy(
        self,
        context: Optional[EchoPolicyContext] = None,
        monotonic_ns: Optional[int] = None,
    ) -> EchoPolicyDecision:
        """Evaluate acoustic echo policy given caller-supplied context."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        ctx = context or EchoPolicyContext(
            stream_id=self.stream_id,
            capability=self._echo_capability,
            output_playback_active=(self.state == VoiceStreamState.ASSISTANT_SPEAKING),
            input_capture_active=self.is_active_listening,
            user_speaking=(self.state == VoiceStreamState.USER_SPEAKING),
            interruption_requested=(self.state == VoiceStreamState.INTERRUPTING),
            allow_half_duplex_fallback=self.config.allow_half_duplex_fallback,
        )
        return self.echo_evaluator.evaluate(ctx, ts)

    def process_audio_frame(self, frame: AudioFrame) -> Tuple[bool, Optional[str]]:
        """Process incoming audio frame into pipeline."""
        return self.pipeline.push_frame(frame)

    def process_vad_measurement(
        self,
        measurement: VADFrameMeasurement,
        *,
        assistant_speaking: bool = False,
    ) -> Tuple[VADState, Optional[VADStateTransitionEvent]]:
        """Process caller-supplied VAD measurement."""
        vad_state, transition_event = self.vad_engine.process_measurement(
            measurement, assistant_speaking=assistant_speaking
        )

        if transition_event:
            self._emit_event(
                "vad_transition",
                {
                    "old_vad_state": transition_event.old_state.value,
                    "new_vad_state": transition_event.new_state.value,
                    "reason": transition_event.reason,
                },
                measurement.monotonic_ns,
            )

        return vad_state, transition_event

    def process_wake_event(
        self,
        wake_word: str,
        *,
        is_verified: bool,
        speaker_id: Optional[str] = None,
        monotonic_ns: Optional[int] = None,
    ) -> bool:
        """Process wake event. Fails closed if unverified or wrong state."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        event = VerifiedWakeEvent(
            stream_id=self.stream_id,
            wake_word=wake_word,
            confidence=0.95 if is_verified else 0.0,
            monotonic_ns=ts,
            speaker_id=speaker_id,
            is_verified=is_verified,
        )

        if not is_verified:
            self._emit_event("wake_unverified", {}, ts)
            return False

        ok = self.state_machine.on_wake_event(event)
        if ok:
            self._emit_event(
                "wake_activated",
                {"speaker_verified": True},
                ts,
            )
        return ok

    def process_utterance(
        self,
        text: str,
        *,
        is_verified_speaker: bool,
        monotonic_ns: Optional[int] = None,
        is_short: bool = False,
    ) -> Dict[str, Any]:
        """Daemon compatibility turn adapter for captured text utterances.

        Enforces wake-word invariant, speaker verification, goodbye invariant, and state machine transitions.
        """
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()

        # 1. PASSIVE WAKE LISTENING MODE
        if self.is_wake_listening:
            wake_cmd = extract_wake_command(text)
            if wake_cmd is None:
                # Ordinary non-wake speech while sleeping -> ignore silently
                return {"action": "ignore", "reason": "no_wake_prefix"}

            if not is_verified_speaker:
                # Unverified speaker saying wake word -> ignore silently
                self._emit_event("wake_rejected_unauthenticated", {}, ts)
                return {"action": "ignore", "reason": "unverified_speaker"}

            t1 = self.now_ns()
            wake_ok = self.state_machine.transition_to(
                VoiceStreamState.ACTIVE_LISTENING,
                event_type="wake_word_activated",
                monotonic_ns=t1,
                reason="Verified wake command matched",
            )
            if not wake_ok:
                return {"action": "ignore", "reason": "wake_transition_failed"}

            if wake_cmd:
                t2 = self.now_ns()
                self.state_machine.transition_to(
                    VoiceStreamState.USER_SPEAKING,
                    event_type="same_utterance_command",
                    monotonic_ns=t2,
                    reason="Same-utterance command present",
                )
                t3 = self.now_ns()
                self.state_machine.transition_to(
                    VoiceStreamState.FINALIZING_USER_TURN,
                    event_type="user_turn_finalized",
                    monotonic_ns=t3,
                    reason="Finalizing user turn",
                )
                t4 = self.now_ns()
                final_tx = FinalTranscript(
                    stream_id=self.stream_id,
                    text=wake_cmd,
                    start_monotonic_ns=t3,
                    end_monotonic_ns=t4,
                    role="user",
                )
                self.accumulator.add_final(final_tx)
                t5 = self.now_ns()
                self.state_machine.transition_to(
                    VoiceStreamState.THINKING,
                    event_type="assistant_thinking",
                    monotonic_ns=t5,
                    reason="Command forwarded to thinking",
                )
                self._emit_event("process_same_utterance_command", {}, t5)
                return {
                    "action": "process_command",
                    "command": wake_cmd,
                    "speaker_verified": True,
                }
            else:
                self._emit_event("wake_acknowledge", {}, t1)
                return {
                    "action": "acknowledge",
                    "response": "Yes?",
                    "speaker_verified": True,
                }

        # 2. ACTIVE COMMAND LISTENING MODE
        if self.is_active_listening:
            if not is_verified_speaker:
                # Unverified speaker in active mode -> ignore
                self._emit_event("active_command_unauthenticated", {}, ts)
                return {"action": "ignore", "reason": "unverified_speaker"}

            if is_stop_command(text):
                # GOODBYE INVARIANT:
                # Silent transition back to WAKE_LISTENING
                # Do NOT call process, speak, or log convo
                self.reset_to_wake_listening(ts)
                self._emit_event("silent_goodbye", {}, ts)
                return {"action": "silent_goodbye", "reason": "stop_command"}

            t1 = self.now_ns()
            self.state_machine.transition_to(
                VoiceStreamState.USER_SPEAKING,
                event_type="active_command_received",
                monotonic_ns=t1,
                reason="Active command received",
            )
            t2 = self.now_ns()
            self.state_machine.transition_to(
                VoiceStreamState.FINALIZING_USER_TURN,
                event_type="user_turn_finalized",
                monotonic_ns=t2,
                reason="Finalizing user turn",
            )
            t3 = self.now_ns()
            final_tx = FinalTranscript(
                stream_id=self.stream_id,
                text=text,
                start_monotonic_ns=t1,
                end_monotonic_ns=t3,
                role="user",
            )
            self.accumulator.add_final(final_tx)
            t4 = self.now_ns()
            self.state_machine.transition_to(
                VoiceStreamState.THINKING,
                event_type="assistant_thinking",
                monotonic_ns=t4,
                reason="Active command forwarded to thinking",
            )
            self._emit_event("process_active_command", {}, t4)
            return {
                "action": "process_command",
                "command": text,
                "speaker_verified": True,
            }

        return {"action": "ignore", "reason": "invalid_state"}

    def assistant_speaking_start(self, monotonic_ns: Optional[int] = None) -> bool:
        """Mark assistant playback start."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        if self.state != VoiceStreamState.THINKING:
            t1 = self.now_ns()
            self.state_machine.transition_to(
                VoiceStreamState.THINKING,
                event_type="assistant_thinking",
                monotonic_ns=t1,
                reason="Preparing to speak",
            )
        t2 = self.now_ns()
        return self.state_machine.assistant_speaking_start(t2)

    def add_assistant_segment(self, text: str, monotonic_ns: Optional[int] = None) -> bool:
        """Add assistant response segment."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        final_tx = FinalTranscript(
            stream_id=self.stream_id,
            text=text,
            start_monotonic_ns=ts,
            end_monotonic_ns=ts + 10,
            role="assistant",
        )
        return self.state_machine.add_assistant_segment(final_tx)

    def request_interruption(
        self,
        request_id: str,
        *,
        is_authenticated: bool,
        speaker_id: Optional[str] = None,
        monotonic_ns: Optional[int] = None,
    ) -> bool:
        """Request barge-in during assistant playback. Fails closed if unauthenticated."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        req = InterruptionRequest(
            stream_id=self.stream_id,
            request_id=request_id,
            monotonic_ns=ts,
            is_authenticated=is_authenticated,
            speaker_id=speaker_id,
        )
        ok = self.state_machine.request_interruption(req)
        if ok:
            self._emit_event(
                "interruption_requested",
                {"request_id": request_id, "speaker_id": speaker_id},
                ts,
            )
        return ok

    def confirm_interruption(
        self,
        request_id: str,
        *,
        is_confirmed: bool = True,
        bytes_played_before_stop: int = 0,
        monotonic_ns: Optional[int] = None,
    ) -> bool:
        """Confirm physical playback termination following interruption."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        conf = InterruptionConfirmation(
            stream_id=self.stream_id,
            request_id=request_id,
            monotonic_ns=ts,
            is_confirmed=is_confirmed,
            bytes_played_before_stop=bytes_played_before_stop,
        )
        ok = self.state_machine.confirm_interruption(conf)
        if ok and is_confirmed:
            self._emit_event(
                "interruption_confirmed",
                {"request_id": request_id},
                ts,
            )
        return ok

    def reset_to_wake_listening(self, monotonic_ns: Optional[int] = None) -> bool:
        """Reset active state and return to passive WAKE_LISTENING."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        self.accumulator.reset()
        self.vad_engine.reset()
        return self.state_machine.transition_to(
            VoiceStreamState.WAKE_LISTENING,
            event_type="reset_to_wake_listening",
            monotonic_ns=ts,
            reason="Reset to passive wake listening",
        )

    def reset(self, monotonic_ns: Optional[int] = None) -> None:
        """Reset runtime state."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        self.pipeline.reset()
        self.vad_engine.reset()
        self.state_machine.reset(ts)
        self._runtime_events.clear()

    def close(self) -> None:
        """Close runtime pipeline and state machine."""
        self.pipeline.close()
        self.vad_engine.close()
        self.reset_to_wake_listening()

    def get_accessibility_state(self) -> AccessibilityState:
        return self.state_machine.get_accessibility_state()

    def get_metrics(self) -> Dict[str, Any]:
        p_metrics = self.pipeline.get_metrics()
        return {
            "stream_id": self.stream_id,
            "state": self.state.value,
            "pipeline_metrics": {
                "received": p_metrics.frames_received,
                "processed": p_metrics.frames_processed,
                "dropped_overflow": p_metrics.frames_dropped_overflow,
                "dropped_out_of_order": p_metrics.frames_dropped_out_of_order,
                "discontinuities": p_metrics.discontinuities_detected,
            },
            "vad_state": self.vad_engine.current_state.value,
            "history_count": len(self._runtime_events),
        }
