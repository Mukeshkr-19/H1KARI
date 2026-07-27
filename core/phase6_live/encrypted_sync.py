"""Durable, SQLite-backed implementations for encrypted sync registries.

Implements the injected interfaces from ``core.phase6_adapters.encrypted_sync``:
- NonceReplayRegistry
- DeviceTrustRegistry
- TransactionRegistry
- EncryptedSyncStorageInterface

All state is stored in a caller-supplied SQLite database.  Default construction
with no ``db_path`` leaves the backend disabled and performs no filesystem
access.

Time semantics:
- All durable timestamps use the injected epoch/wall clock (default time.time).
- Monotonic time is used only for in-process elapsed timing where noted.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from core.phase6_adapters.encrypted_sync import (
    DeviceTrustRegistry,
    EncryptedObjectDescriptor,
    EncryptedSyncStorageInterface,
    EncryptedSyncTransactionProposal,
    EncryptedSyncTransactionRecord,
    EncryptedSyncTransactionState,
    NonceReplayRegistry,
    TransactionRegistry,
)
from core.phase6_adapters.contracts import AdapterException, AdapterReason
from core.phase6_ecosystem.encrypted_sync import SyncConflict, SyncPlan
from core.phase6_live.base import safe_makedirs


Clock = Callable[[], float]


def _now_clock(clock: Optional[Clock]) -> float:
    if clock is None:
        return time.time()
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_clock_value")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("invalid_clock_value")
    return value


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


class SqliteNonceRegistry(NonceReplayRegistry):
    """Durable nonce/replay registry backed by SQLite."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        max_age_seconds: float = 86400.0,
        clock: Optional[Clock] = None,
    ) -> None:
        self._db_path = db_path
        self._disabled = db_path is None
        self._max_age = max_age_seconds
        self._clock = clock
        if not self._disabled:
            self._initialize()

    def _require_enabled(self) -> Path:
        if self._disabled or self._db_path is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return self._db_path

    def _now(self) -> float:
        return _now_clock(self._clock)

    def _initialize(self) -> None:
        path = self._require_enabled()
        safe_makedirs(path.parent)
        conn = _connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_nonce (
                    nonce TEXT PRIMARY KEY,
                    consumed_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_nonce_time ON sync_nonce(consumed_at)"
            )
            conn.commit()
        finally:
            conn.close()
        path.chmod(0o600)

    def is_consumed(self, nonce: str) -> bool:
        if self._disabled:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        path = self._require_enabled()
        if not isinstance(nonce, str) or not nonce:
            return False
        self._prune_old(path)
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT 1 FROM sync_nonce WHERE nonce = ?", (nonce,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def consume(self, nonce: str) -> bool:
        path = self._require_enabled()
        if not isinstance(nonce, str) or not nonce:
            return False
        self._prune_old(path)
        conn = _connect(path)
        try:
            try:
                conn.execute(
                    "INSERT INTO sync_nonce (nonce, consumed_at) VALUES (?, ?)",
                    (nonce, self._now()),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                conn.rollback()
                return False
        finally:
            conn.close()

    def _prune_old(self, path: Path) -> None:
        cutoff = self._now() - self._max_age
        conn = _connect(path)
        try:
            conn.execute("DELETE FROM sync_nonce WHERE consumed_at < ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()

    def __repr__(self) -> str:
        return "SqliteNonceRegistry()"


class SqliteDeviceRegistry(DeviceTrustRegistry):
    """Durable trusted/revoked device registry backed by SQLite.

    Device states:
      - enrolled: explicitly trusted
      - revoked: explicitly revoked
      - unknown (no row): untrusted by default
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        clock: Optional[Clock] = None,
    ) -> None:
        self._db_path = db_path
        self._disabled = db_path is None
        self._clock = clock
        if not self._disabled:
            self._initialize()

    def _require_enabled(self) -> Path:
        if self._disabled or self._db_path is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return self._db_path

    def _now(self) -> float:
        return _now_clock(self._clock)

    def _initialize(self) -> None:
        path = self._require_enabled()
        safe_makedirs(path.parent)
        conn = _connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_device (
                    device_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
        path.chmod(0o600)

    def is_revoked(self, device_id: str) -> bool:
        """Unknown devices and explicitly revoked devices are not trusted."""
        if self._disabled:
            return True
        path = self._require_enabled()
        if not isinstance(device_id, str) or not device_id:
            return True
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT state FROM sync_device WHERE device_id = ?", (device_id,)
            ).fetchone()
            if row is None:
                return True
            return row["state"] != "enrolled"
        finally:
            conn.close()

    def is_trusted(self, device_id: str) -> bool:
        """Only explicitly enrolled devices are trusted."""
        if self._disabled:
            return False
        path = self._require_enabled()
        if not isinstance(device_id, str) or not device_id:
            return False
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT state FROM sync_device WHERE device_id = ?", (device_id,)
            ).fetchone()
            return row is not None and row["state"] == "enrolled"
        finally:
            conn.close()

    def enroll(self, device_id: str) -> bool:
        """Atomic transition: unknown -> enrolled only."""
        path = self._require_enabled()
        if not isinstance(device_id, str) or not device_id:
            return False
        conn = _connect(path)
        try:
            try:
                conn.execute(
                    "INSERT INTO sync_device (device_id, state, updated_at) VALUES (?, ?, ?)",
                    (device_id, "enrolled", self._now()),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                conn.rollback()
                return False
        finally:
            conn.close()

    def reenroll(self, device_id: str) -> bool:
        """Atomic transition: revoked -> enrolled."""
        path = self._require_enabled()
        if not isinstance(device_id, str) or not device_id:
            return False
        conn = _connect(path)
        try:
            cur = conn.execute(
                "UPDATE sync_device SET state = ?, updated_at = ? WHERE device_id = ? AND state = ?",
                ("enrolled", self._now(), device_id, "revoked"),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def revoke(self, device_id: str) -> bool:
        """Atomic transition: enrolled -> revoked."""
        path = self._require_enabled()
        if not isinstance(device_id, str) or not device_id:
            return False
        conn = _connect(path)
        try:
            cur = conn.execute(
                "UPDATE sync_device SET state = ?, updated_at = ? WHERE device_id = ? AND state = ?",
                ("revoked", self._now(), device_id, "enrolled"),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def __repr__(self) -> str:
        return "SqliteDeviceRegistry()"


def _descriptor_to_dict(descriptor: EncryptedObjectDescriptor) -> dict:
    return {
        "object_id": descriptor.object_id,
        "ciphertext_digest": descriptor.ciphertext_digest,
        "size_bytes": descriptor.size_bytes,
        "version": descriptor.version,
        "updated_at": descriptor.updated_at,
        "is_tombstone": descriptor.is_tombstone,
        "authority_state": descriptor.authority_state,
    }


def _descriptor_from_dict(data: dict) -> EncryptedObjectDescriptor:
    return EncryptedObjectDescriptor(
        object_id=data["object_id"],
        ciphertext_digest=data["ciphertext_digest"],
        size_bytes=data["size_bytes"],
        version=data["version"],
        updated_at=data["updated_at"],
        is_tombstone=data["is_tombstone"],
        authority_state=data["authority_state"],
    )


def _plan_to_dict(plan: SyncPlan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "created_at": plan.created_at,
        "uploads": [_descriptor_to_dict(d) for d in plan.objects_to_upload],
        "downloads": [_descriptor_to_dict(d) for d in plan.objects_to_download],
        "conflicts": [
            {
                "object_id": c.object_id,
                "reason": c.reason,
                "local": _descriptor_to_dict(c.local_descriptor),
                "remote": _descriptor_to_dict(c.remote_descriptor),
            }
            for c in plan.conflicts
        ],
    }


def _plan_from_dict(data: dict) -> SyncPlan:
    return SyncPlan(
        plan_id=data["plan_id"],
        objects_to_upload=tuple(_descriptor_from_dict(d) for d in data["uploads"]),
        objects_to_download=tuple(_descriptor_from_dict(d) for d in data["downloads"]),
        conflicts=tuple(
            SyncConflict(
                object_id=c["object_id"],
                reason=c["reason"],
                local_descriptor=_descriptor_from_dict(c["local"]),
                remote_descriptor=_descriptor_from_dict(c["remote"]),
            )
            for c in data["conflicts"]
        ),
        created_at=data["created_at"],
    )


class SqliteTransactionRegistry(TransactionRegistry):
    """Durable staged transaction registry backed by SQLite.

    Insert-once semantics: a transaction record may only be created once.  The
    stored plan is the complete structural plan needed to recompute digests.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        clock: Optional[Clock] = None,
    ) -> None:
        self._db_path = db_path
        self._disabled = db_path is None
        self._clock = clock
        if not self._disabled:
            self._initialize()

    def _require_enabled(self) -> Path:
        if self._disabled or self._db_path is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return self._db_path

    def _now(self) -> float:
        return _now_clock(self._clock)

    def _initialize(self) -> None:
        path = self._require_enabled()
        safe_makedirs(path.parent)
        conn = _connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_transaction (
                    transaction_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    proposal_json TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
        path.chmod(0o600)

    def get(self, transaction_id: str) -> Optional[EncryptedSyncTransactionRecord]:
        path = self._require_enabled()
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT * FROM sync_transaction WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_record(row)
        finally:
            conn.close()

    def put(self, record: EncryptedSyncTransactionRecord) -> bool:
        """Insert a transaction record, or transition it through a valid state.

        Allowed transitions:
          - missing -> PLANNED/STAGED
          - PLANNED -> STAGED
          - STAGED -> COMMITTED
          - STAGED -> ROLLED_BACK
        Terminal states (COMMITTED/ROLLED_BACK) cannot be reactivated or
        overwritten, and the immutable owner/device/proposal correlation cannot
        change.
        """
        path = self._require_enabled()
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT device_id, state, proposal_json FROM sync_transaction WHERE transaction_id = ?",
                (record.transaction_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO sync_transaction (transaction_id, device_id, state, created_at, proposal_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.transaction_id,
                        record.device_id,
                        record.state.value,
                        record.created_at,
                        json.dumps(self._record_to_dict(record)),
                    ),
                )
                conn.commit()
                return True

            old_state = EncryptedSyncTransactionState(row["state"])
            # Immutable correlation must match.
            if row["device_id"] != record.device_id:
                conn.rollback()
                return False
            stored = json.loads(row["proposal_json"])
            new = self._record_to_dict(record)
            if stored["proposal"] != new["proposal"] or stored["transaction_id"] != new["transaction_id"]:
                conn.rollback()
                return False

            # Idempotent re-put of the same state and proposal is allowed.
            if record.state == old_state:
                conn.rollback()
                return True

            # Valid forward transitions only.
            allowed = {
                EncryptedSyncTransactionState.PLANNED: {EncryptedSyncTransactionState.STAGED},
                EncryptedSyncTransactionState.STAGED: {
                    EncryptedSyncTransactionState.COMMITTED,
                    EncryptedSyncTransactionState.ROLLED_BACK,
                },
            }
            next_states = allowed.get(old_state, set())
            if record.state not in next_states:
                conn.rollback()
                return False

            cur = conn.execute(
                """
                UPDATE sync_transaction
                SET state = ?, created_at = ?, proposal_json = ?
                WHERE transaction_id = ? AND state = ?
                """,
                (
                    record.state.value,
                    record.created_at,
                    json.dumps(self._record_to_dict(record)),
                    record.transaction_id,
                    old_state.value,
                ),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False
            conn.commit()
            return True
        finally:
            conn.close()

    _SCHEMA_VERSION = 1
    _MAX_ID_LENGTH = 256
    _MAX_DIGEST_LENGTH = 128
    _MAX_LIST_SIZE = 10_000
    _SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")

    @classmethod
    def _record_to_dict(cls, record: EncryptedSyncTransactionRecord) -> dict:
        return {
            "schema_version": cls._SCHEMA_VERSION,
            "transaction_id": record.transaction_id,
            "device_id": record.device_id,
            "state": record.state.value,
            "created_at": record.created_at,
            "proposal": {
                "transaction_id": record.proposal.transaction_id,
                "commit_digest": record.proposal.commit_digest,
                "rollback_digest": record.proposal.rollback_digest,
                "plan": _plan_to_dict(record.proposal.plan),
            },
        }

    @classmethod
    def _validate_primitive(cls, value: object, name: str, expected_type: type) -> None:
        if expected_type is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, f"invalid_{name}")
            if not math.isfinite(value):
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, f"invalid_{name}")
            return
        if expected_type is str:
            if not isinstance(value, str):
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, f"invalid_{name}")
            return
        if expected_type is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, f"invalid_{name}")
            return
        if not isinstance(value, expected_type):
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, f"invalid_{name}")

    @classmethod
    def _validate_descriptor(cls, data: dict) -> None:
        if not isinstance(data, dict):
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "invalid_descriptor")
        for key, expected in (
            ("object_id", str),
            ("ciphertext_digest", str),
            ("size_bytes", int),
            ("version", int),
            ("updated_at", float),
            ("is_tombstone", bool),
            ("authority_state", str),
        ):
            cls._validate_primitive(data.get(key), key, expected)
        cls._validate_safe_id(data["object_id"])
        cls._validate_safe_id(data["ciphertext_digest"])
        if data["size_bytes"] < 0 or data["version"] < 0:
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "invalid_descriptor")

    @classmethod
    def _validate_safe_id(cls, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "invalid_id")
        if len(value) > cls._MAX_ID_LENGTH:
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "id_too_long")
        if not cls._SAFE_ID_RE.fullmatch(value):
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "invalid_id_chars")

    @classmethod
    def _validate_digest(cls, value: str) -> None:
        cls._validate_safe_id(value)
        if len(value) > cls._MAX_DIGEST_LENGTH:
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "digest_too_long")

    @classmethod
    def _row_to_record(cls, row: sqlite3.Row) -> EncryptedSyncTransactionRecord:
        try:
            data = json.loads(row["proposal_json"])
        except Exception as exc:
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "corrupt_transaction_record") from exc

        if not isinstance(data, dict):
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "invalid_transaction_record")

        # Exact allowed top-level keys.
        allowed_keys = {"schema_version", "transaction_id", "device_id", "state", "created_at", "proposal"}
        if set(data.keys()) != allowed_keys:
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "unexpected_transaction_keys")

        try:
            schema_version = data["schema_version"]
            if schema_version != cls._SCHEMA_VERSION:
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "schema_version_mismatch")

            cls._validate_primitive(data["transaction_id"], "transaction_id", str)
            cls._validate_primitive(data["device_id"], "device_id", str)
            cls._validate_primitive(data["state"], "state", str)
            cls._validate_primitive(data["created_at"], "created_at", float)

            transaction_id = data["transaction_id"]
            device_id = data["device_id"]
            state_value = data["state"]
            created_at = data["created_at"]

            # Row-level correlation with persisted JSON.
            if transaction_id != row["transaction_id"]:
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "transaction_id_mismatch")
            if device_id != row["device_id"]:
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "device_id_mismatch")
            if state_value != row["state"]:
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "state_mismatch")

            cls._validate_safe_id(transaction_id)
            cls._validate_safe_id(device_id)
            state = EncryptedSyncTransactionState(state_value)

            proposal_data = data["proposal"]
            if not isinstance(proposal_data, dict):
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "invalid_proposal")
            for key in ("transaction_id", "commit_digest", "rollback_digest", "plan"):
                if key not in proposal_data:
                    raise AdapterException(AdapterReason.INVALID_CONFIGURATION, f"missing_proposal_{key}")
            cls._validate_safe_id(proposal_data["transaction_id"])
            cls._validate_digest(proposal_data["commit_digest"])
            cls._validate_digest(proposal_data["rollback_digest"])
            if proposal_data["transaction_id"] != transaction_id:
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "proposal_transaction_id_mismatch")

            plan_data = proposal_data["plan"]
            if not isinstance(plan_data, dict):
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "invalid_plan")
            for key in ("plan_id", "created_at", "uploads", "downloads", "conflicts"):
                if key not in plan_data:
                    raise AdapterException(AdapterReason.INVALID_CONFIGURATION, f"missing_plan_{key}")
            cls._validate_safe_id(plan_data["plan_id"])
            cls._validate_primitive(plan_data["created_at"], "plan_created_at", float)
            for name in ("uploads", "downloads"):
                lst = plan_data[name]
                if not isinstance(lst, list):
                    raise AdapterException(AdapterReason.INVALID_CONFIGURATION, f"invalid_{name}")
                if len(lst) > cls._MAX_LIST_SIZE:
                    raise AdapterException(AdapterReason.INVALID_CONFIGURATION, f"{name}_too_large")
                for item in lst:
                    cls._validate_descriptor(item)
            conflicts = plan_data["conflicts"]
            if not isinstance(conflicts, list):
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "invalid_conflicts")
            if len(conflicts) > cls._MAX_LIST_SIZE:
                raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "conflicts_too_large")

            proposal = EncryptedSyncTransactionProposal(
                transaction_id=proposal_data["transaction_id"],
                plan=_plan_from_dict(proposal_data["plan"]),
                commit_digest=proposal_data["commit_digest"],
                rollback_digest=proposal_data["rollback_digest"],
            )
        except AdapterException:
            raise
        except Exception as exc:
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, "invalid_transaction_record") from exc

        return EncryptedSyncTransactionRecord(
            transaction_id=transaction_id,
            proposal=proposal,
            device_id=device_id,
            state=state,
            created_at=float(created_at),
        )

    def __repr__(self) -> str:
        return "SqliteTransactionRegistry()"


class SqliteEncryptedSyncStorage(EncryptedSyncStorageInterface):
    """Durable atomic ciphertext object storage backed by SQLite.

    Transaction lifecycle:
      PLANNED -> STAGED -> COMMITTED
                        -> ROLLED_BACK

    State changes are compare-and-swap.  Terminal states (committed,
    rolled_back) cannot reactivate.  Actual ciphertext objects are stored in
    a separate table keyed by digest.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        clock: Optional[Clock] = None,
    ) -> None:
        self._db_path = db_path
        self._disabled = db_path is None
        self._clock = clock
        if not self._disabled:
            self._initialize()

    def _require_enabled(self) -> Path:
        if self._disabled or self._db_path is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return self._db_path

    def _now(self) -> float:
        return _now_clock(self._clock)

    def _initialize(self) -> None:
        path = self._require_enabled()
        safe_makedirs(path.parent)
        conn = _connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_storage (
                    transaction_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_ciphertext (
                    digest TEXT PRIMARY KEY,
                    ciphertext BLOB NOT NULL,
                    stored_at REAL NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
        path.chmod(0o600)

    def _get_state(self, conn: sqlite3.Connection, transaction_id: str) -> Optional[str]:
        row = conn.execute(
            "SELECT state FROM sync_storage WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        return None if row is None else row["state"]

    def stage(self, transaction_id: str, plan: SyncPlan) -> Tuple[bool, str]:
        path = self._require_enabled()
        if not isinstance(transaction_id, str) or not transaction_id:
            return False, "invalid_transaction_id"
        conn = _connect(path)
        try:
            try:
                conn.execute(
                    "INSERT INTO sync_storage (transaction_id, state, plan_json, created_at) VALUES (?, ?, ?, ?)",
                    (transaction_id, "staged", json.dumps(_plan_to_dict(plan)), self._now()),
                )
                conn.commit()
                return True, "ok"
            except sqlite3.IntegrityError:
                conn.rollback()
                return False, "already_staged"
        finally:
            conn.close()

    def commit(self, transaction_id: str) -> Tuple[bool, str]:
        path = self._require_enabled()
        conn = _connect(path)
        try:
            state = self._get_state(conn, transaction_id)
            if state is None:
                return False, "not_found"
            if state == "committed":
                return False, "already_committed"
            if state == "rolled_back":
                return False, "already_rolled_back"
            if state != "staged":
                return False, "not_staged"
            cur = conn.execute(
                "UPDATE sync_storage SET state = ? WHERE transaction_id = ? AND state = ?",
                ("committed", transaction_id, "staged"),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False, "concurrent_transition"
            conn.commit()
            return True, "ok"
        finally:
            conn.close()

    def rollback(self, transaction_id: str) -> Tuple[bool, str]:
        path = self._require_enabled()
        conn = _connect(path)
        try:
            state = self._get_state(conn, transaction_id)
            if state is None:
                return False, "not_found"
            if state == "rolled_back":
                return False, "already_rolled_back"
            if state not in ("staged", "committed"):
                return False, "cannot_rollback"
            cur = conn.execute(
                "UPDATE sync_storage SET state = ? WHERE transaction_id = ? AND state IN (?, ?)",
                ("rolled_back", transaction_id, "staged", "committed"),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False, "concurrent_transition"
            conn.commit()
            return True, "ok"
        finally:
            conn.close()

    def store_ciphertext(self, digest: str, ciphertext: bytes) -> Tuple[bool, str]:
        """Persist ciphertext bytes keyed by digest."""
        path = self._require_enabled()
        if not isinstance(digest, str) or not digest:
            return False, "invalid_digest"
        if not isinstance(ciphertext, bytes) or not ciphertext:
            return False, "invalid_ciphertext"
        conn = _connect(path)
        try:
            try:
                conn.execute(
                    "INSERT INTO sync_ciphertext (digest, ciphertext, stored_at) VALUES (?, ?, ?)",
                    (digest, ciphertext, self._now()),
                )
                conn.commit()
                return True, "ok"
            except sqlite3.IntegrityError:
                conn.rollback()
                return True, "already_exists"
        finally:
            conn.close()

    def load_ciphertext(self, digest: str) -> Optional[bytes]:
        """Load ciphertext bytes by digest, returning None if missing."""
        path = self._require_enabled()
        if not isinstance(digest, str) or not digest:
            return None
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT ciphertext FROM sync_ciphertext WHERE digest = ?", (digest,)
            ).fetchone()
            return row["ciphertext"] if row else None
        finally:
            conn.close()

    def __repr__(self) -> str:
        return "SqliteEncryptedSyncStorage()"
