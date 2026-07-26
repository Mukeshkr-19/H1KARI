"""Acoustic echo cancellation capability contract (no DSP)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .contracts import AecStatus, DuplexMode, StreamingDecision, StreamingReason


@dataclass(frozen=True, repr=False)
class AecCapability:
    status: AecStatus
    vendor_label: str = "none"
    negotiated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, AecStatus):
            raise ValueError("invalid_aec_status")
        if not isinstance(self.vendor_label, str) or len(self.vendor_label) > 64:
            raise ValueError("invalid_vendor_label")
        if not isinstance(self.negotiated, bool):
            raise ValueError("invalid_negotiated")

    @property
    def echo_cancellation_active(self) -> bool:
        """True only when robust AEC is available and negotiated."""
        return self.negotiated and self.status == AecStatus.AVAILABLE

    @property
    def recommended_duplex(self) -> DuplexMode:
        if self.echo_cancellation_active:
            return DuplexMode.FULL_DUPLEX
        return DuplexMode.HALF_DUPLEX

    def __repr__(self) -> str:
        return (
            f"AecCapability(status={self.status.value!r}, "
            f"active={self.echo_cancellation_active}, duplex={self.recommended_duplex.value!r})"
        )


class AecNegotiator:
    """Capability negotiation only. Never invents DSP or claims false activity."""

    def __init__(self) -> None:
        self._capability = AecCapability(status=AecStatus.UNAVAILABLE, negotiated=False)

    @property
    def capability(self) -> AecCapability:
        return self._capability

    def report(self, status: AecStatus, *, vendor_label: str = "none") -> AecCapability:
        if not isinstance(status, AecStatus):
            raise ValueError("invalid_aec_status")
        self._capability = AecCapability(status=status, vendor_label=vendor_label, negotiated=False)
        return self._capability

    def negotiate(self) -> StreamingDecision:
        cap = self._capability
        if cap.status in (AecStatus.UNAVAILABLE, AecStatus.FAILED):
            self._capability = AecCapability(
                status=cap.status,
                vendor_label=cap.vendor_label,
                negotiated=False,
            )
            return StreamingDecision(False, StreamingReason.AEC_UNAVAILABLE, DuplexMode.HALF_DUPLEX.value)
        if cap.status == AecStatus.DEGRADED:
            # Degraded never claims full active cancellation
            self._capability = AecCapability(
                status=AecStatus.DEGRADED,
                vendor_label=cap.vendor_label,
                negotiated=False,
            )
            return StreamingDecision(False, StreamingReason.AEC_UNAVAILABLE, DuplexMode.HALF_DUPLEX.value)
        self._capability = AecCapability(
            status=AecStatus.AVAILABLE,
            vendor_label=cap.vendor_label,
            negotiated=True,
        )
        return StreamingDecision(True, StreamingReason.OK, DuplexMode.FULL_DUPLEX.value)

    def assert_never_false_active(self) -> bool:
        """Invariant helper for tests: active implies AVAILABLE+negotiated."""
        return (not self._capability.echo_cancellation_active) or (
            self._capability.status == AecStatus.AVAILABLE and self._capability.negotiated
        )


__all__ = ["AecCapability", "AecNegotiator"]
