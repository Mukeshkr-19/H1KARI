"""Reviewed Brain v2 profile answers; legacy neural personal lines are quarantined by default."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Set

NO_REVIEWED_PROFILE_MESSAGE = "I do not have a reviewed memory for that yet."

from core.brain_v2.candidate_scoring import normalize_statement
from core.brain_v2.schemas import SourceLinkedMemory

_PARTNER_RELATIONS: FrozenSet[str] = frozenset(
    {"girlfriend", "partner", "boyfriend", "wife", "husband"}
)

_PROFILE_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Identity", ("identity",)),
    ("Your education", ("user_education",)),
    ("Family / relationships", ("relation", "partner_education")),
    ("Plans / events", ("plan", "event")),
    ("Preferences", ("preference",)),
    ("Location", ("location",)),
    ("Current context", ("current_location",)),
    ("Travel", ("travel",)),
    ("HIKARI project decisions", ("decision",)),
)


@dataclass
class ReviewedProfileFacts:
    """Facts from accepted Brain v2 memories used to suppress stale neural lines."""

    user_locations: Set[str] = field(default_factory=set)
    current_context_locations: Set[str] = field(default_factory=set)
    user_education_orgs: Set[str] = field(default_factory=set)
    partner_education_orgs: Set[str] = field(default_factory=set)
    has_reviewed_location: bool = False
    has_reviewed_current_context: bool = False
    has_reviewed_partner_education: bool = False
    has_reviewed_user_education: bool = False
    session_current_location: Optional[str] = None


def _norm_place(text: str) -> str:
    return normalize_statement(text) or re.sub(r"\s+", " ", (text or "").lower().strip())


def _orgs_from_text(text: str) -> Set[str]:
    found: Set[str] = set()
    for pat in (
        r"\b([A-Za-z][\w\s']*(?:University|College|School))\b",
        r"\b(?:student|studies|studying)\s+at\s+(.+?)(?:\.|$)",
        r"\b(university\s+at\s+city\s+a|university\s+in\s+city\s+a|north\s+city\s+college)\b",
    ):
        for m in re.finditer(pat, text, re.I):
            found.add(_norm_place(m.group(1)))
    return found


def _is_partner_education_memory(mem: SourceLinkedMemory) -> bool:
    meta = mem.metadata or {}
    relation = str(meta.get("relation") or "").lower()
    low = (mem.statement or "").lower()
    if relation in _PARTNER_RELATIONS:
        return True
    return bool(
        re.search(r"\bmy\s+(?:girlfriend|gf|partner|boyfriend|wife|husband)\b", low)
        and re.search(r"\b(?:student|studies|studying|university|college)\b", low)
    )


def _profile_bucket(mem: SourceLinkedMemory) -> str:
    meta = mem.metadata or {}
    ctype = str(meta.get("candidate_type", "fact"))
    low = (mem.statement or "").lower()
    if ctype == "education" and re.search(r"\bi\s+study", low) and not _is_partner_education_memory(mem):
        return "user_education"
    if ctype == "education" and _is_partner_education_memory(mem):
        return "partner_education"
    if ctype == "relation":
        return "relation"
    return ctype


def collect_reviewed_profile_facts(
    memories: List[SourceLinkedMemory],
) -> ReviewedProfileFacts:
    facts = ReviewedProfileFacts()
    for mem in memories:
        meta = mem.metadata or {}
        ctype = str(meta.get("candidate_type", "fact"))
        stmt = (mem.statement or "").strip()
        low = stmt.lower()
        relation = str(meta.get("relation") or "").lower()

        if ctype == "location" or ("live in" in low and ctype != "current_location"):
            facts.has_reviewed_location = True
            loc = meta.get("location")
            if loc:
                facts.user_locations.add(_norm_place(str(loc)))
            m = re.search(r"\bi\s+live\s+in\s+(.+?)(?:\.|$)", low)
            if m:
                facts.user_locations.add(_norm_place(m.group(1)))

        if ctype == "current_location" or meta.get("current_location"):
            facts.has_reviewed_current_context = True
            loc = meta.get("current_location")
            if loc:
                facts.current_context_locations.add(_norm_place(str(loc)))
            m = re.search(
                r"\b(?:right\s+now\s+)?(?:i'?m|i am)\s+(?:currently\s+)?in\s+(.+?)(?:\s+for|\s*\.|$)",
                low,
            )
            if m:
                facts.current_context_locations.add(_norm_place(m.group(1)))

        orgs = _orgs_from_text(stmt)
        if meta.get("organization"):
            orgs.add(_norm_place(str(meta["organization"])))

        is_partner = _is_partner_education_memory(mem)
        if ctype == "education" or re.search(
            r"\b(?:student|studies|studying|university|college)\b", low
        ):
            if is_partner:
                facts.has_reviewed_partner_education = True
                facts.partner_education_orgs |= orgs
            elif re.search(r"\bi\s+study", low):
                facts.has_reviewed_user_education = True
                facts.user_education_orgs |= orgs

    return facts


def format_reviewed_profile_answer(
    memories: List[SourceLinkedMemory],
    *,
    session_current: Optional[tuple[str, str]] = None,
) -> str:
    """User-facing profile from accepted Brain v2 memories only."""
    if not memories and not session_current:
        return ""

    grouped: dict[str, list[SourceLinkedMemory]] = {
        label: [] for label, _ in _PROFILE_SECTIONS
    }
    for mem in memories:
        bucket = _profile_bucket(mem)
        for label, types in _PROFILE_SECTIONS:
            if bucket in types:
                grouped[label].append(mem)
                break
        else:
            grouped["Identity"].append(mem)

    lines = ["What I know about you:", "", "Reviewed memories:"]
    for label, _ in _PROFILE_SECTIONS:
        items = grouped.get(label) or []
        if not items:
            continue
        lines.append(f"{label}:")
        for mem in items:
            lines.append(f"- {mem.statement.strip()}")

    if session_current and not grouped.get("Current context"):
        loc, stmt = session_current
        lines.append("Current context:")
        lines.append(f"- {stmt.strip().rstrip('.')}. (recent session)")

    if len(lines) <= 3 and not session_current:
        return ""
    return "\n".join(lines)


def _should_suppress_neural_line(line: str, facts: ReviewedProfileFacts) -> bool:
    stripped = line.strip()
    low = stripped.lower()

    m = re.match(r"-\s*Home:\s*(.+)", stripped, re.I)
    if m and facts.has_reviewed_location:
        home = _norm_place(m.group(1))
        if facts.user_locations and home not in facts.user_locations:
            return True

    m = re.match(r"-\s*Currently\s+in:\s*(.+)", stripped, re.I)
    if m:
        curr = _norm_place(m.group(1))
        if facts.has_reviewed_current_context:
            if facts.current_context_locations and curr not in facts.current_context_locations:
                return True
        if facts.session_current_location:
            if _norm_place(facts.session_current_location) != curr:
                return True
        if facts.has_reviewed_location and not facts.has_reviewed_current_context:
            return True

    m = re.match(r"-\s*Education:\s*(.+)", stripped, re.I)
    if m:
        school = _norm_place(m.group(1))
        if facts.has_reviewed_partner_education and school in facts.partner_education_orgs:
            return True
        if facts.has_reviewed_user_education and school in facts.user_education_orgs:
            return True

    if facts.has_reviewed_location and low.startswith("- currently in:"):
        if not facts.has_reviewed_current_context and not facts.session_current_location:
            return True

    return False


def merge_profile_with_neural(
    reviewed_answer: str,
    neural_summary: Optional[str],
    facts: ReviewedProfileFacts,
    *,
    accepted_memories: Optional[List[SourceLinkedMemory]] = None,
) -> str:
    """Brain v2 reviewed profile first; non-conflicting neural lines only as fallback."""
    if not neural_summary or not neural_summary.strip():
        return reviewed_answer

    from core.brain_v2.conflicts import scan_conflicts

    conflict_lines = set()
    if accepted_memories is not None:
        for report in scan_conflicts(accepted_memories, neural_summary):
            conflict_lines.add(report.conflicting_line.strip().lower())

    supplemental: list[str] = []
    for line in neural_summary.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("what i know about"):
            continue
        if stripped.lower() in conflict_lines:
            continue
        if _should_suppress_neural_line(stripped, facts):
            continue
        supplemental.append(stripped)

    if not supplemental:
        return reviewed_answer

    return reviewed_answer + "\n\nAdditional context (neural, non-reviewed):\n" + "\n".join(
        supplemental
    )


def legacy_neural_profile_supplement_enabled() -> bool:
    """Removed from shipped Brain-authoritative behavior (always disabled)."""
    return False


def unsafe_neural_profile_supplement_env_set() -> bool:
    """True when a deprecated unsafe override env var is set (blocks READY)."""
    val = os.getenv("HIKARI_BRAIN_V2_UNSAFE_NEURAL_PROFILE_SUPPLEMENT", "").strip().lower()
    return val in ("1", "true", "yes")


def build_merged_user_profile_answer(
    memories: List[SourceLinkedMemory],
    neural_summary: Optional[str] = None,
    *,
    session_current: Optional[tuple[str, str]] = None,
) -> str:
    if not memories and not session_current:
        return NO_REVIEWED_PROFILE_MESSAGE
    facts = collect_reviewed_profile_facts(memories)
    if session_current:
        facts.session_current_location = session_current[0]
    reviewed = format_reviewed_profile_answer(memories, session_current=session_current)
    if not reviewed:
        return NO_REVIEWED_PROFILE_MESSAGE
    if not legacy_neural_profile_supplement_enabled() or not neural_summary:
        return reviewed
    return merge_profile_with_neural(
        reviewed,
        neural_summary,
        facts,
        accepted_memories=memories,
    )
