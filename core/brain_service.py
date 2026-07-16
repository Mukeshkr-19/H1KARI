"""Actor-aware boundary around the existing Brain v2 coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.action_policy import Actor, ActorContext, validate_actor_context
from core.brain_v2.coordinator import BrainV2Coordinator


@dataclass(frozen=True)
class ReviewedMemoryHit:
    text: str
    score: float
    memory_id: str
    candidate_id: str
    episode_id: str
    source_segment_ids: tuple[str, ...]
    predecessor_memory_ids: tuple[str, ...]
    predecessor_evidence_segment_ids: tuple[str, ...]
    correction_actions: tuple[str, ...]


class BrainService:
    def __init__(self, coordinator: Optional[BrainV2Coordinator] = None):
        self._coordinator = coordinator

    @staticmethod
    def _require_owner(actor: ActorContext) -> None:
        valid, _reason = validate_actor_context(actor)
        if not valid or actor.actor is not Actor.OWNER:
            raise PermissionError("owner Brain v2 access required")

    def _for_owner(self, actor: ActorContext) -> BrainV2Coordinator:
        self._require_owner(actor)
        if self._coordinator is None:
            self._coordinator = BrainV2Coordinator()
        return self._coordinator

    def initialize_owner(
        self, actor: ActorContext, **coordinator_options: Any
    ) -> BrainV2Coordinator:
        """Initialize the live coordinator only after an owner check."""
        self._require_owner(actor)
        if self._coordinator is None:
            self._coordinator = BrainV2Coordinator(**coordinator_options)
        return self._coordinator

    def owns(self, coordinator: object) -> bool:
        return self._coordinator is coordinator

    def record_turn(
        self,
        actor: ActorContext,
        session_id: str,
        user_text: str,
        assistant_text: str = "",
        *,
        metadata: Optional[dict] = None,
        speaker_label: Optional[str] = None,
    ) -> str:
        coordinator = self._for_owner(actor)
        if session_id != actor.session_id:
            raise PermissionError("Brain v2 session does not match actor context")
        safe_metadata = dict(metadata or {})
        safe_metadata.update({"actor_id": actor.actor_id, "actor": actor.actor.value})
        return coordinator.record_turn(
            session_id,
            user_text,
            assistant_text,
            speaker_label=speaker_label or actor.actor_id,
            metadata=safe_metadata,
        )

    def recall_reviewed(
        self, actor: ActorContext, query: str, *, limit: int = 8
    ) -> tuple[ReviewedMemoryHit, ...]:
        coordinator = self._for_owner(actor)
        packet = coordinator.build_context_packet(query)
        active = {
            memory.memory_id: memory
            for memory in coordinator.store.get_active_accepted_memories(limit=500)
        }
        results: list[ReviewedMemoryHit] = []
        for hit in packet.hits:
            if hit.source != "source_linked":
                continue
            memory_id = str((hit.metadata or {}).get("memory_id") or "")
            memory = active.get(memory_id)
            if memory is None:
                continue
            metadata = dict(getattr(memory, "metadata", None) or {})
            predecessor_ids = tuple(
                dict.fromkeys(
                    str(metadata[key])
                    for key in ("supersedes", "superseded_from")
                    if metadata.get(key)
                )
            )
            predecessor_evidence_ids = tuple(
                str(segment_id)
                for segment_id in metadata.get(
                    "predecessor_evidence_segment_ids", ()
                )
                if segment_id
            )
            if predecessor_ids and not predecessor_evidence_ids:
                continue
            correction_actions = tuple(
                str(entry.get("action"))
                for entry in metadata.get("correction_audit", ())
                if isinstance(entry, dict) and entry.get("action")
            )
            results.append(
                ReviewedMemoryHit(
                    text=memory.statement,
                    score=hit.score,
                    memory_id=memory.memory_id,
                    candidate_id=memory.candidate_id,
                    episode_id=memory.episode_id,
                    source_segment_ids=tuple(memory.source_segment_ids or ()),
                    predecessor_memory_ids=predecessor_ids,
                    predecessor_evidence_segment_ids=predecessor_evidence_ids,
                    correction_actions=correction_actions,
                )
            )
            if len(results) >= limit:
                break
        return tuple(results)

    def accept_candidate(self, actor: ActorContext, candidate_id: str) -> Any:
        return self._for_owner(actor).accept_candidate(candidate_id)

    def reject_candidate(self, actor: ActorContext, candidate_id: str) -> Any:
        return self._for_owner(actor).reject_candidate(candidate_id)

    def retire_memory(self, actor: ActorContext, memory_id: str, *, reason: str) -> Any:
        return self._for_owner(actor).retire_accepted_memory(memory_id, reason=reason)

    def supersede_memory(
        self,
        actor: ActorContext,
        memory_id: str,
        *,
        statement: str,
        candidate_type: Optional[str] = None,
        reason: str,
    ) -> Any:
        return self._for_owner(actor).supersede_accepted_memory(
            memory_id,
            statement=statement,
            candidate_type=candidate_type,
            reason=reason,
        )
