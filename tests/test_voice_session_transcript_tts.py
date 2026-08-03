"""Tests for transcript pipeline and sentence-bounded TTS pipeline."""

from __future__ import annotations

import asyncio
from typing import AsyncIterable, List
import pytest

from core.voice_session.cancellation import CancellationTracker
from core.voice_session.contracts import (
    MonotonicClockProtocol,
    SessionContext,
    StateEventSinkProtocol,
    TurnSinkProtocol,
)
from core.voice_session.events import TranscriptEvent
from core.voice_session.transcript_pipeline import (
    FinalTranscript,
    PartialTranscript,
    TranscriptPipeline,
)
from core.voice_session.tts_pipeline import (
    TTSPipeline,
    default_speakability_filter,
    split_into_sentences,
)


class FakeClock(MonotonicClockProtocol):
    def __init__(self, time_ns: int = 1000) -> None:
        self.time_ns = time_ns

    def now_ns(self) -> int:
        return self.time_ns


class FakeTurnSink(TurnSinkProtocol):
    def __init__(self) -> None:
        self.turns: List[tuple[str, str, str, int, int]] = []

    async def on_turn(
        self, text: str, session_id: str, utterance_id: str, start_ns: int, end_ns: int
    ) -> None:
        self.turns.append((text, session_id, utterance_id, start_ns, end_ns))


class FakeEventSink(StateEventSinkProtocol):
    def __init__(self) -> None:
        self.events: List[object] = []

    async def emit_event(self, event: object) -> None:
        self.events.append(event)


def test_partial_transcript_never_invokes_turn_sink() -> None:
    async def run() -> None:
        clock = FakeClock(1000)
        tracker = CancellationTracker()
        turn_sink = FakeTurnSink()
        event_sink = FakeEventSink()

        pipeline = TranscriptPipeline(
            clock=clock,
            cancellation_tracker=tracker,
            turn_sink=turn_sink,
            event_sink=event_sink,
        )

        ctx = SessionContext(
            session_id="sess_1",
            utterance_id="utt_1",
            response_id="resp_1",
            playback_id="pb_1",
            event_sequence=1,
            cancellation_generation=0,
            monotonic_ns=500,
        )

        partial = PartialTranscript(
            session_id="sess_1",
            utterance_id="utt_1",
            text="Hello world partial",
            monotonic_ns=600,
            sequence_number=1,
        )

        # Privacy check: repr has no text
        assert "Hello world" not in repr(partial)

        res = await pipeline.process_partial(partial, ctx)
        assert res is True

        # Turn sink must NOT have received anything!
        assert len(turn_sink.turns) == 0

        # Event sink received content-free TranscriptEvent
        assert len(event_sink.events) == 1
        evt = event_sink.events[0]
        assert isinstance(evt, TranscriptEvent)
        assert evt.is_final is False
        assert evt.text_length == len("Hello world partial")
        assert "Hello world" not in repr(evt)

    asyncio.run(run())


def test_final_transcript_submitted_exactly_once() -> None:
    async def run() -> None:
        clock = FakeClock(1000)
        tracker = CancellationTracker()
        turn_sink = FakeTurnSink()

        pipeline = TranscriptPipeline(
            clock=clock,
            cancellation_tracker=tracker,
            turn_sink=turn_sink,
        )

        ctx = SessionContext(
            session_id="sess_1",
            utterance_id="utt_1",
            response_id="resp_1",
            playback_id="pb_1",
            event_sequence=1,
            cancellation_generation=0,
            monotonic_ns=500,
        )

        final = FinalTranscript(
            session_id="sess_1",
            utterance_id="utt_1",
            text="Hello world final",
            start_monotonic_ns=600,
            end_monotonic_ns=800,
            confidence=0.98,
        )

        # Privacy check: repr has no raw text
        assert "Hello world" not in repr(final)

        # Submit first time -> success
        res1 = await pipeline.process_final(final, ctx)
        assert res1 is True
        assert len(turn_sink.turns) == 1
        assert turn_sink.turns[0][0] == "Hello world final"

        # Duplicate final submission -> rejected
        res2 = await pipeline.process_final(final, ctx)
        assert res2 is False
        assert len(turn_sink.turns) == 1

    asyncio.run(run())


def test_final_transcript_timestamp_and_stale_validation() -> None:
    async def run() -> None:
        clock = FakeClock(1000)
        tracker = CancellationTracker()
        turn_sink = FakeTurnSink()

        pipeline = TranscriptPipeline(
            clock=clock,
            cancellation_tracker=tracker,
            turn_sink=turn_sink,
        )

        ctx = SessionContext(
            session_id="sess_1",
            utterance_id="utt_1",
            response_id="resp_1",
            playback_id="pb_1",
            event_sequence=1,
            cancellation_generation=0,
            monotonic_ns=500,
        )

        # Future timestamp (end_ns > clock.now_ns=1000) -> rejected
        future_final = FinalTranscript(
            session_id="sess_1",
            utterance_id="utt_1",
            text="Future speech",
            start_monotonic_ns=600,
            end_monotonic_ns=1200,
        )
        assert await pipeline.process_final(future_final, ctx) is False
        assert len(turn_sink.turns) == 0

        # Inverted timestamp (start > end) -> raised at construction
        with pytest.raises(ValueError):
            FinalTranscript(
                session_id="sess_1",
                utterance_id="utt_1",
                text="Inverted timestamps",
                start_monotonic_ns=900,
                end_monotonic_ns=800,
            )

        # Stale generation check
        tracker.cancel("test_cancellation")
        valid_final = FinalTranscript(
            session_id="sess_1",
            utterance_id="utt_1",
            text="Stale generation text",
            start_monotonic_ns=600,
            end_monotonic_ns=800,
        )
        assert await pipeline.process_final(valid_final, ctx) is False
        assert len(turn_sink.turns) == 0

    asyncio.run(run())


def test_sentence_boundary_chunking() -> None:
    text = "Hello there! How are you? I am ready."
    sentences, remainder = split_into_sentences(text)
    assert sentences == ["Hello there!", "How are you?", "I am ready."]
    assert remainder == ""

    partial_text = "This is a partial sentence"
    sentences2, remainder2 = split_into_sentences(partial_text)
    assert sentences2 == []
    assert remainder2 == "This is a partial sentence"


def test_speakability_filter() -> None:
    assert default_speakability_filter("Hello world.") is True
    assert default_speakability_filter("") is False
    assert default_speakability_filter("   ") is False

    # Tool envelopes
    assert default_speakability_filter("<tool_call>search()</tool_call>") is False
    assert default_speakability_filter('{"action": "search"}') is False

    # Code fences
    assert default_speakability_filter("```python\nprint(1)\n```") is False

    # Secrets
    synthetic_api_key = "sk-" + "1234567890abcdef1234"
    assert default_speakability_filter(f"My key is {synthetic_api_key}") is False
    assert default_speakability_filter("api_key=secret123") is False

    # Over-bound content
    assert default_speakability_filter("a" * 600) is False


def test_tts_pipeline_streaming_and_cancellation() -> None:
    async def run() -> None:
        tracker = CancellationTracker()
        pipeline = TTSPipeline(cancellation_tracker=tracker, queue_maxsize=5)

        ctx = SessionContext(
            session_id="sess_1",
            utterance_id="utt_1",
            response_id="resp_1",
            playback_id="pb_1",
            event_sequence=1,
            cancellation_generation=0,
            monotonic_ns=500,
        )

        async def mock_stream() -> AsyncIterable[str]:
            yield "First sentence. "
            yield "Second sentence! "
            yield "<tool_call>drop</tool_call>"

        count = await pipeline.enqueue_stream(mock_stream(), ctx)
        assert count == 2

        c1 = await pipeline.get_next_chunk()
        assert c1 is not None
        assert c1.text == "First sentence."
        # Privacy check: repr hides text
        assert "First sentence" not in repr(c1)

        # Cancel generation epoch
        tracker.cancel("user_interrupt")

        # Next chunk should be skipped due to stale generation epoch!
        c2 = await pipeline.get_next_chunk()
        assert c2 is None

    asyncio.run(run())


def test_over_capacity_queue_unblocks_on_cancellation() -> None:
    """Prove over-capacity queue unblocks immediately on cancellation without deadlock."""
    async def run() -> None:
        tracker = CancellationTracker()
        pipeline = TTSPipeline(cancellation_tracker=tracker, queue_maxsize=2)

        ctx = SessionContext(
            session_id="sess_capacity",
            utterance_id="utt_1",
            response_id="resp_1",
            playback_id="pb_1",
            event_sequence=1,
            cancellation_generation=0,
            monotonic_ns=500,
        )

        async def infinite_sentences() -> AsyncIterable[str]:
            for i in range(100):
                yield f"Sentence number {i}. "

        # Launch enqueue task in background
        task = asyncio.create_task(pipeline.enqueue_stream(infinite_sentences(), ctx))

        # Wait a tiny bit for queue to fill up
        await asyncio.sleep(0.1)

        # Cancel generation epoch
        tracker.cancel("user_cancel")

        # Enqueue stream task should exit quickly within short timeout without deadlocking!
        queued = await asyncio.wait_for(task, timeout=0.5)
        assert queued >= 2

    asyncio.run(run())


def test_incomplete_trailing_tts_text_dropped() -> None:
    """Prove unterminated trailing text is not spoken at end of stream."""
    async def run() -> None:
        tracker = CancellationTracker()
        pipeline = TTSPipeline(cancellation_tracker=tracker, queue_maxsize=5)

        ctx = SessionContext(
            session_id="sess_trailing",
            utterance_id="utt_1",
            response_id="resp_1",
            playback_id="pb_1",
            event_sequence=1,
            cancellation_generation=0,
            monotonic_ns=500,
        )

        async def stream_with_trailing() -> AsyncIterable[str]:
            yield "Complete sentence one. "
            yield "Unterminated trailing text fragment without punctuation"

        count = await pipeline.enqueue_stream(stream_with_trailing(), ctx)
        assert count == 1  # Only complete sentence one is enqueued

        c1 = await pipeline.get_next_chunk()
        assert c1 is not None
        assert c1.text == "Complete sentence one."

        c2 = await pipeline.get_next_chunk()
        assert c2 is None

    asyncio.run(run())
