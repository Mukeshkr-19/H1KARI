"""Lifecycle and state-machine adversarial tests for Phase 6 adapters.

Covers injected registries, transaction lifecycles, canary flows, job
lifecycles, and archive entry-type rejection.  No real side effects.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Optional, Tuple

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
from core.phase6_adapters.home_assistant import (
    HomeAssistantAdapterOutcome,
    HomeAssistantAdapterReason,
    HomeAssistantTransportContract,
    HomeAssistantTransportEvidence,
    HomeAssistantTransportRequest,
)
from core.phase6_adapters.encrypted_sync import (
    DeviceTrustRegistry,
    EncryptedSyncAdapterOutcome,
    EncryptedSyncAdapterReason,
    EncryptedSyncStorageInterface,
    NonceReplayRegistry,
)
from core.phase6_adapters.remote_worker import (
    CancellationAcknowledgement,
    NonceStoreInterface,
    RemoteWorkerAdapterOutcome,
)
from core.phase6_adapters.skill_staging import (
    ArchiveEntry,
    ArchiveEntryKind,
    SkillStagingAdapterReason,
)
from core.phase6_adapters.measured_routing import (
    CanaryState,
    MeasuredRoutingAdapterOutcome,
    MeasuredRoutingAdapterReason,
)

from core.phase6_ecosystem.encrypted_sync import (
    DeviceTrustRecord,
    EncryptedObjectDescriptor,
    EncryptionProviderInterface,
    SyncManifest,
)
from core.phase6_ecosystem.home_assistant import (
    HomeAssistantCapabilityManifest,
    HomeAssistantConfirmation,
    HomeAssistantEntityRef,
    HomeAssistantServiceRef,
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


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start
    def __call__(self) -> float:
        return self.t
    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeId:
    def __init__(self) -> None:
        self.n = 0
    def __call__(self) -> str:
        self.n += 1
        return f"id{self.n}"


# ---------------------------------------------------------------------------
# Home Assistant lifecycle
# ---------------------------------------------------------------------------

class _HATransport(HomeAssistantTransportContract):
    def __init__(self, final_url: str = "https://hass.local:8123/api/", resolved_host: str = "hass.local") -> None:
        self.final_url = final_url
        self.resolved_host = resolved_host
        self.calls = 0

    def execute_request(self, request: HomeAssistantTransportRequest) -> HomeAssistantTransportEvidence:
        self.calls += 1
        return HomeAssistantTransportEvidence(
            observation_id="obs1",
            proposal_id=request.proposal_id,
            final_url=self.final_url,
            resolved_host=self.resolved_host,
            response_byte_count=2,
            elapsed_seconds=0.01,
            success_category="ok",
            failure_category=None,
            idempotency_contract_proven=False,
            observed_at=0.0,
        )


class _FailingTerminalAuditor:
    def __init__(self) -> None:
        self.calls = 0

    def record(self, **_record) -> None:
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("terminal audit unavailable")


def _ha_manifest() -> HomeAssistantCapabilityManifest:
    return HomeAssistantCapabilityManifest(
        allowed_domains=frozenset({"light"}),
        allowed_entities=frozenset({"light.living_room"}),
        allowed_services=frozenset({"turn_on", "get_state"}),
        sensitive_domains=frozenset({"lock"}),
        sensitive_entities=frozenset({"light.living_room"}),
        read_only_services=frozenset({"get_state"}),
    )


def test_ha_audit_missing_denies_before_transport() -> None:
    config = HomeAssistantAdapterConfig(base_url="https://hass.local:8123", manifest=_ha_manifest())
    transport = _HATransport()
    ha = HomeAssistantAdapter(config=config, clock=FakeClock(), id_factory=FakeId(), transport=transport)
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    prepared = ha.prepare("p1", HomeAssistantEntityRef("light", "light.living_room"), HomeAssistantServiceRef("light", "get_state"), {}, actor, "n1")
    confirmation = HomeAssistantConfirmation("p1", "n1", "owner.1", 0.0)
    result = ha.confirm_and_execute(prepared.proposal, confirmation, actor)
    assert result.outcome is HomeAssistantAdapterOutcome.DENY
    assert result.reason is HomeAssistantAdapterReason.AUDIT_FAILURE
    assert transport.calls == 0


def test_ha_final_host_mismatch_denies() -> None:
    config = HomeAssistantAdapterConfig(base_url="https://hass.local:8123", manifest=_ha_manifest())
    transport = _HATransport(final_url="https://evil.com/api/")
    ha = HomeAssistantAdapter(config=config, clock=FakeClock(), id_factory=FakeId(), auditor=_RecordingAuditor(), transport=transport)
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    prepared = ha.prepare("p1", HomeAssistantEntityRef("light", "light.living_room"), HomeAssistantServiceRef("light", "get_state"), {}, actor, "n1")
    confirmation = HomeAssistantConfirmation("p1", "n1", "owner.1", 0.0)
    result = ha.confirm_and_execute(prepared.proposal, confirmation, actor)
    assert result.outcome is HomeAssistantAdapterOutcome.DENY
    assert result.reason is HomeAssistantAdapterReason.REDIRECT_TARGET_REJECTED


def test_ha_resolved_host_mismatch_denies() -> None:
    config = HomeAssistantAdapterConfig(base_url="https://hass.local:8123", manifest=_ha_manifest())
    transport = _HATransport(resolved_host="other.local")
    ha = HomeAssistantAdapter(config=config, clock=FakeClock(), id_factory=FakeId(), auditor=_RecordingAuditor(), transport=transport)
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    prepared = ha.prepare("p1", HomeAssistantEntityRef("light", "light.living_room"), HomeAssistantServiceRef("light", "get_state"), {}, actor, "n1")
    confirmation = HomeAssistantConfirmation("p1", "n1", "owner.1", 0.0)
    result = ha.confirm_and_execute(prepared.proposal, confirmation, actor)
    assert result.outcome is HomeAssistantAdapterOutcome.DENY
    assert result.reason is HomeAssistantAdapterReason.DNS_HOST_MISMATCH


def test_ha_response_too_large_denies() -> None:
    config = HomeAssistantAdapterConfig(base_url="https://hass.local:8123", manifest=_ha_manifest(), max_response_bytes=1)
    transport = _HATransport()
    ha = HomeAssistantAdapter(config=config, clock=FakeClock(), id_factory=FakeId(), auditor=_RecordingAuditor(), transport=transport)
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    prepared = ha.prepare("p1", HomeAssistantEntityRef("light", "light.living_room"), HomeAssistantServiceRef("light", "get_state"), {}, actor, "n1")
    confirmation = HomeAssistantConfirmation("p1", "n1", "owner.1", 0.0)
    result = ha.confirm_and_execute(prepared.proposal, confirmation, actor)
    assert result.outcome is HomeAssistantAdapterOutcome.DENY
    assert result.reason is HomeAssistantAdapterReason.RESPONSE_TOO_LARGE


def test_ha_deadline_expired_before_transport_denies() -> None:
    clock = FakeClock(start=100.0)
    config = HomeAssistantAdapterConfig(base_url="https://hass.local:8123", manifest=_ha_manifest(), request_timeout_seconds=1.0)
    transport = _HATransport()
    ha = HomeAssistantAdapter(config=config, clock=clock, id_factory=FakeId(), auditor=_RecordingAuditor(), transport=transport)
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    prepared = ha.prepare("p1", HomeAssistantEntityRef("light", "light.living_room"), HomeAssistantServiceRef("light", "get_state"), {}, actor, "n1")
    clock.advance(2.0)  # deadline (101.0) is now in the past
    confirmation = HomeAssistantConfirmation("p1", "n1", "owner.1", 100.0)
    result = ha.confirm_and_execute(prepared.proposal, confirmation, actor)
    assert result.outcome is HomeAssistantAdapterOutcome.DENY
    assert result.reason is HomeAssistantAdapterReason.TIMEOUT_EXCEEDED
    assert transport.calls == 0


def test_ha_state_change_single_invocation_no_retry() -> None:
    config = HomeAssistantAdapterConfig(base_url="https://hass.local:8123", manifest=_ha_manifest())
    transport = _HATransport()
    ha = HomeAssistantAdapter(config=config, clock=FakeClock(), id_factory=FakeId(), auditor=_RecordingAuditor(), transport=transport)
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    prepared = ha.prepare("p1", HomeAssistantEntityRef("light", "light.living_room"), HomeAssistantServiceRef("light", "turn_on"), {}, actor, "n1")
    confirmation = HomeAssistantConfirmation("p1", "n1", "owner.1", 0.0)
    result = ha.confirm_and_execute(prepared.proposal, confirmation, actor)
    assert result.outcome is HomeAssistantAdapterOutcome.ALLOW
    assert transport.calls == 1


def test_ha_terminal_audit_failure_never_reports_success() -> None:
    config = HomeAssistantAdapterConfig(
        base_url="https://hass.local:8123", manifest=_ha_manifest()
    )
    transport = _HATransport()
    ha = HomeAssistantAdapter(
        config=config,
        clock=FakeClock(),
        id_factory=FakeId(),
        auditor=_FailingTerminalAuditor(),
        transport=transport,
    )
    actor = ActorContext(actor_id="owner.1", actor=Actor.OWNER, session_id="s1")
    prepared = ha.prepare(
        "p1",
        HomeAssistantEntityRef("light", "light.living_room"),
        HomeAssistantServiceRef("light", "get_state"),
        {},
        actor,
        "n1",
    )
    confirmation = HomeAssistantConfirmation("p1", "n1", "owner.1", 0.0)
    result = ha.confirm_and_execute(prepared.proposal, confirmation, actor)
    assert transport.calls == 1
    assert result.outcome is HomeAssistantAdapterOutcome.DENY
    assert result.reason is HomeAssistantAdapterReason.AUDIT_FAILURE


def test_ha_malformed_port_denies_safely() -> None:
    manifest = _ha_manifest()
    for bad in ["https://hass.local:70000", "https://hass.local:-1", "https://hass.local:abc"]:
        with pytest.raises(ValueError):
            HomeAssistantAdapterConfig(base_url=bad, manifest=manifest)


def test_ha_config_rejects_bool_and_nan() -> None:
    manifest = _ha_manifest()
    with pytest.raises(ValueError):
        HomeAssistantAdapterConfig(base_url="https://hass.local:8123", manifest=manifest, request_timeout_seconds=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        HomeAssistantAdapterConfig(base_url="https://hass.local:8123", manifest=manifest, max_response_bytes=float("nan"))  # type: ignore[arg-type]


class _RecordingAuditor:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
    def record(self, **record):
        self.records.append(record)


# ---------------------------------------------------------------------------
# Encrypted sync lifecycle
# ---------------------------------------------------------------------------

class _FakeProvider(EncryptionProviderInterface):
    @property
    def is_verified(self) -> bool:
        return True
    def verify_descriptor(self, descriptor):
        return True


class _FakeSyncStorage(EncryptedSyncStorageInterface):
    def __init__(self) -> None:
        self.staged: dict[str, Any] = {}
    def stage(self, transaction_id, plan):
        self.staged[transaction_id] = plan
        return True, "ok"
    def commit(self, transaction_id):
        return transaction_id in self.staged, "ok"
    def rollback(self, transaction_id):
        return transaction_id in self.staged, "ok"


def _descriptor() -> EncryptedObjectDescriptor:
    return EncryptedObjectDescriptor("obj1", "0" * 64, 1, 1, 0.0)


def test_sync_default_disabled() -> None:
    adapter = EncryptedSyncAdapter()
    assert adapter.state is AdapterState.DISABLED


def test_sync_injected_nonce_registry_blocks_replay() -> None:
    class Store(NonceReplayRegistry):
        def __init__(self):
            self._seen: set[str] = set()
        def is_consumed(self, nonce):
            return nonce in self._seen
        def consume(self, nonce):
            if nonce in self._seen:
                return False
            self._seen.add(nonce)
            return True
    config = EncryptedSyncAdapterConfig()
    adapter = EncryptedSyncAdapter(
        config=config,
        encryption_provider=_FakeProvider(),
        storage=_FakeSyncStorage(),
        nonce_registry=Store(),
        clock=FakeClock(),
        id_factory=FakeId(),
    )
    local = SyncManifest("m1", "d1", (_descriptor(),), 0.0)
    remote = SyncManifest("m2", "d1", (_descriptor(),), 0.0)
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    adapter.plan_sync("plan1", local, remote, trust, "nonce1")
    with pytest.raises(AdapterException) as exc:
        adapter.plan_sync("plan2", local, remote, trust, "nonce1")
    assert exc.value.reason is AdapterReason.INVALID_INPUT


def test_sync_device_revoked_before_stage_and_commit() -> None:
    class Revoked(DeviceTrustRegistry):
        def __init__(self):
            self._revoked: set[str] = set()
        def is_revoked(self, device_id):
            return device_id in self._revoked
        def revoke(self, device_id):
            self._revoked.add(device_id)
    reg = Revoked()
    adapter = EncryptedSyncAdapter(
        config=EncryptedSyncAdapterConfig(),
        encryption_provider=_FakeProvider(),
        storage=_FakeSyncStorage(),
        device_registry=reg,
        clock=FakeClock(),
        id_factory=FakeId(),
    )
    reg._revoked.add("d1")
    local = SyncManifest("m1", "d1", (_descriptor(),), 0.0)
    remote = SyncManifest("m2", "d1", (_descriptor(),), 0.0)
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    with pytest.raises(AdapterException) as exc:
        adapter.plan_sync("plan1", local, remote, trust, "nonce1")
    assert exc.value.detail is EncryptedSyncAdapterReason.REVOKED_DEVICE


def test_sync_forged_commit_digest_denies() -> None:
    adapter = EncryptedSyncAdapter(
        config=EncryptedSyncAdapterConfig(),
        encryption_provider=_FakeProvider(),
        storage=_FakeSyncStorage(),
        clock=FakeClock(),
        id_factory=FakeId(),
    )
    local = SyncManifest("m1", "d1", (_descriptor(),), 0.0)
    remote = SyncManifest("m2", "d1", (_descriptor(),), 0.0)
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    proposal = adapter.plan_sync("plan1", local, remote, trust, "nonce1")
    forged = type(proposal)(
        transaction_id=proposal.transaction_id,
        plan=proposal.plan,
        commit_digest=proposal.commit_digest + "00",
        rollback_digest=proposal.rollback_digest,
    )
    assert adapter.commit(forged) is EncryptedSyncAdapterOutcome.DENY


def test_sync_rollback_only_known_transaction() -> None:
    adapter = EncryptedSyncAdapter(
        config=EncryptedSyncAdapterConfig(),
        encryption_provider=_FakeProvider(),
        storage=_FakeSyncStorage(),
        clock=FakeClock(),
        id_factory=FakeId(),
    )
    local = SyncManifest("m1", "d1", (_descriptor(),), 0.0)
    remote = SyncManifest("m2", "d1", (_descriptor(),), 0.0)
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    proposal = adapter.plan_sync("plan1", local, remote, trust, "nonce1")
    assert adapter.rollback(proposal) is EncryptedSyncAdapterOutcome.ALLOW
    # Cannot commit after rollback.
    assert adapter.commit(proposal) is EncryptedSyncAdapterOutcome.DENY


def test_sync_unknown_transaction_id_rejected() -> None:
    adapter = EncryptedSyncAdapter(
        config=EncryptedSyncAdapterConfig(),
        encryption_provider=_FakeProvider(),
        storage=_FakeSyncStorage(),
        clock=FakeClock(),
        id_factory=FakeId(),
    )
    from core.phase6_adapters.encrypted_sync import EncryptedSyncTransactionProposal
    forged = EncryptedSyncTransactionProposal("unknown", None, "sha256.0" * 2, "sha256.1" * 2)  # type: ignore[arg-type]
    assert adapter.commit(forged) is EncryptedSyncAdapterOutcome.DENY
    assert adapter.rollback(forged) is EncryptedSyncAdapterOutcome.DENY


def test_sync_duplicate_commit_denied() -> None:
    adapter = EncryptedSyncAdapter(
        config=EncryptedSyncAdapterConfig(),
        encryption_provider=_FakeProvider(),
        storage=_FakeSyncStorage(),
        clock=FakeClock(),
        id_factory=FakeId(),
    )
    local = SyncManifest("m1", "d1", (_descriptor(),), 0.0)
    remote = SyncManifest("m2", "d1", (_descriptor(),), 0.0)
    trust = DeviceTrustRecord("d1", "device", True, ("k1",))
    proposal = adapter.plan_sync("plan1", local, remote, trust, "nonce1")
    assert adapter.commit(proposal) is EncryptedSyncAdapterOutcome.ALLOW
    assert adapter.commit(proposal) is EncryptedSyncAdapterOutcome.DENY


# ---------------------------------------------------------------------------
# Remote worker lifecycle
# ---------------------------------------------------------------------------

def test_remote_worker_unvalidated_submit_denied() -> None:
    coord = RemoteWorkerCoordinator(config=RemoteWorkerCoordinatorConfig(), clock=FakeClock(), id_factory=FakeId())
    envelope = RemoteWorkerAuthorityEnvelope("e1", "w1", "t1", "c1", ("target",), 10.0, "0" * 16, 1)
    assert coord.submit_job(envelope) is RemoteWorkerAdapterOutcome.DENY


def test_remote_worker_forged_validation_object_cannot_bypass() -> None:
    class AlwaysValid(NonceStoreInterface):
        def is_consumed(self, nonce):
            return False
        def consume(self, nonce):
            return True
    coord = RemoteWorkerCoordinator(config=RemoteWorkerCoordinatorConfig(), clock=FakeClock(), id_factory=FakeId(), nonce_store=AlwaysValid())
    envelope = RemoteWorkerAuthorityEnvelope("e1", "w1", "t1", "c1", ("target",), 10.0, "0" * 16, 1)
    validation = coord.validate_envelope(
        envelope, b"sig", lambda _e, _s: True,
        expected_worker_id="w1", expected_task_id="t1",
        expected_capability="c1", expected_targets=("target",),
    )
    assert validation.outcome is RemoteValidationOutcome.VALID
    # Tamper with envelope after validation
    forged = RemoteWorkerAuthorityEnvelope("e1", "evil", "t1", "c1", ("target",), 10.0, "0" * 16, 1)
    assert coord.submit_job(forged) is RemoteWorkerAdapterOutcome.DENY


def test_remote_worker_response_byte_and_budget_enforced() -> None:
    class Store(NonceStoreInterface):
        def __init__(self):
            self._seen: set[str] = set()
        def is_consumed(self, nonce):
            return nonce in self._seen
        def consume(self, nonce):
            if nonce in self._seen:
                return False
            self._seen.add(nonce)
            return True
    coord = RemoteWorkerCoordinator(
        config=RemoteWorkerCoordinatorConfig(max_response_size_bytes=4),
        clock=FakeClock(),
        id_factory=FakeId(),
        nonce_store=Store(),
    )
    envelope = RemoteWorkerAuthorityEnvelope("e1", "w1", "t1", "c1", ("target",), 10.0, "0" * 16, 2)
    validation = coord.validate_envelope(
        envelope, b"sig", lambda _e, _s: True,
        expected_worker_id="w1", expected_task_id="t1",
        expected_capability="c1", expected_targets=("target",),
    )
    assert validation.outcome is RemoteValidationOutcome.VALID
    assert coord.submit_job(envelope) is RemoteWorkerAdapterOutcome.ALLOW
    result = RemoteWorkerResult("r1", "e1", "w1", "t1", "too large", 0.0)
    acceptance = coord.accept_result(envelope, result, validation)
    assert acceptance.accepted_as_evidence is False


def test_remote_worker_cross_envelope_result_denied() -> None:
    class Store(NonceStoreInterface):
        def __init__(self):
            self._seen: set[str] = set()
        def is_consumed(self, nonce):
            return nonce in self._seen
        def consume(self, nonce):
            if nonce in self._seen:
                return False
            self._seen.add(nonce)
            return True
    coord = RemoteWorkerCoordinator(config=RemoteWorkerCoordinatorConfig(), clock=FakeClock(), id_factory=FakeId(), nonce_store=Store())
    envelope = RemoteWorkerAuthorityEnvelope("e1", "w1", "t1", "c1", ("target",), 10.0, "0" * 16, 2)
    validation = coord.validate_envelope(
        envelope, b"sig", lambda _e, _s: True,
        expected_worker_id="w1", expected_task_id="t1",
        expected_capability="c1", expected_targets=("target",),
    )
    assert coord.submit_job(envelope) is RemoteWorkerAdapterOutcome.ALLOW
    cross = RemoteWorkerResult("r1", "e2", "w1", "t1", "x", 0.0)
    acceptance = coord.accept_result(envelope, cross, validation)
    assert acceptance.accepted_as_evidence is False


def test_remote_worker_cancellation_acknowledgement_and_result_after_cancel() -> None:
    class Store(NonceStoreInterface):
        def __init__(self):
            self._seen: set[str] = set()
        def is_consumed(self, nonce):
            return nonce in self._seen
        def consume(self, nonce):
            if nonce in self._seen:
                return False
            self._seen.add(nonce)
            return True
    clock = FakeClock()
    coord = RemoteWorkerCoordinator(config=RemoteWorkerCoordinatorConfig(), clock=clock, id_factory=FakeId(), nonce_store=Store())
    envelope = RemoteWorkerAuthorityEnvelope("e1", "w1", "t1", "c1", ("target",), 10.0, "0" * 16, 2)
    validation = coord.validate_envelope(
        envelope, b"sig", lambda _e, _s: True,
        expected_worker_id="w1", expected_task_id="t1",
        expected_capability="c1", expected_targets=("target",),
    )
    assert coord.submit_job(envelope) is RemoteWorkerAdapterOutcome.ALLOW
    clock.advance(1.0)
    ack = CancellationAcknowledgement("e1", 1.0, "ack1")
    assert coord.request_cancel(envelope, ack) is RemoteWorkerAdapterOutcome.ALLOW
    result = RemoteWorkerResult("r1", "e1", "w1", "t1", "late", 0.5)
    acceptance = coord.accept_result(envelope, result, validation)
    assert acceptance.accepted_as_evidence is False


def test_remote_worker_job_history_bound_enforced() -> None:
    class Store(NonceStoreInterface):
        def __init__(self):
            self._seen: set[str] = set()
        def is_consumed(self, nonce):
            return nonce in self._seen
        def consume(self, nonce):
            if nonce in self._seen:
                return False
            self._seen.add(nonce)
            return True
    clock = FakeClock()
    coord = RemoteWorkerCoordinator(
        config=RemoteWorkerCoordinatorConfig(max_concurrent_jobs=100, max_job_history=4),
        clock=clock,
        id_factory=FakeId(),
        nonce_store=Store(),
    )
    for i in range(6):
        envelope = RemoteWorkerAuthorityEnvelope(f"e{i}", "w1", f"t{i}", "c1", ("target",), 10.0, f"{i:016d}", 1)
        coord.validate_envelope(
            envelope, b"sig", lambda _e, _s: True,
            expected_worker_id="w1", expected_task_id=f"t{i}",
            expected_capability="c1", expected_targets=("target",),
        )
        coord.submit_job(envelope)
    assert len(coord._jobs) == 4


# ---------------------------------------------------------------------------
# Skill staging lifecycle
# ---------------------------------------------------------------------------

class _EntryReader:
    def __init__(self, entries):
        self.entries = entries
    def read_entries(self, archive_bytes: bytes):
        return tuple(self.entries)


def _make_candidate() -> SkillPackageCandidate:
    content = b"print('ok')"
    digest = hashlib.sha256(content).hexdigest()
    manifest = SkillPackageManifest(
        package_id="pkg1",
        name="Skill",
        version="1.0.0",
        publisher_id="pub1",
        description="A skill",
        declared_permissions=(),
        files=(SkillFileDigest("skill.py", digest, len(content)),),
        dependencies=(),
        created_at=0.0,
    )
    return SkillPackageCandidate(manifest=manifest, file_contents={"skill.py": content})


def _make_review(candidate: SkillPackageCandidate) -> SkillReview:
    return SkillReview(
        review_id="rev1",
        package_id=candidate.manifest.package_id,
        package_digest=candidate.manifest.canonical_digest(),
        version="1.0.0",
        publisher_id="pub1",
        reviewed_permissions=(),
        reviewer_actor_id="owner.1",
        reviewer_role="owner",
        outcome="approved",
        reviewed_at=0.0,
    )


def test_skill_staging_rejects_symlink() -> None:
    reader = _EntryReader([ArchiveEntry("x.py", ArchiveEntryKind.SYMLINK, b"x", 1, 1, 0o644, False, None)])
    adapter = SkillStagingAdapter(
        config=SkillStagingAdapterConfig(),
        archive_reader=reader,
        clock=FakeClock(),
        id_factory=FakeId(),
        signature_verifier=lambda b, s: True,
    )
    candidate = _make_candidate()
    review = _make_review(candidate)
    with pytest.raises(AdapterException) as exc:
        adapter.stage_installation(b"arc", candidate, SignatureEvidence("pub1", "k1", "0" * 128), PublisherTrust("pub1", "trusted", ("k1",), ()), review, [], None, False, "owner.1")
    assert exc.value.detail is SkillStagingAdapterReason.SYMLINK_REJECTED


def test_skill_staging_rejects_hardlink_and_device() -> None:
    for kind in (ArchiveEntryKind.HARDLINK, ArchiveEntryKind.DEVICE):
        reader = _EntryReader([ArchiveEntry("x.py", kind, b"x", 1, 1, 0o644, False, None)])
        adapter = SkillStagingAdapter(config=SkillStagingAdapterConfig(), archive_reader=reader, clock=FakeClock(), id_factory=FakeId(), signature_verifier=lambda b, s: True)
        candidate = _make_candidate()
        review = _make_review(candidate)
        with pytest.raises(AdapterException):
            adapter.stage_installation(b"arc", candidate, SignatureEvidence("pub1", "k1", "0" * 128), PublisherTrust("pub1", "trusted", ("k1",), ()), review, [], None, False, "owner.1")


def test_skill_staging_self_approval_denied() -> None:
    content = b"print('ok')"
    digest = hashlib.sha256(content).hexdigest()
    manifest = SkillPackageManifest(
        package_id="owner.1",
        name="Skill",
        version="1.0.0",
        publisher_id="owner.1",
        description="A skill",
        declared_permissions=(),
        files=(SkillFileDigest("skill.py", digest, len(content)),),
        dependencies=(),
        created_at=0.0,
    )
    candidate = SkillPackageCandidate(manifest=manifest, file_contents={"skill.py": content})
    review = SkillReview(
        review_id="rev1",
        package_id="owner.1",
        package_digest=manifest.canonical_digest(),
        version="1.0.0",
        publisher_id="owner.1",
        reviewed_permissions=(),
        reviewer_actor_id="owner.1",
        reviewer_role="owner",
        outcome="approved",
        reviewed_at=0.0,
    )
    reader = _EntryReader([ArchiveEntry("skill.py", ArchiveEntryKind.REGULAR, content, len(content), len(content), 0o644, False, None)])
    adapter = SkillStagingAdapter(config=SkillStagingAdapterConfig(), archive_reader=reader, clock=FakeClock(), id_factory=FakeId(), signature_verifier=lambda b, s: True)
    with pytest.raises(AdapterException) as exc:
        adapter.stage_installation(b"arc", candidate, SignatureEvidence("owner.1", "k1", "0" * 128), PublisherTrust("owner.1", "trusted", ("k1",), ()), review, [], None, False, "owner.1")
    assert exc.value.detail is SkillStagingAdapterReason.SELF_APPROVAL_DENIED


def test_skill_staging_missing_rollback_on_replacement_denied() -> None:
    content = b"print('ok')"
    reader = _EntryReader([ArchiveEntry("skill.py", ArchiveEntryKind.REGULAR, content, len(content), len(content), 0o644, False, None)])
    adapter = SkillStagingAdapter(config=SkillStagingAdapterConfig(), archive_reader=reader, clock=FakeClock(), id_factory=FakeId(), signature_verifier=lambda b, s: True)
    candidate = _make_candidate()
    review = _make_review(candidate)
    with pytest.raises(AdapterException) as exc:
        adapter.stage_installation(b"arc", candidate, SignatureEvidence("pub1", "k1", "0" * 128), PublisherTrust("pub1", "trusted", ("k1",), ()), review, [], None, True, "owner.1")
    assert exc.value.detail is SkillStagingAdapterReason.MISSING_ROLLBACK


def test_skill_staging_archive_bomb_rejected() -> None:
    # Small archive payload whose ratio far exceeds the configured maximum.
    reader = _EntryReader([ArchiveEntry("big.bin", ArchiveEntryKind.REGULAR, b"x" * 1024, 1024, 1024, 0o644, False, None)])
    adapter = SkillStagingAdapter(config=SkillStagingAdapterConfig(max_compression_ratio=10.0), archive_reader=reader, clock=FakeClock(), id_factory=FakeId(), signature_verifier=lambda b, s: True)
    candidate = _make_candidate()
    review = _make_review(candidate)
    with pytest.raises(AdapterException) as exc:
        adapter.stage_installation(b"x", candidate, SignatureEvidence("pub1", "k1", "0" * 128), PublisherTrust("pub1", "trusted", ("k1",), ()), review, [], None, False, "owner.1")
    assert exc.value.detail is SkillStagingAdapterReason.COMPRESSION_BOMB


def test_skill_staging_case_collision_rejected() -> None:
    reader = _EntryReader([
        ArchiveEntry("skill.py", ArchiveEntryKind.REGULAR, b"a", 1, 1, 0o644, False, None),
        ArchiveEntry("SKILL.py", ArchiveEntryKind.REGULAR, b"b", 1, 1, 0o644, False, None),
    ])
    adapter = SkillStagingAdapter(config=SkillStagingAdapterConfig(), archive_reader=reader, clock=FakeClock(), id_factory=FakeId(), signature_verifier=lambda b, s: True)
    candidate = _make_candidate()
    review = _make_review(candidate)
    with pytest.raises(AdapterException) as exc:
        adapter.stage_installation(b"arc", candidate, SignatureEvidence("pub1", "k1", "0" * 128), PublisherTrust("pub1", "trusted", ("k1",), ()), review, [], None, False, "owner.1")
    assert exc.value.detail is SkillStagingAdapterReason.CASE_COLLISION


# ---------------------------------------------------------------------------
# Measured routing lifecycle
# ---------------------------------------------------------------------------

def _routing_scenario() -> EvaluationScenario:
    return EvaluationScenario(
        scenario_id="s1",
        required_capabilities=(ModelCapability.TEXT_GEN,),
        max_privacy_class=PrivacyClass.REMOTE_OK,
    )


def _local_candidate() -> ModelCandidate:
    return ModelCandidate(
        candidate_id="local1",
        provider_type="local_model",
        model_name="local",
        privacy_class=PrivacyClass.LOCAL_ONLY,
        capabilities=(ModelCapability.TEXT_GEN,),
        provenance_id="p1",
    )


def _local_measurement(**overrides) -> ModelMeasurement:
    base = {
        "candidate_id": "local1",
        "quality_score": 0.9,
        "safety_score": 0.9,
        "latency_ms": 100.0,
        "cost_usd": 0.0,
        "memory_mb": 1000.0,
        "reliability_score": 0.9,
        "measured_at": 0.0,
    }
    base.update(overrides)
    return ModelMeasurement(**base)


def test_routing_canary_confirmation_required_before_incumbent_update() -> None:
    adapter = MeasuredRoutingAdapter(config=MeasuredRoutingAdapterConfig(), clock=FakeClock(), id_factory=FakeId())
    scenario = _routing_scenario()
    local = _local_candidate()
    measurements = {"local1": _local_measurement()}
    result = adapter.evaluate(scenario, [local], measurements)
    assert result.canary_state is CanaryState.CANARY_PROPOSED
    # Without confirmation, last winner should remain unset.
    assert result.rollback_candidate_id is None


def test_routing_expired_canary_denies() -> None:
    clock = FakeClock(start=0.0)
    adapter = MeasuredRoutingAdapter(config=MeasuredRoutingAdapterConfig(canary_expiry_seconds=1.0), clock=clock, id_factory=FakeId())
    scenario = _routing_scenario()
    local = _local_candidate()
    result = adapter.evaluate(scenario, [local], {"local1": _local_measurement()})
    clock.advance(2.0)
    confirmed = adapter.confirm_canary(result.canary.proposal_id)
    assert confirmed.canary_state is CanaryState.EXPIRED
    assert confirmed.outcome is MeasuredRoutingAdapterOutcome.DENY


def test_routing_local_only_blocks_remote() -> None:
    adapter = MeasuredRoutingAdapter(config=MeasuredRoutingAdapterConfig(), clock=FakeClock(), id_factory=FakeId())
    scenario = EvaluationScenario(
        scenario_id="s1",
        required_capabilities=(ModelCapability.TEXT_GEN,),
        max_privacy_class=PrivacyClass.LOCAL_ONLY,
    )
    remote = ModelCandidate(
        candidate_id="remote1",
        provider_type="remote_provider",
        model_name="remote",
        privacy_class=PrivacyClass.REMOTE_OK,
        capabilities=(ModelCapability.TEXT_GEN,),
        provenance_id="p1",
    )
    measurements = {"remote1": ModelMeasurement("remote1", 0.9, 0.9, 100.0, 0.01, 1000.0, 0.9, 0.0)}
    result = adapter.evaluate(scenario, [remote], measurements)
    assert result.outcome is MeasuredRoutingAdapterOutcome.DENY


def test_routing_history_bound_enforced() -> None:
    adapter = MeasuredRoutingAdapter(
        config=MeasuredRoutingAdapterConfig(hysteresis_window_size=2, max_history_per_scenario=2),
        clock=FakeClock(),
        id_factory=FakeId(),
    )
    scenario = _routing_scenario()
    local = _local_candidate()
    for _ in range(5):
        result = adapter.evaluate(scenario, [local], {"local1": _local_measurement()})
        adapter.confirm_canary(result.canary.proposal_id)
    # History should be capped.
    assert len(adapter._history["s1"]) <= 2


def test_routing_stale_observation_rejected() -> None:
    clock = FakeClock(start=1000.0)
    adapter = MeasuredRoutingAdapter(config=MeasuredRoutingAdapterConfig(max_candidate_age_seconds=60.0), clock=clock, id_factory=FakeId())
    scenario = _routing_scenario()
    local = _local_candidate()
    stale = ModelMeasurement(
        candidate_id="local1",
        quality_score=0.9,
        safety_score=0.9,
        latency_ms=100.0,
        cost_usd=0.0,
        memory_mb=1000.0,
        reliability_score=0.9,
        measured_at=0.0,  # far older than max_candidate_age_seconds
    )
    result = adapter.evaluate(scenario, [local], {"local1": stale})
    assert result.outcome is MeasuredRoutingAdapterOutcome.DENY
    assert result.reason is MeasuredRoutingAdapterReason.NO_ELIGIBLE_CANDIDATE
