"""Public adapter interface for Hikari Neural Memory."""

import logging
from typing import Dict, Any, Optional

from .hikari_memory import hikari_memory
from .config import config
from .models import ContextPacket

logger = logging.getLogger(__name__)


def initialize() -> bool:
    return hikari_memory.initialize()


def get_context_packet(
    query: str, session_id: str = None, user_id: str = None
) -> ContextPacket:
    return hikari_memory.get_context(query, user_id)


def process_turn(
    user_message: str, assistant_message: str, metadata: Dict[str, Any] = None
) -> Dict[str, Any]:
    return hikari_memory.process_turn(user_message, assistant_message, metadata)


def ingest_unstructured_text(text: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Public API: extract/store from a single user utterance (no assistant reply)."""
    uid = user_id or config.user_id
    return hikari_memory.process_turn(text or "", "", {"user_id": uid})


def flush():
    hikari_memory.flush()


def shutdown():
    hikari_memory.shutdown()


def seed_user_data():
    hikari_memory.seed_user_data()


def get_stats() -> Dict[str, Any]:
    return hikari_memory.get_stats()


def search(query: str, user_id: str = None) -> list:
    return hikari_memory.search(query, user_id)


def get_recent_memories(limit: int = 20) -> list:
    return hikari_memory.get_recent_memories(limit)


def get_user_profile() -> Dict[str, Any]:
    return hikari_memory.get_user_profile()


def set_user_preference(key: str, value: str):
    hikari_memory.set_user_preference(key, value)


def end_session(summary: str = None):
    hikari_memory.end_session(summary)


def start_new_session() -> str:
    return hikari_memory.start_new_session()
