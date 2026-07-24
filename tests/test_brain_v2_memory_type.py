"""Brain v2 typed memory inference and explicit remember typing."""

from __future__ import annotations

import pytest

from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.memory_type import infer_memory_type, normalize_user_education_statement
from core.brain_v2.recall_intent import (
    INTENT_CURRENT_LOCATION,
    INTENT_EDUCATION,
    INTENT_GENERAL_MEMORY,
    INTENT_IDENTITY_SELF,
    INTENT_LOCATION,
    INTENT_PLAN,
    classify_recall_intent,
    is_positive_brain_v2_recall_answer,
    memory_person_names,
    requested_person_names,
)
from core.brain_v2.retrieval import BrainV2Retrieval
from core.brain_v2.working_memory import WorkingMemory
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.profile_summary import build_merged_user_profile_answer


@pytest.fixture
def episode_db(tmp_path):
    from core.brain_v2 import EpisodeStore

    return EpisodeStore(db_path=tmp_path / "memory_type.db")


def _remember_candidate(episode_db, text: str):
    episode_id = episode_db.create_episode("rem")
    episode_db.add_turn(episode_id, f"Remember this: {text}", is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert candidates, f"no candidate for remember: {text}"
    return candidates[0]


def _accept_turn(episode_db, statement: str, episode_key: str = "ep"):
    episode_id = episode_db.create_episode(episode_key)
    episode_db.add_turn(episode_id, statement, is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert candidates
    MemoryReviewGate(episode_db).accept(candidates[0].candidate_id)
    return candidates[0]


@pytest.mark.parametrize(
    "statement,expected_type",
    [
        ("I prefer local-first private tools.", "preference"),
        ("My favorite artist is Lana Del Rey.", "preference"),
        ("I live in City A.", "location"),
        (
            "my girlfriend Jamie is a medical student at River Medical University.",
            {"education", "relation"},
        ),
        (
            "for HIKARI we decided reviewed Brain v2 memories should come before research.",
            "decision",
        ),
        (
            "on Sunday May 24 2026 I am meeting my girlfriend Jamie at Restaurant B for lunch.",
            {"plan", "event"},
        ),
    ],
)
def test_infer_memory_type(statement, expected_type):
    inferred = infer_memory_type(statement)
    if isinstance(expected_type, set):
        assert inferred.candidate_type in expected_type
    else:
        assert inferred.candidate_type == expected_type


def test_degree_statement_normalizes_to_education():
    normalized = normalize_user_education_statement(
        "I am doing my bachelors in computer science in university at City A."
    )
    assert normalized
    stmt, extra = normalized
    assert "computer science" in stmt.lower()
    assert "city a" in stmt.lower()
    inferred = infer_memory_type(
        "I am doing my bachelors in computer science in university at City A."
    )
    assert inferred.candidate_type == "education"


@pytest.mark.parametrize(
    "remember_body,expected_type",
    [
        ("I prefer local-first private tools.", "preference"),
        ("I live in City A.", "location"),
        (
            "my girlfriend Jamie is a medical student at River Medical University.",
            {"education", "relation"},
        ),
        (
            "for HIKARI we decided reviewed Brain v2 memories should come before research.",
            "decision",
        ),
        (
            "on Sunday May 24 2026 I am meeting my girlfriend Jamie at Restaurant B for lunch.",
            {"plan", "event"},
        ),
    ],
)
def test_explicit_remember_typing(episode_db, remember_body, expected_type):
    cand = _remember_candidate(episode_db, remember_body)
    assert (cand.metadata or {}).get("explicit_remember")
    if isinstance(expected_type, set):
        assert cand.candidate_type in expected_type
    else:
        assert cand.candidate_type == expected_type
    assert cand.candidate_type != "preference" or expected_type == "preference"


def test_plan_recall_intents():
    assert classify_recall_intent("what are my plans for Sunday May 24?") == INTENT_PLAN
    assert classify_recall_intent("what are my plans tomorrow?") == INTENT_PLAN
    assert classify_recall_intent("when am I meeting Jamie?") == INTENT_PLAN
    assert classify_recall_intent("where am I meeting Jamie?") == INTENT_PLAN
    assert classify_recall_intent("what am I doing for lunch?") == INTENT_PLAN


def test_education_recall_intent():
    assert classify_recall_intent("what does Jamie study?") == INTENT_EDUCATION
    assert classify_recall_intent("what do I study?") == INTENT_EDUCATION
    assert classify_recall_intent("what did I study?") == INTENT_EDUCATION
    assert classify_recall_intent("what am I studying?") == INTENT_EDUCATION
    assert classify_recall_intent("where do I study?") == INTENT_EDUCATION
    assert classify_recall_intent("what is my major?") == INTENT_EDUCATION
    assert classify_recall_intent("what university do I attend?") == INTENT_EDUCATION


@pytest.mark.parametrize(
    "query",
    [
        "whats my name?",
        "what's my name?",
        "bro whats my name?",
        "what is my legal name?",
    ],
)
def test_identity_recall_intents(query):
    assert classify_recall_intent(query) == INTENT_IDENTITY_SELF


PLAN_STATEMENT = (
    "on Sunday May 24 2026 I am meeting my girlfriend Jamie at Restaurant B for lunch"
)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("what are my plans for Sunday?", set()),
        ("what are my plans for May 24?", set()),
        ("what does Jamie study?", {"jamie"}),
        ("What is my name?", set()),
    ],
)
def test_requested_person_names_skips_dates_not_people(query, expected):
    assert requested_person_names(query) == expected


def test_memory_person_names_plan_statement():
    names = memory_person_names(
        f"Remember this: {PLAN_STATEMENT}.",
        {"person": "Jamie", "relation": "girlfriend"},
    )
    assert "jamie" in names
    for bad in ("sunday", "may", "restaurant", "nation", "university", "city a"):
        assert bad not in names


EDU_STATEMENT = "my girlfriend Jamie is a medical student at River Medical University"


def test_plan_only_does_not_answer_study_query(episode_db):
    _accept_turn(episode_db, f"Remember this: {PLAN_STATEMENT}.", "plan-only")
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("what does Jamie study?")
    assert reply
    assert "have a reviewed memory" in reply.lower()
    assert "restaurant" not in reply.lower()


def test_education_only_does_not_answer_meeting_query(episode_db):
    _accept_turn(episode_db, f"Remember this: {EDU_STATEMENT}.", "edu-only")
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("where am I meeting Jamie?")
    assert reply
    assert "have a reviewed memory" in reply.lower()
    assert "river medical" not in reply.lower()


def test_education_only_does_not_answer_plan_query(episode_db):
    _accept_turn(episode_db, f"Remember this: {EDU_STATEMENT}.", "edu-plan")
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("what are my plans for Sunday?")
    assert reply
    assert "have a reviewed memory" in reply.lower()
    assert "river medical" not in reply.lower()


def test_plan_or_relation_memories_do_not_answer_identity_query(episode_db):
    _accept_turn(episode_db, f"Remember this: {PLAN_STATEMENT}.", "identity-plan")
    _accept_turn(
        episode_db,
        "My brother is the person who created this assistant and is called Owner A.",
        "identity-relation",
    )
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("whats my name?")
    assert reply
    assert "have a reviewed memory" in reply.lower()
    assert "restaurant" not in reply.lower()
    assert "brother" not in reply.lower()


def test_identity_query_uses_identity_memory_only_when_mixed(episode_db):
    _accept_turn(episode_db, f"Remember this: {PLAN_STATEMENT}.", "identity-mixed-plan")
    _accept_turn(
        episode_db,
        "My brother is the person who created this assistant and is called Person B.",
        "identity-mixed-relation",
    )
    _accept_turn(episode_db, "My name is Owner A.", "identity-mixed-name")

    reply = BrainV2Retrieval(episode_db).answer_from_accepted("bro whats my name?")
    assert reply
    assert "owner a" in reply.lower()
    assert "restaurant" not in reply.lower()
    assert "brother" not in reply.lower()


@pytest.mark.parametrize(
    "query",
    [
        "what do I prefer?",
        "what did we decide about HIKARI?",
        "what are my flights?",
        "who is my girlfriend?",
    ],
)
def test_other_typed_personal_queries_do_not_use_plan_as_answer(episode_db, query):
    _accept_turn(episode_db, f"Remember this: {PLAN_STATEMENT}.", "typed-plan-only")
    reply = BrainV2Retrieval(episode_db).answer_from_accepted(query)
    assert reply
    assert "have a reviewed memory" in reply.lower()
    assert "restaurant" not in reply.lower()


@pytest.mark.parametrize(
    "query",
    [
        "what is my birthday?",
        "where do I work?",
        "what is my blood type?",
    ],
)
def test_untyped_personal_facts_fail_closed_instead_of_returning_other_memory(episode_db, query):
    _accept_turn(episode_db, f"Remember this: {PLAN_STATEMENT}.", "unknown-personal")
    assert classify_recall_intent(query) == INTENT_GENERAL_MEMORY
    reply = BrainV2Retrieval(episode_db).answer_from_accepted(query)
    assert reply
    assert "have a reviewed memory" in reply.lower()
    assert "restaurant" not in reply.lower()


def test_both_memories_choose_correct_answer(episode_db):
    _accept_turn(episode_db, f"Remember this: {PLAN_STATEMENT}.", "both-plan")
    _accept_turn(episode_db, f"Remember this: {EDU_STATEMENT}.", "both-edu")
    retrieval = BrainV2Retrieval(episode_db)

    study = retrieval.answer_from_accepted("what does Jamie study?")
    assert study and is_positive_brain_v2_recall_answer(study)
    assert "river medical" in study.lower() or "medical" in study.lower()
    assert "restaurant" not in study.lower()

    meeting = retrieval.answer_from_accepted("where am I meeting Jamie?")
    assert meeting and is_positive_brain_v2_recall_answer(meeting)
    assert "restaurant" in meeting.lower()
    assert "river medical" not in meeting.lower()

    plans = retrieval.answer_from_accepted("what are my plans for Sunday?")
    assert plans and is_positive_brain_v2_recall_answer(plans)
    assert "restaurant" in plans.lower() or "sunday" in plans.lower()
    assert "river medical" not in plans.lower()


def test_plan_memory_answers_plan_queries(episode_db):
    _accept_turn(
        episode_db,
        f"Remember this: {PLAN_STATEMENT}.",
        "plan-ep",
    )
    retrieval = BrainV2Retrieval(episode_db)
    sunday = retrieval.answer_from_accepted("what are my plans for Sunday?")
    assert sunday
    assert is_positive_brain_v2_recall_answer(sunday)
    assert "restaurant" in sunday.lower() or "jamie" in sunday.lower()

    reply = retrieval.answer_from_accepted("what are my plans for Sunday May 24?")
    assert reply
    assert is_positive_brain_v2_recall_answer(reply)
    assert "restaurant" in reply.lower() or "jamie" in reply.lower()

    where = retrieval.answer_from_accepted("where am I meeting Jamie?")
    assert where
    assert "restaurant" in where.lower()


def test_education_memory_answers_study_query(episode_db):
    _accept_turn(
        episode_db,
        "Remember this: my girlfriend Jamie is a medical student at River Medical University.",
        "edu-ep",
    )
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("what does Jamie study?")
    assert reply
    assert is_positive_brain_v2_recall_answer(reply)
    assert "medical" in reply.lower() or "river medical" in reply.lower()


def test_location_memory_answers_where_live(episode_db):
    _accept_turn(episode_db, "Remember this: I live in City A.", "loc-ep")
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("where do I live?")
    assert reply
    assert "city a" in reply.lower()


def test_hikari_decision_memory_answers_decision_query(episode_db):
    _accept_turn(
        episode_db,
        "Remember this: for HIKARI we decided reviewed Brain v2 memories should come before research.",
        "dec-ep",
    )
    reply = BrainV2Retrieval(episode_db).answer_from_accepted(
        "what did we decide about HIKARI?"
    )
    assert reply
    assert "review" in reply.lower() or "research" in reply.lower()


@pytest.mark.parametrize(
    "raw",
    [
        "I study at North City College as a computer science student",
        "I study in north city college as a computer science student",
    ],
)
def test_user_study_statement_pending_education(episode_db, raw):
    episode_id = episode_db.create_episode("user-edu")
    episode_db.add_turn(episode_id, raw, is_user=True)
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert candidates
    cand = candidates[0]
    assert cand.candidate_type == "education"
    assert "north city" in cand.statement.lower() or "city a" in cand.statement.lower()
    assert cand.review_status == "pending"


def test_normalize_user_education_statement():
    from core.brain_v2.memory_type import normalize_user_education_statement

    result = normalize_user_education_statement(
        "I study in north city college as a computer science student"
    )
    assert result
    stmt, meta = result
    assert "study" in stmt.lower()
    assert "north city" in stmt.lower() or "city a" in stmt.lower()
    assert meta.get("field_of_study") or "computer" in stmt.lower()


def test_normalize_degree_statement_keeps_university_connector_natural():
    from core.brain_v2.memory_type import normalize_user_education_statement

    result = normalize_user_education_statement(
        "I am doing my bachelors in computer science in university at City A"
    )
    assert result
    stmt, meta = result
    assert "Computer Science" in stmt
    assert "University at City A" in stmt
    assert meta.get("organization") == "University at City A"


def test_sister_memory_does_not_answer_girlfriend_study_query(episode_db):
    _accept_turn(
        episode_db,
        "My sister Casey studies at North City College.",
        "sis-ep",
    )
    reply = BrainV2Retrieval(episode_db).answer_from_accepted("what does Jamie study?")
    assert reply
    assert "have a reviewed memory" in reply.lower()
    assert "casey" not in reply.lower()


@pytest.mark.parametrize(
    "query",
    [
        "where am I now?",
        "where am I currently?",
        "where am I right now?",
        "where am I today?",
    ],
)
def test_current_location_recall_intent(query):
    assert classify_recall_intent(query) == INTENT_CURRENT_LOCATION


@pytest.mark.parametrize(
    "query",
    [
        "where do I live?",
        "what is my home?",
        "my city",
    ],
)
def test_stable_location_recall_intent(query):
    assert classify_recall_intent(query) == INTENT_LOCATION


@pytest.mark.parametrize(
    "statement,expected_type",
    [
        ("Right now I'm in City B for summer holidays.", "current_location"),
        ("I'm currently in City B.", "current_location"),
        ("I am in City B.", "current_location"),
        ("I am in City B right now !!", "current_location"),
        ("I live in City A.", "location"),
        ("I moved to City A for studies.", "fact"),
    ],
)
def test_current_vs_stable_location_inference(statement, expected_type):
    inferred = infer_memory_type(statement)
    assert inferred.candidate_type == expected_type


def test_stable_location_answers_where_live_not_where_now(episode_db):
    _accept_turn(episode_db, "Remember this: I live in City A.", "stable-loc")
    retrieval = BrainV2Retrieval(episode_db)

    live = retrieval.answer_from_accepted("where do I live?")
    assert live and "city a" in live.lower()
    assert is_positive_brain_v2_recall_answer(live)

    now = retrieval.answer_from_accepted("where am I now?")
    assert now
    assert "have a reviewed memory" in now.lower()
    assert "city a" not in now.lower()


def test_session_current_location_answers_where_now(episode_db):
    working = WorkingMemory()
    working.note_current_location("City B", "Right now I'm in City B for summer holidays.")
    retrieval = BrainV2Retrieval(episode_db, working=working)

    reply = retrieval.answer_from_accepted("where am I now?")
    assert reply
    assert "for this session" in reply.lower()
    assert "city b" in reply.lower()
    assert "you're in" in reply.lower()
    assert "summer holidays" not in reply.lower()
    assert "city a" not in reply.lower()


def test_current_location_statement_not_promoted_to_durable_candidate(episode_db):
    episode_id = episode_db.create_episode("curr-loc")
    episode_db.add_turn(
        episode_id,
        "Right now I'm in City B for summer holidays.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(episode_db).process_episode(episode_id)[1]
    assert not any(c.candidate_type == "current_location" for c in candidates)


def test_profile_separates_stable_and_current_context(episode_db):
    _accept_turn(episode_db, "Remember this: I live in City A.", "prof-stable")
    coord = BrainV2Coordinator(store=episode_db)
    coord.start_session("sess-1")
    coord.record_turn("sess-1", "Right now I'm in City B for summer holidays.")

    profile = coord.build_user_profile_answer()
    assert profile
    assert "Location:" in profile
    assert "city a" in profile.lower()
    assert "Current context:" in profile
    assert "city b" in profile.lower()
    assert "recent session" in profile.lower()


def test_profile_merged_suppresses_stale_neural_current(episode_db):
    _accept_turn(episode_db, "Remember this: I live in City A.", "prof-neural")
    coord = BrainV2Coordinator(store=episode_db)
    coord.start_session("sess-2")
    coord.record_turn("sess-2", "Right now I'm in City B for summer holidays.")
    neural = "What I know about you:\n- Home: City A\n- Currently in: City C"
    profile = build_merged_user_profile_answer(
        coord.store.get_accepted_memories(limit=50),
        neural,
        session_current=coord.working.get_current_location(),
    )
    assert "city a" in profile.lower()
    assert "city b" in profile.lower()
    assert "city c" not in profile.lower()
