"""AEC and duplex policy adapting to PlatformAecEvidence.

Default mode is half-duplex. Full duplex requires available, enabled, and verified platform AEC
evidence bound to the active stream and device, as well as an active echo reference path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.voice_streaming.aec_evidence import AecEvidenceGate, PlatformAecEvidence
from core.voice_session.contracts import validate_monotonic_ns, validate_session_id
from core.voice_session.events import DegradedStateEvent


@dataclass(frozen=True, repr=False)
class AecPolicyDecision:
    """Duplex decision output from AEC policy evaluation."""

    is_full_duplex: bool
    reason: str
    has_echo_reference: bool
    headphones_active: bool
    monotonic_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.is_full_duplex, bool):
            raise TypeError("is_full_duplex must be a boolean")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        if not isinstance(self.has_echo_reference, bool):
            raise TypeError("has_echo_reference must be a boolean")
        if not isinstance(self.headphones_active, bool):
            raise TypeError("headphones_active must be a boolean")
        object.__setattr__(self, "monotonic_ns", validate_monotonic_ns(self.monotonic_ns))

    def __repr__(self) -> str:
        return (
            f"<AecPolicyDecision full_duplex={self.is_full_duplex} "
            f"reason={self.reason!r}>"
        )


class AecPolicy:
    """AEC policy engine enforcing full duplex prerequisites and safe downgrade."""

    def __init__(
        self,
        *,
        future_skew_ns: int = 1_000_000_000,
        stale_skew_ns: int = 5_000_000_000,
    ) -> None:
        self._future_skew_ns = future_skew_ns
        self._stale_skew_ns = stale_skew_ns
        self._is_full_duplex = False

    @property
    def is_full_duplex(self) -> bool:
        return self._is_full_duplex

    def evaluate(
        self,
        *,
        evidence: Optional[PlatformAecEvidence],
        has_echo_reference: bool,
        headphones_active: bool = False,
        now_ns: int,
        active_stream_id: str,
        active_device_id: str,
    ) -> AecPolicyDecision:
        """Evaluate platform evidence and audio path for full duplex capability."""
        stream_id = validate_session_id(active_stream_id)
        device_id = validate_session_id(active_device_id)
        ts_ns = validate_monotonic_ns(now_ns)

        if not isinstance(has_echo_reference, bool):
            raise TypeError("has_echo_reference must be a boolean")
        if not isinstance(headphones_active, bool):
            raise TypeError("headphones_active must be a boolean")

        if evidence is None:
            self._is_full_duplex = False
            return AecPolicyDecision(
                is_full_duplex=False,
                reason="aec_evidence_absent",
                has_echo_reference=has_echo_reference,
                headphones_active=headphones_active,
                monotonic_ns=ts_ns,
            )

        gate = AecEvidenceGate(
            stream_id=stream_id,
            device_id=device_id,
            future_skew_ns=self._future_skew_ns,
            stale_skew_ns=self._stale_skew_ns,
        )

        acceptance = gate.accept(evidence, now_ns=ts_ns)
        if not acceptance.accepted:
            self._is_full_duplex = False
            return AecPolicyDecision(
                is_full_duplex=False,
                reason=f"gate_rejected_{acceptance.reason}",
                has_echo_reference=has_echo_reference,
                headphones_active=headphones_active,
                monotonic_ns=ts_ns,
            )

        if not acceptance.full_duplex:
            self._is_full_duplex = False
            return AecPolicyDecision(
                is_full_duplex=False,
                reason="aec_not_fully_supported",
                has_echo_reference=has_echo_reference,
                headphones_active=headphones_active,
                monotonic_ns=ts_ns,
            )

        # Full duplex requires physical echo reference loopback path
        if not has_echo_reference:
            self._is_full_duplex = False
            return AecPolicyDecision(
                is_full_duplex=False,
                reason="missing_echo_reference",
                has_echo_reference=has_echo_reference,
                headphones_active=headphones_active,
                monotonic_ns=ts_ns,
            )

        self._is_full_duplex = True
        return AecPolicyDecision(
            is_full_duplex=True,
            reason="ok",
            has_echo_reference=has_echo_reference,
            headphones_active=headphones_active,
            monotonic_ns=ts_ns,
        )

    def mark_lost(self) -> AecPolicyDecision:
        """Explicitly downgrade to half duplex upon evidence loss."""
        self._is_full_duplex = False
        return AecPolicyDecision(
            is_full_duplex=False,
            reason="aec_evidence_lost",
            has_echo_reference=False,
            headphones_active=False,
            monotonic_ns=0,
        )


__all__ = [
    "AecPolicy",
    "AecPolicyDecision",
]
