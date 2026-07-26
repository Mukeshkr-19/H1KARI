"""Bounded buffering and deterministic backpressure for streaming voice."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Generic, Iterable, Optional, TypeVar

from .contracts import StreamingDecision, StreamingReason, TranscriptSegment, validate_mono

T = TypeVar("T")


@dataclass(frozen=True)
class BufferLimits:
    max_frames: int = 64
    max_segments: int = 32
    max_bytes: int = 65_536
    max_hold_ms: int = 5_000

    def __post_init__(self) -> None:
        for name in ("max_frames", "max_segments", "max_bytes", "max_hold_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"invalid_{name}")


@dataclass(frozen=True, repr=False)
class LatencySummary:
    count: int
    dropped: int
    oldest_age_ms: int
    newest_age_ms: int
    byte_estimate: int

    def __repr__(self) -> str:
        return f"LatencySummary(count={self.count}, dropped={self.dropped})"


@dataclass(frozen=True)
class _Buffered:
    item: object
    enqueued_mono: float
    byte_estimate: int
    key: str


class BoundedVoiceBuffer(Generic[T]):
    """Hard-capped queue with deterministic drop-oldest policy."""

    def __init__(self, limits: Optional[BufferLimits] = None) -> None:
        self._limits = limits or BufferLimits()
        self._items: Deque[_Buffered] = deque()
        self._bytes = 0
        self._dropped = 0
        self._keys: set[str] = set()

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def size(self) -> int:
        return len(self._items)

    def summary(self, now_mono: float) -> LatencySummary:
        now = validate_mono(now_mono, "now_mono")
        if not self._items:
            return LatencySummary(0, self._dropped, 0, 0, 0)
        oldest = int((now - self._items[0].enqueued_mono) * 1000)
        newest = int((now - self._items[-1].enqueued_mono) * 1000)
        return LatencySummary(len(self._items), self._dropped, max(0, oldest), max(0, newest), self._bytes)

    def push(
        self,
        item: T,
        *,
        key: str,
        enqueued_mono: float,
        byte_estimate: int,
    ) -> StreamingDecision:
        if not isinstance(key, str) or not key:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT)
        if key in self._keys:
            return StreamingDecision(False, StreamingReason.DUPLICATE)
        mono = validate_mono(enqueued_mono, "enqueued_mono")
        if isinstance(byte_estimate, bool) or not isinstance(byte_estimate, int) or byte_estimate < 0:
            return StreamingDecision(False, StreamingReason.INVALID_INPUT)
        if byte_estimate > self._limits.max_bytes:
            self._dropped += 1
            return StreamingDecision(False, StreamingReason.BUFFER_EXHAUSTED)

        # Drop expired first
        self._drop_expired(mono)

        while self._needs_drop(byte_estimate) and self._items:
            self._drop_oldest()
        if self._needs_drop(byte_estimate):
            self._dropped += 1
            return StreamingDecision(False, StreamingReason.BUFFER_EXHAUSTED)

        self._items.append(_Buffered(item, mono, byte_estimate, key))
        self._keys.add(key)
        self._bytes += byte_estimate
        return StreamingDecision(True, StreamingReason.OK)

    def pop(self) -> Optional[T]:
        if not self._items:
            return None
        buf = self._items.popleft()
        self._keys.discard(buf.key)
        self._bytes = max(0, self._bytes - buf.byte_estimate)
        return buf.item  # type: ignore[return-value]

    def drain(self) -> Iterable[T]:
        while self._items:
            item = self.pop()
            if item is not None:
                yield item

    def _needs_drop(self, incoming_bytes: int) -> bool:
        return (
            len(self._items) >= self._limits.max_frames
            or self._bytes + incoming_bytes > self._limits.max_bytes
        )

    def _drop_oldest(self) -> None:
        buf = self._items.popleft()
        self._keys.discard(buf.key)
        self._bytes = max(0, self._bytes - buf.byte_estimate)
        self._dropped += 1

    def _drop_expired(self, now_mono: float) -> None:
        hold_s = self._limits.max_hold_ms / 1000.0
        while self._items and (now_mono - self._items[0].enqueued_mono) > hold_s:
            self._drop_oldest()


class SegmentLedger:
    """Accepts transcript segments with ordering, duplicate, and replay rejection."""

    def __init__(self, *, max_segments: int = 256) -> None:
        if isinstance(max_segments, bool) or not isinstance(max_segments, int) or max_segments < 1:
            raise ValueError("invalid_max_segments")
        self._max = max_segments
        self._by_id: dict[str, TranscriptSegment] = {}
        self._order: list[str] = []
        self._last_seq = -1

    def accept(self, segment: TranscriptSegment) -> StreamingDecision:
        if not isinstance(segment, TranscriptSegment):
            return StreamingDecision(False, StreamingReason.INVALID_INPUT)
        if segment.segment_id in self._by_id:
            return StreamingDecision(False, StreamingReason.DUPLICATE)
        if self._last_seq >= 0 and segment.sequence < self._last_seq:
            return StreamingDecision(False, StreamingReason.OUT_OF_ORDER)
        if self._last_seq >= 0 and segment.sequence == self._last_seq:
            return StreamingDecision(False, StreamingReason.REPLAYED)
        if len(self._order) >= self._max:
            return StreamingDecision(False, StreamingReason.BUFFER_EXHAUSTED)
        self._by_id[segment.segment_id] = segment
        self._order.append(segment.segment_id)
        self._last_seq = segment.sequence
        return StreamingDecision(True, StreamingReason.OK)

    def ordered(self) -> tuple[TranscriptSegment, ...]:
        return tuple(self._by_id[sid] for sid in self._order)


__all__ = [
    "BoundedVoiceBuffer",
    "BufferLimits",
    "LatencySummary",
    "SegmentLedger",
]
