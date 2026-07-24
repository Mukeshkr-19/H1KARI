"""Review gate — only accepted candidates become durable source-linked memory."""

from __future__ import annotations

import uuid
from typing import Optional

from core.brain_v2.candidate_scoring import find_accepted_duplicate, normalize_statement
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.schemas import (
    MemoryCandidate,
    MemoryCandidateStatus,
    MemoryLayer,
    SourceLinkedMemory,
)

_STRUCTURED_META_KEYS = (
    "person",
    "relation",
    "organization",
    "location",
    "place",
    "date_text",
    "preferred_name",
    "legal_name",
    "official_name",
    "preference_kind",
    "preference_value",
    "explicit_remember",
    "auto_trusted_owner_assertion",
    "auto_trust_policy",
)


class MemoryReviewGate:
    def __init__(self, store: Optional[EpisodeStore] = None):
        self.store = store or EpisodeStore()

    def accept(self, candidate_id: str, *, layer: str = MemoryLayer.SEMANTIC.value) -> SourceLinkedMemory:
        cand = self.store.get_candidate(candidate_id)
        if not cand:
            raise KeyError(f"Memory candidate not found: {candidate_id}")

        if cand.review_status == MemoryCandidateStatus.ACCEPTED.value:
            for mem in self.store.get_accepted_memories(limit=200):
                if mem.candidate_id == cand.candidate_id:
                    return mem

        accepted = self.store.get_active_accepted_memories(limit=200)
        existing = find_accepted_duplicate(cand.statement, accepted)
        if existing:
            self.store.update_candidate_status(
                cand.candidate_id, MemoryCandidateStatus.ACCEPTED
            )
            meta = dict(cand.metadata or {})
            meta["merged_into_existing"] = True
            meta["merged_memory_id"] = existing.memory_id
            meta["normalized_statement"] = normalize_statement(cand.statement)
            self.store.update_candidate_metadata(cand.candidate_id, meta)
            merged_meta = dict(existing.metadata or {})
            merged_meta["duplicate_merge_count"] = int(
                merged_meta.get("duplicate_merge_count", 1)
            ) + 1
            merged_segments = list(existing.source_segment_ids or [])
            for sid in cand.source_segment_ids or []:
                if sid not in merged_segments:
                    merged_segments.append(sid)
            updated = SourceLinkedMemory(
                memory_id=existing.memory_id,
                candidate_id=existing.candidate_id,
                episode_id=existing.episode_id,
                statement=existing.statement,
                source_segment_ids=merged_segments,
                neural_node_key=existing.neural_node_key,
                accepted_at=existing.accepted_at,
                layer=existing.layer,
                metadata=merged_meta,
            )
            return self.store.save_source_linked_memory(updated)

        self.store.update_candidate_status(cand.candidate_id, MemoryCandidateStatus.ACCEPTED)
        linked_meta: dict = {
            "candidate_type": cand.candidate_type,
            "confidence": cand.confidence,
            "salience": cand.salience,
            "rank_score": (cand.metadata or {}).get("rank_score"),
            "normalized_statement": normalize_statement(cand.statement),
        }
        for key in _STRUCTURED_META_KEYS:
            val = (cand.metadata or {}).get(key)
            if val is not None and val != "":
                linked_meta[key] = val

        linked = SourceLinkedMemory(
            memory_id=str(uuid.uuid4()),
            candidate_id=cand.candidate_id,
            episode_id=cand.episode_id,
            statement=cand.statement,
            source_segment_ids=list(cand.source_segment_ids),
            layer=layer,
            metadata=linked_meta,
        )
        return self.store.save_source_linked_memory(linked)

    def reject(self, candidate_id: str) -> MemoryCandidate:
        cand = self.store.get_candidate(candidate_id)
        if not cand:
            raise KeyError(f"Memory candidate not found: {candidate_id}")
        resolved_id = cand.candidate_id
        self.store.update_candidate_status(resolved_id, MemoryCandidateStatus.REJECTED)
        updated = self.store.get_candidate(resolved_id)
        return updated or cand

    def pending_for_episode(self, episode_id: str) -> list[MemoryCandidate]:
        return self.store.get_candidates(
            episode_id=episode_id, status=MemoryCandidateStatus.PENDING
        )
