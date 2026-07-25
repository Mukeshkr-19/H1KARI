"""Bounded in-memory streaming transcript accumulator."""

from __future__ import annotations

from typing import List, Optional, Tuple

from core.voice_streaming.contracts import (
    CaptionRole,
    FinalTranscript,
    InterimTranscript,
    validate_stream_id,
)


class StreamingTranscriptAccumulator:
    """Bounded in-memory transcript accumulator for streaming voice sessions."""

    def __init__(self, stream_id: str, *, max_segments: int = 100) -> None:
        self.stream_id = validate_stream_id(stream_id)
        if isinstance(max_segments, bool) or not isinstance(max_segments, int) or max_segments <= 0:
            raise ValueError("max_segments must be a positive integer")
        self.max_segments = max_segments
        self._segments: List[FinalTranscript] = []
        self._current_interim: Optional[InterimTranscript] = None
        self._last_timestamp_ns: int = 0

    @property
    def current_interim(self) -> Optional[InterimTranscript]:
        return self._current_interim

    def update_interim(self, interim: InterimTranscript) -> bool:
        """Update or revise the current interim transcript.

        Fails closed on cross-session updates or backward timestamps.
        """
        if not isinstance(interim, InterimTranscript):
            return False
        if interim.stream_id != self.stream_id:
            return False
        if interim.monotonic_ns < self._last_timestamp_ns:
            return False
        self._current_interim = interim
        self._last_timestamp_ns = interim.monotonic_ns
        return True

    def add_final(self, final: FinalTranscript) -> bool:
        """Add an immutable final transcript segment.

        Fails closed on cross-session updates, backward timestamps, or invalid segments.
        """
        if not isinstance(final, FinalTranscript):
            return False
        if final.stream_id != self.stream_id:
            return False
        if final.end_monotonic_ns < self._last_timestamp_ns:
            return False
        if final.end_monotonic_ns < final.start_monotonic_ns:
            return False

        self._segments.append(final)
        if len(self._segments) > self.max_segments:
            self._segments.pop(0)

        self._last_timestamp_ns = final.end_monotonic_ns
        self._current_interim = None
        return True

    def get_segments(self) -> Tuple[FinalTranscript, ...]:
        """Return all stored final segments as an immutable tuple."""
        return tuple(self._segments)

    def get_recent_segments(self, count: int = 10) -> Tuple[FinalTranscript, ...]:
        """Return bounded recent final segments."""
        if count <= 0:
            return ()
        return tuple(self._segments[-count:])

    def user_segments(self) -> Tuple[FinalTranscript, ...]:
        """Return final segments authored by the user."""
        return tuple(seg for seg in self._segments if seg.role == "user")

    def assistant_segments(self) -> Tuple[FinalTranscript, ...]:
        """Return final segments authored by the assistant."""
        return tuple(seg for seg in self._segments if seg.role == "assistant")

    def get_unconsolidated_segments(
        self, since_ns: int = 0
    ) -> Tuple[FinalTranscript, ...]:
        """Return timestamped segments since a given monotonic timestamp for Brain consolidation."""
        return tuple(seg for seg in self._segments if seg.start_monotonic_ns >= since_ns)

    def clear_interim(self) -> None:
        self._current_interim = None

    def reset(self) -> None:
        """Clear all volatile interim and final segments."""
        self._segments.clear()
        self._current_interim = None
        self._last_timestamp_ns = 0
