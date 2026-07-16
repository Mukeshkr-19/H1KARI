"""One-use approval grants for policy-controlled actions."""

from __future__ import annotations

import math
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.action_policy import Actor, ActorContext, validate_actor_context

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


def canonicalize_resource(resource: Optional[str]) -> Optional[str]:
    if resource is None:
        return None
    try:
        if not isinstance(resource, str):
            raise ValueError
        value = resource.strip()
        if not value or "\x00" in value or len(value) > 4096:
            raise ValueError
        path = Path(value).expanduser()
        if path.is_symlink():
            raise ValueError
        return str(path.resolve(strict=False))
    except (AttributeError, OSError, RuntimeError, ValueError):
        raise ValueError("invalid_resource") from None


@dataclass(frozen=True)
class ApprovalGrant:
    grant_id: str
    actor_id: str
    actor: Actor
    session_id: str
    action: str
    resource: Optional[str]
    destination: Optional[str]
    task_id: Optional[str]
    expires_at: float
    remaining_uses: int = 1
    revoked: bool = False


class GrantStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.chmod(0o700)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_grants (
                    grant_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource TEXT,
                    destination TEXT,
                    task_id TEXT,
                    expires_at REAL NOT NULL,
                    remaining_uses INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(approval_grants)")
            }
            if "destination" not in columns:
                conn.execute("ALTER TABLE approval_grants ADD COLUMN destination TEXT")
        self.db_path.chmod(0o600)

    def issue(
        self,
        *,
        actor: ActorContext,
        action: str,
        resource: Optional[str] = None,
        destination: Optional[str] = None,
        task_id: Optional[str] = None,
        expires_at: float,
        remaining_uses: int = 1,
        grant_id: Optional[str] = None,
    ) -> ApprovalGrant:
        valid_actor, _reason = validate_actor_context(actor)
        if not valid_actor or actor.actor in {Actor.UNKNOWN, Actor.SYSTEM}:
            raise ValueError("invalid_actor")
        if not isinstance(action, str) or not _IDENTIFIER.fullmatch(action):
            raise ValueError("invalid_action")
        if task_id is not None and (
            not isinstance(task_id, str) or not _IDENTIFIER.fullmatch(task_id)
        ):
            raise ValueError("invalid_task_id")
        if destination is not None and (
            not isinstance(destination, str) or not _IDENTIFIER.fullmatch(destination)
        ):
            raise ValueError("invalid_destination")
        if action == "provider.send_document" and destination is None:
            raise ValueError("destination_required")
        if action != "provider.send_document" and destination is not None:
            raise ValueError("unexpected_destination")
        if (
            type(expires_at) not in (int, float)
            or not math.isfinite(expires_at)
            or expires_at <= time.time()
        ):
            raise ValueError("invalid_expiry")
        if type(remaining_uses) is not int or remaining_uses != 1:
            raise ValueError("invalid_remaining_uses")
        grant = ApprovalGrant(
            grant_id=grant_id or str(uuid.uuid4()),
            actor_id=actor.actor_id,
            actor=actor.actor,
            session_id=actor.session_id,
            action=action,
            resource=canonicalize_resource(resource),
            destination=destination,
            task_id=task_id,
            expires_at=expires_at,
            remaining_uses=remaining_uses,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO approval_grants (
                    grant_id, actor_id, actor, session_id, action, resource,
                    destination, task_id, expires_at, remaining_uses, revoked
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    grant.grant_id,
                    grant.actor_id,
                    grant.actor.value,
                    grant.session_id,
                    grant.action,
                    grant.resource,
                    grant.destination,
                    grant.task_id,
                    grant.expires_at,
                    grant.remaining_uses,
                ),
            )
        return grant

    def revoke(self, grant_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE approval_grants SET revoked = 1 WHERE grant_id = ?",
                (grant_id,),
            )
        return cursor.rowcount == 1

    def consume(
        self,
        grant_id: str,
        *,
        actor: ActorContext,
        action: str,
        resource: Optional[str],
        destination: Optional[str] = None,
        task_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> tuple[bool, str]:
        valid_actor, _reason = validate_actor_context(actor)
        if not valid_actor:
            return False, "invalid_actor"
        try:
            expected_resource = canonicalize_resource(resource)
        except ValueError:
            return False, "invalid_resource"
        if destination is not None and (
            not isinstance(destination, str) or not _IDENTIFIER.fullmatch(destination)
        ):
            return False, "invalid_destination"
        instant = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM approval_grants WHERE grant_id = ?", (grant_id,)
            ).fetchone()
            if row is None:
                return False, "grant_not_found"
            checks = (
                (bool(row["revoked"]), "grant_revoked"),
                (row["expires_at"] <= instant, "grant_expired"),
                (row["remaining_uses"] < 1, "grant_consumed"),
                (row["remaining_uses"] > 1, "grant_invalid_uses"),
                (
                    row["actor_id"] != actor.actor_id
                    or row["actor"] != getattr(actor.actor, "value", actor.actor)
                    or row["session_id"] != actor.session_id,
                    "grant_actor_mismatch",
                ),
                (row["action"] != action, "grant_action_mismatch"),
                (row["resource"] != expected_resource, "grant_resource_mismatch"),
                (row["destination"] != destination, "grant_destination_mismatch"),
                (row["task_id"] != task_id, "grant_task_mismatch"),
            )
            for failed, reason in checks:
                if failed:
                    return False, reason
            conn.execute(
                """
                UPDATE approval_grants
                SET remaining_uses = remaining_uses - 1
                WHERE grant_id = ? AND remaining_uses > 0
                """,
                (grant_id,),
            )
        return True, "grant_consumed"
