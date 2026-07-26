"""Bounded reason → authorize → act → observe → correct execution kernel.

This module is an injected execution kernel. Planner/model output grants zero
authority. Every action is authorized and audited immediately before execution
through caller-supplied adapters. The package performs no I/O, network,
subprocess, filesystem, database, Git, or model calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional, Protocol, Sequence, Tuple

from core.phase6_agent.contracts import (
    ActionObservation,
    ActionOutcome,
    ApprovalReference,
    AuthorizationDecision,
    AuthorizationOutcome,
    AuthorizedAction,
    CorrectionRequest,
    EventKind,
    FailureCode,
    LoopEvent,
    LoopRequest,
    LoopResult,
    LoopState,
    ProposedAction,
    validate_finite_mono,
    validate_identifier,
    validate_text,
)


class Cancelled(Exception):
    """Internal cancellation signal; never exposes details."""


class _BudgetExhausted(Exception):
    pass


class Planner(Protocol):
    def plan(
        self,
        request: LoopRequest,
        *,
        correction: Optional[CorrectionRequest],
        step_index: int,
    ) -> ProposedAction:
        ...


class Authorizer(Protocol):
    def authorize(
        self,
        request: LoopRequest,
        proposal: ProposedAction,
    ) -> AuthorizationDecision:
        ...


class Auditor(Protocol):
    def record(
        self,
        request: LoopRequest,
        proposal: ProposedAction,
        decision: AuthorizationDecision,
    ) -> bool:
        """Return True when the content-free audit record was accepted."""
        ...


class Executor(Protocol):
    def execute(self, authorized: AuthorizedAction) -> ActionObservation:
        ...


class ApprovalResolver(Protocol):
    def resolve(
        self,
        request: LoopRequest,
        proposal: ProposedAction,
    ) -> Optional[ApprovalReference]:
        ...


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool:
        ...


Clock = Callable[[], float]
IdFactory = Callable[[], str]


@dataclass
class _LoopCounters:
    planning_attempts: int = 0
    steps: int = 0
    actions: int = 0
    corrections: int = 0
    consecutive_failures: int = 0


class BoundedAgentLoop:
    """Deterministic bounded agent loop. Safe to instantiate concurrently."""

    def __init__(
        self,
        *,
        planner: Planner,
        authorizer: Authorizer,
        auditor: Auditor,
        executor: Executor,
        clock: Clock,
        id_factory: IdFactory,
        approval_resolver: Optional[ApprovalResolver] = None,
        cancellation: Optional[CancellationToken] = None,
    ) -> None:
        if not callable(getattr(planner, "plan", None)):
            raise ValueError("invalid_planner")
        if not callable(getattr(authorizer, "authorize", None)):
            raise ValueError("invalid_authorizer")
        if not callable(getattr(auditor, "record", None)):
            raise ValueError("invalid_auditor")
        if not callable(getattr(executor, "execute", None)):
            raise ValueError("invalid_executor")
        if not callable(clock) or not callable(id_factory):
            raise ValueError("invalid_clock_or_ids")
        self._planner = planner
        self._authorizer = authorizer
        self._auditor = auditor
        self._executor = executor
        self._clock = clock
        self._id_factory = id_factory
        self._approval_resolver = approval_resolver
        self._cancellation = cancellation
        # Cross-run one-time approval tracking for this kernel instance. A
        # production resolver must additionally persist consumption atomically.
        self._used_approval_ids: set[str] = set()

    def __repr__(self) -> str:
        return "BoundedAgentLoop()"

    def run(self, request: LoopRequest) -> LoopResult:
        if not isinstance(request, LoopRequest):
            return LoopResult(
                request_id="invalid-request",
                state=LoopState.FAILED,
                failure_code=FailureCode.INVALID_INPUT,
                steps_used=0,
                actions_used=0,
                corrections_used=0,
                events=(),
                final_summary="",
            )
        budget = request.budget
        started = validate_finite_mono(self._clock(), "clock")
        events: list[LoopEvent] = []
        counters = _LoopCounters()
        state = LoopState.CREATED
        in_flight_action_id: Optional[str] = None
        allowed_tools = set(request.declared_tools)
        # Action and observation correlation state belongs to one run.
        seen_action_ids: set[str] = set()
        seen_observation_ids: set[str] = set()
        correction: Optional[CorrectionRequest] = None
        final_summary = ""

        def emit(kind: EventKind, code: Optional[FailureCode] = None) -> None:
            if len(events) >= budget.max_event_history:
                return
            events.append(
                LoopEvent(
                    event_id=validate_identifier(self._id_factory(), "event_id"),
                    kind=kind,
                    state=state,
                    code=code,
                    at_mono=validate_finite_mono(self._clock(), "clock"),
                )
            )

        def check_cancel() -> None:
            if self._cancellation is not None and self._cancellation.is_cancelled():
                raise Cancelled()

        def check_time() -> None:
            now = validate_finite_mono(self._clock(), "clock")
            if int((now - started) * 1000.0) >= budget.max_elapsed_ms:
                raise _BudgetExhausted()

        def finish(code: Optional[FailureCode]) -> LoopResult:
            summary = final_summary
            try:
                summary = validate_text(
                    summary, field="final_summary", max_length=512, allow_empty=True
                )
            except ValueError:
                summary = ""
            return LoopResult(
                request_id=request.request_id,
                state=state,
                failure_code=code,
                steps_used=counters.steps,
                actions_used=counters.actions,
                corrections_used=counters.corrections,
                events=tuple(events[: budget.max_event_history]),
                final_summary=summary,
            )

        try:
            emit(EventKind.STATE)
            check_cancel()
            check_time()

            while True:
                check_cancel()
                check_time()
                if counters.steps >= budget.max_total_steps:
                    state = LoopState.EXHAUSTED
                    emit(EventKind.TERMINAL, FailureCode.BUDGET_EXHAUSTED)
                    return finish(FailureCode.BUDGET_EXHAUSTED)

                state = LoopState.PLANNING
                emit(EventKind.STATE)
                check_cancel()
                if counters.planning_attempts >= budget.max_planning_attempts:
                    state = LoopState.EXHAUSTED
                    emit(EventKind.TERMINAL, FailureCode.BUDGET_EXHAUSTED)
                    return finish(FailureCode.BUDGET_EXHAUSTED)
                counters.planning_attempts += 1
                counters.steps += 1
                try:
                    proposal = self._planner.plan(
                        request, correction=correction, step_index=counters.steps
                    )
                except Exception:
                    state = LoopState.FAILED
                    emit(EventKind.TERMINAL, FailureCode.INTERNAL)
                    return finish(FailureCode.INTERNAL)
                if not isinstance(proposal, ProposedAction):
                    state = LoopState.FAILED
                    emit(EventKind.PLAN, FailureCode.INVALID_INPUT)
                    return finish(FailureCode.INVALID_INPUT)
                emit(EventKind.PLAN)
                if proposal.action_id in seen_action_ids:
                    state = LoopState.FAILED
                    emit(EventKind.PLAN, FailureCode.DUPLICATE_ACTION)
                    return finish(FailureCode.DUPLICATE_ACTION)
                seen_action_ids.add(proposal.action_id)

                if proposal.completion_proposal:
                    # No claim of success without a prior successful observation.
                    if counters.actions > 0 and final_summary:
                        state = LoopState.SUCCEEDED
                        emit(EventKind.TERMINAL)
                        return finish(None)
                    state = LoopState.FAILED
                    emit(EventKind.TERMINAL, FailureCode.INVALID_INPUT)
                    return finish(FailureCode.INVALID_INPUT)

                if proposal.tool_id not in allowed_tools:
                    state = LoopState.FAILED
                    emit(EventKind.PLAN, FailureCode.UNDECLARED_TOOL)
                    return finish(FailureCode.UNDECLARED_TOOL)
                if correction is not None and proposal.tool_id not in set(correction.allowed_tools):
                    state = LoopState.FAILED
                    emit(EventKind.CORRECT, FailureCode.SCOPE_EXPANSION)
                    return finish(FailureCode.SCOPE_EXPANSION)

                state = LoopState.AUTHORIZING
                emit(EventKind.STATE)
                check_cancel()
                try:
                    decision = self._authorizer.authorize(request, proposal)
                except Exception:
                    state = LoopState.FAILED
                    emit(EventKind.AUTHORIZE, FailureCode.INTERNAL)
                    return finish(FailureCode.INTERNAL)
                if not isinstance(decision, AuthorizationDecision):
                    state = LoopState.FAILED
                    emit(EventKind.AUTHORIZE, FailureCode.INVALID_INPUT)
                    return finish(FailureCode.INVALID_INPUT)
                emit(EventKind.AUTHORIZE, decision.reason_code)

                if decision.outcome is AuthorizationOutcome.DENY:
                    state = LoopState.DENIED
                    emit(EventKind.TERMINAL, FailureCode.DENIED)
                    return finish(FailureCode.DENIED)

                approval: Optional[ApprovalReference] = None
                if decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL:
                    state = LoopState.WAITING_FOR_APPROVAL
                    emit(EventKind.STATE, FailureCode.APPROVAL_REQUIRED)
                    check_cancel()
                    if self._approval_resolver is None:
                        emit(EventKind.TERMINAL, FailureCode.APPROVAL_REQUIRED)
                        return finish(FailureCode.APPROVAL_REQUIRED)
                    try:
                        approval = self._approval_resolver.resolve(request, proposal)
                    except Exception:
                        state = LoopState.FAILED
                        emit(EventKind.APPROVAL, FailureCode.APPROVAL_INVALID)
                        return finish(FailureCode.APPROVAL_INVALID)
                    if approval is None:
                        emit(EventKind.TERMINAL, FailureCode.APPROVAL_REQUIRED)
                        return finish(FailureCode.APPROVAL_REQUIRED)
                    ok, code = self._validate_approval(
                        request, proposal, approval, self._used_approval_ids
                    )
                    if not ok:
                        state = LoopState.FAILED
                        emit(EventKind.APPROVAL, code)
                        emit(EventKind.TERMINAL, code)
                        return finish(code)
                    self._used_approval_ids.add(approval.approval_id)
                    emit(EventKind.APPROVAL)
                    try:
                        recheck = self._authorizer.authorize(request, proposal)
                    except Exception:
                        state = LoopState.FAILED
                        emit(EventKind.AUTHORIZE, FailureCode.INTERNAL)
                        return finish(FailureCode.INTERNAL)
                    # The policy adapter must consume/recognize the correlated
                    # approval and return ALLOW. A second REQUIRE_APPROVAL is not
                    # permission to execute.
                    if (
                        not isinstance(recheck, AuthorizationDecision)
                        or recheck.outcome is not AuthorizationOutcome.ALLOW
                    ):
                        state = LoopState.DENIED
                        code = (
                            FailureCode.APPROVAL_REQUIRED
                            if isinstance(recheck, AuthorizationDecision)
                            and recheck.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
                            else FailureCode.DENIED
                        )
                        emit(EventKind.AUTHORIZE, code)
                        emit(EventKind.TERMINAL, code)
                        return finish(code)
                    decision = recheck

                if decision.outcome is not AuthorizationOutcome.ALLOW:
                    state = LoopState.DENIED
                    emit(EventKind.TERMINAL, FailureCode.DENIED)
                    return finish(FailureCode.DENIED)

                if counters.actions >= budget.max_tool_actions:
                    state = LoopState.EXHAUSTED
                    emit(EventKind.TERMINAL, FailureCode.BUDGET_EXHAUSTED)
                    return finish(FailureCode.BUDGET_EXHAUSTED)

                check_cancel()
                try:
                    audited = bool(self._auditor.record(request, proposal, decision))
                except Exception:
                    audited = False
                if not audited:
                    state = LoopState.FAILED
                    emit(EventKind.AUDIT, FailureCode.AUDIT_FAILED)
                    emit(EventKind.TERMINAL, FailureCode.AUDIT_FAILED)
                    return finish(FailureCode.AUDIT_FAILED)
                emit(EventKind.AUDIT)

                authorized = AuthorizedAction(
                    proposal=proposal,
                    decision=decision,
                    approval=approval,
                    authorized_at_mono=validate_finite_mono(self._clock(), "clock"),
                )

                state = LoopState.ACTING
                emit(EventKind.STATE)
                check_cancel()
                in_flight_action_id = proposal.action_id
                counters.actions += 1
                try:
                    observation = self._executor.execute(authorized)
                except Exception:
                    observation = None
                emit(EventKind.ACT)

                state = LoopState.OBSERVING
                emit(EventKind.STATE)
                check_cancel()
                if observation is None:
                    state = LoopState.FAILED
                    emit(EventKind.OBSERVE, FailureCode.EXECUTOR_FAILED)
                    emit(EventKind.TERMINAL, FailureCode.EXECUTOR_FAILED)
                    return finish(FailureCode.EXECUTOR_FAILED)
                if not isinstance(observation, ActionObservation):
                    state = LoopState.FAILED
                    emit(EventKind.OBSERVE, FailureCode.OBSERVATION_INVALID)
                    return finish(FailureCode.OBSERVATION_INVALID)
                if len(observation.summary) > budget.max_observation_length:
                    state = LoopState.FAILED
                    emit(EventKind.OBSERVE, FailureCode.OBSERVATION_OVERSIZED)
                    return finish(FailureCode.OBSERVATION_OVERSIZED)
                if observation.observation_id in seen_observation_ids:
                    state = LoopState.FAILED
                    emit(EventKind.OBSERVE, FailureCode.OBSERVATION_DUPLICATE)
                    return finish(FailureCode.OBSERVATION_DUPLICATE)
                if observation.action_id != in_flight_action_id:
                    state = LoopState.FAILED
                    emit(EventKind.OBSERVE, FailureCode.OBSERVATION_STALE)
                    return finish(FailureCode.OBSERVATION_STALE)
                if observation.observation_type != proposal.expected_observation_type:
                    state = LoopState.FAILED
                    emit(EventKind.OBSERVE, FailureCode.OBSERVATION_INVALID)
                    return finish(FailureCode.OBSERVATION_INVALID)
                seen_observation_ids.add(observation.observation_id)
                in_flight_action_id = None
                emit(EventKind.OBSERVE)

                if observation.outcome is ActionOutcome.SUCCESS:
                    counters.consecutive_failures = 0
                    final_summary = observation.summary[:512]
                    correction = None
                    state = LoopState.SUCCEEDED
                    emit(EventKind.TERMINAL)
                    return finish(None)

                if observation.outcome is ActionOutcome.CANCELLED:
                    state = LoopState.CANCELLED
                    emit(EventKind.TERMINAL, FailureCode.CANCELLED)
                    return finish(FailureCode.CANCELLED)

                if observation.outcome is ActionOutcome.NON_RETRYABLE_FAILURE:
                    state = LoopState.FAILED
                    emit(EventKind.TERMINAL, FailureCode.NON_RETRYABLE)
                    return finish(FailureCode.NON_RETRYABLE)

                counters.consecutive_failures += 1
                if counters.consecutive_failures >= budget.max_consecutive_failures:
                    state = LoopState.EXHAUSTED
                    emit(EventKind.TERMINAL, FailureCode.BUDGET_EXHAUSTED)
                    return finish(FailureCode.BUDGET_EXHAUSTED)
                if counters.corrections >= budget.max_corrections:
                    state = LoopState.EXHAUSTED
                    emit(EventKind.TERMINAL, FailureCode.BUDGET_EXHAUSTED)
                    return finish(FailureCode.BUDGET_EXHAUSTED)

                state = LoopState.CORRECTING
                emit(EventKind.STATE)
                check_cancel()
                counters.corrections += 1
                correction = CorrectionRequest(
                    correction_id=validate_identifier(self._id_factory(), "correction_id"),
                    failed_action_id=proposal.action_id,
                    observation_id=observation.observation_id,
                    allowed_tools=tuple(sorted(allowed_tools)),
                    note="retry_from_observation",
                )
                emit(EventKind.CORRECT)

        except Cancelled:
            state = LoopState.CANCELLED
            emit(EventKind.TERMINAL, FailureCode.CANCELLED)
            return finish(FailureCode.CANCELLED)
        except _BudgetExhausted:
            state = LoopState.EXHAUSTED
            emit(EventKind.TERMINAL, FailureCode.BUDGET_EXHAUSTED)
            return finish(FailureCode.BUDGET_EXHAUSTED)

    def _validate_approval(
        self,
        request: LoopRequest,
        proposal: ProposedAction,
        approval: ApprovalReference,
        used_approval_ids: set[str],
    ) -> Tuple[bool, FailureCode]:
        if not isinstance(approval, ApprovalReference):
            return False, FailureCode.APPROVAL_INVALID
        if approval.approval_id in used_approval_ids:
            return False, FailureCode.APPROVAL_DUPLICATE
        now = validate_finite_mono(self._clock(), "clock")
        if approval.expires_at_mono <= now:
            return False, FailureCode.APPROVAL_STALE
        if not approval.matches_proposal(proposal, request):
            return False, FailureCode.APPROVAL_MISMATCH
        return True, FailureCode.APPROVAL_REQUIRED


__all__ = [
    "ApprovalResolver",
    "Auditor",
    "Authorizer",
    "BoundedAgentLoop",
    "CancellationToken",
    "Executor",
    "Planner",
]
