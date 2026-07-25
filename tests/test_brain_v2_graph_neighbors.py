"""Tests for core/brain_v2/graph_neighbors.py (synthetic fixtures only)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.brain_v2.graph_neighbors import (
    BrainMemoryGraph,
    GraphEdge,
    GraphNeighborHit,
    GraphNeighborSeed,
    GraphPath,
    build_memory_graph,
    find_graph_neighbors,
)
from core.brain_v2.schemas import SourceLinkedMemory


def _memory(
    memory_id: str,
    statement: str,
    metadata: dict | None = None,
    *,
    lifecycle_status: str = "active",
) -> SourceLinkedMemory:
    meta = dict(metadata or {})
    meta.setdefault("lifecycle_status", lifecycle_status)
    return SourceLinkedMemory(
        memory_id=memory_id,
        candidate_id=f"cand-{memory_id}",
        episode_id=f"ep-{memory_id}",
        statement=statement,
        source_segment_ids=[],
        metadata=meta,
    )


def test_direct_person_neighbor():
    seed = _memory("m1", "My friend Jamie likes coffee.", {"person": "Jamie"})
    other = _memory("m2", "Jamie works at University A.", {"person": "Jamie"})
    hits = find_graph_neighbors(seed, [seed, other])
    assert len(hits) == 1
    assert hits[0].memory_id == "m2"
    assert hits[0].edge_type == "person"
    assert hits[0].depth == 1


def test_direct_relation_neighbor():
    seed = _memory("m1", "My sister Maya studies medicine.", {"relation": "sister"})
    other = _memory("m2", "My sister Maya lives in City A.", {"relation": "sister"})
    hits = find_graph_neighbors(seed, [seed, other])
    assert len(hits) == 1
    assert hits[0].memory_id == "m2"
    assert hits[0].edge_type == "relation"


def test_shared_organization_neighbor():
    seed = _memory("m1", "I studied at University A.", {"organization": "University A"})
    other = _memory("m2", "Maya studied at University A.", {"organization": "University A"})
    hits = find_graph_neighbors(seed, [seed, other])
    assert len(hits) == 1
    assert hits[0].memory_id == "m2"
    assert hits[0].edge_type == "organization"


def test_shared_location_neighbor():
    seed = _memory("m1", "I live in City A.", {"location": "City A"})
    other = _memory("m2", "Maya lives in City A.", {"location": "City A"})
    hits = find_graph_neighbors(seed, [seed, other])
    assert len(hits) == 1
    assert hits[0].memory_id == "m2"
    assert hits[0].edge_type == "location"


def test_one_hop_traversal():
    a = _memory("a", "I know Jamie.", {"person": "Jamie"})
    b = _memory("b", "Jamie works at University A.", {"person": "Jamie", "organization": "University A"})
    c = _memory("c", "University A is in City A.", {"organization": "University A"})
    hits = find_graph_neighbors(a, [a, b, c])
    memory_ids = {h.memory_id for h in hits}
    assert "b" in memory_ids
    assert "c" in memory_ids


def test_two_hop_traversal():
    a = _memory("a", "I know Jamie.", {"person": "Jamie"})
    b = _memory("b", "Jamie works at University A.", {"person": "Jamie", "organization": "University A"})
    c = _memory("c", "Maya studies at University A.", {"organization": "University A"})
    # Seed is a; c is only reachable via b through organization.
    hits = find_graph_neighbors(a, [a, b, c])
    two_hop = [h for h in hits if h.memory_id == "c"]
    assert two_hop
    assert two_hop[0].depth == 2


def test_depth_penalty():
    a = _memory("a", "I know Jamie.", {"person": "Jamie"})
    b = _memory("b", "Jamie works at University A.", {"person": "Jamie", "organization": "University A"})
    c = _memory("c", "Maya studies at University A.", {"organization": "University A"})
    hits = find_graph_neighbors(a, [a, b, c])
    scores = {h.memory_id: h.score for h in hits}
    assert scores["b"] > scores["c"]


def test_cycle_prevention():
    a = _memory("a", "A", {"person": "Jamie"})
    b = _memory("b", "B", {"person": "Jamie", "location": "City A"})
    c = _memory("c", "C", {"location": "City A", "person": "Jamie"})
    hits = find_graph_neighbors(a, [a, b, c])
    memory_ids = [h.memory_id for h in hits]
    # The seed itself should never be returned, and each neighbor appears once.
    assert "a" not in memory_ids
    assert memory_ids.count("b") == 1
    assert memory_ids.count("c") == 1


def test_duplicate_suppression():
    # a and b share both person and location; should yield exactly one hit.
    a = _memory("a", "I met Jamie in City A.", {"person": "Jamie", "location": "City A"})
    b = _memory("b", "Jamie lives in City A.", {"person": "Jamie", "location": "City A"})
    hits = find_graph_neighbors(a, [a, b])
    assert len(hits) == 1
    assert hits[0].memory_id == "b"


def test_deterministic_ordering():
    a = _memory("a", "A", {"person": "Jamie"})
    b = _memory("b", "B", {"person": "Jamie"})
    c = _memory("c", "C", {"person": "Jamie"})
    run1 = [h.memory_id for h in find_graph_neighbors(a, [a, b, c])]
    run2 = [h.memory_id for h in find_graph_neighbors(a, [a, b, c])]
    assert run1 == run2


def test_result_limit():
    seed = _memory("seed", "Seed", {"person": "Jamie"})
    others = [_memory(f"m{i}", f"Memory {i}", {"person": "Jamie"}) for i in range(20)]
    hits = find_graph_neighbors(seed, [seed] + others, max_results=5)
    assert len(hits) == 5


def test_retired_exclusion():
    seed = _memory("seed", "Seed", {"person": "Jamie"})
    active = _memory("active", "Active", {"person": "Jamie"})
    retired = _memory("retired", "Retired", {"person": "Jamie"}, lifecycle_status="retired")
    hits = find_graph_neighbors(seed, [seed, active, retired])
    assert len(hits) == 1
    assert hits[0].memory_id == "active"


def test_superseded_exclusion():
    seed = _memory("seed", "Seed", {"person": "Jamie"})
    active = _memory("active", "Active", {"person": "Jamie"})
    superseded = _memory("superseded", "Old", {"person": "Jamie"}, lifecycle_status="superseded")
    hits = find_graph_neighbors(seed, [seed, active, superseded])
    assert len(hits) == 1
    assert hits[0].memory_id == "active"


def test_missing_metadata():
    seed = _memory("seed", "Seed", metadata={})
    other = _memory("other", "Other", metadata={})
    hits = find_graph_neighbors(seed, [seed, other])
    assert hits == []


def test_malformed_metadata():
    seed = SourceLinkedMemory(
        memory_id="seed",
        candidate_id="c",
        episode_id="e",
        statement="Seed",
        source_segment_ids=[],
        metadata=None,  # type: ignore[arg-type]
    )
    other = SourceLinkedMemory(
        memory_id="other",
        candidate_id="c2",
        episode_id="e2",
        statement="Other",
        source_segment_ids=[],
        metadata=None,  # type: ignore[arg-type]
    )
    # Should not raise.
    hits = find_graph_neighbors(seed, [seed, other])
    assert hits == []


def test_no_substring_collision():
    a = _memory("a", "A", {"location": "City A"})
    b = _memory("b", "B", {"location": "City AB"})
    hits = find_graph_neighbors(a, [a, b])
    assert hits == []


def test_disconnected_components():
    a = _memory("a", "A", {"person": "Jamie"})
    b = _memory("b", "B", {"person": "Jamie"})
    c = _memory("c", "C", {"person": "Maya"})
    d = _memory("d", "D", {"person": "Maya"})
    # Querying from component {a,b} should not reach {c,d}.
    hits = find_graph_neighbors(a, [a, b, c, d])
    assert {h.memory_id for h in hits} == {"b"}


def test_no_cross_relation_leakage():
    a = _memory("a", "A", {"relation": "sister"})
    b = _memory("b", "B", {"relation": "sister"})
    c = _memory("c", "C", {"relation": "brother"})
    hits = find_graph_neighbors(a, [a, b, c])
    assert {h.memory_id for h in hits} == {"b"}


def test_empty_input():
    seed = _memory("seed", "Seed", {"person": "Jamie"})
    assert find_graph_neighbors(seed, []) == []
    assert find_graph_neighbors([], [seed]) == []


def test_direct_outranks_two_hop():
    # Seed a links to b (person) and c (organization).
    # b links to c via organization, so c is reachable both directly and via b.
    a = _memory("a", "A", {"person": "Jamie", "organization": "University A"})
    b = _memory("b", "B", {"person": "Jamie", "organization": "University B"})
    c = _memory("c", "C", {"organization": "University A"})
    hits = find_graph_neighbors(a, [a, b, c])
    c_hit = [h for h in hits if h.memory_id == "c"][0]
    assert c_hit.depth == 1
    assert c_hit.score == 0.75


def test_score_bounds():
    a = _memory("a", "A", {"person": "Jamie"})
    b = _memory("b", "B", {"person": "Jamie", "location": "City A"})
    c = _memory("c", "C", {"location": "City A"})
    hits = find_graph_neighbors(a, [a, b, c])
    for hit in hits:
        assert 0.0 <= hit.score <= 1.0


def test_provenance_path():
    a = _memory("a", "A", {"person": "Jamie"})
    b = _memory("b", "B", {"person": "Jamie", "organization": "University A"})
    c = _memory("c", "C", {"organization": "University A"})
    hits = find_graph_neighbors(a, [a, b, c])
    two_hop = [h for h in hits if h.memory_id == "c"][0]
    assert two_hop.path.seed_memory_id == "a"
    assert two_hop.path.target_memory_id == "c"
    assert two_hop.path.memory_ids == ("a", "b", "c")
    assert all(isinstance(e, GraphEdge) for e in two_hop.path.edges)


def test_no_database_access_in_source():
    source = Path(__file__).parent.parent / "core" / "brain_v2" / "graph_neighbors.py"
    text = source.read_text(encoding="utf-8")
    assert "sqlite" not in text.lower()
    assert "EpisodeStore" not in text


def test_no_final_answer_generation():
    seed = _memory("seed", "Seed", {"person": "Jamie"})
    other = _memory("other", "Other", {"person": "Jamie"})
    hits = find_graph_neighbors(seed, [seed, other])
    assert isinstance(hits, list)
    assert not any(isinstance(h, str) for h in hits)
    for hit in hits:
        assert isinstance(hit, GraphNeighborHit)


def test_stable_results_across_repeated_calls():
    seed = _memory("seed", "Seed", {"person": "Jamie"})
    others = [_memory(f"m{i}", f"M{i}", {"person": "Jamie"}) for i in range(5)]
    run1 = find_graph_neighbors(seed, [seed] + others, max_results=10)
    run2 = find_graph_neighbors(seed, [seed] + others, max_results=10)
    assert [h.memory_id for h in run1] == [h.memory_id for h in run2]
    assert [h.score for h in run1] == [h.score for h in run2]


def test_candidate_type_does_not_create_cross_type_edge():
    # Two memories share only candidate_type; they should not be neighbors.
    a = _memory("a", "A", {"candidate_type": "relation", "relation": "sister"})
    b = _memory("b", "B", {"candidate_type": "relation", "relation": "brother"})
    hits = find_graph_neighbors(a, [a, b])
    assert hits == []


def test_build_memory_graph_excludes_inactive():
    active = _memory("active", "Active", {"person": "Jamie"})
    retired = _memory("retired", "Old", {"person": "Jamie"}, lifecycle_status="retired")
    graph = build_memory_graph([active, retired])
    assert "active" in graph.nodes
    assert "retired" not in graph.nodes


def test_graph_neighbor_seed_wrapper():
    seed = _memory("seed", "Seed", {"person": "Jamie"})
    other = _memory("other", "Other", {"person": "Jamie"})
    wrapper = GraphNeighborSeed(memory=seed, entity_type="person", entity_key="jamie")
    hits = find_graph_neighbors(wrapper, [seed, other])
    assert len(hits) == 1


def test_graph_neighbor_seed_focus_restricts_first_hop():
    seed = _memory(
        "seed", "Seed", {"person": "Jamie", "location": "City A"}
    )
    person = _memory("person", "Person", {"person": "Jamie"})
    location = _memory("location", "Location", {"location": "City A"})
    wrapper = GraphNeighborSeed(
        memory=seed,
        entity_type="person",
        entity_key="Jamie",
    )
    hits = find_graph_neighbors(wrapper, [seed, person, location], max_depth=1)
    assert [hit.memory_id for hit in hits] == ["person"]


def test_graph_neighbor_seed_requires_complete_focus():
    seed = _memory("seed", "Seed", {"person": "Jamie"})
    wrapper = GraphNeighborSeed(memory=seed, entity_type="person")
    with pytest.raises(ValueError, match="supplied together"):
        find_graph_neighbors(wrapper, [seed])


def test_graph_neighbor_bounds_are_validated():
    seed = _memory("seed", "Seed", {"person": "Jamie"})
    assert find_graph_neighbors(seed, [seed], max_results=0) == []
    with pytest.raises(ValueError, match="max_depth"):
        find_graph_neighbors(seed, [seed], max_depth=3)


def test_replacing_duplicate_memory_id_removes_stale_edges():
    old = _memory("same", "Old", {"person": "Jamie"})
    replacement = _memory("same", "New", {"person": "Morgan"})
    jamie = _memory("jamie", "Jamie", {"person": "Jamie"})
    graph = build_memory_graph([old, replacement, jamie])
    assert graph.neighbor_memory_ids("jamie", "person", "jamie") == []
