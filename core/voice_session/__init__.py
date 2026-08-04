"""Hikari voice-session authority contracts and prospective adapter seams."""

from __future__ import annotations

from core.voice_streaming.aec_evidence import PlatformAecEvidence
from core.voice_session.aec_policy import AecPolicy, AecPolicyDecision
from core.voice_session.activation import (
    CoordinatorActivationState,
    VoiceAuthorityActivation,
    VoiceAuthorityHealth,
    VoiceAuthorityMode,
    activate_voice_session_authority,
)
from core.voice_session.adapters import (
    CancellablePlaybackAdapter,
    EndpointVadObservationAdapter,
    InjectedOwnerVerifierAdapter,
    LocalAudioSubprocessLauncher,
    LocalBytesRendererAdapter,
    PlaybackStopReport,
    VoiceAudioLoopFrameAdapter,
    WholeResponseGenerationAdapter,
    WholeResponseHalfDuplexFallback,
)
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
from core.voice_session.wake_admission import (
    WakeAdmissionReason,
    WakeAdmissionResult,
    admit_local_wake,
)
from core.voice_session.daemon_supervisor import DaemonSupervisorBoundary
from core.voice_session.protocol_v1 import (
    VOICE_PROTOCOL_VERSION,
    VoiceProtocolEmitter,
    VoiceProtocolEnvelope,
    build_voice_protocol_event,
)

__all__ = [
    "AecPolicy",
    "AecPolicyDecision",
    "AudioFrame",
    "BargeInEvent",
    "CancellationTracker",
    "CancellablePlaybackAdapter",
    "CoordinatorActivationState",
    "DaemonSupervisorBoundary",
    "DegradedStateEvent",
    "EchoNoiseRejectorProtocol",
    "EchoNoiseResult",
    "EndpointVadObservationAdapter",
    "FinalTranscript",
    "FrameSourceProtocol",
    "GenerationStreamProtocol",
    "InterruptionConfirmation",
    "InterruptionRequest",
    "InjectedOwnerVerifierAdapter",
    "LocalAudioSubprocessLauncher",
    "LocalBytesRendererAdapter",
    "MonotonicClockProtocol",
    "OwnerVerificationResult",
    "OwnerVerifierProtocol",
    "PartialTranscript",
    "PlatformAecEvidence",
    "PlaybackControllerProtocol",
    "PlaybackEvent",
    "PlaybackStopReport",
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
    "VoiceAuthorityActivation",
    "VoiceAuthorityHealth",
    "VoiceAuthorityMode",
    "VoiceAudioLoopFrameAdapter",
    "VoiceProtocolEmitter",
    "VoiceProtocolEnvelope",
    "VOICE_PROTOCOL_VERSION",
    "WakeAdmissionReason",
    "WakeAdmissionResult",
    "admit_local_wake",
    "activate_voice_session_authority",
    "build_voice_protocol_event",
    "default_speakability_filter",
    "split_into_sentences",
    "WholeResponseGenerationAdapter",
    "WholeResponseHalfDuplexFallback",
]
