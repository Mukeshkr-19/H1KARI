"""Tests for Phase 3 immutable productivity-action contracts."""

from __future__ import annotations

import ast
import math
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core.action_policy import Actor, ActorContext
from core.productivity.contracts import (
    ActionProposal,
    ActionTarget,
    ExecutionResult,
    ExecutionStatus,
    PreviewField,
    ProductivityAction,
    TargetKind,
)


def _owner() -> ActorContext:
    return ActorContext("local-owner", Actor.OWNER, "session-1", "text")


def _guest() -> ActorContext:
    return ActorContext("guest-1", Actor.GUEST, "session-2", "websocket")


@pytest.mark.parametrize(
    "action",
    [
        ProductivityAction.BROWSER_RESEARCH,
        ProductivityAction.EMAIL_DRAFT,
        ProductivityAction.CALENDAR_READ,
        ProductivityAction.CALENDAR_DRAFT,
        ProductivityAction.REMINDER_CREATE,
        ProductivityAction.SCHEDULED_JOB_MANAGE,
        ProductivityAction.SKILL_EXECUTE,
        ProductivityAction.MCP_EXECUTE,
    ],
)
def test_all_productivity_actions_are_defined(action: ProductivityAction) -> None:
    assert isinstance(action, ProductivityAction)
    assert action.value.startswith("browser.") or action.value.startswith("email.") or action.value.startswith("calendar.") or action.value.startswith("reminder.") or action.value.startswith("scheduled_job.") or action.value.startswith("skill.") or action.value.startswith("mcp.")


@pytest.mark.parametrize(
    "kind",
    [
        TargetKind.WEB_DOMAIN,
        TargetKind.EMAIL_RECIPIENT,
        TargetKind.CALENDAR,
        TargetKind.REMINDER_LIST,
        TargetKind.SKILL,
        TargetKind.MCP_SERVER,
    ],
)
def test_all_target_kinds_are_defined(kind: TargetKind) -> None:
    assert isinstance(kind, TargetKind)


def test_action_target_normalizes_web_domain() -> None:
    target = ActionTarget(TargetKind.WEB_DOMAIN, "  Example.COM  ")
    assert target.value == "example.com"


def test_action_target_rejects_invalid_web_domain() -> None:
    with pytest.raises(ValueError):
        ActionTarget(TargetKind.WEB_DOMAIN, "not a domain!")


def test_action_target_accepts_valid_email_recipient() -> None:
    target = ActionTarget(TargetKind.EMAIL_RECIPIENT, "User.Name+tag@example.com")
    assert target.value == "User.Name+tag@example.com"


@pytest.mark.parametrize("value", ["not-an-email", "@example.com", "user@", "a@b"])
def test_action_target_rejects_invalid_email_recipient(value: str) -> None:
    with pytest.raises(ValueError):
        ActionTarget(TargetKind.EMAIL_RECIPIENT, value)


def test_action_target_rejects_empty_value() -> None:
    with pytest.raises(ValueError):
        ActionTarget(TargetKind.CALENDAR, "")


def test_action_target_rejects_nul() -> None:
    with pytest.raises(ValueError):
        ActionTarget(TargetKind.SKILL, "skill\x00name")


def test_action_target_rejects_control_characters() -> None:
    with pytest.raises(ValueError):
        ActionTarget(TargetKind.MCP_SERVER, "server\x01name")


def test_action_target_rejects_oversized_value() -> None:
    with pytest.raises(ValueError):
        ActionTarget(TargetKind.CALENDAR, "x" * 5000)


def test_action_target_rejects_invalid_type() -> None:
    with pytest.raises(ValueError):
        ActionTarget(TargetKind.CALENDAR, 123)  # type: ignore[arg-type]


def test_action_target_repr_excludes_value() -> None:
    target = ActionTarget(TargetKind.EMAIL_RECIPIENT, "private@example.com")
    rep = repr(target)
    assert "private@example.com" not in rep
    assert TargetKind.EMAIL_RECIPIENT.value in rep


def test_preview_field_accepts_newline_and_tab() -> None:
    field = PreviewField("body", "Body", "Line one\nLine two\t indented")
    assert field.value == "Line one\nLine two\t indented"


def test_preview_field_rejects_control_characters() -> None:
    with pytest.raises(ValueError):
        PreviewField("body", "Body", "value\x00")
    with pytest.raises(ValueError):
        PreviewField("body", "Body", "value\x01")


def test_preview_field_rejects_oversized_value() -> None:
    with pytest.raises(ValueError):
        PreviewField("body", "Body", "x" * 5000)


def test_preview_field_repr_excludes_value() -> None:
    field = PreviewField("secret", "Secret", "sensitive user content")
    rep = repr(field)
    assert "sensitive user content" not in rep
    assert "secret" in rep
    assert "Secret" in rep


def test_preview_field_truncated_flag() -> None:
    field = PreviewField("body", "Body", "short", truncated=True)
    assert field.truncated is True


def test_action_proposal_basic_construction() -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=_owner(),
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(PreviewField("query", "Query", "weather"),),
        created_at=1000.0,
        expires_at=2000.0,
    )
    assert proposal.action is ProductivityAction.BROWSER_RESEARCH
    assert proposal.proposal_id == "prop-1"


def test_action_proposal_rejects_invalid_actor_context() -> None:
    invalid_actor = ActorContext("bad!", Actor.OWNER, "session", "text")
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=invalid_actor,
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(),
            created_at=1000.0,
            expires_at=2000.0,
        )


def test_action_proposal_rejects_equal_timestamps() -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=_owner(),
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(),
            created_at=1000.0,
            expires_at=1000.0,
        )


def test_action_proposal_rejects_non_increasing_timestamps() -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=_owner(),
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(),
            created_at=2000.0,
            expires_at=1000.0,
        )


@pytest.mark.parametrize("created_at", [float("nan"), float("inf"), float("-inf")])
def test_action_proposal_rejects_non_finite_created_at(created_at: float) -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=_owner(),
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(),
            created_at=created_at,
            expires_at=2000.0,
        )


@pytest.mark.parametrize("expires_at", [float("nan"), float("inf"), float("-inf")])
def test_action_proposal_rejects_non_finite_expires_at(expires_at: float) -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=_owner(),
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(),
            created_at=1000.0,
            expires_at=expires_at,
        )


@pytest.mark.parametrize("created_at", [True, False])
def test_action_proposal_rejects_boolean_created_at(created_at: object) -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=_owner(),
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(),
            created_at=created_at,  # type: ignore[arg-type]
            expires_at=2000.0,
        )


@pytest.mark.parametrize("expires_at", [True, False])
def test_action_proposal_rejects_boolean_expires_at(expires_at: object) -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=_owner(),
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(),
            created_at=1000.0,
            expires_at=expires_at,  # type: ignore[arg-type]
        )


def test_action_proposal_is_expired_before_expiry() -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=_owner(),
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(),
        created_at=1000.0,
        expires_at=2000.0,
    )
    assert proposal.is_expired(1500.0) is False


def test_action_proposal_is_expired_at_expiry() -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=_owner(),
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(),
        created_at=1000.0,
        expires_at=2000.0,
    )
    assert proposal.is_expired(2000.0) is True


def test_action_proposal_is_expired_after_expiry() -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=_owner(),
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(),
        created_at=1000.0,
        expires_at=2000.0,
    )
    assert proposal.is_expired(2500.0) is True


@pytest.mark.parametrize("now", [float("nan"), float("inf"), float("-inf")])
def test_action_proposal_is_expired_rejects_non_finite_now(now: float) -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=_owner(),
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(),
        created_at=1000.0,
        expires_at=2000.0,
    )
    with pytest.raises(ValueError):
        proposal.is_expired(now)


@pytest.mark.parametrize("now", [True, False])
def test_action_proposal_is_expired_rejects_boolean_now(now: object) -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=_owner(),
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(),
        created_at=1000.0,
        expires_at=2000.0,
    )
    with pytest.raises(ValueError):
        proposal.is_expired(now)  # type: ignore[arg-type]


def test_action_proposal_accepts_two_distinct_email_recipients() -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.EMAIL_DRAFT,
        actor=_owner(),
        targets=(
            ActionTarget(TargetKind.EMAIL_RECIPIENT, "alice@example.com"),
            ActionTarget(TargetKind.EMAIL_RECIPIENT, "bob@example.com"),
        ),
        preview_fields=(),
        created_at=1000.0,
        expires_at=2000.0,
    )
    assert len(proposal.targets) == 2


def test_action_proposal_accepts_two_distinct_web_domains() -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=_owner(),
        targets=(
            ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),
            ActionTarget(TargetKind.WEB_DOMAIN, "other.com"),
        ),
        preview_fields=(),
        created_at=1000.0,
        expires_at=2000.0,
    )
    assert len(proposal.targets) == 2


def test_action_proposal_rejects_normalized_duplicate_domain() -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=_owner(),
            targets=(
                ActionTarget(TargetKind.WEB_DOMAIN, "Example.COM"),
                ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),
            ),
            preview_fields=(),
            created_at=1000.0,
            expires_at=2000.0,
        )


def test_action_proposal_rejects_exact_duplicate_recipient() -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.EMAIL_DRAFT,
            actor=_owner(),
            targets=(
                ActionTarget(TargetKind.EMAIL_RECIPIENT, "alice@example.com"),
                ActionTarget(TargetKind.EMAIL_RECIPIENT, "alice@example.com"),
            ),
            preview_fields=(),
            created_at=1000.0,
            expires_at=2000.0,
        )


def test_action_proposal_rejects_duplicate_preview_keys() -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=_owner(),
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(
                PreviewField("query", "Query", "one"),
                PreviewField("query", "Query", "two"),
            ),
            created_at=1000.0,
            expires_at=2000.0,
        )


def test_action_proposal_rejects_invalid_proposal_id() -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="bad id!",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=_owner(),
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(),
            created_at=1000.0,
            expires_at=2000.0,
        )


def test_action_proposal_rejects_invalid_task_id() -> None:
    with pytest.raises(ValueError):
        ActionProposal(
            proposal_id="prop-1",
            action=ProductivityAction.BROWSER_RESEARCH,
            actor=_owner(),
            targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
            preview_fields=(),
            created_at=1000.0,
            expires_at=2000.0,
            task_id="bad task id!",
        )


def test_action_proposal_user_preview_excludes_actor_internals() -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.EMAIL_DRAFT,
        actor=_owner(),
        targets=(ActionTarget(TargetKind.EMAIL_RECIPIENT, "to@example.com"),),
        preview_fields=(PreviewField("subject", "Subject", "Hello"),),
        created_at=1000.0,
        expires_at=2000.0,
        task_id="task-1",
    )
    preview = proposal.user_preview()
    assert "actor" not in preview
    assert "actor_id" not in preview
    assert "session_id" not in preview
    assert preview["proposal_id"] == "prop-1"
    assert preview["action"] == "email.draft"
    assert preview["task_id"] == "task-1"
    assert preview["targets"][0]["value"] == "to@example.com"
    assert preview["preview_fields"][0]["value"] == "Hello"


def test_action_proposal_repr_excludes_user_content() -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.EMAIL_DRAFT,
        actor=_owner(),
        targets=(ActionTarget(TargetKind.EMAIL_RECIPIENT, "secret@example.com"),),
        preview_fields=(PreviewField("body", "Body", "secret body"),),
        created_at=1000.0,
        expires_at=2000.0,
    )
    rep = repr(proposal)
    assert "secret@example.com" not in rep
    assert "secret body" not in rep
    assert "prop-1" not in rep
    assert "local-owner" not in rep
    assert "session-1" not in rep
    assert "1000.0" not in rep
    assert "2000.0" not in rep
    assert rep == "ActionProposal(targets=1, preview_fields=1)"


def test_action_proposal_repr_is_content_free_with_distinctive_secrets() -> None:
    actor = ActorContext(
        actor_id="actor-7a8b9c0d1e2f",
        actor=Actor.OWNER,
        session_id="session-deadbeefcafe",
        source="text",
    )
    proposal = ActionProposal(
        proposal_id="proposal-deadbeef1234",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=actor,
        targets=(
            ActionTarget(TargetKind.WEB_DOMAIN, "secret-domain.example.com"),
            ActionTarget(TargetKind.EMAIL_RECIPIENT, "leaked-recipient@evil.example"),
        ),
        preview_fields=(
            PreviewField("query", "Query", "secret search query 42"),
            PreviewField("body", "Body", "sensitive body content"),
        ),
        created_at=1234567890.0,
        expires_at=1234567891.0,
        task_id="task-c0ffeebab3",
    )
    rep = repr(proposal)
    forbidden = (
        "proposal-deadbeef1234",
        "task-c0ffeebab3",
        "actor-7a8b9c0d1e2f",
        "session-deadbeefcafe",
        "secret-domain.example.com",
        "leaked-recipient@evil.example",
        "secret search query 42",
        "sensitive body content",
        "1234567890.0",
        "1234567891.0",
    )
    for value in forbidden:
        assert value not in rep
    assert rep == "ActionProposal(targets=2, preview_fields=2)"


def test_action_proposal_is_immutable() -> None:
    proposal = ActionProposal(
        proposal_id="prop-1",
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=_owner(),
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(PreviewField("query", "Query", "weather"),),
        created_at=1000.0,
        expires_at=2000.0,
    )
    with pytest.raises(FrozenInstanceError):
        proposal.proposal_id = "prop-2"  # type: ignore[misc]


def test_execution_result_construction() -> None:
    result = ExecutionResult(
        proposal_id="prop-1",
        status=ExecutionStatus.COMPLETED,
        code="ok",
        audit_id="audit-1",
    )
    assert result.status is ExecutionStatus.COMPLETED
    assert result.code == "ok"
    assert result.audit_id == "audit-1"


def test_execution_result_rejects_invalid_status() -> None:
    with pytest.raises(ValueError):
        ExecutionResult(
            proposal_id="prop-1",
            status="completed",  # type: ignore[arg-type]
            code="ok",
        )


def test_execution_result_repr_excludes_audit_id() -> None:
    result = ExecutionResult(
        proposal_id="prop-1",
        status=ExecutionStatus.COMPLETED,
        code="ok",
        audit_id="secret-audit-id",
    )
    rep = repr(result)
    assert "secret-audit-id" not in rep
    assert "prop-1" in rep


def test_no_email_send_action_exists() -> None:
    values = {action.value for action in ProductivityAction}
    assert "email.send" not in values
    assert "email.draft" in values


def test_no_phase_4_to_6_actions_exist() -> None:
    values = {action.value for action in ProductivityAction}
    assert "vision.capture" not in values
    assert "mobile.pair" not in values
    assert "home.assistant" not in values


def test_contracts_module_has_no_forbidden_imports() -> None:
    contracts_path = Path(__file__).resolve().parents[1] / "core" / "productivity" / "contracts.py"
    tree = ast.parse(contracts_path.read_text(encoding="utf-8"))
    forbidden = {
        "urllib",
        "urllib3",
        "requests",
        "http",
        "socket",
        "subprocess",
        "os",
        "pathlib",
        "sqlite3",
        "pickle",
        "json",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported), f"forbidden imports: {forbidden & imported}"
