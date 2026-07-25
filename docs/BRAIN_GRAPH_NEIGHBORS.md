# Brain v2 Graph-Neighbor Retrieval

## Purpose

`core/brain_v2/graph_neighbors.py` implements a **pure, deterministic, in-memory graph-neighbor retrieval layer** for Brain v2 accepted memories. It takes caller-supplied `SourceLinkedMemory` objects, derives typed entities from their reviewed metadata, and returns bounded one- and two-hop neighbor hits as **supplemental retrieval evidence**.

It is deliberately not a new memory authority and does not generate final answers. It is designed to be wired into `BrainV2Retrieval` later by Mira without altering core retrieval authority or the accepted-memory boundary.

## Accepted-Memory-Only Boundary

Only memories that meet all of the following criteria are indexed or returned:

- Passed in by the caller as `SourceLinkedMemory` objects.
- Lifecycle status is `active` (the default).
- Not `retired` or `superseded`.

Pending, rejected, raw transcript, assistant-authored, legacy neural, and wiki-only content are never authoritative. The module never inspects live Brain state, never opens a database, and never reads secret configuration or private files.

## Graph Construction

`build_memory_graph(memories)` constructs a `BrainMemoryGraph`:

1. Iterate over the caller-supplied memory collection.
2. Skip inactive memories.
3. Extract bounded typed entities from the reviewed `metadata` dict only:
   - `person`
   - `relation`
   - `organization`
   - `location`
   - `place`
   - `date_text`
   - `candidate_type` (captured as a node attribute, not used for traversal)
4. Normalize each entity key (case-fold, trim punctuation, collapse whitespace).
5. Build inverted indexes: `entity_type -> entity_key -> set(memory_id)`.

No statement parsing, no fuzzy matching, and no substring matching are used. Keys must be exact after normalization, so `City A` matches `City A.` but not `City AB`.

## Edge Types

Edges are typed by the shared entity key:

| Edge type | Example shared key | Notes |
|-----------|-------------------|-------|
| `person` | `"jamie"` | Links memories about the same person. |
| `relation` | `"sister"` | Links memories involving the same relation role. |
| `organization` | `"university a"` | Schools, employers, etc. |
| `location` | `"city a"` | Stable or declared locations. |
| `place` | `"restaurant a"` | Named venues or points of interest. |
| `date_text` | `"sunday may 24 2026"` | Shared temporal references. |

`candidate_type` is stored per node but is intentionally excluded from the edge index to avoid cross-type leakage (for example, linking every `relation` memory together).

## Traversal Bounds

`find_graph_neighbors(seeds, memories, max_results=10, max_depth=2)` performs deterministic breadth-first traversal.

- `max_depth=1`: direct neighbors only.
- `max_depth=2`: direct neighbors plus one intermediate hop.

Cycles are prevented with a per-traversal `visited` set. Disconnected components remain separate because there is no implicit or fallback linking.

## Scoring

Scores are deterministic, bounded, and depth-penalized:

- Direct (depth 1): `1.0 - 1 * 0.25 = 0.75`
- Two-hop (depth 2): `1.0 - 2 * 0.25 = 0.50`

The explicit depth penalty guarantees that direct neighbors always rank above equivalent two-hop neighbors. Final results are sorted by `(-score, depth, memory_id, seed_memory_id, edge_type)` for stable, repeatable ordering.

## Provenance

Each `GraphNeighborHit` carries:

- `seed_memory_id`: the starting accepted memory.
- `memory_id` / `memory`: the discovered neighbor.
- `connecting_memory_id`: the immediate predecessor on the path (seed for depth 1, intermediate for depth 2).
- `edge_type`: the shared typed entity of the final hop.
- `shared_entities`: list of `type:key` provenance notes.
- `depth`: hop count from seed.
- `score`: depth-penalized score.
- `path`: a `GraphPath` containing the ordered `GraphEdge` trail.

Duplicate memory IDs are suppressed, keeping the shortest path. Multiple seeds can contribute to the same result; the lowest-depth, most stable provenance is kept.

## Privacy

- Only caller-supplied, reviewed, active memories are ever inspected.
- No live Brain database is opened.
- No private names, places, or conversation data are required by the module.
- Tests and docs use synthetic fixtures only.

## Supplemental / Non-Authoritative Role

Graph-neighbor hits are **support candidates only**. They do not override direct accepted-memory matches and do not produce natural-language answers. The intended use is to enrich the retrieval context packet with related memories that share explicit typed entities, after the primary accepted-memory ranking has been computed.

## Future Retrieval Integration

Mira can wire `find_graph_neighbors()` into `BrainV2Retrieval` as a secondary, bounded evidence pass. Because the component is pure, it can be called with the current top accepted-memory hit as a seed and the active accepted-memory set as the memory pool. Its output should be appended with lower rank caps and clearly labeled as `graph_neighbor` support evidence.

## Non-Goals

- This module is **not** a semantic search engine.
- It does **not** perform LLM-based extraction or reasoning.
- It does **not** write, retire, or supersede memories.
- It does **not** answer personal questions or produce final responses.
- It does **not** arbitrate conflicts between accepted memories.
