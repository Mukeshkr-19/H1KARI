"""Rule-based memory candidate type inference (no LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.brain_v2.location_phrases import (
    has_owner_presence_anchor,
    is_meta_or_deferred_location_phrase,
    is_valid_place_name,
    normalize_declared_place,
)
from core.brain_v2.person_name_tokens import NON_PERSON_WORDS_TITLE, is_non_person_name

_RELATION_ALIASES = (
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
)

_MONTH_PATTERN = (
    r"(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)"
)


@dataclass(frozen=True)
class MemoryTypeInference:
    candidate_type: str
    confidence: float = 0.75
    metadata: Dict[str, object] = field(default_factory=dict)


def extract_person_names(text: str) -> List[str]:
    """Case-sensitive proper names (excludes common non-person tokens)."""
    names: List[str] = []
    for match in re.finditer(r"\b[A-Z][a-z]{2,}\b", text or ""):
        word = match.group(0)
        if word not in NON_PERSON_WORDS_TITLE and not is_non_person_name(word):
            names.append(word)
    return names


def infer_memory_type(
    statement: str,
    *,
    explicit_remember: bool = False,
) -> MemoryTypeInference:
    """Infer candidate_type and structured metadata from statement text."""
    text = (statement or "").strip()
    low = text.lower()
    meta: Dict[str, object] = {}
    if explicit_remember:
        meta["explicit_remember"] = True

    if not text:
        return MemoryTypeInference("fact", 0.3, meta)

    if _is_hikari_decision(low):
        return MemoryTypeInference("decision", 0.9, meta)

    if _is_plan_or_event(low, text):
        meta.update(_extract_plan_metadata(text, low))
        return MemoryTypeInference("plan", 0.88, meta)

    current = _extract_current_location(text, low)
    if current:
        meta["current_location"] = current
        return MemoryTypeInference("current_location", 0.85, meta)

    call_me = re.search(
        r"\b(?:you can\s+)?call\s+me\s+([A-Za-z][\w'-]*(?:\s+[A-Z])?)\b",
        text,
        re.I,
    )
    if call_me:
        meta["preferred_name"] = call_me.group(1).strip().title()
        return MemoryTypeInference("identity", 0.84, meta)

    m = re.search(r"\bi\s+live\s+in\s+([A-Za-z][\w\s'-]{2,60})", text, re.I)
    if m:
        meta["location"] = m.group(1).strip().rstrip(".")
        return MemoryTypeInference("location", 0.86, meta)

    if re.search(r"\bi\s+prefer\b", low):
        return MemoryTypeInference("preference", 0.84, meta)

    if re.search(r"\bi\s+don'?t\s+like\b", low):
        return MemoryTypeInference("preference", 0.82, meta)

    if re.search(r"\bmy\s+name\s+is\b", low):
        return MemoryTypeInference("identity", 0.86, meta)

    user_edu = normalize_user_education_statement(text)
    if user_edu:
        stmt, extra = user_edu
        meta.update(extra)
        meta["normalized_statement"] = stmt
        return MemoryTypeInference("education", 0.86, meta)

    rel = _extract_relation_metadata(text, low)
    if rel:
        meta.update(rel)
        if re.search(
            r"\b(?:student|studies|studying|studied|medical\s+student|university|college)\b",
            low,
        ):
            org = _extract_organization(text)
            if org:
                meta["organization"] = org
            return MemoryTypeInference("education", 0.87, meta)
        return MemoryTypeInference("relation", 0.85, meta)

    if re.search(r"\b(?:return\s+)?flights?\b", low):
        return MemoryTypeInference("travel", 0.8, meta)

    if re.search(r"\bfor\s+hikari\b", low):
        return MemoryTypeInference("decision", 0.75, meta)

    return MemoryTypeInference("fact", 0.55, meta)


def _is_hikari_decision(low: str) -> bool:
    return bool(
        re.search(r"\bfor\s+hikari\b", low)
        and re.search(r"\b(decided|should|use|prefer|will|keep|review)\b", low)
    ) or bool(re.search(r"\bhikari\b.+\bdecided\b", low))


def _is_plan_or_event(low: str, text: str) -> bool:
    if re.search(
        r"\b(?:tomorrow|today|tonight|next\s+(?:week|month|year|monday|tuesday|"
        r"wednesday|thursday|friday|saturday|sunday))\b",
        low,
    ) and re.search(r"\b(?:meeting|meet|lunch|dinner|appointment|plans?)\b", low):
        return True
    if re.search(
        rf"\b(?:on\s+)?(?:sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b",
        low,
    ) and re.search(r"\b(?:meeting|meet|lunch|dinner)\b", low):
        return True
    if re.search(rf"\b(?:on\s+)?{_MONTH_PATTERN}\s+\d{{1,2}}", low) and re.search(
        r"\b(?:meeting|meet|lunch|dinner)\b", low
    ):
        return True
    if re.search(r"\bi\s+am\s+meeting\b", low):
        return True
    if re.search(r"\bmeeting\s+my\b", low):
        return True
    if re.search(r"\bfor\s+(?:lunch|dinner|brunch|breakfast)\b", low) and re.search(
        r"\b(?:meeting|meet|at)\b", low
    ):
        return True
    return False


def _extract_plan_metadata(text: str, low: str) -> Dict[str, object]:
    meta: Dict[str, object] = {}
    date_m = re.search(
        rf"\b(?:on\s+)?(?:(?:sunday|monday|tuesday|wednesday|thursday|friday|saturday)\s+)?"
        rf"{_MONTH_PATTERN}\s+\d{{1,2}}(?:\s+\d{{4}})?\b",
        text,
        re.I,
    )
    if not date_m:
        date_m = re.search(
            rf"\b(?:on\s+)?(?:sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b",
            text,
            re.I,
        )
    if not date_m:
        date_m = re.search(
            r"\b(?:tomorrow|today|tonight|next\s+\w+day)\b",
            text,
            re.I,
        )
    if date_m:
        meta["date_text"] = date_m.group(0).strip()

    place_m = re.search(
        r"\bat\s+([A-Z][A-Za-z0-9\s']+?)(?:\s+for\s+(?:lunch|dinner)|\s+on\s+|\s*$|\.)",
        text,
    )
    if place_m:
        meta["place"] = place_m.group(1).strip().rstrip(".")

    rel = _extract_relation_metadata(text, low)
    if rel.get("person"):
        meta["person"] = rel["person"]
    if rel.get("relation"):
        meta["relation"] = rel["relation"]

    for name in extract_person_names(text):
        if name not in (meta.get("place") or ""):
            if "person" not in meta:
                meta["person"] = name
            break

    return meta


def _extract_relation_metadata(text: str, low: str) -> Dict[str, object]:
    meta: Dict[str, object] = {}
    if _looks_like_current_location_phrase(text, low):
        return meta
    rel_pattern = r"\bmy\s+(" + "|".join(_RELATION_ALIASES) + r")\b"
    m = re.search(rel_pattern, low)
    if m:
        rel = m.group(1).lower()
        if rel == "gf":
            rel = "girlfriend"
        meta["relation"] = rel

    m2 = re.search(
        r"\bmy\s+(?:girlfriend|gf|partner|sister|brother|wife|husband|boyfriend)\s+"
        r"([A-Z][a-z]{2,})",
        text,
        re.I,
    )
    if m2:
        meta["person"] = m2.group(1).strip().title()
    if not meta.get("person"):
        # "Madhu is my sister" → person=Madhu, relation=sister
        m3 = re.search(
            r"\b([A-Z][a-z]+(?:\s+[A-Z])?)\s+is\s+my\s+"
            r"(girlfriend|gf|partner|sister|brother|wife|husband|boyfriend)\b",
            text,
            re.I,
        )
        if m3:
            meta["person"] = m3.group(1).strip().title()
            rel = m3.group(2).lower()
            if rel == "gf":
                rel = "girlfriend"
            meta["relation"] = rel
        else:
            for name in extract_person_names(text):
                meta["person"] = name
                break
    return meta


def normalize_user_education_statement(text: str) -> Optional[tuple[str, Dict[str, object]]]:
    """Normalize first-person study statements (not partner/family education)."""
    low = (text or "").lower()
    if not re.search(r"\bi\s+study", low):
        return None
    if re.search(
        r"\bmy\s+(?:girlfriend|gf|partner|boyfriend|wife|husband|sister|brother|dad|father|mom|mother)\b",
        low,
    ):
        return None

    field: Optional[str] = None
    m_field = re.search(
        r"\b(?:as\s+a|as\s+an)\s+([a-z][a-z\s]+?)(?:\s+student)?(?:\s*\.|$)",
        low,
    )
    if m_field:
        field = m_field.group(1).strip().title()

    org = _extract_user_school(text, low)
    parts = ["I study"]
    if field:
        parts.append(field)
    if org:
        parts.append(f"at {org}")
    statement = " ".join(parts).strip()
    if statement == "I study":
        statement = text.strip().rstrip(".")
    elif not statement.endswith("."):
        statement += "."

    extra: Dict[str, object] = {}
    if org:
        extra["organization"] = org
    if field:
        extra["field_of_study"] = field
    return statement, extra


def _extract_user_school(text: str, low: str) -> Optional[str]:
    m = re.search(
        r"\b(university\s+at\s+city\s+a|university\s+in\s+city\s+a|north\s+city\s+college)\b",
        low,
    )
    if m:
        return m.group(1).title()

    m = re.search(
        r"\bi\s+study\s+(?:at|in)\s+(?:the\s+)?(?:university\s+at\s+)?(.+?)"
        r"(?:\s+as\s+a|\s+as\s+an|\s*$|\.)",
        low,
    )
    if m:
        raw = m.group(1).strip().rstrip(".")
        if raw and "computer science" not in raw.lower():
            return raw.title()

    m = re.search(
        r"\b([A-Z][A-Za-z0-9\s']*(?:University|College|School))\b",
        text,
    )
    if m:
        return m.group(1).strip().rstrip(".")
    return None


def _extract_organization(text: str) -> Optional[str]:
    m = re.search(
        r"\b(?:at|from)\s+([A-Z][A-Za-z0-9\s']+(?:University|College|School|Clinic|Hospital))",
        text,
    )
    if m:
        return m.group(1).strip().rstrip(".")
    m2 = re.search(
        r"\b([A-Z][A-Za-z]+\s+(?:University|College|School))\b",
        text,
    )
    if m2:
        return m2.group(1).strip().rstrip(".")
    return None


def _looks_like_current_location_phrase(text: str, low: str) -> bool:
    """True for temporary presence such as 'I am in City B' (not stable home)."""
    if is_meta_or_deferred_location_phrase(text):
        return False
    if re.search(r"\bi\s+live\s+in\b", low):
        return False
    if re.search(r"\bi\s+moved\s+to\b", low) and not re.search(
        r"\b(?:right\s+now|currently|at\s+the\s+moment|for\s+(?:the\s+)?(?:summer|winter|holidays))\b",
        low,
    ):
        return False
    if has_owner_presence_anchor(text):
        return True
    if re.search(
        r"\b(?:i'?m|i am)\s+(?:visiting|staying\s+in|at)\s+[A-Za-z]",
        text,
        re.I,
    ):
        return True
    return False


def _extract_current_location(text: str, low: str) -> Optional[str]:
    """Temporary/current location — not stable home."""
    if not _looks_like_current_location_phrase(text, low):
        return None

    end = r"(?:\s+right\s+now)?(?:\s+for\b[^.!?]*)?(?:[!.,]|$)"
    patterns = (
        rf"\b(?:i'?m|im|i am)\s+in\s+([A-Za-z][\w\s'-]{{1,60}}?){end}",
        rf"\b(?:i'?m|im|i am)\s+currently\s+in\s+([A-Za-z][\w\s'-]{{1,60}}?){end}",
        rf"\b(?:i'?m|im|i am)\s+(?:visiting|staying\s+in|at)\s+([A-Za-z][\w\s'-]{{1,60}}?){end}",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            place = normalize_declared_place(_normalize_place_label(m.group(1)))
            if place and "live in" not in place.lower():
                return place
    return None


def _normalize_place_label(place: str) -> str:
    cleaned = place.strip().rstrip(".!? ")
    return re.sub(
        r"\s+(?:right\s+now|currently|at\s+the\s+moment)\s*$",
        "",
        cleaned,
        flags=re.I,
    ).strip()
