"""Local episode store — raw segments and structured episodes stay separate."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from core.path_literals import EPISODES_DB

from core.brain_v2.memory_lifecycle import (
    CORRECTION_SOURCE_OPERATOR,
    LIFECYCLE_ACTIVE,
    LIFECYCLE_SUPERSEDED,
    append_audit_entry,
    filter_active_memories,
    is_active_memory,
    lifecycle_status,
)
from core.brain_v2.memory_type import infer_memory_type
from core.brain_v2.schemas import (
    EpisodeLifecycleState,
    MemoryCandidate,
    MemoryCandidateStatus,
    SourceLinkedMemory,
    StructuredEpisode,
    TranscriptSegment,
    _utc_now,
    dumps_json,
    loads_json,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_episodes (
    episode_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'in_progress',
    user_id TEXT DEFAULT 'local_user',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    segment_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES raw_episodes(episode_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    text TEXT NOT NULL,
    is_user INTEGER NOT NULL DEFAULT 1,
    speaker_label TEXT DEFAULT 'user',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_segments_episode ON transcript_segments(episode_id, sequence);

CREATE TABLE IF NOT EXISTS structured_episodes (
    episode_id TEXT PRIMARY KEY REFERENCES raw_episodes(episode_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    title TEXT,
    summary TEXT,
    action_items TEXT,
    events TEXT,
    segment_count INTEGER DEFAULT 0,
    started_at TEXT,
    ended_at TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES raw_episodes(episode_id) ON DELETE CASCADE,
    statement TEXT NOT NULL,
    candidate_type TEXT DEFAULT 'fact',
    confidence REAL DEFAULT 0.5,
    salience REAL DEFAULT 0.5,
    review_status TEXT DEFAULT 'pending',
    source_segment_ids TEXT,
    created_at TEXT NOT NULL,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidates_episode ON memory_candidates(episode_id);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON memory_candidates(review_status);

CREATE TABLE IF NOT EXISTS source_linked_memories (
    memory_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES memory_candidates(candidate_id),
    episode_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    source_segment_ids TEXT,
    neural_node_key TEXT,
    accepted_at TEXT NOT NULL,
    layer TEXT DEFAULT 'semantic',
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_slm_episode ON source_linked_memories(episode_id);
"""


class EpisodeStore:
    """Persists raw episodes, transcript segments, structured summaries, and candidates."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        create_dirs: bool = True,
        readonly: bool = False,
    ):
        if db_path is None:
            from core.brain_v2.db_paths import resolve_episodes_db_path

            db_path = resolve_episodes_db_path()
        self.db_path = Path(db_path)
        self.readonly = readonly
        if readonly:
            if not self.db_path.is_file():
                raise FileNotFoundError(f"Brain v2 database not found at {self.db_path}")
            return
        if create_dirs:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self.readonly:
            uri = f"file:{self.db_path.resolve()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        else:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def create_episode(
        self,
        session_id: str,
        user_id: str = "local_user",
        metadata: Optional[dict] = None,
    ) -> str:
        episode_id = str(uuid.uuid4())
        started_at = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO raw_episodes (episode_id, session_id, lifecycle_state, user_id, started_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    session_id,
                    EpisodeLifecycleState.IN_PROGRESS.value,
                    user_id,
                    started_at,
                    dumps_json(metadata or {}),
                ),
            )
        return episode_id

    def append_segment(self, segment: TranscriptSegment) -> TranscriptSegment:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO transcript_segments
                (segment_id, episode_id, sequence, text, is_user, speaker_label, started_at, ended_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment.segment_id,
                    segment.episode_id,
                    segment.sequence,
                    segment.text,
                    int(segment.is_user),
                    segment.speaker_label,
                    segment.started_at,
                    segment.ended_at,
                    dumps_json(segment.metadata),
                ),
            )
        return segment

    def next_sequence(self, episode_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), -1) + 1 AS n FROM transcript_segments WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def add_turn(
        self,
        episode_id: str,
        text: str,
        *,
        is_user: bool = True,
        speaker_label: str = "user",
        metadata: Optional[dict] = None,
    ) -> TranscriptSegment:
        segment = TranscriptSegment(
            segment_id=str(uuid.uuid4()),
            episode_id=episode_id,
            sequence=self.next_sequence(episode_id),
            text=text.strip(),
            is_user=is_user,
            speaker_label=speaker_label,
            metadata=metadata or {},
        )
        return self.append_segment(segment)

    def set_lifecycle(self, episode_id: str, state: EpisodeLifecycleState) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE raw_episodes SET lifecycle_state = ? WHERE episode_id = ?",
                (state.value, episode_id),
            )

    def mark_episode_ended(self, episode_id: str) -> None:
        ended = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE raw_episodes SET ended_at = ?, lifecycle_state = ? WHERE episode_id = ?",
                (ended, EpisodeLifecycleState.PROCESSING.value, episode_id),
            )

    def save_structured_episode(self, structured: StructuredEpisode) -> StructuredEpisode:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO structured_episodes
                (episode_id, session_id, lifecycle_state, title, summary, action_items, events,
                 segment_count, started_at, ended_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    structured.episode_id,
                    structured.session_id,
                    structured.lifecycle_state,
                    structured.title,
                    structured.summary,
                    dumps_json(structured.action_items),
                    dumps_json(structured.events),
                    structured.segment_count,
                    structured.started_at,
                    structured.ended_at,
                    dumps_json(structured.metadata),
                ),
            )
            conn.execute(
                "UPDATE raw_episodes SET lifecycle_state = ? WHERE episode_id = ?",
                (structured.lifecycle_state, structured.episode_id),
            )
        return structured

    def save_candidates(self, candidates: List[MemoryCandidate]) -> List[MemoryCandidate]:
        with self._connect() as conn:
            for cand in candidates:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_candidates
                    (candidate_id, episode_id, statement, candidate_type, confidence, salience,
                     review_status, source_segment_ids, created_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cand.candidate_id,
                        cand.episode_id,
                        cand.statement,
                        cand.candidate_type,
                        cand.confidence,
                        cand.salience,
                        cand.review_status,
                        dumps_json(cand.source_segment_ids),
                        cand.created_at,
                        dumps_json(cand.metadata),
                    ),
                )
        return candidates

    def update_candidate_status(self, candidate_id: str, status: MemoryCandidateStatus) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE memory_candidates SET review_status = ? WHERE candidate_id = ?",
                (status.value, candidate_id),
            )

    def save_source_linked_memory(self, memory: SourceLinkedMemory) -> SourceLinkedMemory:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO source_linked_memories
                (memory_id, candidate_id, episode_id, statement, source_segment_ids,
                 neural_node_key, accepted_at, layer, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.memory_id,
                    memory.candidate_id,
                    memory.episode_id,
                    memory.statement,
                    dumps_json(memory.source_segment_ids),
                    memory.neural_node_key,
                    memory.accepted_at,
                    memory.layer,
                    dumps_json(memory.metadata),
                ),
            )
        return memory

    def get_raw_segments(self, episode_id: str) -> List[TranscriptSegment]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM transcript_segments WHERE episode_id = ?
                ORDER BY sequence ASC
                """,
                (episode_id,),
            ).fetchall()
        return [self._row_to_segment(r) for r in rows]

    def get_structured_episode(self, episode_id: str) -> Optional[StructuredEpisode]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM structured_episodes WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
        return self._row_to_structured(row) if row else None

    def list_unconsolidated_episode_ids(self, *, min_segments: int = 2) -> List[str]:
        """Raw episodes with transcript segments but no structured_episodes row."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT re.episode_id
                FROM raw_episodes re
                LEFT JOIN structured_episodes se ON se.episode_id = re.episode_id
                INNER JOIN transcript_segments ts ON ts.episode_id = re.episode_id
                WHERE se.episode_id IS NULL
                GROUP BY re.episode_id
                HAVING COUNT(ts.segment_id) >= ?
                ORDER BY re.started_at ASC
                """,
                (min_segments,),
            ).fetchall()
        return [str(r["episode_id"]) for r in rows]

    def get_candidate(self, candidate_id: str) -> Optional[MemoryCandidate]:
        """Resolve by full id or unique prefix."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row:
                return self._row_to_candidate(row)
            rows = conn.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id LIKE ?",
                (f"{candidate_id}%",),
            ).fetchall()
        if len(rows) == 1:
            return self._row_to_candidate(rows[0])
        return None

    def update_candidate_metadata(self, candidate_id: str, metadata: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE memory_candidates SET metadata = ? WHERE candidate_id = ?",
                (dumps_json(metadata), candidate_id),
            )

    def get_candidates(
        self,
        episode_id: Optional[str] = None,
        status: Optional[MemoryCandidateStatus] = None,
    ) -> List[MemoryCandidate]:
        query = "SELECT * FROM memory_candidates WHERE 1=1"
        params: list = []
        if episode_id:
            query += " AND episode_id = ?"
            params.append(episode_id)
        if status:
            query += " AND review_status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        items = [self._row_to_candidate(r) for r in rows]
        items.sort(
            key=lambda c: float((c.metadata or {}).get("rank_score", 0)),
            reverse=True,
        )
        return items

    def get_accepted_memories(
        self, *, episode_id: Optional[str] = None, limit: int = 50
    ) -> List[SourceLinkedMemory]:
        query = "SELECT * FROM source_linked_memories"
        params: list = []
        if episode_id:
            query += " WHERE episode_id = ?"
            params.append(episode_id)
        query += " ORDER BY accepted_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_linked(r) for r in rows]

    def get_active_accepted_memories(
        self, *, episode_id: Optional[str] = None, limit: int = 50
    ) -> List[SourceLinkedMemory]:
        """Return up to ``limit`` active rows; missing lifecycle status counts as active."""
        clause = (
            "COALESCE(json_extract(metadata, '$.lifecycle_status'), 'active') = 'active'"
        )
        query = f"SELECT * FROM source_linked_memories WHERE {clause}"
        params: list = []
        if episode_id:
            query += " AND episode_id = ?"
            params.append(episode_id)
        query += " ORDER BY accepted_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_linked(r) for r in rows]

    def get_episode_ids_with_inactive_accepted_memory(self) -> set:
        """Episode IDs linked to any retired/superseded accepted memory."""
        inactive: set = set()
        offset = 0
        batch = 200
        while True:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT episode_id, metadata FROM source_linked_memories
                    ORDER BY accepted_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (batch, offset),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                meta = loads_json(row["metadata"], {})
                if lifecycle_status(meta) != LIFECYCLE_ACTIVE:
                    inactive.add(str(row["episode_id"]))
            if len(rows) < batch:
                break
            offset += batch
        return inactive

    def resolve_source_linked_memory_id(self, memory_id: str) -> str:
        """Resolve full memory id or unique prefix; raises if missing or ambiguous."""
        resolved = self.get_source_linked_memory(memory_id)
        if resolved:
            return resolved.memory_id
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT memory_id FROM source_linked_memories WHERE memory_id LIKE ?",
                (f"{memory_id}%",),
            ).fetchall()
        if not rows:
            raise KeyError(f"Accepted memory not found: {memory_id}")
        if len(rows) > 1:
            raise ValueError(
                f"Ambiguous memory id prefix {memory_id!r} ({len(rows)} matches)."
            )
        return str(rows[0]["memory_id"])

    def has_active_successor(self, memory_id: str) -> bool:
        target = memory_id
        for mem in self.get_accepted_memories(limit=500):
            if not is_active_memory(mem):
                continue
            meta = mem.metadata or {}
            if meta.get("supersedes") == target or meta.get("superseded_from") == target:
                return True
        return False

    def _persist_candidate_conn(self, conn: sqlite3.Connection, cand: MemoryCandidate) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO memory_candidates
            (candidate_id, episode_id, statement, candidate_type, confidence, salience,
             review_status, source_segment_ids, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cand.candidate_id,
                cand.episode_id,
                cand.statement,
                cand.candidate_type,
                cand.confidence,
                cand.salience,
                cand.review_status,
                dumps_json(cand.source_segment_ids),
                cand.created_at,
                dumps_json(cand.metadata),
            ),
        )

    def _persist_source_linked_conn(
        self, conn: sqlite3.Connection, memory: SourceLinkedMemory
    ) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO source_linked_memories
            (memory_id, candidate_id, episode_id, statement, source_segment_ids,
             neural_node_key, accepted_at, layer, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.memory_id,
                memory.candidate_id,
                memory.episode_id,
                memory.statement,
                dumps_json(memory.source_segment_ids),
                memory.neural_node_key,
                memory.accepted_at,
                memory.layer,
                dumps_json(memory.metadata),
            ),
        )

    def atomic_supersede_accepted_memory(
        self,
        memory_id: str,
        *,
        new_statement: str,
        candidate_type: Optional[str] = None,
        layer: Optional[str] = None,
        reason: str = "superseded_by_operator",
    ) -> Tuple[SourceLinkedMemory, SourceLinkedMemory]:
        """Single-transaction supersede; rolls back entirely on failure."""
        new_statement = (new_statement or "").strip()
        if not new_statement:
            raise ValueError("Supersede requires a non-empty statement.")

        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM source_linked_memories WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()
                if not row:
                    prefix_rows = conn.execute(
                        "SELECT memory_id FROM source_linked_memories WHERE memory_id LIKE ?",
                        (f"{memory_id}%",),
                    ).fetchall()
                    if not prefix_rows:
                        raise KeyError(f"Accepted memory not found: {memory_id}")
                    if len(prefix_rows) > 1:
                        raise ValueError(
                            f"Ambiguous memory id prefix {memory_id!r} ({len(prefix_rows)} matches)."
                        )
                    row = conn.execute(
                        "SELECT * FROM source_linked_memories WHERE memory_id = ?",
                        (str(prefix_rows[0]["memory_id"]),),
                    ).fetchone()
                if not row:
                    raise KeyError(f"Accepted memory not found: {memory_id}")
                old = self._row_to_linked(row)
                old_meta_raw = loads_json(row["metadata"], {})
                if lifecycle_status(old_meta_raw) != LIFECYCLE_ACTIVE:
                    raise ValueError(
                        f"Memory {memory_id} is not active "
                        f"(status={lifecycle_status(old_meta_raw)})."
                    )
                for other in conn.execute(
                    "SELECT memory_id, metadata FROM source_linked_memories"
                ).fetchall():
                    ometa = loads_json(other["metadata"], {})
                    if lifecycle_status(ometa) != LIFECYCLE_ACTIVE:
                        continue
                    if ometa.get("supersedes") == memory_id or ometa.get(
                        "superseded_from"
                    ) == memory_id:
                        raise ValueError(
                            f"Memory {memory_id} already has an active successor."
                        )

                old_meta = append_audit_entry(
                    dict(old.metadata or {}),
                    "supersede",
                    reason=reason,
                    prior_statement=old.statement,
                )
                old_meta["lifecycle_status"] = LIFECYCLE_SUPERSEDED
                old_meta["superseded_at"] = old_meta["correction_audit"][-1]["at"]
                new_id = str(uuid.uuid4())
                old_meta["superseded_by"] = new_id
                predecessor_segments = list(old.source_segment_ids or [])
                retired_old = SourceLinkedMemory(
                    memory_id=old.memory_id,
                    candidate_id=old.candidate_id,
                    episode_id=old.episode_id,
                    statement=old.statement,
                    source_segment_ids=predecessor_segments,
                    neural_node_key=old.neural_node_key,
                    accepted_at=old.accepted_at,
                    layer=old.layer,
                    metadata=old_meta,
                )
                self._persist_source_linked_conn(conn, retired_old)

                inferred = infer_memory_type(new_statement)
                ctype = (
                    candidate_type
                    or (old.metadata or {}).get("candidate_type")
                    or inferred.candidate_type
                )
                new_meta: dict = {
                    "lifecycle_status": LIFECYCLE_ACTIVE,
                    "candidate_type": ctype,
                    "correction_source": CORRECTION_SOURCE_OPERATOR,
                    "supersedes": old.memory_id,
                    "superseded_from": old.memory_id,
                    "predecessor_evidence_segment_ids": predecessor_segments,
                    "correction_audit": [
                        {
                            "action": "supersede_create",
                            "at": old_meta["superseded_at"],
                            "reason": reason,
                            "replaces_memory_id": old.memory_id,
                        }
                    ],
                }
                for key, val in (inferred.metadata or {}).items():
                    if val:
                        new_meta[key] = val
                for key in (
                    "person",
                    "relation",
                    "organization",
                    "location",
                    "place",
                    "date_text",
                ):
                    if key not in new_meta and (old.metadata or {}).get(key):
                        new_meta[key] = (old.metadata or {}).get(key)

                correction_candidate = MemoryCandidate(
                    candidate_id=str(uuid.uuid4()),
                    episode_id=old.episode_id,
                    statement=new_statement,
                    candidate_type=ctype,
                    review_status=MemoryCandidateStatus.ACCEPTED.value,
                    source_segment_ids=[],
                    metadata={
                        "corrects_candidate_id": old.candidate_id,
                        "corrects_memory_id": old.memory_id,
                        "correction_reason": reason,
                        "correction_source": CORRECTION_SOURCE_OPERATOR,
                        "predecessor_evidence_segment_ids": predecessor_segments,
                    },
                )
                self._persist_candidate_conn(conn, correction_candidate)

                replacement = SourceLinkedMemory(
                    memory_id=new_id,
                    candidate_id=correction_candidate.candidate_id,
                    episode_id=old.episode_id,
                    statement=new_statement,
                    source_segment_ids=[],
                    neural_node_key=None,
                    accepted_at=old_meta["superseded_at"],
                    layer=layer or old.layer,
                    metadata=new_meta,
                )
                self._persist_source_linked_conn(conn, replacement)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return retired_old, replacement

    def get_source_linked_memory(self, memory_id: str) -> Optional[SourceLinkedMemory]:
        """Resolve by full id or unique prefix."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_linked_memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if row:
                return self._row_to_linked(row)
            rows = conn.execute(
                "SELECT * FROM source_linked_memories WHERE memory_id LIKE ?",
                (f"{memory_id}%",),
            ).fetchall()
        if len(rows) == 1:
            return self._row_to_linked(rows[0])
        return None

    def count_raw_segments(self, episode_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM transcript_segments WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
        return int(row["c"]) if row else 0

    def _row_to_segment(self, row: sqlite3.Row) -> TranscriptSegment:
        return TranscriptSegment(
            segment_id=row["segment_id"],
            episode_id=row["episode_id"],
            sequence=row["sequence"],
            text=row["text"],
            is_user=bool(row["is_user"]),
            speaker_label=row["speaker_label"] or "user",
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            metadata=loads_json(row["metadata"], {}),
        )

    def _row_to_structured(self, row: sqlite3.Row) -> StructuredEpisode:
        return StructuredEpisode(
            episode_id=row["episode_id"],
            session_id=row["session_id"],
            lifecycle_state=row["lifecycle_state"],
            title=row["title"] or "",
            summary=row["summary"] or "",
            action_items=loads_json(row["action_items"], []),
            events=loads_json(row["events"], []),
            segment_count=row["segment_count"] or 0,
            started_at=row["started_at"] or "",
            ended_at=row["ended_at"],
            metadata=loads_json(row["metadata"], {}),
        )

    def _row_to_candidate(self, row: sqlite3.Row) -> MemoryCandidate:
        return MemoryCandidate(
            candidate_id=row["candidate_id"],
            episode_id=row["episode_id"],
            statement=row["statement"],
            candidate_type=row["candidate_type"] or "fact",
            confidence=float(row["confidence"] or 0.5),
            salience=float(row["salience"] or 0.5),
            review_status=row["review_status"] or MemoryCandidateStatus.PENDING.value,
            source_segment_ids=loads_json(row["source_segment_ids"], []),
            created_at=row["created_at"],
            metadata=loads_json(row["metadata"], {}),
        )

    def _row_to_linked(self, row: sqlite3.Row) -> SourceLinkedMemory:
        return SourceLinkedMemory(
            memory_id=row["memory_id"],
            candidate_id=row["candidate_id"],
            episode_id=row["episode_id"],
            statement=row["statement"],
            source_segment_ids=loads_json(row["source_segment_ids"], []),
            neural_node_key=row["neural_node_key"],
            accepted_at=row["accepted_at"],
            layer=row["layer"] or "semantic",
            metadata=loads_json(row["metadata"], {}),
        )
