"""Household / family facts: store, update, and answer before web research."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from core.quiet import debug

RELATIVES = frozenset(
    {
        "sister",
        "brother",
        "mother",
        "father",
        "son",
        "daughter",
        "wife",
        "husband",
        "parent",
        "cousin",
        "aunt",
        "uncle",
    }
)

_REL_ALT = "|".join(RELATIVES)

_FAMILY_QUERY_RE = re.compile(
    r"(?:"
    r"who\s+is\s+my\s+(?P<rel>" + _REL_ALT + r")"
    r"|whos\s+my\s+(?P<rel2>" + _REL_ALT + r")"
    r"|what(?:'s|s|\s+is)\s+my\s+(?P<rel3>" + _REL_ALT + r")(?:'s)?\s+name"
    r"|my\s+(?P<rel4>" + _REL_ALT + r")(?:'s)?\s+name"
    r"|where\s+does\s+my\s+(?P<rel5>" + _REL_ALT + r")\s+study"
    r"|what\s+does\s+my\s+(?P<rel6>" + _REL_ALT + r")\s+study"
    r"|tell\s+me\s+about\s+my\s+(?P<rel7>" + _REL_ALT + r")"
    r"|(?:do\s+(?:you|u)\s+)?know(?:\s+about)?\s+my\s+(?P<rel8>" + _REL_ALT + r")"
    r"|(?:do\s+(?:you|u)\s+)?know(?:\s+about)?\s+(?:anything\s+)?(?:about\s+)?my\s+(?P<rel9>" + _REL_ALT + r")"
    r")",
    re.IGNORECASE,
)

_KNOW_RELATIVE_RE = re.compile(
    rf"(?:do\s+(?:you|u)\s+)?know(?:\s+about)?\s+"
    rf"(?:(?:anything\s+)?(?:about\s+)?my|(?P<person>[A-Za-z][a-z']+)(?:'s)?)\s+"
    rf"(?P<rel>{_REL_ALT})\s*\??",
    re.IGNORECASE,
)

_POSSESSIVE_NOT_NAMES = frozenset({"my", "me", "our", "his", "her", "their", "your"})

_THIRD_PARTY_REL_RE = re.compile(
    rf"(?:^|\b)(?:who\s+is|(?:do\s+(?:you|u)\s+)?know(?:\s+about)?)\s+"
    rf"(?P<person>(?!my\b|me\b|our\b|his\b|her\b|their\b|your\b)"
    rf"[A-Za-z][a-z']+)(?:'s)?\s+(?P<rel>{_REL_ALT})\s*\??",
    re.IGNORECASE,
)

_WHO_IS_PERSON_RE = re.compile(
    r"^\s*who\s+is\s+(.+?)\s*\??\s*$",
    re.IGNORECASE,
)

_MY_RELATIVE_IS_RE = re.compile(
    r"(?P<name>[A-Za-z][a-zA-Z]+)\s+is\s+my\s+(?P<rel>" + "|".join(RELATIVES) + r")\b",
    re.IGNORECASE,
)
_MY_RELATIVE_IS_ALT_RE = re.compile(
    r"my\s+(?P<rel>" + "|".join(RELATIVES) + r")\s+is\s+(?P<name>[A-Za-z][a-zA-Z]+)\b",
    re.IGNORECASE,
)
_STUDYING_RE = re.compile(
    r"(?:studying|studing|studies|study(?:ing)?)\s+"
    r"(?P<field>[A-Za-z]{1,20})\b\s+(?:at|in)\s+"
    r"(?P<detail>[A-Za-z][\w\s,]+?)"
    r"(?:\s+remember\b|\s+okay\b|[.,]|$)",
    re.IGNORECASE,
)
_FULL_NAME_RE = re.compile(
    r"(?:her|his|their)\s+full\s+name\s+is\s+(?P<name>[A-Za-z][a-zA-Z]+)",
    re.IGNORECASE,
)
_ALIAS_RE = re.compile(
    r"(?:also\s+)?(?:called|known\s+as)\s+(?P<name>[A-Za-z][a-zA-Z]+)",
    re.IGNORECASE,
)
_REMEMBER_HINT_RE = re.compile(r"\bremember\b", re.IGNORECASE)

FACT_KEY_PREFIX = "family:"


def _fact_key(relation: str) -> str:
    return f"{FACT_KEY_PREFIX}{relation.lower()}"


def is_family_question(text: str) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    if _FAMILY_QUERY_RE.search(raw):
        return True
    return False


def is_household_memory_query(text: str) -> bool:
    """Any phrasing that should use family records, not web search / generic LLM."""
    raw = (text or "").strip()
    if not raw:
        return False
    if is_family_question(raw):
        return True
    if _KNOW_RELATIVE_RE.search(raw):
        return True
    if _THIRD_PARTY_REL_RE.search(raw):
        return True
    if looks_like_who_is_about_relative(raw.lower()):
        return True
    return False


def looks_like_who_is_about_relative(text: str) -> bool:
    """If true, DuckDuckGo 'who is' must not steal the query."""
    raw = (text or "").strip().lower()
    return bool(re.search(rf"\bwho\s+is\b.+\b({_REL_ALT})\b", raw))


def _similar_names(a: str, b: str, threshold: float = 0.82) -> bool:
    aa = (a or "").strip().lower()
    bb = (b or "").strip().lower()
    if not aa or not bb:
        return False
    if aa == bb:
        return True
    if len(aa) >= 5 and len(bb) >= 5 and (aa in bb or bb in aa):
        return True
    return SequenceMatcher(None, aa, bb).ratio() >= threshold


def _similar_tokens_to_record(query_text: str, stored_name: str) -> bool:
    """Compare user-entered remainder to a stored relative name."""
    qt = (query_text or "").strip()
    sn = (stored_name or "").strip()
    if not qt or not sn:
        return False
    if _similar_names(qt, sn, threshold=0.78):
        return True
    first_stored = sn.split()[0] if sn.split() else sn
    for word in qt.split():
        wl = word.strip("?.!'\"")
        if len(wl) < 3:
            continue
        if _similar_names(wl, sn, threshold=0.78) or _similar_names(
            wl, first_stored, threshold=0.78
        ):
            return True
    return False


def _parse_third_party_relative(text: str) -> Optional[tuple[str, str]]:
    """Return (person_token, relation) for 'who is / do u know Alex's sister'."""
    raw = (text or "").strip()
    m = _THIRD_PARTY_REL_RE.search(raw)
    if not m:
        return None
    person = (m.group("person") or "").strip().replace("'", "")
    relation = (m.group("rel") or "").strip().lower()
    if not person or not relation or person.lower() in _POSSESSIVE_NOT_NAMES:
        return None
    return person, relation


def _names_match_subject(subject: str, primary_user: Optional[str]) -> bool:
    """Does the named subject refer to the household primary user?"""
    sub = subject.strip().title()
    if not primary_user or primary_user.strip().lower() in ("user", "unknown"):
        return True
    return _similar_names(sub, primary_user, threshold=0.86)


def _format_third_party_answer(
    person_display: str,
    relation: str,
    rec: Dict[str, Any],
    *,
    primary_user: Optional[str] = None,
) -> str:
    name = rec.get("full_name") or rec.get("name")
    if not name:
        return (
            f"I don't have {person_display}'s {relation} saved yet. "
            f'Tell me: "My {relation} is <name>."'
        )
    lines = [f"{person_display}'s {relation} is {name}."]
    if rec.get("studies"):
        lines.append(f" She studies {rec['studies']}.")
    if rec.get("school"):
        lines.append(f" School: {rec['school']}.")
    if primary_user and _names_match_subject(person_display, primary_user):
        lines.append(f" (Same as your {relation} in household memory.)")
    return "".join(lines)


def answer_third_party_relative_question(
    text: str,
    memory,
    neural_bridge=None,
    primary_user: Optional[str] = None,
) -> Optional[str]:
    """
    Handles 'who is Alex sister', 'do u know Alex's sister', etc.
    Uses stored family:* records when we have them, even if profile primary name differs.
    """
    parsed = _parse_third_party_relative(text)
    if not parsed:
        return None
    person_token, relation = parsed
    person_display = person_token.strip().title()
    pu_display = (
        primary_user.strip().title()
        if primary_user and primary_user.strip().lower() not in ("user", "unknown")
        else person_display
    )

    rec = get_family_record(memory, relation)
    has_record = bool(rec.get("name") or rec.get("full_name"))

    if has_record:
        if _names_match_subject(person_token, primary_user):
            return _format_third_party_answer(
                pu_display, relation, rec, primary_user=primary_user
            )
        # Stored household relative — answer for the named person's relative.
        return _format_third_party_answer(person_display, relation, rec)

    if not _names_match_subject(person_token, primary_user):
        debug(
            "[FAMILY] third-party skip: no record and '%s' != primary %r",
            person_token,
            primary_user,
        )
        return None

    if neural_bridge:
        try:
            if neural_bridge.init_neural_memory():
                from core.neural_memory import search

                for node in search(f"{relation} {person_token}", limit=15):
                    blob = f"{node.name} {(node.content or '')}".lower()
                    if relation in blob:
                        return (
                            f"From memory ({pu_display}'s {relation}): {node.name}"
                            + (f" — {node.content[:200]}" if node.content else "")
                        )
        except Exception:
            pass
    return None


def answer_know_relative_question(
    text: str,
    memory,
    neural_bridge=None,
    primary_user: Optional[str] = None,
) -> Optional[str]:
    """'Do you know my sister?' / 'do u know Alex's sister' — household memory, not LLM."""
    raw = (text or "").strip()
    m = _KNOW_RELATIVE_RE.search(raw)
    if not m:
        return None
    person = (m.group("person") or "").strip().replace("'", "")
    relation = (m.group("rel") or "").strip().lower()
    if person:
        return answer_third_party_relative_question(
            raw, memory, neural_bridge, primary_user
        )
    ans = answer_family_question(f"who is my {relation}?", memory, neural_bridge)
    if ans and not ans.startswith("I don't have"):
        if ans.startswith("Your "):
            return "Yes. " + ans
        return f"Yes. {ans}"
    return ans


def answer_who_is_known_contact(
    text: str,
    memory,
    neural_bridge=None,
) -> Optional[str]:
    """
    Bare 'who is Maya?' when that name fuzzy-matches sister/cousin in family records or neural FTS.
    """
    raw = (text or "").strip()
    if not raw.lower().startswith("who is "):
        return None

    remainder = raw[7:].strip().rstrip("?.!").strip()
    if len(remainder) < 3 or len(remainder.split()) > 5:
        return None

    tokens = remainder

    matched_rel: Optional[str] = None
    matched_rec: Optional[Dict[str, Any]] = None

    for rel in RELATIVES:
        rec = get_family_record(memory, rel)
        if not rec:
            continue
        for key in ("name", "full_name", "alias"):
            cand = rec.get(key)
            if isinstance(cand, str):
                cand_stripped = cand.strip()
                # Whole-name match or token match against already-saved relatives.
                if _similar_tokens_to_record(tokens, cand_stripped):
                    matched_rel = rel
                    matched_rec = rec
                    break
        if matched_rel:
            break

    if matched_rel and matched_rec:
        return _format_record_answer(matched_rel, matched_rec, "who")

    # Neural FTS — only for compact queries (avoid hijacking celebrities)
    if neural_bridge and len(tokens.split()) <= 2:
        try:
            if neural_bridge.init_neural_memory():
                from core.neural_memory import search

                for node in search(tokens, limit=12):
                    nm = node.name or ""
                    if _similar_tokens_to_record(tokens, nm) or _similar_names(
                        tokens.split()[0] if tokens.split() else "",
                        nm.split()[0] if nm.split() else "",
                        threshold=0.78,
                    ):
                        return (
                            f"From saved memory — {node.name}"
                            + (f": {(node.content or '')[:220]}" if node.content else "")
                        )
        except Exception:
            pass

    return None


def answer_household_memory(
    text: str,
    memory,
    neural_bridge=None,
    primary_user: Optional[str] = None,
) -> Optional[str]:
    """
    Full household path before research / general LLM: my sister, their sister by name,
    and fuzzy 'who is Firstname?' for relatives we already saved.
    """
    ans = answer_family_question(text, memory, neural_bridge)
    if ans:
        if _KNOW_RELATIVE_RE.search((text or "").strip()) and not _parse_third_party_relative(text):
            if ans.startswith("Your "):
                return "Yes. " + ans
            if not ans.lower().startswith("yes"):
                return f"Yes. {ans}"
        return ans
    ans = answer_know_relative_question(text, memory, neural_bridge, primary_user)
    if ans:
        return ans
    ans = answer_third_party_relative_question(text, memory, neural_bridge, primary_user)
    if ans:
        return ans

    lowered = (text or "").strip().lower()
    if lowered.startswith("who is ") and len(text.split()) <= 12:
        return answer_who_is_known_contact(text, memory, neural_bridge)
    return None


def _parse_relation_from_query(text: str) -> Optional[str]:
    m = _FAMILY_QUERY_RE.search((text or "").strip().lower())
    if not m:
        m2 = re.search(r"\bmy\s+(sister|brother|mother|father)\b", text.lower())
        return m2.group(1).lower() if m2 else None
    for g in m.groups():
        if g:
            return g.lower()
    return "sister"


def get_family_record(memory, relation: str) -> Dict[str, Any]:
    key = _fact_key(relation)
    raw = memory.get_fact(key)
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def save_family_record(memory, relation: str, record: Dict[str, Any]) -> None:
    memory.store_fact(_fact_key(relation), record)


def ingest_family_statement(
    text: str,
    memory,
    neural_bridge=None,
    *,
    last_relation: Optional[str] = None,
) -> Optional[str]:
    """
    Parse and store family facts from user text.
    Returns relation updated, if any.
    """
    if not (text or "").strip():
        return None

    relation_updated: Optional[str] = None
    raw = text.strip()
    lower = raw.lower()
    if is_family_question(raw) or is_household_memory_query(raw):
        return None

    for pattern in (_MY_RELATIVE_IS_RE, _MY_RELATIVE_IS_ALT_RE):
        for match in pattern.finditer(raw):
            if pattern is _MY_RELATIVE_IS_RE:
                name = match.group("name").strip().title()
                rel = match.group("rel").lower()
            else:
                rel = match.group("rel").lower()
                name = match.group("name").strip().title()
            rec = get_family_record(memory, rel)
            rec["name"] = name
            rec["relation"] = rel
            if not rec.get("full_name"):
                rec["full_name"] = name
            save_family_record(memory, rel, rec)
            relation_updated = rel

    rel_for_pronoun = last_relation or relation_updated
    if rel_for_pronoun:
        m = _FULL_NAME_RE.search(raw)
        if m:
            full = m.group("name").strip().title()
            rec = get_family_record(memory, rel_for_pronoun)
            if rec.get("name"):
                rec["full_name"] = full
                rec["alias"] = rec.get("name")
                save_family_record(memory, rel_for_pronoun, rec)
                relation_updated = rel_for_pronoun

    if relation_updated or last_relation:
        rel = relation_updated or last_relation
        m = _STUDYING_RE.search(raw)
        if m:
            rec = get_family_record(memory, rel)
            field = (m.group("field") or "").strip()
            detail = (m.group("detail") or "").strip().rstrip(".,; ")
            if field:
                rec["studies"] = field.upper() if len(field) <= 4 else field.title()
            if detail:
                rec["school"] = detail.title()
                rec["location"] = detail.title()
            save_family_record(memory, rel, rec)
            relation_updated = rel

    if _REMEMBER_HINT_RE.search(raw) and relation_updated is None:
        for rel in RELATIVES:
            if f"my {rel}" in lower:
                relation_updated = rel
                break

    if neural_bridge and (_REMEMBER_HINT_RE.search(raw) or relation_updated):
        try:
            if neural_bridge.init_neural_memory():
                neural_bridge.learn_from_text(raw)
        except Exception:
            pass

    return relation_updated


def _format_record_answer(relation: str, rec: Dict[str, Any], query: str) -> str:
    name = rec.get("full_name") or rec.get("name")
    if not name:
        return (
            f"I don't have your {relation}'s name stored yet. "
            f"Tell me something like: \"My {relation} is <name>.\""
        )

    q = query.lower()
    if "name" in q:
        parts = [f"Your {relation}'s name is {name}."]
        if rec.get("alias") and rec["alias"].lower() != name.lower():
            parts.append(f" You also call her {rec['alias']}.")
        return "".join(parts)

    if "where" in q and "study" in q:
        place = rec.get("school_display") or rec.get("school") or rec.get("location")
        if place:
            return f"Your {relation} {name} studies at {place}."
        return f"I know your {relation} is {name}, but not where she studies yet."

    if "what" in q and "study" in q:
        if rec.get("studies"):
            return f"Your {relation} {name} is studying {rec['studies']}."
        return f"I know your {relation} is {name}, but not what she studies yet."

    # General "who is my sister?"
    lines = [f"Your {relation} is {name}."]
    if rec.get("studies"):
        lines.append(f" She studies {rec['studies']}.")
    place = rec.get("school_display") or rec.get("school")
    if place:
        loc = ""
        if (
            rec.get("location")
            and str(rec["location"]).lower() not in str(place).lower()
        ):
            loc = f" in {rec['location']}"
        lines.append(f" School: {place}{loc}.")
    return "".join(lines)


def answer_family_question(
    text: str,
    memory,
    neural_bridge=None,
) -> Optional[str]:
    """Answer from local family records + neural FTS; never web search."""
    if not is_family_question(text):
        return None

    relation = _parse_relation_from_query(text) or "sister"
    rec = get_family_record(memory, relation)

    if rec.get("name") or rec.get("full_name"):
        return _format_record_answer(relation, rec, text)

    if neural_bridge:
        try:
            if neural_bridge.init_neural_memory():
                from core.neural_memory import search

                hits = search(f"{relation} {text}", limit=10)
                for node in hits:
                    blob = f"{node.name} {node.content or ''}".lower()
                    if relation in blob or (rec.get("name") and rec["name"].lower() in blob):
                        return (
                            f"From memory: {node.name}"
                            + (f" — {node.content[:200]}" if node.content else "")
                        )
        except Exception:
            pass

    results = memory.search_conversations(relation)
    for conv in reversed(results):
        u = conv.get("user", "")
        if relation in u.lower() and re.search(r"\b(is|studying|studies)\b", u.lower()):
            ingest_family_statement(u, memory, neural_bridge)
            rec = get_family_record(memory, relation)
            if rec.get("name"):
                return _format_record_answer(relation, rec, text)

    return (
        f"I don't have your {relation} saved yet. "
        f"You can tell me: \"My {relation} is <name> and she studies ...\"."
    )


def format_family_memory_confirmation(memory, relation: str) -> str:
    """Return a direct confirmation for a stored family update."""
    rec = get_family_record(memory, relation)
    name = rec.get("full_name") or rec.get("name")
    if name:
        return f"Got it. I'll remember your {relation} {name}."
    return f"Got it. I'll remember that family detail."
