"""HIKARI streaming-voice foundations.

Pure, injected-clock contracts for timestamped segments, deterministic VAD,
full-duplex turn control, wake/sleep authority, barge-in, AEC capability
negotiation, and bounded backpressure.

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
