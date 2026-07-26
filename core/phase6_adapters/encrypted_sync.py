"""Encrypted sync optional adapter with fail-closed storage protocol.

This module composes the pure sync planner from ``core.phase6_ecosystem.encrypted_sync``
with injected durable registries and atomic storage.  Default construction
leaves the adapter disabled.  It never inspects plaintext.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Optional, Sequence, Tuple

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
    STALE_TRANSACTION = "stale_transaction"
    TRANSACTION_NOT_STAGED = "transaction_not_staged"
    DIGEST_MISMATCH = "digest_mismatch"
    ALREADY_COMMITTED = "already_committed"
    ALREADY_ROLLED_BACK = "already_rolled_back"


class EncryptedSyncAdapterOutcome(StrEnum):
    """Fixed outcomes for the encrypted sync adapter."""

    ALLOW = "allow"
    CONFLICT = "conflict"
    DENY = "deny"
    UNAVAILABLE = "unavailable"


class EncryptedSyncTransactionState(StrEnum):
    """Durable transaction lifecycle states."""

    PLANNED = "planned"
    STAGED = "staged"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    DENIED = "denied"


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
            _reject_bool(value, name)
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


@dataclass(frozen=True)
class EncryptedSyncTransactionRecord:
    """Durable transaction state record."""

    transaction_id: str
    proposal: EncryptedSyncTransactionProposal
    device_id: str
    state: EncryptedSyncTransactionState
    created_at: float

    def __repr__(self) -> str:
        return "EncryptedSyncTransactionRecord()"


class EncryptedSyncStorageInterface(ABC):
    """Injected atomic storage adapter interface (no real implementation)."""

    @abstractmethod
    def stage(self, transaction_id: str, plan: SyncPlan) -> Tuple[bool, str]:
        ...

    @abstractmethod
    def commit(self, transaction_id: str) -> Tuple[bool, str]:
        ...

    @abstractmethod
    def rollback(self, transaction_id: str) -> Tuple[bool, str]:
        ...


class NonceReplayRegistry(ABC):
    """Injected durable nonce registry (no real implementation)."""

    @abstractmethod
    def is_consumed(self, nonce: str) -> bool:
        ...

    @abstractmethod
    def consume(self, nonce: str) -> bool:
        ...


class DeviceTrustRegistry(ABC):
    """Injected durable device-trust registry (no real implementation)."""

    @abstractmethod
    def is_revoked(self, device_id: str) -> bool:
        ...

    def revoke(self, device_id: str) -> None:
        """Default revocation hook. Subclasses may override."""
        raise NotImplementedError("revoke must be provided by the injected registry")


class TransactionRegistry(ABC):
    """Injected durable transaction registry (no real implementation)."""

    @abstractmethod
    def get(self, transaction_id: str) -> Optional[EncryptedSyncTransactionRecord]:
        ...

    @abstractmethod
    def put(self, record: EncryptedSyncTransactionRecord) -> bool:
        ...


class _InMemoryNonceRegistry(NonceReplayRegistry):
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_consumed(self, nonce: str) -> bool:
        return nonce in self._seen

    def consume(self, nonce: str) -> bool:
        if nonce in self._seen:
            return False
        self._seen.add(nonce)
        return True


class _InMemoryDeviceRegistry(DeviceTrustRegistry):
    def __init__(self) -> None:
        self._revoked: set[str] = set()

    def is_revoked(self, device_id: str) -> bool:
        return device_id in self._revoked

    def revoke(self, device_id: str) -> None:
        self._revoked.add(device_id)


class _InMemoryTransactionRegistry(TransactionRegistry):
    def __init__(self) -> None:
        self._records: dict[str, EncryptedSyncTransactionRecord] = {}

    def get(self, transaction_id: str) -> Optional[EncryptedSyncTransactionRecord]:
        return self._records.get(transaction_id)

    def put(self, record: EncryptedSyncTransactionRecord) -> bool:
        self._records[record.transaction_id] = record
        return True


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
        nonce_registry: Optional[NonceReplayRegistry] = None,
        device_registry: Optional[DeviceTrustRegistry] = None,
        transaction_registry: Optional[TransactionRegistry] = None,
        clock: Optional[object] = None,
        id_factory: Optional[object] = None,
    ) -> None:
        self._config = config
        self._encryption_provider = encryption_provider
        self._storage = storage
        self._clock = clock
        self._id_factory = id_factory
        self._planner = EncryptedSyncPlanner()
        self._nonce_registry = nonce_registry if nonce_registry is not None else _InMemoryNonceRegistry()
        self._device_registry = device_registry if device_registry is not None else _InMemoryDeviceRegistry()
        self._transaction_registry = transaction_registry if transaction_registry is not None else _InMemoryTransactionRegistry()

    @property
    def state(self) -> AdapterState:
        return AdapterState.ENABLED if self._config is not None else AdapterState.DISABLED

    def _now(self) -> float:
        if self._clock is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        now = self._clock() if callable(self._clock) else float(self._clock)
        if not isinstance(now, (int, float)) or isinstance(now, bool):
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
        """Generate a sync plan, stage it atomically, and return a proposal."""
        if self.state is AdapterState.DISABLED:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        assert self._config is not None
        if self._encryption_provider is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        if self._storage is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        now = self._now()

        # Device revocation recheck before staging.
        if self._device_registry.is_revoked(device_trust.device_id):
            raise AdapterException(AdapterReason.INVALID_INPUT, EncryptedSyncAdapterReason.REVOKED_DEVICE)

        bound = self._check_bounds(local_manifest, remote_manifest)
        if bound is not EncryptedSyncAdapterReason.OK:
            raise AdapterException(AdapterReason.INVALID_INPUT, bound)

        # Durable nonce consumption.
        if self._nonce_registry.is_consumed(nonce):
            raise AdapterException(AdapterReason.INVALID_INPUT, EncryptedSyncAdapterReason.REPLAY_DETECTED)
        if not self._nonce_registry.consume(nonce):
            raise AdapterException(AdapterReason.INVALID_INPUT, EncryptedSyncAdapterReason.REPLAY_DETECTED)

        decision = self._planner.generate_sync_plan(
            plan_id=plan_id,
            local_manifest=local_manifest,
            remote_manifest=remote_manifest,
            device_trust=device_trust,
            encryption_provider=self._encryption_provider,
            now=now,
        )
        if decision.outcome is not SyncOutcome.ALLOW or decision.plan is None:
            # Conflicts surface as DENY before any staging.
            raise AdapterException(
                AdapterReason.POLICY_DENIED,
                self._map_sync_reason(decision.reason),
            )
        if decision.plan.conflicts:
            raise AdapterException(
                AdapterReason.POLICY_DENIED,
                EncryptedSyncAdapterReason.CONFLICTS_DETECTED,
            )

        transaction_id = self._next_id()
        content_digest = self._digest_plan(decision.plan)
        proposal = EncryptedSyncTransactionProposal(
            transaction_id=transaction_id,
            plan=decision.plan,
            commit_digest="sha256." + content_digest,
            rollback_digest="sha256." + hashlib.sha256((content_digest + ":rollback").encode("utf-8")).hexdigest(),
        )

        # Log the planned state before attempting atomic storage staging.
        planned_record = EncryptedSyncTransactionRecord(
            transaction_id=transaction_id,
            proposal=proposal,
            device_id=device_trust.device_id,
            state=EncryptedSyncTransactionState.PLANNED,
            created_at=now,
        )
        self._transaction_registry.put(planned_record)

        ok, _ = self._storage.stage(transaction_id, decision.plan)
        if not ok:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY, EncryptedSyncAdapterReason.STAGING_FAILED)

        staged_record = EncryptedSyncTransactionRecord(
            transaction_id=transaction_id,
            proposal=proposal,
            device_id=device_trust.device_id,
            state=EncryptedSyncTransactionState.STAGED,
            created_at=now,
        )
        self._transaction_registry.put(staged_record)
        return proposal

    def commit(self, proposal: EncryptedSyncTransactionProposal) -> EncryptedSyncAdapterOutcome:
        """Atomically commit a staged sync transaction."""
        if self.state is AdapterState.DISABLED:
            return EncryptedSyncAdapterOutcome.UNAVAILABLE
        if self._storage is None:
            return EncryptedSyncAdapterOutcome.UNAVAILABLE

        record = self._transaction_registry.get(proposal.transaction_id)
        if record is None or record.proposal != proposal:
            return EncryptedSyncAdapterOutcome.DENY
        if record.state is EncryptedSyncTransactionState.COMMITTED:
            return EncryptedSyncAdapterOutcome.DENY
        if record.state is EncryptedSyncTransactionState.ROLLED_BACK:
            return EncryptedSyncAdapterOutcome.DENY
        if record.state is not EncryptedSyncTransactionState.STAGED:
            return EncryptedSyncAdapterOutcome.DENY

        # Recompute and compare commit digest.
        digest = self._digest_plan(proposal.plan)
        if proposal.commit_digest != "sha256." + digest:
            return EncryptedSyncAdapterOutcome.DENY

        # Device revocation recheck before commit.
        if self._device_registry.is_revoked(record.device_id):
            return EncryptedSyncAdapterOutcome.DENY

        ok, _ = self._storage.commit(proposal.transaction_id)
        if not ok:
            return EncryptedSyncAdapterOutcome.DENY

        self._transaction_registry.put(
            EncryptedSyncTransactionRecord(
                transaction_id=proposal.transaction_id,
                proposal=proposal,
                device_id=record.device_id,
                state=EncryptedSyncTransactionState.COMMITTED,
                created_at=record.created_at,
            )
        )
        return EncryptedSyncAdapterOutcome.ALLOW

    def rollback(self, proposal: EncryptedSyncTransactionProposal) -> EncryptedSyncAdapterOutcome:
        """Rollback a staged or committed sync transaction."""
        if self.state is AdapterState.DISABLED:
            return EncryptedSyncAdapterOutcome.UNAVAILABLE
        if self._storage is None:
            return EncryptedSyncAdapterOutcome.UNAVAILABLE

        record = self._transaction_registry.get(proposal.transaction_id)
        if record is None or record.proposal != proposal:
            return EncryptedSyncAdapterOutcome.DENY
        if record.state not in (EncryptedSyncTransactionState.STAGED, EncryptedSyncTransactionState.COMMITTED):
            return EncryptedSyncAdapterOutcome.DENY

        ok, _ = self._storage.rollback(proposal.transaction_id)
        if not ok:
            return EncryptedSyncAdapterOutcome.DENY

        self._transaction_registry.put(
            EncryptedSyncTransactionRecord(
                transaction_id=proposal.transaction_id,
                proposal=proposal,
                device_id=record.device_id,
                state=EncryptedSyncTransactionState.ROLLED_BACK,
                created_at=record.created_at,
            )
        )
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
        """Revoke a device through the injected registry.

        Callers with a custom registry should revoke directly on that registry.
        This convenience method delegates when the registry supports it.
        """
        try:
            self._device_registry.revoke(device_id)
        except NotImplementedError as exc:
            raise AdapterException(AdapterReason.INVALID_CONFIGURATION, EncryptedSyncAdapterReason.INVALID_CONFIGURATION) from exc

    def __repr__(self) -> str:
        return "EncryptedSyncAdapter()"


def _reject_bool(value: object, name: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be a boolean")
