"""
HIKARI Neural Memory Bridge
Bridges the orchestrator with the neural memory system for supercharged context
"""

import logging
from typing import Dict, Any, Optional, List

from core.neural_memory import (
    initialize,
    get_context_packet,
    process_turn,
    ingest_unstructured_text,
    get_stats,
    seed_user_data,
    shutdown,
    search,
    get_recent_memories,
    get_user_profile,
    set_user_preference,
    end_session,
    start_new_session,
    MemoryNode,
    ContextPacket,
    NodeType,
    config,
)

logger = logging.getLogger(__name__)


def _uid(user_id: Optional[str]) -> str:
    return user_id if user_id is not None else config.user_id

_initialized = False


def init_neural_memory():
    global _initialized
    if _initialized:
        return True

    try:
        result = initialize()
        if result:
            seed_user_data()
            _initialized = True
            logger.info("Neural memory initialized")
        return result
    except Exception as e:
        logger.error(f"Neural memory init failed: {e}")
        return False


def get_memory_context(query: str, user_id: Optional[str] = None) -> ContextPacket:
    """Get rich context from neural memory"""
    return get_context_packet(query, user_id=_uid(user_id))


def remember(user_msg: str, assistant_msg: str, metadata: Dict = None):
    """Store a conversation turn in neural memory"""
    return process_turn(user_msg, assistant_msg, metadata)


def recall(query: str, user_id: Optional[str] = None) -> str:
    """Recall relevant memories and format as context string"""
    ctx = get_memory_context(query, user_id)

    if not ctx.relevant_nodes:
        return ""

    lines = ["[MEMORY CONTEXT]"]

    # Group by type
    by_type = {}
    for node in ctx.relevant_nodes:
        t = node.node_type
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(node)

    for ntype, nodes in by_type.items():
        if len(nodes) <= 3:
            for n in nodes:
                content = n.content or ""
                lines.append(
                    f"  {ntype}: {n.name} - {content[:80]}"
                    if content
                    else f"  {ntype}: {n.name}"
                )
        else:
            names = [n.name for n in nodes[:5]]
            lines.append(
                f"  {ntype}: {', '.join(names)}" + ("..." if len(nodes) > 5 else "")
            )

    if ctx.recent_episodes:
        lines.append(f"\n  Recent: {len(ctx.recent_episodes)} conversations")
        if ctx.recent_episodes:
            ep = ctx.recent_episodes[0]
            lines.append(
                f"  Last: {ep.title or ep.episode_type} - {ep.summary or 'N/A'}"
            )

    return "\n".join(lines)


def build_memory_prompt(query: str, user_id: Optional[str] = None) -> str:
    """Build a compact memory context for AI prompts"""
    ctx = get_memory_context(query, user_id)

    if not ctx.relevant_nodes:
        return ""

    # Build compact context
    parts = []

    # High salience nodes first
    high_nodes = [n for n in ctx.relevant_nodes if n.salience >= 0.7]
    for node in high_nodes[:8]:
        if node.content:
            parts.append(f"{node.name}: {node.content[:100]}")
        else:
            parts.append(f"{node.name}")

    # Relationships
    if ctx.relevant_edges:
        rel_count = len(ctx.relevant_edges)
        parts.append(f"(+ {rel_count} relationships)")

    if parts:
        return "\n[From memory] " + "; ".join(parts[:5])
    return ""


def learn_from_text(text: str, user_id: Optional[str] = None):
    """Extract and store entities from text (uses public neural-memory API)."""
    try:
        return ingest_unstructured_text(text, user_id=_uid(user_id))
    except Exception as e:
        logger.warning(f"Learn failed: {e}")
        return {"success": False, "error": str(e)}


def get_whoami(user_id: Optional[str] = None) -> Dict[str, Any]:
    """Get complete user profile from neural memory"""
    profile = get_user_profile()

    # Get key nodes
    recent = get_recent_memories(limit=20)

    persons = [n for n in recent if n.node_type == NodeType.PERSON.value]
    projects = [n for n in recent if n.node_type == NodeType.PROJECT.value]
    prefs = [n for n in recent if n.node_type == NodeType.PREFERENCE.value]
    locations = [n for n in recent if n.node_type == NodeType.LOCATION.value]

    return {
        "profile": profile,
        "persons": [{"name": p.name, "content": p.content} for p in persons],
        "projects": [{"name": p.name, "content": p.content} for p in projects],
        "preferences": [{"name": p.name, "content": p.content} for p in prefs],
        "locations": [{"name": l.name, "content": l.content} for l in locations],
        "stats": get_stats(),
    }


def format_whoami(user_id: Optional[str] = None) -> str:
    """Format user info as readable string"""
    info = get_whoami(_uid(user_id))

    lines = ["[MEMORY: What I know about you]", ""]

    for person in info["persons"][:5]:
        lines.append(f"  👤 {person['name']}")
        if person.get("content"):
            lines.append(f"     {person['content'][:80]}")

    if info["projects"]:
        lines.append("")
        lines.append("  💻 Projects:")
        for p in info["projects"][:5]:
            lines.append(f"     • {p['name']}: {p.get('content', 'N/A')[:50]}")

    if info["preferences"]:
        lines.append("")
        lines.append("  ⚙️  Preferences:")
        for pref in info["preferences"][:5]:
            lines.append(f"     • {pref['name']}: {pref.get('content', 'N/A')[:50]}")

    stats = info["stats"]
    lines.append("")
    lines.append(
        f"  📊 Memory stats: {stats.get('nodes', 0)} nodes, {stats.get('edges', 0)} edges"
    )

    return "\n".join(lines)


def smart_query(query: str, user_id: Optional[str] = None) -> List[MemoryNode]:
    """Perform smart search across memory"""
    return search(query, _uid(user_id))


def get_memory_stats() -> Dict[str, Any]:
    """Get comprehensive memory stats"""
    return get_stats()


def end_memory_session():
    """End current memory session with consolidation"""
    end_session()


def start_memory_session() -> str:
    """Start new memory session"""
    return start_new_session()
