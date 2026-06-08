"""Reviewed-memory correction lifecycle (retire / supersede / metadata edit)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_lifecycle import (
    LIFECYCLE_RETIRED,
    append_audit_entry,
    is_active_memory,
    lifecycle_status,
)
from core.brain_v2.schemas import SourceLinkedMemory


class MemoryRepairGate:
    """Non-destructive corrections for accepted source-linked memories."""

    def __init__(self, store: Optional[EpisodeStore] = None):
        self.store = store or EpisodeStore()

    def _require_active(self, memory: SourceLinkedMemory) -> None:
        if not is_active_memory(memory):
            raise ValueError(
                f"Memory {memory.memory_id} is not active (status={lifecycle_status(memory.metadata)})."
            )

    def retire(
        self,
        memory_id: str,
        *,
        reason: str = "retired_by_operator",
    ) -> SourceLinkedMemory:
        resolved_id = self.store.resolve_source_linked_memory_id(memory_id)
        memory = self.store.get_source_linked_memory(resolved_id)
        if not memory:
            raise KeyError(f"Accepted memory not found: {memory_id}")
        self._require_active(memory)
        meta = append_audit_entry(
            dict(memory.metadata or {}),
            "retire",
            reason=reason,
            prior_statement=memory.statement,
        )
        meta["lifecycle_status"] = LIFECYCLE_RETIRED
        meta["retired_at"] = meta["correction_audit"][-1]["at"]
        updated = SourceLinkedMemory(
            memory_id=memory.memory_id,
            candidate_id=memory.candidate_id,
            episode_id=memory.episode_id,
            statement=memory.statement,
            source_segment_ids=list(memory.source_segment_ids or []),
            neural_node_key=memory.neural_node_key,
            accepted_at=memory.accepted_at,
            layer=memory.layer,
            metadata=meta,
        )
        return self.store.save_source_linked_memory(updated)

    def supersede(
        self,
        memory_id: str,
        *,
        statement: str,
        candidate_type: Optional[str] = None,
        layer: Optional[str] = None,
        reason: str = "superseded_by_operator",
    ) -> Tuple[SourceLinkedMemory, SourceLinkedMemory]:
        resolved_id = self.store.resolve_source_linked_memory_id(memory_id)
        return self.store.atomic_supersede_accepted_memory(
            resolved_id,
            new_statement=statement,
            candidate_type=candidate_type,
            layer=layer,
            reason=reason,
        )

    def edit_metadata(
        self,
        memory_id: str,
        *,
        candidate_type: Optional[str] = None,
        layer: Optional[str] = None,
        metadata_updates: Optional[Dict[str, Any]] = None,
        reason: str = "metadata_edit",
    ) -> SourceLinkedMemory:
        resolved_id = self.store.resolve_source_linked_memory_id(memory_id)
        memory = self.store.get_source_linked_memory(resolved_id)
        if not memory:
            raise KeyError(f"Accepted memory not found: {memory_id}")
        self._require_active(memory)
        meta = append_audit_entry(dict(memory.metadata or {}), "edit_metadata", reason=reason)
        if candidate_type is not None:
            meta["candidate_type"] = candidate_type
        if layer is not None:
            pass
        for key, val in (metadata_updates or {}).items():
            if key in ("statement", "lifecycle_status", "superseded_by", "supersedes"):
                raise ValueError(f"Cannot edit protected field via metadata: {key}")
            meta[key] = val
        updated = SourceLinkedMemory(
            memory_id=memory.memory_id,
            candidate_id=memory.candidate_id,
            episode_id=memory.episode_id,
            statement=memory.statement,
            source_segment_ids=list(memory.source_segment_ids or []),
            neural_node_key=memory.neural_node_key,
            accepted_at=memory.accepted_at,
            layer=layer or memory.layer,
            metadata=meta,
        )
        return self.store.save_source_linked_memory(updated)

    def _chain_neighbors(self, mem: SourceLinkedMemory) -> List[SourceLinkedMemory]:
        neighbors: List[SourceLinkedMemory] = []
        meta = mem.metadata or {}
        for key in ("supersedes", "superseded_from", "superseded_by"):
            linked_id = meta.get(key)
            if not linked_id:
                continue
            found = self.store.get_source_linked_memory(str(linked_id))
            if found and found.memory_id != mem.memory_id:
                neighbors.append(found)
        for other in self.store.get_accepted_memories(limit=500):
            ometa = other.metadata or {}
            if ometa.get("supersedes") == mem.memory_id or ometa.get("superseded_by") == mem.memory_id:
                if other.memory_id != mem.memory_id:
                    neighbors.append(other)
        return neighbors

    def memory_history(self, memory_id: str) -> List[SourceLinkedMemory]:
        """Return full multi-hop correction chain newest-first from any member id."""
        resolved_id = self.store.resolve_source_linked_memory_id(memory_id)
        resolved = self.store.get_source_linked_memory(resolved_id)
        if not resolved:
            raise KeyError(f"Accepted memory not found: {memory_id}")

        members: Dict[str, SourceLinkedMemory] = {}
        queue: List[SourceLinkedMemory] = [resolved]
        seen: set = set()

        while queue:
            cursor = queue.pop(0)
            if cursor.memory_id in seen:
                continue
            seen.add(cursor.memory_id)
            members[cursor.memory_id] = cursor
            for neighbor in self._chain_neighbors(cursor):
                if neighbor.memory_id not in seen:
                    queue.append(neighbor)

        return sorted(
            members.values(),
            key=lambda m: m.accepted_at or "",
            reverse=True,
        )
