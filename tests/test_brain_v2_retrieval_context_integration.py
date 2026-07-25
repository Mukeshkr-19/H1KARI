"""Integration tests for supplemental graph/episodic/wiki retrieval."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.brain_v2.candidate_scoring import normalize_statement
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_lifecycle import LIFECYCLE_ACTIVE, LIFECYCLE_RETIRED
from core.brain_v2.memory_repair import MemoryRepairGate
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.retrieval import BrainV2Retrieval, RetrievalContextOptions
from core.brain_v2.schemas import SourceLinkedMemory, StructuredEpisode
from core.brain_v2.wiki_context import WikiContextEntry
from core.brain_v2.working_memory import WorkingMemory
from core.path_literals import EPISODES_DB

REF = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)


def _retrieval(store: EpisodeStore) -> BrainV2Retrieval:
    return BrainV2Retrieval(
        store, WorkingMemory(), neural_bridge=None, allow_neural_procedural=False
    )


def _accept(store: EpisodeStore, statement: str, episode_key: str = "ep") -> SourceLinkedMemory:
    episode_id = store.create_episode(episode_key)
    turn = statement if statement.lower().startswith("remember this:") else f"Remember this: {statement}"
    store.add_turn(episode_id, turn, is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    assert candidates, f"no candidates extracted for {turn!r}"
    return MemoryReviewGate(store).accept(candidates[0].candidate_id)


def _accept_with_metadata(
    store: EpisodeStore,
    statement: str,
    metadata: dict,
    *,
    episode_key: str = "ep",
) -> SourceLinkedMemory:
    linked = _accept(store, statement, episode_key=episode_key)
    meta = dict(linked.metadata or {})
    meta.update(metadata)
    meta.setdefault("lifecycle_status", LIFECYCLE_ACTIVE)
    updated = SourceLinkedMemory(
        memory_id=linked.memory_id,
        candidate_id=linked.candidate_id,
        episode_id=linked.episode_id,
        statement=linked.statement,
        source_segment_ids=list(linked.source_segment_ids or []),
        neural_node_key=linked.neural_node_key,
        accepted_at=linked.accepted_at,
        layer=linked.layer,
        metadata=meta,
    )
    return store.save_source_linked_memory(updated)


def test_default_retrieve_unchanged_without_supplemental_options(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _accept(store, "Owner A lives in City A.")
    baseline = _retrieval(store).retrieve("where does Owner A live?")
    again = _retrieval(store).retrieve(
        "where does Owner A live?",
        enable_graph_neighbors=False,
        enable_episodic_support=False,
    )
    assert [h.text for h in baseline.hits] == [h.text for h in again.hits]
    assert baseline.strategies == again.strategies


def test_supplemental_skipped_without_semantic_anchor(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    packet = _retrieval(store).retrieve(
        "Owner A City A live",
        enable_graph_neighbors=True,
        wiki_entries=[
            WikiContextEntry(
                entry_id="w1",
                text="Owner A notes about City A.",
                subject_keys=("owner a",),
                source_memory_ids=("missing",),
            )
        ],
    )
    assert not any((h.metadata or {}).get("is_supplemental") for h in packet.hits)


def test_retired_memory_cannot_anchor_or_neighbor(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    mid = _accept_with_metadata(
        store,
        "Owner A lives in City A.",
        {"location": "City A", "person": "Owner A", "candidate_type": "location"},
    ).memory_id
    MemoryRepairGate(store).retire(mid)
    packet = _retrieval(store).retrieve(
        "Owner A live City A",
        enable_graph_neighbors=True,
        limit=12,
    )
    assert not any(h.source == "source_linked" for h in packet.hits)
    assert not any((h.metadata or {}).get("is_supplemental") for h in packet.hits)


def test_graph_neighbor_same_subject_after_semantic_hit(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _accept_with_metadata(
        store,
        "Owner A lives in City A.",
        {"location": "City A", "candidate_type": "location", "person": "Owner A"},
        episode_key="home",
    )
    _accept_with_metadata(
        store,
        "Owner A prefers Restaurant A near City A.",
        {"location": "City A", "place": "Restaurant A", "candidate_type": "preference", "person": "Owner A"},
        episode_key="food",
    )
    packet = _retrieval(store).retrieve(
        "Owner A lives City A",
        enable_graph_neighbors=True,
        limit=12,
    )
    graph = [
        h
        for h in packet.hits
        if (h.metadata or {}).get("supplemental_kind") == "graph_neighbor"
    ]
    assert graph
    assert graph[0].metadata.get("authority") == "accepted_memory_anchor"
    assert graph[0].metadata.get("seed_memory_id")
    assert graph[0].metadata.get("path_edges") is not None
    assert graph[0].score < max(
        h.score for h in packet.hits if h.source == "source_linked"
    )
    assert "supplemental_graph_neighbors" in packet.strategies


def test_graph_excludes_unrelated_person_neighbor(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _accept_with_metadata(
        store,
        "Owner A lives in City A.",
        {"location": "City A", "candidate_type": "location", "person": "Owner A"},
        episode_key="owner",
    )
    _accept_with_metadata(
        store,
        "Guest B visits Restaurant A near City A.",
        {"location": "City A", "candidate_type": "location", "person": "Guest B"},
        episode_key="guest",
    )
    packet = _retrieval(store).retrieve(
        "Owner A lives City A",
        enable_graph_neighbors=True,
        limit=12,
    )
    graph_texts = " ".join(
        h.text
        for h in packet.hits
        if (h.metadata or {}).get("supplemental_kind") == "graph_neighbor"
    ).lower()
    assert "guest b" not in graph_texts


def test_graph_two_hop_and_score_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _accept_with_metadata(
        store,
        "Owner A lives in City A.",
        {"location": "City A", "person": "Owner A", "candidate_type": "location"},
        episode_key="a",
    )
    _accept_with_metadata(
        store,
        "Owner A works near School A in City A.",
        {
            "location": "City A",
            "organization": "School A",
            "person": "Owner A",
            "candidate_type": "education",
        },
        episode_key="b",
    )
    _accept_with_metadata(
        store,
        "Owner A keeps School A paperwork ready.",
        {"organization": "School A", "person": "Owner A", "candidate_type": "education"},
        episode_key="c",
    )
    packet = _retrieval(store).retrieve(
        "Owner A lives City A",
        context_options=RetrievalContextOptions(
            enable_graph_neighbors=True,
            graph_max_depth=2,
            graph_max_results=8,
        ),
        limit=20,
    )
    graph = [
        h
        for h in packet.hits
        if (h.metadata or {}).get("supplemental_kind") == "graph_neighbor"
    ]
    assert graph
    direct = max(h.score for h in packet.hits if h.source == "source_linked")
    assert all(h.score < direct for h in graph)
    assert all((h.metadata or {}).get("depth") in (1, 2) for h in graph)


def test_top_semantic_hits_excludes_supplemental(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _accept_with_metadata(
        store,
        "Owner A lives in City A.",
        {"location": "City A", "candidate_type": "location", "person": "Owner A"},
        episode_key="owner",
    )
    _accept_with_metadata(
        store,
        "Owner A prefers Restaurant A in City A.",
        {"location": "City A", "candidate_type": "preference", "person": "Owner A"},
        episode_key="food",
    )
    packet = _retrieval(store).retrieve(
        "Owner A live City A",
        enable_graph_neighbors=True,
        limit=12,
    )
    tops = packet.top_semantic_hits(5)
    assert tops
    assert all(not (h.metadata or {}).get("is_supplemental") for h in tops)
    assert all(h.source == "source_linked" for h in tops)


def test_answer_from_accepted_ignores_supplemental_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    owner = _accept(store, "Owner A lives in City A.")
    retrieval = _retrieval(store)
    answer = retrieval.answer_from_accepted("where does Owner A live?")
    assert answer
    assert "city a" in answer.lower()
    # Supplemental wiki must not appear in top_semantic_hits used for answers
    packet = retrieval.retrieve(
        "where does Owner A live?",
        wiki_entries=[
            WikiContextEntry(
                entry_id="w-only",
                text="Owner A secret wiki-only fact about Restaurant A.",
                source_memory_ids=(owner.memory_id,),
                subject_keys=("owner a",),
            )
        ],
        reference_time=REF,
        limit=12,
    )
    tops = packet.top_semantic_hits(5)
    assert tops
    assert all((h.metadata or {}).get("supplemental_kind") != "wiki_context" for h in tops)
    assert all(not (h.metadata or {}).get("is_supplemental") for h in tops)


def test_to_prompt_labels_supplemental_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _accept_with_metadata(
        store,
        "Owner A studies at School A in City A.",
        {
            "organization": "School A",
            "location": "City A",
            "candidate_type": "education",
            "person": "Owner A",
        },
        episode_key="school",
    )
    _accept_with_metadata(
        store,
        "Owner A keeps School A notes for City A.",
        {"organization": "School A", "candidate_type": "education", "person": "Owner A"},
        episode_key="notes",
    )
    packet = _retrieval(store).retrieve(
        "Owner A School A City A studies",
        enable_graph_neighbors=True,
        limit=12,
    )
    prompt = packet.to_prompt(limit=12)
    assert "supplemental graph_neighbor" in prompt


def test_episodic_support_user_segments_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    linked = _accept(
        store,
        "Remember this: Owner A likes Restaurant A in City A.",
        episode_key="food",
    )
    store.add_turn(linked.episode_id, "Assistant filler noted.", is_user=False)
    store.add_turn(
        linked.episode_id,
        "Owner A mentioned Restaurant A hours with Guest B.",
        is_user=True,
    )
    packet = _retrieval(store).retrieve(
        "Owner A Restaurant A Guest B",
        enable_episodic_support=True,
        reference_time=REF,
        limit=15,
    )
    episodic = [
        h
        for h in packet.hits
        if (h.metadata or {}).get("supplemental_kind") == "episodic_support"
    ]
    assert episodic
    assert all("Assistant filler" not in h.text for h in episodic)
    assert all((h.metadata or {}).get("anchor_memory_id") for h in episodic)
    assert "supplemental_episodic_support" in packet.strategies


def test_episodic_skips_structured_episode_duplicate_text(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    linked = _accept(
        store,
        "Remember this: Owner A likes Restaurant A downtown City A.",
        episode_key="dup",
    )
    structured = store.get_structured_episode(linked.episode_id)
    assert structured is not None
    summary = structured.summary or linked.statement
    store.save_structured_episode(
        StructuredEpisode(
            episode_id=structured.episode_id,
            session_id=structured.session_id,
            lifecycle_state=structured.lifecycle_state,
            title="Restaurant A visit",
            summary=summary,
            action_items=structured.action_items,
            events=structured.events,
            segment_count=structured.segment_count,
            started_at=structured.started_at,
            ended_at=structured.ended_at,
            metadata=structured.metadata,
        )
    )
    packet = _retrieval(store).retrieve(
        "Owner A Restaurant A downtown City A",
        enable_episodic_support=True,
        reference_time=REF,
        limit=15,
    )
    structured_hits = [h for h in packet.hits if h.source == "structured_episode"]
    episodic = [
        h
        for h in packet.hits
        if (h.metadata or {}).get("supplemental_kind") == "episodic_support"
    ]
    if structured_hits and episodic:
        for hit in episodic:
            assert normalize_statement(hit.text) not in {
                normalize_statement(s.text) for s in structured_hits
            }


def test_wiki_context_caller_supplied_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    owner = _accept_with_metadata(
        store,
        "Owner A studies at School A in City A.",
        {"organization": "School A", "candidate_type": "education", "person": "Owner A"},
    )
    entry = WikiContextEntry(
        entry_id="wiki-school-a",
        text="Owner A keeps School A paperwork organized for City A enrollment.",
        section="education",
        title="School A",
        source_memory_ids=(owner.memory_id,),
        subject_keys=("owner a",),
        updated_at="2026-03-10T10:00:00+00:00",
    )
    packet = _retrieval(store).retrieve(
        "Owner A School A City A",
        wiki_entries=[entry],
        reference_time=REF,
        limit=12,
    )
    wiki_hits = [
        h for h in packet.hits if (h.metadata or {}).get("supplemental_kind") == "wiki_context"
    ]
    assert wiki_hits
    assert wiki_hits[0].metadata.get("entry_id") == "wiki-school-a"
    assert wiki_hits[0].metadata.get("source_memory_ids")
    assert "supplemental_wiki_context" in packet.strategies


def test_wiki_assistant_and_unrelated_subject_excluded(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    owner = _accept_with_metadata(
        store,
        "Owner A studies at School A in City A.",
        {"organization": "School A", "person": "Owner A"},
    )
    entries = [
        WikiContextEntry(
            entry_id="bot",
            text="Owner A School A assistant draft note in City A.",
            source_memory_ids=(owner.memory_id,),
            subject_keys=("owner a",),
            is_assistant_authored=True,
        ),
        WikiContextEntry(
            entry_id="other",
            text="Guest B School A schedule note.",
            source_memory_ids=(owner.memory_id,),
            subject_keys=("guest b",),
        ),
        WikiContextEntry(
            entry_id="ok",
            text="Owner A School A enrollment checklist for City A.",
            source_memory_ids=(owner.memory_id,),
            subject_keys=("owner a",),
        ),
    ]
    packet = _retrieval(store).retrieve(
        "Owner A School A City A",
        wiki_entries=entries,
        reference_time=REF,
        limit=12,
    )
    wiki_ids = [
        h.metadata.get("entry_id")
        for h in packet.hits
        if (h.metadata or {}).get("supplemental_kind") == "wiki_context"
    ]
    assert wiki_ids == ["ok"]


def test_malformed_wiki_entries_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _accept(store, "Owner A lives in City A.")
    packet = _retrieval(store).retrieve(
        "Owner A City A live",
        wiki_entries="not-a-sequence",
        limit=10,
    )
    assert any(h.source == "source_linked" for h in packet.hits)
    assert not any((h.metadata or {}).get("supplemental_kind") == "wiki_context" for h in packet.hits)


def test_supplemental_exception_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _accept(store, "Owner A lives in City A.")
    retrieval = _retrieval(store)

    def _boom(*_a, **_k):
        raise RuntimeError("selector failed")

    monkeypatch.setattr(retrieval, "_graph_supplemental_hits", _boom)
    packet = retrieval.retrieve(
        "Owner A City A live",
        enable_graph_neighbors=True,
        limit=10,
    )
    assert any(h.source == "source_linked" for h in packet.hits)


def test_deterministic_ordering_input_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    owner = _accept_with_metadata(
        store,
        "Owner A studies at School A in City A.",
        {"organization": "School A", "person": "Owner A"},
    )
    entries_a = [
        WikiContextEntry(
            entry_id="w-b",
            text="Owner A School A lab notes for City A.",
            source_memory_ids=(owner.memory_id,),
            subject_keys=("owner a",),
            section="labs",
        ),
        WikiContextEntry(
            entry_id="w-a",
            text="Owner A School A enrollment notes for City A.",
            source_memory_ids=(owner.memory_id,),
            subject_keys=("owner a",),
            section="enroll",
        ),
    ]
    first = _retrieval(store).retrieve(
        "Owner A School A City A",
        wiki_entries=entries_a,
        reference_time=REF,
        limit=12,
    )
    second = _retrieval(store).retrieve(
        "Owner A School A City A",
        wiki_entries=list(reversed(entries_a)),
        reference_time=REF,
        limit=12,
    )
    wiki_first = [
        h.metadata.get("entry_id")
        for h in first.hits
        if (h.metadata or {}).get("supplemental_kind") == "wiki_context"
    ]
    wiki_second = [
        h.metadata.get("entry_id")
        for h in second.hits
        if (h.metadata or {}).get("supplemental_kind") == "wiki_context"
    ]
    assert wiki_first == wiki_second


def test_max_supplemental_hits_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    owner = _accept_with_metadata(
        store,
        "Owner A studies at School A in City A.",
        {"organization": "School A", "person": "Owner A"},
    )
    entries = [
        WikiContextEntry(
            entry_id=f"w{i}",
            text=f"Owner A School A note {i} about City A campus life.",
            source_memory_ids=(owner.memory_id,),
            subject_keys=("owner a",),
            section=f"sec-{i}",
        )
        for i in range(6)
    ]
    packet = _retrieval(store).retrieve(
        "Owner A School A City A",
        context_options=RetrievalContextOptions(
            wiki_entries=tuple(entries),
            max_supplemental_hits=2,
            reference_time=REF,
        ),
        limit=20,
    )
    wiki_hits = [
        h for h in packet.hits if (h.metadata or {}).get("supplemental_kind") == "wiki_context"
    ]
    assert len(wiki_hits) <= 2


def test_naive_reference_time_ignored_for_merge(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    owner = _accept_with_metadata(
        store,
        "Owner A studies at School A.",
        {"organization": "School A", "person": "Owner A"},
    )
    entry = WikiContextEntry(
        entry_id="w-ref",
        text="Owner A School A enrollment checklist.",
        source_memory_ids=(owner.memory_id,),
        subject_keys=("owner a",),
        updated_at="2026-03-10T10:00:00+00:00",
    )
    naive = datetime(2026, 3, 15, 12, 0)
    packet = _retrieval(store).retrieve(
        "Owner A School A",
        wiki_entries=[entry],
        reference_time=naive,
        limit=12,
    )
    assert any((h.metadata or {}).get("supplemental_kind") == "wiki_context" for h in packet.hits)


def test_no_store_writes_during_supplemental(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    owner = _accept_with_metadata(
        store,
        "Owner A lives in City A.",
        {"location": "City A", "person": "Owner A"},
    )
    before = store.get_active_accepted_memories(limit=50)
    packet = _retrieval(store).retrieve(
        "Owner A City A live",
        enable_graph_neighbors=True,
        enable_episodic_support=True,
        wiki_entries=[
            WikiContextEntry(
                entry_id="w1",
                text="Owner A City A neighborhood note.",
                source_memory_ids=(owner.memory_id,),
                subject_keys=("owner a",),
            )
        ],
        reference_time=REF,
        limit=15,
    )
    after = store.get_active_accepted_memories(limit=50)
    assert [m.memory_id for m in before] == [m.memory_id for m in after]
    assert packet.hits


def test_sqlite_side_effects_blocked_outside_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    owner = _accept(store, "Owner A lives in City A.")
    import os
    import subprocess

    def _blocked(*_a, **_k):
        raise AssertionError("side effect forbidden")

    monkeypatch.setattr(subprocess, "Popen", _blocked)
    monkeypatch.setattr(subprocess, "run", _blocked)
    monkeypatch.setitem(os.environ, "SHOULD_NOT_MATTER", "1")
    packet = _retrieval(store).retrieve(
        "Owner A City A live",
        wiki_entries=[
            WikiContextEntry(
                entry_id="w1",
                text="Owner A City A note.",
                source_memory_ids=(owner.memory_id,),
                subject_keys=("owner a",),
            )
        ],
        reference_time=REF,
    )
    assert any(h.source == "source_linked" for h in packet.hits)


def test_dedupe_prefers_direct_semantic_over_supplemental(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _accept_with_metadata(
        store,
        "Owner A lives in City A.",
        {"location": "City A", "candidate_type": "location", "person": "Owner A"},
        episode_key="a",
    )
    _accept_with_metadata(
        store,
        "Owner A prefers Restaurant A in City A.",
        {"location": "City A", "candidate_type": "preference", "person": "Owner A"},
        episode_key="b",
    )
    packet = _retrieval(store).retrieve(
        "Owner A live City A",
        enable_graph_neighbors=True,
        limit=20,
    )
    by_mid = {}
    for h in packet.hits:
        mid = (h.metadata or {}).get("memory_id")
        if mid:
            by_mid.setdefault(mid, []).append(h)
    for _mid, group in by_mid.items():
        if len(group) > 1:
            assert any(not (g.metadata or {}).get("is_supplemental") for g in group)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"graph_max_results": -1},
        {"max_supplemental_hits": -1},
        {"graph_score_cap": float("nan")},
        {"wiki_score_cap": 1.1},
        {"supplemental_epsilon": -0.1},
        {"reference_time": datetime(2026, 3, 15, 12, 0)},
    ],
)
def test_context_options_reject_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        RetrievalContextOptions(**kwargs)
