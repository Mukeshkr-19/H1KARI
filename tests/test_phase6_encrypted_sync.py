"""Synthetic test suite for encrypted sync planning contracts (Phase 6 Part D)."""

import hashlib
import pytest

from core.phase6_ecosystem.encrypted_sync import (
    DeviceTrustRecord,
    EncryptedObjectDescriptor,
    EncryptedSyncPlanner,
    EncryptionProviderInterface,
    SyncDecision,
    SyncManifest,
    SyncOutcome,
    SyncReason,
)


class DummyEncryptionProvider(EncryptionProviderInterface):

    def __init__(self, verified: bool = True, descriptors_verified: bool = True):
        self._verified = verified
        self._descriptors_verified = descriptors_verified

    @property
    def is_verified(self) -> bool:
        return self._verified

    def verify_descriptor(self, descriptor: EncryptedObjectDescriptor) -> bool:
        return self._verified and self._descriptors_verified


def _make_descriptor(
    obj_id: str,
    version: int = 1,
    content: bytes = b"data",
    updated_at: float = 1000.0,
    is_tombstone: bool = False,
    authority_state: str = "accepted",
) -> EncryptedObjectDescriptor:
    digest = hashlib.sha256(content).hexdigest()
    return EncryptedObjectDescriptor(
        object_id=obj_id,
        ciphertext_digest=digest,
        size_bytes=len(content),
        version=version,
        updated_at=updated_at,
        is_tombstone=is_tombstone,
        authority_state=authority_state,
    )


def test_deterministic_clean_sync_plan():
    planner = EncryptedSyncPlanner()
    provider = DummyEncryptionProvider(verified=True)
    device_trust = DeviceTrustRecord("dev_remote_01", "MacBook Pro", is_verified=True, trusted_keys=("k1",))

    d_local_1 = _make_descriptor("obj_1", version=1, content=b"v1", updated_at=1000.0)
    d_remote_2 = _make_descriptor("obj_2", version=1, content=b"v2", updated_at=1000.0)

    m_local = SyncManifest("m_local", "dev_local", (d_local_1,), 1000.0)
    m_remote = SyncManifest("m_remote", "dev_remote_01", (d_remote_2,), 1000.0)

    dec = planner.generate_sync_plan("plan_1", m_local, m_remote, device_trust, provider, 1050.0)

    assert dec.outcome == SyncOutcome.ALLOW
    assert dec.reason == SyncReason.CLEAN_SYNC
    assert dec.plan is not None
    assert len(dec.plan.objects_to_upload) == 1
    assert dec.plan.objects_to_upload[0].object_id == "obj_1"
    assert len(dec.plan.objects_to_download) == 1
    assert dec.plan.objects_to_download[0].object_id == "obj_2"
    assert len(dec.plan.conflicts) == 0


def test_concurrent_update_conflict_detection():
    planner = EncryptedSyncPlanner()
    provider = DummyEncryptionProvider(verified=True)
    device_trust = DeviceTrustRecord("dev_remote_01", "MacBook Pro", is_verified=True, trusted_keys=("k1",))

    # Same object_id and same version=1, but different content/digest!
    d_local = _make_descriptor("obj_conflict", version=1, content=b"local_changes", updated_at=1000.0)
    d_remote = _make_descriptor("obj_conflict", version=1, content=b"remote_changes", updated_at=1000.0)

    m_local = SyncManifest("m_local", "dev_local", (d_local,), 1000.0)
    m_remote = SyncManifest("m_remote", "dev_remote_01", (d_remote,), 1000.0)

    dec = planner.generate_sync_plan("plan_2", m_local, m_remote, device_trust, provider, 1050.0)

    assert dec.outcome == SyncOutcome.CONFLICT
    assert dec.reason == SyncReason.CONFLICTS_DETECTED
    assert dec.plan is not None
    assert len(dec.plan.conflicts) == 1
    assert dec.plan.conflicts[0].reason == "concurrent_update"


def test_brain_authority_mismatch_creates_conflict():
    planner = EncryptedSyncPlanner()
    provider = DummyEncryptionProvider(verified=True)
    device_trust = DeviceTrustRecord("dev_remote_01", "MacBook Pro", is_verified=True, trusted_keys=("k1",))

    # Differing authority_state (accepted vs pending)
    d_local = _make_descriptor("obj_auth", version=2, content=b"data_2", updated_at=1000.0, authority_state="accepted")
    d_remote = _make_descriptor("obj_auth", version=1, content=b"data_1", updated_at=900.0, authority_state="pending")

    m_local = SyncManifest("m_local", "dev_local", (d_local,), 1000.0)
    m_remote = SyncManifest("m_remote", "dev_remote_01", (d_remote,), 1000.0)

    dec = planner.generate_sync_plan("plan_3", m_local, m_remote, device_trust, provider, 1050.0)

    assert dec.outcome == SyncOutcome.CONFLICT
    assert len(dec.plan.conflicts) == 1
    assert dec.plan.conflicts[0].reason == "authority_mismatch"


def test_unverified_provider_and_untrusted_device_rejection():
    planner = EncryptedSyncPlanner()

    m_local = SyncManifest("m_local", "dev_local", (), 1000.0)
    m_remote = SyncManifest("m_remote", "dev_remote", (), 1000.0)

    # 1. Unverified provider -> DENY
    prov_unver = DummyEncryptionProvider(verified=False)
    trust_ok = DeviceTrustRecord("dev_remote", "Device", is_verified=True, trusted_keys=("k1",))
    dec1 = planner.generate_sync_plan("p1", m_local, m_remote, trust_ok, prov_unver, 1000.0)
    assert dec1.outcome == SyncOutcome.DENY
    assert dec1.reason == SyncReason.UNVERIFIED_PROVIDER

    # 2. Untrusted device -> DENY
    prov_ok = DummyEncryptionProvider(verified=True)
    trust_unver = DeviceTrustRecord("dev_remote", "Device", is_verified=False, trusted_keys=("k1",))
    dec2 = planner.generate_sync_plan("p2", m_local, m_remote, trust_unver, prov_ok, 1000.0)
    assert dec2.outcome == SyncOutcome.DENY
    assert dec2.reason == SyncReason.UNTRUSTED_DEVICE


def test_explicit_tombstone_handling():
    d_tombstone = _make_descriptor("obj_deleted", version=3, content=b"deleted", updated_at=1000.0, is_tombstone=True)
    assert d_tombstone.is_tombstone is True


def test_content_free_repr_sync():
    planner = EncryptedSyncPlanner()
    desc = _make_descriptor("obj_1")
    manifest = SyncManifest("m1", "dev1", (desc,), 1000.0)
    trust = DeviceTrustRecord("dev1", "Laptop", True, ("k1",))

    assert repr(planner) == "EncryptedSyncPlanner()"
    assert repr(desc) == "EncryptedObjectDescriptor()"
    assert repr(manifest) == "SyncManifest()"
    assert repr(trust) == "DeviceTrustRecord()"


def test_remote_manifest_must_match_trusted_device() -> None:
    planner = EncryptedSyncPlanner()
    local = SyncManifest("m_local", "dev_local", (), 1000.0)
    remote = SyncManifest("m_remote", "dev_remote", (), 1000.0)
    trust = DeviceTrustRecord("different_device", "Other", True, ("k1",))
    decision = planner.generate_sync_plan(
        "plan_device", local, remote, trust, DummyEncryptionProvider(), 1001.0
    )
    assert decision.outcome is SyncOutcome.DENY
    assert decision.reason is SyncReason.UNTRUSTED_DEVICE


def test_every_descriptor_must_be_verified() -> None:
    planner = EncryptedSyncPlanner()
    descriptor = _make_descriptor("obj_verify")
    local = SyncManifest("m_local", "dev_local", (descriptor,), 1000.0)
    remote = SyncManifest("m_remote", "dev_remote", (), 1000.0)
    trust = DeviceTrustRecord("dev_remote", "Remote", True, ("k1",))
    decision = planner.generate_sync_plan(
        "plan_verify", local, remote, trust,
        DummyEncryptionProvider(descriptors_verified=False), 1001.0,
    )
    assert decision.outcome is SyncOutcome.DENY
    assert decision.reason is SyncReason.INVALID_MANIFEST


def test_duplicate_object_ids_rejected() -> None:
    descriptor = _make_descriptor("obj_duplicate")
    with pytest.raises(ValueError, match="duplicate"):
        SyncManifest("m_dup", "dev_local", (descriptor, descriptor), 1000.0)
