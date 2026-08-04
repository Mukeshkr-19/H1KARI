from core.brain_v2.durable_memory_actions import (
    AtomicWriteResult,
    DurableMemoryAdapters,
    ReadbackResult,
)
from core.brain_v2.durable_memory_ingress import (
    DurableIntegrationCapabilities,
    DurableIngressSource,
    DurableMemoryIngress,
    build_durable_ingress_integration,
)
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_repair import MemoryRepairGate


class IdempotentMemoryPort:
    def __init__(self) -> None:
        self.writes = 0
        self.by_key: dict[str, str] = {}

    def atomic_write_active(
        self, *, body: str, idempotency_key: str, actor_id: str, session_id: str
    ) -> AtomicWriteResult:
        del body, actor_id, session_id
        created = idempotency_key not in self.by_key
        self.by_key.setdefault(idempotency_key, "mem_1")
        self.writes += 1
        return AtomicWriteResult(self.by_key[idempotency_key], created)

    def verify_readback(self, *, memory_id: str, expected_body: str) -> ReadbackResult:
        return ReadbackResult(memory_id == "mem_1", True, bool(expected_body))


def test_text_voice_server_delivery_converges_once_by_request_id() -> None:
    port = IdempotentMemoryPort()
    ingress = DurableMemoryIngress(
        DurableMemoryAdapters(atomic_write=port, readback=port)
    )
    first = ingress.process(
        "save this into my brain: I live in Boston",
        request_id="request_1",
        source=DurableIngressSource.TEXT,
        actor_id="owner_1",
        session_id="session_1",
    )
    duplicate = ingress.process(
        "save this into my brain: I live in Boston",
        request_id="request_1",
        source=DurableIngressSource.VOICE,
        actor_id="owner_1",
        session_id="session_1",
    )
    assert port.writes == 1
    assert first.outcome.status == "accepted"
    assert first.acknowledgment.message == "Saved to long-term Brain."
    assert duplicate.duplicate_delivery is True
    assert duplicate.outcome == first.outcome


def test_ordinary_chat_is_ephemeral_and_never_calls_writer() -> None:
    port = IdempotentMemoryPort()
    ingress = DurableMemoryIngress(
        DurableMemoryAdapters(atomic_write=port, readback=port)
    )
    result = ingress.process(
        "I like green tea",
        request_id="request_2",
        source=DurableIngressSource.SERVER,
        actor_id="owner_1",
        session_id="session_1",
    )
    assert result.intent.scope == "ephemeral"
    assert result.outcome.status == "not_saved"
    assert port.writes == 0
    assert "green tea" not in repr(result)


def test_missing_store_adapter_is_truthful_known_no_write() -> None:
    ingress = DurableMemoryIngress(DurableMemoryAdapters())
    result = ingress.process(
        "remember this in my brain: I live in Boston",
        request_id="request_3",
        source=DurableIngressSource.TEXT,
        actor_id="owner_1",
        session_id="session_1",
    )
    assert result.outcome.status == "unavailable"
    assert result.acknowledgment.message == "Not saved."


class CompleteSyntheticFactory:
    capabilities = DurableIntegrationCapabilities(True, True, True, True)

    def __init__(self, adapters: DurableMemoryAdapters) -> None:
        self.adapters = adapters

    def build(self, *, episode_store, repair_gate) -> DurableMemoryAdapters:
        assert repair_gate.store is episode_store
        return self.adapters


def _uninitialized_store_and_gate() -> tuple[EpisodeStore, MemoryRepairGate]:
    # Contract-only synthetic objects: no DB path is resolved and no schema is opened.
    store = object.__new__(EpisodeStore)
    gate = object.__new__(MemoryRepairGate)
    gate.store = store
    return store, gate


def test_production_factory_refuses_missing_capability_evidence_without_store_io() -> None:
    store, gate = _uninitialized_store_and_gate()

    class IncompleteFactory:
        capabilities = DurableIntegrationCapabilities(True, False, True, True)

        def build(self, **_kwargs):
            raise AssertionError("must not build")

    result = build_durable_ingress_integration(
        episode_store=store,
        repair_gate=gate,
        adapter_factory=IncompleteFactory(),
    )
    assert result.available is False
    assert result.reason == "capability_evidence_incomplete"
    assert result.ingress is None


def test_production_factory_requires_all_ports_before_exposing_ingress() -> None:
    store, gate = _uninitialized_store_and_gate()
    partial = build_durable_ingress_integration(
        episode_store=store,
        repair_gate=gate,
        adapter_factory=CompleteSyntheticFactory(DurableMemoryAdapters()),
    )
    assert partial.available is False
    assert partial.reason == "adapter_bundle_incomplete"


def test_complete_factory_contract_exposes_ingress_without_touching_store() -> None:
    store, gate = _uninitialized_store_and_gate()
    port = IdempotentMemoryPort()

    class OtherPorts:
        def find_exact_active(self, *, body: str):
            return None

        def supersede(self, **_kwargs):
            raise AssertionError("not invoked by factory")

        def retire(self, **_kwargs):
            raise AssertionError("not invoked by factory")

    other = OtherPorts()
    integration = build_durable_ingress_integration(
        episode_store=store,
        repair_gate=gate,
        adapter_factory=CompleteSyntheticFactory(
            DurableMemoryAdapters(
                atomic_write=port,
                readback=port,
                exact_active=other,
                supersede=other,
                retire=other,
            )
        ),
    )
    assert integration.available is True
    assert integration.reason == "ready"
    assert isinstance(integration.ingress, DurableMemoryIngress)
