"""Reviewed skill staging optional adapter.

Composes the signed skill-package contracts from
``core.phase6_ecosystem.skill_package`` and ``core.phase6_ecosystem.skill_review``
with an injected archive reader and an isolated staging root.  Default
construction leaves the adapter disabled.  No real filesystem mutation occurs.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from core.phase6_ecosystem.skill_package import (
    PackageValidationOutcome,
    PackageValidationReason,
    PublisherTrust,
    SignatureEvidence,
    SkillInstallPlan,
    SkillPackageCandidate,
    SkillPackageManifest,
    SkillPermissionDeclaration,
    SkillRevocation,
    SkillRollbackMetadata,
)
from core.phase6_ecosystem.skill_review import (
    SkillEvolutionCoordinator,
    SkillEvolutionStateRecord,
    SkillLifecycleState,
    SkillReview,
)

from core.phase6_adapters.contracts import AdapterException, AdapterOutcome, AdapterReason, AdapterState


class SkillStagingAdapterReason(StrEnum):
    """Fixed reason codes for the skill staging adapter."""

    OK = "ok"
    DISABLED = "disabled"
    INVALID_CONFIGURATION = "invalid_configuration"
    MISSING_DEPENDENCY = "missing_dependency"
    PATH_TRAVERSAL = "path_traversal"
    ABSOLUTE_PATH = "absolute_path"
    SYMLINK_REJECTED = "symlink_rejected"
    HARDLINK_REJECTED = "hardlink_rejected"
    DEVICE_NODE_REJECTED = "device_node_rejected"
    CASE_COLLISION = "case_collision"
    EXECUTABLE_MAGIC = "executable_magic"
    NUL_BYTE = "nul_byte"
    EXCESSIVE_NESTING = "excessive_nesting"
    COMPRESSION_BOMB = "compression_bomb"
    SIZE_EXCEEDED = "size_exceeded"
    FILE_COUNT_EXCEEDED = "file_count_exceeded"
    DIGEST_MISMATCH = "digest_mismatch"
    PERMISSION_WIDENING = "permission_widening"
    UNAPPROVED_DEPENDENCIES = "unapproved_dependencies"
    INVALID_SIGNATURE = "invalid_signature"
    UNTRUSTED_PUBLISHER = "untrusted_publisher"
    REVOKED = "revoked"
    MISSING_ROLLBACK = "missing_rollback"
    SELF_APPROVAL_DENIED = "self_approval_denied"


class SkillStagingAdapterOutcome(StrEnum):
    """Fixed outcomes for the skill staging adapter."""

    ALLOW = "allow"
    DENY = "deny"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SkillStagingAdapterConfig:
    """Explicit configuration enabling the skill staging adapter."""

    staging_root: str = "/tmp/hikari_skill_staging"
    max_files: int = 100
    max_total_bytes: int = 10_485_760  # 10 MB
    max_file_bytes: int = 2_097_152   # 2 MB
    max_path_depth: int = 10
    max_compression_ratio: float = 100.0

    def __post_init__(self) -> None:
        for name, value in (
            ("max_files", self.max_files),
            ("max_total_bytes", self.max_total_bytes),
            ("max_file_bytes", self.max_file_bytes),
            ("max_path_depth", self.max_path_depth),
        ):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"invalid {name}")
        if not isinstance(self.max_compression_ratio, (int, float)) or self.max_compression_ratio <= 0:
            raise ValueError("invalid max_compression_ratio")

    def __repr__(self) -> str:
        return "SkillStagingAdapterConfig()"


@dataclass(frozen=True)
class SkillStagingProposal:
    """Content-addressed installation transaction proposal."""

    transaction_id: str
    install_plan: SkillInstallPlan
    rollback_plan: Optional[SkillInstallPlan]
    content_digest: str

    def __repr__(self) -> str:
        return "SkillStagingProposal()"


class ArchiveReaderInterface:
    """Injected archive reader interface (no real implementation)."""

    def read_entries(self, archive_bytes: bytes) -> Mapping[str, bytes]:
        raise NotImplementedError("archive reader is injected")


class SkillStagingAdapter:
    """Disabled-by-default reviewed skill staging adapter.

    The archive reader, signature verifier, review coordinator, clock, and ID
    factory are injected.  No production skill is mutated.  No auto-install or
    self-approval is permitted.
    """

    def __init__(
        self,
        *,
        config: Optional[SkillStagingAdapterConfig] = None,
        archive_reader: Optional[ArchiveReaderInterface] = None,
        signature_verifier: Optional[Callable[[bytes, SignatureEvidence], bool]] = None,
        clock: Optional[object] = None,
        id_factory: Optional[object] = None,
    ) -> None:
        self._config = config
        self._archive_reader = archive_reader
        self._signature_verifier = signature_verifier
        self._clock = clock
        self._id_factory = id_factory
        self._coordinator = SkillEvolutionCoordinator()

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

    def _validate_archive_entries(self, entries: Mapping[str, bytes]) -> Tuple[bool, SkillStagingAdapterReason]:
        assert self._config is not None
        if len(entries) > self._config.max_files:
            return False, SkillStagingAdapterReason.FILE_COUNT_EXCEEDED
        total = 0
        seen_lower: set[str] = set()
        for path, content in entries.items():
            if not isinstance(path, str) or not isinstance(content, bytes):
                return False, SkillStagingAdapterReason.INVALID_CONFIGURATION
            if not path:
                return False, SkillStagingAdapterReason.INVALID_CONFIGURATION
            if path.startswith("/") or re.match(r"^[a-zA-Z]:", path):
                return False, SkillStagingAdapterReason.ABSOLUTE_PATH
            if "\x00" in path or b"\x00" in content:
                return False, SkillStagingAdapterReason.NUL_BYTE
            if ".." in path.split("/"):
                return False, SkillStagingAdapterReason.PATH_TRAVERSAL
            parts = [p for p in path.split("/") if p and p != "."]
            if len(parts) > self._config.max_path_depth:
                return False, SkillStagingAdapterReason.EXCESSIVE_NESTING
            if len(path) > 4096 or len(content) > self._config.max_file_bytes:
                return False, SkillStagingAdapterReason.SIZE_EXCEEDED
            if content.startswith((b"\x7fELF", b"MZ", b"\x00asm")):
                return False, SkillStagingAdapterReason.EXECUTABLE_MAGIC
            total += len(content)
            if total > self._config.max_total_bytes:
                return False, SkillStagingAdapterReason.SIZE_EXCEEDED
            lower = path.lower()
            if lower in seen_lower:
                return False, SkillStagingAdapterReason.CASE_COLLISION
            seen_lower.add(lower)
        # Compression-bomb guard: if the archive appears highly compressible,
        # flag it for review.  We do not decompress real archives here.
        if entries and total > 1024:
            # Heuristic: an uncompressed text-like payload should not expand
            # dramatically.  This is intentionally conservative, not a real test.
            if total > self._config.max_total_bytes // 2:
                pass  # bounded elsewhere
        return True, SkillStagingAdapterReason.OK

    def stage_installation(
        self,
        archive_bytes: bytes,
        candidate: SkillPackageCandidate,
        signature_evidence: SignatureEvidence,
        publisher_trust: PublisherTrust,
        review: SkillReview,
        revocations: Sequence[SkillRevocation],
        rollback_metadata: Optional[SkillRollbackMetadata],
        is_replacement: bool,
        actor_id: str,
    ) -> SkillStagingProposal:
        """Validate an archive and produce a content-addressed install proposal."""
        if self.state is AdapterState.DISABLED:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        assert self._config is not None
        if not isinstance(actor_id, str) or actor_id != review.reviewer_actor_id or review.reviewer_role != "owner":
            raise AdapterException(AdapterReason.POLICY_DENIED, SkillStagingAdapterReason.SELF_APPROVAL_DENIED)
        if self._archive_reader is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        if not isinstance(archive_bytes, bytes) or not archive_bytes or len(archive_bytes) > self._config.max_total_bytes:
            raise AdapterException(AdapterReason.INVALID_INPUT, SkillStagingAdapterReason.SIZE_EXCEEDED)
        entries = self._archive_reader.read_entries(archive_bytes)
        ok, reason = self._validate_archive_entries(entries)
        if not ok:
            raise AdapterException(AdapterReason.INVALID_INPUT, reason)
        if sum(len(content) for content in entries.values()) / len(archive_bytes) > self._config.max_compression_ratio:
            raise AdapterException(AdapterReason.INVALID_INPUT, SkillStagingAdapterReason.COMPRESSION_BOMB)
        expected_entries = dict(candidate.file_contents)
        if set(entries) != set(expected_entries) or any(entries[path] != expected_entries[path] for path in entries):
            raise AdapterException(AdapterReason.INVALID_INPUT, SkillStagingAdapterReason.DIGEST_MISMATCH)
        if self._signature_verifier is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        transaction_id = self._next_id()
        install_plan = SkillInstallPlan(
            plan_id=transaction_id + ".install",
            candidate=candidate,
            review=review,
            signature_evidence=signature_evidence,
            publisher_trust=publisher_trust,
            rollback_metadata=rollback_metadata,
        )
        outcome, pkg_reason, _ = install_plan.validate_plan(
            signature_verifier=self._signature_verifier,
            revocations=revocations,
            is_replacement=is_replacement,
            now=self._now(),
        )
        if outcome != PackageValidationOutcome.VALID:
            if pkg_reason is PackageValidationReason.PERMISSION_WIDENING:
                raise AdapterException(
                    AdapterReason.POLICY_DENIED,
                    SkillStagingAdapterReason.PERMISSION_WIDENING,
                )
            raise AdapterException(AdapterReason.INVALID_INPUT)
        # Rollback plan is only meaningful when caller supplies rollback metadata.
        if rollback_metadata is not None:
            rollback_plan = SkillInstallPlan(
                plan_id=transaction_id + ".rollback",
                candidate=candidate,
                review=review,
                signature_evidence=signature_evidence,
                publisher_trust=publisher_trust,
                rollback_metadata=None,
            )
        else:
            rollback_plan = None
        content_digest = candidate.manifest.canonical_digest()
        return SkillStagingProposal(
            transaction_id=transaction_id,
            install_plan=install_plan,
            rollback_plan=rollback_plan,
            content_digest=content_digest,
        )

    def __repr__(self) -> str:
        return "SkillStagingAdapter()"
