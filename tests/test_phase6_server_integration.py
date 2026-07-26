"""Comprehensive adversarial integration tests for Phase 6 WebSocket server contracts."""

import asyncio
import json
import time

from core.server import WebSocketServer
from core.protocol import validate_server_message
from core.phase6_transport import (
    build_home_assistant_proposal_frame,
)


class DummyWebSocket:
    def __init__(self, is_paired: bool = True, is_loopback: bool = True):
        self.sent: list[dict] = []
        self.closed = False
        self.is_paired = is_paired
        if is_loopback:
            self.remote_address = ("127.0.0.1", 12345)
        else:
            self.remote_address = ("192.168.1.100", 54321)

    async def send(self, message_str: str):
        self.sent.append(json.loads(message_str))

    async def close(self):
        self.closed = True


def test_unknown_phase6_client_type_rejected():
    async def _run():
        server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
        ws = DummyWebSocket(is_paired=True, is_loopback=True)
        client_key = str(id(ws))
        server._paired_client_ids.add(client_key)

        bad_frame = json.dumps({
            "type": "phase6_unknown_action",
            "request_id": "req_001",
            "protocol_version": 1,
        })

        await server._handle_message(ws, bad_frame)
        assert len(ws.sent) == 1
        err = ws.sent[0]
        assert err["type"] == "error" or err["type"] == "phase6_error"

    asyncio.run(_run())


def test_missing_and_unknown_fields_rejected():
    async def _run():
        server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
        ws = DummyWebSocket(is_paired=True, is_loopback=True)
        client_key = str(id(ws))
        server._paired_client_ids.add(client_key)

        # Missing request_id
        missing_field = json.dumps({
            "type": "phase6_integration_list_request",
            "protocol_version": 1,
        })
        await server._handle_message(ws, missing_field)
        assert ws.sent[-1]["type"] == "phase6_error"
        assert ws.sent[-1]["code"] == "invalid_request"

        # Unknown field
        unknown_field = json.dumps({
            "type": "phase6_integration_list_request",
            "request_id": "req_002",
            "protocol_version": 1,
            "unexpected_key": "bad",
        })
        await server._handle_message(ws, unknown_field)
        assert ws.sent[-1]["type"] == "phase6_error"
        assert ws.sent[-1]["code"] == "invalid_request"

    asyncio.run(_run())


def test_client_asserted_identity_rejected():
    async def _run():
        server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
        ws = DummyWebSocket(is_paired=True, is_loopback=True)
        client_key = str(id(ws))
        server._paired_client_ids.add(client_key)

        asserted_payload = json.dumps({
            "type": "phase6_integration_list_request",
            "request_id": "req_003",
            "protocol_version": 1,
            "actor_id": "fake_owner",
            "authority": "supreme",
        })
        await server._handle_message(ws, asserted_payload)
        assert ws.sent[-1]["type"] == "phase6_error"
        assert ws.sent[-1]["code"] == "invalid_request"

    asyncio.run(_run())


def test_guest_unpaired_denied():
    async def _run():
        server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
        ws_unpaired = DummyWebSocket(is_paired=False, is_loopback=True)

        req = json.dumps({
            "type": "phase6_integration_list_request",
            "request_id": "req_004",
            "protocol_version": 1,
        })
        await server._handle_message(ws_unpaired, req)
        assert len(ws_unpaired.sent) == 1
        assert ws_unpaired.sent[0]["type"] == "phase6_error"
        assert ws_unpaired.sent[0]["code"] == "unauthorized"

    asyncio.run(_run())


def test_paired_owner_receives_bounded_status():
    async def _run():
        server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
        ws = DummyWebSocket(is_paired=True, is_loopback=True)
        client_key = str(id(ws))
        server._paired_client_ids.add(client_key)

        req = json.dumps({
            "type": "phase6_integration_list_request",
            "request_id": "req_005",
            "protocol_version": 1,
        })
        await server._handle_message(ws, req)
        assert len(ws.sent) == 5
        for msg in ws.sent:
            assert msg["type"] == "phase6_integration_status"
            assert msg["request_id"] == "req_005"
            assert msg["status"] == "unavailable"
            assert validate_server_message(msg) is None

    asyncio.run(_run())


def test_queue_rejects_malformed_or_mutated_proposals():
    server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
    assert server.queue_phase6_proposal("client", {"proposal_id": "forged"}) is False
    proposal = build_home_assistant_proposal_frame(
        request_id="req_init",
        proposal_id="prop_safe",
        entity_id="light.kitchen",
        domain="light",
        service="turn_on",
        risk="low",
        effect_summary="Turn on kitchen light",
        expires_at=time.time() + 30.0,
        nonce="nonce_safe",
    )
    assert server.queue_phase6_proposal("client", proposal) is True
    proposal["nonce"] = "mutated"
    assert server._phase6_pending_proposals["client"]["prop_safe"]["nonce"] == "nonce_safe"


def test_duplicate_request_id_rejected():
    async def _run():
        server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
        ws = DummyWebSocket(is_paired=True, is_loopback=True)
        client_key = str(id(ws))
        server._paired_client_ids.add(client_key)

        req = json.dumps({
            "type": "phase6_integration_list_request",
            "request_id": "req_dup_01",
            "protocol_version": 1,
        })
        await server._handle_message(ws, req)
        ws.sent.clear()

        await server._handle_message(ws, req)
        assert len(ws.sent) == 1
        assert ws.sent[0]["type"] == "phase6_error"
        assert ws.sent[0]["code"] == "duplicate_request"

    asyncio.run(_run())



def test_stale_and_expired_confirmation_rejected():
    async def _run():
        server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
        ws = DummyWebSocket(is_paired=True, is_loopback=True)
        client_key = str(id(ws))
        server._paired_client_ids.add(client_key)

        expired_proposal = build_home_assistant_proposal_frame(
            request_id="req_init",
            proposal_id="prop_expired",
            entity_id="light.kitchen",
            domain="light",
            service="turn_off",
            risk="medium",
            effect_summary="Turn off kitchen light",
            expires_at=time.time() - 10.0,
            nonce="nonce_exp_123",
        )
        server.queue_phase6_proposal(client_key, expired_proposal)

        confirm_req = json.dumps({
            "type": "phase6_home_assistant_confirm_request",
            "request_id": "req_conf_01",
            "protocol_version": 1,
            "proposal_id": "prop_expired",
            "nonce": "nonce_exp_123",
        })
        await server._handle_message(ws, confirm_req)
        assert ws.sent[-1]["type"] == "phase6_error"
        assert ws.sent[-1]["code"] == "expired"

    asyncio.run(_run())


def test_confirmation_replay_and_nonce_mismatch_rejected():
    async def _run():
        server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
        ws = DummyWebSocket(is_paired=True, is_loopback=True)
        client_key = str(id(ws))
        server._paired_client_ids.add(client_key)

        valid_proposal = build_home_assistant_proposal_frame(
            request_id="req_init",
            proposal_id="prop_valid",
            entity_id="switch.pump",
            domain="switch",
            service="turn_on",
            risk="high",
            effect_summary="Turn on pump",
            expires_at=time.time() + 300.0,
            nonce="nonce_correct",
        )
        server.queue_phase6_proposal(client_key, valid_proposal)

        # Mismatched nonce
        bad_nonce_req = json.dumps({
            "type": "phase6_home_assistant_confirm_request",
            "request_id": "req_conf_bad_nonce",
            "protocol_version": 1,
            "proposal_id": "prop_valid",
            "nonce": "nonce_WRONG",
        })
        await server._handle_message(ws, bad_nonce_req)
        assert ws.sent[-1]["type"] == "phase6_error"
        assert ws.sent[-1]["code"] == "stale_request"

        # Valid confirmation (consumed one-time)
        good_req = json.dumps({
            "type": "phase6_home_assistant_confirm_request",
            "request_id": "req_conf_good",
            "protocol_version": 1,
            "proposal_id": "prop_valid",
            "nonce": "nonce_correct",
        })
        await server._handle_message(ws, good_req)
        assert ws.sent[-1]["type"] == "phase6_error"
        assert ws.sent[-1]["code"] == "unavailable"

        # Replay attempt fails (already consumed)
        replay_req = json.dumps({
            "type": "phase6_home_assistant_confirm_request",
            "request_id": "req_conf_replay",
            "protocol_version": 1,
            "proposal_id": "prop_valid",
            "nonce": "nonce_correct",
        })
        await server._handle_message(ws, replay_req)
        assert ws.sent[-1]["type"] == "phase6_error"
        assert ws.sent[-1]["code"] == "stale_request"

    asyncio.run(_run())


def test_disconnect_cleans_pending_proposals_and_trackers():
    async def _run():
        server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
        ws = DummyWebSocket(is_paired=True, is_loopback=True)
        client_key = str(id(ws))
        server.connected_clients.add(ws)
        server._paired_client_ids.add(client_key)

        proposal = build_home_assistant_proposal_frame(
            request_id="req_init",
            proposal_id="prop_clean",
            entity_id="fan.office",
            domain="fan",
            service="turn_on",
            risk="low",
            effect_summary="Turn on fan",
            expires_at=time.time() + 300.0,
            nonce="nonce_clean",
        )
        server.queue_phase6_proposal(client_key, proposal)
        server._get_phase6_tracker(client_key)

        assert client_key in server._phase6_pending_proposals
        assert client_key in server._phase6_trackers

        # Disconnect cleanup simulation
        server._phase6_pending_proposals.pop(client_key, None)
        server._phase6_trackers.pop(client_key, None)

        assert client_key not in server._phase6_pending_proposals
        assert client_key not in server._phase6_trackers

    asyncio.run(_run())
