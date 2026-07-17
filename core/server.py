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
import ipaddress
import secrets
from typing import Optional, Dict, Any, Set
from datetime import datetime
from http import HTTPStatus

from core.protocol import PROTOCOL_VERSION, validate_client_message
from core.voice_companion.bridge import VoiceCompanionBridge, VOICE_PROCESSING_ERROR_MESSAGE
from core.voice_companion.contract import WS_EVENT_COMPANION_PREFERENCES
from core.voice_companion.status import is_voice_companion_enabled

try:
    from websockets.asyncio.server import serve
    from websockets.datastructures import Headers
    from websockets.http11 import Response

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

    def __init__(
        self,
        orchestrator,
        host: str = "0.0.0.0",
        port: int = 8765,
        *,
        phase1_runtime=None,
    ):
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
        self._phase1_runtime = phase1_runtime
        self._phase1_runtime_lock = asyncio.Lock()
        self._owner_sessions: Dict[str, tuple] = {}
        self._connection_tokens: Dict[str, str] = {}
        self._document_jobs: Dict[tuple[str, str], asyncio.Task] = {}
        self._document_selections: Dict[str, tuple[str, tuple[str, ...]]] = {}
        self._document_task_roots: Dict[str, str] = {}

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

        print(f"[WS] Server starting on {self.host}:{self.port}")
        print(f"[WS] Pairing code: {self.pairing_code}")
        print(f"[WS] Connect from phone: http://<your-ip>:{self.port}/connect")

        self._loop.run_until_complete(self._start_server())
        try:
            self._loop.run_forever()
        except KeyboardInterrupt:
            self.stop()
        finally:
            if self._server is not None:
                self._server.close()
                self._loop.run_until_complete(self._server.wait_closed())

    async def _start_server(self):
        """Create the asyncio WebSocket server on the active event loop."""
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
            process_request=self._process_request,
        )
        return self._server

    async def _process_request(self, _connection, request):
        """Translate HIKARI's HTTP helpers to the asyncio server response."""
        route = {
            "/qr": self._serve_qr_code,
            "/connect": self._serve_connect_page,
            "/api/status": self._serve_api_status,
        }.get(request.path)
        if route is None:
            return None

        route_response = route()
        if route_response is None:
            return None

        status, headers, body = route_response
        status_code = int(status)
        return Response(
            status_code,
            HTTPStatus(status_code).phrase,
            Headers(headers),
            body,
        )

    async def _handle_connection(self, websocket):
        """Handle a new WebSocket connection"""
        client_id = id(websocket)
        client_key = str(client_id)
        self.connected_clients.add(websocket)
        self._connection_tokens[client_key] = secrets.token_hex(16)

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
            for key, job in list(self._document_jobs.items()):
                if key[0] == client_key:
                    job.cancel()
                    self._document_jobs.pop(key, None)
            self._connection_tokens.pop(client_key, None)
            self._paired_client_ids.discard(client_key)
            self._pair_attempts.pop(client_key, None)
            self.device_info.pop(client_key, None)
            self._companion_bridges.pop(client_key, None)
            self._owner_sessions.pop(client_key, None)
            print(f"[WS] Client disconnected ({len(self.connected_clients)} total)")

    @staticmethod
    def _is_loopback(websocket) -> bool:
        remote = getattr(websocket, "remote_address", None)
        host = remote[0] if isinstance(remote, (tuple, list)) and remote else remote
        try:
            address = ipaddress.ip_address(str(host))
            mapped = getattr(address, "ipv4_mapped", None)
            return address.is_loopback or bool(mapped and mapped.is_loopback)
        except ValueError:
            return False

    async def _document_runtime_and_contexts(self, websocket):
        """Create owner identity only from the transport peer, never client JSON."""
        if not self._is_loopback(websocket):
            return None, None, None
        if self._phase1_runtime is None:
            async with self._phase1_runtime_lock:
                if self._phase1_runtime is None:
                    from core.phase1_runtime import create_phase1_runtime

                    self._phase1_runtime = await asyncio.to_thread(create_phase1_runtime)
        key = str(id(websocket))
        if key not in self._owner_sessions:
            from core.phase1_runtime import owner_contexts

            self._owner_sessions[key] = owner_contexts(
                session_id=secrets.token_hex(16), source="websocket"
            )
        actor, context = self._owner_sessions[key]
        return self._phase1_runtime, actor, context

    @staticmethod
    def _document_providers(data: Dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            value
            for value in (data.get("provider"), data.get("fallback_provider"))
            if value
        )

    async def _send_document_result(self, websocket, result, root_task_id: str) -> None:
        if result.explanation is not None:
            payload = {
                "type": "document_explanation",
                "task_id": result.task_id or "",
                "root_task_id": root_task_id,
                "text": result.explanation,
                "provider": result.provider or "saved",
            }
        elif result.error_code:
            message = {
                "actor_not_authorized": "Owner authorization is required; reconnect locally and try again.",
                "invalid_path": "Choose an existing regular .txt file with no symlinks.",
                "unsupported_type": "Choose a UTF-8 .txt file.",
                "too_large": "Choose a text file no larger than 100 KB.",
                "invalid_utf8": "Save the document as UTF-8 text and try again.",
                "invalid_destinations": "Review and select an available provider again.",
                "invalid_question": "Enter a non-empty follow-up question and try again.",
                "invalid_task_state": "Refresh the task status before trying that action again.",
                "task_not_found": "Reconnect using the original task ID.",
                "task_cancelled": "The document task was cancelled; prepare it again to restart.",
                "task_conflict": "The task changed; refresh its status and try again.",
            }.get(result.error_code, "Try again or choose another approved provider.")
            payload = {
                "type": "document_error",
                "task_id": result.task_id or "",
                "root_task_id": root_task_id,
                "code": result.error_code,
                "message": message,
            }
        else:
            payload = {
                "type": "task_update",
                "task_id": result.task_id or "",
                "root_task_id": root_task_id,
                "status": result.status,
                "progress": 0,
                "checkpoint": result.status,
            }
        await websocket.send(json.dumps(payload))

    async def _start_document_job(
        self,
        websocket,
        runtime,
        actor,
        context,
        root_task_id: str,
        operation,
        *args,
        job_task_id: Optional[str] = None,
    ) -> None:
        client_key = str(id(websocket))
        key = (client_key, root_task_id)
        if key in self._document_jobs:
            await websocket.send(json.dumps({
                "type": "document_error",
                "task_id": root_task_id,
                "root_task_id": root_task_id,
                "code": "task_conflict",
                "message": "Document request could not be completed.",
            }))
            return
        token = self._connection_tokens.setdefault(client_key, secrets.token_hex(16))

        async def run() -> None:
            response_task_id = job_task_id or root_task_id
            try:
                await asyncio.sleep(0)
                result = await asyncio.to_thread(
                    operation, *args, actor=actor, context=context
                )
                if result.task_id:
                    response_task_id = result.task_id
                    self._document_task_roots[result.task_id] = root_task_id
                latest = await asyncio.to_thread(
                    runtime.documents.reconnect,
                    root_task_id,
                    actor=actor,
                    context=context,
                )
                if latest.status == "cancelled":
                    return
                if self._connection_tokens.get(client_key) != token:
                    return
                await self._send_document_result(websocket, result, root_task_id)
            except asyncio.CancelledError:
                return
            except Exception:
                if self._connection_tokens.get(client_key) == token:
                    await websocket.send(json.dumps({
                        "type": "document_error",
                        "task_id": response_task_id,
                        "root_task_id": root_task_id,
                        "code": "request_failed",
                        "message": "Document request could not be completed.",
                    }))

        job = asyncio.create_task(run())
        self._document_jobs[key] = job
        if job_task_id and job_task_id != root_task_id:
            self._document_jobs[(client_key, job_task_id)] = job

        def clean(done) -> None:
            for job_key, current in list(self._document_jobs.items()):
                if current is done:
                    self._document_jobs.pop(job_key, None)

        job.add_done_callback(clean)

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

            elif msg_type in {
                "document_prepare",
                "document_confirm",
                "document_follow_up",
                "document_cancel",
                "task_status",
            }:
                runtime, actor, context = await self._document_runtime_and_contexts(websocket)
                task_id = str(data.get("task_id", ""))
                if runtime is None:
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "document_error",
                                "task_id": task_id,
                                "root_task_id": task_id,
                                "code": "actor_not_authorized",
                                "message": "Document access is available only on this computer.",
                            }
                        )
                    )
                elif msg_type == "document_prepare":
                    result = await asyncio.to_thread(
                        runtime.documents.prepare,
                        data["path"],
                        actor=actor,
                        context=context,
                    )
                    if result.error_code:
                        await self._send_document_result(
                            websocket, result, result.task_id or ""
                        )
                    else:
                        providers = self._document_providers(data)
                        self._document_selections[result.task_id] = (
                            data["path"], providers
                        )
                        self._document_task_roots[result.task_id] = result.task_id
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "document_confirmation_required",
                                    "task_id": result.task_id,
                                    "path": data["path"],
                                    "provider": data["provider"],
                                    "fallback_provider": providers[1] if len(providers) > 1 else "",
                                }
                            )
                        )
                elif msg_type == "document_confirm":
                    selection = self._document_selections.get(task_id)
                    providers = self._document_providers(data)
                    if selection is None or providers != selection[1]:
                        await websocket.send(json.dumps({
                            "type": "document_error",
                            "task_id": task_id,
                            "root_task_id": task_id,
                            "code": "destination_mismatch",
                            "message": "Document request could not be completed.",
                        }))
                    else:
                        await self._start_document_job(
                            websocket,
                            runtime,
                            actor,
                            context,
                            task_id,
                            runtime.documents.confirm_and_explain,
                            task_id,
                            selection[1],
                        )
                elif msg_type == "document_follow_up":
                    root_task_id = self._document_task_roots.get(task_id, task_id)
                    selection = self._document_selections.get(root_task_id)
                    providers = self._document_providers(data)
                    if selection is None or providers != selection[1]:
                        await websocket.send(json.dumps({
                            "type": "document_error",
                            "task_id": task_id,
                            "root_task_id": root_task_id,
                            "code": "destination_mismatch",
                            "message": "Document request could not be completed.",
                        }))
                    else:
                        prepared = await asyncio.to_thread(
                            runtime.documents.prepare_follow_up,
                            root_task_id,
                            data["text"],
                            actor=actor,
                            context=context,
                        )
                        if prepared.error_code or not prepared.task_id:
                            await self._send_document_result(
                                websocket, prepared, root_task_id
                            )
                        else:
                            child_task_id = prepared.task_id
                            self._document_task_roots[child_task_id] = root_task_id
                            await websocket.send(json.dumps({
                                "type": "task_update",
                                "task_id": child_task_id,
                                "root_task_id": root_task_id,
                                "status": prepared.status,
                                "progress": 0,
                                "checkpoint": prepared.status,
                            }))
                            await self._start_document_job(
                                websocket,
                                runtime,
                                actor,
                                context,
                                root_task_id,
                                runtime.documents.execute_follow_up,
                                child_task_id,
                                selection[1],
                                job_task_id=child_task_id,
                            )
                elif msg_type == "document_cancel":
                    root_task_id = self._document_task_roots.get(task_id, task_id)
                    result = await asyncio.to_thread(
                        runtime.documents.cancel,
                        task_id,
                        actor=actor,
                        context=context,
                    )
                    await self._send_document_result(websocket, result, root_task_id)
                    if result.status == "cancelled":
                        job = self._document_jobs.pop((client_id, task_id), None)
                        if job:
                            for job_key, current in list(self._document_jobs.items()):
                                if current is job:
                                    self._document_jobs.pop(job_key, None)
                            job.cancel()
                else:
                    root_task_id = self._document_task_roots.get(task_id, task_id)
                    result = await asyncio.to_thread(
                        runtime.documents.reconnect,
                        task_id,
                        actor=actor,
                        context=context,
                    )
                    if result.error_code:
                        await self._send_document_result(websocket, result, root_task_id)
                    elif result.explanation is not None:
                        await self._send_document_result(websocket, result, root_task_id)
                    else:
                        task = await asyncio.to_thread(
                            runtime.tasks.get_task, task_id, context=context
                        )
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "task_update",
                                    "task_id": task_id,
                                    "root_task_id": root_task_id,
                                    "status": task.status.value,
                                    "progress": task.progress,
                                    "checkpoint": task.checkpoint,
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
