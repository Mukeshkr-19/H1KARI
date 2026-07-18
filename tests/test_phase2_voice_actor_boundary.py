"""Phase 2 request-scoped actor boundary for voice turns."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

from core.action_policy import Actor, ActorContext
from core.server import WebSocketServer
from core.request_context import ActorSource, derive_actor_from_transport


class MockWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    @property
    def remote_address(self):
        return ("192.168.1.1", 12345)


class LoopbackWebSocket(MockWebSocket):
    @property
    def remote_address(self):
        return ("127.0.0.1", 12345)


def test_request_context_uses_one_consistent_session_id():
    context = derive_actor_from_transport(
        source=ActorSource.WEBSOCKET,
        connection_token="a" * 32,
        is_loopback=False,
        is_paired=True,
    )

    assert context.session_id == context.actor_context.session_id


def test_remote_voice_turn_is_guest():
    orchestrator = MagicMock()
    orchestrator.process_input.return_value = "safe reply"
    server = WebSocketServer(orchestrator)
    websocket = MockWebSocket()
    server._paired_client_ids.add(str(id(websocket)))

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "voice", "text": "hello"}),
        )
    )

    args, kwargs = orchestrator.process_input.call_args
    assert kwargs["source"] == "voice_remote"
    assert kwargs["context"].actor.value == "guest"


def test_loopback_voice_turn_is_owner():
    orchestrator = MagicMock()
    orchestrator.process_input.return_value = "owner reply"
    server = WebSocketServer(orchestrator)
    websocket = LoopbackWebSocket()
    server._paired_client_ids.add(str(id(websocket)))

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "voice", "text": "hello"}),
        )
    )

    args, kwargs = orchestrator.process_input.call_args
    assert kwargs["source"] == "voice_remote"
    assert kwargs["context"].actor.value == "owner"


def test_voice_transcript_text_does_not_appear_in_debug_output(capsys):
    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    context = ActorContext(
        actor_id="guest", actor=Actor.GUEST, session_id="test-session", source="voice_remote"
    )

    reply = orch.process_input("my secret is 12345", source="voice_remote", context=context)

    out, _err = capsys.readouterr()
    assert "my secret is 12345" not in out
    assert "secret" not in out.lower()
    assert reply is not None
