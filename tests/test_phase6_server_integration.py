"""Adversarial integration tests for Phase 6 WebSocket server & correlation tracker."""

import asyncio
import json
import time

from core.action_policy import ActorContext, Actor
from core.server import WebSocketServer
from core.protocol import validate_server_message
from core.phase6_runtime import Phase6Subsystem, Phase6SubsystemConfig
from core.phase6_adapters.home_assistant import (
    HomeAssistantActionProposal,
    HomeAssistantConfirmation,
    HomeAssistantEntityRef,
    HomeAssistantServiceRef,
    HomeAssistantAdapterOutcome,
    HomeAssistantAdapterReason,
    HomeAssistantAdapterResult,
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


def test_structural_regression_server_has_no_second_proposal_container():
    server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765)
    assert not hasattr(server, "_phase6_pending_proposals")
    assert not hasattr(server, "queue_phase6_proposal")


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
        assert err["type"] in ("error", "phase6_error")

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
            assert validate_server_message(msg) is None

    asyncio.run(_run())


def test_two_phase_home_assistant_lifecycle_and_cancellation():
    async def _run():
        executor_calls = 0

        class FakeRealHAAdapter:
            def prepare(
                self,
                proposal_id: str,
                entity_ref: HomeAssistantEntityRef,
                service_ref: HomeAssistantServiceRef,
                service_data: dict,
                actor_context: ActorContext,
                nonce: str,
            ) -> HomeAssistantAdapterResult:
                now = time.time()
                prop = HomeAssistantActionProposal(
                    proposal_id=proposal_id,
                    entity_ref=entity_ref,
                    service_ref=service_ref,
                    service_data=service_data,
                    is_state_changing=True,
                    prepared_at=now,
                    expires_at=now + 300.0,
                    nonce=nonce,
                )
                return HomeAssistantAdapterResult(
                    HomeAssistantAdapterOutcome.REQUIRE_CONFIRMATION,
                    HomeAssistantAdapterReason.OK,
                    proposal=prop,
                )

            def confirm_and_execute(
                self,
                proposal: HomeAssistantActionProposal,
                confirmation: HomeAssistantConfirmation,
                actor_context: ActorContext,
            ) -> HomeAssistantAdapterResult:
                nonlocal executor_calls
                executor_calls += 1
                return HomeAssistantAdapterResult(
                    HomeAssistantAdapterOutcome.ALLOW,
                    HomeAssistantAdapterReason.OK,
                )

        subsys = Phase6Subsystem(
            config=Phase6SubsystemConfig(enabled=True, home_assistant_enabled=True),
            ha_adapter=FakeRealHAAdapter(),
        )
        server = WebSocketServer(orchestrator=None, host="127.0.0.1", port=8765, phase6_subsystem=subsys)
        ws = DummyWebSocket(is_paired=True, is_loopback=True)
        client_key = str(id(ws))
        server._paired_client_ids.add(client_key)

        prep_req = json.dumps({
            "type": "phase6_home_assistant_prepare_request",
            "request_id": "req_p_ok",
            "protocol_version": 1,
            "entity_id": "light.desk",
            "domain": "light",
            "service": "turn_on",
            "risk": "low",
            "effect_summary": "Turn on desk light",
        })
        await server._handle_message(ws, prep_req)
        assert ws.sent[-1]["type"] == "phase6_home_assistant_proposal"
        prop_id = ws.sent[-1]["proposal_id"]
        nonce = ws.sent[-1]["nonce"]

        confirm_req = json.dumps({
            "type": "phase6_home_assistant_confirm_request",
            "request_id": "req_c_ok",
            "protocol_version": 1,
            "proposal_id": prop_id,
            "nonce": nonce,
        })
        await server._handle_message(ws, confirm_req)
        assert ws.sent[-1]["type"] == "phase6_integration_status"
        assert ws.sent[-1]["status"] == "ready"
        assert executor_calls == 1  # Executor called exactly once

        # Proposal cancellation receives typed status frame with status cancelled
        prep_req_2 = json.dumps({
            "type": "phase6_home_assistant_prepare_request",
            "request_id": "req_p_2",
            "protocol_version": 1,
            "entity_id": "light.fan",
            "domain": "light",
            "service": "turn_off",
            "risk": "low",
            "effect_summary": "Turn off fan light",
        })
        await server._handle_message(ws, prep_req_2)
        prop_frame_2 = ws.sent[-1]
        assert prop_frame_2["type"] == "phase6_home_assistant_proposal"
        prop_id_2 = prop_frame_2["proposal_id"]

        cancel_req = json.dumps({
            "type": "phase6_proposal_cancel_request",
            "request_id": "req_cancel_01",
            "protocol_version": 1,
            "proposal_id": prop_id_2,
        })
        await server._handle_message(ws, cancel_req)
        assert ws.sent[-1]["type"] == "phase6_integration_status"
        assert ws.sent[-1]["status"] == "cancelled"

    asyncio.run(_run())
