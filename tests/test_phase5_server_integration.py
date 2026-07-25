"""Phase 5 WebSocket server integration (synthetic fixtures only)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.action_policy import Actor, ActorContext
from core.phase5.bootstrap import create_phase5_subsystem
from core.phase5.contracts import Capability
from core.phase5.runtime_guard import Phase5RuntimeContext, Phase5RuntimeRequest
from core.protocol import validate_client_message, validate_server_message
from core.server import WebSocketServer


class MockWebSocket:
    def __init__(self, *, loopback: bool = True):
        self.sent = []
        self.remote_address = ("127.0.0.1", 12345) if loopback else ("8.8.8.8", 12345)

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


def _server(tmp_path: Path) -> WebSocketServer:
    orch = MagicMock()
    subsystem = create_phase5_subsystem(
        clock=lambda: 1_700_000_000.0,
        core_grants_db_path=tmp_path / "core_grants.db",
        audit_db_path=tmp_path / "audit.db",
        phase5_grants_db_path=tmp_path / "p5_grants.db",
        phase5_consents_db_path=tmp_path / "p5_consents.db",
        session_db_path=tmp_path / "sessions.db",
    )
    return WebSocketServer(orch, phase5_subsystem=subsystem)


def _pair(server: WebSocketServer, ws: MockWebSocket) -> None:
    server._paired_client_ids.add(str(id(ws)))
    server._connection_tokens[str(id(ws))] = "tok-1"


def _handle(server: WebSocketServer, ws: MockWebSocket, payload: dict):
    asyncio.run(server._handle_message(ws, json.dumps(payload)))


def test_guest_unpaired_denied(tmp_path):
    server = _server(tmp_path)
    ws = MockWebSocket()
    _handle(
        server,
        ws,
        {
            "type": "phase5_session_activate",
            "request_id": "req-guest-1",
            "protocol_version": 1,
            "session_type": "owner",
            "capabilities": ["teach_me"],
            "expires_at": 1_700_000_900.0,
        },
    )
    assert ws.sent[-1]["type"] == "phase5_error"
    assert ws.sent[-1]["code"] == "unauthorized"
    assert validate_server_message(ws.sent[-1]) is None


def test_owner_activation_and_status(tmp_path):
    server = _server(tmp_path)
    ws = MockWebSocket()
    _pair(server, ws)
    _handle(
        server,
        ws,
        {
            "type": "phase5_session_activate",
            "request_id": "req-own-1",
            "protocol_version": 1,
            "session_type": "owner",
            "capabilities": ["teach_me"],
            "expires_at": 1_700_000_900.0,
        },
    )
    assert ws.sent[-1]["type"] == "phase5_session_update"
    assert ws.sent[-1]["state"] == "active"
    assert ws.sent[-1]["request_id"] == "req-own-1"
    session_id = ws.sent[-1]["session_id"]
    _handle(
        server,
        ws,
        {
            "type": "phase5_session_status",
            "request_id": "req-own-2",
            "protocol_version": 1,
            "session_id": session_id,
        },
    )
    assert ws.sent[-1]["type"] == "phase5_session_update"


def test_child_activation(tmp_path):
    server = _server(tmp_path)
    ws = MockWebSocket()
    _pair(server, ws)
    _handle(
        server,
        ws,
        {
            "type": "phase5_session_activate",
            "request_id": "req-child-1",
            "protocol_version": 1,
            "session_type": "child",
            "session_actor_id": "child-1",
            "activation_evidence": "owner-activated-child",
            "capabilities": ["child_mode", "teach_me"],
            "expires_at": 1_700_000_900.0,
        },
    )
    assert ws.sent[-1]["type"] == "phase5_session_update"
    assert ws.sent[-1]["session_type"] == "child"


def test_capability_prepare_after_authorize_only(tmp_path):
    server = _server(tmp_path)
    ws = MockWebSocket()
    _pair(server, ws)
    _handle(
        server,
        ws,
        {
            "type": "phase5_capability_prepare",
            "request_id": "req-cap-1",
            "protocol_version": 1,
            "capability": "teach_me",
            "topic": "photosynthesis",
        },
    )
    last = ws.sent[-1]
    assert last["type"] in {
        "phase5_capability_proposal",
        "phase5_approval_required",
        "phase5_error",
    }
    assert last["request_id"] == "req-cap-1"
    if last["type"] == "phase5_capability_proposal":
        assert last["installs_skills"] is False
        assert "exception" not in last


def test_client_identity_fields_cannot_override(tmp_path):
    server = _server(tmp_path)
    ws = MockWebSocket()
    _pair(server, ws)
    bad = {
        "type": "phase5_session_activate",
        "request_id": "req-id-1",
        "protocol_version": 1,
        "session_type": "owner",
        "capabilities": ["teach_me"],
        "expires_at": 1_700_000_900.0,
        "actor_id": "attacker",
    }
    assert validate_client_message(bad) is not None
    _handle(server, ws, bad)
    assert ws.sent[-1]["code"] == "invalid_request"


def test_helper_grant_owner_only_and_revoke(tmp_path):
    server = _server(tmp_path)
    ws = MockWebSocket()
    _pair(server, ws)
    _handle(
        server,
        ws,
        {
            "type": "phase5_helper_grant_create",
            "request_id": "req-hg-1",
            "protocol_version": 1,
            "helper_actor_id": "helper-1",
            "capability": "teach_me",
            "expires_at": 1_700_003_600.0,
        },
    )
    assert ws.sent[-1]["type"] == "phase5_helper_grants"
    grant_id = ws.sent[-1]["grants"][0]["grant_id"]
    _handle(
        server,
        ws,
        {
            "type": "phase5_helper_grant_revoke",
            "request_id": "req-hg-2",
            "protocol_version": 1,
            "grant_id": grant_id,
        },
    )
    assert ws.sent[-1]["type"] == "phase5_helper_grants"
    assert any(g["grant_id"] == grant_id and g["revoked"] for g in ws.sent[-1]["grants"])


def test_stale_and_duplicate_approval_denied(tmp_path):
    server = _server(tmp_path)
    ws = MockWebSocket()
    _pair(server, ws)
    _handle(
        server,
        ws,
        {
            "type": "phase5_capability_confirm",
            "request_id": "req-conf-1",
            "protocol_version": 1,
            "pending_request_id": "missing-pending",
            "acknowledged": True,
        },
    )
    assert ws.sent[-1]["code"] == "stale_request"


def test_approval_is_bounded_scoped_and_one_time(tmp_path):
    server = _server(tmp_path)
    ws = MockWebSocket()
    _pair(server, ws)
    prepare = {
        "type": "phase5_capability_prepare",
        "request_id": "req-guide-approval",
        "protocol_version": 1,
        "capability": "guide_my_hands",
        "action": "execute_step",
        "resource": "device-panel",
        "data_subject": "owner",
        "goal": "change one setting",
    }

    _handle(server, ws, prepare)
    assert ws.sent[-1]["type"] == "phase5_approval_required"

    _handle(server, ws, prepare)
    assert ws.sent[-1]["type"] == "phase5_error"
    assert ws.sent[-1]["code"] == "duplicate_request"

    _handle(
        server,
        ws,
        {
            "type": "phase5_capability_confirm",
            "request_id": "req-guide-confirm",
            "protocol_version": 1,
            "pending_request_id": "req-guide-approval",
            "acknowledged": True,
        },
    )
    assert ws.sent[-1]["type"] == "phase5_capability_proposal"
    consents = server._phase5_subsystem.consent_store.list_for_owner(
        "local-owner", 1_700_000_001.0
    )
    assert len(consents) == 1
    assert consents[0].scope.allowed_actions == ("execute_step",)
    assert consents[0].scope.resource_pattern == "device-panel"
    assert consents[0].expires_at == 1_700_000_300.0

    _handle(
        server,
        ws,
        {
            "type": "phase5_capability_confirm",
            "request_id": "req-guide-confirm-again",
            "protocol_version": 1,
            "pending_request_id": "req-guide-approval",
            "acknowledged": True,
        },
    )
    assert ws.sent[-1]["code"] == "stale_request"


def test_pending_approval_expires(tmp_path):
    server = _server(tmp_path)
    ws = MockWebSocket()
    _pair(server, ws)
    _handle(
        server,
        ws,
        {
            "type": "phase5_capability_prepare",
            "request_id": "req-care-expiring",
            "protocol_version": 1,
            "capability": "care",
            "care_prompt": "I need support",
        },
    )
    assert ws.sent[-1]["type"] == "phase5_approval_required"
    pending = server._phase5_pending_approvals[str(id(ws))]["req-care-expiring"]
    pending["expires_at"] = 1_699_999_999.0

    _handle(
        server,
        ws,
        {
            "type": "phase5_capability_confirm",
            "request_id": "req-care-confirm",
            "protocol_version": 1,
            "pending_request_id": "req-care-expiring",
            "acknowledged": True,
        },
    )
    assert ws.sent[-1]["type"] == "phase5_error"
    assert ws.sent[-1]["code"] == "stale_request"
    _handle(
        server,
        ws,
        {
            "type": "phase5_capability_confirm",
            "request_id": "req-conf-2",
            "protocol_version": 1,
            "pending_request_id": "missing-pending",
            "acknowledged": True,
        },
    )
    assert ws.sent[-1]["code"] == "stale_request"


def test_missing_runtime_unavailable(tmp_path):
    orch = MagicMock()
    subsystem = create_phase5_subsystem(
        clock=lambda: 1_700_000_000.0,
        core_grants_db_path=tmp_path / "core_grants.db",
        audit_db_path=tmp_path / "audit.db",
        phase5_grants_db_path=tmp_path / "p5_grants.db",
        phase5_consents_db_path=tmp_path / "p5_consents.db",
        session_db_path=tmp_path / "sessions.db",
    )
    subsystem = replace(
        subsystem,
        runtime_service=None,
        capability_service=None,
        session_store=None,
    )
    server = WebSocketServer(orch, phase5_subsystem=subsystem)
    ws = MockWebSocket()
    _pair(server, ws)
    _handle(
        server,
        ws,
        {
            "type": "phase5_session_activate",
            "request_id": "req-unavail-1",
            "protocol_version": 1,
            "session_type": "owner",
            "capabilities": ["teach_me"],
            "expires_at": 1_700_000_900.0,
        },
    )
    assert ws.sent[-1]["code"] == "unavailable"


def test_phase1_4_backward_compatible_pair_message(tmp_path):
    server = _server(tmp_path)
    ws = MockWebSocket()
    _handle(
        server,
        ws,
        {"type": "pair", "code": server.pairing_code, "protocol_version": 1},
    )
    assert any(m.get("type") == "paired" for m in ws.sent)


def test_one_audit_row_on_authorize(tmp_path):
    subsystem = create_phase5_subsystem(
        clock=lambda: 1_700_000_000.0,
        core_grants_db_path=tmp_path / "core_grants.db",
        audit_db_path=tmp_path / "audit.db",
        phase5_grants_db_path=tmp_path / "p5_grants.db",
        phase5_consents_db_path=tmp_path / "p5_consents.db",
        session_db_path=tmp_path / "sessions.db",
    )
    actor = ActorContext("local-owner", Actor.OWNER, "sess")
    decision = subsystem.runtime_service.authorize(
        Phase5RuntimeRequest(
            request_id="req-audit-1",
            capability=Capability.TEACH_ME,
            context=Phase5RuntimeContext(actor_context=actor),
            user_initiated=True,
            now=1_700_000_000.0,
        )
    )
    assert decision.audit_id is not None
