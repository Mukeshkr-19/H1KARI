"""Regression tests for neural_memory hardening (filters, sqlite rows, compaction)."""

import sqlite3

import pytest

from core.neural_memory.extraction_filters import (
    filter_entity_triples,
    filter_preference_subject,
    is_allowed_entity_name,
)
from core.neural_memory.sqlite_rows import row_as_dict
from core.neural_memory.memory_compiler import MemoryCompiler
from core.neural_memory.consolidation import consolidation_engine
from core.neural_memory.storage import MemoryStorage
from core.neural_memory.config import config
from core.neural_memory.models import Session


def test_row_as_dict_sqlite_row():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    cur = con.execute("SELECT 7 AS turn_count, 'abc' AS session_id")
    row = cur.fetchone()
    d = row_as_dict(row)
    assert d["turn_count"] == 7
    assert d["session_id"] == "abc"


def test_row_as_dict_plain_dict():
    assert row_as_dict({"a": 1}) == {"a": 1}
    assert row_as_dict(None) == {}


def test_stopwords_rejected_as_entities():
    assert is_allowed_entity_name("the") is False
    assert is_allowed_entity_name("labeled") is False
    assert is_allowed_entity_name("a") is False
    assert is_allowed_entity_name("HIKARI") is True


def test_filter_entity_triples_dedupes():
    raw = [
        ("PROJECT", "foo", "c1"),
        ("PROJECT", "foo", "c2"),
        ("PROJECT", "the", "bad"),
    ]
    out = filter_entity_triples(raw)
    assert len(out) == 1
    assert out[0][1] == "foo"


def test_filter_preference_subject_strips_junk():
    assert filter_preference_subject("the labeled") is None
    assert filter_preference_subject("dark mode") == "dark"


def test_compiler_skips_junk_project_capture():
    c = MemoryCompiler()
    nodes, _ = c.compile("project: the labeled", "", {"user_id": "u1"})
    names = [n.name for n in nodes]
    assert "the" not in names
    assert "labeled" not in names


@pytest.fixture()
def isolated_neural_memory(tmp_path, monkeypatch):
    """Fresh SQLite + config paths (does not touch live brain directory)."""
    brain = tmp_path / "brain"
    brain.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "BRAIN_DIR", brain)
    monkeypatch.setattr(config, "DB_PATH", brain / "mem.db")
    monkeypatch.setattr(config, "CONFIG_FILE", brain / "config.json")
    monkeypatch.setattr(config, "CACHE_DIR", brain / "cache")
    monkeypatch.setattr(config, "EMBEDDINGS_DIR", brain / "emb")
    monkeypatch.setattr(config, "LOGS_DIR", brain / "logs")
    monkeypatch.setattr(config, "BACKUPS_DIR", brain / "bak")
    monkeypatch.setattr(config, "_config", {"version": 1, "user_id": "utest"})
    config.ensure_directories()

    MemoryStorage._instance = None
    from core.neural_memory.storage import storage as st

    assert st.initialize() is True
    yield st


def test_session_compaction_row_handling(isolated_neural_memory, monkeypatch):
    st = isolated_neural_memory
    sid = "sess-compact-1"
    st.insert_session(
        Session(session_id=sid, user_id="utest", turn_count=12, metadata={})
    )

    consolidation_engine.storage = st
    res = consolidation_engine.session_compaction(sid, summary="unit test summary")
    assert res["status"] == "completed"
    assert "episode_id" in res


def test_extract_sister_relationship(isolated_neural_memory):
    c = MemoryCompiler()
    nodes = c.extract_people_and_relationships(
        "I am Maya, Alex's sister. Remember this.",
        "utest",
    )
    names = {n.name for n in nodes}
    assert "Maya" in names
    assert any("sister" in (n.content or "").lower() for n in nodes)


def test_extract_parent_name_statement(isolated_neural_memory):
    c = MemoryCompiler()
    nodes = c.extract_people_and_relationships(
        "my dads name is Rowan and he lives in Lakeside",
        "utest",
    )
    texts = {f"{n.name} {n.content}".lower() for n in nodes}
    assert any("rowan" in text and "dad" in text for text in texts)


def test_family_retrieval_finds_sister_fact(isolated_neural_memory):
    from core.neural_memory.memory_compiler import compiler
    from core.neural_memory.retrieval_engine import retrieval_engine

    compiler.compile_and_store(
        "I am Maya, Alex's sister. Remember this.",
        "Got it, I'll remember.",
        {"user_id": "utest"},
    )
    ctx = retrieval_engine.retrieve("what was my sister talking to you?", user_id="utest")
    assert ctx.relevant_nodes
    assert any(
        "sister" in f"{n.name} {n.content or ''}".lower() for n in ctx.relevant_nodes
    )


def test_learn_from_text_uses_public_api(monkeypatch):
    """Bridge must not deep-import storage/compiler for learn path."""
    from core.neural_memory_bridge import learn_from_text

    calls = []

    def fake_ingest(text, user_id=None):
        calls.append((text, user_id))
        return {"success": True, "mock": True}

    monkeypatch.setattr(
        "core.neural_memory_bridge.ingest_unstructured_text", fake_ingest
    )
    out = learn_from_text("prefer vim", user_id="utest")
    assert out.get("success") is True
    assert calls == [("prefer vim", "utest")]
