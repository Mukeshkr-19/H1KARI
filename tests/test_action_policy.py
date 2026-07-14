"""The central action-policy skeleton must remain pure and fail closed."""

from __future__ import annotations

import pytest

from core.action_policy import (
    ActionContext,
    ActionRisk,
    Actor,
    DataScope,
    PolicyOutcome,
    evaluate_action,
)


@pytest.mark.parametrize(
    "context,outcome",
    [
        (ActionContext("read_help", Actor.OWNER, DataScope.PUBLIC, ActionRisk.READ_ONLY), PolicyOutcome.ALLOW),
        (ActionContext("read_session", Actor.GUEST, DataScope.SESSION, ActionRisk.READ_ONLY), PolicyOutcome.ALLOW),
        (ActionContext("read_owner", Actor.GUEST, DataScope.OWNER_PRIVATE, ActionRisk.READ_ONLY), PolicyOutcome.DENY),
        (ActionContext("open_app", Actor.GUEST, DataScope.PUBLIC, ActionRisk.OS_CONTROL, True, True), PolicyOutcome.DENY),
        (ActionContext("open_app", Actor.OWNER, DataScope.PUBLIC, ActionRisk.OS_CONTROL), PolicyOutcome.DENY),
        (ActionContext("open_app", Actor.OWNER, DataScope.PUBLIC, ActionRisk.OS_CONTROL, True), PolicyOutcome.REQUIRE_CONFIRMATION),
        (ActionContext("open_app", Actor.OWNER, DataScope.PUBLIC, ActionRisk.OS_CONTROL, True, True), PolicyOutcome.ALLOW),
        (ActionContext("fetch", Actor.OWNER, DataScope.PUBLIC, ActionRisk.NETWORK, True), PolicyOutcome.REQUIRE_CONFIRMATION),
        (ActionContext("delete", Actor.OWNER, DataScope.OWNER_PRIVATE, ActionRisk.DESTRUCTIVE, True, True), PolicyOutcome.DENY),
        (ActionContext("sudo", Actor.OWNER, DataScope.SYSTEM, ActionRisk.PRIVILEGED, True, True), PolicyOutcome.DENY),
        (ActionContext("schedule", Actor.SYSTEM, DataScope.SESSION, ActionRisk.REVERSIBLE_WRITE, True, True), PolicyOutcome.DENY),
        (ActionContext("read", Actor.UNKNOWN, DataScope.PUBLIC, ActionRisk.READ_ONLY), PolicyOutcome.DENY),
    ],
)
def test_policy_matrix(context: ActionContext, outcome: PolicyOutcome):
    assert evaluate_action(context).outcome is outcome


def test_blank_action_identifier_fails_closed():
    decision = evaluate_action(
        ActionContext("  ", Actor.OWNER, DataScope.PUBLIC, ActionRisk.READ_ONLY)
    )
    assert decision.outcome is PolicyOutcome.DENY
    assert "identifier" in decision.reason


def test_confirmation_cannot_override_missing_user_intent():
    decision = evaluate_action(
        ActionContext(
            "open_app",
            Actor.OWNER,
            DataScope.PUBLIC,
            ActionRisk.OS_CONTROL,
            user_initiated=False,
            confirmation_granted=True,
        )
    )
    assert decision.outcome is PolicyOutcome.DENY
    assert "user intent" in decision.reason


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("actor", "owner", "actor classification"),
        ("data_scope", "public", "data-scope classification"),
        ("risk", "read_only", "risk classification"),
        ("user_initiated", 1, "flags"),
        ("confirmation_granted", 1, "flags"),
    ],
)
def test_raw_or_malformed_policy_values_fail_closed(field: str, value: object, reason: str):
    values = {
        "action": "read_help",
        "actor": Actor.OWNER,
        "data_scope": DataScope.PUBLIC,
        "risk": ActionRisk.READ_ONLY,
        "user_initiated": False,
        "confirmation_granted": False,
    }
    values[field] = value
    decision = evaluate_action(ActionContext(**values))  # type: ignore[arg-type]
    assert decision.outcome is PolicyOutcome.DENY
    assert reason in decision.reason
