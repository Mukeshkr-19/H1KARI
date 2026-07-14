"""The server and web client must share one bounded WebSocket v1 contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.protocol import (
    CLIENT_MESSAGES,
    PROTOCOL_SCHEMA,
    PROTOCOL_VERSION,
    SERVER_MESSAGES,
    validate_client_message,
)
from core.server import WebSocketServer


REPO_ROOT = Path(__file__).resolve().parent.parent


class MockWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


def test_protocol_v1_declares_exact_message_directions():
    assert PROTOCOL_SCHEMA["name"] == "hikari.websocket"
    assert PROTOCOL_VERSION == 1
    assert set(CLIENT_MESSAGES) == {
        "pair",
        "ping",
        "identify",
        "message",
        "voice",
        "companion_preferences",
        "status",
    }
    assert set(SERVER_MESSAGES) == {
        "welcome",
        "paired",
        "pair_error",
        "pair_locked",
        "pairing_required",
        "protocol_error",
        "pong",
        "identified",
        "response",
        "status",
        "error",
        "companion_update",
        "companion_preferences_ack",
        "companion_preferences_error",
    }


@pytest.mark.parametrize(
    "message,error",
    [
        ({"type": "message"}, "Missing required field: text"),
        ({"type": "message", "text": 7}, "Invalid field type: text"),
        ({"type": "message", "text": "x", "extra": True}, "Unknown field: extra"),
        ({"type": "message", "text": "x" * 20001}, "Field too long: text"),
        ({"type": "pair", "code": "ABC123", "device_type": "x" * 65}, "Field too long: device_type"),
        ({"type": "voice"}, "Missing required field: text or listening"),
        ({"type": "pair", "code": "ABC123", "protocol_version": True}, "Invalid field type: protocol_version"),
        ({"type": "not-real"}, "Unknown message type"),
    ],
)
def test_client_message_validation_is_stable(message, error):
    assert validate_client_message(message) == error


def test_valid_client_messages_pass_schema_validation():
    messages = [
        {"type": "pair", "code": "ABC123", "protocol_version": 1},
        {"type": "ping"},
        {"type": "identify", "device_type": "web"},
        {"type": "message", "text": "hello"},
        {"type": "voice", "text": "hello"},
        {"type": "voice", "listening": True},
        {
            "type": "companion_preferences",
            "companion_type": "cat",
            "presentation": "female",
        },
        {"type": "status"},
    ]

    assert all(validate_client_message(message) is None for message in messages)


def test_server_rejects_explicitly_unsupported_protocol_without_pair_attempt():
    server = WebSocketServer(MagicMock())
    server.pairing_code = "ABC123"
    websocket = MockWebSocket()

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "pair",
                    "code": "ABC123",
                    "protocol_version": 2,
                }
            ),
        )
    )

    assert websocket.sent == [
        {
            "type": "protocol_error",
            "message": "Unsupported protocol version",
            "supported_version": PROTOCOL_VERSION,
        }
    ]
    assert server._pair_attempts == {}


def test_omitted_protocol_version_remains_v1_compatible():
    server = WebSocketServer(MagicMock())
    server.pairing_code = "ABC123"
    websocket = MockWebSocket()

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "pair", "code": "ABC123"}),
        )
    )

    assert websocket.sent == [
        {
            "type": "paired",
            "message": "Device paired successfully",
            "protocol_version": PROTOCOL_VERSION,
        }
    ]


def test_paired_invalid_payload_is_rejected_before_orchestrator_call():
    server = WebSocketServer(MagicMock())
    websocket = MockWebSocket()
    server._paired_client_ids.add(str(id(websocket)))

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "message", "text": "x" * 20001}),
        )
    )

    assert websocket.sent == [{"type": "error", "message": "Field too long: text"}]
    server.orchestrator.process_input.assert_not_called()


def test_embedded_and_next_clients_use_protocol_v1_contract():
    server = WebSocketServer(MagicMock())
    _status, _headers, body = server._serve_connect_page()
    embedded = body.decode("utf-8")
    frontend = (REPO_ROOT / "hikari-frontend" / "src" / "app" / "page.tsx").read_text(
        encoding="utf-8"
    )

    assert f"protocol_version: {PROTOCOL_VERSION}" in embedded
    assert "__HIKARI_PROTOCOL_VERSION__" not in embedded
    assert 'import protocolSchema from "../../../protocol/hikari-v1.json"' in frontend
    assert "protocol_version: PROTOCOL_VERSION" in frontend
    assert "parseServerMessage(event.data)" in frontend
    assert 'encodeClientMessage("message"' in frontend
