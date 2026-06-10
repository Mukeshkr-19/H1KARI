"""Regression test suite for compiled private wiki writeback."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.wiki_paths import resolve_wiki_dir
from core.brain_v2.wiki_writeback import (
    WIKI_PAGE_NAMES,
    _EMPTY_MESSAGE,
    apply_wiki_writeback,
    compile_wiki_pages,
    format_wiki_preview,
    wiki_eligible_memory,
)
from core.brain_v2.schemas import (
    MemoryCandidate,
    MemoryCandidateStatus,
    SourceLinkedMemory,
    TranscriptSegment,
)
from core.path_literals import EPISODES_DB


@pytest.fixture
def wiki_env(tmp_path, monkeypatch):
    db_file = tmp_path / EPISODES_DB
    wiki_dir = tmp_path / "wiki"
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(db_file))
    monkeypatch.setenv("HIKARI_WIKI_DIR", str(wiki_dir))
    store = EpisodeStore(db_path=db_file)
    return store, wiki_dir


def _seed_candidate(store: EpisodeStore, candidate_id: str, statement: str, status: str, ctype: str = "fact", metadata: dict | None = None) -> MemoryCandidate:
    episode_id = "ep-1"
    with store._connect() as conn:
        row = conn.execute("SELECT episode_id FROM raw_episodes WHERE episode_id = ?", (episode_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO raw_episodes (episode_id, session_id, started_at) VALUES (?, ?, ?)",
                (episode_id, "session-1", "2026-06-10T00:00:00Z")
            )
    cand = MemoryCandidate(
        candidate_id=candidate_id,
        episode_id=episode_id,
        statement=statement,
        candidate_type=ctype,
        review_status=status,
        source_segment_ids=[],
        metadata=metadata or {},
    )
    store.save_candidates([cand])
    return cand


def _seed_linked(store: EpisodeStore, memory_id: str, candidate_id: str, statement: str, ctype: str = "fact", metadata: dict | None = None) -> SourceLinkedMemory:
    _seed_candidate(store, candidate_id, statement, MemoryCandidateStatus.ACCEPTED.value, ctype, metadata)
    meta = metadata or {}
    meta["candidate_type"] = ctype
    mem = SourceLinkedMemory(
        memory_id=memory_id,
        candidate_id=candidate_id,
        episode_id="ep-1",
        statement=statement,
        source_segment_ids=[],
        accepted_at="2026-06-10T00:00:00Z",
        metadata=meta,
    )
    store.save_source_linked_memory(mem)
    return mem


def test_empty_accepted_memory_set(wiki_env):
    store, wiki_dir = wiki_env
    # Empty DB
    result = compile_wiki_pages(store)
    assert result.empty
    assert not result.pages

    preview = format_wiki_preview(result, wiki_dir=wiki_dir)
    assert preview.strip() == _EMPTY_MESSAGE

    # Writeback should do nothing and create no files/dirs
    apply_wiki_writeback(result, wiki_dir=wiki_dir)
    assert not wiki_dir.exists()


def test_preview_creates_no_files(wiki_env):
    store, wiki_dir = wiki_env
    _seed_linked(store, "mem-1", "cand-1", "My name is Owner A.", "identity")

    result = compile_wiki_pages(store)
    assert not result.empty

    preview = format_wiki_preview(result, wiki_dir=wiki_dir)
    assert "Wiki writeback preview" in preview
    assert "My name is Owner A." in preview

    # Verify preview is strictly read-only
    assert not wiki_dir.exists()


def test_writeback_writes_only_to_temp_dir(wiki_env):
    store, wiki_dir = wiki_env
    _seed_linked(store, "mem-1", "cand-1", "My name is Owner A.", "identity")
    _seed_linked(store, "mem-2", "cand-2", "I prefer coffee.", "preference")

    result = compile_wiki_pages(store)
    assert not result.empty

    target = apply_wiki_writeback(result, wiki_dir=wiki_dir)
    assert target == wiki_dir
    assert wiki_dir.is_dir()

    profile_path = wiki_dir / "profile.md"
    pref_path = wiki_dir / "preferences.md"
    assert profile_path.is_file()
    assert pref_path.is_file()

    # Confirms plans.md is skipped because there are no plans
    assert not (wiki_dir / "plans.md").exists()

    # Check profile page contents
    profile_content = profile_path.read_text(encoding="utf-8")
    assert "My name is Owner A." in profile_content
    assert "provenance: memory_id=`mem-1`" in profile_content

    # Standard pages education, locations, relationships must be generated as empty template pages
    for filename in ("education.md", "locations.md", "relationships.md"):
        path = wiki_dir / filename
        assert path.is_file()
        content = path.read_text(encoding="utf-8")
        assert "No active accepted memories." in content


def test_inactive_memories_excluded(wiki_env):
    store, wiki_dir = wiki_env
    # Superseded fact
    _seed_linked(store, "mem-1", "cand-1", "Old statement", "identity", {"lifecycle_status": "superseded"})
    # Retired fact
    _seed_linked(store, "mem-2", "cand-2", "Retired statement", "identity", {"lifecycle_status": "retired"})
    # Active fact
    _seed_linked(store, "mem-3", "cand-3", "Active statement", "identity", {"lifecycle_status": "active"})

    result = compile_wiki_pages(store)
    assert len(result.memory_ids) == 1
    assert result.memory_ids[0] == "mem-3"

    page = result.pages["profile.md"]
    assert "Active statement" in page
    assert "Old statement" not in page
    assert "Retired statement" not in page


def test_pending_rejected_candidates_excluded(wiki_env):
    store, wiki_dir = wiki_env
    # Pending candidate only
    _seed_candidate(store, "cand-1", "Pending statement", MemoryCandidateStatus.PENDING.value)
    # Rejected candidate only
    _seed_candidate(store, "cand-2", "Rejected statement", MemoryCandidateStatus.REJECTED.value)

    result = compile_wiki_pages(store)
    assert result.empty


def test_guest_content_excluded(wiki_env):
    store, wiki_dir = wiki_env

    # 1. Marked via memory metadata
    _seed_linked(store, "mem-1", "cand-1", "Guest statement 1", "identity", {"guest_session": True})

    # 2. Sourced from segment with guest speaker_label
    mem2 = _seed_linked(store, "mem-2", "cand-2", "Guest statement 2", "identity")
    mem2.source_segment_ids = ["seg-1"]
    store.save_source_linked_memory(mem2)

    seg = TranscriptSegment(
        segment_id="seg-1",
        episode_id="ep-1",
        sequence=0,
        text="My name is Guest B.",
        is_user=True,
        speaker_label="guest_b",
        metadata={},
    )
    store.append_segment(seg)

    result = compile_wiki_pages(store)
    assert result.empty
    assert result.skipped_guest == 2


def test_named_owner_segment_is_not_treated_as_guest(wiki_env):
    store, wiki_dir = wiki_env

    mem = _seed_linked(store, "mem-1", "cand-1", "My name is Owner A.", "identity")
    mem.source_segment_ids = ["seg-owner"]
    store.save_source_linked_memory(mem)

    seg = TranscriptSegment(
        segment_id="seg-owner",
        episode_id="ep-1",
        sequence=0,
        text="My name is Owner A.",
        is_user=True,
        speaker_label="Owner A",
        metadata={},
    )
    store.append_segment(seg)

    result = compile_wiki_pages(store)
    assert result.memory_ids == ["mem-1"]
    assert result.skipped_guest == 0
    assert "My name is Owner A." in result.pages["profile.md"]


def test_current_location_excluded(wiki_env):
    store, wiki_dir = wiki_env
    # Stable location lives in locations.md
    _seed_linked(store, "mem-1", "cand-1", "I live in City A.", "location")
    # Current location (trip city context) excluded
    _seed_linked(store, "mem-2", "cand-2", "I am in City B now.", "current_location")

    result = compile_wiki_pages(store)
    assert len(result.memory_ids) == 1
    assert result.memory_ids[0] == "mem-1"

    assert "locations.md" in result.pages
    loc_content = result.pages["locations.md"]
    assert "City A" in loc_content
    assert "City B" not in loc_content


def test_deterministic_output_order(wiki_env):
    store, wiki_dir = wiki_env
    # Seed statements in reverse memory_id order
    _seed_linked(store, "mem-z", "cand-z", "Preference Z", "preference")
    _seed_linked(store, "mem-a", "cand-a", "Preference A", "preference")
    _seed_linked(store, "mem-m", "cand-m", "Preference M", "preference")

    result = compile_wiki_pages(store)
    assert result.memory_ids == ["mem-a", "mem-m", "mem-z"]

    pref_page = result.pages["preferences.md"]
    lines = [line for line in pref_page.splitlines() if line.startswith("- ")]
    assert lines == ["- Preference A", "- Preference M", "- Preference Z"]


def test_stale_page_cleanup(wiki_env):
    store, wiki_dir = wiki_env

    # Seed preference and plans
    _seed_linked(store, "mem-1", "cand-1", "Preference A", "preference")
    _seed_linked(store, "mem-2", "cand-2", "I plan to travel tomorrow.", "plan")

    result = compile_wiki_pages(store)
    apply_wiki_writeback(result, wiki_dir=wiki_dir)

    assert (wiki_dir / "preferences.md").is_file()
    assert (wiki_dir / "plans.md").is_file()

    # Now compile with only preference (plans retired)
    store = EpisodeStore(db_path=store.db_path) # reload
    store.db_path.unlink() # Delete DB to start clean
    store = EpisodeStore(db_path=store.db_path)
    _seed_linked(store, "mem-1", "cand-1", "Preference A", "preference")

    result2 = compile_wiki_pages(store)
    apply_wiki_writeback(result2, wiki_dir=wiki_dir)

    assert (wiki_dir / "preferences.md").is_file()
    # plans.md should be automatically cleaned up/unlinked
    assert not (wiki_dir / "plans.md").exists()


def test_hikari_cli_wiki_preview_subprocess(wiki_env):
    store, wiki_dir = wiki_env
    _seed_linked(store, "mem-1", "cand-1", "My name is Owner A.", "identity")
    import subprocess
    import sys

    env = {**dict(os.environ), "HIKARI_BRAIN_V2_EPISODES_DB": str(store.db_path), "HIKARI_WIKI_DIR": str(wiki_dir)}
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "hikari.py", "--brain-v2-wiki-preview"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "profile.md" in proc.stdout
    assert not list(wiki_dir.glob("*.md"))
