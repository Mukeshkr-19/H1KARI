"""Bounded SQLite store for Phase 4 task handoffs (stdlib only).

This module persists only handoff lifecycle metadata. It stores no task
content beyond the snapshot digest, no authority data, no approval IDs, and no
execution tickets. All operations are actor/session scoped; cross-scope reads
and mutations are rejected. Compare-and-swap transitions use a revision guard.

No worker, timer, thread, sleep, notification, subprocess, network,
AppleScript, or external execution is present.
"""

from __future__ import annotations

import math
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from core.handoff.contracts import (
    HandoffRecord,
    HandoffState,
    _HANDOFF_ID_RE,
    _HANDOFF_TTL_SECONDS,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    request_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_single_active_handoff
    ON handoffs(actor_id, session_id, task_id)
    WHERE state = 'offered';

CREATE INDEX IF NOT EXISTS idx_handoffs_expires_state
    ON handoffs(expires_at, state)
    WHERE state = 'offered';
"""

_SELECT_COLUMNS = (
    "handoff_id, actor_id, session_id, task_id, summary, snapshot_digest, "
    "state, created_at, expires_at, request_id, revision"
)


class _ClosingConnection(sqlite3.Connection):
    """Close the connection deterministically when the context exits."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class HandoffStoreError(ValueError):
    """Base exception for handoff store failures."""


class InvalidHandoffIdError(HandoffStoreError):
    """Raised when the handoff-ID factory produces an invalid identifier."""


class DuplicateHandoffError(HandoffStoreError):
    """Raised when a nonterminal handoff already exists for the tuple."""


class HandoffStore:
    """Actor/session-scoped SQLite store for bounded task handoffs."""

    def __init__(
        self,
        db_path: Path,
        *,
        clock: Callable[[], float],
        handoff_id_factory: Callable[[], str],
        create_dirs: bool = True,
    ) -> None:
        self._db_path = Path(db_path).expanduser().resolve()
        self.clock = clock
        self.handoff_id_factory = handoff_id_factory
        if create_dirs:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self._db_path.parent, 0o700)
        if self._db_path.exists():
            os.chmod(self._db_path, 0o600)
        self._init_db()
        os.chmod(self._db_path, 0o600)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def __repr__(self) -> str:
        return "HandoffStore(<redacted>)"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._db_path), factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> HandoffRecord:
        return HandoffRecord(
            handoff_id=row["handoff_id"],
            actor_id=row["actor_id"],
            session_id=row["session_id"],
            task_id=row["task_id"],
            summary=row["summary"],
            snapshot_digest=row["snapshot_digest"],
            state=HandoffState(row["state"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            request_id=row["request_id"],
            revision=int(row["revision"]),
        )

    def _load_scoped(
        self,
        conn: sqlite3.Connection,
        handoff_id: str,
        actor_id: str,
        session_id: str,
    ) -> Optional[HandoffRecord]:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM handoffs "
            "WHERE handoff_id = ? AND actor_id = ? AND session_id = ?",
            (handoff_id, actor_id, session_id),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def _load_by_id(
        self,
        conn: sqlite3.Connection,
        handoff_id: str,
    ) -> Optional[HandoffRecord]:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM handoffs WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def create_offer(
        self,
        *,
        actor_id: str,
        session_id: str,
        task_id: str,
        summary: str,
        snapshot_digest: str,
        request_id: str,
    ) -> HandoffRecord:
        """Insert a new offered handoff under its actor/session scope."""
        if not isinstance(actor_id, str) or not actor_id:
            raise ValueError("actor_id scope is required")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id scope is required")
        try:
            now = self.clock()
            handoff_id = self.handoff_id_factory()
        except Exception:
            raise HandoffStoreError("handoff creation unavailable") from None
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(float(now))
        ):
            raise HandoffStoreError("handoff creation unavailable")
        if not isinstance(handoff_id, str) or not handoff_id:
            raise ValueError("handoff_id factory produced an invalid identifier")

        if not _HANDOFF_ID_RE.fullmatch(handoff_id):
            raise InvalidHandoffIdError(
                "handoff_id factory produced an invalid identifier"
            )

        with self._connect() as conn:
            try:
                conn.execute(
                    f"""
                    INSERT INTO handoffs (
                        handoff_id, actor_id, session_id, task_id, summary,
                        snapshot_digest, state, created_at, expires_at,
                        request_id, revision
                    ) VALUES ({','.join(['?'] * 11)})
                    """,
                    (
                        handoff_id,
                        actor_id,
                        session_id,
                        task_id,
                        summary,
                        snapshot_digest,
                        HandoffState.OFFERED.value,
                        now,
                        now + _HANDOFF_TTL_SECONDS,
                        request_id,
                        1,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise DuplicateHandoffError(
                    "nonterminal handoff already exists for task"
                ) from exc
            record = self._load_scoped(conn, handoff_id, actor_id, session_id)
            if record is None:
                raise HandoffStoreError("handoff creation unavailable")
            return record

    def get_scoped(
        self,
        handoff_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> Optional[HandoffRecord]:
        """Return the handoff only when the scope matches exactly."""
        if not isinstance(actor_id, str) or not actor_id:
            raise ValueError("actor_id scope is required")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id scope is required")
        with self._connect() as conn:
            return self._load_scoped(conn, handoff_id, actor_id, session_id)

    def get_for_owner(self, handoff_id: str) -> Optional[HandoffRecord]:
        """Return a record after the service authorizes the local owner."""
        with self._connect() as conn:
            return self._load_by_id(conn, handoff_id)

    def is_accepted_for_session(self, handoff_id: str, session_id: str) -> bool:
        """Return only whether one exact origin session owns an accepted handoff."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM handoffs WHERE handoff_id = ? AND session_id = ? "
                    "AND state = ? LIMIT 1",
                    (handoff_id, session_id, HandoffState.ACCEPTED.value),
                ).fetchone()
            return row is not None
        except (sqlite3.Error, TypeError, ValueError):
            return False

    def _transition(
        self,
        handoff_id: str,
        *,
        actor_id: str,
        session_id: str,
        expected_state: HandoffState,
        new_state: HandoffState,
    ) -> Optional[HandoffRecord]:
        """Compare-and-swap a handoff to a new state within its scope."""
        if not isinstance(actor_id, str) or not actor_id:
            raise ValueError("actor_id scope is required")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id scope is required")
        if not isinstance(expected_state, HandoffState):
            raise ValueError("expected_state must be a HandoffState")
        if not isinstance(new_state, HandoffState):
            raise ValueError("new_state must be a HandoffState")

        with self._connect() as conn:
            current = self._load_scoped(conn, handoff_id, actor_id, session_id)
            if current is None:
                return None
            if current.state is not expected_state:
                return None

            # Same-state transitions are idempotent.
            if expected_state is new_state:
                return current

            next_revision = current.revision + 1
            cursor = conn.execute(
                """
                UPDATE handoffs
                SET state = ?, revision = ?
                WHERE handoff_id = ? AND actor_id = ? AND session_id = ?
                  AND state = ? AND revision = ?
                """,
                (
                    new_state.value,
                    next_revision,
                    handoff_id,
                    actor_id,
                    session_id,
                    expected_state.value,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
            return self._load_scoped(conn, handoff_id, actor_id, session_id)

    def accept(
        self,
        handoff_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> Optional[HandoffRecord]:
        """Transition an offered handoff to accepted within its scope."""
        return self._transition(
            handoff_id,
            actor_id=actor_id,
            session_id=session_id,
            expected_state=HandoffState.OFFERED,
            new_state=HandoffState.ACCEPTED,
        )

    def reject(
        self,
        handoff_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> Optional[HandoffRecord]:
        """Transition an offered handoff to rejected within its scope."""
        with self._connect() as conn:
            current = self._load_scoped(conn, handoff_id, actor_id, session_id)
            if current is not None and current.state is HandoffState.REJECTED:
                return current
        return self._transition(
            handoff_id,
            actor_id=actor_id,
            session_id=session_id,
            expected_state=HandoffState.OFFERED,
            new_state=HandoffState.REJECTED,
        )

    def cancel(
        self,
        handoff_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> Optional[HandoffRecord]:
        """Transition an offered handoff to cancelled within its scope."""
        with self._connect() as conn:
            current = self._load_scoped(conn, handoff_id, actor_id, session_id)
            if current is not None and current.state is HandoffState.CANCELLED:
                return current
        return self._transition(
            handoff_id,
            actor_id=actor_id,
            session_id=session_id,
            expected_state=HandoffState.OFFERED,
            new_state=HandoffState.CANCELLED,
        )

    def status(
        self,
        handoff_id: str,
        *,
        actor_id: str,
        session_id: str,
    ) -> Optional[HandoffRecord]:
        """Return the current handoff record only within its exact scope."""
        return self.get_scoped(
            handoff_id,
            actor_id=actor_id,
            session_id=session_id,
        )

    def expire(
        self,
        handoff_id: str,
        *,
        actor_id: str,
        session_id: str,
        now: float,
    ) -> Optional[HandoffRecord]:
        """Transition an offered handoff to expired within its scope.

        Returns the record if it was expired, None if not found or not offered.
        """
        if not isinstance(actor_id, str) or not actor_id:
            raise ValueError("actor_id scope is required")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id scope is required")
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise ValueError("now must be numeric")

        with self._connect() as conn:
            current = self._load_scoped(conn, handoff_id, actor_id, session_id)
            if current is None:
                return None
            if current.state is not HandoffState.OFFERED or not current.is_expired(now):
                return None
            cursor = conn.execute(
                """
                UPDATE handoffs
                SET state = ?, revision = ?
                WHERE handoff_id = ? AND actor_id = ? AND session_id = ?
                  AND state = ? AND revision = ?
                """,
                (
                    HandoffState.EXPIRED.value,
                    current.revision + 1,
                    handoff_id,
                    actor_id,
                    session_id,
                    HandoffState.OFFERED.value,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
            return self._load_scoped(conn, handoff_id, actor_id, session_id)

    def _transition_for_owner(
        self,
        handoff_id: str,
        *,
        expected_state: HandoffState,
        new_state: HandoffState,
    ) -> Optional[HandoffRecord]:
        """CAS transition after the service authorizes the local owner."""
        with self._connect() as conn:
            current = self._load_by_id(conn, handoff_id)
            if current is None:
                return None
            if current.state is new_state:
                return current
            if current.state is not expected_state:
                return None
            cursor = conn.execute(
                """
                UPDATE handoffs
                SET state = ?, revision = revision + 1
                WHERE handoff_id = ? AND state = ? AND revision = ?
                """,
                (
                    new_state.value,
                    handoff_id,
                    expected_state.value,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
            return self._load_by_id(conn, handoff_id)

    def accept_for_owner(self, handoff_id: str) -> Optional[HandoffRecord]:
        return self._transition_for_owner(
            handoff_id,
            expected_state=HandoffState.OFFERED,
            new_state=HandoffState.ACCEPTED,
        )

    def reject_for_owner(self, handoff_id: str) -> Optional[HandoffRecord]:
        return self._transition_for_owner(
            handoff_id,
            expected_state=HandoffState.OFFERED,
            new_state=HandoffState.REJECTED,
        )

    def cancel_for_owner(self, handoff_id: str) -> Optional[HandoffRecord]:
        return self._transition_for_owner(
            handoff_id,
            expected_state=HandoffState.OFFERED,
            new_state=HandoffState.CANCELLED,
        )

    def expire_for_owner(
        self,
        handoff_id: str,
        *,
        now: float,
    ) -> Optional[HandoffRecord]:
        current = self.get_for_owner(handoff_id)
        if current is None or not current.is_expired(now):
            return None
        return self._transition_for_owner(
            handoff_id,
            expected_state=HandoffState.OFFERED,
            new_state=HandoffState.EXPIRED,
        )

    def expire_due(self, *, now: float) -> int:
        """Transition all offered handoffs past their TTL to expired.

        Returns the number of records transitioned.
        """
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise ValueError("now must be numeric")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE handoffs
                SET state = ?, revision = revision + 1
                WHERE state = ? AND expires_at <= ?
                """,
                (HandoffState.EXPIRED.value, HandoffState.OFFERED.value, now),
            )
            conn.commit()
            return cursor.rowcount
