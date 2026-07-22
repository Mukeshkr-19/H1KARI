"""Immutable scheduled-job value objects and pure transition/eligibility helpers.

All types here are frozen dataclasses. No I/O, timers, threads, subprocess,
network, or notification logic is present. Timezone-aware datetimes are
required wherever a timestamp is stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Optional

from core.jobs.quiet_hours import QuietHours, is_quiet

# Conservative opaque-identifier syntax: alphanumerics, dot, underscore,
# colon, hyphen. No whitespace, no control characters, no free-form content.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
_PROPOSAL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
MAX_IDENTIFIER_LENGTH = 128
MAX_FINGERPRINT_LENGTH = 256


class JobState(str, Enum):
    """Lifecycle states for a scheduled job.

    Terminal states are ``completed``, ``failed``, and ``cancelled``.
    """

    SCHEDULED = "scheduled"
    PAUSED = "paused"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Allowed forward transitions. A state may always repeat itself (idempotent).
transition_table: dict[JobState, frozenset[JobState]] = {
    JobState.SCHEDULED: frozenset(
        {JobState.SCHEDULED, JobState.PAUSED, JobState.RUNNING, JobState.CANCELLED}
    ),
    JobState.PAUSED: frozenset(
        {JobState.PAUSED, JobState.SCHEDULED, JobState.CANCELLED}
    ),
    JobState.RUNNING: frozenset(
        {
            JobState.RUNNING,
            JobState.INTERRUPTED,
            JobState.COMPLETED,
            JobState.FAILED,
            JobState.CANCELLED,
        }
    ),
    JobState.INTERRUPTED: frozenset(
        {
            JobState.INTERRUPTED,
            JobState.PAUSED,
            JobState.SCHEDULED,
            JobState.FAILED,
            JobState.CANCELLED,
        }
    ),
    JobState.COMPLETED: frozenset({JobState.COMPLETED}),
    JobState.FAILED: frozenset({JobState.FAILED}),
    JobState.CANCELLED: frozenset({JobState.CANCELLED}),
}

TERMINAL_JOB_STATES = frozenset(
    {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
)


class TransitionError(ValueError):
    """Raised when a job lifecycle transition is not permitted."""


class RetryBudgetExhausted(ValueError):
    """Raised when no further attempt is permitted for a job."""


class IdentifierError(ValueError):
    """Raised for invalid opaque job identifiers or fingerprints."""


def _validate_identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise IdentifierError(f"{name} must be a non-empty string")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise IdentifierError(
            f"{name} exceeds {MAX_IDENTIFIER_LENGTH} characters"
        )
    if not _IDENTIFIER_PATTERN.match(value):
        raise IdentifierError(f"{name} contains invalid characters")


def _validate_fingerprint(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise IdentifierError(f"{name} must be a non-empty string")
    if len(value) > MAX_FINGERPRINT_LENGTH:
        raise IdentifierError(
            f"{name} exceeds {MAX_FINGERPRINT_LENGTH} characters"
        )
    if not _IDENTIFIER_PATTERN.match(value):
        raise IdentifierError(f"{name} contains invalid characters")


def _validate_proposal_id(value: object) -> None:
    if not isinstance(value, str) or not _PROPOSAL_ID_PATTERN.fullmatch(value):
        raise IdentifierError("proposal_id is invalid")


def validate_fingerprint(value: object) -> str:
    """Public validator for a delivery fingerprint.

    Returns the value unchanged when it is a valid non-empty opaque
    string; raises ``IdentifierError`` otherwise. Use this instead of
    the private ``_validate_fingerprint`` helper.
    """
    _validate_fingerprint("fingerprint", value)
    return value  # type: ignore[return-value]


def can_transition(current: JobState, target: JobState) -> bool:
    """Return whether ``target`` is a permitted transition from ``current``.

    Repeating the current state is always allowed (idempotent).
    """
    if not isinstance(current, JobState) or not isinstance(target, JobState):
        raise TransitionError("transition endpoints must be JobState members")
    return target in transition_table.get(current, frozenset())


@dataclass(frozen=True)
class ScheduledJob:
    """Immutable description of a single scheduled job.

    No user payload (raw text, email body, query, calendar content, or other
    free-form content) is stored on this object. Identifiers are opaque and
    validated against a conservative syntax.
    """

    job_id: str
    actor_id: str
    session_id: str
    action: str
    proposal_id: str
    state: JobState
    next_run_at: datetime
    created_at: datetime
    updated_at: datetime
    attempt_count: int = 0
    max_attempts: int = 1
    quiet_hours: Optional[QuietHours] = None
    last_delivery_fingerprint: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_identifier("job_id", self.job_id)
        _validate_identifier("actor_id", self.actor_id)
        _validate_identifier("session_id", self.session_id)
        _validate_identifier("action", self.action)
        _validate_proposal_id(self.proposal_id)
        if not isinstance(self.state, JobState):
            raise TransitionError("state must be a JobState member")
        for name in ("next_run_at", "created_at", "updated_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.next_run_at < self.created_at:
            raise ValueError("next_run_at must not precede created_at")
        if isinstance(self.attempt_count, bool) or not isinstance(
            self.attempt_count, int
        ):
            raise ValueError("attempt_count must be an integer")
        if isinstance(self.max_attempts, bool) or not isinstance(
            self.max_attempts, int
        ):
            raise ValueError("max_attempts must be an integer")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must be non-negative")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt_count exceeds max_attempts")
        if self.last_delivery_fingerprint is not None:
            _validate_fingerprint(
                "last_delivery_fingerprint", self.last_delivery_fingerprint
            )

    def __repr__(self) -> str:
        return (
            f"ScheduledJob(job_id={self.job_id!r}, state={self.state.value!r}, "
            f"attempt_count={self.attempt_count}, max_attempts={self.max_attempts})"
        )

    def with_state(self, target: JobState, *, updated_at: datetime) -> "ScheduledJob":
        """Return a copy in ``target`` state with ``updated_at`` set.

        Raises ``TransitionError`` when the transition is not permitted, and
        ``ValueError`` when ``updated_at`` is not timezone-aware or precedes
        the current ``updated_at``.
        """
        if not can_transition(self.state, target):
            raise TransitionError(
                f"invalid job transition: {self.state.value} -> {target.value}"
            )
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if updated_at < self.updated_at:
            raise ValueError("updated_at must not precede current updated_at")
        return replace(self, state=target, updated_at=updated_at)

    def with_next_run(
        self, next_run_at: datetime, *, updated_at: datetime
    ) -> "ScheduledJob":
        if not isinstance(next_run_at, datetime) or next_run_at.tzinfo is None:
            raise ValueError("next_run_at must be timezone-aware")
        if next_run_at < self.created_at:
            raise ValueError("next_run_at must not precede created_at")
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if updated_at < self.updated_at:
            raise ValueError("updated_at must not precede current updated_at")
        return replace(self, next_run_at=next_run_at, updated_at=updated_at)

    def with_attempt(self, *, updated_at: datetime) -> "ScheduledJob":
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if updated_at < self.updated_at:
            raise ValueError("updated_at must not precede current updated_at")
        if not retry_budget_remains(self):
            raise RetryBudgetExhausted(
                f"no retry remains for job {self.job_id!r} "
                f"(attempt_count={self.attempt_count}, max_attempts={self.max_attempts})"
            )
        return replace(
            self,
            attempt_count=self.attempt_count + 1,
            updated_at=updated_at,
        )

    def with_delivery_fingerprint(
        self, fingerprint: Optional[str], *, updated_at: datetime
    ) -> "ScheduledJob":
        if fingerprint is not None:
            _validate_fingerprint("fingerprint", fingerprint)
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if updated_at < self.updated_at:
            raise ValueError("updated_at must not precede current updated_at")
        return replace(
            self, last_delivery_fingerprint=fingerprint, updated_at=updated_at
        )


def execution_is_eligible(job: ScheduledJob, now: datetime) -> bool:
    """Return whether the job may execute at ``now``.

    Eligibility requires:
    - ``now`` is timezone-aware
    - the job is in ``SCHEDULED`` state
    - ``now`` is at or after ``next_run_at``
    - the job is not currently inside its quiet window
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if job.state is not JobState.SCHEDULED:
        return False
    if now < job.next_run_at:
        return False
    if job.quiet_hours is not None and is_quiet(now, job.quiet_hours):
        return False
    return True


def retry_budget_remains(job: ScheduledJob) -> bool:
    """Return whether at least one further attempt is permitted."""
    return job.attempt_count < job.max_attempts


def delivery_is_meaningful_change(
    job: ScheduledJob, candidate_fingerprint: Optional[str]
) -> bool:
    """Return whether ``candidate_fingerprint`` is a meaningful delivery.

    A meaningful delivery requires a valid non-empty fingerprint that differs
    from the last delivered fingerprint. ``None`` is never a meaningful
    delivery. No semantic comparison or hashing is performed here.
    """
    if candidate_fingerprint is None:
        return False
    _validate_fingerprint("candidate_fingerprint", candidate_fingerprint)
    return job.last_delivery_fingerprint != candidate_fingerprint
