"""Stateful authorization using server-owned action definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Optional

from core.action_audit import ActionAuditStore, opaque_resource_reference
from core.action_policy import (
    ActionContext,
    ActionRisk,
    Actor,
    ActorContext,
    DataScope,
    PolicyOutcome,
    evaluate_action,
    validate_actor_context,
)
from core.grants import GrantStore, canonicalize_resource

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


@dataclass(frozen=True)
class ActionDefinition:
    data_scope: DataScope
    risk: ActionRisk
    resource_required: bool = False
    destination_required: bool = False


ACTION_DEFINITIONS = MappingProxyType(
    {
        "help.read": ActionDefinition(DataScope.PUBLIC, ActionRisk.READ_ONLY),
        "document.read": ActionDefinition(
            DataScope.OWNER_PRIVATE, ActionRisk.READ_ONLY, resource_required=True
        ),
        "provider.send_document": ActionDefinition(
            DataScope.OWNER_PRIVATE,
            ActionRisk.NETWORK,
            resource_required=True,
            destination_required=True,
        ),
        "app.open": ActionDefinition(DataScope.PUBLIC, ActionRisk.OS_CONTROL),
        "schedule.create": ActionDefinition(
            DataScope.SESSION, ActionRisk.REVERSIBLE_WRITE
        ),
    }
)


@dataclass(frozen=True)
class ActionRequest:
    action: str
    actor: ActorContext
    user_initiated: bool = False
    resource: Optional[str] = None
    destination: Optional[str] = None
    task_id: Optional[str] = None
    grant_id: Optional[str] = None


@dataclass(frozen=True)
class AuthorizationDecision:
    outcome: PolicyOutcome
    reason: str
    audit_id: str


class PolicyService:
    def __init__(self, grants: GrantStore, audit: ActionAuditStore):
        self.grants = grants
        self.audit = audit

    def authorize(self, request: ActionRequest) -> AuthorizationDecision:
        valid_actor, _reason = validate_actor_context(request.actor)
        if not valid_actor:
            return self._record(request, None, PolicyOutcome.DENY, "invalid_actor")

        if not isinstance(request.action, str) or request.action not in ACTION_DEFINITIONS:
            return self._record(request, None, PolicyOutcome.DENY, "unknown_action")
        definition = ACTION_DEFINITIONS[request.action]

        if request.task_id is not None and (
            not isinstance(request.task_id, str)
            or not _IDENTIFIER.fullmatch(request.task_id)
        ):
            return self._record(request, None, PolicyOutcome.DENY, "invalid_task_id")

        if request.destination is not None and (
            not isinstance(request.destination, str)
            or not _IDENTIFIER.fullmatch(request.destination)
        ):
            return self._record(request, None, PolicyOutcome.DENY, "invalid_destination")
        if definition.destination_required and request.destination is None:
            return self._record(request, None, PolicyOutcome.DENY, "destination_required")
        if not definition.destination_required and request.destination is not None:
            return self._record(request, None, PolicyOutcome.DENY, "unexpected_destination")
        if not definition.resource_required and request.resource is not None:
            return self._record(request, None, PolicyOutcome.DENY, "unexpected_resource")

        base = evaluate_action(
            ActionContext(
                action=request.action,
                actor=request.actor.actor,
                data_scope=definition.data_scope,
                risk=definition.risk,
                user_initiated=request.user_initiated,
                confirmation_granted=False,
            )
        )
        base_reason = self._policy_reason(base.outcome, request, definition)
        if base.outcome is PolicyOutcome.DENY:
            return self._record(request, None, base.outcome, base_reason)

        try:
            resource = canonicalize_resource(request.resource)
        except (AttributeError, OSError, RuntimeError, ValueError):
            return self._record(request, None, PolicyOutcome.DENY, "invalid_resource")
        if definition.resource_required and resource is None:
            return self._record(request, None, PolicyOutcome.DENY, "resource_required")

        requires_grant = (
            definition.resource_required
            or base.outcome is PolicyOutcome.REQUIRE_CONFIRMATION
        )
        if requires_grant and not request.grant_id:
            return self._record(
                request, resource, PolicyOutcome.REQUIRE_CONFIRMATION, "grant_required"
            )
        if requires_grant:
            valid, reason = self.grants.consume(
                request.grant_id or "",
                actor=request.actor,
                action=request.action,
                resource=resource,
                destination=request.destination,
                task_id=request.task_id,
            )
            if not valid:
                return self._record(request, resource, PolicyOutcome.DENY, reason)

        final = base
        if base.outcome is PolicyOutcome.REQUIRE_CONFIRMATION:
            final = evaluate_action(
                ActionContext(
                    action=request.action,
                    actor=request.actor.actor,
                    data_scope=definition.data_scope,
                    risk=definition.risk,
                    user_initiated=request.user_initiated,
                    confirmation_granted=True,
                )
            )
        reason = "allowed" if final.outcome is PolicyOutcome.ALLOW else "policy_denied"
        return self._record(request, resource, final.outcome, reason)

    @staticmethod
    def _policy_reason(
        outcome: PolicyOutcome,
        request: ActionRequest,
        definition: ActionDefinition,
    ) -> str:
        if not isinstance(request.user_initiated, bool):
            return "invalid_request"
        if request.actor.actor in {Actor.UNKNOWN, Actor.SYSTEM}:
            return "actor_not_authorized"
        if request.actor.actor is Actor.GUEST:
            if definition.data_scope in {DataScope.OWNER_PRIVATE, DataScope.SYSTEM}:
                return "actor_scope_denied"
            if definition.risk is not ActionRisk.READ_ONLY:
                return "actor_side_effect_denied"
        if outcome is PolicyOutcome.DENY:
            return "user_intent_required"
        if outcome is PolicyOutcome.REQUIRE_CONFIRMATION:
            return "grant_required"
        return "allowed"

    def _record(
        self,
        request: ActionRequest,
        resource: Optional[str],
        outcome: PolicyOutcome,
        reason: str,
    ) -> AuthorizationDecision:
        valid_actor, _reason = validate_actor_context(request.actor)
        actor = request.actor if valid_actor else ActorContext(
            "invalid", Actor.UNKNOWN, "invalid", "unknown"
        )
        action = (
            request.action
            if isinstance(request.action, str) and _IDENTIFIER.fullmatch(request.action)
            else "invalid_action"
        )
        task_id = (
            request.task_id
            if isinstance(request.task_id, str) and _IDENTIFIER.fullmatch(request.task_id)
            else None
        )
        audit_id = self.audit.record_decision(
            actor=actor,
            task_id=task_id,
            action=action,
            resource_ref=opaque_resource_reference(resource),
            destination=(
                request.destination
                if isinstance(request.destination, str)
                and _IDENTIFIER.fullmatch(request.destination)
                else None
            ),
            outcome=outcome,
            reason=reason,
        )
        return AuthorizationDecision(outcome, reason, audit_id)
