"""Remote worker coordinator optional adapter.

Composes the authority-envelope contracts from ``core.phase6_agent.remote_worker``
with a durable nonce-store interface and bounded job execution.  Default
construction leaves the adapter disabled.  Remote responses remain evidence only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any, FrozenSet, Mapping, Optional, Sequence, Tuple

from core.phase6_agent.remote_worker import (
    RemoteEnvelopeValidation,
    RemoteEvidenceAcceptance,
    RemoteValidationOutcome,
    RemoteWorkerAuthorityEnvelope,
    RemoteWorkerResult,
    accept_remote_result,
    consume_nonce,
    validate_remote_envelope,
)

from core.phase6_agent.contracts import FailureCode

from core.phase6_adapters.contracts import AdapterException, AdapterOutcome, AdapterReason, AdapterState


class RemoteWorkerAdapterReason(StrEnum):
    """Fixed reason codes for the remote worker coordinator."""

    OK = "ok"
    DISABLED = "disabled"
    INVALID_CONFIGURATION = "invalid_configuration"
    MISSING_DEPENDENCY = "missing_dependency"
    EXPIRED = "expired"
    REVOKED = "revoked"
    REPLAYED = "replayed"
    MISMATCH = "mismatch"
    UNSIGNED = "unsigned"
    UNTRUSTED = "untrusted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    QUARANTINED = "quarantined"
    LOCAL_ACTION_FORBIDDEN = "local_action_forbidden"
    SCOPE_WIDENING = "scope_widening"


class RemoteWorkerAdapterOutcome(StrEnum):
    """Fixed outcomes for the remote worker coordinator."""

    ALLOW = "allow"
    DENY = "deny"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RemoteWorkerCoordinatorConfig:
    """Explicit configuration enabling the remote worker coordinator."""

    max_concurrent_jobs: int = 8
    max_responses_per_envelope: int = 32
    max_response_size_bytes: int = 1_048_576
    max_elapsed_seconds: float = 3600.0
    max_retries: int = 3

    def __post_init__(self) -> None:
        for name, value in (
            ("max_concurrent_jobs", self.max_concurrent_jobs),
            ("max_responses_per_envelope", self.max_responses_per_envelope),
            ("max_response_size_bytes", self.max_response_size_bytes),
        ):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"invalid {name}")
        if not isinstance(self.max_elapsed_seconds, (int, float)) or not math.isfinite(self.max_elapsed_seconds) or not 0 < self.max_elapsed_seconds <= 86_400:
            raise ValueError("invalid max_elapsed_seconds")
        if not isinstance(self.max_retries, int) or not 0 <= self.max_retries <= 16:
            raise ValueError("invalid max_retries")

    def __repr__(self) -> str:
        return "RemoteWorkerCoordinatorConfig()"


class NonceStoreInterface:
    """Injected durable nonce-store interface (no real implementation)."""

    def is_consumed(self, nonce: str) -> bool:
        raise NotImplementedError

    def consume(self, nonce: str) -> bool:
        raise NotImplementedError


class LocalAuthorizerInterface:
    """Injected local action authorizer interface."""

    def authorize_local(self, envelope: RemoteWorkerAuthorityEnvelope, result: RemoteWorkerResult) -> bool:
        raise NotImplementedError


class RemoteWorkerCoordinator:
    """Disabled-by-default remote worker coordinator.

    All durable nonce storage, local authorization, signature verification,
    clock, and ID factory behavior is injected.  Remote workers cannot mark
    tasks successful or execute local actions directly; results are only
    evidence returned to the local authorizer.
    """

    def __init__(
        self,
        *,
        config: Optional[RemoteWorkerCoordinatorConfig] = None,
        nonce_store: Optional[NonceStoreInterface] = None,
        authorizer: Optional[LocalAuthorizerInterface] = None,
        clock: Optional[object] = None,
        id_factory: Optional[object] = None,
    ) -> None:
        self._config = config
        self._nonce_store = nonce_store
        self._authorizer = authorizer
        self._clock = clock
        self._id_factory = id_factory
        self._jobs: dict[str, dict[str, Any]] = {}
        self._quarantined: set[str] = set()
        self._revoked: set[str] = set()
        self._consumed_local_nonces: set[str] = set()
        self._validated: dict[str, RemoteWorkerAuthorityEnvelope] = {}

    @property
    def state(self) -> AdapterState:
        return AdapterState.ENABLED if self._config is not None else AdapterState.DISABLED

    def _now(self) -> float:
        if self._clock is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        now = self._clock() if callable(self._clock) else float(self._clock)
        if not isinstance(now, (int, float)):
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return float(now)

    def _next_id(self) -> str:
        if self._id_factory is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return str(self._id_factory() if callable(self._id_factory) else self._id_factory)

    def _consume_nonce(self, nonce: str) -> bool:
        """Consume a nonce. Return False if already consumed (replay)."""
        if self._nonce_store is not None:
            if not self._nonce_store.consume(nonce):
                return False
        self._consumed_local_nonces.add(nonce)
        return True

    def validate_envelope(
        self,
        envelope: RemoteWorkerAuthorityEnvelope,
        signature: Optional[bytes],
        verify_signature: Any,
        *,
        expected_worker_id: str,
        expected_task_id: str,
        expected_capability: str,
        expected_targets: Tuple[str, ...],
    ) -> RemoteEnvelopeValidation:
        """Fail-closed envelope validation with durable nonce consumption."""
        if self.state is AdapterState.DISABLED:
            return RemoteEnvelopeValidation(RemoteValidationOutcome.INVALID, None)
        if self._nonce_store is not None:
            if self._nonce_store.is_consumed(envelope.nonce):
                return RemoteEnvelopeValidation(RemoteValidationOutcome.REPLAYED, None)
        elif envelope.nonce in self._consumed_local_nonces:
            return RemoteEnvelopeValidation(RemoteValidationOutcome.REPLAYED, None)
        if envelope.revoked or envelope.envelope_id in self._revoked:
            if not self._consume_nonce(envelope.nonce):
                return RemoteEnvelopeValidation(RemoteValidationOutcome.REPLAYED, None)
            return RemoteEnvelopeValidation(RemoteValidationOutcome.REVOKED, None)
        validation = validate_remote_envelope(
            envelope,
            now_mono=self._now(),
            expected_worker_id=expected_worker_id,
            expected_task_id=expected_task_id,
            expected_capability=expected_capability,
            expected_targets=expected_targets,
            consumed_nonces=self._consumed_local_nonces,
            signature=signature,
            verify_signature=verify_signature,
        )
        # Consume the nonce for any outcome that reaches validation to prevent
        # later replay, even from invalid/expired/mismatched envelopes.
        if not self._consume_nonce(envelope.nonce):
            return RemoteEnvelopeValidation(RemoteValidationOutcome.REPLAYED, None)
        if validation.outcome is RemoteValidationOutcome.VALID:
            self._validated[envelope.envelope_id] = envelope
        return validation

    def submit_job(
        self,
        envelope: RemoteWorkerAuthorityEnvelope,
    ) -> RemoteWorkerAdapterOutcome:
        """Submit a bounded job for a remote worker."""
        if self.state is AdapterState.DISABLED:
            return RemoteWorkerAdapterOutcome.UNAVAILABLE
        assert self._config is not None
        validated = self._validated.get(envelope.envelope_id)
        if validated != envelope or envelope.worker_id in self._quarantined:
            return RemoteWorkerAdapterOutcome.DENY
        if envelope.envelope_id in self._revoked:
            return RemoteWorkerAdapterOutcome.DENY
        if len(self._jobs) >= self._config.max_concurrent_jobs:
            return RemoteWorkerAdapterOutcome.DENY
        now = self._now()
        if now >= envelope.expires_at_mono:
            return RemoteWorkerAdapterOutcome.DENY
        elapsed = 0.0
        for job in self._jobs.values():
            if job["envelope"].envelope_id == envelope.envelope_id:
                elapsed = now - job["created_at"]
                break
        if elapsed > self._config.max_elapsed_seconds:
            return RemoteWorkerAdapterOutcome.DENY
        job = {
            "envelope": envelope,
            "responses": 0,
            "retries": 0,
            "created_at": now,
        }
        self._jobs[envelope.envelope_id] = job
        return RemoteWorkerAdapterOutcome.ALLOW

    def accept_result(
        self,
        envelope: RemoteWorkerAuthorityEnvelope,
        result: RemoteWorkerResult,
        validation: RemoteEnvelopeValidation,
    ) -> RemoteEvidenceAcceptance:
        """Accept a remote result as evidence only."""
        if self.state is AdapterState.DISABLED:
            return RemoteEvidenceAcceptance(
                accepted_as_evidence=False,
                failure_code=None,
            )
        assert self._config is not None
        job = self._jobs.get(envelope.envelope_id)
        if job is None or job["envelope"] != envelope or self._validated.get(envelope.envelope_id) != envelope:
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.REMOTE_INVALID)
        if self._now() - job["created_at"] > self._config.max_elapsed_seconds:
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.BUDGET_EXHAUSTED)
        if job["responses"] >= min(envelope.max_responses, self._config.max_responses_per_envelope):
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.BUDGET_EXHAUSTED)
        # Byte-size guard, not character count.
        if len(result.summary.encode("utf-8")) > self._config.max_response_size_bytes:
            return RemoteEvidenceAcceptance(
                accepted_as_evidence=False,
                failure_code=None,
            )
        acceptance = accept_remote_result(
            result,
            envelope=envelope,
            envelope_validation=validation,
            response_count=job["responses"],
        )
        if acceptance.accepted_as_evidence:
            if self._authorizer is not None:
                if not self._authorizer.authorize_local(envelope, result):
                    return RemoteEvidenceAcceptance(
                        accepted_as_evidence=False,
                        failure_code=FailureCode.DENIED,
                    )
            job["responses"] += 1
        return acceptance

    def revoke_envelope(self, envelope_id: str) -> None:
        self._revoked.add(envelope_id)

    def quarantine_worker(self, worker_id: str) -> None:
        self._quarantined.add(worker_id)

    def __repr__(self) -> str:
        return "RemoteWorkerCoordinator()"
