"""
HIKARI v2.0 - WebSocket Server
Enables phone/watch/AirPods connectivity via WebSocket + HTTP
QR code generation for easy phone pairing
"""

import os
import sys
import json
import asyncio
import threading
import html
import hmac
import secrets
from typing import Optional, Dict, Any, Set
from datetime import datetime
from http import HTTPStatus

from core.protocol import PROTOCOL_VERSION, validate_client_message
from core.voice_companion.bridge import VoiceCompanionBridge, VOICE_PROCESSING_ERROR_MESSAGE
from core.voice_companion.contract import WS_EVENT_COMPANION_PREFERENCES
from core.voice_companion.status import is_voice_companion_enabled

try:
    import websockets
    from websockets.server import serve

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

try:
    import qrcode
    import io
    import base64

    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False


MAX_PAIRING_ATTEMPTS = 5


class WebSocketServer:
    """WebSocket server for device connections"""

    def __init__(self, orchestrator, host: str = "0.0.0.0", port: int = 8765):
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self.connected_clients: Set = set()
        self._paired_client_ids: Set[str] = set()
        self._pair_attempts: Dict[str, int] = {}
        self.device_info: Dict[str, Dict] = {}
        self._server = None
        self._running = False
        self._loop = None
        self.pairing_code = self._generate_pairing_code()
        self._companion_bridges: Dict[str, VoiceCompanionBridge] = {}

    def _voice_companion_enabled(self) -> bool:
        return is_voice_companion_enabled()

    def _generate_pairing_code(self) -> str:
        """Generate a cryptographically random 6-character pairing code."""
        return secrets.token_hex(3).upper()

    def start(self):
        """Start the WebSocket server"""
        if not WEBSOCKETS_AVAILABLE:
            print("[WS] websockets not installed, skipping server")
            return

        self._running = True
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def handler(websocket, path=None):
            await self._handle_connection(websocket)

        async def process_request(path, request_headers):
            """Handle HTTP requests for web interface and QR code"""
            if path == "/qr":
                return self._serve_qr_code()
            if path == "/connect":
                return self._serve_connect_page()
            if path == "/api/status":
                return self._serve_api_status()
            return None  # Let WebSocket handle it

        start_server = serve(
            handler,
            self.host,
            self.port,
            process_request=process_request,
        )

        print(f"[WS] Server starting on {self.host}:{self.port}")
        print(f"[WS] Pairing code: {self.pairing_code}")
        print(f"[WS] Connect from phone: http://<your-ip>:{self.port}/connect")

        self._loop.run_until_complete(start_server)
        try:
            self._loop.run_forever()
        except KeyboardInterrupt:
            self.stop()

    async def _handle_connection(self, websocket):
        """Handle a new WebSocket connection"""
        client_id = id(websocket)
        self.connected_clients.add(websocket)

        # Send welcome message
        await websocket.send(
            json.dumps(
                {
                    "type": "welcome",
                    "message": "Connected to HIKARI",
                    "protocol_version": PROTOCOL_VERSION,
                }
            )
        )

        self.device_info[str(client_id)] = {
            "connected_at": datetime.now().isoformat(),
            "type": "unknown",
        }

        print(f"[WS] Client connected ({len(self.connected_clients)} total)")
        if self._voice_companion_enabled():
            self._companion_for(websocket)

        try:
            async for message in websocket:
                await self._handle_message(websocket, message)
        except Exception as e:
            print(f"[WS] Client error: {e}")
        finally:
            self.connected_clients.discard(websocket)
            client_key = str(id(websocket))
            self._paired_client_ids.discard(client_key)
            self._pair_attempts.pop(client_key, None)
            self.device_info.pop(client_key, None)
            self._companion_bridges.pop(client_key, None)
            print(f"[WS] Client disconnected ({len(self.connected_clients)} total)")

    def _companion_for(self, websocket) -> VoiceCompanionBridge:
        key = str(id(websocket))
        if key not in self._companion_bridges:

            async def send_companion(payload: Dict[str, Any]) -> None:
                await websocket.send(json.dumps(payload))

            bridge = VoiceCompanionBridge()
            bridge.set_async_send(send_companion)
            self._companion_bridges[key] = bridge
        return self._companion_bridges[key]

    async def _handle_voice_turn(self, websocket, user_input: str) -> str:
        """Voice-only companion lifecycle with awaited, ordered companion_update events."""
        bridge = self._companion_for(websocket)
        try:
            full_text = await bridge.run_voice_turn_async(
                user_input,
                lambda: self.orchestrator.process_input(user_input, source="voice_remote"),
            )
        except Exception:
            await bridge.emit_voice_processing_failure_async()
            safe_text = VOICE_PROCESSING_ERROR_MESSAGE
            await websocket.send(json.dumps({"type": "response", "text": safe_text}))
            return safe_text
        await websocket.send(json.dumps({"type": "response", "text": full_text}))
        await bridge.finish_voice_turn_async()
        return full_text

    async def _handle_message(self, websocket, message: str):
        """Process incoming message from client"""
        try:
            data = json.loads(message)
            if not isinstance(data, dict):
                await websocket.send(
                    json.dumps({"type": "error", "message": "Invalid message payload"})
                )
                return

            msg_type = data.get("type", "")
            client_id = str(id(websocket))

            if msg_type == "pair":
                validation_error = validate_client_message(data)
                if validation_error:
                    await websocket.send(
                        json.dumps({"type": "error", "message": validation_error})
                    )
                    return
                requested_version = data.get("protocol_version", PROTOCOL_VERSION)
                if requested_version != PROTOCOL_VERSION:
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "protocol_error",
                                "message": "Unsupported protocol version",
                                "supported_version": PROTOCOL_VERSION,
                            }
                        )
                    )
                    return
                attempts = self._pair_attempts.get(client_id, 0)
                if attempts >= MAX_PAIRING_ATTEMPTS:
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "pair_locked",
                                "message": "Too many invalid pairing attempts",
                            }
                        )
                    )
                    return

                code = str(data.get("code", ""))
                if hmac.compare_digest(code, self.pairing_code):
                    self._paired_client_ids.add(client_id)
                    self._pair_attempts.pop(client_id, None)
                    info = self.device_info.setdefault(
                        client_id,
                        {"connected_at": datetime.now().isoformat(), "type": "unknown"},
                    )
                    info["type"] = str(data.get("device_type", info["type"]))[:64]
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "paired",
                                "message": "Device paired successfully",
                                "protocol_version": PROTOCOL_VERSION,
                            }
                        )
                    )
                else:
                    attempts += 1
                    self._pair_attempts[client_id] = attempts
                    response_type = (
                        "pair_locked"
                        if attempts >= MAX_PAIRING_ATTEMPTS
                        else "pair_error"
                    )
                    message_text = (
                        "Too many invalid pairing attempts"
                        if response_type == "pair_locked"
                        else "Invalid pairing code"
                    )
                    await websocket.send(
                        json.dumps({"type": response_type, "message": message_text})
                    )
                return

            if msg_type == "ping":
                validation_error = validate_client_message(data)
                if validation_error:
                    await websocket.send(
                        json.dumps({"type": "error", "message": validation_error})
                    )
                    return
                await websocket.send(json.dumps({"type": "pong"}))
                return

            if client_id not in self._paired_client_ids:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "pairing_required",
                            "message": "Pair this connection before sending requests",
                        }
                    )
                )
                return

            validation_error = validate_client_message(data)
            if validation_error:
                await websocket.send(
                    json.dumps({"type": "error", "message": validation_error})
                )
                return

            if msg_type == "identify":
                device_type = str(data.get("device_type", "unknown"))[:64]
                self.device_info.setdefault(
                    client_id,
                    {"connected_at": datetime.now().isoformat(), "type": "unknown"},
                )["type"] = device_type
                await websocket.send(
                    json.dumps(
                        {
                            "type": "identified",
                            "device_type": device_type,
                        }
                    )
                )

            elif msg_type == "message":
                # Process user message through orchestrator
                user_input = data.get("text", "")
                if user_input:
                    response = self.orchestrator.process_input(
                        user_input, source="device"
                    )
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "response",
                                "text": response or "No response generated",
                            }
                        )
                    )

            elif msg_type == "voice":
                text = data.get("text", "")
                if text:
                    if self._voice_companion_enabled():
                        await self._handle_voice_turn(websocket, text)
                    else:
                        response = self.orchestrator.process_input(
                            text, source="voice_remote"
                        )
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "response",
                                    "text": response or "",
                                }
                            )
                        )
                elif data.get("listening") and self._voice_companion_enabled():
                    await self._companion_for(websocket).on_voice_listening_async()

            elif msg_type == WS_EVENT_COMPANION_PREFERENCES:
                if not self._voice_companion_enabled():
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "companion_preferences_error",
                                "message": "Voice companion is disabled on this server.",
                            }
                        )
                    )
                else:
                    bridge = self._companion_for(websocket)
                    try:
                        bridge.apply_preferences(
                            str(data.get("companion_type", "")),
                            str(data.get("presentation", "")),
                        )
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "companion_preferences_ack",
                                    "preferences": bridge.preferences.to_dict(),
                                }
                            )
                        )
                    except ValueError as exc:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "companion_preferences_error",
                                    "message": str(exc),
                                }
                            )
                        )

            elif msg_type == "status":
                status = self.orchestrator._get_status_report()
                await websocket.send(
                    json.dumps(
                        {
                            "type": "status",
                            "text": status,
                        }
                    )
                )

            else:
                await websocket.send(
                    json.dumps({"type": "error", "message": "Unknown message type"})
                )

        except json.JSONDecodeError:
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "message": "Invalid JSON",
                    }
                )
            )
        except Exception as e:
            print(f"[WS] Request failed: {e}")
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "message": "Request failed",
                    }
                )
            )

    def broadcast(self, message: Dict):
        """Send message to all connected clients"""
        data = json.dumps(message)
        for client in self.connected_clients.copy():
            if str(id(client)) not in self._paired_client_ids:
                continue
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send(data),
                    self._loop,
                )
            except Exception:
                pass

    def _html_response(self, body: str):
        """Serve HTML with basic hardening headers."""
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            ("X-Content-Type-Options", "nosniff"),
            (
                "Content-Security-Policy",
                "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self' ws: wss:; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            ),
        ]
        return HTTPStatus.OK, headers, body.encode()

    def _serve_qr_code(self):
        """Serve QR code image"""
        if not QR_AVAILABLE:
            return None

        import socket

        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        url = f"http://{local_ip}:{self.port}/connect"

        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        img_base64 = base64.b64encode(buffer.read()).decode()

        safe_url = html.escape(url, quote=True)

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head><title>HIKARI - QR Code</title></head>
        <body style="background:#0a0a0a;color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;font-family:system-ui;">
            <h1>Scan to connect</h1>
            <img src="data:image/png;base64,{img_base64}" alt="QR Code" />
            <p style="margin-top:20px;">Enter the pairing code shown in the local HIKARI terminal.</p>
            <p>Or open: <code>{safe_url}</code></p>
        </body>
        </html>
        """
        return self._html_response(html_body)

    def _serve_connect_page(self):
        """Serve the connection page for phones"""
        html_body = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <meta name="apple-mobile-web-app-capable" content="yes">
            <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
            <meta name="theme-color" content="#0a0a0a">
            <title>HIKARI</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { background: #0a0a0a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; height: 100vh; display: flex; flex-direction: column; }
                .header { padding: 20px; text-align: center; border-bottom: 1px solid #222; }
                .header h1 { font-size: 24px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
                .status { font-size: 12px; color: #666; margin-top: 5px; }
                .status.connected { color: #4ade80; }
                .chat { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
                .message { max-width: 85%; padding: 12px 16px; border-radius: 18px; font-size: 15px; line-height: 1.4; }
                .message.user { align-self: flex-end; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border-bottom-right-radius: 4px; }
                .message.ai { align-self: flex-start; background: #1a1a2e; border: 1px solid #333; border-bottom-left-radius: 4px; }
                .input-area { padding: 15px; border-top: 1px solid #222; display: flex; gap: 10px; }
                .input-area input { flex: 1; background: #1a1a2e; border: 1px solid #333; border-radius: 25px; padding: 12px 20px; color: white; font-size: 16px; outline: none; }
                .input-area input:focus { border-color: #667eea; }
                .input-area button { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border: none; border-radius: 25px; padding: 12px 24px; color: white; font-size: 16px; cursor: pointer; }
                .pairing { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; padding: 20px; }
                .pairing input { background: #1a1a2e; border: 1px solid #333; border-radius: 12px; padding: 15px; color: white; font-size: 24px; text-align: center; width: 200px; letter-spacing: 8px; margin: 20px 0; }
                .pairing button { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border: none; border-radius: 12px; padding: 15px 40px; color: white; font-size: 16px; cursor: pointer; }
                .hidden { display: none !important; }
                .orb { width: 60px; height: 60px; border-radius: 50%; background: radial-gradient(circle, #667eea, #764ba2); margin: 0 auto 20px; animation: pulse 2s infinite; }
                @keyframes pulse { 0%, 100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.1); opacity: 0.8; } }
                .typing { color: #666; font-size: 14px; padding: 8px 16px; align-self: flex-start; }
            </style>
        </head>
        <body>
            <div id="pairing-screen" class="pairing">
                <div class="orb"></div>
                <h2>Connect to HIKARI</h2>
                <p style="color:#666;margin-top:10px;">Enter the pairing code shown on your computer</p>
                <input type="text" id="pairing-code" placeholder="000000" maxlength="6" autocomplete="off">
                <button onclick="pair()">Connect</button>
            </div>

            <div id="chat-screen" class="hidden" style="height:100%;display:flex;flex-direction:column;">
                <div class="header">
                    <h1>HIKARI</h1>
                    <div id="connection-status" class="status">Connecting...</div>
                </div>
                <div id="chat-messages" class="chat"></div>
                <div class="input-area">
                    <input type="text" id="message-input" placeholder="Ask me anything..." autocomplete="off">
                    <button onclick="sendMessage()">Send</button>
                </div>
            </div>

            <script>
                let ws = null;
                const pairingCode = document.getElementById('pairing-code');
                const pairingScreen = document.getElementById('pairing-screen');
                const chatScreen = document.getElementById('chat-screen');
                const chatMessages = document.getElementById('chat-messages');
                const messageInput = document.getElementById('message-input');
                const statusEl = document.getElementById('connection-status');

                function connect() {
                    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                    ws = new WebSocket(protocol + '//' + window.location.host);

                    ws.onopen = () => {
                        statusEl.textContent = 'Connected';
                        statusEl.classList.add('connected');
                    };

                    ws.onmessage = (event) => {
                        const data = JSON.parse(event.data);
                        if (data.type === 'response') {
                            addMessage(data.text, 'ai');
                        }
                    };

                    ws.onclose = () => {
                        statusEl.textContent = 'Disconnected - reconnecting...';
                        statusEl.classList.remove('connected');
                        setTimeout(connect, 3000);
                    };
                }

                function pair() {
                    const code = pairingCode.value.trim();
                    if (code.length !== 6) return;

                    connect();
                    ws.onopen = () => {
                        ws.send(JSON.stringify({
                            type: 'pair',
                            code: code,
                            device_type: 'mobile',
                            protocol_version: __HIKARI_PROTOCOL_VERSION__
                        }));
                    };

                    ws.onmessage = (event) => {
                        const data = JSON.parse(event.data);
                        if (data.type === 'paired') {
                            pairingScreen.classList.add('hidden');
                            chatScreen.classList.remove('hidden');
                            chatScreen.style.display = 'flex';
                            statusEl.textContent = 'Connected';
                            statusEl.classList.add('connected');
                            addMessage('Connected! Ask me anything.', 'ai');
                        } else if (data.type === 'pair_error' || data.type === 'pair_locked') {
                            alert(data.message || 'Pairing failed.');
                        } else if (data.type === 'protocol_error') {
                            alert(data.message || 'Unsupported server protocol.');
                        } else if (data.type === 'response') {
                            addMessage(data.text, 'ai');
                        }
                    };
                }

                function sendMessage() {
                    const text = messageInput.value.trim();
                    if (!text || !ws) return;

                    addMessage(text, 'user');
                    ws.send(JSON.stringify({ type: 'message', text: text }));
                    messageInput.value = '';
                }

                function addMessage(text, type) {
                    const div = document.createElement('div');
                    div.className = 'message ' + type;
                    div.textContent = text;
                    chatMessages.appendChild(div);
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                }

                messageInput.addEventListener('keypress', (e) => {
                    if (e.key === 'Enter') sendMessage();
                });

                pairingCode.addEventListener('keypress', (e) => {
                    if (e.key === 'Enter') pair();
                });
            </script>
        </body>
        </html>
        """
        html_body = html_body.replace(
            "__HIKARI_PROTOCOL_VERSION__", str(PROTOCOL_VERSION)
        )
        return self._html_response(html_body)

    def _serve_api_status(self):
        """Serve API status as JSON"""
        status = {
            "running": self._running,
            "clients": len(self.connected_clients),
        }
        return (
            HTTPStatus.OK,
            [
                ("Content-Type", "application/json"),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ],
            json.dumps(status).encode(),
        )

    def stop(self):
        """Stop the server"""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        print("[WS] Server stopped")
