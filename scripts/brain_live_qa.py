#!/usr/bin/env python3
"""Terminal Brain v2 live QA — full orchestrator, fake fixtures only. Exit 1 on failure."""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.brain_v2.db_paths import ENV_BRAIN_V2_EPISODES_DB
from core.path_literals import EPISODES_DB

ENV_EPISODES = ENV_BRAIN_V2_EPISODES_DB


@dataclass
class Turn:
    user: str
    check: Callable[[str], bool]
    label: str


@dataclass
class ScenarioResult:
    name: str
    rows: List[tuple[str, bool]] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)


def _has(*parts: str) -> Callable[[str], bool]:
    def _fn(reply: str) -> bool:
        low = (reply or "").lower()
        return all(p.lower() in low for p in parts)

    return _fn


def _lacks(*parts: str) -> Callable[[str], bool]:
    def _fn(reply: str) -> bool:
        low = (reply or "").lower()
        return all(p.lower() not in low for p in parts)

    return _fn


def _all_of(*checks: Callable[[str], bool]) -> Callable[[str], bool]:
    def _fn(reply: str) -> bool:
        return all(c(reply) for c in checks)

    return _fn


def _no_reviewed_memory_yet(reply: str) -> bool:
    low = (reply or "").lower()
    return "reviewed memory" in low and (
        "do not have" in low or "don't have" in low
    )


def _asks_save_scope(reply: str) -> bool:
    low = (reply or "").lower()
    return "save in memory" in low and "session only" in low


def _saved_long_term(reply: str) -> bool:
    low = (reply or "").lower()
    return "saved in long-term" in low or "brain v2" in low or "remember" in low


def _auto_saved_core(reply: str) -> bool:
    low = (reply or "").lower()
    if "save in memory" in low and "session only" in low:
        return False
    return _saved_long_term(reply)


def _weather_ok(reply: str) -> bool:
    low = (reply or "").lower()
    if "secret" in low or "appid" in low:
        return False
    return any(
        token in low
        for token in (
            "°",
            "humidity",
            "weather in",
            "api key",
            "couldn't find",
            "weather service is unavailable",
            "which city",
        )
    )


def _run_scenario(
    name: str, turns: List[Turn], *, seed_accept: Optional[List[str]] = None
) -> ScenarioResult:
    result = ScenarioResult(name=name)
    tmp = Path(tempfile.mkdtemp(prefix="hikari-brain-qa-"))
    os.environ[ENV_EPISODES] = str(tmp / EPISODES_DB)
    os.environ["HIKARI_LEGACY_DATA_DIR"] = str(tmp / "legacy")
    os.environ.pop("HIKARI_NEURAL_MEMORY_DB", None)
    os.environ.pop("HIKARI_DISABLE_BRAIN_V2", None)
    os.environ["HIKARI_DISABLE_PROACTIVE_SCHEDULER"] = "1"
    os.environ["HIKARI_DISABLE_OSASCRIPT"] = "1"
    os.environ.setdefault("HIKARI_PRIMARY_USER", "Owner A")

    from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
    from core.brain_v2.memory_review_gate import MemoryReviewGate
    from core.brain_v2.episode_store import EpisodeStore
    from core.orchestrator import HIKARI_Orchestrator

    if seed_accept:
        store = EpisodeStore(db_path=tmp / EPISODES_DB)
        for stmt in seed_accept:
            eid = store.create_episode("seed")
            store.add_turn(eid, stmt, is_user=True)
            cands = EpisodeConsolidationPipeline(store).process_episode(eid)[1]
            if cands:
                MemoryReviewGate(store).accept(cands[0].candidate_id)

    orch = HIKARI_Orchestrator()
    if not orch.brain_v2 or not orch._brain_v2_session:
        result.failures.append(f"{name}: Brain v2 runtime not ready")
        return result

    print(f"\n=== {name} ===")
    for turn in turns:
        reply = orch.process_input(turn.user) or ""
        ok = turn.check(reply)
        result.rows.append((turn.label, ok))
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {turn.label}")
        print(f"       You: {turn.user}")
        print(f"       HIKARI: {reply[:200]}{'...' if len(reply) > 200 else ''}")
        if not ok:
            result.failures.append(f"{name} / {turn.label}: {reply!r}")
    try:
        orch.finalize_session()
    except Exception:
        pass
    return result


def _print_table(results: List[ScenarioResult]) -> None:
    print("\n" + "=" * 72)
    print(f"{'SCENARIO':<28} {'CHECK':<32} {'RESULT':<6}")
    print("-" * 72)
    for scenario in results:
        for label, passed in scenario.rows:
            print(f"{scenario.name:<28} {label:<32} {'PASS' if passed else 'FAIL':<6}")
    print("=" * 72)


def main() -> int:
    results: List[ScenarioResult] = []

    results.append(
        _run_scenario(
            "no_name_before_memory",
            [
                Turn(
                    "what is my name?",
                    _no_reviewed_memory_yet,
                    "unknown name before memory",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "identity_auto_save",
            [
                Turn(
                    "Remember this: My name is Owner A.",
                    _has("brain v2"),
                    "identity accepted",
                ),
                Turn(
                    "what is my name?",
                    _has("owner a"),
                    "recall identity",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "bare_identity",
            [
                Turn("My name is Owner A.", _auto_saved_core, "bare my name is accepted"),
                Turn("what is my name?", _has("owner a"), "recall bare identity"),
            ],
        )
    )

    results.append(
        _run_scenario(
            "bare_preference",
            [
                Turn("I prefer Topic A.", _auto_saved_core, "bare preference accepted"),
                Turn("what do you know about me?", _has("topic a"), "preference in profile"),
            ],
        )
    )

    results.append(
        _run_scenario(
            "bare_dislike",
            [
                Turn("I don't like Topic B.", _auto_saved_core, "bare dislike accepted"),
                Turn("what do you know about me?", _has("topic b"), "dislike in profile"),
            ],
        )
    )

    results.append(
        _run_scenario(
            "bare_education",
            [
                Turn("I study at School A.", _auto_saved_core, "bare education accepted"),
                Turn("what do you know about me?", _has("school a"), "education in profile"),
            ],
        )
    )

    results.append(
        _run_scenario(
            "preferred_name",
            [
                Turn(
                    "you can call me Person C",
                    lambda r: _auto_saved_core(r) or "call you" in r.lower(),
                    "preferred name stored",
                ),
                Turn(
                    "what is my name?",
                    _has("person c"),
                    "recall preferred name",
                ),
            ],
            seed_accept=[
                "Remember this: My name is Owner A but official name is Person C.",
            ],
        )
    )

    results.append(
        _run_scenario(
            "legal_vs_preferred_name",
            [
                Turn(
                    "My real name is Owner Legal but call me Person C",
                    _all_of(_auto_saved_core, _has("owner legal")),
                    "dual identity stored",
                ),
                Turn(
                    "what is my real name?",
                    _all_of(_has("owner legal"), _lacks("person c")),
                    "real name query returns legal only",
                ),
                Turn(
                    "what is my name?",
                    _has("person c"),
                    "display name query returns preferred",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "degree_education",
            [
                Turn(
                    "I am doing my bachelors in Topic A at School A",
                    _auto_saved_core,
                    "degree statement saved",
                ),
                Turn(
                    "what do I study?",
                    _all_of(_has("topic a"), _lacks("reviewed memory")),
                    "education recall",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "stable_home",
            [
                Turn(
                    "Remember this: I live in City A.",
                    _has("brain v2"),
                    "stable home accepted",
                ),
                Turn(
                    "where do I live?",
                    _has("city a"),
                    "stable home recall",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "bare_stable_home",
            [
                Turn("I live in City A.", _auto_saved_core, "bare I live in accepted"),
                Turn(
                    "where do I live?",
                    _has("city a"),
                    "recall bare stable home",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "current_location",
            [
                Turn(
                    "where am i now",
                    _no_reviewed_memory_yet,
                    "no location before declare",
                ),
                Turn(
                    "I am in City B",
                    _has("current location"),
                    "declare city b",
                ),
                Turn(
                    "where am i now ?",
                    _has("city b"),
                    "recall city b",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "meta_phrase",
            [
                Turn("I am in City B", _has("current location"), "set session city"),
                Turn(
                    "the city im in now",
                    _all_of(_has("city b"), _lacks("the city im in now")),
                    "meta phrase must not corrupt",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "relationship",
            [
                Turn(
                    "Person C is my sister",
                    _asks_save_scope,
                    "sister asks save scope",
                ),
                Turn("save in memory", _saved_long_term, "sister saved"),
                Turn(
                    "who is my sister?",
                    _has("person c"),
                    "recall sister",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "plan_memory",
            [
                Turn(
                    "Remember this: I will meet Person C for lunch tomorrow.",
                    _has("brain v2"),
                    "plan accepted",
                ),
                Turn(
                    "what are my plans tomorrow?",
                    _all_of(_has("person c"), _has("lunch"), _lacks("reviewed memory")),
                    "plan recall",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "profile",
            [
                Turn(
                    "Remember this: I live in City A.",
                    _has("brain v2"),
                    "seed profile fact",
                ),
                Turn(
                    "what do you know about me?",
                    _all_of(_has("city a"), _lacks("neural")),
                    "profile from brain v2 only",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "memory_summary",
            [
                Turn(
                    "Remember this: I live in City A.",
                    _has("brain v2"),
                    "seed location",
                ),
                Turn(
                    "Remember this: I prefer Topic A.",
                    _has("brain v2"),
                    "seed preference",
                ),
                Turn(
                    "what do you remember?",
                    _all_of(_has("what i know about you"), _has("city a"), _has("topic a")),
                    "summary is merged profile",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "guest_declarative_no_store",
            [
                Turn(
                    "I am Guest B talking to you now",
                    _has("guest b"),
                    "guest intro deterministic",
                ),
                Turn(
                    "My name is Guest B.",
                    lambda r: "not store" in r.lower() or "will not" in r.lower(),
                    "guest fact not stored",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "guest_privacy",
            [
                Turn(
                    "Remember this: My girlfriend is Person C.",
                    _has("brain v2"),
                    "owner relationship stored",
                ),
                Turn(
                    "I am Guest B talking to you now",
                    _has("guest b"),
                    "guest intro",
                ),
                Turn(
                    "who is my girlfriend?",
                    _all_of(
                        _has("do not have"),
                        _lacks("person c"),
                    ),
                    "guest cannot read owner memory",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "guest_restore_owner_location",
            [
                Turn("I am in City B", _has("current location"), "owner session city"),
                Turn("I am Guest B talking to you now", _has("guest b"), "guest intro"),
                Turn(
                    "where am i now?",
                    _all_of(_has("guest"), _lacks("city b")),
                    "guest cannot read owner session city",
                ),
                Turn("back to owner", _has("back to you"), "owner reset"),
                Turn(
                    "where am i now?",
                    _all_of(_has("for this session"), _has("city b")),
                    "owner session city restored",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "unknown_fact",
            [
                Turn(
                    "who is my brother?",
                    _no_reviewed_memory_yet,
                    "honest unknown",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "weather_session",
            [
                Turn("I am in City B", _has("current location"), "set session city"),
                Turn(
                    "whats the weather outside",
                    lambda r: "city b" in r.lower() and _weather_ok(r),
                    "weather outside resolves session city",
                ),
                Turn(
                    "whats the weather in the city im in now",
                    lambda r: "city b" in r.lower() and _weather_ok(r),
                    "weather resolves city b safely",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "noisy_session_place",
            [
                Turn(
                    "I am in my hometown which is City B for summer vacation",
                    _has("current location"),
                    "noisy declare accepted",
                ),
                Turn(
                    "where am i now?",
                    _all_of(_has("city b"), _lacks("hometown which")),
                    "where now returns clean city label",
                ),
                Turn(
                    "whats the weather outside",
                    lambda r: "city b" in r.lower() and _weather_ok(r),
                    "weather uses refined city",
                ),
            ],
        )
    )

    results.append(
        _run_scenario(
            "home_weather",
            [
                Turn(
                    "Remember this: I live in City A.",
                    _has("brain v2"),
                    "stable home for weather",
                ),
                Turn(
                    "whats the weather in the city i live in",
                    lambda r: "city a" in r.lower() and _weather_ok(r),
                    "home weather from stable memory",
                ),
            ],
        )
    )

    def _task_reply_ok(reply: str, *, allow_open: bool = False) -> bool:
        low = (reply or "").lower()
        if "trouble thinking" in low:
            return False
        if "remember that in brain v2" in low:
            return False
        if allow_open and "opening" in low:
            return True
        return "will not store that as a brain v2 memory" in low

    results.append(
        _run_scenario(
            "task_not_memory",
            [
                Turn(
                    "remind me to call Person C tomorrow",
                    lambda r: _task_reply_ok(r)
                    and "not scheduled yet" in r.lower(),
                    "remind me not stored",
                ),
                Turn(
                    "open the settings panel",
                    lambda r: _task_reply_ok(r, allow_open=True),
                    "open not stored",
                ),
                Turn(
                    "write code for Topic A",
                    lambda r: _task_reply_ok(r) and "coding task request" in r.lower(),
                    "write code not stored",
                ),
                Turn(
                    "schedule my meeting with Person C",
                    lambda r: _task_reply_ok(r)
                    and "calendar scheduling is not wired up yet" in r.lower(),
                    "schedule not stored",
                ),
            ],
        )
    )

    all_failures: List[str] = []
    for scenario in results:
        all_failures.extend(scenario.failures)

    _print_table(results)

    if all_failures:
        print(f"\nFAILED: {len(all_failures)} check(s)")
        for failure in all_failures:
            print(f"  - {failure}")
        return 1
    print("\nALL LIVE QA SCENARIOS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
