"""Multi-level consolidation for Hikari Neural Memory."""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .storage import storage
from .config import config
from .models import Episode, NodeType
from .sqlite_rows import row_as_dict

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return legacy naive UTC datetime without deprecated utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ConsolidationEngine:
    def __init__(self):
        self.storage = storage
        self.turn_count = 0
        self.last_bounded = 0
        self.last_daily = None
        self.daily_interval = timedelta(hours=24)

    def micro_update(self, session_id: Optional[str] = None):
        """Per-turn: Update access timestamps and reinforce recent edges."""
        self.turn_count += 1

        if session_id:
            self.storage.increment_session_turns(session_id)

        if self.turn_count % 5 == 0:
            recent = self.storage.get_recent_nodes(limit=10)
            for node in recent:
                self.storage.update_node_access(node.id)

    def bounded_maintenance(self):
        """Every 10 turns: Batch updates, neighborhood dedup."""
        if self.turn_count - self.last_bounded < 10:
            return {"status": "skipped", "reason": "not_yet_due"}

        self.last_bounded = self.turn_count
        result = {
            "status": "completed",
            "turn_count": self.turn_count,
            "nodes_processed": 0,
            "edges_processed": 0,
        }

        try:
            recent_nodes = self.storage.get_recent_nodes(limit=100)

            for node in recent_nodes:
                self.storage.update_node_access(node.id)
                edges = self.storage.get_edges_for_node(node.id)
                result["edges_processed"] += len(edges)

            result["nodes_processed"] = len(recent_nodes)

            logger.info(f"Bounded consolidation completed: {result}")
            return result

        except Exception as e:
            logger.error(f"Bounded consolidation failed: {e}")
            return {"status": "failed", "error": str(e)}

    def session_compaction(self, session_id: str, summary: Optional[str] = None):
        """Session-end: Summarize episode, update salience."""
        try:
            row = self.storage.fetch_one(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            )

            if not row:
                return {"status": "skipped", "reason": "session_not_found"}

            session = row_as_dict(row)
            episode = Episode(
                episode_type="CONVERSATION",
                title=f"Session {session_id[:8]}",
                summary=summary or "User interaction session",
                session_id=session_id,
                turn_count=session.get("turn_count", 0),
                started_at=session.get("started_at"),
            )
            episode.user_id = session.get("user_id") or config.user_id

            if episode.turn_count > 10:
                episode.importance = 0.7
            elif episode.turn_count > 5:
                episode.importance = 0.5

            episode_id = self.storage.insert_episode(episode)

            self.storage.end_session(session_id, {"episode_id": episode_id})

            uid = session.get("user_id") or config.user_id
            person = self.storage.get_node_by_name(
                uid, NodeType.PERSON.value, uid
            ) or self.storage.get_node_by_name(uid, None, uid)
            if person and person.id:
                bump = min(1.0, float(person.salience or 0.5) + 0.02)
                self.storage.update_node_salience(person.id, bump)

            return {
                "status": "completed",
                "episode_id": episode_id,
                "turn_count": episode.turn_count,
            }

        except Exception as e:
            logger.error(f"Session compaction failed: {e}")
            return {"status": "failed", "error": str(e)}

    def full_consolidation(self, force: bool = False):
        """Daily: Merge duplicates, resolve contradictions, archive stale."""
        now = _utc_now()

        if not force and self.last_daily:
            if now - self.last_daily < self.daily_interval:
                return {"status": "skipped", "reason": "not_yet_due"}

        self.last_daily = now

        result = {
            "status": "completed",
            "started_at": now.isoformat(),
            "archived_nodes": 0,
            "merged_edges": 0,
        }

        try:
            archived = self.storage.archive_stale_nodes(
                days=30, min_salience=config.ARCHIVE_SALIENCE_THRESHOLD
            )
            result["archived_nodes"] = archived

            self.storage.vacuum()

            result["completed_at"] = _utc_now().isoformat()
            logger.info(f"Full consolidation completed: {result}")
            return result

        except Exception as e:
            logger.error(f"Full consolidation failed: {e}")
            result["status"] = "failed"
            result["error"] = str(e)
            return result

    def check_and_consolidate(self):
        """Called periodically to trigger appropriate consolidation."""
        results = []

        bounded_result = self.bounded_maintenance()
        if bounded_result["status"] == "completed":
            results.append(("bounded", bounded_result))

        if (
            self.last_daily is None
            or _utc_now() - self.last_daily >= self.daily_interval
        ):
            daily_result = self.full_consolidation()
            if daily_result["status"] == "completed":
                results.append(("daily", daily_result))

        return results


consolidation_engine = ConsolidationEngine()
