"""Tests for the Phase 3 scheduled-jobs WebSocket bridge.

These tests cover list, pause, resume, cancel, unpaired rejection, invalid
fields, cross-session behavior, runtime exceptions, malformed runtime output,
exact wire shapes, and the absence of execution.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from core.jobs import JobState, ScheduledJob, ScheduledJobRuntime, ScheduledJobStore
from core.jobs.service import ScheduledJobService
from core.server import WebSocketServer


class MockWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


class LoopbackWebSocket(MockWebSocket):
    @property
    def remote_address(self):
        return ("127.0.0.1", 12345)


def _paired_websocket(server: WebSocketServer) -> MockWebSocket:
    websocket = MockWebSocket()
    server._paired_client_ids.add(str(id(websocket)))
    return websocket


def _paired_loopback(server: WebSocketServer) -> LoopbackWebSocket:
    websocket = LoopbackWebSocket()
    server._paired_client_ids.add(str(id(websocket)))
    return websocket


def _make_runtime(result: dict | None = None) -> MagicMock:
    runtime = MagicMock()
    runtime.list_jobs.return_value = result or {
        "type": "scheduled_jobs",
        "jobs": [
            {
                "job_id": "job-1",
                "action": "digest",
                "state": "scheduled",
                "next_run_at": "2026-07-18T09:00:00+00:00",
                "attempt_count": 0,
                "max_attempts": 1,
            }
        ],
    }
    runtime.pause.return_value = result or {
        "type": "scheduled_job_update",
        "job": {
            "job_id": "job-1",
            "action": "digest",
            "state": "paused",
            "next_run_at": "2026-07-18T09:00:00+00:00",
            "attempt_count": 0,
            "max_attempts": 1,
        },
    }
    runtime.resume.return_value = result or {
        "type": "scheduled_job_update",
        "job": {
            "job_id": "job-1",
            "action": "digest",
            "state": "scheduled",
            "next_run_at": "2026-07-18T09:00:00+00:00",
            "attempt_count": 0,
            "max_attempts": 1,
        },
    }
    runtime.cancel.return_value = result or {
        "type": "scheduled_job_update",
        "job": {
            "job_id": "job-1",
            "action": "digest",
            "state": "cancelled",
            "next_run_at": "2026-07-18T09:00:00+00:00",
            "attempt_count": 0,
            "max_attempts": 1,
        },
    }
    return runtime


def test_scheduled_jobs_list_returns_protocol_shape():
    runtime = _make_runtime()
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(server._handle_message(websocket, json.dumps({"type": "scheduled_jobs_list"})))

    assert len(websocket.sent) == 1
    msg = websocket.sent[0]
    assert msg["type"] == "scheduled_jobs"
    assert "jobs" in msg
    assert len(msg["jobs"]) == 1
    assert msg["jobs"][0]["job_id"] == "job-1"
    assert "quiet_hours_label" not in msg["jobs"][0]
    assert "ok" not in msg
    runtime.list_jobs.assert_called_once()


def test_scheduled_job_pause_returns_protocol_shape():
    runtime = _make_runtime()
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(
            websocket, json.dumps({"type": "scheduled_job_pause", "job_id": "job-1"})
        )
    )

    assert len(websocket.sent) == 1
    msg = websocket.sent[0]
    assert msg["type"] == "scheduled_job_update"
    assert msg["job"]["state"] == "paused"
    assert "ok" not in msg
    runtime.pause.assert_called_once()


def test_scheduled_job_resume_returns_protocol_shape():
    runtime = _make_runtime()
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(
            websocket, json.dumps({"type": "scheduled_job_resume", "job_id": "job-1"})
        )
    )

    assert len(websocket.sent) == 1
    msg = websocket.sent[0]
    assert msg["type"] == "scheduled_job_update"
    assert msg["job"]["state"] == "scheduled"
    runtime.resume.assert_called_once()


def test_scheduled_job_cancel_returns_protocol_shape():
    runtime = _make_runtime()
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(
            websocket, json.dumps({"type": "scheduled_job_cancel", "job_id": "job-1"})
        )
    )

    assert len(websocket.sent) == 1
    msg = websocket.sent[0]
    assert msg["type"] == "scheduled_job_update"
    assert msg["job"]["state"] == "cancelled"
    runtime.cancel.assert_called_once()


def test_unpaired_scheduled_job_request_is_rejected():
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=_make_runtime())
    websocket = MockWebSocket()

    asyncio.run(
        server._handle_message(
            websocket, json.dumps({"type": "scheduled_jobs_list"})
        )
    )

    assert len(websocket.sent) == 1
    assert websocket.sent[0]["type"] == "pairing_required"


def test_missing_runtime_returns_unavailable_error():
    server = WebSocketServer(MagicMock())
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(
            websocket, json.dumps({"type": "scheduled_job_pause", "job_id": "job-1"})
        )
    )

    assert len(websocket.sent) == 1
    msg = websocket.sent[0]
    assert msg["type"] == "scheduled_job_error"
    assert msg["job_id"] == "job-1"
    assert msg["code"] == "unavailable"


def test_invalid_job_id_is_rejected_before_runtime():
    runtime = _make_runtime()
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "scheduled_job_pause", "job_id": "bad id!"}),
        )
    )

    assert len(websocket.sent) == 1
    msg = websocket.sent[0]
    assert msg["type"] == "error"
    runtime.pause.assert_not_called()


def test_client_actor_session_fields_are_rejected():
    runtime = _make_runtime()
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps(
                {
                    "type": "scheduled_job_pause",
                    "job_id": "job-1",
                    "actor_id": "owner",
                    "session_id": "session-1",
                }
            ),
        )
    )

    assert len(websocket.sent) == 1
    msg = websocket.sent[0]
    assert msg["type"] == "error"
    runtime.pause.assert_not_called()


def test_runtime_exception_returns_safe_error():
    runtime = MagicMock()
    runtime.list_jobs.side_effect = RuntimeError("secret details")
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(websocket, json.dumps({"type": "scheduled_jobs_list"}))
    )

    assert len(websocket.sent) == 1
    msg = websocket.sent[0]
    assert msg["type"] == "scheduled_job_error"
    assert msg["code"] == "unavailable"
    assert "secret" not in str(msg)
    assert "details" not in str(msg)


def test_malformed_runtime_output_falls_back_to_error():
    runtime = MagicMock()
    runtime.list_jobs.return_value = {"type": "scheduled_jobs", "jobs": "not-a-list"}
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(websocket, json.dumps({"type": "scheduled_jobs_list"}))
    )

    assert len(websocket.sent) == 1
    msg = websocket.sent[0]
    assert msg["type"] == "scheduled_job_error"
    assert msg["code"] == "unavailable"


def test_cross_session_does_not_expose_existing_job(tmp_path):
    now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    store = ScheduledJobStore(tmp_path / "jobs.db")
    runtime = ScheduledJobRuntime(ScheduledJobService(store, clock=lambda: now))
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    session_a = _paired_loopback(server)
    session_b = _paired_loopback(server)
    actor_a = server._derive_actor_context(session_a).actor_context
    actor_b = server._derive_actor_context(session_b).actor_context

    assert actor_a.session_id != actor_b.session_id
    assert server._derive_actor_context(session_a).actor_context == actor_a
    store.add(
        ScheduledJob(
            job_id="private-job",
            actor_id=actor_a.actor_id,
            session_id=actor_a.session_id,
            action="digest",
            proposal_id="proposal-1",
            state=JobState.SCHEDULED,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
    )

    asyncio.run(
        server._handle_message(
            session_a,
            json.dumps({"type": "scheduled_jobs_list"}),
        )
    )
    assert session_a.sent[-1]["jobs"][0]["job_id"] == "private-job"

    asyncio.run(
        server._handle_message(
            session_b,
            json.dumps({"type": "scheduled_jobs_list"}),
        )
    )
    assert session_b.sent[-1] == {"type": "scheduled_jobs", "jobs": []}

    asyncio.run(
        server._handle_message(
            session_b,
            json.dumps(
                {"type": "scheduled_job_pause", "job_id": "private-job"}
            ),
        )
    )
    assert session_b.sent[-1] == {
        "type": "scheduled_job_error",
        "job_id": "private-job",
        "code": "job_not_found",
    }


def test_obsolete_runtime_shape_is_rejected():
    runtime = MagicMock()
    runtime.list_jobs.return_value = {"type": "scheduled_job_list", "jobs": []}
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(websocket, json.dumps({"type": "scheduled_jobs_list"}))
    )

    assert websocket.sent == [
        {
            "type": "scheduled_job_error",
            "job_id": "invalid-job-id",
            "code": "unavailable",
        }
    ]


def test_canonical_quiet_hours_label_is_preserved():
    runtime = MagicMock()
    runtime.list_jobs.return_value = {
        "type": "scheduled_jobs",
        "jobs": [
            {
                "job_id": "job-1",
                "action": "digest",
                "state": "scheduled",
                "next_run_at": "2026-07-18T09:00:00+00:00",
                "quiet_hours_label": "1320-1380 (America/New_York)",
                "attempt_count": 0,
                "max_attempts": 1,
            }
        ],
    }
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(websocket, json.dumps({"type": "scheduled_jobs_list"}))
    )

    msg = websocket.sent[0]
    assert msg["jobs"][0]["quiet_hours_label"] == "1320-1380 (America/New_York)"


def test_client_actor_field_is_rejected_for_list():
    runtime = MagicMock()
    runtime.list_jobs.return_value = {
        "type": "scheduled_jobs",
        "jobs": [],
    }
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(
            websocket,
            json.dumps({"type": "scheduled_jobs_list", "actor_id": "attacker"}),
        )
    )

    assert len(websocket.sent) == 1
    msg = websocket.sent[0]
    assert msg["type"] == "error"
    runtime.list_jobs.assert_not_called()


def test_no_execution_side_effects():
    runtime = MagicMock()
    runtime.list_jobs.return_value = {
        "type": "scheduled_jobs",
        "jobs": [],
    }
    server = WebSocketServer(MagicMock(), scheduled_job_runtime=runtime)
    websocket = _paired_websocket(server)

    asyncio.run(
        server._handle_message(websocket, json.dumps({"type": "scheduled_jobs_list"}))
    )

    assert runtime.list_jobs.called
    assert not runtime.pause.called
    assert not runtime.resume.called
    assert not runtime.cancel.called
