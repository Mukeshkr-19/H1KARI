"""Remote worker coordinator optional adapter.

Composes the authority-envelope contracts from ``core.phase6_agent.remote_worker``
with a durable nonce-store interface and bounded job execution.  Default
construction leaves the adapter disabled.  Remote responses remain evidence only.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, FrozenSet, Mapping, Optional, Sequence, Tuple

from core.phase6_agent.remote_worker import (
    RemoteEnvelopeValidation,
    RemoteEvidenceAcceptance,
    RemoteValidationOutcome,
    RemoteWorkerAuthorityEnvelope,
    RemoteWorkerResult,
    accept_remote_result,
    validate_remote_envelope,
)

from core.phase6_agent.contracts import FailureCode

from core.phase6_adapters.contracts import AdapterException, AdapterReason, AdapterState


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
    CANCELLED = "cancelled"
    NOT_SUBMITTED = "not_submitted"
    TIMESTAMP_INVALID = "timestamp_invalid"


class RemoteWorkerAdapterOutcome(StrEnum):
    """Fixed outcomes for the remote worker coordinator."""

    ALLOW = "allow"
    DENY = "deny"
    UNAVAILABLE = "unavailable"


class RemoteWorkerJobState(StrEnum):
    """Bounded lifecycle states for a remote-worker job."""

    RECEIVED = "received"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    ACTIVE = "active"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED_EVIDENCE = "completed_evidence"
    EXPIRED = "expired"
    REVOKED = "revoked"
    QUARANTINED = "quarantined"
    FAILED = "failed"


@dataclass(frozen=True)
class RemoteWorkerCoordinatorConfig:
    """Explicit configuration enabling the remote worker coordinator."""

    max_concurrent_jobs: int = 8
    max_responses_per_envelope: int = 32
    max_response_size_bytes: int = 1_048_576
    max_elapsed_seconds: float = 3600.0
    max_future_skew_seconds: float = 60.0
    max_job_history: int = 64

    def __post_init__(self) -> None:
        for name, value in (
            ("max_concurrent_jobs", self.max_concurrent_jobs),
            ("max_responses_per_envelope", self.max_responses_per_envelope),
            ("max_response_size_bytes", self.max_response_size_bytes),
        ):
            _reject_bool(value, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"invalid {name}")
        for name, value in (
            ("max_elapsed_seconds", self.max_elapsed_seconds),
            ("max_future_skew_seconds", self.max_future_skew_seconds),
        ):
            _reject_bool_nan(value, name)
            if not 0 < value <= 86_400:
                raise ValueError(f"invalid {name}")
        _reject_bool(self.max_job_history, "max_job_history")
        if not isinstance(self.max_job_history, int) or not 1 <= self.max_job_history <= 256:
            raise ValueError("invalid max_job_history")

    def __repr__(self) -> str:
        return "RemoteWorkerCoordinatorConfig()"


@dataclass(frozen=True)
class CancellationAcknowledgement:
    """Bounded evidence that a cancellation was acknowledged."""

    envelope_id: str
    acknowledged_at: float
    acknowledgement_id: str

    def __repr__(self) -> str:
        return "CancellationAcknowledgement()"


@dataclass
class _JobRecord:
    envelope: RemoteWorkerAuthorityEnvelope
    state: RemoteWorkerJobState
    created_at: float
    responses: int = 0
    retries: int = 0
    cancellation: Optional[CancellationAcknowledgement] = None


class NonceStoreInterface(ABC):
    """Injected durable nonce-store interface (no real implementation)."""

    @abstractmethod
    def is_consumed(self, nonce: str) -> bool:
        ...

    @abstractmethod
    def consume(self, nonce: str) -> bool:
        ...


class LocalAuthorizerInterface(ABC):
    """Injected local action authorizer interface."""

    @abstractmethod
    def authorize_local(self, envelope: RemoteWorkerAuthorityEnvelope, result: RemoteWorkerResult) -> bool:
        ...


class _InMemoryNonceStore(NonceStoreInterface):
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_consumed(self, nonce: str) -> bool:
        return nonce in self._seen

    def consume(self, nonce: str) -> bool:
        if nonce in self._seen:
            return False
        self._seen.add(nonce)
        return True


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
        self._nonce_store = nonce_store if nonce_store is not None else _InMemoryNonceStore()
        self._authorizer = authorizer
        self._clock = clock
        self._id_factory = id_factory
        self._jobs: dict[str, _JobRecord] = {}
        self._quarantined: FrozenSet[str] = frozenset()
        self._revoked: FrozenSet[str] = frozenset()
        self._validated: dict[str, RemoteWorkerAuthorityEnvelope] = {}

    @property
    def state(self) -> AdapterState:
        return AdapterState.ENABLED if self._config is not None else AdapterState.DISABLED

    def _now(self) -> float:
        if self._clock is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        now = self._clock() if callable(self._clock) else float(self._clock)
        if not isinstance(now, (int, float)) or isinstance(now, bool):
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return float(now)

    def _next_id(self) -> str:
        if self._id_factory is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return str(self._id_factory() if callable(self._id_factory) else self._id_factory)

    def _consume_nonce(self, nonce: str) -> bool:
        return self._nonce_store.consume(nonce)

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
            return RemoteEnvelopeValidation(RemoteValidationOutcome.INVALID, FailureCode.REMOTE_INVALID)

        # Never compare an envelope only with itself; check trusted expected scope first.
        if envelope.worker_id in self._quarantined:
            return RemoteEnvelopeValidation(RemoteValidationOutcome.INVALID, FailureCode.REMOTE_INVALID)
        if envelope.envelope_id in self._revoked or envelope.revoked:
            return RemoteEnvelopeValidation(RemoteValidationOutcome.REVOKED, FailureCode.REMOTE_REVOKED)

        # Replay check before consuming.
        if self._nonce_store.is_consumed(envelope.nonce):
            return RemoteEnvelopeValidation(RemoteValidationOutcome.REPLAYED, FailureCode.REMOTE_REPLAYED)

        validation = validate_remote_envelope(
            envelope,
            now_mono=self._now(),
            expected_worker_id=expected_worker_id,
            expected_task_id=expected_task_id,
            expected_capability=expected_capability,
            expected_targets=expected_targets,
            consumed_nonces=set(),  # durable store handles replay
            signature=signature,
            verify_signature=verify_signature,
        )

        # Consume nonce for any outcome to prevent replay of invalid/expired envelopes.
        if not self._consume_nonce(envelope.nonce):
            return RemoteEnvelopeValidation(RemoteValidationOutcome.REPLAYED, FailureCode.REMOTE_REPLAYED)

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

        # Submit only a stored, successfully validated envelope.
        validated = self._validated.get(envelope.envelope_id)
        if validated != envelope:
            return RemoteWorkerAdapterOutcome.DENY
        if envelope.worker_id in self._quarantined:
            return RemoteWorkerAdapterOutcome.DENY
        if envelope.envelope_id in self._revoked or envelope.revoked:
            return RemoteWorkerAdapterOutcome.DENY

        now = self._now()
        if now >= envelope.expires_at_mono:
            return RemoteWorkerAdapterOutcome.DENY
        if len(self._jobs) >= self._config.max_concurrent_jobs:
            return RemoteWorkerAdapterOutcome.DENY

        job = _JobRecord(
            envelope=envelope,
            state=RemoteWorkerJobState.SUBMITTED,
            created_at=now,
        )
        self._jobs[envelope.envelope_id] = job
        job.state = RemoteWorkerJobState.ACTIVE
        self._cleanup_jobs(now)
        return RemoteWorkerAdapterOutcome.ALLOW

    def _cleanup_jobs(self, now: float) -> None:
        """Evict terminal/expired jobs deterministically to keep history bounded."""
        if not self._jobs:
            return
        assert self._config is not None
        max_history = self._config.max_job_history
        # Always drop jobs whose elapsed window has passed.
        expired = [
            eid for eid, job in self._jobs.items()
            if now - job.created_at > self._config.max_elapsed_seconds
        ]
        for eid in expired:
            self._jobs.pop(eid, None)
        if len(self._jobs) <= max_history:
            return
        terminal_states = {
            RemoteWorkerJobState.COMPLETED_EVIDENCE,
            RemoteWorkerJobState.CANCELLED,
            RemoteWorkerJobState.EXPIRED,
            RemoteWorkerJobState.REVOKED,
            RemoteWorkerJobState.QUARANTINED,
            RemoteWorkerJobState.FAILED,
        }
        # Evict oldest terminal jobs first.
        terminal = sorted(
            ((eid, job) for eid, job in self._jobs.items() if job.state in terminal_states),
            key=lambda item: item[1].created_at,
        )
        evict_count = len(self._jobs) - max_history
        for eid, _ in terminal[:evict_count]:
            self._jobs.pop(eid, None)
        # If still over the bound, evict oldest jobs regardless of state.
        if len(self._jobs) > max_history:
            oldest = sorted(self._jobs.items(), key=lambda item: item[1].created_at)
            for eid, _ in oldest[:len(self._jobs) - max_history]:
                self._jobs.pop(eid, None)

    def request_cancel(
        self,
        envelope: RemoteWorkerAuthorityEnvelope,
        acknowledgement: CancellationAcknowledgement,
    ) -> RemoteWorkerAdapterOutcome:
        """Record a bounded cancellation acknowledgement for a job."""
        if self.state is AdapterState.DISABLED:
            return RemoteWorkerAdapterOutcome.UNAVAILABLE
        job = self._jobs.get(envelope.envelope_id)
        if job is None or job.envelope != envelope:
            return RemoteWorkerAdapterOutcome.DENY
        if job.state in (RemoteWorkerJobState.CANCELLED, RemoteWorkerJobState.COMPLETED_EVIDENCE, RemoteWorkerJobState.FAILED):
            return RemoteWorkerAdapterOutcome.DENY
        if acknowledgement.envelope_id != envelope.envelope_id:
            return RemoteWorkerAdapterOutcome.DENY
        now = self._now()
        if acknowledgement.acknowledged_at > now or acknowledgement.acknowledged_at < job.created_at:
            return RemoteWorkerAdapterOutcome.DENY
        job.cancellation = acknowledgement
        job.state = RemoteWorkerJobState.CANCELLED
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
        if job is None or job.envelope != envelope:
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.REMOTE_INVALID)
        if self._validated.get(envelope.envelope_id) != envelope:
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.REMOTE_INVALID)
        if envelope.envelope_id in self._revoked or envelope.revoked:
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.REMOTE_REVOKED)
        if envelope.worker_id in self._quarantined:
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.REMOTE_UNTRUSTED)
        if job.state is RemoteWorkerJobState.CANCELLED:
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.CANCELLED)
        if job.state is not RemoteWorkerJobState.ACTIVE:
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.REMOTE_INVALID)

        now = self._now()
        if now >= envelope.expires_at_mono or now - job.created_at > self._config.max_elapsed_seconds:
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.BUDGET_EXHAUSTED)
        if result.observed_at_mono < job.created_at or result.observed_at_mono > now + self._config.max_future_skew_seconds:
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.OBSERVATION_STALE)

        if job.responses >= min(envelope.max_responses, self._config.max_responses_per_envelope):
            return RemoteEvidenceAcceptance(accepted_as_evidence=False, failure_code=FailureCode.BUDGET_EXHAUSTED)

        # Byte-size guard, not character count.
        if len(result.summary.encode("utf-8")) > self._config.max_response_size_bytes:
            return RemoteEvidenceAcceptance(
                accepted_as_evidence=False,
                failure_code=FailureCode.OBSERVATION_OVERSIZED,
            )

        acceptance = accept_remote_result(
            result,
            envelope=envelope,
            envelope_validation=validation,
            response_count=job.responses,
        )
        if not acceptance.accepted_as_evidence:
            return acceptance

        if self._authorizer is not None:
            if not self._authorizer.authorize_local(envelope, result):
                return RemoteEvidenceAcceptance(
                    accepted_as_evidence=False,
                    failure_code=FailureCode.DENIED,
                )

        job.responses += 1
        if job.responses >= envelope.max_responses:
            job.state = RemoteWorkerJobState.COMPLETED_EVIDENCE
        self._cleanup_jobs(now)
        return acceptance

    def revoke_envelope(self, envelope_id: str) -> None:
        self._revoked = frozenset(self._revoked | {envelope_id})

    def quarantine_worker(self, worker_id: str) -> None:
        self._quarantined = frozenset(self._quarantined | {worker_id})

    def __repr__(self) -> str:
        return "RemoteWorkerCoordinator()"


def _reject_bool(value: object, name: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be a boolean")


def _reject_bool_nan(value: object, name: str) -> None:
    _reject_bool(value, name)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"invalid {name}")
