"""Bounded Phase 3 scheduled-job creation.

Builds immutable creation requests bound to an exact actor/session/action/
proposal, generates canonical server-side job IDs via an injected factory,
persists the resulting ``ScheduledJob`` in ``SCHEDULED`` state, and appends a
content-free ``CREATED`` audit event.

No timers, threads, subprocess, network, provider, notification, or external
execution is present. No actor/session/proposal identifiers, payloads, targets,
provider data, or exception text are surfaced through errors or repr.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from core.jobs.quiet_hours import QuietHours
from core.jobs.store import ScheduledJobStore

# Conservative opaque-identifier syntax shared with the contracts module.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
_MAX_IDENTIFIER_LENGTH = 128
_MAX_ACTION_LENGTH = 128
_MAX_PROPOSAL_ID_LENGTH = 80
_MAX_MAX_ATTEMPTS = 100
_MAX_NEXT_RUN_LEAD_SECONDS = 365 * 24 * 3600  # one year


class JobCreationError(ValueError):
    """Fixed, safe error for job creation. No content or identifiers reflected."""


def _validate_opaque_id(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise JobCreationError(f"{name} is required")
    if len(value) > _MAX_IDENTIFIER_LENGTH:
        raise JobCreationError(f"{name} is too long")
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise JobCreationError(f"{name} is malformed")
    return value


def _validate_proposal_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise JobCreationError("proposal_id is required")
    if len(value) > _MAX_PROPOSAL_ID_LENGTH:
        raise JobCreationError("proposal_id is too long")
    if not re.fullmatch(r"^[a-z0-9][a-z0-9_.-]{0,79}$", value):
        raise JobCreationError("proposal_id is malformed")
    return value


def _validate_action(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise JobCreationError("action is required")
    if len(value) > _MAX_ACTION_LENGTH:
        raise JobCreationError("action is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise JobCreationError("action contains control characters")
    return value


def _validate_aware_dt(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise JobCreationError(f"{name} must be timezone-aware")
    return value


@dataclass(frozen=True)
class JobCreationRequest:
    """Immutable creation request bound to exact actor/session/action/proposal.

    No payload, target, provider data, or user content is stored.
    ``meaningful_change_fingerprint`` is an optional candidate for later
    delivery acknowledgement; it is never copied into
    ``last_delivery_fingerprint`` at creation time.
    """

    actor_id: str
    session_id: str
    action: str
    proposal_id: str
    next_run_at: datetime
    max_attempts: int = 1
    quiet_hours: Optional[QuietHours] = None
    meaningful_change_fingerprint: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_opaque_id("actor_id", self.actor_id)
        _validate_opaque_id("session_id", self.session_id)
        _validate_action(self.action)
        _validate_proposal_id(self.proposal_id)
        _validate_aware_dt("next_run_at", self.next_run_at)
        if isinstance(self.max_attempts, bool) or not isinstance(
            self.max_attempts, int
        ):
            raise JobCreationError("max_attempts must be an integer")
        if self.max_attempts < 1 or self.max_attempts > _MAX_MAX_ATTEMPTS:
            raise JobCreationError("max_attempts is out of range")
        if self.quiet_hours is not None and not isinstance(
            self.quiet_hours, QuietHours
        ):
            raise JobCreationError("quiet_hours must be a QuietHours instance")
        if self.meaningful_change_fingerprint is not None:
            try:
                validate_fingerprint(self.meaningful_change_fingerprint)
            except IdentifierError as exc:
                raise JobCreationError("fingerprint is invalid") from exc

    def __repr__(self) -> str:
        return f"JobCreationRequest(max_attempts={self.max_attempts})"


class JobCreationService:
    """Create scheduled jobs with server-generated IDs and audit appends."""

    def __init__(
        self,
        store: ScheduledJobStore,
        audit_store: ScheduledJobAuditStore,
        clock: Callable[[], datetime],
        job_id_factory: Callable[[], str],
        event_id_factory: Callable[[], str],
    ) -> None:
        if not isinstance(store, ScheduledJobStore):
            raise TypeError("store must be a ScheduledJobStore")
        if not isinstance(audit_store, ScheduledJobAuditStore):
            raise TypeError("audit_store must be a ScheduledJobAuditStore")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(job_id_factory):
            raise TypeError("job_id_factory must be callable")
        if not callable(event_id_factory):
            raise TypeError("event_id_factory must be callable")
        self._store = store
        self._audit_store = audit_store
        self._clock = clock
        self._job_id_factory = job_id_factory
        self._event_id_factory = event_id_factory

    def _now(self) -> datetime:
        try:
            value = self._clock()
        except Exception:
            raise JobCreationError("clock unavailable") from None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise JobCreationError("clock must return a timezone-aware datetime")
        return value

    def _generate_job_id(self) -> str:
        try:
            value = self._job_id_factory()
        except Exception:
            raise JobCreationError("job id factory failed") from None
        try:
            return _validate_opaque_id("job_id", value)
        except JobCreationError as exc:
            raise JobCreationError("job id factory produced an invalid id") from exc

    def _generate_event_id(self) -> str:
        try:
            value = self._event_id_factory()
        except Exception:
            raise JobCreationError("event id factory failed") from None
        try:
            return _validate_opaque_id("event_id", value)
        except JobCreationError as exc:
            raise JobCreationError(
                "event id factory produced an invalid id"
            ) from exc

    def _deactivate_after_failed_audit(
        self,
        job_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> bool:
        """Best-effort bounded compensation that never reveals job details."""
        for _ in range(3):
            try:
                current = self._store.get(
                    job_id, actor_id=actor_id, session_id=session_id
                )
            except Exception:
                return False
            if current is None or current.state in TERMINAL_JOB_STATES:
                return True
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
                return True
        return False

    def create(self, request: JobCreationRequest) -> ScheduledJob:
        """Persist a new ``SCHEDULED`` job and append a ``CREATED`` audit event.

        Returns the persisted ``ScheduledJob``. Raises ``JobCreationError`` on
        any validation, clock, factory, persistence, or audit failure. No
        content, identifiers, paths, or exception text is reflected. If the
        audit append fails after insert, the exact newly inserted revision is
        removed via actor/session-scoped CAS compensation.
        """
        if not isinstance(request, JobCreationRequest):
            raise JobCreationError("creation request is required")

        now = self._now()
        # Bound the lead window so a runaway clock cannot schedule far-future jobs.
        if request.next_run_at - now > timedelta(seconds=_MAX_NEXT_RUN_LEAD_SECONDS):
            raise JobCreationError("next_run_at is too far in the future")

        job_id = self._generate_job_id()
        event_id = self._generate_event_id()

        job = ScheduledJob(
            job_id=job_id,
            actor_id=request.actor_id,
            session_id=request.session_id,
            action=request.action,
            proposal_id=request.proposal_id,
            state=JobState.SCHEDULED,
            next_run_at=request.next_run_at,
            created_at=now,
            updated_at=now,
            attempt_count=0,
            max_attempts=request.max_attempts,
            quiet_hours=request.quiet_hours,
            last_delivery_fingerprint=None,
        )

        try:
            self._store.add(job)
        except Exception:
            raise JobCreationError("job persistence failed") from None

        try:
            event = AuditEvent(
                event_id=event_id,
                job_id=job_id,
                action=request.action,
                previous_state=None,
                new_state=JobState.SCHEDULED,
                occurred_at=now,
                reason_code=AuditReasonCode.CREATED,
            )
            self._audit_store.append(event)
        except Exception:
            cleaned = False
            try:
                cleaned = self._store.remove_if_unmodified(
                    job_id,
                    actor_id=request.actor_id,
                    session_id=request.session_id,
                    expected_updated_at=job.updated_at,
                    expected_state=JobState.SCHEDULED,
                )
            except Exception:
                cleaned = False
            if not cleaned:
                cleaned = self._deactivate_after_failed_audit(
                    job_id,
                    actor_id=request.actor_id,
                    session_id=request.session_id,
                )
            if not cleaned:
                raise JobCreationError("audit append failed; cleanup incomplete") from None
            raise JobCreationError("audit append failed") from None

        return job
