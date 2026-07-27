"""Platform AEC evidence contracts with stream/device binding.

Never invents DSP. Full duplex requires available+enabled+verified evidence
bound to the active stream and device, with fresh monotonic timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.voice_streaming.contracts import validate_monotonic_ns, validate_stream_id
from core.voice_streaming.echo_policy import EchoCapability

_HARD_MAX_FUTURE_SKEW_NS = 5_000_000_000
_HARD_MAX_STALE_SKEW_NS = 60_000_000_000


def _require_positive_int(value: object, name: str, *, hard_max: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid_{name}")
    if value < 1 or value > hard_max:
        raise ValueError(f"invalid_{name}")
    return value


@dataclass(frozen=True, repr=False)
class PlatformAecEvidence:
    stream_id: str
    device_id: str
    available: bool
    enabled: bool
    verified: bool
    observed_at_ns: int
    software: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", validate_stream_id(self.stream_id))
        if not isinstance(self.device_id, str) or not self.device_id or len(self.device_id) > 128:
            raise ValueError("invalid_device_id")
        for name in ("available", "enabled", "verified", "software"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"invalid_{name}")
        object.__setattr__(self, "observed_at_ns", validate_monotonic_ns(self.observed_at_ns))

    @property
    def supports_full_duplex(self) -> bool:
        return self.available and self.enabled and self.verified

    def to_echo_capability(self) -> EchoCapability:
        if not self.supports_full_duplex:
            return EchoCapability()
        if self.software:
            return EchoCapability(software_aec_available=True, software_aec_verified=True)
        return EchoCapability(native_aec_available=True, native_aec_verified=True)

    def __repr__(self) -> str:
        return f"PlatformAecEvidence(supports_full_duplex={self.supports_full_duplex})"


@dataclass(frozen=True, repr=False)
class AecEvidenceAcceptance:
    accepted: bool
    reason: str
    full_duplex: bool = False

    def __repr__(self) -> str:
        return (
            f"AecEvidenceAcceptance(accepted={self.accepted}, "
            f"reason={self.reason!r}, full_duplex={self.full_duplex})"
        )


class AecEvidenceGate:
    """Accept/reject platform AEC evidence against active stream/device/clock."""

    def __init__(
        self,
        *,
        stream_id: str,
        device_id: str,
        future_skew_ns: int = 1_000_000_000,
        stale_skew_ns: int = 5_000_000_000,
    ) -> None:
        self._stream_id = validate_stream_id(stream_id)
        if not isinstance(device_id, str) or not device_id or len(device_id) > 128:
            raise ValueError("invalid_device_id")
        self._device_id = device_id
        self._future_skew_ns = _require_positive_int(
            future_skew_ns, "future_skew_ns", hard_max=_HARD_MAX_FUTURE_SKEW_NS
        )
        self._stale_skew_ns = _require_positive_int(
            stale_skew_ns, "stale_skew_ns", hard_max=_HARD_MAX_STALE_SKEW_NS
        )
        if self._future_skew_ns > self._stale_skew_ns:
            raise ValueError("invalid_skew_relationship")
        self._current: Optional[PlatformAecEvidence] = None

    @property
    def current(self) -> Optional[PlatformAecEvidence]:
        return self._current

    def clear(self) -> None:
        self._current = None

    def accept(self, evidence: PlatformAecEvidence, *, now_ns: int) -> AecEvidenceAcceptance:
        if not isinstance(evidence, PlatformAecEvidence):
            return AecEvidenceAcceptance(False, "invalid_evidence")
        if evidence.stream_id != self._stream_id:
            return AecEvidenceAcceptance(False, "cross_stream")
        if evidence.device_id != self._device_id:
            return AecEvidenceAcceptance(False, "cross_device")
        now = validate_monotonic_ns(now_ns)
        if evidence.observed_at_ns > now + self._future_skew_ns:
            return AecEvidenceAcceptance(False, "future_evidence")
        if now - evidence.observed_at_ns > self._stale_skew_ns:
            return AecEvidenceAcceptance(False, "stale_evidence")
        self._current = evidence
        return AecEvidenceAcceptance(
            True,
            "ok",
            full_duplex=evidence.supports_full_duplex,
        )

    def mark_lost(self) -> None:
        self._current = None


__all__ = [
    "AecEvidenceAcceptance",
    "AecEvidenceGate",
    "PlatformAecEvidence",
]
