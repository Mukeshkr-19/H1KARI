"""Private, durable chat sessions for the local HIKARI owner.

The session ledger is deliberately separate from Brain v2.  It preserves a
local transcript so a user can resume an old chat, while Brain v2 remains the
reviewed authority for durable personal facts.  Importing this module performs
no I/O; callers must explicitly construct the store from a private runtime
path.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
from typing import Callable, Optional, Sequence, Tuple


_SESSION_ID = re.compile(r"^chat_[a-z0-9]{24}$")
_CONTROL_OR_FORMAT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|access[_ -]?token|secret[_ -]?key)\s*[:=]\s*"
        r"[^\s,;]{8,}"
    ),
)
_MAX_TITLE_CODEPOINTS = 80
_MAX_TURN_CODEPOINTS = 24_000
_MAX_RESTORE_TURNS = 768
_MAX_LIST_RESULTS = 100
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]{2,}", re.I)
_LOW_INFORMATION = {
    "about", "after", "again", "also", "because", "before", "could",
    "from", "have", "just", "like", "that", "their", "there", "these",
    "they", "this", "those", "what", "when", "where", "which", "with",
    "would", "your",
}


class ConversationSessionError(Exception):
    """Content-free session failure safe for a public error boundary."""


@dataclass(frozen=True)
class ConversationSessionRecord:
    session_id: str
    owner_id: str
    title: str
    archived: bool
    created_at: float
    updated_at: float
    turn_count: int = 0

    def __repr__(self) -> str:
        return (
            "ConversationSessionRecord("
            f"archived={self.archived}, turn_count={self.turn_count})"
        )


@dataclass(frozen=True)
class StoredConversationTurn:
    sequence: int
    user_text: str
    assistant_text: str
    source: str
    created_at: float

    def __repr__(self) -> str:
        return f"StoredConversationTurn(sequence={self.sequence})"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS conversation_turns (
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (session_id, sequence),
    FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id)
        ON DELETE CASCADE
) STRICT;

CREATE INDEX IF NOT EXISTS idx_conversation_sessions_owner_updated
    ON conversation_sessions(owner_id, archived, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_session_sequence
    ON conversation_turns(session_id, sequence DESC);
"""


def _validate_identifier(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ConversationSessionError(f"{name} is invalid")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        raise ConversationSessionError(f"{name} is invalid")
    return value


def validate_session_id(value: object) -> str:
    if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
        raise ConversationSessionError("session id is invalid")
    return value


def _clean_text(value: object, *, limit: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConversationSessionError("conversation text is invalid")
    text = _CONTROL_OR_FORMAT.sub("", value)
    text = "".join(character for character in text if unicodedata.category(character) != "Cf")
    text = text.strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[private credential omitted]", text)
    if (not text and not allow_empty) or len(text) > limit:
        raise ConversationSessionError("conversation text is invalid")
    return text


def validate_title(value: object) -> str:
    return _clean_text(value, limit=_MAX_TITLE_CODEPOINTS)


def title_from_user_text(value: object) -> str:
    text = _clean_text(value, limit=_MAX_TURN_CODEPOINTS)
    title = " ".join(text.split())
    if len(title) > 60:
        title = title[:57].rstrip() + "..."
    return validate_title(title or "New conversation")


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token.casefold()
        for token in _WORD.findall(value)
        if token.casefold() not in _LOW_INFORMATION
    )


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class ConversationSessionStore:
    """SQLite transcript ledger scoped to an exact local owner identifier."""

    def __init__(
        self,
        db_path: Path,
        *,
        session_id_factory: Callable[[], str],
        clock: Callable[[], float] = time.time,
        create_dirs: bool = True,
    ) -> None:
        if not isinstance(db_path, Path):
            raise TypeError("db_path must be a pathlib.Path")
        if not callable(session_id_factory) or not callable(clock):
            raise TypeError("session factories must be callable")
        self._db_path = db_path.expanduser().resolve()
        self._session_id_factory = session_id_factory
        self._clock = clock
        self._lock = threading.RLock()
        try:
            if create_dirs:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self._db_path.parent, 0o700)
            self._init_db()
            os.chmod(self._db_path, 0o600)
        except (OSError, sqlite3.Error):
            raise ConversationSessionError("conversation store unavailable") from None

    def __repr__(self) -> str:
        return "ConversationSessionStore()"

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                str(self._db_path),
                factory=_ClosingConnection,
                timeout=10.0,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except sqlite3.Error:
            raise ConversationSessionError("conversation store unavailable") from None

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    def _now(self) -> float:
        try:
            value = self._clock()
        except Exception:
            raise ConversationSessionError("conversation clock unavailable") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConversationSessionError("conversation clock unavailable")
        result = float(value)
        if not math.isfinite(result):
            raise ConversationSessionError("conversation clock unavailable")
        return result

    def _new_session_id(self) -> str:
        try:
            return validate_session_id(self._session_id_factory())
        except ConversationSessionError:
            raise
        except Exception:
            raise ConversationSessionError("session id generation failed") from None

    @staticmethod
    def _record(row: sqlite3.Row) -> ConversationSessionRecord:
        return ConversationSessionRecord(
            session_id=str(row["session_id"]),
            owner_id=str(row["owner_id"]),
            title=str(row["title"]),
            archived=bool(row["archived"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            turn_count=int(row["turn_count"]),
        )

    def create(self, *, owner_id: str, title: str = "New conversation") -> ConversationSessionRecord:
        owner = _validate_identifier(owner_id, name="owner id")
        bounded_title = validate_title(title)
        now = self._now()
        with self._lock:
            for _attempt in range(4):
                session_id = self._new_session_id()
                try:
                    with self._connect() as connection:
                        connection.execute(
                            "INSERT INTO conversation_sessions "
                            "(session_id, owner_id, title, archived, created_at, updated_at) "
                            "VALUES (?, ?, ?, 0, ?, ?)",
                            (session_id, owner, bounded_title, now, now),
                        )
                        connection.commit()
                    return ConversationSessionRecord(
                        session_id=session_id,
                        owner_id=owner,
                        title=bounded_title,
                        archived=False,
                        created_at=now,
                        updated_at=now,
                    )
                except sqlite3.IntegrityError:
                    continue
                except sqlite3.Error:
                    raise ConversationSessionError("conversation create failed") from None
        raise ConversationSessionError("session id generation failed")

    def get(self, *, owner_id: str, session_id: str) -> Optional[ConversationSessionRecord]:
        owner = _validate_identifier(owner_id, name="owner id")
        session = validate_session_id(session_id)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT s.session_id, s.owner_id, s.title, s.archived, "
                    "s.created_at, s.updated_at, COUNT(t.sequence) AS turn_count "
                    "FROM conversation_sessions s "
                    "LEFT JOIN conversation_turns t ON t.session_id = s.session_id "
                    "WHERE s.owner_id = ? AND s.session_id = ? GROUP BY s.session_id",
                    (owner, session),
                ).fetchone()
        except sqlite3.Error:
            raise ConversationSessionError("conversation lookup failed") from None
        return self._record(row) if row else None

    def latest(self, *, owner_id: str, include_archived: bool = False) -> Optional[ConversationSessionRecord]:
        owner = _validate_identifier(owner_id, name="owner id")
        archived_clause = "" if include_archived else "AND s.archived = 0"
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT s.session_id, s.owner_id, s.title, s.archived, "
                    "s.created_at, s.updated_at, COUNT(t.sequence) AS turn_count "
                    "FROM conversation_sessions s "
                    "LEFT JOIN conversation_turns t ON t.session_id = s.session_id "
                    f"WHERE s.owner_id = ? {archived_clause} GROUP BY s.session_id "
                    "ORDER BY s.updated_at DESC, s.session_id DESC LIMIT 1",
                    (owner,),
                ).fetchone()
        except sqlite3.Error:
            raise ConversationSessionError("conversation lookup failed") from None
        return self._record(row) if row else None

    def list_sessions(
        self,
        *,
        owner_id: str,
        include_archived: bool = False,
        limit: int = 20,
    ) -> Tuple[ConversationSessionRecord, ...]:
        owner = _validate_identifier(owner_id, name="owner id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_LIST_RESULTS:
            raise ConversationSessionError("session list limit is invalid")
        archived_clause = "" if include_archived else "AND s.archived = 0"
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT s.session_id, s.owner_id, s.title, s.archived, "
                    "s.created_at, s.updated_at, COUNT(t.sequence) AS turn_count "
                    "FROM conversation_sessions s "
                    "LEFT JOIN conversation_turns t ON t.session_id = s.session_id "
                    f"WHERE s.owner_id = ? {archived_clause} GROUP BY s.session_id "
                    "ORDER BY s.updated_at DESC, s.session_id DESC LIMIT ?",
                    (owner, limit),
                ).fetchall()
        except sqlite3.Error:
            raise ConversationSessionError("session listing failed") from None
        return tuple(self._record(row) for row in rows)

    def append_turn(
        self,
        *,
        owner_id: str,
        session_id: str,
        user_text: str,
        assistant_text: str,
        source: str,
    ) -> StoredConversationTurn:
        owner = _validate_identifier(owner_id, name="owner id")
        session = validate_session_id(session_id)
        user = _clean_text(user_text, limit=_MAX_TURN_CODEPOINTS)
        assistant = _clean_text(assistant_text, limit=_MAX_TURN_CODEPOINTS)
        channel = _clean_text(source, limit=32)
        now = self._now()
        try:
            with self._lock, self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT title, archived FROM conversation_sessions "
                    "WHERE session_id = ? AND owner_id = ?",
                    (session, owner),
                ).fetchone()
                if row is None or bool(row["archived"]):
                    connection.rollback()
                    raise ConversationSessionError("conversation not found")
                sequence = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM conversation_turns "
                        "WHERE session_id = ?",
                        (session,),
                    ).fetchone()[0]
                )
                connection.execute(
                    "INSERT INTO conversation_turns "
                    "(session_id, sequence, user_text, assistant_text, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (session, sequence, user, assistant, channel, now),
                )
                new_title = (
                    title_from_user_text(user)
                    if row["title"] == "New conversation" and sequence == 1
                    else str(row["title"])
                )
                connection.execute(
                    "UPDATE conversation_sessions SET title = ?, updated_at = ? "
                    "WHERE session_id = ? AND owner_id = ?",
                    (new_title, now, session, owner),
                )
                connection.commit()
        except ConversationSessionError:
            raise
        except sqlite3.Error:
            raise ConversationSessionError("conversation append failed") from None
        return StoredConversationTurn(sequence, user, assistant, channel, now)

    def load_turns(
        self,
        *,
        owner_id: str,
        session_id: str,
        limit: int = _MAX_RESTORE_TURNS,
    ) -> Tuple[StoredConversationTurn, ...]:
        owner = _validate_identifier(owner_id, name="owner id")
        session = validate_session_id(session_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_RESTORE_TURNS:
            raise ConversationSessionError("turn load limit is invalid")
        try:
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM conversation_sessions WHERE session_id = ? AND owner_id = ?",
                    (session, owner),
                ).fetchone()
                if exists is None:
                    return ()
                rows = connection.execute(
                    "SELECT sequence, user_text, assistant_text, source, created_at FROM ("
                    "SELECT sequence, user_text, assistant_text, source, created_at "
                    "FROM conversation_turns WHERE session_id = ? "
                    "ORDER BY sequence DESC LIMIT ?) ORDER BY sequence ASC",
                    (session, limit),
                ).fetchall()
        except sqlite3.Error:
            raise ConversationSessionError("conversation load failed") from None
        return tuple(
            StoredConversationTurn(
                sequence=int(row["sequence"]),
                user_text=str(row["user_text"]),
                assistant_text=str(row["assistant_text"]),
                source=str(row["source"]),
                created_at=float(row["created_at"]),
            )
            for row in rows
        )

    def search_relevant_turns(
        self,
        *,
        owner_id: str,
        session_id: str,
        query: str,
        limit: int = 4,
        exclude_recent: int = _MAX_RESTORE_TURNS,
    ) -> Tuple[StoredConversationTurn, ...]:
        """Find a few lexical matches older than the restored working window."""
        owner = _validate_identifier(owner_id, name="owner id")
        session = validate_session_id(session_id)
        query_tokens = _tokens(_clean_text(query, limit=_MAX_TURN_CODEPOINTS))
        if not query_tokens:
            return ()
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 8
            or isinstance(exclude_recent, bool)
            or not isinstance(exclude_recent, int)
            or not 1 <= exclude_recent <= _MAX_RESTORE_TURNS
        ):
            raise ConversationSessionError("conversation search bounds are invalid")
        try:
            with self._connect() as connection:
                owner_row = connection.execute(
                    "SELECT 1 FROM conversation_sessions WHERE session_id = ? AND owner_id = ?",
                    (session, owner),
                ).fetchone()
                if owner_row is None:
                    return ()
                maximum = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(sequence), 0) FROM conversation_turns "
                        "WHERE session_id = ?",
                        (session,),
                    ).fetchone()[0]
                )
                cutoff = maximum - exclude_recent
                if cutoff <= 0:
                    return ()
                cursor = connection.execute(
                    "SELECT sequence, user_text, assistant_text, source, created_at "
                    "FROM conversation_turns WHERE session_id = ? AND sequence <= ? "
                    "ORDER BY sequence ASC",
                    (session, cutoff),
                )
                scored = []
                for row in cursor:
                    overlap = len(
                        query_tokens
                        & _tokens(f"{row['user_text']} {row['assistant_text']}")
                    )
                    if overlap:
                        scored.append((overlap, int(row["sequence"]), row))
                selected = sorted(
                    sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[:limit],
                    key=lambda item: item[1],
                )
        except sqlite3.Error:
            raise ConversationSessionError("conversation search failed") from None
        return tuple(
            StoredConversationTurn(
                sequence=int(row["sequence"]),
                user_text=str(row["user_text"]),
                assistant_text=str(row["assistant_text"]),
                source=str(row["source"]),
                created_at=float(row["created_at"]),
            )
            for _overlap, _sequence, row in selected
        )

    def rename(self, *, owner_id: str, session_id: str, title: str) -> bool:
        return self._update_session(
            owner_id=owner_id,
            session_id=session_id,
            title=validate_title(title),
        )

    def archive(self, *, owner_id: str, session_id: str) -> bool:
        return self._update_session(owner_id=owner_id, session_id=session_id, archived=1)

    def unarchive(self, *, owner_id: str, session_id: str) -> bool:
        return self._update_session(owner_id=owner_id, session_id=session_id, archived=0)

    def _update_session(
        self,
        *,
        owner_id: str,
        session_id: str,
        title: Optional[str] = None,
        archived: Optional[int] = None,
    ) -> bool:
        owner = _validate_identifier(owner_id, name="owner id")
        session = validate_session_id(session_id)
        now = self._now()
        assignments = []
        values: list[object] = []
        if title is not None:
            assignments.append("title = ?")
            values.append(title)
        if archived is not None:
            assignments.append("archived = ?")
            values.append(archived)
        assignments.append("updated_at = ?")
        values.append(now)
        values.extend((session, owner))
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    f"UPDATE conversation_sessions SET {', '.join(assignments)} "
                    "WHERE session_id = ? AND owner_id = ?",
                    values,
                )
                connection.commit()
                return cursor.rowcount == 1
        except sqlite3.Error:
            raise ConversationSessionError("conversation update failed") from None

    def delete(self, *, owner_id: str, session_id: str) -> bool:
        owner = _validate_identifier(owner_id, name="owner id")
        session = validate_session_id(session_id)
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM conversation_sessions WHERE session_id = ? AND owner_id = ?",
                    (session, owner),
                )
                connection.commit()
                return cursor.rowcount == 1
        except sqlite3.Error:
            raise ConversationSessionError("conversation deletion failed") from None


def create_conversation_session_store(
    *,
    db_path: Optional[Path] = None,
    session_id_factory: Optional[Callable[[], str]] = None,
    clock: Callable[[], float] = time.time,
) -> ConversationSessionStore:
    """Create the private session store only when an interactive runtime asks."""
    import secrets

    from core.runtime_paths import hikari_home

    resolved = db_path or (hikari_home() / "conversations" / "sessions.db")
    factory = session_id_factory or (lambda: f"chat_{secrets.token_hex(12)}")
    return ConversationSessionStore(
        Path(resolved),
        session_id_factory=factory,
        clock=clock,
    )


def hydrate_session_turns(
    turns: Sequence[StoredConversationTurn],
) -> Tuple[Tuple[str, str], ...]:
    """Return bounded user/assistant pairs for the in-memory context engine."""
    return tuple((turn.user_text, turn.assistant_text) for turn in turns[-_MAX_RESTORE_TURNS:])
