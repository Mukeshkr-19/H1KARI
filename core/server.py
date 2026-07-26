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
import re
import secrets
import time
from typing import Optional, Dict, Any, Set
from datetime import datetime
from http import HTTPStatus

from core.protocol import (
    PROTOCOL_VERSION,
    validate_client_message,
    validate_server_message,
)
from core.request_context import ActorSource, RequestContext, derive_actor_from_transport
from core.voice_companion.bridge import VoiceCompanionBridge, VOICE_PROCESSING_ERROR_MESSAGE
from core.voice_companion.contract import WS_EVENT_COMPANION_PREFERENCES
from core.voice_companion.status import is_voice_companion_enabled

from core.productivity import (
    ApprovalScope,
    CalendarDraftProposalFactory,
    CalendarPreparationRegistry,
    CalendarReadProposalFactory,
    ConfirmationResult,
    EmailDraftPreparationRegistry,
    EmailDraftProposalFactory,
    ProductivityRuntime,
    ReminderPreparationRegistry,
    ReminderProposalFactory,
    ResearchPreparationRegistry,
    ResearchProposalFactory,
    error_message,
    update_message,
)
from core.productivity.dispatch import (
    build_execution_request,
    build_scheduled_adapter_input,
)
from core.productivity.execution import (
    ExecutionTicket,
    ProductivityExecutionCoordinator,
)
from core.productivity.action_results import BrowserSearchResult, CalendarReadResult
from core.productivity.transport import (
    calendar_result_message,
    research_result_message,
)
from core.productivity.service import ProductivityCode
from core.jobs.runtime import ScheduledJobRuntime
from core.jobs.bootstrap import ScheduledJobSubsystem
from core.jobs.coordinator import (
    ScheduledReadScheduleCode,
    StableOwnerScope,
)
from core.jobs.quiet_hours import QuietHours
from core.jobs.delivery import DeliveryAttemptResult, DeliveryAttemptStatus
from core.jobs.delivery_runtime import (
    DeliveryRuntimeCode,
    MeaningfulChangeDeliveryRuntime,
)
from core.jobs.runner import ExecutionResult, ExecutionStatus
from core.handoff import HandoffTransportAdapter
from core.pairing import PairingRuntime
from core.visual_transfer import VisualTransferRuntime
from core.vision import VisionRuntime

from core.action_policy import Actor
from core.phase5.bootstrap import Phase5BootstrapError, Phase5Subsystem, create_phase5_subsystem
from core.phase5.contracts import (
    Capability,
    Outcome,
    Phase5Actor,
    Phase5ActorContext,
    ScopeConstraint,
)
from core.phase5.capability_service import CapabilityExecutionRequest
from core.phase5.runtime_guard import Phase5RuntimeContext, Phase5RuntimeRequest
from core.phase5.session_lifecycle import (
    AuthoritySource,
    SessionActivationRequest,
    SessionAuthoritySnapshot,
    SessionState,
    SessionType,
)
from core.phase5.transport import (
    PHASE5_CLIENT_MESSAGE_TYPES,
    build_approval_required_message,
    build_capability_proposal_message,
    build_helper_grants_message,
    build_phase5_error,
    outcome_to_error_code,
    session_to_update_message,
)
from core.phase6_transport import (
    PHASE6_CLIENT_MESSAGE_TYPES,
    Phase6CorrelationTracker,
    Phase6ErrorCode,
    build_agent_run_frame,
    build_encrypted_sync_frame,
    build_error_frame,
    build_home_assistant_proposal_frame,
    build_integration_status_frame,
    build_model_eval_frame,
    build_remote_worker_frame,
    build_repo_intel_frame,
    build_skill_evolution_frame,
    build_time_sense_frame,
)


_PHASE5_PENDING_APPROVAL_TTL_SECONDS = 300.0
_PHASE5_MAX_PENDING_APPROVALS_PER_CONNECTION = 32


class _EmptyPreparationRegistry:
    """No-op registry stand-in when a prepare registry was not injected."""

    def get(self, actor, proposal_id):
        return None

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
PAIRING_ATTEMPT_WINDOW_SECONDS = 300
_PREPARE_REQUEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


def _reject_json_constant(_value: str) -> None:
    """Reject non-standard JSON constants without reflecting their value."""
    raise ValueError("invalid JSON constant")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    """Build one JSON object while rejecting ambiguous duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_client_json(message: str) -> object:
    """Parse strict RFC-compatible JSON for the public WebSocket boundary."""
    return json.loads(
        message,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_unique_json_object,
    )


class WebSocketServer:
    """WebSocket server for device connections"""

    def __init__(
        self,
        orchestrator,
        host: str = "0.0.0.0",
        port: int = 8765,
        *,
        phase1_runtime=None,
        productivity_runtime: Optional[ProductivityRuntime] = None,
        scheduled_job_runtime: Optional[ScheduledJobRuntime] = None,
        scheduled_job_subsystem: Optional[ScheduledJobSubsystem] = None,
        email_draft_factory: Optional[EmailDraftProposalFactory] = None,
        email_draft_registry: Optional[EmailDraftPreparationRegistry] = None,
        calendar_read_factory: Optional[CalendarReadProposalFactory] = None,
        calendar_draft_factory: Optional[CalendarDraftProposalFactory] = None,
        calendar_registry: Optional[CalendarPreparationRegistry] = None,
        research_factory: Optional[ResearchProposalFactory] = None,
        research_registry: Optional[ResearchPreparationRegistry] = None,
        reminder_factory: Optional[ReminderProposalFactory] = None,
        reminder_registry: Optional[ReminderPreparationRegistry] = None,
        productivity_execution_coordinator: Optional[
            ProductivityExecutionCoordinator
        ] = None,
        pairing_runtime: Optional[PairingRuntime] = None,
        handoff_transport: Optional[HandoffTransportAdapter] = None,
        visual_transfer_runtime: Optional[VisualTransferRuntime] = None,
        vision_runtime: Optional[VisionRuntime] = None,
        phase5_subsystem: Optional[Phase5Subsystem] = None,
        phase6_subsystem: Any = None,
    ):
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self.connected_clients: Set = set()
        self._paired_client_ids: Set[str] = set()
        self._pair_attempts: Dict[str, int | tuple[int, float]] = {}
        self._pair_clock = time.monotonic
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
        self._productivity_runtime = productivity_runtime
        self._scheduled_job_runtime = scheduled_job_runtime
        self._scheduled_job_subsystem = scheduled_job_subsystem
        self._email_draft_factory = email_draft_factory
        self._email_draft_registry = email_draft_registry
        self._calendar_read_factory = calendar_read_factory
        self._calendar_draft_factory = calendar_draft_factory
        self._calendar_registry = calendar_registry
        self._research_factory = research_factory
        self._research_registry = research_registry
        self._reminder_factory = reminder_factory
        self._reminder_registry = reminder_registry
        self._productivity_execution_coordinator = productivity_execution_coordinator
        self._pairing_runtime = pairing_runtime
        self._handoff_transport = handoff_transport
        self._visual_transfer_runtime = visual_transfer_runtime
        self._vision_runtime = vision_runtime
        self._phase4_device_ids: Dict[str, str] = {}
        self._phase4_challenges: Dict[str, tuple[str, str]] = {}
        self._phase4_handoff_origins: Dict[str, str] = {}
        self._phase4_pending_transfers: Dict[str, tuple[str, str]] = {}
        self._phase4_transfer_analyses: Dict[
            str, tuple[str, str, str, str]
        ] = {}
        self._vision_jobs: Dict[tuple[str, str], asyncio.Task] = {}
        self._scheduled_runner_task: asyncio.Task | None = None
        self._phase4_sweeper_task: asyncio.Task | None = None
        self._scheduled_runner = None
        self._phase5_subsystem: Phase5Subsystem | None = phase5_subsystem
        self._phase5_bootstrap_failed = False
        self._phase5_pending_approvals: Dict[str, Dict[str, dict]] = {}
        self._phase6_subsystem = phase6_subsystem
        self._phase6_pending_proposals: Dict[str, Dict[str, dict]] = {}
        self._phase6_trackers: Dict[str, Phase6CorrelationTracker] = {}

    def _voice_companion_enabled(self) -> bool:
        return is_voice_companion_enabled()

    def _generate_pairing_code(self) -> str:
        """Generate a cryptographically random 10-character pairing code."""
        return secrets.token_hex(5).upper()

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
            if self._scheduled_runner_task is not None:
                self._scheduled_runner_task.cancel()
                self._loop.run_until_complete(
                    asyncio.gather(
                        self._scheduled_runner_task,
                        return_exceptions=True,
                    )
                )
                self._scheduled_runner_task = None
            if self._phase4_sweeper_task is not None:
                self._phase4_sweeper_task.cancel()
                self._loop.run_until_complete(
                    asyncio.gather(
                        self._phase4_sweeper_task,
                        return_exceptions=True,
                    )
                )
                self._phase4_sweeper_task = None
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
        if self._scheduled_job_subsystem is not None:
            try:
                self._scheduled_runner = self._scheduled_job_subsystem.create_runner(
                    self._execute_scheduled_read
                )
                await asyncio.to_thread(self._scheduled_runner.recover_startup)
                self._scheduled_runner_task = asyncio.create_task(
                    self._scheduled_runner_loop()
                )
            except Exception:
                self._scheduled_runner = None
                self._scheduled_runner_task = None
        if any(
            runtime is not None
            for runtime in (
                self._pairing_runtime,
                self._handoff_transport,
                self._visual_transfer_runtime,
                self._vision_runtime,
            )
        ):
            self._phase4_sweeper_task = asyncio.create_task(
                self._phase4_sweeper_loop()
            )
        return self._server

    async def _expire_phase4_state(self) -> None:
        """Expire bounded Phase 4 state without coupling subsystem failures."""
        runtimes = (
            self._pairing_runtime,
            self._handoff_transport,
            self._visual_transfer_runtime,
            self._vision_runtime,
        )
        for runtime in runtimes:
            expire_due = getattr(runtime, "expire_due", None)
            if not callable(expire_due):
                continue
            try:
                await asyncio.to_thread(expire_due)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    async def _phase4_sweeper_loop(self) -> None:
        """Run periodic expiry while the WebSocket server is active."""
        while self._running:
            await self._expire_phase4_state()
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return

    async def _scheduled_runner_loop(self) -> None:
        """Run bounded scheduled reads while the server event loop is active."""
        while self._running and self._scheduled_runner is not None:
            try:
                outcomes = await asyncio.to_thread(
                    self._scheduled_runner.run_once, limit=8
                )
                if outcomes:
                    await self._broadcast_scheduled_jobs()
                if self._scheduled_job_subsystem is not None:
                    await asyncio.to_thread(
                        self._scheduled_job_subsystem.action_store.purge_expired,
                        self._scheduled_job_subsystem.clock(),
                        limit=8,
                    )
            except asyncio.CancelledError:
                return
            except Exception:
                pass
            await asyncio.sleep(1)

    async def _broadcast_scheduled_jobs(self) -> None:
        """Refresh scheduled-job state for connected local owner clients."""
        runtime = self._scheduled_job_runtime
        if runtime is None:
            return
        for websocket in tuple(self.connected_clients):
            client_key = str(id(websocket))
            if client_key not in self._paired_client_ids or not self._is_loopback(websocket):
                continue
            try:
                actor = self._derive_actor_context(websocket).actor_context
                message = await asyncio.to_thread(runtime.list_jobs, actor)
                await self._send_scheduled_job_message(
                    websocket, message, "scheduled-jobs"
                )
            except Exception:
                continue

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

    def _derive_actor_context(self, websocket) -> RequestContext:
        """Derive an immutable request context from server-observed transport state only."""
        client_key = str(id(websocket))
        connection_token = self._connection_tokens.get(client_key) or secrets.token_hex(16)
        if client_key not in self._connection_tokens:
            self._connection_tokens[client_key] = connection_token
        is_paired = client_key in self._paired_client_ids
        return derive_actor_from_transport(
            source=ActorSource.WEBSOCKET,
            connection_token=connection_token,
            is_loopback=self._is_loopback(websocket),
            is_paired=is_paired,
            session_id=connection_token,
        )

    @staticmethod
    def _pair_rate_key(websocket) -> str:
        """Return a reconnect-stable source key for legacy pairing attempts."""
        remote = getattr(websocket, "remote_address", None)
        host = remote[0] if isinstance(remote, (tuple, list)) and remote else remote
        try:
            address = ipaddress.ip_address(str(host))
            mapped = getattr(address, "ipv4_mapped", None)
            if mapped is not None:
                address = mapped
            return f"source:{address.compressed}"
        except ValueError:
            return "source:unknown"

    def _pair_attempt_count(self, rate_key: str) -> int:
        """Return attempts in the active window, expiring stale lockouts."""
        now = self._pair_clock()
        entry = self._pair_attempts.get(rate_key)
        if isinstance(entry, tuple):
            attempts, started_at = entry
            if now - started_at < PAIRING_ATTEMPT_WINDOW_SECONDS:
                return attempts
            self._pair_attempts.pop(rate_key, None)
            return 0
        if isinstance(entry, int):
            return entry
        return 0

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
                if isinstance(message, bytes):
                    await self._handle_visual_binary(websocket, message)
                else:
                    await self._handle_message(websocket, message)
        except Exception:
            print("[WS] Client error")
        finally:
            try:
                cleanup_actor = self._derive_actor_context(websocket).actor_context
            except Exception:
                cleanup_actor = None
            if self._email_draft_registry is not None:
                try:
                    actor = self._derive_actor_context(websocket).actor_context
                    self._email_draft_registry.clear_session(
                        actor.actor_id, actor.session_id
                    )
                except Exception:
                    pass
            if self._calendar_registry is not None:
                try:
                    actor = self._derive_actor_context(websocket).actor_context
                    self._calendar_registry.clear_session(
                        actor.actor_id, actor.session_id
                    )
                except Exception:
                    pass
            if self._research_registry is not None:
                try:
                    actor = self._derive_actor_context(websocket).actor_context
                    self._research_registry.clear_session(
                        actor.actor_id, actor.session_id
                    )
                except Exception:
                    pass
            if self._reminder_registry is not None:
                try:
                    actor = self._derive_actor_context(websocket).actor_context
                    self._reminder_registry.clear_session(
                        actor.actor_id, actor.session_id
                    )
                except Exception:
                    pass
            if self._visual_transfer_runtime is not None:
                try:
                    actor = self._derive_actor_context(websocket).actor_context
                    self._visual_transfer_runtime.clear_session(actor.session_id)
                except Exception:
                    pass
            self.connected_clients.discard(websocket)
            client_key = str(id(websocket))
            for key, job in list(self._document_jobs.items()):
                if key[0] == client_key:
                    job.cancel()
                    self._document_jobs.pop(key, None)
            self._connection_tokens.pop(client_key, None)
            self._paired_client_ids.discard(client_key)
            self._phase5_pending_approvals.pop(client_key, None)
            self._phase6_pending_proposals.pop(client_key, None)
            self._phase6_trackers.pop(client_key, None)
            self._pair_attempts.pop(client_key, None)
            self.device_info.pop(client_key, None)
            self._companion_bridges.pop(client_key, None)
            self._owner_sessions.pop(client_key, None)
            device_id = self._phase4_device_ids.pop(client_key, None)
            if device_id is not None and self._pairing_runtime is not None:
                try:
                    await asyncio.to_thread(
                        self._pairing_runtime.disconnect,
                        device_id,
                    )
                except Exception:
                    pass
            challenge = self._phase4_challenges.pop(client_key, None)
            if challenge is not None and self._pairing_runtime is not None:
                try:
                    await asyncio.to_thread(
                        self._pairing_runtime.cancel,
                        challenge[0],
                        challenge[1],
                    )
                except Exception:
                    pass
            self._phase4_pending_transfers.pop(client_key, None)
            self._phase4_transfer_analyses.pop(client_key, None)
            for key, job in list(self._vision_jobs.items()):
                if key[0] == client_key:
                    if self._vision_runtime is not None:
                        try:
                            if cleanup_actor is not None:
                                await asyncio.to_thread(
                                    self._vision_runtime.cancel_bound,
                                    cleanup_actor,
                                    key[1],
                                )
                        except Exception:
                            pass
                    job.cancel()
                    self._vision_jobs.pop(key, None)
            if self._vision_runtime is not None:
                try:
                    if cleanup_actor is not None:
                        self._vision_runtime.clear_session(cleanup_actor)
                except Exception:
                    pass
            for handoff_id, origin_key in list(self._phase4_handoff_origins.items()):
                if origin_key == client_key:
                    self._phase4_handoff_origins.pop(handoff_id, None)
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

    async def _handle_voice_turn(self, websocket, user_input: str, context: RequestContext) -> str:
        """Voice-only companion lifecycle with awaited, ordered companion_update events."""
        bridge = self._companion_for(websocket)
        try:
            full_text = await bridge.run_voice_turn_async(
                user_input,
                lambda: self.orchestrator.process_input(
                    user_input,
                    source="voice_remote",
                    context=context.actor_context,
                ),
            )
        except Exception:
            await bridge.emit_voice_processing_failure_async()
            safe_text = VOICE_PROCESSING_ERROR_MESSAGE
            await websocket.send(json.dumps({"type": "response", "text": safe_text}))
            return safe_text
        await websocket.send(json.dumps({"type": "response", "text": full_text}))
        await bridge.finish_voice_turn_async()
        return full_text

    async def _send_phase4_message(self, websocket, message: dict) -> None:
        """Send one canonical Phase 4 message or a bounded generic failure."""
        if not isinstance(message, dict) or validate_server_message(message) is not None:
            await websocket.send(
                json.dumps({"type": "error", "message": "Server request failed"})
            )
            return
        await websocket.send(json.dumps(message))

    async def _handle_pairing_control(self, websocket, data: dict) -> None:
        runtime = self._pairing_runtime
        request_id = data.get("request_id", "invalid-request")
        if runtime is None:
            await self._send_phase4_message(
                websocket,
                {
                    "type": "pairing_error",
                    "request_id": request_id,
                    "code": "unavailable",
                },
            )
            return
        msg_type = data["type"]
        client_key = str(id(websocket))
        pending_challenge = self._phase4_challenges.get(client_key)
        if (
            msg_type == "pairing_prepare"
            and pending_challenge is not None
            and pending_challenge[0] != request_id
        ):
            await self._send_phase4_message(
                websocket,
                {
                    "type": "pairing_error",
                    "request_id": request_id,
                    "code": "rate_limited",
                },
            )
            return
        actor = self._derive_actor_context(websocket).actor_context
        try:
            if msg_type == "pairing_prepare":
                result = await asyncio.to_thread(runtime.prepare, request_id)
            elif msg_type == "pairing_confirm":
                result = await asyncio.to_thread(
                    runtime.confirm,
                    request_id,
                    data["challenge_id"],
                    data["code"],
                )
            elif msg_type == "pairing_cancel":
                result = await asyncio.to_thread(
                    runtime.cancel,
                    request_id,
                    data["challenge_id"],
                )
            else:
                result = await asyncio.to_thread(
                    runtime.revoke,
                    actor,
                    request_id,
                    data["device_id"],
                )
        except Exception:
            result = {
                "type": "pairing_error",
                "request_id": request_id,
                "code": "unavailable",
            }

        if result.get("type") == "pairing_challenge":
            self._phase4_challenges[client_key] = (
                result["request_id"],
                result["challenge_id"],
            )
        elif result.get("type") == "pairing_confirmed":
            self._paired_client_ids.add(client_key)
            self._pair_attempts.pop(client_key, None)
            self._phase4_device_ids[client_key] = result["device_id"]
            self._phase4_challenges.pop(client_key, None)
        elif result.get("type") == "pairing_update" and result.get("status") == "cancelled":
            self._phase4_challenges.pop(client_key, None)
        elif result.get("type") == "pairing_update" and result.get("status") == "revoked":
            revoked_id = result.get("device_id")
            for client_key, device_id in list(self._phase4_device_ids.items()):
                if device_id == revoked_id:
                    self._phase4_device_ids.pop(client_key, None)
                    self._paired_client_ids.discard(client_key)
        await self._send_phase4_message(websocket, result)

    async def _send_handoff_result(self, websocket, data: dict, result: dict) -> None:
        """Route handoff results to the caller and the exact known counterpart."""
        await self._send_phase4_message(websocket, result)
        msg_type = result.get("type")
        handoff_id = result.get("handoff_id")
        caller_key = str(id(websocket))
        if msg_type == "handoff_offer" and isinstance(handoff_id, str):
            self._phase4_handoff_origins[handoff_id] = caller_key
            for candidate in tuple(self.connected_clients):
                candidate_key = str(id(candidate))
                if (
                    candidate is websocket
                    or candidate_key not in self._paired_client_ids
                    or not self._is_loopback(candidate)
                ):
                    continue
                try:
                    await self._send_phase4_message(candidate, result)
                except Exception:
                    continue
            return
        if not isinstance(handoff_id, str):
            return
        origin_key = self._phase4_handoff_origins.get(handoff_id)
        if origin_key is not None and origin_key != caller_key:
            for candidate in tuple(self.connected_clients):
                if str(id(candidate)) == origin_key:
                    try:
                        await self._send_phase4_message(candidate, result)
                    except Exception:
                        pass
                    break
        if result.get("status") in {"accepted", "rejected", "cancelled", "expired"}:
            self._phase4_handoff_origins.pop(handoff_id, None)

    async def _handle_handoff_control(self, websocket, data: dict) -> None:
        adapter = self._handoff_transport
        if adapter is None:
            result = {
                "type": "handoff_error",
                "request_id": data.get("request_id", "invalid-request"),
                "code": "unavailable",
            }
        else:
            actor = self._derive_actor_context(websocket).actor_context
            try:
                result = await asyncio.to_thread(adapter.dispatch, actor, data)
            except Exception:
                result = {
                    "type": "handoff_error",
                    "request_id": data.get("request_id", "invalid-request"),
                    "code": "unavailable",
                }
        await self._send_handoff_result(websocket, data, result)

    async def _handle_vision_control(self, websocket, data: dict) -> None:
        runtime = self._vision_runtime
        request_id = data.get("request_id", "invalid-request")
        actor = self._derive_actor_context(websocket).actor_context
        client_key = str(id(websocket))
        if runtime is None:
            result = {
                "type": "vision_analysis_error",
                "request_id": request_id,
                "code": "unavailable",
            }
        else:
            try:
                if data["type"] == "vision_analysis_prepare":
                    result = await asyncio.to_thread(
                        runtime.prepare,
                        actor,
                        request_id,
                        data["handoff_id"],
                        data["capability"],
                        data.get("mode", "private_local"),
                    )
                elif data["type"] == "vision_analysis_cancel":
                    analysis_id = data["analysis_id"]
                    job = self._vision_jobs.pop((client_key, analysis_id), None)
                    if job is not None:
                        job.cancel()
                    result = await asyncio.to_thread(
                        runtime.cancel,
                        actor,
                        request_id,
                        analysis_id,
                    )
                else:
                    result = await asyncio.to_thread(
                        runtime.status,
                        actor,
                        request_id,
                        data["analysis_id"],
                    )
            except Exception:
                result = {
                    "type": "vision_analysis_error",
                    "request_id": request_id,
                    "code": "unavailable",
                }
        await self._send_phase4_message(websocket, result)

    async def _handle_visual_control(self, websocket, data: dict) -> None:
        runtime = self._visual_transfer_runtime
        request_id = data.get("request_id", "invalid-request")
        actor = self._derive_actor_context(websocket).actor_context
        if runtime is None:
            messages = [{
                "type": "visual_transfer_error",
                "request_id": request_id,
                "code": "unavailable",
            }]
        else:
            try:
                if data["type"] == "visual_transfer_begin":
                    if str(id(websocket)) in self._phase4_pending_transfers:
                        messages = [{
                            "type": "visual_transfer_error",
                            "request_id": request_id,
                            "code": "rate_limited",
                        }]
                    else:
                        messages = await asyncio.to_thread(
                            runtime.begin,
                            actor.session_id,
                            request_id,
                            data["handoff_id"],
                            data["mime_type"],
                            data["size_bytes"],
                            data["width"],
                            data["height"],
                            data["frame_count"],
                        )
                elif data["type"] == "visual_transfer_status":
                    messages = await asyncio.to_thread(
                        runtime.status,
                        actor.session_id,
                        request_id,
                        data["transfer_id"],
                    )
                else:
                    messages = await asyncio.to_thread(
                        runtime.cancel,
                        actor.session_id,
                        request_id,
                        data["transfer_id"],
                    )
            except Exception:
                messages = [{
                    "type": "visual_transfer_error",
                    "request_id": request_id,
                    "code": "unavailable",
                }]
        for result in messages:
            if result.get("type") == "visual_transfer_ready":
                client_key = str(id(websocket))
                transfer_id = result["transfer_id"]
                analysis_id = data.get("analysis_id")
                if isinstance(analysis_id, str):
                    if self._vision_runtime is None:
                        await asyncio.to_thread(
                            runtime.cancel,
                            actor.session_id,
                            request_id,
                            transfer_id,
                        )
                        await self._send_phase4_message(
                            websocket,
                            {
                                "type": "vision_analysis_error",
                                "request_id": request_id,
                                "analysis_id": analysis_id,
                                "code": "unavailable",
                            },
                        )
                        continue
                    vision_result = await asyncio.to_thread(
                        self._vision_runtime.attach_transfer,
                        actor,
                        analysis_id,
                        data["handoff_id"],
                        transfer_id,
                    )
                    if vision_result.get("type") == "vision_analysis_error":
                        await asyncio.to_thread(
                            runtime.cancel,
                            actor.session_id,
                            request_id,
                            transfer_id,
                        )
                        await self._send_phase4_message(websocket, vision_result)
                        continue
                    self._phase4_transfer_analyses[client_key] = (
                        analysis_id,
                        data["handoff_id"],
                        transfer_id,
                        data["mime_type"],
                    )
                    await self._send_phase4_message(websocket, vision_result)
                self._phase4_pending_transfers[client_key] = (
                    request_id,
                    transfer_id,
                )
            if result.get("status") in {"cancelled", "failed"}:
                client_key = str(id(websocket))
                self._phase4_pending_transfers.pop(client_key, None)
                binding = self._phase4_transfer_analyses.pop(client_key, None)
                if binding is not None and self._vision_runtime is not None:
                    vision_result = await asyncio.to_thread(
                        self._vision_runtime.cancel_bound, actor, binding[0]
                    )
                    await self._send_phase4_message(websocket, vision_result)
            await self._send_phase4_message(websocket, result)

    async def _run_vision_analysis(
        self,
        websocket,
        actor,
        binding: tuple[str, str, str, str],
        frame: bytes,
    ) -> None:
        analysis_id, handoff_id, transfer_id, mime_type = binding
        runtime = self._vision_runtime
        if runtime is None:
            return
        try:
            results = await asyncio.to_thread(
                runtime.analyze,
                actor,
                analysis_id,
                handoff_id,
                transfer_id,
                frame,
                mime_type=mime_type,
            )
            for result in results:
                await self._send_phase4_message(websocket, result)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self._vision_jobs.pop((str(id(websocket)), analysis_id), None)

    async def _handle_visual_binary(self, websocket, frame: bytes) -> None:
        """Process one authenticated binary frame outside the JSON protocol."""
        client_key = str(id(websocket))
        if client_key not in self._paired_client_ids:
            await websocket.send(
                json.dumps({
                    "type": "pairing_required",
                    "message": "Pair this connection before sending requests",
                })
            )
            return
        pending = self._phase4_pending_transfers.get(client_key)
        runtime = self._visual_transfer_runtime
        if pending is None or runtime is None:
            await self._send_phase4_message(
                websocket,
                {
                    "type": "visual_transfer_error",
                    "request_id": "invalid-request",
                    "code": "transfer_not_found",
                },
            )
            return
        request_id, transfer_id = pending
        actor = self._derive_actor_context(websocket).actor_context
        try:
            messages = await asyncio.to_thread(
                runtime.receive_binary,
                actor.session_id,
                request_id,
                transfer_id,
                frame,
            )
        except Exception:
            messages = [{
                "type": "visual_transfer_error",
                "request_id": request_id,
                "code": "unavailable",
            }]
        for result in messages:
            await self._send_phase4_message(websocket, result)
        self._phase4_pending_transfers.pop(client_key, None)
        binding = self._phase4_transfer_analyses.pop(client_key, None)
        completed = any(
            result.get("type") == "visual_transfer_complete" for result in messages
        )
        if binding is not None and completed and self._vision_runtime is not None:
            analysis_id = binding[0]
            job = asyncio.create_task(
                self._run_vision_analysis(websocket, actor, binding, frame)
            )
            self._vision_jobs[(client_key, analysis_id)] = job
        elif binding is not None and self._vision_runtime is not None:
            vision_result = await asyncio.to_thread(
                self._vision_runtime.cancel_bound,
                actor,
                binding[0],
            )
            await self._send_phase4_message(websocket, vision_result)


    def _get_phase5_subsystem(self) -> Phase5Subsystem | None:
        """Lazy Phase 5 composition. Fail closed when bootstrap is unavailable."""
        if self._phase5_subsystem is not None:
            return self._phase5_subsystem
        if self._phase5_bootstrap_failed:
            return None
        try:
            self._phase5_subsystem = create_phase5_subsystem()
        except Phase5BootstrapError:
            self._phase5_bootstrap_failed = True
            return None
        except Exception:
            self._phase5_bootstrap_failed = True
            return None
        return self._phase5_subsystem

    async def _send_phase5_message(self, websocket, message: dict) -> None:
        error = validate_server_message(message)
        if error is not None:
            request_id = message.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                request_id = "invalid-request"
            message = build_phase5_error(request_id=request_id, code="internal_error")
            if validate_server_message(message) is not None:
                message = {
                    "type": "phase5_error",
                    "request_id": "invalid-request",
                    "protocol_version": 1,
                    "code": "internal_error",
                }
        await websocket.send(json.dumps(message))

    def _phase5_request_id(self, data: dict) -> str:
        request_id = data.get("request_id")
        if isinstance(request_id, str) and re.fullmatch(
            r"^[a-z0-9][a-z0-9_.-]{0,79}$", request_id
        ):
            return request_id
        return "invalid-request"

    def _queue_phase5_approval(
        self,
        websocket,
        request_id: str,
        pending: dict,
        now: float,
    ) -> str | None:
        """Store one bounded, expiring approval request for this connection."""
        client_key = str(id(websocket))
        pending_map = self._phase5_pending_approvals.setdefault(client_key, {})
        for key, value in tuple(pending_map.items()):
            if float(value.get("expires_at", 0.0)) <= now:
                pending_map.pop(key, None)
        if request_id in pending_map:
            return "duplicate_request"
        if len(pending_map) >= _PHASE5_MAX_PENDING_APPROVALS_PER_CONNECTION:
            return "unavailable"
        pending["expires_at"] = now + _PHASE5_PENDING_APPROVAL_TTL_SECONDS
        pending_map[request_id] = pending
        return None

    async def _handle_phase5_control(self, websocket, data: dict) -> None:
        """Route Phase 5 frames through server-derived identity and fail-closed services."""
        request_id = self._phase5_request_id(data)
        context = self._derive_actor_context(websocket)
        actor = context.actor_context

        # Guests / unpaired / non-loopback never receive Phase 5 authority.
        if actor.actor is not Actor.OWNER or not context.is_paired:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(request_id=request_id, code="unauthorized"),
            )
            return

        subsystem = self._get_phase5_subsystem()
        if subsystem is None:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(request_id=request_id, code="unavailable"),
            )
            return

        msg_type = data.get("type")
        try:
            if msg_type == "phase5_session_activate":
                await self._phase5_session_activate(websocket, data, actor, subsystem)
            elif msg_type == "phase5_session_status":
                await self._phase5_session_status(websocket, data, actor, subsystem)
            elif msg_type == "phase5_session_close":
                await self._phase5_session_transition(
                    websocket, data, actor, subsystem, SessionState.CLOSED
                )
            elif msg_type == "phase5_session_lock":
                await self._phase5_session_transition(
                    websocket, data, actor, subsystem, SessionState.LOCKED
                )
            elif msg_type == "phase5_session_revoke":
                await self._phase5_session_transition(
                    websocket, data, actor, subsystem, SessionState.REVOKED
                )
            elif msg_type == "phase5_capability_prepare":
                await self._phase5_capability_prepare(websocket, data, actor, subsystem)
            elif msg_type == "phase5_capability_confirm":
                await self._phase5_capability_confirm(websocket, data, actor, subsystem)
            elif msg_type == "phase5_helper_grant_create":
                await self._phase5_helper_grant_create(websocket, data, actor, subsystem)
            elif msg_type == "phase5_helper_grant_list":
                await self._phase5_helper_grant_list(websocket, data, actor, subsystem)
            elif msg_type == "phase5_helper_grant_revoke":
                await self._phase5_helper_grant_revoke(websocket, data, actor, subsystem)
            else:
                await self._send_phase5_message(
                    websocket,
                    build_phase5_error(request_id=request_id, code="invalid_request"),
                )
        except Exception:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(request_id=request_id, code="internal_error"),
            )

    async def _phase5_session_activate(self, websocket, data, actor, subsystem) -> None:
        request_id = self._phase5_request_id(data)
        runtime = subsystem.runtime_service
        if runtime is None:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="unavailable")
            )
            return

        session_type = SessionType(str(data["session_type"]))
        capabilities = tuple(Capability(value) for value in data["capabilities"])
        expires_at = float(data["expires_at"])
        owner_actor_id = actor.actor_id
        session_actor_id = owner_actor_id
        grant = None
        evidence = None
        source = AuthoritySource.OWNER_DIRECT

        if session_type is SessionType.CHILD:
            session_actor_id = str(data.get("session_actor_id") or "")
            evidence = data.get("activation_evidence")
            if not session_actor_id or not evidence:
                await self._send_phase5_message(
                    websocket,
                    build_phase5_error(request_id=request_id, code="invalid_request"),
                )
                return
            source = AuthoritySource.CHILD_ACTIVATION
        elif session_type is SessionType.TRUSTED_HELPER:
            session_actor_id = str(data.get("session_actor_id") or "")
            grant_id = data.get("grant_id")
            if not session_actor_id or not isinstance(grant_id, str):
                await self._send_phase5_message(
                    websocket,
                    build_phase5_error(request_id=request_id, code="invalid_request"),
                )
                return
            grant = subsystem.policy_service.get_helper_grant(grant_id)
            if grant is None:
                await self._send_phase5_message(
                    websocket,
                    build_phase5_error(request_id=request_id, code="not_found"),
                )
                return
            source = AuthoritySource.HELPER_GRANT

        authority = SessionAuthoritySnapshot(
            source=source,
            owner_actor_id=owner_actor_id,
            grant=grant,
            activation_evidence=evidence if isinstance(evidence, str) else None,
        )
        activation = SessionActivationRequest(
            request_id=request_id,
            session_type=session_type,
            owner_actor_id=owner_actor_id,
            session_actor_id=session_actor_id,
            requested_capabilities=capabilities,
            requested_expires_at=expires_at,
            authority_snapshot=authority,
        )
        decision = await asyncio.to_thread(runtime.activate_session, actor, activation)
        if decision.outcome is Outcome.ALLOW and decision.session is not None:
            await self._send_phase5_message(
                websocket,
                session_to_update_message(request_id=request_id, session=decision.session),
            )
            return
        code = outcome_to_error_code(decision.outcome)
        await self._send_phase5_message(
            websocket, build_phase5_error(request_id=request_id, code=code)
        )

    async def _phase5_session_status(self, websocket, data, actor, subsystem) -> None:
        request_id = self._phase5_request_id(data)
        runtime = subsystem.runtime_service
        if runtime is None:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="unavailable")
            )
            return
        session_id = str(data["session_id"])
        session = await asyncio.to_thread(
            runtime.get_session_status, session_id, actor.actor_id
        )
        if session is None:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="not_found")
            )
            return
        await self._send_phase5_message(
            websocket,
            session_to_update_message(request_id=request_id, session=session),
        )

    async def _phase5_session_transition(
        self, websocket, data, actor, subsystem, to_state: SessionState
    ) -> None:
        request_id = self._phase5_request_id(data)
        runtime = subsystem.runtime_service
        if runtime is None:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="unavailable")
            )
            return
        session_id = str(data["session_id"])
        authority = SessionAuthoritySnapshot(
            source=AuthoritySource.OWNER_DIRECT,
            owner_actor_id=actor.actor_id,
        )
        decision = await asyncio.to_thread(
            runtime.transition_session,
            session_id,
            to_state,
            actor.actor_id,
            authority,
        )
        if decision.outcome is Outcome.ALLOW and decision.session is not None:
            await self._send_phase5_message(
                websocket,
                session_to_update_message(request_id=request_id, session=decision.session),
            )
            return
        code = outcome_to_error_code(decision.outcome)
        reason_value = getattr(getattr(decision, "reason", None), "value", "")
        if isinstance(reason_value, str) and "missing" in reason_value:
            code = "not_found"
        await self._send_phase5_message(
            websocket, build_phase5_error(request_id=request_id, code=code)
        )

    async def _phase5_capability_prepare(self, websocket, data, actor, subsystem) -> None:
        request_id = self._phase5_request_id(data)
        runtime = subsystem.runtime_service
        capability_service = subsystem.capability_service
        if runtime is None or capability_service is None:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="unavailable")
            )
            return

        capability = Capability(str(data["capability"]))
        action = data.get("action") if isinstance(data.get("action"), str) and data.get("action") else None
        resource = data.get("resource") if isinstance(data.get("resource"), str) and data.get("resource") else None
        data_subject = data.get("data_subject") if isinstance(data.get("data_subject"), str) and data.get("data_subject") else None
        rt_request = Phase5RuntimeRequest(
            request_id=request_id,
            capability=capability,
            context=Phase5RuntimeContext(actor_context=actor, source="websocket"),
            action=action,
            resource=resource,
            data_subject=data_subject,
            user_initiated=True,
            now=float(subsystem.policy_service.clock()),
        )
        decision = await asyncio.to_thread(runtime.authorize, rt_request)
        if decision.outcome is Outcome.REQUIRE_APPROVAL or decision.approval_required:
            pending = {
                "capability": capability.value,
                "topic": data.get("topic"),
                "goal": data.get("goal"),
                "care_prompt": data.get("care_prompt"),
                "action": data.get("action"),
                "resource": data.get("resource"),
                "data_subject": data.get("data_subject"),
                "actor_id": actor.actor_id,
                "session_id": actor.session_id,
            }
            queue_error = self._queue_phase5_approval(
                websocket, request_id, pending, rt_request.now
            )
            if queue_error is not None:
                await self._send_phase5_message(
                    websocket,
                    build_phase5_error(request_id=request_id, code=queue_error),
                )
                return
            await self._send_phase5_message(
                websocket,
                build_approval_required_message(
                    request_id=request_id,
                    pending_request_id=request_id,
                    capability=capability,
                ),
            )
            return
        if decision.outcome is not Outcome.ALLOW:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(
                    request_id=request_id,
                    code=outcome_to_error_code(decision.outcome),
                ),
            )
            return

        phase5_actor = Phase5ActorContext(
            actor_id=actor.actor_id,
            actor=Phase5Actor.OWNER,
            session_id=actor.session_id,
            source="websocket",
        )
        exec_request = CapabilityExecutionRequest(
            request_id=request_id,
            actor=phase5_actor,
            capability=capability,
            action=rt_request.action,
            resource=rt_request.resource,
            data_subject=rt_request.data_subject,
            topic=data.get("topic") if isinstance(data.get("topic"), str) else None,
            goal=data.get("goal") if isinstance(data.get("goal"), str) else None,
            care_prompt=data.get("care_prompt") if isinstance(data.get("care_prompt"), str) else None,
        )
        prepared = await asyncio.to_thread(capability_service.prepare, exec_request, decision)
        if prepared.outcome is Outcome.REQUIRE_APPROVAL or (
            prepared.approval_required and prepared.outcome is not Outcome.ALLOW
        ):
            pending = {
                "capability": capability.value,
                "topic": data.get("topic"),
                "goal": data.get("goal"),
                "care_prompt": data.get("care_prompt"),
                "action": data.get("action"),
                "resource": data.get("resource"),
                "data_subject": data.get("data_subject"),
                "actor_id": actor.actor_id,
                "session_id": actor.session_id,
            }
            queue_error = self._queue_phase5_approval(
                websocket, request_id, pending, rt_request.now
            )
            if queue_error is not None:
                await self._send_phase5_message(
                    websocket,
                    build_phase5_error(request_id=request_id, code=queue_error),
                )
                return
            await self._send_phase5_message(
                websocket,
                build_approval_required_message(
                    request_id=request_id,
                    pending_request_id=request_id,
                    capability=capability,
                ),
            )
            return
        if prepared.outcome is not Outcome.ALLOW:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(
                    request_id=request_id,
                    code=outcome_to_error_code(prepared.outcome),
                ),
            )
            return
        await self._send_phase5_message(
            websocket,
            build_capability_proposal_message(
                request_id=request_id,
                capability=capability,
                decision=prepared,
            ),
        )

    async def _phase5_capability_confirm(self, websocket, data, actor, subsystem) -> None:
        request_id = self._phase5_request_id(data)
        pending_request_id = str(data["pending_request_id"])
        client_key = str(id(websocket))
        pending_map = self._phase5_pending_approvals.get(client_key, {})
        pending = pending_map.pop(pending_request_id, None)
        if pending is None:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(request_id=request_id, code="stale_request"),
            )
            return
        now = float(subsystem.policy_service.clock())
        if float(pending.get("expires_at", 0.0)) <= now:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(request_id=request_id, code="stale_request"),
            )
            return
        if pending.get("actor_id") != actor.actor_id:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(request_id=request_id, code="unauthorized"),
            )
            return

        capability = Capability(str(pending["capability"]))
        try:
            await asyncio.to_thread(
                lambda: subsystem.policy_service.issue_owner_consent(
                    owner_actor=actor,
                    capability=capability,
                    scope=ScopeConstraint(
                        capability=capability,
                        data_subject=pending.get("data_subject"),
                        resource_pattern=pending.get("resource"),
                        allowed_actions=(pending["action"],)
                        if isinstance(pending.get("action"), str) and pending["action"]
                        else (),
                    ),
                    expires_at=now + _PHASE5_PENDING_APPROVAL_TTL_SECONDS,
                    consent_id=("consent-" + pending_request_id)[:80],
                )
            )
        except Exception:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(request_id=request_id, code="denied"),
            )
            return

        runtime = subsystem.runtime_service
        capability_service = subsystem.capability_service
        if runtime is None or capability_service is None:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="unavailable")
            )
            return

        action = pending.get("action") if isinstance(pending.get("action"), str) and pending.get("action") else None
        resource = pending.get("resource") if isinstance(pending.get("resource"), str) and pending.get("resource") else None
        data_subject = pending.get("data_subject") if isinstance(pending.get("data_subject"), str) and pending.get("data_subject") else None
        rt_request = Phase5RuntimeRequest(
            request_id=pending_request_id,
            capability=capability,
            context=Phase5RuntimeContext(actor_context=actor, source="websocket"),
            action=action,
            resource=resource,
            data_subject=data_subject,
            user_initiated=True,
            now=float(subsystem.policy_service.clock()),
        )
        decision = await asyncio.to_thread(runtime.authorize, rt_request)
        if decision.outcome is Outcome.REQUIRE_APPROVAL:
            await self._send_phase5_message(
                websocket,
                build_approval_required_message(
                    request_id=request_id,
                    pending_request_id=pending_request_id,
                    capability=capability,
                ),
            )
            self._phase5_pending_approvals.setdefault(client_key, {})[pending_request_id] = pending
            return
        if decision.outcome is not Outcome.ALLOW:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(
                    request_id=request_id,
                    code=outcome_to_error_code(decision.outcome),
                ),
            )
            return

        phase5_actor = Phase5ActorContext(
            actor_id=actor.actor_id,
            actor=Phase5Actor.OWNER,
            session_id=actor.session_id,
            source="websocket",
        )
        exec_request = CapabilityExecutionRequest(
            request_id=pending_request_id,
            actor=phase5_actor,
            capability=capability,
            action=rt_request.action,
            resource=rt_request.resource,
            data_subject=rt_request.data_subject,
            topic=pending.get("topic") if isinstance(pending.get("topic"), str) else None,
            goal=pending.get("goal") if isinstance(pending.get("goal"), str) else None,
            care_prompt=pending.get("care_prompt") if isinstance(pending.get("care_prompt"), str) else None,
        )
        prepared = await asyncio.to_thread(capability_service.prepare, exec_request, decision)
        if prepared.outcome is not Outcome.ALLOW:
            await self._send_phase5_message(
                websocket,
                build_phase5_error(
                    request_id=request_id,
                    code=outcome_to_error_code(prepared.outcome),
                ),
            )
            return
        await self._send_phase5_message(
            websocket,
            build_capability_proposal_message(
                request_id=request_id,
                capability=capability,
                decision=prepared,
            ),
        )

    async def _phase5_helper_grant_create(self, websocket, data, actor, subsystem) -> None:
        request_id = self._phase5_request_id(data)
        if actor.actor is not Actor.OWNER:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="unauthorized")
            )
            return
        capability = Capability(str(data["capability"]))
        allowed = data.get("allowed_actions") or []
        scope = ScopeConstraint(
            capability=capability,
            data_subject=data.get("data_subject") if isinstance(data.get("data_subject"), str) else None,
            resource_pattern=data.get("resource_pattern") if isinstance(data.get("resource_pattern"), str) else None,
            allowed_actions=tuple(allowed) if isinstance(allowed, list) else (),
        )
        try:
            grant = await asyncio.to_thread(
                lambda: subsystem.policy_service.issue_helper_grant(
                    owner_actor=actor,
                    helper_actor_id=str(data["helper_actor_id"]),
                    capability=capability,
                    scope=scope,
                    expires_at=float(data["expires_at"]),
                    grant_id=("grant-" + request_id)[:80],
                )
            )
        except Exception:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="denied")
            )
            return
        await self._send_phase5_message(
            websocket,
            build_helper_grants_message(request_id=request_id, grants=[grant]),
        )

    async def _phase5_helper_grant_list(self, websocket, data, actor, subsystem) -> None:
        request_id = self._phase5_request_id(data)
        if actor.actor is not Actor.OWNER:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="unauthorized")
            )
            return
        grants = await asyncio.to_thread(
            subsystem.policy_service.phase5_grants.list_for_owner, actor.actor_id
        )
        await self._send_phase5_message(
            websocket,
            build_helper_grants_message(request_id=request_id, grants=grants),
        )

    async def _phase5_helper_grant_revoke(self, websocket, data, actor, subsystem) -> None:
        request_id = self._phase5_request_id(data)
        if actor.actor is not Actor.OWNER:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="unauthorized")
            )
            return
        grant_id = str(data["grant_id"])
        runtime = subsystem.runtime_service
        if runtime is None:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="unavailable")
            )
            return
        revoked = await asyncio.to_thread(
            runtime.revoke_helper_access, grant_id, actor.actor_id
        )
        if not revoked:
            await self._send_phase5_message(
                websocket, build_phase5_error(request_id=request_id, code="not_found")
            )
            return
        grants = await asyncio.to_thread(
            subsystem.policy_service.phase5_grants.list_for_owner, actor.actor_id
        )
        await self._send_phase5_message(
            websocket,
            build_helper_grants_message(request_id=request_id, grants=grants),
        )

    def _get_phase6_tracker(self, client_key: str) -> Phase6CorrelationTracker:
        tracker = self._phase6_trackers.get(client_key)
        if tracker is None:
            tracker = Phase6CorrelationTracker()
            self._phase6_trackers[client_key] = tracker
        return tracker

    async def _send_phase6_message(self, websocket, message: dict) -> None:
        validation_error = validate_server_message(message)
        if validation_error:
            request_id = message.get("request_id") if isinstance(message, dict) else None
            if not isinstance(request_id, str) or not _PREPARE_REQUEST_ID_RE.fullmatch(request_id):
                request_id = "invalid-request"
            message = build_error_frame(request_id=request_id, code=Phase6ErrorCode.INTERNAL_ERROR)
        try:
            await websocket.send(json.dumps(message))
        except Exception:
            pass

    def queue_phase6_proposal(self, client_key: str, proposal: dict) -> bool:
        if (
            not isinstance(client_key, str)
            or not client_key
            or not isinstance(proposal, dict)
            or proposal.get("type") != "phase6_home_assistant_proposal"
            or validate_server_message(proposal) is not None
        ):
            return False
        pending_map = self._phase6_pending_proposals.setdefault(client_key, {})
        if len(pending_map) >= 32:
            oldest_id = next(iter(pending_map))
            pending_map.pop(oldest_id, None)
        proposal_id = proposal.get("proposal_id")
        if isinstance(proposal_id, str):
            pending_map[proposal_id] = dict(proposal)
            return True
        return False

    async def _handle_phase6_control(self, websocket, data: dict) -> None:
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not _PREPARE_REQUEST_ID_RE.fullmatch(request_id):
            request_id = "invalid-request"

        context = self._derive_actor_context(websocket)
        actor = context.actor_context

        if not context.is_paired or actor.actor is not Actor.OWNER:
            await self._send_phase6_message(
                websocket,
                build_error_frame(request_id=request_id, code=Phase6ErrorCode.UNAUTHORIZED),
            )
            return

        client_key = str(id(websocket))
        tracker = self._get_phase6_tracker(client_key)

        if not tracker.track_request(request_id):
            await self._send_phase6_message(
                websocket,
                build_error_frame(request_id=request_id, code=Phase6ErrorCode.DUPLICATE_REQUEST),
            )
            return

        msg_type = data.get("type")
        try:
            if msg_type == "phase6_integration_list_request":
                await self._phase6_integration_list(websocket, data, request_id, client_key)
            elif msg_type == "phase6_home_assistant_confirm_request":
                await self._phase6_home_assistant_confirm(websocket, data, request_id, client_key)
            else:
                await self._send_phase6_message(
                    websocket,
                    build_error_frame(request_id=request_id, code=Phase6ErrorCode.INVALID_REQUEST),
                )
        except Exception:
            await self._send_phase6_message(
                websocket,
                build_error_frame(request_id=request_id, code=Phase6ErrorCode.INTERNAL_ERROR),
            )

    async def _phase6_integration_list(self, websocket, data: dict, request_id: str, client_key: str) -> None:
        ha_status = "unavailable"
        sync_status = "unavailable"
        worker_status = "unavailable"
        skill_status = "unavailable"
        model_status = "unavailable"

        if self._phase6_subsystem is not None:
            ha_status = "ready" if getattr(self._phase6_subsystem, "home_assistant_enabled", False) else "disabled"
            sync_status = "ready" if getattr(self._phase6_subsystem, "encrypted_sync_enabled", False) else "disabled"
            worker_status = "ready" if getattr(self._phase6_subsystem, "remote_workers_enabled", False) else "disabled"
            skill_status = "ready" if getattr(self._phase6_subsystem, "skill_staging_enabled", False) else "disabled"
            model_status = "ready" if getattr(self._phase6_subsystem, "measured_routing_enabled", False) else "disabled"

        integrations = [
            ("home_assistant", "Home Assistant", ha_status, "Entity control plane"),
            ("encrypted_sync", "Encrypted Sync", sync_status, "Manifest sync planner"),
            ("remote_workers", "Remote Workers", worker_status, "Isolated job telemetry"),
            ("skill_evolution", "Skill Evolution", skill_status, "Reviewed skill packages"),
            ("model_evaluation", "Model Evaluation", model_status, "Local model routing"),
        ]
        for int_id, name, status, summary in integrations:
            await self._send_phase6_message(
                websocket,
                build_integration_status_frame(
                    request_id=request_id,
                    integration_id=int_id,
                    name=name,
                    status=status,
                    details_summary=summary,
                ),
            )

    async def _phase6_home_assistant_confirm(self, websocket, data: dict, request_id: str, client_key: str) -> None:
        proposal_id = data.get("proposal_id")
        nonce = data.get("nonce")

        pending_map = self._phase6_pending_proposals.get(client_key, {})
        proposal = pending_map.get(proposal_id)

        if not proposal or proposal.get("nonce") != nonce:
            await self._send_phase6_message(
                websocket,
                build_error_frame(request_id=request_id, code=Phase6ErrorCode.STALE_REQUEST),
            )
            return

        tracker = self._get_phase6_tracker(client_key)
        if not tracker.track_nonce(nonce):
            pending_map.pop(proposal_id, None)
            await self._send_phase6_message(
                websocket,
                build_error_frame(request_id=request_id, code=Phase6ErrorCode.STALE_REQUEST),
            )
            return

        expires_at = proposal.get("expires_at", 0)
        if time.time() > expires_at:
            pending_map.pop(proposal_id, None)
            await self._send_phase6_message(
                websocket,
                build_error_frame(request_id=request_id, code=Phase6ErrorCode.EXPIRED),
            )
            return

        # One-time consumption
        pending_map.pop(proposal_id, None)

        if self._phase6_subsystem is None:
            await self._send_phase6_message(
                websocket,
                build_error_frame(request_id=request_id, code=Phase6ErrorCode.UNAVAILABLE),
            )
            return

        await self._send_phase6_message(
            websocket,
            build_error_frame(request_id=request_id, code=Phase6ErrorCode.UNAVAILABLE),
        )

    async def _handle_message(self, websocket, message: str):
        """Process incoming message from client"""
        try:
            try:
                data = _parse_client_json(message)
            except (json.JSONDecodeError, ValueError):
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "message": "Invalid JSON",
                        }
                    )
                )
                return
            if not isinstance(data, dict):
                await websocket.send(
                    json.dumps({"type": "error", "message": "Invalid message payload"})
                )
                return

            msg_type = data.get("type", "")
            client_id = str(id(websocket))

            if msg_type in PHASE5_CLIENT_MESSAGE_TYPES:
                validation_error = validate_client_message(data)
                if validation_error:
                    await self._send_phase5_message(
                        websocket,
                        build_phase5_error(
                            request_id=self._phase5_request_id(data),
                            code="invalid_request",
                        ),
                    )
                    return
                await self._handle_phase5_control(websocket, data)
                return

            if msg_type in PHASE6_CLIENT_MESSAGE_TYPES:
                validation_error = validate_client_message(data)
                if validation_error:
                    request_id = data.get("request_id") if isinstance(data, dict) else None
                    if not isinstance(request_id, str) or not _PREPARE_REQUEST_ID_RE.fullmatch(request_id):
                        request_id = "invalid-request"
                    await self._send_phase6_message(
                        websocket,
                        build_error_frame(
                            request_id=request_id,
                            code=Phase6ErrorCode.INVALID_REQUEST,
                        ),
                    )
                    return
                await self._handle_phase6_control(websocket, data)
                return

            if msg_type in {
                "pairing_prepare",
                "pairing_confirm",
                "pairing_cancel",
                "pairing_revoke",
            }:
                validation_error = validate_client_message(data)
                if validation_error:
                    request_id = data.get("request_id")
                    if not isinstance(request_id, str) or not _PREPARE_REQUEST_ID_RE.fullmatch(request_id):
                        request_id = "invalid-request"
                    await self._send_phase4_message(
                        websocket,
                        {
                            "type": "pairing_error",
                            "request_id": request_id,
                            "code": "invalid_request",
                        },
                    )
                    return
                await self._handle_pairing_control(websocket, data)
                return

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
                rate_key = self._pair_rate_key(websocket)
                attempts = self._pair_attempt_count(rate_key)
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
                    self._pair_attempts.pop(rate_key, None)
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
                    previous = self._pair_attempts.get(rate_key)
                    started_at = (
                        previous[1]
                        if isinstance(previous, tuple)
                        else self._pair_clock()
                    )
                    self._pair_attempts[rate_key] = (attempts, started_at)
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
                if msg_type in {
                    "productivity_email_draft_prepare",
                    "productivity_calendar_read_prepare",
                    "productivity_calendar_draft_prepare",
                    "productivity_research_prepare",
                    "productivity_reminder_prepare",
                }:
                    request_id = data.get("request_id")
                    result = self._safe_productivity_error("invalid-proposal")
                    result = self._with_prepare_request_id(result, request_id)
                    await self._send_productivity_message(
                        websocket, result, "invalid-proposal"
                    )
                    return
                await websocket.send(
                    json.dumps({"type": "error", "message": validation_error})
                )
                return

            if msg_type in {
                "handoff_prepare",
                "handoff_accept",
                "handoff_reject",
                "handoff_cancel",
                "handoff_status",
            }:
                await self._handle_handoff_control(websocket, data)
                return

            if msg_type in {
                "visual_transfer_begin",
                "visual_transfer_status",
                "visual_transfer_cancel",
            }:
                await self._handle_visual_control(websocket, data)
                return

            if msg_type in {
                "vision_analysis_prepare",
                "vision_analysis_cancel",
                "vision_analysis_status",
            }:
                await self._handle_vision_control(websocket, data)
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
                # Process user message through orchestrator with request-scoped actor context
                user_input = data.get("text", "")
                if user_input:
                    context = self._derive_actor_context(websocket)
                    response = self.orchestrator.process_input(
                        user_input,
                        source="device",
                        context=context.actor_context,
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
                    context = self._derive_actor_context(websocket)
                    if self._voice_companion_enabled():
                        await self._handle_voice_turn(websocket, text, context)
                    else:
                        response = self.orchestrator.process_input(
                            text,
                            source="voice_remote",
                            context=context.actor_context,
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

            elif msg_type == "productivity_email_draft_prepare":
                request_id = data.get("request_id")
                if (
                    self._productivity_runtime is None
                    or self._email_draft_factory is None
                    or self._email_draft_registry is None
                ):
                    result = self._with_prepare_request_id(
                        self._safe_productivity_error("invalid-proposal"),
                        request_id,
                    )
                    await self._send_productivity_message(
                        websocket,
                        result,
                        "invalid-proposal",
                    )
                    return
                actor = self._derive_actor_context(websocket).actor_context
                proposal_id = "invalid-proposal"
                try:
                    prepared = self._email_draft_factory.prepare(
                        actor,
                        data["recipient"],
                        data["subject"],
                        data["body"],
                    )
                    proposal_id = prepared.proposal.proposal_id
                    self._email_draft_registry.put(
                        actor, proposal_id, prepared.draft
                    )
                    result = await asyncio.to_thread(
                        self._productivity_runtime.prepare,
                        actor,
                        prepared.proposal,
                    )
                    if result.get("type") != "productivity_confirmation_required":
                        self._email_draft_registry.remove(actor, proposal_id)
                except Exception:
                    try:
                        self._email_draft_registry.remove(actor, proposal_id)
                    except Exception:
                        pass
                    result = self._safe_productivity_error(proposal_id)
                result = self._with_prepare_request_id(result, request_id)
                await self._send_productivity_message(
                    websocket, result, proposal_id
                )
                return

            elif msg_type == "productivity_calendar_read_prepare":
                await self._handle_calendar_read_prepare(websocket, data)
                return

            elif msg_type == "productivity_calendar_draft_prepare":
                await self._handle_calendar_draft_prepare(websocket, data)
                return

            elif msg_type == "productivity_research_prepare":
                await self._handle_research_prepare(websocket, data)
                return

            elif msg_type == "productivity_reminder_prepare":
                await self._handle_reminder_prepare(websocket, data)
                return

            elif msg_type == "scheduled_job_create":
                await self._handle_scheduled_job_create(websocket, data)
                return

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

            elif msg_type in {
                "scheduled_jobs_list",
                "scheduled_job_pause",
                "scheduled_job_resume",
                "scheduled_job_cancel",
            }:
                if self._scheduled_job_runtime is None:
                    await websocket.send(
                        json.dumps(
                            self._safe_scheduled_job_error(
                                data.get("job_id"), "unavailable"
                            )
                        )
                    )
                    return
                context = self._derive_actor_context(websocket)
                actor = context.actor_context
                job_id = str(data.get("job_id", "")) if msg_type != "scheduled_jobs_list" else ""
                try:
                    if msg_type == "scheduled_jobs_list":
                        result = await asyncio.to_thread(
                            self._scheduled_job_runtime.list_jobs, actor
                        )
                    elif msg_type == "scheduled_job_pause":
                        result = await asyncio.to_thread(
                            self._scheduled_job_runtime.pause, actor, job_id
                        )
                    elif msg_type == "scheduled_job_resume":
                        result = await asyncio.to_thread(
                            self._scheduled_job_runtime.resume, actor, job_id
                        )
                    else:
                        result = await asyncio.to_thread(
                            self._scheduled_job_runtime.cancel, actor, job_id
                        )
                except Exception:
                    result = self._safe_scheduled_job_error(job_id, "unavailable")
                await self._send_scheduled_job_message(websocket, result, job_id)
                if (
                    msg_type == "scheduled_job_cancel"
                    and isinstance(result, dict)
                    and result.get("type") == "scheduled_job_update"
                    and isinstance(result.get("job"), dict)
                    and result["job"].get("state") == "cancelled"
                ):
                    await asyncio.to_thread(self._remove_scheduled_action, actor, job_id)
                return

            elif msg_type in {
                "productivity_confirm",
                "productivity_cancel",
                "productivity_status",
            }:
                proposal_id = str(data.get("proposal_id", ""))
                if self._productivity_runtime is None:
                    await websocket.send(
                        json.dumps(
                            error_message(
                                proposal_id or "invalid-proposal",
                                ProductivityCode.CONSUMPTION_FAILED,
                            )
                        )
                    )
                    return
                context = self._derive_actor_context(websocket)
                actor = context.actor_context
                runtime = self._productivity_runtime
                try:
                    if msg_type == "productivity_confirm":
                        scope = ApprovalScope(str(data.get("scope", "")))
                        await self._handle_productivity_confirm(
                            websocket,
                            actor,
                            proposal_id,
                            scope,
                            duration_seconds=data.get("duration_seconds"),
                            acknowledge=data.get("acknowledged", False),
                        )
                        return
                    elif msg_type == "productivity_cancel":
                        result = await asyncio.to_thread(
                            runtime.cancel, actor, proposal_id
                        )
                    else:
                        result = await asyncio.to_thread(
                            runtime.status, actor, proposal_id
                        )
                except Exception:
                    result = self._safe_productivity_error(proposal_id)
                self._clear_prepared_inputs_if_terminal(actor, proposal_id, result)
                await self._send_productivity_message(
                    websocket,
                    result,
                    proposal_id,
                )
                return

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

        except Exception:
            print("[WS] Request failed")
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

    def _clear_prepared_inputs_if_terminal(
        self,
        actor,
        proposal_id: str,
        result: object,
    ) -> None:
        """Drop retained prepare inputs only after terminal or expiry outcomes."""
        if not isinstance(result, dict):
            return
        msg_type = result.get("type")
        if msg_type == "productivity_update":
            if result.get("status") in {"completed", "failed", "cancelled"}:
                self._remove_prepared_inputs(actor, proposal_id)
        elif msg_type in {
            "productivity_research_result",
            "productivity_calendar_result",
        }:
            self._remove_prepared_inputs(actor, proposal_id)
        elif msg_type == "productivity_error" and result.get("code") == "proposal_expired":
            self._remove_prepared_inputs(actor, proposal_id)

    def _preparation_registry_or_empty(self, registry: object) -> object:
        if registry is None:
            return _EmptyPreparationRegistry()
        return registry

    def _public_confirmation_message(self, confirmation: object) -> dict | None:
        """Return a validated public confirmation message, never exposing approval_id."""
        if not isinstance(confirmation, ConfirmationResult):
            return None
        public = confirmation.public_message
        if not isinstance(public, dict):
            return None
        if "approval_id" in public:
            public = {key: value for key, value in public.items() if key != "approval_id"}
        if validate_server_message(public) is not None:
            return None
        return public

    async def _revoke_unconsumed_approval(self, actor, proposal_id: str) -> None:
        """Revoke a just-issued approval without touching other sessions' inputs."""
        runtime = self._productivity_runtime
        if runtime is None:
            return
        try:
            await asyncio.to_thread(runtime.cancel, actor, proposal_id)
        except Exception:
            pass

    async def _handle_productivity_confirm(
        self,
        websocket,
        actor,
        proposal_id: str,
        scope: ApprovalScope,
        *,
        duration_seconds: object = None,
        acknowledge: object = False,
    ) -> None:
        """Confirm once, publish the approved message, then optionally execute."""
        runtime = self._productivity_runtime
        if runtime is None:
            await self._send_productivity_message(
                websocket,
                self._safe_productivity_error(proposal_id),
                proposal_id,
            )
            return

        try:
            confirmation = await asyncio.to_thread(
                runtime.confirm_and_ticket,
                actor,
                proposal_id,
                scope,
                duration_seconds=duration_seconds,
                acknowledge=acknowledge,
            )
        except Exception:
            await self._send_productivity_message(
                websocket,
                self._safe_productivity_error(proposal_id),
                proposal_id,
            )
            return

        public = self._public_confirmation_message(confirmation)
        if public is None:
            await self._send_productivity_message(
                websocket,
                self._safe_productivity_error(proposal_id),
                proposal_id,
            )
            return

        await self._send_productivity_message(websocket, public, proposal_id)
        self._clear_prepared_inputs_if_terminal(actor, proposal_id, public)

        coordinator = self._productivity_execution_coordinator
        if coordinator is None:
            return
        if (
            public.get("type") != "productivity_update"
            or public.get("status") != "approved"
        ):
            return
        if not isinstance(confirmation, ConfirmationResult):
            return

        approval_id = confirmation.approval_id
        if not isinstance(approval_id, str) or not _PREPARE_REQUEST_ID_RE.fullmatch(
            approval_id
        ):
            await self._revoke_unconsumed_approval(actor, proposal_id)
            await self._send_productivity_message(
                websocket,
                self._safe_productivity_error(proposal_id),
                proposal_id,
            )
            return

        try:
            request = build_execution_request(
                actor,
                proposal_id,
                confirmation,
                email_registry=self._preparation_registry_or_empty(
                    self._email_draft_registry
                ),
                calendar_registry=self._preparation_registry_or_empty(
                    self._calendar_registry
                ),
                research_registry=self._preparation_registry_or_empty(
                    self._research_registry
                ),
                reminder_registry=self._preparation_registry_or_empty(
                    self._reminder_registry
                ),
            )
        except Exception:
            await self._revoke_unconsumed_approval(actor, proposal_id)
            await self._send_productivity_message(
                websocket,
                self._safe_productivity_error(proposal_id),
                proposal_id,
            )
            return

        try:
            authorized = await asyncio.to_thread(coordinator.authorize, request)
        except Exception:
            await self._revoke_unconsumed_approval(actor, proposal_id)
            await self._send_productivity_message(
                websocket,
                self._safe_productivity_error(proposal_id),
                proposal_id,
            )
            return

        if not isinstance(authorized, ExecutionTicket):
            await self._revoke_unconsumed_approval(actor, proposal_id)
            if (
                isinstance(authorized, dict)
                and validate_server_message(authorized) is None
            ):
                terminal = authorized
            else:
                terminal = self._safe_productivity_error(proposal_id)
            await self._send_productivity_message(websocket, terminal, proposal_id)
            self._clear_prepared_inputs_if_terminal(actor, proposal_id, terminal)
            return

        try:
            executing = update_message(proposal_id, "executing")
        except Exception:
            executing = self._safe_productivity_error(proposal_id)
            await self._send_productivity_message(websocket, executing, proposal_id)
            return

        await self._send_productivity_message(websocket, executing, proposal_id)

        try:
            terminal = await asyncio.to_thread(
                coordinator.execute_authorized, authorized
            )
        except Exception:
            terminal = self._safe_productivity_error(proposal_id)

        if not isinstance(terminal, dict) or validate_server_message(terminal) is not None:
            terminal = self._safe_productivity_error(proposal_id)

        await self._send_productivity_message(websocket, terminal, proposal_id)
        self._clear_prepared_inputs_if_terminal(actor, proposal_id, terminal)

    def _remove_prepared_inputs(self, actor, proposal_id: str) -> None:
        if self._email_draft_registry is not None:
            try:
                self._email_draft_registry.remove(actor, proposal_id)
            except Exception:
                pass
        if self._calendar_registry is not None:
            try:
                self._calendar_registry.remove(actor, proposal_id)
            except Exception:
                pass
        if self._research_registry is not None:
            try:
                self._research_registry.remove(actor, proposal_id)
            except Exception:
                pass
        if self._reminder_registry is not None:
            try:
                self._reminder_registry.remove(actor, proposal_id)
            except Exception:
                pass

    @staticmethod
    def _with_prepare_request_id(message: object, request_id: object) -> object:
        """Echo a validated client request_id onto prepare success/error payloads."""
        if not isinstance(message, dict):
            return message
        if not isinstance(request_id, str) or not _PREPARE_REQUEST_ID_RE.fullmatch(
            request_id
        ):
            return message
        attached = {**message, "request_id": request_id}
        if validate_server_message(attached) is not None:
            return message
        return attached

    def _is_valid_reminder_confirmation(
        self,
        result: object,
        proposal_id: object,
        request_id: object,
    ) -> bool:
        """Validate a reminder prepare response before retaining or exposing it.

        Requires the runtime result to be a valid ``productivity_confirmation_required``
        message whose ``proposal_id``, ``action``, and ``request_id`` exactly match
        the server-generated values and whose field set is documented by the protocol.
        The validated client ``request_id`` is attached before validation so the
        response is checked against the canonical protocol shape.
        """
        if not isinstance(result, dict) or not isinstance(proposal_id, str):
            return False
        if "request_id" in result and result.get("request_id") != request_id:
            return False
        attached = self._with_prepare_request_id(result, request_id)
        if validate_server_message(attached) is not None:
            return False
        if attached.get("type") != "productivity_confirmation_required":
            return False
        if attached.get("proposal_id") != proposal_id:
            return False
        if attached.get("action") != "reminder.create":
            return False
        if attached.get("request_id") != request_id:
            return False
        return True

    @staticmethod
    def _parse_calendar_instant(value: object):
        """Parse a protocol-validated ISO instant without surfacing parse details."""
        if not isinstance(value, str):
            return None
        try:
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            dt = datetime.fromisoformat(normalized)
        except Exception:
            return None
        if dt.tzinfo is None:
            return None
        try:
            if dt.utcoffset() is None:
                return None
        except Exception:
            return None
        return dt

    async def _handle_calendar_read_prepare(self, websocket, data: Dict[str, Any]) -> None:
        request_id = data.get("request_id")
        if (
            self._productivity_runtime is None
            or self._calendar_read_factory is None
            or self._calendar_registry is None
        ):
            result = self._with_prepare_request_id(
                self._safe_productivity_error("invalid-proposal"),
                request_id,
            )
            await self._send_productivity_message(
                websocket,
                result,
                "invalid-proposal",
            )
            return
        actor = self._derive_actor_context(websocket).actor_context
        proposal_id = "invalid-proposal"
        try:
            start = self._parse_calendar_instant(data.get("start"))
            end = self._parse_calendar_instant(data.get("end"))
            if start is None or end is None:
                raise ValueError("invalid calendar instant")
            prepared = self._calendar_read_factory.prepare(
                actor,
                start,
                end,
                data.get("calendar_name"),
            )
            proposal_id = prepared.proposal.proposal_id
            self._calendar_registry.put(actor, proposal_id, prepared.read)
            result = await asyncio.to_thread(
                self._productivity_runtime.prepare,
                actor,
                prepared.proposal,
            )
            if result.get("type") != "productivity_confirmation_required":
                self._calendar_registry.remove(actor, proposal_id)
        except Exception:
            try:
                self._calendar_registry.remove(actor, proposal_id)
            except Exception:
                pass
            result = self._safe_productivity_error(proposal_id)
        result = self._with_prepare_request_id(result, request_id)
        await self._send_productivity_message(websocket, result, proposal_id)

    async def _handle_calendar_draft_prepare(self, websocket, data: Dict[str, Any]) -> None:
        request_id = data.get("request_id")
        if (
            self._productivity_runtime is None
            or self._calendar_draft_factory is None
            or self._calendar_registry is None
        ):
            result = self._with_prepare_request_id(
                self._safe_productivity_error("invalid-proposal"),
                request_id,
            )
            await self._send_productivity_message(
                websocket,
                result,
                "invalid-proposal",
            )
            return
        actor = self._derive_actor_context(websocket).actor_context
        proposal_id = "invalid-proposal"
        try:
            start = self._parse_calendar_instant(data.get("start"))
            end = self._parse_calendar_instant(data.get("end"))
            if start is None or end is None:
                raise ValueError("invalid calendar instant")
            prepared = self._calendar_draft_factory.prepare(
                actor,
                data["title"],
                start,
                end,
                data["calendar_name"],
                data.get("location"),
                data.get("notes"),
            )
            proposal_id = prepared.proposal.proposal_id
            self._calendar_registry.put(actor, proposal_id, prepared.draft)
            result = await asyncio.to_thread(
                self._productivity_runtime.prepare,
                actor,
                prepared.proposal,
            )
            if result.get("type") != "productivity_confirmation_required":
                self._calendar_registry.remove(actor, proposal_id)
        except Exception:
            try:
                self._calendar_registry.remove(actor, proposal_id)
            except Exception:
                pass
            result = self._safe_productivity_error(proposal_id)
        result = self._with_prepare_request_id(result, request_id)
        await self._send_productivity_message(websocket, result, proposal_id)

    async def _handle_research_prepare(self, websocket, data: Dict[str, Any]) -> None:
        request_id = data.get("request_id")
        if (
            self._productivity_runtime is None
            or self._research_factory is None
            or self._research_registry is None
        ):
            result = self._with_prepare_request_id(
                self._safe_productivity_error("invalid-proposal"),
                request_id,
            )
            await self._send_productivity_message(
                websocket,
                result,
                "invalid-proposal",
            )
            return
        actor = self._derive_actor_context(websocket).actor_context
        proposal_id = "invalid-proposal"
        try:
            prepare_kwargs: Dict[str, Any] = {}
            if "max_results" in data:
                prepare_kwargs["max_results"] = data["max_results"]
            prepared = self._research_factory.prepare(
                actor,
                data["query"],
                data.get("domains"),
                **prepare_kwargs,
            )
            proposal_id = prepared.proposal.proposal_id
            self._research_registry.put(actor, proposal_id, prepared.input)
            result = await asyncio.to_thread(
                self._productivity_runtime.prepare,
                actor,
                prepared.proposal,
            )
            if result.get("type") != "productivity_confirmation_required":
                self._research_registry.remove(actor, proposal_id)
        except Exception:
            try:
                self._research_registry.remove(actor, proposal_id)
            except Exception:
                pass
            result = self._safe_productivity_error(proposal_id)
        result = self._with_prepare_request_id(result, request_id)
        await self._send_productivity_message(websocket, result, proposal_id)

    async def _handle_reminder_prepare(self, websocket, data: Dict[str, Any]) -> None:
        request_id = data.get("request_id")
        if (
            self._productivity_runtime is None
            or self._reminder_factory is None
            or self._reminder_registry is None
        ):
            result = self._with_prepare_request_id(
                self._safe_productivity_error("invalid-proposal"),
                request_id,
            )
            await self._send_productivity_message(
                websocket,
                result,
                "invalid-proposal",
            )
            return
        actor = self._derive_actor_context(websocket).actor_context
        proposal_id = "invalid-proposal"
        try:
            remind_at = self._parse_calendar_instant(data.get("remind_at"))
            if remind_at is None:
                raise ValueError("invalid reminder instant")
            prepared = self._reminder_factory.prepare(
                actor,
                data["title"],
                remind_at,
                data.get("notes"),
                data.get("list_name"),
            )
            proposal_id = prepared.proposal.proposal_id
            self._reminder_registry.put(actor, proposal_id, prepared.reminder)
            result = await asyncio.to_thread(
                self._productivity_runtime.prepare,
                actor,
                prepared.proposal,
            )
            if not self._is_valid_reminder_confirmation(result, proposal_id, request_id):
                self._reminder_registry.remove(actor, proposal_id)
                result = self._with_prepare_request_id(
                    self._safe_productivity_error("invalid-proposal"),
                    request_id,
                )
                proposal_id = "invalid-proposal"
        except Exception:
            try:
                self._reminder_registry.remove(actor, proposal_id)
            except Exception:
                pass
            result = self._safe_productivity_error("invalid-proposal")
            proposal_id = "invalid-proposal"
        result = self._with_prepare_request_id(result, request_id)
        await self._send_productivity_message(websocket, result, proposal_id)

    @staticmethod
    def _safe_productivity_error(proposal_id: object) -> dict:
        """Return a canonical error without reflecting malformed identifiers."""
        try:
            return error_message(
                proposal_id,
                ProductivityCode.CONSUMPTION_FAILED,
            )
        except Exception:
            return error_message(
                "invalid-proposal",
                ProductivityCode.CONSUMPTION_FAILED,
            )

    async def _send_productivity_message(
        self,
        websocket,
        message: object,
        proposal_id: object,
    ) -> None:
        """Validate the injected runtime boundary before sending a message."""
        if not isinstance(message, dict) or validate_server_message(message) is not None:
            message = self._safe_productivity_error("invalid-proposal")
        await websocket.send(json.dumps(message))

    @staticmethod
    def _safe_scheduled_job_error(job_id: object, code: str = "unavailable") -> dict:
        """Return a canonical scheduled_job_error without reflecting malformed identifiers."""
        candidate = {
            "type": "scheduled_job_error",
            "job_id": job_id if isinstance(job_id, str) else "invalid-job-id",
            "code": code,
        }
        if validate_server_message(candidate) is None:
            return candidate
        return {
            "type": "scheduled_job_error",
            "job_id": "invalid-job-id",
            "code": "unavailable",
        }

    async def _send_scheduled_job_message(
        self,
        websocket,
        message: object,
        job_id: object,
    ) -> None:
        """Validate the injected scheduled-job runtime boundary before sending."""
        if not isinstance(message, dict) or validate_server_message(message) is not None:
            message = self._safe_scheduled_job_error(job_id)
        await websocket.send(json.dumps(message))

    @staticmethod
    def _with_scheduled_request_id(message: object, request_id: object) -> object:
        if (
            not isinstance(message, dict)
            or not isinstance(request_id, str)
            or not _PREPARE_REQUEST_ID_RE.fullmatch(request_id)
        ):
            return message
        candidate = {**message, "request_id": request_id}
        return candidate if validate_server_message(candidate) is None else message

    def _remove_scheduled_action(self, actor, job_id: str) -> None:
        subsystem = self._scheduled_job_subsystem
        runtime = self._scheduled_job_runtime
        if subsystem is None or runtime is None:
            return
        try:
            scheduled_actor = runtime.scheduled_actor(actor)
            envelope = subsystem.action_store.get(
                job_id,
                actor_id=scheduled_actor.actor_id,
                session_id=scheduled_actor.session_id,
            )
            if envelope is not None:
                subsystem.action_store.delete(
                    job_id,
                    actor_id=scheduled_actor.actor_id,
                    session_id=scheduled_actor.session_id,
                    expected_revision=envelope.revision,
                )
        except Exception:
            return

    async def _handle_scheduled_job_create(
        self, websocket, data: Dict[str, Any]
    ) -> None:
        request_id = data.get("request_id")
        proposal_id = data.get("proposal_id")
        fallback = self._with_scheduled_request_id(
            self._safe_scheduled_job_error("scheduled-jobs", "unavailable"),
            request_id,
        )
        subsystem = self._scheduled_job_subsystem
        runtime = self._scheduled_job_runtime
        if (
            subsystem is None
            or runtime is None
            or self._productivity_runtime is None
            or self._calendar_registry is None
            or self._research_registry is None
        ):
            await self._send_scheduled_job_message(
                websocket, fallback, "scheduled-jobs"
            )
            return

        actor = self._derive_actor_context(websocket).actor_context
        try:
            current = await asyncio.to_thread(
                self._productivity_runtime.status, actor, proposal_id
            )
            if (
                not isinstance(current, dict)
                or current.get("type") != "productivity_update"
                or current.get("proposal_id") != proposal_id
                or current.get("status") != "preview"
            ):
                raise ValueError
            action, adapter_input = build_scheduled_adapter_input(
                actor,
                proposal_id,
                calendar_registry=self._calendar_registry,
                research_registry=self._research_registry,
            )
            next_run_at = self._parse_calendar_instant(data.get("next_run_at"))
            if next_run_at is None:
                raise ValueError
            quiet_data = data.get("quiet_hours")
            quiet_hours = None
            if quiet_data is not None:
                if not isinstance(quiet_data, dict):
                    raise ValueError
                quiet_hours = QuietHours(
                    timezone_name=quiet_data.get("timezone"),
                    start_minute=quiet_data.get("start_minute"),
                    end_minute=quiet_data.get("end_minute"),
                )
            scheduled_actor = runtime.scheduled_actor(actor)
            owner = StableOwnerScope(
                scheduled_actor.actor_id,
                scheduled_actor.session_id,
            )
            created = await asyncio.to_thread(
                subsystem.coordinator.schedule,
                owner=owner,
                proposal_id=proposal_id,
                action=action,
                adapter_input=adapter_input,
                next_run_at=next_run_at,
                max_attempts=data.get("max_attempts"),
                quiet_hours=quiet_hours,
            )
            if (
                created.code is not ScheduledReadScheduleCode.SCHEDULED
                or created.job is None
            ):
                raise ValueError
            listing = await asyncio.to_thread(runtime.list_jobs, actor)
            if not isinstance(listing, dict) or listing.get("type") != "scheduled_jobs":
                raise ValueError
            job_view = next(
                item
                for item in listing.get("jobs", ())
                if isinstance(item, dict) and item.get("job_id") == created.job.job_id
            )
            result = self._with_scheduled_request_id(
                {"type": "scheduled_job_update", "job": job_view}, request_id
            )
            if not isinstance(result, dict) or validate_server_message(result) is not None:
                raise ValueError
        except Exception:
            result = fallback
        else:
            try:
                await asyncio.to_thread(
                    self._productivity_runtime.cancel, actor, proposal_id
                )
            except Exception:
                pass
            self._remove_prepared_inputs(actor, proposal_id)
        await self._send_scheduled_job_message(
            websocket, result, "scheduled-jobs"
        )

    def _execute_scheduled_read(self, job) -> ExecutionResult:
        """Execute one claimed read and require an acknowledged result delivery."""
        subsystem = self._scheduled_job_subsystem
        loop = self._loop
        if subsystem is None or loop is None or not loop.is_running():
            return ExecutionResult(ExecutionStatus.FAILED)
        outcome = subsystem.coordinator.execute_claimed(job)
        if outcome.result is None or outcome.fingerprint is None:
            return ExecutionResult(ExecutionStatus.FAILED)

        delivered = False

        def deliver(_snapshot) -> DeliveryAttemptResult:
            nonlocal delivered
            if delivered:
                return DeliveryAttemptResult(DeliveryAttemptStatus.REJECTED)
            delivered = True
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._deliver_scheduled_result(job, outcome.result), loop
                )
                accepted = future.result(timeout=15)
            except Exception:
                accepted = False
            return DeliveryAttemptResult(
                DeliveryAttemptStatus.ACKNOWLEDGED
                if accepted
                else DeliveryAttemptStatus.FAILED
            )

        delivery_runtime = MeaningfulChangeDeliveryRuntime(
            subsystem.store,
            subsystem.lifecycle,
            subsystem.clock,
            deliver,
        )
        delivered_result = delivery_runtime.deliver_change(
            job.job_id,
            actor_id=job.actor_id,
            session_id=job.session_id,
            candidate_fingerprint=outcome.fingerprint,
        )
        if delivered_result.code in {
            DeliveryRuntimeCode.ACKNOWLEDGED,
            DeliveryRuntimeCode.UNCHANGED,
        }:
            return ExecutionResult(ExecutionStatus.SUCCESS)
        if delivered_result.code is DeliveryRuntimeCode.SUPPRESSED:
            return ExecutionResult(ExecutionStatus.SUPPRESSED)
        return ExecutionResult(ExecutionStatus.FAILED)

    async def _deliver_scheduled_result(self, job, result: object) -> bool:
        """Deliver one bounded read result to one connected local owner client."""
        if isinstance(result, BrowserSearchResult):
            base = research_result_message(job.proposal_id, result)
            message = {
                "type": "scheduled_job_research_result",
                "job_id": job.job_id,
                "items": base["items"],
            }
        elif isinstance(result, CalendarReadResult):
            base = calendar_result_message(job.proposal_id, result)
            message = {
                "type": "scheduled_job_calendar_result",
                "job_id": job.job_id,
                "events": base["events"],
            }
        else:
            return False
        if validate_server_message(message) is not None:
            return False
        runtime = self._scheduled_job_runtime
        if runtime is None:
            return False
        for websocket in tuple(self.connected_clients):
            client_key = str(id(websocket))
            if client_key not in self._paired_client_ids or not self._is_loopback(websocket):
                continue
            try:
                actor = self._derive_actor_context(websocket).actor_context
                scheduled_actor = runtime.scheduled_actor(actor)
                if (
                    scheduled_actor.actor_id != job.actor_id
                    or scheduled_actor.session_id != job.session_id
                ):
                    continue
                await websocket.send(json.dumps(message))
                return True
            except Exception:
                continue
        return False

    async def publish_productivity_proposal(self, websocket, proposal) -> None:
        """Publish a productivity proposal to a single paired connection.

        The actor context is derived from server-observed transport state.
        No proposal content is logged or broadcast.
        """
        client_key = str(id(websocket))
        if client_key not in self._paired_client_ids:
            return
        if self._productivity_runtime is None:
            return
        context = self._derive_actor_context(websocket)
        actor = context.actor_context
        proposal_id = getattr(proposal, "proposal_id", "invalid-proposal")
        try:
            message = await asyncio.to_thread(
                self._productivity_runtime.prepare,
                actor,
                proposal,
            )
        except Exception:
            message = self._safe_productivity_error(proposal_id)
        await self._send_productivity_message(websocket, message, proposal_id)

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
                <input type="text" id="pairing-code" placeholder="0000000000" maxlength="10" autocomplete="off">
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
                    if (!/^[0-9A-F]{6,10}$/.test(code)) return;

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
        if self._scheduled_runner_task is not None:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._scheduled_runner_task.cancel)
            else:
                self._scheduled_runner_task.cancel()
        if self._phase4_sweeper_task is not None:
            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._phase4_sweeper_task.cancel)
            else:
                self._phase4_sweeper_task.cancel()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        print("[WS] Server stopped")
