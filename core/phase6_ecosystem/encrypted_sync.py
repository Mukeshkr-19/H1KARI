"""Optional user-controlled encrypted sync planning contracts for Phase 6.

Provides pure, deterministic sync manifest comparison, conflict detection,
and sync plan generation without implementing storage, network, or encryption algorithms.
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Optional, Sequence, Tuple

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

MAX_SYNC_OBJECTS = 1000
MAX_SYNC_BYTES = 1_000_000_000


class SyncOutcome(StrEnum):
    """Fixed outcome for sync planning evaluation."""

    ALLOW = "allow"
    CONFLICT = "conflict"
    DENY = "deny"


class SyncReason(StrEnum):
    """Fixed, non-attributable reason codes for sync planning."""

    CLEAN_SYNC = "clean_sync"
    CONFLICTS_DETECTED = "conflicts_detected"
    UNVERIFIED_PROVIDER = "unverified_provider"
    UNTRUSTED_DEVICE = "untrusted_device"
    REPLAY_DETECTED = "replay_detected"
    DOWNGRADE_DETECTED = "downgrade_detected"
    BOUNDS_EXCEEDED = "bounds_exceeded"
    INVALID_MANIFEST = "invalid_manifest"


@dataclass(frozen=True)
class EncryptedObjectDescriptor:
    """Metadata descriptor for an opaque encrypted object (zero plaintext)."""

    object_id: str
    ciphertext_digest: str  # SHA-256 hex digest of opaque ciphertext
    size_bytes: int
    version: int
    updated_at: float
    is_tombstone: bool = False
    authority_state: str = "accepted"  # "accepted", "pending", "rejected"

    def __post_init__(self) -> None:
        if not isinstance(self.object_id, str) or not _IDENTIFIER_RE.fullmatch(self.object_id):
            raise ValueError("invalid object_id")
        if not isinstance(self.ciphertext_digest, str) or not _SHA256_HEX_RE.fullmatch(self.ciphertext_digest):
            raise ValueError("ciphertext_digest must be a 64-character lowercase hex string")
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        if self.size_bytes > 100_000_000:
            raise ValueError("object size exceeds bound")
        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be a positive integer >= 1")
        if self.authority_state not in ("accepted", "pending", "rejected"):
            raise ValueError("invalid authority_state")
        if not isinstance(self.updated_at, (int, float)) or isinstance(self.updated_at, bool) or not math.isfinite(self.updated_at):
            raise ValueError("invalid updated_at")
        if not isinstance(self.is_tombstone, bool):
            raise ValueError("invalid tombstone flag")

    def __repr__(self) -> str:
        return "EncryptedObjectDescriptor()"


@dataclass(frozen=True)
class SyncManifest:
    """Manifest of encrypted object descriptors for a sync snapshot."""

    manifest_id: str
    device_id: str
    descriptors: Tuple[EncryptedObjectDescriptor, ...]
    created_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_id, str) or not _IDENTIFIER_RE.fullmatch(self.manifest_id):
            raise ValueError("invalid manifest_id")
        if not isinstance(self.device_id, str) or not _IDENTIFIER_RE.fullmatch(self.device_id):
            raise ValueError("invalid device_id")
        if len(self.descriptors) > MAX_SYNC_OBJECTS:
            raise ValueError(f"object count exceeds maximum allowed ({MAX_SYNC_OBJECTS})")
        if not isinstance(self.descriptors, tuple) or any(
            not isinstance(descriptor, EncryptedObjectDescriptor)
            for descriptor in self.descriptors
        ):
            raise ValueError("invalid descriptors")
        ids = [descriptor.object_id for descriptor in self.descriptors]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate object_id")
        if sum(descriptor.size_bytes for descriptor in self.descriptors) > MAX_SYNC_BYTES:
            raise ValueError("sync bytes exceed bound")
        if not isinstance(self.created_at, (int, float)) or isinstance(self.created_at, bool) or not math.isfinite(self.created_at):
            raise ValueError("invalid created_at")

        sorted_desc = tuple(sorted(self.descriptors, key=lambda d: d.object_id))
        if self.descriptors != sorted_desc:
            object.__setattr__(self, "descriptors", sorted_desc)

    def descriptor_map(self) -> Mapping[str, EncryptedObjectDescriptor]:
        return {d.object_id: d for d in self.descriptors}

    def __repr__(self) -> str:
        return "SyncManifest()"


@dataclass(frozen=True)
class DeviceTrustRecord:
    """Explicit device trust record supplied by caller."""

    device_id: str
    device_name: str
    is_verified: bool
    trusted_keys: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not _IDENTIFIER_RE.fullmatch(self.device_id):
            raise ValueError("invalid device_id")
        if not isinstance(self.device_name, str) or not self.device_name or len(self.device_name) > 120:
            raise ValueError("invalid device_name")
        if not isinstance(self.is_verified, bool):
            raise ValueError("invalid verification flag")
        if not isinstance(self.trusted_keys, tuple) or not self.trusted_keys:
            raise ValueError("trusted_keys required")

    def __repr__(self) -> str:
        return "DeviceTrustRecord()"


@dataclass(frozen=True)
class SyncConflict:
    """Explicit record of a sync conflict requiring manual resolution."""

    object_id: str
    local_descriptor: EncryptedObjectDescriptor
    remote_descriptor: EncryptedObjectDescriptor
    reason: str  # "concurrent_update", "version_mismatch", "tombstone_conflict", "authority_mismatch"

    def __repr__(self) -> str:
        return "SyncConflict()"


@dataclass(frozen=True)
class SyncPlan:
    """Deterministic plan for synchronizing local and remote manifests."""

    plan_id: str
    objects_to_upload: Tuple[EncryptedObjectDescriptor, ...]
    objects_to_download: Tuple[EncryptedObjectDescriptor, ...]
    conflicts: Tuple[SyncConflict, ...]
    created_at: float

    def __repr__(self) -> str:
        return "SyncPlan()"


@dataclass(frozen=True)
class SyncDecision:
    """Decision wrapper for sync planning evaluation."""

    outcome: SyncOutcome
    reason: SyncReason
    plan: Optional[SyncPlan] = None

    def __repr__(self) -> str:
        return "SyncDecision()"


class EncryptionProviderInterface:
    """Injected interface for caller-supplied encryption provider (zero implementation)."""

    @property
    def is_verified(self) -> bool:
        raise NotImplementedError

    def verify_descriptor(self, descriptor: EncryptedObjectDescriptor) -> bool:
        raise NotImplementedError


class EncryptedSyncPlanner:
    """Pure, deterministic planner for encrypted sync manifests."""

    def generate_sync_plan(
        self,
        plan_id: str,
        local_manifest: SyncManifest,
        remote_manifest: SyncManifest,
        device_trust: DeviceTrustRecord,
        encryption_provider: EncryptionProviderInterface,
        now: float,
    ) -> SyncDecision:
        """Compare local and remote manifests deterministically without network or storage calls."""
        # 1. Verify encryption provider
        try:
            provider_verified = bool(encryption_provider.is_verified)
        except Exception:
            provider_verified = False
        if not provider_verified:
            return SyncDecision(SyncOutcome.DENY, SyncReason.UNVERIFIED_PROVIDER)

        # 2. Verify device trust
        if not device_trust.is_verified or device_trust.device_id != remote_manifest.device_id:
            return SyncDecision(SyncOutcome.DENY, SyncReason.UNTRUSTED_DEVICE)

        if not _IDENTIFIER_RE.fullmatch(plan_id):
            return SyncDecision(SyncOutcome.DENY, SyncReason.INVALID_MANIFEST)
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            return SyncDecision(SyncOutcome.DENY, SyncReason.INVALID_MANIFEST)
        if local_manifest.created_at > now or remote_manifest.created_at > now:
            return SyncDecision(SyncOutcome.DENY, SyncReason.REPLAY_DETECTED)

        for descriptor in local_manifest.descriptors + remote_manifest.descriptors:
            try:
                valid_descriptor = bool(encryption_provider.verify_descriptor(descriptor))
            except Exception:
                valid_descriptor = False
            if not valid_descriptor:
                return SyncDecision(SyncOutcome.DENY, SyncReason.INVALID_MANIFEST)

        local_map = local_manifest.descriptor_map()
        remote_map = remote_manifest.descriptor_map()

        all_ids = sorted(set(local_map.keys()) | set(remote_map.keys()))

        to_upload: list[EncryptedObjectDescriptor] = []
        to_download: list[EncryptedObjectDescriptor] = []
        conflicts: list[SyncConflict] = []

        for obj_id in all_ids:
            local_desc = local_map.get(obj_id)
            remote_desc = remote_map.get(obj_id)

            if local_desc is not None and remote_desc is None:
                # Local only -> candidate for upload
                to_upload.append(local_desc)
            elif local_desc is None and remote_desc is not None:
                # Remote only -> candidate for download
                to_download.append(remote_desc)
            elif local_desc is not None and remote_desc is not None:
                # Both exist -> check digests & versions
                if local_desc.ciphertext_digest == remote_desc.ciphertext_digest:
                    # Identical content -> no action needed
                    continue

                # Check Brain authority state preservation
                if local_desc.authority_state != remote_desc.authority_state:
                    conflicts.append(
                        SyncConflict(
                            object_id=obj_id,
                            local_descriptor=local_desc,
                            remote_descriptor=remote_desc,
                            reason="authority_mismatch",
                        )
                    )
                    continue

                # Version check
                if remote_desc.version > local_desc.version:
                    if remote_desc.updated_at < local_desc.updated_at:
                        # Downgrade / timestamp anomaly -> conflict
                        conflicts.append(
                            SyncConflict(
                                object_id=obj_id,
                                local_descriptor=local_desc,
                                remote_descriptor=remote_desc,
                                reason="version_mismatch",
                            )
                        )
                    else:
                        to_download.append(remote_desc)
                elif local_desc.version > remote_desc.version:
                    if local_desc.updated_at < remote_desc.updated_at:
                        # Timestamp anomaly -> conflict
                        conflicts.append(
                            SyncConflict(
                                object_id=obj_id,
                                local_descriptor=local_desc,
                                remote_descriptor=remote_desc,
                                reason="version_mismatch",
                            )
                        )
                    else:
                        to_upload.append(local_desc)
                else:
                    # Equal version but different digest -> concurrent modification conflict!
                    conflicts.append(
                        SyncConflict(
                            object_id=obj_id,
                            local_descriptor=local_desc,
                            remote_descriptor=remote_desc,
                            reason="concurrent_update",
                        )
                    )

        plan = SyncPlan(
            plan_id=plan_id,
            objects_to_upload=tuple(to_upload),
            objects_to_download=tuple(to_download),
            conflicts=tuple(conflicts),
            created_at=now,
        )

        if conflicts:
            return SyncDecision(SyncOutcome.CONFLICT, SyncReason.CONFLICTS_DETECTED, plan)

        return SyncDecision(SyncOutcome.ALLOW, SyncReason.CLEAN_SYNC, plan)

    def __repr__(self) -> str:
        return "EncryptedSyncPlanner()"
