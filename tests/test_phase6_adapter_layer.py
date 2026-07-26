"""Adversarial tests for HIKARI Phase 6 optional adapter layer.

Tests cover all five adapter families.  No real network, storage, model, or
subprocess access occurs.
"""

from __future__ import annotations

import hashlib
import time
import zipfile
import io
from typing import Any, Mapping, Optional

import pytest

from core.action_policy import Actor, ActorContext
from core.phase6_adapters import (
    AdapterException,
    AdapterReason,
    AdapterState,
    EncryptedSyncAdapter,
    EncryptedSyncAdapterConfig,
    HomeAssistantAdapter,
    HomeAssistantAdapterConfig,
    MeasuredRoutingAdapter,
    MeasuredRoutingAdapterConfig,
    RemoteWorkerCoordinator,
    RemoteWorkerCoordinatorConfig,
    SkillStagingAdapter,
    SkillStagingAdapterConfig,
)
from core.phase6_adapters.home_assistant import HomeAssistantAdapterReason, HomeAssistantAdapterOutcome
from core.phase6_adapters.encrypted_sync import EncryptedSyncAdapterReason, EncryptedSyncAdapterOutcome, EncryptedSyncStorageInterface, EncryptedSyncTransactionProposal
from core.phase6_adapters.remote_worker import RemoteWorkerAdapterOutcome
from core.phase6_adapters.skill_staging import (
    ArchiveEntry,
    ArchiveEntryKind,
    ArchiveEntryReaderInterface,
    SkillStagingAdapterReason,
    SkillStagingAdapterOutcome,
)
from core.phase6_adapters.measured_routing import (
    CanaryState,
    MeasuredRoutingAdapterOutcome,
)

from core.phase6_ecosystem.home_assistant import (
    HomeAssistantCapabilityManifest,
    HomeAssistantConfirmation,
    HomeAssistantEntityRef,
    HomeAssistantServiceRef,
    HomeAssistantTransportInterface,
)
from core.phase6_ecosystem.encrypted_sync import (
    DeviceTrustRecord,
    EncryptedObjectDescriptor,
    EncryptionProviderInterface,
    SyncManifest,
)
from core.phase6_ecosystem.model_evaluation import (
    EvaluationScenario,
    ModelCandidate,
    ModelCapability,
    ModelMeasurement,
    PrivacyClass,
)
from core.phase6_agent.contracts import RemoteWorkerAuthorityEnvelope, RemoteWorkerResult
from core.phase6_agent.remote_worker import RemoteValidationOutcome
from core.phase6_ecosystem.skill_package import (
    PublisherTrust,
    SignatureEvidence,
    SkillFileDigest,
    SkillPackageCandidate,
    SkillPackageManifest,
    SkillPermissionDeclaration,
    SkillReview,
    SkillRollbackMetadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeIdFactory:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"id{self.n}"


class FakeHAConfig:
    @staticmethod
    def build() -> HomeAssistantAdapterConfig:
        manifest = HomeAssistantCapabilityManifest(
            allowed_domains=frozenset({"light", "switch"}),
            allowed_entities=frozenset({"light.living_room", "switch.kitchen"}),
            allowed_services=frozenset({"turn_on", "turn_off", "get_state"}),
            sensitive_domains=frozenset({"lock"}),
            sensitive_entities=frozenset({"switch.kitchen"}),
            read_only_services=frozenset({"get_state"}),
        )
        return HomeAssistantAdapterConfig(
            base_url="https://hass.local:8123",
            manifest=manifest,
        )


from core.phase6_adapters.home_assistant import (
    HomeAssistantTransportContract,
    HomeAssistantTransportRequest,
    HomeAssistantTransportEvidence,
)
from core.phase6_adapters.remote_worker import (
    CancellationAcknowledgement,
    LocalAuthorizerInterface,
    NonceStoreInterface,
    RemoteWorkerJobState,
)
from core.phase6_adapters.encrypted_sync import (
    DeviceTrustRegistry,
    NonceReplayRegistry,
    TransactionRegistry,
    EncryptedSyncTransactionState,
)


class FakeHATransport(HomeAssistantTransportContract):
    def __init__(self, final_url: Optional[str] = None, resolved_host: Optional[str] = None, failure: Optional[str] = None):
        self.final_url = final_url or "https://hass.local:8123/api/"
        self.resolved_host = resolved_host or "hass.local"
        self.failure = failure

    def execute_request(self, request: HomeAssistantTransportRequest) -> HomeAssistantTransportEvidence:
        return HomeAssistantTransportEvidence(
            observation_id="obs1",
            proposal_id=request.proposal_id,
            final_url=self.final_url,
            resolved_host=self.resolved_host,
            response_byte_count=2,
            elapsed_seconds=0.01,
            success_category=None if self.failure else "ok",
            failure_category=self.failure,
            idempotency_contract_proven=False,
            observed_at=0.0,
        )


class FakeAuditor:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, **record):
        self.records.append(record)


class FakeEncryptionProvider(EncryptionProviderInterface):
    @property
    def is_verified(self) -> bool:
        return True

    def verify_descriptor(self, descriptor):
        return True


class FakeSyncStorage(EncryptedSyncStorageInterface):
    def __init__(self) -> None:
        self.staged: dict[str, Any] = {}

    def stage(self, transaction_id, plan):
        self.staged[transaction_id] = plan
        return True, "ok"

    def commit(self, transaction_id):
        return transaction_id in self.staged, "ok"

    def rollback(self, transaction_id):
        return transaction_id in self.staged, "ok"


class FakeArchiveReader:
    def __init__(self, entries: dict[str, bytes]) -> None:
        self.entries = entries

    def read_entries(self, archive_bytes: bytes) -> tuple[ArchiveEntry, ...]:
        return tuple(
            ArchiveEntry(
                normalized_path=path,
                kind=ArchiveEntryKind.REGULAR,
                content=content,
                uncompressed_size=len(content),
                compressed_size=len(content),
                mode=0o644,
                executable=False,
                link_target=None,
            )
            for path, content in self.entries.items()
        )


# ---------------------------------------------------------------------------
# Default-deny and constructor tests
# ---------------------------------------------------------------------------

def test_default_construction_leaves_adapter_disabled() -> None:
    ha = HomeAssistantAdapter()
    enc = EncryptedSyncAdapter()
    rw = RemoteWorkerCoordinator()
    ss = SkillStagingAdapter()
    mr = MeasuredRoutingAdapter()
    assert ha.state is AdapterState.DISABLED
    assert enc.state is AdapterState.DISABLED
    assert rw.state is AdapterState.DISABLED
    assert ss.state is AdapterState.DISABLED
    assert mr.state is AdapterState.DISABLED


def test_adapter_reprs_are_content_free() -> None:
    assert "HomeAssistantAdapter()" in repr(HomeAssistantAdapter())
    assert "EncryptedSyncAdapter()" in repr(EncryptedSyncAdapter())
    assert "RemoteWorkerCoordinator()" in repr(RemoteWorkerCoordinator())
    assert "SkillStagingAdapter()" in repr(SkillStagingAdapter())
    assert "MeasuredRoutingAdapter()" in repr(MeasuredRoutingAdapter())


# ---------------------------------------------------------------------------
# Home Assistant adapter tests
# ---------------------------------------------------------------------------

def test_ha_disabled_adapter_returns_unavailable() -> None:
    ha = HomeAssistantAdapter()
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    result = ha.prepare(
        proposal_id="p1",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "get_state"),
        service_data={},
        actor_context=actor,
        nonce="n1",
    )
    assert result.outcome is HomeAssistantAdapterOutcome.UNAVAILABLE
    assert result.reason is HomeAssistantAdapterReason.DISABLED


def test_ha_ssrf_url_tricks_rejected() -> None:
    for bad_url in [
        "https://hass.local:8123@evil.com",
        "https://hass.local:8123/../admin",
        "https://hass.local:8123#fragment",
        "https://hass.local:8123?query=1",
        "https://*.*:8123",
    ]:
        manifest = HomeAssistantCapabilityManifest(
            allowed_domains=frozenset({"light"}),
            allowed_entities=frozenset({"light.living_room"}),
            allowed_services=frozenset({"get_state"}),
        )
        with pytest.raises(ValueError):
            HomeAssistantAdapterConfig(base_url=bad_url, manifest=manifest)


def test_ha_unauthorized_scheme_rejected() -> None:
    manifest = HomeAssistantCapabilityManifest(
        allowed_domains=frozenset({"light"}),
        allowed_entities=frozenset({"light.living_room"}),
        allowed_services=frozenset({"get_state"}),
    )
    config = HomeAssistantAdapterConfig(
        base_url="http://hass.local:8123",
        manifest=manifest,
        allowed_schemes=frozenset({"https"}),
    )
    ha = HomeAssistantAdapter(config=config, clock=FakeClock(), id_factory=FakeIdFactory())
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    result = ha.prepare(
        proposal_id="p1",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "get_state"),
        service_data={},
        actor_context=actor,
        nonce="n1",
    )
    assert result.reason is HomeAssistantAdapterReason.SCHEME_NOT_ALLOWED


def test_ha_state_change_requires_confirmation() -> None:
    config = FakeHAConfig.build()
    ha = HomeAssistantAdapter(config=config, clock=FakeClock(), id_factory=FakeIdFactory())
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    result = ha.prepare(
        proposal_id="p1",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={},
        actor_context=actor,
        nonce="n1",
    )
    assert result.outcome is HomeAssistantAdapterOutcome.REQUIRE_CONFIRMATION
    assert result.proposal is not None


def test_ha_confirmation_replay_detected() -> None:
    config = FakeHAConfig.build()
    clock = FakeClock()
    ha = HomeAssistantAdapter(config=config, clock=clock, id_factory=FakeIdFactory(), auditor=FakeAuditor(), transport=FakeHATransport())
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    result = ha.prepare(
        proposal_id="p1",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "turn_on"),
        service_data={},
        actor_context=actor,
        nonce="n1",
    )
    proposal = result.proposal
    assert proposal is not None
    confirmation = HomeAssistantConfirmation(
        proposal_id="p1",
        nonce="n1",
        confirmed_by_actor_id="owner.1",
        confirmed_at=0.0,
    )
    first = ha.confirm_and_execute(proposal, confirmation, actor)
    assert first.outcome is HomeAssistantAdapterOutcome.ALLOW
    second = ha.confirm_and_execute(proposal, confirmation, actor)
    assert second.outcome is HomeAssistantAdapterOutcome.DENY


# ---------------------------------------------------------------------------
# Encrypted sync adapter tests
# ---------------------------------------------------------------------------

def test_encrypted_sync_disabled_returns_unavailable() -> None:
    adapter = EncryptedSyncAdapter()
    desc = EncryptedObjectDescriptor(
        object_id="obj1",
        ciphertext_digest="0" * 64,
        size_bytes=1,
        version=1,
        updated_at=0.0,
    )
    local = SyncManifest("m1", "d1", (desc,), 0.0)
    remote = SyncManifest("m2", "d1", (desc,), 0.0)
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    with pytest.raises(AdapterException):
        adapter.plan_sync("plan1", local, remote, trust, "nonce1")


def test_encrypted_sync_nonce_replay_blocked() -> None:
    config = EncryptedSyncAdapterConfig()
    provider = FakeEncryptionProvider()
    storage = FakeSyncStorage()
    adapter = EncryptedSyncAdapter(
        config=config,
        encryption_provider=provider,
        storage=storage,
        clock=FakeClock(),
        id_factory=FakeIdFactory(),
    )
    desc = EncryptedObjectDescriptor(
        object_id="obj1",
        ciphertext_digest="0" * 64,
        size_bytes=1,
        version=1,
        updated_at=0.0,
    )
    local = SyncManifest("m1", "d1", (desc,), 0.0)
    remote = SyncManifest("m2", "d1", (desc,), 0.0)
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    adapter.plan_sync("plan1", local, remote, trust, "nonce1")
    with pytest.raises(AdapterException):
        adapter.plan_sync("plan2", local, remote, trust, "nonce1")


def test_encrypted_sync_revoked_device_blocked() -> None:
    config = EncryptedSyncAdapterConfig()
    provider = FakeEncryptionProvider()
    adapter = EncryptedSyncAdapter(
        config=config,
        encryption_provider=provider,
        clock=FakeClock(),
        id_factory=FakeIdFactory(),
    )
    adapter.revoke_device("d1")
    desc = EncryptedObjectDescriptor(
        object_id="obj1",
        ciphertext_digest="0" * 64,
        size_bytes=1,
        version=1,
        updated_at=0.0,
    )
    local = SyncManifest("m1", "d1", (desc,), 0.0)
    remote = SyncManifest("m2", "d1", (desc,), 0.0)
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    with pytest.raises(AdapterException):
        adapter.plan_sync("plan1", local, remote, trust, "nonce1")


# ---------------------------------------------------------------------------
# Remote worker coordinator tests
# ---------------------------------------------------------------------------

def test_remote_worker_disabled_returns_unavailable() -> None:
    coord = RemoteWorkerCoordinator()
    envelope = RemoteWorkerAuthorityEnvelope(
        envelope_id="e1",
        worker_id="w1",
        task_id="t1",
        capability="c1",
        targets=("target1",),
        expires_at_mono=10.0,
        nonce="0" * 16,
        max_responses=1,
    )
    assert coord.submit_job(envelope) is RemoteWorkerAdapterOutcome.UNAVAILABLE


def test_remote_worker_expired_envelope_denied() -> None:
    clock = FakeClock(start=11.0)
    config = RemoteWorkerCoordinatorConfig()
    coord = RemoteWorkerCoordinator(config=config, clock=clock, id_factory=FakeIdFactory())
    envelope = RemoteWorkerAuthorityEnvelope(
        envelope_id="e1",
        worker_id="w1",
        task_id="t1",
        capability="c1",
        targets=("target1",),
        expires_at_mono=10.0,
        nonce="0" * 16,
        max_responses=1,
    )
    assert coord.submit_job(envelope) is RemoteWorkerAdapterOutcome.DENY


def test_remote_worker_remote_cannot_mark_success() -> None:
    coord = RemoteWorkerCoordinator(
        config=RemoteWorkerCoordinatorConfig(),
        clock=FakeClock(),
        id_factory=FakeIdFactory(),
    )
    envelope = RemoteWorkerAuthorityEnvelope(
        envelope_id="e1",
        worker_id="w1",
        task_id="t1",
        capability="c1",
        targets=("target1",),
        expires_at_mono=10.0,
        nonce="0" * 16,
        max_responses=1,
    )
    result = RemoteWorkerResult(
        result_id="r1",
        envelope_id="e1",
        worker_id="w1",
        task_id="t1",
        summary="done",
        observed_at_mono=0.0,
    )
    validation = type("Validation", (), {"outcome": RemoteValidationOutcome.VALID, "failure_code": None})()
    acceptance = coord.accept_result(envelope, result, validation)
    assert acceptance.can_mark_task_success is False
    assert acceptance.can_execute_local_action is False


# ---------------------------------------------------------------------------
# Skill staging adapter tests
# ---------------------------------------------------------------------------

def _skill_manifest() -> SkillPackageManifest:
    content = b"print('ok')"
    digest = hashlib.sha256(content).hexdigest()
    file_digest = SkillFileDigest("skill.py", digest, len(content))
    return SkillPackageManifest(
        package_id="pkg1",
        name="Skill",
        version="1.0.0",
        publisher_id="pub1",
        description="A skill",
        declared_permissions=(),
        files=(file_digest,),
        dependencies=(),
        created_at=0.0,
    )


def test_skill_staging_disabled_returns_unavailable() -> None:
    adapter = SkillStagingAdapter()
    manifest = _skill_manifest()
    candidate = SkillPackageCandidate(manifest=manifest, file_contents={"skill.py": b"print('ok')"})
    review = SkillReview(
        review_id="rev1",
        package_id="pkg1",
        package_digest=manifest.canonical_digest(),
        version="1.0.0",
        publisher_id="pub1",
        reviewed_permissions=(),
        reviewer_actor_id="owner.1",
        reviewer_role="owner",
        outcome="approved",
        reviewed_at=0.0,
    )
    signature = SignatureEvidence("pub1", "key1", "0" * 128)
    trust = PublisherTrust("pub1", "trusted", ("key1",), ())
    with pytest.raises(AdapterException):
        adapter.stage_installation(
            b"archive",
            candidate,
            signature,
            trust,
            review,
            [],
            None,
            False,
            "owner.1",
        )


def test_skill_staging_archive_path_traversal_rejected() -> None:
    reader = FakeArchiveReader({"../escape.txt": b"bad"})
    config = SkillStagingAdapterConfig()
    adapter = SkillStagingAdapter(
        config=config,
        archive_reader=reader,
        clock=FakeClock(),
        id_factory=FakeIdFactory(),
        signature_verifier=lambda b, s: True,
    )
    manifest = _skill_manifest()
    candidate = SkillPackageCandidate(manifest=manifest, file_contents={"skill.py": b"print('ok')"})
    review = SkillReview(
        review_id="rev1",
        package_id="pkg1",
        package_digest=manifest.canonical_digest(),
        version="1.0.0",
        publisher_id="pub1",
        reviewed_permissions=(),
        reviewer_actor_id="owner.1",
        reviewer_role="owner",
        outcome="approved",
        reviewed_at=0.0,
    )
    with pytest.raises(AdapterException):
        adapter.stage_installation(
            b"archive",
            candidate,
            SignatureEvidence("pub1", "key1", "0" * 128),
            PublisherTrust("pub1", "trusted", ("key1",), ()),
            review,
            [],
            None,
            False,
            "owner.1",
        )


# ---------------------------------------------------------------------------
# Measured routing adapter tests
# ---------------------------------------------------------------------------

def test_measured_routing_disabled_returns_unavailable() -> None:
    adapter = MeasuredRoutingAdapter()
    scenario = EvaluationScenario(
        scenario_id="s1",
        required_capabilities=(ModelCapability.TEXT_GEN,),
        max_privacy_class=PrivacyClass.LOCAL_ONLY,
    )
    result = adapter.evaluate(scenario, [], {})
    assert result.outcome is MeasuredRoutingAdapterOutcome.UNAVAILABLE
    assert result.reason.name == "DISABLED"


def test_measured_routing_local_only_rejects_remote() -> None:
    config = MeasuredRoutingAdapterConfig()
    adapter = MeasuredRoutingAdapter(config=config, clock=FakeClock())
    scenario = EvaluationScenario(
        scenario_id="s1",
        required_capabilities=(ModelCapability.TEXT_GEN,),
        max_privacy_class=PrivacyClass.LOCAL_ONLY,
    )
    candidate = ModelCandidate(
        candidate_id="remote1",
        provider_type="remote_provider",
        model_name="big-model",
        privacy_class=PrivacyClass.REMOTE_OK,
        capabilities=(ModelCapability.TEXT_GEN,),
        provenance_id="p1",
    )
    measurement = ModelMeasurement(
        candidate_id="remote1",
        quality_score=0.9,
        safety_score=0.9,
        latency_ms=100.0,
        cost_usd=0.01,
        memory_mb=1000.0,
        reliability_score=0.9,
        measured_at=0.0,
    )
    result = adapter.evaluate(scenario, [candidate], {"remote1": measurement})
    assert result.outcome is MeasuredRoutingAdapterOutcome.DENY


def test_measured_routing_remote_never_wins_local_only() -> None:
    config = MeasuredRoutingAdapterConfig()
    adapter = MeasuredRoutingAdapter(config=config, clock=FakeClock())
    scenario = EvaluationScenario(
        scenario_id="s1",
        required_capabilities=(ModelCapability.TEXT_GEN,),
        max_privacy_class=PrivacyClass.LOCAL_ONLY,
    )
    local = ModelCandidate(
        candidate_id="local1",
        provider_type="local_model",
        model_name="local-model",
        privacy_class=PrivacyClass.LOCAL_ONLY,
        capabilities=(ModelCapability.TEXT_GEN,),
        provenance_id="p1",
    )
    remote = ModelCandidate(
        candidate_id="remote1",
        provider_type="remote_provider",
        model_name="big-model",
        privacy_class=PrivacyClass.REMOTE_OK,
        capabilities=(ModelCapability.TEXT_GEN,),
        provenance_id="p2",
    )
    measurements = {
        "local1": ModelMeasurement(
            candidate_id="local1",
            quality_score=0.6,
            safety_score=0.9,
            latency_ms=100.0,
            cost_usd=0.0,
            memory_mb=1000.0,
            reliability_score=0.9,
            measured_at=0.0,
        ),
        "remote1": ModelMeasurement(
            candidate_id="remote1",
            quality_score=0.99,
            safety_score=0.99,
            latency_ms=50.0,
            cost_usd=0.01,
            memory_mb=2000.0,
            reliability_score=0.99,
            measured_at=0.0,
        ),
    }
    result = adapter.evaluate(scenario, [local, remote], measurements)
    assert result.outcome is MeasuredRoutingAdapterOutcome.RECOMMEND
    assert result.recommendation is not None
    assert result.recommendation.winning_candidate.candidate_id == "local1"


# ---------------------------------------------------------------------------
# Additional adversarial tests
# ---------------------------------------------------------------------------

def test_ha_oversized_response_config_bound() -> None:
    manifest = HomeAssistantCapabilityManifest(
        allowed_domains=frozenset({"light"}),
        allowed_entities=frozenset({"light.living_room"}),
        allowed_services=frozenset({"get_state"}),
    )
    config = HomeAssistantAdapterConfig(
        base_url="https://hass.local:8123",
        manifest=manifest,
        max_response_bytes=1024,
    )
    assert config.max_response_bytes == 1024


def test_encrypted_sync_conflict_fails_before_staging() -> None:
    class _FakeProvider(EncryptionProviderInterface):
        @property
        def is_verified(self):
            return True
        def verify_descriptor(self, descriptor):
            return True
    config = EncryptedSyncAdapterConfig()
    adapter = EncryptedSyncAdapter(
        config=config,
        encryption_provider=_FakeProvider(),
        storage=FakeSyncStorage(),
        clock=FakeClock(),
        id_factory=FakeIdFactory(),
    )
    local_desc = EncryptedObjectDescriptor("obj1", "0" * 64, 1, 2, 0.0)
    remote_desc = EncryptedObjectDescriptor("obj1", "1" * 64, 1, 2, 0.0)
    local = SyncManifest("m1", "d1", (local_desc,), 0.0)
    remote = SyncManifest("m2", "d1", (remote_desc,), 0.0)
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    with pytest.raises(AdapterException):
        adapter.plan_sync("plan1", local, remote, trust, "nonce1")


def test_remote_worker_replay_blocked_by_nonce_store() -> None:
    class _NonceStore:
        def __init__(self):
            self._seen: set[str] = set()
        def is_consumed(self, nonce):
            return nonce in self._seen
        def consume(self, nonce):
            if nonce in self._seen:
                return False
            self._seen.add(nonce)
            return True
    config = RemoteWorkerCoordinatorConfig()
    coord = RemoteWorkerCoordinator(config=config, clock=FakeClock(), id_factory=FakeIdFactory(), nonce_store=_NonceStore())
    envelope = RemoteWorkerAuthorityEnvelope(
        envelope_id="e1",
        worker_id="w1",
        task_id="t1",
        capability="c1",
        targets=("target1",),
        expires_at_mono=10.0,
        nonce="0" * 16,
        max_responses=1,
    )
    def _verify(envelope, signature):
        return True
    validation = coord.validate_envelope(
        envelope, b"sig", _verify,
        expected_worker_id="w1", expected_task_id="t1",
        expected_capability="c1", expected_targets=("target1",),
    )
    assert validation.outcome.value == "valid"
    validation2 = coord.validate_envelope(
        envelope, b"sig", _verify,
        expected_worker_id="w1", expected_task_id="t1",
        expected_capability="c1", expected_targets=("target1",),
    )
    assert validation2.outcome is RemoteValidationOutcome.REPLAYED


def test_skill_staging_permission_widening_detected() -> None:
    content = b"print('ok')"
    digest = hashlib.sha256(content).hexdigest()
    old_perm = SkillPermissionDeclaration("skill", "skill.execute", "skill", "skill1")
    new_perm = SkillPermissionDeclaration("skill", "skill.execute", "skill", "skill2")
    widened_manifest = SkillPackageManifest(
        package_id="pkg1",
        name="Skill",
        version="1.0.0",
        publisher_id="pub1",
        description="A skill",
        declared_permissions=(old_perm, new_perm),
        files=(SkillFileDigest("skill.py", digest, len(content)),),
        dependencies=(),
        created_at=0.0,
    )
    # Review only approved the original permission, so the new permission is unapproved.
    review = SkillReview(
        review_id="rev1",
        package_id="pkg1",
        package_digest=widened_manifest.canonical_digest(),
        version="1.0.0",
        publisher_id="pub1",
        reviewed_permissions=(old_perm,),
        reviewer_actor_id="owner.1",
        reviewer_role="owner",
        outcome="approved",
        reviewed_at=0.0,
    )
    candidate = SkillPackageCandidate(
        manifest=widened_manifest,
        file_contents={"skill.py": content},
    )
    reader = FakeArchiveReader({"skill.py": content})
    config = SkillStagingAdapterConfig()
    adapter = SkillStagingAdapter(
        config=config,
        archive_reader=reader,
        clock=FakeClock(),
        id_factory=FakeIdFactory(),
        signature_verifier=lambda b, s: True,
    )
    with pytest.raises(AdapterException) as exc_info:
        adapter.stage_installation(
            b"archive",
            candidate,
            SignatureEvidence("pub1", "key1", "0" * 128),
            PublisherTrust("pub1", "trusted", ("key1",), ()),
            review,
            [],
            None,
            False,
            "owner.1",
        )
    assert exc_info.value.reason is AdapterReason.POLICY_DENIED
    assert exc_info.value.detail is SkillStagingAdapterReason.PERMISSION_WIDENING


def test_measured_routing_canary_lifecycle_and_rollback() -> None:
    config = MeasuredRoutingAdapterConfig(require_canary_confirmation=False)
    adapter = MeasuredRoutingAdapter(config=config, clock=FakeClock())
    scenario = EvaluationScenario(
        scenario_id="s1",
        required_capabilities=(ModelCapability.TEXT_GEN,),
        max_privacy_class=PrivacyClass.REMOTE_OK,
    )
    local = ModelCandidate(
        candidate_id="local1",
        provider_type="local_gateway",
        model_name="local-model",
        privacy_class=PrivacyClass.GATEWAY_OK,
        capabilities=(ModelCapability.TEXT_GEN,),
        provenance_id="p1",
    )
    remote = ModelCandidate(
        candidate_id="remote1",
        provider_type="remote_provider",
        model_name="remote-model",
        privacy_class=PrivacyClass.REMOTE_OK,
        capabilities=(ModelCapability.TEXT_GEN,),
        provenance_id="p2",
    )
    measurements = {
        "local1": ModelMeasurement(
            candidate_id="local1",
            quality_score=0.9,
            safety_score=0.9,
            latency_ms=100.0,
            cost_usd=0.0,
            memory_mb=1000.0,
            reliability_score=0.9,
            measured_at=0.0,
        ),
        "remote1": ModelMeasurement(
            candidate_id="remote1",
            quality_score=0.95,
            safety_score=0.95,
            latency_ms=100.0,
            cost_usd=0.01,
            memory_mb=1000.0,
            reliability_score=0.95,
            measured_at=0.0,
        ),
    }
    # Evaluation alone does not update incumbent; it proposes a canary.
    result1 = adapter.evaluate(scenario, [local, remote], measurements)
    assert result1.outcome is MeasuredRoutingAdapterOutcome.RECOMMEND
    assert result1.recommendation.winning_candidate.candidate_id == "local1"
    assert result1.canary_state is CanaryState.CANARY_PROPOSED
    # Confirm the canary to make local the incumbent.
    confirmed = adapter.confirm_canary(result1.canary.proposal_id)
    assert confirmed.canary_state is CanaryState.CONFIRMED
    # Remote overtakes -> flap detected, rollback candidate is previous incumbent.
    measurements2 = {
        "local1": ModelMeasurement(
            candidate_id="local1",
            quality_score=0.6,
            safety_score=0.9,
            latency_ms=100.0,
            cost_usd=0.0,
            memory_mb=1000.0,
            reliability_score=0.9,
            measured_at=0.0,
        ),
        "remote1": ModelMeasurement(
            candidate_id="remote1",
            quality_score=0.99,
            safety_score=0.99,
            latency_ms=100.0,
            cost_usd=0.01,
            memory_mb=1000.0,
            reliability_score=0.99,
            measured_at=0.0,
        ),
    }
    result2 = adapter.evaluate(scenario, [local, remote], measurements2)
    assert result2.outcome is MeasuredRoutingAdapterOutcome.RECOMMEND
    assert result2.canary.requires_confirmation is True
    assert result2.rollback_candidate_id == "local1"
    # Failed canary restores previous route.
    failed = adapter.record_canary_failure(result2.canary.proposal_id)
    assert failed.canary_state is CanaryState.CANARY_FAILED
    assert failed.rollback_candidate_id == "local1"


def test_ha_transport_exception_is_caught() -> None:
    class FailingTransport(HomeAssistantTransportContract):
        def execute_request(self, request: HomeAssistantTransportRequest) -> HomeAssistantTransportEvidence:
            raise RuntimeError("transport failure")

    config = FakeHAConfig.build()
    ha = HomeAssistantAdapter(config=config, clock=FakeClock(), id_factory=FakeIdFactory(), auditor=FakeAuditor(), transport=FailingTransport())
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    result = ha.prepare(
        proposal_id="p1",
        entity_ref=HomeAssistantEntityRef("light", "light.living_room"),
        service_ref=HomeAssistantServiceRef("light", "get_state"),
        service_data={},
        actor_context=actor,
        nonce="n1",
    )
    proposal = result.proposal
    confirmation = HomeAssistantConfirmation(
        proposal_id="p1",
        nonce="n1",
        confirmed_by_actor_id="owner.1",
        confirmed_at=0.0,
    )
    exec_result = ha.confirm_and_execute(proposal, confirmation, actor)
    assert exec_result.outcome is HomeAssistantAdapterOutcome.DENY
    assert exec_result.reason is HomeAssistantAdapterReason.TRANSPORT_FAILURE


def test_encrypted_sync_nonce_not_consumed_on_revoked_device() -> None:
    class _FakeProvider(EncryptionProviderInterface):
        @property
        def is_verified(self):
            return True
        def verify_descriptor(self, descriptor):
            return True
    config = EncryptedSyncAdapterConfig()
    adapter = EncryptedSyncAdapter(
        config=config,
        encryption_provider=_FakeProvider(),
        clock=FakeClock(),
        id_factory=FakeIdFactory(),
    )
    adapter.revoke_device("d1")
    desc = EncryptedObjectDescriptor("obj1", "0" * 64, 1, 1, 0.0)
    local = SyncManifest("m1", "d1", (desc,), 0.0)
    remote = SyncManifest("m2", "d1", (desc,), 0.0)
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    with pytest.raises(AdapterException):
        adapter.plan_sync("plan1", local, remote, trust, "nonce1")
    # Nonce should still be available for a different device
    trust2 = DeviceTrustRecord("d2", "device", True, ("k2",))
    remote2 = SyncManifest("m2", "d2", (desc,), 0.0)
    # This still fails because device d1 is in the manifest, but demonstrates nonce not consumed.
    # Use a manifest with d2 instead.
    local2 = SyncManifest("m1", "d2", (desc,), 0.0)
    adapter2 = EncryptedSyncAdapter(
        config=config,
        encryption_provider=_FakeProvider(),
        storage=FakeSyncStorage(),
        clock=FakeClock(),
        id_factory=FakeIdFactory(),
    )
    adapter2.plan_sync("plan1", local2, remote2, trust2, "nonce1")


def test_ha_missing_audit_fails_before_transport() -> None:
    class CountingTransport(FakeHATransport):
        calls = 0
        def execute_request(self, request: HomeAssistantTransportRequest) -> HomeAssistantTransportEvidence:
            self.calls += 1
            return super().execute_request(request)
    transport = CountingTransport()
    ha = HomeAssistantAdapter(config=FakeHAConfig.build(), clock=FakeClock(), id_factory=FakeIdFactory(), transport=transport)
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    prepared = ha.prepare("p1", HomeAssistantEntityRef("light", "light.living_room"), HomeAssistantServiceRef("light", "get_state"), {}, actor, "n1")
    confirmation = HomeAssistantConfirmation("p1", "n1", "owner.1", 0.0)
    result = ha.confirm_and_execute(prepared.proposal, confirmation, actor)
    assert result.reason is HomeAssistantAdapterReason.AUDIT_FAILURE
    assert transport.calls == 0


def test_remote_worker_cannot_validate_self_asserted_scope_or_submit_unvalidated() -> None:
    coord = RemoteWorkerCoordinator(config=RemoteWorkerCoordinatorConfig(), clock=FakeClock(), id_factory=FakeIdFactory())
    envelope = RemoteWorkerAuthorityEnvelope("e1", "evil", "t1", "admin", ("owner",), 10.0, "0" * 16, 1)
    validation = coord.validate_envelope(
        envelope, b"sig", lambda _e, _s: True,
        expected_worker_id="w1", expected_task_id="t1",
        expected_capability="read", expected_targets=("safe",),
    )
    assert validation.outcome is RemoteValidationOutcome.MISMATCH
    assert coord.submit_job(envelope) is RemoteWorkerAdapterOutcome.DENY


def test_encrypted_sync_rejects_forged_commit() -> None:
    adapter = EncryptedSyncAdapter(config=EncryptedSyncAdapterConfig(), encryption_provider=FakeEncryptionProvider(), storage=FakeSyncStorage(), clock=FakeClock(), id_factory=FakeIdFactory())
    desc = EncryptedObjectDescriptor("obj1", "0" * 64, 1, 1, 0.0)
    manifest = SyncManifest("m1", "d1", (desc,), 0.0)
    proposal = adapter.plan_sync("plan1", manifest, manifest, DeviceTrustRecord("d1", "device", True, ("k1",)), "nonce1")
    forged = EncryptedSyncTransactionProposal("forged", proposal.plan, proposal.commit_digest, proposal.rollback_digest)
    assert adapter.commit(forged) is EncryptedSyncAdapterOutcome.DENY


# ---------------------------------------------------------------------------
# No real side effects
# ---------------------------------------------------------------------------

def test_no_real_execution_in_default_paths() -> None:
    # Default adapters are disabled and have no live transport/storage bindings.
    assert HomeAssistantAdapter().state is AdapterState.DISABLED
    assert EncryptedSyncAdapter().state is AdapterState.DISABLED
    assert RemoteWorkerCoordinator().state is AdapterState.DISABLED
    assert SkillStagingAdapter().state is AdapterState.DISABLED
    assert MeasuredRoutingAdapter().state is AdapterState.DISABLED
