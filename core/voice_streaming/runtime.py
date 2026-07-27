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
    AudioFrameMetadata,
    AudioFramePipeline,
    FramePipelineConfig,
    FramePipelineMetrics,
)


def _pcm_peak_probability(pcm: bytes, sample_width: int) -> float:
    """Return normalized signed little-endian PCM peak without exposing content."""
    if not pcm or sample_width not in (1, 2, 3, 4) or len(pcm) % sample_width:
        return 0.0
    peak = 0
    for offset in range(0, len(pcm), sample_width):
        sample = int.from_bytes(
            pcm[offset : offset + sample_width],
            byteorder="little",
            signed=True,
        )
        peak = max(peak, abs(sample))
    return min(1.0, peak / float(1 << (sample_width * 8 - 1)))


from core.voice_streaming.live_audio import (
    AudioInputCapability,
    LiveAudioFrame,
    VoiceAudioLoop,
)
from core.voice_streaming.aec_evidence import (
    AecEvidenceGate,
    PlatformAecEvidence,
)
from core.voice_streaming.interruption_evidence import InterruptionEvidence
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
    "hikari stop",
    "hikari done",
    "stop hikari",
    "be quiet",
    "stop listening",
}

# Soft interrupt during playback only — stays in active listening (not wake sleep).
SOFT_INTERRUPT_WORDS = {
    "cancel",
}

# Exact phrases that interrupt assistant speech. Stop/goodbye return to wake sleep;
# cancel remains a soft interrupt that stays active.
SPEECH_INTERRUPT_WORDS = STOP_WORDS | SOFT_INTERRUPT_WORDS | {
    "goodbye",
    "good bye",
    "go to sleep",
    "stop listening",
    "be quiet",
    "hikari stop",
    "hikari done",
    "stop hikari",
    "stop",
    "cancel",
}

GOODBYE_INTERRUPT_WORDS = {
    "stop",
    "be quiet",
    "hikari stop",
    "hikari done",
    "hickory stop",
    "hickory done",
    "stop hikari",
    "stop hickory",
    "goodbye",
    "good bye",
    "go to sleep",
    "stop listening",
    "bye",
    "exit",
    "sleep",
    "that's all",
    "that's it",
    "nothing else",
    # Bare "done" stays an active-listen sleep word only (too easy to false-trigger).
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

    The reviewed local Whisper spelling "Hickory" is accepted as the same
    anchored wake prefix. Speaker verification still runs before activation.
    """
    if not isinstance(text, str):
        return None
    match = re.fullmatch(
        r"\s*(?:(?:(?:hey|okay|hi)[\s,]+)?(?:hikari|hickory)|"
        r"(?:hey|okay|hi)[\s,]+(?:kari|carrie|carry))\b[\s,.:;!?-]*(.*?)\s*",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is not None:
        return match.group(1).strip()
    return None


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
    """Classify only explicit stop commands during playback (exact phrase)."""
    return speech_interrupt_mode(text) is not None


def _normalize_interrupt_phrase(text: str) -> str:
    """Normalize interrupt text; map reviewed Whisper wake misspellings to hikari."""
    normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())
    # Keep wake spelling aligned with extract_wake_command acceptance.
    tokens = [
        "hikari" if tok == "hickory" else tok
        for tok in normalized.split()
    ]
    return " ".join(tokens)


def speech_interrupt_mode(text: str) -> str | None:
    """Return interrupt mode for an exact playback interrupt phrase.

    Stop and goodbye phrases return ``goodbye`` (wake-only sleep).
    ``cancel`` remains a soft interrupt that stays in active listening.
    Wake-prefixed forms ("hikari stop", "hey hickory stop") resolve via the
    trailing command after the accepted wake prefix.
    """
    if not isinstance(text, str):
        return None
    normalized = _normalize_interrupt_phrase(text)
    if normalized == "cancel":
        return "cancel"
    if normalized in GOODBYE_INTERRUPT_WORDS:
        return "goodbye"
    # Exact wake + interrupt: "hey hikari stop" -> trailing "stop"
    trailing = extract_wake_command(text)
    if trailing:
        trailing_norm = _normalize_interrupt_phrase(trailing)
        if trailing_norm == "cancel":
            return "cancel"
        if trailing_norm in GOODBYE_INTERRUPT_WORDS:
            return "goodbye"
    return None


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
        from core.streaming_voice.aec import AecNegotiator, AecStatus
        self._aec_negotiator = AecNegotiator()
        self._seen_utterance_keys: set[str] = set()
        self._max_utterance_keys = max(16, min(self.config.max_history, 256))
        self._active_response_id: Optional[str] = None
        self._active_utterance_id: Optional[str] = None
        self._cancelled = False
        self._audio_loop: Optional[VoiceAudioLoop] = None
        self._aec_gate = AecEvidenceGate(stream_id=self.stream_id, device_id="default")
        self._device_id = "default"
        self._last_vad_speech_ns: int = 0  # command-path only; never barge-in proof
        self._barge_speech_ns: int = 0  # speech observed during assistant playback
        self._playback_started_ns: int = 0
        self._seen_interruption_ids: set[str] = set()
        self._max_interruption_ids = 64
        self._input_capability = AudioInputCapability.UTTERANCE_ONLY
        # Honest default: no verified AEC until platform reports evidence.
        self._aec_negotiator.report(AecStatus.UNAVAILABLE)

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
        utterance_id: Optional[str] = None,
        monotonic_ns: Optional[int] = None,
        is_short: bool = False,
    ) -> Dict[str, Any]:
        """Daemon compatibility turn adapter for captured text utterances.

        Enforces wake-word invariant, speaker verification, goodbye invariant, and
        state-machine transitions. ``utterance_id`` is an optional transport
        correlation identifier; replay protection is applied only when it is
        supplied, because identical words in later turns are valid speech.
        """
        if utterance_id is not None:
            if (
                not isinstance(utterance_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", utterance_id)
            ):
                return {"action": "ignore", "reason": "invalid_utterance_id"}
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()

        if self.state == VoiceStreamState.ASSISTANT_SPEAKING:
            # Assistant playback cannot become ordinary user speech.
            return {"action": "ignore", "reason": "assistant_playback_active"}

        if self.state in (VoiceStreamState.INTERRUPTING, VoiceStreamState.INTERRUPTED):
            return {"action": "ignore", "reason": "interruption_in_progress"}

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
            # Command-path speech must not authorize later barge-in.
            self._last_vad_speech_ns = t1

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
                if utterance_id is not None and not self._remember_utterance_key(
                    f"utterance:{utterance_id}"
                ):
                    self.reset_to_wake_listening(self.now_ns())
                    return {"action": "ignore", "reason": "duplicate_utterance"}
                self._emit_event("process_same_utterance_command", {}, t5)
                if utterance_id is not None:
                    self._active_utterance_id = utterance_id
                command = wake_cmd
                self.clear_turn_transcript()
                return {
                    "action": "process_command",
                    "command": command,
                    "speaker_verified": True,
                    "utterance_id": utterance_id,
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
            self._last_vad_speech_ns = t1
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
            if utterance_id is not None and not self._remember_utterance_key(
                f"utterance:{utterance_id}"
            ):
                return {"action": "ignore", "reason": "duplicate_utterance"}
            self._emit_event("process_active_command", {}, t4)
            if utterance_id is not None:
                self._active_utterance_id = utterance_id
            command = text
            self.clear_turn_transcript()
            return {
                "action": "process_command",
                "command": command,
                "speaker_verified": True,
                "utterance_id": utterance_id,
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
        ok = self.state_machine.assistant_speaking_start(t2)
        if ok:
            self._playback_started_ns = t2
            self._barge_speech_ns = 0
        return ok

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

    def note_barge_speech(self, speech_observed_ns: int) -> None:
        """Refresh barge-in speech marker from a verified playback utterance.

        Frame-stream barge endpointing may finalize after a short hangover of
        silence; keep the speech marker fresh enough for the evidence gate when
        the caller already transcribed an explicit interrupt phrase.
        """
        try:
            ts = int(speech_observed_ns)
        except (TypeError, ValueError):
            return
        if ts <= 0:
            return
        if self._playback_started_ns > 0 and ts > self._playback_started_ns:
            self._barge_speech_ns = max(self._barge_speech_ns, ts)

    def request_interruption(
        self,
        request_id: str = "",
        *,
        evidence: Optional[InterruptionEvidence] = None,
        is_authenticated: Optional[bool] = None,
        speaker_id: Optional[str] = None,
        monotonic_ns: Optional[int] = None,
    ) -> bool:
        """Request barge-in during assistant playback.

        Caller-supplied ``is_authenticated`` / ``speaker_id`` are never trusted.
        Authorization requires a verified ``InterruptionEvidence`` object.
        """
        del is_authenticated, speaker_id  # intentionally ignored
        now = self.now_ns()
        if evidence is None:
            self._emit_event("interruption_denied", {"reason": "missing_evidence"}, now)
            return False
        if not isinstance(evidence, InterruptionEvidence):
            self._emit_event("interruption_denied", {"reason": "invalid_evidence"}, now)
            return False

        # Prefer evidence timestamps; optional monotonic_ns must not widen trust.
        ts = evidence.observed_at_ns
        if monotonic_ns is not None:
            try:
                mono = validate_monotonic_ns(monotonic_ns)
            except Exception:
                self._emit_event("interruption_denied", {"reason": "invalid_timestamp"}, now)
                return False
            if mono != evidence.observed_at_ns:
                self._emit_event("interruption_denied", {"reason": "timestamp_mismatch"}, now)
                return False

        if request_id and request_id != evidence.interruption_id:
            self._emit_event("interruption_denied", {"reason": "request_mismatch"}, now)
            return False

        if self.state != VoiceStreamState.ASSISTANT_SPEAKING:
            reason = "playback_not_active"
            if self.state == VoiceStreamState.WAKE_LISTENING:
                reason = "sleeping"
            self._emit_event("interruption_denied", {"reason": reason}, now)
            return False

        if self._playback_started_ns <= 0:
            self._emit_event("interruption_denied", {"reason": "playback_not_active"}, now)
            return False

        if evidence.stream_id != self.stream_id:
            self._emit_event("interruption_denied", {"reason": "cross_stream"}, now)
            return False

        if not evidence.speaker_verified:
            self._emit_event("interruption_denied", {"reason": "unverified_speaker"}, now)
            return False

        if evidence.observed_at_ns > now + 1_000_000_000:
            self._emit_event("interruption_denied", {"reason": "future_evidence"}, now)
            return False
        if evidence.expires_at_ns < now:
            self._emit_event("interruption_denied", {"reason": "stale_evidence"}, now)
            return False
        if evidence.speech_observed_ns <= self._playback_started_ns:
            # Prior command speech (even within 2s) cannot authorize barge-in.
            self._emit_event("interruption_denied", {"reason": "speech_before_playback"}, now)
            return False
        if now - evidence.speech_observed_ns > 2_000_000_000:
            self._emit_event("interruption_denied", {"reason": "stale_speech"}, now)
            return False

        target = evidence.target_assistant_utterance_id
        allowed_targets: set[str] = set()
        if self._active_utterance_id is not None:
            allowed_targets.add(self._active_utterance_id)
        if self._active_response_id is not None:
            allowed_targets.add(self._active_response_id)
        if not allowed_targets:
            allowed_targets = {self.stream_id, "assistant_playback"}
        if target not in allowed_targets:
            self._emit_event("interruption_denied", {"reason": "wrong_utterance"}, now)
            return False

        if evidence.interruption_id in self._seen_interruption_ids:
            self._emit_event("interruption_denied", {"reason": "duplicate_interruption"}, now)
            return False

        if self._input_capability == AudioInputCapability.FRAME_STREAM:
            # Frame mode requires live barge VAD after playback began.
            if (
                self._barge_speech_ns <= self._playback_started_ns
                or (now - self._barge_speech_ns) > 2_000_000_000
            ):
                self._emit_event("interruption_denied", {"reason": "vad_stale"}, now)
                return False
        # Utterance-only: evidence object is the speech proof; do not invent frames.

        if len(self._seen_interruption_ids) >= self._max_interruption_ids:
            self._emit_event("interruption_denied", {"reason": "bound_exceeded"}, now)
            return False

        req = InterruptionRequest(
            stream_id=self.stream_id,
            request_id=evidence.interruption_id,
            monotonic_ns=ts,
            is_authenticated=True,  # derived only after evidence gates pass
            speaker_id=None,
        )
        ok = self.state_machine.request_interruption(req)
        if ok:
            self._seen_interruption_ids.add(evidence.interruption_id)
            self._emit_event(
                "interruption_requested",
                {"accepted": True, "source": evidence.verification_source.value},
                ts,
            )
        else:
            self._emit_event("interruption_denied", {"reason": "state_reject"}, now)
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
                {"accepted": True, "confirmed": True},
                ts,
            )
        return ok

    def reset_to_wake_listening(self, monotonic_ns: Optional[int] = None) -> bool:
        """Reset active state and return to passive WAKE_LISTENING."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        self.accumulator.reset()
        self.vad_engine.reset()
        self.state_machine.active_interruption_request = None
        self._active_response_id = None
        self._active_utterance_id = None
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
        self._seen_utterance_keys.clear()
        self._active_response_id = None
        self._active_utterance_id = None
        self._cancelled = False
        self._last_vad_speech_ns = 0
        self._barge_speech_ns = 0
        self._playback_started_ns = 0
        self._seen_interruption_ids.clear()
        self._aec_gate.clear()
        if self._audio_loop is not None:
            try:
                self._audio_loop.close()
            except Exception:
                pass
            self._audio_loop = None
            self.set_input_capability(AudioInputCapability.UTTERANCE_ONLY, frame_loop_open=False)

    def close(self) -> None:
        """Close runtime pipeline and state machine."""
        self.pipeline.close()
        self.vad_engine.close()
        self.reset_to_wake_listening()


    def attach_audio_loop(self, loop: VoiceAudioLoop) -> None:
        """Attach an injected live-audio loop. Does not open the microphone."""
        if not isinstance(loop, VoiceAudioLoop):
            raise TypeError("loop_must_be_VoiceAudioLoop")
        if loop.stream_id != self.stream_id:
            raise ValueError("cross_stream_audio_loop")
        self._audio_loop = loop
        snap = loop.snapshot()
        frame_open = (
            snap.open
            and not snap.closed
            and not snap.cancelled
            and loop.capability == AudioInputCapability.FRAME_STREAM
        )
        self.set_input_capability(
            AudioInputCapability.FRAME_STREAM if frame_open else AudioInputCapability.UTTERANCE_ONLY,
            frame_loop_open=frame_open,
        )

    def set_input_capability(
        self,
        capability: AudioInputCapability,
        *,
        frame_loop_open: bool = False,
    ) -> Dict[str, Any]:
        """Public capability update. FRAME_STREAM requires an opened frame loop."""
        if not isinstance(capability, AudioInputCapability):
            raise TypeError("invalid_capability")
        if not isinstance(frame_loop_open, bool):
            raise TypeError("invalid_frame_loop_open")
        reason = "ok"
        applied = capability
        if capability == AudioInputCapability.FRAME_STREAM and not frame_loop_open:
            applied = AudioInputCapability.UTTERANCE_ONLY
            reason = "frame_stream_requires_open_loop"
        prev = self._input_capability
        self._input_capability = applied
        if applied != AudioInputCapability.FRAME_STREAM and prev == AudioInputCapability.FRAME_STREAM:
            # Downgrade clears incompatible AEC/full-duplex claims.
            self.mark_aec_lost()
        self._emit_event(
            "input_capability",
            {
                "capability": applied.value,
                "frame_loop_open": bool(
                    frame_loop_open and applied == AudioInputCapability.FRAME_STREAM
                ),
                "reason": reason,
            },
            self.now_ns(),
        )
        return {
            "accepted": reason == "ok",
            "capability": applied.value,
            "reason": reason,
        }

    @property
    def input_capability(self) -> AudioInputCapability:
        return self._input_capability

    def interruption_target_id(self) -> str:
        """Stable correlation target for interruption evidence binding."""
        if self._active_utterance_id is not None:
            return self._active_utterance_id
        if self._active_response_id is not None:
            return self._active_response_id
        return "assistant_playback"

    def _validate_playback_correlation_id(self, value: object, *, name: str) -> Optional[str]:
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value)
        ):
            return None
        return value

    def bind_assistant_playback(self, utterance_id: str, response_id: str) -> bool:
        """Bind assistant utterance/response correlation for interruption targeting.

        Allowed only in THINKING or ASSISTANT_SPEAKING. Does not change wake/sleep
        authority. Identical re-bind is idempotent; conflicting bind fails closed.
        """
        now = self.now_ns()
        uid = self._validate_playback_correlation_id(utterance_id, name="utterance_id")
        rid = self._validate_playback_correlation_id(response_id, name="response_id")
        if uid is None or rid is None:
            self._emit_event(
                "assistant_playback_bind",
                {"accepted": False, "reason": "invalid_id"},
                now,
            )
            return False
        if self.state not in (
            VoiceStreamState.THINKING,
            VoiceStreamState.ASSISTANT_SPEAKING,
        ):
            self._emit_event(
                "assistant_playback_bind",
                {"accepted": False, "reason": "invalid_state"},
                now,
            )
            return False
        if self._active_utterance_id is not None or self._active_response_id is not None:
            if (
                self._active_utterance_id == uid
                and self._active_response_id == rid
            ):
                self._emit_event(
                    "assistant_playback_bind",
                    {"accepted": True, "reason": "idempotent"},
                    now,
                )
                return True
            self._emit_event(
                "assistant_playback_bind",
                {"accepted": False, "reason": "conflict"},
                now,
            )
            return False
        self._active_utterance_id = uid
        self._active_response_id = rid
        self._emit_event(
            "assistant_playback_bind",
            {"accepted": True, "reason": "ok"},
            now,
        )
        return True

    def clear_assistant_playback(self, *, expected_response_id: Optional[str] = None) -> bool:
        """Clear assistant playback correlation for an exact response match.

        Canonical cancel/reset paths clear internally. Callers must supply the
        expected response id; mismatched or missing expected ids fail closed.
        """
        now = self.now_ns()
        if self._active_utterance_id is None and self._active_response_id is None:
            self._emit_event(
                "assistant_playback_clear",
                {"cleared": False, "reason": "already_clear"},
                now,
            )
            return True
        if expected_response_id is None:
            self._emit_event(
                "assistant_playback_clear",
                {"cleared": False, "reason": "missing_expected"},
                now,
            )
            return False
        rid = self._validate_playback_correlation_id(
            expected_response_id, name="expected_response_id"
        )
        if rid is None:
            self._emit_event(
                "assistant_playback_clear",
                {"cleared": False, "reason": "invalid_id"},
                now,
            )
            return False
        if rid != self._active_response_id:
            self._emit_event(
                "assistant_playback_clear",
                {"cleared": False, "reason": "stale_or_cross_response"},
                now,
            )
            return False
        self._active_utterance_id = None
        self._active_response_id = None
        self._emit_event(
            "assistant_playback_clear",
            {"cleared": True, "reason": "ok"},
            now,
        )
        return True

    def set_device_id(self, device_id: str) -> None:
        if not isinstance(device_id, str) or not device_id or len(device_id) > 128:
            raise ValueError("invalid_device_id")
        self._device_id = device_id
        self._aec_gate = AecEvidenceGate(stream_id=self.stream_id, device_id=device_id)

    def ingest_live_frame(self, frame: LiveAudioFrame) -> Dict[str, Any]:
        """Feed one live frame into pipeline + VAD. Never calls orchestrator."""
        if not isinstance(frame, LiveAudioFrame):
            return {"accepted": False, "reason": "invalid_frame"}
        if frame.stream_id != self.stream_id:
            return {"accepted": False, "reason": "cross_stream"}
        if self.state == VoiceStreamState.ASSISTANT_SPEAKING:
            # Observe barge-in speech only; never orchestrate during playback.
            speech_p = _pcm_peak_probability(frame.pcm, frame.sample_width)
            barge_observed = speech_p >= 0.35
            if barge_observed and frame.monotonic_ns > self._playback_started_ns:
                self._barge_speech_ns = frame.monotonic_ns
            self._emit_event(
                "live_frame_playback_observe",
                {"barge_observed": barge_observed, "can_orchestrate": False},
                frame.monotonic_ns,
            )
            return {
                "accepted": False,
                "reason": "assistant_playback_active",
                "barge_observed": barge_observed,
                "can_orchestrate": False,
            }

        meta = AudioFrameMetadata(
            stream_id=frame.stream_id,
            sequence_id=frame.sequence,
            monotonic_ns=frame.monotonic_ns,
            sample_rate=frame.sample_rate,
            channels=frame.channels,
            sample_width=frame.sample_width,
            duration_ms=max(
                1.0,
                (len(frame.pcm) / float(frame.sample_rate * frame.channels * frame.sample_width))
                * 1000.0,
            ),
            payload_bytes=len(frame.pcm),
        )
        audio_frame = AudioFrame(meta, frame.pcm)
        ok, reason = self.pipeline.push_frame(audio_frame)
        if not ok:
            return {"accepted": False, "reason": reason or "pipeline_reject"}

        speech_p = _pcm_peak_probability(frame.pcm, frame.sample_width)
        measurement = VADFrameMeasurement(
            sequence_id=frame.sequence,
            monotonic_ns=frame.monotonic_ns,
            speech_probability=speech_p,
            energy_db=-60.0 + (speech_p * 40.0),
            frame_duration_ms=meta.duration_ms,
        )
        vad_state, transition = self.process_vad_measurement(
            measurement,
            assistant_speaking=(self.state == VoiceStreamState.ASSISTANT_SPEAKING),
        )
        if vad_state.value in {"confirmed_speech", "possible_speech", "interruption_candidate"}:
            self._last_vad_speech_ns = frame.monotonic_ns
        self._emit_event(
            "live_frame_ingested",
            {
                "sequence": frame.sequence,
                "vad_state": vad_state.value,
                "transition": transition.new_state.value if transition else None,
            },
            frame.monotonic_ns,
        )
        return {
            "accepted": True,
            "reason": "ok",
            "vad_state": vad_state.value,
            "wake_listening": self.is_wake_listening,
            "can_orchestrate": False,
        }

    def submit_platform_aec_evidence(self, evidence: PlatformAecEvidence) -> Dict[str, Any]:
        now = self.now_ns()
        decision = self._aec_gate.accept(evidence, now_ns=now)
        if not decision.accepted:
            self.set_echo_capability(EchoCapability())
            from core.streaming_voice.aec import AecStatus
            self._aec_negotiator.report(AecStatus.UNAVAILABLE)
            return {"accepted": False, "reason": decision.reason, "full_duplex": False}
        self.set_echo_capability(evidence.to_echo_capability())
        from core.streaming_voice.aec import AecStatus
        if evidence.supports_full_duplex:
            self._aec_negotiator.report(AecStatus.AVAILABLE, vendor_label="platform")
            self._aec_negotiator.negotiate()
        else:
            self._aec_negotiator.report(AecStatus.UNAVAILABLE)
        return {
            "accepted": True,
            "reason": "ok",
            "full_duplex": decision.full_duplex and self.duplex_mode() == "full_duplex",
        }

    def mark_aec_lost(self) -> None:
        """AEC loss during playback immediately returns to half duplex."""
        self._aec_gate.mark_lost()
        self.set_echo_capability(EchoCapability())
        from core.streaming_voice.aec import AecStatus
        self._aec_negotiator.report(AecStatus.UNAVAILABLE)
        self._emit_event("aec_lost", {}, self.now_ns())

    def clear_turn_transcript(self) -> None:
        self.accumulator.reset()

    def allows_orchestrator_process(self) -> bool:
        """True only when the canonical runtime is in an active command path."""
        if self._cancelled:
            return False
        return self.state in (
            VoiceStreamState.ACTIVE_LISTENING,
            VoiceStreamState.USER_SPEAKING,
            VoiceStreamState.FINALIZING_USER_TURN,
            VoiceStreamState.THINKING,
            VoiceStreamState.ASSISTANT_SPEAKING,
            VoiceStreamState.INTERRUPTING,
            VoiceStreamState.INTERRUPTED,
        )

    def duplex_mode(self) -> str:
        """Honest duplex mode from echo policy + bounded AEC negotiator."""
        from core.streaming_voice.contracts import DuplexMode as StreamingDuplexMode
        decision = self.evaluate_echo_policy()
        aec = self._aec_negotiator.capability
        gate_ok = bool(self._aec_gate.current and self._aec_gate.current.supports_full_duplex)
        if decision.full_duplex_safe and aec.echo_cancellation_active and gate_ok:
            return StreamingDuplexMode.FULL_DUPLEX.value
        return StreamingDuplexMode.HALF_DUPLEX.value

    def echo_cancellation_active(self) -> bool:
        """Never true without verified negotiated AEC evidence."""
        return self._aec_negotiator.capability.echo_cancellation_active

    def report_aec_status(self, status, *, vendor_label: str = "none") -> None:
        self._aec_negotiator.report(status, vendor_label=vendor_label)
        negotiated = self._aec_negotiator.negotiate()
        if negotiated.accepted:
            self.set_echo_capability(
                EchoCapability(native_aec_available=True, native_aec_verified=True)
            )
        else:
            self.set_echo_capability(EchoCapability())

    def _remember_utterance_key(self, key: str) -> bool:
        if key in self._seen_utterance_keys:
            return False
        if len(self._seen_utterance_keys) >= self._max_utterance_keys:
            # Deterministic eviction of an arbitrary oldest-inserted key is not
            # available on set; clear and fail closed for this insert.
            self._seen_utterance_keys.clear()
            return False
        self._seen_utterance_keys.add(key)
        return True

    def cancel_active(self, monotonic_ns: Optional[int] = None) -> bool:
        """Cancel listening/thinking/speaking/draining and clear volatile state."""
        ts = monotonic_ns if monotonic_ns is not None else self.now_ns()
        self._cancelled = True
        self._active_response_id = None
        self._active_utterance_id = None
        self.accumulator.reset()
        self.vad_engine.reset()
        self.state_machine.active_interruption_request = None
        self._last_vad_speech_ns = 0
        self._barge_speech_ns = 0
        self._playback_started_ns = 0
        if self._audio_loop is not None:
            try:
                self._audio_loop.cancel()
            except Exception:
                pass
        ok = self.state_machine.transition_to(
            VoiceStreamState.WAKE_LISTENING,
            event_type="cancel_active",
            monotonic_ns=ts,
            reason="Cancellation cleared active voice state",
        )
        if not ok:
            self.state_machine.reset(ts)
            ok = self.state_machine.start_wake_listening(self.now_ns())
        self._cancelled = False
        self._emit_event("cancel_active", {}, ts)
        return ok

    def content_free_summary(self) -> Dict[str, Any]:
        """State summary without transcript text or audio bytes."""
        return {
            "stream_id": self.stream_id,
            "state": self.state.value,
            "wake_listening": self.is_wake_listening,
            "active_listening": self.is_active_listening,
            "duplex_mode": self.duplex_mode(),
            "echo_cancellation_active": self.echo_cancellation_active(),
            "history_count": len(self._runtime_events),
            "vad_state": self.vad_engine.current_state.value,
            "input_capability": self._input_capability.value,
        }

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
