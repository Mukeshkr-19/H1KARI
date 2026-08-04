"""Prospective single-authority VoiceSessionCoordinator.

Coordinates speech capture, VAD, transcription, owner verification, echo/noise rejection,
LLM generation, sentence-bounded TTS rendering/playback, barge-in, and AEC policy through
explicit injected adapters. Production daemon activation remains intentionally opt-in.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import AsyncIterable, Callable, List, Optional, Set

from core.voice_session.aec_policy import AecPolicy, AecPolicyDecision
from core.voice_session.cancellation import (
    CancellationTracker,
    InterruptionConfirmation,
    InterruptionRequest,
)
from core.voice_session.contracts import (
    AudioFrame,
    EchoNoiseRejectorProtocol,
    EchoNoiseResult,
    FrameSourceProtocol,
    GenerationStreamProtocol,
    MonotonicClockProtocol,
    OwnerVerificationResult,
    OwnerVerifierProtocol,
    PlaybackControllerProtocol,
    ResumePolicyProtocol,
    SessionContext,
    StateEventSinkProtocol,
    TTSRendererProtocol,
    TranscriberProtocol,
    TurnSinkProtocol,
    VADSourceProtocol,
    validate_capture_duration_bound,
    validate_capture_frame_bound,
    validate_generation,
    validate_monotonic_ns,
    validate_playback_id,
    validate_response_id,
    validate_sequence,
    validate_session_id,
    validate_utterance_id,
)
from core.voice_session.events import (
    BargeInEvent,
    DegradedStateEvent,
    PlaybackEvent,
    StateChangeEvent,
    VoiceSessionEvent,
)
from core.voice_session.transcript_pipeline import (
    FinalTranscript,
    PartialTranscript,
    TranscriptPipeline,
)
from core.voice_session.tts_pipeline import TTSChunk, TTSPipeline
from core.voice_streaming.aec_evidence import PlatformAecEvidence

_MAX_AUDIO_BYTE_LEN = 10_000_000


class VoiceSessionState(str, Enum):
    IDLE = "idle"
    SLEEPING = "sleeping"
    WAKE_DETECTED = "wake_detected"
    AWAITING_COMMAND = "awaiting_command"
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    PROCESSING_FINAL = "processing_final"
    ASSISTANT_GENERATING = "assistant_generating"
    ASSISTANT_SPEAKING = "assistant_speaking"
    BARGE_IN_PENDING = "barge_in_pending"
    CANCELLING_ASSISTANT = "cancelling_assistant"
    INTERRUPTED = "interrupted"
    DEGRADED_HALF_DUPLEX = "degraded_half_duplex"
    ERROR = "error"
    STOPPED = "stopped"


class DefaultMonotonicClock:
    def now_ns(self) -> int:
        return time.monotonic_ns()


class DefaultResumePolicy:
    def should_resume(self, previous_response_id: str, rejected_reason: str) -> bool:
        return False


class SerializedEventEmitter:
    """Thread-safe, atomic, serialized event sequence allocator and delivery boundary."""

    def __init__(self, event_sink: Optional[StateEventSinkProtocol] = None) -> None:
        self._event_sink = event_sink
        self._async_lock = asyncio.Lock()
        self._seq = 0
        self._latched_failed = False

    @property
    def current_seq(self) -> int:
        return self._seq

    def reset(self) -> None:
        self._seq = 0
        self._latched_failed = False

    async def emit(
        self, event_factory: Callable[[int], VoiceSessionEvent]
    ) -> Optional[VoiceSessionEvent]:
        async with self._async_lock:
            if self._latched_failed:
                return None
            candidate_seq = self._seq + 1
            event = event_factory(candidate_seq)
            if self._event_sink is not None:
                try:
                    await self._event_sink.emit_event(event)
                except Exception:
                    # Latch failed state so sequence N+1 is never delivered after N failed
                    self._latched_failed = True
                    return None
            self._seq = candidate_seq
            return event


@dataclass(frozen=True)
class ActiveResponseTuple:
    """Authoritative tuple binding an active response turn."""

    session_id: str
    utterance_id: str
    response_id: str
    playback_id: str
    cancellation_generation: int


class VoiceSessionCoordinator:
    """Authoritative, injected realtime voice session coordinator foundation."""

    def __init__(
        self,
        *,
        session_id: str = "default_session",
        frame_source: Optional[FrameSourceProtocol] = None,
        vad_source: Optional[VADSourceProtocol] = None,
        transcriber: Optional[TranscriberProtocol] = None,
        owner_verifier: Optional[OwnerVerifierProtocol] = None,
        echo_noise_rejector: Optional[EchoNoiseRejectorProtocol] = None,
        generator: Optional[GenerationStreamProtocol] = None,
        tts_renderer: Optional[TTSRendererProtocol] = None,
        playback_controller: Optional[PlaybackControllerProtocol] = None,
        clock: Optional[MonotonicClockProtocol] = None,
        event_sink: Optional[StateEventSinkProtocol] = None,
        turn_sink: Optional[TurnSinkProtocol] = None,
        resume_policy: Optional[ResumePolicyProtocol] = None,
        aec_policy: Optional[AecPolicy] = None,
        max_capture_frames: int = 50,
        max_capture_duration_s: float = 2.0,
    ) -> None:
        self._session_id = validate_session_id(session_id)
        self._frame_source = frame_source
        self._vad_source = vad_source
        self._transcriber = transcriber
        self._owner_verifier = owner_verifier
        self._echo_noise_rejector = echo_noise_rejector
        self._generator = generator
        self._tts_renderer = tts_renderer
        self._playback_controller = playback_controller
        self._clock = clock or DefaultMonotonicClock()
        self._event_sink = event_sink
        self._turn_sink = turn_sink
        self._resume_policy = resume_policy or DefaultResumePolicy()
        self._aec_policy = aec_policy or AecPolicy()
        self._max_capture_frames = validate_capture_frame_bound(max_capture_frames)
        self._max_capture_duration_s = validate_capture_duration_bound(max_capture_duration_s)

        self._emitter = SerializedEventEmitter(event_sink=self._event_sink)
        self._cancellation_tracker = CancellationTracker()
        self._transcript_pipeline = TranscriptPipeline(
            clock=self._clock,
            cancellation_tracker=self._cancellation_tracker,
            turn_sink=self._turn_sink,
            event_sink=self._event_sink,
            owner_verifier=self._owner_verifier,
            echo_noise_rejector=self._echo_noise_rejector,
            emitter_fn=self._emitter.emit,
        )
        self._tts_pipeline = TTSPipeline(
            cancellation_tracker=self._cancellation_tracker,
            renderer=self._tts_renderer,
        )

        self._state = VoiceSessionState.IDLE
        self._utt_counter = 0
        self._resp_counter = 0
        self._pb_counter = 0

        self._utterance_id = "utt_0"
        self._response_id = "resp_0"
        self._playback_id = "pb_0"

        self._active_response: Optional[ActiveResponseTuple] = None
        self._response_tasks: Set[asyncio.Task] = set()

        self._lock = threading.Lock()
        self._is_started = False
        self._is_shutdown = False

        self._vad_task: Optional[asyncio.Task] = None

    @property
    def state(self) -> VoiceSessionState:
        with self._lock:
            return self._state

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def cancellation_generation(self) -> int:
        return self._cancellation_tracker.current_generation

    def _alloc_utt_id(self) -> str:
        with self._lock:
            self._utt_counter += 1
            return f"utt_{self._utt_counter}"

    def _alloc_resp_id(self) -> str:
        with self._lock:
            self._resp_counter += 1
            return f"resp_{self._resp_counter}"

    def _alloc_pb_id(self) -> str:
        with self._lock:
            self._pb_counter += 1
            return f"pb_{self._pb_counter}"

    def get_current_context(self) -> SessionContext:
        with self._lock:
            return SessionContext(
                session_id=self._session_id,
                utterance_id=self._utterance_id,
                response_id=self._response_id,
                playback_id=self._playback_id,
                event_sequence=self._emitter.current_seq,
                cancellation_generation=self._cancellation_tracker.current_generation,
                monotonic_ns=self._clock.now_ns(),
            )

    async def _transition_to(
        self, new_state: VoiceSessionState, reason: str = "ok"
    ) -> None:
        """Transition state and emit event with strictly serialized sequence delivery."""
        with self._lock:
            if self._is_shutdown and new_state != VoiceSessionState.STOPPED:
                return
            old_state = self._state
            if old_state == new_state and new_state != VoiceSessionState.STOPPED:
                return
            self._state = new_state
            gen = self._cancellation_tracker.current_generation
            sess_id = self._session_id
            utt_id = self._utterance_id
            resp_id = self._response_id
            pb_id = self._playback_id

        await self._emitter.emit(
            lambda seq: StateChangeEvent(
                session_id=sess_id,
                utterance_id=utt_id,
                response_id=resp_id,
                playback_id=pb_id,
                event_sequence=seq,
                monotonic_ns=self._clock.now_ns(),
                cancellation_generation=gen,
                old_state=old_state.value,
                new_state=new_state.value,
                reason=reason,
            )
        )

    async def start(self) -> None:
        """Start worker loops explicitly."""
        with self._lock:
            if self._is_shutdown:
                return
            if self._is_started:
                return
            self._is_started = True

        if self._vad_source is not None:
            self._vad_task = asyncio.create_task(self._vad_worker_loop())

    async def shutdown(self) -> None:
        """Independently cancel and join ALL worker tasks deterministically."""
        with self._lock:
            if self._is_shutdown:
                return
            self._is_shutdown = True

        self._cancellation_tracker.cancel("shutdown")
        self._transcript_pipeline.shutdown()
        self._tts_pipeline.shutdown()

        if self._playback_controller is not None:
            try:
                await self._playback_controller.stop()
            except Exception:
                pass

        await self._cancel_and_join_response_tasks()

        if self._vad_task is not None and not self._vad_task.done():
            self._vad_task.cancel()
            try:
                await self._vad_task
            except (asyncio.CancelledError, Exception):
                pass

        await self._transition_to(VoiceSessionState.STOPPED, reason="shutdown")

    async def _cancel_and_join_response_tasks(self) -> None:
        """Cancel and join all active response tasks from previous/current turns."""
        with self._lock:
            tasks = list(self._response_tasks)
            self._response_tasks.clear()
            self._active_response = None

        self._tts_pipeline.clear()

        curr_task = asyncio.current_task()
        sibling_tasks = [t for t in tasks if t != curr_task and not t.done()]

        for t in sibling_tasks:
            t.cancel()

        if sibling_tasks:
            await asyncio.gather(*sibling_tasks, return_exceptions=True)

    def _discard_response_task(self, task: asyncio.Task) -> None:
        with self._lock:
            self._response_tasks.discard(task)

    def _is_response_active_locked(self, tuple_item: Optional[ActiveResponseTuple]) -> bool:
        """Check if tuple is active while caller already holds self._lock."""
        if tuple_item is None:
            return False
        if self._is_shutdown:
            return False
        if self._active_response != tuple_item:
            return False
        if (
            tuple_item.session_id != self._session_id
            or tuple_item.utterance_id != self._utterance_id
            or tuple_item.response_id != self._response_id
            or tuple_item.playback_id != self._playback_id
        ):
            return False
        if self._cancellation_tracker.is_stale(tuple_item.cancellation_generation):
            return False
        return True

    def _matches_active_response_locked(self, tuple_item: Optional[ActiveResponseTuple]) -> bool:
        """Check exact equality with self._active_response regardless of generation staleness.

        Used strictly for correlated terminal cleanup to ensure deliberate cancellation epoch
        advancement (e.g. during verified barge-in) does not prevent response finalization.
        """
        if tuple_item is None or self._is_shutdown:
            return False
        return self._active_response == tuple_item

    def is_response_active(self, tuple_item: Optional[ActiveResponseTuple]) -> bool:
        with self._lock:
            return self._is_response_active_locked(tuple_item)

    def is_active_playback(
        self,
        *,
        session_id: object,
        response_id: object,
        playback_id: object,
        cancellation_generation: object,
    ) -> bool:
        """Fail-closed identity gate for asynchronous playback callbacks/adapters."""
        with self._lock:
            active = self._active_response
            if not self._is_response_active_locked(active):
                return False
            return (
                isinstance(cancellation_generation, int)
                and not isinstance(cancellation_generation, bool)
                and active is not None
                and session_id == active.session_id
                and response_id == active.response_id
                and playback_id == active.playback_id
                and cancellation_generation == active.cancellation_generation
            )

    async def _finalize_response(
        self, active_tuple: ActiveResponseTuple, outcome: str
    ) -> None:
        """Idempotent unified response finalization path for all terminal outcomes."""
        with self._lock:
            if not self._matches_active_response_locked(active_tuple):
                return
            self._active_response = None
            curr_task = asyncio.current_task()
            sibling_tasks = [t for t in self._response_tasks if t != curr_task and not t.done()]
            self._response_tasks.difference_update(sibling_tasks)

        self._tts_pipeline.clear()

        for t in sibling_tasks:
            t.cancel()

        if sibling_tasks:
            await asyncio.gather(*sibling_tasks, return_exceptions=True)

        if self._playback_controller is not None and outcome not in ("completed", "shutdown"):
            try:
                await self._playback_controller.stop()
            except Exception:
                pass

        if outcome == "completed":
            await self._transition_to(VoiceSessionState.LISTENING, reason="response_completed")
        elif outcome == "interrupted":
            await self._transition_to(VoiceSessionState.INTERRUPTED, reason="barge_in_verified")
        elif outcome in ("renderer_unavailable", "playback_unavailable", "playback_failed"):
            await self._transition_to(VoiceSessionState.DEGRADED_HALF_DUPLEX, reason=outcome)
        elif outcome in (
            "barge_verification_error",
            "consumer_error",
            "generation_error",
            "invalid_audio",
            "renderer_error",
            "transcript_error",
        ):
            await self._transition_to(VoiceSessionState.ERROR, reason=outcome)
        elif outcome == "shutdown":
            await self._transition_to(VoiceSessionState.STOPPED, reason="shutdown")
        else:
            await self._transition_to(VoiceSessionState.LISTENING, reason=outcome)

    async def update_aec_evidence(
        self,
        evidence: Optional[PlatformAecEvidence],
        has_echo_reference: bool,
        headphones_active: bool = False,
        device_id: str = "default_device",
    ) -> AecPolicyDecision:
        """Evaluate platform AEC evidence and adjust pipeline duplex mode."""
        with self._lock:
            if self._is_shutdown:
                return AecPolicyDecision(
                    is_full_duplex=False,
                    reason="shutdown",
                    has_echo_reference=False,
                    headphones_active=False,
                    monotonic_ns=0,
                )

        now_ns = self._clock.now_ns()
        decision = self._aec_policy.evaluate(
            evidence=evidence,
            has_echo_reference=has_echo_reference,
            headphones_active=headphones_active,
            now_ns=now_ns,
            active_stream_id=self._session_id,
            active_device_id=device_id,
        )

        if not decision.is_full_duplex:
            ctx = self.get_current_context()
            await self._emitter.emit(
                lambda seq: DegradedStateEvent(
                    session_id=ctx.session_id,
                    utterance_id=ctx.utterance_id,
                    response_id=ctx.response_id,
                    playback_id=ctx.playback_id,
                    event_sequence=seq,
                    monotonic_ns=now_ns,
                    cancellation_generation=ctx.cancellation_generation,
                    reason=decision.reason,
                    is_full_duplex=False,
                )
            )

            with self._lock:
                st = self._state
            if st in (VoiceSessionState.ASSISTANT_SPEAKING, VoiceSessionState.ASSISTANT_GENERATING):
                await self._transition_to(
                    VoiceSessionState.DEGRADED_HALF_DUPLEX, reason=decision.reason
                )

        return decision

    async def handle_barge_in_speech(
        self,
        audio_frames: Optional[list[AudioFrame]] = None,
        raw_transcript: Optional[str] = None,
        *,
        observed_session_id: Optional[str] = None,
        observed_response_id: Optional[str] = None,
        observed_playback_id: Optional[str] = None,
    ) -> bool:
        """Handle detected user speech observation during assistant playback.

        Section A: Require active non-null ActiveResponseTuple and active assistant state.
        Calls from idle/listening/stopped or without active response fail closed immediately.
        """
        with self._lock:
            if self._is_shutdown:
                return False
            if self._state not in (VoiceSessionState.ASSISTANT_GENERATING, VoiceSessionState.ASSISTANT_SPEAKING):
                return False
            active_tuple = self._active_response
            if not self._is_response_active_locked(active_tuple):
                return False
            observed_ids = (
                observed_session_id,
                observed_response_id,
                observed_playback_id,
            )
            if any(value is not None for value in observed_ids) and observed_ids != (
                active_tuple.session_id,
                active_tuple.response_id,
                active_tuple.playback_id,
            ):
                return False

        # Provisional utterance ID
        prov_utt_id = self._alloc_utt_id()
        ctx = self.get_current_context()

        # Step 1: Immediate reversible pause
        if self._playback_controller is not None:
            try:
                await self._playback_controller.pause()
            except Exception:
                pass

        await self._transition_to(VoiceSessionState.BARGE_IN_PENDING, reason="probable_speech")

        await self._emitter.emit(
            lambda seq: BargeInEvent(
                session_id=ctx.session_id,
                utterance_id=prov_utt_id,
                response_id=ctx.response_id,
                playback_id=ctx.playback_id,
                event_sequence=seq,
                monotonic_ns=self._clock.now_ns(),
                cancellation_generation=ctx.cancellation_generation,
                action="paused",
                reason="probable_speech",
            )
        )

        # Step 2: Fail closed if required evidence or verifiers missing
        if not audio_frames or self._owner_verifier is None or self._echo_noise_rejector is None:
            return await self._handle_rejected_barge_in(
                reason="missing_verification_evidence", ctx=ctx, active_tuple=active_tuple
            )

        # Step 3: Owner verification & Echo/noise evaluation
        try:
            owner_res = self._owner_verifier.verify_owner(audio_frames)
            echo_res = self._echo_noise_rejector.evaluate_echo_noise(audio_frames)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._finalize_response(active_tuple, outcome="barge_verification_error")
            return False

        if not owner_res.is_owner:
            return await self._handle_rejected_barge_in(
                reason=f"unverified_speaker_{owner_res.reason}", ctx=ctx, active_tuple=active_tuple
            )

        if echo_res.is_echo:
            return await self._handle_rejected_barge_in(
                reason="echo_detected", ctx=ctx, active_tuple=active_tuple
            )
        if echo_res.is_noise:
            return await self._handle_rejected_barge_in(
                reason="noise_detected", ctx=ctx, active_tuple=active_tuple
            )

        # Transcribe
        text = raw_transcript
        if not text and self._transcriber is not None:
            try:
                text = await self._transcriber.transcribe_final(audio_frames)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._finalize_response(active_tuple, outcome="transcript_error")
                return False

        if not text:
            return await self._handle_rejected_barge_in(
                reason="empty_transcript", ctx=ctx, active_tuple=active_tuple
            )

        start_ns = audio_frames[0].monotonic_ns if audio_frames else self._clock.now_ns()
        end_ns = audio_frames[-1].monotonic_ns if audio_frames else self._clock.now_ns()

        # Verification succeeded -> Make provisional utterance ID canonical
        with self._lock:
            self._utterance_id = prov_utt_id

        final = FinalTranscript(
            session_id=ctx.session_id,
            utterance_id=prov_utt_id,
            text=text,
            start_monotonic_ns=start_ns,
            end_monotonic_ns=end_ns,
        )

        # Step 4: Verification succeeded -> PERMANENT cancellation of old response
        request_id = f"req_{prov_utt_id}"
        gen = self._cancellation_tracker.cancel("barge_in_verified")

        req = InterruptionRequest(
            request_id=request_id,
            session_id=ctx.session_id,
            utterance_id=prov_utt_id,
            response_id=ctx.response_id,
            playback_id=ctx.playback_id,
            cancellation_generation=gen,
            monotonic_ns=self._clock.now_ns(),
            reason="barge_in_verified",
        )
        self._cancellation_tracker.record_request(req)

        if self._playback_controller is not None:
            try:
                await self._playback_controller.stop()
            except Exception:
                pass
            conf = InterruptionConfirmation(
                request_id=request_id,
                session_id=ctx.session_id,
                playback_id=ctx.playback_id,
                confirmed=True,
                monotonic_ns=self._clock.now_ns(),
            )
            self._cancellation_tracker.record_confirmation(conf)

        await self._finalize_response(active_tuple, outcome="interrupted")

        # Step 5: Submit verified final transcript under new cancellation epoch exactly once
        barge_ctx = self.get_current_context()
        try:
            # Owner and echo/noise evidence was already verified above. Avoid invoking
            # stateful verifier adapters twice for the same barge-in observation.
            success = await self._transcript_pipeline.process_final(
                final=final, ctx=barge_ctx, audio_frames=None
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._transition_to(VoiceSessionState.ERROR, reason="transcript_error")
            return False

        if success:
            await self._transition_to(VoiceSessionState.LISTENING, reason="turn_submitted")
            return True
        else:
            return False

    async def _handle_rejected_barge_in(
        self, reason: str, ctx: SessionContext, active_tuple: ActiveResponseTuple
    ) -> bool:
        """Reject probable speech and terminally abandon the paused response."""
        await self._emitter.emit(
            lambda seq: BargeInEvent(
                session_id=ctx.session_id,
                utterance_id=ctx.utterance_id,
                response_id=ctx.response_id,
                playback_id=ctx.playback_id,
                event_sequence=seq,
                monotonic_ns=self._clock.now_ns(),
                cancellation_generation=ctx.cancellation_generation,
                action="rejected",
                reason=reason,
            )
        )

        # Do not resume content after an unverified interruption. A rejected barge-in
        # terminally clears generation/render/playback consumers and returns to listening.
        await self._finalize_response(active_tuple, outcome="rejected_abandon")
        return False

    async def process_user_turn(
        self, text: str, audio_frames: Optional[list[AudioFrame]] = None
    ) -> bool:
        """Process verified user turn and trigger response generation.

        Section D: State-gated user turns. Reject calls while assistant is active
        or cancelled/interrupted-unresolved.
        """
        with self._lock:
            if self._is_shutdown:
                return False

            allowed_states = (
                VoiceSessionState.IDLE,
                VoiceSessionState.AWAITING_COMMAND,
                VoiceSessionState.LISTENING,
                VoiceSessionState.SLEEPING,
                VoiceSessionState.DEGRADED_HALF_DUPLEX,
                VoiceSessionState.ERROR,
            )
            if self._state not in allowed_states:
                return False

        utt_id = self._alloc_utt_id()
        with self._lock:
            self._utterance_id = utt_id

        ctx = self.get_current_context()
        final = FinalTranscript(
            session_id=ctx.session_id,
            utterance_id=utt_id,
            text=text,
            start_monotonic_ns=self._clock.now_ns(),
            end_monotonic_ns=self._clock.now_ns(),
        )

        await self._transition_to(VoiceSessionState.PROCESSING_FINAL, reason="user_turn")

        try:
            success = await self._transcript_pipeline.process_final(
                final=final, ctx=ctx, audio_frames=audio_frames
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._transition_to(VoiceSessionState.ERROR, reason="transcript_error")
            return False
        if not success:
            await self._transition_to(VoiceSessionState.ERROR, reason="transcript_rejected")
            return False

        if self._generator is not None:
            resp_id = self._alloc_resp_id()
            pb_id = self._alloc_pb_id()

            with self._lock:
                self._response_id = resp_id
                self._playback_id = pb_id
                gen = self._cancellation_tracker.current_generation
                active_tuple = ActiveResponseTuple(
                    session_id=self._session_id,
                    utterance_id=utt_id,
                    response_id=resp_id,
                    playback_id=pb_id,
                    cancellation_generation=gen,
                )
                self._active_response = active_tuple

            await self._transition_to(VoiceSessionState.ASSISTANT_GENERATING, reason="generating")
            gen_ctx = self.get_current_context()

            # Launch generation task and TTS consumer task into managed response tasks set
            g_task = asyncio.create_task(
                self._run_generation_and_enqueue(text, gen_ctx, active_tuple)
            )
            p_task = asyncio.create_task(
                self._run_tts_playback_consumer(active_tuple)
            )

            with self._lock:
                self._response_tasks.add(g_task)
                self._response_tasks.add(p_task)
            g_task.add_done_callback(self._discard_response_task)
            p_task.add_done_callback(self._discard_response_task)
        else:
            await self._transition_to(VoiceSessionState.LISTENING, reason="turn_submitted")

        return True

    async def _run_generation_and_enqueue(
        self, prompt_text: str, ctx: SessionContext, active_tuple: ActiveResponseTuple
    ) -> None:
        """Run LLM stream generation and enqueue sentence chunks into TTS pipeline."""
        try:
            if self._generator is None:
                return
            if not self.is_response_active(active_tuple):
                return
            stream = self._generator.generate(prompt_text)
            await self._tts_pipeline.enqueue_stream(stream, ctx)
            if self.is_response_active(active_tuple):
                await self._transition_to(VoiceSessionState.ASSISTANT_SPEAKING, reason="speaking")
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.is_response_active(active_tuple):
                await self._finalize_response(active_tuple, outcome="generation_error")

    async def _run_tts_playback_consumer(
        self, active_tuple: ActiveResponseTuple
    ) -> None:
        """Concurrent consumer loop rendering and playing sentence-bounded TTS chunks safely.

        Section A & B: Unified response finalization for all terminal outcomes.
        """
        try:
            while True:
                if not self.is_response_active(active_tuple):
                    break

                chunk = await self._tts_pipeline.get_next_chunk()
                if chunk is None:
                    # End of response stream sentinel reached!
                    if self.is_response_active(active_tuple):
                        ctx = self.get_current_context()
                        await self._emitter.emit(
                            lambda seq: PlaybackEvent(
                                session_id=active_tuple.session_id,
                                utterance_id=active_tuple.utterance_id,
                                response_id=active_tuple.response_id,
                                playback_id=active_tuple.playback_id,
                                event_sequence=seq,
                                monotonic_ns=self._clock.now_ns(),
                                cancellation_generation=active_tuple.cancellation_generation,
                                action="completed",
                                position_ms=0,
                            )
                        )
                        await self._finalize_response(active_tuple, outcome="completed")
                    break

                if not self.is_response_active(active_tuple):
                    break
                if not self.is_active_playback(
                    session_id=chunk.session_id,
                    response_id=chunk.response_id,
                    playback_id=chunk.playback_id,
                    cancellation_generation=chunk.cancellation_generation,
                ):
                    continue

                # Check TTS renderer existence
                if self._tts_renderer is None:
                    ctx = self.get_current_context()
                    await self._emitter.emit(
                        lambda seq: DegradedStateEvent(
                            session_id=chunk.session_id,
                            utterance_id=ctx.utterance_id,
                            response_id=chunk.response_id,
                            playback_id=chunk.playback_id,
                            event_sequence=seq,
                            monotonic_ns=self._clock.now_ns(),
                            cancellation_generation=chunk.cancellation_generation,
                            reason="no_tts_renderer",
                            is_full_duplex=False,
                        )
                    )
                    await self._finalize_response(active_tuple, outcome="renderer_unavailable")
                    break

                # Render TTS
                try:
                    audio_bytes = await self._tts_renderer.render(chunk.text)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await self._finalize_response(active_tuple, outcome="renderer_error")
                    break

                if not self.is_response_active(active_tuple):
                    break

                # Validate rendered audio bytes (non-empty bytes, max 10MB)
                if not isinstance(audio_bytes, bytes) or len(audio_bytes) == 0 or len(audio_bytes) > _MAX_AUDIO_BYTE_LEN:
                    await self._finalize_response(active_tuple, outcome="invalid_audio")
                    break

                # Play TTS audio
                if self._playback_controller is None:
                    await self._finalize_response(active_tuple, outcome="playback_unavailable")
                    break

                try:
                    play_success = await self._playback_controller.play(audio_bytes, chunk.playback_id)
                except asyncio.CancelledError:
                    if self._playback_controller is not None:
                        try:
                            await self._playback_controller.stop()
                        except Exception:
                            pass
                    raise
                except Exception:
                    await self._finalize_response(active_tuple, outcome="playback_failed")
                    break

                if not self.is_response_active(active_tuple):
                    if self._playback_controller is not None:
                        try:
                            await self._playback_controller.stop()
                        except Exception:
                            pass
                    break

                # Emit play event ONLY if playback reported success!
                if play_success:
                    ctx = self.get_current_context()
                    await self._emitter.emit(
                        lambda seq: PlaybackEvent(
                            session_id=chunk.session_id,
                            utterance_id=ctx.utterance_id,
                            response_id=chunk.response_id,
                            playback_id=chunk.playback_id,
                            event_sequence=seq,
                            monotonic_ns=self._clock.now_ns(),
                            cancellation_generation=chunk.cancellation_generation,
                            action="play",
                            position_ms=0,
                        )
                    )
                else:
                    await self._finalize_response(active_tuple, outcome="playback_failed")
                    break
        except asyncio.CancelledError:
            if self._playback_controller is not None:
                try:
                    await self._playback_controller.stop()
                except Exception:
                    pass
            raise
        except Exception:
            await self._finalize_response(active_tuple, outcome="consumer_error")

    async def _vad_worker_loop(self) -> None:
        """Background VAD observation and bounded frame endpointing loop."""
        try:
            while True:
                with self._lock:
                    if self._is_shutdown:
                        break
                if self._vad_source is None:
                    break

                obs = await self._vad_source.observe()
                if obs is None:
                    await asyncio.sleep(0.01)
                    continue

                is_speech, confidence, ts_ns = obs
                with self._lock:
                    st = self._state

                if is_speech and st in (VoiceSessionState.ASSISTANT_SPEAKING, VoiceSessionState.ASSISTANT_GENERATING):
                    with self._lock:
                        observed = self._active_response
                        if not self._is_response_active_locked(observed):
                            observed = None
                    if observed is None:
                        continue
                    frames = await self._gather_bounded_frames()
                    await self.handle_barge_in_speech(
                        audio_frames=frames,
                        observed_session_id=observed.session_id,
                        observed_response_id=observed.response_id,
                        observed_playback_id=observed.playback_id,
                    )

                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._transition_to(VoiceSessionState.ERROR, reason="vad_loop_error")

    async def _gather_bounded_frames(self) -> list[AudioFrame]:
        """Pure, injected bounded frame endpointing collector.

        Cancellable, bounded by deadline and max frame count.
        Catches asyncio.TimeoutError separately. Re-raises asyncio.CancelledError.
        Does NOT invoke VADSourceProtocol concurrently with VAD worker loop!
        """
        frames: list[AudioFrame] = []
        if self._frame_source is None:
            return frames

        loop = asyncio.get_running_loop()
        deadline_s = loop.time() + self._max_capture_duration_s

        for _ in range(self._max_capture_frames):
            with self._lock:
                if self._is_shutdown:
                    break

            remaining_s = deadline_s - loop.time()
            if remaining_s <= 0.0:
                break

            try:
                frame = await asyncio.wait_for(self._frame_source.get_frame(), timeout=min(remaining_s, 0.1))
            except asyncio.TimeoutError:
                break
            except asyncio.CancelledError:
                raise
            except Exception:
                break

            if frame is None:
                break
            frames.append(frame)

        return frames

    def __repr__(self) -> str:
        with self._lock:
            st = self._state.value
            seq = self._emitter.current_seq
        gen = self._cancellation_tracker.current_generation
        return f"<VoiceSessionCoordinator state={st!r} seq={seq} gen={gen}>"


__all__ = [
    "VoiceSessionCoordinator",
    "VoiceSessionState",
]
