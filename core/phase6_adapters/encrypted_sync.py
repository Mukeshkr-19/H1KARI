"""Encrypted sync optional adapter with fail-closed storage protocol.

This module composes the pure sync planner from ``core.phase6_ecosystem.encrypted_sync``
with injected storage and crypto adapters.  Default construction leaves the
adapter disabled.  It never inspects plaintext.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import FrozenSet, Mapping, Optional, Sequence, Tuple

from core.phase6_ecosystem.encrypted_sync import (
    DeviceTrustRecord,
    EncryptedObjectDescriptor,
    EncryptedSyncPlanner,
    EncryptionProviderInterface,
    SyncDecision,
    SyncManifest,
    SyncOutcome,
    SyncPlan,
    SyncReason,
)

from core.phase6_adapters.contracts import AdapterException, AdapterReason, AdapterState


class EncryptedSyncAdapterReason(StrEnum):
    """Fixed reason codes for the encrypted sync adapter."""

    OK = "ok"
    DISABLED = "disabled"
    INVALID_CONFIGURATION = "invalid_configuration"
    MISSING_DEPENDENCY = "missing_dependency"
    UNVERIFIED_PROVIDER = "unverified_provider"
    UNTRUSTED_DEVICE = "untrusted_device"
    BOUNDS_EXCEEDED = "bounds_exceeded"
    CONFLICTS_DETECTED = "conflicts_detected"
    REPLAY_DETECTED = "replay_detected"
    REVOKED_DEVICE = "revoked_device"
    STAGING_FAILED = "staging_failed"
    COMMIT_FAILED = "commit_failed"
    ROLLBACK_FAILED = "rollback_failed"
    NONCE_REPLAY = "nonce_replay"


class EncryptedSyncAdapterOutcome(StrEnum):
    """Fixed outcomes for the encrypted sync adapter."""

    ALLOW = "allow"
    CONFLICT = "conflict"
    DENY = "deny"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class EncryptedSyncAdapterConfig:
    """Explicit configuration enabling the encrypted sync adapter.

    Hard object/count/total-byte bounds are declared here and enforced before
    any storage adapter is invoked.
    """

    max_objects: int = 1000
    max_total_bytes: int = 1_000_000_000
    max_object_bytes: int = 100_000_000
    max_version: int = 1_000_000
    max_devices: int = 16

    def __post_init__(self) -> None:
        for name, value in (
            ("max_objects", self.max_objects),
            ("max_total_bytes", self.max_total_bytes),
            ("max_object_bytes", self.max_object_bytes),
            ("max_version", self.max_version),
            ("max_devices", self.max_devices),
        ):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"invalid {name}")

    def __repr__(self) -> str:
        return "EncryptedSyncAdapterConfig()"


@dataclass(frozen=True)
class EncryptedSyncTransactionProposal:
    """Content-addressed staging proposal for a sync operation."""

    transaction_id: str
    plan: SyncPlan
    commit_digest: str
    rollback_digest: str

    def __repr__(self) -> str:
        return "EncryptedSyncTransactionProposal()"


class EncryptedSyncStorageInterface:
    """Injected storage adapter interface (no real implementation)."""

    def stage(self, transaction_id: str, plan: SyncPlan) -> Tuple[bool, str]:
        raise NotImplementedError("storage adapter is injected")

    def commit(self, transaction_id: str) -> Tuple[bool, str]:
        raise NotImplementedError("storage adapter is injected")

    def rollback(self, transaction_id: str) -> Tuple[bool, str]:
        raise NotImplementedError("storage adapter is injected")


class EncryptedSyncAdapter:
    """Disabled-by-default encrypted sync adapter.

    All storage, crypto, clock, and ID factory behavior is injected.  The
    adapter operates on opaque ciphertext descriptors only and never inspects
    plaintext.
    """

    def __init__(
        self,
        *,
        config: Optional[EncryptedSyncAdapterConfig] = None,
        encryption_provider: Optional[EncryptionProviderInterface] = None,
        storage: Optional[EncryptedSyncStorageInterface] = None,
        clock: Optional[object] = None,
        id_factory: Optional[object] = None,
    ) -> None:
        self._config = config
        self._encryption_provider = encryption_provider
        self._storage = storage
        self._clock = clock
        self._id_factory = id_factory
        self._planner = EncryptedSyncPlanner()
        self._consumed_nonces: set[str] = set()
        self._revoked_devices: set[str] = set()
        self._committed_transactions: set[str] = set()
        self._staged_transactions: dict[str, EncryptedSyncTransactionProposal] = {}

    @property
    def state(self) -> AdapterState:
        return AdapterState.ENABLED if self._config is not None else AdapterState.DISABLED

    def _now(self) -> float:
        if self._clock is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        now = self._clock() if callable(self._clock) else float(self._clock)
        if not isinstance(now, (int, float)):
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return float(now)

    def _next_id(self) -> str:
        if self._id_factory is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return str(self._id_factory() if callable(self._id_factory) else self._id_factory)

    def _digest_plan(self, plan: SyncPlan) -> str:
        """Return a content-addressed digest of a sync plan."""
        parts = [plan.plan_id, str(plan.created_at)]
        for descriptor in sorted(plan.objects_to_upload + plan.objects_to_download, key=lambda d: d.object_id):
            parts.extend([descriptor.object_id, descriptor.ciphertext_digest])
        for conflict in sorted(plan.conflicts, key=lambda c: c.object_id):
            parts.extend([conflict.object_id, conflict.reason])
        payload = "|".join(parts).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _check_bounds(self, local_manifest: SyncManifest, remote_manifest: SyncManifest) -> EncryptedSyncAdapterReason:
        assert self._config is not None
        all_descriptors = local_manifest.descriptors + remote_manifest.descriptors
        if len(all_descriptors) > self._config.max_objects:
            return EncryptedSyncAdapterReason.BOUNDS_EXCEEDED
        total = sum(d.size_bytes for d in all_descriptors)
        if total > self._config.max_total_bytes:
            return EncryptedSyncAdapterReason.BOUNDS_EXCEEDED
        for d in all_descriptors:
            if d.size_bytes > self._config.max_object_bytes:
                return EncryptedSyncAdapterReason.BOUNDS_EXCEEDED
            if d.version > self._config.max_version:
                return EncryptedSyncAdapterReason.BOUNDS_EXCEEDED
        return EncryptedSyncAdapterReason.OK

    def plan_sync(
        self,
        plan_id: str,
        local_manifest: SyncManifest,
        remote_manifest: SyncManifest,
        device_trust: DeviceTrustRecord,
        nonce: str,
    ) -> EncryptedSyncTransactionProposal:
        """Generate a sync plan and stage it atomically."""
        if self.state is AdapterState.DISABLED:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        assert self._config is not None
        if self._encryption_provider is None or self._storage is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        if device_trust.device_id in self._revoked_devices:
            raise AdapterException(AdapterReason.INVALID_INPUT)
        bound = self._check_bounds(local_manifest, remote_manifest)
        if bound is not EncryptedSyncAdapterReason.OK:
            raise AdapterException(AdapterReason.INVALID_INPUT)
        if nonce in self._consumed_nonces:
            raise AdapterException(AdapterReason.INVALID_INPUT)
        self._consumed_nonces.add(nonce)
        decision = self._planner.generate_sync_plan(
            plan_id=plan_id,
            local_manifest=local_manifest,
            remote_manifest=remote_manifest,
            device_trust=device_trust,
            encryption_provider=self._encryption_provider,
            now=self._now(),
        )
        if decision.outcome is not SyncOutcome.ALLOW or decision.plan is None or decision.plan.conflicts:
            raise AdapterException(
                AdapterReason.POLICY_DENIED,
                self._map_sync_reason(decision.reason),
            )
        transaction_id = self._next_id()
        content_digest = self._digest_plan(decision.plan)
        proposal = EncryptedSyncTransactionProposal(
            transaction_id=transaction_id,
            plan=decision.plan,
            commit_digest="sha256." + content_digest,
            rollback_digest="sha256." + hashlib.sha256((content_digest + ":rollback").encode("utf-8")).hexdigest(),
        )
        ok, _ = self._storage.stage(transaction_id, decision.plan)
        if not ok:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        self._staged_transactions[transaction_id] = proposal
        return proposal

    def commit(self, proposal: EncryptedSyncTransactionProposal) -> EncryptedSyncAdapterOutcome:
        """Atomically commit a staged sync transaction."""
        if self.state is AdapterState.DISABLED:
            return EncryptedSyncAdapterOutcome.UNAVAILABLE
        if self._storage is None:
            return EncryptedSyncAdapterOutcome.UNAVAILABLE
        expected = self._staged_transactions.get(proposal.transaction_id)
        if expected != proposal or proposal.transaction_id in self._committed_transactions:
            return EncryptedSyncAdapterOutcome.DENY
        digest = self._digest_plan(proposal.plan)
        if proposal.commit_digest != "sha256." + digest:
            return EncryptedSyncAdapterOutcome.DENY
        ok, _ = self._storage.commit(proposal.transaction_id)
        if not ok:
            return EncryptedSyncAdapterOutcome.DENY
        self._committed_transactions.add(proposal.transaction_id)
        return EncryptedSyncAdapterOutcome.ALLOW

    def rollback(self, proposal: EncryptedSyncTransactionProposal) -> EncryptedSyncAdapterOutcome:
        """Rollback a staged sync transaction."""
        if self.state is AdapterState.DISABLED:
            return EncryptedSyncAdapterOutcome.UNAVAILABLE
        if self._storage is None:
            return EncryptedSyncAdapterOutcome.UNAVAILABLE
        expected = self._staged_transactions.get(proposal.transaction_id)
        if expected != proposal:
            return EncryptedSyncAdapterOutcome.DENY
        ok, _ = self._storage.rollback(proposal.transaction_id)
        if not ok:
            return EncryptedSyncAdapterOutcome.DENY
        return EncryptedSyncAdapterOutcome.ALLOW

    def _map_sync_reason(self, reason: SyncReason) -> EncryptedSyncAdapterReason:
        return {
            SyncReason.UNVERIFIED_PROVIDER: EncryptedSyncAdapterReason.UNVERIFIED_PROVIDER,
            SyncReason.UNTRUSTED_DEVICE: EncryptedSyncAdapterReason.UNTRUSTED_DEVICE,
            SyncReason.REPLAY_DETECTED: EncryptedSyncAdapterReason.REPLAY_DETECTED,
            SyncReason.BOUNDS_EXCEEDED: EncryptedSyncAdapterReason.BOUNDS_EXCEEDED,
            SyncReason.INVALID_MANIFEST: EncryptedSyncAdapterReason.INVALID_CONFIGURATION,
        }.get(reason, EncryptedSyncAdapterReason.INVALID_CONFIGURATION)

    def revoke_device(self, device_id: str) -> None:
        self._revoked_devices.add(device_id)

    def __repr__(self) -> str:
        return "EncryptedSyncAdapter()"
