"""Durable, SQLite-backed remote worker security state.

Implements durable equivalents of the injected interfaces from
``core.phase6_adapters.remote_worker``:
- NonceStoreInterface
- durable job/result correlation
- worker trust/revocation
- worker quarantine
- cancellation acknowledgements

No remote network transport is implemented here.

Time semantics:
- All durable timestamps use the injected epoch/wall clock (default time.time).
- Monotonic time is used only for in-process elapsed timing.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import unicodedata
from enum import StrEnum
from pathlib import Path
from typing import Callable, Optional, Tuple

from core.phase6_adapters.remote_worker import (
    CancellationAcknowledgement,
    NonceStoreInterface,
)
from core.phase6_adapters.contracts import AdapterException, AdapterReason
from core.phase6_agent.contracts import RemoteWorkerAuthorityEnvelope
from core.phase6_live.base import safe_makedirs


Clock = Callable[[], float]

# Reasonable identifier constraints.
_MAX_IDENTIFIER_LENGTH = 512


def _is_control_or_format_byte(value: int) -> bool:
    if value < 32 or value == 127:
        return True
    # C1 controls and format characters (Cf).
    if 0x80 <= value <= 0x9F:
        return True
    return False


def _valid_identifier(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if len(value) > _MAX_IDENTIFIER_LENGTH:
        return False
    # Rejects NUL/control/format/bidi characters.
    if "\x00" in value:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    for ch in value:
        if unicodedata.bidirectional(ch) in {"RLE", "LRE", "RLO", "LRO", "PDF", "RLM", "LRM", "ALM", "LRI", "RLI", "FSI", "PDI"}:
            return False
        if unicodedata.category(ch) == "Cf":
            return False
    return True


def _valid_epoch(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value):
        return False
    if value < 0 or value > 1_000_000_000_000:
        return False
    return True


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


class SqliteRemoteWorkerNonceStore(NonceStoreInterface):
    """Durable nonce store for remote workers backed by SQLite."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        max_age_seconds: float = 86400.0,
        clock: Optional[Clock] = None,
    ) -> None:
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)):
            raise ValueError("invalid_max_age_seconds")
        age = float(max_age_seconds)
        if not math.isfinite(age) or age <= 0.0 or age > 31_536_000.0:
            raise ValueError("invalid_max_age_seconds")
        self._db_path = db_path
        self._disabled = db_path is None
        self._max_age = age
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
                CREATE TABLE IF NOT EXISTS rw_nonce (
                    nonce TEXT PRIMARY KEY,
                    consumed_at REAL NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
        path.chmod(0o600)

    def is_consumed(self, nonce: str) -> bool:
        if self._disabled:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        path = self._require_enabled()
        self._prune_old(path)
        conn = _connect(path)
        try:
            row = conn.execute("SELECT 1 FROM rw_nonce WHERE nonce = ?", (nonce,)).fetchone()
            return row is not None
        finally:
            conn.close()

    def consume(self, nonce: str) -> bool:
        path = self._require_enabled()
        self._prune_old(path)
        conn = _connect(path)
        try:
            try:
                conn.execute(
                    "INSERT INTO rw_nonce (nonce, consumed_at) VALUES (?, ?)",
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
            conn.execute("DELETE FROM rw_nonce WHERE consumed_at < ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()

    def __repr__(self) -> str:
        return "SqliteRemoteWorkerNonceStore()"


class SqliteRemoteWorkerState:
    """Durable remote worker trust, quarantine, job, and cancellation state."""

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
                CREATE TABLE IF NOT EXISTS rw_worker (
                    worker_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rw_job (
                    envelope_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    targets TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at_epoch REAL NOT NULL,
                    response_count INTEGER NOT NULL DEFAULT 0,
                    cancelled_at REAL,
                    max_responses INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rw_cancellation (
                    envelope_id TEXT PRIMARY KEY,
                    acknowledged_at REAL NOT NULL,
                    acknowledgement_id TEXT NOT NULL,
                    FOREIGN KEY (envelope_id) REFERENCES rw_job(envelope_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rw_result (
                    envelope_id TEXT PRIMARY KEY,
                    result_bytes BLOB NOT NULL,
                    observed_at_epoch REAL NOT NULL,
                    content_type TEXT NOT NULL DEFAULT 'text/plain',
                    FOREIGN KEY (envelope_id) REFERENCES rw_job(envelope_id)
                )
                """
            )
            # Fail closed on incompatible legacy mono expiry schema.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(rw_job)")}
            if "expires_at_mono" in cols:
                raise AdapterException(
                    AdapterReason.INVALID_INPUT,
                    "incompatible_expiry_schema",
                )
            if "expires_at_epoch" not in cols and cols:
                raise AdapterException(
                    AdapterReason.INVALID_INPUT,
                    "incompatible_expiry_schema",
                )
            conn.commit()
        finally:
            conn.close()
        path.chmod(0o600)

    # Worker trust/revocation ------------------------------------------------

    def is_revoked(self, worker_id: str) -> bool:
        """Explicitly revoked workers and unknown workers are not trusted."""
        if self._disabled:
            return True
        path = self._require_enabled()
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT state FROM rw_worker WHERE worker_id = ?", (worker_id,)
            ).fetchone()
            if row is None:
                return True
            return row["state"] == "revoked"
        finally:
            conn.close()

    def is_trusted(self, worker_id: str) -> bool:
        if self._disabled:
            return False
        path = self._require_enabled()
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT state FROM rw_worker WHERE worker_id = ?", (worker_id,)
            ).fetchone()
            return row is not None and row["state"] == "enrolled"
        finally:
            conn.close()

    def enroll_worker(self, worker_id: str) -> bool:
        """Atomic transition: unknown -> enrolled only."""
        path = self._require_enabled()
        if not _valid_identifier(worker_id):
            return False
        conn = _connect(path)
        try:
            try:
                conn.execute(
                    "INSERT INTO rw_worker (worker_id, state, updated_at) VALUES (?, ?, ?)",
                    (worker_id, "enrolled", self._now()),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                conn.rollback()
                return False
        finally:
            conn.close()

    def reenroll_worker(self, worker_id: str) -> bool:
        """Atomic transition: revoked/quarantined -> enrolled."""
        path = self._require_enabled()
        if not _valid_identifier(worker_id):
            return False
        conn = _connect(path)
        try:
            cur = conn.execute(
                "UPDATE rw_worker SET state = ?, updated_at = ? WHERE worker_id = ? AND state IN (?, ?)",
                ("enrolled", self._now(), worker_id, "revoked", "quarantined"),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def revoke_worker(self, worker_id: str) -> bool:
        """Atomic transition: enrolled -> revoked."""
        path = self._require_enabled()
        if not _valid_identifier(worker_id):
            return False
        conn = _connect(path)
        try:
            cur = conn.execute(
                "UPDATE rw_worker SET state = ?, updated_at = ? WHERE worker_id = ? AND state = ?",
                ("revoked", self._now(), worker_id, "enrolled"),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    # Quarantine -----------------------------------------------------------

    def is_quarantined(self, worker_id: str) -> bool:
        if self._disabled:
            return True
        path = self._require_enabled()
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT state FROM rw_worker WHERE worker_id = ?", (worker_id,)
            ).fetchone()
            return row is not None and row["state"] == "quarantined"
        finally:
            conn.close()

    def quarantine_worker(self, worker_id: str) -> bool:
        """Atomic transition enrolled -> quarantined."""
        path = self._require_enabled()
        if not _valid_identifier(worker_id):
            return False
        conn = _connect(path)
        try:
            cur = conn.execute(
                "UPDATE rw_worker SET state = ?, updated_at = ? WHERE worker_id = ? AND state = ?",
                ("quarantined", self._now(), worker_id, "enrolled"),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def is_enrolled(self, worker_id: str) -> bool:
        """Return True only if the worker is explicitly enrolled and not revoked/quarantined."""
        return self.is_trusted(worker_id)

    # Job/result correlation ----------------------------------------------

    def record_job(
        self,
        envelope: RemoteWorkerAuthorityEnvelope,
        state: str,
        now: float,
        *,
        expires_at_epoch: float,
    ) -> bool:
        """Record a job exactly once. Returns False if the envelope already exists.

        A durable security record requires a valid, future wall-clock expiry.
        Monotonic deadlines are restart-meaningless and are never persisted.
        Existing rows loaded without a valid epoch expiry are treated as
        expired (fail closed).
        """
        path = self._require_enabled()
        if not _valid_identifier(envelope.envelope_id):
            return False
        if not _valid_identifier(envelope.worker_id):
            return False
        if not _valid_identifier(envelope.task_id):
            return False
        if not isinstance(envelope.capability, str) or not envelope.capability:
            return False
        if not isinstance(state, str) or not state:
            return False
        if not _valid_epoch(now):
            return False
        if not _valid_epoch(expires_at_epoch):
            return False
        if expires_at_epoch <= now:
            return False

        conn = _connect(path)
        try:
            try:
                conn.execute(
                """
                INSERT INTO rw_job (envelope_id, worker_id, task_id, capability, targets, state, created_at, expires_at_epoch, max_responses)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.envelope_id,
                    envelope.worker_id,
                    envelope.task_id,
                    envelope.capability,
                    json.dumps(envelope.targets),
                    state,
                    now,
                    float(expires_at_epoch),
                    envelope.max_responses,
                ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                conn.rollback()
                return False
        finally:
            conn.close()

    def is_job_expired(self, envelope_id: str, now: Optional[float] = None) -> bool:
        """A job with missing or non-finite epoch expiry is always treated as expired."""
        if now is None:
            now = self._now()
        path = self._require_enabled()
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT expires_at_epoch FROM rw_job WHERE envelope_id = ?", (envelope_id,)
            ).fetchone()
            if row is None:
                return True
            epoch = row["expires_at_epoch"]
            if not isinstance(epoch, (int, float)) or isinstance(epoch, bool) or not math.isfinite(epoch):
                return True
            return now >= float(epoch)
        finally:
            conn.close()

    def get_job_state(self, envelope_id: str) -> Optional[str]:
        path = self._require_enabled()
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT state FROM rw_job WHERE envelope_id = ?", (envelope_id,)
            ).fetchone()
            return row["state"] if row else None
        finally:
            conn.close()

    # Lifecycle state machine ------------------------------------------------
    class _JobState(StrEnum):
        RECEIVED = "received"
        VALIDATED = "validated"
        SUBMITTED = "submitted"
        ACTIVE = "active"
        CANCEL_REQUESTED = "cancel_requested"
        CANCELLED = "cancelled"
        COMPLETED_EVIDENCE = "completed_evidence"
        EXPIRED = "expired"
        REVOKED = "revoked"
        QUARANTINED = "quarantined"
        FAILED = "failed"

    _TERMINAL_STATES = frozenset({_JobState.CANCELLED, _JobState.COMPLETED_EVIDENCE, _JobState.EXPIRED, _JobState.REVOKED, _JobState.QUARANTINED, _JobState.FAILED})

    _VALID_TRANSITIONS: dict[_JobState, frozenset[_JobState]] = {
        _JobState.RECEIVED: frozenset({_JobState.VALIDATED, _JobState.EXPIRED, _JobState.REVOKED, _JobState.QUARANTINED, _JobState.FAILED, _JobState.CANCEL_REQUESTED}),
        _JobState.VALIDATED: frozenset({_JobState.SUBMITTED, _JobState.EXPIRED, _JobState.REVOKED, _JobState.QUARANTINED, _JobState.FAILED, _JobState.CANCEL_REQUESTED}),
        _JobState.SUBMITTED: frozenset({_JobState.ACTIVE, _JobState.EXPIRED, _JobState.REVOKED, _JobState.QUARANTINED, _JobState.FAILED, _JobState.CANCEL_REQUESTED}),
        _JobState.ACTIVE: frozenset({_JobState.COMPLETED_EVIDENCE, _JobState.CANCEL_REQUESTED, _JobState.EXPIRED, _JobState.REVOKED, _JobState.QUARANTINED, _JobState.FAILED}),
        _JobState.CANCEL_REQUESTED: frozenset({_JobState.CANCELLED, _JobState.EXPIRED, _JobState.REVOKED, _JobState.QUARANTINED, _JobState.FAILED}),
    }

    # Results ---------------------------------------------------------------

    _HARD_MAX_RESULT_BYTES = 16_777_216  # 16 MiB hard ceiling
    _ALLOWED_CONTENT_TYPES = frozenset({"text/plain", "application/json"})
    _MAX_FUTURE_SKEW_SECONDS = 300.0

    def _validate_result_text(self, text: str) -> bool:
        # Permit documented whitespace (tab/LF/CR); reject other controls.
        for ch in text:
            if (ord(ch) < 32 and ch not in "\t\n\r") or ord(ch) == 127:
                return False
        # Reject Unicode bidi/format characters.
        for ch in text:
            if unicodedata.bidirectional(ch) in {"RLE", "LRE", "RLO", "LRO", "PDF", "RLM", "LRM", "ALM", "LRI", "RLI", "FSI", "PDI"}:
                return False
            if unicodedata.category(ch) == "Cf":
                return False
        return True

    def _validate_result_json(self, text: str) -> bool:
        def _no_dup_keys(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError("duplicate_key")
                out[key] = value
            return out

        def _reject_constant(name: str):
            raise ValueError(name)

        try:
            data = json.loads(
                text,
                object_pairs_hook=_no_dup_keys,
                parse_constant=_reject_constant,
            )
        except Exception:
            return False

        def _check(obj: object, depth: int) -> bool:
            if depth > 5:
                return False
            if isinstance(obj, dict):
                if len(obj) > 1024:
                    return False
                for key, value in obj.items():
                    if not isinstance(key, str) or not self._validate_result_text(key):
                        return False
                    if not _check(value, depth + 1):
                        return False
                return True
            if isinstance(obj, list):
                if len(obj) > 1024:
                    return False
                return all(_check(item, depth + 1) for item in obj)
            if isinstance(obj, str):
                return self._validate_result_text(obj)
            if isinstance(obj, bool) or obj is None or isinstance(obj, (int, float)):
                if isinstance(obj, float) and not math.isfinite(obj):
                    return False
                return True
            return False

        return _check(data, 0)

    def record_result(
        self,
        envelope_id: str,
        result_bytes: bytes,
        observed_at_epoch: float,
        *,
        max_result_bytes: int = 1_048_576,
        content_type: str = "text/plain",
    ) -> bool:
        """Record remote result evidence under strict correlation and bounds.

        The envelope must exist, the worker must be enrolled, the job must be
        active, not expired, not cancelled, and the response budget must not be
        exhausted. Only bounded UTF-8 text or JSON is accepted; arbitrary
        binary data is rejected.
        """
        path = self._require_enabled()
        if not _valid_identifier(envelope_id):
            return False
        if not isinstance(result_bytes, bytes):
            return False
        if not _valid_epoch(observed_at_epoch):
            return False
        if not isinstance(content_type, str) or "\x00" in content_type or len(content_type) > 256:
            return False
        if content_type not in self._ALLOWED_CONTENT_TYPES:
            return False

        # Hard cap on caller-supplied max_result_bytes.
        if isinstance(max_result_bytes, bool) or not isinstance(max_result_bytes, int) or max_result_bytes <= 0:
            return False
        # Reject caller values above the public hard maximum (do not clamp).
        if max_result_bytes > self._HARD_MAX_RESULT_BYTES:
            return False
        if len(result_bytes) > max_result_bytes:
            return False

        # Only accept bounded UTF-8 text or JSON.
        try:
            text = result_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return False
        if not self._validate_result_text(text):
            return False
        if content_type == "application/json" and not self._validate_result_json(text):
            return False

        now = self._now()
        conn = _connect(path)
        try:
            # Use immediate transaction for serializable checks under concurrency.
            conn.execute("BEGIN IMMEDIATE")
            try:
                job = conn.execute(
                    """
                    SELECT j.state, j.created_at, j.expires_at_epoch, j.cancelled_at, j.response_count, j.max_responses, j.worker_id, w.state AS worker_state
                    FROM rw_job j
                    LEFT JOIN rw_worker w ON j.worker_id = w.worker_id
                    WHERE j.envelope_id = ?
                    """,
                    (envelope_id,),
                ).fetchone()
                if job is None:
                    conn.rollback()
                    return False
                if job["worker_state"] != "enrolled":
                    conn.rollback()
                    return False
                if job["state"] not in ("active", "submitted"):
                    conn.rollback()
                    return False
                if job["cancelled_at"] is not None:
                    conn.rollback()
                    return False

                created_at = job["created_at"]
                if not isinstance(created_at, (int, float)) or isinstance(created_at, bool) or not math.isfinite(created_at):
                    conn.rollback()
                    return False
                if observed_at_epoch < created_at:
                    conn.rollback()
                    return False

                epoch = job["expires_at_epoch"]
                if not isinstance(epoch, (int, float)) or isinstance(epoch, bool) or not math.isfinite(epoch):
                    conn.rollback()
                    return False
                if now >= float(epoch):
                    conn.rollback()
                    return False
                if observed_at_epoch > float(epoch):
                    conn.rollback()
                    return False
                if observed_at_epoch > now + self._MAX_FUTURE_SKEW_SECONDS:
                    conn.rollback()
                    return False

                if job["response_count"] >= job["max_responses"]:
                    conn.rollback()
                    return False

                inc = conn.execute(
                    """
                    UPDATE rw_job
                    SET response_count = response_count + 1
                    WHERE envelope_id = ? AND response_count < max_responses
                    """,
                    (envelope_id,),
                )
                if inc.rowcount != 1:
                    conn.rollback()
                    return False
                try:
                    conn.execute(
                        """
                        INSERT INTO rw_result (envelope_id, result_bytes, observed_at_epoch, content_type)
                        VALUES (?, ?, ?, ?)
                        """,
                        (envelope_id, result_bytes, observed_at_epoch, content_type),
                    )
                except sqlite3.IntegrityError:
                    # Result for this envelope already recorded (insert-once).
                    conn.rollback()
                    return False
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

    def set_job_state(self, envelope_id: str, state: str) -> bool:
        """Update job state, enforcing the bounded lifecycle."""
        path = self._require_enabled()
        try:
            new_state = self._JobState(state)
        except ValueError:
            return False
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT state FROM rw_job WHERE envelope_id = ?", (envelope_id,)
            ).fetchone()
            if row is None:
                return False
            try:
                old_state = self._JobState(row["state"])
            except ValueError:
                return False
            if old_state in self._TERMINAL_STATES:
                return False
            allowed = self._VALID_TRANSITIONS.get(old_state, frozenset())
            if new_state not in allowed:
                return False
            # Compare-and-swap update to prevent race-induced invalid transitions.
            cur = conn.execute(
                "UPDATE rw_job SET state = ? WHERE envelope_id = ? AND state = ?",
                (state, envelope_id, old_state.value),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False
            conn.commit()
            return True
        finally:
            conn.close()

    def increment_response(self, envelope_id: str, max_responses: int = 0) -> bool:
        """Atomically increment response count if under the bound."""
        path = self._require_enabled()
        conn = _connect(path)
        try:
            if max_responses <= 0:
                row = conn.execute(
                    "SELECT max_responses FROM rw_job WHERE envelope_id = ?", (envelope_id,)
                ).fetchone()
                if row is None:
                    return False
                max_responses = row["max_responses"]
            cur = conn.execute(
                """
                UPDATE rw_job
                SET response_count = response_count + 1
                WHERE envelope_id = ? AND response_count < ?
                """,
                (envelope_id, max_responses),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def get_response_count(self, envelope_id: str) -> int:
        path = self._require_enabled()
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT response_count FROM rw_job WHERE envelope_id = ?", (envelope_id,)
            ).fetchone()
            return row["response_count"] if row else 0
        finally:
            conn.close()

    # Cancellation ----------------------------------------------------------

    def record_cancellation(self, envelope_id: str, ack: CancellationAcknowledgement) -> bool:
        """Record a cancellation acknowledgement exactly once.

        A cancellation acknowledgement is committed only when the job state is
        atomically transitioned to CANCELLED. Duplicate acknowledgements are
        idempotent only if every immutable field (acknowledgement_id and
        timestamp) matches exactly.
        """
        path = self._require_enabled()
        if not _valid_identifier(envelope_id):
            return False
        if not _valid_identifier(ack.acknowledgement_id):
            return False
        if not _valid_epoch(ack.acknowledged_at):
            return False
        conn = _connect(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT acknowledgement_id, acknowledged_at FROM rw_cancellation WHERE envelope_id = ?",
                    (envelope_id,),
                ).fetchone()
                if existing is not None:
                    # Idempotent only if ack fields match AND job is terminal cancelled
                    # with the same canonical cancellation timestamp.
                    if (
                        existing["acknowledgement_id"] == ack.acknowledgement_id
                        and float(existing["acknowledged_at"]) == ack.acknowledged_at
                    ):
                        job = conn.execute(
                            "SELECT state, cancelled_at FROM rw_job WHERE envelope_id = ?",
                            (envelope_id,),
                        ).fetchone()
                        if (
                            job is not None
                            and job["state"] == "cancelled"
                            and job["cancelled_at"] is not None
                            and float(job["cancelled_at"]) == ack.acknowledged_at
                        ):
                            conn.commit()
                            return True
                    conn.rollback()
                    return False

                # Insert acknowledgement and CAS job transition in the same transaction.
                conn.execute(
                    """
                    INSERT INTO rw_cancellation (envelope_id, acknowledged_at, acknowledgement_id)
                    VALUES (?, ?, ?)
                    """,
                    (envelope_id, ack.acknowledged_at, ack.acknowledgement_id),
                )
                cur = conn.execute(
                    """
                    UPDATE rw_job
                    SET state = ?, cancelled_at = ?
                    WHERE envelope_id = ? AND state IN (?, ?, ?)
                    """,
                    ("cancelled", ack.acknowledged_at, envelope_id, "active", "submitted", "cancel_requested"),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return False
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

    def is_cancelled(self, envelope_id: str) -> bool:
        path = self._require_enabled()
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT cancelled_at FROM rw_job WHERE envelope_id = ?", (envelope_id,)
            ).fetchone()
            return row is not None and row["cancelled_at"] is not None
        finally:
            conn.close()

    def __repr__(self) -> str:
        return "SqliteRemoteWorkerState()"
