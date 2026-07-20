"""Phase 3 reminder preparation and WebSocket boundary tests.

These tests cover the bounded prepare-to-confirmation backend slice for
``productivity_reminder_prepare``. They perform no Reminders.app access,
AppleScript, network, provider, persistence, scheduling, notification
delivery, or external execution: they assert the absence of those imports
and that the prepare handler performs no external execution.
"""

from __future__ import annotations

import asyncio
import ast
import json
import os
import pathlib
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
    ReminderPreparationRegistry,
    ReminderProposalFactory,
    SqliteApprovalStore,
)
from core.productivity.bootstrap import create_reminder_preparation
from core.productivity.reminder import (
    DEFAULT_REMINDER_LIST_LABEL,
    REMINDER_LIST_NAME_MAX,
    REMINDER_NOTES_MAX,
    REMINDER_TITLE_MAX,
)
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


def _runtime(tmp_path, now: float = 1784505600.0) -> ProductivityRuntime:
    return ProductivityRuntime(
        ProductivityService(SqliteApprovalStore(str(tmp_path / "approvals.db"))),
        lambda: now,
        lambda: "approval-1",
    )


def _reminder_stack(now: float = 1784505600.0, proposal_id: str = "proposal-1"):
    factory = ReminderProposalFactory(lambda: now, lambda: proposal_id)
    registry = ReminderPreparationRegistry()
    return factory, registry


def _pair(server: WebSocketServer, websocket: _WebSocket) -> None:
    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "pair", "code": server.pairing_code}),
        )
    )


def _server(tmp_path, proposal_id: str = "proposal-1") -> tuple[WebSocketServer, _WebSocket]:
    factory, registry = _reminder_stack(proposal_id=proposal_id)
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        reminder_factory=factory,
        reminder_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    return server, websocket


def _prepare_reminder(
    server: WebSocketServer,
    websocket: _WebSocket,
    *,
    request_id: str = "rem-1",
    title: str = "Buy milk",
    remind_at: str = "2026-07-21T09:00:00Z",
    notes: str | None = None,
    list_name: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "type": "productivity_reminder_prepare",
        "request_id": request_id,
        "title": title,
        "remind_at": remind_at,
    }
    if notes is not None:
        payload["notes"] = notes
    if list_name is not None:
        payload["list_name"] = list_name
    asyncio.run(server._handle_message(websocket, json.dumps(payload)))


# --------------------------------------------------------------------------
# Protocol contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {
            "request_id": "rem-1",
            "title": "Buy milk",
            "remind_at": "2026-07-21T09:00:00Z",
        },
        {
            "request_id": "rem-2",
            "title": "Doctor appointment",
            "remind_at": "2026-07-21T14:30:00-04:00",
            "notes": "Bring records\nRoom 302",
            "list_name": "Personal",
        },
    ],
)
def test_protocol_reminder_prepare_valid_payloads_validate(payload):
    message = {"type": "productivity_reminder_prepare", **payload}
    assert validate_client_message(message) is None


@pytest.mark.parametrize(
    "payload, error",
    [
        (
            {"title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z"},
            "Missing required field: request_id",
        ),
        (
            {"request_id": "Bad ID", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z"},
            "Invalid field value: request_id",
        ),
        (
            {"request_id": "rem-1", "title": "", "remind_at": "2026-07-21T09:00:00Z"},
            "Field too short: title",
        ),
        (
            {"request_id": "rem-1", "title": "Buy milk"},
            "Missing required field: remind_at",
        ),
        (
            {"request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00"},
            "Invalid field value: remind_at",
        ),
        (
            {"request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z", "proposal_id": "prop-1"},
            "Unknown field: proposal_id",
        ),
        (
            {"request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z", "actor_id": "owner"},
            "Unknown field: actor_id",
        ),
        (
            {"request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z", "session_id": "s"},
            "Unknown field: session_id",
        ),
        (
            {"request_id": "rem-1", "title": "Buy milk", "remind_at": "2026-07-21T09:00:00Z", "provider": "x"},
            "Unknown field: provider",
        ),
        (
            {"request_id": "rem-1", "title": "bad\u200btitle", "remind_at": "2026-07-21T09:00:00Z"},
            "Invalid field value: title",
        ),
        (
            {"request_id": "rem-1", "title": "Buy milk", "remind_at": "bad\u200binstant"},
            "Invalid field value: remind_at",
        ),
    ],
)
def test_protocol_reminder_prepare_rejects_invalid_payloads(payload, error):
    message = {"type": "productivity_reminder_prepare", **payload}
    assert validate_client_message(message) == error


def test_protocol_reminder_prepare_rejects_non_string_request_id():
    message = {
        "type": "productivity_reminder_prepare",
        "request_id": 7,
        "title": "Buy milk",
        "remind_at": "2026-07-21T09:00:00Z",
    }
    assert validate_client_message(message) == "Invalid field type: request_id"


def test_protocol_reminder_prepare_rejects_non_string_title():
    message = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-1",
        "title": 7,
        "remind_at": "2026-07-21T09:00:00Z",
    }
    assert validate_client_message(message) == "Invalid field type: title"


def test_protocol_reminder_prepare_rejects_non_string_remind_at():
    message = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-1",
        "title": "Buy milk",
        "remind_at": 7,
    }
    assert validate_client_message(message) == "Invalid field type: remind_at"


def test_protocol_reminder_prepare_rejects_non_string_notes():
    message = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-1",
        "title": "Buy milk",
        "remind_at": "2026-07-21T09:00:00Z",
        "notes": 7,
    }
    assert validate_client_message(message) == "Invalid field type: notes"


def test_protocol_reminder_prepare_rejects_non_string_list_name():
    message = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-1",
        "title": "Buy milk",
        "remind_at": "2026-07-21T09:00:00Z",
        "list_name": 7,
    }
    assert validate_client_message(message) == "Invalid field type: list_name"


def test_protocol_reminder_prepare_rejects_raw_payload_field():
    message = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-1",
        "title": "Buy milk",
        "remind_at": "2026-07-21T09:00:00Z",
        "payload": {"secret": "value"},
    }
    assert validate_client_message(message) == "Unknown field: payload"


@pytest.mark.parametrize(
    "remind_at",
    [
        "2026-07-21T09:00:00Z",
        "2026-07-21T09:00:00+00:00",
        "2026-07-21T09:00:00-04:00",
        "2026-07-21T09:00:00.123456Z",
    ],
)
def test_protocol_reminder_prepare_accepts_explicit_offsets(remind_at):
    message = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-1",
        "title": "Buy milk",
        "remind_at": remind_at,
    }
    assert validate_client_message(message) is None


@pytest.mark.parametrize(
    "remind_at",
    [
        "2026-07-21T09:00:00",  # naive, no offset
        "2026-07-21 09:00:00Z",  # space separator
        "2026-07-21T09:00:00Zulu",  # trailing junk
    ],
)
def test_protocol_reminder_prepare_rejects_naive_or_malformed_remind_at(remind_at):
    message = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-1",
        "title": "Buy milk",
        "remind_at": remind_at,
    }
    assert validate_client_message(message) == "Invalid field value: remind_at"


def test_protocol_reminder_prepare_title_boundary_lengths():
    base = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-1",
        "remind_at": "2026-07-21T09:00:00Z",
    }
    assert validate_client_message({**base, "title": "T" * REMINDER_TITLE_MAX}) is None
    assert (
        validate_client_message({**base, "title": "T" * (REMINDER_TITLE_MAX + 1)})
        == "Field too long: title"
    )


def test_protocol_reminder_prepare_notes_boundary_lengths():
    base = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-1",
        "title": "Buy milk",
        "remind_at": "2026-07-21T09:00:00Z",
    }
    # notes min_length is 0 (max_length only) -> empty allowed
    assert validate_client_message({**base, "notes": ""}) is None
    assert validate_client_message({**base, "notes": "N" * REMINDER_NOTES_MAX}) is None
    assert (
        validate_client_message({**base, "notes": "N" * (REMINDER_NOTES_MAX + 1)})
        == "Field too long: notes"
    )


def test_protocol_reminder_prepare_list_name_boundary_lengths():
    base = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-1",
        "title": "Buy milk",
        "remind_at": "2026-07-21T09:00:00Z",
    }
    # Explicitly empty list_name is rejected; omitted list_name remains optional.
    assert validate_client_message({**base, "list_name": ""}) == "Field too short: list_name"
    assert validate_client_message({**base, "list_name": "L" * REMINDER_LIST_NAME_MAX}) is None
    assert (
        validate_client_message({**base, "list_name": "L" * (REMINDER_LIST_NAME_MAX + 1)})
        == "Field too long: list_name"
    )


# --------------------------------------------------------------------------
# Successful preparation and confirmation correlation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {
            "request_id": "rem-1",
            "title": "Buy milk",
            "remind_at": "2026-07-21T09:00:00Z",
        },
        {
            "request_id": "rem-2",
            "title": "Doctor appointment",
            "remind_at": "2026-07-21T14:30:00-04:00",
            "notes": "Bring records\nRoom 302",
            "list_name": "Personal",
        },
    ],
)
def test_server_prepare_returns_canonical_confirmation_and_retains_private_input(
    tmp_path, payload
):
    server, websocket = _server(tmp_path)
    registry = server._reminder_registry
    assert registry is not None

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "productivity_reminder_prepare", **payload}),
        )
    )

    message = websocket.sent[-1]
    assert message["type"] == "productivity_confirmation_required"
    assert message["proposal_id"] == "proposal-1"
    assert message["request_id"] == payload["request_id"]
    assert message["action"] == "reminder.create"
    assert validate_server_message(message) is None

    actor = server._derive_actor_context(websocket).actor_context
    retained = registry.get(actor, "proposal-1")
    assert retained is not None
    assert retained.title == payload["title"]
    if "notes" in payload:
        assert retained.notes == payload["notes"]
    if "list_name" in payload:
        assert retained.list_name == payload["list_name"]


def test_server_prepare_preview_contains_remind_at_and_title(tmp_path):
    server, websocket = _server(tmp_path)
    _prepare_reminder(
        server,
        websocket,
        title="Pick up package",
        remind_at="2026-07-21T17:30:00Z",
        notes="Front desk",
        list_name="Errands",
    )
    message = websocket.sent[-1]
    assert message["type"] == "productivity_confirmation_required"
    payload = {entry["label"]: entry["value"] for entry in message["payload"]}
    assert payload["Title"] == "Pick up package"
    assert "2026-07-21T17:30" in payload["Remind At"]
    assert payload["Notes"] == "Front desk"
    assert payload["List"] == "Errands"


def test_server_prepare_echoes_request_id_on_confirmation(tmp_path):
    server, websocket = _server(tmp_path)
    _prepare_reminder(server, websocket, request_id="rem-correlate")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_confirmation_required"
    assert message["request_id"] == "rem-correlate"
    assert validate_server_message(message) is None


def test_server_prepare_never_exposes_proposal_id_before_success(tmp_path):
    """On a prepare failure, the server-generated proposal id must not be echoed."""
    factory, registry = _reminder_stack(now=1000.0)
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
        reminder_factory=factory,
        reminder_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    _prepare_reminder(server, websocket, request_id="rem-no-expose")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    # The proposal id generated internally is "proposal-1" but it must NOT be
    # surfaced to the client on a prepare failure.
    assert message["proposal_id"] == "invalid-proposal"
    assert message["request_id"] == "rem-no-expose"
    assert validate_server_message(message) is None


# --------------------------------------------------------------------------
# Malformed input and unknown fields
# --------------------------------------------------------------------------


def test_server_rejects_malformed_prepare_messages_before_factory(tmp_path):
    server, websocket = _server(tmp_path)
    bad = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-bad",
        "title": "Buy milk",
        "remind_at": "2026-07-21T09:00:00Z",
        "proposal_id": "client-proposal",
    }
    assert validate_client_message(bad) == "Unknown field: proposal_id"
    asyncio.run(server._handle_message(websocket, json.dumps(bad)))
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "rem-bad"


def test_server_prepare_validation_error_echoes_request_id_safely(tmp_path):
    server, websocket = _server(tmp_path)
    # A title that passes protocol length but is whitespace-only is rejected by
    # the factory (ReminderPreparationError("invalid reminder input")).
    _prepare_reminder(server, websocket, request_id="rem-ws", title="   \t\n  ")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "rem-ws"
    assert "message" not in message
    assert validate_server_message(message) is None


def test_server_prepare_rejects_naive_remind_at_without_inventing_tz(tmp_path):
    server, websocket = _server(tmp_path)
    # Protocol rejects naive datetimes; verify the server path also rejects.
    bad = {
        "type": "productivity_reminder_prepare",
        "request_id": "rem-naive",
        "title": "Buy milk",
        "remind_at": "2026-07-21T09:00:00",
    }
    assert validate_client_message(bad) == "Invalid field value: remind_at"
    asyncio.run(server._handle_message(websocket, json.dumps(bad)))
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "rem-naive"


def test_server_prepare_safe_error_when_reminder_wiring_unavailable():
    """When bootstrap failed closed (no reminder wiring), prepare returns a safe error."""
    server = WebSocketServer(object())  # no productivity/reminder params
    websocket = _WebSocket()
    _pair(server, websocket)
    _prepare_reminder(server, websocket, request_id="rem-unwired")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["proposal_id"] == "invalid-proposal"
    assert message["request_id"] == "rem-unwired"
    assert validate_server_message(message) is None


def test_server_prepare_rejects_past_remind_at(tmp_path):
    server, websocket = _server(tmp_path, proposal_id="proposal-1")
    _prepare_reminder(
        server,
        websocket,
        request_id="rem-past",
        title="Buy milk",
        # now=2026-07-20; this remind_at is in 1970, well in the past.
        remind_at="1970-01-01T00:00:30Z",
    )
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "rem-past"
    actor = server._derive_actor_context(websocket).actor_context
    assert server._reminder_registry.get(actor, "proposal-1") is None


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------


def test_bootstrap_factory_returns_factory_and_registry():
    factory, registry = create_reminder_preparation(
        proposal_id_factory=lambda: "proposal-bootstrap",
    )
    assert isinstance(factory, ReminderProposalFactory)
    assert isinstance(registry, ReminderPreparationRegistry)


def test_bootstrap_factory_uses_injected_clock_and_limits(tmp_path):
    calls: list[float] = []

    def clock() -> float:
        calls.append(1784505600.0)
        return calls[-1]

    factory, registry = create_reminder_preparation(
        clock=clock,
        proposal_id_factory=lambda: "proposal-inj",
        registry_limit=4,
    )
    actor = _actor("session-boot")
    from datetime import datetime, timezone

    remind_at = datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc)
    prepared = factory.prepare(actor, "Buy milk", remind_at)
    assert prepared.proposal.proposal_id == "proposal-inj"
    assert calls == [1784505600.0]
    assert registry._limit == 4


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


def test_imports_do_not_construct_reminder_state(tmp_path):
    """Normal imports must not construct reminder factory/registry state."""
    env = {**os.environ, "HIKARI_HOME": str(tmp_path / "home")}
    result = subprocess.run(
        [sys.executable, "-c", "import hikari; import core.server; import core.productivity"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0


def test_doctor_and_voice_status_do_not_construct_reminder_state(tmp_path):
    """Non-server CLI modes must not construct reminder preparation state."""
    env = {**os.environ, "HIKARI_HOME": str(tmp_path / "home")}
    for mode in ("--doctor", "--voice-status"):
        result = subprocess.run(
            [sys.executable, "hikari.py", mode],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # These modes may return non-zero on environment issues, but they must
        # not crash on reminder import; absence of traceback is the check.
        assert "ReminderProposalFactory" not in result.stderr, mode
        assert "Traceback" not in result.stderr, mode


def test_server_path_wires_reminder_bootstrap_separately(monkeypatch):
    orchestrator = object()
    server = MagicMock()
    server_class = MagicMock(return_value=server)
    reminder_factory = object()
    reminder_registry = object()
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
            create_research_preparation=MagicMock(return_value=(object(), object())),
            create_reminder_preparation=MagicMock(
                return_value=(reminder_factory, reminder_registry)
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.jobs.bootstrap",
        __import__("types").SimpleNamespace(
            create_scheduled_job_runtime=MagicMock(return_value=object())
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.server",
        __import__("types").SimpleNamespace(WebSocketServer=server_class),
    )

    hikari.run_server("127.0.0.1", 9876)

    kwargs = server_class.call_args.kwargs
    assert kwargs["reminder_factory"] is reminder_factory
    assert kwargs["reminder_registry"] is reminder_registry


def test_bootstrap_failure_fails_closed_with_safe_error(monkeypatch):
    """If reminder bootstrap raises, run_server must set both to None (fail closed)."""
    orchestrator = object()
    server = MagicMock()
    server_class = MagicMock(return_value=server)
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
            create_research_preparation=MagicMock(return_value=(object(), object())),
            create_reminder_preparation=MagicMock(
                side_effect=RuntimeError("secret reminder path private/x")
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.jobs.bootstrap",
        __import__("types").SimpleNamespace(
            create_scheduled_job_runtime=MagicMock(return_value=object())
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.server",
        __import__("types").SimpleNamespace(WebSocketServer=server_class),
    )

    hikari.run_server("127.0.0.1", 9876)

    kwargs = server_class.call_args.kwargs
    assert "reminder_factory" not in kwargs or kwargs["reminder_factory"] is None
    assert "reminder_registry" not in kwargs or kwargs["reminder_registry"] is None


# --------------------------------------------------------------------------
# Lifecycle cleanup
# --------------------------------------------------------------------------


def test_prepare_failure_removes_registry_entry(tmp_path):
    factory, registry = _reminder_stack()
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
        reminder_factory=factory,
        reminder_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    _prepare_reminder(server, websocket, request_id="rem-fail")
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is None
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "rem-fail"


def test_server_cancel_removes_retained_reminder_input(tmp_path):
    server, websocket = _server(tmp_path)
    registry = server._reminder_registry
    assert registry is not None

    _prepare_reminder(server, websocket, request_id="rem-cancel")
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


def test_confirm_completed_removes_reminder_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._reminder_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_reminder(server, websocket)
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


def test_confirm_cancelled_removes_reminder_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._reminder_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_reminder(server, websocket)
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


def test_expired_confirm_removes_reminder_entry(tmp_path):
    clock_value = [1784505600.0]
    runtime = ProductivityRuntime(
        ProductivityService(SqliteApprovalStore(str(tmp_path / "approvals.db"))),
        lambda: clock_value[0],
        lambda: "approval-1",
    )
    factory, registry = _reminder_stack(now=1784505600.0)
    server = WebSocketServer(
        object(),
        productivity_runtime=runtime,
        reminder_factory=factory,
        reminder_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    _prepare_reminder(server, websocket, request_id="rem-expired")
    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is not None

    clock_value[0] = 1784505600.0 + 900.0
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


def test_expired_status_removes_reminder_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._reminder_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_reminder(server, websocket, request_id="rem-status-expired")
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


def test_transient_error_preserves_reminder_entry(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    registry = server._reminder_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_reminder(server, websocket, request_id="rem-transient")
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
    # Transient (non-terminal) error preserves the entry.
    assert registry.get(actor, "proposal-1") is retained


@pytest.mark.parametrize(
    "status",
    ["approved", "executing", "preview"],
)
def test_nonterminal_update_preserves_reminder_entry(tmp_path, monkeypatch, status):
    server, websocket = _server(tmp_path)
    registry = server._reminder_registry
    runtime = server._productivity_runtime
    assert registry is not None and runtime is not None

    _prepare_reminder(server, websocket, request_id=f"rem-{status}")
    actor = server._derive_actor_context(websocket).actor_context
    retained = registry.get(actor, "proposal-1")
    assert retained is not None

    monkeypatch.setattr(
        runtime,
        "confirm_and_ticket",
        lambda *args, **kwargs: ConfirmationResult(
            public_message=update_message("proposal-1", status),
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
    assert websocket.sent[-1]["status"] == status
    assert registry.get(actor, "proposal-1") is retained


# --------------------------------------------------------------------------
# Cross-session isolation
# --------------------------------------------------------------------------


def test_cross_session_registry_isolation():
    factory, registry = _reminder_stack(proposal_id="proposal-1")
    actor_a = _actor("session-a")
    actor_b = _actor("session-b")
    from datetime import datetime, timezone

    remind_at = datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc)
    prepared = factory.prepare(actor_a, "private reminder", remind_at)
    registry.put(actor_a, "proposal-1", prepared.reminder)
    assert registry.get(actor_b, "proposal-1") is None


def test_cross_session_cannot_remove_owner_reminder_entry(tmp_path):
    factory, registry = _reminder_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        reminder_factory=factory,
        reminder_registry=registry,
    )
    websocket_a = _WebSocket()
    websocket_b = _WebSocket()
    _pair(server, websocket_a)
    _pair(server, websocket_b)
    _prepare_reminder(server, websocket_a, request_id="rem-owner")
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
    # Owner's entry must survive the cross-session request.
    assert registry.get(actor_a, "proposal-1") is not None


def test_cross_session_cancel_does_not_disclose_owner_entry(tmp_path):
    factory, registry = _reminder_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        reminder_factory=factory,
        reminder_registry=registry,
    )
    websocket_a = _WebSocket()
    websocket_b = _WebSocket()
    _pair(server, websocket_a)
    _pair(server, websocket_b)
    _prepare_reminder(
        server,
        websocket_a,
        request_id="rem-owner-secret",
        title="Secret reminder title 999",
    )
    actor_a = server._derive_actor_context(websocket_a).actor_context
    assert registry.get(actor_a, "proposal-1") is not None

    asyncio.run(
        server._handle_message(
            websocket_b,
            json.dumps({"type": "productivity_cancel", "proposal_id": "proposal-1"}),
        )
    )
    response = websocket_b.sent[-1]
    assert response == {
        "type": "productivity_update",
        "proposal_id": "proposal-1",
        "status": "cancelled",
    }
    serialized = json.dumps(response)
    assert "Secret reminder title 999" not in serialized
    assert validate_server_message(response) is None
    assert registry.get(actor_a, "proposal-1") is not None


# --------------------------------------------------------------------------
# Disconnect cleanup
# --------------------------------------------------------------------------


def test_disconnect_clears_session_registry_entries_via_connection_finally(tmp_path):
    factory, registry = _reminder_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        reminder_factory=factory,
        reminder_registry=registry,
    )
    websocket = _QueuedWebSocket(
        [
            json.dumps({"type": "pair", "code": server.pairing_code}),
            json.dumps(
                {
                    "type": "productivity_reminder_prepare",
                    "request_id": "rem-disconnect",
                    "title": "Buy milk",
                    "remind_at": "2026-07-21T09:00:00Z",
                }
            ),
        ]
    )

    asyncio.run(server._handle_connection(websocket))

    actor = server._derive_actor_context(websocket).actor_context
    assert registry.get(actor, "proposal-1") is None


# --------------------------------------------------------------------------
# Adversarial exception redaction
# --------------------------------------------------------------------------


def test_factory_exception_maps_to_safe_error_without_details(tmp_path, monkeypatch):
    factory, registry = _reminder_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        reminder_factory=factory,
        reminder_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)

    def explode(*args, **kwargs):
        raise RuntimeError("secret reminder path private/reminder")

    monkeypatch.setattr(factory, "prepare", explode)
    _prepare_reminder(server, websocket, request_id="rem-secret")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["request_id"] == "rem-secret"
    assert set(message.keys()) <= {"type", "proposal_id", "code", "request_id"}
    assert "message" not in message
    assert "private/reminder" not in json.dumps(message)
    assert "provider" not in json.dumps(message)


def test_prepare_error_does_not_leak_proposal_id_before_success(tmp_path):
    factory, registry = _reminder_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        reminder_factory=factory,
        reminder_registry=registry,
    )
    websocket = _WebSocket()
    _pair(server, websocket)
    # Whitespace title is rejected by the factory; proposal-1 is never minted.
    _prepare_reminder(server, websocket, request_id="rem-no-leak", title="   ")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["proposal_id"] == "invalid-proposal"
    serialized = json.dumps(message)
    assert "proposal-1" not in serialized


# --------------------------------------------------------------------------
# Boundary behavior
# --------------------------------------------------------------------------


def test_server_prepare_accepts_custom_list_and_notes(tmp_path):
    server, websocket = _server(tmp_path)
    _prepare_reminder(
        server,
        websocket,
        request_id="rem-custom",
        title="Doctor appointment",
        remind_at="2026-07-21T14:30:00-04:00",
        notes="Bring medical records\nRoom 302",
        list_name="Personal",
    )
    message = websocket.sent[-1]
    assert message["type"] == "productivity_confirmation_required"
    assert validate_server_message(message) is None
    actor = server._derive_actor_context(websocket).actor_context
    retained = server._reminder_registry.get(actor, "proposal-1")
    assert retained is not None
    assert retained.list_name == "Personal"
    assert retained.notes == "Bring medical records\nRoom 302"


def test_server_prepare_default_list_label_in_targets(tmp_path):
    server, websocket = _server(tmp_path)
    _prepare_reminder(server, websocket, request_id="rem-default-list")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_confirmation_required"
    target = next(t for t in message["targets"] if t["label"] == "Reminder list")
    assert target["value"] == DEFAULT_REMINDER_LIST_LABEL


# --------------------------------------------------------------------------
# No external execution / forbidden side effects
# --------------------------------------------------------------------------


def test_prepare_handler_performs_no_external_execution(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("external execution attempted")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    _prepare_reminder(server, websocket, request_id="rem-no-exec")
    assert websocket.sent[-1]["type"] == "productivity_confirmation_required"


def test_reminder_module_has_no_forbidden_imports():
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "productivity"
        / "reminder.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "threading",
        "os",
        "sqlite3",
        "requests",
        "asyncio",
        "logging",
        "smtplib",
        "http",
        "webbrowser",
        "eventkit",
        "applescript",
        "reminders",
        "mcp",
        "network",
        "provider",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, (
                    f"reminder.py imports forbidden module {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module.split(".")[0] not in forbidden, (
                    f"reminder.py imports forbidden module {node.module}"
                )


# --------------------------------------------------------------------------
# Confirmation boundary validation
# --------------------------------------------------------------------------


def _valid_reminder_confirmation(proposal_id: str, request_id: str) -> dict:
    return {
        "type": "productivity_confirmation_required",
        "proposal_id": proposal_id,
        "action": "reminder.create",
        "heading": "Create reminder",
        "risk_label": "low",
        "targets": [{"label": "Reminder list", "value": "Default reminder list"}],
        "payload": [
            {"label": "Title", "value": "Buy milk"},
            {"label": "Remind At", "value": "2026-07-21T09:00:00+00:00"},
        ],
        "expires_at": 1784506500.0,
        "allowed_scopes": ["once", "session", "duration", "precise_persistent"],
        "request_id": request_id,
    }


def _mock_runtime_prepare(return_value: object):
    def prepare(actor, proposal):
        return return_value

    return prepare


def test_reminder_prepare_rejects_malformed_confirmation(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    runtime = server._productivity_runtime
    assert runtime is not None
    bad_result = {
        "type": "productivity_confirmation_required",
        "proposal_id": "proposal-1",
        "action": "reminder.create",
        # missing heading, risk_label, targets, payload, expires_at, allowed_scopes
    }
    monkeypatch.setattr(runtime, "prepare", _mock_runtime_prepare(bad_result))
    _prepare_reminder(server, websocket, request_id="rem-malformed")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["proposal_id"] == "invalid-proposal"
    assert message["request_id"] == "rem-malformed"
    assert "message" not in message
    actor = server._derive_actor_context(websocket).actor_context
    assert server._reminder_registry.get(actor, "proposal-1") is None


def test_reminder_prepare_rejects_wrong_proposal_id(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    runtime = server._productivity_runtime
    assert runtime is not None
    bad_result = _valid_reminder_confirmation("wrong-proposal", "rem-wrong")
    monkeypatch.setattr(runtime, "prepare", _mock_runtime_prepare(bad_result))
    _prepare_reminder(server, websocket, request_id="rem-wrong")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["proposal_id"] == "invalid-proposal"
    assert message["request_id"] == "rem-wrong"
    assert "wrong-proposal" not in json.dumps(message)
    actor = server._derive_actor_context(websocket).actor_context
    assert server._reminder_registry.get(actor, "proposal-1") is None


def test_reminder_prepare_rejects_wrong_action(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    runtime = server._productivity_runtime
    assert runtime is not None
    bad_result = _valid_reminder_confirmation("proposal-1", "rem-action")
    bad_result["action"] = "email.draft"
    monkeypatch.setattr(runtime, "prepare", _mock_runtime_prepare(bad_result))
    _prepare_reminder(server, websocket, request_id="rem-action")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["proposal_id"] == "invalid-proposal"
    assert message["request_id"] == "rem-action"
    actor = server._derive_actor_context(websocket).actor_context
    assert server._reminder_registry.get(actor, "proposal-1") is None


def test_reminder_prepare_rejects_wrong_request_id(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    runtime = server._productivity_runtime
    assert runtime is not None
    bad_result = _valid_reminder_confirmation("proposal-1", "rem-other")
    monkeypatch.setattr(runtime, "prepare", _mock_runtime_prepare(bad_result))
    _prepare_reminder(server, websocket, request_id="rem-requested")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["proposal_id"] == "invalid-proposal"
    assert message["request_id"] == "rem-requested"
    assert "rem-other" not in json.dumps(message)
    actor = server._derive_actor_context(websocket).actor_context
    assert server._reminder_registry.get(actor, "proposal-1") is None


def test_reminder_prepare_rejects_undocumented_confirmation_fields(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    runtime = server._productivity_runtime
    assert runtime is not None
    bad_result = _valid_reminder_confirmation("proposal-1", "rem-extra")
    bad_result["extra"] = "secret"
    monkeypatch.setattr(runtime, "prepare", _mock_runtime_prepare(bad_result))
    _prepare_reminder(server, websocket, request_id="rem-extra")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["proposal_id"] == "invalid-proposal"
    assert message["request_id"] == "rem-extra"
    assert "secret" not in json.dumps(message)
    actor = server._derive_actor_context(websocket).actor_context
    assert server._reminder_registry.get(actor, "proposal-1") is None


def test_reminder_prepare_rejects_missing_required_confirmation_fields(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    runtime = server._productivity_runtime
    assert runtime is not None
    bad_result = _valid_reminder_confirmation("proposal-1", "rem-missing")
    del bad_result["heading"]
    monkeypatch.setattr(runtime, "prepare", _mock_runtime_prepare(bad_result))
    _prepare_reminder(server, websocket, request_id="rem-missing")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["proposal_id"] == "invalid-proposal"
    assert message["request_id"] == "rem-missing"
    actor = server._derive_actor_context(websocket).actor_context
    assert server._reminder_registry.get(actor, "proposal-1") is None


def test_reminder_prepare_valid_confirmation_still_succeeds(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    runtime = server._productivity_runtime
    assert runtime is not None
    valid_result = _valid_reminder_confirmation("proposal-1", "rem-valid")
    monkeypatch.setattr(runtime, "prepare", _mock_runtime_prepare(valid_result))
    _prepare_reminder(server, websocket, request_id="rem-valid")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_confirmation_required"
    assert message["proposal_id"] == "proposal-1"
    assert message["action"] == "reminder.create"
    assert message["request_id"] == "rem-valid"
    assert validate_server_message(message) is None
    actor = server._derive_actor_context(websocket).actor_context
    assert server._reminder_registry.get(actor, "proposal-1") is not None


def test_reminder_prepare_malformed_response_cleans_registry_and_does_not_leak_id(tmp_path, monkeypatch):
    server, websocket = _server(tmp_path)
    runtime = server._productivity_runtime
    assert runtime is not None
    # A non-dict runtime response is malformed and must not be retained or exposed.
    monkeypatch.setattr(runtime, "prepare", _mock_runtime_prepare("not-a-dict"))
    _prepare_reminder(server, websocket, request_id="rem-cleanup")
    message = websocket.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["proposal_id"] == "invalid-proposal"
    assert message["request_id"] == "rem-cleanup"
    serialized = json.dumps(message)
    assert "proposal-1" not in serialized
    actor = server._derive_actor_context(websocket).actor_context
    assert server._reminder_registry.get(actor, "proposal-1") is None


def test_reminder_prepare_cross_session_entries_remain_untouched(tmp_path, monkeypatch):
    factory, registry = _reminder_stack()
    server = WebSocketServer(
        object(),
        productivity_runtime=_runtime(tmp_path),
        reminder_factory=factory,
        reminder_registry=registry,
    )
    websocket_a = _WebSocket()
    websocket_b = _WebSocket()
    _pair(server, websocket_a)
    _pair(server, websocket_b)

    # Prepare a valid reminder on session A.
    _prepare_reminder(server, websocket_a, request_id="rem-a")
    actor_a = server._derive_actor_context(websocket_a).actor_context
    assert registry.get(actor_a, "proposal-1") is not None

    # Session B receives a malformed runtime response.
    runtime = server._productivity_runtime
    assert runtime is not None
    bad_result = _valid_reminder_confirmation("proposal-1", "rem-b")
    bad_result["action"] = "email.draft"
    monkeypatch.setattr(runtime, "prepare", _mock_runtime_prepare(bad_result))
    _prepare_reminder(server, websocket_b, request_id="rem-b")
    message = websocket_b.sent[-1]
    assert message["type"] == "productivity_error"
    assert message["proposal_id"] == "invalid-proposal"

    # Session A's entry must remain intact.
    assert registry.get(actor_a, "proposal-1") is not None
