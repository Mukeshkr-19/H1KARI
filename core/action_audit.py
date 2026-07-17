"""Content-free durable audit records for policy decisions and results."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Optional

from core.action_policy import ActorContext, PolicyOutcome, validate_actor_context

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_ACTOR_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_RESOURCE_REFERENCE = re.compile(r"^sha256\.[0-9a-f]{64}$")


class _ClosingConnection(sqlite3.Connection):
    """Preserve sqlite transaction contexts while closing deterministically."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class AuditResultStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _stable_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid_{field}")
    return value


def opaque_resource_reference(resource: Optional[str]) -> Optional[str]:
    if resource is None:
        return None
    if not isinstance(resource, str) or not resource:
        raise ValueError("invalid_resource")
    return f"sha256.{hashlib.sha256(resource.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    recorded_at: float
    actor_id: str
    actor: str
    session_id: str
    task_id: Optional[str]
    action: str
    resource_ref: Optional[str]
    destination: Optional[str]
    outcome: str
    reason: str
    result_status: Optional[str]
    result_code: Optional[str]


class ActionAuditStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.chmod(0o700)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        sanitized = False
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS action_audit (
                    audit_id TEXT PRIMARY KEY,
                    recorded_at REAL NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    task_id TEXT,
                    action TEXT NOT NULL,
                    resource_ref TEXT,
                    destination TEXT,
                    outcome TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    result_status TEXT,
                    result_code TEXT
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(action_audit)")}
            if "resource_ref" not in columns:
                conn.execute("ALTER TABLE action_audit ADD COLUMN resource_ref TEXT")
            if "destination" not in columns:
                conn.execute("ALTER TABLE action_audit ADD COLUMN destination TEXT")
            if "resource" in columns:
                cursor = conn.execute(
                    "UPDATE action_audit SET resource = NULL WHERE resource IS NOT NULL"
                )
                sanitized = cursor.rowcount > 0
            sanitized = self._sanitize_legacy_rows(conn) or sanitized
        if sanitized:
            with self._connect() as conn:
                conn.execute("VACUUM")
        self.db_path.chmod(0o600)

    @staticmethod
    def _sanitize_legacy_rows(conn: sqlite3.Connection) -> bool:
        rows = conn.execute(
            """
            SELECT audit_id, actor_id, actor, session_id, task_id, action,
                   resource_ref, destination, outcome, reason,
                   result_status, result_code
            FROM action_audit
            """
        ).fetchall()
        changed = False
        valid_actors = {"owner", "guest", "system", "unknown"}
        valid_outcomes = {item.value for item in PolicyOutcome}
        valid_statuses = {item.value for item in AuditResultStatus}
        for row in rows:
            values = dict(row)
            updates = {
                "audit_id": (
                    values["audit_id"]
                    if isinstance(values["audit_id"], str)
                    and _IDENTIFIER.fullmatch(values["audit_id"])
                    else f"legacy.{uuid.uuid4().hex}"
                ),
                "actor_id": (
                    values["actor_id"]
                    if isinstance(values["actor_id"], str)
                    and _ACTOR_IDENTIFIER.fullmatch(values["actor_id"])
                    else "invalid"
                ),
                "actor": (
                    values["actor"] if values["actor"] in valid_actors else "unknown"
                ),
                "session_id": (
                    values["session_id"]
                    if isinstance(values["session_id"], str)
                    and _ACTOR_IDENTIFIER.fullmatch(values["session_id"])
                    else "invalid"
                ),
                "task_id": ActionAuditStore._nullable_identifier(values["task_id"]),
                "action": (
                    values["action"]
                    if isinstance(values["action"], str)
                    and _IDENTIFIER.fullmatch(values["action"])
                    else "invalid_action"
                ),
                "resource_ref": (
                    values["resource_ref"]
                    if isinstance(values["resource_ref"], str)
                    and _RESOURCE_REFERENCE.fullmatch(values["resource_ref"])
                    else None
                ),
                "destination": ActionAuditStore._nullable_identifier(
                    values["destination"]
                ),
                "outcome": (
                    values["outcome"]
                    if values["outcome"] in valid_outcomes
                    else PolicyOutcome.DENY.value
                ),
                "reason": (
                    values["reason"]
                    if isinstance(values["reason"], str)
                    and _IDENTIFIER.fullmatch(values["reason"])
                    else "legacy_record"
                ),
                "result_status": (
                    values["result_status"]
                    if values["result_status"] in valid_statuses
                    else None
                ),
                "result_code": ActionAuditStore._nullable_identifier(
                    values["result_code"]
                ),
            }
            if all(values[key] == value for key, value in updates.items()):
                continue
            conn.execute(
                """
                UPDATE action_audit SET
                    audit_id = ?, actor_id = ?, actor = ?, session_id = ?, task_id = ?,
                    action = ?, resource_ref = ?, destination = ?, outcome = ?,
                    reason = ?, result_status = ?, result_code = ?
                WHERE audit_id = ?
                """,
                (*updates.values(), values["audit_id"]),
            )
            changed = True
        return changed

    @staticmethod
    def _nullable_identifier(value: object) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str) and _IDENTIFIER.fullmatch(value):
            return value
        return None

    def record_decision(
        self,
        *,
        actor: ActorContext,
        task_id: Optional[str],
        action: str,
        resource_ref: Optional[str],
        destination: Optional[str],
        outcome: PolicyOutcome,
        reason: str,
    ) -> str:
        valid_actor, _reason = validate_actor_context(actor)
        if not valid_actor:
            raise ValueError("invalid_actor")
        stable_action = _stable_identifier(action, "action")
        stable_reason = _stable_identifier(reason, "reason")
        if not isinstance(outcome, PolicyOutcome):
            raise ValueError("invalid_outcome")
        if task_id is not None:
            _stable_identifier(task_id, "task_id")
        if resource_ref is not None and (
            not isinstance(resource_ref, str)
            or not _RESOURCE_REFERENCE.fullmatch(resource_ref)
        ):
            raise ValueError("invalid_resource_ref")
        if destination is not None:
            _stable_identifier(destination, "destination")
        audit_id = str(uuid.uuid4())
        role = actor.actor.value
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO action_audit (
                    audit_id, recorded_at, actor_id, actor, session_id, task_id,
                    action, resource_ref, destination, outcome, reason,
                    result_status, result_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    audit_id,
                    time.time(),
                    actor.actor_id,
                    role,
                    actor.session_id,
                    task_id,
                    stable_action,
                    resource_ref,
                    destination,
                    outcome.value,
                    stable_reason,
                ),
            )
        return audit_id

    def record_result(
        self,
        audit_id: str,
        *,
        status: AuditResultStatus | str,
        code: Optional[str] = None,
    ) -> None:
        try:
            stable_status = AuditResultStatus(status).value
        except (TypeError, ValueError):
            raise ValueError("invalid_result_status") from None
        stable_code = _stable_identifier(code, "result_code") if code is not None else None
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE action_audit SET result_status = ?, result_code = ?
                WHERE audit_id = ?
                """,
                (stable_status, stable_code, audit_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"audit record not found: {audit_id}")

    def list_recent(self, limit: int = 20) -> list[AuditRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT audit_id, recorded_at, actor_id, actor, session_id, task_id,
                       action, resource_ref, destination, outcome, reason,
                       result_status, result_code
                FROM action_audit ORDER BY recorded_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [AuditRecord(**dict(row)) for row in rows]
