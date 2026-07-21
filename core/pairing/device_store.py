"""SQLite-backed device session store with CAS revisions and restrictive permissions."""

from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from core.pairing.contracts import (
    DEVICE_SESSION_TTL_SECONDS,
    ContractValidationError,
    DeviceDisplayRecord,
    DeviceErrorCode,
    DeviceMutationOutcome,
    DeviceOutcomeStatus,
    DeviceSessionRecord,
    DeviceSessionState,
    IssueDeviceOutcome,
    validate_challenge_id,
    validate_device_id,
    validate_device_label,
)


class DeviceStoreError(Exception):
    """Fixed store error without paths, SQL, or identifiers."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_sessions (
    device_id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    device_label TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    updated_at REAL NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_device_sessions_state_updated
    ON device_sessions(state, updated_at);
"""


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class DeviceSessionStore:
    """Durable device-session store with compare-and-swap mutations."""

    def __init__(
        self,
        db_path: Path,
        *,
        clock: Callable[[], float],
        device_id_factory: Callable[[], str],
        create_dirs: bool = True,
    ) -> None:
        if not isinstance(db_path, Path):
            raise TypeError("db_path must be a pathlib.Path")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(device_id_factory):
            raise TypeError("device_id_factory must be callable")
        self._db_path = db_path.expanduser().resolve()
        self._clock = clock
        self._device_id_factory = device_id_factory
        if create_dirs:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self._db_path.parent, 0o700)
        self._init_db()
        if self._db_path.exists():
            os.chmod(self._db_path, 0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            conn = sqlite3.connect(str(self._db_path), factory=_ClosingConnection)
            conn.row_factory = sqlite3.Row
            yield conn
        except sqlite3.Error:
            raise DeviceStoreError("database connection failed") from None

    def _init_db(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                conn.commit()
        except DeviceStoreError:
            raise
        except sqlite3.Error:
            raise DeviceStoreError("database schema initialization failed") from None

    def _now(self) -> float:
        try:
            value = self._clock()
        except Exception:
            raise DeviceStoreError("clock unavailable") from None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise DeviceStoreError("clock returned an invalid timestamp")
        if not math.isfinite(float(value)):
            raise DeviceStoreError("clock returned an invalid timestamp")
        return float(value)

    def _new_device_id(self) -> str:
        try:
            value = self._device_id_factory()
        except Exception:
            raise DeviceStoreError("device id factory failed") from None
        return validate_device_id(value)

    def _row_to_record(self, row: sqlite3.Row) -> DeviceSessionRecord:
        return DeviceSessionRecord(
            device_id=row["device_id"],
            challenge_id=row["challenge_id"],
            state=DeviceSessionState(row["state"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            updated_at=float(row["updated_at"]),
            device_label=row["device_label"],
        )

    def _load(self, conn: sqlite3.Connection, device_id: str) -> Optional[DeviceSessionRecord]:
        row = conn.execute(
            "SELECT device_id, challenge_id, state, device_label, created_at, expires_at, updated_at "
            "FROM device_sessions WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def _transition(
        self,
        conn: sqlite3.Connection,
        *,
        device_id: str,
        expected_state: DeviceSessionState,
        new_state: DeviceSessionState,
        expected_updated_at: float,
        updated_at: float,
        expires_at: float | None = None,
    ) -> Optional[DeviceSessionRecord]:
        if expected_state is new_state:
            current = self._load(conn, device_id)
            if (
                current is not None
                and current.state is expected_state
                and current.updated_at == expected_updated_at
            ):
                return current
            return None

        if expires_at is None:
            cursor = conn.execute(
                """
                UPDATE device_sessions
                SET state = ?, updated_at = ?
                WHERE device_id = ? AND state = ? AND updated_at = ?
                """,
                (
                    new_state.value,
                    updated_at,
                    device_id,
                    expected_state.value,
                    expected_updated_at,
                ),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE device_sessions
                SET state = ?, updated_at = ?, expires_at = ?
                WHERE device_id = ? AND state = ? AND updated_at = ?
                """,
                (
                    new_state.value,
                    updated_at,
                    expires_at,
                    device_id,
                    expected_state.value,
                    expected_updated_at,
                ),
            )
        if cursor.rowcount != 1:
            conn.rollback()
            return None
        conn.commit()
        return self._load(conn, device_id)

    def issue(
        self,
        *,
        challenge_id: str,
        device_label: str | None = None,
    ) -> IssueDeviceOutcome:
        try:
            challenge_id = validate_challenge_id(challenge_id)
            label = validate_device_label(device_label)
        except ContractValidationError:
            return IssueDeviceOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.INVALID_INPUT,
            )

        try:
            now = self._now()
            device_id = self._new_device_id()
        except (DeviceStoreError, ContractValidationError):
            return IssueDeviceOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.UNAVAILABLE,
            )

        expires_at = now + DEVICE_SESSION_TTL_SECONDS
        try:
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT device_id FROM device_sessions WHERE challenge_id = ?",
                    (challenge_id,),
                ).fetchone()
                if existing is not None:
                    return IssueDeviceOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.UNAVAILABLE,
                    )
                conn.execute(
                    """
                    INSERT INTO device_sessions (
                        device_id, challenge_id, state, device_label,
                        created_at, expires_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        challenge_id,
                        DeviceSessionState.ACTIVE.value,
                        label,
                        now,
                        expires_at,
                        now,
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError:
            return IssueDeviceOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.UNAVAILABLE,
            )
        except (DeviceStoreError, sqlite3.Error):
            return IssueDeviceOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.UNAVAILABLE,
            )

        return IssueDeviceOutcome(
            status=DeviceOutcomeStatus.OK,
            device_id=device_id,
        )

    def get_active(self, device_id: str) -> Optional[DeviceSessionRecord]:
        try:
            device_id = validate_device_id(device_id)
        except ContractValidationError:
            return None

        try:
            now = self._now()
        except DeviceStoreError:
            return None

        try:
            with self._connect() as conn:
                record = self._load(conn, device_id)
                if record is None:
                    return None
                if record.state is not DeviceSessionState.ACTIVE:
                    return None
                if now >= record.expires_at:
                    return None
                return record
        except (DeviceStoreError, sqlite3.Error):
            return None

    def mark_stale(self, device_id: str) -> DeviceMutationOutcome:
        try:
            device_id = validate_device_id(device_id)
        except ContractValidationError:
            return DeviceMutationOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.INVALID_INPUT,
            )

        try:
            now = self._now()
        except DeviceStoreError:
            return DeviceMutationOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.UNAVAILABLE,
            )

        try:
            with self._connect() as conn:
                current = self._load(conn, device_id)
                if current is None:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.NOT_FOUND,
                    )
                if current.state is DeviceSessionState.STALE:
                    return DeviceMutationOutcome(status=DeviceOutcomeStatus.OK)
                if current.state is not DeviceSessionState.ACTIVE:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.UNAVAILABLE,
                    )
                updated = self._transition(
                    conn,
                    device_id=device_id,
                    expected_state=DeviceSessionState.ACTIVE,
                    new_state=DeviceSessionState.STALE,
                    expected_updated_at=current.updated_at,
                    updated_at=now,
                )
                if updated is None:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.UNAVAILABLE,
                    )
        except (DeviceStoreError, sqlite3.Error):
            return DeviceMutationOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.UNAVAILABLE,
            )

        return DeviceMutationOutcome(status=DeviceOutcomeStatus.OK)

    def reconnect(self, device_id: str) -> DeviceMutationOutcome:
        try:
            device_id = validate_device_id(device_id)
        except ContractValidationError:
            return DeviceMutationOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.INVALID_INPUT,
            )

        try:
            now = self._now()
        except DeviceStoreError:
            return DeviceMutationOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.UNAVAILABLE,
            )

        try:
            with self._connect() as conn:
                current = self._load(conn, device_id)
                if current is None:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.NOT_FOUND,
                    )
                if current.state is DeviceSessionState.REVOKED:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.REVOKED,
                    )
                if current.state is DeviceSessionState.EXPIRED:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.EXPIRED,
                    )
                if now >= current.expires_at:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.EXPIRED,
                    )
                if current.state is DeviceSessionState.ACTIVE:
                    return DeviceMutationOutcome(status=DeviceOutcomeStatus.OK)
                if current.state is not DeviceSessionState.STALE:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.UNAVAILABLE,
                    )
                new_expires = now + DEVICE_SESSION_TTL_SECONDS
                updated = self._transition(
                    conn,
                    device_id=device_id,
                    expected_state=DeviceSessionState.STALE,
                    new_state=DeviceSessionState.ACTIVE,
                    expected_updated_at=current.updated_at,
                    updated_at=now,
                    expires_at=new_expires,
                )
                if updated is None:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.UNAVAILABLE,
                    )
        except (DeviceStoreError, sqlite3.Error):
            return DeviceMutationOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.UNAVAILABLE,
            )

        return DeviceMutationOutcome(status=DeviceOutcomeStatus.OK)

    def revoke(self, device_id: str) -> DeviceMutationOutcome:
        try:
            device_id = validate_device_id(device_id)
        except ContractValidationError:
            return DeviceMutationOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.INVALID_INPUT,
            )

        try:
            now = self._now()
        except DeviceStoreError:
            return DeviceMutationOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.UNAVAILABLE,
            )

        try:
            with self._connect() as conn:
                current = self._load(conn, device_id)
                if current is None:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.NOT_FOUND,
                    )
                if current.state is DeviceSessionState.REVOKED:
                    return DeviceMutationOutcome(status=DeviceOutcomeStatus.OK)
                if current.state is DeviceSessionState.EXPIRED:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.EXPIRED,
                    )
                updated = self._transition(
                    conn,
                    device_id=device_id,
                    expected_state=current.state,
                    new_state=DeviceSessionState.REVOKED,
                    expected_updated_at=current.updated_at,
                    updated_at=now,
                )
                if updated is None:
                    return DeviceMutationOutcome(
                        status=DeviceOutcomeStatus.ERROR,
                        error=DeviceErrorCode.UNAVAILABLE,
                    )
        except (DeviceStoreError, sqlite3.Error):
            return DeviceMutationOutcome(
                status=DeviceOutcomeStatus.ERROR,
                error=DeviceErrorCode.UNAVAILABLE,
            )

        return DeviceMutationOutcome(status=DeviceOutcomeStatus.OK)

    def expire_due(self) -> int:
        try:
            now = self._now()
        except DeviceStoreError:
            return 0

        expired = 0
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT device_id, challenge_id, state, device_label,
                           created_at, expires_at, updated_at
                    FROM device_sessions
                    WHERE state IN (?, ?) AND expires_at <= ?
                    """,
                    (
                        DeviceSessionState.ACTIVE.value,
                        DeviceSessionState.STALE.value,
                        now,
                    ),
                ).fetchall()
                for row in rows:
                    current = self._row_to_record(row)
                    updated = self._transition(
                        conn,
                        device_id=current.device_id,
                        expected_state=current.state,
                        new_state=DeviceSessionState.EXPIRED,
                        expected_updated_at=current.updated_at,
                        updated_at=now,
                    )
                    if updated is not None:
                        expired += 1
        except (DeviceStoreError, sqlite3.Error):
            return expired
        return expired

    def list_display(self, *, limit: int = 50) -> list[DeviceDisplayRecord]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if limit < 1 or limit > 200:
            raise ValueError("limit is out of range")

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT state, device_label
                    FROM device_sessions
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except (DeviceStoreError, sqlite3.Error):
            return []

        return [
            DeviceDisplayRecord(
                state=DeviceSessionState(row["state"]),
                device_label=row["device_label"],
            )
            for row in rows
        ]

    def get_record(self, device_id: str) -> Optional[DeviceSessionRecord]:
        """Return the stored record regardless of active eligibility."""
        try:
            device_id = validate_device_id(device_id)
        except ContractValidationError:
            return None
        try:
            with self._connect() as conn:
                return self._load(conn, device_id)
        except (DeviceStoreError, sqlite3.Error):
            return None
