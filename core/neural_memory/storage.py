"""SQLite storage layer for Hikari Neural Memory."""

import sqlite3
import json
import logging
import re
from pathlib import Path
from typing import Optional, List, Tuple, Any
from contextlib import contextmanager

from .config import config
from .models import MemoryNode, MemoryEdge, Episode, Session

logger = logging.getLogger(__name__)


class MemoryStorage:
    _instance: Optional["MemoryStorage"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._conn: Optional[sqlite3.Connection] = None
        config.ensure_directories()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(config.DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self):
        schema_path = config.SCHEMA_PATH
        if not schema_path.exists():
            logger.error(f"Schema file not found: {schema_path}")
            return False

        with open(schema_path) as f:
            schema = f.read()

        with self.get_connection() as conn:
            conn.executescript(schema)
            conn.commit()
            logger.info(f"Memory database initialized at {config.DB_PATH}")
        return True

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.get_connection() as conn:
            return conn.execute(query, params)

    def execute_many(self, query: str, params_list: List[tuple]):
        with self.get_connection() as conn:
            conn.executemany(query, params_list)
            conn.commit()

    def fetch_one(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self.get_connection() as conn:
            return conn.execute(query, params).fetchone()

    def fetch_all(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self.get_connection() as conn:
            return conn.execute(query, params).fetchall()

    def insert_node(self, node: MemoryNode) -> int:
        query = """
            INSERT INTO nodes (node_type, name, alias, content, metadata, salience,
                             activation_count, last_accessed, created_at, updated_at,
                             user_id, is_archived, is_pinned)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, node.to_db_tuple())
            conn.commit()
            return cursor.lastrowid

    def upsert_node(self, node: MemoryNode) -> int:
        query = """
            INSERT INTO nodes (node_type, name, alias, content, metadata, salience,
                             activation_count, last_accessed, created_at, updated_at,
                             user_id, is_archived, is_pinned)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_type, name, user_id) DO UPDATE SET
                alias=excluded.alias,
                content=excluded.content,
                metadata=excluded.metadata,
                salience=excluded.salience,
                activation_count=excluded.activation_count,
                last_accessed=excluded.last_accessed,
                updated_at=excluded.updated_at,
                is_archived=excluded.is_archived,
                is_pinned=excluded.is_pinned
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, node.to_db_tuple())
            conn.commit()
            return cursor.lastrowid or node.id

    def get_node_by_id(self, node_id: int) -> Optional[MemoryNode]:
        row = self.fetch_one(
            "SELECT * FROM nodes WHERE id = ? AND is_archived = 0", (node_id,)
        )
        return MemoryNode.from_db_row(dict(row)) if row else None

    def get_node_by_name(
        self, name: str, node_type: Optional[str] = None, user_id: str = None
    ) -> Optional[MemoryNode]:
        user_id = user_id or config.user_id
        if node_type:
            row = self.fetch_one(
                "SELECT * FROM nodes WHERE name = ? AND node_type = ? AND user_id = ? AND is_archived = 0",
                (name, node_type, user_id),
            )
        else:
            row = self.fetch_one(
                "SELECT * FROM nodes WHERE name = ? AND user_id = ? AND is_archived = 0",
                (name, user_id),
            )
        return MemoryNode.from_db_row(dict(row)) if row else None

    def get_nodes_by_type(
        self, node_type: str, user_id: str = None, limit: int = 100
    ) -> List[MemoryNode]:
        user_id = user_id or config.user_id
        rows = self.fetch_all(
            "SELECT * FROM nodes WHERE node_type = ? AND user_id = ? AND is_archived = 0 ORDER BY salience DESC LIMIT ?",
            (node_type, user_id, limit),
        )
        return [MemoryNode.from_db_row(dict(row)) for row in rows]

    def get_recent_nodes(
        self, user_id: str = None, limit: int = 50
    ) -> List[MemoryNode]:
        user_id = user_id or config.user_id
        rows = self.fetch_all(
            "SELECT * FROM nodes WHERE user_id = ? AND is_archived = 0 ORDER BY last_accessed DESC LIMIT ?",
            (user_id, limit),
        )
        return [MemoryNode.from_db_row(dict(row)) for row in rows]

    def update_node_access(self, node_id: int):
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE nodes SET last_accessed = datetime('now'), activation_count = activation_count + 1 WHERE id = ?",
                (node_id,),
            )
            conn.commit()

    def update_node_salience(self, node_id: int, salience: float):
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE nodes SET salience = ?, updated_at = datetime('now') WHERE id = ?",
                (salience, node_id),
            )
            conn.commit()

    def insert_edge(self, edge: MemoryEdge) -> int:
        query = """
            INSERT OR IGNORE INTO edges (source_id, target_id, edge_type, weight, context,
                                        bidirectional, last_accessed, created_at, user_id, is_archived)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, edge.to_db_tuple())
            conn.commit()
            return cursor.lastrowid

    def upsert_edge(self, edge: MemoryEdge) -> int:
        query = """
            INSERT INTO edges (source_id, target_id, edge_type, weight, context,
                              bidirectional, last_accessed, created_at, user_id, is_archived)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, target_id, edge_type, user_id) DO UPDATE SET
                weight=excluded.weight,
                context=excluded.context,
                bidirectional=excluded.bidirectional,
                last_accessed=excluded.last_accessed,
                is_archived=excluded.is_archived
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, edge.to_db_tuple())
            conn.commit()
            return cursor.lastrowid or edge.id

    def get_edges_for_node(self, node_id: int, user_id: str = None) -> List[MemoryEdge]:
        user_id = user_id or config.user_id
        rows = self.fetch_all(
            """SELECT * FROM edges WHERE
               (source_id = ? OR target_id = ?) AND user_id = ? AND is_archived = 0
               ORDER BY weight DESC""",
            (node_id, node_id, user_id),
        )
        return [MemoryEdge.from_db_row(dict(row)) for row in rows]

    def get_neighbors(
        self, node_id: int, edge_type: Optional[str] = None, user_id: str = None
    ) -> List[MemoryNode]:
        user_id = user_id or config.user_id
        if edge_type:
            rows = self.fetch_all(
                """SELECT n.* FROM nodes n
                   JOIN edges e ON (n.id = e.target_id AND e.source_id = ?)
                   WHERE e.edge_type = ? AND e.user_id = ? AND n.is_archived = 0
                   UNION
                   SELECT n.* FROM nodes n
                   JOIN edges e ON (n.id = e.source_id AND e.target_id = ?)
                   WHERE e.edge_type = ? AND e.user_id = ? AND n.is_archived = 0""",
                (node_id, edge_type, user_id, node_id, edge_type, user_id),
            )
        else:
            rows = self.fetch_all(
                """SELECT n.* FROM nodes n
                   JOIN edges e ON (n.id = e.target_id AND e.source_id = ?)
                   WHERE e.user_id = ? AND n.is_archived = 0
                   UNION
                   SELECT n.* FROM nodes n
                   JOIN edges e ON (n.id = e.source_id AND e.target_id = ?)
                   WHERE e.user_id = ? AND n.is_archived = 0""",
                (node_id, user_id, node_id, user_id),
            )
        return [MemoryNode.from_db_row(dict(row)) for row in rows]

    def search_nodes_fts(
        self, query: str, user_id: str = None, limit: int = 20
    ) -> List[MemoryNode]:
        user_id = user_id or config.user_id
        terms = re.findall(r"[A-Za-z0-9_]{2,}", query or "")
        if not terms:
            return []
        fts_query = " OR ".join(terms[:12])
        rows = self.fetch_all(
            """SELECT n.* FROM nodes n
               JOIN nodes_fts fts ON n.id = fts.rowid
               WHERE nodes_fts MATCH ? AND n.user_id = ?
               ORDER BY rank
               LIMIT ?""",
            (fts_query, user_id, limit),
        )
        return [MemoryNode.from_db_row(dict(row)) for row in rows]

    def insert_episode(self, episode: Episode) -> int:
        query = """
            INSERT INTO episodes (episode_type, title, summary, content, nodes, sentiment,
                                importance, outcome, session_id, turn_count, duration_seconds,
                                started_at, ended_at, user_id, is_archived)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self.get_connection() as conn:
            cursor = conn.execute(query, episode.to_db_tuple())
            conn.commit()
            return cursor.lastrowid

    def get_recent_episodes(
        self, user_id: str = None, limit: int = 10
    ) -> List[Episode]:
        user_id = user_id or config.user_id
        rows = self.fetch_all(
            "SELECT * FROM episodes WHERE user_id = ? AND is_archived = 0 ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [Episode.from_db_row(dict(row)) for row in rows]

    def update_episode(self, episode_id: int, **kwargs):
        valid_fields = [
            "summary",
            "content",
            "sentiment",
            "importance",
            "outcome",
            "turn_count",
            "duration_seconds",
            "ended_at",
            "is_archived",
        ]
        updates = {k: v for k, v in kwargs.items() if k in valid_fields}
        if not updates:
            return
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [episode_id]
        with self.get_connection() as conn:
            conn.execute(f"UPDATE episodes SET {set_clause} WHERE id = ?", values)
            conn.commit()

    def insert_session(self, session: Session) -> int:
        query = """
            INSERT INTO sessions (session_id, session_type, started_at, ended_at, turn_count, user_id, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                query,
                (
                    session.session_id,
                    session.session_type,
                    session.started_at,
                    session.ended_at,
                    session.turn_count,
                    session.user_id,
                    json.dumps(session.metadata),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_active_session(self, user_id: str = None) -> Optional[Session]:
        user_id = user_id or config.user_id
        row = self.fetch_one(
            "SELECT * FROM sessions WHERE user_id = ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (user_id,),
        )
        if row:
            row_dict = dict(row)
            if row_dict.get("metadata"):
                row_dict["metadata"] = json.loads(row_dict["metadata"])
            return Session(**row_dict)
        return None

    def end_session(self, session_id: str, metadata: dict = None):
        with self.get_connection() as conn:
            meta = json.dumps(metadata) if metadata else None
            conn.execute(
                "UPDATE sessions SET ended_at = datetime('now'), metadata = COALESCE(?, metadata) WHERE session_id = ?",
                (meta, session_id),
            )
            conn.commit()

    def increment_session_turns(self, session_id: str):
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET turn_count = turn_count + 1 WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()

    def get_user_profile(self, user_id: str = None) -> dict:
        user_id = user_id or config.user_id
        rows = self.fetch_all("SELECT key, value FROM user_profile", ())
        return {row["key"]: row["value"] for row in rows}

    def set_user_profile(self, key: str, value: str):
        with self.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_profile (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                (key, value),
            )
            conn.commit()

    def get_stats(self) -> dict:
        node_count = self.fetch_one(
            "SELECT COUNT(*) as c FROM nodes WHERE is_archived = 0"
        )[0]
        edge_count = self.fetch_one(
            "SELECT COUNT(*) as c FROM edges WHERE is_archived = 0"
        )[0]
        episode_count = self.fetch_one(
            "SELECT COUNT(*) as c FROM episodes WHERE is_archived = 0"
        )[0]
        return {"nodes": node_count, "edges": edge_count, "episodes": episode_count}

    def archive_stale_nodes(self, days: int = 30, min_salience: float = 0.1):
        with self.get_connection() as conn:
            cursor = conn.execute(
                """UPDATE nodes SET is_archived = 1, updated_at = datetime('now')
                   WHERE is_archived = 0 AND salience < ?
                   AND datetime(last_accessed) < datetime('now', ?)""",
                (min_salience, f"-{days} days"),
            )
            conn.commit()
            return cursor.rowcount

    def vacuum(self):
        with self.get_connection() as conn:
            conn.execute("VACUUM")
            conn.commit()


storage = MemoryStorage()
