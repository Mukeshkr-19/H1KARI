"""Deterministic recall intent routing for Brain v2 retrieval (no LLM)."""

from __future__ import annotations

import re
from typing import FrozenSet, Optional, Set

from core.brain_v2.person_name_tokens import (
    capitalized_person_names_in_text,
    is_non_person_name,
)

INTENT_IDENTITY_SELF = "identity_self"
INTENT_FAMILY_PERSON = "family_person"
INTENT_RELATIONSHIP = "relationship"
INTENT_PREFERENCE = "preference"
INTENT_LOCATION = "location"
INTENT_CURRENT_LOCATION = "current_location"
INTENT_TRAVEL = "travel"
INTENT_PLAN = "plan"
INTENT_EDUCATION = "education"
INTENT_HIKARI_DECISION = "hikari_project_decision"
INTENT_PROFILE_SUMMARY = "profile_summary"
INTENT_GENERAL_MEMORY = "general_memory"
INTENT_NON_MEMORY = "non_memory"

BRAIN_V2_NO_REVIEWED_MEMORY_MESSAGE = (
    "I do not have a reviewed memory for that yet."
)
BRAIN_V2_UNAVAILABLE_MESSAGE = (
    "Brain v2 is temporarily unavailable. "
    "I do not have a reviewed memory for that yet."
)

PERSONAL_RECALL_INTENTS: FrozenSet[str] = frozenset(
    {
        INTENT_IDENTITY_SELF,
        INTENT_FAMILY_PERSON,
        INTENT_RELATIONSHIP,
        INTENT_PREFERENCE,
        INTENT_LOCATION,
        INTENT_CURRENT_LOCATION,
        INTENT_TRAVEL,
        INTENT_PLAN,
        INTENT_EDUCATION,
        INTENT_HIKARI_DECISION,
        INTENT_PROFILE_SUMMARY,
        INTENT_GENERAL_MEMORY,
    }
)

_FAMILY_RELATIONS = (
    "sister",
    "brother",
    "mom",
    "mother",
    "dad",
    "father",
    "gf",
    "girlfriend",
    "partner",
    "boyfriend",
    "wife",
    "husband",
    "parents",
    "parent",
)


def classify_recall_intent(query: str) -> str:
    """Classify a user query into a recall intent label."""
    raw = (query or "").strip()
    q = raw.lower().rstrip("?!.").strip()
    if not q:
        return INTENT_NON_MEMORY

    if _matches_hikari_decision(q):
        return INTENT_HIKARI_DECISION
    if _matches_profile_summary(q):
        return INTENT_PROFILE_SUMMARY
    if _matches_identity_self(q):
        return INTENT_IDENTITY_SELF
    if _matches_family_person(q):
        return INTENT_FAMILY_PERSON
    if _matches_relation_in_place(q):
        return INTENT_FAMILY_PERSON
    if _matches_plan(q):
        return INTENT_PLAN
    if _matches_education_query(q):
        return INTENT_EDUCATION
    if _matches_relationship(q):
        return INTENT_RELATIONSHIP
    if _matches_preference(q):
        return INTENT_PREFERENCE
    if _matches_current_location(q):
        return INTENT_CURRENT_LOCATION
    if _matches_stable_location(q):
        return INTENT_LOCATION
    if _matches_travel(q):
        return INTENT_TRAVEL
    if _matches_general_memory(q):
        return INTENT_GENERAL_MEMORY
    if _matches_personal_factual_self(q):
        return _personal_factual_intent_for(q)

    if _matches_conservative_personal_cue(q):
        return INTENT_GENERAL_MEMORY

    return INTENT_NON_MEMORY


def is_personal_recall_intent(intent: str) -> bool:
    return intent in PERSONAL_RECALL_INTENTS


def is_plausible_personal_memory_query(query: str) -> bool:
    """Conservative personal-memory detection; prefer no-reviewed over neural fallback."""
    return has_personal_memory_authority_surface(query)


def is_task_or_action_request(query: str) -> bool:
    """Help/action phrasing is not a personal-memory factual recall question."""
    return _is_task_or_action_shape((query or "").lower().rstrip("?!.").strip())


def has_personal_memory_authority_surface(query: str) -> bool:
    """True when Brain v2 must own recall (legacy neural answer surface is quarantined)."""
    if is_task_or_action_request(query):
        return False
    intent = classify_recall_intent(query)
    if is_personal_recall_intent(intent):
        return True
    q = (query or "").lower().rstrip("?!.").strip()
    return bool(q) and _matches_personal_memory_surface_cue(q)


def is_personal_factual_question(query: str) -> bool:
    """Personal factual recall must not fall through to general AI when Brain v2 is on."""
    return has_personal_memory_authority_surface(query)


def should_skip_external_research(query: str) -> bool:
    """Personal recall questions must not use web/research agents."""
    return is_plausible_personal_memory_query(query)


def is_positive_brain_v2_recall_answer(text: Optional[str]) -> bool:
    """True when Brain v2 returned a reviewed memory answer (not a missing-memory fallback)."""
    if is_brain_v2_no_reviewed_memory_answer(text):
        return False
    low = (text or "").lower()
    if (
        "from reviewed memory" in low
        or "from reviewed brain v2 memories" in low
        or "from recent session context" in low
        or bool(
            re.search(
                r"\b(?:my|your)\s+(?:real\s+|legal\s+|official\s+)?name\s+is\b",
                low,
            )
        )
        or bool(re.search(r"\b(?:legal|real|official)\s+name\s+is\b", low))
        or bool(re.search(r"\byou(?:'re| are)\s+in\s+.+\s+for\s+this\s+session\b", low))
        or bool(re.search(r"\bi\s+call\s+you\s+\w+", low))
        or bool(re.search(r"\byes\s+-\s+.+\s+visited\s+as\s+a\s+guest\b", low))
    ):
        return True
    if low.startswith("yes."):
        return True
    if re.search(
        r"\b(?:live in|study|studies|bachelors?|degree|major|prefer|fav(?:ou?rite)?|graduat|"
        r"rising senior|meet|meeting|will be|"
        r"works?|worked|medical student|girlfriend|boyfriend|my sister|my brother|"
        r"my dad|my mom)\b",
        low,
    ):
        return True
    return False


def is_brain_v2_conflict_review_answer(text: Optional[str]) -> bool:
    """True when Brain v2 blocked recall pending legacy conflict review."""
    from core.brain_v2.neural_conflict_state import CONFLICT_REVIEW_NEEDED_MESSAGE

    return bool(text and text.strip() == CONFLICT_REVIEW_NEEDED_MESSAGE)


def is_brain_v2_no_reviewed_memory_answer(text: Optional[str]) -> bool:
    """True when Brain v2 returned an honest missing-reviewed-memory response."""
    if not (text or "").strip():
        return False
    low = text.lower()
    return (
        "do not have a reviewed memory" in low
        or "don't have a reviewed memory" in low
        or "don't have reviewed brain v2 memories" in low
        or "brain v2 is temporarily unavailable" in low
    )


def is_brain_v2_authoritative_personal_recall_answer(text: Optional[str]) -> bool:
    """Personal recall must stop here; legacy neural must not answer next."""
    if not (text or "").strip():
        return False
    return (
        is_brain_v2_conflict_review_answer(text)
        or is_positive_brain_v2_recall_answer(text)
        or is_brain_v2_no_reviewed_memory_answer(text)
    )


_ALIAS_TO_CANONICAL = {
    "mom": "mother",
    "mother": "mother",
    "dad": "father",
    "father": "father",
    "gf": "girlfriend",
    "girlfriend": "girlfriend",
    "partner": "partner",
    "sister": "sister",
    "brother": "brother",
    "wife": "wife",
    "husband": "husband",
    "boyfriend": "boyfriend",
}

_PARTNER_RELATIONS: FrozenSet[str] = frozenset({"girlfriend", "partner", "boyfriend"})


def normalize_relation_token(token: str) -> str:
    return _ALIAS_TO_CANONICAL.get((token or "").lower().strip(), (token or "").lower().strip())


def requested_relations(query: str) -> Set[str]:
    """Extract normalized relation(s) explicitly asked about in the query."""
    q = (query or "").lower()
    found: Set[str] = set()
    if not q:
        return found

    for alias, canonical in _ALIAS_TO_CANONICAL.items():
        patterns = (
            rf"\bmy\s+{re.escape(alias)}\b",
            rf"\bdo\s+(?:you|u)\s+know\s+my\s+{re.escape(alias)}\b",
            rf"\bwho\s+is\s+my\s+{re.escape(alias)}\b",
            rf"\bwhat\s+does\s+my\s+{re.escape(alias)}\b",
            rf"\bwhere\s+does\s+my\s+{re.escape(alias)}\b",
            rf"\bwhat\s+(?:school|university|college)\s+does\s+my\s+{re.escape(alias)}\b",
        )
        if any(re.search(pat, q) for pat in patterns):
            found.add(canonical)
    return found


def memory_relations_from_text(statement: str, metadata: Optional[dict] = None) -> Set[str]:
    """Normalized relation(s) present in an accepted memory statement/metadata."""
    found: Set[str] = set()
    meta = metadata or {}
    if meta.get("relation"):
        found.add(normalize_relation_token(str(meta["relation"])))

    text = (statement or "").lower()
    for alias, canonical in _ALIAS_TO_CANONICAL.items():
        if re.search(rf"\bmy\s+{re.escape(alias)}\b", text):
            found.add(canonical)
    return found


def requested_person_names(query: str) -> Set[str]:
    """Lowercase person name(s) explicitly referenced in a recall query."""
    raw = query or ""
    found = capitalized_person_names_in_text(raw)

    low = raw.lower()
    patterns = (
        r"\bmeeting\s+([a-z]{3,})\b",
        r"\bwith\s+([a-z]{3,})\b",
        r"\bwhat\s+does\s+([a-z]{3,})\s+study\b",
        r"\bwhat\s+is\s+([a-z]{3,})\s+studying\b",
        r"\bwhat\s+(?:school|university|college)\s+does\s+([a-z]{3,})\b",
    )
    for pat in patterns:
        match = re.search(pat, low)
        if match:
            name = match.group(1).lower()
            if not is_non_person_name(name) and name not in _ALIAS_TO_CANONICAL:
                found.add(name)
    return found


def memory_person_names(statement: str, metadata: Optional[dict] = None) -> Set[str]:
    """Person name tokens associated with an accepted memory."""
    found: Set[str] = set()
    meta = metadata or {}
    person = meta.get("person")
    if person and not is_non_person_name(str(person)):
        found.add(str(person).lower())
    found |= capitalized_person_names_in_text(statement or "")
    return found


def person_names_compatible(requested: Set[str], memory: Set[str]) -> bool:
    if not requested:
        return True
    if not memory:
        return False
    return bool(requested & memory)


def relations_compatible(requested: Set[str], memory: Set[str]) -> bool:
    """True when query relation(s) match memory relation(s)."""
    if not requested:
        return True
    if not memory:
        return False
    for req in requested:
        for mem in memory:
            if req == mem:
                return True
            if req in _PARTNER_RELATIONS and mem in _PARTNER_RELATIONS:
                return True
    return False


def _matches_hikari_decision(q: str) -> bool:
    return bool(
        re.search(r"\b(?:for\s+)?hikari\b", q)
        and re.search(r"\b(decid|decision|brain|design|project|memory|review|pipeline)\b", q)
    ) or bool(re.search(r"\bwhat\s+did\s+we\s+decide\b", q) and "hikari" in q)


def _matches_profile_summary(q: str) -> bool:
    return bool(
        re.search(
            r"\bwhat\s+do\s+you\s+know\s+about\s+me\b",
            q,
        )
        or re.search(r"\bwhat\s+have\s+you\s+(?:learned|remembered)\s+about\s+me\b", q)
        or re.search(r"\bsummarize\s+(?:what\s+you\s+know|my\s+profile)\b", q)
        or q in {"what do you know about me", "tell me what you know about me"}
    )


def _matches_identity_self(q: str) -> bool:
    return bool(
        re.search(r"\bwho\s+am\s+i\b", q)
        or re.search(r"\bwhat(?:'s|\s+is)\s+my\s+(?:(?:official|legal|real|full)\s+)?name\b", q)
        or re.search(r"\bwhats?\s+my\s+(?:(?:official|legal|real|full)\s+)?name\b", q)
        or re.search(r"\b(?:tell|remind)\s+me\s+my\s+(?:(?:official|legal|real|full)\s+)?name\b", q)
        or q in {
            "my name",
            "my official name",
            "my legal name",
            "my real name",
            "my full name",
            "my identity",
        }
    )


# Structural personal-factual firewall (possessive anchor + recall shape; not phrase lists).
_TASK_ACTION_VERBS = (
    r"write|draft|fix|build|create|send|email|proofread|rewrite|edit|"
    r"polish|format|debug|code|resume|cv|essay|report"
)
_HOW_TO_PROCEDURAL = r"fix|write|build|create|draft|send|debug|code|email|resume|cv"


def matches_personal_factual_firewall(q: str) -> bool:
    """Conservative boundary: first-person possessive + factual recall, excluding task shapes."""
    if not q or _is_task_or_action_shape(q):
        return False
    if not _has_self_possessive_anchor(q):
        return False
    return _has_factual_recall_interrogative(q)


def _is_help_me_personal_recall(q: str) -> bool:
    """Help/remind phrasing that still requests recall of a stored personal fact."""
    if not re.search(
        r"\b(?:help\s+me|can\s+you\s+help\s+me|could\s+you\s+help\s+me)\s+"
        r"(?:remember|recall)\b",
        q,
    ):
        return False
    return _has_self_possessive_anchor(q) and (
        _has_factual_recall_interrogative(q)
        or re.search(r"\b(?:remember|recall)\s+(?:what|where|when|who|which|my)\b", q)
    )


def _is_task_or_action_shape(q: str) -> bool:
    """Imperative/help/how-to-fix phrasing — not asking Brain v2 to recall a stored fact."""
    if not q:
        return False
    if _is_help_me_personal_recall(q):
        return False
    if re.search(
        r"\bhelp\s+me\s+(?:prepare|practice|study|write|draft|debug|fix|build|send|email)\b",
        q,
    ):
        return True
    if re.search(r"\bhelp\s+me\b", q):
        return True
    if re.search(rf"\bhow\s+do\s+i\s+(?:{_HOW_TO_PROCEDURAL})\b", q):
        return True
    if re.search(
        rf"\b(?:please\s+)?(?:can|could|would)\s+you\s+"
        rf"(?:help\s+me\s+)?(?:{_TASK_ACTION_VERBS})\b",
        q,
    ):
        return True
    if re.search(rf"\b(?:{_TASK_ACTION_VERBS})\s+my\s+\w+\b", q):
        return True
    if re.search(
        r"\bplan\s+my\s+(?:study|work|revision|learning|exam|revision)\s+"
        r"(?:schedule|plan|calendar|routine)\b",
        q,
    ):
        return True
    if re.search(r"\bmake\s+me\s+a\s+(?:resume|cv|study\s+plan)\b", q):
        return True
    return False


def _has_self_possessive_anchor(q: str) -> bool:
    """User-owned referent: my/me/I in a recall-oriented question."""
    if re.search(r"\bmy\b", q):
        return True
    if re.search(
        r"\b(?:who|what|where|when|which|how)\s+am\s+i\b"
        r"|\babout\s+me\b"
        r"|\bi\s+(?:told|said|mentioned|shared)\s+(?:you|u)\b"
        r"|\bwhat\s+did\s+i\s+tell\s+you\b"
        r"|\bdo\s+i\s+have\b"
        r"|\bwhere\s+(?:was|am)\s+i\b"
        r"|\bwhen\s+(?:was|am)\s+i\b"
        r"|\bhow\s+old\s+am\s+i\b"
        r"|\bhow\s+many\b"
        r"|\b(?:when|where|what|who|which)\s+(?:do|did|does|is|was|are|were)\s+i\b"
        r"|\bwhich\b.+\b(?:do|did|does|was|were|are|is)\s+i\b",
        q,
    ):
        return True
    return False


def _has_factual_recall_interrogative(q: str) -> bool:
    """Interrogative shape that requests a stored personal fact (not procedural help)."""
    if _matches_identity_self(q):
        return True
    if re.search(
        r"\bwhat(?:'s|\s+is|\s+are|\s+was|\s+were)\s+my\s+\w+",
        q,
    ):
        return True
    if re.search(
        r"\bwhere\s+(?:is|was|are|were|am|do|did)\s+(?:my|i)\b",
        q,
    ):
        return True
    if re.search(
        r"\bwhen\s+(?:is|was|are|were|do|did|am)\s+(?:my|i)\b",
        q,
    ):
        return True
    if re.search(r"\bwhen\s+do\s+i\s+\w+", q):
        return True
    if re.search(r"\bwhere\s+do\s+i\s+\w+", q):
        return True
    if re.search(r"\bwho\s+is\s+my\s+\w+", q):
        return True
    if re.search(r"\bwhich\s+.+\s+(?:do|did|does|was|were|are|is)\s+i\b", q):
        return True
    if re.search(r"\bdo\s+i\s+have\b", q):
        return True
    if re.search(r"\bwhat\s+time\s+is\s+my\s+\w+", q):
        return True
    if re.search(r"\bwhat\s+did\s+i\s+tell\s+you\b", q):
        return True
    if re.search(r"\bhow\s+old\s+am\s+i\b", q):
        return True
    if re.search(r"\bhow\s+many\s+\w+\s+do\s+i\s+have\b", q):
        return True
    if re.search(r"\bdo\s+(?:you|u)\s+know\s+my\s+\w+", q):
        return True
    if re.search(r"\btell\s+me\s+(?:my|about\s+my)\s+\w+", q):
        return True
    if re.search(r"\b(?:remember|recall)\b", q) and re.search(r"\b(?:my|i|me)\b", q):
        return True
    if re.search(r"\bmy\s+\w+(?:'s)?\s+\w+", q) and re.search(
        r"\b(?:what|where|when|who|which|how|tell|know|remember)\b",
        q,
    ):
        return True
    if re.search(r"\b(?:what|where|when|who|which)\b", q) and re.search(r"\bmy\b", q):
        return True
    return False


def _matches_personal_factual_self(q: str) -> bool:
    """Questions seeking a stored fact about the user (structural firewall, not phrase lists)."""
    return matches_personal_factual_firewall(q)


def _personal_factual_intent_for(q: str) -> str:
    if re.search(r"\b(?:birthday|born|birthplace)\b", q):
        return INTENT_GENERAL_MEMORY
    if re.search(r"\b(?:work|job|employer|occupation)\b", q):
        return INTENT_GENERAL_MEMORY
    if re.search(r"\b(?:university|college|school|attend)\b", q):
        return INTENT_EDUCATION
    if re.search(r"\bsiblings?\b", q):
        return INTENT_FAMILY_PERSON
    if _matches_identity_self(q):
        return INTENT_IDENTITY_SELF
    return INTENT_GENERAL_MEMORY


def _matches_family_person(q: str) -> bool:
    rel_alt = "|".join(_FAMILY_RELATIONS)
    if re.search(rf"\bmy\s+(?:{rel_alt})'?s?\s+", q):
        return True
    if re.search(rf"\b(?:did|does|do|is|are|was|were)\s+my\s+(?:{rel_alt})\b", q):
        return True
    if re.search(r"\bdo\s+(?:you|u)\s+know\s+my\s+", q):
        return any(rel in q for rel in _FAMILY_RELATIONS)
    if re.search(r"\bwho\s+is\s+my\s+", q):
        return any(rel in q for rel in _FAMILY_RELATIONS)
    if re.match(rf"^my\s+(?:{rel_alt})s?\s*$", q):
        return True
    if re.search(rf"\btell\s+me\s+about\s+my\s+(?:{rel_alt})\b", q):
        return True
    if re.search(
        rf"\bwhat\s+is\s+my\s+(?:sister|brother|mother|father|mom|dad|"
        rf"girlfriend|partner|boyfriend|wife|husband|parents?)\s+doing\b",
        q,
    ):
        return True
    if re.search(rf"\bwhere\s+are\s+my\s+parents?\b", q):
        return True
    if re.search(rf"\bhow\s+is\s+my\s+(?:{rel_alt})\b", q):
        return True
    if re.search(rf"\bdoes\s+my\s+(?:{rel_alt})\s+live\b", q):
        return True
    if re.search(rf"\bis\s+my\s+(?:gf|girlfriend|partner|boyfriend|wife|husband)\s+in\b", q):
        return True
    if re.search(rf"\btell\s+me\s+about\s+my\s+partner\b", q):
        return True
    return False


def _matches_relation_in_place(q: str) -> bool:
    """Family/relationship location checks: is my sister in City A?, are my parents in ..."""
    rel_alt = "|".join(_FAMILY_RELATIONS)
    return bool(
        re.search(rf"\b(?:is|are)\s+my\s+(?:{rel_alt})\s+in\b", q)
        or re.search(rf"\b(?:is|are)\s+my\s+parents?\s+in\b", q)
    )


def _matches_plan(q: str) -> bool:
    return bool(
        re.search(r"\bwhat\s+are\s+my\s+plans\b", q)
        or re.search(r"\bwhen\s+am\s+i\s+meeting\b", q)
        or re.search(r"\bwhere\s+am\s+i\s+meeting\b", q)
        or re.search(r"\bwhat\s+am\s+i\s+doing\s+for\s+(?:lunch|dinner|brunch)\b", q)
        or re.search(r"\bwhat\s+is\s+my\s+(?:plan|meeting)\b", q)
    )


def is_casual_greeting(text: str) -> bool:
    """Short social openers that must not trigger LLM identity hallucination."""
    q = (text or "").strip().lower().rstrip("?!.").strip()
    if not q:
        return False
    if q in {
        "hi",
        "hello",
        "hey",
        "hiya",
        "yo",
        "good morning",
        "good afternoon",
        "good evening",
        "thanks",
        "thank you",
    }:
        return True
    return bool(re.match(r"^(?:hi|hello|hey)(?:\s+(?:there|hikari))?$", q))


def _matches_education_query(q: str) -> bool:
    rel_alt = "|".join(_FAMILY_RELATIONS)
    return bool(
        re.search(r"\bwhat\s+(?:do|did)\s+i\s+stud(?:y|ied)\b", q)
        or re.search(r"\bwhat\s+am\s+i\s+studying\b", q)
        or re.search(r"\bwhere\s+do\s+i\s+stud(?:y|ied)\b", q)
        or re.search(r"\bwhat\s+is\s+my\s+(?:major|degree)\b", q)
        or re.search(r"\bwhat\s+(?:university|college)\s+do\s+i\s+attend\b", q)
        or re.search(r"\bwhat\s+degree\s+am\s+i\s+pursuing\b", q)
        or re.search(r"\bwhat\s+does\s+(?:my\s+)?(?:\w+\s+)?\w+\s+study\b", q)
        or re.search(r"\bwhat\s+is\s+(?:my\s+)?(?:\w+\s+)?\w+\s+studying\b", q)
        or re.search(r"\bwhat\s+(?:school|university|college)\s+does\s+", q)
        or re.search(r"\bwhere\s+did\s+i\s+(?:go\s+to\s+)?(?:school|university|college)\b", q)
        or re.search(r"\bwhat\s+(?:school|university|college)\s+did\s+i\s+attend\b", q)
        or re.search(
            rf"\bwhere\s+did\s+my\s+(?:{rel_alt}|\w+)\s+stud",
            q,
        )
        or re.search(
            rf"\bwhere\s+does\s+my\s+(?:gf|girlfriend|partner|boyfriend|{rel_alt})\s+stud",
            q,
        )
    )


def _matches_relationship(q: str) -> bool:
    return bool(
        re.search(
            r"\bwhat\s+does\s+my\s+(?:gf|girlfriend|partner|boyfriend|wife|husband)\s+",
            q,
        )
        or re.search(
            r"\bwhere\s+does\s+my\s+(?:sister|brother|mom|mother|dad|father|gf|girlfriend|partner)\s+",
            q,
        )
        or re.search(
            r"\btell\s+me\s+about\s+my\s+(?:gf|girlfriend|partner|boyfriend|wife|husband)\b",
            q,
        )
    )


def _matches_preference(q: str) -> bool:
    return bool(
        re.search(r"\bwhat\s+do\s+i\s+prefer\b", q)
        or re.search(r"\bwhat\s+(?:are|is)\s+my\s+preferences?\b", q)
        or re.search(r"\bwhat\s+do\s+i\s+(?:like|don'?t\s+like|dislike)\b", q)
        or re.search(r"\bwho(?:'s|\s+is)\s+my\s+fav(?:ou?rite)?\s+artist\b", q)
        or re.search(r"\bwhat(?:'s|\s+is)\s+my\s+fav(?:ou?rite)?\s+\w+\b", q)
    )


def query_seeks_session_location(query: str) -> bool:
    """True when the user asks for temporary/current presence (working memory)."""
    q = (query or "").lower().rstrip("?!.").strip()
    return _matches_current_location(q)


def _matches_current_location(q: str) -> bool:
    return bool(
        re.search(r"\bwhere\s+am\s+i\s+(?:now|currently|right\s+now|today)\b", q)
        or re.search(r"\bwhere\s+am\s+i\s+at\b", q)
        or re.search(r"\bwhere\s+am\s+i\b", q)
    )


def _matches_stable_location(q: str) -> bool:
    return bool(
        re.search(r"\bwhere\s+do\s+i\s+live\b", q)
        or re.search(r"\bwhat(?:'s|\s+is)\s+my\s+home\b", q)
        or re.search(r"\bmy\s+(?:home|address|city)\b", q)
    )


def _matches_location(q: str) -> bool:
    """Backward-compatible alias for stable home location queries."""
    return _matches_stable_location(q)


def _matches_travel(q: str) -> bool:
    return bool(
        ("flight" in q or "return" in q)
        and any(w in q for w in ("when", "what", "where", "travel", "trip"))
    )


def _matches_general_memory(q: str) -> bool:
    return bool(
        re.search(r"\bwhat\s+do\s+you\s+remember\b", q)
        or re.search(r"\bdo\s+you\s+remember\b", q)
        or re.search(r"\bwhat\s+have\s+you\s+saved\b", q)
        or re.search(r"\bwhat\s+have\s+we\s+talked\s+about\b", q)
    )


def _matches_conservative_personal_cue(q: str) -> bool:
    """Catch natural personal-memory phrasing that specific matchers may miss."""
    rel_alt = "|".join(_FAMILY_RELATIONS)
    if re.search(rf"\bmy\s+(?:{rel_alt})\b", q) and re.search(
        r"\b(?:remember|recall|know about|tell me|where|what|who|when)\b", q
    ):
        return True
    if re.search(rf"\babout\s+my\s+(?:{rel_alt})\b", q):
        return True
    return False


def _matches_personal_memory_surface_cue(q: str) -> bool:
    """Second-line personal surface: structural factual firewall or household referent."""
    if matches_personal_factual_firewall(q):
        return True
    if _matches_conservative_personal_cue(q):
        return True
    rel_alt = "|".join(_FAMILY_RELATIONS)
    return bool(re.search(rf"\bmy\s+(?:{rel_alt})\b", q))
