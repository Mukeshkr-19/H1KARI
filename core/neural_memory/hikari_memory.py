"""Main orchestrator for Hikari Neural Memory."""

import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

from .config import config
from .storage import storage
from .models import MemoryNode, MemoryEdge, Episode, ContextPacket, Session, NodeType
from .memory_graph import graph
from .memory_compiler import compiler
from .retrieval_engine import retrieval_engine
from .consolidation import consolidation_engine
from .cache import node_cache, context_cache
from .safety import safety

logger = logging.getLogger(__name__)


class HikariMemory:
    _instance: Optional["HikariMemory"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._session_id: Optional[str] = None
        self._turn_count = 0

    def initialize(self) -> bool:
        try:
            safety._validate_paths()
            safety.check_corruption()

            if not storage.initialize():
                return False

            self._ensure_user_person_anchor()

            self._session_id = str(uuid.uuid4())
            session = Session(session_id=self._session_id, user_id=config.user_id)
            storage.insert_session(session)

            logger.info(f"Hikari Memory initialized. Session: {self._session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Hikari Memory: {e}")
            return False

    def _ensure_user_person_anchor(self) -> None:
        """One stable PERSON node per user_id for graph edges (preferences, etc.)."""
        uid = config.user_id
        existing = storage.get_node_by_name(uid, NodeType.PERSON.value, uid)
        if existing:
            return
        storage.insert_node(
            MemoryNode(
                node_type=NodeType.PERSON.value,
                name=uid,
                content="Primary user anchor (auto-created)",
                salience=0.75,
                user_id=uid,
            )
        )
        logger.info("Created user PERSON anchor: %s", uid)

    def get_context(self, query: str, user_id: str = None) -> ContextPacket:
        safe_query, error = safety.validate_query(query)
        if not safe_query:
            logger.warning(f"Invalid query: {error}")
            return ContextPacket(query=query)

        cache_key = f"ctx:{query[:50]}:{user_id or 'default'}"
        cached = context_cache.get(cache_key)
        if cached:
            return cached

        context = retrieval_engine.retrieve(query, self._session_id, user_id)

        for node in context.relevant_nodes:
            storage.update_node_access(node.id)

        context_cache.set(cache_key, context)
        return context

    def process_turn(
        self, user_message: str, assistant_message: str, metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        metadata = metadata or {}
        self._turn_count += 1

        result = compiler.compile_and_store(
            user_message,
            assistant_message,
            {
                **metadata,
                "session_id": self._session_id,
                "user_id": metadata.get("user_id") or config.user_id,
            },
        )

        consolidation_engine.micro_update(self._session_id)

        if self._turn_count % 10 == 0:
            consolidation_engine.bounded_maintenance()

        return {
            "success": True,
            "turn_count": self._turn_count,
            "nodes_created": len(result["nodes"]),
            "edges_created": len(result["edges"]),
        }

    def seed_user_data(self):
        compiler.seed_initial_data()

    def end_session(self, summary: Optional[str] = None):
        if self._session_id:
            consolidation_engine.session_compaction(self._session_id, summary)
            storage.end_session(self._session_id)
            context_cache.invalidate_pattern(self._session_id)
            self._session_id = None

    def start_new_session(self) -> str:
        if self._session_id:
            self.end_session()

        self._session_id = str(uuid.uuid4())
        session = Session(session_id=self._session_id, user_id=config.user_id)
        storage.insert_session(session)
        self._turn_count = 0

        return self._session_id

    def flush(self):
        context_cache.clear()
        node_cache.clear()
        logger.info("Memory caches flushed")

    def shutdown(self):
        self.end_session()
        consolidation_engine.full_consolidation()
        safety.backup_if_needed()
        self.flush()
        logger.info("Hikari Memory shut down")

    def get_stats(self) -> Dict[str, Any]:
        db_stats = storage.get_stats()
        cache_stats = {
            "node_cache": node_cache.get_stats(),
            "context_cache": context_cache.get_stats(),
        }
        memory_health = safety.check_corruption()

        return {
            **db_stats,
            "caches": cache_stats,
            "health": memory_health,
            "session_id": self._session_id,
            "turn_count": self._turn_count,
        }

    def search(self, query: str, user_id: str = None) -> list[MemoryNode]:
        return storage.search_nodes_fts(query, user_id)

    def get_person(self, name: str) -> Optional[MemoryNode]:
        cache_key = f"person:{name}"
        cached = node_cache.get(cache_key)
        if cached:
            return cached

        person = storage.get_node_by_name(name, "PERSON")
        if person:
            node_cache.set(cache_key, person)
        return person

    def get_user_profile(self) -> Dict[str, Any]:
        return storage.get_user_profile()

    def set_user_preference(self, key: str, value: str):
        storage.set_user_profile(key, value)

    def get_recent_memories(self, limit: int = 20) -> list[MemoryNode]:
        return storage.get_recent_nodes(limit=limit)

    def get_ego_network(
        self, node_id: int, depth: int = 2
    ) -> tuple[list[MemoryNode], list[MemoryEdge]]:
        return graph.get_ego_network(node_id, depth)


hikari_memory = HikariMemory()
