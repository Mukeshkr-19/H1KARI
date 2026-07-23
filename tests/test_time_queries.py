"""Regression tests for deterministic offline time-of-day handling."""

from datetime import datetime, timezone

from core.time_queries import answer_time_query


NOW_UTC = datetime(2026, 7, 23, 3, 34, tzinfo=timezone.utc)


def test_india_time_uses_kolkata_zone_instead_of_computer_local_time():
    assert answer_time_query("what is the time in India?", now=NOW_UTC) == (
        "The current time in India is 9:04 AM IST."
    )


def test_time_followup_correction_uses_previous_time_intent():
    assert answer_time_query(
        "no, in India man?", previous_was_time=True, now=NOW_UTC
    ) == "The current time in India is 9:04 AM IST."


def test_location_phrase_is_not_hijacked_without_previous_time_intent():
    assert answer_time_query("I studied in India", now=NOW_UTC) is None


def test_unknown_location_does_not_silently_return_local_time():
    answer = answer_time_query("what time is it in Atlantis?", now=NOW_UTC)
    assert answer is not None
    assert "don't recognize" in answer
    assert "local time" not in answer


def test_plain_time_question_still_uses_local_clock():
    answer = answer_time_query("what time is it?", now=NOW_UTC)
    assert answer is not None
    assert answer.startswith("The local time is ")


def test_orchestrator_tracks_time_intent_for_short_corrections(monkeypatch):
    from core.orchestrator import HIKARI_Orchestrator

    orch = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)
    monkeypatch.setattr(
        "core.orchestrator.answer_time_query",
        lambda text, *, previous_was_time=False: (
            "india-time" if previous_was_time and "india" in text else "first-time"
        ),
    )

    assert orch._handle_special_commands("what is the time in India?") == "first-time"
    assert orch._last_special_intent == "time"
    assert orch._handle_special_commands("no, in India man?") == "india-time"
