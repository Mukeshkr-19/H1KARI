"""Brain v2 candidate quality, extraction, retrieval, and CLI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.brain_v2.candidate_quality import (
    QUALITY_KEEP,
    QUALITY_REJECT,
    QUALITY_WEAK,
    classify_candidate,
)
from core.brain_v2.candidate_scoring import annotate_and_rank_candidates, normalize_statement
from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.retrieval import BrainV2Retrieval
from core.brain_v2.schemas import MemoryCandidate, MemoryCandidateStatus, SourceLinkedMemory


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "quality_v2.db")


def test_filler_not_pending_candidate(episode_db):
    episode_id = episode_db.create_episode("filler")
    episode_db.add_turn(episode_id, "okay", is_user=True)
    episode_db.add_turn(episode_id, "got it", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert not candidates


def test_question_not_pending_candidate(episode_db):
    episode_id = episode_db.create_episode("q")
    episode_db.add_turn(episode_id, "where do I live?", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert not candidates


def test_remember_this_extracts_content(episode_db):
    episode_id = episode_db.create_episode("rem")
    episode_db.add_turn(
        episode_id,
        "Remember this: I prefer local-first private tools.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert candidates
    stmt = candidates[0].statement.lower()
    assert "local-first" in stmt
    assert (candidates[0].metadata or {}).get("explicit_remember")
    assert (candidates[0].metadata or {}).get("quality_label") == QUALITY_KEEP


def test_family_fact_is_keep_candidate(episode_db):
    episode_id = episode_db.create_episode("fam")
    episode_db.add_turn(
        episode_id,
        "My sister Maya studies at North Valley University.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert candidates
    assert any((c.metadata or {}).get("quality_label") == QUALITY_KEEP for c in candidates)


def test_hikari_decision_is_keep_candidate(episode_db):
    episode_id = episode_db.create_episode("dec")
    episode_db.add_turn(
        episode_id,
        "For HIKARI we decided to keep Brain v2 review manual.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert candidates
    assert any(c.candidate_type == "decision" for c in candidates)


def test_assistant_filler_segment_ignored(episode_db):
    episode_id = episode_db.create_episode("asst")
    episode_db.add_turn(episode_id, "hi", is_user=True)
    episode_db.add_turn(episode_id, "Got it.", is_user=False, speaker_label="assistant")
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert not any("got it" in c.statement.lower() for c in candidates)


def test_explicit_remember_ranks_above_vague_declarative(episode_db):
    remember = MemoryCandidate(
        candidate_id="r1",
        episode_id="e1",
        statement="I prefer local-first private tools.",
        candidate_type="preference",
        confidence=0.85,
        salience=0.85,
        source_segment_ids=["s1"],
        metadata={"explicit_remember": True, "quality_label": QUALITY_KEEP},
    )
    vague = MemoryCandidate(
        candidate_id="w1",
        episode_id="e1",
        statement="I think something happened yesterday in town.",
        candidate_type="fact",
        confidence=0.5,
        salience=0.45,
        source_segment_ids=["s2"],
        metadata={"quality_label": QUALITY_WEAK},
    )
    ranked = annotate_and_rank_candidates([vague, remember])
    assert ranked[0].candidate_id == "r1"


def test_duplicate_marked_against_accepted_memory(episode_db):
    accepted_stmt = "I live in City B."
    episode_id = episode_db.create_episode("dup-acc")
    episode_db.add_turn(episode_id, accepted_stmt, is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    gate = MemoryReviewGate(episode_db)
    gate.accept(candidates[0].candidate_id)

    episode_id2 = episode_db.create_episode("dup-acc-2")
    episode_db.add_turn(episode_id2, accepted_stmt, is_user=True)
    candidates2 = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id2)[1]
    assert (candidates2[0].metadata or {}).get("duplicate_of_existing_memory")


def test_accepted_memory_in_retrieval_with_source_note(episode_db):
    episode_id = episode_db.create_episode("ret")
    episode_db.add_turn(episode_id, "My sister Maya studies at North Valley University.", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    MemoryReviewGate(episode_db).accept(candidates[0].candidate_id)

    packet = BrainV2Retrieval(episode_db).retrieve("where does my sister study?")
    semantic = [h for h in packet.hits if h.layer == "semantic"]
    assert semantic
    assert "source:" in semantic[0].text
    assert "maya" in semantic[0].text.lower() or "north valley" in semantic[0].text.lower()


def test_pending_not_semantic_truth(episode_db):
    episode_id = episode_db.create_episode("pend")
    episode_db.add_turn(episode_id, "Remember this: I prefer local-first private tools.", is_user=True)
    EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)

    packet = BrainV2Retrieval(episode_db).retrieve("local-first tools")
    assert not any(
        h.layer == "semantic" and "local-first" in h.text.lower() for h in packet.hits
    )


def test_rejected_not_semantic_truth(episode_db):
    episode_id = episode_db.create_episode("rej")
    episode_db.add_turn(episode_id, "My dad Rowan lives in Lake Town.", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    MemoryReviewGate(episode_db).reject(candidates[0].candidate_id)

    packet = BrainV2Retrieval(episode_db).retrieve("where does my dad live?")
    semantic = " ".join(h.text.lower() for h in packet.hits if h.layer == "semantic")
    assert "rowan" not in semantic


def test_semantic_outranks_episodic(episode_db):
    episode_id = episode_db.create_episode("rank")
    episode_db.add_turn(episode_id, "Remember this: I prefer local-first private tools.", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    MemoryReviewGate(episode_db).accept(candidates[0].candidate_id)

    packet = BrainV2Retrieval(episode_db).retrieve("local-first")
    scores = {h.layer: h.score for h in packet.hits}
    if "semantic" in scores and "episodic" in scores:
        assert scores["semantic"] > scores["episodic"]


def test_cli_pending_shows_quality(episode_db, capsys):
    from core.brain_v2 import cli as brain_cli

    episode_id = episode_db.create_episode("cli-q")
    episode_db.add_turn(episode_id, "Remember this: I prefer local-first private tools.", is_user=True)
    EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)

    coord = __import__("core.brain_v2.coordinator", fromlist=["BrainV2Coordinator"]).BrainV2Coordinator(
        store=episode_db
    )
    with patch.object(brain_cli, "_coordinator_readonly", return_value=coord):
        brain_cli.cmd_pending()
    out = capsys.readouterr().out
    assert "quality=" in out
    assert "score=" in out


def test_classify_filler_and_question():
    assert classify_candidate("okay").label == QUALITY_REJECT
    assert classify_candidate("what is my name?").label == QUALITY_REJECT


@pytest.mark.parametrize(
    "statement",
    [
        "do u know what my gf did",
        "do u know my sister",
        "who is my sister",
        "do you remember what we talked about",
        "what does my gf do",
        "where does my sister study",
    ],
)
def test_informal_questions_reject_from_queue(statement):
    verdict = classify_candidate(statement, candidate_type="relation")
    assert verdict.label == QUALITY_REJECT
    assert "question" in verdict.reasons


@pytest.mark.parametrize(
    "statement",
    [
        "my gf Jamie works at River Clinic",
        "My sister Casey studies at North City College",
    ],
)
def test_declarative_relation_facts_stay_keep(statement):
    verdict = classify_candidate(statement, candidate_type="relation")
    assert verdict.label == QUALITY_KEEP


@pytest.mark.parametrize(
    "statement,allowed",
    [
        ("I am tired today", {QUALITY_WEAK, QUALITY_REJECT}),
        ("this project is good", {QUALITY_WEAK, QUALITY_REJECT}),
        ("My sister Maya studies at North Valley University", {QUALITY_KEEP}),
        ("I live in City B", {QUALITY_KEEP}),
        ("I prefer local-first tools", {QUALITY_KEEP}),
    ],
)
def test_entity_detection_regression(statement, allowed):
    verdict = classify_candidate(statement)
    assert verdict.label in allowed, (statement, verdict.label, verdict.reasons)


def test_remember_prefer_single_candidate(episode_db):
    episode_id = episode_db.create_episode("dedupe-remember")
    episode_db.add_turn(
        episode_id,
        "Remember this: I prefer local-first private tools.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert len(candidates) == 1
    assert "local-first" in candidates[0].statement.lower()


def test_cli_accept_promote_success_message(episode_db, capsys):
    from core.brain_v2 import cli as brain_cli
    from core.brain_v2.coordinator import BrainV2Coordinator

    coord = BrainV2Coordinator(store=episode_db)
    episode_id = episode_db.create_episode("cli-promote-ok")
    episode_db.add_turn(episode_id, "My name is Alex and I live in City B.")
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    coord.promoter.promote = MagicMock(return_value="brain_v2:abc123")

    with patch.object(brain_cli, "_coordinator_promote", return_value=coord):
        assert (
            brain_cli.run_brain_v2_cli(
                "accept",
                candidates[0].candidate_id,
                confirm_promote=brain_cli.PROMOTE_CONFIRM_TOKEN,
            )
            == 0
        )
    out = capsys.readouterr().out
    assert "Promoted to neural memory: brain_v2:abc123" in out


def test_cli_accept_promote_failed_message(episode_db, capsys):
    from core.brain_v2 import cli as brain_cli
    from core.brain_v2.coordinator import BrainV2Coordinator

    coord = BrainV2Coordinator(store=episode_db)
    episode_id = episode_db.create_episode("cli-promote-fail")
    episode_db.add_turn(episode_id, "My name is Alex and I live in City B.")
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    coord.promoter.promote = MagicMock(return_value=None)

    with patch.object(brain_cli, "_coordinator_promote", return_value=coord):
        assert (
            brain_cli.run_brain_v2_cli(
                "accept",
                candidates[0].candidate_id,
                confirm_promote=brain_cli.PROMOTE_CONFIRM_TOKEN,
            )
            == 0
        )
    out = capsys.readouterr().out
    assert "Accepted, but neural promotion was not confirmed." in out
