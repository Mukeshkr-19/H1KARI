"""Phase 3 browser-research preparation and WebSocket boundary tests."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import hikari
import pytest

from core.action_policy import Actor, ActorContext
from core.productivity import (
    ProductivityRuntime,
    ProductivityService,
    ResearchPreparationRegistry,
    ResearchProposalFactory,
    SqliteApprovalStore,
)
from core.productivity.bootstrap import create_research_preparation
from core.productivity.runtime import ConfirmationResult
from core.productivity.transport import error_message, update_message
from core.productivity.service import ProductivityCode
from core.protocol import validate_client_message, validate_server_message
from core.server import WebSocketServer


REPO_ROOT = Path(__file__).resolve().parent.parent


class _WebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    @property
    def remote_address(self):
        return ("127.0.0.1", 12345)

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _QueuedWebSocket(_WebSocket):
    def __init__(self, messages: list[str]) -> None:
        super().__init__()
        self._messages = messages
        self._index = 0

    async def __anext__(self):
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        message = self._messages[self._index]
        self._index += 1
        return message


def _actor(session: str = "session-1") -> ActorContext:
    return ActorContext("local-owner", Actor.OWNER, session, "websocket")


def _runtime(tmp_path, now: float = 1000.0) -> ProductivityRuntime:
    return ProductivityRuntime(
        ProductivityService(SqliteApprovalStore(str(tmp_path / "approvals.db"))),
        lambda: now,
        lambda: "approval-1",
    )


def _research_stack(now: float = 1000.0, proposal_id: str = "proposal-1"):
    factory = ResearchProposalFactory(lambda: now, lambda: proposal_id)
    registry = ResearchPreparationRegistry()
    return factory, registry


def _pair(server: WebSocketServer, websocket: _WebSocket) -> None:
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "pair", "code": server.pairing_code}),
        )
    )


def _server(tmp_path, proposal_id: str = "proposal-1") -> tuple[WebSocketServer, _WebSocket]:
    factory, registry = _research_stack(proposal_id=proposal_id)
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        research_factory=factory,
        research_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    return server, websocket


def _prepare_research(
    server: WebSocketServer,
    websocket: _WebSocket,
    *,
    request_id: str = "research-req-1",
    query: str = "What changed in the latest release?",
    domains: list[str] | None = None,
    max_results: int | None = None,
) -> None:
    payload: dict[str, object] = {
        "type": "productivity_research_prepare",
        "request_id": request_id,
        "query": query,
    }
    if domains is not None:
        payload["domains"] = domains
    if max_results is not None:
        payload["max_results"] = max_results
    asyncio.run(server._handle_message(websocket, json.dumps(payload)))


@pytest.mark.parametrize(
    "payload",
    [
        {
            "request_id": "research-req-1",
            "query": "What changed in the latest release?",
        },
        {
            "request_id": "research-req-2",
            "query": "  keep exact spacing  ",
            "domains": ["example.com", "docs.example.com"],
            "max_results": 12,
        },
    ],
)
def test_server_prepare_returns_canonical_confirmation_and_retains_private_input(
    tmp_path, payload
):
    server, websocket = _server(tmp_path)
    registry = server._research_registry
    assert registry is not None

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_research_prepare", **payload}),
        )
    )

    message = websocket.sent[-1]
    assert message["type"] == "productivity_confirmation_required"
    assert message["proposal_id"] == "proposal-1"
    assert message["request_id"] == payload["request_id"]
    assert message["action"] == "browser.research"
    assert validate_server_message(message) is None
    actor = server._derive_actor_context(websocket).actor_context
    retained = registry.get(actor, "proposal-1")
    assert retained is not None
    assert retained.query == payload["query"]


def test_server_prepare_canonicalizes_idna_domains_in_confirmation(tmp_path):
    server, websocket = _server(tmp_path)
    _prepare_research(
        server,
        websocket,
        request_id="research-idna",
        query="q",
        domains=["münchen.de"],
    )
    message = websocket.sent[-1]
    assert message["type"] == "productivity_confirmation_required"
    domain_payload = next(
        entry for entry in message["payload"] if entry["label"] == "Allowed domains"
    )
    assert domain_payload["value"] == "xn--mnchen-3ya.de"


def test_server_prepare_echoes_request_id_on_safe_validation_error(tmp_path):
    server, websocket = _server(tmp_path)
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_research_prepare",
                    "request_id": "research-bad",
                    "query": "   ",
                }
            ),
        )
    )
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "research-bad"
    assert "message" not in message
    assert validate_server_message(message) is None


@pytest.mark.parametrize(
    "domains",
    [
        ["127.0.0.1"],
        ["localhost"],
        ["example.com", "example.com"],
        ["münchen.de", "xn--mnchen-3ya.de"],
    ],
)
def test_server_rejects_invalid_domains_before_registry(tmp_path, domains):
    server, websocket = _server(tmp_path)
    registry = server._research_registry
    assert registry is not None
    _prepare_research(
        server,
        websocket,
        request_id="research-domain-bad",
        query="q",
        domains=domains,
    )
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "research-domain-bad"
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is None


def test_server_rejects_malformed_prepare_messages_before_factory(tmp_path):
    server, websocket = _server(tmp_path)
    bad = {
        "type": "productivity_research_prepare",
        "request_id": "research-bad",
        "query": "q",
        "proposal_id": "client-proposal",
    }
    assert validate_client_message(bad) == "Unknown field: proposal_id"
    asyncio.run(server._handle_message(websocket, json.dumps(bad)))
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "research-bad"


def test_cross_session_registry_isolation(tmp_path):
    factory, registry = _research_stack(proposal_id="proposal-1")
    actor_a = _actor("session-a")
    actor_b = _actor("session-b")
    prepared = factory.prepare(actor_a, "private query", domains=["example.com"])
    registry.put(actor_a, "proposal-1", prepared.input)
    assert registry.get(actor_b, "proposal-1") is None


def test_server_cancel_removes_retained_research_input(tmp_path):
    server, websocket = _server(tmp_path)
    registry = server._research_registry
    assert registry is not None

    _prepare_research(server, websocket, request_id="research-cancel")
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_cancel", "proposal_id": "proposal-1"}),
        )
    )
    assert websocket.sent[-1]["status"] == "cancelled"
    assert registry.get(actor, "proposal-1") is None


def test_confirm_completed_removes_research_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._research_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_research(server, websocket)
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    monkeypatch.setattr(
        runtime,
        "confirm_and_ticket",
        lambda *args, **kwargs: ConfirmationResult(
            public_message=update_message("proposal-1", "completed"),
            proposal_id="proposal-1",
        ),
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal-1",
                    "scope": "once",
                }
            ),
        )
    )
    assert websocket.sent[-1]["status"] == "completed"
    assert registry.get(actor, "proposal-1") is None


def test_confirm_cancelled_removes_research_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._research_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_research(server, websocket)
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    monkeypatch.setattr(
        runtime,
        "confirm_and_ticket",
        lambda *args, **kwargs: ConfirmationResult(
            public_message=update_message("proposal-1", "cancelled"),
            proposal_id="proposal-1",
        ),
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal-1",
                    "scope": "once",
                }
            ),
        )
    )
    assert websocket.sent[-1]["status"] == "cancelled"
    assert registry.get(actor, "proposal-1") is None


def test_expired_confirm_removes_research_entry(tmp_path):
    clock_value = [1000.0]
    runtime = ProductivityRuntime(
        ProductivityService(SqliteApprovalStore(str(tmp_path / "approvals.db"))),
        lambda: clock_value[0],
        lambda: "approval-1",
    )
    factory, registry = _research_stack(now=1000.0)
    server = WebSocketServer(
        object(),
        productivity_runtime=runtime,
        research_factory=factory,
        research_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    _prepare_research(server, websocket, request_id="research-expired")
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    clock_value[0] = 1900.0
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal-1",
                    "scope": "once",
                }
            ),
        )
    )
    assert websocket.sent[-1]["code"] == "proposal_expired"
    assert registry.get(actor, "proposal-1") is None


def test_expired_status_removes_research_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._research_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_research(server, websocket, request_id="research-status-expired")
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    monkeypatch.setattr(
        runtime,
        "status",
        lambda *args, **kwargs: error_message(
            "proposal-1", ProductivityCode.PROPOSAL_EXPIRED
        ),
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_status", "proposal_id": "proposal-1"}),
        )
    )
    assert websocket.sent[-1]["code"] == "proposal_expired"
    assert registry.get(actor, "proposal-1") is None


def test_transient_error_preserves_research_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._research_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_research(server, websocket, request_id="research-transient")
    actor = server._derive_actor_context(websocket).actor_context
    retained = registry.get(actor, "proposal-1")
    assert retained is not None

    monkeypatch.setattr(
        runtime,
        "confirm_and_ticket",
        lambda *args, **kwargs: ConfirmationResult(
            public_message=error_message(
                "proposal-1", ProductivityCode.CONSUMPTION_FAILED
            ),
            proposal_id="proposal-1",
        ),
    )
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal-1",
                    "scope": "once",
                }
            ),
        )
    )
    assert websocket.sent[-1]["code"] == "unavailable"
    assert registry.get(actor, "proposal-1") is retained


def test_cross_session_cannot_remove_owner_research_entry(tmp_path):
    factory, registry = _research_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        research_factory=factory,
        research_registry=registry,
    )
    websocket_a = _WebSocket()
    websocket_b = _WebSocket()
    _pair(server, websocket_a)
    _pair(server, websocket_b)
    _prepare_research(server, websocket_a, request_id="research-owner")
    actor_a = server._derive_actor_context(websocket_a).actor_context
    assert registry.get(actor_a, "proposal-1") is not None

    asyncio.run(
        server._handle_message(
            websocket_b,
            json.dumps(
                {
                    "type": "productivity_confirm",
                    "proposal_id": "proposal-1",
                    "scope": "once",
                }
            ),
        )
    )
    assert websocket_b.sent[-1]["type"] == "productivity_error"
    assert registry.get(actor_a, "proposal-1") is not None


def test_disconnect_clears_session_registry_entries_via_connection_finally(tmp_path):
    factory, registry = _research_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        research_factory=factory,
        research_registry=registry,
    )
    websocket = _QueuedWebSocket(
        [
            json.dumps({"type": "pair", "code": server.pairing_code}),
            json.dumps(
                {
                    "type": "productivity_research_prepare",
                    "request_id": "research-disconnect",
                    "query": "q",
                }
            ),
        ]
    )

    asyncio.run(server._handle_connection(websocket))

    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is None


def test_prepare_failure_removes_registry_entry(tmp_path):
    factory, registry = _research_stack()
    runtime = _runtime(tmp_path)

    def fail_prepare(actor, proposal):
        return {
            "type": "productivity_error",
            "proposal_id": proposal.proposal_id,
            "code": "unavailable",
        }

    runtime.prepare = fail_prepare  # type: ignore[method-assign]
    server = WebSocketServer(
        object(),
        productivity_runtime=runtime,
        research_factory=factory,
        research_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_research_prepare",
                    "request_id": "research-fail",
                    "query": "q",
                }
            ),
        )
    )
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is None
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "research-fail"


def test_factory_exception_maps_to_safe_error_without_details(tmp_path, monkeypatch):
    factory, registry = _research_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        research_factory=factory,
        research_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)

    def explode(*args, **kwargs):
        raise RuntimeError("secret browser path private/research")

    monkeypatch.setattr(factory, "prepare", explode)
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "productivity_research_prepare",
                    "request_id": "research-secret",
                    "query": "q",
                }
            ),
        )
    )
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "research-secret"
    assert set(message.keys()) <= {"type", "proposal_id", "code", "request_id"}
    assert "message" not in message
    assert "provider" not in json.dumps(message)
    assert "private/research" not in json.dumps(message)


def test_bootstrap_is_lazy_and_side_effect_free(tmp_path):
    state_home = tmp_path / "private-home"
    env = {**os.environ, "HIKARI_HOME": str(state_home)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import hikari; import core.productivity.bootstrap",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert not (state_home / "policy").exists()

    factory, registry = create_research_preparation(
        proposal_id_factory=lambda: "proposal-bootstrap",
    )
    assert factory is not None
    assert registry is not None


def test_server_path_wires_research_bootstrap_separately(monkeypatch):
    orchestrator = object()
    server = MagicMock()
    server_class = MagicMock(return_value=server)
    research_factory = object()
    research_registry = object()
    monkeypatch.setitem(
        sys.modules,
        "core.orchestrator",
        __import__("types").SimpleNamespace(get_orchestrator=lambda: orchestrator),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.productivity.bootstrap",
        __import__("types").SimpleNamespace(
            create_productivity_runtime=MagicMock(return_value=object()),
            create_email_draft_preparation=MagicMock(return_value=(object(), object())),
            create_calendar_preparation=MagicMock(return_value=(object(), object(), object())),
            create_research_preparation=MagicMock(
                return_value=(research_factory, research_registry)
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.jobs.bootstrap",
        __import__("types").SimpleNamespace(create_scheduled_job_runtime=MagicMock(return_value=object())),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.server",
        __import__("types").SimpleNamespace(WebSocketServer=server_class),
    )

    hikari.run_server("127.0.0.1", 9876)

    kwargs = server_class.call_args.kwargs
    assert kwargs["research_factory"] is research_factory
    assert kwargs["research_registry"] is research_registry


def test_prepare_handler_performs_no_external_execution(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("external execution attempted")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    _prepare_research(
        server,
        websocket,
        request_id="research-no-exec",
        query="q",
        domains=["example.com"],
    )
    assert websocket.sent[-1]["type"] == "productivity_confirmation_required"
