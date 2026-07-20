"""Immutable, privacy-safe scheduled-job audit events for Phase 3.

This module defines a bounded, append-only audit record for scheduled-job
lifecycle changes. An audit event carries only structural identifiers and
state, never actor/session/proposal identifiers, payloads, targets, provider
data, user content, exception text, or approval identifiers.

All types are frozen dataclasses. No I/O, SQLite, network, subprocess,
threads, timers, sleep, logging, notification, or external execution is
present. Timezone-aware datetimes are required wherever a timestamp is stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from core.jobs.contracts import JobState, can_transition

# Conservative opaque-identifier syntax, identical to core.jobs.contracts.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
MAX_IDENTIFIER_LENGTH = 128
# Bounded timestamp window: reject absurd or malformed instants.
_MIN_OCCURRED_YEAR = 2000
_MAX_OCCURRED_YEAR = 2100


class AuditReasonCode(str, Enum):
    """Fixed, non-attributable reason codes for an audit event.

    These codes describe the kind of lifecycle change only. They never carry
    payload, identifiers beyond the job, or provider/exception detail.
    """

    CREATED = "created"
    STATE_TRANSITION = "state_transition"
    DELIVERED = "delivered"
    RETRY_EXHAUSTED = "retry_exhausted"
    TERMINAL = "terminal"
    CONTROL = "control"
    QUIET_SUPPRESSED = "quiet_suppressed"


class AuditTransitionError(ValueError):
    """Raised when an audit event describes an impossible state transition."""


class AuditValidationError(ValueError):
    """Raised for malformed audit identifiers, timestamps, or oversized values."""


class AuditStoreError(ValueError):
    """Fixed, safe error for audit-store operations.

    Public store methods raise only this error. Messages never include
    database paths, SQL, identifiers, or SQLite exception text.
    """


# Canonical persisted value sets, reused by the audit store schema CHECK
# constraints so malformed states/reason codes are rejected at write time.
VALID_REASON_CODES = tuple(rc.value for rc in AuditReasonCode)
VALID_JOB_STATES = tuple(s.value for s in JobState)


def _validate_opaque_id(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise AuditValidationError(f"{name} must be a non-empty string")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise AuditValidationError(
            f"{name} exceeds {MAX_IDENTIFIER_LENGTH} characters"
        )
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise AuditValidationError(f"{name} contains invalid characters")
    return value


def _validate_occurred_at(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AuditValidationError("occurred_at must be timezone-aware")
    if value.year < _MIN_OCCURRED_YEAR or value.year > _MAX_OCCURRED_YEAR:
        raise AuditValidationError("occurred_at is outside the supported range")
    return value


@dataclass(frozen=True)
class AuditEvent:
    """Immutable, bounded audit record for a scheduled-job lifecycle change.

    Privacy contract: only ``event_id``, ``job_id``, ``action``, the two
    states, ``occurred_at``, and a fixed ``reason_code`` are stored. No
    actor/session/proposal identifiers, payloads, targets, provider data, user
    content, exception text, or approval identifiers are present.
    """

    event_id: str
    job_id: str
    action: str
    previous_state: Optional[JobState]
    new_state: Optional[JobState]
    occurred_at: datetime
    reason_code: AuditReasonCode

    def __post_init__(self) -> None:
        _validate_opaque_id("event_id", self.event_id)
        _validate_opaque_id("job_id", self.job_id)
        _validate_opaque_id("action", self.action)
        if self.previous_state is not None and not isinstance(
            self.previous_state, JobState
        ):
            raise AuditValidationError("previous_state must be a JobState or None")
        if not isinstance(self.new_state, JobState):
            raise AuditValidationError("new_state must be a JobState")
        validate_transition(self.previous_state, self.new_state)
        _validate_occurred_at(self.occurred_at)
        if not isinstance(self.reason_code, AuditReasonCode):
            raise AuditValidationError("reason_code must be an AuditReasonCode")

    def __repr__(self) -> str:
        return (
            f"AuditEvent(event_id={self.event_id!r}, job_id={self.job_id!r}, "
            f"reason={self.reason_code.value!r}, "
            f"new_state={self.new_state.value if self.new_state else None!r})"
        )


def validate_transition(
    previous: Optional[JobState], new: Optional[JobState]
) -> bool:
    """Return whether the described transition is permitted by JobState rules.

    Pure validation against the existing ``can_transition`` table. A ``None``
    ``previous`` denotes creation and only requires ``new`` to be a valid
    ``JobState``. Raises ``AuditTransitionError`` for impossible transitions and
    ``AuditValidationError`` for malformed endpoints.
    """
    if new is None or not isinstance(new, JobState):
        raise AuditValidationError("new_state must be a JobState")
    if previous is None:
        return True
    if not isinstance(previous, JobState):
        raise AuditValidationError("previous_state must be a JobState or None")
    if not can_transition(previous, new):
        raise AuditTransitionError(
            f"invalid job transition: {previous.value} -> {new.value}"
        )
    return True
