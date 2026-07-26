"""Synthetic test suite for reviewed skill evolution lifecycle (Phase 6 Part B)."""

import hashlib
import pytest

from core.action_policy import Actor, ActorContext
from core.phase6_ecosystem.skill_package import (
    PublisherTrust,
    SignatureEvidence,
    SkillFileDigest,
    SkillPackageCandidate,
    SkillPackageManifest,
    SkillPermissionDeclaration,
    SkillReview,
    SkillRollbackMetadata,
)
from core.phase6_ecosystem.skill_review import (
    SkillEvolutionCoordinator,
    SkillEvolutionReason,
    SkillEvolutionStateRecord,
    SkillLifecycleState,
)


def _make_candidate(pkg_id: str = "pkg_rev_01", version: str = "1.0.0") -> SkillPackageCandidate:
    files = (SkillFileDigest("main.py", hashlib.sha256(b"code").hexdigest(), 4),)
    perms = (SkillPermissionDeclaration("skill", "skill.execute", "skill", "timer"),)
    manifest = SkillPackageManifest(
        package_id=pkg_id,
        name="Review Test Skill",
        version=version,
        publisher_id="pub_acme",
        description="Testing review lifecycle",
        declared_permissions=perms,
        files=files,
        dependencies=(),
        created_at=1000.0,
    )
    return SkillPackageCandidate(manifest, {"main.py": b"code"})


def test_full_successful_lifecycle_progression():
    coord = SkillEvolutionCoordinator()
    candidate = _make_candidate()
    owner_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")
    now = 1000.0

    # 1. Propose
    record, reason = coord.propose_package("evt_1", candidate, owner_ctx, now)
    assert record.state == SkillLifecycleState.PROPOSED
    assert reason == SkillEvolutionReason.OK

    # 2. Validate
    reason = coord.validate_package("evt_2", record, owner_ctx, now + 1)
    assert record.state == SkillLifecycleState.VALIDATED
    assert reason == SkillEvolutionReason.OK

    # 3. Submit for review
    reason = coord.submit_for_review("evt_3", record, owner_ctx, now + 2)
    assert record.state == SkillLifecycleState.AWAITING_REVIEW
    assert reason == SkillEvolutionReason.OK

    # 4. Approve review by owner
    review = SkillReview(
        review_id="rev_1",
        package_id=record.manifest().package_id,
        package_digest=record.digest(),
        version=record.manifest().version,
        publisher_id=record.manifest().publisher_id,
        reviewed_permissions=record.manifest().declared_permissions,
        reviewer_actor_id="owner_1",
        reviewer_role="owner",
        outcome="approved",
        reviewed_at=now + 3,
    )
    reason = coord.approve_review("evt_4", record, review, owner_ctx, now + 3)
    assert record.state == SkillLifecycleState.APPROVED
    assert reason == SkillEvolutionReason.OK

    # 5. Create install plan & mark ready
    trust = PublisherTrust("pub_acme", "trusted", ("k1",), ())
    sig = SignatureEvidence("pub_acme", "k1", "ab" * 64)
    plan, reason = coord.create_install_plan(
        "evt_5",
        record,
        "plan_1",
        sig,
        trust,
        lambda d, s: True,
        [],
        rollback_metadata=None,
        is_replacement=False,
        actor_context=owner_ctx,
        now=now + 4,
    )
    assert record.state == SkillLifecycleState.INSTALL_READY
    assert plan is not None
    assert reason == SkillEvolutionReason.OK

    # 6. Record installation
    reason = coord.record_installation("evt_6", record, owner_ctx, now + 5)
    assert record.state == SkillLifecycleState.INSTALLED_RECORDED
    assert reason == SkillEvolutionReason.OK
    assert len(record.audit_log) == 6


def test_assistant_self_approval_denied():
    coord = SkillEvolutionCoordinator()
    candidate = _make_candidate()
    assistant_ctx = ActorContext(actor=Actor.SYSTEM, actor_id="assistant_ai", session_id="s1")
    owner_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")

    record, _ = coord.propose_package("evt_1", candidate, assistant_ctx, 1000.0)
    coord.validate_package("evt_2", record, assistant_ctx, 1001.0)
    coord.submit_for_review("evt_3", record, assistant_ctx, 1002.0)

    review = SkillReview("r1", record.manifest().package_id, record.digest(), record.manifest().version, record.manifest().publisher_id, record.manifest().declared_permissions, "assistant_ai", "owner", "approved", 1003.0)

    # Attempt self-approval by non-owner -> DENIED
    reason = coord.approve_review("evt_4", record, review, assistant_ctx, 1003.0)
    assert reason == SkillEvolutionReason.ASSISTANT_SELF_APPROVAL_DENIED
    assert record.state == SkillLifecycleState.AWAITING_REVIEW


def test_rejection_prevents_bypassing_resubmission():
    coord = SkillEvolutionCoordinator()
    candidate = _make_candidate()
    owner_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")

    record, _ = coord.propose_package("evt_1", candidate, owner_ctx, 1000.0)
    coord.validate_package("evt_2", record, owner_ctx, 1001.0)
    coord.reject_review("evt_3", record, owner_ctx, 1002.0)

    assert record.state == SkillLifecycleState.REJECTED

    # Resubmitting identical candidate fails
    record.state = SkillLifecycleState.VALIDATED
    reason = coord.submit_for_review("evt_4", record, owner_ctx, 1003.0)
    assert reason == SkillEvolutionReason.REJECTED_RESUBMISSION
    assert record.state == SkillLifecycleState.REJECTED


def test_revocation_transition():
    coord = SkillEvolutionCoordinator()
    candidate = _make_candidate()
    owner_ctx = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")

    record, _ = coord.propose_package("evt_1", candidate, owner_ctx, 1000.0)
    coord.revoke("evt_2", record, owner_ctx, 1001.0)
    assert record.state == SkillLifecycleState.REVOKED


def test_content_free_repr_review():
    coord = SkillEvolutionCoordinator()
    candidate = _make_candidate()
    record = SkillEvolutionStateRecord(candidate)
    assert repr(coord) == "SkillEvolutionCoordinator()"
    assert repr(record) == "SkillEvolutionStateRecord()"


def test_non_owner_cannot_revoke_or_record_installation() -> None:
    coord = SkillEvolutionCoordinator()
    candidate = _make_candidate()
    owner = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")
    guest = ActorContext(actor=Actor.GUEST, actor_id="guest_1", session_id="s1")
    record, _ = coord.propose_package("evt_1", candidate, owner, 1000.0)
    assert coord.revoke("evt_2", record, guest, 1001.0) is SkillEvolutionReason.UNAUTHORIZED_REVIEWER
    assert record.state is SkillLifecycleState.PROPOSED


def test_replacement_requires_explicit_rollback_metadata() -> None:
    coord = SkillEvolutionCoordinator()
    candidate = _make_candidate()
    owner = ActorContext(actor=Actor.OWNER, actor_id="owner_1", session_id="s1")
    record, _ = coord.propose_package("evt_1", candidate, owner, 1000.0)
    coord.validate_package("evt_2", record, owner, 1001.0)
    coord.submit_for_review("evt_3", record, owner, 1002.0)
    review = SkillReview(
        "rev_1", record.manifest().package_id, record.digest(), record.manifest().version,
        record.manifest().publisher_id, record.manifest().declared_permissions,
        owner.actor_id, "owner", "approved", 1003.0,
    )
    assert coord.approve_review("evt_4", record, review, owner, 1003.0) is SkillEvolutionReason.OK
    plan, reason = coord.create_install_plan(
        "evt_5", record, "plan_1", SignatureEvidence("pub_acme", "k1", "ab" * 64),
        PublisherTrust("pub_acme", "trusted", ("k1",), ()), lambda *_: True, (),
        rollback_metadata=None, is_replacement=True, actor_context=owner, now=1004.0,
    )
    assert plan is None
    assert reason is SkillEvolutionReason.POLICY_DENIED
