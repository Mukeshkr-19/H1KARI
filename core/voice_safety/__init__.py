"""Wake-safety and daemon-recovery foundations.

This package provides pure, local, deterministic building blocks:

- ``contracts``: content-safe immutable contracts (session ids, wake event
  ids, timestamps, bounded scores, playback state, cooldown state, tri-state
  owner verification, awaiting-command deadline, final wake decisions, and
  stable enum reason codes).
- ``wake_gate``: a fail-closed ``WakeSafetyGate`` that grants exactly one
  bounded awaiting-command authorization per qualified wake and consumes it
  exactly once on command confirmation.
- ``calibration``: pure threshold calibration from labeled score observations.
- ``daemon_recovery``: a pure bounded restart-recovery state machine.

Nothing in this package speaks, invokes the orchestrator, starts playback,
opens a microphone, or touches the daemon.  Importing this package has no
side effects.
"""

from core.voice_safety.contracts import (
    MAX_IDENTIFIER_LENGTH,
    AwaitingCommandDeadline,
    BoundedConfidence,
    BoundedQualityScore,
    CandidateTimestamp,
    ConfirmationCue,
    CooldownState,
    CorrelatedIntent,
    InvalidCandidateError,
    NotCalibratedError,
    ObservationTimestamp,
    OwnerVerification,
    OwnerVerificationReason,
    OwnerVerificationState,
    PlaybackReason,
    PlaybackState,
    SessionID,
    VADEvidenceTimestamp,
    VoiceSafetyError,
    WakeCandidate,
    WakeDecision,
    WakeEvaluation,
    WakeEventID,
    WakeReason,
)
from core.voice_safety.wake_gate import DEFAULT_AWAITING_COMMAND_NS, WakeSafetyGate
from core.voice_safety.calibration import (
    CalibrationReason,
    CalibrationReport,
    LabeledScore,
    calibrate_threshold,
)
from core.voice_safety.daemon_recovery import (
    DaemonRecoveryPolicy,
    FailureKind,
    RecoveryAction,
    RecoveryDecision,
    RecoveryReason,
    RecoveryState,
)

__all__ = [
    "MAX_IDENTIFIER_LENGTH",
    "AwaitingCommandDeadline",
    "BoundedConfidence",
    "BoundedQualityScore",
    "CalibrationReason",
    "CalibrationReport",
    "CandidateTimestamp",
    "ConfirmationCue",
    "CooldownState",
    "CorrelatedIntent",
    "DEFAULT_AWAITING_COMMAND_NS",
    "DaemonRecoveryPolicy",
    "FailureKind",
    "InvalidCandidateError",
    "LabeledScore",
    "NotCalibratedError",
    "ObservationTimestamp",
    "OwnerVerification",
    "OwnerVerificationReason",
    "OwnerVerificationState",
    "PlaybackReason",
    "PlaybackState",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryReason",
    "RecoveryState",
    "SessionID",
    "VADEvidenceTimestamp",
    "VoiceSafetyError",
    "WakeCandidate",
    "WakeDecision",
    "WakeEvaluation",
    "WakeEventID",
    "WakeReason",
    "WakeSafetyGate",
    "calibrate_threshold",
]
