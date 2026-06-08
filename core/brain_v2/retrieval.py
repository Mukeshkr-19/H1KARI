"""Brain v2 retrieval — layered context packet with dedup and ranking."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from core.brain_v2.candidate_scoring import normalize_statement
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.recall_intent import (
    INTENT_CURRENT_LOCATION,
    INTENT_EDUCATION,
    INTENT_FAMILY_PERSON,
    INTENT_GENERAL_MEMORY,
    INTENT_HIKARI_DECISION,
    INTENT_IDENTITY_SELF,
    INTENT_LOCATION,
    INTENT_PLAN,
    INTENT_PREFERENCE,
    INTENT_PROFILE_SUMMARY,
    INTENT_RELATIONSHIP,
    INTENT_TRAVEL,
    classify_recall_intent,
    memory_person_names,
    memory_relations_from_text,
    matches_personal_factual_firewall,
    person_names_compatible,
    relations_compatible,
    requested_person_names,
    requested_relations,
    query_seeks_session_location,
)
from core.brain_v2.schemas import MemoryCandidateStatus, MemoryLayer, SourceLinkedMemory
from core.brain_v2.working_memory import WorkingMemory

_EPISODIC_MAX_SCORE = 0.42
_SEMANTIC_MIN_SCORE = 0.22

_EDUCATION_TEXT = re.compile(
    r"\b(?:study|studies|studying|studied|student|university|college|school)\b",
    re.I,
)
_PLAN_ACTIVITY = re.compile(
    r"\b(?:meeting|meet|lunch|dinner|appointment|plans?)\b",
    re.I,
)
_PLAN_DATE = re.compile(
    r"\b(?:tomorrow|today|tonight|sunday|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|january|february|march|april|may|june|july|august|"
    r"september|october|november|december|\d{1,2}\s+\d{4})\b",
    re.I,
)
_PLAN_MEAL = re.compile(r"\b(?:lunch|dinner|brunch|breakfast)\b", re.I)
_PLAN_AT_PLACE = re.compile(r"\bat\s+[A-Z]", re.I)
_IDENTITY_NAME_TEXT = re.compile(
    r"\bmy\s+(?:(?:official|legal|full)\s+)?name\s+is\b",
    re.I,
)

_PROFILE_SECTIONS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("identity", ("identity",)),
    ("family / relationships", ("relation", "education")),
    ("plans / events", ("plan", "event")),
    ("preferences", ("preference",)),
    ("locations / travel", ("location", "current_location", "travel")),
    ("HIKARI project decisions", ("decision",)),
)

_FAMILY_TOKENS = (
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
)


@dataclass(frozen=True)
class BrainV2MemoryHit:
    layer: str
    text: str
    score: float
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainV2ContextPacket:
    query: str
    hits: List[BrainV2MemoryHit] = field(default_factory=list)
    strategies: List[str] = field(default_factory=list)
    recall_intent: str = "non_memory"

    def to_prompt(self, limit: int = 8) -> str:
        if not self.hits:
            return ""
        lines = ["[Brain v2 context]"]
        by_layer: Dict[str, List[BrainV2MemoryHit]] = {}
        for hit in self.hits[:limit]:
            by_layer.setdefault(hit.layer, []).append(hit)
        for layer, items in by_layer.items():
            lines.append(f"{layer}:")
            for item in items:
                text = item.text[:200]
                lines.append(f"- {text}")
        return "\n".join(lines)

    def top_semantic_hits(self, limit: int = 5) -> List[BrainV2MemoryHit]:
        semantic = [h for h in self.hits if h.layer == MemoryLayer.SEMANTIC.value]
        return sorted(semantic, key=lambda h: h.score, reverse=True)[:limit]


class BrainV2Retrieval:
    def __init__(
        self,
        store: Optional[EpisodeStore] = None,
        working: Optional[WorkingMemory] = None,
        neural_bridge=None,
        *,
        allow_neural_procedural: bool = True,
        allow_neural_conflict_reads: bool = True,
    ):
        self.store = store or EpisodeStore()
        self.working = working or WorkingMemory()
        self._neural = neural_bridge
        self.allow_neural_procedural = allow_neural_procedural
        self.allow_neural_conflict_reads = allow_neural_conflict_reads

    def retrieve(
        self,
        query: str,
        *,
        speaker_context: Optional[Dict[str, str]] = None,
        task_context: Optional[str] = None,
        limit: int = 10,
    ) -> BrainV2ContextPacket:
        intent = classify_recall_intent(query)
        strategies: List[str] = [f"recall_intent:{intent}"]
        hits: List[BrainV2MemoryHit] = []
        q_tokens = {
            t for t in re.findall(r"[a-zA-Z][a-zA-Z']+", (query or "").lower()) if len(t) > 2
        }

        if task_context:
            self.working.set_task(task_context)
        if speaker_context:
            self.working.note_speaker(
                speaker_context.get("speaker"),
                speaker_context.get("household"),
            )
        for line in self.working.to_context_lines(5):
            hits.append(
                BrainV2MemoryHit(
                    layer=MemoryLayer.WORKING.value,
                    text=line,
                    score=0.95,
                    source="working_memory",
                )
            )
        if hits:
            strategies.append("working_memory")

        if speaker_context:
            for key in ("speaker", "household", "family"):
                val = speaker_context.get(key)
                if val:
                    hits.append(
                        BrainV2MemoryHit(
                            layer=MemoryLayer.ENTITY.value,
                            text=f"{key}: {val}",
                            score=0.9,
                            source="speaker_context",
                        )
                    )
            strategies.append("speaker_context")

        rejected_norms = self._rejected_normalized_statements()
        pending_norms = self._pending_normalized_statements()

        accepted = self.store.get_active_accepted_memories(limit=80)
        for mem in accepted:
            score = self._score_accepted_memory(mem, q_tokens, intent, query)
            if score < _SEMANTIC_MIN_SCORE:
                continue
            hits.append(
                BrainV2MemoryHit(
                    layer=MemoryLayer.SEMANTIC.value,
                    text=self._format_source_linked(mem),
                    score=score,
                    source="source_linked",
                    metadata={
                        "episode_id": mem.episode_id,
                        "memory_id": mem.memory_id,
                        "segments": self._evidence_segment_ids(mem),
                        "candidate_type": (mem.metadata or {}).get("candidate_type"),
                    },
                )
            )
        if any(h.source == "source_linked" for h in hits):
            strategies.append("source_linked_semantic")

        excluded_episode_ids = self.store.get_episode_ids_with_inactive_accepted_memory()
        has_semantic = any(h.source == "source_linked" for h in hits)
        structured_hits = self._structured_episode_hits(
            q_tokens,
            limit=3,
            excluded_norms=rejected_norms | pending_norms,
            excluded_episode_ids=excluded_episode_ids,
            cap_lower=has_semantic,
        )
        hits.extend(structured_hits)
        if structured_hits:
            strategies.append("structured_episodic_support")

        proc = self._procedural_hits(query)
        hits.extend(proc)
        if proc:
            strategies.append("procedural_neural")

        hits = self._dedupe_and_cap(hits, limit)
        return BrainV2ContextPacket(
            query=query, hits=hits, strategies=strategies, recall_intent=intent
        )

    def build_profile_summary_context(self, query: str, *, limit: int = 8) -> str:
        """Grouped accepted semantic memories for profile-style questions."""
        accepted = self.store.get_active_accepted_memories(limit=200)
        if not accepted:
            return ""

        grouped: Dict[str, List[SourceLinkedMemory]] = {label: [] for label, _ in _PROFILE_SECTIONS}
        for mem in accepted:
            ctype = (mem.metadata or {}).get("candidate_type", "fact")
            for label, types in _PROFILE_SECTIONS:
                if ctype in types:
                    grouped[label].append(mem)
                    break
            else:
                grouped["identity"].append(mem)

        lines = ["[Brain v2 reviewed profile]"]
        count = 0
        for label, _ in _PROFILE_SECTIONS:
            items = grouped.get(label) or []
            if not items:
                continue
            lines.append(f"{label}:")
            for mem in items:
                if count >= limit:
                    break
                lines.append(f"- {self._format_source_linked(mem)}")
                count += 1
            if count >= limit:
                break

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)

    def _unresolved_conflict_snapshot(self):
        from core.brain_v2.neural_conflict_state import (
            UnresolvedConflictSnapshot,
            build_unresolved_conflict_snapshot,
        )

        if os.getenv("HIKARI_BRAIN_V2_EVAL") == "1" or not self.allow_neural_conflict_reads:
            return UnresolvedConflictSnapshot()

        from core.brain_v2.legacy_reconciliation import fetch_neural_summary_readonly

        accepted = self.store.get_active_accepted_memories(limit=200)
        summary, available = fetch_neural_summary_readonly()
        neural_summary = summary if available else None
        return build_unresolved_conflict_snapshot(accepted, neural_summary)

    def answer_from_accepted(self, query: str) -> Optional[str]:
        """Direct answer from accepted source-linked memories only."""
        intent = classify_recall_intent(query)
        if intent == INTENT_PROFILE_SUMMARY:
            summary = self.build_profile_summary_context(query, limit=8)
            if summary:
                return (
                    "From reviewed Brain v2 memories:\n"
                    + summary.replace("[Brain v2 reviewed profile]\n", "").strip()
                )
            return self._no_reviewed_memory_reply(intent, query)

        if intent == INTENT_CURRENT_LOCATION:
            session_answer = self._answer_current_location_from_session()
            if session_answer:
                return session_answer

        if intent == INTENT_GENERAL_MEMORY:
            session_answer = self._answer_current_location_from_session()
            if session_answer and query_seeks_session_location(query):
                return session_answer

        if intent == INTENT_GENERAL_MEMORY and matches_personal_factual_firewall(query.lower()):
            return self._no_reviewed_memory_reply(intent, query)

        packet = self.retrieve(query, limit=12)
        semantic = packet.top_semantic_hits(5)

        if intent in (
            INTENT_IDENTITY_SELF,
            INTENT_FAMILY_PERSON,
            INTENT_RELATIONSHIP,
            INTENT_PREFERENCE,
            INTENT_TRAVEL,
            INTENT_HIKARI_DECISION,
            INTENT_EDUCATION,
            INTENT_PLAN,
        ):
            semantic = self._filter_semantic_hits_by_recall_type(semantic, intent)

        if intent == INTENT_CURRENT_LOCATION:
            semantic = self._filter_semantic_hits_by_location_kind(semantic, current=True)
        elif intent == INTENT_LOCATION:
            semantic = self._filter_semantic_hits_by_location_kind(semantic, current=False)

        if intent in (INTENT_FAMILY_PERSON, INTENT_RELATIONSHIP, INTENT_EDUCATION):
            requested = requested_relations(query)
            if requested:
                semantic = self._filter_semantic_hits_by_relation(semantic, requested)

        requested_people = requested_person_names(query)
        if requested_people and semantic:
            semantic = self._filter_semantic_hits_by_person(semantic, requested_people)

        if intent == INTENT_IDENTITY_SELF:
            identity_answer = self._answer_identity_self()
            if identity_answer:
                return identity_answer

        if not semantic:
            if intent != "non_memory":
                return self._no_reviewed_memory_reply(intent, query)
            return None

        top = semantic[0]
        if top.score < _SEMANTIC_MIN_SCORE:
            return self._no_reviewed_memory_reply(intent, query)

        prefix = "Yes. " if intent == INTENT_FAMILY_PERSON and "know" in query.lower() else ""
        body = self._statement_from_hit(top)
        return f"{prefix}From reviewed memory: {body}"

    def best_stable_home_place(self) -> Optional[str]:
        """Best-effort stable home city label from accepted memories."""
        best: Optional[str] = None
        best_score: float = -1.0
        for mem in self.store.get_active_accepted_memories(limit=200):
            if not self._memory_matches_stable_location(mem):
                continue
            meta = mem.metadata or {}
            loc = str(meta.get("location") or "").strip()
            if not loc:
                m = re.search(r"\bi\s+live\s+in\s+([A-Za-z][\w\s'-]{2,60})", mem.statement or "", re.I)
                if m:
                    loc = m.group(1).strip().rstrip(".!? ")
            if not loc:
                continue
            score = float(meta.get("rank_score") or 0.0)
            if score >= best_score:
                best_score = score
                best = loc
        return best

    def _accepted_by_memory_id(self) -> Dict[str, SourceLinkedMemory]:
        return {m.memory_id: m for m in self.store.get_active_accepted_memories(limit=200)}

    @staticmethod
    def _memory_matches_identity_intent(mem: SourceLinkedMemory) -> bool:
        meta = mem.metadata or {}
        ctype = str(meta.get("candidate_type", "fact"))
        if ctype != "identity":
            return False
        if meta.get("preferred_name"):
            return True
        return bool(_IDENTITY_NAME_TEXT.search(mem.statement or ""))

    def _answer_identity_self(self) -> Optional[str]:
        """Merge legal name + preferred/call-me identity into one honest answer."""
        preferred: Optional[str] = None
        statements: List[str] = []
        seen_norms: set[str] = set()
        for mem in self.store.get_active_accepted_memories(limit=200):
            if not self._memory_matches_identity_intent(mem):
                continue
            meta = mem.metadata or {}
            pname = str(meta.get("preferred_name") or "").strip()
            if not pname:
                m_pref = re.search(
                    r"\bpreferred\s+name\s+is\s+([A-Za-z][\w'-]+)\b",
                    mem.statement or "",
                    re.I,
                )
                if m_pref:
                    pname = m_pref.group(1).strip().title()
            if pname:
                preferred = pname
            stmt = (mem.statement or "").strip()
            if not stmt:
                continue
            norm = normalize_statement(stmt)
            if norm in seen_norms:
                continue
            seen_norms.add(norm)
            statements.append(stmt.rstrip("."))

        if not preferred and not statements:
            return None

        parts: List[str] = []
        if preferred:
            parts.append(f"I call you {preferred}")
        for stmt in statements:
            low = stmt.lower()
            if preferred and preferred.lower() in low and "preferred name" in low:
                continue
            parts.append(stmt)
        if not parts:
            return None
        return "From reviewed memory: " + ". ".join(parts) + "."

    @staticmethod
    def _memory_matches_relation_intent(mem: SourceLinkedMemory) -> bool:
        ctype = str((mem.metadata or {}).get("candidate_type", "fact"))
        return ctype in ("relation", "education")

    @staticmethod
    def _memory_matches_preference_intent(mem: SourceLinkedMemory) -> bool:
        return str((mem.metadata or {}).get("candidate_type", "fact")) == "preference"

    @staticmethod
    def _memory_matches_travel_intent(mem: SourceLinkedMemory) -> bool:
        return str((mem.metadata or {}).get("candidate_type", "fact")) == "travel"

    @staticmethod
    def _memory_matches_decision_intent(mem: SourceLinkedMemory) -> bool:
        return str((mem.metadata or {}).get("candidate_type", "fact")) == "decision"

    @staticmethod
    def _memory_matches_education_intent(mem: SourceLinkedMemory) -> bool:
        meta = mem.metadata or {}
        ctype = str(meta.get("candidate_type", "fact"))
        text = mem.statement or ""
        if ctype == "education":
            return True
        if ctype == "relation" and _EDUCATION_TEXT.search(text):
            return True
        return False

    @staticmethod
    def _memory_matches_plan_intent(mem: SourceLinkedMemory) -> bool:
        meta = mem.metadata or {}
        ctype = str(meta.get("candidate_type", "fact"))
        text = mem.statement or ""
        if ctype in ("plan", "event"):
            return True
        if not _PLAN_ACTIVITY.search(text):
            return False
        if meta.get("date_text") or meta.get("place"):
            return True
        if _PLAN_DATE.search(text) or _PLAN_AT_PLACE.search(text) or _PLAN_MEAL.search(text):
            return True
        return False

    @staticmethod
    def _memory_matches_stable_location(mem: SourceLinkedMemory) -> bool:
        meta = mem.metadata or {}
        ctype = str(meta.get("candidate_type", "fact"))
        text = (mem.statement or "").lower()
        if ctype == "current_location":
            return False
        if ctype == "location" or meta.get("location"):
            return True
        return "live in" in text

    @staticmethod
    def _memory_matches_current_location(mem: SourceLinkedMemory) -> bool:
        meta = mem.metadata or {}
        ctype = str(meta.get("candidate_type", "fact"))
        text = (mem.statement or "").lower()
        if ctype == "current_location" or meta.get("current_location"):
            return True
        if "live in" in text:
            return False
        return bool(re.search(r"\b(?:right\s+now|currently|at\s+the\s+moment)\b", text))

    def _answer_current_location_from_session(self) -> Optional[str]:
        loc_data = self.working.get_current_location()
        if not loc_data:
            return None
        _loc, stmt = loc_data
        body = (stmt or "").strip().rstrip(".")
        return f"From recent session context: {body}."

    def _filter_semantic_hits_by_location_kind(
        self,
        hits: List[BrainV2MemoryHit],
        *,
        current: bool,
    ) -> List[BrainV2MemoryHit]:
        if not hits:
            return hits
        accepted_by_id = self._accepted_by_memory_id()
        matcher = (
            self._memory_matches_current_location
            if current
            else self._memory_matches_stable_location
        )
        filtered: List[BrainV2MemoryHit] = []
        for hit in hits:
            memory_id = (hit.metadata or {}).get("memory_id")
            mem = accepted_by_id.get(memory_id)
            if mem and matcher(mem):
                filtered.append(hit)
        return filtered

    def _filter_semantic_hits_by_recall_type(
        self,
        hits: List[BrainV2MemoryHit],
        intent: str,
    ) -> List[BrainV2MemoryHit]:
        if not hits:
            return hits
        accepted_by_id = self._accepted_by_memory_id()
        matchers = {
            INTENT_IDENTITY_SELF: self._memory_matches_identity_intent,
            INTENT_FAMILY_PERSON: self._memory_matches_relation_intent,
            INTENT_RELATIONSHIP: self._memory_matches_relation_intent,
            INTENT_PREFERENCE: self._memory_matches_preference_intent,
            INTENT_TRAVEL: self._memory_matches_travel_intent,
            INTENT_HIKARI_DECISION: self._memory_matches_decision_intent,
            INTENT_EDUCATION: self._memory_matches_education_intent,
            INTENT_PLAN: self._memory_matches_plan_intent,
        }
        matcher = matchers[intent]
        filtered: List[BrainV2MemoryHit] = []
        for hit in hits:
            memory_id = (hit.metadata or {}).get("memory_id")
            mem = accepted_by_id.get(memory_id)
            if mem and matcher(mem):
                filtered.append(hit)
        return filtered

    def _filter_semantic_hits_by_relation(
        self,
        hits: List[BrainV2MemoryHit],
        requested: Set[str],
    ) -> List[BrainV2MemoryHit]:
        """Keep only semantic hits whose accepted memory matches requested relation(s)."""
        if not hits or not requested:
            return hits
        accepted_by_id = self._accepted_by_memory_id()
        filtered: List[BrainV2MemoryHit] = []
        for hit in hits:
            memory_id = (hit.metadata or {}).get("memory_id")
            mem = accepted_by_id.get(memory_id)
            if not mem:
                continue
            mem_rels = memory_relations_from_text(mem.statement, mem.metadata)
            if relations_compatible(requested, mem_rels):
                filtered.append(hit)
        return filtered

    def _filter_semantic_hits_by_person(
        self,
        hits: List[BrainV2MemoryHit],
        requested: Set[str],
    ) -> List[BrainV2MemoryHit]:
        if not hits or not requested:
            return hits
        accepted_by_id = self._accepted_by_memory_id()
        filtered: List[BrainV2MemoryHit] = []
        for hit in hits:
            memory_id = (hit.metadata or {}).get("memory_id")
            mem = accepted_by_id.get(memory_id)
            if not mem:
                continue
            mem_people = memory_person_names(mem.statement, mem.metadata)
            if person_names_compatible(requested, mem_people):
                filtered.append(hit)
        return filtered

    def _no_reviewed_memory_reply(self, intent: str, query: str) -> str:
        from core.brain_v2.neural_conflict_state import CONFLICT_REVIEW_NEEDED_MESSAGE

        if self._unresolved_conflict_snapshot().blocks_recall_intent(intent):
            return CONFLICT_REVIEW_NEEDED_MESSAGE
        if intent in (INTENT_FAMILY_PERSON, INTENT_RELATIONSHIP):
            requested = requested_relations(query)
            if requested:
                rel_label = next(iter(requested))
                return (
                    f"I don't have a reviewed memory about your {rel_label} yet. "
                    "Check pending candidates with `hikari.py --brain-v2-pending`."
                )
        q = (query or "").lower()
        if intent == INTENT_FAMILY_PERSON:
            for rel in _FAMILY_TOKENS:
                if rel in q:
                    return (
                        f"I don't have a reviewed memory about your {rel} yet. "
                        "Check pending candidates with `hikari.py --brain-v2-pending`."
                    )
        if intent == INTENT_HIKARI_DECISION:
            return (
                "I don't have a reviewed memory about a HIKARI project decision yet. "
                "Check pending candidates with `hikari.py --brain-v2-pending`."
            )
        if intent in (
            INTENT_PREFERENCE,
            INTENT_LOCATION,
            INTENT_CURRENT_LOCATION,
            INTENT_TRAVEL,
            INTENT_IDENTITY_SELF,
            INTENT_PLAN,
            INTENT_EDUCATION,
            INTENT_PROFILE_SUMMARY,
            INTENT_GENERAL_MEMORY,
        ):
            return "I do not have a reviewed memory for that yet."
        return "I do not have a reviewed memory for that yet."

    def _statement_from_hit(self, hit: BrainV2MemoryHit) -> str:
        text = hit.text
        for marker in (" [source:", " [memory_id:"):
            if marker in text:
                text = text.split(marker, 1)[0].strip()
        return text

    def _evidence_segment_ids(self, mem: SourceLinkedMemory) -> List[str]:
        from core.brain_v2.memory_lifecycle import is_operator_reviewed_correction

        if is_operator_reviewed_correction(mem):
            return []
        return list(mem.source_segment_ids or [])

    def _format_source_linked(self, mem: SourceLinkedMemory) -> str:
        from core.brain_v2.memory_lifecycle import is_operator_reviewed_correction

        parts = [mem.statement]
        if is_operator_reviewed_correction(mem):
            parts.append("[source: operator-reviewed correction]")
        elif mem.source_segment_ids:
            parts.append(f"[source: {len(mem.source_segment_ids)} segment(s)]")
        if mem.memory_id:
            parts.append(f"[memory_id: {mem.memory_id[:8]}]")
        return " ".join(parts)

    def _score_accepted_memory(
        self, mem: SourceLinkedMemory, q_tokens: set, intent: str, query: str
    ) -> float:
        text = (mem.statement or "").lower()
        overlap = sum(1 for t in q_tokens if t in text)
        task = min(1.0, overlap / max(1, len(q_tokens))) if q_tokens else 0.35
        meta = mem.metadata or {}
        salience = float(meta.get("salience", 0.55))
        conf = float(meta.get("confidence", 0.55))
        rank = float(meta.get("rank_score", 0.5))
        evidence = min(0.1, 0.03 * len(self._evidence_segment_ids(mem)))
        ctype = str(meta.get("candidate_type", "fact"))

        intent_boost = self._intent_boost(intent, ctype, text, q_tokens, query, meta)
        base = task * 0.42 + salience * 0.2 + conf * 0.14 + rank * 0.1 + evidence
        return max(0.0, min(1.0, base + intent_boost))

    def _intent_boost(
        self,
        intent: str,
        candidate_type: str,
        text: str,
        q_tokens: set,
        query: str,
        metadata: Optional[dict] = None,
    ) -> float:
        boost = 0.0
        meta = metadata or {}
        mem_rels = memory_relations_from_text(text, meta)
        requested = requested_relations(query)
        requested_people = requested_person_names(query)
        mem_people = memory_person_names(text, meta)

        if requested_people:
            if person_names_compatible(requested_people, mem_people):
                boost += 0.3
            elif mem_people:
                boost -= 0.45

        if intent in (INTENT_FAMILY_PERSON, INTENT_RELATIONSHIP) and requested:
            if relations_compatible(requested, mem_rels):
                boost += 0.28
                if candidate_type in ("relation", "education"):
                    boost += 0.1
            elif mem_rels:
                boost -= 0.4
            return boost

        if intent == INTENT_PROFILE_SUMMARY:
            return 0.15

        type_map = {
            INTENT_IDENTITY_SELF: ("identity", 0.22),
            INTENT_FAMILY_PERSON: ("relation", 0.2),
            INTENT_RELATIONSHIP: ("relation", 0.22),
            INTENT_EDUCATION: ("education", 0.26),
            INTENT_PREFERENCE: ("preference", 0.24),
            INTENT_LOCATION: ("location", 0.24),
            INTENT_CURRENT_LOCATION: ("current_location", 0.26),
            INTENT_TRAVEL: ("travel", 0.22),
            INTENT_HIKARI_DECISION: ("decision", 0.26),
        }
        if intent in type_map:
            expected, amount = type_map[intent]
            if candidate_type == expected:
                boost += amount
            elif intent == INTENT_EDUCATION and candidate_type == "relation":
                boost += amount * 0.65

        if intent == INTENT_CURRENT_LOCATION:
            if candidate_type == "current_location" or meta.get("current_location"):
                boost += 0.26
            if candidate_type == "location" or "live in" in text:
                boost -= 0.55

        if intent == INTENT_LOCATION:
            if candidate_type == "location" or "live in" in text:
                boost += 0.24
            if candidate_type == "current_location" or meta.get("current_location"):
                boost -= 0.55

        if intent == INTENT_PLAN:
            if candidate_type in ("plan", "event"):
                boost += 0.28
            if any(w in text for w in ("meeting", "lunch", "dinner", "plans")):
                boost += 0.12
            date_text = str(meta.get("date_text") or "").lower()
            if date_text and date_text in (query or "").lower():
                boost += 0.2
            for token in ("tomorrow", "sunday", "monday", "may", "lunch"):
                if token in (query or "").lower() and token in text:
                    boost += 0.08

        if intent == INTENT_EDUCATION:
            if candidate_type == "education":
                boost += 0.2
            if any(w in text for w in ("study", "studies", "student", "university", "college")):
                boost += 0.15

        if intent == INTENT_HIKARI_DECISION:
            if "hikari" in text or "brain" in text:
                boost += 0.18

        if intent == INTENT_PREFERENCE:
            if "prefer" in text or "don't like" in text or "like" in text:
                boost += 0.15

        if intent == INTENT_LOCATION and ("live" in text or "city" in text):
            boost += 0.15

        if intent == INTENT_CURRENT_LOCATION:
            if candidate_type == "current_location" or meta.get("current_location"):
                boost += 0.15
            if re.search(r"\b(?:right\s+now|currently)\b", text):
                boost += 0.1

        return boost

    def _rejected_normalized_statements(self) -> set:
        return self._candidate_norms_for_status(MemoryCandidateStatus.REJECTED)

    def _pending_normalized_statements(self) -> set:
        return self._candidate_norms_for_status(MemoryCandidateStatus.PENDING)

    def _candidate_norms_for_status(self, status: MemoryCandidateStatus) -> set:
        norms: set = set()
        for cand in self.store.get_candidates(status=status):
            norm = normalize_statement(cand.statement)
            if norm:
                norms.add(norm)
        return norms

    def _structured_episode_hits(
        self,
        q_tokens: set,
        limit: int,
        *,
        excluded_norms: Optional[set] = None,
        excluded_episode_ids: Optional[set] = None,
        cap_lower: bool = False,
    ) -> List[BrainV2MemoryHit]:
        """Support context only — capped below accepted semantic memories."""
        hits: List[BrainV2MemoryHit] = []
        excluded_norms = excluded_norms or set()
        excluded_episode_ids = excluded_episode_ids or set()
        with self.store._connect() as conn:
            rows = conn.execute(
                """
                SELECT episode_id, title, summary FROM structured_episodes
                ORDER BY ended_at DESC LIMIT 15
                """
            ).fetchall()
        for row in rows:
            if row["episode_id"] in excluded_episode_ids:
                continue
            blob = f"{row['title']} {row['summary']}".lower()
            blob_norm = normalize_statement(blob)
            if any(rn in blob_norm for rn in excluded_norms if len(rn) > 12):
                continue
            if self._looks_like_raw_transcript_leak(blob):
                continue
            overlap = sum(1 for t in q_tokens if t in blob) if q_tokens else 0
            if q_tokens and overlap == 0:
                continue
            score = min(_EPISODIC_MAX_SCORE, 0.22 + overlap * 0.06)
            if cap_lower:
                score = min(score, _EPISODIC_MAX_SCORE * 0.75)
            hits.append(
                BrainV2MemoryHit(
                    layer=MemoryLayer.EPISODIC.value,
                    text=f"(episode) {row['title']}: {row['summary'][:120]}",
                    score=score,
                    source="structured_episode",
                    metadata={"episode_id": row["episode_id"]},
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def _looks_like_raw_transcript_leak(self, text: str) -> bool:
        """Skip assistant filler lines if they appear in summaries."""
        low = text.lower()
        return any(
            p in low
            for p in ("got it.", "okay.", "noted.", "user: got", "assistant:")
        )

    def _procedural_hits(self, query: str) -> List[BrainV2MemoryHit]:
        if not self.allow_neural_procedural:
            return []
        try:
            from core.brain import HikariBrain

            packet = HikariBrain(self._neural).build_context_packet(query, limit=4)
            out: List[BrainV2MemoryHit] = []
            for item in packet.items:
                if item.layer == "procedural":
                    out.append(
                        BrainV2MemoryHit(
                            layer=MemoryLayer.PROCEDURAL.value,
                            text=f"{item.name}: {item.content}",
                            score=item.score,
                            source="neural_procedural",
                        )
                    )
            return out
        except Exception:
            return []

    def pending_or_rejected_in_results(self, query: str) -> bool:
        """True if any non-accepted candidate text would match query (guard for tests)."""
        q_norm = normalize_statement(query)
        for status in (MemoryCandidateStatus.PENDING, MemoryCandidateStatus.REJECTED):
            for cand in self.store.get_candidates(status=status):
                if normalize_statement(cand.statement) and (
                    not q_norm
                    or normalize_statement(cand.statement) in q_norm
                    or q_norm in normalize_statement(cand.statement)
                ):
                    return True
        return False

    def _dedupe_and_cap(self, hits: List[BrainV2MemoryHit], limit: int) -> List[BrainV2MemoryHit]:
        seen: set[str] = set()
        unique: List[BrainV2MemoryHit] = []
        for hit in sorted(hits, key=lambda h: h.score, reverse=True):
            key = normalize_statement(hit.text) or re.sub(
                r"\s+", " ", hit.text.lower().strip()
            )[:100]
            if key in seen:
                continue
            seen.add(key)
            unique.append(hit)
        return unique[:limit]
