"""Immutable Phase 6 bounded-agent contracts.

Pure value objects and validators. No I/O, network, subprocess, model, or
database access. Model/planner output grants zero authority; every executable
action must be authorized by an injected policy boundary outside this module.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Tuple


_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_ACTOR_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_HEX_NONCE_RE = re.compile(r"^[0-9a-f]{16,128}$")

_MAX_SHORT_TEXT = 256
_MAX_DESCRIPTION = 512
_MAX_OBSERVATION = 8192
_MAX_TARGETS = 32
_MAX_DECLARED_TOOLS = 32
_MAX_EVENTS = 256

# Hard budget ceilings (callers may only request lower values).
HARD_MAX_PLANNING_ATTEMPTS = 16
HARD_MAX_TOTAL_STEPS = 64
HARD_MAX_TOOL_ACTIONS = 32
HARD_MAX_CORRECTIONS = 16
HARD_MAX_CONSECUTIVE_FAILURES = 16
HARD_MAX_OBSERVATION_LENGTH = _MAX_OBSERVATION
HARD_MAX_EVENT_HISTORY = _MAX_EVENTS
HARD_MAX_ELAPSED_MS = 3_600_000
HARD_MAX_REMOTE_RESPONSES = 32

DEFAULT_PLANNING_ATTEMPTS = 3
DEFAULT_TOTAL_STEPS = 8
DEFAULT_TOOL_ACTIONS = 5
DEFAULT_CORRECTIONS = 3
DEFAULT_CONSECUTIVE_FAILURES = 3
DEFAULT_OBSERVATION_LENGTH = 4096
DEFAULT_EVENT_HISTORY = 64
DEFAULT_ELAPSED_MS = 60_000
DEFAULT_REMOTE_RESPONSES = 8


class LoopState(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    AUTHORIZING = "authorizing"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    ACTING = "acting"
    OBSERVING = "observing"
    CORRECTING = "correcting"
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXHAUSTED = "exhausted"


class ActionOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    RETRYABLE_FAILURE = "retryable_failure"
    NON_RETRYABLE_FAILURE = "non_retryable_failure"
    CANCELLED = "cancelled"


class AuthorizationOutcome(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ConfidenceCategory(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


class FailureCode(StrEnum):
    """Fixed privacy-safe failure codes. Never embed raw input or exceptions."""

    INVALID_INPUT = "invalid_input"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_INVALID = "approval_invalid"
    APPROVAL_STALE = "approval_stale"
    APPROVAL_DUPLICATE = "approval_duplicate"
    APPROVAL_MISMATCH = "approval_mismatch"
    AUDIT_FAILED = "audit_failed"
    EXECUTOR_FAILED = "executor_failed"
    OBSERVATION_INVALID = "observation_invalid"
    OBSERVATION_DUPLICATE = "observation_duplicate"
    OBSERVATION_STALE = "observation_stale"
    OBSERVATION_OVERSIZED = "observation_oversized"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    NON_RETRYABLE = "non_retryable"
    SCOPE_EXPANSION = "scope_expansion"
    UNDECLARED_TOOL = "undeclared_tool"
    DUPLICATE_ACTION = "duplicate_action"
    INTERNAL = "internal"
    REMOTE_INVALID = "remote_invalid"
    REMOTE_EXPIRED = "remote_expired"
    REMOTE_REVOKED = "remote_revoked"
    REMOTE_REPLAYED = "remote_replayed"
    REMOTE_MISMATCH = "remote_mismatch"
    REMOTE_UNTRUSTED = "remote_untrusted"


class EventKind(StrEnum):
    STATE = "state"
    PLAN = "plan"
    AUTHORIZE = "authorize"
    APPROVAL = "approval"
    AUDIT = "audit"
    ACT = "act"
    OBSERVE = "observe"
    CORRECT = "correct"
    REMOTE = "remote"
    TERMINAL = "terminal"


def _reject(code: str) -> ValueError:
    return ValueError(code)


def validate_identifier(value: object, field: str = "id") -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise _reject(f"invalid_{field}")
    if "*" in value or value.endswith(".") or ".." in value:
        raise _reject(f"invalid_{field}")
    return value


def validate_actor_id(value: object, field: str = "actor_id") -> str:
    if not isinstance(value, str) or not _ACTOR_RE.fullmatch(value):
        raise _reject(f"invalid_{field}")
    return value


def _has_controls(value: str) -> bool:
    for ch in value:
        code = ord(ch)
        if code < 32 or code == 127 or code == 0:
            return True
    return False


def validate_text(
    value: object,
    *,
    field: str,
    max_length: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise _reject(f"invalid_{field}")
    if _has_controls(value):
        raise _reject(f"invalid_{field}")
    if not allow_empty and (not value or value.strip() == ""):
        raise _reject(f"invalid_{field}")
    if len(value) > max_length:
        raise _reject(f"invalid_{field}")
    if "*" == value or value.startswith("*") or value.endswith("*"):
        raise _reject(f"invalid_{field}")
    return value


def validate_finite_mono(value: object, field: str = "timestamp") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _reject(f"invalid_{field}")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise _reject(f"invalid_{field}")
    return number


def validate_positive_int(
    value: object,
    *,
    field: str,
    maximum: int,
    minimum: int = 1,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _reject(f"invalid_{field}")
    if value < minimum or value > maximum:
        raise _reject(f"invalid_{field}")
    return value


def validate_target(value: object, field: str = "target") -> str:
    text = validate_text(value, field=field, max_length=4096)
    if any(token in text for token in ("*", "?", "://../", "\x00")):
        raise _reject(f"invalid_{field}")
    return text


@dataclass(frozen=True, repr=False)
class LoopBudget:
    """Caller-provided budgets clamped below hard maximums."""

    max_planning_attempts: int = DEFAULT_PLANNING_ATTEMPTS
    max_total_steps: int = DEFAULT_TOTAL_STEPS
    max_tool_actions: int = DEFAULT_TOOL_ACTIONS
    max_corrections: int = DEFAULT_CORRECTIONS
    max_consecutive_failures: int = DEFAULT_CONSECUTIVE_FAILURES
    max_observation_length: int = DEFAULT_OBSERVATION_LENGTH
    max_event_history: int = DEFAULT_EVENT_HISTORY
    max_elapsed_ms: int = DEFAULT_ELAPSED_MS
    max_remote_responses: int = DEFAULT_REMOTE_RESPONSES

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_planning_attempts",
            validate_positive_int(
                self.max_planning_attempts,
                field="max_planning_attempts",
                maximum=HARD_MAX_PLANNING_ATTEMPTS,
            ),
        )
        object.__setattr__(
            self,
            "max_total_steps",
            validate_positive_int(
                self.max_total_steps, field="max_total_steps", maximum=HARD_MAX_TOTAL_STEPS
            ),
        )
        object.__setattr__(
            self,
            "max_tool_actions",
            validate_positive_int(
                self.max_tool_actions, field="max_tool_actions", maximum=HARD_MAX_TOOL_ACTIONS
            ),
        )
        object.__setattr__(
            self,
            "max_corrections",
            validate_positive_int(
                self.max_corrections, field="max_corrections", maximum=HARD_MAX_CORRECTIONS
            ),
        )
        object.__setattr__(
            self,
            "max_consecutive_failures",
            validate_positive_int(
                self.max_consecutive_failures,
                field="max_consecutive_failures",
                maximum=HARD_MAX_CONSECUTIVE_FAILURES,
            ),
        )
        object.__setattr__(
            self,
            "max_observation_length",
            validate_positive_int(
                self.max_observation_length,
                field="max_observation_length",
                maximum=HARD_MAX_OBSERVATION_LENGTH,
                minimum=1,
            ),
        )
        object.__setattr__(
            self,
            "max_event_history",
            validate_positive_int(
                self.max_event_history,
                field="max_event_history",
                maximum=HARD_MAX_EVENT_HISTORY,
            ),
        )
        object.__setattr__(
            self,
            "max_elapsed_ms",
            validate_positive_int(
                self.max_elapsed_ms, field="max_elapsed_ms", maximum=HARD_MAX_ELAPSED_MS
            ),
        )
        object.__setattr__(
            self,
            "max_remote_responses",
            validate_positive_int(
                self.max_remote_responses,
                field="max_remote_responses",
                maximum=HARD_MAX_REMOTE_RESPONSES,
            ),
        )

    def __repr__(self) -> str:
        return (
            f"LoopBudget(steps={self.max_total_steps}, "
            f"actions={self.max_tool_actions}, corrections={self.max_corrections})"
        )


@dataclass(frozen=True, repr=False)
class LoopRequest:
    """Bounded loop request. Declared tools are an exact allowlist."""

    request_id: str
    actor_id: str
    session_id: str
    goal_summary: str
    declared_tools: Tuple[str, ...]
    budget: LoopBudget
    source: str = "phase6"

    def __post_init__(self) -> None:
        validate_identifier(self.request_id, "request_id")
        validate_actor_id(self.actor_id, "actor_id")
        validate_actor_id(self.session_id, "session_id")
        validate_text(self.goal_summary, field="goal_summary", max_length=_MAX_DESCRIPTION)
        validate_identifier(self.source, "source")
        if not isinstance(self.budget, LoopBudget):
            raise _reject("invalid_budget")
        if not isinstance(self.declared_tools, tuple):
            raise _reject("invalid_declared_tools")
        if not self.declared_tools or len(self.declared_tools) > _MAX_DECLARED_TOOLS:
            raise _reject("invalid_declared_tools")
        seen: set[str] = set()
        cleaned: list[str] = []
        for tool in self.declared_tools:
            tid = validate_identifier(tool, "tool_id")
            if tid in seen:
                raise _reject("duplicate_declared_tool")
            seen.add(tid)
            cleaned.append(tid)
        object.__setattr__(self, "declared_tools", tuple(cleaned))

    def __repr__(self) -> str:
        return f"LoopRequest(tools={len(self.declared_tools)})"


@dataclass(frozen=True, repr=False)
class ProposedAction:
    """Planner output. Grants zero authority by itself."""

    action_id: str
    tool_id: str
    target: str
    action: str
    step_description: str
    expected_observation_type: str
    confidence: ConfidenceCategory
    completion_proposal: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.action_id, "action_id")
        validate_identifier(self.tool_id, "tool_id")
        validate_target(self.target, "target")
        validate_identifier(self.action, "action")
        validate_text(self.step_description, field="step_description", max_length=_MAX_DESCRIPTION)
        validate_identifier(self.expected_observation_type, "expected_observation_type")
        if not isinstance(self.confidence, ConfidenceCategory):
            raise _reject("invalid_confidence")
        if not isinstance(self.completion_proposal, bool):
            raise _reject("invalid_completion_proposal")
        # Reject chain-of-thought shaped field names if smuggled via description keywords only
        # is not required; structural absence of reasoning fields is the contract.

    def __repr__(self) -> str:
        return f"ProposedAction(confidence={self.confidence.value!r})"


@dataclass(frozen=True, repr=False)
class ApprovalReference:
    """Exact, scoped, correlated, one-time approval claim."""

    approval_id: str
    request_id: str
    actor_id: str
    session_id: str
    action_id: str
    tool_id: str
    target: str
    action: str
    expires_at_mono: float

    def __post_init__(self) -> None:
        validate_identifier(self.approval_id, "approval_id")
        validate_identifier(self.request_id, "request_id")
        validate_actor_id(self.actor_id, "actor_id")
        validate_actor_id(self.session_id, "session_id")
        validate_identifier(self.action_id, "action_id")
        validate_identifier(self.tool_id, "tool_id")
        validate_target(self.target, "target")
        validate_identifier(self.action, "action")
        validate_finite_mono(self.expires_at_mono, "expires_at_mono")

    def matches_proposal(self, proposal: ProposedAction, request: LoopRequest) -> bool:
        return (
            self.request_id == request.request_id
            and self.actor_id == request.actor_id
            and self.session_id == request.session_id
            and self.action_id == proposal.action_id
            and self.tool_id == proposal.tool_id
            and self.target == proposal.target
            and self.action == proposal.action
        )

    def __repr__(self) -> str:
        return "ApprovalReference(scoped=True)"


@dataclass(frozen=True, repr=False)
class AuthorizationDecision:
    outcome: AuthorizationOutcome
    reason_code: FailureCode
    audit_required: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, AuthorizationOutcome):
            raise _reject("invalid_authorization_outcome")
        if not isinstance(self.reason_code, FailureCode):
            raise _reject("invalid_reason_code")
        if not isinstance(self.audit_required, bool):
            raise _reject("invalid_audit_required")

    def __repr__(self) -> str:
        return f"AuthorizationDecision(outcome={self.outcome.value!r})"


@dataclass(frozen=True, repr=False)
class AuthorizedAction:
    """Action that has passed authorization for a single execution attempt."""

    proposal: ProposedAction
    decision: AuthorizationDecision
    approval: Optional[ApprovalReference] = None
    authorized_at_mono: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, ProposedAction):
            raise _reject("invalid_proposal")
        if not isinstance(self.decision, AuthorizationDecision):
            raise _reject("invalid_decision")
        if self.approval is not None and not isinstance(self.approval, ApprovalReference):
            raise _reject("invalid_approval")
        validate_finite_mono(self.authorized_at_mono, "authorized_at_mono")
        if self.decision.outcome is not AuthorizationOutcome.ALLOW:
            raise _reject("unauthorized_action")

    def __repr__(self) -> str:
        return "AuthorizedAction(allow=True)"


@dataclass(frozen=True, repr=False)
class ActionObservation:
    observation_id: str
    action_id: str
    observation_type: str
    outcome: ActionOutcome
    summary: str
    observed_at_mono: float

    def __post_init__(self) -> None:
        validate_identifier(self.observation_id, "observation_id")
        validate_identifier(self.action_id, "action_id")
        validate_identifier(self.observation_type, "observation_type")
        if not isinstance(self.outcome, ActionOutcome):
            raise _reject("invalid_outcome")
        validate_text(self.summary, field="summary", max_length=_MAX_OBSERVATION)
        validate_finite_mono(self.observed_at_mono, "observed_at_mono")

    def __repr__(self) -> str:
        return f"ActionObservation(outcome={self.outcome.value!r})"


@dataclass(frozen=True, repr=False)
class CorrectionRequest:
    """Structured correction from an observation; cannot broaden scope."""

    correction_id: str
    failed_action_id: str
    observation_id: str
    allowed_tools: Tuple[str, ...]
    note: str

    def __post_init__(self) -> None:
        validate_identifier(self.correction_id, "correction_id")
        validate_identifier(self.failed_action_id, "failed_action_id")
        validate_identifier(self.observation_id, "observation_id")
        validate_text(self.note, field="note", max_length=_MAX_DESCRIPTION)
        if not isinstance(self.allowed_tools, tuple) or not self.allowed_tools:
            raise _reject("invalid_allowed_tools")
        if len(self.allowed_tools) > _MAX_DECLARED_TOOLS:
            raise _reject("invalid_allowed_tools")
        cleaned = tuple(validate_identifier(t, "tool_id") for t in self.allowed_tools)
        if len(set(cleaned)) != len(cleaned):
            raise _reject("duplicate_allowed_tool")
        object.__setattr__(self, "allowed_tools", cleaned)

    def __repr__(self) -> str:
        return f"CorrectionRequest(tools={len(self.allowed_tools)})"


@dataclass(frozen=True, repr=False)
class LoopEvent:
    event_id: str
    kind: EventKind
    state: LoopState
    code: Optional[FailureCode]
    at_mono: float

    def __post_init__(self) -> None:
        validate_identifier(self.event_id, "event_id")
        if not isinstance(self.kind, EventKind):
            raise _reject("invalid_event_kind")
        if not isinstance(self.state, LoopState):
            raise _reject("invalid_state")
        if self.code is not None and not isinstance(self.code, FailureCode):
            raise _reject("invalid_code")
        validate_finite_mono(self.at_mono, "at_mono")

    def __repr__(self) -> str:
        return f"LoopEvent(kind={self.kind.value!r}, state={self.state.value!r})"


@dataclass(frozen=True, repr=False)
class LoopResult:
    request_id: str
    state: LoopState
    failure_code: Optional[FailureCode]
    steps_used: int
    actions_used: int
    corrections_used: int
    events: Tuple[LoopEvent, ...]
    final_summary: str

    def __post_init__(self) -> None:
        validate_identifier(self.request_id, "request_id")
        if not isinstance(self.state, LoopState):
            raise _reject("invalid_state")
        if self.failure_code is not None and not isinstance(self.failure_code, FailureCode):
            raise _reject("invalid_failure_code")
        for name, value in (
            ("steps_used", self.steps_used),
            ("actions_used", self.actions_used),
            ("corrections_used", self.corrections_used),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _reject(f"invalid_{name}")
        if not isinstance(self.events, tuple) or len(self.events) > _MAX_EVENTS:
            raise _reject("invalid_events")
        if any(not isinstance(event, LoopEvent) for event in self.events):
            raise _reject("invalid_events")
        validate_text(
            self.final_summary,
            field="final_summary",
            max_length=_MAX_DESCRIPTION,
            allow_empty=True,
        )

    def __repr__(self) -> str:
        return f"LoopResult(state={self.state.value!r})"


@dataclass(frozen=True, repr=False)
class RemoteWorkerAuthorityEnvelope:
    """Non-delegable remote-worker authority. Contains no secrets or prompts."""

    envelope_id: str
    worker_id: str
    task_id: str
    capability: str
    targets: Tuple[str, ...]
    expires_at_mono: float
    nonce: str
    max_responses: int
    revoked: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.envelope_id, "envelope_id")
        validate_identifier(self.worker_id, "worker_id")
        validate_identifier(self.task_id, "task_id")
        validate_identifier(self.capability, "capability")
        validate_finite_mono(self.expires_at_mono, "expires_at_mono")
        if not isinstance(self.nonce, str) or not _HEX_NONCE_RE.fullmatch(self.nonce):
            raise _reject("invalid_nonce")
        if not isinstance(self.targets, tuple) or not self.targets or len(self.targets) > _MAX_TARGETS:
            raise _reject("invalid_targets")
        cleaned = tuple(validate_target(t, "target") for t in self.targets)
        if len(set(cleaned)) != len(cleaned):
            raise _reject("duplicate_target")
        object.__setattr__(self, "targets", cleaned)
        object.__setattr__(
            self,
            "max_responses",
            validate_positive_int(
                self.max_responses,
                field="max_responses",
                maximum=HARD_MAX_REMOTE_RESPONSES,
            ),
        )
        if not isinstance(self.revoked, bool):
            raise _reject("invalid_revoked")

    def __repr__(self) -> str:
        return f"RemoteWorkerAuthorityEnvelope(revoked={self.revoked})"


@dataclass(frozen=True, repr=False)
class RemoteWorkerResult:
    """Untrusted structured evidence until locally validated."""

    result_id: str
    envelope_id: str
    worker_id: str
    task_id: str
    summary: str
    observed_at_mono: float

    def __post_init__(self) -> None:
        validate_identifier(self.result_id, "result_id")
        validate_identifier(self.envelope_id, "envelope_id")
        validate_identifier(self.worker_id, "worker_id")
        validate_identifier(self.task_id, "task_id")
        validate_text(self.summary, field="summary", max_length=_MAX_DESCRIPTION)
        validate_finite_mono(self.observed_at_mono, "observed_at_mono")

    def __repr__(self) -> str:
        return "RemoteWorkerResult(untrusted=True)"


__all__ = [
    "ActionObservation",
    "ActionOutcome",
    "ApprovalReference",
    "AuthorizationDecision",
    "AuthorizationOutcome",
    "AuthorizedAction",
    "ConfidenceCategory",
    "CorrectionRequest",
    "EventKind",
    "FailureCode",
    "LoopBudget",
    "LoopEvent",
    "LoopRequest",
    "LoopResult",
    "LoopState",
    "ProposedAction",
    "RemoteWorkerAuthorityEnvelope",
    "RemoteWorkerResult",
    "validate_actor_id",
    "validate_finite_mono",
    "validate_identifier",
    "validate_target",
    "validate_text",
]
