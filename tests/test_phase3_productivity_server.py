"""Bounded Phase 3 productivity WebSocket bridge tests."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from core.action_policy import Actor, ActorContext
from core.protocol import validate_client_message, validate_server_message
from core.server import WebSocketServer
from core.productivity import (
    ActionProposal,
    ActionTarget,
    ApprovalScope,
    ConfirmationResult,
    PreviewField,
    ProductivityAction,
    ProductivityRuntime,
    ProductivityService,
    SqliteApprovalStore,
    TargetKind,
)


class MockWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


class LoopbackWebSocket(MockWebSocket):
    @property
    def remote_address(self):
        return ("127.0.0.1", 12345)


def _pair(server: WebSocketServer, websocket: MockWebSocket) -> None:
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "pair", "code": server.pairing_code}),
        )
    )


def _make_runtime(tmp_path, now: float = 1500.0):
    store = SqliteApprovalStore(str(tmp_path / "approvals.db"))
    service = ProductivityService(store)
    return ProductivityRuntime(service, lambda: now, lambda: "approval_1")


def _make_proposal(actor: ActorContext, proposal_id: str = "proposal_1") -> ActionProposal:
    return ActionProposal(
        proposal_id=proposal_id,
        action=ProductivityAction.EMAIL_DRAFT,
        actor=actor,
        targets=(ActionTarget(TargetKind.EMAIL_RECIPIENT, "user@example.com"),),
        preview_fields=(PreviewField("subject", "Subject", "Hello"),),
        created_at=1000.0,
        expires_at=2000.0,
    )


def test_paired_confirm_cancel_status_use_runtime():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    runtime.confirm_and_ticket.return_value = ConfirmationResult(
        public_message={
            "type": "productivity_update",
            "proposal_id": "proposal_1",
            "status": "approved",
        },
        approval_id="approval_1",
        proposal_id="proposal_1",
        scope=ApprovalScope.ONCE,
    )
    runtime.cancel.return_value = {
        "type": "productivity_update",
        "proposal_id": "proposal_1",
        "status": "cancelled",
    }
    runtime.status.return_value = {
        "type": "productivity_update",
        "proposal_id": "proposal_1",
        "status": "preview",
    }

    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    assert server._productivity_execution_coordinator is None
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    messages = [
        {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"},
        {"type": "productivity_cancel", "proposal_id": "proposal_1"},
        {"type": "productivity_status", "proposal_id": "proposal_1"},
    ]
    for payload in messages:
        asyncio.run(server._handle_message(websocket, json.dumps(payload)))
        assert validate_server_message(websocket.sent[-1]) is None

    assert runtime.confirm_and_ticket.call_count == 1
    assert runtime.cancel.call_count == 1
    assert runtime.status.call_count == 1
    assert websocket.sent[1]["status"] == "approved"
    assert "approval_id" not in websocket.sent[1]


def test_unpaired_productivity_messages_are_rejected_before_runtime_access():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    websocket = MockWebSocket()

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    assert websocket.sent == [
        {"type": "pairing_required", "message": "Pair this connection before sending requests"}
    ]
    runtime.confirm.assert_not_called()
    runtime.confirm_and_ticket.assert_not_called()
    runtime.cancel.assert_not_called()
    runtime.status.assert_not_called()


def test_actor_is_derived_from_transport_not_client():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    runtime.confirm_and_ticket.return_value = ConfirmationResult(
        public_message={
            "type": "productivity_update",
            "proposal_id": "proposal_1",
            "status": "approved",
        },
        approval_id="approval_1",
        proposal_id="proposal_1",
        scope=ApprovalScope.ONCE,
    )
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    runtime.confirm_and_ticket.assert_called_once()
    actor = runtime.confirm_and_ticket.call_args[0][0]
    assert actor.actor == Actor.OWNER
    assert actor.actor_id == "local-owner"


def test_client_actor_approval_action_fields_are_rejected_by_protocol():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

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
    runtime.confirm.assert_not_called()
    runtime.confirm_and_ticket.assert_not_called()


def test_missing_runtime_returns_canonical_unavailable_error():
    orchestrator = MagicMock()
    server = WebSocketServer(orchestrator, productivity_runtime=None)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    assert websocket.sent[-1] == {
        "type": "productivity_error",
        "proposal_id": "proposal_1",
        "code": "unavailable",
    }
    assert validate_server_message(websocket.sent[-1]) is None


def test_internal_proposal_publication_targets_only_intended_paired_socket():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    runtime.prepare.return_value = {
        "type": "productivity_confirmation_required",
        "proposal_id": "proposal_1",
        "action": "email.draft",
        "heading": "Draft email",
        "risk_label": "medium",
        "targets": [{"label": "Email recipient", "value": "user@example.com"}],
        "payload": [{"label": "Subject", "value": "Hello", "truncated": False}],
        "expires_at": 2000.0,
        "allowed_scopes": ["once"],
    }
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)

    paired_ws = LoopbackWebSocket()
    unpaired_ws = LoopbackWebSocket()
    _pair(server, paired_ws)

    actor = ActorContext(
        actor_id="local-owner", actor=Actor.OWNER, session_id="session_1"
    )
    proposal = _make_proposal(actor)

    asyncio.run(server.publish_productivity_proposal(paired_ws, proposal))
    asyncio.run(server.publish_productivity_proposal(unpaired_ws, proposal))

    # Account for the pairing acknowledgement sent before publication.
    assert len(paired_ws.sent) == 2
    assert paired_ws.sent[0]["type"] == "paired"
    assert paired_ws.sent[1]["type"] == "productivity_confirmation_required"
    assert paired_ws.sent[1]["proposal_id"] == "proposal_1"
    assert validate_server_message(paired_ws.sent[1]) is None
    assert unpaired_ws.sent == []


def test_publish_productivity_proposal_is_no_op_without_runtime():
    orchestrator = MagicMock()
    server = WebSocketServer(orchestrator, productivity_runtime=None)
    paired_ws = LoopbackWebSocket()
    _pair(server, paired_ws)

    actor = ActorContext(
        actor_id="local-owner", actor=Actor.OWNER, session_id="session_1"
    )
    proposal = _make_proposal(actor)

    asyncio.run(server.publish_productivity_proposal(paired_ws, proposal))
    # Only the pairing acknowledgement was sent; no productivity message.
    assert len(paired_ws.sent) == 1
    assert paired_ws.sent[0]["type"] == "paired"


def test_malformed_proposal_id_is_rejected_by_protocol():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "BAD:ID", "scope": "once"}
            ),
        )
    )

    assert websocket.sent[-1]["type"] == "error"
    runtime.confirm.assert_not_called()
    runtime.confirm_and_ticket.assert_not_called()


def test_stale_proposal_id_is_handled_by_runtime():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    runtime.confirm_and_ticket.return_value = ConfirmationResult(
        public_message={
            "type": "productivity_error",
            "proposal_id": "stale_1",
            "code": "proposal_invalid",
        },
        proposal_id="stale_1",
        scope=ApprovalScope.ONCE,
    )
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "stale_1", "scope": "once"}
            ),
        )
    )

    assert websocket.sent[-1] == {
        "type": "productivity_error",
        "proposal_id": "stale_1",
        "code": "proposal_invalid",
    }


def test_runtime_exception_becomes_safe_unavailable_error():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    runtime.confirm_and_ticket.side_effect = RuntimeError("private provider detail")
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1", "scope": "once"}
            ),
        )
    )

    message = websocket.sent[-1]
    assert message == {
        "type": "productivity_error",
        "proposal_id": "proposal_1",
        "code": "unavailable",
    }
    assert "private" not in str(message)
    assert validate_server_message(message) is None


def test_invalid_runtime_output_is_replaced_before_send():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    runtime.status.return_value = {
        "type": "productivity_error",
        "proposal_id": "proposal_1",
        "code": "unavailable",
        "message": "private provider detail",
    }
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_status", "proposal_id": "proposal_1"}),
        )
    )

    assert websocket.sent[-1] == {
        "type": "productivity_error",
        "proposal_id": "invalid-proposal",
        "code": "unavailable",
    }


def test_publication_with_malformed_proposal_is_safe():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    runtime.prepare.side_effect = RuntimeError("private provider detail")
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)
    malformed = MagicMock()
    malformed.proposal_id = "BAD:ID"

    asyncio.run(server.publish_productivity_proposal(websocket, malformed))

    assert websocket.sent[-1] == {
        "type": "productivity_error",
        "proposal_id": "invalid-proposal",
        "code": "unavailable",
    }


def test_authorize_execution_is_not_exposed_as_client_message():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "authorize_execution",
                    "approval_id": "approval_1",
                    "action": "email.draft",
                    "proposal_id": "proposal_1",
                }
            ),
        )
    )

    assert websocket.sent[-1] == {"type": "error", "message": "Unknown message type"}
    runtime.authorize_execution.assert_not_called()


def test_existing_document_and_voice_branches_remain_present():
    orchestrator = MagicMock()
    orchestrator.process_input.return_value = "safe reply"
    server = WebSocketServer(orchestrator)
    websocket = LoopbackWebSocket()
    server._paired_client_ids.add(str(id(websocket)))

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "message", "text": "hello"}),
        )
    )

    assert websocket.sent == [{"type": "response", "text": "safe reply"}]

    voice_ws = LoopbackWebSocket()
    server._paired_client_ids.add(str(id(voice_ws)))
    asyncio.run(
        server._handle_message(
            voice_ws,
            json.dumps({"type": "voice", "text": "hello"}),
        )
    )

    assert voice_ws.sent == [{"type": "response", "text": "safe reply"}]


def test_productivity_messages_require_valid_client_message():
    orchestrator = MagicMock()
    runtime = MagicMock(spec=ProductivityRuntime)
    server = WebSocketServer(orchestrator, productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_1"}
            ),
        )
    )

    assert websocket.sent[-1]["type"] == "error"
    runtime.confirm.assert_not_called()
    runtime.confirm_and_ticket.assert_not_called()


def test_productivity_status_with_missing_runtime_returns_unavailable():
    orchestrator = MagicMock()
    server = WebSocketServer(orchestrator, productivity_runtime=None)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_status", "proposal_id": "proposal_1"}),
        )
    )

    assert websocket.sent[-1] == {
        "type": "productivity_error",
        "proposal_id": "proposal_1",
        "code": "unavailable",
    }
