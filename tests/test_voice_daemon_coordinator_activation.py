from core.speech_adapters import WakeTranscriptionEvidence
from core.voice_session.activation import CoordinatorActivationState, VoiceAuthorityMode
from core.voice_session.coordinator import VoiceSessionCoordinator
from services import hikari_daemon as daemon


def test_daemon_boundary_defaults_to_legacy_and_content_free_health(monkeypatch) -> None:
    monkeypatch.delenv("HIKARI_VOICE_SESSION_COORDINATOR", raising=False)
    daemon._voice_authority_activation = None
    called = False

    def factory() -> VoiceSessionCoordinator:
        nonlocal called
        called = True
        return VoiceSessionCoordinator(session_id="session_1")

    activation = daemon.get_voice_authority_activation(
        daemon_loaded=False,
        wake_evidence=WakeTranscriptionEvidence(0.9, 90, 95, True),
        coordinator_factory=factory,
        now_ns=100,
    )
    assert called is False
    assert activation.health.authority is VoiceAuthorityMode.LEGACY
    assert activation.health.coordinator is CoordinatorActivationState.DISABLED_BY_DEFAULT
    assert daemon.get_voice_authority_health(daemon_loaded=False) == {
        "daemon": "unloaded",
        "authority": "legacy",
        "coordinator": "disabled_by_default",
        "aec": "unavailable",
        "duplex": "half_duplex",
        "coordinator_always_listening_enabled": False,
    }


def test_environment_flag_alone_cannot_replace_legacy_runtime(monkeypatch) -> None:
    monkeypatch.setenv("HIKARI_VOICE_SESSION_COORDINATOR", "1")
    activation = daemon.get_voice_authority_activation(
        daemon_loaded=True,
        wake_evidence=None,
        coordinator_factory=lambda: VoiceSessionCoordinator(session_id="session_1"),
    )
    assert activation.coordinator is None
    assert activation.health.authority is VoiceAuthorityMode.LEGACY
    assert (
        activation.health.coordinator
        is CoordinatorActivationState.DISABLED_MISSING_CALIBRATED_EVIDENCE
    )


def test_existing_legacy_process_function_is_not_replaced_by_activation(monkeypatch) -> None:
    monkeypatch.setenv("HIKARI_VOICE_SESSION_COORDINATOR", "1")
    original = daemon.process
    daemon.get_voice_authority_activation(daemon_loaded=True)
    assert daemon.process is original
