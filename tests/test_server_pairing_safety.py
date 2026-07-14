"""Server pairing must be a real authorization and secrecy boundary."""

from __future__ import annotations

import asyncio
import json
import re
import unittest
from unittest.mock import MagicMock, patch

from core.protocol import PROTOCOL_VERSION
from core.server import MAX_PAIRING_ATTEMPTS, WebSocketServer


class MockWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


class EmptyConnection(MockWebSocket):
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class TestServerPairingSafety(unittest.TestCase):
    def test_pairing_code_is_random_six_character_hex(self):
        codes = {WebSocketServer(MagicMock()).pairing_code for _ in range(20)}

        self.assertEqual(len(codes), 20)
        self.assertTrue(all(re.fullmatch(r"[0-9A-F]{6}", code) for code in codes))

    def test_qr_page_does_not_disclose_pairing_code(self):
        server = WebSocketServer(MagicMock())
        server.pairing_code = '<script>alert("xss")</script>'
        with patch("core.server.QR_AVAILABLE", True), patch(
            "core.server.qrcode.QRCode"
        ) as qr_cls, patch("socket.gethostname", return_value="host"), patch(
            "socket.gethostbyname", return_value="127.0.0.1"
        ):
            img = MagicMock()
            qr_cls.return_value.make_image.return_value = img
            status, headers, body = server._serve_qr_code()

        html = body.decode("utf-8")
        self.assertNotIn(server.pairing_code, html)
        self.assertNotIn("&lt;script&gt;alert", html)
        self.assertIn("local HIKARI terminal", html)
        self.assertEqual(status.value, 200)
        header_names = {name.lower() for name, _ in headers}
        self.assertIn("x-content-type-options", header_names)
        self.assertIn("content-security-policy", header_names)

    def test_connect_page_has_hardening_headers(self):
        server = WebSocketServer(MagicMock())
        status, headers, body = server._serve_connect_page()
        self.assertEqual(status.value, 200)
        header_map = {name.lower(): value for name, value in headers}
        self.assertEqual(header_map["cache-control"], "no-store")
        self.assertEqual(header_map["referrer-policy"], "no-referrer")
        self.assertEqual(header_map["x-frame-options"], "DENY")
        self.assertEqual(header_map["x-content-type-options"], "nosniff")
        self.assertIn(
            "frame-ancestors 'none'", header_map["content-security-policy"]
        )
        self.assertIn(
            "connect-src 'self' ws: wss:", header_map["content-security-policy"]
        )
        self.assertIn(b"textContent", body)

    def test_public_status_does_not_disclose_pairing_or_device_details(self):
        server = WebSocketServer(MagicMock())
        server.device_info["private-client"] = {"type": "phone"}
        status, headers, body = server._serve_api_status()
        payload = json.loads(body)

        self.assertEqual(status.value, 200)
        self.assertEqual(payload, {"running": False, "clients": 0})
        self.assertNotIn(server.pairing_code, body.decode("utf-8"))
        header_map = {name.lower(): value for name, value in headers}
        self.assertEqual(header_map["cache-control"], "no-store")
        self.assertEqual(header_map["x-content-type-options"], "nosniff")

    def test_unpaired_clients_cannot_use_protected_message_types(self):
        server = WebSocketServer(MagicMock())
        protected = [
            {"type": "identify", "device_type": "phone"},
            {"type": "message", "text": "private request"},
            {"type": "voice", "text": "private voice request"},
            {"type": "status"},
            {"type": "companion_preferences", "companion_type": "orb"},
        ]

        for payload in protected:
            with self.subTest(payload=payload):
                websocket = MockWebSocket()
                asyncio.run(server._handle_message(websocket, json.dumps(payload)))
                self.assertEqual(
                    websocket.sent,
                    [
                        {
                            "type": "pairing_required",
                            "message": "Pair this connection before sending requests",
                        }
                    ],
                )

        server.orchestrator.process_input.assert_not_called()

    def test_ping_is_available_before_pairing(self):
        server = WebSocketServer(MagicMock())
        websocket = MockWebSocket()

        asyncio.run(server._handle_message(websocket, json.dumps({"type": "ping"})))

        self.assertEqual(websocket.sent, [{"type": "pong"}])

    def test_unknown_message_type_has_stable_error_after_pairing(self):
        server = WebSocketServer(MagicMock())
        websocket = MockWebSocket()
        server._paired_client_ids.add(str(id(websocket)))

        asyncio.run(
            server._handle_message(
                websocket,
                json.dumps({"type": "unsupported-private-operation"}),
            )
        )

        self.assertEqual(
            websocket.sent,
            [{"type": "error", "message": "Unknown message type"}],
        )

    def test_broadcast_reaches_only_paired_clients(self):
        server = WebSocketServer(MagicMock())
        paired = MockWebSocket()
        unpaired = MockWebSocket()
        server.connected_clients = {paired, unpaired}
        server._paired_client_ids.add(str(id(paired)))
        server._loop = MagicMock()

        with patch("core.server.asyncio.run_coroutine_threadsafe") as submit:
            server.broadcast({"type": "private_event", "text": "private"})

        self.assertEqual(submit.call_count, 1)
        coroutine = submit.call_args.args[0]
        coroutine.close()

    def test_failed_pairing_attempts_lock_connection(self):
        server = WebSocketServer(MagicMock())
        server.pairing_code = "ABC123"
        websocket = MockWebSocket()

        for _ in range(MAX_PAIRING_ATTEMPTS):
            asyncio.run(
                server._handle_message(
                    websocket,
                    json.dumps({"type": "pair", "code": "WRONG0"}),
                )
            )

        self.assertEqual(websocket.sent[-1]["type"], "pair_locked")
        asyncio.run(
            server._handle_message(
                websocket,
                json.dumps({"type": "pair", "code": "ABC123"}),
            )
        )
        self.assertEqual(websocket.sent[-1]["type"], "pair_locked")
        self.assertNotIn(str(id(websocket)), server._paired_client_ids)

    def test_successful_pair_allows_message_and_records_max_device_type(self):
        orchestrator = MagicMock()
        orchestrator.process_input.return_value = "safe reply"
        server = WebSocketServer(orchestrator)
        server.pairing_code = "ABC123"
        websocket = MockWebSocket()

        async def exercise():
            await server._handle_message(
                websocket,
                json.dumps(
                    {
                        "type": "pair",
                        "code": "ABC123",
                        "device_type": "p" * 64,
                    }
                ),
            )
            await server._handle_message(
                websocket,
                json.dumps({"type": "message", "text": "hello"}),
            )

        asyncio.run(exercise())

        self.assertEqual(
            [item["type"] for item in websocket.sent],
            ["paired", "response"],
        )
        self.assertEqual(websocket.sent[-1]["text"], "safe reply")
        self.assertEqual(len(server.device_info[str(id(websocket))]["type"]), 64)
        orchestrator.process_input.assert_called_once_with("hello", source="device")

    def test_welcome_does_not_disclose_secret_and_disconnect_cleans_state(self):
        server = WebSocketServer(MagicMock())
        websocket = EmptyConnection()
        client_id = str(id(websocket))
        server._paired_client_ids.add(client_id)
        server._pair_attempts[client_id] = 1

        asyncio.run(server._handle_connection(websocket))

        self.assertEqual(
            websocket.sent,
            [
                {
                    "type": "welcome",
                    "message": "Connected to HIKARI",
                    "protocol_version": PROTOCOL_VERSION,
                }
            ],
        )
        self.assertNotIn(server.pairing_code, json.dumps(websocket.sent))
        self.assertNotIn(client_id, server._paired_client_ids)
        self.assertNotIn(client_id, server._pair_attempts)
        self.assertNotIn(client_id, server.device_info)

    def test_internal_request_errors_are_not_returned_to_client(self):
        marker = "PRIVATE_INTERNAL_FAILURE"
        orchestrator = MagicMock()
        orchestrator.process_input.side_effect = RuntimeError(marker)
        server = WebSocketServer(orchestrator)
        websocket = MockWebSocket()
        server._paired_client_ids.add(str(id(websocket)))

        asyncio.run(
            server._handle_message(
                websocket,
                json.dumps({"type": "message", "text": "hello"}),
            )
        )

        self.assertEqual(
            websocket.sent,
            [{"type": "error", "message": "Request failed"}],
        )
        self.assertNotIn(marker, json.dumps(websocket.sent))
