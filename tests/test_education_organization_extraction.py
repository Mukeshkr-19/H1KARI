"""Education memory parsing must preserve generic institution names."""

from __future__ import annotations

from core.brain_v2.memory_type import infer_memory_type


def test_university_at_name_is_not_stripped_from_organization():
    inferred = infer_memory_type(
        "I study at University at River City as a computer science student"
    )

    assert inferred.candidate_type == "education"
    assert inferred.metadata["organization"] == "University at River City"
    assert (
        inferred.metadata["normalized_statement"]
        == "I study Computer Science at University at River City."
    )


def test_regular_university_name_still_extracts_generically():
    inferred = infer_memory_type(
        "I study at Redwood University as a computer science student"
    )

    assert inferred.metadata["organization"] == "Redwood University"
