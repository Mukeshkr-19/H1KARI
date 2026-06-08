"""Shared non-person stopwords for query/memory person-name extraction."""

from __future__ import annotations

import re
from typing import FrozenSet, Set

_WEEKDAYS_LOWER: FrozenSet[str] = frozenset(
    {
        "sunday",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
    }
)

_MONTHS_LOWER: FrozenSet[str] = frozenset(
    {
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    }
)

_MEAL_AND_PLAN_LOWER: FrozenSet[str] = frozenset(
    {
        "lunch",
        "dinner",
        "brunch",
        "breakfast",
        "meeting",
        "meet",
        "plans",
        "plan",
        "restaurant",
    }
)

_TEMPORAL_LOWER: FrozenSet[str] = frozenset(
    {
        "today",
        "tomorrow",
        "tonight",
    }
)

_ORG_PLACE_LOWER: FrozenSet[str] = frozenset(
    {
        "university",
        "college",
        "school",
        "hospital",
        "clinic",
        "nation",
        "bbq",
        "medical",
        "student",
        "citya",
        "cityb",
        "north",
        "valley",
        "river",
        "city",
    }
)

_SYSTEM_LOWER: FrozenSet[str] = frozenset(
    {
        "hikari",
        "brain",
        "remember",
    }
)

_RELATION_LOWER: FrozenSet[str] = frozenset(
    {
        "dad",
        "father",
        "mom",
        "mother",
        "sister",
        "brother",
        "gf",
        "girlfriend",
        "partner",
        "boyfriend",
        "wife",
        "husband",
        "parents",
        "parent",
    }
)

_QUERY_WORDS_LOWER: FrozenSet[str] = frozenset(
    {
        "what",
        "whats",
        "where",
        "when",
        "who",
        "which",
        "how",
        "tell",
        "do",
        "does",
        "did",
        "is",
        "are",
        "can",
        "could",
    }
)

NON_PERSON_WORDS_LOWER: FrozenSet[str] = (
    _WEEKDAYS_LOWER
    | _MONTHS_LOWER
    | _MEAL_AND_PLAN_LOWER
    | _TEMPORAL_LOWER
    | _ORG_PLACE_LOWER
    | _SYSTEM_LOWER
    | _RELATION_LOWER
    | _QUERY_WORDS_LOWER
)

# Title-case tokens skipped by memory_type.extract_person_names (same rules).
NON_PERSON_WORDS_TITLE: FrozenSet[str] = frozenset(
    w.capitalize() if w != "bbq" else "BBQ" for w in NON_PERSON_WORDS_LOWER
) | frozenset(
    {
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "Remember",
        "Hikari",
        "HIKARI",
        "Brain",
        "University",
        "College",
        "Nation",
        "Citya",
        "Medical",
        "Student",
        "BBQ",
        "Restaurant",
    }
)

_ORG_SUFFIXES_LOWER: FrozenSet[str] = frozenset(
    {"university", "college", "school", "hospital", "clinic", "nation"}
)


def is_non_person_name(word: str) -> bool:
    """True when a token must not be treated as a person's name."""
    low = (word or "").strip().lower()
    if not low:
        return True
    if low in NON_PERSON_WORDS_LOWER:
        return True
    for suffix in _ORG_SUFFIXES_LOWER:
        if low == suffix or low.endswith(suffix):
            return True
    return False


def capitalized_person_names_in_text(text: str) -> Set[str]:
    """Lowercase person names from Title-case tokens in free text."""
    found: Set[str] = set()
    for match in re.finditer(r"\b([A-Z][a-z]{2,})\b", text or ""):
        word = match.group(1)
        if not is_non_person_name(word):
            found.add(word.lower())
    return found
