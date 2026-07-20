"""Pure scheduled-job delivery classification for Phase 3.

This module decides whether a scheduled-job state change warrants a delivery
(notification) without performing any delivery, I/O, or job mutation. It reuses
the existing ``delivery_is_meaningful_change`` and quiet-hours helpers rather
than duplicating transition or eligibility rules.

All types are frozen dataclasses or enums. No I/O, SQLite, network, subprocess,
threads, timers, sleep, logging, notification frameworks, or external execution
is present. No notification body, user content, email, calendar data, URLs,
provider responses, or secrets are produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from core.jobs.contracts import (
    IdentifierError,
    JobState,
    ScheduledJob,
    TERMINAL_JOB_STATES,
    delivery_is_meaningful_change,
    validate_fingerprint,
)
from core.jobs.quiet_hours import QuietHours, is_quiet

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
MAX_IDENTIFIER_LENGTH = 128
_MAX_NEXT_RUN_LEN = 64


class DeliveryOutcome(str, Enum):
    """Fixed classification of a delivery decision.

    - ``unchanged``: same state/revision/fingerprint; do not redeliver.
    - ``meaningful``: a state, next-run, or retry change warrants delivery.
    - ``suppressed_quiet_hours``: quiet window active; suppress, do not mutate.
    - ``terminal``: terminal transition; no further delivery.
    """

    UNCHANGED = "unchanged"
    MEANINGFUL = "meaningful"
    SUPPRESSED_QUIET_HOURS = "suppressed_quiet_hours"
    TERMINAL = "terminal"


class DeliveryAttemptStatus(str, Enum):
    """Fixed structural status of an injected delivery attempt."""

    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    FAILED = "failed"
    SUPPRESSED = "suppressed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class DeliveryAttemptResult:
    """Immutable, content-free result of an injected delivery attempt."""

    status: DeliveryAttemptStatus

    def __post_init__(self) -> None:
        if not isinstance(self.status, DeliveryAttemptStatus):
            raise DeliveryValidationError("status must be a DeliveryAttemptStatus")

    def __repr__(self) -> str:
        return f"DeliveryAttemptResult(status={self.status.value!r})"


class DeliveryValidationError(ValueError):
    """Raised for malformed delivery inputs."""


def _validate_opaque_id(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise DeliveryValidationError(f"{name} must be a non-empty string")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise DeliveryValidationError(
            f"{name} exceeds {MAX_IDENTIFIER_LENGTH} characters"
        )
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise DeliveryValidationError(f"{name} contains invalid characters")
    return value


@dataclass(frozen=True)
class DeliverySnapshot:
    """Immutable, bounded structural snapshot of a job for delivery comparison.

    Contains only structural fields needed to decide redelivery. No payload,
    target, provider, user content, or secret is present. ``fingerprint`` is the
    candidate delivery fingerprint used for change detection.
    """

    job_id: str
    state: JobState
    next_run_at: str
    attempt_count: int
    max_attempts: int
    fingerprint: Optional[str]

    def __post_init__(self) -> None:
        _validate_opaque_id("job_id", self.job_id)
        if not isinstance(self.state, JobState):
            raise DeliveryValidationError("state must be a JobState")
        if not isinstance(self.next_run_at, str) or not self.next_run_at or len(
            self.next_run_at
        ) > _MAX_NEXT_RUN_LEN:
            raise DeliveryValidationError("next_run_at must be a bounded string")
        try:
            parsed_next_run = datetime.fromisoformat(self.next_run_at)
        except ValueError as exc:
            raise DeliveryValidationError("next_run_at must be an ISO timestamp") from exc
        if parsed_next_run.tzinfo is None:
            raise DeliveryValidationError("next_run_at must be timezone-aware")
        if isinstance(self.attempt_count, bool) or not isinstance(
            self.attempt_count, int
        ):
            raise DeliveryValidationError("attempt_count must be an integer")
        if isinstance(self.max_attempts, bool) or not isinstance(
            self.max_attempts, int
        ):
            raise DeliveryValidationError("max_attempts must be an integer")
        if self.attempt_count < 0:
            raise DeliveryValidationError("attempt_count must be non-negative")
        if self.max_attempts < 1:
            raise DeliveryValidationError("max_attempts must be at least 1")
        if self.attempt_count > self.max_attempts:
            raise DeliveryValidationError("attempt_count exceeds max_attempts")
        if self.fingerprint is not None:
            try:
                validate_fingerprint(self.fingerprint)
            except IdentifierError as exc:
                raise DeliveryValidationError("fingerprint is invalid") from exc

    def __repr__(self) -> str:
        return (
            f"DeliverySnapshot(state={self.state.value!r}, "
            f"attempt_count={self.attempt_count}, "
            f"max_attempts={self.max_attempts})"
        )


def build_delivery_snapshot(job: ScheduledJob) -> DeliverySnapshot:
    """Extract a bounded structural snapshot from a ``ScheduledJob``.

    Pure extraction only; performs no I/O or mutation. The ``fingerprint`` is
    taken from the job's last delivered fingerprint so callers can compare
    against a candidate without re-reading the job.
    """
    return DeliverySnapshot(
        job_id=job.job_id,
        state=job.state,
        next_run_at=job.next_run_at.isoformat(),
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        fingerprint=job.last_delivery_fingerprint,
    )


def classify_delivery(
    job: ScheduledJob,
    candidate_fingerprint: Optional[str],
    *,
    now: datetime,
    quiet_hours: Optional[QuietHours] = None,
) -> DeliveryOutcome:
    """Classify whether a delivery should occur for ``job`` at ``now``.

    Decision order (deterministic, no mutation):

    1. Terminal state (completed/failed/cancelled) -> ``terminal``.
    2. Quiet window active at ``now`` -> ``suppressed_quiet_hours``.
    3. Candidate fingerprint equals last delivered -> ``unchanged``.
    4. Otherwise -> ``meaningful``.

    Reuses ``delivery_is_meaningful_change`` and ``is_quiet``; it does not
    duplicate transition or eligibility rules. ``now`` must be timezone-aware.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise DeliveryValidationError("now must be timezone-aware")

    # Terminal transitions are classified without further delivery.
    if job.state in TERMINAL_JOB_STATES:
        return DeliveryOutcome.TERMINAL

    # Quiet hours suppress delivery without modifying the job.
    effective_quiet_hours = quiet_hours if quiet_hours is not None else job.quiet_hours
    if effective_quiet_hours is not None and is_quiet(now, effective_quiet_hours):
        return DeliveryOutcome.SUPPRESSED_QUIET_HOURS

    # Same state/revision/fingerprint must not redeliver.
    try:
        meaningful = delivery_is_meaningful_change(job, candidate_fingerprint)
    except IdentifierError:
        raise DeliveryValidationError("fingerprint is invalid") from None
    if not meaningful:
        return DeliveryOutcome.UNCHANGED

    return DeliveryOutcome.MEANINGFUL
