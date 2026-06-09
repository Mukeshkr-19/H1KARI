"""Brain v2 coordinator — episode intake, consolidation, review, retrieval."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.candidate_scoring import normalize_statement
from core.brain_v2.candidate_quality import apply_quality_gate
from core.brain_v2.location_phrases import normalize_declared_place
from core.brain_v2.memory_type import infer_memory_type
from core.brain_v2.owner_auto_trust import (
    is_explicit_remember_command,
    is_owner_scoped_auto_trust_candidate,
    pick_trusted_owner_candidate,
)
from core.brain_v2.durable_memory import DurableMemoryPromoter
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_repair import MemoryRepairGate
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.brain_v2.retrieval import BrainV2ContextPacket, BrainV2Retrieval
from core.brain_v2.schemas import (
    MemoryCandidate,
    MemoryCandidateStatus,
    SourceLinkedMemory,
    StructuredEpisode,
)
from core.brain_v2.working_memory import WorkingMemory
from core.speaker_context import is_temporary_speaker_intro


class BrainV2Coordinator:
    """Local-first Brain v2 entry point used by orchestrator and doctor."""

    def __init__(
        self,
        store: Optional[EpisodeStore] = None,
        neural_bridge=None,
        *,
        allow_neural_procedural: bool = True,
        allow_neural_conflict_reads: bool = True,
    ):
        self.store = store or EpisodeStore()
        self.working = WorkingMemory()
        self.consolidation = EpisodeConsolidationPipeline(self.store)
        self.review_gate = MemoryReviewGate(self.store)
        self.repair_gate = MemoryRepairGate(self.store)
        self.promoter = DurableMemoryPromoter(self.store, neural_bridge)
        self.retrieval = BrainV2Retrieval(
            self.store,
            self.working,
            neural_bridge,
            allow_neural_procedural=allow_neural_procedural,
            allow_neural_conflict_reads=allow_neural_conflict_reads,
        )
        self._session_episodes: Dict[str, str] = {}
        self._turns_since_consolidation: Dict[str, int] = {}
        self.consolidate_every_n_turns = 8

    def start_session(self, session_id: Optional[str] = None) -> str:
        sid = session_id or str(uuid.uuid4())
        self.working.set_session(sid)
        self._session_episodes.pop(sid, None)
        self._turns_since_consolidation.pop(sid, None)
        return sid

    def ensure_episode(self, session_id: str) -> str:
        if session_id in self._session_episodes:
            return self._session_episodes[session_id]
        episode_id = self.store.create_episode(session_id)
        self._session_episodes[session_id] = episode_id
        return episode_id

    def record_turn(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str = "",
        *,
        speaker_label: str = "user",
        metadata: Optional[dict] = None,
    ) -> str:
        episode_id = self.ensure_episode(session_id)
        self.store.add_turn(
            episode_id,
            user_text,
            is_user=True,
            speaker_label=speaker_label,
            metadata=metadata,
        )
        if assistant_text:
            self.store.add_turn(
                episode_id,
                assistant_text,
                is_user=False,
                speaker_label="assistant",
                metadata=metadata,
            )
        self.working.push_turn(user_text, assistant_text, session_id=session_id)
        inferred = infer_memory_type(user_text)
        if inferred.candidate_type == "current_location":
            loc = inferred.metadata.get("current_location")
            place = normalize_declared_place(str(loc or ""))
            if place:
                self.working.note_current_location(place, user_text)
        elif (
            (metadata or {}).get("session_speaker_intro")
            or is_temporary_speaker_intro(user_text)
        ):
            pass
        turns = self._turns_since_consolidation.get(session_id, 0) + 1
        self._turns_since_consolidation[session_id] = turns
        if turns >= self.consolidate_every_n_turns:
            self._consolidate_active_episode(session_id)
        return episode_id

    def ingest_trusted_owner_declaration(
        self,
        session_id: str,
        user_text: str,
    ) -> Dict[str, Any]:
        """Learn clear owner self-facts immediately, without touching legacy neural memory.

        Only owner-scoped, low-ambiguity types are auto-accepted. A contradictory
        singleton fact is retained as pending rather than silently replacing
        reviewed truth.
        """
        episode_id = self.store.create_episode(f"{session_id}:owner-disclosure")
        self.store.add_turn(episode_id, user_text, is_user=True, speaker_label="user")
        _structured, candidates = self.consolidation.process_episode(episode_id)
        candidate = pick_trusted_owner_candidate(candidates, user_text)
        if not candidate:
            candidate = self._build_fallback_owner_candidate(
                episode_id, user_text, candidates
            )
        if not candidate:
            return {"status": "not_candidate"} if not candidates else {"status": "pending_review"}

        singleton_types = {"identity", "location", "education"}

        active_same_type = [
            memory
            for memory in self.store.get_active_accepted_memories(limit=500)
            if str((memory.metadata or {}).get("candidate_type", "fact"))
            == candidate.candidate_type
        ]
        candidate_norm = normalize_statement(candidate.statement)
        has_exact_active = any(
            normalize_statement(memory.statement) == candidate_norm
            for memory in active_same_type
        )
        preferred_name = str((candidate.metadata or {}).get("preferred_name") or "").strip()
        legal_name = str((candidate.metadata or {}).get("legal_name") or "").strip()
        alias_identity_update = (
            candidate.candidate_type == "identity" and bool(preferred_name)
        )
        identity_legal_correction = (
            candidate.candidate_type == "identity" and bool(legal_name)
        )
        if (
            candidate.candidate_type in singleton_types
            and active_same_type
            and not has_exact_active
            and not alias_identity_update
            and not identity_legal_correction
        ):
            return {
                "status": "pending_conflict",
                "candidate_type": candidate.candidate_type,
            }

        if identity_legal_correction and active_same_type and not has_exact_active:
            for mem in active_same_type:
                self.retire_accepted_memory(
                    mem.memory_id,
                    reason="identity_legal_correction",
                )

        trusted_meta = dict(candidate.metadata or {})
        trusted_meta["auto_trusted_owner_assertion"] = True
        trusted_meta["auto_trust_policy"] = "owner_self_disclosure_v1"
        self.store.update_candidate_metadata(candidate.candidate_id, trusted_meta)

        linked: Optional[SourceLinkedMemory] = None
        same_pending = [
            pending
            for pending in self.store.get_candidates(status=MemoryCandidateStatus.PENDING)
            if pending.candidate_type == candidate.candidate_type
            and normalize_statement(pending.statement) == candidate_norm
        ]
        for pending in same_pending:
            linked, _promo = self.accept_candidate(pending.candidate_id, promote=False)
        if not linked:
            if not any(
                c.candidate_id == candidate.candidate_id
                for c in self.store.get_candidates(status=MemoryCandidateStatus.PENDING)
            ):
                self.store.save_candidates([candidate])
            linked, _promo = self.accept_candidate(candidate.candidate_id, promote=False)
        return {
            "status": "accepted",
            "candidate_type": candidate.candidate_type,
            "memory": linked,
        }

    def _build_fallback_owner_candidate(
        self,
        episode_id: str,
        user_text: str,
        existing: List[MemoryCandidate],
    ) -> Optional[MemoryCandidate]:
        """Build a trustable candidate when consolidation did not surface one."""
        if pick_trusted_owner_candidate(existing, user_text):
            return None
        segments = self.store.get_raw_segments(episode_id)
        segment_id = segments[-1].segment_id if segments else ""
        explicit = is_explicit_remember_command(user_text)
        for statement, ctype, conf, extra in self.consolidation.extract_declaration_statements(
            user_text
        ):
            stored, verdict = apply_quality_gate(
                statement,
                candidate_type=ctype,
                is_user=True,
                explicit_remember=explicit or bool((extra or {}).get("explicit_remember")),
            )
            if not stored:
                continue
            meta = {
                "extractor": "owner_disclosure_fallback",
                **verdict.to_metadata(),
                **(extra or {}),
            }
            candidate = MemoryCandidate(
                candidate_id=str(uuid.uuid4()),
                episode_id=episode_id,
                statement=stored,
                candidate_type=ctype,
                confidence=conf,
                salience=min(1.0, conf + 0.05),
                source_segment_ids=[segment_id] if segment_id else [],
                metadata=meta,
            )
            if is_owner_scoped_auto_trust_candidate(candidate, user_text):
                return candidate
        return None

    def _consolidate_active_episode(self, session_id: str) -> None:
        episode_id = self._session_episodes.get(session_id)
        if not episode_id or self.store.count_raw_segments(episode_id) < 2:
            return
        try:
            self.consolidation.process_episode(episode_id)
            self._turns_since_consolidation[session_id] = 0
            self._session_episodes[session_id] = self.store.create_episode(session_id)
        except Exception:
            pass

    def close_and_consolidate(self, session_id: str) -> Tuple[StructuredEpisode, List[MemoryCandidate]]:
        episode_id = self._session_episodes.pop(session_id, None)
        if not episode_id:
            raise KeyError(f"No active episode for session {session_id}")
        return self.consolidation.process_episode(episode_id)

    def accept_candidate(
        self, candidate_id: str, *, promote: bool = False
    ) -> tuple[SourceLinkedMemory, Optional[str]]:
        linked = self.review_gate.accept(candidate_id)
        promo_key: Optional[str] = None
        if promote:
            promo_key = self.promoter.promote(linked)
            for mem in self.store.get_accepted_memories(limit=500):
                if mem.memory_id == linked.memory_id:
                    linked = mem
                    break
        return linked, promo_key

    def reject_candidate(self, candidate_id: str) -> MemoryCandidate:
        return self.review_gate.reject(candidate_id)

    def build_context_packet(
        self,
        query: str,
        *,
        speaker_context: Optional[Dict[str, str]] = None,
        task_context: Optional[str] = None,
    ) -> BrainV2ContextPacket:
        return self.retrieval.retrieve(
            query,
            speaker_context=speaker_context,
            task_context=task_context,
        )

    def build_profile_summary_context(self, query: str, *, limit: int = 8) -> str:
        return self.retrieval.build_profile_summary_context(query, limit=limit)

    def build_user_profile_answer(
        self, *, neural_summary: Optional[str] = None
    ) -> Optional[str]:
        """Reviewed Brain v2 profile; legacy neural personal lines quarantined unless opt-in."""
        from core.brain_v2.profile_summary import build_merged_user_profile_answer

        accepted = self.store.get_active_accepted_memories(limit=200)
        session_current = self.working.get_current_location()
        return build_merged_user_profile_answer(
            accepted,
            None,
            session_current=session_current,
        )

    def try_answer_from_accepted_memories(self, query: str) -> Optional[str]:
        """Answer from accepted Brain v2 memories only; None if not a recall question."""
        try:
            return self.retrieval.answer_from_accepted(query)
        except Exception:
            return None

    def consolidate_pending_episodes(self, *, min_segments: int = 2) -> Dict[str, int]:
        """Consolidate raw in-progress episodes (recovery / maintenance)."""
        summary = {"episodes": 0, "candidates": 0, "skipped": 0, "errors": 0}
        for episode_id in self.store.list_unconsolidated_episode_ids(
            min_segments=min_segments
        ):
            if self.store.get_structured_episode(episode_id):
                summary["skipped"] += 1
                continue
            try:
                _structured, candidates = self.consolidation.process_episode(episode_id)
                summary["episodes"] += 1
                summary["candidates"] += len(candidates)
            except Exception:
                summary["errors"] += 1
        return summary

    def build_prompt_context(self, query: str, **kwargs: Any) -> str:
        from core.brain_v2.recall_intent import INTENT_PROFILE_SUMMARY, classify_recall_intent

        intent = classify_recall_intent(query)
        limit = int(kwargs.get("limit", 8))
        parts: List[str] = []
        if intent == INTENT_PROFILE_SUMMARY:
            profile = self.build_profile_summary_context(query, limit=limit)
            if profile:
                parts.append(profile)
        packet = self.build_context_packet(query, **kwargs)
        if packet.to_prompt():
            parts.append(packet.to_prompt())
        return "\n".join(p for p in parts if p)

    def retag_accepted_memories(self) -> Dict[str, int]:
        """Re-infer candidate_type/metadata for accepted memories (metadata only)."""
        counts = {"updated": 0, "unchanged": 0}
        for mem in self.store.get_accepted_memories(limit=500):
            inferred = infer_memory_type(mem.statement)
            meta = dict(mem.metadata or {})
            changed = False
            new_type = inferred.candidate_type
            if meta.get("candidate_type") != new_type:
                meta["candidate_type"] = new_type
                changed = True
            for key, val in inferred.metadata.items():
                if key == "explicit_remember":
                    continue
                if meta.get(key) != val:
                    meta[key] = val
                    changed = True
            if changed:
                counts["updated"] += 1
                self.store.save_source_linked_memory(
                    SourceLinkedMemory(
                        memory_id=mem.memory_id,
                        candidate_id=mem.candidate_id,
                        episode_id=mem.episode_id,
                        statement=mem.statement,
                        source_segment_ids=list(mem.source_segment_ids or []),
                        neural_node_key=mem.neural_node_key,
                        accepted_at=mem.accepted_at,
                        layer=mem.layer,
                        metadata=meta,
                    )
                )
            else:
                counts["unchanged"] += 1
        return counts

    def status_summary(self) -> Dict[str, Any]:
        from core.brain_v2.status import collect_brain_v2_status

        stats = collect_brain_v2_status(self.store.db_path)
        stats["active_session"] = self.working.active_session_id
        return stats

    def retire_accepted_memory(self, memory_id: str, *, reason: str = "retired_by_operator"):
        return self.repair_gate.retire(memory_id, reason=reason)

    def supersede_accepted_memory(
        self,
        memory_id: str,
        *,
        statement: str,
        candidate_type: Optional[str] = None,
        reason: str = "superseded_by_operator",
    ):
        return self.repair_gate.supersede(
            memory_id,
            statement=statement,
            candidate_type=candidate_type,
            reason=reason,
        )

    def edit_accepted_memory_metadata(
        self,
        memory_id: str,
        *,
        candidate_type: Optional[str] = None,
        metadata_updates: Optional[dict] = None,
    ):
        return self.repair_gate.edit_metadata(
            memory_id,
            candidate_type=candidate_type,
            metadata_updates=metadata_updates,
        )

    def accepted_memory_history(self, memory_id: str):
        return self.repair_gate.memory_history(memory_id)
