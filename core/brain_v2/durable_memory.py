"""Promote accepted source-linked memories into neural durable storage."""

from __future__ import annotations

import logging
from typing import Optional

from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.schemas import SourceLinkedMemory

logger = logging.getLogger(__name__)


class DurableMemoryPromoter:
    """Writes accepted memories to the existing neural memory bridge (local SQLite brain)."""

    def __init__(self, store: Optional[EpisodeStore] = None, neural_bridge=None):
        self.store = store or EpisodeStore()
        self._bridge = neural_bridge

    def _bridge_module(self):
        if self._bridge is not None:
            return self._bridge
        from core import neural_memory_bridge

        return neural_memory_bridge

    def promote(self, linked: SourceLinkedMemory) -> Optional[str]:
        """Store statement in neural memory; return node key hint if successful."""
        bridge = self._bridge_module()
        try:
            if not bridge.init_neural_memory():
                return None
            result = bridge.learn_from_text(
                linked.statement,
                user_id=linked.metadata.get("user_id"),
            )
            if not result or not result.get("success", True):
                return None
            node_key = f"brain_v2:{linked.memory_id}"
            updated = SourceLinkedMemory(
                memory_id=linked.memory_id,
                candidate_id=linked.candidate_id,
                episode_id=linked.episode_id,
                statement=linked.statement,
                source_segment_ids=linked.source_segment_ids,
                neural_node_key=node_key,
                accepted_at=linked.accepted_at,
                layer=linked.layer,
                metadata={**linked.metadata, "promoted": True},
            )
            self.store.save_source_linked_memory(updated)
            return node_key
        except Exception as exc:
            logger.warning("Durable memory promotion failed: %s", exc)
            return None
