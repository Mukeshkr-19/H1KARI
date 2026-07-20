"""Deterministic tests for the bounded Phase 3 productivity execution coordinator."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.action_policy import Actor, ActorContext
from core.protocol import validate_server_message
from core.productivity import (
    ActionProposal,
    ActionTarget,
    PreviewField,
    ProductivityAction,
    ProductivityRuntime,
    ProductivityService,
    SqliteApprovalStore,
    TargetKind,
)
from core.productivity.execution import (
    ActionAdapter,
    AdapterInput,
    AdapterResult,
    AdapterResultStatus,
    ExecutionRequest,
    ExecutionTicket,
    ProductivityExecutionCoordinator,
)
from core.productivity.action_results import (
    BrowserSearchResult,
    BrowserSearchResultItem,
    CalendarEventItem,
    CalendarReadResult,
)


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class ApprovalIds:
    def __init__(self, *values: object) -> None:
        self._values = list(values)
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        if not self._values:
            raise RuntimeError("no id")
        value = self._values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


@dataclass(frozen=True, repr=False)
class EmailDraftInput(AdapterInput):
    recipient: str
    subject: str
    body: str
    action: ProductivityAction = field(
        default=ProductivityAction.EMAIL_DRAFT, init=False, repr=False
    )


@dataclass(frozen=True, repr=False)
class BrowserResearchInput(AdapterInput):
    query: str
    action: ProductivityAction = field(
        default=ProductivityAction.BROWSER_RESEARCH, init=False, repr=False
    )


@dataclass(frozen=True, repr=False)
class CalendarReadInput(AdapterInput):
    calendar_name: str
    action: ProductivityAction = field(
        default=ProductivityAction.CALENDAR_READ, init=False, repr=False
    )


@dataclass(frozen=True)
class ResearchShapedResult(AdapterResult):
    result: BrowserSearchResult | None = None


def make_calendar_read_proposal(
    actor: ActorContext, proposal_id: str = "proposal_3"
) -> ActionProposal:
    return ActionProposal(
        proposal_id=proposal_id,
        action=ProductivityAction.CALENDAR_READ,
        actor=actor,
        targets=(ActionTarget(TargetKind.CALENDAR, "Work"),),
        preview_fields=(PreviewField("calendar", "Calendar", "Work"),),
        created_at=1000.0,
        expires_at=2000.0,
    )


@pytest.fixture
def owner() -> ActorContext:
    return ActorContext(actor_id="owner_1", actor=Actor.OWNER, session_id="session_1")


@pytest.fixture
def other_session(owner: ActorContext) -> ActorContext:
    return ActorContext(
        actor_id=owner.actor_id,
        actor=Actor.OWNER,
        session_id="session_2",
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteApprovalStore:
    return SqliteApprovalStore(str(tmp_path / "approvals.db"))


@pytest.fixture
def service(store: SqliteApprovalStore) -> ProductivityService:
    return ProductivityService(store)


@pytest.fixture
def runtime(service: ProductivityService) -> ProductivityRuntime:
    return ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("approval_1"))


def make_email_proposal(actor: ActorContext, proposal_id: str = "proposal_1") -> ActionProposal:
    return ActionProposal(
        proposal_id=proposal_id,
        action=ProductivityAction.EMAIL_DRAFT,
        actor=actor,
        targets=(ActionTarget(TargetKind.EMAIL_RECIPIENT, "user@example.com"),),
        preview_fields=(
            PreviewField("recipient", "Recipient", "user@example.com"),
            PreviewField("subject", "Subject", "Hello"),
        ),
        created_at=1000.0,
        expires_at=2000.0,
    )


def make_browser_proposal(actor: ActorContext, proposal_id: str = "proposal_2") -> ActionProposal:
    return ActionProposal(
        proposal_id=proposal_id,
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=actor,
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(PreviewField("query", "Search query", "hikari"),),
        created_at=1000.0,
        expires_at=2000.0,
    )


def assert_valid(message: dict) -> None:
    assert validate_server_message(message) is None


# ---------------------------------------------------------------------------
# Adapter helpers
# ---------------------------------------------------------------------------


class SuccessAdapter:
    def __init__(self) -> None:
        self.calls: list[AdapterInput] = []

    def __call__(self, input: AdapterInput) -> AdapterResult:
        self.calls.append(input)
        return AdapterResult(AdapterResultStatus.SUCCESS)


class FailureAdapter:
    def __init__(self) -> None:
        self.calls: list[AdapterInput] = []

    def __call__(self, input: AdapterInput) -> AdapterResult:
        self.calls.append(input)
        return AdapterResult(AdapterResultStatus.FAILED, code="draft_failed")


class ExceptionAdapter:
    def __call__(self, input: AdapterInput) -> AdapterResult:
        raise RuntimeError("private adapter detail")


class BadResultAdapter:
    def __call__(self, input: AdapterInput) -> AdapterResult:
        return object()  # type: ignore[return-value]


def complete_adapters(
    action: ProductivityAction | None = None,
    adapter: ActionAdapter | None = None,
) -> dict[ProductivityAction, ActionAdapter]:
    result: dict[ProductivityAction, ActionAdapter] = {
        candidate: FailureAdapter() for candidate in ProductivityAction
    }
    if action is not None and adapter is not None:
        result[action] = adapter
    return result


# ---------------------------------------------------------------------------
# Construction / adapter map validation
# ---------------------------------------------------------------------------


def test_coordinator_requires_productivityruntime(service: ProductivityService) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("a"))
    with pytest.raises(TypeError):
        ProductivityExecutionCoordinator("not-a-runtime", {})  # type: ignore[arg-type]


def test_coordinator_rejects_duplicate_adapter_instance(service: ProductivityService) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("a"))
    shared = SuccessAdapter()
    with pytest.raises(ValueError):
        ProductivityExecutionCoordinator(
            runtime,
            {
                ProductivityAction.EMAIL_DRAFT: shared,
                ProductivityAction.BROWSER_RESEARCH: shared,
            },
        )


def test_coordinator_rejects_undeclared_action_key(service: ProductivityService) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("a"))
    with pytest.raises(ValueError):
        ProductivityExecutionCoordinator(
            runtime,
            {"email.draft": SuccessAdapter()},  # type: ignore[dict-item]
        )


def test_coordinator_rejects_non_callable_adapter(service: ProductivityService) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("a"))
    with pytest.raises(ValueError):
        ProductivityExecutionCoordinator(
            runtime,
            {ProductivityAction.EMAIL_DRAFT: "not-callable"},  # type: ignore[dict-item]
        )


def test_coordinator_accepts_partial_adapter_map(service: ProductivityService) -> None:
    runtime = ProductivityRuntime(service, MutableClock(1500.0), ApprovalIds("a"))
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        {ProductivityAction.EMAIL_DRAFT: SuccessAdapter()},
    )

    assert set(coordinator._adapters) == {ProductivityAction.EMAIL_DRAFT}


# ---------------------------------------------------------------------------
# Execution flow
# ---------------------------------------------------------------------------


def test_successful_execution_returns_completed(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    message = coordinator.execute(request)

    assert message == {
        "type": "productivity_update",
        "proposal_id": proposal.proposal_id,
        "status": "completed",
    }
    assert len(adapter.calls) == 1
    assert isinstance(adapter.calls[0], EmailDraftInput)
    assert_valid(message)


def test_research_success_preserves_bounded_items(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_browser_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    search = BrowserSearchResult(
        "query text here",
        (
            BrowserSearchResultItem(
                "Hello",
                "https://example.com/path",
                "example.com",
                "A snippet",
            ),
        ),
    )

    class ResearchAdapter:
        def __init__(self) -> None:
            self.calls: list[AdapterInput] = []

        def __call__(self, input: AdapterInput) -> ResearchShapedResult:
            self.calls.append(input)
            return ResearchShapedResult(
                AdapterResultStatus.SUCCESS, result=search
            )

    adapter = ResearchAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.BROWSER_RESEARCH, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id=proposal.proposal_id,
        adapter_input=BrowserResearchInput("hikari"),
    )
    message = coordinator.execute(request)

    assert message["type"] == "productivity_research_result"
    assert message["proposal_id"] == proposal.proposal_id
    assert message["items"] == [
        {
            "title": "Hello",
            "url": "https://example.com/path",
            "domain": "example.com",
            "snippet": "A snippet",
        }
    ]
    assert "query" not in message
    assert "approval_id" not in message
    assert len(adapter.calls) == 1
    assert_valid(message)


def test_calendar_read_success_preserves_bounded_events(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    from datetime import datetime, timezone

    proposal = make_calendar_read_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    start = datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    events = CalendarReadResult(
        (CalendarEventItem("Meet", start, end, "Work", "Room 1"),),
        "Work",
    )

    class CalendarAdapter:
        def __init__(self) -> None:
            self.calls: list[AdapterInput] = []

        def __call__(self, input: AdapterInput) -> CalendarReadResult:
            self.calls.append(input)
            return events

    adapter = CalendarAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.CALENDAR_READ, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.CALENDAR_READ,
        proposal_id=proposal.proposal_id,
        adapter_input=CalendarReadInput("Work"),
    )
    message = coordinator.execute(request)

    assert message["type"] == "productivity_calendar_result"
    assert message["proposal_id"] == proposal.proposal_id
    assert message["events"] == [
        {
            "title": "Meet",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "calendar": "Work",
            "location": "Room 1",
        }
    ]
    assert "approval_id" not in message
    assert "provider" not in message
    assert len(adapter.calls) == 1
    assert_valid(message)


def test_research_failure_returns_failed_update(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_browser_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    class ResearchFailAdapter:
        def __call__(self, input: AdapterInput) -> ResearchShapedResult:
            return ResearchShapedResult(AdapterResultStatus.FAILED, code="failed")

    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.BROWSER_RESEARCH, ResearchFailAdapter()),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id=proposal.proposal_id,
        adapter_input=BrowserResearchInput("hikari"),
    )
    message = coordinator.execute(request)
    assert message == {
        "type": "productivity_update",
        "proposal_id": proposal.proposal_id,
        "status": "failed",
    }
    assert_valid(message)


def test_missing_read_adapter_fails_before_approval_consumption(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_calendar_read_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    authorize = MagicMock(wraps=runtime.authorize_execution)
    runtime.authorize_execution = authorize
    adapters = complete_adapters()
    del adapters[ProductivityAction.CALENDAR_READ]
    coordinator = ProductivityExecutionCoordinator(runtime, adapters)
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.CALENDAR_READ,
        proposal_id=proposal.proposal_id,
        adapter_input=CalendarReadInput("Work"),
    )
    message = coordinator.execute(request)

    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"
    authorize.assert_not_called()
    assert_valid(message)


def test_adapter_failure_returns_failed(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = FailureAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    message = coordinator.execute(request)

    assert message == {
        "type": "productivity_update",
        "proposal_id": proposal.proposal_id,
        "status": "failed",
    }
    assert len(adapter.calls) == 1
    assert_valid(message)


def test_adapter_exception_returns_generic_error(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, ExceptionAdapter()),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    message = coordinator.execute(request)

    assert message["type"] == "productivity_error"
    assert message["code"] == "unavailable"
    assert "private" not in str(message)
    assert "adapter" not in str(message).lower()
    assert_valid(message)


def test_authorization_failure_does_not_invoke_adapter(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    # Do not confirm; approval does not exist.

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    message = coordinator.execute(request)

    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"
    assert adapter.calls == []
    assert_valid(message)


def test_replay_does_not_invoke_adapter(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    first = coordinator.execute(request)
    second = coordinator.execute(request)

    assert first["status"] == "completed"
    assert second["type"] == "productivity_error"
    assert second["code"] == "proposal_invalid"
    assert len(adapter.calls) == 1
    assert_valid(first)
    assert_valid(second)


def test_wrong_action_rejected_before_adapter(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.BROWSER_RESEARCH,
        proposal_id=proposal.proposal_id,
        adapter_input=BrowserResearchInput("hikari"),
    )
    message = coordinator.execute(request)

    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"
    assert adapter.calls == []
    assert_valid(message)


def test_missing_adapter_returns_error_without_runtime_access(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    authorize = MagicMock(wraps=runtime.authorize_execution)
    runtime.authorize_execution = authorize
    coordinator = ProductivityExecutionCoordinator(runtime, {})
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )

    message = coordinator.execute(request)

    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"
    authorize.assert_not_called()
    assert_valid(message)


def test_cross_session_rejected_before_adapter(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
    other_session: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=other_session,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    message = coordinator.execute(request)

    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"
    assert adapter.calls == []
    assert_valid(message)


def test_wrong_proposal_rejected_before_adapter(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id="proposal_other",
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    message = coordinator.execute(request)

    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"
    assert adapter.calls == []
    assert_valid(message)


def test_expired_approval_rejected_before_adapter(
    service: ProductivityService,
    owner: ActorContext,
) -> None:
    clock = MutableClock(1500.0)
    runtime = ProductivityRuntime(service, clock, ApprovalIds("approval_1"))
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    clock.value = 1900.0  # approval expired at 1800.0
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    message = coordinator.execute(request)

    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_expired"
    assert adapter.calls == []
    assert_valid(message)


def test_revoked_approval_rejected_before_adapter(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)
    runtime.cancel(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    message = coordinator.execute(request)

    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"
    assert adapter.calls == []
    assert_valid(message)


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("approval_id", "BAD ID"),
        ("proposal_id", "BAD ID"),
        ("action", "email.draft"),
        ("adapter_input", {"recipient": "x"}),
    ],
)
def test_execution_request_rejects_invalid_fields(owner: ActorContext, field: str, value: object) -> None:
    kwargs = {
        "actor": owner,
        "approval_id": "approval_1",
        "action": ProductivityAction.EMAIL_DRAFT,
        "proposal_id": "proposal_1",
        "adapter_input": EmailDraftInput("user@example.com", "Hello", "Body"),
    }
    kwargs[field] = value  # type: ignore[literal-required]
    with pytest.raises(ValueError):
        ExecutionRequest(**kwargs)


def test_execution_request_has_privacy_safe_repr(owner: ActorContext) -> None:
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id="proposal_1",
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    text = repr(request)
    assert "approval_1" not in text
    assert "owner_1" not in text
    assert "session_1" not in text
    assert "user@example.com" not in text
    assert "Body" not in text


def test_adapter_input_has_privacy_safe_repr() -> None:
    input = EmailDraftInput("user@example.com", "Hello", "Body")
    text = repr(input)
    assert "user@example.com" not in text
    assert "Hello" not in text
    assert "Body" not in text
    assert "EmailDraftInput(...)" in text


def test_adapter_input_default_bounds_remain_conservative() -> None:
    ok = EmailDraftInput("user@example.com", "Hello", "B" * 4000)
    ok.validate()
    with pytest.raises(ValueError):
        EmailDraftInput("user@example.com", "Hello", "B" * 4001).validate()
    with pytest.raises(ValueError):
        EmailDraftInput("user@example.com", "S" * 4000, "B" * 4193).validate()


# ---------------------------------------------------------------------------
# Canonical outbound validation
# ---------------------------------------------------------------------------


def test_all_outbound_messages_pass_validate_server_message(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, SuccessAdapter()),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    message = coordinator.execute(request)
    assert validate_server_message(message) is None


# ---------------------------------------------------------------------------
# Side-effect import scan
# ---------------------------------------------------------------------------


def test_execution_source_has_no_side_effect_imports() -> None:
    source = (
        Path(__file__).resolve().parent.parent / "core" / "productivity" / "execution.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "subprocess",
        "requests",
        "browser_automation",
        "mac_integration",
        "smtplib",
        "logging",
        "sqlite3",
        "mcp",
        "skills",
    )
    for name in forbidden:
        assert f"import {name}" not in source
        assert f"from {name}" not in source


# ---------------------------------------------------------------------------
# Two-stage interface
# ---------------------------------------------------------------------------


def test_authorize_returns_ticket_and_execute_authorized_completes(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )

    ticket = coordinator.authorize(request)
    assert isinstance(ticket, ExecutionTicket)
    assert ticket.action is ProductivityAction.EMAIL_DRAFT
    assert ticket.proposal_id == proposal.proposal_id

    message = coordinator.execute_authorized(ticket)
    assert message == {
        "type": "productivity_update",
        "proposal_id": proposal.proposal_id,
        "status": "completed",
    }
    assert len(adapter.calls) == 1
    assert_valid(message)


def test_authorize_failure_returns_canonical_error(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    # No confirmation; approval does not exist.

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )

    message = coordinator.execute(request)
    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"
    assert adapter.calls == []
    assert_valid(message)


def test_execute_authorized_replay_fails_closed(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )

    ticket = coordinator.authorize(request)
    first = coordinator.execute_authorized(ticket)
    second = coordinator.execute_authorized(ticket)

    assert first["status"] == "completed"
    assert second["type"] == "productivity_error"
    assert second["code"] == "proposal_invalid"
    assert len(adapter.calls) == 1
    assert_valid(first)
    assert_valid(second)


def test_execute_authorized_wrong_coordinator_fails_closed(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator_a = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    coordinator_b = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )

    ticket = coordinator_a.authorize(request)
    message = coordinator_b.execute_authorized(ticket)

    assert message["type"] == "productivity_error"
    assert message["code"] == "proposal_invalid"
    assert adapter.calls == []
    assert_valid(message)


def test_execute_authorized_malformed_ticket_fails_closed(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(),
    )
    for malformed in ("not-a-ticket", 123, None, {"action": "email.draft"}):
        message = coordinator.execute_authorized(malformed)  # type: ignore[arg-type]
        assert message["type"] == "productivity_error"
        assert message["code"] == "unavailable"
        assert_valid(message)


def test_execute_authorized_adapter_exception_returns_canonical_error(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, ExceptionAdapter()),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )

    ticket = coordinator.authorize(request)
    message = coordinator.execute_authorized(ticket)

    assert message["type"] == "productivity_error"
    assert message["code"] == "unavailable"
    assert "private" not in str(message)
    assert "adapter" not in str(message).lower()
    assert_valid(message)


def test_execute_authorized_invalid_adapter_result_returns_canonical_error(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, BadResultAdapter()),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )

    ticket = coordinator.authorize(request)
    message = coordinator.execute_authorized(ticket)

    assert message["type"] == "productivity_error"
    assert message["code"] == "unavailable"
    assert_valid(message)


def test_execution_ticket_safe_repr(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, SuccessAdapter()),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )

    ticket = coordinator.authorize(request)
    text = repr(ticket)
    assert "approval_1" not in text
    assert "owner_1" not in text
    assert "session_1" not in text
    assert "user@example.com" not in text
    assert "Hello" not in text
    assert "Body" not in text
    assert "proposal_1" not in text
    assert "ExecutionTicket(" in text


def test_concurrent_replay_invokes_adapter_exactly_once(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    adapter = SuccessAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )

    ticket = coordinator.authorize(request)
    assert isinstance(ticket, ExecutionTicket)

    worker_count = 16
    barrier = threading.Barrier(worker_count)
    results: list[dict] = []
    results_lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        message = coordinator.execute_authorized(ticket)
        with results_lock:
            results.append(message)

    threads = [threading.Thread(target=worker) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Exactly one adapter invocation despite concurrent execution attempts.
    assert len(adapter.calls) == 1
    assert isinstance(adapter.calls[0], EmailDraftInput)

    completed = [m for m in results if m.get("status") == "completed"]
    errors = [m for m in results if m.get("type") == "productivity_error"]
    assert len(completed) == 1
    assert len(errors) == worker_count - 1
    for message in results:
        assert_valid(message)


def test_ticket_repr_excludes_marker_and_state(
    service: ProductivityService,
    runtime: ProductivityRuntime,
    owner: ActorContext,
) -> None:
    proposal = make_email_proposal(owner)
    runtime.prepare(owner, proposal)
    runtime.confirm(owner, proposal.proposal_id)

    coordinator = ProductivityExecutionCoordinator(
        runtime,
        complete_adapters(ProductivityAction.EMAIL_DRAFT, SuccessAdapter()),
    )
    request = ExecutionRequest(
        actor=owner,
        approval_id="approval_1",
        action=ProductivityAction.EMAIL_DRAFT,
        proposal_id=proposal.proposal_id,
        adapter_input=EmailDraftInput("user@example.com", "Hello", "Body"),
    )
    ticket = coordinator.authorize(request)
    text = repr(ticket)
    # Marker object and consumed state must never appear in repr.
    assert "marker" not in text.lower()
    assert "consumed" not in text.lower()
    assert "lock" not in text.lower()
