"""HIKARI streaming-voice bounded contracts and compatibility facade.

Canonical production wake/sleep/turn authority is
``core.voice_streaming.runtime.VoiceStreamingRuntime`` (daemon-facing).
``TurnStateMachine`` delegates to that runtime so the packages cannot
disagree. Pure helpers (AEC negotiation, barge-in correlation, bounded
buffers, transcript segment contracts, metadata VAD) remain here.

No live microphone, DSP, model, network, filesystem, or daemon wiring.
"""

from .aec import AecCapability, AecNegotiator
from .backpressure import BoundedVoiceBuffer, BufferLimits, LatencySummary, SegmentLedger
from .barge_in import BargeInController, BargeInResult
from .contracts import (
    AecStatus,
    AudioFrameMeta,
    ConfidenceCategory,
    DuplexMode,
    InterruptionEvent,
    SegmentStatus,
    SpeakerCategory,
    StreamingDecision,
    StreamingReason,
    TranscriptSegment,
    TurnState,
    VadState,
    WakeEvidence,
)
from .turn import TurnSnapshot, TurnStateMachine, transition_table
from .vad import VadConfig, VadSnapshot, VadStateMachine
from .wake_sleep import WakeSleepAuthority, WakeSleepSnapshot

__all__ = [
    "AecCapability",
    "AecNegotiator",
    "AecStatus",
    "AudioFrameMeta",
    "BoundedVoiceBuffer",
    "BufferLimits",
    "BargeInController",
    "BargeInResult",
    "ConfidenceCategory",
    "DuplexMode",
    "InterruptionEvent",
    "LatencySummary",
    "SegmentLedger",
    "SegmentStatus",
    "SpeakerCategory",
    "StreamingDecision",
    "StreamingReason",
    "TranscriptSegment",
    "TurnSnapshot",
    "TurnState",
    "TurnStateMachine",
    "VadConfig",
    "VadSnapshot",
    "VadState",
    "VadStateMachine",
    "WakeEvidence",
    "WakeSleepAuthority",
    "WakeSleepSnapshot",
    "transition_table",
]
