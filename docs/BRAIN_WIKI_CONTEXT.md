# Brain v2 wiki context (accepted-memory anchored)

## Purpose

`core/brain_v2/wiki_context.py` is a **pure, deterministic, database-independent**
selector for caller-supplied wiki entries. It can supplement accepted Brain v2
semantic retrieval with bounded wiki context.

This is a **selection foundation only**. It is not connected to production
retrieval, the orchestrator, filesystem wiki compilation, databases, or network
services.

## Trust and authority model

| Layer | Authority |
|-------|-----------|
| Active accepted semantic memory (`WikiContextAnchor`) | Required gate; only durable personal truth |
| Wiki context hits | Supplemental evidence only |
| Pending / rejected / retired / superseded anchors | No wiki results |
| Assistant-authored wiki rows | Never factual authority |

Wiki context must never authorize an answer that lacks an active accepted
semantic-memory anchor. Hits always set `is_supplemental=True` with
`supplemental_reason=accepted_memory_anchored_wiki`.

## API

| Type | Role |
|------|------|
| `WikiContextAnchor` | Active accepted memory gate (`memory_id`, `statement`, lifecycle, review status, optional `strength`, `subject_keys`) |
| `WikiContextEntry` | Caller-supplied wiki row with provenance fields |
| `WikiContextPolicy` | Caps and filters |
| `WikiContextScoreBreakdown` | Weighted score components |
| `WikiContextHit` | Immutable supplemental result |
| `select_wiki_context(...)` | Principal entry point |

```python
select_wiki_context(
    query,
    anchor,
    entries,
    policy=None,
    *,
    reference_time=None,
) -> tuple[WikiContextHit, ...]
```

Entries may be `WikiContextEntry` instances, mappings, or duck-typed objects.
Invalid or empty rows are ignored.

## Lifecycle gates

Selection runs only when:

1. Anchor is present with non-empty `memory_id` and `statement`
2. `lifecycle_status` is `active` (default excluded: `retired`, `superseded`)
3. `review_status` is exactly `accepted`

Pending, rejected, missing, retired, superseded, or otherwise inactive anchors
yield an empty tuple.

## Scoring formula

Component and final scores are clamped to `[0.0, 1.0]`:

```text
total =
  0.30 * lexical
+ 0.26 * anchor_relevance
+ 0.16 * entry_quality
+ 0.14 * recency
+ 0.14 * source_agreement
- penalties
```

| Factor | Meaning |
|--------|---------|
| Lexical | Query token overlap against title/section/text |
| Anchor relevance | Anchor strength plus token overlap with accepted statement |
| Entry quality | Explicit `quality` or informative-length heuristic |
| Recency | Age vs injected `reference_time` (`updated_at` or `created_at`; omitted clock, malformed, or future timestamp → `0.0`) |
| Source agreement | Highest when `anchor.memory_id` is in `source_memory_ids` |
| Penalties | Low-information short text; trailing questions |

Hits below `policy.min_score` are dropped.

## Deterministic ordering

1. Examine at most `max_entries_examined` caller rows
2. Score eligible rows and sort by score descending, then `section`, then `entry_id`
3. Deduplicate by `normalize_statement` in that sorted order
4. Enforce `max_per_section` and `max_per_source`
5. Return at most `max_hits`

Stable tie-breaking does not depend on Python hash randomization.

## Privacy constraints

- No database, filesystem, subprocess, network, AI, or environment access
- No private Brain DB inspection
- Caller supplies only authorized synthetic or already-loaded rows
- Text truncated; entry/result/section/source caps enforced
- When the anchor declares `subject_keys`, entries must declare at least one
  overlapping subject; missing or unrelated subjects are excluded fail-closed
- Tests and docs use synthetic labels only (`Owner A`, `City A`, `School A`,
  `Restaurant A`, `Guest B`)

## Integration contract

Future Mira-owned wiring into `retrieval.py` should:

1. After an **active accepted** semantic hit is chosen, build `WikiContextAnchor`
   from that memory (id, statement, lifecycle, review status, optional strength
   and subject keys).
2. Pass only preloaded, owner-scoped wiki entries already authorized for the
   session — never compile or read wiki files inside this selector.
3. Call `select_wiki_context` with an injected clock and tight policy.
4. Map hits into a supplemental context layer with scores capped **below**
   accepted semantic memories.
5. Keep `answer_from_accepted` free of wiki-only answers.

Do **not** treat this document as claiming production integration.

## Known limitations

- No filesystem wiki compilation or page graph
- No automatic subject extraction from free text (uses caller `subject_keys`)
- Among normalized duplicates, the highest-scoring hit wins; ties break by
  `section` then `entry_id` (order-independent)
- Not exported from `core/brain_v2/__init__.py` yet
- Not wired into orchestrator or retrieval

## Tests

```bash
.venv/bin/python -m pytest tests/test_brain_v2_wiki_context.py -q
```
