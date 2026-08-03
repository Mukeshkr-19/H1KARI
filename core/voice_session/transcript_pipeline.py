"""Transcript processing pipeline for partial and final transcription events.

Partials are replaceable caption events and never invoke the turn sink. Finals carry real
monotonic start/end timestamps, undergo owner verification and echo/noise rejection, and are
submitted to the turn sink exactly once per utterance.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Awaitable, Callable, Optional, Set

from core.voice_session.cancellation import CancellationTracker
from core.voice_session.contracts import (
    EchoNoiseRejectorProtocol,
    MonotonicClockProtocol,
    OwnerVerifierProtocol,
    SessionContext,
    StateEventSinkProtocol,
    TurnSinkProtocol,
    validate_monotonic_ns,
    validate_sequence,
    validate_session_id,
    validate_utterance_id,
)
from core.voice_session.events import TranscriptEvent, VoiceSessionEvent


def _sanitize_transcript_text(text: object) -> str:
    if not isinstance(text, str):
        raise TypeError("Transcript text must be a string")
    cleaned = "".join(
        ch for ch in text if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    ).strip()
    if not cleaned:
        raise ValueError("Transcript text cannot be empty or whitespace-only")
    return cleaned


@dataclass(frozen=True, repr=False)
class PartialTranscript:
    """Bounded, timestamped partial caption event."""

    session_id: str
    utterance_id: str
    text: str
    monotonic_ns: int
    sequence_number: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", validate_session_id(self.session_id))
        object.__setattr__(self, "utterance_id", validate_utterance_id(self.utterance_id))
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))
        object.__setattr__(self, "sequence_number", validate_sequence(self.sequence_number))
        object.__setattr__(self, "text", _sanitize_transcript_text(self.text))

    def __repr__(self) -> str:
        return (
            f"<PartialTranscript seq={self.sequence_number} "
            f"len={len(self.text)} ns={self.monotonic_ns}>"
        )


@dataclass(frozen=True, repr=False)
class FinalTranscript:
    """Immutable final transcript carrying speech timestamps."""

    session_id: str
    utterance_id: str
    text: str
    start_monotonic_ns: int
    end_monotonic_ns: int
    confidence: float = 1.0
    sequence_number: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", validate_session_id(self.session_id))
        object.__setattr__(self, "utterance_id", validate_utterance_id(self.utterance_id))
        object.__setattr__(
            self, "start_monotonic_ns", validate_monotonic_ns(self.start_monotonic_ns)
        )
        object.__setattr__(
            self, "end_monotonic_ns", validate_monotonic_ns(self.end_monotonic_ns)
        )
        if self.end_monotonic_ns < self.start_monotonic_ns:
            raise ValueError("end_monotonic_ns cannot be earlier than start_monotonic_ns")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence must be a float")
        conf = float(self.confidence)
        if not (0.0 <= conf <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", conf)
        object.__setattr__(self, "sequence_number", validate_sequence(self.sequence_number))
        object.__setattr__(self, "text", _sanitize_transcript_text(self.text))

    def __repr__(self) -> str:
        return (
            f"<FinalTranscript seq={self.sequence_number} "
            f"start={self.start_monotonic_ns} end={self.end_monotonic_ns} conf={self.confidence:.2f}>"
        )


class TranscriptPipeline:
    """Injected pipeline filtering and submitting validated final transcripts."""

    def __init__(
        self,
        *,
        clock: MonotonicClockProtocol,
        cancellation_tracker: CancellationTracker,
        turn_sink: Optional[TurnSinkProtocol] = None,
        event_sink: Optional[StateEventSinkProtocol] = None,
        owner_verifier: Optional[OwnerVerifierProtocol] = None,
        echo_noise_rejector: Optional[EchoNoiseRejectorProtocol] = None,
        sequence_allocator: Optional[Callable[[], int]] = None,
        emitter_fn: Optional[
            Callable[[Callable[[int], VoiceSessionEvent]], Awaitable[Optional[VoiceSessionEvent]]]
        ] = None,
    ) -> None:
        self._clock = clock
        self._cancellation_tracker = cancellation_tracker
        self._turn_sink = turn_sink
        self._event_sink = event_sink
        self._owner_verifier = owner_verifier
        self._echo_noise_rejector = echo_noise_rejector
        self._sequence_allocator = sequence_allocator
        self._emitter_fn = emitter_fn
        self._lock = threading.Lock()
        self._submitted_utterances: Set[str] = set()
        self._is_shutdown = False

    def shutdown(self) -> None:
        with self._lock:
            self._is_shutdown = True

    async def process_partial(
        self, partial: PartialTranscript, ctx: SessionContext
    ) -> bool:
        """Process partial caption transcript event. NEVER submits to turn sink."""
        if not isinstance(partial, PartialTranscript):
            raise TypeError("partial must be a PartialTranscript")
        if not isinstance(ctx, SessionContext):
            raise TypeError("ctx must be a SessionContext")

        with self._lock:
            if self._is_shutdown:
                return False

        if partial.session_id != ctx.session_id:
            return False

        if self._cancellation_tracker.is_stale(ctx.cancellation_generation):
            return False

        if self._emitter_fn is not None:
            await self._emitter_fn(
                lambda seq: TranscriptEvent(
                    session_id=ctx.session_id,
                    utterance_id=partial.utterance_id,
                    response_id=ctx.response_id,
                    playback_id=ctx.playback_id,
                    event_sequence=seq,
                    monotonic_ns=partial.monotonic_ns,
                    cancellation_generation=ctx.cancellation_generation,
                    is_final=False,
                    text_length=len(partial.text),
                )
            )
        elif self._event_sink is not None:
            seq = self._sequence_allocator() if self._sequence_allocator is not None else ctx.event_sequence
            evt = TranscriptEvent(
                session_id=ctx.session_id,
                utterance_id=partial.utterance_id,
                response_id=ctx.response_id,
                playback_id=ctx.playback_id,
                event_sequence=seq,
                monotonic_ns=partial.monotonic_ns,
                cancellation_generation=ctx.cancellation_generation,
                is_final=False,
                text_length=len(partial.text),
            )
            await self._event_sink.emit_event(evt)
        return True

    async def process_final(
        self,
        final: FinalTranscript,
        ctx: SessionContext,
        audio_frames: Optional[list] = None,
    ) -> bool:
        """Validate and submit final transcript exactly once to turn sink."""
        if not isinstance(final, FinalTranscript):
            raise TypeError("final must be a FinalTranscript")
        if not isinstance(ctx, SessionContext):
            raise TypeError("ctx must be a SessionContext")

        with self._lock:
            if self._is_shutdown:
                return False
            if final.utterance_id in self._submitted_utterances:
                return False

        # Reject cross-session
        if final.session_id != ctx.session_id:
            return False

        # Reject stale generation
        if self._cancellation_tracker.is_stale(ctx.cancellation_generation):
            return False

        # Validate timestamps against clock
        now_ns = self._clock.now_ns()
        if final.start_monotonic_ns > now_ns or final.end_monotonic_ns > now_ns:
            return False
        if final.end_monotonic_ns < final.start_monotonic_ns:
            return False

        # Owner verification
        if self._owner_verifier is not None and audio_frames is not None:
            owner_res = self._owner_verifier.verify_owner(audio_frames)
            if not owner_res.is_owner:
                return False

        # Echo/noise rejection
        if self._echo_noise_rejector is not None and audio_frames is not None:
            echo_res = self._echo_noise_rejector.evaluate_echo_noise(audio_frames)
            if echo_res.is_echo or echo_res.is_noise:
                return False

        # Register submission exactly once
        with self._lock:
            if self._is_shutdown or final.utterance_id in self._submitted_utterances:
                return False
            self._submitted_utterances.add(final.utterance_id)

        # Emit content-free event
        if self._emitter_fn is not None:
            await self._emitter_fn(
                lambda seq: TranscriptEvent(
                    session_id=ctx.session_id,
                    utterance_id=final.utterance_id,
                    response_id=ctx.response_id,
                    playback_id=ctx.playback_id,
                    event_sequence=seq,
                    monotonic_ns=final.end_monotonic_ns,
                    cancellation_generation=ctx.cancellation_generation,
                    is_final=True,
                    text_length=len(final.text),
                )
            )
        elif self._event_sink is not None:
            seq = self._sequence_allocator() if self._sequence_allocator is not None else ctx.event_sequence
            evt = TranscriptEvent(
                session_id=ctx.session_id,
                utterance_id=final.utterance_id,
                response_id=ctx.response_id,
                playback_id=ctx.playback_id,
                event_sequence=seq,
                monotonic_ns=final.end_monotonic_ns,
                cancellation_generation=ctx.cancellation_generation,
                is_final=True,
                text_length=len(final.text),
            )
            await self._event_sink.emit_event(evt)

        # Submit to turn sink
        if self._turn_sink is not None:
            await self._turn_sink.on_turn(
                text=final.text,
                session_id=final.session_id,
                utterance_id=final.utterance_id,
                start_ns=final.start_monotonic_ns,
                end_ns=final.end_monotonic_ns,
            )
        return True


__all__ = [
    "FinalTranscript",
    "PartialTranscript",
    "TranscriptPipeline",
]
