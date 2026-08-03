"""Streaming local TTS pipeline with sentence boundary chunking and speakability filtering.

Ensures no partial words or unterminated trailing text fragments are emitted, filters unsafe
content (tools, code, secrets), enforces queue backpressure, and unblocks cleanly on cancellation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import threading
from typing import AsyncIterable, Callable, List, Optional

from core.voice_session.cancellation import CancellationTracker
from core.voice_session.contracts import (
    SessionContext,
    TTSRendererProtocol,
    validate_generation,
    validate_playback_id,
    validate_response_id,
    validate_sequence,
    validate_session_id,
)

_SECRET_PATTERNS = (
    re.compile(r"sk-[a-zA-Z0-9]{16,}", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*=", re.IGNORECASE),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"password\s*=", re.IGNORECASE),
    re.compile(r"secret\s*=", re.IGNORECASE),
)

_TOOL_ENVELOPE_PATTERNS = (
    re.compile(r"<tool_call>", re.IGNORECASE),
    re.compile(r"</tool_call>", re.IGNORECASE),
    re.compile(r"\{\s*\"action\"\s*:", re.IGNORECASE),
    re.compile(r"\{\s*\"tool\"\s*:", re.IGNORECASE),
)

_MAX_CHUNK_CHAR_LEN = 500
_MAX_BUFFER_CHAR_LEN = 1000


def default_speakability_filter(text: str) -> bool:
    """Filter out unspeakable text (tools, code, secrets, over-bound content)."""
    if not isinstance(text, str):
        return False
    cleaned = text.strip()
    if not cleaned:
        return False

    if len(cleaned) > _MAX_CHUNK_CHAR_LEN:
        return False

    # Code fences check
    if "```" in cleaned:
        return False

    # Tool envelope check
    for pat in _TOOL_ENVELOPE_PATTERNS:
        if pat.search(cleaned):
            return False

    # Secret check
    for pat in _SECRET_PATTERNS:
        if pat.search(cleaned):
            return False

    return True


def split_into_sentences(text: str) -> tuple[List[str], str]:
    """Split string into complete sentence chunks and a remainder buffer.

    Sentences must end with '.', '!', '?', or newline.
    """
    if not text:
        return [], ""

    pattern = re.compile(r"([^.!?\n]+[.!?\n]+)")
    matches = pattern.findall(text)

    if not matches:
        return [], text

    matched_len = sum(len(m) for m in matches)
    remainder = text[matched_len:]
    sentences = [m.strip() for m in matches if m.strip()]

    return sentences, remainder


@dataclass(frozen=True, repr=False)
class TTSChunk:
    """Sentence-bounded TTS chunk for rendering."""

    chunk_id: str
    session_id: str
    response_id: str
    playback_id: str
    cancellation_generation: int
    text: str
    sequence_number: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunk_id", validate_session_id(self.chunk_id))
        object.__setattr__(self, "session_id", validate_session_id(self.session_id))
        object.__setattr__(self, "response_id", validate_response_id(self.response_id))
        object.__setattr__(self, "playback_id", validate_playback_id(self.playback_id))
        object.__setattr__(
            self, "cancellation_generation", validate_generation(self.cancellation_generation)
        )
        object.__setattr__(self, "sequence_number", validate_sequence(self.sequence_number))
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be non-empty string")

    def __repr__(self) -> str:
        return (
            f"<TTSChunk id={self.chunk_id!r} seq={self.sequence_number} "
            f"gen={self.cancellation_generation} len={len(self.text)}>"
        )


class TTSPipeline:
    """Streaming TTS pipeline with sentence boundary enforcement and queue backpressure safety."""

    def __init__(
        self,
        *,
        cancellation_tracker: CancellationTracker,
        renderer: Optional[TTSRendererProtocol] = None,
        speakability_filter: Callable[[str], bool] = default_speakability_filter,
        queue_maxsize: int = 10,
    ) -> None:
        self._cancellation_tracker = cancellation_tracker
        self._renderer = renderer
        self._speakability_filter = speakability_filter
        self._queue: asyncio.Queue[Optional[TTSChunk]] = asyncio.Queue(maxsize=queue_maxsize)
        self._lock = threading.Lock()
        self._is_shutdown = False

    def clear(self) -> None:
        """Clear all pending chunks in the queue."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Exception:
                break

    def shutdown(self) -> None:
        with self._lock:
            self._is_shutdown = True
        self.clear()

    async def _safe_put_chunk(self, chunk: Optional[TTSChunk], gen: int) -> bool:
        """Put item into bounded queue without deadlocking on cancellation/shutdown."""
        while True:
            with self._lock:
                if self._is_shutdown:
                    return False
            if self._cancellation_tracker.is_stale(gen):
                return False
            try:
                await asyncio.wait_for(self._queue.put(chunk), timeout=0.05)
                return True
            except asyncio.TimeoutError:
                continue

    async def enqueue_stream(
        self, text_stream: AsyncIterable[str], ctx: SessionContext
    ) -> int:
        """Consume LLM text stream, sentence-chunk it, filter speakability, and queue.

        Do NOT speak unterminated trailing text fragments without sentence boundaries.
        Bound pending text buffer length to prevent punctuation-free memory growth.
        """
        if not isinstance(ctx, SessionContext):
            raise TypeError("ctx must be a SessionContext")

        buffer = ""
        seq = 0
        chunks_queued = 0

        async for fragment in text_stream:
            with self._lock:
                if self._is_shutdown:
                    break
            if self._cancellation_tracker.is_stale(ctx.cancellation_generation):
                break

            buffer += fragment
            sentences, remainder = split_into_sentences(buffer)
            buffer = remainder

            # Enforce max pending buffer size for punctuation-free stream
            if len(buffer) > _MAX_BUFFER_CHAR_LEN:
                buffer = ""

            for sentence in sentences:
                if self._cancellation_tracker.is_stale(ctx.cancellation_generation):
                    break

                if self._speakability_filter(sentence):
                    seq += 1
                    chunk = TTSChunk(
                        chunk_id=f"chunk_{ctx.response_id}_{seq}",
                        session_id=ctx.session_id,
                        response_id=ctx.response_id,
                        playback_id=ctx.playback_id,
                        cancellation_generation=ctx.cancellation_generation,
                        text=sentence,
                        sequence_number=seq,
                    )
                    put_ok = await self._safe_put_chunk(chunk, ctx.cancellation_generation)
                    if not put_ok:
                        return chunks_queued
                    chunks_queued += 1

        # Sentinel to signal end of stream (incomplete trailing text buffer is dropped)
        await self._safe_put_chunk(None, ctx.cancellation_generation)
        return chunks_queued

    async def get_next_chunk(self) -> Optional[TTSChunk]:
        """Pop next valid non-stale chunk from queue."""
        while True:
            with self._lock:
                if self._is_shutdown:
                    return None
            try:
                chunk = await asyncio.wait_for(self._queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                continue

            if chunk is None:
                return None
            if self._cancellation_tracker.is_stale(chunk.cancellation_generation):
                continue
            return chunk

    async def render_full_response_fallback(
        self, full_text: str, ctx: SessionContext
    ) -> List[TTSChunk]:
        """Half-duplex fallback interface rendering non-streaming full response."""
        if not isinstance(ctx, SessionContext):
            raise TypeError("ctx must be a SessionContext")
        if self._cancellation_tracker.is_stale(ctx.cancellation_generation):
            return []

        sentences, remainder = split_into_sentences(full_text)

        result: List[TTSChunk] = []
        seq = 0
        for s in sentences:
            if self._speakability_filter(s):
                seq += 1
                chunk = TTSChunk(
                    chunk_id=f"fallback_{ctx.response_id}_{seq}",
                    session_id=ctx.session_id,
                    response_id=ctx.response_id,
                    playback_id=ctx.playback_id,
                    cancellation_generation=ctx.cancellation_generation,
                    text=s,
                    sequence_number=seq,
                )
                result.append(chunk)
        return result


__all__ = [
    "TTSChunk",
    "TTSPipeline",
    "default_speakability_filter",
    "split_into_sentences",
]
