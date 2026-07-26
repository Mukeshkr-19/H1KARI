"""Synthetic contract tests for Phase 6 bounded-agent value objects."""

from __future__ import annotations

import pytest

from core.phase6_agent.contracts import (
    ActionObservation,
    ActionOutcome,
    ApprovalReference,
    AuthorizationDecision,
    AuthorizationOutcome,
    AuthorizedAction,
    ConfidenceCategory,
    CorrectionRequest,
    FailureCode,
    LoopBudget,
    LoopRequest,
    ProposedAction,
    RemoteWorkerAuthorityEnvelope,
)


def _proposal(**kwargs) -> ProposedAction:
    base = dict(
        action_id="act-1",
        tool_id="tool-a",
        target="exact.target",
        action="run",
        step_description="Do one safe step",
        expected_observation_type="result",
        confidence=ConfidenceCategory.MEDIUM,
    )
    base.update(kwargs)
    return ProposedAction(**base)


def test_loop_budget_defaults_and_hard_caps():
    budget = LoopBudget()
    assert budget.max_total_steps == 8
    with pytest.raises(ValueError):
        LoopBudget(max_total_steps=10_000)
    with pytest.raises(ValueError):
        LoopBudget(max_elapsed_ms=0)


def test_request_rejects_duplicates_wildcards_and_controls():
    budget = LoopBudget()
    LoopRequest(
        request_id="req-1",
        actor_id="owner-1",
        session_id="sess-1",
        goal_summary="bounded goal",
        declared_tools=("tool-a", "tool-b"),
        budget=budget,
    )
    with pytest.raises(ValueError):
        LoopRequest(
            request_id="req-1",
            actor_id="owner-1",
            session_id="sess-1",
            goal_summary="bounded goal",
            declared_tools=("tool-a", "tool-a"),
            budget=budget,
        )
    with pytest.raises(ValueError):
        ProposedAction(
            action_id="act-1",
            tool_id="tool-a",
            target="*",
            action="run",
            step_description="x",
            expected_observation_type="result",
            confidence=ConfidenceCategory.LOW,
        )
    with pytest.raises(ValueError):
        ProposedAction(
            action_id="act-1",
            tool_id="tool-a",
            target="exact",
            action="run",
            step_description="bad\x00text",
            expected_observation_type="result",
            confidence=ConfidenceCategory.LOW,
        )


def test_no_chain_of_thought_fields_on_proposal():
    fields = set(ProposedAction.__dataclass_fields__)
    forbidden = {
        "reasoning",
        "thought",
        "chain_of_thought",
        "hidden_reasoning",
        "scratchpad",
    }
    assert fields.isdisjoint(forbidden)


def test_content_free_repr():
    proposal = _proposal()
    text = repr(proposal)
    assert "exact.target" not in text
    assert "Do one safe step" not in text
    approval = ApprovalReference(
        approval_id="appr-1",
        request_id="req-1",
        actor_id="owner-1",
        session_id="sess-1",
        action_id="act-1",
        tool_id="tool-a",
        target="exact.target",
        action="run",
        expires_at_mono=100.0,
    )
    assert "owner-1" not in repr(approval)
    assert "exact.target" not in repr(approval)


def test_authorized_action_requires_allow():
    proposal = _proposal()
    deny = AuthorizationDecision(AuthorizationOutcome.DENY, FailureCode.DENIED)
    with pytest.raises(ValueError):
        AuthorizedAction(proposal=proposal, decision=deny, authorized_at_mono=1.0)
    allow = AuthorizationDecision(AuthorizationOutcome.ALLOW, FailureCode.DENIED)
    AuthorizedAction(proposal=proposal, decision=allow, authorized_at_mono=1.0)


def test_observation_and_correction_validation():
    ActionObservation(
        observation_id="obs-1",
        action_id="act-1",
        observation_type="result",
        outcome=ActionOutcome.SUCCESS,
        summary="ok",
        observed_at_mono=1.0,
    )
    with pytest.raises(ValueError):
        CorrectionRequest(
            correction_id="cor-1",
            failed_action_id="act-1",
            observation_id="obs-1",
            allowed_tools=("tool-a", "tool-a"),
            note="retry",
        )


def test_remote_envelope_rejects_bad_nonce_and_duplicates():
    RemoteWorkerAuthorityEnvelope(
        envelope_id="env-1",
        worker_id="worker-1",
        task_id="task-1",
        capability="analyze",
        targets=("repo.main",),
        expires_at_mono=100.0,
        nonce="aabbccddeeff0011",
        max_responses=2,
    )
    with pytest.raises(ValueError):
        RemoteWorkerAuthorityEnvelope(
            envelope_id="env-1",
            worker_id="worker-1",
            task_id="task-1",
            capability="analyze",
            targets=("repo.main", "repo.main"),
            expires_at_mono=100.0,
            nonce="aabbccddeeff0011",
            max_responses=2,
        )
