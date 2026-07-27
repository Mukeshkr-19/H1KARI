"""Injected live audio frame source contracts.

Importing this module performs no microphone access. Default construction is
unavailable. Clocks and backends are injected. Raw PCM never appears in repr,
exceptions, events, or snapshots.

Replay guarantee
----------------
Seen frame IDs are tracked in a bounded deque+set. When capacity is reached,
further frames are rejected with BOUND_EXCEEDED until the loop is reset/closed
for a new stream/session. IDs are never silently evicted mid-session, so an
old frame cannot be replayed within the active security window.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, Deque, Optional, Protocol, runtime_checkable

from core.voice_streaming.contracts import validate_monotonic_ns, validate_stream_id
from core.voice_streaming.frame_pipeline import (
    ALLOWED_CHANNELS,
    ALLOWED_SAMPLE_RATES,
    ALLOWED_SAMPLE_WIDTHS,
)


class AudioInputCapability(StrEnum):
    UNAVAILABLE = "unavailable"
    UTTERANCE_ONLY = "utterance_only"
    FRAME_STREAM = "frame_stream"


class CaptureSourceCategory(StrEnum):
    UNKNOWN = "unknown"
    MICROPHONE = "microphone"
    LOOPBACK = "loopback"
    SYNTHETIC = "synthetic"
    UNAVAILABLE = "unavailable"


class AudioFrameSourceReason(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    NOT_OPEN = "not_open"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    INVALID_FRAME = "invalid_frame"
    DUPLICATE_FRAME = "duplicate_frame"
    OUT_OF_ORDER = "out_of_order"
    STALE_TIMESTAMP = "stale_timestamp"
    FUTURE_TIMESTAMP = "future_timestamp"
    CROSS_STREAM = "cross_stream"
    QUEUE_EXHAUSTED = "queue_exhausted"
    FRAME_TOO_LARGE = "frame_too_large"
    BACKPRESSURE_DROP = "backpressure_drop"
    HARDWARE_ERROR = "hardware_error"
    TIMEOUT = "timeout"
    BOUND_EXCEEDED = "bound_exceeded"
    INVALID_CONFIG = "invalid_config"


# Conservative hard maximums for VoiceAudioLoopConfig
_HARD_MAX_QUEUE_DEPTH = 256
_HARD_MAX_BUFFERED_BYTES = 4_194_304
_HARD_MAX_FRAME_BYTES = 65_536
_HARD_MAX_ELAPSED_MS = 600_000
_HARD_MAX_CONSECUTIVE_FAILURES = 64
_HARD_MAX_SEEN_FRAME_IDS = 8_192
_HARD_MAX_FUTURE_SKEW_NS = 5_000_000_000
_HARD_MAX_STALE_SKEW_NS = 120_000_000_000


def _require_positive_int(value: object, name: str, *, hard_max: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid_{name}")
    if value < 1 or value > hard_max:
        raise ValueError(f"invalid_{name}")
    return value


@dataclass(frozen=True, repr=False)
class LiveAudioFrame:
    """One bounded PCM frame. Payload and correlation IDs are excluded from repr."""

    stream_id: str
    frame_id: str
    sequence: int
    monotonic_ns: int
    sample_rate: int
    channels: int
    sample_width: int
    pcm: bytes = field(repr=False)
    capture_source: CaptureSourceCategory = CaptureSourceCategory.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        if not isinstance(self.frame_id, str) or not self.frame_id or len(self.frame_id) > 128:
            raise ValueError("invalid_frame_id")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in self.frame_id):
            raise ValueError("invalid_frame_id")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("invalid_sequence")
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        if self.sample_rate not in ALLOWED_SAMPLE_RATES:
            raise ValueError("invalid_sample_rate")
        if self.channels not in ALLOWED_CHANNELS:
            raise ValueError("invalid_channels")
        if self.sample_width not in ALLOWED_SAMPLE_WIDTHS:
            raise ValueError("invalid_sample_width")
        if not isinstance(self.pcm, (bytes, bytearray)):
            raise TypeError("pcm_must_be_bytes")
        pcm = bytes(self.pcm)
        if len(pcm) < 1 or len(pcm) > _HARD_MAX_FRAME_BYTES:
            raise ValueError("invalid_pcm_size")
        if len(pcm) % (self.channels * self.sample_width) != 0:
            raise ValueError("invalid_pcm_alignment")
        object.__setattr__(self, "pcm", pcm)
        if not isinstance(self.capture_source, CaptureSourceCategory):
            raise ValueError("invalid_capture_source")

    def __repr__(self) -> str:
        return f"LiveAudioFrame(bytes={len(self.pcm)})"


@dataclass(frozen=True, repr=False)
class AudioFrameSourceResult:
    accepted: bool
    reason: AudioFrameSourceReason
    frame: Optional[LiveAudioFrame] = None

    def __repr__(self) -> str:
        return (
            f"AudioFrameSourceResult(accepted={self.accepted}, "
            f"reason={self.reason.value!r}, has_frame={self.frame is not None})"
        )


@runtime_checkable
class AudioFrameSource(Protocol):
    def open(self) -> AudioFrameSourceResult:
        ...

    def read_frame(self) -> AudioFrameSourceResult:
        ...

    def cancel(self) -> AudioFrameSourceResult:
        ...

    def close(self) -> AudioFrameSourceResult:
        ...

    @property
    def capability(self) -> AudioInputCapability:
        ...


@dataclass(frozen=True)
class VoiceAudioLoopConfig:
    max_queue_depth: int = 32
    max_buffered_bytes: int = 262_144
    max_frame_bytes: int = 65_536
    max_elapsed_ms: int = 120_000
    max_consecutive_failures: int = 8
    max_seen_frame_ids: int = 4096
    future_skew_ns: int = 1_000_000_000
    stale_skew_ns: int = 30_000_000_000

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_queue_depth",
            _require_positive_int(self.max_queue_depth, "max_queue_depth", hard_max=_HARD_MAX_QUEUE_DEPTH),
        )
        object.__setattr__(
            self,
            "max_buffered_bytes",
            _require_positive_int(
                self.max_buffered_bytes, "max_buffered_bytes", hard_max=_HARD_MAX_BUFFERED_BYTES
            ),
        )
        object.__setattr__(
            self,
            "max_frame_bytes",
            _require_positive_int(self.max_frame_bytes, "max_frame_bytes", hard_max=_HARD_MAX_FRAME_BYTES),
        )
        object.__setattr__(
            self,
            "max_elapsed_ms",
            _require_positive_int(self.max_elapsed_ms, "max_elapsed_ms", hard_max=_HARD_MAX_ELAPSED_MS),
        )
        object.__setattr__(
            self,
            "max_consecutive_failures",
            _require_positive_int(
                self.max_consecutive_failures,
                "max_consecutive_failures",
                hard_max=_HARD_MAX_CONSECUTIVE_FAILURES,
            ),
        )
        object.__setattr__(
            self,
            "max_seen_frame_ids",
            _require_positive_int(
                self.max_seen_frame_ids, "max_seen_frame_ids", hard_max=_HARD_MAX_SEEN_FRAME_IDS
            ),
        )
        object.__setattr__(
            self,
            "future_skew_ns",
            _require_positive_int(
                self.future_skew_ns, "future_skew_ns", hard_max=_HARD_MAX_FUTURE_SKEW_NS
            ),
        )
        object.__setattr__(
            self,
            "stale_skew_ns",
            _require_positive_int(self.stale_skew_ns, "stale_skew_ns", hard_max=_HARD_MAX_STALE_SKEW_NS),
        )
        if self.max_frame_bytes > self.max_buffered_bytes:
            raise ValueError("invalid_frame_vs_buffer")
        if self.max_queue_depth > self.max_seen_frame_ids:
            raise ValueError("invalid_queue_vs_replay")
        if self.future_skew_ns > self.stale_skew_ns:
            raise ValueError("invalid_skew_relationship")


@dataclass(frozen=True, repr=False)
class VoiceAudioLoopSnapshot:
    capability: AudioInputCapability
    open: bool
    cancelled: bool
    closed: bool
    queued_frames: int
    buffered_bytes: int
    frames_accepted: int
    frames_dropped: int
    consecutive_failures: int
    last_reason: AudioFrameSourceReason
    replay_exhausted: bool = False

    def __repr__(self) -> str:
        return (
            f"VoiceAudioLoopSnapshot(capability={self.capability.value!r}, "
            f"open={self.open}, cancelled={self.cancelled}, closed={self.closed}, "
            f"queued={self.queued_frames}, dropped={self.frames_dropped}, "
            f"replay_exhausted={self.replay_exhausted})"
        )


class UnavailableAudioFrameSource:
    """Default source: honest unavailable, no hardware access."""

    def __init__(self) -> None:
        self._closed = False

    @property
    def capability(self) -> AudioInputCapability:
        return AudioInputCapability.UNAVAILABLE

    def open(self) -> AudioFrameSourceResult:
        return AudioFrameSourceResult(False, AudioFrameSourceReason.UNAVAILABLE)

    def read_frame(self) -> AudioFrameSourceResult:
        return AudioFrameSourceResult(False, AudioFrameSourceReason.UNAVAILABLE)

    def cancel(self) -> AudioFrameSourceResult:
        return AudioFrameSourceResult(True, AudioFrameSourceReason.CANCELLED)

    def close(self) -> AudioFrameSourceResult:
        self._closed = True
        return AudioFrameSourceResult(True, AudioFrameSourceReason.CLOSED)


class SyntheticAudioFrameSource:
    """Injected in-memory source for contract tests. No microphone."""

    def __init__(self, frames: list[LiveAudioFrame], *, stream_id: str) -> None:
        self._stream_id = validate_stream_id(stream_id)
        self._frames: Deque[LiveAudioFrame] = deque(frames)
        self._open = False
        self._cancelled = False
        self._closed = False

    @property
    def capability(self) -> AudioInputCapability:
        return AudioInputCapability.FRAME_STREAM

    def open(self) -> AudioFrameSourceResult:
        if self._closed:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.CLOSED)
        if self._cancelled:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.CANCELLED)
        self._open = True
        return AudioFrameSourceResult(True, AudioFrameSourceReason.OK)

    def read_frame(self) -> AudioFrameSourceResult:
        if self._closed:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.CLOSED)
        if self._cancelled:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.CANCELLED)
        if not self._open:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.NOT_OPEN)
        if not self._frames:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.TIMEOUT)
        frame = self._frames.popleft()
        if frame.stream_id != self._stream_id:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.CROSS_STREAM)
        return AudioFrameSourceResult(True, AudioFrameSourceReason.OK, frame)

    def cancel(self) -> AudioFrameSourceResult:
        self._cancelled = True
        self._frames.clear()
        return AudioFrameSourceResult(True, AudioFrameSourceReason.CANCELLED)

    def close(self) -> AudioFrameSourceResult:
        self._closed = True
        self._open = False
        self._frames.clear()
        return AudioFrameSourceResult(True, AudioFrameSourceReason.CLOSED)


def try_create_pyaudio_source(*, stream_id: str) -> AudioFrameSource:
    """Lazy optional PyAudio probe. Always unavailable without explicit injection."""
    try:
        import pyaudio  # noqa: F401
    except Exception:
        return UnavailableAudioFrameSource()
    # Package presence is not hardware evidence. Remain unavailable.
    return UnavailableAudioFrameSource()


class VoiceAudioLoop:
    """Bounded live-audio controller with deterministic backpressure."""

    def __init__(
        self,
        stream_id: str,
        *,
        source: Optional[AudioFrameSource] = None,
        clock: Optional[Callable[[], int]] = None,
        config: Optional[VoiceAudioLoopConfig] = None,
    ) -> None:
        self.stream_id = validate_stream_id(stream_id)
        self._source: AudioFrameSource = source or UnavailableAudioFrameSource()
        self._clock = clock or (lambda: 0)
        self._config = config or VoiceAudioLoopConfig()
        self._queue: Deque[LiveAudioFrame] = deque()
        self._buffered_bytes = 0
        self._open = False
        self._cancelled = False
        self._closed = False
        self._seen_order: Deque[str] = deque()
        self._seen_ids: set[str] = set()
        self._replay_exhausted = False
        self._last_seq = -1
        self._last_ns = -1
        self._started_ns = 0
        self._frames_accepted = 0
        self._frames_dropped = 0
        self._consecutive_failures = 0
        self._last_reason = AudioFrameSourceReason.UNAVAILABLE

    @property
    def capability(self) -> AudioInputCapability:
        return self._source.capability

    def snapshot(self) -> VoiceAudioLoopSnapshot:
        return VoiceAudioLoopSnapshot(
            capability=self.capability,
            open=self._open,
            cancelled=self._cancelled,
            closed=self._closed,
            queued_frames=len(self._queue),
            buffered_bytes=self._buffered_bytes,
            frames_accepted=self._frames_accepted,
            frames_dropped=self._frames_dropped,
            consecutive_failures=self._consecutive_failures,
            last_reason=self._last_reason,
            replay_exhausted=self._replay_exhausted,
        )

    def open(self) -> AudioFrameSourceResult:
        if self._closed:
            return self._fail(AudioFrameSourceReason.CLOSED, count_drop=False)
        if self._cancelled:
            return self._fail(AudioFrameSourceReason.CANCELLED, count_drop=False)
        try:
            result = self._source.open()
        except Exception:
            return self._fail(AudioFrameSourceReason.HARDWARE_ERROR)
        self._last_reason = result.reason
        if not result.accepted:
            self._consecutive_failures += 1
            return result
        self._open = True
        self._started_ns = int(self._clock())
        self._consecutive_failures = 0
        return result

    def cancel(self) -> AudioFrameSourceResult:
        self._cancelled = True
        self._queue.clear()
        self._buffered_bytes = 0
        try:
            self._source.cancel()
        except Exception:
            pass
        self._last_reason = AudioFrameSourceReason.CANCELLED
        return AudioFrameSourceResult(True, AudioFrameSourceReason.CANCELLED)

    def close(self) -> AudioFrameSourceResult:
        self._closed = True
        self._open = False
        self._queue.clear()
        self._buffered_bytes = 0
        self._seen_ids.clear()
        self._seen_order.clear()
        self._replay_exhausted = False
        try:
            self._source.close()
        except Exception:
            pass
        self._last_reason = AudioFrameSourceReason.CLOSED
        return AudioFrameSourceResult(True, AudioFrameSourceReason.CLOSED)

    def pull(self) -> AudioFrameSourceResult:
        if self._closed:
            return self._fail(AudioFrameSourceReason.CLOSED, count_drop=False)
        if self._cancelled:
            return self._fail(AudioFrameSourceReason.CANCELLED, count_drop=False)
        if not self._open:
            return self._fail(AudioFrameSourceReason.NOT_OPEN, count_drop=False)

        now = int(self._clock())
        if self._started_ns and (now - self._started_ns) > self._config.max_elapsed_ms * 1_000_000:
            return self._fail(AudioFrameSourceReason.BOUND_EXCEEDED)
        if self._consecutive_failures >= self._config.max_consecutive_failures:
            return self._fail(AudioFrameSourceReason.HARDWARE_ERROR)

        try:
            raw = self._source.read_frame()
        except Exception:
            return self._fail(AudioFrameSourceReason.HARDWARE_ERROR)
        if not raw.accepted or raw.frame is None:
            self._consecutive_failures += 1
            self._last_reason = raw.reason
            return raw

        validated = self._validate_and_enqueue(raw.frame, now=now)
        if not validated.accepted:
            return validated
        return self.dequeue()

    def enqueue_injected(self, frame: LiveAudioFrame) -> AudioFrameSourceResult:
        if self._closed:
            return self._fail(AudioFrameSourceReason.CLOSED, count_drop=False)
        if self._cancelled:
            return self._fail(AudioFrameSourceReason.CANCELLED, count_drop=False)
        now = int(self._clock())
        return self._validate_and_enqueue(frame, now=now)

    def dequeue(self) -> AudioFrameSourceResult:
        if self._cancelled:
            return self._fail(AudioFrameSourceReason.CANCELLED, count_drop=False)
        if not self._queue:
            return AudioFrameSourceResult(False, AudioFrameSourceReason.TIMEOUT)
        frame = self._queue.popleft()
        self._buffered_bytes = max(0, self._buffered_bytes - len(frame.pcm))
        self._last_reason = AudioFrameSourceReason.OK
        return AudioFrameSourceResult(True, AudioFrameSourceReason.OK, frame)

    def _validate_and_enqueue(self, frame: LiveAudioFrame, *, now: int) -> AudioFrameSourceResult:
        if not isinstance(frame, LiveAudioFrame):
            return self._fail(AudioFrameSourceReason.INVALID_FRAME)
        if frame.stream_id != self.stream_id:
            return self._fail(AudioFrameSourceReason.CROSS_STREAM)
        if len(frame.pcm) > self._config.max_frame_bytes:
            return self._fail(AudioFrameSourceReason.FRAME_TOO_LARGE)

        # Time checks against injected now — including first frame.
        if frame.monotonic_ns > now + self._config.future_skew_ns:
            return self._fail(AudioFrameSourceReason.FUTURE_TIMESTAMP)
        if now - frame.monotonic_ns > self._config.stale_skew_ns:
            return self._fail(AudioFrameSourceReason.STALE_TIMESTAMP)

        if frame.frame_id in self._seen_ids:
            return self._fail(AudioFrameSourceReason.DUPLICATE_FRAME)
        if self._last_seq >= 0 and frame.sequence <= self._last_seq:
            return self._fail(AudioFrameSourceReason.OUT_OF_ORDER)
        if self._last_ns >= 0 and frame.monotonic_ns < self._last_ns:
            return self._fail(AudioFrameSourceReason.OUT_OF_ORDER)

        if self._replay_exhausted or len(self._seen_ids) >= self._config.max_seen_frame_ids:
            self._replay_exhausted = True
            return self._fail(AudioFrameSourceReason.BOUND_EXCEEDED)

        while (
            len(self._queue) >= self._config.max_queue_depth
            or self._buffered_bytes + len(frame.pcm) > self._config.max_buffered_bytes
        ):
            if not self._queue:
                return self._fail(AudioFrameSourceReason.QUEUE_EXHAUSTED)
            dropped = self._queue.popleft()
            self._buffered_bytes = max(0, self._buffered_bytes - len(dropped.pcm))
            self._frames_dropped += 1
            self._last_reason = AudioFrameSourceReason.BACKPRESSURE_DROP

        # Accept only after all checks — advance canonical sequence state here.
        self._seen_ids.add(frame.frame_id)
        self._seen_order.append(frame.frame_id)
        self._last_seq = frame.sequence
        self._last_ns = frame.monotonic_ns
        self._queue.append(frame)
        self._buffered_bytes += len(frame.pcm)
        self._frames_accepted += 1
        self._consecutive_failures = 0
        self._last_reason = AudioFrameSourceReason.OK
        return AudioFrameSourceResult(True, AudioFrameSourceReason.OK, frame)

    def _fail(
        self, reason: AudioFrameSourceReason, *, count_drop: bool = True
    ) -> AudioFrameSourceResult:
        self._last_reason = reason
        if count_drop and reason not in (AudioFrameSourceReason.TIMEOUT, AudioFrameSourceReason.OK):
            self._frames_dropped += 1
        return AudioFrameSourceResult(False, reason)


__all__ = [
    "AudioFrameSource",
    "AudioFrameSourceReason",
    "AudioFrameSourceResult",
    "AudioInputCapability",
    "CaptureSourceCategory",
    "LiveAudioFrame",
    "SyntheticAudioFrameSource",
    "UnavailableAudioFrameSource",
    "VoiceAudioLoop",
    "VoiceAudioLoopConfig",
    "VoiceAudioLoopSnapshot",
    "try_create_pyaudio_source",
]
