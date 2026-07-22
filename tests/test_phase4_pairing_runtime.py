"""Tests for core.pairing.runtime transport-independent boundary."""

from __future__ import annotations

import ast
import pathlib
from collections import deque

import pytest

from core.action_policy import Actor, ActorContext
from core.pairing.challenge_store import PairingChallengeStore
from core.pairing.contracts import (
    CHALLENGE_TTL_SECONDS,
    DEVICE_SESSION_TTL_SECONDS,
    MAX_CONFIRMATION_ATTEMPTS,
)
from core.pairing.device_store import DeviceSessionStore
from core.pairing.runtime import PairingRuntime
from core.pairing.service import PairingService
from core.protocol import PROTOCOL_VERSION, validate_server_message


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self._value = start

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += seconds


class _Sink:
    def __init__(self) -> None:
        self.codes: list[str] = []
        self.raise_on_display = False

    def __call__(self, code: str) -> None:
        if self.raise_on_display:
            raise RuntimeError(f"sink failed for {code}")
        self.codes.append(code)


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def sink() -> _Sink:
    return _Sink()


@pytest.fixture
def runtime(clock: _Clock, sink: _Sink, tmp_path) -> PairingRuntime:
    challenge_ids = deque(["challenge-1", "challenge-2", "challenge-3"])
    codes = deque(["ABC123", "DEF456", "111111", "222222", "333333", "444444", "555555"])
    device_ids = deque(["device-1", "device-2", "device-3"])
    challenge_store = PairingChallengeStore(
        clock=clock,
        challenge_id_factory=lambda: challenge_ids.popleft(),
        secret_code_factory=lambda: codes.popleft(),
        digest_key=b"runtime-test-digest-key",
    )
    device_store = DeviceSessionStore(
        tmp_path / "devices.db",
        clock=clock,
        device_id_factory=lambda: device_ids.popleft(),
    )
    service = PairingService(
        challenge_store=challenge_store,
        device_store=device_store,
    )
    return PairingRuntime(service, clock, sink)


def _owner() -> ActorContext:
    return ActorContext(
        actor_id="local-owner",
        actor=Actor.OWNER,
        session_id="session-1",
        source="local",
    )


def _guest() -> ActorContext:
    return ActorContext(
        actor_id="guest",
        actor=Actor.GUEST,
        session_id="session-2",
        source="websocket",
    )


def test_prepare_returns_exact_validated_challenge(
    runtime: PairingRuntime, sink: _Sink, clock: _Clock
) -> None:
    message = runtime.prepare("request-1")
    assert validate_server_message(message) is None
    assert message == {
        "type": "pairing_challenge",
        "request_id": "request-1",
        "challenge_id": "challenge-1",
        "expires_at": 1000.0 + CHALLENGE_TTL_SECONDS,
    }
    assert "code" not in message
    assert "ABC123" not in repr(message)
    assert sink.codes == ["ABC123"]


def test_secret_displayed_once_and_never_returned(
    runtime: PairingRuntime, sink: _Sink
) -> None:
    first = runtime.prepare("request-1")
    second = runtime.prepare("request-1")
    assert validate_server_message(first) is None
    assert validate_server_message(second) is None
    assert first["challenge_id"] == second["challenge_id"] == "challenge-1"
    assert sink.codes == ["ABC123"]
    assert "ABC123" not in repr(first)
    assert "ABC123" not in repr(second)
    assert "code" not in first
    assert "code" not in second


def test_confirm_success_returns_validated_confirmed(
    runtime: PairingRuntime, clock: _Clock
) -> None:
    runtime.prepare("request-1")
    confirmed = runtime.confirm("request-1", "challenge-1", "ABC123")
    assert validate_server_message(confirmed) is None
    assert confirmed == {
        "type": "pairing_confirmed",
        "request_id": "request-1",
        "device_id": "device-1",
        "expires_at": clock() + DEVICE_SESSION_TTL_SECONDS,
        "protocol_version": PROTOCOL_VERSION,
    }


def test_wrong_code_and_lockout(runtime: PairingRuntime) -> None:
    runtime.prepare("request-1")
    for code in ("111111", "222222", "333333", "444444"):
        message = runtime.confirm("request-1", "challenge-1", code)
        assert validate_server_message(message) is None
        assert message == {
            "type": "pairing_error",
            "request_id": "request-1",
            "code": "challenge_invalid",
        }

    locked = runtime.confirm("request-1", "challenge-1", "555555")
    assert validate_server_message(locked) is None
    assert locked == {
        "type": "pairing_error",
        "request_id": "request-1",
        "code": "pairing_locked",
    }

    still_locked = runtime.confirm("request-1", "challenge-1", "ABC123")
    assert still_locked["code"] == "pairing_locked"


def test_expiry_boundary(runtime: PairingRuntime, clock: _Clock) -> None:
    runtime.prepare("request-1")
    clock.advance(CHALLENGE_TTL_SECONDS)
    expired = runtime.confirm("request-1", "challenge-1", "ABC123")
    assert validate_server_message(expired) is None
    assert expired == {
        "type": "pairing_error",
        "request_id": "request-1",
        "code": "challenge_expired",
    }


def test_confirm_replay_does_not_issue_second_device(runtime: PairingRuntime) -> None:
    runtime.prepare("request-1")
    first = runtime.confirm("request-1", "challenge-1", "ABC123")
    replay = runtime.confirm("request-1", "challenge-1", "ABC123")
    assert first["type"] == "pairing_confirmed"
    assert first["device_id"] == "device-1"
    assert validate_server_message(replay) is None
    assert replay == {
        "type": "pairing_error",
        "request_id": "request-1",
        "code": "challenge_invalid",
    }


def test_cancel_and_duplicate_cancel(runtime: PairingRuntime) -> None:
    runtime.prepare("request-1")
    first = runtime.cancel("request-1", "challenge-1")
    second = runtime.cancel("request-1", "challenge-1")
    assert validate_server_message(first) is None
    assert validate_server_message(second) is None
    assert first == {
        "type": "pairing_update",
        "request_id": "request-1",
        "status": "cancelled",
        "challenge_id": "challenge-1",
    }
    assert second == first


def test_owner_revoke_and_guest_rejection(runtime: PairingRuntime) -> None:
    runtime.prepare("request-1")
    confirmed = runtime.confirm("request-1", "challenge-1", "ABC123")
    device_id = confirmed["device_id"]

    guest = runtime.revoke(_guest(), "request-2", device_id)
    assert validate_server_message(guest) is None
    assert guest == {
        "type": "pairing_error",
        "request_id": "request-2",
        "code": "unauthorized",
    }

    revoked = runtime.revoke(_owner(), "request-2", device_id)
    assert validate_server_message(revoked) is None
    assert revoked == {
        "type": "pairing_update",
        "request_id": "request-2",
        "status": "revoked",
        "device_id": device_id,
    }

    again = runtime.revoke(_owner(), "request-3", device_id)
    assert again["type"] == "pairing_update"
    assert again["status"] == "revoked"

    missing = runtime.revoke(_owner(), "request-4", "missing-device")
    assert missing == {
        "type": "pairing_error",
        "request_id": "request-4",
        "code": "device_not_found",
    }


def test_sink_exception_fail_closed_without_secret_leakage(
    runtime: PairingRuntime, sink: _Sink
) -> None:
    sink.raise_on_display = True
    message = runtime.prepare("request-1")
    assert validate_server_message(message) is None
    assert message == {
        "type": "pairing_error",
        "request_id": "request-1",
        "code": "unavailable",
    }
    assert "ABC123" not in repr(message)
    assert sink.codes == []


def test_forbidden_client_fields_rejected(runtime: PairingRuntime) -> None:
    message = runtime.prepare("request-1", actor_id="attacker", session_id="evil")
    assert message["code"] == "invalid_request"
    assert validate_server_message(message) is None


def test_invalid_request_id(runtime: PairingRuntime) -> None:
    message = runtime.prepare("BAD")
    assert validate_server_message(message) is None
    assert message["type"] == "pairing_error"
    assert message["code"] == "invalid_request"


def test_expire_due_returns_counts_only(runtime: PairingRuntime, clock: _Clock) -> None:
    runtime.prepare("request-1")
    clock.advance(CHALLENGE_TTL_SECONDS)
    counts = runtime.expire_due()
    assert counts == (1, 0)
    assert isinstance(counts, tuple)


def test_content_free_repr(runtime: PairingRuntime) -> None:
    assert repr(runtime) == "PairingRuntime()"


def test_injected_clock_boundary_before_expiry(
    runtime: PairingRuntime, clock: _Clock
) -> None:
    runtime.prepare("request-1")
    clock.advance(CHALLENGE_TTL_SECONDS - 1)
    confirmed = runtime.confirm("request-1", "challenge-1", "ABC123")
    assert confirmed["type"] == "pairing_confirmed"


def test_runtime_has_no_forbidden_imports() -> None:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "pairing"
        / "runtime.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "asyncio",
        "requests",
        "http",
        "urllib",
        "sqlite3",
        "secrets",
        "time",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported)


def test_lockout_uses_max_attempts_constant() -> None:
    assert MAX_CONFIRMATION_ATTEMPTS == 5
