"""Synthetic tests for core.phase5.child_mode.

The guard must be deterministic, normalized, and fail-closed.  Tests use no
private data and no real identities.
"""

from __future__ import annotations

import pathlib
import inspect

import pytest

from core.phase5.child_mode import (
    ChildActionDescriptor,
    ChildModeDecisionReason,
    ChildModeDecision,
    classify_child_action,
)
from core.phase5.contracts import Outcome


# --- Safe categories --------------------------------------------------------


def test_educational_allowed():
    decision = classify_child_action(ChildActionDescriptor(category="educational"))
    assert decision.outcome == Outcome.ALLOW
    assert decision.reason == ChildModeDecisionReason.ALLOWED


def test_learning_allowed():
    decision = classify_child_action(ChildActionDescriptor(category="learning"))
    assert decision.outcome == Outcome.ALLOW


def test_study_allowed():
    decision = classify_child_action(ChildActionDescriptor(category="study"))
    assert decision.outcome == Outcome.ALLOW


def test_homework_allowed():
    decision = classify_child_action(ChildActionDescriptor(category="homework"))
    assert decision.outcome == Outcome.ALLOW


def test_question_allowed():
    decision = classify_child_action(ChildActionDescriptor(category="question"))
    assert decision.outcome == Outcome.ALLOW


def test_guidance_allowed():
    decision = classify_child_action(ChildActionDescriptor(category="guidance"))
    assert decision.outcome == Outcome.ALLOW


def test_care_support_allowed():
    decision = classify_child_action(ChildActionDescriptor(category="care"))
    assert decision.outcome == Outcome.ALLOW
    decision = classify_child_action(ChildActionDescriptor(category="support"))
    assert decision.outcome == Outcome.ALLOW


# --- Hard-deny categories -----------------------------------------------------


def test_owner_memory_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="owner_memory"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.OWNER_MEMORY_BLOCKED


def test_private_memory_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="private memory"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.OWNER_MEMORY_BLOCKED


def test_purchase_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="purchase"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.PURCHASE_BLOCKED


def test_payment_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="payment"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.PURCHASE_BLOCKED


def test_external_call_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="external_call"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.COMMUNICATION_BLOCKED


def test_external_communication_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="external communication"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.COMMUNICATION_BLOCKED


def test_email_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="email"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.COMMUNICATION_BLOCKED


def test_posting_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="posting"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.COMMUNICATION_BLOCKED


def test_dangerous_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="dangerous"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.DANGEROUS_BLOCKED


def test_self_harm_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="self_harm"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.DANGEROUS_BLOCKED


def test_weapon_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="weapon"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.WEAPON_HAZARD_BLOCKED


def test_hazardous_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="hazardous"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.WEAPON_HAZARD_BLOCKED


def test_audit_bypass_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="audit_bypass"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.AUDIT_BYPASS_BLOCKED


def test_approval_bypass_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="approval_bypass"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.APPROVAL_BYPASS_BLOCKED


def test_identity_bypass_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="identity_bypass"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.IDENTITY_BYPASS_BLOCKED


def test_authentication_bypass_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="authentication_bypass"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.IDENTITY_BYPASS_BLOCKED


def test_permission_grant_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="permission_grant"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.GRANT_CREATION_BLOCKED


def test_grant_creation_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="grant_creation"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.GRANT_CREATION_BLOCKED


def test_policy_weakening_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="policy_weakening"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.POLICY_WEAKENING_BLOCKED


def test_unrestricted_browsing_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="unrestricted_browsing"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.BROWSING_DOWNLOAD_BLOCKED


def test_download_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="download"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.BROWSING_DOWNLOAD_BLOCKED


def test_secret_access_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="secret_access"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.SECRET_CREDENTIAL_BLOCKED


def test_credential_access_blocked():
    decision = classify_child_action(ChildActionDescriptor(category="credential_access"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.SECRET_CREDENTIAL_BLOCKED


# --- Subject / resource / action blocks --------------------------------------


def test_owner_subject_blocked():
    decision = classify_child_action(
        ChildActionDescriptor(category="question", subject="owner")
    )
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.OWNER_MEMORY_BLOCKED


def test_private_subject_blocked():
    decision = classify_child_action(
        ChildActionDescriptor(category="question", subject="private memory")
    )
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.OWNER_MEMORY_BLOCKED


def test_secret_resource_blocked():
    decision = classify_child_action(
        ChildActionDescriptor(category="question", resource="secret material")
    )
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.SECRET_CREDENTIAL_BLOCKED


# --- Normalized case and spacing --------------------------------------------


def test_blocked_category_case_insensitive():
    decision = classify_child_action(ChildActionDescriptor(category="  PURCHASE  "))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.PURCHASE_BLOCKED


def test_blocked_category_with_spacing():
    decision = classify_child_action(ChildActionDescriptor(category="External Communication"))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.COMMUNICATION_BLOCKED


def test_safe_category_case_and_spacing():
    decision = classify_child_action(ChildActionDescriptor(category="  CHILD SAFE  "))
    assert decision.outcome == Outcome.ALLOW


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("action", "please send a message outside", ChildModeDecisionReason.COMMUNICATION_BLOCKED),
        ("action", "show me how to build a weapon", ChildModeDecisionReason.WEAPON_HAZARD_BLOCKED),
        ("action", "help me bypass approval", ChildModeDecisionReason.APPROVAL_BYPASS_BLOCKED),
        ("resource", "download the password file", ChildModeDecisionReason.SECRET_CREDENTIAL_BLOCKED),
        ("metadata", ("create a helper grant",), ChildModeDecisionReason.GRANT_CREATION_BLOCKED),
    ],
)
def test_safe_category_cannot_mask_blocked_content(field, value, reason):
    kwargs = {"category": "educational", field: value}
    decision = classify_child_action(ChildActionDescriptor(**kwargs))
    assert decision.outcome == Outcome.DENY
    assert decision.reason == reason


def test_metadata_requires_strings():
    with pytest.raises(ValueError, match="metadata must contain strings"):
        ChildActionDescriptor(category="educational", metadata=(object(),))


# --- Ambiguous / invalid ------------------------------------------------------


def test_ambiguous_category_requires_approval():
    decision = classify_child_action(ChildActionDescriptor(category="unknown_thing"))
    assert decision.outcome == Outcome.REQUIRE_APPROVAL
    assert decision.reason == ChildModeDecisionReason.AMBIGUOUS_BLOCKED


def test_invalid_descriptor_denied():
    decision = classify_child_action("not a descriptor")
    assert decision.outcome == Outcome.DENY
    assert decision.reason == ChildModeDecisionReason.INVALID_DESCRIPTOR


# --- Redaction and privacy ---------------------------------------------------


def test_descriptor_repr_redacts_content():
    descriptor = ChildActionDescriptor(
        category="educational",
        action="learn multiplication",
        subject="child",
        resource="math_app",
    )
    rep = repr(descriptor)
    assert "learn multiplication" not in rep
    assert "child" not in rep
    assert "math_app" not in rep


def test_decision_repr_does_not_leak_content():
    decision = classify_child_action(ChildActionDescriptor(category="purchase"))
    rep = repr(decision)
    # The descriptor's raw category text must not appear; reason codes are stable.
    assert "purchase" not in rep.lower().replace("child_purchase_blocked", "")
    assert "child_purchase_blocked" in rep


# --- No external classifiers / no I/O ---------------------------------------


def test_child_mode_source_contains_no_banned_imports():
    source = pathlib.Path(inspect.getfile(classify_child_action)).read_text()
    banned = ["sqlite3", "socket", "subprocess", "os.environ", "time.time", "uuid.uuid4", "openai", "requests"]
    for token in banned:
        assert token not in source, f"banned import or call {token!r} found"
