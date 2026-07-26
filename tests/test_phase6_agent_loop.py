"""Synthetic BoundedAgentLoop behavior tests with injected fakes only."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import List, Optional

import pytest

from core.phase6_agent.contracts import (
    ActionObservation,
    ActionOutcome,
    ApprovalReference,
    AuthorizationDecision,
    AuthorizationOutcome,
    ConfidenceCategory,
    CorrectionRequest,
    FailureCode,
    LoopBudget,
    LoopRequest,
    LoopState,
    ProposedAction,
)
from core.phase6_agent.loop import BoundedAgentLoop


class FakeClock:
    def __init__(self, values: List[float] | None = None, start: float = 0.0):
        self._values = list(values or [])
        self._current = start

    def __call__(self) -> float:
        if self._values:
            self._current = self._values.pop(0)
            return self._current
        self._current += 0.001
        return self._current

    def advance(self, delta: float) -> None:
        self._current += delta
        self._values.insert(0, self._current)


class IdSeq:
    def __init__(self, prefix: str = "id"):
        self.n = 0
        self.prefix = prefix

    def __call__(self) -> str:
        self.n += 1
        return f"{self.prefix}-{self.n}"


@dataclass
class ScriptedPlanner:
    actions: List[ProposedAction]
    calls: int = 0
    last_correction: Optional[CorrectionRequest] = None

    def plan(self, request, *, correction, step_index):
        self.calls += 1
        self.last_correction = correction
        if not self.actions:
            raise RuntimeError("unexpected plan")
        return self.actions.pop(0)


@dataclass
class ScriptedAuthorizer:
    decisions: List[AuthorizationDecision]
    calls: int = 0

    def authorize(self, request, proposal):
        self.calls += 1
        if not self.decisions:
            return AuthorizationDecision(AuthorizationOutcome.DENY, FailureCode.DENIED)
        return self.decisions.pop(0)


@dataclass
class RecordingAuditor:
    ok: bool = True
    calls: int = 0
    raise_exc: bool = False

    def record(self, request, proposal, decision) -> bool:
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError("audit boom")
        return self.ok


@dataclass
class ScriptedExecutor:
    observations: List[ActionObservation | Exception]
    calls: int = 0

    def execute(self, authorized):
        self.calls += 1
        if not self.observations:
            raise RuntimeError("no observation")
        item = self.observations.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@dataclass
class ApprovalBox:
    approval: Optional[ApprovalReference]
    calls: int = 0

    def resolve(self, request, proposal):
        self.calls += 1
        return self.approval


@dataclass
class CancelFlag:
    cancelled: bool = False

    def is_cancelled(self) -> bool:
        return self.cancelled


def _request(**kwargs) -> LoopRequest:
    base = dict(
        request_id="req-1",
        actor_id="owner-1",
        session_id="sess-1",
        goal_summary="do the thing",
        declared_tools=("tool-a", "tool-b"),
        budget=LoopBudget(max_total_steps=10, max_tool_actions=5, max_corrections=3),
    )
    base.update(kwargs)
    return LoopRequest(**base)


def _proposal(action_id: str, tool_id: str = "tool-a") -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        tool_id=tool_id,
        target="exact.target",
        action="run",
        step_description="step",
        expected_observation_type="result",
        confidence=ConfidenceCategory.HIGH,
    )


def _obs(action_id: str, oid: str, outcome: ActionOutcome, summary: str = "ok") -> ActionObservation:
    return ActionObservation(
        observation_id=oid,
        action_id=action_id,
        observation_type="result",
        outcome=outcome,
        summary=summary,
        observed_at_mono=1.0,
    )


def _allow() -> AuthorizationDecision:
    return AuthorizationDecision(AuthorizationOutcome.ALLOW, FailureCode.DENIED)


def _deny() -> AuthorizationDecision:
    return AuthorizationDecision(AuthorizationOutcome.DENY, FailureCode.DENIED)


def _need_approval() -> AuthorizationDecision:
    return AuthorizationDecision(AuthorizationOutcome.REQUIRE_APPROVAL, FailureCode.APPROVAL_REQUIRED)


def _loop(planner, authorizer, auditor, executor, clock=None, approval=None, cancel=None):
    return BoundedAgentLoop(
        planner=planner,
        authorizer=authorizer,
        auditor=auditor,
        executor=executor,
        clock=clock or FakeClock(start=10.0),
        id_factory=IdSeq("evt"),
        approval_resolver=approval,
        cancellation=cancel,
    )


def test_one_step_success():
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_allow()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS, "done")])
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(_request())
    assert result.state is LoopState.SUCCEEDED
    assert result.actions_used == 1
    assert result.final_summary == "done"
    assert executor.calls == 1


def test_multi_step_success_via_correction_then_success():
    planner = ScriptedPlanner([_proposal("act-1"), _proposal("act-2")])
    authorizer = ScriptedAuthorizer([_allow(), _allow()])
    executor = ScriptedExecutor(
        [
            _obs("act-1", "obs-1", ActionOutcome.RETRYABLE_FAILURE, "retry"),
            _obs("act-2", "obs-2", ActionOutcome.SUCCESS, "fixed"),
        ]
    )
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(_request())
    assert result.state is LoopState.SUCCEEDED
    assert result.corrections_used == 1
    assert result.final_summary == "fixed"
    assert planner.last_correction is not None


def test_maximum_step_exhaustion():
    actions = [_proposal(f"act-{i}") for i in range(1, 6)]
    planner = ScriptedPlanner(actions)
    authorizer = ScriptedAuthorizer([_allow()] * 10)
    executor = ScriptedExecutor(
        [_obs(f"act-{i}", f"obs-{i}", ActionOutcome.RETRYABLE_FAILURE) for i in range(1, 6)]
    )
    req = _request(budget=LoopBudget(max_total_steps=3, max_tool_actions=10, max_corrections=10))
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(req)
    assert result.state is LoopState.EXHAUSTED
    assert result.failure_code is FailureCode.BUDGET_EXHAUSTED


def test_action_budget_exhaustion():
    planner = ScriptedPlanner([_proposal("act-1"), _proposal("act-2")])
    authorizer = ScriptedAuthorizer([_allow(), _allow()])
    executor = ScriptedExecutor(
        [
            _obs("act-1", "obs-1", ActionOutcome.RETRYABLE_FAILURE),
            _obs("act-2", "obs-2", ActionOutcome.SUCCESS),
        ]
    )
    req = _request(budget=LoopBudget(max_total_steps=10, max_tool_actions=1, max_corrections=5))
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(req)
    # first action fails -> correct -> second plan authorized but action budget exhausted before act
    assert result.state is LoopState.EXHAUSTED
    assert executor.calls == 1


def test_correction_budget_exhaustion():
    planner = ScriptedPlanner([_proposal(f"act-{i}") for i in range(1, 5)])
    authorizer = ScriptedAuthorizer([_allow()] * 5)
    executor = ScriptedExecutor(
        [_obs(f"act-{i}", f"obs-{i}", ActionOutcome.RETRYABLE_FAILURE) for i in range(1, 5)]
    )
    req = _request(budget=LoopBudget(max_total_steps=20, max_tool_actions=10, max_corrections=1, max_consecutive_failures=10))
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(req)
    assert result.state is LoopState.EXHAUSTED
    assert result.corrections_used == 1


def test_elapsed_time_exhaustion_injected_clock():
    # Clock returns a large jump after start.
    clock = FakeClock(values=[1.0, 1.0, 1.0, 1000.0])
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_allow()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS)])
    req = _request(budget=LoopBudget(max_elapsed_ms=100, max_total_steps=10, max_tool_actions=5))
    result = _loop(planner, authorizer, RecordingAuditor(), executor, clock=clock).run(req)
    assert result.state is LoopState.EXHAUSTED
    assert result.failure_code is FailureCode.BUDGET_EXHAUSTED
    assert executor.calls == 0


def test_cancellation_before_planning():
    cancel = CancelFlag(True)
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_allow()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS)])
    result = _loop(planner, authorizer, RecordingAuditor(), executor, cancel=cancel).run(_request())
    assert result.state is LoopState.CANCELLED
    assert planner.calls == 0
    assert executor.calls == 0


def test_denial_never_calls_executor():
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_deny()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS)])
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(_request())
    assert result.state is LoopState.DENIED
    assert executor.calls == 0


def test_approval_required_without_resolver():
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_need_approval()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS)])
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(_request())
    assert result.state is LoopState.WAITING_FOR_APPROVAL
    assert executor.calls == 0


def test_valid_one_time_approval_then_duplicate_fails():
    proposal = _proposal("act-1")
    approval = ApprovalReference(
        approval_id="appr-1",
        request_id="req-1",
        actor_id="owner-1",
        session_id="sess-1",
        action_id="act-1",
        tool_id="tool-a",
        target="exact.target",
        action="run",
        expires_at_mono=10_000.0,
    )
    # First run succeeds with approval + recheck allow.
    planner = ScriptedPlanner([proposal])
    authorizer = ScriptedAuthorizer([_need_approval(), _allow()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS, "ok")])
    box = ApprovalBox(approval)
    loop = _loop(planner, authorizer, RecordingAuditor(), executor, approval=box)
    result = loop.run(_request())
    assert result.state is LoopState.SUCCEEDED

    # Same loop instance: duplicate approval id fails.
    planner2 = ScriptedPlanner([_proposal("act-2")])
    authorizer2 = ScriptedAuthorizer([_need_approval(), _allow()])
    executor2 = ScriptedExecutor([_obs("act-2", "obs-2", ActionOutcome.SUCCESS)])
    loop2 = BoundedAgentLoop(
        planner=planner2,
        authorizer=authorizer2,
        auditor=RecordingAuditor(),
        executor=executor2,
        clock=FakeClock(start=10.0),
        id_factory=IdSeq("evt2"),
        approval_resolver=ApprovalBox(approval),
    )
    # Transfer used approvals by using same loop object path: use first loop with new scripts
    loop._planner = ScriptedPlanner([_proposal("act-9")])
    loop._authorizer = ScriptedAuthorizer([_need_approval()])
    loop._executor = ScriptedExecutor([_obs("act-9", "obs-9", ActionOutcome.SUCCESS)])
    loop._approval_resolver = ApprovalBox(approval)
    dup = loop.run(_request())
    assert dup.state is LoopState.FAILED
    assert dup.failure_code is FailureCode.APPROVAL_DUPLICATE


def test_stale_approval():
    approval = ApprovalReference(
        approval_id="appr-stale",
        request_id="req-1",
        actor_id="owner-1",
        session_id="sess-1",
        action_id="act-1",
        tool_id="tool-a",
        target="exact.target",
        action="run",
        expires_at_mono=1.0,
    )
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_need_approval()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS)])
    result = _loop(
        planner,
        authorizer,
        RecordingAuditor(),
        executor,
        clock=FakeClock(start=50.0),
        approval=ApprovalBox(approval),
    ).run(_request())
    assert result.failure_code is FailureCode.APPROVAL_STALE
    assert executor.calls == 0


def test_wrong_actor_approval_mismatch():
    approval = ApprovalReference(
        approval_id="appr-bad",
        request_id="req-1",
        actor_id="other-owner",
        session_id="sess-1",
        action_id="act-1",
        tool_id="tool-a",
        target="exact.target",
        action="run",
        expires_at_mono=10_000.0,
    )
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_need_approval()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS)])
    result = _loop(
        planner, authorizer, RecordingAuditor(), executor, approval=ApprovalBox(approval)
    ).run(_request())
    assert result.failure_code is FailureCode.APPROVAL_MISMATCH
    assert executor.calls == 0


def test_policy_recheck_after_approval_can_deny():
    approval = ApprovalReference(
        approval_id="appr-2",
        request_id="req-1",
        actor_id="owner-1",
        session_id="sess-1",
        action_id="act-1",
        tool_id="tool-a",
        target="exact.target",
        action="run",
        expires_at_mono=10_000.0,
    )
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_need_approval(), _deny()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS)])
    result = _loop(
        planner, authorizer, RecordingAuditor(), executor, approval=ApprovalBox(approval)
    ).run(_request())
    assert result.state is LoopState.DENIED
    assert executor.calls == 0


def test_policy_recheck_must_explicitly_allow() -> None:
    approval = ApprovalReference(
        "appr-repeat", "req-1", "owner-1", "sess-1", "act-1", "tool-a",
        "exact.target", "run", 10_000.0,
    )
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS)])
    result = _loop(
        ScriptedPlanner([_proposal("act-1")]),
        ScriptedAuthorizer([_need_approval(), _need_approval()]),
        RecordingAuditor(), executor, approval=ApprovalBox(approval),
    ).run(_request())
    assert result.failure_code is FailureCode.APPROVAL_REQUIRED
    assert executor.calls == 0


def test_audit_failure_blocks_action():
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_allow()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS)])
    result = _loop(planner, authorizer, RecordingAuditor(ok=False), executor).run(_request())
    assert result.failure_code is FailureCode.AUDIT_FAILED
    assert executor.calls == 0
    result2 = _loop(
        ScriptedPlanner([_proposal("act-2")]),
        ScriptedAuthorizer([_allow()]),
        RecordingAuditor(raise_exc=True),
        ScriptedExecutor([_obs("act-2", "obs-2", ActionOutcome.SUCCESS)]),
    ).run(_request())
    assert result2.failure_code is FailureCode.AUDIT_FAILED


def test_executor_exception_sanitized():
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_allow()])
    executor = ScriptedExecutor([RuntimeError("secret path /tmp/x")])
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(_request())
    assert result.failure_code is FailureCode.EXECUTOR_FAILED
    assert "secret" not in result.final_summary
    assert "tmp" not in repr(result)


def test_oversized_observation_rejected():
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_allow()])
    big = _obs("act-1", "obs-1", ActionOutcome.SUCCESS, summary="x" * 200)
    executor = ScriptedExecutor([big])
    req = _request(budget=LoopBudget(max_observation_length=50, max_total_steps=5, max_tool_actions=3))
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(req)
    assert result.failure_code is FailureCode.OBSERVATION_OVERSIZED


def test_stale_observation_rejected():
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_allow()])
    executor = ScriptedExecutor([_obs("act-other", "obs-1", ActionOutcome.SUCCESS)])
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(_request())
    assert result.failure_code is FailureCode.OBSERVATION_STALE


def test_duplicate_observation_rejected_across_retries():
    # First failure, then duplicate observation id on second action.
    planner = ScriptedPlanner([_proposal("act-1"), _proposal("act-2")])
    authorizer = ScriptedAuthorizer([_allow(), _allow()])
    executor = ScriptedExecutor(
        [
            _obs("act-1", "obs-dup", ActionOutcome.RETRYABLE_FAILURE),
            _obs("act-2", "obs-dup", ActionOutcome.SUCCESS),
        ]
    )
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(_request())
    assert result.failure_code is FailureCode.OBSERVATION_DUPLICATE


def test_correction_cannot_introduce_new_tool():
    planner = ScriptedPlanner([_proposal("act-1"), _proposal("act-2", tool_id="tool-evil")])
    # tool-evil not in declared tools
    authorizer = ScriptedAuthorizer([_allow(), _allow()])
    executor = ScriptedExecutor(
        [
            _obs("act-1", "obs-1", ActionOutcome.RETRYABLE_FAILURE),
            _obs("act-2", "obs-2", ActionOutcome.SUCCESS),
        ]
    )
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(_request())
    assert result.failure_code is FailureCode.UNDECLARED_TOOL


def test_non_retryable_cannot_retry():
    planner = ScriptedPlanner([_proposal("act-1"), _proposal("act-2")])
    authorizer = ScriptedAuthorizer([_allow(), _allow()])
    executor = ScriptedExecutor(
        [
            _obs("act-1", "obs-1", ActionOutcome.NON_RETRYABLE_FAILURE),
            _obs("act-2", "obs-2", ActionOutcome.SUCCESS),
        ]
    )
    result = _loop(planner, authorizer, RecordingAuditor(), executor).run(_request())
    assert result.failure_code is FailureCode.NON_RETRYABLE
    assert executor.calls == 1


def test_input_immutability():
    req = _request()
    snapshot = copy.deepcopy(req.declared_tools)
    planner = ScriptedPlanner([_proposal("act-1")])
    authorizer = ScriptedAuthorizer([_allow()])
    executor = ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS)])
    _loop(planner, authorizer, RecordingAuditor(), executor).run(req)
    assert req.declared_tools == snapshot


def test_two_independent_loops_do_not_share_state():
    def build():
        return _loop(
            ScriptedPlanner([_proposal("act-1")]),
            ScriptedAuthorizer([_allow()]),
            RecordingAuditor(),
            ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS, "a")]),
        )

    a = build()
    b = build()
    ra = a.run(_request(request_id="req-a"))
    rb = b.run(_request(request_id="req-b"))
    assert ra.state is LoopState.SUCCEEDED
    assert rb.state is LoopState.SUCCEEDED
    # Independent instances both accept the same action identifier.


def test_deterministic_repeated_results():
    def run_once():
        clock = FakeClock(values=[10.0] * 40)
        ids = IdSeq("x")
        loop = BoundedAgentLoop(
            planner=ScriptedPlanner([_proposal("act-1")]),
            authorizer=ScriptedAuthorizer([_allow()]),
            auditor=RecordingAuditor(),
            executor=ScriptedExecutor([_obs("act-1", "obs-1", ActionOutcome.SUCCESS, "same")]),
            clock=clock,
            id_factory=ids,
        )
        return loop.run(_request())

    r1 = run_once()
    r2 = run_once()
    assert r1.state == r2.state
    assert r1.final_summary == r2.final_summary
    assert r1.steps_used == r2.steps_used
    assert [e.kind for e in r1.events] == [e.kind for e in r2.events]


def test_no_side_effect_imports_in_loop_module():
    import ast
    from pathlib import Path as _Path
    import core.phase6_agent.loop as loop_mod
    tree = ast.parse(_Path(loop_mod.__file__).read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".")[0])
    for banned in ("subprocess", "socket", "requests", "sqlite3", "openai", "httpx"):
        assert banned not in imported
