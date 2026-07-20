"""Tests for the Phase 3 productivity transport adapter.

These tests verify that ``ActionProposal`` instances are converted into
canonical v1 server messages, that service result codes are mapped to the
bounded client error codes, and that no internal identifiers or user payload
leak into outbound messages.
"""

from __future__ import annotations

import math

import pytest

from core.action_policy import Actor, ActorContext
from core.productivity.contracts import (
    ActionProposal,
    ActionTarget,
    PreviewField,
    ProductivityAction,
    TargetKind,
)
from core.productivity.service import ProductivityCode
from core.productivity.transport import (
    TransportError,
    confirmation_required,
    error_code_for,
    error_message,
    preview_entries,
    target_entries,
    update_message,
)


@pytest.fixture
def owner_context() -> ActorContext:
    return ActorContext(
        actor_id="owner_1",
        actor=Actor.OWNER,
        session_id="session_1",
        source="text",
    )


@pytest.fixture
def proposal(owner_context: ActorContext) -> ActionProposal:
    return ActionProposal(
        proposal_id="prop_1",
        action=ProductivityAction.EMAIL_DRAFT,
        actor=owner_context,
        targets=(
            ActionTarget(TargetKind.EMAIL_RECIPIENT, "alice@example.com"),
            ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),
        ),
        preview_fields=(
            PreviewField(key="subject", label="Subject", value="Hello", truncated=False),
            PreviewField(key="body", label="Body", value="Preview text", truncated=True),
        ),
        created_at=1000.0,
        expires_at=2000.0,
        task_id="task_1",
    )


def _all_actions() -> list[ProductivityAction]:
    return list(ProductivityAction)


def test_confirmation_required_valid_for_all_actions(owner_context: ActorContext) -> None:
    for action in _all_actions():
        prop = ActionProposal(
            proposal_id="prop_1",
            action=action,
            actor=owner_context,
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(
                PreviewField(key="query", label="Query", value="search", truncated=False),
            ),
            created_at=1000.0,
            expires_at=2000.0,
        )
        message = confirmation_required(prop)
        assert message["type"] == "productivity_confirmation_required"
        assert message["proposal_id"] == "prop_1"
        assert message["action"] == action.value
        assert "heading" in message
        assert "risk_label" in message
        assert message["allowed_scopes"] == [
            "once",
            "session",
            "duration",
            "precise_persistent",
        ]
        assert message["expires_at"] == 2000.0


def test_confirmation_required_exact_payload_shape(proposal: ActionProposal) -> None:
    message = confirmation_required(proposal)
    assert message == {
        "type": "productivity_confirmation_required",
        "proposal_id": "prop_1",
        "action": "email.draft",
        "heading": "Draft email",
        "risk_label": "medium",
        "targets": [
            {"label": "Email recipient", "value": "alice@example.com"},
            {"label": "Web domain", "value": "example.com"},
        ],
        "payload": [
            {"label": "Subject", "value": "Hello", "truncated": False},
            {"label": "Body", "value": "Preview text", "truncated": True},
        ],
        "expires_at": 2000.0,
        "allowed_scopes": [
            "once",
            "session",
            "duration",
            "precise_persistent",
        ],
    }


def test_target_entries_uses_fixed_target_kind_labels() -> None:
    targets = [
        ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),
        ActionTarget(TargetKind.EMAIL_RECIPIENT, "bob@example.com"),
        ActionTarget(TargetKind.CALENDAR, "personal"),
        ActionTarget(TargetKind.REMINDER_LIST, "reminders"),
        ActionTarget(TargetKind.SKILL, "summarize"),
        ActionTarget(TargetKind.MCP_SERVER, "filesystem"),
    ]
    entries = target_entries(targets)
    assert entries == [
        {"label": "Web domain", "value": "example.com"},
        {"label": "Email recipient", "value": "bob@example.com"},
        {"label": "Calendar", "value": "personal"},
        {"label": "Reminder list", "value": "reminders"},
        {"label": "Skill", "value": "summarize"},
        {"label": "MCP server", "value": "filesystem"},
    ]


def test_preview_entries_includes_truncated_flag() -> None:
    fields = [
        PreviewField(key="a", label="A", value="short", truncated=False),
        PreviewField(key="b", label="B", value="long...", truncated=True),
    ]
    assert preview_entries(fields) == [
        {"label": "A", "value": "short", "truncated": False},
        {"label": "B", "value": "long...", "truncated": True},
    ]


def test_preview_entries_rejects_non_preview_field() -> None:
    with pytest.raises(TransportError):
        preview_entries(["not a preview field"])  # type: ignore[arg-type]


def test_target_entries_rejects_non_action_target() -> None:
    with pytest.raises(TransportError):
        target_entries(["not a target"])  # type: ignore[arg-type]


def test_confirmation_required_rejects_non_proposal() -> None:
    with pytest.raises(TransportError):
        confirmation_required("not a proposal")  # type: ignore[arg-type]


def test_confirmation_required_enforces_protocol_bounds(owner_context: ActorContext) -> None:
    # Oversized payload array should fail protocol validation.
    prop = ActionProposal(
        proposal_id="prop_1",
        action=ProductivityAction.EMAIL_DRAFT,
        actor=owner_context,
        targets=(ActionTarget(TargetKind.EMAIL_RECIPIENT, "alice@example.com"),),
        preview_fields=tuple(
            PreviewField(key=f"f{i}", label=f"Field {i}", value="v", truncated=False)
            for i in range(33)
        ),
        created_at=1000.0,
        expires_at=2000.0,
    )
    with pytest.raises(TransportError):
        confirmation_required(prop)



def test_confirmation_required_excludes_internal_identifiers(proposal: ActionProposal) -> None:
    message = confirmation_required(proposal)
    serialized = str(message)
    assert "owner_1" not in serialized
    assert "session_1" not in serialized
    assert "task_1" not in serialized
    assert "approval" not in serialized


def test_error_code_for_maps_all_codes_to_allowed_set() -> None:
    allowed = {"confirm_failed", "cancel_failed", "proposal_expired", "proposal_invalid", "unavailable"}
    for code in ProductivityCode:
        if code is ProductivityCode.OK:
            continue
        assert error_code_for(code) in allowed


def test_error_code_for_context_confirm() -> None:
    assert error_code_for(ProductivityCode.UNAUTHORIZED_ACTOR, context="confirm") == "confirm_failed"
    assert error_code_for(ProductivityCode.REGISTRY_FULL, context="confirm") == "confirm_failed"
    assert error_code_for(ProductivityCode.CONSUMPTION_FAILED, context="confirm") == "confirm_failed"


def test_error_code_for_context_cancel() -> None:
    assert error_code_for(ProductivityCode.UNAUTHORIZED_ACTOR, context="cancel") == "cancel_failed"
    assert error_code_for(ProductivityCode.REGISTRY_FULL, context="cancel") == "cancel_failed"
    assert error_code_for(ProductivityCode.CONSUMPTION_FAILED, context="cancel") == "cancel_failed"


def test_error_code_for_default_mapping() -> None:
    assert error_code_for(ProductivityCode.PROPOSAL_EXPIRED) == "proposal_expired"
    assert error_code_for(ProductivityCode.APPROVAL_EXPIRED_OR_CONSUMED) == "proposal_expired"
    assert error_code_for(ProductivityCode.PROPOSAL_NOT_FOUND) == "proposal_invalid"
    assert error_code_for(ProductivityCode.DUPLICATE_PROPOSAL) == "proposal_invalid"
    assert error_code_for(ProductivityCode.INVALID_SCOPE) == "proposal_invalid"
    assert error_code_for(ProductivityCode.INVALID_EXPIRY) == "proposal_invalid"
    assert error_code_for(ProductivityCode.APPROVAL_NOT_FOUND) == "proposal_invalid"
    assert error_code_for(ProductivityCode.STATE_MISMATCH) == "proposal_invalid"
    assert error_code_for(ProductivityCode.UNAUTHORIZED_ACTOR) == "unavailable"
    assert error_code_for(ProductivityCode.REGISTRY_FULL) == "unavailable"
    assert error_code_for(ProductivityCode.CONSUMPTION_FAILED) == "unavailable"


def test_error_code_for_rejects_ok() -> None:
    with pytest.raises(TransportError):
        error_code_for(ProductivityCode.OK)


def test_error_code_for_rejects_invalid_code() -> None:
    with pytest.raises(TransportError):
        error_code_for("not a code")  # type: ignore[arg-type]


def test_error_message_exact_shape() -> None:
    message = error_message("prop_1", ProductivityCode.PROPOSAL_EXPIRED)
    assert message == {
        "type": "productivity_error",
        "proposal_id": "prop_1",
        "code": "proposal_expired",
    }


def test_error_message_respects_context() -> None:
    message = error_message("prop_1", ProductivityCode.UNAUTHORIZED_ACTOR, context="confirm")
    assert message["code"] == "confirm_failed"


def test_error_message_excludes_internal_identifiers() -> None:
    message = error_message("prop_1", ProductivityCode.STATE_MISMATCH)
    serialized = str(message)
    assert "actor" not in serialized
    assert "session" not in serialized
    assert "approval" not in serialized
    assert "exception" not in serialized
    assert "trace" not in serialized


def test_error_message_rejects_invalid_proposal_id() -> None:
    with pytest.raises(TransportError):
        error_message(123, ProductivityCode.PROPOSAL_EXPIRED)  # type: ignore[arg-type]


def test_update_message_for_all_allowed_statuses() -> None:
    for status in ("approved", "executing", "completed", "failed", "cancelling", "cancelled"):
        message = update_message("prop_1", status)
        assert message == {
            "type": "productivity_update",
            "proposal_id": "prop_1",
            "status": status,
        }


def test_update_message_rejects_invalid_status() -> None:
    with pytest.raises(TransportError):
        update_message("prop_1", "bogus")


def test_update_message_rejects_invalid_proposal_id() -> None:
    with pytest.raises(TransportError):
        update_message(123, "approved")  # type: ignore[arg-type]


def test_update_message_excludes_internal_identifiers() -> None:
    message = update_message("prop_1", "approved")
    serialized = str(message)
    assert "actor" not in serialized
    assert "session" not in serialized
    assert "approval" not in serialized
