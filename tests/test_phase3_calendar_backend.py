"""Phase 3 calendar preparation and WebSocket boundary tests."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import hikari
import pytest

from core.action_policy import Actor, ActorContext
from core.productivity import (
    CalendarDraftProposalFactory,
    CalendarPreparationRegistry,
    CalendarReadProposalFactory,
    ProductivityRuntime,
    ProductivityService,
    SqliteApprovalStore,
)
from core.productivity.bootstrap import create_calendar_preparation
from core.productivity.runtime import ConfirmationResult
from core.productivity.transport import error_message, update_message
from core.productivity.service import ProductivityCode
from core.protocol import validate_client_message, validate_server_message
from core.server import WebSocketServer


REPO_ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc
READ_START = "2026-07-18T09:00:00-04:00"
READ_END = "2026-07-18T10:00:00-04:00"
DRAFT_START = "2026-07-19T14:00:00Z"
DRAFT_END = "2026-07-19T15:30:00Z"


class _WebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    @property
    def remote_address(self):
        return ("127.0.0.1", 12345)

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _QueuedWebSocket(_WebSocket):
    def __init__(self, messages: list[str]) -> None:
        super().__init__()
        self._messages = messages
        self._index = 0

    async def __anext__(self):
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        message = self._messages[self._index]
        self._index += 1
        return message


def _actor(session: str = "session-1") -> ActorContext:
    return ActorContext("local-owner", Actor.OWNER, session, "websocket")


def _runtime(tmp_path, now: float = 1000.0) -> ProductivityRuntime:
    return ProductivityRuntime(
        ProductivityService(SqliteApprovalStore(str(tmp_path / "approvals.db"))),
        lambda: now,
        lambda: "approval-1",
    )


def _calendar_stack(now: float = 1000.0, proposal_id: str = "proposal-1"):
    read_factory = CalendarReadProposalFactory(lambda: now, lambda: proposal_id)
    draft_factory = CalendarDraftProposalFactory(lambda: now, lambda: proposal_id)
    registry = CalendarPreparationRegistry()
    return read_factory, draft_factory, registry


def _pair(server: WebSocketServer, websocket: _WebSocket) -> None:
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "pair", "code": server.pairing_code}),
        )
    )


def _server(tmp_path, proposal_id: str = "proposal-1") -> tuple[WebSocketServer, _WebSocket]:
    read_factory, draft_factory, registry = _calendar_stack(proposal_id=proposal_id)
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        calendar_read_factory=read_factory,
        calendar_draft_factory=draft_factory,
        calendar_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    return server, websocket


def _prepare_calendar_read(
    server: WebSocketServer,
    websocket: _WebSocket,
    *,
    request_id: str = "cal-read-1",
) -> None:
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_calendar_read_prepare",
                    "request_id": request_id,
                    "start": READ_START,
                    "end": READ_END,
                }
            ),
        )
    )


@pytest.mark.parametrize(
    "message_type,payload,registry_attr",
    [
        (
            "productivity_calendar_read_prepare",
            {
                "request_id": "cal-read-1",
                "start": READ_START,
                "end": READ_END,
                "calendar_name": "Work",
            },
            "read",
        ),
        (
            "productivity_calendar_draft_prepare",
            {
                "request_id": "cal-draft-1",
                "title": "Planning",
                "start": DRAFT_START,
                "end": DRAFT_END,
                "calendar_name": "Work",
                "location": "Office",
                "notes": "Bring notes",
            },
            "draft",
        ),
    ],
)
def test_server_prepare_returns_canonical_confirmation_and_retains_private_input(
    tmp_path, message_type, payload, registry_attr
):
    server, websocket = _server(tmp_path)
    registry = server._calendar_registry
    assert registry is not None

    asyncio.run(server._handle_message(websocket, json.dumps({"type": message_type, **payload})))

    message = websocket.sent[-1]
    assert message["type"] == "productivity_confirmation_required"
    assert message["proposal_id"] == "proposal-1"
    assert message["request_id"] == payload["request_id"]
    assert validate_server_message(message) is None
    actor = server._derive_actor_context(websocket).actor_context
    retained = registry.get(actor, "proposal-1")
    assert retained is not None
    if registry_attr == "draft":
        assert any(
            t.get("value") == "Work" for t in message.get("targets", [])
        )
        assert any(
            f.get("label") == "Calendar" and f.get("value") == "Work"
            for f in message.get("payload", [])
        )
        assert retained.calendar_name == "Work"


def test_server_read_prepare_echoes_request_id_on_safe_validation_error(tmp_path):
    server, websocket = _server(tmp_path)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_calendar_read_prepare",
                    "request_id": "cal-read-bad",
                    "start": "2026-02-30T09:00:00-04:00",
                    "end": READ_END,
                }
            ),
        )
    )

    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "cal-read-bad"
    assert "message" not in message
    assert validate_server_message(message) is None


def test_server_rejects_malformed_prepare_messages_before_factory(tmp_path):
    server, websocket = _server(tmp_path)
    bad = {
        "type": "productivity_calendar_draft_prepare",
        "request_id": "cal-draft-bad",
        "title": "x",
        "start": DRAFT_START,
        "end": DRAFT_END,
        "calendar_name": "Work",
        "proposal_id": "client-proposal",
    }
    assert validate_client_message(bad) == "Unknown field: proposal_id"
    asyncio.run(server._handle_message(websocket, json.dumps(bad)))
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "cal-draft-bad"


def test_cross_session_registry_isolation(tmp_path):
    read_factory, draft_factory, registry = _calendar_stack(proposal_id="proposal-1")
    actor_a = _actor("session-a")
    actor_b = _actor("session-b")
    prepared = read_factory.prepare(
        actor_a,
        datetime(2026, 7, 18, 9, 0, tzinfo=UTC),
        datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
    )
    registry.put(actor_a, "proposal-1", prepared.read)
    assert registry.get(actor_b, "proposal-1") is None


def test_server_cancel_removes_retained_calendar_input(tmp_path):
    server, websocket = _server(tmp_path)
    registry = server._calendar_registry
    assert registry is not None

    _prepare_calendar_read(server, websocket, request_id="cal-read-cancel")
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_cancel", "proposal_id": "proposal-1"}),
        )
    )
    assert websocket.sent[-1]["status"] == "cancelled"
    assert registry.get(actor, "proposal-1") is None


def test_confirm_completed_removes_calendar_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._calendar_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_calendar_read(server, websocket)
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    monkeypatch.setattr(
        runtime,
        "confirm_and_ticket",
        lambda *args, **kwargs: ConfirmationResult(
            public_message=update_message("proposal-1", "completed"),
            proposal_id="proposal-1",
        ),
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal-1",
                    "scope": "once",
                }
            ),
        )
    )
    assert websocket.sent[-1]["status"] == "completed"
    assert registry.get(actor, "proposal-1") is None


def test_confirm_cancelled_removes_calendar_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._calendar_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_calendar_read(server, websocket)
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    monkeypatch.setattr(
        runtime,
        "confirm_and_ticket",
        lambda *args, **kwargs: ConfirmationResult(
            public_message=update_message("proposal-1", "cancelled"),
            proposal_id="proposal-1",
        ),
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal-1",
                    "scope": "once",
                }
            ),
        )
    )
    assert websocket.sent[-1]["status"] == "cancelled"
    assert registry.get(actor, "proposal-1") is None


def test_expired_confirm_removes_calendar_entry(tmp_path):
    clock_value = [1000.0]
    runtime = ProductivityRuntime(
        ProductivityService(SqliteApprovalStore(str(tmp_path / "approvals.db"))),
        lambda: clock_value[0],
        lambda: "approval-1",
    )
    read_factory, draft_factory, registry = _calendar_stack(now=1000.0)
    server = WebSocketServer(
        object(),
        productivity_runtime=runtime,
        calendar_read_factory=read_factory,
        calendar_draft_factory=draft_factory,
        calendar_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    _prepare_calendar_read(server, websocket, request_id="cal-read-expired")
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    clock_value[0] = 1900.0
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal-1",
                    "scope": "once",
                }
            ),
        )
    )
    assert websocket.sent[-1]["code"] == "proposal_expired"
    assert registry.get(actor, "proposal-1") is None


def test_expired_status_removes_calendar_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._calendar_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_calendar_read(server, websocket, request_id="cal-read-status-expired")
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    monkeypatch.setattr(
        runtime,
        "status",
        lambda *args, **kwargs: error_message(
            "proposal-1", ProductivityCode.PROPOSAL_EXPIRED
        ),
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_status", "proposal_id": "proposal-1"}),
        )
    )
    assert websocket.sent[-1]["code"] == "proposal_expired"
    assert registry.get(actor, "proposal-1") is None


def test_transient_error_preserves_calendar_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._calendar_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_calendar_read(server, websocket, request_id="cal-read-transient")
    actor = server._derive_actor_context(websocket).actor_context
    retained = registry.get(actor, "proposal-1")
    assert retained is not None

    monkeypatch.setattr(
        runtime,
        "confirm_and_ticket",
        lambda *args, **kwargs: ConfirmationResult(
            public_message=error_message(
                "proposal-1", ProductivityCode.CONSUMPTION_FAILED
            ),
            proposal_id="proposal-1",
        ),
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal-1",
                    "scope": "once",
                }
            ),
        )
    )
    assert websocket.sent[-1]["code"] == "unavailable"
    assert registry.get(actor, "proposal-1") is retained


def test_cross_session_cannot_remove_owner_calendar_entry(tmp_path):
    read_factory, draft_factory, registry = _calendar_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        calendar_read_factory=read_factory,
        calendar_draft_factory=draft_factory,
        calendar_registry=registry,
    )
    websocket_a = _WebSocket()
    websocket_b = _WebSocket()
    _pair(server, websocket_a)
    _pair(server, websocket_b)
    _prepare_calendar_read(server, websocket_a, request_id="cal-read-owner")
    actor_a = server._derive_actor_context(websocket_a).actor_context
    assert registry.get(actor_a, "proposal-1") is not None

    asyncio.run(
        server._handle_message(
            websocket_b,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal-1",
                    "scope": "once",
                }
            ),
        )
    )
    assert websocket_b.sent[-1]["type"] == "productivity_error"
    assert registry.get(actor_a, "proposal-1") is not None


def test_disconnect_clears_session_registry_entries_via_connection_finally(tmp_path):
    read_factory, draft_factory, registry = _calendar_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        calendar_read_factory=read_factory,
        calendar_draft_factory=draft_factory,
        calendar_registry=registry,
    )
    websocket = _QueuedWebSocket(
        [
            json.dumps({"type": "pair", "code": server.pairing_code}),
            json.dumps(
                {
                    "type": "productivity_calendar_read_prepare",
                    "request_id": "cal-read-disconnect",
                    "start": READ_START,
                    "end": READ_END,
                }
            ),
        ]
    )

    asyncio.run(server._handle_connection(websocket))

    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is None


def test_prepare_failure_removes_registry_entry(tmp_path):
    read_factory, draft_factory, registry = _calendar_stack()
    runtime = _runtime(tmp_path)

    def fail_prepare(actor, proposal):
        return {
            "type": "productivity_error",
            "proposal_id": proposal.proposal_id,
            "code": "unavailable",
        }

    runtime.prepare = fail_prepare  # type: ignore[method-assign]
    server = WebSocketServer(
        object(),
        productivity_runtime=runtime,
        calendar_read_factory=read_factory,
        calendar_draft_factory=draft_factory,
        calendar_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_calendar_read_prepare",
                    "request_id": "cal-read-fail",
                    "start": READ_START,
                    "end": READ_END,
                }
            ),
        )
    )
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is None
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "cal-read-fail"


def test_factory_exception_maps_to_safe_error_without_details(tmp_path, monkeypatch):
    read_factory, draft_factory, registry = _calendar_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        calendar_read_factory=read_factory,
        calendar_draft_factory=draft_factory,
        calendar_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)

    def explode(*args, **kwargs):
        raise RuntimeError("secret provider path private/calendar")

    monkeypatch.setattr(read_factory, "prepare", explode)
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_calendar_read_prepare",
                    "request_id": "cal-read-secret",
                    "start": READ_START,
                    "end": READ_END,
                }
            ),
        )
    )
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "cal-read-secret"
    assert set(message.keys()) <= {"type", "proposal_id", "code", "request_id"}
    assert "message" not in message
    assert "provider" not in json.dumps(message)
    assert "private/calendar" not in json.dumps(message)


def test_bootstrap_is_lazy_and_side_effect_free(tmp_path, monkeypatch):
    state_home = tmp_path / "private-home"
    env = {**os.environ, "HIKARI_HOME": str(state_home)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import hikari; import core.productivity.bootstrap",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert not (state_home / "policy").exists()

    read_factory, draft_factory, registry = create_calendar_preparation(
        proposal_id_factory=lambda: "proposal-bootstrap",
    )
    assert read_factory is not None
    assert draft_factory is not None
    assert registry is not None


def test_server_path_wires_calendar_bootstrap_separately(monkeypatch):
    orchestrator = object()
    server = MagicMock()
    server_class = MagicMock(return_value=server)
    calendar_read = object()
    calendar_draft = object()
    calendar_registry = object()
    monkeypatch.setitem(
        sys.modules,
        "core.orchestrator",
        __import__("types").SimpleNamespace(get_orchestrator=lambda: orchestrator),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.productivity.bootstrap",
        __import__("types").SimpleNamespace(
            create_productivity_runtime=MagicMock(return_value=object()),
            create_email_draft_preparation=MagicMock(return_value=(object(), object())),
            create_calendar_preparation=MagicMock(
                return_value=(calendar_read, calendar_draft, calendar_registry)
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.jobs.bootstrap",
        __import__("types").SimpleNamespace(create_scheduled_job_runtime=MagicMock(return_value=object())),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.server",
        __import__("types").SimpleNamespace(WebSocketServer=server_class),
    )

    hikari.run_server("127.0.0.1", 9876)

    kwargs = server_class.call_args.kwargs
    assert kwargs["calendar_read_factory"] is calendar_read
    assert kwargs["calendar_draft_factory"] is calendar_draft
    assert kwargs["calendar_registry"] is calendar_registry
