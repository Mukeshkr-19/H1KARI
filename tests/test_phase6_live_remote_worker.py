"""Tests for the live remote-worker durable state backend.

Uses temporary directories and synthetic envelopes only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.phase6_adapters import AdapterException, AdapterReason
from core.phase6_adapters.remote_worker import CancellationAcknowledgement
from core.phase6_agent.contracts import RemoteWorkerAuthorityEnvelope
from core.phase6_live.remote_worker import (
    SqliteRemoteWorkerNonceStore,
    SqliteRemoteWorkerState,
)


def _envelope(envelope_id: str = "e1", worker_id: str = "w1") -> RemoteWorkerAuthorityEnvelope:
    return RemoteWorkerAuthorityEnvelope(
        envelope_id=envelope_id,
        worker_id=worker_id,
        task_id="t1",
        capability="read",
        targets=("target1",),
        expires_at_mono=10.0,
        nonce="0" * 16,
        max_responses=2,
    )


def test_nonce_store_disabled_without_db() -> None:
    store = SqliteRemoteWorkerNonceStore()
    with pytest.raises(AdapterException) as exc:
        store.is_consumed("x")
    assert exc.value.reason is AdapterReason.MISSING_DEPENDENCY
    with pytest.raises(AdapterException) as exc:
        store.consume("x")
    assert exc.value.reason is AdapterReason.MISSING_DEPENDENCY


def test_nonce_store_persistence(tmp_path: Path) -> None:
    db = tmp_path / "nonce.db"
    store1 = SqliteRemoteWorkerNonceStore(db)
    assert store1.consume("nonce1") is True
    store2 = SqliteRemoteWorkerNonceStore(db)
    assert store2.is_consumed("nonce1") is True
    assert store2.consume("nonce1") is False


def test_worker_state_disabled_without_db() -> None:
    state = SqliteRemoteWorkerState()
    assert state.is_revoked("w1") is True  # fail closed
    with pytest.raises(AdapterException) as exc:
        state.revoke_worker("w1")
    assert exc.value.reason is AdapterReason.MISSING_DEPENDENCY


def test_worker_revocation_persistence(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    state1 = SqliteRemoteWorkerState(db)
    state1.enroll_worker("w1")
    state1.revoke_worker("w1")
    assert state1.is_revoked("w1") is True
    state2 = SqliteRemoteWorkerState(db)
    assert state2.is_revoked("w1") is True


def test_quarantine_persistence(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    state = SqliteRemoteWorkerState(db)
    state.enroll_worker("w1")
    state.quarantine_worker("w1")
    assert state.is_quarantined("w1") is True


def test_job_correlation(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    state = SqliteRemoteWorkerState(db)
    env = _envelope()
    assert state.record_job(env, "submitted", 0.0, expires_at_epoch=10.0) is True
    assert state.get_job_state("e1") == "submitted"
    state.increment_response("e1")
    assert state.get_response_count("e1") == 1


def test_cancellation_persistence(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    state = SqliteRemoteWorkerState(db)
    env = _envelope()
    state.record_job(env, "active", 0.0, expires_at_epoch=10.0)
    ack = CancellationAcknowledgement("e1", 1.0, "ack1")
    assert state.record_cancellation("e1", ack) is True
    assert state.is_cancelled("e1") is True


def test_revoke_and_quarantine_are_keyed_by_worker(tmp_path: Path) -> None:
    state = SqliteRemoteWorkerState(tmp_path / "state.db")
    state.enroll_worker("w1")
    state.enroll_worker("w2")
    state.revoke_worker("w1")
    state.quarantine_worker("w2")
    assert state.is_revoked("w1") is True
    assert state.is_quarantined("w2") is True
    # Unknown and quarantined workers are not enrolled/trusted.
    assert state.is_trusted("w1") is False
    assert state.is_trusted("w2") is False
    assert state.is_trusted("w3") is False


def test_enrollment_does_not_override_revoked(tmp_path: Path) -> None:
    state = SqliteRemoteWorkerState(tmp_path / "state.db")
    state.enroll_worker("w1")
    assert state.is_trusted("w1") is True
    state.revoke_worker("w1")
    assert state.is_trusted("w1") is False
    # Re-enrollment without explicit intent is denied.
    assert state.enroll_worker("w1") is False
    assert state.is_trusted("w1") is False
    # Explicit reenrollment succeeds.
    assert state.reenroll_worker("w1") is True
    assert state.is_trusted("w1") is True


def test_cancellation_insert_once_and_cas(tmp_path: Path) -> None:
    state = SqliteRemoteWorkerState(tmp_path / "state.db")
    env = _envelope()
    state.record_job(env, "active", 0.0, expires_at_epoch=10.0)
    ack1 = CancellationAcknowledgement("e1", 1.0, "ack1")
    ack2 = CancellationAcknowledgement("e1", 2.0, "ack2")
    assert state.record_cancellation("e1", ack1) is True
    # Duplicate identical acknowledgement is idempotent.
    assert state.record_cancellation("e1", ack1) is True
    # Conflicting acknowledgement is rejected.
    assert state.record_cancellation("e1", ack2) is False


def test_record_result_validates_identifiers_and_bytes(tmp_path: Path) -> None:
    import time as _time
    state = SqliteRemoteWorkerState(tmp_path / "state.db")
    env = _envelope()
    state.enroll_worker("w1")
    state.record_job(env, "active", 0.0, expires_at_epoch=_time.time() + 100.0)
    assert state.record_result("e1", b"ok", _time.time()) is True
    # Duplicate result rejected (insert-once).
    assert state.record_result("e1", b"ok2", 2.0) is False
    # Invalid identifier.
    assert state.record_result("", b"ok", 1.0) is False
    # Oversized result.
    assert state.record_result("e2", b"x", 1.0, max_result_bytes=0) is False
    # Bad timestamp.
    assert state.record_result("e3", b"ok", float("nan")) is False


def test_record_job_rejects_invalid_envelope_and_epoch(tmp_path: Path) -> None:
    state = SqliteRemoteWorkerState(tmp_path / "state.db")
    env = _envelope()
    assert state.record_job(env, "submitted", 0.0, expires_at_epoch=float("inf")) is False
    assert state.record_job(env, "submitted", float("nan"), expires_at_epoch=10.0) is False



def test_max_result_bytes_rejects_above_hard_max(tmp_path: Path) -> None:
    import time as _time
    state = SqliteRemoteWorkerState(tmp_path / "rw.db", clock=_time.time)
    state.enroll_worker("w1")
    env = _envelope(worker_id="w1", envelope_id="e1")
    state.record_job(env, "active", _time.time(), expires_at_epoch=_time.time() + 100.0)
    hard = state._HARD_MAX_RESULT_BYTES
    assert state.record_result("e1", b"ok", _time.time(), max_result_bytes=hard) is True
    # Above hard max rejected (not clamped)
    assert state.record_result("e1", b"ok2", _time.time(), max_result_bytes=hard + 1) is False
    assert state.record_result("e2", b"x", _time.time(), max_result_bytes=True) is False  # type: ignore[arg-type]
    assert state.record_result("e2", b"x", _time.time(), max_result_bytes=0) is False
    assert state.record_result("e2", b"x", _time.time(), max_result_bytes=-1) is False


def test_result_exact_cap_and_json_nan_rejected(tmp_path: Path) -> None:
    import time as _time
    state = SqliteRemoteWorkerState(tmp_path / "rw.db", clock=_time.time)
    state.enroll_worker("w1")
    env = _envelope(worker_id="w1", envelope_id="cap1")
    state.record_job(env, "active", _time.time(), expires_at_epoch=_time.time() + 100.0)
    payload = b"a" * 8
    assert state.record_result("cap1", payload, _time.time(), max_result_bytes=8) is True
    env2 = _envelope(worker_id="w1", envelope_id="cap2")
    state.record_job(env2, "active", _time.time(), expires_at_epoch=_time.time() + 100.0)
    assert state.record_result("cap2", b"a" * 9, _time.time(), max_result_bytes=8) is False
    env3 = _envelope(worker_id="w1", envelope_id="j1")
    state.record_job(env3, "active", _time.time(), expires_at_epoch=_time.time() + 100.0)
    assert state.record_result(
        "j1", b'{"a": NaN}', _time.time(), content_type="application/json"
    ) is False
    assert state.record_result(
        "j1", b'{"a": 1, "a": 2}', _time.time(), content_type="application/json"
    ) is False


def test_cancellation_idempotent_requires_cancelled_job(tmp_path: Path) -> None:
    import sqlite3
    import time as _time
    db = tmp_path / "rw.db"
    state = SqliteRemoteWorkerState(db, clock=_time.time)
    state.enroll_worker("w1")
    env = _envelope(worker_id="w1", envelope_id="e1")
    state.record_job(env, "active", 0.0, expires_at_epoch=10.0)
    ack = CancellationAcknowledgement("e1", 1.0, "ack1")
    assert state.record_cancellation("e1", ack) is True
    assert state.record_cancellation("e1", ack) is True
    # Tamper: ack row exists but job not cancelled
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE rw_job SET state = 'active', cancelled_at = NULL WHERE envelope_id = 'e1'")
    conn.commit()
    conn.close()
    assert state.record_cancellation("e1", ack) is False


def test_rw_job_schema_has_epoch_not_mono(tmp_path: Path) -> None:
    import sqlite3
    db = tmp_path / "rw.db"
    state = SqliteRemoteWorkerState(db)
    conn = sqlite3.connect(str(db))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(rw_job)")}
    conn.close()
    assert "expires_at_epoch" in cols
    assert "expires_at_mono" not in cols


def test_incompatible_mono_schema_fails_closed(tmp_path: Path) -> None:
    import sqlite3
    from core.phase6_adapters.contracts import AdapterException
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE rw_job (envelope_id TEXT PRIMARY KEY, expires_at_mono REAL NOT NULL)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(AdapterException):
        SqliteRemoteWorkerState(db)
