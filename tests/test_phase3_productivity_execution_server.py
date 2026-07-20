"""Server-boundary tests for the Phase 3 productivity execution lifecycle.

Uses fake coordinators and adapters only. No Mail, Calendar, Reminders, browser,
network, subprocess, provider, or filesystem side effects beyond the temporary
approval store used by the real runtime.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.action_policy import Actor, ActorContext
from core.protocol import validate_server_message
from core.server import WebSocketServer
from core.productivity import (
    ActionProposal,
    ActionTarget,
    CalendarPreparationRegistry,
    EmailDraftPreparationRegistry,
    PreviewField,
    PreparedEmailDraft,
    ProductivityAction,
    ProductivityRuntime,
    ProductivityService,
    ResearchPreparationRegistry,
    SqliteApprovalStore,
    TargetKind,
)
from core.productivity.action_results import (
    BrowserSearchResult,
    BrowserSearchResultItem,
    CalendarEventItem,
    CalendarReadResult,
)
from core.productivity.calendar import PreparedCalendarRead
from core.productivity.execution import (
    AdapterInput,
    AdapterResult,
    AdapterResultStatus,
    ProductivityExecutionCoordinator,
)
from core.productivity.research import PreparedResearchInput


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


class LoopbackWebSocket(MockWebSocket):
    @property
    def remote_address(self):
        return ("127.0.0.1", 12345)


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class ApprovalIds:
    def __init__(self, *values: object) -> None:
        self._values = list(values)

    def __call__(self) -> object:
        if not self._values:
            raise RuntimeError("no id")
        return self._values.pop(0)


class RecordingAdapter:
    def __init__(self, *, succeed: bool = True) -> None:
        self.calls: list[AdapterInput] = []
        self._succeed = succeed

    def __call__(self, input: AdapterInput) -> AdapterResult:
        self.calls.append(input)
        if self._succeed:
            return AdapterResult(AdapterResultStatus.SUCCESS)
        return AdapterResult(AdapterResultStatus.FAILED, code="failed")


class RaisingAdapter:
    def __call__(self, input: AdapterInput) -> AdapterResult:
        raise RuntimeError("private adapter detail")


def _pair(server: WebSocketServer, websocket: MockWebSocket) -> None:
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "pair", "code": server.pairing_code}),
        )
    )


def _paired_owner(server: WebSocketServer, websocket: MockWebSocket) -> ActorContext:
    return server._derive_actor_context(websocket).actor_context


def _make_runtime(tmp_path: Path, now: float = 1500.0) -> ProductivityRuntime:
    store = SqliteApprovalStore(str(tmp_path / "approvals.db"))
    return ProductivityRuntime(ProductivityService(store), MutableClock(now), ApprovalIds("approval_1", "approval_2"))


def _complete_adapters(
    action: ProductivityAction,
    adapter: object,
) -> dict[ProductivityAction, object]:
    mapping: dict[ProductivityAction, object] = {
        candidate: RecordingAdapter(succeed=False) for candidate in ProductivityAction
    }
    mapping[action] = adapter
    return mapping


def _email_proposal(actor: ActorContext, proposal_id: str = "proposal_1") -> ActionProposal:
    return ActionProposal(
        proposal_id=proposal_id,
        action=ProductivityAction.EMAIL_DRAFT,
        actor=actor,
        targets=(ActionTarget(TargetKind.EMAIL_RECIPIENT, "user@example.com"),),
        preview_fields=(PreviewField("subject", "Subject", "Hello"),),
        created_at=1000.0,
        expires_at=2000.0,
    )


def _wire_email_stack(
    tmp_path: Path,
    *,
    adapter: object | None = None,
    with_prepared: bool = True,
) -> tuple[WebSocketServer, LoopbackWebSocket, RecordingAdapter | RaisingAdapter, EmailDraftPreparationRegistry]:
    runtime = _make_runtime(tmp_path)
    registry = EmailDraftPreparationRegistry()
    email_adapter = adapter if adapter is not None else RecordingAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        _complete_adapters(ProductivityAction.EMAIL_DRAFT, email_adapter),  # type: ignore[arg-type]
    )
    server = WebSocketServer(
        MagicMock(),
        productivity_runtime=runtime,
        email_draft_registry=registry,
        productivity_execution_coordinator=coordinator,
    )
    websocket = LoopbackWebSocket()
    _pair(server, websocket)
    actor = _paired_owner(server, websocket)
    proposal = _email_proposal(actor)
    asyncio.run(server.publish_productivity_proposal(websocket, proposal))
    if with_prepared:
        registry.put(actor, proposal.proposal_id, PreparedEmailDraft("user@example.com", "Hello", "Body"))
    return server, websocket, email_adapter, registry  # type: ignore[return-value]


def test_confirm_executes_through_coordinator_and_clears_prepared(tmp_path: Path) -> None:
    server, websocket, adapter, registry = _wire_email_stack(tmp_path)
    actor = _paired_owner(server, websocket)
    before = len(websocket.sent)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    messages = websocket.sent[before:]
    assert [m.get("status") for m in messages if m.get("type") == "productivity_update"] == [
        "approved",
        "executing",
        "completed",
    ]
    for message in messages:
        assert validate_server_message(message) is None
        assert "approval_id" not in message
        assert "private" not in str(message)
    assert len(adapter.calls) == 1
    assert registry.get(actor, "proposal_1") is None


def test_missing_coordinator_approves_without_consuming(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    authorize = MagicMock(wraps=runtime.authorize_execution)
    runtime.authorize_execution = authorize
    registry = EmailDraftPreparationRegistry()
    server = WebSocketServer(
        MagicMock(),
        productivity_runtime=runtime,
        email_draft_registry=registry,
    )
    websocket = LoopbackWebSocket()
    _pair(server, websocket)
    actor = _paired_owner(server, websocket)
    asyncio.run(server.publish_productivity_proposal(websocket, _email_proposal(actor)))
    registry.put(actor, "proposal_1", PreparedEmailDraft("user@example.com", "Hello", "Body"))

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    assert websocket.sent[-1] == {
        "type": "productivity_update",
        "proposal_id": "proposal_1",
        "status": "approved",
    }
    authorize.assert_not_called()
    assert registry.get(actor, "proposal_1") is not None


def test_dispatch_failure_revokes_without_adapter_call(tmp_path: Path) -> None:
    server, websocket, adapter, registry = _wire_email_stack(tmp_path, with_prepared=False)
    actor = _paired_owner(server, websocket)
    before = len(websocket.sent)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    messages = websocket.sent[before:]
    assert messages[0]["status"] == "approved"
    assert messages[-1]["type"] == "productivity_error"
    assert "approval_id" not in messages[-1]
    assert "/Users" not in str(messages[-1])
    assert adapter.calls == []
    assert registry.get(actor, "proposal_1") is None
    approvals = server._productivity_runtime._service._store.list_for_actor(  # type: ignore[union-attr]
        "local-owner", limit=64
    )
    assert len(approvals) == 1
    assert approvals[0].revoked is True


def test_adapter_failure_returns_failed_without_leaking(tmp_path: Path) -> None:
    adapter = RecordingAdapter(succeed=False)
    server, websocket, _, registry = _wire_email_stack(tmp_path, adapter=adapter)
    actor = _paired_owner(server, websocket)
    before = len(websocket.sent)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    statuses = [
        m.get("status")
        for m in websocket.sent[before:]
        if m.get("type") == "productivity_update"
    ]
    assert statuses == ["approved", "executing", "failed"]
    assert len(adapter.calls) == 1
    assert registry.get(actor, "proposal_1") is None


def test_adapter_exception_returns_safe_error(tmp_path: Path) -> None:
    server, websocket, _, _ = _wire_email_stack(tmp_path, adapter=RaisingAdapter())
    before = len(websocket.sent)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    terminal = websocket.sent[-1]
    assert terminal["type"] == "productivity_error"
    assert "private" not in str(terminal)
    assert "adapter" not in str(terminal).lower()
    assert "secret" not in str(terminal)
    assert validate_server_message(terminal) is None
    assert all("approval_id" not in message for message in websocket.sent[before:])


def test_duplicate_confirm_does_not_reexecute(tmp_path: Path) -> None:
    server, websocket, adapter, _ = _wire_email_stack(tmp_path)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal_1",
                    "scope": "once",
                }
            ),
        )
    )
    after_first = len(websocket.sent)
    assert len(adapter.calls) == 1
    assert websocket.sent[-1]["status"] == "completed"

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal_1",
                    "scope": "once",
                }
            ),
        )
    )

    assert len(adapter.calls) == 1
    assert all(m.get("status") != "executing" for m in websocket.sent[after_first:])
    assert validate_server_message(websocket.sent[-1]) is None
    assert "approval_id" not in websocket.sent[-1]


def test_cross_session_confirm_does_not_execute(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    adapter = RecordingAdapter()
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        _complete_adapters(ProductivityAction.EMAIL_DRAFT, adapter),  # type: ignore[arg-type]
    )
    registry = EmailDraftPreparationRegistry()
    server = WebSocketServer(
        MagicMock(),
        productivity_runtime=runtime,
        email_draft_registry=registry,
        productivity_execution_coordinator=coordinator,
    )
    session_a = LoopbackWebSocket()
    session_b = LoopbackWebSocket()
    _pair(server, session_a)
    _pair(server, session_b)
    actor_a = _paired_owner(server, session_a)
    asyncio.run(server.publish_productivity_proposal(session_a, _email_proposal(actor_a)))
    registry.put(actor_a, "proposal_1", PreparedEmailDraft("user@example.com", "Hello", "Body"))

    asyncio.run(
        server._handle_message(
            session_b,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    assert adapter.calls == []
    assert registry.get(actor_a, "proposal_1") is not None
    assert all(m.get("status") != "executing" for m in session_b.sent)
    assert all(m.get("status") != "completed" for m in session_b.sent)


def test_status_and_cancel_never_execute(tmp_path: Path) -> None:
    server, websocket, adapter, registry = _wire_email_stack(tmp_path)
    actor = _paired_owner(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_status", "proposal_id": "proposal_1"}),
        )
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_cancel", "proposal_id": "proposal_1"}),
        )
    )

    assert adapter.calls == []
    assert websocket.sent[-1]["status"] == "cancelled"
    assert registry.get(actor, "proposal_1") is None


def test_malformed_confirm_never_executes(tmp_path: Path) -> None:
    server, websocket, adapter, _ = _wire_email_stack(tmp_path)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal_1",
                    "scope": "once",
                    "actor_id": "attacker",
                }
            ),
        )
    )

    assert websocket.sent[-1]["type"] == "error"
    assert adapter.calls == []


@dataclass(frozen=True)
class _ResearchShapedResult(AdapterResult):
    result: BrowserSearchResult | None = None


class ResearchRecordingAdapter:
    def __init__(self, result: BrowserSearchResult) -> None:
        self.calls: list[AdapterInput] = []
        self._result = result

    def __call__(self, input: AdapterInput) -> _ResearchShapedResult:
        self.calls.append(input)
        return _ResearchShapedResult(AdapterResultStatus.SUCCESS, result=self._result)


class CalendarRecordingAdapter:
    def __init__(self, result: CalendarReadResult) -> None:
        self.calls: list[AdapterInput] = []
        self._result = result

    def __call__(self, input: AdapterInput) -> CalendarReadResult:
        self.calls.append(input)
        return self._result


def _research_proposal(actor: ActorContext, proposal_id: str = "proposal_1") -> ActionProposal:
    return ActionProposal(
        proposal_id=proposal_id,
        action=ProductivityAction.BROWSER_RESEARCH,
        actor=actor,
        targets=(ActionTarget(TargetKind.WEB_DOMAIN, "example.com"),),
        preview_fields=(PreviewField("query", "Query", "hikari docs"),),
        created_at=1000.0,
        expires_at=2000.0,
    )


def _calendar_read_proposal(
    actor: ActorContext, proposal_id: str = "proposal_1"
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


def test_research_result_is_terminal_and_clears_prepared(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    registry = ResearchPreparationRegistry()
    search = BrowserSearchResult(
        "query text here",
        (
            BrowserSearchResultItem(
                "Hello",
                "https://example.com/docs",
                "example.com",
                "Snippet text",
            ),
        ),
    )
    adapter = ResearchRecordingAdapter(search)
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        _complete_adapters(ProductivityAction.BROWSER_RESEARCH, adapter),  # type: ignore[arg-type]
    )
    server = WebSocketServer(
        MagicMock(),
        productivity_runtime=runtime,
        research_registry=registry,
        productivity_execution_coordinator=coordinator,
    )
    websocket = LoopbackWebSocket()
    _pair(server, websocket)
    actor = _paired_owner(server, websocket)
    asyncio.run(server.publish_productivity_proposal(websocket, _research_proposal(actor)))
    registry.put(
        actor,
        "proposal_1",
        PreparedResearchInput("query text here", ("example.com",), 5),
    )
    before = len(websocket.sent)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    messages = websocket.sent[before:]
    updates = [m for m in messages if m.get("type") == "productivity_update"]
    assert [m.get("status") for m in updates] == ["approved", "executing"]
    terminal = messages[-1]
    assert terminal["type"] == "productivity_research_result"
    assert terminal["items"][0]["title"] == "Hello"
    assert "query" not in terminal
    assert validate_server_message(terminal) is None
    assert len(adapter.calls) == 1
    assert registry.get(actor, "proposal_1") is None


def test_calendar_result_is_terminal_and_clears_prepared(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    registry = CalendarPreparationRegistry()
    start = datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    result = CalendarReadResult(
        (CalendarEventItem("Meet", start, end, "Work", "Room 1"),),
        "Work",
    )
    adapter = CalendarRecordingAdapter(result)
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        _complete_adapters(ProductivityAction.CALENDAR_READ, adapter),  # type: ignore[arg-type]
    )
    server = WebSocketServer(
        MagicMock(),
        productivity_runtime=runtime,
        calendar_registry=registry,
        productivity_execution_coordinator=coordinator,
    )
    websocket = LoopbackWebSocket()
    _pair(server, websocket)
    actor = _paired_owner(server, websocket)
    asyncio.run(
        server.publish_productivity_proposal(websocket, _calendar_read_proposal(actor))
    )
    registry.put(
        actor,
        "proposal_1",
        PreparedCalendarRead(start, end, "Work"),
    )
    before = len(websocket.sent)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    messages = websocket.sent[before:]
    updates = [m for m in messages if m.get("type") == "productivity_update"]
    assert [m.get("status") for m in updates] == ["approved", "executing"]
    terminal = messages[-1]
    assert terminal["type"] == "productivity_calendar_result"
    assert terminal["events"][0]["calendar"] == "Work"
    assert validate_server_message(terminal) is None
    assert len(adapter.calls) == 1
    assert registry.get(actor, "proposal_1") is None


def test_missing_read_adapter_fails_before_consumption(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    authorize = MagicMock(wraps=runtime.authorize_execution)
    runtime.authorize_execution = authorize
    registry = CalendarPreparationRegistry()
    adapters = _complete_adapters(ProductivityAction.EMAIL_DRAFT, RecordingAdapter())
    del adapters[ProductivityAction.CALENDAR_READ]
    coordinator = ProductivityExecutionCoordinator(
        runtime,
        adapters,  # type: ignore[arg-type]
    )
    server = WebSocketServer(
        MagicMock(),
        productivity_runtime=runtime,
        calendar_registry=registry,
        productivity_execution_coordinator=coordinator,
    )
    websocket = LoopbackWebSocket()
    _pair(server, websocket)
    actor = _paired_owner(server, websocket)
    asyncio.run(
        server.publish_productivity_proposal(websocket, _calendar_read_proposal(actor))
    )
    start = datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    registry.put(actor, "proposal_1", PreparedCalendarRead(start, end, "Work"))

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    assert websocket.sent[-1]["type"] == "productivity_error"
    authorize.assert_not_called()
    assert registry.get(actor, "proposal_1") is not None
