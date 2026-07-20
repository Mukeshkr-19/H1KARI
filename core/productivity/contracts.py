"""Pure, immutable, side-effect-free Phase 3 productivity-action contracts."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple

from core.action_policy import ActorContext, validate_actor_context


class ProductivityAction(StrEnum):
    """Bounded action identifiers for the minimum Phase 3 surface."""

    BROWSER_RESEARCH = "browser.research"
    EMAIL_DRAFT = "email.draft"
    CALENDAR_READ = "calendar.read"
    CALENDAR_DRAFT = "calendar.draft"
    REMINDER_CREATE = "reminder.create"
    SCHEDULED_JOB_MANAGE = "scheduled_job.manage"
    SKILL_EXECUTE = "skill.execute"
    MCP_EXECUTE = "mcp.execute"


class TargetKind(StrEnum):
    """Typed target kinds for Phase 3 actions."""

    WEB_DOMAIN = "web_domain"
    EMAIL_RECIPIENT = "email_recipient"
    CALENDAR = "calendar"
    REMINDER_LIST = "reminder_list"
    SKILL = "skill"
    MCP_SERVER = "mcp_server"


class ExecutionStatus(StrEnum):
    """Bounded execution statuses for an action result."""

    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_MAX_TARGET_LENGTH = 4096
_MAX_EMAIL_LENGTH = 320
_MAX_PREVIEW_KEY_LENGTH = 64
_MAX_PREVIEW_LABEL_LENGTH = 128
_MAX_PREVIEW_VALUE_LENGTH = 4096
_MAX_IDENTIFIER_LENGTH = 80

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$"
)
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+-]{1,64}"
    r"@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


def _has_control_chars(value: str, *, allow_newline_tab: bool = False) -> bool:
    allowed = {"\n", "\t"} if allow_newline_tab else set()
    for char in value:
        if char in allowed:
            continue
        code = ord(char)
        if code < 32 or code == 127:
            return True
    return False


def _validate_identifier(value: Optional[str], field: str) -> None:
    if value is None:
        raise ValueError(f"{field} is required")
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")


@dataclass(frozen=True)
class ActionTarget:
    """Immutable typed target for a Phase 3 action.

    The actual target value is intentionally excluded from ``__repr__`` to avoid
    leaking user content into logs or exceptions.
    """

    kind: TargetKind
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TargetKind):
            raise ValueError("invalid target kind")
        if not isinstance(self.value, str):
            raise ValueError("target value must be a string")
        if not self.value:
            raise ValueError("target value must not be empty")
        if "\x00" in self.value:
            raise ValueError("target value must not contain NUL")
        if _has_control_chars(self.value):
            raise ValueError("target value must not contain control characters")

        if self.kind is TargetKind.WEB_DOMAIN:
            normalized = self.value.strip().lower()
            if not normalized or len(normalized) > 253:
                raise ValueError("invalid web domain")
            if not _DOMAIN_RE.fullmatch(normalized):
                raise ValueError("invalid web domain format")
            object.__setattr__(self, "value", normalized)
        elif self.kind is TargetKind.EMAIL_RECIPIENT:
            if len(self.value) > _MAX_EMAIL_LENGTH:
                raise ValueError("email recipient too long")
            if not _EMAIL_RE.fullmatch(self.value):
                raise ValueError("invalid email recipient format")
        else:
            if len(self.value) > _MAX_TARGET_LENGTH:
                raise ValueError("target value too long")

    def __repr__(self) -> str:
        return f"ActionTarget(kind={self.kind.value!r})"


@dataclass(frozen=True)
class PreviewField:
    """Immutable user-facing preview field.

    The display ``value`` is excluded from ``__repr__`` because it may contain
    user content.
    """

    key: str
    label: str
    value: str
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not _IDENTIFIER_RE.fullmatch(self.key):
            raise ValueError("invalid preview key")
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("preview label must not be empty")
        if len(self.label) > _MAX_PREVIEW_LABEL_LENGTH:
            raise ValueError("preview label too long")
        if not isinstance(self.value, str):
            raise ValueError("preview value must be a string")
        if "\x00" in self.value:
            raise ValueError("preview value must not contain NUL")
        if _has_control_chars(self.value, allow_newline_tab=True):
            raise ValueError("preview value must not contain control characters")
        if len(self.value) > _MAX_PREVIEW_VALUE_LENGTH:
            raise ValueError("preview value too long")
        if not isinstance(self.truncated, bool):
            raise ValueError("truncated must be a boolean")

    def __repr__(self) -> str:
        return (
            f"PreviewField(key={self.key!r}, label={self.label!r}, "
            f"truncated={self.truncated!r})"
        )


@dataclass(frozen=True)
class ActionProposal:
    """Immutable Phase 3 action proposal.

    Contains no execution or persistence methods and no secret-bearing repr.
    """

    proposal_id: str
    action: ProductivityAction
    actor: ActorContext
    targets: Tuple[ActionTarget, ...]
    preview_fields: Tuple[PreviewField, ...]
    created_at: float
    expires_at: float
    task_id: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_identifier(self.proposal_id, "proposal_id")

        if not isinstance(self.action, ProductivityAction):
            raise ValueError("invalid action")

        valid, _ = validate_actor_context(self.actor)
        if not valid:
            raise ValueError("invalid actor context")

        if not isinstance(self.targets, tuple):
            raise ValueError("targets must be a tuple")
        if not isinstance(self.preview_fields, tuple):
            raise ValueError("preview_fields must be a tuple")

        seen_targets: set[tuple[TargetKind, str]] = set()
        for target in self.targets:
            if not isinstance(target, ActionTarget):
                raise ValueError("targets must contain ActionTarget instances")
            key = (target.kind, target.value)
            if key in seen_targets:
                raise ValueError(f"duplicate target: {target.kind.value}")
            seen_targets.add(key)

        seen_keys: set[str] = set()
        for field in self.preview_fields:
            if not isinstance(field, PreviewField):
                raise ValueError("preview_fields must contain PreviewField instances")
            if field.key in seen_keys:
                raise ValueError(f"duplicate preview key: {field.key}")
            seen_keys.add(field.key)

        if self.task_id is not None:
            _validate_identifier(self.task_id, "task_id")

        if isinstance(self.created_at, bool) or not isinstance(self.created_at, (int, float)):
            raise ValueError("created_at must be numeric")
        if isinstance(self.expires_at, bool) or not isinstance(self.expires_at, (int, float)):
            raise ValueError("expires_at must be numeric")
        if math.isnan(self.created_at) or math.isinf(self.created_at):
            raise ValueError("created_at must be finite")
        if math.isnan(self.expires_at) or math.isinf(self.expires_at):
            raise ValueError("expires_at must be finite")
        if self.created_at >= self.expires_at:
            raise ValueError("created_at must be less than expires_at")

    def is_expired(self, now: float) -> bool:
        """Return True if this proposal has expired at the given timestamp.

        ``now`` must be a finite numeric timestamp. Booleans, NaN, and infinite
        values are rejected.
        """
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise ValueError("now must be numeric")
        if math.isnan(now) or math.isinf(now):
            raise ValueError("now must be finite")
        return now >= self.expires_at

    def user_preview(self) -> dict:
        """Return the explicit user-facing preview structure.

        Excludes internal actor IDs, session IDs, and other hidden metadata.
        """
        return {
            "proposal_id": self.proposal_id,
            "action": self.action.value,
            "targets": [
                {"kind": target.kind.value, "value": target.value}
                for target in self.targets
            ],
            "preview_fields": [
                {
                    "key": field.key,
                    "label": field.label,
                    "value": field.value,
                    "truncated": field.truncated,
                }
                for field in self.preview_fields
            ],
            "task_id": self.task_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    def __repr__(self) -> str:
        return (
            f"ActionProposal("
            f"targets={len(self.targets)}, "
            f"preview_fields={len(self.preview_fields)})"
        )


@dataclass(frozen=True)
class ExecutionResult:
    """Minimal immutable execution-result contract."""

    proposal_id: str
    status: ExecutionStatus
    code: str
    audit_id: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_identifier(self.proposal_id, "proposal_id")
        if not isinstance(self.status, ExecutionStatus):
            raise ValueError("invalid status")
        _validate_identifier(self.code, "code")
        if self.audit_id is not None:
            _validate_identifier(self.audit_id, "audit_id")

    def __repr__(self) -> str:
        return (
            f"ExecutionResult(proposal_id={self.proposal_id!r}, "
            f"status={self.status.value!r}, code={self.code!r})"
        )
