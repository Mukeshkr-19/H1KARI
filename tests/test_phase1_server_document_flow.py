from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.server import WebSocketServer


class Socket:
    def __init__(self, host: str):
        self.remote_address = (host, 5000)
        self.sent = []
        self.sent_at = []

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))
        self.sent_at.append(time.monotonic())


class OneMessageSocket(Socket):
    def __init__(self, host: str, payload: dict):
        super().__init__(host)
        self._message = json.dumps(payload)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._message is None:
            raise StopAsyncIteration
        message, self._message = self._message, None
        return message


def _runtime():
    runtime = SimpleNamespace(documents=MagicMock(), tasks=MagicMock())
    runtime.documents.prepare.return_value = SimpleNamespace(
        task_id="task-1", status="queued", explanation=None, error_code=None, provider=None
    )
    runtime.documents.confirm_and_explain.return_value = SimpleNamespace(
        task_id="task-1", status="completed", explanation="Safe explanation", error_code=None,
        provider="ollama",
    )
    runtime.documents.reconnect.return_value = SimpleNamespace(
        task_id="task-1", status="running", explanation=None, error_code=None, provider=None
    )
    runtime.documents.cancel.return_value = SimpleNamespace(
        task_id="task-1", status="cancelled", explanation=None, error_code=None, provider=None
    )
    runtime.documents.prepare_follow_up.return_value = SimpleNamespace(
        task_id="child-1", status="queued", explanation=None, error_code=None, provider=None
    )
    runtime.documents.execute_follow_up.return_value = SimpleNamespace(
        task_id="child-1", status="completed", explanation="Follow-up answer",
        error_code=None, provider="ollama",
    )
    runtime.tasks.get_task.return_value = SimpleNamespace(
        status=SimpleNamespace(value="running"), progress=40, checkpoint="provider"
    )
    return runtime


def _send(server, socket, payload):
    server._paired_client_ids.add(str(id(socket)))
    asyncio.run(server._handle_message(socket, json.dumps(payload)))


def _send_and_drain(server, socket, payload):
    server._paired_client_ids.add(str(id(socket)))

    async def exercise():
        await server._handle_message(socket, json.dumps(payload))
        jobs = list(server._document_jobs.values())
        if jobs:
            await asyncio.gather(*jobs)

    asyncio.run(exercise())


def _bind(server, providers=("ollama",)):
    server._document_selections["task-1"] = ("/private/report.txt", providers)
    server._document_task_roots["task-1"] = "task-1"


def test_remote_pairing_never_grants_local_document_access():
    runtime = _runtime()
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    socket = Socket("192.0.2.5")

    _send(server, socket, {
        "type": "document_prepare", "path": "/private/report.txt", "provider": "ollama"
    })

    assert socket.sent == [{
        "type": "document_error",
        "task_id": "",
        "root_task_id": "",
        "code": "actor_not_authorized",
        "message": "Document access is available only on this computer.",
    }]
    runtime.documents.prepare.assert_not_called()


def test_ipv4_mapped_ipv6_loopback_is_local():
    runtime = _runtime()
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    socket = Socket("::ffff:127.0.0.1")

    _send(server, socket, {
        "type": "document_prepare", "path": "/private/report.txt", "provider": "ollama"
    })

    assert socket.sent[0]["type"] == "document_confirmation_required"


def test_local_prepare_discloses_only_confirmation_path_and_task():
    runtime = _runtime()
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    socket = Socket("127.0.0.1")

    _send(server, socket, {
        "type": "document_prepare", "path": "/private/report.txt",
        "provider": "ollama", "fallback_provider": "google",
    })

    assert socket.sent == [{
        "type": "document_confirmation_required",
        "task_id": "task-1",
        "path": "/private/report.txt",
        "provider": "ollama",
        "fallback_provider": "google",
    }]
    call = runtime.documents.prepare.call_args
    assert call.kwargs["actor"].actor_id == "local-owner"
    assert call.kwargs["actor"].session_id == call.kwargs["context"].session_id


def test_confirmation_returns_explanation_without_internal_grant_ids():
    runtime = _runtime()
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    _bind(server, ("ollama", "google"))
    socket = Socket("::1")

    _send_and_drain(server, socket, {
        "type": "document_confirm",
        "task_id": "task-1",
        "provider": "ollama",
        "fallback_provider": "google",
    })

    assert socket.sent == [{
        "type": "document_explanation",
        "task_id": "task-1",
        "root_task_id": "task-1",
        "text": "Safe explanation",
        "provider": "ollama",
    }]
    assert "grant" not in json.dumps(socket.sent).lower()
    assert runtime.documents.confirm_and_explain.call_args.args[1] == ("ollama", "google")


def test_confirm_rejects_destination_swap_from_prepared_snapshot():
    runtime = _runtime()
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    socket = Socket("127.0.0.1")

    _send(server, socket, {
        "type": "document_prepare", "path": "/private/report.txt",
        "provider": "ollama", "fallback_provider": "google",
    })
    socket.sent.clear()
    _send(server, socket, {
        "type": "document_confirm", "task_id": "task-1",
        "provider": "ollama", "fallback_provider": "evil-provider",
    })

    assert socket.sent == [{
        "type": "document_error", "task_id": "task-1", "root_task_id": "task-1",
        "code": "destination_mismatch",
        "message": "Document request could not be completed.",
    }]
    runtime.documents.confirm_and_explain.assert_not_called()


def test_reconnect_uses_stable_owner_with_a_fresh_connection_session():
    runtime = _runtime()
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    first, second = Socket("127.0.0.1"), Socket("127.0.0.1")

    _send(server, first, {"type": "task_status", "task_id": "task-1"})
    _send(server, second, {"type": "task_status", "task_id": "task-1"})

    calls = runtime.documents.reconnect.call_args_list
    assert calls[0].kwargs["actor"].actor_id == calls[1].kwargs["actor"].actor_id == "local-owner"
    assert calls[0].kwargs["actor"].session_id != calls[1].kwargs["actor"].session_id
    assert first.sent[-1] == {
        "type": "task_update", "task_id": "task-1", "status": "running",
        "root_task_id": "task-1", "progress": 40, "checkpoint": "provider",
    }


def test_reconnect_recovers_completed_explanation_with_saved_provider():
    runtime = _runtime()
    runtime.documents.reconnect.return_value = SimpleNamespace(
        task_id="task-1", status="completed", explanation="Persisted explanation",
        error_code=None, provider=None,
    )
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    socket = Socket("127.0.0.1")

    _send(server, socket, {"type": "task_status", "task_id": "task-1"})

    assert socket.sent == [{
        "type": "document_explanation",
        "task_id": "task-1",
        "root_task_id": "task-1",
        "text": "Persisted explanation",
        "provider": "saved",
    }]
    runtime.tasks.get_task.assert_not_called()


def test_client_supplied_actor_metadata_is_rejected_by_protocol():
    runtime = _runtime()
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    socket = Socket("127.0.0.1")

    _send(server, socket, {
        "type": "document_prepare", "path": "/private/report.txt",
        "provider": "ollama", "actor": "owner",
    })

    assert socket.sent == [{"type": "error", "message": "Unknown field: actor"}]
    runtime.documents.prepare.assert_not_called()


def test_local_owner_can_cancel_without_receiving_internal_state():
    runtime = _runtime()
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    socket = Socket("127.0.0.1")

    _send(server, socket, {"type": "document_cancel", "task_id": "task-1"})

    runtime.documents.cancel.assert_called_once()
    assert socket.sent == [{
        "type": "task_update", "task_id": "task-1", "status": "cancelled",
        "root_task_id": "task-1", "progress": 0, "checkpoint": "cancelled",
    }]


def test_slow_provider_does_not_block_status_on_another_connection():
    runtime = _runtime()
    completed = runtime.documents.confirm_and_explain.return_value

    def slow_confirm(*_args, **_kwargs):
        time.sleep(0.2)
        return completed

    runtime.documents.confirm_and_explain.side_effect = slow_confirm
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    _bind(server)
    confirm_socket = Socket("127.0.0.1")
    status_socket = Socket("127.0.0.1")
    server._paired_client_ids.update({str(id(confirm_socket)), str(id(status_socket))})

    async def exercise():
        await asyncio.gather(
            server._handle_message(confirm_socket, json.dumps({
                "type": "document_confirm", "task_id": "task-1", "provider": "ollama"
            })),
            server._handle_message(status_socket, json.dumps({
                "type": "task_status", "task_id": "task-1"
            })),
        )
        jobs = list(server._document_jobs.values())
        if jobs:
            await asyncio.gather(*jobs)

    asyncio.run(exercise())

    assert status_socket.sent_at[0] < confirm_socket.sent_at[0]


def test_same_socket_can_status_and_cancel_a_slow_confirm_without_late_success():
    runtime = _runtime()
    started = threading.Event()
    release = threading.Event()

    def slow_confirm(*_args, **_kwargs):
        started.set()
        release.wait(1)
        return runtime.documents.confirm_and_explain.return_value

    runtime.documents.confirm_and_explain.side_effect = slow_confirm
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    _bind(server)
    socket = Socket("127.0.0.1")
    server._paired_client_ids.add(str(id(socket)))

    async def exercise():
        await server._handle_message(socket, json.dumps({
            "type": "document_confirm", "task_id": "task-1", "provider": "ollama"
        }))
        assert await asyncio.to_thread(started.wait, 0.3)
        before = time.monotonic()
        await server._handle_message(socket, json.dumps({
            "type": "task_status", "task_id": "task-1"
        }))
        await server._handle_message(socket, json.dumps({
            "type": "document_cancel", "task_id": "task-1"
        }))
        elapsed = time.monotonic() - before
        release.set()
        await asyncio.sleep(0.05)
        return elapsed

    elapsed = asyncio.run(exercise())

    assert elapsed < 0.15
    assert [item["type"] for item in socket.sent] == ["task_update", "task_update"]
    assert socket.sent[-1]["status"] == "cancelled"


def test_follow_up_child_payload_keeps_root_task_correlation():
    runtime = _runtime()
    runtime.documents.execute_follow_up.return_value = SimpleNamespace(
        task_id="child-1", status="completed", explanation="Follow-up answer",
        error_code=None, provider="ollama",
    )
    runtime.documents.reconnect.return_value = SimpleNamespace(
        task_id="task-1", status="completed", explanation="Original explanation",
        error_code=None, provider=None,
    )
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    _bind(server)
    socket = Socket("127.0.0.1")

    _send_and_drain(server, socket, {
        "type": "document_follow_up", "task_id": "task-1", "text": "Why?",
        "provider": "ollama",
    })

    assert socket.sent == [
        {
            "type": "task_update", "task_id": "child-1", "root_task_id": "task-1",
            "status": "queued", "progress": 0, "checkpoint": "queued",
        },
        {
            "type": "document_explanation", "task_id": "child-1",
            "root_task_id": "task-1", "text": "Follow-up answer", "provider": "ollama",
        },
    ]


def test_cancel_targets_running_follow_up_child_and_suppresses_egress_and_late_success():
    runtime = _runtime()
    cancelled = threading.Event()
    provider_called = threading.Event()

    def slow_follow_up(*_args, **_kwargs):
        if not cancelled.wait(0.5):
            provider_called.set()
        return SimpleNamespace(
            task_id="child-1", status="completed", explanation="late",
            error_code=None, provider="ollama",
        )

    def cancel(task_id, **_kwargs):
        assert task_id == "child-1"
        cancelled.set()
        return SimpleNamespace(
            task_id="child-1", status="cancelled", explanation=None,
            error_code=None, provider=None,
        )

    runtime.documents.execute_follow_up.side_effect = slow_follow_up
    runtime.documents.cancel.side_effect = cancel
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    _bind(server)
    socket = Socket("127.0.0.1")
    server._paired_client_ids.add(str(id(socket)))

    async def exercise():
        await server._handle_message(socket, json.dumps({
            "type": "document_follow_up", "task_id": "task-1", "text": "Why?",
            "provider": "ollama",
        }))
        for _ in range(30):
            if socket.sent and socket.sent[-1].get("task_id") == "child-1":
                break
            await asyncio.sleep(0.01)
        await server._handle_message(socket, json.dumps({
            "type": "document_cancel", "task_id": "child-1"
        }))
        await asyncio.sleep(0.05)

    asyncio.run(exercise())

    assert cancelled.is_set()
    assert not provider_called.is_set()
    assert [item["type"] for item in socket.sent] == ["task_update", "task_update"]
    assert socket.sent[-1]["task_id"] == "child-1"
    assert socket.sent[-1]["root_task_id"] == "task-1"
    assert socket.sent[-1]["status"] == "cancelled"


def test_disconnect_cancels_background_delivery():
    runtime = _runtime()

    def slow_confirm(*_args, **_kwargs):
        time.sleep(0.1)
        return runtime.documents.confirm_and_explain.return_value

    runtime.documents.confirm_and_explain.side_effect = slow_confirm
    server = WebSocketServer(MagicMock(), phase1_runtime=runtime)
    _bind(server)
    socket = OneMessageSocket("127.0.0.1", {
        "type": "document_confirm", "task_id": "task-1", "provider": "ollama"
    })
    server._paired_client_ids.add(str(id(socket)))

    asyncio.run(server._handle_connection(socket))

    assert [item["type"] for item in socket.sent] == ["welcome"]
    assert not server._document_jobs
