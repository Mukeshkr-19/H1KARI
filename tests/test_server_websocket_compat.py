"""Compatibility checks for the supported asyncio WebSocket server API."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve as asyncio_serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from core.protocol import PROTOCOL_VERSION
from core.server import WebSocketServer, serve


def test_server_uses_supported_asyncio_factory():
    assert serve is asyncio_serve


def test_http_adapter_preserves_public_routes_and_websocket_fallthrough():
    async def exercise():
        server = WebSocketServer(MagicMock())
        server._serve_qr_code = MagicMock(
            return_value=(
                200,
                [
                    ("Content-Type", "text/html; charset=utf-8"),
                    ("Cache-Control", "no-store"),
                ],
                b"qr",
            )
        )

        for path, content_type in (
            ("/qr", "text/html; charset=utf-8"),
            ("/connect", "text/html; charset=utf-8"),
            ("/api/status", "application/json"),
        ):
            response = await server._process_request(
                None, Request(path, Headers())
            )
            assert isinstance(response, Response)
            assert response.status_code == 200
            assert response.headers["Content-Type"] == content_type
            assert response.headers["Cache-Control"] == "no-store"

        assert await server._process_request(
            None, Request("/websocket", Headers())
        ) is None

    asyncio.run(exercise())


def test_qr_route_falls_through_when_qr_support_is_unavailable():
    async def exercise():
        server = WebSocketServer(MagicMock())
        server._serve_qr_code = MagicMock(return_value=None)

        response = await server._process_request(
            None, Request("/qr", Headers())
        )

        assert response is None
        server._serve_qr_code.assert_called_once_with()

    asyncio.run(exercise())


def test_start_closes_and_awaits_server_after_stop_ends_loop():
    server = WebSocketServer(MagicMock(), host="127.0.0.1", port=0)
    live_server = MagicMock()
    live_server.wait_closed = AsyncMock()
    runner_loop = asyncio.new_event_loop()
    server_loop = MagicMock()
    server_loop.run_until_complete.side_effect = runner_loop.run_until_complete
    server_loop.is_running.return_value = True
    server_loop.run_forever.side_effect = server.stop

    async def create_server():
        server._server = live_server
        return live_server

    try:
        with patch("core.server.asyncio.new_event_loop", return_value=server_loop), patch(
            "core.server.asyncio.set_event_loop"
        ), patch.object(server, "_start_server", new=create_server):
            server.start()
    finally:
        runner_loop.close()

    server_loop.call_soon_threadsafe.assert_called_once_with(server_loop.stop)
    live_server.close.assert_called_once_with()
    live_server.wait_closed.assert_awaited_once_with()


def test_live_loopback_http_and_websocket_pairing_contracts():
    async def http_get(port: int, path: str) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Connection: close\r\n\r\n".encode("ascii")
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=3)
        writer.close()
        await writer.wait_closed()
        return response

    async def exercise():
        server = WebSocketServer(MagicMock(), host="127.0.0.1", port=0)
        live_server = await server._start_server()
        port = live_server.sockets[0].getsockname()[1]

        try:
            response = await http_get(port, "/api/status")
            headers, body = response.split(b"\r\n\r\n", 1)
            assert b" 200 OK\r\n" in headers
            assert b"Cache-Control: no-store\r\n" in headers
            assert json.loads(body) == {"running": False, "clients": 0}

            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                welcome = json.loads(await websocket.recv())
                assert welcome == {
                    "type": "welcome",
                    "message": "Connected to HIKARI",
                    "protocol_version": PROTOCOL_VERSION,
                }
                assert server._is_loopback(websocket)

                await websocket.send(
                    json.dumps(
                        {
                            "type": "pair",
                            "code": server.pairing_code,
                            "device_type": "test-client",
                            "protocol_version": PROTOCOL_VERSION,
                        }
                    )
                )
                paired = json.loads(await websocket.recv())
                assert paired["type"] == "paired"
                assert "code" not in paired
        finally:
            live_server.close()
            await live_server.wait_closed()

    asyncio.run(exercise())
