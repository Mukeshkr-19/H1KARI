"""Bounded Phase 3 scheduled-job lifecycle controller.

Provides pause, resume, and cancel operations with CAS via the store, appends
content-free audit events, and ensures cross-session operations reveal no job
existence (a fixed safe result is returned when the job is missing or belongs
to another scope).

No timers, threads, subprocess, network, provider, notification, or external
execution is present. No actor/session/proposal identifiers, payloads, targets,
provider data, or exception text are surfaced through errors or repr.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Optional

from core.jobs.audit import AuditEvent, AuditReasonCode
from core.jobs.audit_store import ScheduledJobAuditStore
from core.jobs.contracts import (
    IdentifierError,
    JobState,
    ScheduledJob,
    TERMINAL_JOB_STATES,
    validate_fingerprint,
)
from core.jobs.delivery import (
    DeliveryAttemptResult,
    DeliveryAttemptStatus,
    DeliveryOutcome,
    classify_delivery,
)
from core.jobs.store import ScheduledJobStore


class LifecycleOutcomeCode(StrEnum):
    """Fixed, safe outcome codes for lifecycle operations."""

    OK = "ok"
    NOT_FOUND = "not_found"
    INVALID_STATE = "invalid_state"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class LifecycleResult:
    """Immutable, content-free result of a lifecycle operation.

    Contains only the outcome code and the new state when successful. No
    actor/session/proposal identifiers, payloads, or exception text are
    surfaced.
    """

    code: LifecycleOutcomeCode
    new_state: Optional[JobState] = None

    def __repr__(self) -> str:
        return (
            f"LifecycleResult(code={self.code.value!r}, "
            f"new_state={self.new_state.value if self.new_state else None!r})"
        )


class JobLifecycleError(ValueError):
    """Fixed, safe error for lifecycle operations."""


class JobLifecycleController:
    """Pause, resume, and cancel scheduled jobs with CAS and audit appends."""

    def __init__(
        self,
        store: ScheduledJobStore,
        audit_store: ScheduledJobAuditStore,
        clock: Callable[[], datetime],
        event_id_factory: Callable[[], str],
    ) -> None:
        if not isinstance(store, ScheduledJobStore):
            raise TypeError("store must be a ScheduledJobStore")
        if not isinstance(audit_store, ScheduledJobAuditStore):
            raise TypeError("audit_store must be a ScheduledJobAuditStore")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(event_id_factory):
            raise TypeError("event_id_factory must be callable")
        self._store = store
        self._audit_store = audit_store
        self._clock = clock
        self._event_id_factory = event_id_factory

    def _now(self) -> datetime:
        try:
            value = self._clock()
        except Exception:
            raise JobLifecycleError("clock unavailable") from None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise JobLifecycleError("clock must return a timezone-aware datetime")
        return value

    def _generate_event_id(self) -> str:
        try:
            value = self._event_id_factory()
        except Exception:
            raise JobLifecycleError("event id factory failed") from None
        if not isinstance(value, str) or not value:
            raise JobLifecycleError("event id factory produced an invalid id")
        return value

    def _append_event(
        self,
        *,
        job: ScheduledJob,
        previous_state: JobState,
        new_state: JobState,
        reason: AuditReasonCode,
        occurred_at: datetime,
    ) -> None:
        try:
            event = AuditEvent(
                event_id=self._generate_event_id(),
                job_id=job.job_id,
                action=job.action,
                previous_state=previous_state,
                new_state=new_state,
                occurred_at=occurred_at,
                reason_code=reason,
            )
            self._audit_store.append(event)
        except Exception:
            raise JobLifecycleError("audit append failed") from None

    def _load(
        self,
        job_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> tuple[Optional[ScheduledJob], Optional[LifecycleResult]]:
        try:
            job = self._store.get(
                job_id, actor_id=actor_id, session_id=session_id
            )
        except Exception:
            return None, LifecycleResult(code=LifecycleOutcomeCode.UNAVAILABLE)
        if job is None:
            # Cross-session or missing: reveal no job existence.
            return None, LifecycleResult(code=LifecycleOutcomeCode.NOT_FOUND)
        return job, None

    def _deactivate_if_active(
        self,
        job_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> Optional[JobState]:
        """Bounded exact-CAS fallback used only after audit compensation fails."""
        for _ in range(3):
            try:
                current = self._store.get(
                    job_id, actor_id=actor_id, session_id=session_id
                )
            except Exception:
                return None
            if current is None:
                return None
            if current.state in TERMINAL_JOB_STATES or current.state in (
                JobState.PAUSED,
                JobState.INTERRUPTED,
            ):
                return current.state
            try:
                cancelled = self._store.transition(
                    job_id,
                    expected_state=current.state,
                    new_state=JobState.CANCELLED,
                    expected_updated_at=current.updated_at,
                    updated_at=current.updated_at,
                    actor_id=actor_id,
                    session_id=session_id,
                )
            except Exception:
                cancelled = None
            if cancelled is not None:
                return JobState.CANCELLED
        return None

    def pause(
        self,
        job_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> LifecycleResult:
        """Transition ``scheduled`` -> ``paused`` with CAS.

        Idempotent when already paused. Cross-session or missing jobs return
        ``NOT_FOUND`` without revealing existence.
        """
        job, load_error = self._load(
            job_id, actor_id=actor_id, session_id=session_id
        )
        if load_error is not None:
            return load_error
        assert job is not None
        if job.state is JobState.PAUSED:
            return LifecycleResult(
                code=LifecycleOutcomeCode.OK, new_state=JobState.PAUSED
            )
        if job.state is not JobState.SCHEDULED:
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)

        now = self._now()
        try:
            updated = self._store.pause(
                job_id,
                expected_updated_at=job.updated_at,
                updated_at=now,
                actor_id=actor_id,
                session_id=session_id,
            )
        except Exception:
            return LifecycleResult(code=LifecycleOutcomeCode.UNAVAILABLE)
        if updated is None:
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)
        try:
            self._append_event(
                job=updated,
                previous_state=JobState.SCHEDULED,
                new_state=JobState.PAUSED,
                reason=AuditReasonCode.CONTROL,
                occurred_at=now,
            )
        except JobLifecycleError:
            # PAUSED is a documented fail-closed state: it cannot be claimed.
            return LifecycleResult(
                code=LifecycleOutcomeCode.UNAVAILABLE, new_state=JobState.PAUSED
            )
        return LifecycleResult(
            code=LifecycleOutcomeCode.OK, new_state=JobState.PAUSED
        )

    def resume(
        self,
        job_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> LifecycleResult:
        """Transition ``paused`` or ``interrupted`` -> ``scheduled`` with CAS.

        Idempotent when already scheduled. Resume never duplicates a prior
        completed run because ``COMPLETED`` is terminal and not a valid source
        state for this transition.
        """
        job, load_error = self._load(
            job_id, actor_id=actor_id, session_id=session_id
        )
        if load_error is not None:
            return load_error
        assert job is not None
        if job.state is JobState.SCHEDULED:
            return LifecycleResult(
                code=LifecycleOutcomeCode.OK, new_state=JobState.SCHEDULED
            )
        if job.state not in (JobState.PAUSED, JobState.INTERRUPTED):
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)

        now = self._now()
        try:
            if job.state is JobState.PAUSED:
                updated = self._store.resume(
                    job_id,
                    expected_updated_at=job.updated_at,
                    updated_at=now,
                    actor_id=actor_id,
                    session_id=session_id,
                )
            else:
                updated = self._store.transition(
                    job_id,
                    expected_state=JobState.INTERRUPTED,
                    new_state=JobState.SCHEDULED,
                    expected_updated_at=job.updated_at,
                    updated_at=now,
                    actor_id=actor_id,
                    session_id=session_id,
                )
        except Exception:
            return LifecycleResult(code=LifecycleOutcomeCode.UNAVAILABLE)
        if updated is None:
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)
        try:
            self._append_event(
                job=updated,
                previous_state=job.state,
                new_state=JobState.SCHEDULED,
                reason=AuditReasonCode.CONTROL,
                occurred_at=now,
            )
        except JobLifecycleError:
            # Never leave an unaudited resume eligible for execution. PAUSED is
            # the common fail-closed state for either supported source state.
            try:
                compensated = self._store.transition(
                    job_id,
                    expected_state=JobState.SCHEDULED,
                    new_state=JobState.PAUSED,
                    expected_updated_at=updated.updated_at,
                    updated_at=now,
                    actor_id=actor_id,
                    session_id=session_id,
                )
            except Exception:
                compensated = None
            compensated_state = (
                JobState.PAUSED
                if compensated is not None
                else self._deactivate_if_active(
                    job_id, actor_id=actor_id, session_id=session_id
                )
            )
            return LifecycleResult(
                code=LifecycleOutcomeCode.UNAVAILABLE,
                new_state=compensated_state,
            )
        return LifecycleResult(
            code=LifecycleOutcomeCode.OK, new_state=JobState.SCHEDULED
        )

    def cancel(
        self,
        job_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> LifecycleResult:
        """Transition any active state -> ``cancelled`` with CAS.

        Idempotent when already cancelled. Cross-session or missing jobs return
        ``NOT_FOUND`` without revealing existence.
        """
        job, load_error = self._load(
            job_id, actor_id=actor_id, session_id=session_id
        )
        if load_error is not None:
            return load_error
        assert job is not None
        if job.state is JobState.CANCELLED:
            return LifecycleResult(
                code=LifecycleOutcomeCode.OK, new_state=JobState.CANCELLED
            )
        if job.state in (JobState.COMPLETED, JobState.FAILED):
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)

        now = self._now()
        try:
            updated = self._store.cancel(
                job_id,
                expected_updated_at=job.updated_at,
                updated_at=now,
                actor_id=actor_id,
                session_id=session_id,
            )
        except Exception:
            return LifecycleResult(code=LifecycleOutcomeCode.UNAVAILABLE)
        if updated is None:
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)
        try:
            self._append_event(
                job=updated,
                previous_state=job.state,
                new_state=JobState.CANCELLED,
                reason=AuditReasonCode.CONTROL,
                occurred_at=now,
            )
        except JobLifecycleError:
            # CANCELLED is terminal and therefore fail-closed even when its
            # audit append is temporarily unavailable.
            return LifecycleResult(
                code=LifecycleOutcomeCode.UNAVAILABLE,
                new_state=JobState.CANCELLED,
            )
        return LifecycleResult(
            code=LifecycleOutcomeCode.OK, new_state=JobState.CANCELLED
        )

    def acknowledge_delivery(
        self,
        job_id: str,
        *,
        actor_id: str,
        session_id: str,
        candidate_fingerprint: object,
        delivery_result: object,
    ) -> LifecycleResult:
        """Record a positively acknowledged meaningful delivery fingerprint.

        Updates ``last_delivery_fingerprint`` only when the candidate is valid,
        classification is ``meaningful``, quiet hours do not suppress delivery,
        and ``delivery_result`` is an acknowledged ``DeliveryAttemptResult``.
        Unchanged, suppressed, rejected, failed, stale, and cross-session cases
        leave the fingerprint untouched.
        """
        job, load_error = self._load(
            job_id, actor_id=actor_id, session_id=session_id
        )
        if load_error is not None:
            return load_error
        assert job is not None

        try:
            fingerprint = validate_fingerprint(candidate_fingerprint)
        except IdentifierError:
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)
        except Exception:
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)

        if not isinstance(delivery_result, DeliveryAttemptResult):
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)
        if delivery_result.status is not DeliveryAttemptStatus.ACKNOWLEDGED:
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)

        try:
            now = self._now()
        except JobLifecycleError:
            return LifecycleResult(code=LifecycleOutcomeCode.UNAVAILABLE)

        try:
            outcome = classify_delivery(job, fingerprint, now=now)
        except Exception:
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)
        if outcome is not DeliveryOutcome.MEANINGFUL:
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)

        try:
            updated = self._store.update_delivery_fingerprint(
                job_id,
                fingerprint=fingerprint,
                expected_updated_at=job.updated_at,
                updated_at=now,
                actor_id=actor_id,
                session_id=session_id,
            )
        except Exception:
            return LifecycleResult(code=LifecycleOutcomeCode.UNAVAILABLE)
        if updated is None:
            return LifecycleResult(code=LifecycleOutcomeCode.INVALID_STATE)
        try:
            self._append_event(
                job=updated,
                previous_state=updated.state,
                new_state=updated.state,
                reason=AuditReasonCode.DELIVERED,
                occurred_at=now,
            )
        except JobLifecycleError:
            # Revert the exact acknowledged revision so a missing audit event
            # is never reported as a successful acknowledgement.
            try:
                reverted = self._store.update_delivery_fingerprint(
                    job_id,
                    fingerprint=job.last_delivery_fingerprint,
                    expected_updated_at=updated.updated_at,
                    updated_at=now,
                    actor_id=actor_id,
                    session_id=session_id,
                )
            except Exception:
                reverted = None
            fallback_state = None
            if reverted is None:
                fallback_state = self._deactivate_if_active(
                    job_id, actor_id=actor_id, session_id=session_id
                )
            return LifecycleResult(
                code=LifecycleOutcomeCode.UNAVAILABLE,
                new_state=(reverted.state if reverted is not None else fallback_state),
            )
        return LifecycleResult(code=LifecycleOutcomeCode.OK, new_state=updated.state)
