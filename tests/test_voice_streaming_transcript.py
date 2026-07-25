"""Tests for streaming transcript accumulator."""

import pytest
from dataclasses import FrozenInstanceError

from core.voice_streaming.contracts import FinalTranscript, InterimTranscript
from core.voice_streaming.transcript import StreamingTranscriptAccumulator


def test_interim_revision():
    """Test 18: Interim revision."""
    acc = StreamingTranscriptAccumulator("stream_1")
    assert acc.current_interim is None

    # First interim
    i1 = InterimTranscript(stream_id="stream_1", text="hel", monotonic_ns=100)
    assert acc.update_interim(i1) is True
    assert acc.current_interim == i1

    # Revised interim with more text
    i2 = InterimTranscript(stream_id="stream_1", text="hello world", monotonic_ns=150)
    assert acc.update_interim(i2) is True
    assert acc.current_interim == i2
    assert acc.current_interim.text == "hello world"


def test_final_immutability():
    """Test 19: Final immutability."""
    acc = StreamingTranscriptAccumulator("stream_1")
    f1 = FinalTranscript(
        stream_id="stream_1",
        text="Hello assistant",
        start_monotonic_ns=100,
        end_monotonic_ns=200,
        role="user",
    )
    assert acc.add_final(f1) is True

    # Dataclass is frozen and immutable
    with pytest.raises(FrozenInstanceError):
        f1.text = "Mutated text"  # type: ignore[misc]

    # Returned tuple is immutable
    segments = acc.get_segments()
    assert len(segments) == 1
    assert segments[0] == f1


def test_cross_session_rejection():
    """Test 23: Cross-session rejection."""
    acc = StreamingTranscriptAccumulator("stream_1")

    # Interim with wrong stream ID
    i_wrong = InterimTranscript(stream_id="stream_OTHER", text="hello", monotonic_ns=100)
    assert acc.update_interim(i_wrong) is False
    assert acc.current_interim is None

    # Final with wrong stream ID
    f_wrong = FinalTranscript(
        stream_id="stream_OTHER",
        text="hello world",
        start_monotonic_ns=100,
        end_monotonic_ns=200,
    )
    assert acc.add_final(f_wrong) is False
    assert len(acc.get_segments()) == 0


def test_bounded_transcript_list():
    """Test 24: Bounded transcript list."""
    acc = StreamingTranscriptAccumulator("stream_1", max_segments=3)

    for i in range(5):
        seg = FinalTranscript(
            stream_id="stream_1",
            text=f"Segment {i}",
            start_monotonic_ns=100 + (i * 20),
            end_monotonic_ns=110 + (i * 20),
        )
        assert acc.add_final(seg) is True

    segments = acc.get_segments()
    assert len(segments) == 3
    # Oldest segments 0 and 1 dropped
    assert segments[0].text == "Segment 2"
    assert segments[1].text == "Segment 3"
    assert segments[2].text == "Segment 4"


def test_user_assistant_role_separation():
    """Test 25: User/assistant role separation."""
    acc = StreamingTranscriptAccumulator("stream_1")

    user_seg = FinalTranscript(
        stream_id="stream_1",
        text="What time is it?",
        start_monotonic_ns=100,
        end_monotonic_ns=200,
        role="user",
    )
    assistant_seg = FinalTranscript(
        stream_id="stream_1",
        text="It is 9:00 PM.",
        start_monotonic_ns=210,
        end_monotonic_ns=300,
        role="assistant",
    )

    acc.add_final(user_seg)
    acc.add_final(assistant_seg)

    user_segs = acc.user_segments()
    assistant_segs = acc.assistant_segments()

    assert len(user_segs) == 1
    assert user_segs[0].text == "What time is it?"
    assert user_segs[0].role == "user"

    assert len(assistant_segs) == 1
    assert assistant_segs[0].text == "It is 9:00 PM."
    assert assistant_segs[0].role == "assistant"

    # Cannot convert assistant output to user role
    with pytest.raises(FrozenInstanceError):
        assistant_segs[0].role = "user"  # type: ignore[misc]


def test_unconsolidated_segments_for_brain():
    acc = StreamingTranscriptAccumulator("stream_1")
    s1 = FinalTranscript("stream_1", "One", 100, 150)
    s2 = FinalTranscript("stream_1", "Two", 200, 250)
    acc.add_final(s1)
    acc.add_final(s2)

    unconsolidated = acc.get_unconsolidated_segments(since_ns=180)
    assert len(unconsolidated) == 1
    assert unconsolidated[0].text == "Two"


def test_transcript_accumulator_reset():
    acc = StreamingTranscriptAccumulator("stream_1")
    acc.update_interim(InterimTranscript("stream_1", "interim", 100))
    acc.add_final(FinalTranscript("stream_1", "final", 100, 200))

    assert acc.current_interim is None
    assert len(acc.get_segments()) == 1

    acc.reset()
    assert acc.current_interim is None
    assert len(acc.get_segments()) == 0
