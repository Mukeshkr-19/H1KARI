"""Multi-strategy retrieval engine for Hikari Neural Memory."""

import logging
from typing import List, Optional, Tuple
from datetime import datetime, timedelta, timezone

from .storage import storage
from .models import MemoryNode, MemoryEdge, Episode, ContextPacket, NodeType, EdgeType
from .config import config

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return legacy naive UTC datetime without deprecated utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

FAMILY_QUERY_WORDS = frozenset(
    {
        "sister",
        "brother",
        "mother",
        "father",
        "son",
        "daughter",
        "wife",
        "husband",
        "parent",
        "cousin",
        "aunt",
        "uncle",
        "sibling",
        "family",
        "relative",
    }
)


class RetrievalEngine:
    def __init__(self):
        self.storage = storage
        self.max_nodes = config.MAX_NODES_PER_RETRIEVAL
        self.max_edges = config.MAX_EDGES_PER_RETRIEVAL

    def retrieve(
        self,
        query: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> ContextPacket:
        strategies_used = []

        relevant_nodes = []
        relevant_edges = []
        recent_episodes = []
        user_profile = {}
        session_context = {}
        confidence = 0.0

        # Strategy 1: Direct lookup by exact matches
        nodes_1, edges_1, conf_1 = self._direct_lookup(query, user_id)
        if nodes_1:
            relevant_nodes.extend(nodes_1)
            relevant_edges.extend(edges_1)
            confidence = max(confidence, conf_1)
            strategies_used.append("direct_lookup")

        # Strategy 2: Recent context
        nodes_2, edges_2, conf_2 = self._recent_context(user_id)
        if nodes_2:
            for node in nodes_2:
                if node not in relevant_nodes:
                    relevant_nodes.append(node)
            relevant_edges.extend(edges_2)
            confidence = max(confidence, conf_2 * 0.8)
            strategies_used.append("recent_context")

        # Strategy 3: Graph expansion
        if relevant_nodes:
            nodes_3, edges_3, conf_3 = self._graph_expansion(relevant_nodes, user_id)
            if nodes_3:
                for node in nodes_3:
                    if node not in relevant_nodes:
                        relevant_nodes.append(node)
                relevant_edges.extend(edges_3)
                confidence = max(confidence, conf_3 * 0.6)
                strategies_used.append("graph_expansion")

        # Strategy 4: Vector/semantic fallback (FTS)
        if len(relevant_nodes) < 5:
            nodes_4, conf_4 = self._semantic_fallback(query, user_id)
            if nodes_4:
                for node in nodes_4:
                    if node not in relevant_nodes:
                        relevant_nodes.append(node)
                confidence = max(confidence, conf_4 * 0.5)
                strategies_used.append("semantic_fallback")

        # Strategy 5: Family / relationship queries (e.g. "my sister", "who is my sister")
        nodes_5, edges_5, conf_5 = self._family_relationship_lookup(query, user_id)
        if nodes_5:
            for node in nodes_5:
                if node not in relevant_nodes:
                    relevant_nodes.append(node)
            relevant_edges.extend(edges_5)
            confidence = max(confidence, conf_5)
            strategies_used.append("family_relationship")

        # Strategy 6: Structured personal facts (permanent/current location, relations)
        nodes_6, conf_6 = self._structured_personal_lookup(query, user_id)
        if nodes_6:
            for node in nodes_6:
                if node not in relevant_nodes:
                    relevant_nodes.append(node)
            confidence = max(confidence, conf_6)
            strategies_used.append("structured_personal")

        # Session context
        if session_id:
            session_context = self._get_session_context(session_id)

        # User profile
        user_profile = self.storage.get_user_profile(user_id)

        # Recent episodes
        recent_episodes = self.storage.get_recent_episodes(user_id, limit=5)

        # Sort and limit
        relevant_nodes.sort(key=lambda n: n.salience, reverse=True)
        relevant_nodes = relevant_nodes[: self.max_nodes]

        relevant_edges.sort(key=lambda e: e.weight, reverse=True)
        relevant_edges = relevant_edges[: self.max_edges]

        return ContextPacket(
            query=query,
            relevant_nodes=relevant_nodes,
            relevant_edges=relevant_edges,
            recent_episodes=recent_episodes,
            user_profile=user_profile,
            session_context=session_context,
            confidence=min(confidence, 1.0),
            retrieval_strategies_used=strategies_used,
        )

    def _direct_lookup(
        self, query: str, user_id: Optional[str] = None
    ) -> Tuple[List[MemoryNode], List[MemoryEdge], float]:
        nodes = []
        edges = []
        confidence = 0.0

        query_lower = query.lower()
        query_words = query_lower.split()

        # Check for person names
        person_nodes = self.storage.get_nodes_by_type(NodeType.PERSON.value, user_id)
        for node in person_nodes:
            if node.name.lower() in query_lower or (
                node.alias and node.alias.lower() in query_lower
            ):
                nodes.append(node)
                edges.extend(self.storage.get_edges_for_node(node.id, user_id))
                confidence = 0.95
                break

        # Check for project names
        if not nodes:
            project_nodes = self.storage.get_nodes_by_type(
                NodeType.PROJECT.value, user_id
            )
            for node in project_nodes:
                if node.name.lower() in query_lower:
                    nodes.append(node)
                    edges.extend(self.storage.get_edges_for_node(node.id, user_id))
                    confidence = 0.9
                    break

        # Check for skill/tool mentions
        if not nodes:
            for ntype in [NodeType.SKILL.value, NodeType.TOOL.value]:
                type_nodes = self.storage.get_nodes_by_type(ntype, user_id)
                for node in type_nodes:
                    if node.name.lower() in query_lower:
                        nodes.append(node)
                        confidence = 0.85
                        break
                if nodes:
                    break

        return nodes, edges, confidence

    def _recent_context(
        self, user_id: Optional[str] = None
    ) -> Tuple[List[MemoryNode], List[MemoryEdge], float]:
        nodes = []
        edges = []

        recent = self.storage.get_recent_nodes(user_id, limit=20)
        recent_cutoff = _utc_now() - timedelta(hours=24)

        for node in recent:
            if node.last_accessed:
                try:
                    last_access = datetime.fromisoformat(node.last_accessed)
                    if last_access > recent_cutoff:
                        nodes.append(node)
                except (ValueError, TypeError):
                    pass

        for node in nodes[:10]:
            node_edges = self.storage.get_edges_for_node(node.id, user_id)
            edges.extend(node_edges)

        confidence = min(0.7, len(nodes) * 0.1)

        return nodes, edges, confidence

    def _graph_expansion(
        self, seed_nodes: List[MemoryNode], user_id: Optional[str] = None
    ) -> Tuple[List[MemoryNode], List[MemoryEdge], float]:
        expanded_nodes = []
        all_edges = []

        for seed in seed_nodes[:5]:
            neighbors = self.storage.get_neighbors(seed.id, user_id=user_id)
            for neighbor in neighbors:
                if neighbor not in expanded_nodes:
                    neighbor_edges = self.storage.get_edges_for_node(
                        neighbor.id, user_id
                    )
                    expanded_nodes.append(neighbor)
                    all_edges.extend(neighbor_edges)

        confidence = min(0.6, len(expanded_nodes) * 0.1)

        return expanded_nodes, all_edges, confidence

    def _family_relationship_lookup(
        self, query: str, user_id: Optional[str] = None
    ) -> Tuple[List[MemoryNode], List[MemoryEdge], float]:
        query_lower = (query or "").lower()
        if not any(word in query_lower for word in FAMILY_QUERY_WORDS):
            return [], [], 0.0

        nodes: List[MemoryNode] = []
        edges: List[MemoryEdge] = []
        terms = [w for w in FAMILY_QUERY_WORDS if w in query_lower] or [
            "sister",
            "brother",
        ]

        hits = []
        seen_ids = set()
        for term in terms[:6]:
            try:
                for hit in self.storage.search_nodes_fts(term, user_id, limit=8):
                    hit_id = getattr(hit, "id", None)
                    key = hit_id if hit_id is not None else (hit.node_type, hit.name)
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                    hits.append(hit)
            except Exception as e:
                logger.warning("Family FTS lookup failed for %s: %s", term, e)

        for node in hits:
            if node.node_type in (
                NodeType.FACT.value,
                NodeType.PERSON.value,
                NodeType.CONVERSATION.value,
            ):
                nodes.append(node)
                edges.extend(self.storage.get_edges_for_node(node.id, user_id))

        confidence = min(0.85, 0.45 + len(nodes) * 0.08) if nodes else 0.0
        return nodes, edges, confidence

    def _structured_personal_lookup(
        self, query: str, user_id: Optional[str] = None
    ) -> Tuple[List[MemoryNode], float]:
        """Fetch LOCATION/FACT slots used by core.brain (permanent_home, current_location, user:father)."""
        q = (query or "").lower()
        wanted_names: List[str] = []
        if "where do i live" in q:
            wanted_names.append("permanent_home")
        if "where am i" in q:
            wanted_names.append("current_location")
        if any(w in q for w in ("dad", "father", "mom", "mother", "parent")):
            wanted_names.extend(("user:father", "user:mother"))
        if any(w in q for w in ("sister", "brother")):
            wanted_names.extend(("user:sister", "user:brother"))
        if any(w in q for w in ("gf", "girlfriend", "partner")):
            wanted_names.append("user:partner")

        nodes: List[MemoryNode] = []
        for slot in wanted_names:
            row = self.storage.get_node_by_name(slot, NodeType.LOCATION.value, user_id)
            if not row:
                row = self.storage.get_node_by_name(slot, NodeType.FACT.value, user_id)
            if row:
                nodes.append(row)

        if not nodes and ("where" in q or "live" in q):
            for ntype in (NodeType.LOCATION.value, NodeType.FACT.value):
                for node in self.storage.get_nodes_by_type(ntype, user_id, limit=30):
                    meta = node.metadata or {}
                    if isinstance(meta, dict) and meta.get("kind") in (
                        "permanent_home",
                        "current_location",
                        "relation",
                    ):
                        nodes.append(node)

        conf = min(0.9, 0.5 + len(nodes) * 0.15) if nodes else 0.0
        return nodes, conf

    def _semantic_fallback(
        self, query: str, user_id: Optional[str] = None
    ) -> Tuple[List[MemoryNode], float]:
        try:
            nodes = self.storage.search_nodes_fts(query, user_id, limit=10)
            confidence = 0.5 if nodes else 0.0
            return nodes, confidence
        except Exception as e:
            logger.warning(f"FTS search failed: {e}")
            return [], 0.0

    def _get_session_context(self, session_id: str) -> dict:
        try:
            session = self.storage.fetch_one(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            )
            if session:
                return dict(session)
        except Exception:
            pass
        return {}

    def retrieve_by_type(
        self, node_type: str, user_id: Optional[str] = None, limit: int = 20
    ) -> List[MemoryNode]:
        return self.storage.get_nodes_by_type(node_type, user_id, limit)

    def retrieve_for_person(
        self, person_name: str, user_id: Optional[str] = None
    ) -> Tuple[Optional[MemoryNode], List[MemoryNode], List[MemoryEdge]]:
        person = self.storage.get_node_by_name(
            person_name, NodeType.PERSON.value, user_id
        )

        if not person:
            person = self.storage.get_node_by_name(person_name, user_id=user_id)

        if person:
            neighbors = self.storage.get_neighbors(person.id, user_id=user_id)
            edges = self.storage.get_edges_for_node(person.id, user_id=user_id)
            return person, neighbors, edges

        return None, [], []


retrieval_engine = RetrievalEngine()
