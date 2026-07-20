"""Real-runtime Phase 3 productivity end-to-end tests.

These tests drive the full productivity lifecycle through a real
``WebSocketServer`` wired to a real ``ProductivityRuntime`` built from a real
``SqliteApprovalStore`` and ``ProductivityService``. No ``ProductivityRuntime``
is mocked. A temporary SQLite database is used for every test.

The tests verify the server boundary, not just the runtime: paired publication,
confirmation, status correlation, cancellation/revocation, expiry, replay
protection, cross-session non-disclosure, invalid-ID rejection, protocol
validation of outbound messages, privacy of outbound payloads, and that without
an injected execution coordinator the authorize/execute path is never invoked.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.action_policy import Actor, ActorContext
from core.protocol import validate_client_message, validate_server_message
from core.server import WebSocketServer
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
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        if not self._values:
            raise RuntimeError("no id")
        value = self._values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _pair(server: WebSocketServer, websocket: MockWebSocket) -> None:
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "pair", "code": server.pairing_code}),
        )
    )


def _make_runtime(tmp_path: Path, clock: MutableClock, approval_ids: ApprovalIds) -> ProductivityRuntime:
    store = SqliteApprovalStore(str(tmp_path / "approvals.db"))
    service = ProductivityService(store)
    return ProductivityRuntime(service, clock, approval_ids)


def _make_proposal(actor: ActorContext, proposal_id: str, now: float) -> ActionProposal:
    return ActionProposal(
        proposal_id=proposal_id,
        action=ProductivityAction.EMAIL_DRAFT,
        actor=actor,
        targets=(ActionTarget(TargetKind.EMAIL_RECIPIENT, "user@example.com"),),
        preview_fields=(PreviewField("subject", "Subject", "Hello"),),
        created_at=now - 1.0,
        expires_at=now + 1000.0,
    )


def _paired_owner_context(
    server: WebSocketServer, websocket: MockWebSocket
) -> ActorContext:
    """Return the stable actor context derived from the real paired socket."""
    return server._derive_actor_context(websocket).actor_context


def _last_productivity(server_sent: list[dict]) -> dict:
    for message in reversed(server_sent):
        if message.get("type") in (
            "productivity_confirmation_required",
            "productivity_update",
            "productivity_error",
        ):
            return message
    raise AssertionError("no productivity message sent")


# --- Paired publication -> productivity_confirmation_required -----------------

def test_paired_publication_emits_confirmation_required(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    actor = _paired_owner_context(server, websocket)
    proposal = _make_proposal(actor, "proposal_pub", clock.value)

    asyncio.run(server.publish_productivity_proposal(websocket, proposal))

    # paired ack + confirmation_required
    assert websocket.sent[0]["type"] == "paired"
    message = websocket.sent[1]
    assert message["type"] == "productivity_confirmation_required"
    assert message["proposal_id"] == "proposal_pub"
    assert message["action"] == "email.draft"
    assert validate_server_message(message) is None


# --- Confirm -> approved ------------------------------------------------------

def test_confirm_flow_reaches_approved(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    actor = _paired_owner_context(server, websocket)
    proposal = _make_proposal(actor, "proposal_confirm", clock.value)
    asyncio.run(server.publish_productivity_proposal(websocket, proposal))

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_confirm", "scope": "once"}
            ),
        )
    )
    message = _last_productivity(websocket.sent)
    assert message["type"] == "productivity_update"
    assert message["status"] == "approved"
    assert validate_server_message(message) is None


# --- Status correlation -------------------------------------------------------

def test_status_correlates_with_confirmed_state(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    actor = _paired_owner_context(server, websocket)
    proposal = _make_proposal(actor, "proposal_status", clock.value)
    asyncio.run(server.publish_productivity_proposal(websocket, proposal))

    # before confirm
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_status", "proposal_id": "proposal_status"}),
        )
    )
    assert _last_productivity(websocket.sent)["status"] == "preview"

    # after confirm
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_status", "scope": "once"}
            ),
        )
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_status", "proposal_id": "proposal_status"}),
        )
    )
    assert _last_productivity(websocket.sent)["status"] == "approved"


# --- Cancellation and revocation ---------------------------------------------

def test_cancel_revokes_approval(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    actor = _paired_owner_context(server, websocket)
    proposal = _make_proposal(actor, "proposal_cancel", clock.value)
    asyncio.run(server.publish_productivity_proposal(websocket, proposal))
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_cancel", "scope": "once"}
            ),
        )
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_cancel", "proposal_id": "proposal_cancel"}),
        )
    )
    cancel_msg = _last_productivity(websocket.sent)
    assert cancel_msg["status"] == "cancelled"

    # status after cancel reports revoked
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_status", "proposal_id": "proposal_cancel"}),
        )
    )
    assert _last_productivity(websocket.sent)["status"] == "cancelled"


# --- Expiry -------------------------------------------------------------------

def test_expired_proposal_is_rejected(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    actor = _paired_owner_context(server, websocket)
    # proposal already expired relative to clock
    proposal = ActionProposal(
        proposal_id="proposal_expired",
        action=ProductivityAction.EMAIL_DRAFT,
        actor=actor,
        targets=(ActionTarget(TargetKind.EMAIL_RECIPIENT, "user@example.com"),),
        preview_fields=(PreviewField("subject", "Subject", "Hello"),),
        created_at=100.0,
        expires_at=200.0,
    )
    asyncio.run(server.publish_productivity_proposal(websocket, proposal))
    # publication itself fails closed (prepare returns error)
    assert _last_productivity(websocket.sent)["type"] == "productivity_error"

    # confirm on an expired proposal is denied by the runtime
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_expired", "scope": "once"}
            ),
        )
    )
    assert _last_productivity(websocket.sent)["type"] == "productivity_error"


# --- Replay / double confirm --------------------------------------------------

def test_double_confirm_is_idempotent(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1", "approval_2"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    actor = _paired_owner_context(server, websocket)
    proposal = _make_proposal(actor, "proposal_replay", clock.value)
    asyncio.run(server.publish_productivity_proposal(websocket, proposal))

    confirm_payload = {
        "type": "productivity_confirm",
        "proposal_id": "proposal_replay",
        "scope": "once",
    }
    asyncio.run(server._handle_message(websocket, json.dumps(confirm_payload)))
    assert _last_productivity(websocket.sent)["status"] == "approved"
    first_approval_calls = runtime._approval_id_factory.calls

    # second confirm: proposal already consumed -> runtime returns approved again
    asyncio.run(server._handle_message(websocket, json.dumps(confirm_payload)))
    second = _last_productivity(websocket.sent)
    assert second["status"] == "approved"
    # no second approval id was minted (registry already complete)
    assert runtime._approval_id_factory.calls == first_approval_calls


# --- Cross-session non-disclosure --------------------------------------------

def test_cross_session_non_disclosure(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
    session_a = LoopbackWebSocket()
    session_b = LoopbackWebSocket()
    _pair(server, session_a)
    _pair(server, session_b)
    actor_a = _paired_owner_context(server, session_a)
    actor_b = _paired_owner_context(server, session_b)
    assert actor_a.session_id != actor_b.session_id
    proposal = _make_proposal(actor_a, "proposal_xs", clock.value)
    asyncio.run(server.publish_productivity_proposal(session_a, proposal))

    assert _last_productivity(session_a.sent)["type"] == (
        "productivity_confirmation_required"
    )

    # session B never received the proposal
    assert all(m.get("type") != "productivity_confirmation_required" for m in session_b.sent)

    # session B cannot confirm session A's proposal (registry is actor+session scoped)
    asyncio.run(
        server._handle_message(
            session_b,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_xs", "scope": "once"}
            ),
        )
    )
    assert _last_productivity(session_b.sent)["type"] == "productivity_error"

    # session B status on A's proposal is not disclosed
    asyncio.run(
        server._handle_message(
            session_b,
            json.dumps({"type": "productivity_status", "proposal_id": "proposal_xs"}),
        )
    )
    assert _last_productivity(session_b.sent)["type"] == "productivity_error"


# --- Invalid IDs rejected before runtime -------------------------------------

def test_malformed_proposal_id_rejected_before_runtime(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
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
    # runtime was never reached
    assert runtime._service._registry._proposals == {}


# --- Runtime messages validate through validate_server_message ---------------

def test_all_outbound_messages_validate(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    actor = _paired_owner_context(server, websocket)
    proposal = _make_proposal(actor, "proposal_validate", clock.value)
    asyncio.run(server.publish_productivity_proposal(websocket, proposal))
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_validate", "scope": "once"}
            ),
        )
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_status", "proposal_id": "proposal_validate"}),
        )
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_cancel", "proposal_id": "proposal_validate"}),
        )
    )

    for message in websocket.sent:
        if message.get("type", "").startswith("productivity_"):
            assert validate_server_message(message) is None


# --- No approval IDs / actor / session IDs / exception text / provider in sent

def test_outbound_messages_exclude_secrets(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_secret_1"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    actor = _paired_owner_context(server, websocket)
    proposal = _make_proposal(actor, "proposal_priv", clock.value)
    asyncio.run(server.publish_productivity_proposal(websocket, proposal))
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_priv", "scope": "once"}
            ),
        )
    )

    serialized = json.dumps(websocket.sent)
    assert "approval_secret_1" not in serialized
    assert "owner_secret" not in serialized
    assert "session_secret" not in serialized
    # the derived session id must never appear in outbound payloads
    assert actor.session_id not in serialized
    assert "provider" not in serialized
    assert "Traceback" not in serialized
    assert "Exception" not in serialized


# --- authorize_execution remains unavailable as a client message -------------

def test_authorize_execution_not_exposed(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1"))
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
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
    # authorize_execution is never a client-facing message type
    assert runtime._service._registry._proposals == {}


# --- No external action is executed ------------------------------------------

def test_no_external_action_executed(tmp_path: Path) -> None:
    clock = MutableClock(1500.0)
    runtime = _make_runtime(tmp_path, clock, ApprovalIds("approval_1"))
    authorize_execution = MagicMock(wraps=runtime.authorize_execution)
    runtime.authorize_execution = authorize_execution
    server = WebSocketServer(MagicMock(), productivity_runtime=runtime)
    websocket = LoopbackWebSocket()
    _pair(server, websocket)

    actor = _paired_owner_context(server, websocket)
    proposal = _make_proposal(actor, "proposal_noexec", clock.value)
    asyncio.run(server.publish_productivity_proposal(websocket, proposal))
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {"type": "productivity_confirm", "proposal_id": "proposal_noexec", "scope": "once"}
            ),
        )
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_cancel", "proposal_id": "proposal_noexec"}),
        )
    )

    authorize_execution.assert_not_called()
    # Without an injected coordinator, approval is issued but never consumed:
    # the only DB write is the approval row, and it is revoked by the cancel.
    approvals = runtime._service._store.list_for_actor("local-owner", limit=64)
    assert len(approvals) == 1
    assert approvals[0].revoked is True
    assert approvals[0].proposal_id == "proposal_noexec"
