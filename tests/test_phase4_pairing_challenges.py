"""Tests for core.pairing.challenge_store and pairing service challenge flows."""

from __future__ import annotations

import ast
import math
import pathlib
import threading
from collections import deque

import pytest

from core.pairing.challenge_store import PairingChallengeStore
from core.pairing.contracts import (
    CHALLENGE_TTL_SECONDS,
    MAX_CONFIRMATION_ATTEMPTS,
    PairingChallengeState,
    PairingErrorCode,
    PairingOutcomeStatus,
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

    def set(self, value: float) -> None:
        self._value = value


@pytest.fixture
def digest_key() -> bytes:
    return b"phase4-test-digest-key"


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def challenge_ids() -> deque[str]:
    return deque(["challenge-1", "challenge-2", "challenge-3", "challenge-4"])


@pytest.fixture
def codes() -> deque[str]:
    return deque(["ABC123", "DEF456", "111111", "222222", "333333", "444444", "555555"])


@pytest.fixture
def challenge_store(
    clock: _Clock,
    challenge_ids: deque[str],
    codes: deque[str],
    digest_key: bytes,
) -> PairingChallengeStore:
    return PairingChallengeStore(
        clock=clock,
        challenge_id_factory=lambda: challenge_ids.popleft(),
        secret_code_factory=lambda: codes.popleft(),
        digest_key=digest_key,
    )


@pytest.fixture
def device_ids() -> deque[str]:
    return deque(["device-1", "device-2", "device-3"])


@pytest.fixture
def device_store(clock: _Clock, device_ids: deque[str], tmp_path) -> DeviceSessionStore:
    return DeviceSessionStore(
        tmp_path / "devices.db",
        clock=clock,
        device_id_factory=lambda: device_ids.popleft(),
    )


@pytest.fixture
def service(challenge_store: PairingChallengeStore, device_store: DeviceSessionStore) -> PairingService:
    return PairingService(
        challenge_store=challenge_store,
        device_store=device_store,
    )


def test_prepare_and_confirm_success(
    challenge_store: PairingChallengeStore,
    service: PairingService,
) -> None:
    prepared = challenge_store.prepare("request-1", device_label="Phone")
    assert prepared.status is PairingOutcomeStatus.OK
    assert prepared.challenge_id == "challenge-1"
    assert prepared.code == "ABC123"

    confirmed = service.confirm("request-1", "challenge-1", "ABC123")
    assert confirmed.status is PairingOutcomeStatus.OK
    assert confirmed.device_id == "device-1"
    snapshot = challenge_store.get_challenge("challenge-1")
    assert snapshot is not None
    assert snapshot.state is PairingChallengeState.CONSUMED


def test_challenge_id_collision_fails_without_overwriting_existing_record(
    clock: _Clock, digest_key: bytes
) -> None:
    store = PairingChallengeStore(
        clock=clock,
        challenge_id_factory=lambda: "challenge-1",
        secret_code_factory=lambda: "ABC123",
        digest_key=digest_key,
    )
    first = store.prepare("request-1")
    second = store.prepare("request-2")
    assert first.status is PairingOutcomeStatus.OK
    assert second.status is PairingOutcomeStatus.ERROR
    assert second.error is PairingErrorCode.UNAVAILABLE
    snapshot = store.get_challenge("challenge-1")
    assert snapshot is not None
    assert snapshot.request_id == "request-1"


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_challenge_clock_fails_closed(value: float, digest_key: bytes) -> None:
    store = PairingChallengeStore(
        clock=lambda: value,
        challenge_id_factory=lambda: "challenge-1",
        secret_code_factory=lambda: "ABC123",
        digest_key=digest_key,
    )
    outcome = store.prepare("request-1")
    assert outcome.status is PairingOutcomeStatus.ERROR
    assert outcome.error is PairingErrorCode.UNAVAILABLE
    assert store.get_challenge("challenge-1") is None


@pytest.mark.parametrize("attempt", range(1, MAX_CONFIRMATION_ATTEMPTS))
def test_wrong_code_attempts_increment(
    challenge_store: PairingChallengeStore,
    attempt: int,
) -> None:
    challenge_store.prepare("request-1")
    wrong_codes = ["111111", "222222", "333333", "444444"]
    outcome = None
    for index in range(attempt):
        outcome = challenge_store.confirm(
            "request-1",
            "challenge-1",
            wrong_codes[index],
        )
    assert outcome is not None
    assert outcome.status is PairingOutcomeStatus.ERROR
    assert outcome.error is PairingErrorCode.WRONG_CODE
    assert outcome.attempts_remaining == MAX_CONFIRMATION_ATTEMPTS - attempt

    snapshot = challenge_store.get_challenge("challenge-1")
    assert snapshot is not None
    assert snapshot.attempts == attempt
    assert snapshot.state is PairingChallengeState.PENDING


def test_fifth_wrong_attempt_locks(challenge_store: PairingChallengeStore) -> None:
    challenge_store.prepare("request-1")
    for code in ("111111", "222222", "333333", "444444"):
        challenge_store.confirm("request-1", "challenge-1", code)

    locked = challenge_store.confirm("request-1", "challenge-1", "555555")
    assert locked.status is PairingOutcomeStatus.ERROR
    assert locked.error is PairingErrorCode.LOCKED

    snapshot = challenge_store.get_challenge("challenge-1")
    assert snapshot is not None
    assert snapshot.state is PairingChallengeState.LOCKED

    still_locked = challenge_store.confirm("request-1", "challenge-1", "ABC123")
    assert still_locked.error is PairingErrorCode.LOCKED


def test_exact_expiry_boundary(challenge_store: PairingChallengeStore, clock: _Clock) -> None:
    challenge_store.prepare("request-1")
    clock.advance(CHALLENGE_TTL_SECONDS - 1)
    assert challenge_store.confirm("request-1", "challenge-1", "ABC123").status is (
        PairingOutcomeStatus.OK
    )

    challenge_store.prepare("request-2")
    clock.advance(CHALLENGE_TTL_SECONDS)
    expired = challenge_store.confirm("request-2", "challenge-2", "DEF456")
    assert expired.status is PairingOutcomeStatus.ERROR
    assert expired.error is PairingErrorCode.EXPIRED


def test_expire_due_transitions_pending(challenge_store: PairingChallengeStore, clock: _Clock) -> None:
    challenge_store.prepare("request-1")
    clock.advance(CHALLENGE_TTL_SECONDS)
    assert challenge_store.expire_due() == 1
    snapshot = challenge_store.get_challenge("challenge-1")
    assert snapshot is not None
    assert snapshot.state is PairingChallengeState.EXPIRED


def test_replay_after_consumption_fails(
    challenge_store: PairingChallengeStore,
    service: PairingService,
) -> None:
    challenge_store.prepare("request-1")
    first = service.confirm("request-1", "challenge-1", "ABC123")
    assert first.status is PairingOutcomeStatus.OK
    assert first.device_id == "device-1"

    replay = service.confirm("request-1", "challenge-1", "ABC123")
    assert replay.status is PairingOutcomeStatus.ERROR
    assert replay.error is PairingErrorCode.ALREADY_CONSUMED
    assert replay.device_id is None


def test_duplicate_prepare_returns_same_challenge_without_secret(
    challenge_store: PairingChallengeStore,
) -> None:
    first = challenge_store.prepare("request-1")
    second = challenge_store.prepare("request-1")
    assert first.challenge_id == second.challenge_id == "challenge-1"
    assert first.code == "ABC123"
    assert second.code is None


def test_duplicate_cancel_is_idempotent(challenge_store: PairingChallengeStore) -> None:
    challenge_store.prepare("request-1")
    first = challenge_store.cancel("challenge-1")
    second = challenge_store.cancel("challenge-1")
    assert first.status is PairingOutcomeStatus.OK
    assert second.status is PairingOutcomeStatus.OK


def test_cancel_consumed_challenge_fails_closed(
    challenge_store: PairingChallengeStore,
) -> None:
    challenge_store.prepare("request-1")
    challenge_store.confirm("request-1", "challenge-1", "ABC123")
    outcome = challenge_store.cancel("challenge-1")
    assert outcome.status is PairingOutcomeStatus.ERROR


def test_invalid_ids_and_codes(challenge_store: PairingChallengeStore) -> None:
    invalid_prepare = challenge_store.prepare("")
    assert invalid_prepare.error is PairingErrorCode.INVALID_INPUT

    challenge_store.prepare("request-1")
    bad_confirm = challenge_store.confirm("request-1", "challenge-1", "bad")
    assert bad_confirm.error is PairingErrorCode.INVALID_INPUT

    not_found = challenge_store.confirm("request-1", "missing", "ABC123")
    assert not_found.error is PairingErrorCode.NOT_FOUND


def test_future_and_non_monotonic_clock_values(
    challenge_ids: deque[str],
    codes: deque[str],
    digest_key: bytes,
) -> None:
    values = iter([5000.0, 4000.0, 4500.0])

    def clock() -> float:
        return next(values)

    store = PairingChallengeStore(
        clock=clock,
        challenge_id_factory=lambda: challenge_ids.popleft(),
        secret_code_factory=lambda: codes.popleft(),
        digest_key=digest_key,
    )
    prepared = store.prepare("request-1")
    assert prepared.status is PairingOutcomeStatus.OK


def test_concurrent_confirmation_issues_one_device(
    challenge_store: PairingChallengeStore,
    service: PairingService,
) -> None:
    challenge_store.prepare("request-1")
    results: list = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        results.append(service.confirm("request-1", "challenge-1", "ABC123"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    successes = [item for item in results if item.status is PairingOutcomeStatus.OK]
    failures = [item for item in results if item.status is PairingOutcomeStatus.ERROR]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].error is PairingErrorCode.ALREADY_CONSUMED


def test_service_rejects_actor_and_session_fields(service: PairingService) -> None:
    outcome = service.prepare(
        "request-1",
        actor_id="attacker",
        session_id="evil",
    )
    assert outcome.status is PairingOutcomeStatus.ERROR
    assert outcome.error is PairingErrorCode.INVALID_INPUT


def test_plaintext_code_absent_from_challenge_store_internals(
    challenge_store: PairingChallengeStore,
) -> None:
    challenge_store.prepare("request-1")
    snapshot = challenge_store.get_challenge("challenge-1")
    assert snapshot is not None
    assert "ABC123" not in repr(snapshot)
    assert snapshot.digest != "ABC123"


def test_challenge_store_has_no_forbidden_imports() -> None:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "pairing"
        / "challenge_store.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "asyncio",
        "requests",
        "http",
        "urllib",
        "sqlite3",
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
