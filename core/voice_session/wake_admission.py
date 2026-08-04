"""Fail-closed bridge from local STT evidence to the pure wake-safety gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

from core.speech_adapters import LocalTranscriptionResult
from core.voice_safety.contracts import (
    BoundedConfidence,
    BoundedQualityScore,
    CandidateTimestamp,
    ObservationTimestamp,
    OwnerVerification,
    PlaybackState,
    SessionID,
    VADEvidenceTimestamp,
    WakeCandidate,
    WakeEvaluation,
    WakeEventID,
)
from core.voice_safety.wake_gate import WakeSafetyGate


class WakeAdmissionReason(StrEnum):
    EVALUATED = "evaluated"
    MISSING_EVIDENCE = "missing_evidence"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True, repr=False)
class WakeAdmissionResult:
    admitted: bool
    reason: WakeAdmissionReason
    evaluation: Optional[WakeEvaluation] = None

    def __post_init__(self) -> None:
        if not isinstance(self.admitted, bool):
            raise TypeError("admitted must be a boolean")
        if not isinstance(self.reason, WakeAdmissionReason):
            raise TypeError("reason must be a WakeAdmissionReason")
        if self.evaluation is not None and not isinstance(self.evaluation, WakeEvaluation):
            raise TypeError("evaluation must be WakeEvaluation or None")
        if self.admitted and (self.evaluation is None or not self.evaluation.accepted):
            raise ValueError("admitted requires an accepted evaluation")

    def __repr__(self) -> str:
        return (
            f"<WakeAdmissionResult admitted={self.admitted} reason={self.reason.value!r} "
            f"has_evaluation={self.evaluation is not None}>"
        )


def admit_local_wake(
    *,
    gate: WakeSafetyGate,
    transcription: LocalTranscriptionResult,
    detected_wake_name: str,
    session_id: str,
    event_id: str,
    owner_verification: OwnerVerification,
    playback: PlaybackState,
    now_ns: int,
    quality: float = 1.0,
    same_utterance_command: bool = False,
) -> WakeAdmissionResult:
    """Evaluate genuine local wake evidence; missing evidence never fabricates a score."""
    if not isinstance(gate, WakeSafetyGate) or not isinstance(
        transcription, LocalTranscriptionResult
    ):
        return WakeAdmissionResult(False, WakeAdmissionReason.INVALID_INPUT)
    evidence = transcription.wake_evidence
    if evidence is None:
        return WakeAdmissionResult(False, WakeAdmissionReason.MISSING_EVIDENCE)
    try:
        candidate = WakeCandidate(
            event_id=WakeEventID(event_id),
            session_id=SessionID(session_id),
            wake_name=detected_wake_name,
            candidate_timestamp=CandidateTimestamp(evidence.observed_monotonic_ns),
            observation_timestamp=ObservationTimestamp(evidence.observed_monotonic_ns),
            vad_evidence_timestamp=VADEvidenceTimestamp(
                evidence.vad_observed_monotonic_ns
            ),
            vad_has_speech=evidence.vad_has_speech,
            confidence=BoundedConfidence(evidence.calibrated_score),
            quality=BoundedQualityScore(quality),
            playback=playback,
            owner_verification=owner_verification,
            confidence_is_hotword_bias=False,
            same_utterance_command=same_utterance_command,
        )
        evaluation = gate.evaluate(candidate, now_ns=now_ns)
    except (TypeError, ValueError):
        return WakeAdmissionResult(False, WakeAdmissionReason.INVALID_INPUT)
    return WakeAdmissionResult(
        admitted=evaluation.accepted,
        reason=WakeAdmissionReason.EVALUATED,
        evaluation=evaluation,
    )


__all__ = [
    "WakeAdmissionReason",
    "WakeAdmissionResult",
    "admit_local_wake",
]
