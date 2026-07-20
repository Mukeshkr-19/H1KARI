"""Pure Phase 3 productivity transport adapter.

This module converts immutable productivity contracts into canonical v1
WebSocket server messages. It performs no I/O, network access, execution,
logging, or server wiring.
"""

from __future__ import annotations

from typing import Iterable

from core.protocol import validate_server_message
from core.productivity.action_results import (
    BrowserSearchResult,
    CalendarReadResult,
)
from core.productivity.contracts import (
    ActionProposal,
    ActionTarget,
    PreviewField,
    ProductivityAction,
    TargetKind,
)
from core.productivity.service import ProductivityCode


class TransportError(ValueError):
    """Raised when a message cannot be produced without violating the protocol."""


# Fixed action-to-heading mapping.
_ACTION_HEADING: dict[ProductivityAction, str] = {
    ProductivityAction.BROWSER_RESEARCH: "Browser research",
    ProductivityAction.EMAIL_DRAFT: "Draft email",
    ProductivityAction.CALENDAR_READ: "Read calendar",
    ProductivityAction.CALENDAR_DRAFT: "Draft calendar event",
    ProductivityAction.REMINDER_CREATE: "Create reminder",
    ProductivityAction.SCHEDULED_JOB_MANAGE: "Manage scheduled job",
    ProductivityAction.SKILL_EXECUTE: "Run skill",
    ProductivityAction.MCP_EXECUTE: "Run MCP tool",
}

# Fixed action-to-risk-label mapping.
_ACTION_RISK_LABEL: dict[ProductivityAction, str] = {
    ProductivityAction.BROWSER_RESEARCH: "low",
    ProductivityAction.EMAIL_DRAFT: "medium",
    ProductivityAction.CALENDAR_READ: "low",
    ProductivityAction.CALENDAR_DRAFT: "low",
    ProductivityAction.REMINDER_CREATE: "low",
    ProductivityAction.SCHEDULED_JOB_MANAGE: "medium",
    ProductivityAction.SKILL_EXECUTE: "high",
    ProductivityAction.MCP_EXECUTE: "high",
}

# Fixed target-kind label mapping.
_TARGET_LABEL: dict[TargetKind, str] = {
    TargetKind.WEB_DOMAIN: "Web domain",
    TargetKind.EMAIL_RECIPIENT: "Email recipient",
    TargetKind.CALENDAR: "Calendar",
    TargetKind.REMINDER_LIST: "Reminder list",
    TargetKind.SKILL: "Skill",
    TargetKind.MCP_SERVER: "MCP server",
}


# Allowed productivity_update status values from the v1 contract.
_UPDATE_STATUSES = frozenset(
    [
        "preview",
        "confirming",
        "approved",
        "executing",
        "completed",
        "failed",
        "cancelling",
        "cancelled",
    ]
)


def target_entries(targets: Iterable[ActionTarget]) -> list[dict]:
    """Convert ``ActionTarget`` instances to canonical preview entries.

    Each entry contains only a fixed target-kind label and the bounded target
    value. No actor, session, or approval identifiers are included.
    """
    result: list[dict] = []
    for target in targets:
        if not isinstance(target, ActionTarget):
            raise TransportError("targets must contain ActionTarget instances")
        label = _TARGET_LABEL.get(target.kind)
        if label is None:
            raise TransportError(f"unknown target kind: {target.kind}")
        result.append({"label": label, "value": target.value})
    return result


def preview_entries(fields: Iterable[PreviewField]) -> list[dict]:
    """Convert ``PreviewField`` instances to canonical payload entries.

    Each entry contains the field label, bounded display value, and an explicit
    truncated flag. No actor, session, or approval identifiers are included.
    """
    result: list[dict] = []
    for field in fields:
        if not isinstance(field, PreviewField):
            raise TransportError("fields must contain PreviewField instances")
        result.append(
            {"label": field.label, "value": field.value, "truncated": field.truncated}
        )
    return result


def confirmation_required(proposal: ActionProposal) -> dict:
    """Convert an ``ActionProposal`` into a productivity_confirmation_required message.

    The resulting message is validated against the v1 server protocol before
    being returned. It never includes actor IDs, session IDs, approval IDs, or
    any content outside the explicit preview.
    """
    if not isinstance(proposal, ActionProposal):
        raise TransportError("proposal must be an ActionProposal")

    message = {
        "type": "productivity_confirmation_required",
        "proposal_id": proposal.proposal_id,
        "action": proposal.action.value,
        "heading": _ACTION_HEADING[proposal.action],
        "risk_label": _ACTION_RISK_LABEL[proposal.action],
        "targets": target_entries(proposal.targets),
        "payload": preview_entries(proposal.preview_fields),
        "expires_at": proposal.expires_at,
        "allowed_scopes": [
            "once",
            "session",
            "duration",
            "precise_persistent",
        ],
    }

    error = validate_server_message(message)
    if error is not None:
        raise TransportError(f"invalid productivity_confirmation_required: {error}")

    return message


def error_code_for(code: ProductivityCode, *, context: str | None = None) -> str:
    """Map a bounded service result code to a canonical productivity_error code.

    The returned code is always one of: ``confirm_failed``,
    ``cancel_failed``, ``proposal_expired``, ``proposal_invalid``, or
    ``unavailable``.

    The optional ``context`` argument (``"confirm"`` or ``"cancel"``)
    disambiguates authorization and availability failures for the caller.
    """
    if not isinstance(code, ProductivityCode):
        raise TransportError("code must be a ProductivityCode")
    if code is ProductivityCode.OK:
        raise TransportError("OK cannot be mapped to an error code")

    if context == "confirm":
        if code in (
            ProductivityCode.UNAUTHORIZED_ACTOR,
            ProductivityCode.REGISTRY_FULL,
            ProductivityCode.CONSUMPTION_FAILED,
        ):
            return "confirm_failed"
    elif context == "cancel":
        if code in (
            ProductivityCode.UNAUTHORIZED_ACTOR,
            ProductivityCode.REGISTRY_FULL,
            ProductivityCode.CONSUMPTION_FAILED,
        ):
            return "cancel_failed"

    if code in (
        ProductivityCode.PROPOSAL_EXPIRED,
        ProductivityCode.APPROVAL_EXPIRED_OR_CONSUMED,
    ):
        return "proposal_expired"
    if code in (
        ProductivityCode.PROPOSAL_NOT_FOUND,
        ProductivityCode.DUPLICATE_PROPOSAL,
        ProductivityCode.INVALID_SCOPE,
        ProductivityCode.INVALID_EXPIRY,
        ProductivityCode.INVALID_ACKNOWLEDGEMENT,
        ProductivityCode.APPROVAL_NOT_FOUND,
        ProductivityCode.STATE_MISMATCH,
    ):
        return "proposal_invalid"

    return "unavailable"


def error_message(
    proposal_id: str,
    code: ProductivityCode,
    *,
    context: str | None = None,
) -> dict:
    """Produce a canonical productivity_error message.

    The service code is mapped to one of the allowed client error codes. No
    exception text, provider details, or internal identifiers are included.
    """
    if not isinstance(proposal_id, str):
        raise TransportError("proposal_id must be a string")

    mapped = error_code_for(code, context=context)

    message = {
        "type": "productivity_error",
        "proposal_id": proposal_id,
        "code": mapped,
    }

    error = validate_server_message(message)
    if error is not None:
        raise TransportError(f"invalid productivity_error: {error}")

    return message


def update_message(proposal_id: str, status: str) -> dict:
    """Produce a canonical productivity_update message.

    ``status`` must be one of the allowed v1 status values. The message never
    includes actor IDs, session IDs, or approval IDs.
    """
    if not isinstance(proposal_id, str):
        raise TransportError("proposal_id must be a string")
    if status not in _UPDATE_STATUSES:
        raise TransportError(f"invalid status: {status}")

    message = {
        "type": "productivity_update",
        "proposal_id": proposal_id,
        "status": status,
    }

    error = validate_server_message(message)
    if error is not None:
        raise TransportError(f"invalid productivity_update: {error}")

    return message


def research_result_message(proposal_id: str, result: BrowserSearchResult) -> dict:
    """Produce a canonical ``productivity_research_result`` message.

    The message carries only the proposal ID and bounded item fields. Query text,
    actor/session/approval identifiers, and provider payloads are excluded.
    """
    if not isinstance(proposal_id, str):
        raise TransportError("proposal_id must be a string")
    if not isinstance(result, BrowserSearchResult):
        raise TransportError("result must be a BrowserSearchResult")

    items: list[dict] = []
    for item in result.items:
        entry: dict[str, str] = {
            "title": item.title,
            "url": item.url,
            "domain": item.domain,
        }
        if item.snippet is not None and item.snippet.strip():
            entry["snippet"] = item.snippet
        items.append(entry)

    message = {
        "type": "productivity_research_result",
        "proposal_id": proposal_id,
        "items": items,
    }
    error = validate_server_message(message)
    if error is not None:
        raise TransportError(f"invalid productivity_research_result: {error}")
    return message


def calendar_result_message(proposal_id: str, result: CalendarReadResult) -> dict:
    """Produce a canonical ``productivity_calendar_result`` message.

    The message carries only the proposal ID and bounded event fields. Actor,
    session, approval, and provider payloads are excluded.
    """
    if not isinstance(proposal_id, str):
        raise TransportError("proposal_id must be a string")
    if not isinstance(result, CalendarReadResult):
        raise TransportError("result must be a CalendarReadResult")

    events: list[dict] = []
    for event in result.events:
        try:
            start = event.start.isoformat()
            end = event.end.isoformat()
        except Exception as exc:
            raise TransportError("invalid calendar event instant") from exc
        entry: dict[str, str] = {
            "title": event.title,
            "start": start,
            "end": end,
            "calendar": event.calendar_label,
        }
        if event.location is not None and event.location.strip():
            entry["location"] = event.location
        events.append(entry)

    message = {
        "type": "productivity_calendar_result",
        "proposal_id": proposal_id,
        "events": events,
    }
    error = validate_server_message(message)
    if error is not None:
        raise TransportError(f"invalid productivity_calendar_result: {error}")
    return message
