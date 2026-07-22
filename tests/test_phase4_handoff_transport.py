"""Phase 4 handoff transport adapter tests."""

from __future__ import annotations

from typing import Optional

import pytest

from core.action_policy import Actor, ActorContext
from core.handoff.contracts import FrozenHandoffPreview
from core.handoff.runtime import HandoffRuntime
from core.handoff.service import HandoffService
from core.handoff.store import HandoffStore
from core.handoff.transport import HandoffTransportAdapter
from core.protocol import validate_server_message


@pytest.fixture
def owner_actor() -> ActorContext:
    return ActorContext(
        actor_id="local-owner",
        actor=Actor.OWNER,
        session_id="session-1",
        source="local",
    )


@pytest.fixture
def guest_actor() -> ActorContext:
    return ActorContext(
        actor_id="guest",
        actor=Actor.GUEST,
        session_id="guest-session",
        source="websocket",
    )


@pytest.fixture
def fake_clock():
    value = {"now": 1000.0}

    def clock() -> float:
        return value["now"]

    return clock, value


@pytest.fixture
def id_sequence():
    counter = {"n": 0}

    def factory() -> str:
        counter["n"] += 1
        return f"h{counter['n']:03d}"

    return factory


@pytest.fixture
def preview_lookup():
    previews = {
        "task-123": FrozenHandoffPreview(task_id="task-123", summary="Review report"),
    }

    def lookup(actor: ActorContext, task_id: str) -> Optional[FrozenHandoffPreview]:
        return previews.get(task_id)

    return lookup, previews


def _adapter(tmp_path, clock, factory, lookup, policy=None) -> HandoffTransportAdapter:
    store = HandoffStore(
        tmp_path / "handoffs.db",
        clock=clock,
        handoff_id_factory=factory,
    )
    service = HandoffService(
        store,
        task_lookup=lookup,
        acceptance_policy=policy
        or (lambda actor, preview: actor.actor is Actor.OWNER),
    )
    return HandoffTransportAdapter(HandoffRuntime(service))


def test_dispatch_prepare_returns_handoff_offer(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    adapter = _adapter(tmp_path, clock, id_sequence, lookup)

    result = adapter.dispatch(
        owner_actor,
        {
            "type": "handoff_prepare",
            "request_id": "req-1",
            "task_id": "task-123",
            "summary": "Review report",
        },
    )
    assert result["type"] == "handoff_offer"
    assert result["request_id"] == "req-1"
    assert result["task_id"] == "task-123"
    assert result["summary"] == "Review report"
    assert validate_server_message(result) is None


def test_dispatch_accept_reject_cancel_status(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    guest_actor,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    adapter = _adapter(tmp_path, clock, id_sequence, lookup)

    adapter.dispatch(
        guest_actor,
        {
            "type": "handoff_prepare",
            "request_id": "req-1",
            "task_id": "task-123",
            "summary": "Review report",
        },
    )

    accept = adapter.dispatch(
        owner_actor,
        {
            "type": "handoff_accept",
            "request_id": "req-2",
            "handoff_id": "h001",
            "acknowledged": True,
        },
    )
    assert accept == {
        "type": "handoff_update",
        "request_id": "req-2",
        "handoff_id": "h001",
        "status": "accepted",
    }
    assert validate_server_message(accept) is None

    status = adapter.dispatch(
        owner_actor,
        {
            "type": "handoff_status",
            "request_id": "req-3",
            "handoff_id": "h001",
        },
    )
    assert status["status"] == "accepted"


def test_dispatch_unknown_type_returns_unavailable(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    adapter = _adapter(tmp_path, clock, id_sequence, lookup)

    result = adapter.dispatch(
        owner_actor,
        {"type": "handoff_unknown", "request_id": "req-1"},
    )
    assert result == {
        "type": "handoff_error",
        "request_id": "req-1",
        "code": "unavailable",
    }
    assert validate_server_message(result) is None


def test_dispatch_replaces_invalid_runtime_output(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    adapter = _adapter(tmp_path, clock, id_sequence, lookup)
    adapter._runtime.prepare = lambda *a, **k: {"type": "handoff_offer"}  # type: ignore[assignment]

    result = adapter.dispatch(
        owner_actor,
        {
            "type": "handoff_prepare",
            "request_id": "req-1",
            "task_id": "task-123",
            "summary": "Review report",
        },
    )
    assert result["type"] == "handoff_error"
    assert result["code"] == "unavailable"
    assert validate_server_message(result) is None


def test_dispatch_ignores_identity_fields_in_message(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    guest_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    adapter = _adapter(tmp_path, clock, id_sequence, lookup)

    result = adapter.dispatch(
        guest_actor,
        {
            "type": "handoff_prepare",
            "request_id": "req-1",
            "task_id": "task-123",
            "summary": "Review report",
            "actor_id": "owner",
            "session_id": "forged",
            "approval_id": "approval-1",
        },
    )
    assert result["type"] == "handoff_offer"
    assert result["request_id"] == "req-1"
    assert validate_server_message(result) is None


def test_dispatch_request_id_correlation(
    tmp_path,
    fake_clock,
    id_sequence,
    preview_lookup,
    owner_actor,
):
    clock, _ = fake_clock
    lookup, _ = preview_lookup
    adapter = _adapter(tmp_path, clock, id_sequence, lookup)

    offer = adapter.dispatch(
        owner_actor,
        {
            "type": "handoff_prepare",
            "request_id": "req-correlation",
            "task_id": "task-123",
            "summary": "Review report",
        },
    )
    assert offer["request_id"] == "req-correlation"

    reject = adapter.dispatch(
        owner_actor,
        {
            "type": "handoff_reject",
            "request_id": "req-reject",
            "handoff_id": offer["handoff_id"],
        },
    )
    assert reject["request_id"] == "req-reject"


def test_transport_rejects_invalid_runtime_type():
    with pytest.raises(TypeError):
        HandoffTransportAdapter("not-a-runtime")  # type: ignore[arg-type]


def test_no_forbidden_imports_in_runtime_and_transport():
    import ast
    from pathlib import Path
    from core.handoff import runtime as runtime_module
    from core.handoff import transport as transport_module

    forbidden = {
        "subprocess",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "asyncio",
        "logging",
    }
    for module in (runtime_module, transport_module):
        path = Path(module.__file__)
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        assert forbidden.isdisjoint(imported), f"forbidden import in {module.__name__}"
