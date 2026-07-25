# Brain v2 episodic support (accepted-memory anchored)

## Purpose

`core/brain_v2/episodic_support.py` is a **pure, database-independent** selector that
returns bounded transcript support hits linked to an **active accepted** Brain v2
memory. It lets future retrieval supplement reviewed semantic truth with
controlled episodic context without making episodes an independent source of
durable personal fact.

This module is **not** wired into production retrieval or the orchestrator yet.
Callers must pass structured episodes and transcript segments themselves.

## Accepted-anchor requirement

Episodic support runs only when the caller supplies an `EpisodicSupportAnchor`
with:

- a non-empty `memory_id`
- `lifecycle_status=active` (see lifecycle exclusions below)
- an explicit episode link via `episode_id` and/or overlapping
  `source_segment_ids`

If the anchor is missing, inactive, or unlinked to the supplied episode/segment
bundle, `select_episodic_support` returns an empty tuple.

## Subordinate authority

Episodic hits are always supplemental:

- `EpisodicSupportHit.is_supplemental` is always `True`
- `supplemental_reason` records why the hit is support-only
  (`accepted_memory_linked_episode` or `anchor_source_segment_overlap`)
- scores are ranking aids for support context, not truth authority
- the module never answers personal recall, never elevates pending/rejected
  candidates, and never reactivates retired or superseded memories

Accepted source-linked semantic memories remain the only durable personal truth
layer in Brain v2. Episodic support must stay below that layer when integrated.

## Scoring

`EpisodicScoreBreakdown` combines clamped components (final total in `[0.0, 1.0]`):

| Factor | Role |
|--------|------|
| Lexical | Query token overlap with segment text |
| Anchor strength | Optional anchor `strength` plus overlap with the accepted statement; boost when the segment is in `source_segment_ids` |
| Recency | Age vs injected `reference_time` (malformed timestamps → `0.0`) |
| Source quality | User-authored informative segments score higher |
| Penalties | Low-information short utterances with weak lexical match |

Ordering is deterministic: score descending, then `episode_id`, then
`segment_id`. Duplicate normalized statements are suppressed.

Default policy also excludes casual filler and question-only segments so support
stays closer to declarative owner evidence.

## Provenance

Each hit preserves:

- `episode_id`, `segment_id`
- `anchor_memory_id`
- optional `session_id`, `speaker_label`, `started_at`
- truncated segment `text` (bounded by policy)
- score breakdown `reasons`

The selector does not invent narrative answers or strip provenance.

## Lifecycle exclusions

By default, anchors with `retired` or `superseded` lifecycle status yield no
support. Policy may extend `excluded_lifecycle_statuses`. Callers may also pass
`excluded_episode_ids` (for example episodes tied only to inactive accepted
rows) so those episodes never contribute support.

## Privacy

- No database open, no filesystem reads, no network, no general AI.
- No legacy conversation-log or wiki reads.
- Callers supply only the episode/segment rows they already authorize.
- Segment text is truncated; episode and segment counts are capped.
- Synthetic fixtures only in tests and docs (`Owner A`, `City A`, `School A`,
  `Restaurant A`).

## API

| Type | Role |
|------|------|
| `EpisodicSupportAnchor` | Accepted memory anchor |
| `EpisodicSupportPolicy` | Caps, filters, exclusions |
| `EpisodicScoreBreakdown` | Weighted score components |
| `EpisodicSupportHit` | One supplemental support row |
| `select_episodic_support(...)` | Pure selection entrypoint |

```python
select_episodic_support(
    query,
    anchor,
    episodes,
    segments,
    policy=None,
    *,
    reference_time=None,
) -> tuple[EpisodicSupportHit, ...]
```

Episodes and segments may be `StructuredEpisode` / `TranscriptSegment` or any
duck-typed objects with the same fields.

## Integration plan (future; not implemented here)

Recommended future wiring (do **not** treat as shipped):

1. In retrieval, after an **active accepted** semantic hit is chosen, build an
   `EpisodicSupportAnchor` from that memory (`memory_id`, `episode_id`,
   `source_segment_ids`, statement, lifecycle, optional strength).
2. Load only linked structured episodes and their transcript segments from the
   already-open store path used by retrieval (or pass in-memory rows from tests).
3. Call `select_episodic_support` with an injected clock and a tight policy.
4. Map hits into episodic-layer context with scores capped below semantic
   accepted-memory scores.
5. Keep `answer_from_accepted` free of episodic-only answers.

Shared runtime files such as `retrieval.py` and `orchestrator.py` were
intentionally left unchanged by this work.

## Non-goals

- Production retrieval or orchestrator integration
- Independent personal-fact answers from episodes
- Pending/rejected candidate elevation
- Assistant text as owner truth
- Unlimited transcript injection
- Persistence, model calls, or database access inside this module
- Wiki writeback or legacy neural personal recall

## Tests

```bash
.venv/bin/python -m pytest tests/test_brain_v2_episodic_support.py -q
```
