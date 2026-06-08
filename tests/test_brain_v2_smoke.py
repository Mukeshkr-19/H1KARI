"""End-to-end Brain v2 smoke — chat turns through accept and retrieval."""

from __future__ import annotations

import pytest

from core.brain_v2 import BrainV2Coordinator, EpisodeStore, MemoryCandidateStatus
from core.brain_v2.retrieval import BrainV2Retrieval


@pytest.fixture
def episode_db(tmp_path):
    return EpisodeStore(db_path=tmp_path / "smoke_v2.db")


@pytest.fixture
def coordinator(episode_db):
    return BrainV2Coordinator(store=episode_db)


def test_full_omi_style_pipeline_smoke(coordinator: BrainV2Coordinator):
    session = coordinator.start_session("smoke-session")

    coordinator.record_turn(
        session,
        "Hi, I am setting up HIKARI brain repair.",
        "Hello! I will track this session.",
    )
    coordinator.record_turn(
        session,
        "Remember this: I prefer local-first private tools.",
        "Understood.",
    )
    coordinator.record_turn(
        session,
        "My sister Maya studies at North Valley University.",
        "Noted.",
    )

    structured, candidates = coordinator.close_and_consolidate(session)

    assert structured.segment_count >= 4
    assert coordinator.store.count_raw_segments(structured.episode_id) >= 4
    assert candidates
    assert all(c.review_status == MemoryCandidateStatus.PENDING.value for c in candidates)

    pending = coordinator.store.get_candidates(status=MemoryCandidateStatus.PENDING)
    assert pending

    family = next(
        (
            c
            for c in pending
            if "maya" in c.statement.lower() or "north valley" in c.statement.lower()
        ),
        None,
    )
    assert family is not None
    linked, _promo = coordinator.accept_candidate(family.candidate_id, promote=False)
    assert linked.source_segment_ids

    packet = coordinator.build_context_packet("where does my sister study?")
    semantic = " ".join(
        h.text for h in packet.hits if h.layer == "semantic"
    ).lower()
    assert "maya" in semantic or "north valley" in semantic
    working = " ".join(h.text for h in packet.hits if h.layer == "working").lower()
    assert "hello! i will track" in working or "turn:" in working

    retrieval = BrainV2Retrieval(coordinator.store, coordinator.working)
    assert not retrieval.pending_or_rejected_in_results("maya studies")


def test_cli_commands_on_smoke_data(coordinator, capsys):
    from core.brain_v2 import cli as brain_cli

    session = coordinator.start_session("cli-sess")
    coordinator.record_turn(session, "Remember this: I live in City B.", "OK.")
    structured, candidates = coordinator.close_and_consolidate(session)
    assert candidates
    cand_id = candidates[0].candidate_id

    brain_cli._coordinator_readonly = lambda write=False: coordinator  # type: ignore[attr-defined]

    assert brain_cli.cmd_pending() == 0
    out = capsys.readouterr().out
    assert "Pending" in out or cand_id[:8] in out

    assert brain_cli.cmd_show(cand_id) == 0
    detail = capsys.readouterr().out
    assert structured.episode_id in detail
    assert "source_segment_ids" in detail

    assert brain_cli.cmd_accept(cand_id, promote=False) == 0
    assert brain_cli.cmd_memories() == 0
    mem_out = capsys.readouterr().out
    assert "City B" in mem_out or "Accepted" in mem_out

    assert brain_cli.cmd_status() == 0
