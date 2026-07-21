"""Pure, immutable, side-effect-free Phase 4 task-handoff contracts.

A handoff transfers only a bounded task reference and a frozen preview.
It never transfers authority, approval IDs, or execution rights.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

from core.action_policy import ActorContext, validate_actor_context


class HandoffState(StrEnum):
    """Lifecycle states for a bounded task handoff."""

    OFFERED = "offered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_HANDOFF_STATES


class HandoffErrorCode(StrEnum):
    """Bounded safe error codes for handoff operations."""

    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    TASK_NOT_FOUND = "task_not_found"
    HANDOFF_NOT_FOUND = "handoff_not_found"
    HANDOFF_EXPIRED = "handoff_expired"
    HANDOFF_CONFLICT = "handoff_conflict"
    POLICY_DENIED = "policy_denied"


_HANDOFF_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_REQUEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

_MIN_SUMMARY_LENGTH = 1
_MAX_SUMMARY_LENGTH = 200
_HANDOFF_TTL_SECONDS = 15 * 60
_MAX_TIMESTAMP = 2 ** 53

_TERMINAL_HANDOFF_STATES = frozenset(
    {
        HandoffState.ACCEPTED,
        HandoffState.REJECTED,
        HandoffState.CANCELLED,
        HandoffState.EXPIRED,
    }
)


def _validate_identifier(value: str, pattern: re.Pattern, field: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"invalid {field}")


def _has_control_chars(value: str) -> bool:
    for char in value:
        code = ord(char)
        if code < 32 or code == 127:
            return True
    return False


def _has_unicode_cf(value: str) -> bool:
    for char in value:
        if unicodedata.category(char) == "Cf":
            return True
    return False


def _validate_summary(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("summary must be a string")
    code_points = len(value)
    if code_points < _MIN_SUMMARY_LENGTH or code_points > _MAX_SUMMARY_LENGTH:
        raise ValueError("summary length must be between 1 and 200 code points")
    if _has_control_chars(value):
        raise ValueError("summary must not contain control characters")
    if _has_unicode_cf(value):
        raise ValueError("summary must not contain Unicode format characters")
    if not value.strip():
        raise ValueError("summary must not be whitespace-only")


def _snapshot_digest(task_id: str, summary: str) -> str:
    payload = {"task_id": task_id, "summary": summary}
    canonical = json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FrozenHandoffPreview:
    """Immutable frozen preview of the task being handed off.

    The task ID and summary values are user content and are intentionally
    omitted from ``__repr__``.
    """

    task_id: str
    summary: str

    def __post_init__(self) -> None:
        _validate_identifier(self.task_id, _TASK_ID_RE, "task_id")
        if len(self.task_id) > 128:
            raise ValueError("task_id must not exceed 128 characters")
        _validate_summary(self.summary)

    @property
    def snapshot_digest(self) -> str:
        return _snapshot_digest(self.task_id, self.summary)

    def __repr__(self) -> str:
        return "FrozenHandoffPreview(<redacted>)"


@dataclass(frozen=True)
class HandoffRecord:
    """Immutable record of one bounded task handoff.

    Contains no authority, approval, or execution data. The ``__repr__``
    omits all identifiers, content, and timestamps.
    """

    handoff_id: str
    actor_id: str
    session_id: str
    task_id: str
    summary: str
    snapshot_digest: str
    state: HandoffState
    created_at: float
    expires_at: float
    request_id: str
    revision: int = 1

    def __post_init__(self) -> None:
        _validate_identifier(self.handoff_id, _HANDOFF_ID_RE, "handoff_id")
        if not isinstance(self.actor_id, str) or not self.actor_id:
            raise ValueError("actor_id is required")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("session_id is required")
        _validate_identifier(self.task_id, _TASK_ID_RE, "task_id")
        if len(self.task_id) > 128:
            raise ValueError("task_id must not exceed 128 characters")
        _validate_summary(self.summary)
        if not isinstance(self.snapshot_digest, str) or not _HEX_DIGEST_RE.fullmatch(
            self.snapshot_digest
        ):
            raise ValueError("snapshot_digest must be a 64-character hex string")
        if not isinstance(self.state, HandoffState):
            raise ValueError("state must be a HandoffState")
        _validate_identifier(self.request_id, _REQUEST_ID_RE, "request_id")

        if isinstance(self.created_at, bool) or not isinstance(self.created_at, (int, float)):
            raise ValueError("created_at must be numeric")
        if isinstance(self.expires_at, bool) or not isinstance(self.expires_at, (int, float)):
            raise ValueError("expires_at must be numeric")
        if math.isnan(self.created_at) or math.isinf(self.created_at):
            raise ValueError("created_at must be finite")
        if math.isnan(self.expires_at) or math.isinf(self.expires_at):
            raise ValueError("expires_at must be finite")
        if self.expires_at != self.created_at + _HANDOFF_TTL_SECONDS:
            raise ValueError("expires_at must equal created_at + 15 minutes")

        if not isinstance(self.revision, int) or isinstance(self.revision, bool):
            raise ValueError("revision must be an integer")
        if self.revision < 1:
            raise ValueError("revision must be positive")

    def is_expired(self, now: float) -> bool:
        """Return True if this handoff has expired at the given timestamp."""
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise ValueError("now must be numeric")
        if math.isnan(now) or math.isinf(now):
            raise ValueError("now must be finite")
        return now >= self.expires_at

    def __repr__(self) -> str:
        return f"HandoffRecord(state={self.state.value!r})"


@dataclass(frozen=True)
class HandoffResult:
    """Bounded, content-safe result of a handoff operation.

    On success, ``state`` contains the resulting terminal or non-terminal state.
    On failure, ``error_code`` contains a safe, fixed enum value.
    """

    success: bool
    request_id: Optional[str] = None
    handoff_id: Optional[str] = None
    state: Optional[HandoffState] = None
    error_code: Optional[HandoffErrorCode] = None

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise ValueError("success must be a boolean")
        if self.request_id is not None:
            _validate_identifier(self.request_id, _REQUEST_ID_RE, "request_id")
        if self.handoff_id is not None:
            _validate_identifier(self.handoff_id, _HANDOFF_ID_RE, "handoff_id")
        if self.state is not None and not isinstance(self.state, HandoffState):
            raise ValueError("state must be a HandoffState or None")
        if self.error_code is not None and not isinstance(self.error_code, HandoffErrorCode):
            raise ValueError("error_code must be a HandoffErrorCode or None")
        if self.success and self.error_code is not None:
            raise ValueError("successful results cannot include an error_code")
        if not self.success and self.error_code is None:
            raise ValueError("failed results must include an error_code")

    def __repr__(self) -> str:
        return (
            f"HandoffResult(success={self.success!r}, "
            f"state={self.state.value if self.state else None!r}, "
            f"error_code={self.error_code.value if self.error_code else None!r})"
        )


def make_offer_record(
    *,
    handoff_id: str,
    actor: ActorContext,
    preview: FrozenHandoffPreview,
    request_id: str,
    created_at: float,
) -> HandoffRecord:
    """Construct an immutable offered handoff record.

    The actor context is taken directly from the transport-injected source.
    """
    valid, _ = validate_actor_context(actor)
    if not valid:
        raise ValueError("invalid actor context")

    return HandoffRecord(
        handoff_id=handoff_id,
        actor_id=actor.actor_id,
        session_id=actor.session_id,
        task_id=preview.task_id,
        summary=preview.summary,
        snapshot_digest=preview.snapshot_digest,
        state=HandoffState.OFFERED,
        created_at=created_at,
        expires_at=created_at + _HANDOFF_TTL_SECONDS,
        request_id=request_id,
        revision=1,
    )
