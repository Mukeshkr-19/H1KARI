"""Inert production activation boundary for voice session authority.

The existing daemon runtime remains authoritative unless a caller explicitly
enables the coordinator *and* supplies genuine calibrated wake evidence and a
coordinator factory.  Importing this module performs no environment reads,
audio access, process work, or model construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Optional

from core.speech_adapters import WakeTranscriptionEvidence
from core.voice_session.coordinator import VoiceSessionCoordinator
from core.voice_streaming.aec_evidence import AecEvidenceAcceptance


class VoiceAuthorityMode(StrEnum):
    LEGACY = "legacy"
    COORDINATOR = "coordinator"


class CoordinatorActivationState(StrEnum):
    DISABLED_BY_DEFAULT = "disabled_by_default"
    DISABLED_MISSING_CALIBRATED_EVIDENCE = "disabled_missing_calibrated_evidence"
    DISABLED_MISSING_FACTORY = "disabled_missing_factory"
    DISABLED_FACTORY_FAILED = "disabled_factory_failed"
    DISABLED_LEGACY_AUTHORITY_ACTIVE = "disabled_legacy_authority_active"
    ACTIVE = "active"


class DaemonLoadState(StrEnum):
    UNLOADED = "unloaded"
    LOADED = "loaded"


class AecHealthState(StrEnum):
    UNAVAILABLE = "unavailable"
    EVIDENCE_AVAILABLE = "evidence_available"


class DuplexHealthState(StrEnum):
    HALF_DUPLEX = "half_duplex"
    FULL_DUPLEX_CANDIDATE = "full_duplex_candidate"


@dataclass(frozen=True, repr=False)
class VoiceAuthorityHealth:
    daemon: DaemonLoadState
    authority: VoiceAuthorityMode
    coordinator: CoordinatorActivationState
    aec: AecHealthState
    duplex: DuplexHealthState
    coordinator_always_listening_enabled: bool = False

    def __repr__(self) -> str:
        return (
            "VoiceAuthorityHealth("
            f"daemon={self.daemon.value!r}, authority={self.authority.value!r}, "
            f"coordinator={self.coordinator.value!r}, aec={self.aec.value!r}, "
            f"duplex={self.duplex.value!r}, "
            "coordinator_always_listening_enabled="
            f"{self.coordinator_always_listening_enabled})"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "daemon": self.daemon.value,
            "authority": self.authority.value,
            "coordinator": self.coordinator.value,
            "aec": self.aec.value,
            "duplex": self.duplex.value,
            "coordinator_always_listening_enabled": (
                self.coordinator_always_listening_enabled
            ),
        }


@dataclass(frozen=True, repr=False)
class VoiceAuthorityActivation:
    health: VoiceAuthorityHealth
    coordinator: Optional[VoiceSessionCoordinator] = None

    def __post_init__(self) -> None:
        if self.health.authority is VoiceAuthorityMode.COORDINATOR:
            if not isinstance(self.coordinator, VoiceSessionCoordinator):
                raise ValueError("coordinator authority requires coordinator instance")
        elif self.coordinator is not None:
            raise ValueError("legacy authority cannot carry coordinator instance")

    def __repr__(self) -> str:
        return (
            "VoiceAuthorityActivation("
            f"authority={self.health.authority.value!r}, "
            f"has_coordinator={self.coordinator is not None})"
        )


def activate_voice_session_authority(
    *,
    coordinator_enabled: bool,
    daemon_loaded: bool,
    wake_evidence: Optional[WakeTranscriptionEvidence],
    coordinator_factory: Optional[Callable[[], VoiceSessionCoordinator]],
    legacy_authority_active: bool = True,
    aec_acceptance: Optional[AecEvidenceAcceptance] = None,
    now_ns: Optional[int] = None,
    max_wake_evidence_age_ns: int = 500_000_000,
) -> VoiceAuthorityActivation:
    """Select one authority; default/failure always returns the legacy runtime."""
    for name, value in (
        ("coordinator_enabled", coordinator_enabled),
        ("daemon_loaded", daemon_loaded),
        ("legacy_authority_active", legacy_authority_active),
    ):
        if not isinstance(value, bool):
            raise TypeError(f"{name} must be a boolean")

    daemon_state = DaemonLoadState.LOADED if daemon_loaded else DaemonLoadState.UNLOADED
    accepted_aec = (
        isinstance(aec_acceptance, AecEvidenceAcceptance)
        and aec_acceptance.accepted
    )
    aec_state = AecHealthState.EVIDENCE_AVAILABLE if accepted_aec else AecHealthState.UNAVAILABLE
    duplex = (
        DuplexHealthState.FULL_DUPLEX_CANDIDATE
        if accepted_aec and aec_acceptance.full_duplex
        else DuplexHealthState.HALF_DUPLEX
    )

    def legacy(state: CoordinatorActivationState) -> VoiceAuthorityActivation:
        return VoiceAuthorityActivation(
            VoiceAuthorityHealth(
                daemon=daemon_state,
                authority=VoiceAuthorityMode.LEGACY,
                coordinator=state,
                aec=aec_state,
                duplex=duplex,
                coordinator_always_listening_enabled=False,
            )
        )

    if not coordinator_enabled:
        return legacy(CoordinatorActivationState.DISABLED_BY_DEFAULT)
    evidence_fresh = False
    if isinstance(wake_evidence, WakeTranscriptionEvidence):
        evidence_fresh = (
            isinstance(now_ns, int)
            and not isinstance(now_ns, bool)
            and now_ns >= 0
            and isinstance(max_wake_evidence_age_ns, int)
            and not isinstance(max_wake_evidence_age_ns, bool)
            and 0 < max_wake_evidence_age_ns <= 5_000_000_000
            and wake_evidence.observed_monotonic_ns <= now_ns
            and now_ns - wake_evidence.observed_monotonic_ns <= max_wake_evidence_age_ns
            and wake_evidence.is_vad_fresh(
                now_ns=now_ns, max_age_ns=max_wake_evidence_age_ns
            )
        )
    if not evidence_fresh:
        return legacy(CoordinatorActivationState.DISABLED_MISSING_CALIBRATED_EVIDENCE)
    if legacy_authority_active:
        return legacy(CoordinatorActivationState.DISABLED_LEGACY_AUTHORITY_ACTIVE)
    if coordinator_factory is None or not callable(coordinator_factory):
        return legacy(CoordinatorActivationState.DISABLED_MISSING_FACTORY)

    try:
        coordinator = coordinator_factory()
    except Exception:
        return legacy(CoordinatorActivationState.DISABLED_FACTORY_FAILED)
    if not isinstance(coordinator, VoiceSessionCoordinator):
        return legacy(CoordinatorActivationState.DISABLED_MISSING_FACTORY)
    return VoiceAuthorityActivation(
        VoiceAuthorityHealth(
            daemon=daemon_state,
            authority=VoiceAuthorityMode.COORDINATOR,
            coordinator=CoordinatorActivationState.ACTIVE,
            aec=aec_state,
            duplex=duplex,
            coordinator_always_listening_enabled=False,
        ),
        coordinator=coordinator,
    )


__all__ = [
    "AecHealthState",
    "CoordinatorActivationState",
    "DaemonLoadState",
    "DuplexHealthState",
    "VoiceAuthorityActivation",
    "VoiceAuthorityHealth",
    "VoiceAuthorityMode",
    "activate_voice_session_authority",
]
