"""Deterministic tests for evidence-aware coordinator wake admission."""

from core.speech_adapters import LocalTranscriptionResult, WakeTranscriptionEvidence
from core.voice_safety.contracts import OwnerVerification, PlaybackState
from core.voice_safety.wake_gate import WakeSafetyGate
from core.voice_session.wake_admission import WakeAdmissionReason, admit_local_wake


NOW = 5_000_000_000


def _gate() -> WakeSafetyGate:
    return WakeSafetyGate(
        calibrated=True,
        confidence_threshold=0.75,
        clock=lambda: NOW,
    )


def test_missing_wake_evidence_fails_closed_without_fabricated_evaluation() -> None:
    result = admit_local_wake(
        gate=_gate(),
        transcription=LocalTranscriptionResult("hikari"),
        detected_wake_name="Hikari",
        session_id="sess-1",
        event_id="wake-1",
        owner_verification=OwnerVerification.verified(),
        playback=PlaybackState.idle(),
        now_ns=NOW,
    )
    assert result.admitted is False
    assert result.reason is WakeAdmissionReason.MISSING_EVIDENCE
    assert result.evaluation is None


def test_fresh_calibrated_owner_evidence_is_delegated_to_wake_gate() -> None:
    transcription = LocalTranscriptionResult(
        "hikari",
        WakeTranscriptionEvidence(
            calibrated_score=0.9,
            observed_monotonic_ns=NOW - 50_000_000,
            vad_observed_monotonic_ns=NOW - 20_000_000,
            vad_has_speech=True,
        ),
    )
    result = admit_local_wake(
        gate=_gate(),
        transcription=transcription,
        detected_wake_name="Hikari",
        session_id="sess-1",
        event_id="wake-1",
        owner_verification=OwnerVerification.verified(),
        playback=PlaybackState.idle(),
        now_ns=NOW,
    )
    assert result.admitted is True
    assert result.evaluation is not None and result.evaluation.accepted is True


def test_stale_vad_low_score_and_playback_each_fail_closed() -> None:
    cases = (
        (0.9, NOW - 900_000_000, PlaybackState.idle()),
        (0.2, NOW - 20_000_000, PlaybackState.idle()),
        (0.9, NOW - 20_000_000, PlaybackState.playing()),
    )
    for index, (score, vad_ns, playback) in enumerate(cases):
        result = admit_local_wake(
            gate=_gate(),
            transcription=LocalTranscriptionResult(
                "hikari",
                WakeTranscriptionEvidence(score, NOW - 50_000_000, vad_ns, True),
            ),
            detected_wake_name="Hikari",
            session_id="sess-1",
            event_id=f"wake-{index}",
            owner_verification=OwnerVerification.verified(),
            playback=playback,
            now_ns=NOW,
        )
        assert result.admitted is False
        assert result.evaluation is not None and result.evaluation.accepted is False
