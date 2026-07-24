"""Local voice transcription artifacts must normalize before routing."""

from __future__ import annotations

from core.orchestrator import HIKARI_Orchestrator


def test_spoken_2026_split_is_normalized_without_changing_other_years():
    orchestrator = HIKARI_Orchestrator.__new__(HIKARI_Orchestrator)

    assert orchestrator._normalize_user_input_text(
        "who won the 2020 26 fifa world cup?"
    ) == "who won the 2026 fifa world cup?"
    assert orchestrator._normalize_user_input_text("what happened in 2020?") == (
        "what happened in 2020?"
    )
