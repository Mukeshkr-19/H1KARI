"""End-to-end Phase 4 control-plane tests through the WebSocket server."""

from __future__ import annotations

import asyncio
import json
import struct

from core.action_policy import Actor
from core.handoff import FrozenHandoffPreview
from core.phase4 import create_phase4_subsystem
from core.protocol import validate_server_message
from core.server import WebSocketServer


class _WebSocket:
    def __init__(self, host: str) -> None:
        self.host = host
        self.sent: list[dict] = []

    @property
    def remote_address(self):
        return (self.host, 12345)

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


class _QueuedWebSocket(_WebSocket):
    def __init__(self, host: str, messages: list[str]) -> None:
        super().__init__(host)
        self._messages = iter(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + (b"\x00" * 4)


def _png() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", b"")
        + _chunk(b"IEND", b"")
    )


def _send(server: WebSocketServer, socket: _WebSocket, payload: dict) -> None:
    asyncio.run(server._handle_message(socket, json.dumps(payload)))


def _subsystem(tmp_path, policy_calls: list):
    def lookup(actor, task_id):
        if actor.actor is not Actor.GUEST or task_id != "task-1":
            return None
        return FrozenHandoffPreview(task_id="task-1", summary="Review result")

    def policy(actor, preview):
        policy_calls.append((actor.actor, preview))
        return actor.actor is Actor.OWNER

    return create_phase4_subsystem(
        task_lookup=lookup,
        acceptance_policy=policy,
        clock=lambda: 1000.0,
        handoff_db_path=tmp_path / "handoffs.db",
        pairing_db_path=tmp_path / "devices.db",
        handoff_id_factory=lambda: "handoff-1",
        transfer_id_factory=lambda: "transfer-1",
        challenge_id_factory=lambda: "challenge-1",
        device_id_factory=lambda: "device-1",
        secret_code_factory=lambda: "ABC123",
        digest_key=b"server-test-digest-key",
        display_sink=lambda _code: None,
    )


def _server(tmp_path, policy_calls: list):
    subsystem = _subsystem(tmp_path, policy_calls)
    server = WebSocketServer(
        object(),
        pairing_runtime=subsystem.pairing_runtime,
        handoff_transport=subsystem.handoff_transport,
        visual_transfer_runtime=subsystem.visual_transfer_runtime,
    )
    return server, subsystem


def test_pairing_is_one_use_and_remote_remains_guest(tmp_path) -> None:
    server, _ = _server(tmp_path, [])
    remote = _WebSocket("10.0.0.2")

    _send(server, remote, {"type": "pairing_prepare", "request_id": "request-1"})
    assert remote.sent[-1] == {
        "type": "pairing_challenge",
        "request_id": "request-1",
        "challenge_id": "challenge-1",
        "expires_at": 1120.0,
    }
    _send(
        server,
        remote,
        {
            "type": "pairing_confirm",
            "request_id": "request-1",
            "challenge_id": "challenge-1",
            "code": "ABC123",
        },
    )
    assert remote.sent[-1]["type"] == "pairing_confirmed"
    assert server._derive_actor_context(remote).actor_context.actor is Actor.GUEST

    _send(
        server,
        remote,
        {
            "type": "pairing_confirm",
            "request_id": "request-1",
            "challenge_id": "challenge-1",
            "code": "ABC123",
        },
    )
    assert remote.sent[-1]["type"] == "pairing_error"
    assert remote.sent[-1]["code"] == "challenge_invalid"


def test_one_connection_cannot_accumulate_pairing_challenges(tmp_path) -> None:
    server, _ = _server(tmp_path, [])
    remote = _WebSocket("10.0.0.2")
    _send(server, remote, {"type": "pairing_prepare", "request_id": "request-1"})
    _send(server, remote, {"type": "pairing_prepare", "request_id": "request-2"})
    assert remote.sent[-1] == {
        "type": "pairing_error",
        "request_id": "request-2",
        "code": "rate_limited",
    }


def test_revocation_requires_transport_derived_owner(tmp_path) -> None:
    server, _ = _server(tmp_path, [])
    remote = _WebSocket("10.0.0.2")
    owner = _WebSocket("127.0.0.1")
    _send(server, remote, {"type": "pairing_prepare", "request_id": "request-1"})
    _send(server, remote, {
        "type": "pairing_confirm",
        "request_id": "request-1",
        "challenge_id": "challenge-1",
        "code": "ABC123",
    })

    revoke = {"type": "pairing_revoke", "request_id": "request-2", "device_id": "device-1"}
    _send(server, remote, revoke)
    assert remote.sent[-1]["code"] == "unauthorized"

    server._paired_client_ids.add(str(id(owner)))
    _send(server, owner, revoke)
    assert owner.sent[-1]["status"] == "revoked"
    assert str(id(remote)) not in server._paired_client_ids


def test_guest_offer_owner_acceptance_and_visual_binary_are_exactly_scoped(tmp_path) -> None:
    policy_calls: list = []
    server, _ = _server(tmp_path, policy_calls)
    guest = _WebSocket("10.0.0.2")
    owner = _WebSocket("127.0.0.1")
    other = _WebSocket("10.0.0.3")
    server.connected_clients.update({guest, owner, other})
    for socket, token in (
        (guest, "guest-session"),
        (owner, "owner-session"),
        (other, "other-session"),
    ):
        key = str(id(socket))
        server._paired_client_ids.add(key)
        server._connection_tokens[key] = token

    _send(server, guest, {
        "type": "handoff_prepare",
        "request_id": "offer-1",
        "task_id": "task-1",
        "summary": "Review result",
    })
    offer = guest.sent[-1]
    assert offer["type"] == "handoff_offer"
    assert owner.sent[-1] == offer
    assert other.sent == []
    assert validate_server_message(offer) is None

    _send(server, owner, {
        "type": "handoff_accept",
        "request_id": "accept-1",
        "handoff_id": "handoff-1",
        "acknowledged": True,
    })
    assert owner.sent[-1]["status"] == "accepted"
    assert guest.sent[-1]["status"] == "accepted"
    assert "handoff-1" not in server._phase4_handoff_origins
    assert len(policy_calls) == 1
    assert policy_calls[0][0] is Actor.OWNER
    assert policy_calls[0][1] == FrozenHandoffPreview(
        task_id="task-1", summary="Review result"
    )

    frame = _png()
    _send(server, other, {
        "type": "visual_transfer_begin",
        "request_id": "visual-other",
        "handoff_id": "handoff-1",
        "mime_type": "image/png",
        "size_bytes": len(frame),
        "width": 1,
        "height": 1,
        "frame_count": 1,
    })
    assert other.sent[-1]["code"] == "handoff_not_accepted"

    _send(server, guest, {
        "type": "visual_transfer_begin",
        "request_id": "visual-1",
        "handoff_id": "handoff-1",
        "mime_type": "image/png",
        "size_bytes": len(frame),
        "width": 1,
        "height": 1,
        "frame_count": 1,
    })
    assert guest.sent[-1]["type"] == "visual_transfer_ready"
    _send(server, guest, {
        "type": "visual_transfer_begin",
        "request_id": "visual-2",
        "handoff_id": "handoff-1",
        "mime_type": "image/png",
        "size_bytes": len(frame),
        "width": 1,
        "height": 1,
        "frame_count": 1,
    })
    assert guest.sent[-1]["code"] == "rate_limited"
    asyncio.run(server._handle_visual_binary(guest, frame))
    assert [message["type"] for message in guest.sent[-2:]] == [
        "visual_transfer_update",
        "visual_transfer_complete",
    ]
    assert str(id(guest)) not in server._phase4_pending_transfers


def test_json_bytes_fields_and_unpaired_binary_fail_closed(tmp_path) -> None:
    server, _ = _server(tmp_path, [])
    remote = _WebSocket("10.0.0.2")
    server._paired_client_ids.add(str(id(remote)))
    _send(server, remote, {
        "type": "visual_transfer_begin",
        "request_id": "visual-1",
        "handoff_id": "handoff-1",
        "mime_type": "image/png",
        "size_bytes": 8,
        "width": 1,
        "height": 1,
        "frame_count": 1,
        "base64": "forbidden",
    })
    assert remote.sent[-1]["type"] == "error"
    assert str(id(remote)) not in server._phase4_pending_transfers
    server._paired_client_ids.discard(str(id(remote)))
    asyncio.run(server._handle_visual_binary(remote, b"raw"))
    assert remote.sent[-1]["type"] == "pairing_required"


def test_disconnect_marks_device_stale_and_clears_connection_state(tmp_path) -> None:
    server, subsystem = _server(tmp_path, [])
    socket = _QueuedWebSocket(
        "10.0.0.2",
        [
            json.dumps({"type": "pairing_prepare", "request_id": "request-1"}),
            json.dumps({
                "type": "pairing_confirm",
                "request_id": "request-1",
                "challenge_id": "challenge-1",
                "code": "ABC123",
            }),
        ],
    )
    asyncio.run(server._handle_connection(socket))

    record = subsystem.pairing_runtime._service._get_device("device-1")
    assert record is not None
    assert record.state.value == "stale"
    client_key = str(id(socket))
    assert client_key not in server._paired_client_ids
    assert client_key not in server._phase4_device_ids
    assert client_key not in server._connection_tokens


def test_disconnect_cancels_pending_pairing_challenge(tmp_path) -> None:
    server, subsystem = _server(tmp_path, [])
    socket = _QueuedWebSocket(
        "10.0.0.2",
        [json.dumps({"type": "pairing_prepare", "request_id": "request-1"})],
    )
    asyncio.run(server._handle_connection(socket))

    challenge = subsystem.pairing_runtime._service._get_challenge("challenge-1")
    assert challenge is not None
    assert challenge.state.value == "cancelled"
    assert str(id(socket)) not in server._phase4_challenges
