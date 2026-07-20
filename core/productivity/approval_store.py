"""SQLite-backed durable store for Phase 3 productivity approvals.

The store persists only the fields of ``ProductivityApproval``. No proposal
content, targets, preview fields, or user payload ever reaches the database.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from core.action_policy import Actor
from core.productivity.authorization import ApprovalScope, ProductivityApproval
from core.productivity.contracts import ProductivityAction


class ApprovalStoreError(Exception):
    """Raised when an approval-store operation cannot complete."""


_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_SNAPSHOT_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ConsumptionResult:
    """Result of an atomic consume attempt."""

    success: bool
    approval: Optional[ProductivityApproval] = None


class SqliteApprovalStore:
    """Durable, actor-scoped store for productivity approvals.

    The database file is created with mode 0600 inside a directory with mode
    0700. The store does not import or use any clock, network, or execution
    facility.

    All SQLite, filesystem, and permission failures are surfaced as a fixed
    ``ApprovalStoreError`` without exception causes, database paths, SQL,
    approval IDs, actor IDs, SQLite messages, or row data.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS approvals (
        approval_id TEXT PRIMARY KEY,
        actor_id TEXT NOT NULL,
        actor TEXT NOT NULL,
        session_id TEXT,
        action TEXT NOT NULL,
        proposal_id TEXT NOT NULL,
        snapshot_digest TEXT NOT NULL,
        issued_at REAL NOT NULL,
        scope TEXT NOT NULL,
        expiry REAL,
        remaining_uses INTEGER,
        revoked INTEGER NOT NULL DEFAULT 0
    ) STRICT;

    CREATE INDEX IF NOT EXISTS idx_approvals_actor ON approvals(actor_id);
    CREATE INDEX IF NOT EXISTS idx_approvals_proposal ON approvals(proposal_id);
    """

    def __init__(self, db_path: str) -> None:
        try:
            self._db_path = Path(db_path)
            self._closed = False
            self._ensure_directory()
            self._ensure_schema()
            self._ensure_permissions()
        except ApprovalStoreError:
            raise
        except Exception:
            raise ApprovalStoreError("store initialization failed") from None

    def _ensure_directory(self) -> None:
        try:
            directory = self._db_path.parent
            if not directory.exists():
                directory.mkdir(parents=True, mode=0o700)
            elif not directory.is_dir():
                raise ApprovalStoreError("database parent is not a directory")
            else:
                os.chmod(directory, 0o700)
        except ApprovalStoreError:
            raise
        except OSError:
            raise ApprovalStoreError("database directory operation failed") from None

    def _ensure_schema(self) -> None:
        try:
            with self._connection() as conn:
                conn.executescript(self._SCHEMA)
                conn.commit()
        except ApprovalStoreError:
            raise
        except sqlite3.Error:
            raise ApprovalStoreError("database schema initialization failed") from None

    def _ensure_permissions(self) -> None:
        try:
            if self._db_path.exists():
                os.chmod(self._db_path, 0o600)
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = self._db_path.parent / (self._db_path.name + suffix)
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
        except OSError:
            raise ApprovalStoreError("database permission operation failed") from None

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._closed:
            raise ApprovalStoreError("store is closed")
        try:
            conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        except sqlite3.Error:
            raise ApprovalStoreError("database connection failed") from None
        try:
            yield conn
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def close(self) -> None:
        """Close the store deterministically."""
        self._closed = True

    def __enter__(self) -> "SqliteApprovalStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _row_to_approval(self, row: sqlite3.Row) -> ProductivityApproval:
        try:
            return ProductivityApproval(
                approval_id=row["approval_id"],
                actor_id=row["actor_id"],
                actor=Actor(row["actor"]),
                session_id=row["session_id"],
                action=ProductivityAction(row["action"]),
                proposal_id=row["proposal_id"],
                snapshot_digest=row["snapshot_digest"],
                issued_at=row["issued_at"],
                scope=ApprovalScope(row["scope"]),
                expiry=row["expiry"],
                remaining_uses=row["remaining_uses"],
                revoked=bool(row["revoked"]),
            )
        except Exception:
            raise ApprovalStoreError("malformed approval row") from None

    def _approval_to_values(self, approval: ProductivityApproval) -> tuple:
        return (
            approval.approval_id,
            approval.actor_id,
            approval.actor.value,
            approval.session_id,
            approval.action.value,
            approval.proposal_id,
            approval.snapshot_digest,
            approval.issued_at,
            approval.scope.value,
            approval.expiry,
            approval.remaining_uses,
            int(approval.revoked),
        )

    def issue(self, approval: ProductivityApproval) -> None:
        """Persist a new approval. Raises on duplicate IDs."""
        try:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO approvals (
                        approval_id, actor_id, actor, session_id, action,
                        proposal_id, snapshot_digest, issued_at, scope, expiry,
                        remaining_uses, revoked
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._approval_to_values(approval),
                )
        except ApprovalStoreError:
            raise
        except sqlite3.IntegrityError:
            raise ApprovalStoreError("duplicate approval_id") from None
        except sqlite3.Error:
            raise ApprovalStoreError("approval issue failed") from None

    def get(self, approval_id: str, actor_id: str) -> Optional[ProductivityApproval]:
        """Return the approval if it exists and belongs to the actor."""
        try:
            with self._connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM approvals WHERE approval_id = ? AND actor_id = ?",
                    (approval_id, actor_id),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return self._row_to_approval(row)
        except ApprovalStoreError:
            raise
        except sqlite3.Error:
            raise ApprovalStoreError("approval read failed") from None

    def consume_once(self, approval_id: str, actor_id: str, now: float) -> ConsumptionResult:
        """Atomically consume a once approval.

        Returns a ``ConsumptionResult`` with the approval if the CAS succeeds.
        """
        try:
            with self._connection() as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        """
                        SELECT * FROM approvals
                        WHERE approval_id = ? AND actor_id = ?
                        """,
                        (approval_id, actor_id),
                    ).fetchone()

                    if row is None:
                        return ConsumptionResult(success=False)

                    approval = self._row_to_approval(row)
                    if approval.revoked:
                        return ConsumptionResult(success=False)
                    if approval.is_expired(now):
                        return ConsumptionResult(success=False)
                    if approval.remaining_uses != 1:
                        return ConsumptionResult(success=False)

                    cursor = conn.execute(
                        """
                        UPDATE approvals
                        SET remaining_uses = 0
                        WHERE approval_id = ?
                          AND actor_id = ?
                          AND remaining_uses = 1
                          AND revoked = 0
                          AND (expiry > ? OR expiry IS NULL)
                        """,
                        (approval_id, actor_id, now),
                    )
                    if cursor.rowcount != 1:
                        return ConsumptionResult(success=False)

                    return ConsumptionResult(success=True, approval=approval)
                finally:
                    conn.execute("COMMIT")
        except ApprovalStoreError:
            raise
        except sqlite3.Error:
            raise ApprovalStoreError("approval consume failed") from None

    def consume(self, approval_id: str, actor_id: str, now: float) -> ConsumptionResult:
        """Atomically consume or verify an approval for any scope.

        For ``ONCE`` scope this decrements ``remaining_uses``. For all other
        scopes it verifies the approval is present, unrevoked, and unexpired
        without mutating durable state.
        """
        try:
            with self._connection() as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        """
                        SELECT * FROM approvals
                        WHERE approval_id = ? AND actor_id = ?
                        """,
                        (approval_id, actor_id),
                    ).fetchone()

                    if row is None:
                        return ConsumptionResult(success=False)

                    approval = self._row_to_approval(row)
                    if approval.revoked:
                        return ConsumptionResult(success=False)
                    if approval.is_expired(now):
                        return ConsumptionResult(success=False)

                    if approval.scope is ApprovalScope.ONCE:
                        if approval.remaining_uses != 1:
                            return ConsumptionResult(success=False)

                        cursor = conn.execute(
                            """
                            UPDATE approvals
                            SET remaining_uses = 0
                            WHERE approval_id = ?
                              AND actor_id = ?
                              AND remaining_uses = 1
                              AND revoked = 0
                              AND (expiry > ? OR expiry IS NULL)
                            """,
                            (approval_id, actor_id, now),
                        )
                        if cursor.rowcount != 1:
                            return ConsumptionResult(success=False)

                    return ConsumptionResult(success=True, approval=approval)
                finally:
                    conn.execute("COMMIT")
        except ApprovalStoreError:
            raise
        except sqlite3.Error:
            raise ApprovalStoreError("approval consume failed") from None

    def revoke(self, approval_id: str, actor_id: str) -> bool:
        """Revoke an approval. Returns True if an approval was revoked."""
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE approvals
                    SET revoked = 1
                    WHERE approval_id = ? AND actor_id = ? AND revoked = 0
                    """,
                    (approval_id, actor_id),
                )
                return cursor.rowcount == 1
        except sqlite3.Error:
            raise ApprovalStoreError("approval revoke failed") from None

    def find_current(
        self,
        actor_id: str,
        proposal_id: str,
        scope: ApprovalScope,
        session_id: Optional[str] = None,
    ) -> Optional[ProductivityApproval]:
        """Return the current unrevoked approval for an actor/proposal/scope.

        For ``ONCE`` and ``SESSION`` scopes the approval is also matched against
        the supplied ``session_id``. For ``DURATION`` and ``PRECISE_PERSISTENT``
        scopes ``session_id`` is ignored and the stored ``session_id`` must be
        ``None``.
        """
        try:
            with self._connection() as conn:
                conn.row_factory = sqlite3.Row
                if scope in (ApprovalScope.ONCE, ApprovalScope.SESSION):
                    cursor = conn.execute(
                        """
                        SELECT * FROM approvals
                        WHERE actor_id = ?
                          AND proposal_id = ?
                          AND scope = ?
                          AND session_id IS ?
                          AND revoked = 0
                        ORDER BY issued_at DESC, approval_id ASC
                        LIMIT 1
                        """,
                        (actor_id, proposal_id, scope.value, session_id),
                    )
                else:
                    cursor = conn.execute(
                        """
                        SELECT * FROM approvals
                        WHERE actor_id = ?
                          AND proposal_id = ?
                          AND scope = ?
                          AND session_id IS NULL
                          AND revoked = 0
                        ORDER BY issued_at DESC, approval_id ASC
                        LIMIT 1
                        """,
                        (actor_id, proposal_id, scope.value),
                    )
                row = cursor.fetchone()
                if row is None:
                    return None
                return self._row_to_approval(row)
        except ApprovalStoreError:
            raise
        except sqlite3.Error:
            raise ApprovalStoreError("approval query failed") from None

    def rebind_precise_persistent(
        self,
        actor_id: str,
        action: ProductivityAction,
        snapshot_digest_value: str,
        proposal_id: str,
    ) -> Optional[ProductivityApproval]:
        """Atomically bind a matching persistent grant to a new proposal.

        Selection is limited to an active precise-persistent approval owned by
        the same actor and bound to the exact action and snapshot digest. The
        caller cannot nominate an approval identifier.
        """
        if not isinstance(actor_id, str) or not _IDENTIFIER_RE.fullmatch(actor_id):
            raise ApprovalStoreError("invalid approval rebind request")
        if not isinstance(action, ProductivityAction):
            raise ApprovalStoreError("invalid approval rebind request")
        if not isinstance(snapshot_digest_value, str) or not _SNAPSHOT_DIGEST_RE.fullmatch(
            snapshot_digest_value
        ):
            raise ApprovalStoreError("invalid approval rebind request")
        if not isinstance(proposal_id, str) or not _IDENTIFIER_RE.fullmatch(proposal_id):
            raise ApprovalStoreError("invalid approval rebind request")

        try:
            with self._connection() as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        """
                        SELECT * FROM approvals
                        WHERE actor_id = ?
                          AND action = ?
                          AND snapshot_digest = ?
                          AND scope = 'precise_persistent'
                          AND session_id IS NULL
                          AND expiry IS NULL
                          AND remaining_uses IS NULL
                          AND revoked = 0
                        ORDER BY issued_at DESC, approval_id ASC
                        LIMIT 1
                        """,
                        (actor_id, action.value, snapshot_digest_value),
                    ).fetchone()
                    if row is None:
                        return None

                    approval = self._row_to_approval(row)
                    cursor = conn.execute(
                        """
                        UPDATE approvals
                        SET proposal_id = ?
                        WHERE approval_id = ?
                          AND actor_id = ?
                          AND proposal_id = ?
                          AND action = ?
                          AND snapshot_digest = ?
                          AND scope = 'precise_persistent'
                          AND session_id IS NULL
                          AND expiry IS NULL
                          AND remaining_uses IS NULL
                          AND revoked = 0
                        """,
                        (
                            proposal_id,
                            approval.approval_id,
                            actor_id,
                            approval.proposal_id,
                            action.value,
                            snapshot_digest_value,
                        ),
                    )
                    if cursor.rowcount != 1:
                        return None

                    rebound_row = conn.execute(
                        """
                        SELECT * FROM approvals
                        WHERE approval_id = ? AND actor_id = ? AND proposal_id = ?
                        """,
                        (approval.approval_id, actor_id, proposal_id),
                    ).fetchone()
                    if rebound_row is None:
                        return None
                    return self._row_to_approval(rebound_row)
                finally:
                    conn.execute("COMMIT")
        except ApprovalStoreError:
            raise
        except sqlite3.Error:
            raise ApprovalStoreError("approval rebind failed") from None

    def find_current_for_proposal(
        self,
        actor_id: str,
        proposal_id: str,
        session_id: Optional[str] = None,
    ) -> Optional[ProductivityApproval]:
        """Return the current approval for an actor/proposal.

        Selection is deterministic and active-first: unrevoked approvals are
        ordered ahead of revoked approvals. ``ONCE`` and ``SESSION`` approvals
        are only returned when they belong to the supplied ``session_id``.
        """
        try:
            with self._connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT * FROM approvals
                    WHERE actor_id = ?
                      AND proposal_id = ?
                      AND (
                          (scope IN ('once', 'session') AND session_id IS ?)
                          OR scope IN ('duration', 'precise_persistent')
                      )
                    ORDER BY revoked ASC, issued_at DESC, approval_id ASC
                    LIMIT 1
                    """,
                    (actor_id, proposal_id, session_id),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return self._row_to_approval(row)
        except ApprovalStoreError:
            raise
        except sqlite3.Error:
            raise ApprovalStoreError("approval query failed") from None

    def revoke_all_for_proposal(self, actor_id: str, proposal_id: str) -> int:
        """Revoke every approval matching the actor and proposal.

        Returns the number of approvals revoked. This operation does not load
        approvals into memory and does not disclose cross-session existence.
        """
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE approvals
                    SET revoked = 1
                    WHERE actor_id = ? AND proposal_id = ? AND revoked = 0
                    """,
                    (actor_id, proposal_id),
                )
                return cursor.rowcount
        except sqlite3.Error:
            raise ApprovalStoreError("approval revoke failed") from None

    def revoke_durable_for_proposal(self, actor_id: str, proposal_id: str) -> int:
        """Revoke only durable (duration/precise_persistent) approvals.

        This is used when the in-memory proposal registry is absent and only
        actor-bound approvals can be safely revoked without touching
        session-bound ONCE/SESSION approvals.
        """
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE approvals
                    SET revoked = 1
                    WHERE actor_id = ?
                        AND proposal_id = ?
                        AND revoked = 0
                        AND scope IN ('duration', 'precise_persistent')
                    """,
                    (actor_id, proposal_id),
                )
                return cursor.rowcount
        except sqlite3.Error:
            raise ApprovalStoreError("approval revoke failed") from None

    def list_for_actor(
        self,
        actor_id: str,
        *,
        limit: int,
    ) -> list[ProductivityApproval]:
        """Return at most ``limit`` approvals for an actor.

        ``limit`` must be between 1 and 256. Results are ordered by
        ``issued_at DESC, approval_id ASC``.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 256:
            raise ApprovalStoreError("limit must be between 1 and 256")
        try:
            with self._connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT * FROM approvals
                    WHERE actor_id = ?
                    ORDER BY issued_at DESC, approval_id ASC
                    LIMIT ?
                    """,
                    (actor_id, limit),
                )
                return [self._row_to_approval(row) for row in cursor.fetchall()]
        except ApprovalStoreError:
            raise
        except sqlite3.Error:
            raise ApprovalStoreError("approval list failed") from None
