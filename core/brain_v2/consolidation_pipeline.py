"""Episode consolidation — raw segments → structured episode → memory candidates."""

from __future__ import annotations

import re
import uuid
from typing import List, Optional, Tuple

from core.brain_v2.candidate_quality import (
    EXTRACTION_POLICY_VERSION,
    QUALITY_WEAK,
    apply_quality_gate,
)
from core.brain_v2.candidate_scoring import annotate_and_rank_candidates, normalize_statement
from core.brain_v2.memory_type import (
    extract_owner_identity_names,
    infer_memory_type,
    normalize_user_education_statement,
)
from core.speaker_context import is_speaker_context_reset, is_temporary_speaker_intro
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.schemas import (
    EpisodeLifecycleState,
    MemoryCandidate,
    StructuredEpisode,
    TranscriptSegment,
)

_REMEMBER = re.compile(
    r"\bremember\s+(?:this|that)\b[:\s,-]*(.+)", re.I
)
_IDENTITY = re.compile(r"\bmy\s+name\s+is\s+([A-Za-z][\w\s'-]{1,60})", re.I)
_CALL_ME = re.compile(
    r"\b(?:you can\s+)?call\s+me\s+([A-Za-z][\w'-]*(?:\s+[A-Z])?)\b",
    re.I,
)
_LOCATION = re.compile(r"\bi\s+live\s+in\s+([A-Za-z][\w\s'-]{1,60})", re.I)
_PREFER = re.compile(r"\bi\s+prefer\s+(.+)", re.I)
_DISLIKE = re.compile(r"\bi\s+don'?t\s+like\s+(.+)", re.I)
_RELATION = re.compile(
    r"\bmy\s+"
    r"(dad|father|mom|mother|sister|brother|gf|girlfriend|partner|wife|husband)\b"
    r"(.+)",
    re.I,
)
_STUDY_WORK = re.compile(
    r"\bi\s+(study|studies|work|works)\s+(?:at|in)\s+([A-Za-z][\w\s'-]{2,60})",
    re.I,
)
_HIKARI_DECISION = re.compile(
    r"\b(?:for\s+)?hikari\b.+\b(decided|should|use|prefer|will|keep)\b[:\s,-]*(.+)?",
    re.I,
)
_TRAVEL = re.compile(
    r"\b(?:return\s+)?flight\b.+\b(?:july|january|february|march|april|may|june|"
    r"august|september|october|november|december|\d{1,2})\b",
    re.I,
)
_MY_X_IS_Y = re.compile(
    r"\bmy\s+(\w+)\s+is\s+([A-Za-z][\w\s'-]{2,60})",
    re.I,
)

_ACTION_PATTERN = re.compile(
    r"\b(?:remind me to|(?:i will|i'll|need to)\s+"
    r"(?:call|email|send|submit|finish|complete|do|buy|pay|book|schedule|"
    r"remind|clean|pick\s+up|drop\s+off)\b)\s*(.+)",
    re.I,
)


class EpisodeConsolidationPipeline:
    def __init__(self, store: Optional[EpisodeStore] = None):
        self.store = store or EpisodeStore()

    def process_episode(self, episode_id: str) -> Tuple[StructuredEpisode, List[MemoryCandidate]]:
        segments = self.store.get_raw_segments(episode_id)
        if not segments:
            raise ValueError(f"No transcript segments for episode {episode_id}")

        self.store.mark_episode_ended(episode_id)
        structured = self._build_structured(episode_id, segments)
        raw_candidates = self._extract_candidates(episode_id, segments)
        accepted = self.store.get_accepted_memories(limit=200)
        candidates = annotate_and_rank_candidates(raw_candidates, accepted_memories=accepted)
        self.store.save_structured_episode(structured)
        self.store.save_candidates(candidates)
        return structured, candidates

    def _build_structured(
        self, episode_id: str, segments: List[TranscriptSegment]
    ) -> StructuredEpisode:
        user_lines = [s.text for s in segments if s.is_user and s.text.strip()]
        all_text = " ".join(s.text for s in segments if s.text.strip())
        title = self._title_from_text(user_lines or [all_text])
        summary = self._summary_from_segments(segments)
        action_items = self._action_items(segments)
        events = self._events(segments)
        started = segments[0].started_at
        ended = segments[-1].ended_at or segments[-1].started_at
        session_id = self._session_id_for(episode_id)

        return StructuredEpisode(
            episode_id=episode_id,
            session_id=session_id,
            lifecycle_state=EpisodeLifecycleState.COMPLETED.value,
            title=title,
            summary=summary,
            action_items=action_items,
            events=events,
            segment_count=len(segments),
            started_at=started,
            ended_at=ended,
            metadata={"source": "brain_v2_consolidation"},
        )

    def _session_id_for(self, episode_id: str) -> str:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT session_id FROM raw_episodes WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
        return row["session_id"] if row else episode_id

    def _title_from_text(self, lines: List[str]) -> str:
        first = (lines[0] if lines else "").strip()
        if not first:
            return "Conversation"
        if len(first) <= 72:
            return first
        return first[:69].rstrip() + "..."

    def _summary_from_segments(self, segments: List[TranscriptSegment]) -> str:
        parts = []
        for seg in segments[-6:]:
            role = "User" if seg.is_user else seg.speaker_label.title()
            text = seg.text.strip()
            if text:
                parts.append(f"{role}: {text[:120]}")
        return " | ".join(parts) if parts else "Session with no transcript text."

    def _action_items(self, segments: List[TranscriptSegment]) -> List[str]:
        items = []
        for seg in segments:
            if not seg.is_user:
                continue
            m = _ACTION_PATTERN.search(seg.text)
            if m:
                item = m.group(1).strip().rstrip(".")
                if item and item not in items:
                    items.append(item)
        return items

    def _events(self, segments: List[TranscriptSegment]) -> List[str]:
        events = []
        for seg in segments:
            if seg.is_user and re.search(
                r"\b(flight|meeting|appointment|calendar)\b", seg.text, re.I
            ):
                events.append(seg.text.strip()[:200])
        return events[:5]

    def _extract_candidates(
        self, episode_id: str, segments: List[TranscriptSegment]
    ) -> List[MemoryCandidate]:
        seen: set[str] = set()
        candidates: List[MemoryCandidate] = []

        for seg in segments:
            if not seg.is_user:
                continue
            if (seg.metadata or {}).get("skip_candidate_extraction"):
                continue
            text = seg.text.strip()
            if not text:
                continue

            if is_temporary_speaker_intro(text) or is_speaker_context_reset(text):
                continue

            for statement, ctype, conf, extra in self._extract_from_segment(text):
                if ctype == "current_location":
                    continue
                key = statement.lower()[:140]
                if key in seen:
                    continue
                seen.add(key)

                stored, verdict = apply_quality_gate(
                    statement,
                    candidate_type=ctype,
                    is_user=True,
                    explicit_remember=bool((extra or {}).get("explicit_remember")),
                )
                if not stored:
                    continue

                meta = {
                    "extractor": "rule_v2",
                    "extraction_policy_version": EXTRACTION_POLICY_VERSION,
                    **verdict.to_metadata(),
                    **(extra or {}),
                }
                salience = min(1.0, conf + 0.05)
                confidence = conf
                if verdict.label == QUALITY_WEAK:
                    confidence = max(0.25, conf * 0.65)
                    salience = max(0.25, salience * 0.7)

                candidates.append(
                    MemoryCandidate(
                        candidate_id=str(uuid.uuid4()),
                        episode_id=episode_id,
                        statement=stored,
                        candidate_type=ctype,
                        confidence=confidence,
                        salience=salience,
                        source_segment_ids=[seg.segment_id],
                        metadata=meta,
                    )
                )
        return candidates

    def extract_declaration_statements(
        self, text: str
    ) -> List[Tuple[str, str, float, Optional[dict]]]:
        """Rule-based extraction for a single owner disclosure (no episode required)."""
        return self._extract_from_segment(text)

    def _extract_from_segment(
        self, text: str
    ) -> List[Tuple[str, str, float, Optional[dict]]]:
        """Return list of (statement, type, confidence, extra_meta)."""
        from core.brain_statements import is_task_or_action_statement

        if is_task_or_action_statement(text):
            return []

        found: List[Tuple[str, str, float, Optional[dict]]] = []

        m = _REMEMBER.search(text)
        if m:
            content = (m.group(1) or "").strip().rstrip(".")
            statement = content if len(content) >= 8 else text
            inferred = infer_memory_type(statement, explicit_remember=True)
            extra = {"explicit_remember": True, **inferred.metadata}
            found.append(
                (statement, inferred.candidate_type, inferred.confidence, extra)
            )

        m_call = _CALL_ME.search(text)
        if m_call:
            name = m_call.group(1).strip().title()
            stmt = f"My preferred name is {name}."
            identity_meta = {"preferred_name": name}
            m_declared = _IDENTITY.search(text)
            if m_declared:
                declared = re.split(
                    r"\s+(?:but|and)\s+(?:you\s+can\s+|u\s+can\s+)?call\s+me\b|[,.;!?]",
                    m_declared.group(1).strip(),
                    maxsplit=1,
                    flags=re.I,
                )[0].strip()
                if declared:
                    identity_meta["legal_name"] = " ".join(
                        piece.capitalize() for piece in declared.split()
                    )
            found.append(
                (
                    stmt,
                    "identity",
                    0.86,
                    identity_meta,
                )
            )

        for pattern, ctype, conf, builder in (
            (_IDENTITY, "identity", 0.86, lambda t, m: t),
            (_LOCATION, "location", 0.84, lambda t, m: f"I live in {m.group(1).strip().title()}."),
            (_PREFER, "preference", 0.82, lambda t, m: f"I prefer {m.group(1).strip().rstrip('.')}."),
            (_DISLIKE, "preference", 0.8, lambda t, m: f"I don't like {m.group(1).strip().rstrip('.')}."),
            (
                _STUDY_WORK,
                "education",
                0.8,
                lambda t, m: (
                    normalize_user_education_statement(t)[0]
                    if normalize_user_education_statement(t)
                    else f"I {m.group(1)} at {m.group(2).strip().title()}."
                ),
            ),
            (_HIKARI_DECISION, "decision", 0.9, lambda t, m: t.strip()),
            (_TRAVEL, "travel", 0.8, lambda t, m: t.strip()),
        ):
            m = pattern.search(text)
            if m:
                stmt = builder(text, m)
                inferred = infer_memory_type(stmt)
                extra = dict(inferred.metadata) if inferred.metadata else None
                found.append(
                    (
                        stmt,
                        inferred.candidate_type if inferred.confidence >= 0.7 else ctype,
                        max(conf, inferred.confidence),
                        extra,
                    )
                )

        m = _RELATION.search(text)
        if m:
            stmt = text.strip()
            inferred = infer_memory_type(stmt)
            extra = {"relation": m.group(1).lower(), **(inferred.metadata or {})}
            found.append(
                (
                    stmt,
                    inferred.candidate_type,
                    max(0.85, inferred.confidence),
                    extra,
                )
            )

        m = _MY_X_IS_Y.search(text)
        if m and m.group(1).lower() not in ("name", "dad", "mom"):
            found.append((text.strip(), "fact", 0.72, None))

        if not found and len(text) >= 12:
            inferred = infer_memory_type(text)
            extra = dict(inferred.metadata) if inferred.metadata else None
            found.append(
                (text.strip(), inferred.candidate_type, inferred.confidence, extra)
            )

        identity = extract_owner_identity_names(text)
        if identity.get("legal_name") or identity.get("preferred_name"):
            parts: List[str] = []
            if identity.get("legal_name"):
                parts.append(f"My legal name is {identity['legal_name']}.")
            if identity.get("preferred_name"):
                parts.append(f"My preferred name is {identity['preferred_name']}.")
            found = [
                (" ".join(parts), "identity", 0.88, dict(identity))
            ] + [item for item in found if item[1] != "identity"]

        return self._dedupe_extractions(found)

    def _dedupe_extractions(
        self, found: List[Tuple[str, str, float, Optional[dict]]]
    ) -> List[Tuple[str, str, float, Optional[dict]]]:
        """Drop same-segment duplicates (e.g. remember + prefer on one sentence)."""
        seen_norms: set[str] = set()
        deduped: List[Tuple[str, str, float, Optional[dict]]] = []
        for statement, ctype, conf, extra in found:
            norm = normalize_statement(statement)
            if not norm or norm in seen_norms:
                continue
            seen_norms.add(norm)
            deduped.append((statement, ctype, conf, extra))
        return deduped
