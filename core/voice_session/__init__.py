"""Hikari VoiceSessionCoordinator foundation.

Unwired, synthetic, self-contained voice session foundation separating capture, VAD,
transcription, verification, orchestration, TTS, playback, cancellation, and events.
"""

from __future__ import annotations

from core.voice_streaming.aec_evidence import PlatformAecEvidence
from core.voice_session.aec_policy import AecPolicy, AecPolicyDecision
from core.voice_session.cancellation import (
    CancellationTracker,
    InterruptionConfirmation,
    InterruptionRequest,
)
from core.voice_session.contracts import (
    AudioFrame,
    EchoNoiseRejectorProtocol,
    EchoNoiseResult,
    FrameSourceProtocol,
    GenerationStreamProtocol,
    MonotonicClockProtocol,
    OwnerVerificationResult,
    OwnerVerifierProtocol,
    PlaybackControllerProtocol,
    ResumePolicyProtocol,
    SessionContext,
    StateEventSinkProtocol,
    TTSRendererProtocol,
    TranscriberProtocol,
    TurnSinkProtocol,
    VADSourceProtocol,
)
from core.voice_session.coordinator import (
    VoiceSessionCoordinator,
    VoiceSessionState,
)
from core.voice_session.events import (
    BargeInEvent,
    DegradedStateEvent,
    PlaybackEvent,
    StateChangeEvent,
    TranscriptEvent,
    VoiceSessionEvent,
)
from core.voice_session.transcript_pipeline import (
    FinalTranscript,
    PartialTranscript,
    TranscriptPipeline,
)
from core.voice_session.tts_pipeline import (
    TTSChunk,
    TTSPipeline,
    default_speakability_filter,
    split_into_sentences,
)

__all__ = [
    "AecPolicy",
    "AecPolicyDecision",
    "AudioFrame",
    "BargeInEvent",
    "CancellationTracker",
    "DegradedStateEvent",
    "EchoNoiseRejectorProtocol",
    "EchoNoiseResult",
    "FinalTranscript",
    "FrameSourceProtocol",
    "GenerationStreamProtocol",
    "InterruptionConfirmation",
    "InterruptionRequest",
    "MonotonicClockProtocol",
    "OwnerVerificationResult",
    "OwnerVerifierProtocol",
    "PartialTranscript",
    "PlatformAecEvidence",
    "PlaybackControllerProtocol",
    "PlaybackEvent",
    "ResumePolicyProtocol",
    "SessionContext",
    "StateChangeEvent",
    "StateEventSinkProtocol",
    "TTSChunk",
    "TTSPipeline",
    "TTSRendererProtocol",
    "TranscriberProtocol",
    "TranscriptEvent",
    "TranscriptPipeline",
    "TurnSinkProtocol",
    "VADSourceProtocol",
    "VoiceSessionCoordinator",
    "VoiceSessionEvent",
    "VoiceSessionState",
    "default_speakability_filter",
    "split_into_sentences",
]
