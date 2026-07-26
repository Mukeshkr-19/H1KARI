"""Reviewed skill staging optional adapter.

Composes the signed skill-package contracts from
``core.phase6_ecosystem.skill_package`` and ``core.phase6_ecosystem.skill_review``
with an injected archive reader and an isolated staging root.  Default
construction leaves the adapter disabled.  No real filesystem mutation occurs.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Optional, Sequence, Tuple

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
    UNKNOWN_ENTRY_TYPE = "unknown_entry_type"
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
    INVALID_MODE = "invalid_mode"
    UNICODE_CONFUSABLE = "unicode_confusable"


class SkillStagingAdapterOutcome(StrEnum):
    """Fixed outcomes for the skill staging adapter."""

    ALLOW = "allow"
    DENY = "deny"
    UNAVAILABLE = "unavailable"


class ArchiveEntryKind(StrEnum):
    """Kind of an archive entry."""

    REGULAR = "regular"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    DEVICE = "device"
    OTHER = "other"


@dataclass(frozen=True)
class ArchiveEntry:
    """Entry-level metadata for an archive member.

    Content bytes are populated only for regular files.
    """

    normalized_path: str
    kind: ArchiveEntryKind
    content: Optional[bytes]
    uncompressed_size: int
    compressed_size: int
    mode: int
    executable: bool
    link_target: Optional[str] = None

    def __repr__(self) -> str:
        return "ArchiveEntry()"


class ArchiveEntryReaderInterface(ABC):
    """Injected archive reader that returns entry metadata (no implementation)."""

    @abstractmethod
    def read_entries(self, archive_bytes: bytes) -> Tuple[ArchiveEntry, ...]:
        ...


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
        _reject_bool(self.max_files, "max_files")
        _reject_bool(self.max_file_bytes, "max_file_bytes")
        for name, value in (
            ("max_files", self.max_files),
            ("max_total_bytes", self.max_total_bytes),
            ("max_file_bytes", self.max_file_bytes),
            ("max_path_depth", self.max_path_depth),
        ):
            _reject_bool(value, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"invalid {name}")
        if not isinstance(self.max_total_bytes, int) or self.max_total_bytes <= 0:
            raise ValueError("invalid max_total_bytes")
        _reject_bool_nan(self.max_compression_ratio, "max_compression_ratio")
        if self.max_compression_ratio <= 0:
            raise ValueError("invalid max_compression_ratio")
        if self.max_file_bytes > self.max_total_bytes:
            raise ValueError("max_file_bytes cannot exceed max_total_bytes")

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
        archive_reader: Optional[ArchiveEntryReaderInterface] = None,
        signature_verifier: Optional[Any] = None,
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
        if not isinstance(now, (int, float)) or isinstance(now, bool):
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return float(now)

    def _next_id(self) -> str:
        if self._id_factory is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        return str(self._id_factory() if callable(self._id_factory) else self._id_factory)

    def _validate_archive_entries(self, entries: Tuple[ArchiveEntry, ...], archive_size: int) -> Tuple[bool, SkillStagingAdapterReason]:
        assert self._config is not None
        if len(entries) > self._config.max_files:
            return False, SkillStagingAdapterReason.FILE_COUNT_EXCEEDED
        seen_norm: set[str] = set()
        seen_lower: set[str] = set()
        total_uncompressed = 0
        for entry in entries:
            path = entry.normalized_path
            if not isinstance(path, str) or not path:
                return False, SkillStagingAdapterReason.INVALID_CONFIGURATION
            # Reject NUL bytes and control characters in paths.
            if "\x00" in path or _has_control_chars(path):
                return False, SkillStagingAdapterReason.NUL_BYTE
            # Unicode confusables / non-canonical normalization.
            try:
                normalized = unicodedata.normalize("NFC", path)
            except Exception:
                return False, SkillStagingAdapterReason.UNICODE_CONFUSABLE
            if path != normalized:
                return False, SkillStagingAdapterReason.UNICODE_CONFUSABLE
            if any(unicodedata.combining(ch) > 0 for ch in path):
                # Reject visually confusable combining marks in paths.
                return False, SkillStagingAdapterReason.UNICODE_CONFUSABLE

            if path.startswith("/") or re.match(r"^[a-zA-Z]:", path):
                return False, SkillStagingAdapterReason.ABSOLUTE_PATH
            parts = [p for p in path.split("/") if p and p != "."]
            if ".." in parts or any(p == ".." for p in parts):
                return False, SkillStagingAdapterReason.PATH_TRAVERSAL
            if len(parts) > self._config.max_path_depth:
                return False, SkillStagingAdapterReason.EXCESSIVE_NESTING
            if path.endswith("/") or path.endswith("\\"):
                return False, SkillStagingAdapterReason.PATH_TRAVERSAL

            # Entry-type rejection.
            if entry.kind is ArchiveEntryKind.SYMLINK:
                return False, SkillStagingAdapterReason.SYMLINK_REJECTED
            if entry.kind is ArchiveEntryKind.HARDLINK:
                return False, SkillStagingAdapterReason.HARDLINK_REJECTED
            if entry.kind is ArchiveEntryKind.DEVICE:
                return False, SkillStagingAdapterReason.DEVICE_NODE_REJECTED
            if entry.kind not in (ArchiveEntryKind.REGULAR, ArchiveEntryKind.DIRECTORY):
                return False, SkillStagingAdapterReason.UNKNOWN_ENTRY_TYPE

            # Mode / executable indication.
            if entry.mode & 0o4000 or entry.mode & 0o2000:
                # setuid/setgid not permitted
                return False, SkillStagingAdapterReason.INVALID_MODE
            if entry.executable:
                return False, SkillStagingAdapterReason.EXECUTABLE_MAGIC

            # Per-entry compressed size bound.
            if entry.compressed_size > self._config.max_file_bytes:
                return False, SkillStagingAdapterReason.SIZE_EXCEEDED

            if entry.kind is ArchiveEntryKind.REGULAR:
                if entry.content is None:
                    return False, SkillStagingAdapterReason.DIGEST_MISMATCH
                if len(entry.content) > self._config.max_file_bytes:
                    return False, SkillStagingAdapterReason.SIZE_EXCEEDED
                if len(entry.content) != entry.uncompressed_size:
                    return False, SkillStagingAdapterReason.DIGEST_MISMATCH
                if entry.content.startswith((b"\x7fELF", b"MZ", b"\x00asm", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")):
                    return False, SkillStagingAdapterReason.EXECUTABLE_MAGIC
                total_uncompressed += entry.uncompressed_size

            if entry.uncompressed_size > self._config.max_file_bytes:
                return False, SkillStagingAdapterReason.SIZE_EXCEEDED

            if path in seen_norm:
                return False, SkillStagingAdapterReason.CASE_COLLISION
            seen_norm.add(path)
            lower = path.lower()
            if lower in seen_lower:
                return False, SkillStagingAdapterReason.CASE_COLLISION
            seen_lower.add(lower)

        if total_uncompressed > self._config.max_total_bytes:
            return False, SkillStagingAdapterReason.SIZE_EXCEEDED
        if archive_size > 0 and total_uncompressed / archive_size > self._config.max_compression_ratio:
            return False, SkillStagingAdapterReason.COMPRESSION_BOMB
        if total_uncompressed > self._config.max_total_bytes:
            return False, SkillStagingAdapterReason.SIZE_EXCEEDED
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
        if not isinstance(actor_id, str) or not actor_id:
            raise AdapterException(AdapterReason.INVALID_INPUT, SkillStagingAdapterReason.SELF_APPROVAL_DENIED)
        # Reviewer must be an authenticated owner identity supplied by the caller.
        if review.reviewer_role != "owner" or actor_id != review.reviewer_actor_id:
            raise AdapterException(AdapterReason.POLICY_DENIED, SkillStagingAdapterReason.SELF_APPROVAL_DENIED)
        # Actor cannot review/approve its own generated package.
        if actor_id == candidate.manifest.publisher_id:
            raise AdapterException(AdapterReason.POLICY_DENIED, SkillStagingAdapterReason.SELF_APPROVAL_DENIED)
        if self._archive_reader is None:
            raise AdapterException(AdapterReason.MISSING_DEPENDENCY)
        if not isinstance(archive_bytes, bytes) or not archive_bytes or len(archive_bytes) > self._config.max_total_bytes:
            raise AdapterException(AdapterReason.INVALID_INPUT, SkillStagingAdapterReason.SIZE_EXCEEDED)
        entries = self._archive_reader.read_entries(archive_bytes)
        ok, reason = self._validate_archive_entries(entries, len(archive_bytes))
        if not ok:
            raise AdapterException(AdapterReason.INVALID_INPUT, reason)

        # Candidate archive files must exactly match the reviewed manifest.
        declared_paths = {f.relative_path for f in candidate.manifest.files}
        entry_map = {e.normalized_path: e for e in entries if e.kind is ArchiveEntryKind.REGULAR}
        if set(entry_map.keys()) != declared_paths:
            raise AdapterException(AdapterReason.INVALID_INPUT, SkillStagingAdapterReason.DIGEST_MISMATCH)
        for file_digest in candidate.manifest.files:
            entry = entry_map[file_digest.relative_path]
            if entry.uncompressed_size != file_digest.size_bytes:
                raise AdapterException(AdapterReason.INVALID_INPUT, SkillStagingAdapterReason.DIGEST_MISMATCH)
            content = entry.content
            assert content is not None
            computed = hashlib.sha256(content).hexdigest()
            if computed != file_digest.sha256_hex:
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
            if pkg_reason is PackageValidationReason.MISSING_ROLLBACK_METADATA:
                raise AdapterException(AdapterReason.POLICY_DENIED, SkillStagingAdapterReason.MISSING_ROLLBACK)
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


def _has_control_chars(value: str) -> bool:
    for ch in value:
        code = ord(ch)
        if code < 32 or code == 127:
            return True
    return False


def _reject_bool(value: object, name: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be a boolean")


def _reject_bool_nan(value: object, name: str) -> None:
    _reject_bool(value, name)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"invalid {name}")
