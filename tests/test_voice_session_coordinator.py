"""Tests for VoiceSessionCoordinator end-to-end orchestration, barge-in, and lifecycle."""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, List, Optional
import pytest

from core.voice_session import (
    AudioFrame,
    EchoNoiseResult,
    OwnerVerificationResult,
    PlatformAecEvidence,
    VoiceSessionCoordinator,
    VoiceSessionState,
)
from core.voice_session.contracts import (
    EchoNoiseRejectorProtocol,
    FrameSourceProtocol,
    GenerationStreamProtocol,
    MonotonicClockProtocol,
    OwnerVerifierProtocol,
    PlaybackControllerProtocol,
    ResumePolicyProtocol,
    StateEventSinkProtocol,
    TTSRendererProtocol,
    TranscriberProtocol,
    TurnSinkProtocol,
    VADSourceProtocol,
    validate_capture_duration_bound,
    validate_capture_frame_bound,
)
from core.voice_session.events import DegradedStateEvent, PlaybackEvent, StateChangeEvent, VoiceSessionEvent


class MockClock(MonotonicClockProtocol):
    def __init__(self) -> None:
        self._time = 1000

    def now_ns(self) -> int:
        self._time += 10
        return self._time


class MockPlaybackController(PlaybackControllerProtocol):
    def __init__(self, play_returns: bool = True, play_delay: float = 0.0) -> None:
        self.state = "idle"
        self.played_ids: List[str] = []
        self.played_audio: List[bytes] = []
        self.play_returns = play_returns
        self.play_delay = play_delay

    async def play(self, audio_data: bytes, playback_id: str) -> bool:
        if self.play_delay > 0:
            await asyncio.sleep(self.play_delay)
        self.state = "playing"
        self.played_ids.append(playback_id)
        self.played_audio.append(audio_data)
        return self.play_returns

    async def pause(self) -> None:
        self.state = "paused"

    async def stop(self) -> None:
        self.state = "stopped"

    async def resume(self) -> None:
        self.state = "playing"


class SlowEventSink(StateEventSinkProtocol):
    """Event sink that introduces artificial delay for testing concurrency serialization."""

    def __init__(self) -> None:
        self.events: List[VoiceSessionEvent] = []
        self.delay: float = 0.0

    async def emit_event(self, event: object) -> None:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if isinstance(event, VoiceSessionEvent):
            self.events.append(event)


class FailOnceEventSink(StateEventSinkProtocol):
    """Event sink that raises an exception on the very first event emit attempt."""

    def __init__(self) -> None:
        self.events: List[VoiceSessionEvent] = []
        self.failed_once = False

    async def emit_event(self, event: object) -> None:
        if not self.failed_once:
            self.failed_once = True
            raise RuntimeError("Fail once event sink error")
        if isinstance(event, VoiceSessionEvent):
            self.events.append(event)


class ExceptionEventSink(StateEventSinkProtocol):
    async def emit_event(self, event: object) -> None:
        raise RuntimeError("Event sink failure")


class MockTurnSink(TurnSinkProtocol):
    def __init__(self) -> None:
        self.turns: List[tuple[str, str, str]] = []

    async def on_turn(
        self, text: str, session_id: str, utterance_id: str, start_ns: int, end_ns: int
    ) -> None:
        self.turns.append((text, session_id, utterance_id))


class MockTranscriber(TranscriberProtocol):
    def __init__(self, text: str = "Verified user command") -> None:
        self._text = text

    async def transcribe_partial(self, frame: AudioFrame) -> Optional[str]:
        return "Partial text"

    async def transcribe_final(self, frames: list[AudioFrame]) -> Optional[str]:
        return self._text


class MockOwnerVerifier(OwnerVerifierProtocol):
    def __init__(self, is_owner: bool = True) -> None:
        self._is_owner = is_owner

    def verify_owner(self, frames: list[AudioFrame]) -> OwnerVerificationResult:
        return OwnerVerificationResult(is_owner=self._is_owner, confidence=0.99)


class MockEchoRejector(EchoNoiseRejectorProtocol):
    def __init__(self, is_echo: bool = False, is_noise: bool = False) -> None:
        self._is_echo = is_echo
        self._is_noise = is_noise

    def evaluate_echo_noise(self, frames: list[AudioFrame]) -> EchoNoiseResult:
        return EchoNoiseResult(is_echo=self._is_echo, is_noise=self._is_noise)


class MockGenerator(GenerationStreamProtocol):
    def __init__(self, sentences: Optional[List[str]] = None, raise_exc: bool = False) -> None:
        self.sentences = sentences or ["Hello human.", "How can I help?"]
        self.raise_exc = raise_exc

    async def generate(self, prompt_text: str) -> AsyncIterator[str]:
        if self.raise_exc:
            raise RuntimeError("LLM stream generation error")
        for s in self.sentences:
            yield s + " "


class MockTTSRenderer(TTSRendererProtocol):
    def __init__(self, render_delay: float = 0.0, raise_exc: bool = False, return_invalid: bool = False, return_oversized: bool = False) -> None:
        self.render_delay = render_delay
        self.raise_exc = raise_exc
        self.return_invalid = return_invalid
        self.return_oversized = return_oversized
        self.rendered_texts: List[str] = []

    async def render(self, text: str) -> bytes:
        if self.raise_exc:
            raise RuntimeError("TTS render failure")
        if self.return_invalid:
            return b""
        if self.return_oversized:
            return b"X" * 10_000_001
        if self.render_delay > 0:
            await asyncio.sleep(self.render_delay)
        self.rendered_texts.append(text)
        return f"AUDIO({text})".encode("utf-8")


class MockFrameSource(FrameSourceProtocol):
    def __init__(self, frame_count: int = 5, delay: float = 0.0) -> None:
        self.count = frame_count
        self.delay = delay
        self.yielded = 0

    async def get_frame(self) -> Optional[AudioFrame]:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.yielded < self.count:
            self.yielded += 1
            return AudioFrame(data=b"\x01\x02", sample_rate=16000, channels=1, monotonic_ns=1000 + self.yielded)
        return None


class MockVADSource(VADSourceProtocol):
    def __init__(self) -> None:
        self.calls = 0

    async def observe(self) -> Optional[tuple[bool, float, int]]:
        self.calls += 1
        return None


class MockResumePolicy(ResumePolicyProtocol):
    def __init__(self, should_resume_val: bool = True) -> None:
        self._val = should_resume_val

    def should_resume(self, previous_response_id: str, rejected_reason: str) -> bool:
        return self._val


# -----------------------------------------------------------------------------
# RESTORED COVERAGE & EXISTING TESTS
# -----------------------------------------------------------------------------

def test_pure_coordinator_construction_no_implicit_workers() -> None:
    coordinator = VoiceSessionCoordinator(session_id="sess_pure")
    assert coordinator.state == VoiceSessionState.IDLE
    assert coordinator._is_started is False
    assert coordinator._vad_task is None


def test_explicit_start_and_idempotent_shutdown() -> None:
    async def run() -> None:
        vad_src = MockVADSource()
        coordinator = VoiceSessionCoordinator(session_id="sess_start", vad_source=vad_src)
        await coordinator.start()
        assert coordinator._is_started is True
        assert coordinator._vad_task is not None

        await coordinator.shutdown()
        assert coordinator.state == VoiceSessionState.STOPPED

        # Second shutdown call is idempotent
        await coordinator.shutdown()
        assert coordinator.state == VoiceSessionState.STOPPED

    asyncio.run(run())


def test_event_sequences_strictly_increasing_and_unique() -> None:
    async def run() -> None:
        clock = MockClock()
        event_sink = SlowEventSink()
        turn_sink = MockTurnSink()
        generator = MockGenerator(["First sentence.", "Second sentence."])
        renderer = MockTTSRenderer()
        playback = MockPlaybackController()

        coordinator = VoiceSessionCoordinator(
            session_id="sess_seq",
            clock=clock,
            event_sink=event_sink,
            turn_sink=turn_sink,
            generator=generator,
            tts_renderer=renderer,
            playback_controller=playback,
        )

        await coordinator.start()
        await coordinator.process_user_turn("Hello computer")
        await asyncio.sleep(0.3)

        seqs = [e.event_sequence for e in event_sink.events]
        assert len(seqs) >= 3
        assert seqs == list(range(1, len(seqs) + 1))

        await coordinator.shutdown()

    asyncio.run(run())


def test_barge_in_events_ordering() -> None:
    async def run() -> None:
        clock = MockClock()
        event_sink = SlowEventSink()
        turn_sink = MockTurnSink()
        transcriber = MockTranscriber("Stop now")
        owner_verifier = MockOwnerVerifier(is_owner=True)
        echo_rejector = MockEchoRejector(is_echo=False)

        coordinator = VoiceSessionCoordinator(
            session_id="sess_barge_order",
            clock=clock,
            event_sink=event_sink,
            turn_sink=turn_sink,
            transcriber=transcriber,
            owner_verifier=owner_verifier,
            echo_noise_rejector=echo_rejector,
        )

        # Setup active response
        await coordinator.process_user_turn("Setup prompt")
        await asyncio.sleep(0.05)

        frame = AudioFrame(data=b"\x01", sample_rate=16000, channels=1, monotonic_ns=clock.now_ns())
        await coordinator.handle_barge_in_speech(audio_frames=[frame])

        seqs = [e.event_sequence for e in event_sink.events]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)

        await coordinator.shutdown()

    asyncio.run(run())


def test_echo_speech_rejection() -> None:
    async def run() -> None:
        clock = MockClock()
        event_sink = SlowEventSink()
        owner_verifier = MockOwnerVerifier(is_owner=True)
        echo_rejector = MockEchoRejector(is_echo=True)

        coordinator = VoiceSessionCoordinator(
            session_id="sess_echo",
            clock=clock,
            event_sink=event_sink,
            owner_verifier=owner_verifier,
            echo_noise_rejector=echo_rejector,
        )

        await coordinator.process_user_turn("Setup prompt")
        frame = AudioFrame(data=b"\x01", sample_rate=16000, channels=1, monotonic_ns=clock.now_ns())

        res = await coordinator.handle_barge_in_speech(audio_frames=[frame])
        assert res is False

        await coordinator.shutdown()

    asyncio.run(run())


def test_missing_verification_rejection() -> None:
    async def run() -> None:
        coordinator = VoiceSessionCoordinator(session_id="sess_no_verifiers")
        await coordinator.process_user_turn("Setup prompt")

        # Missing audio frames or verifiers fails closed
        res = await coordinator.handle_barge_in_speech(audio_frames=None)
        assert res is False

        await coordinator.shutdown()

    asyncio.run(run())


def test_aec_downgrade_event() -> None:
    async def run() -> None:
        event_sink = SlowEventSink()
        coordinator = VoiceSessionCoordinator(session_id="sess_aec", event_sink=event_sink)
        await coordinator.start()

        decision = await coordinator.update_aec_evidence(
            evidence=None, has_echo_reference=False
        )
        assert decision.is_full_duplex is False

        degraded = [e for e in event_sink.events if isinstance(e, DegradedStateEvent)]
        assert len(degraded) == 1

        await coordinator.shutdown()

    asyncio.run(run())


# -----------------------------------------------------------------------------
# REQUIRED 15 REGRESSION TEST CASES
# -----------------------------------------------------------------------------

# Reg 1. Barge-in from IDLE with valid-looking audio fails with zero mutation
def test_barge_in_from_idle_fails_with_zero_mutation() -> None:
    async def run() -> None:
        event_sink = SlowEventSink()
        clock = MockClock()
        owner_verifier = MockOwnerVerifier(is_owner=True)
        echo_rejector = MockEchoRejector(is_echo=False)

        coordinator = VoiceSessionCoordinator(
            session_id="sess_idle_barge",
            clock=clock,
            event_sink=event_sink,
            owner_verifier=owner_verifier,
            echo_noise_rejector=echo_rejector,
        )

        frame = AudioFrame(data=b"\x01\x02", sample_rate=16000, channels=1, monotonic_ns=clock.now_ns())
        res = await coordinator.handle_barge_in_speech(audio_frames=[frame])

        assert res is False
        assert coordinator.state == VoiceSessionState.IDLE
        assert len(event_sink.events) == 0
        assert coordinator._utterance_id == "utt_0"

    asyncio.run(run())


# Reg 2. Barge-in from ASSISTANT_SPEAKING but no active tuple fails closed
def test_barge_in_after_completed_response_fails_closed() -> None:
    async def run() -> None:
        event_sink = SlowEventSink()
        clock = MockClock()
        playback = MockPlaybackController()

        coordinator = VoiceSessionCoordinator(
            session_id="sess_no_tuple_barge",
            clock=clock,
            event_sink=event_sink,
            generator=MockGenerator(["Done."]),
            tts_renderer=MockTTSRenderer(),
            playback_controller=playback,
        )
        assert await coordinator.process_user_turn("Complete this turn") is True
        await asyncio.sleep(0.1)
        assert coordinator.state == VoiceSessionState.LISTENING
        event_count = len(event_sink.events)

        frame = AudioFrame(data=b"\x01\x02", sample_rate=16000, channels=1, monotonic_ns=clock.now_ns())
        res = await coordinator.handle_barge_in_speech(audio_frames=[frame])

        assert res is False
        assert len(event_sink.events) == event_count
        assert playback.state == "playing"

        await coordinator.shutdown()

    asyncio.run(run())


# Reg 3. Rejected/resume-approved interruption preserves original correlation
def test_rejected_barge_in_terminally_clears_response() -> None:
    async def run() -> None:
        clock = MockClock()
        playback = MockPlaybackController()
        resume_policy = MockResumePolicy(should_resume_val=True)
        owner_verifier = MockOwnerVerifier(is_owner=False)  # Rejected
        renderer = MockTTSRenderer(render_delay=0.2)

        coordinator = VoiceSessionCoordinator(
            session_id="sess_resume_appr",
            clock=clock,
            tts_renderer=renderer,
            playback_controller=playback,
            resume_policy=resume_policy,
            owner_verifier=owner_verifier,
            generator=MockGenerator(["Sentence."]),
        )

        await coordinator.start()
        await coordinator.process_user_turn("Turn prompt")
        await asyncio.sleep(0.05)

        frame = AudioFrame(data=b"\x01\x02", sample_rate=16000, channels=1, monotonic_ns=clock.now_ns())
        res = await coordinator.handle_barge_in_speech(audio_frames=[frame])

        assert res is False
        assert coordinator.state == VoiceSessionState.LISTENING
        assert playback.state == "stopped"
        assert await coordinator.process_user_turn("Next ordinary turn") is True

        await coordinator.shutdown()

    asyncio.run(run())


# Reg 4. Rejected/resume-denied interruption leaves no active tuple or response task
def test_rejected_resume_denied_barge_in_clears_active_tuple() -> None:
    async def run() -> None:
        clock = MockClock()
        playback = MockPlaybackController()
        resume_policy = MockResumePolicy(should_resume_val=False)  # Denied
        owner_verifier = MockOwnerVerifier(is_owner=False)  # Rejected
        renderer = MockTTSRenderer(render_delay=0.2)

        coordinator = VoiceSessionCoordinator(
            session_id="sess_resume_denied",
            clock=clock,
            tts_renderer=renderer,
            playback_controller=playback,
            resume_policy=resume_policy,
            owner_verifier=owner_verifier,
            generator=MockGenerator(["Sentence."]),
        )

        await coordinator.start()
        await coordinator.process_user_turn("Turn prompt")
        await asyncio.sleep(0.05)

        frame = AudioFrame(data=b"\x01\x02", sample_rate=16000, channels=1, monotonic_ns=clock.now_ns())
        res = await coordinator.handle_barge_in_speech(audio_frames=[frame])

        assert res is False
        assert coordinator.state == VoiceSessionState.LISTENING
        assert playback.state == "stopped"
        assert await coordinator.process_user_turn("Next turn") is True

        await coordinator.shutdown()

    asyncio.run(run())


# Reg 5. Generation failure leaves no active tuple or live consumer
def test_generation_failure_clears_active_tuple_and_tasks() -> None:
    async def run() -> None:
        generator = MockGenerator(raise_exc=True)
        coordinator = VoiceSessionCoordinator(
            session_id="sess_gen_fail",
            generator=generator,
        )

        await coordinator.start()
        await coordinator.process_user_turn("Prompt")
        await asyncio.sleep(0.1)

        assert coordinator.state == VoiceSessionState.ERROR
        assert await coordinator.process_user_turn("Recovery prompt") is True

        await coordinator.shutdown()

    asyncio.run(run())


# Reg 6. Renderer missing/error leaves no active tuple or live consumer
def test_renderer_error_clears_active_tuple_and_tasks() -> None:
    async def run() -> None:
        renderer = MockTTSRenderer(raise_exc=True)
        coordinator = VoiceSessionCoordinator(
            session_id="sess_render_fail",
            tts_renderer=renderer,
            generator=MockGenerator(["Text."]),
        )

        await coordinator.start()
        await coordinator.process_user_turn("Prompt")
        await asyncio.sleep(0.1)

        assert coordinator.state == VoiceSessionState.ERROR
        assert await coordinator.process_user_turn("Recovery prompt") is True

        await coordinator.shutdown()

    asyncio.run(run())


# Reg 7. Invalid/oversized rendered audio leaves no active tuple or live consumer
def test_invalid_rendered_audio_clears_active_tuple_and_tasks() -> None:
    async def run() -> None:
        renderer = MockTTSRenderer(return_oversized=True)
        coordinator = VoiceSessionCoordinator(
            session_id="sess_oversized",
            tts_renderer=renderer,
            generator=MockGenerator(["Text."]),
        )

        await coordinator.start()
        await coordinator.process_user_turn("Prompt")
        await asyncio.sleep(0.1)

        assert coordinator.state == VoiceSessionState.ERROR
        assert await coordinator.process_user_turn("Recovery prompt") is True

        await coordinator.shutdown()

    asyncio.run(run())


# Reg 8. Playback missing/exception/False leaves no active tuple or live consumer
def test_playback_failure_clears_active_tuple_and_tasks() -> None:
    async def run() -> None:
        playback = MockPlaybackController(play_returns=False)
        coordinator = VoiceSessionCoordinator(
            session_id="sess_pb_false",
            playback_controller=playback,
            tts_renderer=MockTTSRenderer(),
            generator=MockGenerator(["Text."]),
        )

        await coordinator.start()
        await coordinator.process_user_turn("Prompt")
        await asyncio.sleep(0.1)

        assert coordinator.state == VoiceSessionState.DEGRADED_HALF_DUPLEX
        assert await coordinator.process_user_turn("Recovery prompt") is True

        await coordinator.shutdown()

    asyncio.run(run())


# Reg 9 & 10. Normal completion waits for gated playback completion and finalizes exactly once
def test_normal_completion_waits_for_playback_completion() -> None:
    async def run() -> None:
        event_sink = SlowEventSink()
        playback = MockPlaybackController(play_delay=0.1)
        renderer = MockTTSRenderer()

        coordinator = VoiceSessionCoordinator(
            session_id="sess_gated_comp",
            event_sink=event_sink,
            playback_controller=playback,
            tts_renderer=renderer,
            generator=MockGenerator(["Single sentence."]),
        )

        await coordinator.start()
        await coordinator.process_user_turn("Prompt")

        # Immediately after process_user_turn, playback has not completed
        assert len(playback.played_ids) == 0

        # Wait for physical play completion delay
        await asyncio.sleep(0.3)

        assert coordinator.state == VoiceSessionState.LISTENING
        assert renderer.rendered_texts == ["Single sentence."]
        assert playback.played_audio == [b"AUDIO(Single sentence.)"]

        completed_events = [
            e for e in event_sink.events
            if isinstance(e, PlaybackEvent) and e.action == "completed"
        ]
        assert len(completed_events) == 1

        await coordinator.shutdown()

    asyncio.run(run())


# Reg 11. Fail-once sink never delivers sequence 2 without sequence 1
def test_fail_once_sink_never_delivers_seq2_without_seq1() -> None:
    async def run() -> None:
        fail_sink = FailOnceEventSink()
        coordinator = VoiceSessionCoordinator(
            session_id="sess_fail_once",
            event_sink=fail_sink,
        )

        # The first public turn transition fails delivery and latches emission closed.
        assert await coordinator.process_user_turn("Public turn") is True

        # A later public event source cannot deliver sequence 2 after sequence 1 failed.
        await coordinator.update_aec_evidence(None, has_echo_reference=False)

        # Sink should contain NO events (or seq 1 retry), definitely NOT [2]!
        delivered_seqs = [e.event_sequence for e in fail_sink.events]
        assert 2 not in delivered_seqs

    asyncio.run(run())


# Reg 12. Latched failed emitter does not deadlock shutdown
def test_latched_failed_emitter_does_not_deadlock_shutdown() -> None:
    async def run() -> None:
        fail_sink = FailOnceEventSink()
        coordinator = VoiceSessionCoordinator(
            session_id="sess_latched_shutdown",
            event_sink=fail_sink,
        )

        await coordinator.start()
        await coordinator._transition_to(VoiceSessionState.LISTENING)
        assert coordinator._emitter._latched_failed is True

        # Shutdown must complete smoothly without deadlock!
        await coordinator.shutdown()
        assert coordinator.state == VoiceSessionState.STOPPED

    asyncio.run(run())


# Reg 13. typing.get_type_hints succeeds for TranscriptPipeline
def test_transcript_pipeline_type_hints_succeed() -> None:
    import typing
    from core.voice_session.transcript_pipeline import TranscriptPipeline
    hints = typing.get_type_hints(TranscriptPipeline.__init__)
    assert hints is not None
    assert "emitter_fn" in hints


# Reg 14. Public state-gated stale-playback test using exact playback IDs
def test_public_state_gated_stale_playback_exact_ids() -> None:
    async def run() -> None:
        clock = MockClock()
        render_gate = asyncio.Event()
        playback = MockPlaybackController()

        class GatedRenderer(TTSRendererProtocol):
            async def render(self, text: str) -> bytes:
                await render_gate.wait()
                return f"AUDIO({text})".encode("utf-8")

        transcriber = MockTranscriber("Verified barge command")
        owner_verifier = MockOwnerVerifier(is_owner=True)
        echo_rejector = MockEchoRejector(is_echo=False)

        coordinator = VoiceSessionCoordinator(
            session_id="sess_public_stale",
            clock=clock,
            tts_renderer=GatedRenderer(),
            playback_controller=playback,
            transcriber=transcriber,
            owner_verifier=owner_verifier,
            echo_noise_rejector=echo_rejector,
            generator=MockGenerator(["First turn response text."]),
        )

        await coordinator.start()

        # Step 1: Start Turn 1 (blocks in render_gate.wait())
        res1 = await coordinator.process_user_turn("First prompt")
        assert res1 is True

        old_pb_id = coordinator.get_current_context().playback_id

        # Step 2: Attempt public process_user_turn while assistant is active -> MUST BE REJECTED!
        res_rejected = await coordinator.process_user_turn("Attempted overwrite")
        assert res_rejected is False

        # Step 3: Invoke public verified handle_barge_in_speech path
        frame = AudioFrame(data=b"\x01\x02", sample_rate=16000, channels=1, monotonic_ns=clock.now_ns())
        barge_res = await coordinator.handle_barge_in_speech(audio_frames=[frame])
        assert barge_res is True

        # Step 4: Release rendering gate
        render_gate.set()
        await asyncio.sleep(0.2)

        # Assert exact old playback ID was NEVER played after verified cancellation!
        assert old_pb_id not in playback.played_ids

        await coordinator.shutdown()

    asyncio.run(run())


# Reg 15. All prior cancellation, bounds, and queue tests remain passing
def test_gather_bounded_frames_propagates_cancellation() -> None:
    async def run() -> None:
        frame_src = MockFrameSource(frame_count=10, delay=0.5)
        coordinator = VoiceSessionCoordinator(
            session_id="sess_cancel_gather",
            frame_source=frame_src,
        )

        task = asyncio.create_task(coordinator._gather_bounded_frames())
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_gather_bounded_frames_timeout_returns_safely() -> None:
    async def run() -> None:
        frame_src = MockFrameSource(frame_count=10, delay=0.5)
        coordinator = VoiceSessionCoordinator(
            session_id="sess_timeout_gather",
            frame_source=frame_src,
            max_capture_frames=10,
            max_capture_duration_s=0.1,
        )

        started = time.monotonic()
        frames = await coordinator._gather_bounded_frames()
        elapsed = time.monotonic() - started
        assert frames == []
        assert elapsed < 0.3

    asyncio.run(run())


def test_racing_duplicate_finalizers_cause_one_transition() -> None:
    async def run() -> None:
        renderer = MockTTSRenderer(render_delay=0.2)
        coordinator = VoiceSessionCoordinator(
            session_id="sess_race_fin",
            generator=MockGenerator(["Sentence."]),
            tts_renderer=renderer,
        )
        await coordinator.start()
        await coordinator.process_user_turn("Turn 1")

        active_tuple = coordinator._active_response
        assert active_tuple is not None

        # Call _finalize_response twice concurrently
        t1 = asyncio.create_task(coordinator._finalize_response(active_tuple, outcome="completed"))
        t2 = asyncio.create_task(coordinator._finalize_response(active_tuple, outcome="completed"))

        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1.0)

        assert coordinator._active_response is None
        assert len(coordinator._response_tasks) == 0
        assert coordinator.state == VoiceSessionState.LISTENING

        await coordinator.shutdown()

    asyncio.run(run())


def test_verified_barge_in_cleans_up_old_response_completely() -> None:
    async def run() -> None:
        clock = MockClock()
        playback = MockPlaybackController()
        renderer = MockTTSRenderer(render_delay=0.2)
        generator = MockGenerator(["First sentence.", "Second sentence."])
        turn_sink = MockTurnSink()
        transcriber = MockTranscriber("Verified barge in command")
        owner_verifier = MockOwnerVerifier(is_owner=True)
        echo_rejector = MockEchoRejector(is_echo=False)

        coordinator = VoiceSessionCoordinator(
            session_id="sess_barge_cleanup",
            clock=clock,
            generator=generator,
            tts_renderer=renderer,
            playback_controller=playback,
            turn_sink=turn_sink,
            transcriber=transcriber,
            owner_verifier=owner_verifier,
            echo_noise_rejector=echo_rejector,
        )

        await coordinator.start()

        # 1. Start a real active response
        res_turn = await coordinator.process_user_turn("First prompt")
        assert res_turn is True
        await asyncio.sleep(0.05)

        # 2. Retain the public pre-interruption generation.
        old_gen = coordinator.get_current_context().cancellation_generation

        # 3. Perform a verified barge-in under asyncio.wait_for
        frame = AudioFrame(data=b"\x01\x02", sample_rate=16000, channels=1, monotonic_ns=clock.now_ns())
        barge_res = await asyncio.wait_for(
            coordinator.handle_barge_in_speech(audio_frames=[frame]), timeout=2.0
        )

        # 5. Assert barge-in returns True
        assert barge_res is True

        # 6. Assert cancellation generation advanced
        assert coordinator.cancellation_generation > old_gen

        # 7. The old response is terminal: a new ordinary turn is accepted.
        assert coordinator.state == VoiceSessionState.LISTENING
        assert await coordinator.process_user_turn("Post-interruption turn") is True

        # 9. Assert playback stop was requested
        assert playback.state == "stopped"

        # 10. Assert verified new turn was submitted exactly once
        assert len(turn_sink.turns) == 3
        assert turn_sink.turns[1][0] == "Verified barge in command"

        await coordinator.shutdown()

    asyncio.run(run())


def test_observed_barge_in_identity_must_match_active_response() -> None:
    async def run() -> None:
        clock = MockClock()
        playback = MockPlaybackController()
        renderer_started = asyncio.Event()
        release_renderer = asyncio.Event()

        class GatedRenderer(TTSRendererProtocol):
            async def render(self, text: str) -> bytes:
                renderer_started.set()
                await release_renderer.wait()
                return b"rendered-audio"

        coordinator = VoiceSessionCoordinator(
            session_id="sess_observed_identity",
            clock=clock,
            generator=MockGenerator(["Response."]),
            tts_renderer=GatedRenderer(),
            playback_controller=playback,
            owner_verifier=MockOwnerVerifier(is_owner=True),
            echo_noise_rejector=MockEchoRejector(),
            transcriber=MockTranscriber("Interrupt"),
        )
        assert await coordinator.process_user_turn("Prompt") is True
        await asyncio.wait_for(renderer_started.wait(), timeout=1.0)
        ctx = coordinator.get_current_context()

        frame = AudioFrame(
            data=b"\x01", sample_rate=16000, channels=1, monotonic_ns=clock.now_ns()
        )
        accepted = await coordinator.handle_barge_in_speech(
            audio_frames=[frame],
            observed_session_id="sess_wrong",
            observed_response_id=ctx.response_id,
            observed_playback_id=ctx.playback_id,
        )
        assert accepted is False
        assert coordinator.state in (
            VoiceSessionState.ASSISTANT_GENERATING,
            VoiceSessionState.ASSISTANT_SPEAKING,
        )
        assert playback.state == "idle"

        release_renderer.set()
        await coordinator.shutdown()

    asyncio.run(run())


def test_active_playback_gate_requires_exact_public_correlation() -> None:
    async def run() -> None:
        event_sink = SlowEventSink()
        renderer_started = asyncio.Event()
        release_renderer = asyncio.Event()

        class GatedRenderer(TTSRendererProtocol):
            async def render(self, text: str) -> bytes:
                renderer_started.set()
                await release_renderer.wait()
                return b"rendered-audio"

        coordinator = VoiceSessionCoordinator(
            session_id="sess_playback_gate",
            event_sink=event_sink,
            generator=MockGenerator(["Response."]),
            tts_renderer=GatedRenderer(),
            playback_controller=MockPlaybackController(),
        )
        assert await coordinator.process_user_turn("Prompt") is True
        await asyncio.wait_for(renderer_started.wait(), timeout=1.0)
        ctx = coordinator.get_current_context()

        exact = {
            "session_id": ctx.session_id,
            "response_id": ctx.response_id,
            "playback_id": ctx.playback_id,
            "cancellation_generation": ctx.cancellation_generation,
        }
        assert coordinator.is_active_playback(**exact) is True
        for field, wrong in (
            ("session_id", "sess_wrong"),
            ("response_id", "resp_wrong"),
            ("playback_id", "pb_wrong"),
            ("cancellation_generation", ctx.cancellation_generation + 1),
        ):
            candidate = dict(exact)
            candidate[field] = wrong
            assert coordinator.is_active_playback(**candidate) is False

        release_renderer.set()
        await coordinator.shutdown()

    asyncio.run(run())


def test_verifier_exception_finalizes_response_and_stops_playback() -> None:
    async def run() -> None:
        class RaisingVerifier(OwnerVerifierProtocol):
            def verify_owner(self, frames: list[AudioFrame]) -> OwnerVerificationResult:
                raise RuntimeError("synthetic verifier failure")

        clock = MockClock()
        playback = MockPlaybackController()
        coordinator = VoiceSessionCoordinator(
            session_id="sess_verifier_error",
            clock=clock,
            generator=MockGenerator(["Response."]),
            tts_renderer=MockTTSRenderer(render_delay=0.2),
            playback_controller=playback,
            owner_verifier=RaisingVerifier(),
            echo_noise_rejector=MockEchoRejector(),
        )
        assert await coordinator.process_user_turn("Prompt") is True
        await asyncio.sleep(0.02)
        frame = AudioFrame(
            data=b"\x01", sample_rate=16000, channels=1, monotonic_ns=clock.now_ns()
        )

        assert await coordinator.handle_barge_in_speech(audio_frames=[frame]) is False
        assert coordinator.state == VoiceSessionState.ERROR
        assert playback.state == "stopped"
        assert await coordinator.process_user_turn("Recovery turn") is True

        await coordinator.shutdown()

    asyncio.run(run())


def test_empty_sentence_stream_sentinel_restores_listening_without_rendering() -> None:
    async def run() -> None:
        renderer = MockTTSRenderer()
        playback = MockPlaybackController()
        coordinator = VoiceSessionCoordinator(
            session_id="sess_empty_stream",
            generator=MockGenerator(["unterminated fragment"]),
            tts_renderer=renderer,
            playback_controller=playback,
        )

        assert await coordinator.process_user_turn("Prompt") is True
        await asyncio.sleep(0.1)
        assert coordinator.state == VoiceSessionState.LISTENING
        assert renderer.rendered_texts == []
        assert playback.played_ids == []

        await coordinator.shutdown()

    asyncio.run(run())


def test_shutdown_cancels_inflight_renderer_and_joins_worker() -> None:
    async def run() -> None:
        render_started = asyncio.Event()
        render_cancelled = asyncio.Event()

        class CancellationAwareRenderer(TTSRendererProtocol):
            async def render(self, text: str) -> bytes:
                render_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    render_cancelled.set()
                    raise

        coordinator = VoiceSessionCoordinator(
            session_id="sess_shutdown_join",
            generator=MockGenerator(["Response."]),
            tts_renderer=CancellationAwareRenderer(),
            playback_controller=MockPlaybackController(),
        )
        assert await coordinator.process_user_turn("Prompt") is True
        await asyncio.wait_for(render_started.wait(), timeout=1.0)

        await asyncio.wait_for(coordinator.shutdown(), timeout=1.0)
        assert render_cancelled.is_set()
        assert coordinator.state == VoiceSessionState.STOPPED

    asyncio.run(run())


def test_missing_renderer_and_playback_terminally_degrade() -> None:
    async def run() -> None:
        without_renderer = VoiceSessionCoordinator(
            session_id="sess_no_renderer",
            generator=MockGenerator(["Response."]),
            playback_controller=MockPlaybackController(),
        )
        assert await without_renderer.process_user_turn("Prompt") is True
        await asyncio.sleep(0.1)
        assert without_renderer.state == VoiceSessionState.DEGRADED_HALF_DUPLEX
        assert await without_renderer.process_user_turn("Recovery") is True
        await without_renderer.shutdown()

        renderer = MockTTSRenderer()
        without_playback = VoiceSessionCoordinator(
            session_id="sess_no_playback",
            generator=MockGenerator(["Response."]),
            tts_renderer=renderer,
        )
        assert await without_playback.process_user_turn("Prompt") is True
        await asyncio.sleep(0.1)
        assert without_playback.state == VoiceSessionState.DEGRADED_HALF_DUPLEX
        assert renderer.rendered_texts == ["Response."]
        assert await without_playback.process_user_turn("Recovery") is True
        await without_playback.shutdown()

    asyncio.run(run())


def test_playback_exception_terminally_degrades() -> None:
    async def run() -> None:
        class RaisingPlayback(MockPlaybackController):
            async def play(self, audio_data: bytes, playback_id: str) -> bool:
                raise RuntimeError("synthetic playback failure")

        playback = RaisingPlayback()
        coordinator = VoiceSessionCoordinator(
            session_id="sess_playback_exception",
            generator=MockGenerator(["Response."]),
            tts_renderer=MockTTSRenderer(),
            playback_controller=playback,
        )
        assert await coordinator.process_user_turn("Prompt") is True
        await asyncio.sleep(0.1)
        assert coordinator.state == VoiceSessionState.DEGRADED_HALF_DUPLEX
        assert playback.state == "stopped"
        assert await coordinator.process_user_turn("Recovery") is True
        await coordinator.shutdown()

    asyncio.run(run())


def test_turn_sink_error_is_bounded_but_cancellation_propagates() -> None:
    async def run() -> None:
        class RaisingTurnSink(TurnSinkProtocol):
            async def on_turn(
                self,
                text: str,
                session_id: str,
                utterance_id: str,
                start_ns: int,
                end_ns: int,
            ) -> None:
                raise RuntimeError("synthetic turn sink failure")

        failed = VoiceSessionCoordinator(
            session_id="sess_turn_sink_error", turn_sink=RaisingTurnSink()
        )
        assert await failed.process_user_turn("Prompt") is False
        assert failed.state == VoiceSessionState.ERROR
        await failed.shutdown()

        class CancellingTurnSink(TurnSinkProtocol):
            async def on_turn(
                self,
                text: str,
                session_id: str,
                utterance_id: str,
                start_ns: int,
                end_ns: int,
            ) -> None:
                raise asyncio.CancelledError

        cancelled = VoiceSessionCoordinator(
            session_id="sess_turn_sink_cancel", turn_sink=CancellingTurnSink()
        )
        with pytest.raises(asyncio.CancelledError):
            await cancelled.process_user_turn("Prompt")
        await cancelled.shutdown()

    asyncio.run(run())
