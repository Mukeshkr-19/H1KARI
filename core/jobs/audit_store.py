"""Append-only SQLite store for Phase 3 scheduled-job audit events.

This module persists only the bounded ``AuditEvent`` fields. It stores no
actor/session/proposal identifiers, approvals, targets, payloads, user content,
provider details, exception text, notification bodies, or secrets.

Appends are atomic and reject duplicate event IDs without overwriting. Reads
are bounded by an explicit limit (1..256) and scoped to an exact canonical job
ID, ordered deterministically by ``occurred_at`` then ``event_id``. The public
API raises only ``AuditStoreError``; no database path, SQL, identifier, or
SQLite exception text is ever surfaced.

No network, subprocess, notifications, browser, provider, timers, threads,
logging, or external execution is present.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from core.jobs.audit import (
    AuditEvent,
    AuditReasonCode,
    AuditStoreError,
    AuditTransitionError,
    AuditValidationError,
    VALID_JOB_STATES,
    VALID_REASON_CODES,
    validate_transition,
)
from core.jobs.contracts import JobState

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
MAX_IDENTIFIER_LENGTH = 128
_MIN_LIMIT = 1
_MAX_LIMIT = 256

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_job_audit (
    event_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    action TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    PRIMARY KEY (event_id),
    CHECK (reason_code IN ({reason_codes})),
    CHECK (new_state IN ({states})),
    CHECK (previous_state IS NULL OR previous_state IN ({states}))
);
CREATE INDEX IF NOT EXISTS idx_scheduled_job_audit_job_occurred
    ON scheduled_job_audit(job_id, occurred_at, event_id);
""".format(
    reason_codes=",".join(f"'{c}'" for c in VALID_REASON_CODES),
    states=",".join(f"'{s}'" for s in VALID_JOB_STATES),
)


class _ClosingConnection(sqlite3.Connection):
    """Close the connection deterministically when the context exits."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _validate_job_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise AuditStoreError("job id is required")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise AuditStoreError("job id is too long")
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise AuditStoreError("job id is malformed")
    return value


def _require_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise AuditStoreError("limit must be an integer")
    if limit < _MIN_LIMIT or limit > _MAX_LIMIT:
        raise AuditStoreError(f"limit must be in {_MIN_LIMIT}..{_MAX_LIMIT}")
    return limit


def _serialize_dt(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AuditStoreError("timestamp must be timezone-aware")
    return value.isoformat()


def _deserialize_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise AuditStoreError("stored timestamp is missing timezone")
    return dt


def _row_to_event(row: sqlite3.Row) -> AuditEvent:
    try:
        previous = (
            JobState(row["previous_state"]) if row["previous_state"] else None
        )
        new_state = JobState(row["new_state"])
        reason = AuditReasonCode(row["reason_code"])
    except (ValueError, KeyError) as exc:
        raise AuditStoreError("corrupt audit record") from exc
    try:
        return AuditEvent(
            event_id=row["event_id"],
            job_id=row["job_id"],
            action=row["action"],
            previous_state=previous,
            new_state=new_state,
            occurred_at=_deserialize_dt(row["occurred_at"]),
            reason_code=reason,
        )
    except (AuditValidationError, AuditTransitionError) as exc:
        raise AuditStoreError("corrupt audit record") from exc


class ScheduledJobAuditStore:
    """Append-only, privacy-bounded SQLite store for audit events."""

    def __init__(self, db_path: Path, *, create_dirs: bool = True) -> None:
        try:
            self.db_path = Path(db_path).expanduser().resolve()
            if create_dirs:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self.db_path.parent, 0o700)
            if self.db_path.exists():
                os.chmod(self.db_path, 0o600)
            self._init_db()
            self._apply_permissions()
        except AuditStoreError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AuditStoreError("audit store unavailable") from None

    def _apply_permissions(self) -> None:
        try:
            os.chmod(self.db_path, 0o600)
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = self.db_path.with_name(self.db_path.name + suffix)
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
        except OSError:
            raise AuditStoreError("audit store unavailable") from None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def append(self, event: AuditEvent) -> None:
        """Append a validated audit event atomically.

        The event and its transition are revalidated before any write. Impossible
        transitions are never stored. Duplicate event IDs are rejected without
        overwriting. Raises only ``AuditStoreError``.
        """
        if not isinstance(event, AuditEvent):
            raise AuditStoreError("audit event is required")
        try:
            validate_transition(event.previous_state, event.new_state)
        except (AuditTransitionError, AuditValidationError) as exc:
            raise AuditStoreError("audit event transition rejected") from exc

        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO scheduled_job_audit (
                        event_id, job_id, action, previous_state, new_state,
                        occurred_at, reason_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.job_id,
                        event.action,
                        event.previous_state.value if event.previous_state else None,
                        event.new_state.value,
                        _serialize_dt(event.occurred_at),
                        event.reason_code.value,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                raise AuditStoreError("duplicate audit event id") from None
            except sqlite3.Error:
                conn.rollback()
                raise AuditStoreError("audit store write failed") from None
        self._apply_permissions()

    def read(self, job_id: str, *, limit: int = 50) -> list[AuditEvent]:
        """Return bounded, deterministic audit events for an exact job ID.

        Events are ordered by ``occurred_at`` then ``event_id`` (ascending).
        ``limit`` must be an integer in 1..256. Raises only ``AuditStoreError``.
        """
        _validate_job_id(job_id)
        limit = _require_limit(limit)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT event_id, job_id, action, previous_state, new_state, "
                    "occurred_at, reason_code FROM scheduled_job_audit "
                    "WHERE job_id = ? "
                    "ORDER BY occurred_at ASC, event_id ASC LIMIT ?",
                    (job_id, limit),
                ).fetchall()
        except sqlite3.Error:
            raise AuditStoreError("audit store read failed") from None
        try:
            return [_row_to_event(row) for row in rows]
        except AuditStoreError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise AuditStoreError("audit read failed") from exc
