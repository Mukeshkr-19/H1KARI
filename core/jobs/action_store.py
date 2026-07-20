"""Private retained adapter inputs for scheduled Phase 3 actions.

The store keeps one exact, already-validated ``AdapterInput`` behind an
actor/session-scoped scheduled-job key.  It is deliberately separate from the
public scheduled-job record so payload content never appears in job listings,
audit events, protocol messages, or representations.

The implementation uses only the Python standard library and local SQLite.  It
does not log, execute actions, inspect providers, or perform network or
subprocess work.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from core.productivity.action_inputs import (
    BrowserResearchAdapterInput,
    CalendarDraftAdapterInput,
    CalendarReadAdapterInput,
    EmailDraftAdapterInput,
    ReminderCreateAdapterInput,
)
from core.productivity.contracts import ProductivityAction
from core.productivity.execution import AdapterInput


_CANONICAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_SCOPE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_ROWS_PER_SESSION = 64
_MAX_RETENTION = timedelta(days=366)
_MIN_YEAR = 2000
_MAX_YEAR = 2100
_ERROR_MESSAGE = "scheduled action store operation failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_action_inputs (
    job_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (job_id, actor_id, session_id),
    CHECK (revision >= 1)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_scheduled_action_inputs_scope_expiry
    ON scheduled_action_inputs(actor_id, session_id, expires_at, job_id);
"""

_SELECT_COLUMNS = (
    "job_id, proposal_id, actor_id, session_id, action, payload_json, "
    "created_at, expires_at, revision"
)


class ScheduledActionStoreError(ValueError):
    """Fixed, content-free failure for every public store operation."""

    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)


@dataclass(frozen=True, repr=False)
class StoredActionEnvelope:
    """Immutable actor/session-bound retained input.

    The representation intentionally omits every identifier and input field.
    ``revision`` is an opaque compare-and-swap guard; this store never mutates
    payload content in place.
    """

    job_id: str
    proposal_id: str
    actor_id: str
    session_id: str
    adapter_input: AdapterInput
    created_at: datetime
    expires_at: datetime
    revision: int = 1

    def __post_init__(self) -> None:
        try:
            _validate_canonical_id(self.job_id)
            _validate_canonical_id(self.proposal_id)
            _validate_scope_id(self.actor_id)
            _validate_scope_id(self.session_id)
            _validate_adapter_input(self.adapter_input)
            _validate_aware_datetime(self.created_at)
            _validate_aware_datetime(self.expires_at)
            if self.expires_at <= self.created_at:
                raise ValueError
            if self.expires_at - self.created_at > _MAX_RETENTION:
                raise ValueError
            if (
                isinstance(self.revision, bool)
                or not isinstance(self.revision, int)
                or self.revision < 1
            ):
                raise ValueError
        except Exception:
            raise ScheduledActionStoreError() from None

    @property
    def action(self) -> ProductivityAction:
        return self.adapter_input.action

    def __repr__(self) -> str:
        return (
            "StoredActionEnvelope("
            f"action={self.action.value!r}, revision={self.revision})"
        )


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _validate_canonical_id(value: object) -> str:
    if not isinstance(value, str) or not _CANONICAL_ID_RE.fullmatch(value):
        raise ScheduledActionStoreError()
    return value


def _validate_scope_id(value: object) -> str:
    if not isinstance(value, str) or not _SCOPE_ID_RE.fullmatch(value):
        raise ScheduledActionStoreError()
    return value


def _validate_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ScheduledActionStoreError()
    return value


def _validate_aware_datetime(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ScheduledActionStoreError()
    if value.year < _MIN_YEAR or value.year > _MAX_YEAR:
        raise ScheduledActionStoreError()
    try:
        timestamp = value.timestamp()
    except Exception:
        raise ScheduledActionStoreError() from None
    if not math.isfinite(timestamp):
        raise ScheduledActionStoreError()
    return value


def _serialize_datetime(value: datetime) -> str:
    return _validate_aware_datetime(value).astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )


def _deserialize_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ScheduledActionStoreError()
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ScheduledActionStoreError() from None
    return _validate_aware_datetime(parsed)


def _validate_adapter_input(value: object) -> AdapterInput:
    if not isinstance(value, AdapterInput):
        raise ScheduledActionStoreError()
    try:
        value.validate()
    except Exception:
        raise ScheduledActionStoreError() from None
    if type(value) not in _ENCODERS:
        raise ScheduledActionStoreError()
    return value


def _encode_research(value: BrowserResearchAdapterInput) -> dict[str, object]:
    return {
        "query": value.query,
        "domains": list(value.domains),
        "max_results": value.max_results,
    }


def _encode_email(value: EmailDraftAdapterInput) -> dict[str, object]:
    return {"recipient": value.recipient, "subject": value.subject, "body": value.body}


def _encode_calendar_read(value: CalendarReadAdapterInput) -> dict[str, object]:
    return {
        "start": value.start,
        "end": value.end,
        "calendar_name": value.calendar_name,
    }


def _encode_calendar_draft(value: CalendarDraftAdapterInput) -> dict[str, object]:
    return {
        "title": value.title,
        "start": value.start,
        "end": value.end,
        "calendar_name": value.calendar_name,
        "location": value.location,
        "notes": value.notes,
    }


def _encode_reminder(value: ReminderCreateAdapterInput) -> dict[str, object]:
    return {
        "title": value.title,
        "remind_at": value.remind_at,
        "notes": value.notes,
        "list_name": value.list_name,
    }


_ENCODERS = {
    BrowserResearchAdapterInput: _encode_research,
    EmailDraftAdapterInput: _encode_email,
    CalendarReadAdapterInput: _encode_calendar_read,
    CalendarDraftAdapterInput: _encode_calendar_draft,
    ReminderCreateAdapterInput: _encode_reminder,
}


def _encode_input(value: AdapterInput) -> str:
    validated = _validate_adapter_input(value)
    encoder = _ENCODERS[type(validated)]
    try:
        encoded = encoder(validated)  # type: ignore[arg-type]
        return json.dumps(
            encoded,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        raise ScheduledActionStoreError() from None


def _require_exact_fields(value: object, expected: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ScheduledActionStoreError()
    return value


def _decode_input(action_value: object, payload_value: object) -> AdapterInput:
    if not isinstance(action_value, str) or not isinstance(payload_value, str):
        raise ScheduledActionStoreError()
    try:
        action = ProductivityAction(action_value)
        raw = json.loads(payload_value)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ScheduledActionStoreError() from None

    try:
        if action is ProductivityAction.BROWSER_RESEARCH:
            data = _require_exact_fields(
                raw, frozenset({"query", "domains", "max_results"})
            )
            domains = data["domains"]
            if not isinstance(domains, list):
                raise ScheduledActionStoreError()
            result: AdapterInput = BrowserResearchAdapterInput(
                data["query"], tuple(domains), data["max_results"]
            )
        elif action is ProductivityAction.EMAIL_DRAFT:
            data = _require_exact_fields(
                raw, frozenset({"recipient", "subject", "body"})
            )
            result = EmailDraftAdapterInput(
                data["recipient"], data["subject"], data["body"]
            )
        elif action is ProductivityAction.CALENDAR_READ:
            data = _require_exact_fields(
                raw, frozenset({"start", "end", "calendar_name"})
            )
            result = CalendarReadAdapterInput(
                data["start"], data["end"], data["calendar_name"]
            )
        elif action is ProductivityAction.CALENDAR_DRAFT:
            data = _require_exact_fields(
                raw,
                frozenset(
                    {"title", "start", "end", "calendar_name", "location", "notes"}
                ),
            )
            result = CalendarDraftAdapterInput(
                data["title"],
                data["start"],
                data["end"],
                data["calendar_name"],
                data["location"],
                data["notes"],
            )
        elif action is ProductivityAction.REMINDER_CREATE:
            data = _require_exact_fields(
                raw, frozenset({"title", "remind_at", "notes", "list_name"})
            )
            result = ReminderCreateAdapterInput(
                data["title"], data["remind_at"], data["notes"], data["list_name"]
            )
        else:
            raise ScheduledActionStoreError()
        result.validate()
    except ScheduledActionStoreError:
        raise
    except Exception:
        raise ScheduledActionStoreError() from None
    if result.action is not action:
        raise ScheduledActionStoreError()
    return result


def _row_to_envelope(row: sqlite3.Row) -> StoredActionEnvelope:
    return StoredActionEnvelope(
        job_id=row["job_id"],
        proposal_id=row["proposal_id"],
        actor_id=row["actor_id"],
        session_id=row["session_id"],
        adapter_input=_decode_input(row["action"], row["payload_json"]),
        created_at=_deserialize_datetime(row["created_at"]),
        expires_at=_deserialize_datetime(row["expires_at"]),
        revision=_validate_revision(row["revision"]),
    )


class ScheduledActionStore:
    """Private SQLite store for immutable scheduled adapter inputs."""

    def __init__(self, db_path: str | Path, *, create_dirs: bool = True) -> None:
        try:
            self.db_path = Path(db_path).expanduser().resolve()
            if create_dirs:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._apply_permissions(include_db=False)
            self._initialize()
            self._apply_permissions(include_db=True)
        except ScheduledActionStoreError:
            raise
        except Exception:
            raise ScheduledActionStoreError() from None

    def __repr__(self) -> str:
        return "ScheduledActionStore()"

    def _apply_permissions(self, *, include_db: bool) -> None:
        try:
            os.chmod(self.db_path.parent, 0o700)
            if include_db and self.db_path.exists():
                os.chmod(self.db_path, 0o600)
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = self.db_path.with_name(self.db_path.name + suffix)
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
        except OSError:
            raise ScheduledActionStoreError() from None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = sqlite3.connect(
                str(self.db_path), factory=_ClosingConnection, timeout=5.0
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
        except Exception:
            raise ScheduledActionStoreError() from None
        try:
            yield connection
        finally:
            connection.close()
            self._apply_permissions(include_db=True)

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
                connection.commit()
        except ScheduledActionStoreError:
            raise
        except Exception:
            raise ScheduledActionStoreError() from None

    def put(self, envelope: StoredActionEnvelope) -> None:
        """Insert one exact input, enforcing at most 64 rows per session."""
        if not isinstance(envelope, StoredActionEnvelope):
            raise ScheduledActionStoreError()
        payload = _encode_input(envelope.adapter_input)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                count = connection.execute(
                    "SELECT COUNT(*) FROM scheduled_action_inputs "
                    "WHERE actor_id = ? AND session_id = ?",
                    (envelope.actor_id, envelope.session_id),
                ).fetchone()[0]
                if not isinstance(count, int) or count >= _MAX_ROWS_PER_SESSION:
                    connection.rollback()
                    raise ScheduledActionStoreError()
                connection.execute(
                    "INSERT INTO scheduled_action_inputs ("
                    "job_id, proposal_id, actor_id, session_id, action, payload_json, "
                    "created_at, expires_at, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        envelope.job_id,
                        envelope.proposal_id,
                        envelope.actor_id,
                        envelope.session_id,
                        envelope.action.value,
                        payload,
                        _serialize_datetime(envelope.created_at),
                        _serialize_datetime(envelope.expires_at),
                        envelope.revision,
                    ),
                )
                connection.commit()
        except ScheduledActionStoreError:
            raise
        except Exception:
            raise ScheduledActionStoreError() from None

    def get(
        self, job_id: str, *, actor_id: str, session_id: str
    ) -> StoredActionEnvelope | None:
        """Return one exact scoped input, or ``None`` without existence leakage."""
        _validate_canonical_id(job_id)
        _validate_scope_id(actor_id)
        _validate_scope_id(session_id)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM scheduled_action_inputs "
                    "WHERE job_id = ? AND actor_id = ? AND session_id = ?",
                    (job_id, actor_id, session_id),
                ).fetchone()
        except ScheduledActionStoreError:
            raise
        except Exception:
            raise ScheduledActionStoreError() from None
        try:
            return _row_to_envelope(row) if row is not None else None
        except ScheduledActionStoreError:
            raise
        except Exception:
            raise ScheduledActionStoreError() from None

    def delete(
        self,
        job_id: str,
        *,
        actor_id: str,
        session_id: str,
        expected_revision: int,
    ) -> bool:
        """CAS-delete an exact scoped revision; stale/cross-scope is a no-op."""
        _validate_canonical_id(job_id)
        _validate_scope_id(actor_id)
        _validate_scope_id(session_id)
        _validate_revision(expected_revision)
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM scheduled_action_inputs "
                    "WHERE job_id = ? AND actor_id = ? AND session_id = ? "
                    "AND revision = ?",
                    (job_id, actor_id, session_id, expected_revision),
                )
                connection.commit()
                return cursor.rowcount == 1
        except ScheduledActionStoreError:
            raise
        except Exception:
            raise ScheduledActionStoreError() from None

    def purge_expired(self, now: datetime, *, limit: int = 64) -> int:
        """Securely delete at most ``limit`` expired rows in deterministic order."""
        now_text = _serialize_datetime(now)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
            raise ScheduledActionStoreError()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    "SELECT job_id, actor_id, session_id, revision "
                    "FROM scheduled_action_inputs WHERE expires_at <= ? "
                    "ORDER BY expires_at ASC, job_id ASC LIMIT ?",
                    (now_text, limit),
                ).fetchall()
                deleted = 0
                for row in rows:
                    cursor = connection.execute(
                        "DELETE FROM scheduled_action_inputs "
                        "WHERE job_id = ? AND actor_id = ? AND session_id = ? "
                        "AND revision = ?",
                        (
                            row["job_id"],
                            row["actor_id"],
                            row["session_id"],
                            row["revision"],
                        ),
                    )
                    deleted += cursor.rowcount
                connection.commit()
                return deleted
        except ScheduledActionStoreError:
            raise
        except Exception:
            raise ScheduledActionStoreError() from None

    def count(self, *, actor_id: str, session_id: str) -> int:
        """Return the bounded count for one exact actor/session scope."""
        _validate_scope_id(actor_id)
        _validate_scope_id(session_id)
        try:
            with self._connect() as connection:
                value = connection.execute(
                    "SELECT COUNT(*) FROM scheduled_action_inputs "
                    "WHERE actor_id = ? AND session_id = ?",
                    (actor_id, session_id),
                ).fetchone()[0]
        except ScheduledActionStoreError:
            raise
        except Exception:
            raise ScheduledActionStoreError() from None
        if not isinstance(value, int) or not 0 <= value <= _MAX_ROWS_PER_SESSION:
            raise ScheduledActionStoreError()
        return value
