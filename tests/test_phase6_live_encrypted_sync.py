"""Tests for the live encrypted-sync SQLite backends.

Uses temporary directories and synthetic ciphertext only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.phase6_adapters import AdapterException, AdapterReason
from core.phase6_adapters.encrypted_sync import (
    EncryptedSyncAdapter,
    EncryptedSyncAdapterConfig,
    EncryptedSyncAdapterOutcome,
)
from core.phase6_ecosystem.encrypted_sync import (
    DeviceTrustRecord,
    EncryptedObjectDescriptor,
    EncryptionProviderInterface,
    SyncManifest,
    SyncPlan,
)
from core.phase6_live.encrypted_sync import (
    SqliteDeviceRegistry,
    SqliteEncryptedSyncStorage,
    SqliteNonceRegistry,
    SqliteTransactionRegistry,
)


class _FakeProvider(EncryptionProviderInterface):
    @property
    def is_verified(self) -> bool:
        return True

    def verify_descriptor(self, descriptor):
        return True


def _desc() -> EncryptedObjectDescriptor:
    return EncryptedObjectDescriptor("obj1", "0" * 64, 1, 1, 0.0)


def test_nonce_registry_disabled_without_db() -> None:
    reg = SqliteNonceRegistry()
    with pytest.raises(AdapterException) as exc:
        reg.is_consumed("x")
    assert exc.value.reason is AdapterReason.MISSING_DEPENDENCY
    with pytest.raises(AdapterException) as exc:
        reg.consume("x")
    assert exc.value.reason is AdapterReason.MISSING_DEPENDENCY


def test_nonce_persistence_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "nonce.db"
    reg1 = SqliteNonceRegistry(db)
    assert reg1.consume("nonce1") is True
    assert reg1.is_consumed("nonce1") is True
    reg2 = SqliteNonceRegistry(db)
    assert reg2.is_consumed("nonce1") is True
    assert reg2.consume("nonce1") is False


def test_device_registry_persistence_and_revocation(tmp_path: Path) -> None:
    db = tmp_path / "device.db"
    reg = SqliteDeviceRegistry(db)
    # Unknown devices are untrusted/revoked by default.
    assert reg.is_revoked("d1") is True
    assert reg.is_trusted("d1") is False
    assert reg.enroll("d1") is True
    assert reg.is_revoked("d1") is False
    assert reg.is_trusted("d1") is True
    reg.revoke("d1")
    assert reg.is_revoked("d1") is True
    assert reg.is_trusted("d1") is False
    # Enroll must not silently override revoked.
    assert reg.enroll("d1") is False
    assert reg.is_revoked("d1") is True
    # Explicit reenrollment succeeds.
    assert reg.reenroll("d1") is True
    assert reg.is_trusted("d1") is True
    reg2 = SqliteDeviceRegistry(db)
    assert reg2.is_trusted("d1") is True


def test_transaction_registry_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "tx.db"
    reg = SqliteTransactionRegistry(db)
    desc = _desc()
    plan = SyncPlan("plan1", (desc,), (), (), 0.0)
    from core.phase6_adapters.encrypted_sync import EncryptedSyncTransactionProposal, EncryptedSyncTransactionRecord, EncryptedSyncTransactionState
    proposal = EncryptedSyncTransactionProposal("tx1", plan, "sha256." + "0" * 64, "sha256." + "1" * 64)
    record = EncryptedSyncTransactionRecord("tx1", proposal, "d1", EncryptedSyncTransactionState.STAGED, 0.0)
    assert reg.put(record) is True
    loaded = reg.get("tx1")
    assert loaded is not None
    assert loaded.transaction_id == "tx1"
    assert loaded.state is EncryptedSyncTransactionState.STAGED
    assert loaded.proposal.commit_digest == proposal.commit_digest


def test_storage_stage_commit_rollback(tmp_path: Path) -> None:
    db = tmp_path / "storage.db"
    storage = SqliteEncryptedSyncStorage(db)
    plan = SyncPlan("plan1", (_desc(),), (), (), 0.0)
    ok, _ = storage.stage("tx1", plan)
    assert ok is True
    assert storage.commit("tx1") == (True, "ok")
    assert storage.commit("tx1") == (False, "already_committed")
    assert storage.rollback("tx1")[0] is True
    assert storage.rollback("tx1")[0] is False


def test_storage_rollback_unknown_denies(tmp_path: Path) -> None:
    storage = SqliteEncryptedSyncStorage(tmp_path / "storage2.db")
    assert storage.rollback("unknown") == (False, "not_found")


def test_adapter_with_durable_registries(tmp_path: Path) -> None:
    nonce_db = tmp_path / "nonce.db"
    device_db = tmp_path / "device.db"
    tx_db = tmp_path / "tx.db"
    storage_db = tmp_path / "storage.db"
    adapter = EncryptedSyncAdapter(
        config=EncryptedSyncAdapterConfig(),
        encryption_provider=_FakeProvider(),
        storage=SqliteEncryptedSyncStorage(storage_db),
        nonce_registry=SqliteNonceRegistry(nonce_db),
        device_registry=SqliteDeviceRegistry(device_db),
        transaction_registry=SqliteTransactionRegistry(tx_db),
        clock=lambda: 0.0,
        id_factory=lambda: "id0",
    )
    local = SyncManifest("m1", "d1", (_desc(),), 0.0)
    remote = SyncManifest("m2", "d1", (_desc(),), 0.0)
    adapter._device_registry.enroll("d1")
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    proposal = adapter.plan_sync("plan1", local, remote, trust, "nonce1")
    assert adapter.commit(proposal) is EncryptedSyncAdapterOutcome.ALLOW


def test_nonce_pruning_keeps_recent(tmp_path: Path) -> None:
    db = tmp_path / "nonce.db"
    reg = SqliteNonceRegistry(db, max_age_seconds=1000)
    assert reg.consume("old")
    # Simulate old nonce by manually aging it.
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE sync_nonce SET consumed_at = consumed_at - 2000")
    conn.commit()
    conn.close()
    assert reg.consume("new") is True
    assert reg.is_consumed("old") is False


def test_device_registry_revoke_idempotent(tmp_path: Path) -> None:
    reg = SqliteDeviceRegistry(tmp_path / "device.db")
    reg.revoke("d1")
    reg.revoke("d1")
    assert reg.is_revoked("d1") is True


def test_transaction_insert_once_and_corrupt_record(tmp_path: Path) -> None:
    db = tmp_path / "tx.db"
    reg = SqliteTransactionRegistry(db)
    desc = _desc()
    plan = SyncPlan("plan1", (desc,), (), (), 0.0)
    from core.phase6_adapters.encrypted_sync import EncryptedSyncTransactionProposal, EncryptedSyncTransactionRecord, EncryptedSyncTransactionState
    proposal = EncryptedSyncTransactionProposal("tx1", plan, "sha256." + "0" * 64, "sha256." + "1" * 64)
    record = EncryptedSyncTransactionRecord("tx1", proposal, "d1", EncryptedSyncTransactionState.STAGED, 0.0)
    assert reg.put(record) is True
    # Duplicate with same data is allowed (idempotent CAS transition).
    assert reg.put(record) is True
    # Duplicate with different data is rejected.
    proposal2 = EncryptedSyncTransactionProposal("tx1", plan, "sha256." + "9" * 64, "sha256." + "1" * 64)
    record2 = EncryptedSyncTransactionRecord("tx1", proposal2, "d1", EncryptedSyncTransactionState.STAGED, 0.0)
    assert reg.put(record2) is False
    # Corrupt JSON row fails closed.
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE sync_transaction SET proposal_json = ? WHERE transaction_id = ?", ("not-json", "tx1"))
    conn.commit()
    conn.close()
    with pytest.raises(AdapterException):
        reg.get("tx1")


def test_ciphertext_store_and_load(tmp_path: Path) -> None:
    db = tmp_path / "storage.db"
    storage = SqliteEncryptedSyncStorage(db)
    assert storage.store_ciphertext("d1", b"secret") == (True, "ok")
    assert storage.load_ciphertext("d1") == b"secret"
    assert storage.store_ciphertext("d1", b"secret2") == (True, "already_exists")
    assert storage.load_ciphertext("d1") == b"secret"
    assert storage.load_ciphertext("missing") is None
    assert storage.store_ciphertext("", b"secret") == (False, "invalid_digest")
    assert storage.store_ciphertext("d2", "not bytes") == (False, "invalid_ciphertext")  # type: ignore[arg-type]
    # Persistence across a fresh instance.
    storage2 = SqliteEncryptedSyncStorage(db)
    assert storage2.load_ciphertext("d1") == b"secret"


# Corruption / schema validation tests for persisted transaction records.


def _make_valid_record_json() -> dict:
    return {
        "schema_version": 1,
        "transaction_id": "tx1",
        "device_id": "d1",
        "state": "STAGED",
        "created_at": 1.0,
        "proposal": {
            "transaction_id": "tx1",
            "commit_digest": "sha256." + "0" * 64,
            "rollback_digest": "sha256." + "1" * 64,
            "plan": {
                "plan_id": "plan1",
                "created_at": 1.0,
                "uploads": [],
                "downloads": [],
                "conflicts": [],
            },
        },
    }


def test_corrupt_record_extra_field(tmp_path: Path) -> None:
    db = tmp_path / "tx.db"
    reg = SqliteTransactionRegistry(db)
    import sqlite3

    conn = sqlite3.connect(str(db))
    data = _make_valid_record_json()
    data["extra"] = "field"
    conn.execute(
        "INSERT INTO sync_transaction (transaction_id, device_id, state, created_at, proposal_json) VALUES (?, ?, ?, ?, ?)",
        ("tx1", "d1", "STAGED", 1.0, " ".join(json.dumps(data).split())),
    )
    conn.commit()
    conn.close()
    with pytest.raises(AdapterException):
        reg.get("tx1")


def test_corrupt_record_missing_field(tmp_path: Path) -> None:
    db = tmp_path / "tx.db"
    reg = SqliteTransactionRegistry(db)
    import sqlite3

    conn = sqlite3.connect(str(db))
    data = _make_valid_record_json()
    del data["created_at"]
    conn.execute(
        "INSERT INTO sync_transaction (transaction_id, device_id, state, created_at, proposal_json) VALUES (?, ?, ?, ?, ?)",
        ("tx1", "d1", "STAGED", 1.0, json.dumps(data)),
    )
    conn.commit()
    conn.close()
    with pytest.raises(AdapterException):
        reg.get("tx1")


def test_corrupt_record_wrong_type(tmp_path: Path) -> None:
    db = tmp_path / "tx.db"
    reg = SqliteTransactionRegistry(db)
    import sqlite3

    conn = sqlite3.connect(str(db))
    data = _make_valid_record_json()
    data["created_at"] = "not-a-number"
    conn.execute(
        "INSERT INTO sync_transaction (transaction_id, device_id, state, created_at, proposal_json) VALUES (?, ?, ?, ?, ?)",
        ("tx1", "d1", "STAGED", 1.0, json.dumps(data)),
    )
    conn.commit()
    conn.close()
    with pytest.raises(AdapterException):
        reg.get("tx1")


def test_corrupt_record_bool_as_number(tmp_path: Path) -> None:
    db = tmp_path / "tx.db"
    reg = SqliteTransactionRegistry(db)
    import sqlite3

    conn = sqlite3.connect(str(db))
    data = _make_valid_record_json()
    data["created_at"] = True
    conn.execute(
        "INSERT INTO sync_transaction (transaction_id, device_id, state, created_at, proposal_json) VALUES (?, ?, ?, ?, ?)",
        ("tx1", "d1", "STAGED", 1.0, json.dumps(data)),
    )
    conn.commit()
    conn.close()
    with pytest.raises(AdapterException):
        reg.get("tx1")


def test_corrupt_record_nan_infinity(tmp_path: Path) -> None:
    db = tmp_path / "tx.db"
    reg = SqliteTransactionRegistry(db)
    import sqlite3

    for bad in (float("nan"), float("inf")):
        conn = sqlite3.connect(str(db))
        data = _make_valid_record_json()
        data["created_at"] = bad
        conn.execute("DELETE FROM sync_transaction")
        conn.execute(
            "INSERT INTO sync_transaction (transaction_id, device_id, state, created_at, proposal_json) VALUES (?, ?, ?, ?, ?)",
            ("tx1", "d1", "STAGED", 1.0, json.dumps(data)),
        )
        conn.commit()
        conn.close()
        with pytest.raises(AdapterException):
            reg.get("tx1")


def test_corrupt_record_invalid_digest(tmp_path: Path) -> None:
    db = tmp_path / "tx.db"
    reg = SqliteTransactionRegistry(db)
    import sqlite3

    conn = sqlite3.connect(str(db))
    data = _make_valid_record_json()
    data["proposal"]["commit_digest"] = "not-a-digest"
    conn.execute(
        "INSERT INTO sync_transaction (transaction_id, device_id, state, created_at, proposal_json) VALUES (?, ?, ?, ?, ?)",
        ("tx1", "d1", "STAGED", 1.0, json.dumps(data)),
    )
    conn.commit()
    conn.close()
    with pytest.raises(AdapterException):
        reg.get("tx1")


def test_corrupt_record_state_mismatch(tmp_path: Path) -> None:
    db = tmp_path / "tx.db"
    reg = SqliteTransactionRegistry(db)
    import sqlite3

    conn = sqlite3.connect(str(db))
    data = _make_valid_record_json()
    data["state"] = "COMMITTED"
    conn.execute(
        "INSERT INTO sync_transaction (transaction_id, device_id, state, created_at, proposal_json) VALUES (?, ?, ?, ?, ?)",
        ("tx1", "d1", "STAGED", 1.0, json.dumps(data)),
    )
    conn.commit()
    conn.close()
    with pytest.raises(AdapterException):
        reg.get("tx1")
