"""Pure, deterministic graph-neighbor retrieval for Brain v2 accepted memories.

This module is intentionally stateless and database-free. It receives caller-
supplied :class:`SourceLinkedMemory` objects, builds an in-memory typed entity
graph from their reviewed metadata, and returns bounded one- and two-hop
neighbor hits as supplemental retrieval evidence.

It never opens a database, never reads live Brain state, and never generates
natural-language answers. All authority boundaries (active-only, accepted-only,
retired/superseded exclusion) are enforced inside the functions below.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from core.brain_v2.memory_lifecycle import is_active_memory
from core.brain_v2.schemas import SourceLinkedMemory

# Entity fields that may link two memories. ``candidate_type`` is intentionally
# captured as a node attribute but is *not* used for traversal, because linking
# memories only by broad type (for example, every ``relation`` memory) would
# create cross-relation leakage.
_TRAVERSABLE_ENTITY_TYPES = (
    "person",
    "relation",
    "organization",
    "location",
    "place",
    "date_text",
)

# Priority order used when a memory shares more than one entity with a neighbor.
# Earlier items are reported as the primary edge type.
_ENTITY_PRIORITY = {
    "person": 0,
    "relation": 1,
    "organization": 2,
    "location": 3,
    "place": 4,
    "date_text": 5,
}

_DIRECT_SCORE = 1.0
_DEPTH_PENALTY = 0.25


class _GraphEntityKey:
    """Internal normalized entity key."""

    __slots__ = ("entity_type", "key")

    def __init__(self, entity_type: str, key: str) -> None:
        self.entity_type = entity_type
        self.key = key

    def __repr__(self) -> str:  # pragma: no cover
        return f"_GraphEntityKey({self.entity_type!r}, {self.key!r})"


@dataclass(frozen=True)
class GraphNeighborSeed:
    """A seed memory plus the optional entity that the caller is focusing on."""

    memory: SourceLinkedMemory
    entity_type: Optional[str] = None
    entity_key: Optional[str] = None


@dataclass(frozen=True)
class GraphEdge:
    """A single graph edge linking two memories through a shared typed entity."""

    source_memory_id: str
    target_memory_id: str
    entity_type: str
    entity_key: str


@dataclass(frozen=True)
class GraphPath:
    """Immutable provenance trail from a seed memory to a discovered neighbor."""

    edges: Tuple[GraphEdge, ...] = ()

    @property
    def seed_memory_id(self) -> Optional[str]:
        if not self.edges:
            return None
        return self.edges[0].source_memory_id

    @property
    def target_memory_id(self) -> Optional[str]:
        if not self.edges:
            return None
        return self.edges[-1].target_memory_id

    @property
    def memory_ids(self) -> Tuple[str, ...]:
        if not self.edges:
            return ()
        ids: List[str] = [self.edges[0].source_memory_id]
        for edge in self.edges:
            ids.append(edge.target_memory_id)
        return tuple(ids)


@dataclass
class GraphNeighborHit:
    """One supplemental retrieval candidate discovered through graph traversal."""

    seed_memory_id: str
    memory_id: str
    statement: str
    edge_type: str
    shared_entities: List[str]
    depth: int
    score: float
    path: GraphPath
    connecting_memory_id: Optional[str] = None
    memory: Optional[SourceLinkedMemory] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        # Coerce score to the documented bounds defensively.
        object.__setattr__(self, "score", max(0.0, min(1.0, float(self.score))))


@dataclass
class BrainMemoryGraph:
    """In-memory typed entity graph over active accepted Brain v2 memories."""

    nodes: Dict[str, SourceLinkedMemory] = field(default_factory=dict)
    _index: Dict[str, Dict[str, Set[str]]] = field(default_factory=dict)
    _node_entities: Dict[str, Dict[str, Set[str]]] = field(default_factory=dict)
    _candidate_types: Dict[str, str] = field(default_factory=dict)

    def add_memory(self, memory: SourceLinkedMemory) -> None:
        """Register an active memory and all of its traversable entities."""
        entities = _extract_entities(memory)
        memory_id = memory.memory_id
        previous = self._node_entities.get(memory_id, {})
        for entity_type, keys in previous.items():
            for key in keys:
                linked = self._index.get(entity_type, {}).get(key)
                if linked is not None:
                    linked.discard(memory_id)
        self.nodes[memory_id] = memory
        node_entities: Dict[str, Set[str]] = {}
        for entity in entities:
            self._index.setdefault(entity.entity_type, {}).setdefault(
                entity.key, set()
            ).add(memory_id)
            node_entities.setdefault(entity.entity_type, set()).add(entity.key)
        self._node_entities[memory_id] = node_entities
        candidate_type = (memory.metadata or {}).get("candidate_type")
        if candidate_type:
            self._candidate_types[memory_id] = str(candidate_type).lower().strip()

    def neighbor_memory_ids(
        self, memory_id: str, entity_type: str, entity_key: str
    ) -> List[str]:
        """Return all other memory ids linked by the given typed entity key."""
        linked = self._index.get(entity_type, {}).get(entity_key, set())
        return sorted(mid for mid in linked if mid != memory_id)

    def get_entity_keys(self, memory_id: str, entity_type: str) -> Set[str]:
        return set(self._node_entities.get(memory_id, {}).get(entity_type, set()))


GraphSeedInput = Union[SourceLinkedMemory, GraphNeighborSeed]


def _normalize_key(value: object) -> str:
    """Stable, punctuation-trimming normalizer for entity keys.

    Keeps internal spaces so that ``City A`` and ``City A.`` match, but
    ``City A`` and ``City AB`` do not.
    """
    text = " ".join(str(value).split())
    text = text.strip()
    text = re.sub(r"^[^\w\s]+", "", text)
    text = re.sub(r"[^\w\s]+$", "", text)
    return text.lower()


def _extract_entities(memory: SourceLinkedMemory) -> List[_GraphEntityKey]:
    """Pull bounded typed entities from reviewed metadata only.

    No statement parsing is performed here; entity values are expected to have
    been written into the memory's metadata during candidate review. Malformed
    or missing metadata is handled gracefully and returns an bounded key list.
    """
    if not isinstance(memory, SourceLinkedMemory):
        return []
    metadata = memory.metadata
    if not isinstance(metadata, dict):
        return []

    entities: List[_GraphEntityKey] = []
    for field in _TRAVERSABLE_ENTITY_TYPES:
        raw = metadata.get(field)
        if raw is None or raw == "":
            continue
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple, set)):
            values = raw
        else:
            values = [raw]
        for value in values:
            key = _normalize_key(value)
            if key:
                entities.append(_GraphEntityKey(field, key))

    # candidate_type is captured as a node attribute via build_memory_graph, but
    # it is intentionally not used as a traversable edge to avoid cross-type leaks.
    return entities


def build_memory_graph(
    memories: Iterable[SourceLinkedMemory],
    *,
    active_only: bool = True,
) -> BrainMemoryGraph:
    """Build a typed entity graph from a collection of source-linked memories.

    Args:
        memories: Caller-supplied memories. The graph only indexes memories
            whose lifecycle status is ``active`` when ``active_only`` is true.
        active_only: If true, retired and superseded memories are skipped.

    Returns:
        A :class:`BrainMemoryGraph` containing nodes and typed entity indexes.
    """
    graph = BrainMemoryGraph()
    for memory in memories:
        if active_only and not is_active_memory(memory):
            continue
        if not isinstance(memory, SourceLinkedMemory):
            continue
        graph.add_memory(memory)
    return graph


def _memory_to_seed(memory_or_seed: GraphSeedInput) -> SourceLinkedMemory:
    if isinstance(memory_or_seed, GraphNeighborSeed):
        return memory_or_seed.memory
    return memory_or_seed


def _score_for_depth(depth: int) -> float:
    """Deterministic depth-penalized score.

    Depth 1 (direct neighbor) always outranks depth 2 (two-hop neighbor).
    """
    return max(0.0, _DIRECT_SCORE - depth * _DEPTH_PENALTY)


def _build_hit(
    seed_id: str,
    target_memory: SourceLinkedMemory,
    depth: int,
    path: GraphPath,
    entity_type: str,
    entity_key: str,
) -> GraphNeighborHit:
    shared = [f"{entity_type}:{entity_key}"]
    return GraphNeighborHit(
        seed_memory_id=seed_id,
        memory_id=target_memory.memory_id,
        statement=(target_memory.statement or ""),
        edge_type=entity_type,
        shared_entities=shared,
        depth=depth,
        score=_score_for_depth(depth),
        path=path,
        connecting_memory_id=path.memory_ids[-2] if len(path.memory_ids) >= 2 else target_memory.memory_id,
        memory=target_memory,
    )


def _discover_neighbors(
    graph: BrainMemoryGraph,
    seed: SourceLinkedMemory,
    max_depth: int,
    *,
    first_hop_entity_type: Optional[str] = None,
    first_hop_entity_key: Optional[str] = None,
) -> List[GraphNeighborHit]:
    seed_id = seed.memory_id
    if seed_id not in graph.nodes:
        return []

    discovered: Dict[str, Tuple[int, GraphNeighborHit]] = {}
    visited: Set[str] = {seed_id}
    queue: deque = deque()
    # (current_memory_id, depth, path)
    queue.append((seed_id, 0, GraphPath(())))

    while queue:
        current_id, depth, path = queue.popleft()
        if depth >= max_depth:
            continue

        current = graph.nodes.get(current_id)
        if current is None:
            continue

        for entity_type in _TRAVERSABLE_ENTITY_TYPES:
            if depth == 0 and first_hop_entity_type is not None:
                if entity_type != first_hop_entity_type:
                    continue
            keys = graph.get_entity_keys(current_id, entity_type)
            if not keys:
                continue
            for key in sorted(keys):
                if depth == 0 and first_hop_entity_key is not None:
                    if key != first_hop_entity_key:
                        continue
                linked_ids = graph.neighbor_memory_ids(current_id, entity_type, key)
                for neighbor_id in sorted(linked_ids):
                    if neighbor_id == seed_id:
                        continue
                    if neighbor_id in visited:
                        continue
                    neighbor = graph.nodes.get(neighbor_id)
                    if neighbor is None:
                        continue

                    edge = GraphEdge(current_id, neighbor_id, entity_type, key)
                    new_path = GraphPath(path.edges + (edge,))
                    new_depth = depth + 1

                    hit = _build_hit(
                        seed_id,
                        neighbor,
                        new_depth,
                        new_path,
                        entity_type,
                        key,
                    )
                    # Record the shortest, stablest path to this neighbor.
                    if neighbor_id not in discovered or new_depth < discovered[neighbor_id][0]:
                        discovered[neighbor_id] = (new_depth, hit)

                    visited.add(neighbor_id)
                    queue.append((neighbor_id, new_depth, new_path))

    return [hit for _, (_, hit) in sorted(discovered.items(), key=lambda kv: kv[0])]


def find_graph_neighbors(
    seeds: Union[GraphSeedInput, Sequence[GraphSeedInput]],
    memories: Sequence[SourceLinkedMemory],
    *,
    max_results: int = 10,
    max_depth: int = 2,
) -> List[GraphNeighborHit]:
    """Return bounded graph-neighbor hits for one or more seed memories.

    The function builds an in-memory typed entity graph from ``memories`` and
    performs deterministic breadth-first traversal up to ``max_depth`` hops.

    Args:
        seeds: A single seed or sequence of seeds. Seeds may be raw
            :class:`SourceLinkedMemory` objects or :class:`GraphNeighborSeed`
            wrappers.
        memories: The pool of active accepted memories to search.
        max_results: Maximum number of hits to return.
        max_depth: Maximum hop count. Supported values are 1 and 2.

    Returns:
        A list of :class:`GraphNeighborHit` objects sorted by descending score,
        then by deterministic tie-breakers (depth, memory_id, seed_memory_id,
        edge_type). The returned hits are supplemental evidence only; they do
        not override direct accepted-memory matches.
    """
    if not memories:
        return []
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise ValueError("max_results must be an integer")
    if max_results <= 0:
        return []
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        raise ValueError("max_depth must be an integer")
    if max_depth not in (1, 2):
        raise ValueError("max_depth must be 1 or 2")

    if isinstance(seeds, (SourceLinkedMemory, GraphNeighborSeed)):
        seed_list: List[GraphSeedInput] = [seeds]
    else:
        seed_list = list(seeds)
    if not seed_list:
        return []

    # Ensure seeds are part of the graph so they can act as traversal roots even
    # if the caller passes a separate collection.
    resolved_seeds = [_memory_to_seed(s) for s in seed_list]
    all_memories = list(memories) + resolved_seeds
    graph = build_memory_graph(all_memories)

    best_by_id: Dict[str, GraphNeighborHit] = {}
    for seed_input, seed in zip(seed_list, resolved_seeds):
        focus_type: Optional[str] = None
        focus_key: Optional[str] = None
        if isinstance(seed_input, GraphNeighborSeed):
            if (seed_input.entity_type is None) != (seed_input.entity_key is None):
                raise ValueError("entity_type and entity_key must be supplied together")
            if seed_input.entity_type is not None:
                focus_type = str(seed_input.entity_type).strip().lower()
                if focus_type not in _TRAVERSABLE_ENTITY_TYPES:
                    raise ValueError("unsupported seed entity_type")
                focus_key = _normalize_key(seed_input.entity_key)
                if not focus_key:
                    raise ValueError("seed entity_key must be non-empty")
        for hit in _discover_neighbors(
            graph,
            seed,
            max_depth,
            first_hop_entity_type=focus_type,
            first_hop_entity_key=focus_key,
        ):
            existing = best_by_id.get(hit.memory_id)
            if existing is None or hit.depth < existing.depth:
                best_by_id[hit.memory_id] = hit
            elif hit.depth == existing.depth:
                # Stable tie-break: prefer the earlier seed, then earlier edge.
                if (hit.seed_memory_id, hit.edge_type) < (
                    existing.seed_memory_id,
                    existing.edge_type,
                ):
                    best_by_id[hit.memory_id] = hit

    sorted_hits = sorted(
        best_by_id.values(),
        key=lambda h: (
            -h.score,
            h.depth,
            h.memory_id,
            h.seed_memory_id,
            h.edge_type,
        ),
    )
    return sorted_hits[:max_results]
