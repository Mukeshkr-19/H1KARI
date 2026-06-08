"""Hikari Neural Memory System.

A graph-based memory system that stores knowledge as nodes and edges
with multi-level consolidation for efficient retrieval.

**Public API** — import only from ``core.neural_memory`` (adapter functions + types).

Usage:
    from core.neural_memory import initialize, get_context_packet, process_turn, shutdown

    initialize()
    context = get_context_packet("What projects am I working on?")
    process_turn("I'm working on my-app", "Great — tell me more.")
    shutdown()
"""

from .adapter import (
    initialize,
    get_context_packet,
    process_turn,
    ingest_unstructured_text,
    flush,
    shutdown,
    seed_user_data,
    get_stats,
    search,
    get_recent_memories,
    get_user_profile,
    set_user_preference,
    end_session,
    start_new_session,
)
from .models import (
    MemoryNode,
    MemoryEdge,
    Episode,
    Session,
    ContextPacket,
    NodeType,
    EdgeType,
    EpisodeType,
    Outcome,
)
from .hikari_memory import hikari_memory
from .config import config

__all__ = [
    "initialize",
    "get_context_packet",
    "process_turn",
    "ingest_unstructured_text",
    "flush",
    "shutdown",
    "seed_user_data",
    "get_stats",
    "search",
    "get_recent_memories",
    "get_user_profile",
    "set_user_preference",
    "end_session",
    "start_new_session",
    "MemoryNode",
    "MemoryEdge",
    "Episode",
    "Session",
    "ContextPacket",
    "NodeType",
    "EdgeType",
    "EpisodeType",
    "Outcome",
    "hikari_memory",
    "config",
]
