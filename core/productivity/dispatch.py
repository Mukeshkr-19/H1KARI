"""Bounded Phase 3 execution dispatch.

Resolves exactly one prepared input across the in-memory preparation registries,
converts it to a bounded adapter input, and constructs an immutable
``ExecutionRequest``. No external side effects are performed; registry entries are
not removed.
"""

from __future__ import annotations

import re

from core.action_policy import ActorContext, validate_actor_context
from core.productivity.action_inputs import (
    ActionInputConversionError,
    adapter_input_from_prepared,
)
from core.productivity.calendar import PreparedCalendarEventDraft, PreparedCalendarRead
from core.productivity.contracts import ProductivityAction
from core.productivity.execution import ExecutionRequest
from core.productivity.runtime import ConfirmationResult
from core.protocol import validate_server_message


class DispatchError(ValueError):
    """Raised when dispatch cannot resolve exactly one prepared input."""


_DISPATCH_ERROR_MESSAGE = "dispatch failed"

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


def _fail() -> None:
    raise DispatchError(_DISPATCH_ERROR_MESSAGE)


def _validate_confirmation(
    proposal_id: str,
    confirmation: object,
) -> None:
    """Validate the complete server-private ``ConfirmationResult``.

    Fails closed with a single generic ``DispatchError`` on any deviation:
    wrong type, proposal mismatch, malformed approval ID, invalid public message,
    or unexpected public-message fields.
    """
    if not isinstance(confirmation, ConfirmationResult):
        _fail()
    if confirmation.proposal_id != proposal_id:
        _fail()
    approval_id = confirmation.approval_id
    if not isinstance(approval_id, str) or not _IDENTIFIER_RE.fullmatch(approval_id):
        _fail()
    public_message = confirmation.public_message
    if not isinstance(public_message, dict):
        _fail()
    if validate_server_message(public_message) is not None:
        _fail()
    if public_message.get("type") != "productivity_update":
        _fail()
    if public_message.get("status") != "approved":
        _fail()
    if public_message.get("proposal_id") != proposal_id:
        _fail()


def _prepared_matches(
    actor: ActorContext,
    proposal_id: str,
    *,
    email_registry: object | None = None,
    calendar_registry: object,
    research_registry: object,
    reminder_registry: object | None = None,
) -> list[tuple[ProductivityAction, object]]:
    matches: list[tuple[ProductivityAction, object]] = []
    try:
        if email_registry is not None:
            email_item = email_registry.get(actor, proposal_id)
            if email_item is not None:
                matches.append((ProductivityAction.EMAIL_DRAFT, email_item))

        calendar_item = calendar_registry.get(actor, proposal_id)
        if calendar_item is not None:
            if type(calendar_item) is PreparedCalendarRead:
                matches.append((ProductivityAction.CALENDAR_READ, calendar_item))
            elif type(calendar_item) is PreparedCalendarEventDraft:
                matches.append((ProductivityAction.CALENDAR_DRAFT, calendar_item))
            else:
                _fail()

        research_item = research_registry.get(actor, proposal_id)
        if research_item is not None:
            matches.append((ProductivityAction.BROWSER_RESEARCH, research_item))

        if reminder_registry is not None:
            reminder_item = reminder_registry.get(actor, proposal_id)
            if reminder_item is not None:
                matches.append((ProductivityAction.REMINDER_CREATE, reminder_item))
    except DispatchError:
        raise
    except Exception:
        _fail()
    return matches


def build_execution_request(
    actor: ActorContext,
    proposal_id: str,
    confirmation: ConfirmationResult,
    *,
    email_registry: object,
    calendar_registry: object,
    research_registry: object,
    reminder_registry: object,
) -> ExecutionRequest:
    """Resolve exactly one prepared input and build an ``ExecutionRequest``."""
    valid_actor, _ = validate_actor_context(actor)
    if not valid_actor or not isinstance(proposal_id, str) or not _IDENTIFIER_RE.fullmatch(proposal_id):
        _fail()
    _validate_confirmation(proposal_id, confirmation)
    matches = _prepared_matches(
        actor,
        proposal_id,
        email_registry=email_registry,
        calendar_registry=calendar_registry,
        research_registry=research_registry,
        reminder_registry=reminder_registry,
    )
    if len(matches) != 1:
        _fail()
    action, prepared = matches[0]
    try:
        adapter_input = adapter_input_from_prepared(action, prepared)
        approval_id = confirmation.approval_id
        if not isinstance(approval_id, str) or not approval_id:
            _fail()
        return ExecutionRequest(
            actor=actor,
            approval_id=approval_id,
            action=action,
            proposal_id=proposal_id,
            adapter_input=adapter_input,
        )
    except DispatchError:
        raise
    except Exception:
        _fail()


def build_scheduled_adapter_input(
    actor: ActorContext,
    proposal_id: str,
    *,
    calendar_registry: object,
    research_registry: object,
) -> tuple[ProductivityAction, object]:
    """Resolve exactly one active, frozen read input for delayed execution."""
    valid_actor, _ = validate_actor_context(actor)
    if not valid_actor or not isinstance(proposal_id, str) or not _IDENTIFIER_RE.fullmatch(proposal_id):
        _fail()
    matches = _prepared_matches(
        actor,
        proposal_id,
        calendar_registry=calendar_registry,
        research_registry=research_registry,
    )
    if len(matches) != 1 or matches[0][0] not in {
        ProductivityAction.BROWSER_RESEARCH,
        ProductivityAction.CALENDAR_READ,
    }:
        _fail()
    action, prepared = matches[0]
    try:
        return action, adapter_input_from_prepared(action, prepared)
    except Exception:
        _fail()
