"""Phase 3 email-draft preparation and WebSocket boundary tests."""

from __future__ import annotations

import asyncio
import json

import pytest

from core.action_policy import Actor, ActorContext
from core.productivity import (
    EmailDraftPreparationError,
    EmailDraftPreparationRegistry,
    EmailDraftProposalFactory,
    ProductivityRuntime,
    ProductivityService,
    SqliteApprovalStore,
)
from core.protocol import validate_server_message
from core.server import WebSocketServer


class _WebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    @property
    def remote_address(self):
        return ("127.0.0.1", 12345)

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


def _actor(session: str = "session-1") -> ActorContext:
    return ActorContext("local-owner", Actor.OWNER, session, "websocket")


def _runtime(tmp_path, now: float = 1000.0) -> ProductivityRuntime:
    return ProductivityRuntime(
        ProductivityService(SqliteApprovalStore(str(tmp_path / "approvals.db"))),
        lambda: now,
        lambda: "approval-1",
    )


def _pair(server: WebSocketServer, websocket: _WebSocket) -> None:
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "pair", "code": server.pairing_code}),
        )
    )


def test_factory_is_deterministic_bounded_and_content_safe():
    factory = EmailDraftProposalFactory(lambda: 1000.0, lambda: "proposal-1")
    prepared = factory.prepare(
        _actor(), "user@example.com", "Hello", "x" * 20_000
    )

    assert prepared.proposal.proposal_id == "proposal-1"
    assert prepared.proposal.expires_at == 1300.0
    assert prepared.proposal.preview_fields[-1].truncated is True
    assert len(prepared.proposal.preview_fields[-1].value) == 2000
    assert prepared.draft.body == "x" * 20_000
    assert repr(prepared) == "EmailDraftPreparation(...)"
    assert repr(prepared.draft) == "PreparedEmailDraft(...)"


@pytest.mark.parametrize(
    "clock,identifier",
    [
        (lambda: float("nan"), lambda: "proposal-1"),
        (lambda: 1000.0, lambda: "BAD ID"),
        (lambda: 1000.0, lambda: (_ for _ in ()).throw(RuntimeError("secret"))),
    ],
)
def test_factory_rejects_bad_clock_or_id_without_details(clock, identifier):
    factory = EmailDraftProposalFactory(clock, identifier)
    with pytest.raises(EmailDraftPreparationError) as error:
        factory.prepare(_actor(), "user@example.com", "", "")
    assert str(error.value) == "email draft preparation failed"
    assert error.value.__cause__ is None


def test_registry_is_bounded_and_session_scoped():
    factory = EmailDraftProposalFactory(lambda: 1000.0, lambda: "proposal-1")
    prepared = factory.prepare(_actor(), "user@example.com", "", "")
    registry = EmailDraftPreparationRegistry(limit=1)
    registry.put(_actor(), "proposal-1", prepared.draft)

    assert registry.get(_actor(), "proposal-1") is prepared.draft
    assert registry.get(_actor("session-2"), "proposal-1") is None
    registry.clear_session("local-owner", "session-1")
    assert registry.get(_actor(), "proposal-1") is None


def test_server_prepare_returns_canonical_confirmation_and_retains_private_input(tmp_path):
    runtime = _runtime(tmp_path)
    factory = EmailDraftProposalFactory(lambda: 1000.0, lambda: "proposal-1")
    registry = EmailDraftPreparationRegistry()
    server = WebSocketServer(
        object(),
        productivity_runtime=runtime,
        email_draft_factory=factory,
        email_draft_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_email_draft_prepare",
                    "request_id": "email-req-1",
                    "recipient": "user@example.com",
                    "subject": "Hello",
                    "body": "Private body",
                }
            ),
        )
    )

    message = websocket.sent[-1]
    assert message["type"] == "productivity_confirmation_required"
    assert message["proposal_id"] == "proposal-1"
    assert message["request_id"] == "email-req-1"
    assert validate_server_message(message) is None
    actor = server._derive_actor_context(websocket).actor_context
    retained = registry.get(actor, "proposal-1")
    assert retained is not None and retained.body == "Private body"


def test_server_prepare_echoes_request_id_on_safe_validation_error(tmp_path):
    runtime = _runtime(tmp_path)
    server = WebSocketServer(
        object(),
        productivity_runtime=runtime,
        email_draft_factory=EmailDraftProposalFactory(
            lambda: 1000.0, lambda: "proposal-1"
        ),
        email_draft_registry=EmailDraftPreparationRegistry(),
    )
    websocket = _WebSocket()
    _pair(server, websocket)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_email_draft_prepare",
                    "request_id": "email-req-cf",
                    "recipient": "user@example.com",
                    "subject": "",
                    "body": "bad\u200btext",
                }
            ),
        )
    )

    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "email-req-cf"
    assert message["code"] in {
        "unavailable",
        "proposal_invalid",
        "confirm_failed",
        "cancel_failed",
        "proposal_expired",
    }
    assert "message" not in message
    assert validate_server_message(message) is None


def test_server_cancel_removes_retained_draft(tmp_path):
    runtime = _runtime(tmp_path)
    registry = EmailDraftPreparationRegistry()
    server = WebSocketServer(
        object(),
        productivity_runtime=runtime,
        email_draft_factory=EmailDraftProposalFactory(
            lambda: 1000.0, lambda: "proposal-1"
        ),
        email_draft_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_email_draft_prepare",
                    "request_id": "email-req-2",
                    "recipient": "user@example.com",
                    "subject": "",
                    "body": "Private body",
                }
            ),
        )
    )
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
