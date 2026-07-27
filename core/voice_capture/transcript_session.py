"""Bounded local transcript session. Partials never orchestrate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
from typing import Callable, Optional, Protocol


class TranscriptSessionReason(StrEnum):
    OK = "ok"
    DUPLICATE_FINAL = "duplicate_final"
    STALE = "stale"
    CANCELLED = "cancelled"
    EMPTY = "empty"
    FILLER = "filler"
    LOW_CONFIDENCE = "low_confidence"
    BOUND_EXCEEDED = "bound_exceeded"
    UNAVAILABLE = "unavailable"
    INVALID_INPUT = "invalid_input"


_FILLER = frozenset({"", "um", "uh", "hmm", "mm", "mhm", "ah", "...", "."})


@dataclass(frozen=True, repr=False)
class TranscriptEvent:
    is_final: bool
    text: str
    confidence: float
    utterance_id: str
    stream_id: str
    reason: TranscriptSessionReason = TranscriptSessionReason.OK
    ask_clarification: bool = False

    def __repr__(self) -> str:
        return (
            f"TranscriptEvent(final={self.is_final}, reason={self.reason.value!r}, "
            f"chars={len(self.text)}, clarify={self.ask_clarification})"
        )


class LocalTranscriber(Protocol):
    def transcribe_pcm16(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16_000,
        short_utterance: bool = False,
    ) -> tuple[str, float]: ...


class BoundedTranscriptSession:
    """One utterance correlation: at most one final; partials optional."""

    def __init__(
        self,
        *,
        stream_id: str,
        utterance_id: str,
        transcriber: Optional[LocalTranscriber] = None,
        max_chars: int = 4_000,
        low_confidence_threshold: float = 0.45,
        partials_supported: bool = False,
        generation: int = 1,
    ) -> None:
        for name, value in (("stream_id", stream_id), ("utterance_id", utterance_id)):
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None
            ):
                raise ValueError(f"invalid_{name}")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1 <= max_chars <= 16_000:
            raise ValueError("invalid_max_chars")
        if (
            isinstance(low_confidence_threshold, bool)
            or not isinstance(low_confidence_threshold, (int, float))
            or not math.isfinite(float(low_confidence_threshold))
            or not 0.0 <= float(low_confidence_threshold) <= 1.0
        ):
            raise ValueError("invalid_low_confidence_threshold")
        if type(partials_supported) is not bool:
            raise ValueError("invalid_partials_supported")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError("invalid_generation")
        self.stream_id = stream_id
        self.utterance_id = utterance_id
        self._transcriber = transcriber
        self._max_chars = max_chars
        self._low_conf = low_confidence_threshold
        self.partials_supported = partials_supported
        self._generation = generation
        self._finalized = False
        self._cancelled_generation = 0
        self._last_partial = ""

    def cancel(self) -> None:
        self._cancelled_generation = self._generation

    def on_partial(self, text: str) -> Optional[TranscriptEvent]:
        if not self.partials_supported:
            return None
        if self._cancelled_generation == self._generation or self._finalized:
            return None
        if not isinstance(text, str):
            return None
        cleaned = " ".join(text.split())[: self._max_chars]
        if cleaned == self._last_partial:
            return None
        self._last_partial = cleaned
        return TranscriptEvent(
            is_final=False,
            text=cleaned,
            confidence=0.0,
            utterance_id=self.utterance_id,
            stream_id=self.stream_id,
            reason=TranscriptSessionReason.OK,
        )

    def finalize_pcm(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16_000,
        short_utterance: bool = False,
        expected_generation: Optional[int] = None,
    ) -> TranscriptEvent:
        if expected_generation is not None and expected_generation != self._generation:
            return self._event("", 0.0, TranscriptSessionReason.STALE)
        if self._cancelled_generation == self._generation:
            return self._event("", 0.0, TranscriptSessionReason.CANCELLED)
        if self._finalized:
            return self._event("", 0.0, TranscriptSessionReason.DUPLICATE_FINAL)
        if self._transcriber is None:
            self._finalized = True
            return self._event("", 0.0, TranscriptSessionReason.UNAVAILABLE)
        if (
            not isinstance(pcm, (bytes, bytearray))
            or not pcm
            or len(pcm) % 2 != 0
            or len(pcm) > 1_920_000
            or sample_rate != 16_000
            or type(short_utterance) is not bool
        ):
            self._finalized = True
            return self._event("", 0.0, TranscriptSessionReason.INVALID_INPUT)
        try:
            text, confidence = self._transcriber.transcribe_pcm16(
                bytes(pcm), sample_rate=sample_rate, short_utterance=short_utterance
            )
        except Exception:
            self._finalized = True
            return self._event("", 0.0, TranscriptSessionReason.UNAVAILABLE)
        if not isinstance(text, str):
            self._finalized = True
            return self._event("", 0.0, TranscriptSessionReason.UNAVAILABLE)
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            self._finalized = True
            return self._event("", 0.0, TranscriptSessionReason.UNAVAILABLE)
        confidence = float(confidence)
        text = " ".join(text.split())
        if len(text) > self._max_chars:
            self._finalized = True
            return self._event(text[: self._max_chars], confidence, TranscriptSessionReason.BOUND_EXCEEDED)
        normalized = text.casefold().strip(" .,!?")
        if normalized in _FILLER:
            self._finalized = True
            return self._event(text, confidence, TranscriptSessionReason.FILLER)
        if not text:
            self._finalized = True
            return self._event("", confidence, TranscriptSessionReason.EMPTY)
        if confidence < self._low_conf:
            self._finalized = True
            return self._event(
                text,
                confidence,
                TranscriptSessionReason.LOW_CONFIDENCE,
                ask_clarification=True,
            )
        self._finalized = True
        return self._event(text, confidence, TranscriptSessionReason.OK)

    def _event(
        self,
        text: str,
        confidence: float,
        reason: TranscriptSessionReason,
        *,
        ask_clarification: bool = False,
    ) -> TranscriptEvent:
        return TranscriptEvent(
            is_final=True,
            text=text,
            confidence=confidence,
            utterance_id=self.utterance_id,
            stream_id=self.stream_id,
            reason=reason,
            ask_clarification=ask_clarification,
        )
