from core.speech_adapters import WakeTranscriptionEvidence
from core.voice_session.activation import (
    AecHealthState,
    CoordinatorActivationState,
    DaemonLoadState,
    DuplexHealthState,
    VoiceAuthorityMode,
    activate_voice_session_authority,
)
from core.voice_session.coordinator import VoiceSessionCoordinator


def _fresh_evidence() -> WakeTranscriptionEvidence:
    return WakeTranscriptionEvidence(
        calibrated_score=0.91,
        observed_monotonic_ns=90,
        vad_observed_monotonic_ns=95,
        vad_has_speech=True,
    )


def test_default_activation_is_unloaded_legacy_half_duplex_and_never_calls_factory() -> None:
    calls = 0

    def factory() -> VoiceSessionCoordinator:
        nonlocal calls
        calls += 1
        return VoiceSessionCoordinator(session_id="session_1")

    result = activate_voice_session_authority(
        coordinator_enabled=False,
        daemon_loaded=False,
        wake_evidence=_fresh_evidence(),
        coordinator_factory=factory,
        now_ns=100,
    )
    assert calls == 0
    assert result.coordinator is None
    assert result.health.daemon is DaemonLoadState.UNLOADED
    assert result.health.authority is VoiceAuthorityMode.LEGACY
    assert result.health.coordinator is CoordinatorActivationState.DISABLED_BY_DEFAULT
    assert result.health.aec is AecHealthState.UNAVAILABLE
    assert result.health.duplex is DuplexHealthState.HALF_DUPLEX
    assert result.health.coordinator_always_listening_enabled is False


def test_flag_without_fresh_calibrated_evidence_stays_legacy() -> None:
    calls = 0

    def factory() -> VoiceSessionCoordinator:
        nonlocal calls
        calls += 1
        return VoiceSessionCoordinator(session_id="session_1")

    result = activate_voice_session_authority(
        coordinator_enabled=True,
        daemon_loaded=True,
        wake_evidence=None,
        coordinator_factory=factory,
        now_ns=100,
    )
    assert calls == 0
    assert result.health.authority is VoiceAuthorityMode.LEGACY
    assert (
        result.health.coordinator
        is CoordinatorActivationState.DISABLED_MISSING_CALIBRATED_EVIDENCE
    )


def test_stale_evidence_cannot_activate_coordinator() -> None:
    result = activate_voice_session_authority(
        coordinator_enabled=True,
        daemon_loaded=True,
        wake_evidence=_fresh_evidence(),
        coordinator_factory=lambda: VoiceSessionCoordinator(session_id="session_1"),
        now_ns=1_000_000_000,
    )
    assert result.health.authority is VoiceAuthorityMode.LEGACY
    assert result.coordinator is None


def test_explicit_flag_fresh_evidence_and_factory_are_all_required() -> None:
    result = activate_voice_session_authority(
        coordinator_enabled=True,
        daemon_loaded=True,
        wake_evidence=_fresh_evidence(),
        coordinator_factory=lambda: VoiceSessionCoordinator(session_id="session_1"),
        legacy_authority_active=False,
        now_ns=100,
    )
    assert result.health.authority is VoiceAuthorityMode.COORDINATOR
    assert result.health.coordinator is CoordinatorActivationState.ACTIVE
    assert isinstance(result.coordinator, VoiceSessionCoordinator)
    # AEC is independent: coordinator eligibility never invents duplex proof.
    assert result.health.duplex is DuplexHealthState.HALF_DUPLEX


def test_live_legacy_authority_blocks_coordinator_even_with_other_evidence() -> None:
    calls = 0

    def factory() -> VoiceSessionCoordinator:
        nonlocal calls
        calls += 1
        return VoiceSessionCoordinator(session_id="session_1")

    result = activate_voice_session_authority(
        coordinator_enabled=True,
        daemon_loaded=True,
        wake_evidence=_fresh_evidence(),
        coordinator_factory=factory,
        legacy_authority_active=True,
        now_ns=100,
    )
    assert calls == 0
    assert result.health.authority is VoiceAuthorityMode.LEGACY
    assert (
        result.health.coordinator
        is CoordinatorActivationState.DISABLED_LEGACY_AUTHORITY_ACTIVE
    )


def test_factory_failure_fails_back_to_legacy_without_dual_authority() -> None:
    def broken_factory() -> VoiceSessionCoordinator:
        raise RuntimeError("synthetic")

    result = activate_voice_session_authority(
        coordinator_enabled=True,
        daemon_loaded=True,
        wake_evidence=_fresh_evidence(),
        coordinator_factory=broken_factory,
        legacy_authority_active=False,
        now_ns=100,
    )
    assert result.coordinator is None
    assert result.health.authority is VoiceAuthorityMode.LEGACY
    assert result.health.coordinator is CoordinatorActivationState.DISABLED_FACTORY_FAILED
