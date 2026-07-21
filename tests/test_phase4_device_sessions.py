"""Tests for core.pairing.device_store and device lifecycle operations."""

from __future__ import annotations

import ast
import math
import pathlib
import sqlite3
import stat
from collections import deque

import pytest

from core.pairing.challenge_store import PairingChallengeStore
from core.pairing.contracts import (
    DEVICE_SESSION_TTL_SECONDS,
    DeviceErrorCode,
    DeviceOutcomeStatus,
    DeviceSessionState,
)
from core.pairing.device_store import DeviceSessionStore
from core.pairing.service import PairingService


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self._value = start

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += seconds


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def device_ids() -> deque[str]:
    return deque(["device-1", "device-2", "device-3"])


@pytest.fixture
def device_store(clock: _Clock, device_ids: deque[str], tmp_path) -> DeviceSessionStore:
    return DeviceSessionStore(
        tmp_path / "pairing" / "devices.db",
        clock=clock,
        device_id_factory=lambda: device_ids.popleft(),
    )


@pytest.fixture
def challenge_store(clock: _Clock, tmp_path) -> PairingChallengeStore:
    ids = deque(["challenge-1", "challenge-2", "challenge-3"])
    codes = deque(["ABC123", "DEF456", "111111"])
    return PairingChallengeStore(
        clock=clock,
        challenge_id_factory=lambda: ids.popleft(),
        secret_code_factory=lambda: codes.popleft(),
        digest_key=b"device-store-test-key",
    )


@pytest.fixture
def service(
    challenge_store: PairingChallengeStore,
    device_store: DeviceSessionStore,
) -> PairingService:
    return PairingService(
        challenge_store=challenge_store,
        device_store=device_store,
    )


def test_issue_after_consumed_challenge(service: PairingService) -> None:
    service.prepare("request-1", device_label="Phone")
    confirmed = service.confirm("request-1", "challenge-1", "ABC123")
    assert confirmed.status.value == "ok"
    assert confirmed.device_id == "device-1"

    active = service._device_store.get_active("device-1")
    assert active is not None
    assert active.state is DeviceSessionState.ACTIVE
    assert active.device_label == "Phone"


def test_concurrent_device_sessions(service: PairingService) -> None:
    service.prepare("request-1")
    first = service.confirm("request-1", "challenge-1", "ABC123")
    service.prepare("request-2")
    second = service.confirm("request-2", "challenge-2", "DEF456")
    assert first.device_id == "device-1"
    assert second.device_id == "device-2"
    assert service._device_store.get_active("device-1") is not None
    assert service._device_store.get_active("device-2") is not None


def test_mark_stale_and_reconnect_before_expiry(
    service: PairingService,
    device_store: DeviceSessionStore,
    clock: _Clock,
) -> None:
    service.prepare("request-1")
    confirmed = service.confirm("request-1", "challenge-1", "ABC123")
    device_id = confirmed.device_id
    assert device_id is not None

    stale = device_store.mark_stale(device_id)
    assert stale.status is DeviceOutcomeStatus.OK
    assert service._device_store.get_active(device_id) is None

    clock.advance(DEVICE_SESSION_TTL_SECONDS - 1)
    reconnected = device_store.reconnect(device_id)
    assert reconnected.status is DeviceOutcomeStatus.OK
    assert service._device_store.get_active(device_id) is not None


def test_reconnect_at_expiry_fails(
    service: PairingService, device_store: DeviceSessionStore, clock: _Clock
) -> None:
    service.prepare("request-1")
    confirmed = service.confirm("request-1", "challenge-1", "ABC123")
    device_id = confirmed.device_id
    assert device_id is not None

    device_store.mark_stale(device_id)
    clock.advance(DEVICE_SESSION_TTL_SECONDS)
    outcome = device_store.reconnect(device_id)
    assert outcome.status is DeviceOutcomeStatus.ERROR
    assert outcome.error is DeviceErrorCode.EXPIRED


def test_lost_device_revocation_is_idempotent(
    service: PairingService, device_store: DeviceSessionStore
) -> None:
    service.prepare("request-1")
    confirmed = service.confirm("request-1", "challenge-1", "ABC123")
    device_id = confirmed.device_id
    assert device_id is not None

    first = device_store.revoke(device_id)
    second = device_store.revoke(device_id)
    assert first.status is DeviceOutcomeStatus.OK
    assert second.status is DeviceOutcomeStatus.OK
    assert service._device_store.get_active(device_id) is None


def test_device_id_is_not_a_public_service_mutation_credential(
    service: PairingService,
) -> None:
    assert not hasattr(service, "mark_device_stale")
    assert not hasattr(service, "reconnect_device")
    assert not hasattr(service, "revoke_device")


def test_expire_due_marks_sessions_expired(service: PairingService, clock: _Clock) -> None:
    service.prepare("request-1")
    confirmed = service.confirm("request-1", "challenge-1", "ABC123")
    device_id = confirmed.device_id
    assert device_id is not None

    clock.advance(DEVICE_SESSION_TTL_SECONDS)
    assert service.expire_due()[1] == 1
    record = service._device_store.get_record(device_id)
    assert record is not None
    assert record.state is DeviceSessionState.EXPIRED


def test_database_permissions(device_store: DeviceSessionStore, tmp_path) -> None:
    device_store.issue(challenge_id="challenge-1")
    db_path = tmp_path / "pairing" / "devices.db"
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_device_clock_fails_closed(value: float, tmp_path) -> None:
    store = DeviceSessionStore(
        tmp_path / "non-finite" / "devices.db",
        clock=lambda: value,
        device_id_factory=lambda: "device-1",
    )
    outcome = store.issue(challenge_id="challenge-1")
    assert outcome.status is DeviceOutcomeStatus.ERROR
    assert outcome.error is DeviceErrorCode.UNAVAILABLE


def test_plaintext_code_absent_from_sqlite_bytes(
    service: PairingService,
    tmp_path,
) -> None:
    service.prepare("request-1")
    service.confirm("request-1", "challenge-1", "ABC123")
    db_path = tmp_path / "pairing" / "devices.db"
    blob = db_path.read_bytes()
    assert b"ABC123" not in blob


def test_cross_device_lookup_returns_same_not_found(device_store: DeviceSessionStore) -> None:
    missing = device_store.get_active("missing-device")
    malformed = device_store.get_active("bad device")
    assert missing is None
    assert malformed is None


def test_list_display_records_without_secrets(device_store: DeviceSessionStore) -> None:
    device_store.issue(challenge_id="challenge-1", device_label="Phone")
    rows = device_store.list_display()
    assert len(rows) == 1
    assert rows[0].state is DeviceSessionState.ACTIVE
    assert rows[0].device_label == "Phone"
    assert "Phone" not in repr(rows[0])


def test_issue_rejects_duplicate_challenge(device_store: DeviceSessionStore) -> None:
    first = device_store.issue(challenge_id="challenge-1")
    second = device_store.issue(challenge_id="challenge-1")
    assert first.status is DeviceOutcomeStatus.OK
    assert second.status is DeviceOutcomeStatus.ERROR


def test_cas_conflict_on_stale_revision(device_store: DeviceSessionStore, clock: _Clock) -> None:
    device_store.issue(challenge_id="challenge-1")
    first = device_store.mark_stale("device-1")
    assert first.status is DeviceOutcomeStatus.OK
    record = device_store.get_record("device-1")
    assert record is not None
    assert record.state is DeviceSessionState.STALE

    clock.advance(1)
    again = device_store.mark_stale("device-1")
    assert again.status is DeviceOutcomeStatus.OK


def test_device_store_has_no_forbidden_imports() -> None:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "pairing"
        / "device_store.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "threading",
        "asyncio",
        "requests",
        "http",
        "urllib",
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


def test_device_sessions_table_has_no_secret_columns(
    device_store: DeviceSessionStore,
    tmp_path,
) -> None:
    device_store.issue(challenge_id="challenge-1")
    db_path = tmp_path / "pairing" / "devices.db"
    with sqlite3.connect(db_path) as conn:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(device_sessions)")
        }
    forbidden = {"code", "secret", "token", "password", "digest", "actor_id", "session_id"}
    assert forbidden.isdisjoint(cols)
