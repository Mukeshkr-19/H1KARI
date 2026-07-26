"""Remote-worker authority contracts without networking.

Remote workers have no implicit owner authority. Envelopes are immutable,
non-delegable, and fail closed on expiry, revocation, replay, mismatch, or
failed injected signature verification. Results remain untrusted evidence and
cannot mark tasks successful or execute local actions.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableSet
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple

from core.phase6_agent.contracts import (
    FailureCode,
    RemoteWorkerAuthorityEnvelope,
    RemoteWorkerResult,
    validate_finite_mono,
    validate_identifier,
)


class RemoteValidationOutcome(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    EXPIRED = "expired"
    REVOKED = "revoked"
    REPLAYED = "replayed"
    MISMATCH = "mismatch"
    UNSIGNED = "unsigned"
    UNTRUSTED = "untrusted"


SignatureVerifier = Callable[[RemoteWorkerAuthorityEnvelope, bytes], bool]


@dataclass(frozen=True, repr=False)
class RemoteEnvelopeValidation:
    outcome: RemoteValidationOutcome
    failure_code: Optional[FailureCode]

    def __repr__(self) -> str:
        return f"RemoteEnvelopeValidation(outcome={self.outcome.value!r})"


@dataclass(frozen=True, repr=False)
class RemoteEvidenceAcceptance:
    """Local acceptance of remote evidence. Never implies task success."""

    accepted_as_evidence: bool
    failure_code: Optional[FailureCode]
    can_mark_task_success: bool = False
    can_execute_local_action: bool = False

    def __post_init__(self) -> None:
        if self.can_mark_task_success or self.can_execute_local_action:
            raise ValueError("remote_elevation_forbidden")

    def __repr__(self) -> str:
        return f"RemoteEvidenceAcceptance(accepted={self.accepted_as_evidence})"


def validate_remote_envelope(
    envelope: object,
    *,
    now_mono: float,
    expected_worker_id: str,
    expected_task_id: str,
    expected_capability: str,
    expected_targets: Tuple[str, ...],
    consumed_nonces: Mapping[str, object] | MutableSet[str],
    signature: Optional[bytes],
    verify_signature: Optional[SignatureVerifier],
) -> RemoteEnvelopeValidation:
    """Fail-closed envelope validation. Performs no network I/O."""
    if not isinstance(envelope, RemoteWorkerAuthorityEnvelope):
        return RemoteEnvelopeValidation(RemoteValidationOutcome.INVALID, FailureCode.REMOTE_INVALID)
    try:
        validate_identifier(expected_worker_id, "worker_id")
        validate_identifier(expected_task_id, "task_id")
        validate_identifier(expected_capability, "capability")
        now = validate_finite_mono(now_mono, "now_mono")
    except ValueError:
        return RemoteEnvelopeValidation(RemoteValidationOutcome.INVALID, FailureCode.REMOTE_INVALID)

    if envelope.revoked:
        return RemoteEnvelopeValidation(RemoteValidationOutcome.REVOKED, FailureCode.REMOTE_REVOKED)
    if envelope.expires_at_mono < now:
        return RemoteEnvelopeValidation(RemoteValidationOutcome.EXPIRED, FailureCode.REMOTE_EXPIRED)
    if envelope.nonce in consumed_nonces:
        return RemoteEnvelopeValidation(RemoteValidationOutcome.REPLAYED, FailureCode.REMOTE_REPLAYED)
    if (
        envelope.worker_id != expected_worker_id
        or envelope.task_id != expected_task_id
        or envelope.capability != expected_capability
        or tuple(envelope.targets) != tuple(expected_targets)
    ):
        return RemoteEnvelopeValidation(RemoteValidationOutcome.MISMATCH, FailureCode.REMOTE_MISMATCH)

    if signature is None or verify_signature is None:
        return RemoteEnvelopeValidation(RemoteValidationOutcome.UNSIGNED, FailureCode.REMOTE_INVALID)
    if not isinstance(signature, (bytes, bytearray)) or len(signature) == 0 or len(signature) > 8192:
        return RemoteEnvelopeValidation(RemoteValidationOutcome.UNSIGNED, FailureCode.REMOTE_INVALID)
    try:
        ok = bool(verify_signature(envelope, bytes(signature)))
    except Exception:
        return RemoteEnvelopeValidation(RemoteValidationOutcome.UNSIGNED, FailureCode.REMOTE_INVALID)
    if not ok:
        return RemoteEnvelopeValidation(RemoteValidationOutcome.UNSIGNED, FailureCode.REMOTE_INVALID)

    return RemoteEnvelopeValidation(RemoteValidationOutcome.VALID, None)


def consume_nonce(
    consumed_nonces: MutableSet[str],
    envelope: RemoteWorkerAuthorityEnvelope,
) -> bool:
    """Record nonce as consumed. Returns False if already present (replay)."""
    if envelope.nonce in consumed_nonces:
        return False
    consumed_nonces.add(envelope.nonce)
    return True


def accept_remote_result(
    result: object,
    *,
    envelope: RemoteWorkerAuthorityEnvelope,
    envelope_validation: RemoteEnvelopeValidation,
    response_count: int,
) -> RemoteEvidenceAcceptance:
    """Accept remote result as untrusted evidence only after envelope validation."""
    if envelope_validation.outcome is not RemoteValidationOutcome.VALID:
        return RemoteEvidenceAcceptance(
            accepted_as_evidence=False,
            failure_code=envelope_validation.failure_code or FailureCode.REMOTE_INVALID,
        )
    if not isinstance(result, RemoteWorkerResult):
        return RemoteEvidenceAcceptance(
            accepted_as_evidence=False,
            failure_code=FailureCode.REMOTE_UNTRUSTED,
        )
    if (
        result.envelope_id != envelope.envelope_id
        or result.worker_id != envelope.worker_id
        or result.task_id != envelope.task_id
    ):
        return RemoteEvidenceAcceptance(
            accepted_as_evidence=False,
            failure_code=FailureCode.REMOTE_MISMATCH,
        )
    if response_count >= envelope.max_responses:
        return RemoteEvidenceAcceptance(
            accepted_as_evidence=False,
            failure_code=FailureCode.BUDGET_EXHAUSTED,
        )
    return RemoteEvidenceAcceptance(
        accepted_as_evidence=True,
        failure_code=None,
        can_mark_task_success=False,
        can_execute_local_action=False,
    )


__all__ = [
    "RemoteEnvelopeValidation",
    "RemoteEvidenceAcceptance",
    "RemoteValidationOutcome",
    "accept_remote_result",
    "consume_nonce",
    "validate_remote_envelope",
]
