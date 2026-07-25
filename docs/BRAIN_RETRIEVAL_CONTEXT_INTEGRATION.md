# Brain v2 Retrieval Context Integration

## Purpose

`core/brain_v2/retrieval.py` wires **supplemental**, accepted-memory-anchored context into
`BrainV2Retrieval.retrieve()` using the pure selectors in:

- `graph_neighbors.find_graph_neighbors`
- `episodic_support.select_episodic_support`
- `wiki_context.select_wiki_context`

Supplemental hits enrich the context packet only. They never become authoritative recall
answers (`top_semantic_hits` and `answer_from_accepted` stay accepted-only).

## Default behavior

With defaults (`enable_graph_neighbors=False`, `enable_episodic_support=False`, no
`wiki_entries`), `retrieve()` matches the pre-integration ranking path. The existing
`_structured_episode_hits` support pass is unchanged.

## API

### `RetrievalContextOptions` (frozen dataclass)

| Field | Default | Role |
|-------|---------|------|
| `enable_graph_neighbors` | `False` | Graph-neighbor supplemental pass |
| `enable_episodic_support` | `False` | Transcript segment supplemental pass |
| `wiki_entries` | `()` | Caller-supplied wiki rows (no filesystem IO) |
| `reference_time` | `None` | Injected clock for recency scoring |
| `graph_max_seeds` | `3` | Top semantic anchors used as graph seeds |
| `graph_max_results` | `8` | Cap on graph neighbor hits |
| `graph_max_depth` | `2` | Graph BFS depth (1 or 2) |
| `graph_score_cap` | `0.74` | Kind cap for graph supplemental scores |
| `episodic_score_cap` | `0.42` | Kind cap for episodic supplemental scores |
| `wiki_score_cap` | `0.40` | Kind cap for wiki supplemental scores |
| `supplemental_epsilon` | `0.02` | Keeps supplemental below anchor semantic score |
| `episodic_policy` | `None` | Optional `EpisodicSupportPolicy` override |
| `wiki_policy` | `None` | Optional `WikiContextPolicy` override |

Count fields must be non-negative integers; score caps and epsilon must be
finite values in `[0, 1]`; and a supplied reference time must be timezone-aware.

### `retrieve()` kwargs

`retrieve()` accepts optional `context_options` plus kwargs that merge into it:
`wiki_entries`, `reference_time`, `enable_graph_neighbors`, `enable_episodic_support`.
There is no untyped options dict.

Malformed optional inputs fail closed (for example, `wiki_entries` as a string becomes an
empty tuple; naive `reference_time` is not merged). Supplemental sub-passes are isolated
with `try/except` so semantic hits survive selector failures.

## Authority and ordering

1. Primary semantic hits (`source="source_linked"`) are scored and ranked as before.
2. Supplemental passes run **only after** at least one active accepted semantic hit exists.
3. Each supplemental hit sets `metadata.is_supplemental=True`,
   `metadata.supplemental_kind` (`graph_neighbor`, `episodic_support`, `wiki_context`), and
   `metadata.authority="accepted_memory_anchor"`.
4. Supplemental scores are capped with `min(kind_cap, anchor_score - epsilon)`.
5. `_dedupe_and_cap` prefers non-supplemental hits when normalized text collides.
6. `BrainV2ContextPacket.top_semantic_hits()` excludes supplemental rows.
7. `to_prompt()` appends honest labels: `(supplemental <kind>)`.

When supplemental features are enabled and no caller `reference_time` is provided, ranking
uses the fixed timezone-aware sentinel `2026-01-15T12:00:00+00:00` (no `datetime.now()` in
the retrieval ranking path).

## Graph neighbors

- Pool: active accepted memories from `EpisodeStore.get_active_accepted_memories`.
- Seeds: top-N direct semantic hits.
- Traversal: `find_graph_neighbors` with provenance metadata (`path_edges`, `seed_memory_id`,
  `target_memory_id`, `edge_type`, `depth`, `shared_entities`).
- Neighbors already present as direct semantic hits are skipped.
- Neighbors with disjoint declared person subjects vs the seed are skipped.
- Total supplemental hits across kinds are capped by `max_supplemental_hits`.

## Episodic support

- Anchors built from accepted memories tied to top semantic hits.
- Loads `get_structured_episode` and `get_raw_segments` **only** for anchor episode ids.
- Uses `select_episodic_support` (user segments only).
- Skips segment text whose normalized form matches existing `structured_episode` hits.

## Wiki context

- Uses caller-supplied `WikiContextEntry` objects or coercible mapping rows only.
- `select_wiki_context` anchored on the top semantic hit.
- No filesystem, network, or environment reads.

## Limitations

- Graph linking depends on reviewed metadata entity keys; sparse metadata yields fewer neighbors.
- Wiki subject-key compatibility can exclude otherwise relevant notes.
- Supplemental episodic support is bounded by policy defaults and anchor episode scope.
- Multiple supplemental kinds compete for the same `limit` slot after dedupe.

## Future work (Mira)

- Thread `RetrievalContextOptions` from CLI / API recall entrypoints.
- Optional per-intent toggles (for example, disable wiki on `INTENT_PLAN`).
- Metrics on supplemental hit acceptance during answer generation.
- Deeper graph seed selection using entity focus (`GraphNeighborSeed`).
