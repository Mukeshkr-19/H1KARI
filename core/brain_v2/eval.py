"""Brain v2 offline eval — isolated temp DB, synthetic fixtures only."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from core.path_literals import DOT_HIKARI_BRAIN, EPISODES_DB, HIKARI_MEMORY_DB

from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_repair import MemoryRepairGate
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.memory_lifecycle import lifecycle_status
from core.brain_v2.profile_summary import build_merged_user_profile_answer
from core.brain_v2.recall_intent import should_skip_external_research
from core.brain_v2.retrieval import BrainV2Retrieval
from core.brain_v2.schemas import MemoryCandidateStatus
from core.brain_v2.working_memory import WorkingMemory

# Synthetic fixture names/places only — never real private data.
OWNER_NAME = "Owner A"
GUEST_NAME = "Guest B"
PERSON_C = "Person C"
CITY_A = "City A"
CITY_B = "City B"
SCHOOL_A = "School A"
RESTAURANT_A = "Restaurant A"
JAMIE = "Jamie"

PLAN_STATEMENT = (
    f"on Sunday May 24 2026 I am meeting my girlfriend {JAMIE} "
    f"at {RESTAURANT_A} for lunch"
)
EDU_STATEMENT = f"my girlfriend {JAMIE} is a medical student at {SCHOOL_A}"

EVAL_FORBIDDEN_OUTPUT_MARKERS: Tuple[str, ...] = (
    "failed to initialize hikari memory",
    DOT_HIKARI_BRAIN,
    HIKARI_MEMORY_DB,
    "readonly database",
    "unable to open database file",
)


def isolated_retrieval(
    store: EpisodeStore,
    working: Optional[WorkingMemory] = None,
) -> BrainV2Retrieval:
    """Eval-only retrieval — never imports or initializes live neural memory."""
    return BrainV2Retrieval(
        store,
        working=working or WorkingMemory(),
        neural_bridge=None,
        allow_neural_procedural=False,
        allow_neural_conflict_reads=False,
    )


def isolated_coordinator(store: EpisodeStore) -> BrainV2Coordinator:
    """Eval-only coordinator — temp DB only, no neural bridge or procedural layer."""
    return BrainV2Coordinator(
        store=store,
        neural_bridge=None,
        allow_neural_procedural=False,
    )


@dataclass(frozen=True)
class BrainV2EvalResult:
    exit_code: int
    report: str
    db_path: Path


@dataclass
class _EvalCase:
    name: str
    run: Callable[[EpisodeStore, BrainV2Coordinator], bool]


def _accept_turn(store: EpisodeStore, statement: str, episode_key: str = "ep") -> None:
    episode_id = store.create_episode(episode_key)
    store.add_turn(episode_id, statement, is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    if not candidates:
        raise RuntimeError(f"no candidate for: {statement[:80]}")
    MemoryReviewGate(store).accept(candidates[0].candidate_id)


def _case_stable_vs_current(
    store: EpisodeStore, coord: BrainV2Coordinator
) -> bool:
    _accept_turn(store, f"Remember this: I live in {CITY_A}.", "stable-loc")
    coord.start_session("eval-loc")
    coord.record_turn(
        "eval-loc",
        f"Right now I'm in {CITY_B} for summer holidays.",
    )
    retrieval = isolated_retrieval(store, working=coord.working)

    live = retrieval.answer_from_accepted("where do I live?")
    if not live or CITY_A.lower() not in live.lower():
        return False
    from core.brain_v2.recall_intent import is_positive_brain_v2_recall_answer

    if not is_positive_brain_v2_recall_answer(live):
        return False

    now = retrieval.answer_from_accepted("where am I now?")
    if not now or CITY_B.lower() not in now.lower():
        return False
    if "for this session" not in now.lower():
        return False
    if CITY_A.lower() in now.lower():
        return False
    return True


def _case_profile_stale_neural(
    store: EpisodeStore, coord: BrainV2Coordinator
) -> bool:
    _accept_turn(store, f"Remember this: I live in {CITY_A}.", "prof-neural")
    coord.start_session("eval-prof")
    coord.record_turn(
        "eval-prof",
        f"Right now I'm in {CITY_B} for summer holidays.",
    )
    stale_neural = (
        f"What I know about you:\n- Home: {CITY_A}\n- Currently in: City C"
    )
    profile = build_merged_user_profile_answer(
        store.get_active_accepted_memories(limit=50),
        stale_neural,
        session_current=coord.working.get_current_location(),
    )
    if not profile:
        return False
    low = profile.lower()
    if CITY_A.lower() not in low or CITY_B.lower() not in low:
        return False
    if "city c" in low:
        return False
    return True


def _case_plan_recall(store: EpisodeStore, _coord: BrainV2Coordinator) -> bool:
    _accept_turn(store, f"Remember this: {PLAN_STATEMENT}.", "plan-ep")
    retrieval = isolated_retrieval(store)

    for query in (
        "what are my plans for Sunday?",
        "where am I meeting Jamie?",
    ):
        from core.brain_v2.recall_intent import is_positive_brain_v2_recall_answer

        reply = retrieval.answer_from_accepted(query)
        if not reply or not is_positive_brain_v2_recall_answer(reply):
            return False
        low = reply.lower()
        if RESTAURANT_A.lower() not in low and JAMIE.lower() not in low:
            return False
    return True


def _case_education_recall(store: EpisodeStore, _coord: BrainV2Coordinator) -> bool:
    _accept_turn(store, f"Remember this: {EDU_STATEMENT}.", "edu-ep")
    from core.brain_v2.recall_intent import is_positive_brain_v2_recall_answer

    reply = isolated_retrieval(store).answer_from_accepted("what does Jamie study?")
    if not reply or not is_positive_brain_v2_recall_answer(reply):
        return False
    low = reply.lower()
    return SCHOOL_A.lower() in low or "medical" in low


def _case_guest_speaker(store: EpisodeStore, coord: BrainV2Coordinator) -> bool:
    _accept_turn(store, f"Remember this: I live in {CITY_A}.", "owner-loc")
    episode_id = store.create_episode("guest-intro")
    store.add_turn(
        episode_id,
        f"I am {GUEST_NAME} talking to you now",
        is_user=True,
        speaker_label=GUEST_NAME,
    )
    guest_candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    if guest_candidates:
        return False

    coord.start_session("guest-sess")
    coord.record_turn(
        "guest-sess",
        f"I am {GUEST_NAME} talking to you now",
        speaker_label=GUEST_NAME,
    )
    stale_neural = (
        f"What I know about you:\n- Name: {GUEST_NAME}\n- Home: {CITY_B}\n"
    )
    profile = coord.build_user_profile_answer()
    if not profile:
        return False
    low = profile.lower()
    if CITY_A.lower() not in low or CITY_B.lower() in low:
        return False
    for mem in store.get_accepted_memories(limit=50):
        stmt = (mem.statement or "").lower()
        if GUEST_NAME.lower() in stmt and "talking to you" in stmt:
            return False
    return True


def _case_personal_recall_blocks_research(
    _store: EpisodeStore, _coord: BrainV2Coordinator
) -> bool:
    personal = (
        should_skip_external_research("where do I live?")
        and should_skip_external_research("what does Jamie study?")
        and should_skip_external_research("where am I meeting Jamie?")
    )
    general = not should_skip_external_research("what is the capital of France?")
    return personal and general


def _case_pending_rejected_not_truth(
    store: EpisodeStore, _coord: BrainV2Coordinator
) -> bool:
    episode_id = store.create_episode("rej-pend")
    store.add_turn(
        episode_id,
        f"My dad lives in rejected-{CITY_B}.",
        is_user=True,
    )
    store.add_turn(
        episode_id,
        f"My mom lives in accepted-{CITY_A}.",
        is_user=True,
    )
    store.add_turn(
        episode_id,
        f"I am visiting pending-{CITY_B} next week.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    gate = MemoryReviewGate(store)
    accepted_any = False
    for cand in candidates:
        low = cand.statement.lower()
        if "dad" in low or "rejected" in low:
            gate.reject(cand.candidate_id)
        elif "mom" in low or "accepted" in low:
            gate.accept(cand.candidate_id)
            accepted_any = True

    if not accepted_any:
        return False

    from core.brain_v2.recall_intent import is_positive_brain_v2_recall_answer

    reply = isolated_retrieval(store).answer_from_accepted("where does my mom live?")
    if not reply or not is_positive_brain_v2_recall_answer(reply):
        return False
    low = reply.lower()
    if "rejected" in low or "dad" in low:
        return False
    if f"accepted-{CITY_A}".lower() not in low and CITY_A.lower() not in low:
        return False

    dad_reply = isolated_retrieval(store).answer_from_accepted("where does my dad live?")
    if dad_reply and is_positive_brain_v2_recall_answer(dad_reply):
        if "rejected" in dad_reply.lower() or "dad" in dad_reply.lower():
            return False

    pending = store.get_candidates(status=MemoryCandidateStatus.PENDING)
    if pending:
        retrieval = isolated_retrieval(store)
        packet = retrieval.retrieve("where am I visiting next week?")
        blob = " ".join(h.text.lower() for h in packet.hits)
        if "pending" in blob:
            return False
    return True


def _case_supersede_active_truth(store: EpisodeStore, _coord: BrainV2Coordinator) -> bool:
    _accept_turn(store, f"Remember this: I live in {CITY_A}.", "supersede-ep")
    active = store.get_active_accepted_memories(limit=20)
    if len(active) != 1:
        return False
    old_id = active[0].memory_id
    repair = MemoryRepairGate(store)
    _old, new = repair.supersede(
        old_id,
        statement=f"Remember this: I live in {CITY_B}.",
        candidate_type="location",
    )
    active_after = store.get_active_accepted_memories(limit=20)
    if len(active_after) != 1:
        return False
    if active_after[0].memory_id == old_id:
        return False
    retired = store.get_source_linked_memory(old_id)
    if not retired or lifecycle_status(retired.metadata) != "superseded":
        return False
    reply = isolated_retrieval(store).answer_from_accepted("where do I live?")
    if not reply or CITY_B.lower() not in reply.lower():
        return False
    if CITY_A.lower() in reply.lower():
        return False
    return True


_EVAL_CASES: Tuple[_EvalCase, ...] = (
    _EvalCase("stable_location_vs_current", _case_stable_vs_current),
    _EvalCase("profile_stale_neural_suppression", _case_profile_stale_neural),
    _EvalCase("plan_recall", _case_plan_recall),
    _EvalCase("education_recall", _case_education_recall),
    _EvalCase("guest_speaker_no_owner_overwrite", _case_guest_speaker),
    _EvalCase("personal_recall_blocks_research", _case_personal_recall_blocks_research),
    _EvalCase("pending_rejected_not_truth", _case_pending_rejected_not_truth),
    _EvalCase("supersede_active_truth", _case_supersede_active_truth),
)


def _format_report(results: List[Tuple[str, bool]]) -> str:
    name_w = max(len(name) for name, _ in results)
    lines = ["Brain v2 eval", "-" * (name_w + 10)]
    passed = 0
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        lines.append(f"{name:<{name_w}}  {status}")
        if ok:
            passed += 1
    lines.append("-" * (name_w + 10))
    total = len(results)
    lines.append(f"{passed}/{total} passed")
    return "\n".join(lines)


def _isolated_store() -> Tuple[EpisodeStore, Path, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory(prefix="hikari_brain_v2_eval_")
    db_path = Path(tmp.name) / EPISODES_DB
    return EpisodeStore(db_path=db_path), db_path, tmp


def run_brain_v2_eval() -> BrainV2EvalResult:
    """Run synthetic Brain v2 eval cases on an isolated temp DB.

    Never reads or writes the live brain directory. Returns exit code 0 when all
    cases pass, else 1, plus a concise pass/fail table string.
    """
    saved_env = {
        key: os.environ.get(key)
        for key in (
            "HIKARI_NEURAL_MEMORY_DB",
            "HIKARI_BRAIN_V2_EPISODES_DB",
            "HIKARI_BRAIN_V2_CONFLICTS_PRIVATE",
            "HIKARI_DISABLE_BRAIN_V2",
        )
    }
    os.environ.pop("HIKARI_NEURAL_MEMORY_DB", None)
    os.environ.pop("HIKARI_BRAIN_V2_EPISODES_DB", None)
    os.environ.pop("HIKARI_BRAIN_V2_CONFLICTS_PRIVATE", None)
    os.environ["HIKARI_BRAIN_V2_EVAL"] = "1"

    try:
        results, last_db = _run_eval_cases()
    finally:
        os.environ.pop("HIKARI_BRAIN_V2_EVAL", None)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    report = _format_report(results)
    all_pass = all(ok for _, ok in results)
    return BrainV2EvalResult(
        exit_code=0 if all_pass else 1,
        report=report,
        db_path=last_db,
    )


def _run_eval_cases() -> Tuple[List[Tuple[str, bool]], Path]:
    results: List[Tuple[str, bool]] = []
    last_db = Path()
    for case in _EVAL_CASES:
        store, db_path, tmp = _isolated_store()
        last_db = db_path
        coord = isolated_coordinator(store)
        try:
            try:
                ok = case.run(store, coord)
            except Exception:
                ok = False
            results.append((case.name, ok))
        finally:
            tmp.cleanup()
    return results, last_db
