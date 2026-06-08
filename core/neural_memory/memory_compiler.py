"""Entity and relationship extraction from text."""

import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from .config import config
from .extraction_filters import (
    filter_entity_triples,
    filter_preference_subject,
    is_allowed_entity_name,
)
from .models import MemoryEdge, MemoryNode, NodeType, EdgeType
from .storage import storage

logger = logging.getLogger(__name__)

_SELF_INTRO_RE = re.compile(
    r"(?:\bi am\b|\bi'm\b|\bmy name is\b|\bcall me\b)\s+"
    r"([A-Za-z][a-zA-Z]+(?:\s+(?!and\b|but\b|official\b|offical\b|legal\b|real\b|name\b)[A-Za-z][a-zA-Z]+)?)",
    re.IGNORECASE,
)
_RELATION_OF_RE = re.compile(
    r"([A-Za-z][a-zA-Z]+)'s\s+"
    r"(sister|brother|mother|mom|father|dad|son|daughter|wife|husband|parent|cousin|aunt|uncle)",
    re.IGNORECASE,
)
_SELF_RELATION_RE = re.compile(
    r"(?:\bi am\b|\bi'm\b)\s+([A-Za-z][a-zA-Z]+)[,\s]+"
    r"([A-Za-z][a-zA-Z]+)'s\s+"
    r"(sister|brother|mother|mom|father|dad|son|daughter|wife|husband|parent|cousin)",
    re.IGNORECASE,
)
_REMEMBER_TAIL_RE = re.compile(
    r"(?:remember|don't forget|note that|save this|save that)"
    r"(?:\s+this|\s+that)?[.:]?\s*(.*)$",
    re.IGNORECASE,
)
_MY_RELATIVE_IS_RE = re.compile(
    r"(?P<name>[A-Za-z][a-zA-Z]+)\s+is\s+my\s+"
    r"(sister|brother|mother|mom|father|dad|son|daughter|wife|husband|parent|cousin)\b",
    re.IGNORECASE,
)
_MY_RELATIVE_IS_ALT_RE = re.compile(
    r"my\s+(?P<rel>sister|brother|mother|mom|father|dad|son|daughter|wife|husband|parent|cousin)"
    r"\s+is\s+(?P<name>[A-Za-z][a-zA-Z]+)\b",
    re.IGNORECASE,
)
_MY_RELATIVE_NAME_RE = re.compile(
    r"my\s+(?P<rel>dad|father|mom|mother|sister|brother|girlfriend|gf|partner)"
    r"(?:'s|s)?\s+name\s+is\s+"
    r"(?P<name>[A-Za-z][a-zA-Z]+(?:\s+(?!and\b|he\b|she\b|they\b|who\b|lives?\b)[A-Za-z][a-zA-Z]+)?)\b",
    re.IGNORECASE,
)
_FULL_NAME_RE = re.compile(
    r"(?:her|his|their)\s+full\s+name\s+is\s+(?P<name>[A-Za-z][a-zA-Z]+)",
    re.IGNORECASE,
)
_STUDYING_RE = re.compile(
    r"(?:studying|studing|studies|study(?:ing)?)\s+"
    r"(?P<field>[A-Za-z]{1,20})\b\s+(?:at|in)\s+(?P<detail>[A-Za-z][\w\s,]+)",
    re.IGNORECASE,
)

_LIVE_IN_RE = re.compile(
    r"\bi\s+live\s+in\s+(?P<loc>[A-Za-z][\w\s]+?)(?:\s+because|\s+and\s+i|\s*,|\s+for\s+|\s*$)",
    re.IGNORECASE,
)
_CURRENT_IN_RE = re.compile(
    r"\b(?:i\s+am|i'm|im)\s+in\s+(?P<loc>[A-Za-z][\w\s]+?)"
    r"(?:\s+for\s+(?P<ctx>[A-Za-z][\w\s]+?))?(?:\s+and|,|\.|$)",
    re.IGNORECASE,
)
_USER_STUDY_RE = re.compile(
    r"\b(?:i\s+am\s+a\s+)?(?:\w+\s+)?student\s+at\s+(?P<school>[A-Za-z][\w\s]+?)(?:\s+and|,|\.|$)",
    re.IGNORECASE,
)
_USER_STUDY_BECAUSE_RE = re.compile(
    r"\bbecause\s+(?:i\s+)?(?:study|studying)\s+(?:at|in)\s+(?P<school>[A-Za-z][\w\s]+?)(?:\s+and|,|\.|$)",
    re.IGNORECASE,
)
_MY_NAME_RE = re.compile(
    r"\b(?:my\s+name\s+is|call\s+me)\s+"
    r"(?P<name>[A-Za-z][a-zA-Z]+(?:\s+(?!and\b|but\b|official\b|offical\b|legal\b|real\b|name\b)[A-Za-z][a-zA-Z]+)?)\b",
    re.IGNORECASE,
)
_OFFICIAL_NAME_RE = re.compile(
    r"\b(?:official|offical|legal|real)\s+name\s+is\s+"
    r"(?P<name>[A-Za-z][a-zA-Z]+(?:\s+(?!and\b|but\b|he\b|she\b|they\b|lives?\b|stud(?:y|ies|ying)\b)[A-Za-z][a-zA-Z]+){0,3})\b",
    re.IGNORECASE,
)
_PARENT_NAME_LIVES_RE = re.compile(
    r"\bmy\s+(?P<rel>dad'?s?|father'?s?|mom'?s?|mother'?s?)\s+name\s+is\s+"
    r"(?P<name>[A-Za-z][a-zA-Z]+(?:\s+(?!and\b|he\b|she\b|who\b|lives?\b)[A-Za-z][a-zA-Z]+)?)"
    r"(?:\s+and\s+(?:he|she)\s+lives?\s+in\s+(?P<loc>[A-Za-z][\w\s]+?))?"
    r"(?:\s+and\s+(?:he|she)\s+lives?\s+with\s+my\s+dad)?"
    r"(?:\s+and|,|\.|$)",
    re.IGNORECASE,
)
_PARTNER_IS_RE = re.compile(
    r"(?P<name>[A-Za-z][a-zA-Z]+(?:\s+[A-Za-z][a-zA-Z]+)?)\s+is\s+my\s+"
    r"(?:gf|girlfriend|girl\s+friend|partner|boyfriend|boy\s+friend)\b",
    re.IGNORECASE,
)
_BAD_PERSON_ENTITY_NAMES = frozenset(
    {
        "who",
        "whos",
        "who's",
        "what",
        "whats",
        "what's",
        "where",
        "when",
        "why",
        "how",
        "her",
        "she",
        "he",
        "they",
    }
)
_PARTNER_LIVES_RE = re.compile(
    r"(?:she|he|they)\s+live(?:s)?\s+in\s+(?P<loc>[A-Za-z][\w\s]+?)(?:\s+and|,|\.|$)",
    re.IGNORECASE,
)
_PARTNER_STUDIES_RE = re.compile(
    r"(?:she|he|they|gf|girlfriend|partner)\s+stud(?:y|ies|ying)\s+(?:at|in)\s+"
    r"(?P<school>[A-Za-z][\w\s]+?)(?:\s+and|,|\.|$)",
    re.IGNORECASE,
)
_RELATION_NORM = {
    "dad": "father",
    "dads": "father",
    "father": "father",
    "fathers": "father",
    "mom": "mother",
    "moms": "mother",
    "mother": "mother",
    "mothers": "mother",
}

_NAME_STOP = frozenset(
    {
        "and",
        "but",
        "he",
        "she",
        "they",
        "his",
        "her",
        "who",
        "lives",
        "live",
        "living",
        "with",
        "studies",
        "study",
        "studying",
        "studing",
        "also",
        "in",
        "at",
        "im",
        "i'm",
        "here",
        "them",
        "now",
        "got",
        "it",
        "for",
        "on",
        "the",
        "a",
        "my",
        "me",
        "official",
        "offical",
        "legal",
        "real",
        "name",
    }
)

_PLACE_STOP = _NAME_STOP | frozenset(
    {
        "because",
        "summer",
        "vacation",
        "vaction",
        "holidays",
        "holiday",
        "break",
        "remember",
        "okay",
        "flight",
        "return",
        "reach",
        "arrive",
        "started",
        "start",
        "flew",
        "fly",
        "flying",
        "came",
        "come",
        "moved",
        "back",
        "morning",
        "night",
        "evening",
        "local",
        "time",
        "car",
        "to",
        "from",
    }
)


def _clean_person_name(raw: str) -> str:
    parts: list[str] = []
    for token in (raw or "").split():
        wl = token.lower().strip(".,;:!?'\"")
        if wl in _NAME_STOP or wl in _RELATION_NORM:
            break
        if len(wl) < 2:
            continue
        parts.append(token.strip(".,;:!?'\""))
    return " ".join(parts).title() if parts else ""


def _clean_place(raw: str) -> str:
    parts: list[str] = []
    for token in (raw or "").split():
        wl = token.lower().strip(".,;:!?'\"")
        if wl in _PLACE_STOP or len(parts) >= 4:
            break
        if len(wl) < 2:
            continue
        parts.append(token.strip(".,;:!?'\""))
    return " ".join(parts).title() if parts else ""


def _clean_time_context(raw: str) -> str:
    parts: list[str] = []
    stop = _PLACE_STOP - frozenset(
        {
            "break",
            "vacation",
            "vaction",
            "holiday",
            "holidays",
            "summer",
            "winter",
            "spring",
            "fall",
        }
    )
    for token in (raw or "").split():
        wl = token.lower().strip(".,;:!?'\"")
        if wl in stop or len(parts) >= 6:
            break
        if len(wl) < 2:
            continue
        parts.append(token.strip(".,;:!?'\""))
    return " ".join(parts).lower() if parts else ""


_PARENT_NAME_RE = re.compile(
    r"\bmy\s+(?P<rel>dad'?s?|father'?s?|mom'?s?|mother'?s?)\s+name\s+is\s+"
    r"(?P<name>[A-Za-z][a-zA-Z]+)",
    re.IGNORECASE,
)
_CITY_TOKEN = r"[A-Za-z][a-zA-Z]+(?:\s+[A-Za-z][a-zA-Z]+)?"
_PARENT_LIVES_RE = re.compile(
    rf"(?:and\s+)?(?:he|she|they)\s+(?:also\s+)?live(?:s)?\s+in\s+"
    rf"(?P<loc>{_CITY_TOKEN})",
    re.IGNORECASE,
)
_HERE_WITH_THEM_RE = re.compile(
    r"\b(?:i'm|im)\s+here\s+with\s+(?:them|my\s+(?:dad|parents|family))\s*(?:now)?",
    re.IGNORECASE,
)
_SISTER_FULL_NAME_RE = re.compile(
    r"\bmy\s+sisters?\s+full\s+name\s+(?:is\s+)?(?P<name>[A-Za-z][a-zA-Z]+(?:\s+[A-Za-z][a-zA-Z]+)?)",
    re.IGNORECASE,
)
_MY_RELATION_NAME_LOOSE_RE = re.compile(
    r"\bmy\s+(?P<rel>sister|brother|mom|mother|dad|father)\b"
    r"(?:\s+(?:her|his|their))?\s+name\s+is\s+"
    r"(?P<name>[A-Za-z][a-zA-Z]+(?:\s+(?!she\b|he\b|they\b|stud(?:y|ies|ying|ing)\b|live(?:s)?\b|and\b)[A-Za-z][a-zA-Z]+)?)",
    re.IGNORECASE,
)
_HER_FULL_NAME_SLOT_RE = re.compile(
    r"\b(?:her|his|their)\s+full\s+name\s+is\s+(?P<name>[A-Za-z][a-zA-Z]+)",
    re.IGNORECASE,
)
_LOCATION_CORRECTION_RE = re.compile(
    r"\b(?:no\s+)?(?:it'?s|it\s+is)\s+(?:just\s+)?(?:called\s+)?"
    r"(?P<loc>[A-Za-z][a-zA-Z]+(?:\s+[A-Za-z][a-zA-Z]+)?)\b",
    re.IGNORECASE,
)
_RETURN_FLIGHT_RE = re.compile(
    r"(?:return\s+)?flight\s+(?:on\s+)?"
    r"(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)?\s*"
    r"(?P<dep>\d{1,2})(?:st|nd|rd|th)?\s*(?P<dep_time>early\s+morning|morning|evening|night)?",
    re.IGNORECASE,
)
_RETURN_ARRIVE_RE = re.compile(
    r"(?:reach|arrive|arrival)\s+(?:in\s+|at\s+)?(?P<dest>[A-Za-z][\w\s]+?)\s+on\s+"
    r"(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)?\s*"
    r"(?P<arr>\d{1,2})(?:st|nd|rd|th)?\s*(?P<arr_time>early\s+morning|morning|evening|night)?"
    r"(?:\s+local\s+time)?",
    re.IGNORECASE,
)


def _month_label(month: Optional[str], day: str) -> str:
    if month:
        return f"{month.title()} {day}"
    return day


class MemoryCompiler:
    PROJECT_PATTERNS = [
        r"(?:working on|project|codebase|repo)[:\s]+([A-Za-z0-9_\-./]+)",
        r"\b([a-z]+-[a-z]+-\d+)\b",  # JIRA-style: s26-fixflow
        r"\b([A-Za-z0-9_]+/[A-Za-z0-9_]+)\b",  # GitHub style: user/repo
    ]

    SKILL_KEYWORDS = {
        "python": NodeType.SKILL.value,
        "javascript": NodeType.SKILL.value,
        "typescript": NodeType.SKILL.value,
        "docker": NodeType.TOOL.value,
        "git": NodeType.TOOL.value,
        "vim": NodeType.TOOL.value,
        "tmux": NodeType.TOOL.value,
        "fastapi": NodeType.TOOL.value,
        "react": NodeType.TOOL.value,
        "sql": NodeType.SKILL.value,
    }

    def __init__(self):
        self.storage = storage

    def compile(
        self, user_message: str, assistant_message: str, metadata: dict = None
    ) -> Tuple[List[MemoryNode], List[MemoryEdge]]:
        metadata = metadata or {}
        user_id = metadata.get("user_id") or config.user_id

        raw_entities: List[Tuple[str, str, str]] = []
        text = f"{user_message or ''} {assistant_message or ''}"
        text_lower = text.lower()

        for project_match in re.finditer(self.PROJECT_PATTERNS[0], text, re.IGNORECASE):
            raw_entities.append(
                (
                    NodeType.PROJECT.value,
                    project_match.group(1).strip(),
                    "Mentioned as project/repo context",
                )
            )

        for jira_match in re.finditer(self.PROJECT_PATTERNS[1], text):
            raw_entities.append(
                (
                    NodeType.PROJECT.value,
                    jira_match.group(1).strip(),
                    "JIRA-style project id",
                )
            )

        for gh_match in re.finditer(self.PROJECT_PATTERNS[2], text):
            raw_entities.append(
                (
                    NodeType.PROJECT.value,
                    gh_match.group(1).strip(),
                    "GitHub-style slug",
                )
            )

        for keyword, node_type in self.SKILL_KEYWORDS.items():
            if keyword in text_lower:
                raw_entities.append(
                    (node_type, keyword.title(), "Mentioned in conversation")
                )

        filtered = filter_entity_triples(raw_entities)
        nodes: List[MemoryNode] = []
        for node_type, name, content in filtered:
            nodes.append(
                MemoryNode(
                    node_type=node_type,
                    name=name,
                    content=content,
                    salience=0.55,
                    user_id=user_id,
                )
            )

        return nodes, []

    def extract_relationships(
        self, text: str, user_id: str
    ) -> List[Tuple[str, str, str]]:
        """
        Returns tuples: (object_name, edge_type, context)
        User is implicit (anchor person node == user_id).
        """
        relationships: List[Tuple[str, str, str]] = []
        text_lower = text.lower()

        preference_patterns = [
            (r"prefer[s]?\s+([A-Za-z0-9\s\-_./]+)", EdgeType.PREFERS.value),
            (r"like[s]?\s+([A-Za-z0-9\s\-_./]+)", EdgeType.PREFERS.value),
            (r"hate[s]?\s+([A-Za-z0-9\s\-_./]+)", EdgeType.OPPOSES.value),
        ]

        for pattern, edge_type in preference_patterns:
            for match in re.finditer(pattern, text_lower):
                subject = filter_preference_subject(match.group(1))
                if not subject:
                    continue
                relationships.append(
                    (subject, edge_type, f"Extracted from: {text[:120]}")
                )

        return relationships

    def _append_relation_nodes(
        self,
        nodes: List[MemoryNode],
        user_id: str,
        relation: str,
        person: str,
        meta: dict,
        *,
        display_name: Optional[str] = None,
    ) -> None:
        label = display_name or person
        nodes.append(
            MemoryNode(
                node_type=NodeType.PERSON.value,
                name=label,
                content=f"User's {relation}",
                metadata=dict(meta),
                salience=0.95,
                user_id=user_id,
            )
        )
        loc = meta.get("location")
        school = meta.get("school")
        full = meta.get("full_name") or person
        content = f"User's {relation} is {full}"
        if loc:
            content += f" and lives in {loc}."
        else:
            content += "."
        if school:
            content += f" Studies at {school}."
        nodes.append(
            MemoryNode(
                node_type=NodeType.FACT.value,
                name=f"user:{relation}",
                content=content,
                metadata=dict(meta),
                salience=0.96,
                user_id=user_id,
            )
        )

    def extract_people_and_relationships(
        self, text: str, user_id: str
    ) -> List[MemoryNode]:
        """Extract self-introductions, family links, and explicit remember phrases."""
        if not (text or "").strip():
            return []

        nodes: List[MemoryNode] = []
        seen: set[tuple[str, str]] = set()

        def _add(node: MemoryNode) -> None:
            key = (node.node_type, node.name.strip().lower())
            if key in seen or not is_allowed_entity_name(node.name, min_len=2):
                return
            seen.add(key)
            nodes.append(node)

        for match in _SELF_RELATION_RE.finditer(text):
            person = match.group(1).strip().title()
            owner = match.group(2).strip().title()
            relation = match.group(3).lower()
            _add(
                MemoryNode(
                    node_type=NodeType.PERSON.value,
                    name=person,
                    content=f"{relation} of {owner}",
                    salience=0.88,
                    user_id=user_id,
                )
            )
            _add(
                MemoryNode(
                    node_type=NodeType.PERSON.value,
                    name=owner,
                    content=f"Has {relation} {person}",
                    salience=0.72,
                    user_id=user_id,
                )
            )
            _add(
                MemoryNode(
                    node_type=NodeType.FACT.value,
                    name=f"{person} is {owner}'s {relation}",
                    content=text.strip()[:500],
                    salience=0.92,
                    user_id=user_id,
                )
            )

        for match in _SELF_INTRO_RE.finditer(text):
            name = _clean_person_name(match.group(1))
            if not name:
                continue
            _add(
                MemoryNode(
                    node_type=NodeType.PERSON.value,
                    name=name,
                    content="Self-identified in conversation",
                    salience=0.8,
                    user_id=user_id,
                )
            )

        for match in _RELATION_OF_RE.finditer(text):
            owner = match.group(1).strip().title()
            relation = match.group(2).lower()
            if not is_allowed_entity_name(owner, min_len=2):
                continue
            _add(
                MemoryNode(
                    node_type=NodeType.FACT.value,
                    name=f"{owner}'s {relation}",
                    content=text.strip()[:500],
                    salience=0.85,
                    user_id=user_id,
                )
            )

        for match in _REMEMBER_TAIL_RE.finditer(text):
            payload = (match.group(1) or "").strip()
            if not payload:
                payload = text[: match.start()].strip().rstrip(".,;:")
            if len(payload) >= 4:
                _add(
                    MemoryNode(
                        node_type=NodeType.FACT.value,
                        name=payload[:80],
                        content=payload[:500],
                        salience=0.9,
                        user_id=user_id,
                    )
                )

        for pattern in (_MY_RELATIVE_IS_RE, _MY_RELATIVE_IS_ALT_RE):
            for match in pattern.finditer(text):
                if pattern is _MY_RELATIVE_IS_RE:
                    person = match.group("name").strip().title()
                    relation = match.group(2).lower()
                else:
                    relation = match.group("rel").lower()
                    person = match.group("name").strip().title()
                _add(
                    MemoryNode(
                        node_type=NodeType.PERSON.value,
                        name=person,
                        content=f"User's {relation}",
                        salience=0.9,
                        user_id=user_id,
                    )
                )
                _add(
                    MemoryNode(
                        node_type=NodeType.FACT.value,
                        name=f"user's {relation} is {person}",
                        content=text.strip()[:500],
                        salience=0.93,
                        user_id=user_id,
                    )
                )

        for match in _MY_RELATIVE_NAME_RE.finditer(text):
            relation = match.group("rel").lower()
            if relation == "gf":
                relation = "girlfriend"
            person = match.group("name").strip().title()
            _add(
                MemoryNode(
                    node_type=NodeType.PERSON.value,
                    name=person,
                    content=f"User's {relation}",
                    salience=0.92,
                    user_id=user_id,
                )
            )
            _add(
                MemoryNode(
                    node_type=NodeType.FACT.value,
                    name=f"user's {relation} is {person}",
                    content=text.strip()[:500],
                    salience=0.94,
                    user_id=user_id,
                )
            )

        study_match = _STUDYING_RE.search(text)
        if study_match:
            field = (study_match.group("field") or "").strip()
            school = (study_match.group("detail") or "").strip()
            detail = f"studies {field} at {school}".strip()
            _add(
                MemoryNode(
                    node_type=NodeType.FACT.value,
                    name=f"relative studies {field or 'program'}",
                    content=detail[:500],
                    salience=0.88,
                    user_id=user_id,
                )
            )

        full_match = _FULL_NAME_RE.search(text)
        if full_match:
            full = full_match.group("name").strip().title()
            _add(
                MemoryNode(
                    node_type=NodeType.PERSON.value,
                    name=full,
                    alias=None,
                    content="Full name provided for a relative",
                    salience=0.9,
                    user_id=user_id,
                )
            )
            _add(
                MemoryNode(
                    node_type=NodeType.FACT.value,
                    name=f"relative full name {full}",
                    content=text.strip()[:500],
                    salience=0.91,
                    user_id=user_id,
                )
            )

        return nodes

    def extract_structured_personal_facts(
        self, text: str, user_id: str
    ) -> List[MemoryNode]:
        """
        Declarative personal facts with metadata (authority for brain recall).
        Stable node names so newer facts overwrite older slots (recency).
        """
        if not (text or "").strip():
            return []

        nodes: List[MemoryNode] = []
        raw = text.strip()
        lower = raw.lower()

        m = _LIVE_IN_RE.search(raw)
        if m:
            city = _clean_place(m.group("loc"))
            if city:
                nodes.append(
                    MemoryNode(
                        node_type=NodeType.LOCATION.value,
                        name="permanent_home",
                        content=f"User lives in {city}.",
                        metadata={
                            "kind": "permanent_home",
                            "location": city,
                            "source": raw[:300],
                        },
                        salience=0.96,
                        user_id=user_id,
                    )
                )

        m = _CURRENT_IN_RE.search(raw)
        if m:
            city = _clean_place(m.group("loc"))
            ctx = _clean_time_context(m.group("ctx")) if m.group("ctx") else ""
            if city:
                meta = {
                    "kind": "current_location",
                    "location": city,
                    "source": raw[:300],
                }
                if ctx:
                    meta["time_context"] = ctx.lower()
                content = f"User is currently in {city}."
                if ctx:
                    content = f"User is currently in {city} for {ctx.lower()}."
                nodes.append(
                    MemoryNode(
                        node_type=NodeType.LOCATION.value,
                        name="current_location",
                        content=content,
                        metadata=meta,
                        salience=0.97,
                        user_id=user_id,
                    )
                )

        m = _LOCATION_CORRECTION_RE.search(raw)
        if m:
            city = _clean_place(m.group("loc"))
            if city and city.lower() not in {"good", "fine", "okay", "ok", "right"}:
                nodes.append(
                    MemoryNode(
                        node_type=NodeType.LOCATION.value,
                        name="current_location",
                        content=f"User is currently in {city}.",
                        metadata={
                            "kind": "current_location",
                            "location": city,
                            "source": raw[:300],
                        },
                        salience=0.99,
                        user_id=user_id,
                    )
                )

        for pat in (_USER_STUDY_BECAUSE_RE, _USER_STUDY_RE):
            m = pat.search(raw)
            if m:
                school = _clean_place(m.group("school"))
                if school:
                    nodes.append(
                        MemoryNode(
                            node_type=NodeType.FACT.value,
                            name="user_education",
                            content=f"User studies at {school}.",
                            metadata={
                                "kind": "education",
                                "school": school,
                                "source": raw[:300],
                            },
                            salience=0.9,
                            user_id=user_id,
                        )
                    )
                break

        m = _MY_NAME_RE.search(raw)
        if m and " my " not in m.group("name").lower():
            name = _clean_person_name(m.group("name"))
            nodes.append(
                MemoryNode(
                    node_type=NodeType.PERSON.value,
                    name=name,
                    content="Primary user name",
                    metadata={
                        "kind": "identity",
                        "person_name": name,
                        "name_type": "preferred",
                    },
                    salience=0.94,
                    user_id=user_id,
                )
            )

        m = _OFFICIAL_NAME_RE.search(raw)
        if m:
            name = _clean_person_name(m.group("name"))
            if name:
                nodes.append(
                    MemoryNode(
                        node_type=NodeType.FACT.value,
                        name="user:official_name",
                        content=f"User's official name is {name}.",
                        metadata={
                            "kind": "identity",
                            "official_name": name,
                            "name_type": "official",
                            "source": raw[:300],
                        },
                        salience=0.93,
                        user_id=user_id,
                    )
                )

        father_loc: Optional[str] = None
        for match in _PARENT_NAME_RE.finditer(raw):
            rel_raw = match.group("rel").lower().replace("'", "")
            relation = _RELATION_NORM.get(rel_raw, rel_raw)
            tail = raw[match.end() :]
            person = _clean_person_name(match.group("name") + " " + tail[:40])
            if not person:
                continue
            loc_m = _PARENT_LIVES_RE.search(tail) or re.search(
                rf"live(?:s)?\s+in\s+({_CITY_TOKEN})", tail, re.I
            )
            if loc_m:
                loc_raw = loc_m.groupdict().get("loc") or loc_m.group(1)
                loc = _clean_place(loc_raw)
            else:
                loc = ""
            if relation == "mother" and not loc and father_loc and re.search(
                r"with\s+my\s+dad", raw, re.I
            ):
                loc = father_loc
            if relation == "father" and loc:
                father_loc = loc
            meta = {
                "kind": "relation",
                "relation": relation,
                "person_name": person,
                "source": raw[:300],
            }
            if loc:
                meta["location"] = loc
            self._append_relation_nodes(nodes, user_id, relation, person, meta)

        if _HERE_WITH_THEM_RE.search(raw):
            loc_m = re.search(
                rf"(?:also\s+)?live(?:s)?\s+in\s+({_CITY_TOKEN})", raw, re.I
            )
            here_city = _clean_place(loc_m.group(1)) if loc_m else father_loc
            if here_city:
                nodes.append(
                    MemoryNode(
                        node_type=NodeType.LOCATION.value,
                        name="current_location",
                        content=f"User is currently in {here_city} with parents.",
                        metadata={
                            "kind": "current_location",
                            "location": here_city,
                            "time_context": "with parents",
                            "source": raw[:300],
                        },
                        salience=0.98,
                        user_id=user_id,
                    )
                )

        m = _SISTER_FULL_NAME_RE.search(raw) or _HER_FULL_NAME_SLOT_RE.search(raw)
        if m:
            full = _clean_person_name(m.group("name"))
            if full:
                meta = {
                    "kind": "relation",
                    "relation": "sister",
                    "person_name": full.split()[0],
                    "full_name": full,
                    "source": raw[:300],
                }
                self._append_relation_nodes(
                    nodes, user_id, "sister", full, meta, display_name=full
                )

        for m in _MY_RELATION_NAME_LOOSE_RE.finditer(raw):
            relation = _RELATION_NORM.get(m.group("rel").lower(), m.group("rel").lower())
            person = _clean_person_name(m.group("name"))
            if not person:
                continue
            tail = raw[m.end() :]
            meta = {
                "kind": "relation",
                "relation": relation,
                "person_name": person,
                "source": raw[:300],
            }
            study_m = re.search(
                r"\b(?:she|he|they)?\s*stud(?:y|ies|ying|ing)\s+"
                r"(?:[A-Za-z]{1,20}\s+)?(?:at|in)\s+"
                r"(?P<school>[A-Za-z][\w\s]+?)(?:\s+she\b|\s+he\b|\s+they\b|\s+and\b|,|\.|$)",
                tail,
                re.I,
            )
            if study_m:
                school = _clean_place(study_m.group("school"))
                if school:
                    meta["school"] = school
            loc_m = re.search(
                rf"\b(?:she|he|they)?\s*live(?:s)?\s+in\s+(?P<loc>{_CITY_TOKEN})",
                tail,
                re.I,
            )
            if loc_m:
                loc = _clean_place(loc_m.group("loc"))
                if loc:
                    meta["location"] = loc
            self._append_relation_nodes(nodes, user_id, relation, person, meta)

        m = _PARTNER_IS_RE.search(raw)
        if m:
            person = _clean_person_name(m.group("name"))
            if not person or person.lower() in _BAD_PERSON_ENTITY_NAMES:
                return nodes
            meta = {
                "kind": "relation",
                "relation": "partner",
                "person_name": person,
                "source": raw[:300],
            }
            nodes.append(
                MemoryNode(
                    node_type=NodeType.PERSON.value,
                    name=person,
                    content="User's partner",
                    metadata=meta,
                    salience=0.95,
                    user_id=user_id,
                )
            )
            nodes.append(
                MemoryNode(
                    node_type=NodeType.FACT.value,
                    name="user:partner",
                    content=f"User's partner is {person}.",
                    metadata=meta,
                    salience=0.96,
                    user_id=user_id,
                )
            )
            lm = _PARTNER_LIVES_RE.search(raw)
            if lm:
                loc = _clean_place(lm.group("loc"))
                if loc:
                    meta["location"] = loc
                    nodes[-1].metadata = dict(meta)
                    nodes[-1].content = f"User's partner is {person} and lives in {loc}."
            sm = _PARTNER_STUDIES_RE.search(raw)
            if sm:
                school = _clean_place(sm.group("school"))
                if school:
                    meta["school"] = school
                    nodes[-1].metadata = dict(meta)
                    nodes[-1].content += f" Studies at {school}."

        if "return" in lower and "flight" in lower:
            dep_m = _RETURN_FLIGHT_RE.search(raw)
            arr_m = _RETURN_ARRIVE_RE.search(raw)
            if dep_m or arr_m:
                dep_day = dep_m.group("dep") if dep_m else ""
                dep_month = (dep_m.group("month") or "") if dep_m else ""
                dep_time = (dep_m.group("dep_time") or "").strip() if dep_m else ""
                dest = _clean_place(arr_m.group("dest")) if arr_m else ""
                arr_day = arr_m.group("arr") if arr_m else ""
                arr_month = (arr_m.group("month") or "") if arr_m else ""
                arr_time = (arr_m.group("arr_time") or "").strip() if arr_m else ""
                meta = {
                    "kind": "travel",
                    "slot": "return_flight",
                    "return_depart_date": _month_label(dep_month or None, dep_day),
                    "return_depart_time": dep_time,
                    "return_arrival_date": _month_label(arr_month or None, arr_day),
                    "return_arrival_time": arr_time,
                    "return_destination": dest,
                    "source": raw[:400],
                }
                parts = []
                if dep_day:
                    line = f"Return flight on {_month_label(dep_month or None, dep_day)}"
                    if dep_time:
                        line += f" {dep_time}"
                    parts.append(line)
                if dest and arr_day:
                    line = f"arrive in {dest} on {_month_label(arr_month or None, arr_day)}"
                    if arr_time:
                        line += f" {arr_time}"
                    parts.append(line)
                content = ". ".join(parts) + "." if parts else raw[:300]
                nodes.append(
                    MemoryNode(
                        node_type=NodeType.FACT.value,
                        name="user:travel_return",
                        content=content,
                        metadata=meta,
                        salience=0.97,
                        user_id=user_id,
                    )
                )

        return nodes

    def compile_and_store(
        self, user_message: str, assistant_message: str, metadata: dict = None
    ) -> dict:
        metadata = metadata or {}
        user_id = metadata.get("user_id") or config.user_id

        nodes, _ = self.compile(user_message, assistant_message, metadata)
        stored_nodes: List[int] = []
        stored_edges: List[int] = []

        for node in nodes:
            stored_nodes.append(self.storage.upsert_node(node))

        user_text = user_message or ""
        combined = f"{user_message or ''} {assistant_message or ''}"
        for node in self.extract_people_and_relationships(user_text, user_id):
            stored_nodes.append(self.storage.upsert_node(node))
        for node in self.extract_structured_personal_facts(user_message or "", user_id):
            stored_nodes.append(self.storage.upsert_node(node))
        person = self.storage.get_node_by_name(user_id, NodeType.PERSON.value, user_id)
        if not person:
            anchor = MemoryNode(
                node_type=NodeType.PERSON.value,
                name=user_id,
                content="Primary user anchor",
                metadata={"kind": "identity_anchor", "user_id": user_id},
                salience=0.75,
                user_id=user_id,
            )
            stored_nodes.append(self.storage.upsert_node(anchor))
            person = self.storage.get_node_by_name(user_id, NodeType.PERSON.value, user_id)
        if not person:
            logger.warning("No anchor PERSON node for user_id=%s after repair; skipping edges", user_id)
            return {"nodes": stored_nodes, "edges": stored_edges}

        for obj_name, edge_type, ctx in self.extract_relationships(user_text, user_id):
            pref = MemoryNode(
                node_type=NodeType.PREFERENCE.value,
                name=obj_name[:80],
                content=f"Inferred preference ({edge_type})",
                salience=0.45,
                user_id=user_id,
            )
            tid = self.storage.upsert_node(pref)
            edge = MemoryEdge(
                source_id=person.id,
                target_id=tid,
                edge_type=edge_type,
                weight=0.65,
                context=ctx,
                user_id=user_id,
            )
            stored_edges.append(self.storage.upsert_edge(edge))

        return {"nodes": stored_nodes, "edges": stored_edges}

    def seed_initial_data(self):
        """
        Optional local seed file (never committed): live brain seed_nodes.json
        Schema: { "nodes": [ {"node_type":"PROJECT","name":"...","content":"...", "salience": 0.7 }, ... ] }
        """
        seed_path: Path = config.BRAIN_DIR / "seed_nodes.json"
        if not seed_path.is_file():
            logger.info(
                "No seed_nodes.json at %s — skipping seed (add file locally to seed graph).",
                seed_path,
            )
            return

        try:
            data = json.loads(seed_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to read seed_nodes.json: %s", e)
            return

        nodes_spec = data.get("nodes") or []
        uid = config.user_id
        for spec in nodes_spec:
            try:
                node = MemoryNode(
                    node_type=str(spec.get("node_type", NodeType.FACT.value)),
                    name=str(spec.get("name", "")).strip(),
                    alias=spec.get("alias"),
                    content=spec.get("content"),
                    salience=float(spec.get("salience", 0.6)),
                    user_id=str(spec.get("user_id", uid)),
                )
                if not node.name:
                    continue
                if self.storage.get_node_by_name(
                    node.name, node.node_type, node.user_id
                ):
                    continue
                self.storage.insert_node(node)
                logger.info("Seeded node from file: %s (%s)", node.name, node.node_type)
            except Exception as e:
                logger.warning("Skip bad seed entry %r: %s", spec, e)


compiler = MemoryCompiler()
