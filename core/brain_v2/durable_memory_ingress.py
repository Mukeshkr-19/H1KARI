"""Single durable-memory ingress shared by text, voice, and server intake.

The boundary is persistence-implementation agnostic: current EpisodeStore and
MemoryRepairGate semantics are supplied through ``DurableMemoryAdapters``.
It performs no database access on import and does not alter the persistent
schema.  A bounded request correlation cache prevents duplicate execution when
the same intake is delivered by more than one transport in one process.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Protocol, Sequence

from core.brain_v2.durable_memory_actions import (
    DurableActionOutcome,
    DurableActionPlan,
    DurableMemoryAdapters,
    execute_durable_plan,
    plan_durable_action,
)
from core.brain_v2.durable_memory_intent import (
    DurableMemoryIntent,
    RecentContextUtterance,
    is_canonical_correlation_id,
    parse_durable_memory_intent,
)
from core.brain_v2.durable_memory_outcome import DurableAck, format_durable_acknowledgment
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_repair import MemoryRepairGate


class DurableIngressSource(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    SERVER = "server"


@dataclass(frozen=True, repr=False)
class DurableIngressResult:
    request_id: str
    source: DurableIngressSource
    intent: DurableMemoryIntent
    plan: DurableActionPlan
    outcome: DurableActionOutcome
    acknowledgment: DurableAck
    duplicate_delivery: bool = False

    def __repr__(self) -> str:
        return (
            "DurableIngressResult("
            f"source={self.source.value!r}, action={self.intent.action!r}, "
            f"status={self.outcome.status!r}, duplicate_delivery={self.duplicate_delivery})"
        )


class DurableMemoryIngress:
    """Parse, plan, execute, read back, and acknowledge exactly once per request."""

    def __init__(self, adapters: DurableMemoryAdapters, *, max_correlations: int = 512) -> None:
        if not isinstance(adapters, DurableMemoryAdapters):
            raise TypeError("adapters must be DurableMemoryAdapters")
        if isinstance(max_correlations, bool) or not isinstance(max_correlations, int):
            raise TypeError("max_correlations must be an integer")
        if max_correlations < 1 or max_correlations > 4096:
            raise ValueError("max_correlations out of range")
        self._adapters = adapters
        self._max_correlations = max_correlations
        self._completed: OrderedDict[str, DurableIngressResult] = OrderedDict()

    def process(
        self,
        text: object,
        *,
        request_id: str,
        source: DurableIngressSource,
        actor_id: object,
        session_id: object,
        recent_context: Optional[Sequence[RecentContextUtterance]] = None,
        now_ms: int = 0,
        restart_generation: int = 0,
        target_memory_id: str = "",
    ) -> DurableIngressResult:
        if not is_canonical_correlation_id(request_id):
            raise ValueError("invalid_request_id")
        if not isinstance(source, DurableIngressSource):
            raise TypeError("source must be DurableIngressSource")
        prior = self._completed.get(request_id)
        if prior is not None:
            self._completed.move_to_end(request_id)
            return DurableIngressResult(
                request_id=prior.request_id,
                source=source,
                intent=prior.intent,
                plan=prior.plan,
                outcome=prior.outcome,
                acknowledgment=prior.acknowledgment,
                duplicate_delivery=True,
            )

        intent = parse_durable_memory_intent(
            text,
            actor_id=actor_id,
            session_id=session_id,
            recent_context=recent_context,
            now_ms=now_ms,
            restart_generation=restart_generation,
        )
        plan = plan_durable_action(intent)
        outcome = execute_durable_plan(
            plan,
            adapters=self._adapters,
            actor_id=intent.actor_id,
            session_id=intent.session_id,
            target_memory_id=target_memory_id,
        )
        ack = format_durable_acknowledgment(outcome)
        result = DurableIngressResult(
            request_id=request_id,
            source=source,
            intent=intent,
            plan=plan,
            outcome=outcome,
            acknowledgment=ack,
        )
        self._completed[request_id] = result
        self._completed.move_to_end(request_id)
        while len(self._completed) > self._max_correlations:
            self._completed.popitem(last=False)
        return result


@dataclass(frozen=True)
class DurableIntegrationCapabilities:
    """Required guarantees from a concrete EpisodeStore adapter factory."""

    provenance_preserved: bool
    persistent_idempotency: bool
    readback_verified: bool
    non_destructive_repair: bool

    def __post_init__(self) -> None:
        for value in (
            self.provenance_preserved,
            self.persistent_idempotency,
            self.readback_verified,
            self.non_destructive_repair,
        ):
            if not isinstance(value, bool):
                raise TypeError("durable integration capabilities must be booleans")

    @property
    def complete(self) -> bool:
        return all(
            (
                self.provenance_preserved,
                self.persistent_idempotency,
                self.readback_verified,
                self.non_destructive_repair,
            )
        )


class ProvenanceDurableAdapterFactory(Protocol):
    capabilities: DurableIntegrationCapabilities

    def build(
        self, *, episode_store: EpisodeStore, repair_gate: MemoryRepairGate
    ) -> DurableMemoryAdapters: ...


@dataclass(frozen=True, repr=False)
class DurableIngressIntegration:
    available: bool
    reason: str
    ingress: Optional[DurableMemoryIngress] = None

    def __repr__(self) -> str:
        return (
            "DurableIngressIntegration("
            f"available={self.available}, reason={self.reason!r}, "
            f"has_ingress={self.ingress is not None})"
        )


def build_durable_ingress_integration(
    *,
    episode_store: object,
    repair_gate: object,
    adapter_factory: Optional[ProvenanceDurableAdapterFactory],
) -> DurableIngressIntegration:
    """Build only from a complete provenance-preserving concrete factory.

    This function performs no store reads or writes.  It refuses partial ports,
    mismatched store/repair ownership, or capability claims missing persistent
    idempotency/read-back/non-destructive repair.
    """
    if not isinstance(episode_store, EpisodeStore):
        return DurableIngressIntegration(False, "episode_store_required")
    if not isinstance(repair_gate, MemoryRepairGate) or repair_gate.store is not episode_store:
        return DurableIngressIntegration(False, "repair_gate_store_mismatch")
    if adapter_factory is None or not callable(getattr(adapter_factory, "build", None)):
        return DurableIngressIntegration(False, "adapter_factory_required")
    capabilities = getattr(adapter_factory, "capabilities", None)
    if not isinstance(capabilities, DurableIntegrationCapabilities) or not capabilities.complete:
        return DurableIngressIntegration(False, "capability_evidence_incomplete")
    try:
        adapters = adapter_factory.build(
            episode_store=episode_store, repair_gate=repair_gate
        )
    except Exception:
        return DurableIngressIntegration(False, "adapter_factory_failed")
    if not isinstance(adapters, DurableMemoryAdapters):
        return DurableIngressIntegration(False, "invalid_adapter_bundle")
    writer = adapters.atomic_write or adapters.candidate
    if any(
        port is None
        for port in (
            writer,
            adapters.readback,
            adapters.exact_active,
            adapters.supersede,
            adapters.retire,
        )
    ):
        return DurableIngressIntegration(False, "adapter_bundle_incomplete")
    return DurableIngressIntegration(
        True, "ready", DurableMemoryIngress(adapters)
    )


__all__ = [
    "DurableIngressIntegration",
    "DurableIngressResult",
    "DurableIngressSource",
    "DurableIntegrationCapabilities",
    "DurableMemoryIngress",
    "ProvenanceDurableAdapterFactory",
    "build_durable_ingress_integration",
]
