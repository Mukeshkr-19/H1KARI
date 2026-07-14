"""Neural-memory graph algorithms must preserve paths, state, and rank mass."""

from __future__ import annotations

import pytest

from core.neural_memory.config import config
from core.neural_memory.memory_graph import MemoryGraph
from core.neural_memory.models import EdgeType, MemoryEdge, MemoryNode, NodeType
from core.neural_memory.storage import storage


@pytest.fixture()
def graph_store(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    monkeypatch.setattr(config, "BRAIN_DIR", brain)
    monkeypatch.setattr(config, "DB_PATH", brain / "memory.db")
    monkeypatch.setattr(config, "CONFIG_FILE", brain / "config.json")
    monkeypatch.setattr(config, "CACHE_DIR", brain / "cache")
    monkeypatch.setattr(config, "EMBEDDINGS_DIR", brain / "embeddings")
    monkeypatch.setattr(config, "LOGS_DIR", brain / "logs")
    monkeypatch.setattr(config, "BACKUPS_DIR", brain / "backups")
    monkeypatch.setattr(config, "_config", {"version": 1, "user_id": "test-user"})
    config.ensure_directories()
    assert storage.initialize() is True

    graph = MemoryGraph()
    graph.storage = storage
    return graph, storage


def test_find_paths_keeps_converging_alternatives(graph_store):
    graph, store = graph_store
    ids = _nodes(store, "A", "B", "C", "D", "E")
    _edge(store, ids["A"], ids["B"])
    _edge(store, ids["A"], ids["C"])
    _edge(store, ids["B"], ids["D"])
    _edge(store, ids["C"], ids["D"])
    _edge(store, ids["D"], ids["E"])

    paths = graph.find_paths(ids["A"], ids["E"], max_length=5)

    assert [ids["A"], ids["B"], ids["D"], ids["E"]] in paths
    assert [ids["A"], ids["C"], ids["D"], ids["E"]] in paths


def test_bridges_use_alternate_routes_without_mutating_edges(graph_store):
    graph, store = graph_store
    ids = _nodes(store, "A", "B", "C", "D")
    _edge(store, ids["A"], ids["B"])
    _edge(store, ids["B"], ids["C"])
    _edge(store, ids["C"], ids["A"])
    _edge(store, ids["C"], ids["D"])
    _edge(store, ids["D"], ids["D"])

    assert graph.get_bridges() == [(ids["C"], ids["D"])]
    rows = store.fetch_all("SELECT is_archived FROM edges ORDER BY id")
    assert [row["is_archived"] for row in rows] == [0, 0, 0, 0, 0]


def test_page_rank_uses_direction_and_preserves_rank_mass(graph_store):
    graph, store = graph_store
    ids = _nodes(store, "A", "B", "C", "D")
    _edge(store, ids["A"], ids["B"])
    _edge(store, ids["B"], ids["C"])
    _edge(store, ids["D"], ids["B"])

    ranks = graph.get_page_rank(iterations=50)

    assert ranks[ids["C"]] > ranks[ids["B"]] > ranks[ids["A"]]
    assert sum(ranks.values()) == pytest.approx(1.0)


def test_page_rank_does_not_promote_zero_weight_edge(graph_store):
    graph, store = graph_store
    ids = _nodes(store, "A", "B", "C")
    _edge(store, ids["A"], ids["B"], weight=0.0)
    _edge(store, ids["A"], ids["C"], weight=1.0)

    ranks = graph.get_page_rank(iterations=50)

    assert ranks[ids["C"]] > ranks[ids["B"]]


def _nodes(store, *names):
    return {
        name: store.insert_node(
            MemoryNode(node_type=NodeType.FACT.value, name=name, user_id="test-user")
        )
        for name in names
    }


def _edge(store, source_id, target_id, *, weight=1.0, bidirectional=False):
    return store.insert_edge(
        MemoryEdge(
            source_id=source_id,
            target_id=target_id,
            edge_type=EdgeType.LINKED_TO.value,
            weight=weight,
            bidirectional=bidirectional,
            user_id="test-user",
        )
    )
