"""Pure, default-deny policy boundary for future action caller migration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Actor(StrEnum):
    OWNER = "owner"
    GUEST = "guest"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class DataScope(StrEnum):
    PUBLIC = "public"
    SESSION = "session"
    OWNER_PRIVATE = "owner_private"
    SYSTEM = "system"


class ActionRisk(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    NETWORK = "network"
    OS_CONTROL = "os_control"
    DESTRUCTIVE = "destructive"
    PRIVILEGED = "privileged"


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"
    DENY = "deny"


@dataclass(frozen=True)
class ActionContext:
    action: str
    actor: Actor
    data_scope: DataScope
    risk: ActionRisk
    user_initiated: bool = False
    confirmation_granted: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    outcome: PolicyOutcome
    reason: str


def evaluate_action(context: ActionContext) -> PolicyDecision:
    """Return a deterministic decision without executing or importing any action."""
    if not isinstance(context.action, str) or not context.action.strip():
        return PolicyDecision(PolicyOutcome.DENY, "action identifier is required")

    if not isinstance(context.actor, Actor):
        return PolicyDecision(PolicyOutcome.DENY, "actor classification is invalid")
    if not isinstance(context.data_scope, DataScope):
        return PolicyDecision(PolicyOutcome.DENY, "data-scope classification is invalid")
    if not isinstance(context.risk, ActionRisk):
        return PolicyDecision(PolicyOutcome.DENY, "risk classification is invalid")
    if not isinstance(context.user_initiated, bool) or not isinstance(
        context.confirmation_granted, bool
    ):
        return PolicyDecision(PolicyOutcome.DENY, "policy flags must be boolean")

    if context.actor in {Actor.UNKNOWN, Actor.SYSTEM}:
        return PolicyDecision(
            PolicyOutcome.DENY,
            "unknown and autonomous system actors have no action grant",
        )

    if context.actor is Actor.GUEST:
        if context.data_scope in {DataScope.OWNER_PRIVATE, DataScope.SYSTEM}:
            return PolicyDecision(PolicyOutcome.DENY, "guest cannot access this data scope")
        if context.risk is not ActionRisk.READ_ONLY:
            return PolicyDecision(PolicyOutcome.DENY, "guest side effects are not permitted")
        return PolicyDecision(PolicyOutcome.ALLOW, "guest read is limited to public or session data")

    if context.risk is ActionRisk.READ_ONLY:
        return PolicyDecision(PolicyOutcome.ALLOW, "owner read-only action")

    if not context.user_initiated:
        return PolicyDecision(PolicyOutcome.DENY, "side effect lacks explicit user intent")

    if context.risk in {ActionRisk.DESTRUCTIVE, ActionRisk.PRIVILEGED}:
        return PolicyDecision(
            PolicyOutcome.DENY,
            "high-risk action requires a future action-specific policy",
        )

    if not context.confirmation_granted:
        return PolicyDecision(
            PolicyOutcome.REQUIRE_CONFIRMATION,
            "reversible side effect requires a verified confirmation grant",
        )

    return PolicyDecision(PolicyOutcome.ALLOW, "owner explicitly initiated and confirmed")
