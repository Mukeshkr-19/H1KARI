"""Bounded SQLite store for Phase3 scheduled jobs (stdlib only).

This module persists ``ScheduledJob`` contract values only. It stores no raw
prompts, queries, email bodies, calendar content, provider responses, or
secrets. All operations are actor/session scoped; cross-scope reads and
mutations are rejected. Compare-and-swap transitions use the existing
transition table and an ``updated_at`` revision guard.

Before every mutation the current actor/session-scoped job is loaded and
the proposed change is validated through the contract's ``with_*`` helpers;
the validated value is then used in the CAS update. Invalid proposals
raise before any write, so the stored row is never partially overwritten.

No worker, timer, thread, sleep, notification, subprocess, network,
AppleScript, recurrence engine, or overdue execution is present.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from core.jobs.contracts import (
    JobState,
    ScheduledJob,
    TransitionError,
    can_transition,
    validate_fingerprint,
)
from core.jobs.quiet_hours import QuietHours

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    action TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    state TEXT NOT NULL,
    next_run_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    qh_timezone TEXT,
    qh_start_minute INTEGER,
    qh_end_minute INTEGER,
    last_delivery_fingerprint TEXT,
    PRIMARY KEY (job_id, actor_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_scope_updated
    ON scheduled_jobs(actor_id, session_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_state_updated
    ON scheduled_jobs(state, updated_at);
"""

_SELECT_COLUMNS = (
    "job_id, actor_id, session_id, action, proposal_id, state, "
    "next_run_at, created_at, updated_at, attempt_count, max_attempts, "
    "qh_timezone, qh_start_minute, qh_end_minute, last_delivery_fingerprint"
)

_MIN_LIMIT = 1
_MAX_LIMIT = 200


class _ClosingConnection(sqlite3.Connection):
    """Close the connection deterministically when the context exits."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _require_scope(actor_id: str, session_id: str) -> None:
    if not isinstance(actor_id, str) or not actor_id:
        raise ValueError("actor_id scope is required")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id scope is required")


def _require_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    if limit < _MIN_LIMIT or limit > _MAX_LIMIT:
        raise ValueError(f"limit must be in {_MIN_LIMIT}..{_MAX_LIMIT}")
    return limit


def _serialize_dt(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.isoformat()


def _deserialize_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("stored timestamp is missing timezone")
    return dt


def _row_to_job(row: sqlite3.Row) -> ScheduledJob:
    tz = row["qh_timezone"]
    start = row["qh_start_minute"]
    end = row["qh_end_minute"]
    present = [v is not None for v in (tz, start, end)]
    if any(present) and not all(present):
        raise ValueError("quiet-hours columns are partially populated")
    quiet_hours = None
    if tz is not None and start is not None and end is not None:
        quiet_hours = QuietHours(
            timezone_name=tz, start_minute=int(start), end_minute=int(end)
        )
    return ScheduledJob(
        job_id=row["job_id"],
        actor_id=row["actor_id"],
        session_id=row["session_id"],
        action=row["action"],
        proposal_id=row["proposal_id"],
        state=JobState(row["state"]),
        next_run_at=_deserialize_dt(row["next_run_at"]),
        created_at=_deserialize_dt(row["created_at"]),
        updated_at=_deserialize_dt(row["updated_at"]),
        attempt_count=int(row["attempt_count"] or 0),
        max_attempts=int(row["max_attempts"] or 1),
        quiet_hours=quiet_hours,
        last_delivery_fingerprint=row["last_delivery_fingerprint"],
    )


class ScheduledJobStore:
    """Actor/session-scoped SQLite store for scheduled jobs."""

    def __init__(self, db_path: Path, *, create_dirs: bool = True) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        if create_dirs:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.db_path.parent, 0o700)
        if self.db_path.exists():
            os.chmod(self.db_path, 0o600)
        self._init_db()
        os.chmod(self.db_path, 0o600)

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

    def _load_scoped(
        self, conn: sqlite3.Connection, job_id: str, actor_id: str, session_id: str
    ) -> Optional[ScheduledJob]:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM scheduled_jobs "
            "WHERE job_id = ? AND actor_id = ? AND session_id = ?",
            (job_id, actor_id, session_id),
        ).fetchone()
        return _row_to_job(row) if row else None

    def add(self, job: ScheduledJob) -> ScheduledJob:
        """Insert a new scheduled job under its actor/session scope."""
        _require_scope(job.actor_id, job.session_id)
        qh = job.quiet_hours
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO scheduled_jobs (
                    job_id, actor_id, session_id, action, proposal_id, state,
                    next_run_at, created_at, updated_at, attempt_count,
                    max_attempts, qh_timezone, qh_start_minute, qh_end_minute,
                    last_delivery_fingerprint
                ) VALUES ({','.join(['?'] * 15)})
                """,
                (
                    job.job_id,
                    job.actor_id,
                    job.session_id,
                    job.action,
                    job.proposal_id,
                    job.state.value,
                    _serialize_dt(job.next_run_at),
                    _serialize_dt(job.created_at),
                    _serialize_dt(job.updated_at),
                    job.attempt_count,
                    job.max_attempts,
                    qh.timezone_name if qh else None,
                    qh.start_minute if qh else None,
                    qh.end_minute if qh else None,
                    job.last_delivery_fingerprint,
                ),
            )
            conn.commit()
        return job

    def remove_if_unmodified(
        self,
        job_id: str,
        *,
        actor_id: str,
        session_id: str,
        expected_updated_at: datetime,
        expected_state: JobState,
    ) -> bool:
        """Actor/session-scoped CAS delete for create/claim compensation.

        Deletes exactly one row only when ``job_id``, actor, session, state, and
        ``updated_at`` all match. A concurrent mutation or cross-session lookup
        is a no-op and returns ``False``. Never deletes an unrelated job.
        """
        _require_scope(actor_id, session_id)
        if not isinstance(expected_state, JobState):
            raise ValueError("expected_state must be a JobState")
        if (
            not isinstance(expected_updated_at, datetime)
            or expected_updated_at.tzinfo is None
        ):
            raise ValueError("expected_updated_at must be timezone-aware")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM scheduled_jobs
                WHERE job_id = ? AND actor_id = ? AND session_id = ?
                  AND state = ? AND updated_at = ?
                """,
                (
                    job_id,
                    actor_id,
                    session_id,
                    expected_state.value,
                    _serialize_dt(expected_updated_at),
                ),
            )
            deleted = cursor.rowcount == 1
            if deleted:
                conn.commit()
            else:
                conn.rollback()
            return deleted

    def get(
        self, job_id: str, *, actor_id: str, session_id: str
    ) -> Optional[ScheduledJob]:
        """Return the job only when the scope matches exactly."""
        _require_scope(actor_id, session_id)
        with self._connect() as conn:
            return self._load_scoped(conn, job_id, actor_id, session_id)

    def list(
        self,
        *,
        actor_id: str,
        session_id: str,
        limit: int = 50,
    ) -> list[ScheduledJob]:
        """List jobs for the exact actor/session scope, newest first."""
        _require_scope(actor_id, session_id)
        limit = _require_limit(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM scheduled_jobs "
                "WHERE actor_id = ? AND session_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (actor_id, session_id, limit),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def transition(
        self,
        job_id: str,
        *,
        expected_state: JobState,
        new_state: JobState,
        expected_updated_at: datetime,
        updated_at: datetime,
        actor_id: str,
        session_id: str,
    ) -> Optional[ScheduledJob]:
        """Compare-and-swap the job state within its scope.

        The current actor/session-scoped job is loaded first and the change is
        validated through ``ScheduledJob.with_state`` before any write. A
        same-state transition is idempotent: it returns the loaded job
        unchanged without modifying any column. A concurrent mutation (scope
        mismatch, wrong current state, or stale ``updated_at``) returns
        ``None`` and leaves the stored row untouched.
        """
        _require_scope(actor_id, session_id)
        if not can_transition(expected_state, new_state):
            raise TransitionError(
                f"invalid job transition: {expected_state.value} -> {new_state.value}"
            )
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if (
            not isinstance(expected_updated_at, datetime)
            or expected_updated_at.tzinfo is None
        ):
            raise ValueError("expected_updated_at must be timezone-aware")
        with self._connect() as conn:
            current = self._load_scoped(conn, job_id, actor_id, session_id)
            if current is None:
                return None
            if (
                current.state is not expected_state
                or current.updated_at != expected_updated_at
            ):
                return None
            # Idempotent same-state transition: no write, preserve revision.
            if expected_state is new_state:
                return current
            validated = current.with_state(new_state, updated_at=updated_at)
            cursor = conn.execute(
                """
                UPDATE scheduled_jobs
                SET state = ?, updated_at = ?
                WHERE job_id = ? AND actor_id = ? AND session_id = ?
                  AND state = ? AND updated_at = ?
                """,
                (
                    validated.state.value,
                    _serialize_dt(validated.updated_at),
                    job_id,
                    actor_id,
                    session_id,
                    expected_state.value,
                    _serialize_dt(expected_updated_at),
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
            return self._load_scoped(conn, job_id, actor_id, session_id)

    def update_next_run(
        self,
        job_id: str,
        *,
        next_run_at: datetime,
        expected_updated_at: datetime,
        updated_at: datetime,
        actor_id: str,
        session_id: str,
    ) -> Optional[ScheduledJob]:
        """Compare-and-swap ``next_run_at`` within scope.

        The current job is loaded and the change validated through
        ``ScheduledJob.with_next_run`` before any write, so a regressed
        ``updated_at`` or a ``next_run_at`` before ``created_at`` raises
        before the stored row is touched.
        """
        _require_scope(actor_id, session_id)
        if not isinstance(next_run_at, datetime) or next_run_at.tzinfo is None:
            raise ValueError("next_run_at must be timezone-aware")
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if (
            not isinstance(expected_updated_at, datetime)
            or expected_updated_at.tzinfo is None
        ):
            raise ValueError("expected_updated_at must be timezone-aware")
        with self._connect() as conn:
            current = self._load_scoped(conn, job_id, actor_id, session_id)
            if current is None:
                return None
            if current.updated_at != expected_updated_at:
                return None
            validated = current.with_next_run(
                next_run_at, updated_at=updated_at
            )
            cursor = conn.execute(
                """
                UPDATE scheduled_jobs
                SET next_run_at = ?, updated_at = ?
                WHERE job_id = ? AND actor_id = ? AND session_id = ?
                  AND updated_at = ?
                """,
                (
                    _serialize_dt(validated.next_run_at),
                    _serialize_dt(validated.updated_at),
                    job_id,
                    actor_id,
                    session_id,
                    _serialize_dt(expected_updated_at),
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
            return self._load_scoped(conn, job_id, actor_id, session_id)

    def update_delivery_fingerprint(
        self,
        job_id: str,
        *,
        fingerprint: Optional[str],
        expected_updated_at: datetime,
        updated_at: datetime,
        actor_id: str,
        session_id: str,
    ) -> Optional[ScheduledJob]:
        """Compare-and-swap the delivery fingerprint within scope.

        The current job is loaded and the change validated through
        ``ScheduledJob.with_delivery_fingerprint`` (which validates the
        fingerprint via the public ``validate_fingerprint`` helper) before
        any write.
        """
        _require_scope(actor_id, session_id)
        if fingerprint is not None:
            validate_fingerprint(fingerprint)
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if (
            not isinstance(expected_updated_at, datetime)
            or expected_updated_at.tzinfo is None
        ):
            raise ValueError("expected_updated_at must be timezone-aware")
        with self._connect() as conn:
            current = self._load_scoped(conn, job_id, actor_id, session_id)
            if current is None:
                return None
            if current.updated_at != expected_updated_at:
                return None
            validated = current.with_delivery_fingerprint(
                fingerprint, updated_at=updated_at
            )
            cursor = conn.execute(
                """
                UPDATE scheduled_jobs
                SET last_delivery_fingerprint = ?, updated_at = ?
                WHERE job_id = ? AND actor_id = ? AND session_id = ?
                  AND updated_at = ?
                """,
                (
                    validated.last_delivery_fingerprint,
                    _serialize_dt(validated.updated_at),
                    job_id,
                    actor_id,
                    session_id,
                    _serialize_dt(expected_updated_at),
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
            return self._load_scoped(conn, job_id, actor_id, session_id)

    def update_attempt(
        self,
        job_id: str,
        *,
        expected_updated_at: datetime,
        updated_at: datetime,
        actor_id: str,
        session_id: str,
    ) -> Optional[ScheduledJob]:
        """Compare-and-swap ``attempt_count`` (increment by 1) within scope.

        The current job is loaded and the change validated through
        ``ScheduledJob.with_attempt`` (which enforces the retry budget) before
        any write. Raises ``RetryBudgetExhausted`` when no further attempt is
        permitted.
        """
        _require_scope(actor_id, session_id)
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if (
            not isinstance(expected_updated_at, datetime)
            or expected_updated_at.tzinfo is None
        ):
            raise ValueError("expected_updated_at must be timezone-aware")
        with self._connect() as conn:
            current = self._load_scoped(conn, job_id, actor_id, session_id)
            if current is None:
                return None
            if current.updated_at != expected_updated_at:
                return None
            validated = current.with_attempt(updated_at=updated_at)
            cursor = conn.execute(
                """
                UPDATE scheduled_jobs
                SET attempt_count = ?, updated_at = ?
                WHERE job_id = ? AND actor_id = ? AND session_id = ?
                  AND updated_at = ?
                """,
                (
                    validated.attempt_count,
                    _serialize_dt(validated.updated_at),
                    job_id,
                    actor_id,
                    session_id,
                    _serialize_dt(expected_updated_at),
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
            return self._load_scoped(conn, job_id, actor_id, session_id)

    def pause(
        self,
        job_id: str,
        *,
        expected_updated_at: datetime,
        updated_at: datetime,
        actor_id: str,
        session_id: str,
    ) -> Optional[ScheduledJob]:
        """Scheduled -> paused (uses the existing transition table)."""
        return self.transition(
            job_id,
            expected_state=JobState.SCHEDULED,
            new_state=JobState.PAUSED,
            expected_updated_at=expected_updated_at,
            updated_at=updated_at,
            actor_id=actor_id,
            session_id=session_id,
        )

    def resume(
        self,
        job_id: str,
        *,
        expected_updated_at: datetime,
        updated_at: datetime,
        actor_id: str,
        session_id: str,
    ) -> Optional[ScheduledJob]:
        """Paused -> scheduled (uses the existing transition table)."""
        return self.transition(
            job_id,
            expected_state=JobState.PAUSED,
            new_state=JobState.SCHEDULED,
            expected_updated_at=expected_updated_at,
            updated_at=updated_at,
            actor_id=actor_id,
            session_id=session_id,
        )

    def cancel(
        self,
        job_id: str,
        *,
        expected_updated_at: datetime,
        updated_at: datetime,
        actor_id: str,
        session_id: str,
    ) -> Optional[ScheduledJob]:
        """Any allowed state -> cancelled (uses the existing transition table)."""
        current = self.get(job_id, actor_id=actor_id, session_id=session_id)
        if current is None:
            return None
        return self.transition(
            job_id,
            expected_state=current.state,
            new_state=JobState.CANCELLED,
            expected_updated_at=expected_updated_at,
            updated_at=updated_at,
            actor_id=actor_id,
            session_id=session_id,
        )

    def claim_due(
        self,
        *,
        now: datetime,
        updated_at: datetime,
        limit: int = 1,
    ) -> tuple[ScheduledJob, ...]:
        """Atomically claim up to ``limit`` due ``SCHEDULED`` jobs.

        A job is "due" when its state is ``SCHEDULED`` and ``next_run_at <= now``.
        Each candidate is transitioned to ``RUNNING`` via the existing CAS
        ``transition`` path so concurrent runners cannot both observe the same
        ``SCHEDULED`` row and execute it twice. CAS conflicts are silently
        skipped (another runner won the race). Returns the successfully claimed
        jobs in deterministic ``updated_at`` order.

        ``limit`` is bounded to ``1..64``. ``now`` and ``updated_at`` must be
        timezone-aware. No cross-scope data is read or returned.
        """
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if limit < 1 or limit > 64:
            raise ValueError("limit must be in 1..64")

        claimed: list[ScheduledJob] = []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM scheduled_jobs "
                "WHERE state = ? AND next_run_at <= ? "
                "ORDER BY next_run_at ASC, updated_at ASC LIMIT ?",
                (
                    JobState.SCHEDULED.value,
                    _serialize_dt(now),
                    limit,
                ),
            ).fetchall()
            for row in rows:
                candidate = _row_to_job(row)
                cursor = conn.execute(
                    """
                    UPDATE scheduled_jobs
                    SET state = ?, updated_at = ?
                    WHERE job_id = ? AND actor_id = ? AND session_id = ?
                      AND state = ? AND updated_at = ?
                    """,
                    (
                        JobState.RUNNING.value,
                        _serialize_dt(updated_at),
                        candidate.job_id,
                        candidate.actor_id,
                        candidate.session_id,
                        JobState.SCHEDULED.value,
                        _serialize_dt(candidate.updated_at),
                    ),
                )
                if cursor.rowcount != 1:
                    # Concurrent runner already claimed this row; skip.
                    continue
                claimed.append(
                    _row_to_job(
                        conn.execute(
                            f"SELECT {_SELECT_COLUMNS} FROM scheduled_jobs "
                            "WHERE job_id = ? AND actor_id = ? AND session_id = ?",
                            (
                                candidate.job_id,
                                candidate.actor_id,
                                candidate.session_id,
                            ),
                        ).fetchone()
                    )
                )
            conn.commit()
        return tuple(claimed)

    def list_state(
        self, state: JobState, *, limit: int = 64
    ) -> tuple[ScheduledJob, ...]:
        """Return bounded jobs in one structural state for startup recovery."""
        if not isinstance(state, JobState):
            raise ValueError("state must be a JobState")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
            raise ValueError("limit must be in 1..64")
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM scheduled_jobs "
                "WHERE state = ? ORDER BY updated_at ASC LIMIT ?",
                (state.value, limit),
            ).fetchall()
        return tuple(_row_to_job(row) for row in rows)
