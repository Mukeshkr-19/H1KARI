"""Bounded, deterministic frame pipeline for streaming audio metadata and buffers."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, List, Optional, Tuple

from core.voice_streaming.contracts import validate_monotonic_ns, validate_stream_id


class FrameOverflowMode(str, Enum):
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    FAIL_CLOSED = "fail_closed"


ALLOWED_SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000, 96000)
ALLOWED_CHANNELS = (1, 2, 4, 8)
ALLOWED_SAMPLE_WIDTHS = (1, 2, 3, 4)


def validate_sample_rate(rate: int) -> int:
    if isinstance(rate, bool) or not isinstance(rate, int):
        raise TypeError("Sample rate must be an integer")
    if rate not in ALLOWED_SAMPLE_RATES:
        raise ValueError(f"Sample rate {rate} not in allowed set {ALLOWED_SAMPLE_RATES}")
    return rate


def validate_channels(channels: int) -> int:
    if isinstance(channels, bool) or not isinstance(channels, int):
        raise TypeError("Channels must be an integer")
    if channels not in ALLOWED_CHANNELS:
        raise ValueError(f"Channels {channels} not in allowed set {ALLOWED_CHANNELS}")
    return channels


def validate_sample_width(width: int) -> int:
    if isinstance(width, bool) or not isinstance(width, int):
        raise TypeError("Sample width must be an integer")
    if width not in ALLOWED_SAMPLE_WIDTHS:
        raise ValueError(f"Sample width {width} not in allowed set {ALLOWED_SAMPLE_WIDTHS}")
    return width


@dataclass(frozen=True)
class AudioFrameMetadata:
    """Immutable metadata for a single timestamped audio frame."""

    stream_id: str
    sequence_id: int
    monotonic_ns: int
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2
    duration_ms: float = 20.0
    payload_bytes: int = 640
    is_end_of_stream: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        object.__setattr__(self, "sample_rate", validate_sample_rate(self.sample_rate))
        object.__setattr__(self, "channels", validate_channels(self.channels))
        object.__setattr__(self, "sample_width", validate_sample_width(self.sample_width))

        if isinstance(self.sequence_id, bool) or not isinstance(self.sequence_id, int) or self.sequence_id < 0:
            raise ValueError("sequence_id must be a non-negative integer")

        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, (int, float)) or not math.isfinite(self.duration_ms) or self.duration_ms <= 0:
            raise ValueError("duration_ms must be a positive number")

        if isinstance(self.payload_bytes, bool) or not isinstance(self.payload_bytes, int) or self.payload_bytes <= 0:
            raise ValueError("payload_bytes must be a positive integer")

        if not isinstance(self.is_end_of_stream, bool):
            raise TypeError("is_end_of_stream must be a boolean")


@dataclass(frozen=True)
class AudioFrame:
    """Timestamped audio frame container.

    Raw payload bytes are excluded from __repr__ to ensure zero audio data leaks into logs.
    """

    metadata: AudioFrameMetadata
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, AudioFrameMetadata):
            raise TypeError("metadata must be AudioFrameMetadata")
        if not isinstance(self.payload, (bytes, bytearray)):
            raise TypeError("Payload must be bytes")
        if isinstance(self.payload, bytearray):
            object.__setattr__(self, "payload", bytes(self.payload))
        if len(self.payload) != self.metadata.payload_bytes:
            raise ValueError(
                f"Payload size {len(self.payload)} does not match metadata {self.metadata.payload_bytes}"
            )

    def __repr__(self) -> str:
        return (
            f"AudioFrame(stream_id={self.metadata.stream_id!r}, "
            f"seq={self.metadata.sequence_id}, "
            f"ns={self.metadata.monotonic_ns}, "
            f"bytes={len(self.payload)}, "
            f"eos={self.metadata.is_end_of_stream})"
        )


@dataclass(frozen=True)
class FramePipelineConfig:
    """Configuration for frame pipeline queue and validation bounds."""

    max_queue_size: int = 100
    max_frame_duration_ms: float = 500.0
    max_payload_bytes: int = 65536
    overflow_mode: FrameOverflowMode = FrameOverflowMode.DROP_OLDEST
    gap_threshold_ms: float = 50.0

    def __post_init__(self) -> None:
        if isinstance(self.max_queue_size, bool) or not isinstance(self.max_queue_size, int) or self.max_queue_size <= 0:
            raise ValueError("max_queue_size must be a positive integer")
        if isinstance(self.max_frame_duration_ms, bool) or not isinstance(self.max_frame_duration_ms, (int, float)) or not math.isfinite(self.max_frame_duration_ms) or self.max_frame_duration_ms <= 0:
            raise ValueError("max_frame_duration_ms must be a positive number")
        if isinstance(self.max_payload_bytes, bool) or not isinstance(self.max_payload_bytes, int) or self.max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be a positive integer")
        if not isinstance(self.overflow_mode, FrameOverflowMode):
            raise ValueError("overflow_mode must be a FrameOverflowMode")
        if isinstance(self.gap_threshold_ms, bool) or not isinstance(self.gap_threshold_ms, (int, float)) or not math.isfinite(self.gap_threshold_ms) or self.gap_threshold_ms < 0:
            raise ValueError("gap_threshold_ms must be a non-negative finite number")


@dataclass(frozen=True)
class FrameDiscontinuityEvent:
    """Event emitted when a frame sequence gap or timestamp discontinuity occurs."""

    stream_id: str
    expected_sequence_id: int
    actual_sequence_id: int
    gap_ms: float
    monotonic_ns: int


@dataclass(frozen=True)
class FramePipelineMetrics:
    """Privacy-safe operational metrics for audio frame pipeline."""

    stream_id: str
    frames_received: int
    frames_processed: int
    frames_dropped_overflow: int
    frames_dropped_out_of_order: int
    frames_dropped_invalid: int
    discontinuities_detected: int
    total_bytes_processed: int
    total_duration_ms: float
    is_closed: bool


class AudioFramePipeline:
    """Bounded, deterministic frame queue and pipeline processor."""

    def __init__(
        self,
        stream_id: str,
        config: Optional[FramePipelineConfig] = None,
        clock: Optional[Callable[[], int]] = None,
    ) -> None:
        self.stream_id = validate_stream_id(stream_id)
        self.config = config or FramePipelineConfig()
        self._clock = clock or (lambda: 0)

        self._queue: Deque[AudioFrame] = deque()
        self._is_closed: bool = False

        self._last_sequence_id: Optional[int] = None
        self._last_monotonic_ns: Optional[int] = None
        self._last_frame_duration_ms: float = 0.0

        # Metrics
        self._frames_received: int = 0
        self._frames_processed: int = 0
        self._frames_dropped_overflow: int = 0
        self._frames_dropped_out_of_order: int = 0
        self._frames_dropped_invalid: int = 0
        self._discontinuities_detected: int = 0
        self._total_bytes_processed: int = 0
        self._total_duration_ms: float = 0.0

        self._discontinuity_events: List[FrameDiscontinuityEvent] = []

    @property
    def is_closed(self) -> bool:
        return self._is_closed

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    def get_discontinuity_events(self) -> Tuple[FrameDiscontinuityEvent, ...]:
        return tuple(self._discontinuity_events)

    def push_frame(self, frame: AudioFrame) -> Tuple[bool, Optional[str]]:
        """Push a timestamped frame into the pipeline queue.

        Validates bounds, sequence ordering, timestamp continuity, and queue size.
        Returns (success: bool, reason: Optional[str]).
        """
        if self._is_closed:
            self._frames_dropped_invalid += 1
            return False, "pipeline_closed"

        if not isinstance(frame, AudioFrame):
            self._frames_dropped_invalid += 1
            return False, "invalid_frame_type"

        meta = frame.metadata
        if meta.stream_id != self.stream_id:
            self._frames_dropped_invalid += 1
            return False, "stream_id_mismatch"

        # Frame duration and payload bounds validation
        if meta.duration_ms > self.config.max_frame_duration_ms:
            self._frames_dropped_invalid += 1
            return False, "frame_duration_exceeded"

        if meta.payload_bytes > self.config.max_payload_bytes:
            self._frames_dropped_invalid += 1
            return False, "payload_size_exceeded"

        self._frames_received += 1

        # Out-of-order or duplicate frame detection
        if self._last_sequence_id is not None:
            if meta.sequence_id <= self._last_sequence_id:
                self._frames_dropped_out_of_order += 1
                return False, "out_of_order_or_duplicate"
            if self._last_monotonic_ns is not None and meta.monotonic_ns < self._last_monotonic_ns:
                self._frames_dropped_out_of_order += 1
                return False, "backward_timestamp"

            # Discontinuity detection
            expected_seq = self._last_sequence_id + 1
            seq_gap = meta.sequence_id - expected_seq
            time_gap_ms = 0.0
            if self._last_monotonic_ns is not None:
                elapsed_ns = meta.monotonic_ns - self._last_monotonic_ns
                time_gap_ms = (elapsed_ns / 1_000_000.0) - self._last_frame_duration_ms

            if seq_gap > 0 or time_gap_ms > self.config.gap_threshold_ms:
                disc_event = FrameDiscontinuityEvent(
                    stream_id=self.stream_id,
                    expected_sequence_id=expected_seq,
                    actual_sequence_id=meta.sequence_id,
                    gap_ms=max(0.0, time_gap_ms),
                    monotonic_ns=meta.monotonic_ns,
                )
                self._discontinuity_events.append(disc_event)
                self._discontinuities_detected += 1

        # Queue overflow handling
        if len(self._queue) >= self.config.max_queue_size:
            if self.config.overflow_mode == FrameOverflowMode.FAIL_CLOSED:
                self._frames_dropped_overflow += 1
                return False, "queue_overflow_fail_closed"
            elif self.config.overflow_mode == FrameOverflowMode.DROP_NEWEST:
                self._frames_dropped_overflow += 1
                return False, "queue_overflow_drop_newest"
            elif self.config.overflow_mode == FrameOverflowMode.DROP_OLDEST:
                self._queue.popleft()
                self._frames_dropped_overflow += 1

        self._queue.append(frame)
        self._last_sequence_id = meta.sequence_id
        self._last_monotonic_ns = meta.monotonic_ns
        self._last_frame_duration_ms = meta.duration_ms

        if meta.is_end_of_stream:
            self._is_closed = True

        return True, None

    def pop_frame(self) -> Optional[AudioFrame]:
        """Pop the next available frame from the pipeline queue."""
        if not self._queue:
            return None
        frame = self._queue.popleft()
        self._frames_processed += 1
        self._total_bytes_processed += len(frame.payload)
        self._total_duration_ms += frame.metadata.duration_ms
        return frame

    def close(self) -> None:
        """Close the pipeline."""
        self._is_closed = True

    def reset(self) -> None:
        """Reset internal queue state and metrics."""
        self._queue.clear()
        self._is_closed = False
        self._last_sequence_id = None
        self._last_monotonic_ns = None
        self._last_frame_duration_ms = 0.0
        self._discontinuity_events.clear()
        self._frames_received = 0
        self._frames_processed = 0
        self._frames_dropped_overflow = 0
        self._frames_dropped_out_of_order = 0
        self._frames_dropped_invalid = 0
        self._discontinuities_detected = 0
        self._total_bytes_processed = 0
        self._total_duration_ms = 0.0

    def get_metrics(self) -> FramePipelineMetrics:
        """Expose privacy-safe pipeline operational metrics."""
        return FramePipelineMetrics(
            stream_id=self.stream_id,
            frames_received=self._frames_received,
            frames_processed=self._frames_processed,
            frames_dropped_overflow=self._frames_dropped_overflow,
            frames_dropped_out_of_order=self._frames_dropped_out_of_order,
            frames_dropped_invalid=self._frames_dropped_invalid,
            discontinuities_detected=self._discontinuities_detected,
            total_bytes_processed=self._total_bytes_processed,
            total_duration_ms=self._total_duration_ms,
            is_closed=self._is_closed,
        )
