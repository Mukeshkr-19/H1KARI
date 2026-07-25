"""Tests for frame pipeline metadata, validation, queue bounds, and raw-audio redaction."""

import pytest

from core.voice_streaming.frame_pipeline import (
    AudioFrame,
    AudioFrameMetadata,
    AudioFramePipeline,
    FrameOverflowMode,
    FramePipelineConfig,
)


def test_audio_frame_metadata_validation():
    """Verify frame metadata validations for sample rates, channels, payload size, etc."""
    meta = AudioFrameMetadata(
        stream_id="stream_1",
        sequence_id=0,
        monotonic_ns=1000,
        sample_rate=16000,
        channels=1,
        sample_width=2,
        duration_ms=20.0,
        payload_bytes=640,
    )
    assert meta.stream_id == "stream_1"
    assert meta.sequence_id == 0
    assert meta.monotonic_ns == 1000

    # Invalid sample rate
    with pytest.raises(ValueError, match="Sample rate"):
        AudioFrameMetadata(
            stream_id="stream_1", sequence_id=1, monotonic_ns=2000, sample_rate=12345
        )

    # Invalid channels
    with pytest.raises(ValueError, match="Channels"):
        AudioFrameMetadata(
            stream_id="stream_1", sequence_id=1, monotonic_ns=2000, channels=3
        )

    # Negative sequence ID
    with pytest.raises(ValueError, match="sequence_id"):
        AudioFrameMetadata(
            stream_id="stream_1", sequence_id=-1, monotonic_ns=2000
        )


def test_audio_frame_raw_data_redaction():
    """Verify raw audio bytes are redacted from __repr__ and string conversions."""
    raw_bytes = b"\x00\x01" * 320
    meta = AudioFrameMetadata(
        stream_id="stream_1",
        sequence_id=0,
        monotonic_ns=1000,
        payload_bytes=640,
    )
    frame = AudioFrame(metadata=meta, payload=raw_bytes)

    repr_str = repr(frame)
    assert "stream_1" in repr_str
    assert "bytes=640" in repr_str
    # Ensure raw byte content is NOT echoed in string representation
    assert str(raw_bytes) not in repr_str
    assert "b'\\x00\\x01'" not in repr_str


def test_audio_frame_payload_size_mismatch():
    """Verify mismatch between payload length and metadata payload_bytes raises error."""
    meta = AudioFrameMetadata(
        stream_id="stream_1", sequence_id=0, monotonic_ns=1000, payload_bytes=640
    )
    with pytest.raises(ValueError, match="Payload size"):
        AudioFrame(metadata=meta, payload=b"\x00" * 100)


def test_audio_frame_copies_mutable_payload():
    payload = bytearray(b"\x00" * 640)
    frame = AudioFrame(
        AudioFrameMetadata("stream_1", 0, 1000, payload_bytes=640),
        payload,
    )
    payload[0] = 1
    assert isinstance(frame.payload, bytes)
    assert frame.payload[0] == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_frame_bounds_rejected(value):
    with pytest.raises(ValueError):
        AudioFrameMetadata("stream_1", 0, 1000, duration_ms=value)
    with pytest.raises(ValueError):
        FramePipelineConfig(gap_threshold_ms=value)


def test_pipeline_queue_overflow_modes():
    """Verify queue overflow modes: DROP_OLDEST, DROP_NEWEST, FAIL_CLOSED."""
    # 1. DROP_OLDEST
    cfg_oldest = FramePipelineConfig(max_queue_size=2, overflow_mode=FrameOverflowMode.DROP_OLDEST)
    pipeline = AudioFramePipeline("s1", config=cfg_oldest)

    payload = b"\x00" * 640
    f0 = AudioFrame(AudioFrameMetadata("s1", 0, 100, payload_bytes=640), payload)
    f1 = AudioFrame(AudioFrameMetadata("s1", 1, 200, payload_bytes=640), payload)
    f2 = AudioFrame(AudioFrameMetadata("s1", 2, 300, payload_bytes=640), payload)

    assert pipeline.push_frame(f0)[0] is True
    assert pipeline.push_frame(f1)[0] is True
    assert pipeline.push_frame(f2)[0] is True  # Drops f0

    assert pipeline.pending_count == 2
    popped = pipeline.pop_frame()
    assert popped is not None
    assert popped.metadata.sequence_id == 1  # f0 was dropped

    # 2. DROP_NEWEST
    cfg_newest = FramePipelineConfig(max_queue_size=2, overflow_mode=FrameOverflowMode.DROP_NEWEST)
    pipeline_n = AudioFramePipeline("s2", config=cfg_newest)

    f0_n = AudioFrame(AudioFrameMetadata("s2", 0, 100, payload_bytes=640), payload)
    f1_n = AudioFrame(AudioFrameMetadata("s2", 1, 200, payload_bytes=640), payload)
    f2_n = AudioFrame(AudioFrameMetadata("s2", 2, 300, payload_bytes=640), payload)

    assert pipeline_n.push_frame(f0_n)[0] is True
    assert pipeline_n.push_frame(f1_n)[0] is True
    ok, reason = pipeline_n.push_frame(f2_n)
    assert ok is False
    assert reason == "queue_overflow_drop_newest"

    popped_n = pipeline_n.pop_frame()
    assert popped_n is not None
    assert popped_n.metadata.sequence_id == 0

    # 3. FAIL_CLOSED
    cfg_fc = FramePipelineConfig(max_queue_size=1, overflow_mode=FrameOverflowMode.FAIL_CLOSED)
    pipeline_fc = AudioFramePipeline("s3", config=cfg_fc)

    assert pipeline_fc.push_frame(AudioFrame(AudioFrameMetadata("s3", 0, 100, payload_bytes=640), payload))[0] is True
    ok_fc, reason_fc = pipeline_fc.push_frame(AudioFrame(AudioFrameMetadata("s3", 1, 200, payload_bytes=640), payload))
    assert ok_fc is False
    assert reason_fc == "queue_overflow_fail_closed"


def test_pipeline_discontinuity_and_ordering():
    """Verify sequence gaps and out-of-order / duplicate frame handling."""
    pipeline = AudioFramePipeline("stream_gap", config=FramePipelineConfig(gap_threshold_ms=30.0))
    payload = b"\x00" * 640

    # Push sequence 0
    f0 = AudioFrame(AudioFrameMetadata("stream_gap", 0, 100_000_000, duration_ms=20.0, payload_bytes=640), payload)
    assert pipeline.push_frame(f0)[0] is True

    # Duplicate sequence 0 rejected
    f0_dup = AudioFrame(AudioFrameMetadata("stream_gap", 0, 120_000_000, duration_ms=20.0, payload_bytes=640), payload)
    ok_dup, reason_dup = pipeline.push_frame(f0_dup)
    assert ok_dup is False
    assert reason_dup == "out_of_order_or_duplicate"

    # Push sequence 5 (gap of 4 frames)
    f5 = AudioFrame(AudioFrameMetadata("stream_gap", 5, 500_000_000, duration_ms=20.0, payload_bytes=640), payload)
    assert pipeline.push_frame(f5)[0] is True

    disc_events = pipeline.get_discontinuity_events()
    assert len(disc_events) == 1
    assert disc_events[0].expected_sequence_id == 1
    assert disc_events[0].actual_sequence_id == 5


def test_pipeline_stream_closure_and_eos():
    """Verify End-Of-Stream marker closes the pipeline."""
    pipeline = AudioFramePipeline("stream_eos")
    payload = b"\x00" * 640

    eos_frame = AudioFrame(
        AudioFrameMetadata("stream_eos", 0, 100, is_end_of_stream=True, payload_bytes=640),
        payload,
    )
    assert pipeline.push_frame(eos_frame)[0] is True
    assert pipeline.is_closed is True

    # Pushing after close fails
    next_frame = AudioFrame(
        AudioFrameMetadata("stream_eos", 1, 200, payload_bytes=640), payload
    )
    ok, reason = pipeline.push_frame(next_frame)
    assert ok is False
    assert reason == "pipeline_closed"


def test_pipeline_metrics():
    """Verify operational metrics tracking."""
    pipeline = AudioFramePipeline("stream_metrics")
    payload = b"\x00" * 640
    frame = AudioFrame(AudioFrameMetadata("stream_metrics", 0, 100, duration_ms=20.0, payload_bytes=640), payload)

    pipeline.push_frame(frame)
    pipeline.pop_frame()

    metrics = pipeline.get_metrics()
    assert metrics.frames_received == 1
    assert metrics.frames_processed == 1
    assert metrics.total_bytes_processed == 640
    assert metrics.total_duration_ms == 20.0

    pipeline.reset()
    reset_metrics = pipeline.get_metrics()
    assert reset_metrics.frames_received == 0
    assert reset_metrics.frames_processed == 0
    assert reset_metrics.total_bytes_processed == 0
