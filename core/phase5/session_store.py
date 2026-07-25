"""Bounded SQLite-backed Phase 5 session store.

Persists AccessSession snapshots safely with optimistic concurrency, explicit
database permissions, and strict privacy guarantees (zero raw evidence or private
content stored).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from core.phase5.contracts import Capability, CapabilityGrant, ScopeConstraint
from core.phase5.session_lifecycle import (
    AccessSession,
    AuthoritySource,
    SessionAuthoritySnapshot,
    SessionDecisionReason,
    SessionState,
    SessionTransition,
    SessionType,
    _validate_actor_identifier,
    _validate_finite_timestamp,
    _validate_identifier,
)

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_EVIDENCE_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_LIMIT = 100


class StaleRevisionError(ValueError):
    """Raised when updating a session with a stale or mismatched revision counter."""

    pass


class _ClosingConnection(sqlite3.Connection):
    """Preserve SQLite transaction contexts while closing deterministically."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _digest_evidence(evidence: Optional[str]) -> Optional[str]:
    """Return SHA-256 digest of activation evidence, never storing raw text."""
    if evidence is None:
        return None
    # Loaded child sessions carry only the stored digest. Keep it stable across
    # lifecycle updates instead of hashing the digest again on every save.
    if _EVIDENCE_DIGEST_RE.fullmatch(evidence):
        return evidence
    return hashlib.sha256(evidence.encode("utf-8")).hexdigest()


class Phase5SessionStore:
    """SQLite-backed store for Phase 5 AccessSession value objects."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser().resolve()
        parent_dir = self.db_path.parent
        parent_dir.mkdir(parents=True, exist_ok=True)
        # Apply 0o700 permission to subdirectories, avoiding system root/tmp
        try:
            if parent_dir not in (Path("/tmp"), Path("/private/tmp"), Path("/")):
                parent_dir.chmod(0o700)
        except OSError:
            pass
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS phase5_sessions (
                    session_id TEXT PRIMARY KEY,
                    session_type TEXT NOT NULL,
                    owner_actor_id TEXT NOT NULL,
                    session_actor_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    capabilities TEXT NOT NULL,
                    authority_source TEXT NOT NULL,
                    evidence_digest TEXT,
                    grant_id TEXT,
                    revoked_at REAL,
                    locked_at REAL,
                    closed_at REAL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    serialized_snapshot TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_owner
                ON phase5_sessions(owner_actor_id, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_actor_active
                ON phase5_sessions(session_actor_id, state, expires_at)
                """
            )
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass

    def save_session(
        self,
        session: AccessSession,
        expected_revision: Optional[int] = None,
    ) -> AccessSession:
        """Persist or update an AccessSession snapshot.

        Enforces optimistic concurrency checking when an expected revision is specified.
        Rejects attempts to modify immutable session identity or broaden capabilities.
        """
        if not isinstance(session, AccessSession):
            raise ValueError("invalid session object")

        _validate_identifier(session.session_id, "session_id")
        _validate_actor_identifier(session.owner_actor_id, "owner_actor_id")
        _validate_actor_identifier(session.session_actor_id, "session_actor_id")
        _validate_finite_timestamp(session.created_at, "created_at")
        _validate_finite_timestamp(session.expires_at, "expires_at")

        capabilities_str = ",".join(cap.value for cap in session.capabilities)
        evidence_digest = _digest_evidence(session.authority_snapshot.activation_evidence)
        grant_id = (
            session.authority_snapshot.grant.grant_id
            if session.authority_snapshot.grant
            else None
        )

        snapshot_dict = self._serialize_session_dict(session)
        serialized_json = json.dumps(snapshot_dict)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = conn.execute(
                """SELECT session_type, owner_actor_id, session_actor_id,
                          capabilities, authority_source, evidence_digest,
                          grant_id, revision
                   FROM phase5_sessions WHERE session_id = ?""",
                (session.session_id,),
            ).fetchone()

            if row is None:
                # Creation path
                new_revision = 1
                if expected_revision is not None and expected_revision != 0 and expected_revision != 1:
                    raise StaleRevisionError("expected revision mismatch on creation")
                conn.execute(
                    """
                    INSERT INTO phase5_sessions (
                        session_id, session_type, owner_actor_id, session_actor_id,
                        state, created_at, expires_at, capabilities, authority_source,
                        evidence_digest, grant_id, revoked_at, locked_at, closed_at,
                        revision, serialized_snapshot
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        session.session_type.value,
                        session.owner_actor_id,
                        session.session_actor_id,
                        session.state.value,
                        session.created_at,
                        session.expires_at,
                        capabilities_str,
                        session.authority_snapshot.source.value,
                        evidence_digest,
                        grant_id,
                        session.revoked_at,
                        session.locked_at,
                        session.closed_at,
                        new_revision,
                        serialized_json,
                    ),
                )
                conn.commit()
                return session

            # Update path
            current_revision = row["revision"]
            if expected_revision is not None and current_revision != expected_revision:
                raise StaleRevisionError("stale revision encountered during update")

            # Immutable field check
            if (
                row["session_type"] != session.session_type.value
                or row["owner_actor_id"] != session.owner_actor_id
                or row["session_actor_id"] != session.session_actor_id
                or row["authority_source"] != session.authority_snapshot.source.value
                or row["evidence_digest"] != evidence_digest
                or row["grant_id"] != grant_id
            ):
                raise ValueError("immutable session authority fields cannot be modified")

            # Capability expansion check
            existing_caps = set(row["capabilities"].split(",")) if row["capabilities"] else set()
            new_caps = {cap.value for cap in session.capabilities}
            if not new_caps.issubset(existing_caps):
                raise ValueError("capability set expansion is prohibited during update")

            new_revision = current_revision + 1
            cursor = conn.execute(
                """
                UPDATE phase5_sessions SET
                    state = ?,
                    expires_at = ?,
                    revoked_at = ?,
                    locked_at = ?,
                    closed_at = ?,
                    revision = ?,
                    serialized_snapshot = ?
                WHERE session_id = ? AND revision = ?
                """,
                (
                    session.state.value,
                    session.expires_at,
                    session.revoked_at,
                    session.locked_at,
                    session.closed_at,
                    new_revision,
                    serialized_json,
                    session.session_id,
                    current_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleRevisionError("failed optimistic concurrency check")
            conn.commit()

        return session

    def get_session(self, session_id: str) -> Optional[AccessSession]:
        """Fetch an AccessSession snapshot by session_id."""
        _validate_identifier(session_id, "session_id")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT serialized_snapshot FROM phase5_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return self._deserialize_session_json(row["serialized_snapshot"])

    def get_revision(self, session_id: str) -> Optional[int]:
        """Fetch the current revision number of a stored session."""
        _validate_identifier(session_id, "session_id")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT revision FROM phase5_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return row["revision"]

    def get_active_session_for_actor(self, actor_id: str, now: float) -> Optional[AccessSession]:
        """Retrieve the current active session for an actor if non-expired."""
        _validate_actor_identifier(actor_id, "actor_id")
        _validate_finite_timestamp(now, "now")

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT serialized_snapshot FROM phase5_sessions
                WHERE session_actor_id = ? AND state = ? AND expires_at > ?
                ORDER BY created_at DESC, session_id ASC
                """,
                (actor_id, SessionState.ACTIVE.value, now),
            ).fetchall()

        for row in rows:
            session = self._deserialize_session_json(row["serialized_snapshot"])
            if session and session.is_active(now):
                return session
        return None

    def list_owner_sessions(self, owner_actor_id: str, limit: int = 50) -> Tuple[AccessSession, ...]:
        """List bounded recent sessions owned by an owner actor."""
        _validate_actor_identifier(owner_actor_id, "owner_actor_id")
        effective_limit = min(max(1, limit), _MAX_LIMIT)

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT serialized_snapshot FROM phase5_sessions
                WHERE owner_actor_id = ?
                ORDER BY created_at DESC, session_id ASC
                LIMIT ?
                """,
                (owner_actor_id, effective_limit),
            ).fetchall()

        result = []
        for row in rows:
            session = self._deserialize_session_json(row["serialized_snapshot"])
            if session:
                result.append(session)
        return tuple(result)

    def expire_due_sessions(self, now: float) -> int:
        """Mark active sessions as EXPIRED if their expires_at timestamp is <= now."""
        _validate_finite_timestamp(now, "now")

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            rows = conn.execute(
                """
                SELECT session_id, revision, serialized_snapshot FROM phase5_sessions
                WHERE state = ? AND expires_at <= ?
                """,
                (SessionState.ACTIVE.value, now),
            ).fetchall()

            expired_count = 0
            for row in rows:
                session_id = row["session_id"]
                current_rev = row["revision"]
                session = self._deserialize_session_json(row["serialized_snapshot"])
                if session is None:
                    continue

                # Transition to EXPIRED
                new_transition = SessionTransition(
                    transition_id=f"{session_id}.expired.{int(now)}",
                    session_id=session_id,
                    from_state=session.state,
                    to_state=SessionState.EXPIRED,
                    reason=SessionDecisionReason.SESSION_EXPIRED,
                    actor_id=session.owner_actor_id,
                    timestamp=now,
                )

                expired_session = AccessSession(
                    session_id=session.session_id,
                    session_type=session.session_type,
                    owner_actor_id=session.owner_actor_id,
                    session_actor_id=session.session_actor_id,
                    state=SessionState.EXPIRED,
                    created_at=session.created_at,
                    expires_at=session.expires_at,
                    capabilities=session.capabilities,
                    authority_snapshot=session.authority_snapshot,
                    transitions=session.transitions + (new_transition,),
                    revoked_at=session.revoked_at,
                    locked_at=session.locked_at,
                    closed_at=session.closed_at,
                )

                serialized_json = json.dumps(self._serialize_session_dict(expired_session))
                cursor = conn.execute(
                    """
                    UPDATE phase5_sessions SET
                        state = ?,
                        revision = revision + 1,
                        serialized_snapshot = ?
                    WHERE session_id = ? AND revision = ?
                    """,
                    (SessionState.EXPIRED.value, serialized_json, session_id, current_rev),
                )
                if cursor.rowcount == 1:
                    expired_count += 1

            conn.commit()
            return expired_count

    def close(self) -> None:
        """Close store resources."""
        pass

    def __repr__(self) -> str:
        return "Phase5SessionStore()"

    # --- Internal Serialization Helpers ---

    def _serialize_session_dict(self, session: AccessSession) -> dict:
        auth = session.authority_snapshot
        evidence_digest = _digest_evidence(auth.activation_evidence)

        grant_dict = None
        if auth.grant is not None:
            g = auth.grant
            grant_dict = {
                "grant_id": g.grant_id,
                "helper_actor_id": g.helper_actor_id,
                "owner_actor_id": g.owner_actor_id,
                "capability": g.capability.value,
                "scope": {
                    "capability": g.scope.capability.value,
                    "data_subject": g.scope.data_subject,
                    "resource_pattern": g.scope.resource_pattern,
                    "max_duration_seconds": g.scope.max_duration_seconds,
                    "allowed_actions": list(g.scope.allowed_actions),
                },
                "issued_at": g.issued_at,
                "expires_at": g.expires_at,
                "revoked": g.revoked,
                "revoked_at": g.revoked_at,
            }

        return {
            "session_id": session.session_id,
            "session_type": session.session_type.value,
            "owner_actor_id": session.owner_actor_id,
            "session_actor_id": session.session_actor_id,
            "state": session.state.value,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
            "capabilities": [cap.value for cap in session.capabilities],
            "authority_snapshot": {
                "source": auth.source.value,
                "owner_actor_id": auth.owner_actor_id,
                "evidence_digest": evidence_digest,
                "grant": grant_dict,
            },
            "transitions": [
                {
                    "transition_id": t.transition_id,
                    "session_id": t.session_id,
                    "from_state": t.from_state.value,
                    "to_state": t.to_state.value,
                    "reason": t.reason.value,
                    "actor_id": t.actor_id,
                    "timestamp": t.timestamp,
                }
                for t in session.transitions
            ],
            "revoked_at": session.revoked_at,
            "locked_at": session.locked_at,
            "closed_at": session.closed_at,
        }

    def _deserialize_session_json(self, data_str: str) -> AccessSession:
        try:
            d = json.loads(data_str)
            auth_d = d["authority_snapshot"]

            grant_obj = None
            if auth_d.get("grant") is not None:
                g_dict = auth_d["grant"]
                s_dict = g_dict["scope"]
                scope = ScopeConstraint(
                    capability=Capability(s_dict["capability"]),
                    data_subject=s_dict.get("data_subject"),
                    resource_pattern=s_dict.get("resource_pattern"),
                    max_duration_seconds=s_dict.get("max_duration_seconds"),
                    allowed_actions=tuple(s_dict.get("allowed_actions", [])),
                )
                grant_obj = CapabilityGrant(
                    grant_id=g_dict["grant_id"],
                    helper_actor_id=g_dict["helper_actor_id"],
                    owner_actor_id=g_dict["owner_actor_id"],
                    capability=Capability(g_dict["capability"]),
                    scope=scope,
                    issued_at=g_dict["issued_at"],
                    expires_at=g_dict["expires_at"],
                    revoked=g_dict["revoked"],
                    revoked_at=g_dict.get("revoked_at"),
                )

            evidence_val = auth_d.get("evidence_digest")
            if evidence_val is None and AuthoritySource(auth_d["source"]) is AuthoritySource.CHILD_ACTIVATION:
                evidence_val = "sha256_placeholder"

            authority = SessionAuthoritySnapshot(
                source=AuthoritySource(auth_d["source"]),
                owner_actor_id=auth_d["owner_actor_id"],
                grant=grant_obj,
                activation_evidence=evidence_val,
            )

            transitions = tuple(
                SessionTransition(
                    transition_id=t["transition_id"],
                    session_id=t["session_id"],
                    from_state=SessionState(t["from_state"]),
                    to_state=SessionState(t["to_state"]),
                    reason=SessionDecisionReason(t["reason"]),
                    actor_id=t["actor_id"],
                    timestamp=t["timestamp"],
                )
                for t in d.get("transitions", [])
            )

            return AccessSession(
                session_id=d["session_id"],
                session_type=SessionType(d["session_type"]),
                owner_actor_id=d["owner_actor_id"],
                session_actor_id=d["session_actor_id"],
                state=SessionState(d["state"]),
                created_at=d["created_at"],
                expires_at=d["expires_at"],
                capabilities=tuple(Capability(cap) for cap in d["capabilities"]),
                authority_snapshot=authority,
                transitions=transitions,
                revoked_at=d.get("revoked_at"),
                locked_at=d.get("locked_at"),
                closed_at=d.get("closed_at"),
            )
        except Exception as e:
            raise ValueError("corrupt session record") from e
