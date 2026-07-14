"""Pairing pages must not treat pairing data as raw HTML."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core.server import WebSocketServer


class TestServerPairingSafety(unittest.TestCase):
    def test_qr_page_escapes_hostile_pairing_code(self):
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
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;alert", html)
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
