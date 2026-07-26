"""Signed skill package contracts and manifest validation for Phase 6.

Provides immutable, canonicalized skill package contracts, deterministic file
hashing via stdlib hashlib, publisher trust models, signature verification boundaries,
permission widening detectors, and install plan validation without executing or installing
package code.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from enum import StrEnum
from typing import Callable, Mapping, Optional, Sequence, Set, Tuple

# Canonical regex patterns
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[a-zA-Z0-9.-]+)?$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# Package bounds
MAX_FILES = 100
MAX_PACKAGE_BYTES = 10_485_760  # 10 MB
MAX_FILE_BYTES = 2_097_152     # 2 MB
MAX_PATH_DEPTH = 10

# Denied binary / executable extensions
FORBIDDEN_EXTENSIONS: Set[str] = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin",
    ".dylib", ".elf", ".o", ".a", ".class", ".jar", ".node",
    ".wasm", ".sh", ".bat", ".cmd", ".vbs", ".app", ".dmg",
}


class PackageValidationOutcome(StrEnum):
    """Fixed package validation outcome."""

    VALID = "valid"
    INVALID = "invalid"
    DENIED = "denied"


class PackageValidationReason(StrEnum):
    """Fixed, privacy-safe validation reason codes."""

    OK = "ok"
    MALFORMED_MANIFEST = "malformed_manifest"
    INVALID_VERSION = "invalid_version"
    PATH_TRAVERSAL = "path_traversal"
    ABSOLUTE_PATH = "absolute_path"
    SYMLINK_REJECTED = "symlink_rejected"
    CONTROL_CHARS = "control_chars"
    NUL_BYTE = "nul_byte"
    BINARY_CONTENT_DENIED = "binary_content_denied"
    SIZE_EXCEEDED = "size_exceeded"
    FILE_COUNT_EXCEEDED = "file_count_exceeded"
    PATH_DEPTH_EXCEEDED = "path_depth_exceeded"
    CASE_COLLISION = "case_collision"
    DIGEST_MISMATCH = "digest_mismatch"
    MISSING_FILE = "missing_file"
    EXTRA_FILE = "extra_file"
    INVALID_SIGNATURE = "invalid_signature"
    UNTRUSTED_PUBLISHER = "untrusted_publisher"
    REVOKED_PUBLISHER = "revoked_publisher"
    REVOKED_PACKAGE = "revoked_package"
    PERMISSION_WIDENING = "permission_widening"
    UNAPPROVED_PERMISSIONS = "unapproved_permissions"
    UNAPPROVED_DEPENDENCIES = "unapproved_dependencies"
    MISSING_ROLLBACK_METADATA = "missing_rollback_metadata"


def _has_control_chars(value: str) -> bool:
    for char in value:
        code = ord(char)
        if code < 32 or code == 127:
            return True
    return False


@dataclass(frozen=True)
class SkillPermissionDeclaration:
    """Declared permission requirement for a skill."""

    tool_kind: str  # "mcp" or "skill"
    action: str     # e.g. "skill:execute", "mcp:execute"
    target_kind: str  # "skill" or "mcp_server"
    target_value: str
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        allowed = {
            ("skill", "skill.execute", "skill"),
            ("mcp", "mcp.execute", "mcp_server"),
        }
        if (self.tool_kind, self.action, self.target_kind) not in allowed:
            raise ValueError("invalid permission declaration")
        if (
            not isinstance(self.target_value, str)
            or not self.target_value
            or len(self.target_value) > 4096
            or any(marker in self.target_value for marker in ("*", "?", "[", "]"))
        ):
            raise ValueError("invalid target_value")
        if _has_control_chars(self.target_value) or any(ord(ch) > 126 for ch in self.target_value):
            raise ValueError("control characters prohibited in permission declaration")
        if self.reason is not None and (
            not isinstance(self.reason, str)
            or len(self.reason) > 256
            or _has_control_chars(self.reason)
        ):
            raise ValueError("invalid permission reason")

    def permission_key(self) -> str:
        return f"{self.tool_kind}:{self.action}:{self.target_kind}:{self.target_value}"

    def __repr__(self) -> str:
        return "SkillPermissionDeclaration()"


@dataclass(frozen=True)
class SkillFileDigest:
    """Metadata and SHA-256 digest of a file in a skill package."""

    relative_path: str
    sha256_hex: str
    size_bytes: int
    is_symlink: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise ValueError("relative_path is required")
        if not isinstance(self.sha256_hex, str) or not _SHA256_HEX_RE.fullmatch(self.sha256_hex):
            raise ValueError("sha256_hex must be a 64-character lowercase hex string")
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        if not isinstance(self.is_symlink, bool):
            raise ValueError("is_symlink must be boolean")
        if self.is_symlink:
            raise ValueError("symlinks prohibited")

    def __repr__(self) -> str:
        return "SkillFileDigest()"


@dataclass(frozen=True)
class SkillPackageManifest:
    """Canonical, validated manifest describing a signed skill package."""

    package_id: str
    name: str
    version: str
    publisher_id: str
    description: str
    declared_permissions: Tuple[SkillPermissionDeclaration, ...]
    files: Tuple[SkillFileDigest, ...]
    dependencies: Tuple[str, ...]
    created_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.package_id, str) or not _IDENTIFIER_RE.fullmatch(self.package_id):
            raise ValueError("invalid package_id")
        if not isinstance(self.publisher_id, str) or not _IDENTIFIER_RE.fullmatch(self.publisher_id):
            raise ValueError("invalid publisher_id")
        if not isinstance(self.version, str) or not _SEMVER_RE.fullmatch(self.version):
            raise ValueError("version must follow semantic versioning")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 120 or _has_control_chars(self.name):
            raise ValueError("name is required")
        if not isinstance(self.description, str) or len(self.description) > 2048 or _has_control_chars(self.description):
            raise ValueError("invalid description")
        if not isinstance(self.created_at, (int, float)) or isinstance(self.created_at, bool) or not math.isfinite(self.created_at) or self.created_at < 0:
            raise ValueError("invalid created_at")
        if not isinstance(self.files, tuple) or not self.files or any(not isinstance(f, SkillFileDigest) for f in self.files):
            raise ValueError("files must contain SkillFileDigest")
        if not isinstance(self.declared_permissions, tuple) or any(
            not isinstance(permission, SkillPermissionDeclaration)
            for permission in self.declared_permissions
        ):
            raise ValueError("invalid declared_permissions")
        if len({permission.permission_key() for permission in self.declared_permissions}) != len(self.declared_permissions):
            raise ValueError("duplicate declared permission")
        if not isinstance(self.dependencies, tuple) or any(
            not isinstance(dep, str) or not _IDENTIFIER_RE.fullmatch(dep)
            for dep in self.dependencies
        ):
            raise ValueError("invalid dependencies")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("duplicate dependency")

        # Enforce deterministic file ordering by relative_path
        sorted_files = tuple(sorted(self.files, key=lambda f: f.relative_path))
        if self.files != sorted_files:
            object.__setattr__(self, "files", sorted_files)

        # Validate file bounds and path safety
        if len(self.files) > MAX_FILES:
            raise ValueError(f"file count exceeds maximum allowed ({MAX_FILES})")

        total_bytes = 0
        seen_paths_lower: Set[str] = set()

        for f in self.files:
            if f.is_symlink:
                raise ValueError(f"symlinks prohibited: {f.relative_path}")
            if "\x00" in f.relative_path:
                raise ValueError("NUL bytes prohibited in file path")
            if _has_control_chars(f.relative_path):
                raise ValueError("control characters prohibited in file path")
            if "\\" in f.relative_path or any(ord(ch) > 126 for ch in f.relative_path):
                raise ValueError("non-canonical file path")
            if f.relative_path.startswith("/") or re.match(r"^[a-zA-Z]:", f.relative_path):
                raise ValueError("absolute paths prohibited")

            parts = [p for p in f.relative_path.split("/") if p and p != "."]
            if ".." in parts:
                raise ValueError("path traversal ('..') prohibited")
            if len(parts) > MAX_PATH_DEPTH:
                raise ValueError(f"path depth exceeds maximum allowed ({MAX_PATH_DEPTH})")

            # Check binary extensions
            ext = "." + parts[-1].rsplit(".", 1)[-1].lower() if "." in parts[-1] else ""
            if ext in FORBIDDEN_EXTENSIONS:
                raise ValueError(f"executable/binary extension prohibited: {ext}")

            # Case collision check
            path_lower = f.relative_path.lower()
            if path_lower in seen_paths_lower:
                raise ValueError(f"case-collision detected in file path: {f.relative_path}")
            seen_paths_lower.add(path_lower)

            if f.size_bytes > MAX_FILE_BYTES:
                raise ValueError(f"individual file size exceeds maximum allowed ({MAX_FILE_BYTES})")
            total_bytes += f.size_bytes

        if total_bytes > MAX_PACKAGE_BYTES:
            raise ValueError(f"total package size exceeds maximum allowed ({MAX_PACKAGE_BYTES})")

    def canonical_json(self) -> str:
        """Return deterministic canonical JSON serialization of the manifest."""
        data = {
            "package_id": self.package_id,
            "name": self.name,
            "version": self.version,
            "publisher_id": self.publisher_id,
            "description": self.description,
            "declared_permissions": [
                {
                    "tool_kind": p.tool_kind,
                    "action": p.action,
                    "target_kind": p.target_kind,
                    "target_value": p.target_value,
                }
                for p in sorted(self.declared_permissions, key=lambda x: x.permission_key())
            ],
            "files": [
                {
                    "relative_path": f.relative_path,
                    "sha256_hex": f.sha256_hex,
                    "size_bytes": f.size_bytes,
                }
                for f in self.files
            ],
            "dependencies": sorted(self.dependencies),
            "created_at": self.created_at,
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def canonical_digest(self) -> str:
        """Return SHA-256 digest of the canonical JSON manifest string."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def __repr__(self) -> str:
        return "SkillPackageManifest()"


def detect_permission_widening(
    older: SkillPackageManifest,
    newer: SkillPackageManifest,
) -> Tuple[bool, Tuple[str, ...]]:
    """Detect if newer manifest requires permissions not declared in older manifest."""
    older_keys = {p.permission_key() for p in older.declared_permissions}
    newer_keys = {p.permission_key() for p in newer.declared_permissions}

    widened_keys = tuple(sorted(newer_keys - older_keys))
    is_widened = len(widened_keys) > 0
    return is_widened, widened_keys


@dataclass(frozen=True)
class SkillPackageCandidate:
    """Candidate skill package containing manifest and in-memory file contents."""

    manifest: SkillPackageManifest
    file_contents: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, SkillPackageManifest):
            raise ValueError("invalid manifest")
        if not isinstance(self.file_contents, Mapping):
            raise ValueError("file_contents must be a mapping")
        frozen: dict[str, bytes] = {}
        for path, content in self.file_contents.items():
            if not isinstance(path, str) or not isinstance(content, bytes):
                raise ValueError("invalid file_contents")
            frozen[path] = bytes(content)
        object.__setattr__(self, "file_contents", MappingProxyType(frozen))

    def validate_candidate(self) -> Tuple[PackageValidationOutcome, PackageValidationReason, str]:
        """Validate that file_contents exactly match manifest file digests."""
        declared_paths = {f.relative_path for f in self.manifest.files}
        actual_paths = set(self.file_contents.keys())

        if declared_paths != actual_paths:
            missing = declared_paths - actual_paths
            if missing:
                return PackageValidationOutcome.INVALID, PackageValidationReason.MISSING_FILE, "missing declared files"
            return PackageValidationOutcome.INVALID, PackageValidationReason.EXTRA_FILE, "extra undeclared files present"

        for f in self.manifest.files:
            content = self.file_contents[f.relative_path]
            if len(content) != f.size_bytes:
                return PackageValidationOutcome.INVALID, PackageValidationReason.SIZE_EXCEEDED, "file size mismatch"
            computed_digest = hashlib.sha256(content).hexdigest()
            if computed_digest != f.sha256_hex:
                return PackageValidationOutcome.INVALID, PackageValidationReason.DIGEST_MISMATCH, "file SHA-256 digest mismatch"
            if b"\x00" in content or content.startswith((b"\x7fELF", b"MZ", b"\x00asm", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")):
                return PackageValidationOutcome.DENIED, PackageValidationReason.BINARY_CONTENT_DENIED, "binary content denied"

        return PackageValidationOutcome.VALID, PackageValidationReason.OK, "package valid"

    def __repr__(self) -> str:
        return "SkillPackageCandidate()"


@dataclass(frozen=True)
class SignatureEvidence:
    """Digital signature evidence for a skill package manifest."""

    publisher_id: str
    key_id: str
    signature_hex: str
    algorithm: str = "ed25519"
    signed_at: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.publisher_id, str) or not _IDENTIFIER_RE.fullmatch(self.publisher_id):
            raise ValueError("invalid publisher_id")
        if not isinstance(self.key_id, str) or not _IDENTIFIER_RE.fullmatch(self.key_id):
            raise ValueError("key_id is required")
        if not isinstance(self.signature_hex, str) or not re.fullmatch(r"[0-9a-f]{2,16384}", self.signature_hex):
            raise ValueError("signature_hex is required")
        if self.algorithm != "ed25519":
            raise ValueError("unsupported signature algorithm")
        if not isinstance(self.signed_at, (int, float)) or isinstance(self.signed_at, bool) or not math.isfinite(self.signed_at) or self.signed_at < 0:
            raise ValueError("invalid signed_at")

    def __repr__(self) -> str:
        return "SignatureEvidence()"


@dataclass(frozen=True)
class PublisherTrust:
    """Trust record for a skill publisher and key set."""

    publisher_id: str
    trust_level: str  # "trusted", "untrusted", "revoked"
    trusted_keys: Tuple[str, ...]
    revoked_keys: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.publisher_id, str) or not _IDENTIFIER_RE.fullmatch(self.publisher_id):
            raise ValueError("invalid publisher_id")
        if self.trust_level not in {"trusted", "untrusted", "revoked"}:
            raise ValueError("invalid trust_level")
        for values in (self.trusted_keys, self.revoked_keys):
            if not isinstance(values, tuple) or any(not _IDENTIFIER_RE.fullmatch(value) for value in values):
                raise ValueError("invalid trust keys")
            if len(values) != len(set(values)):
                raise ValueError("duplicate trust key")
        if set(self.trusted_keys) & set(self.revoked_keys):
            raise ValueError("trusted and revoked keys overlap")

    def is_trusted_publisher(self, key_id: str) -> bool:
        if self.trust_level != "trusted":
            return False
        if key_id in self.revoked_keys:
            return False
        return key_id in self.trusted_keys

    def __repr__(self) -> str:
        return "PublisherTrust()"


@dataclass(frozen=True)
class SkillReview:
    """Record of human/owner security review of a skill package."""

    review_id: str
    package_id: str
    package_digest: str
    version: str
    publisher_id: str
    reviewed_permissions: Tuple[SkillPermissionDeclaration, ...]
    reviewer_actor_id: str
    reviewer_role: str  # "owner"
    outcome: str  # "approved", "rejected", "pending"
    reviewed_at: float
    expires_at: Optional[float] = None

    def __post_init__(self) -> None:
        for field, value in (
            ("review_id", self.review_id),
            ("package_id", self.package_id),
            ("publisher_id", self.publisher_id),
            ("reviewer_actor_id", self.reviewer_actor_id),
        ):
            if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
                raise ValueError(f"invalid {field}")
        if not _SHA256_HEX_RE.fullmatch(self.package_digest):
            raise ValueError("invalid package_digest")
        if not _SEMVER_RE.fullmatch(self.version):
            raise ValueError("invalid version")
        if self.reviewer_role != "owner" or self.outcome not in {"approved", "rejected", "pending"}:
            raise ValueError("invalid review authority or outcome")
        if not isinstance(self.reviewed_permissions, tuple) or any(
            not isinstance(permission, SkillPermissionDeclaration)
            for permission in self.reviewed_permissions
        ):
            raise ValueError("invalid reviewed_permissions")
        if not isinstance(self.reviewed_at, (int, float)) or isinstance(self.reviewed_at, bool) or not math.isfinite(self.reviewed_at):
            raise ValueError("invalid reviewed_at")
        if self.expires_at is not None and (
            not isinstance(self.expires_at, (int, float))
            or isinstance(self.expires_at, bool)
            or not math.isfinite(self.expires_at)
            or self.expires_at <= self.reviewed_at
        ):
            raise ValueError("invalid expires_at")

    def is_valid(self, package_digest: str, now: float) -> bool:
        if self.outcome != "approved":
            return False
        if self.package_digest != package_digest:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        return True

    def __repr__(self) -> str:
        return "SkillReview()"


@dataclass(frozen=True)
class SkillRollbackMetadata:
    """Backup and rollback reference before replacing an installed skill."""

    previous_version: str
    previous_digest: str
    backup_reference: str

    def __post_init__(self) -> None:
        if not _SEMVER_RE.fullmatch(self.previous_version):
            raise ValueError("invalid previous_version")
        if not _SHA256_HEX_RE.fullmatch(self.previous_digest):
            raise ValueError("invalid previous_digest")
        if not _IDENTIFIER_RE.fullmatch(self.backup_reference):
            raise ValueError("invalid backup_reference")

    def __repr__(self) -> str:
        return "SkillRollbackMetadata()"


@dataclass(frozen=True)
class SkillRevocation:
    """Explicit revocation record for publisher, package, or specific version."""

    revocation_id: str
    target_type: str  # "publisher", "package", "version"
    target_identifier: str
    reason: str
    revoked_at: float

    def __post_init__(self) -> None:
        if not _IDENTIFIER_RE.fullmatch(self.revocation_id):
            raise ValueError("invalid revocation_id")
        if self.target_type not in {"publisher", "package", "version"}:
            raise ValueError("invalid target_type")
        if not isinstance(self.target_identifier, str) or not self.target_identifier:
            raise ValueError("invalid target_identifier")
        if not isinstance(self.reason, str) or not self.reason or len(self.reason) > 256 or _has_control_chars(self.reason):
            raise ValueError("invalid revocation reason")
        if not isinstance(self.revoked_at, (int, float)) or isinstance(self.revoked_at, bool) or not math.isfinite(self.revoked_at):
            raise ValueError("invalid revoked_at")

    def __repr__(self) -> str:
        return "SkillRevocation()"


@dataclass(frozen=True)
class SkillInstallPlan:
    """Validated plan ready for integration/installation."""

    plan_id: str
    candidate: SkillPackageCandidate
    review: SkillReview
    signature_evidence: SignatureEvidence
    publisher_trust: PublisherTrust
    rollback_metadata: Optional[SkillRollbackMetadata] = None

    def __post_init__(self) -> None:
        if not _IDENTIFIER_RE.fullmatch(self.plan_id):
            raise ValueError("invalid plan_id")
        if not isinstance(self.candidate, SkillPackageCandidate):
            raise ValueError("invalid candidate")
        if not isinstance(self.review, SkillReview):
            raise ValueError("invalid review")
        if not isinstance(self.signature_evidence, SignatureEvidence):
            raise ValueError("invalid signature_evidence")
        if not isinstance(self.publisher_trust, PublisherTrust):
            raise ValueError("invalid publisher_trust")

    def validate_plan(
        self,
        signature_verifier: Callable[[bytes, SignatureEvidence], bool],
        revocations: Sequence[SkillRevocation],
        is_replacement: bool,
        now: float,
    ) -> Tuple[PackageValidationOutcome, PackageValidationReason, str]:
        """Validate all security and policy requirements of the install plan."""
        # 1. Candidate validation
        c_outcome, c_reason, c_msg = self.candidate.validate_candidate()
        if c_outcome != PackageValidationOutcome.VALID:
            return c_outcome, c_reason, c_msg

        manifest = self.candidate.manifest
        manifest_digest = manifest.canonical_digest()

        if manifest.dependencies:
            return PackageValidationOutcome.DENIED, PackageValidationReason.UNAPPROVED_DEPENDENCIES, "dependencies require a separate reviewed resolver"

        if (
            self.signature_evidence.publisher_id != manifest.publisher_id
            or self.publisher_trust.publisher_id != manifest.publisher_id
        ):
            return PackageValidationOutcome.DENIED, PackageValidationReason.UNTRUSTED_PUBLISHER, "publisher identity mismatch"

        # 2. Check revocations
        for rev in revocations:
            if rev.target_type == "publisher" and rev.target_identifier == manifest.publisher_id:
                return PackageValidationOutcome.DENIED, PackageValidationReason.REVOKED_PUBLISHER, "publisher revoked"
            if rev.target_type == "package" and rev.target_identifier == manifest.package_id:
                return PackageValidationOutcome.DENIED, PackageValidationReason.REVOKED_PACKAGE, "package revoked"
            if rev.target_type == "version" and rev.target_identifier == f"{manifest.package_id}:{manifest.version}":
                return PackageValidationOutcome.DENIED, PackageValidationReason.REVOKED_PACKAGE, "package version revoked"

        # 3. Check publisher trust
        if not self.publisher_trust.is_trusted_publisher(self.signature_evidence.key_id):
            return PackageValidationOutcome.DENIED, PackageValidationReason.UNTRUSTED_PUBLISHER, "untrusted publisher or key"

        # 4. Check signature using caller-supplied verifier
        manifest_bytes = manifest.canonical_json().encode("utf-8")
        try:
            signature_ok = bool(signature_verifier(manifest_bytes, self.signature_evidence))
        except Exception:
            signature_ok = False
        if not signature_ok:
            return PackageValidationOutcome.DENIED, PackageValidationReason.INVALID_SIGNATURE, "invalid manifest signature"

        # 5. Check review match and validity
        if (
            not self.review.is_valid(manifest_digest, now)
            or self.review.package_id != manifest.package_id
            or self.review.version != manifest.version
            or self.review.publisher_id != manifest.publisher_id
        ):
            return PackageValidationOutcome.DENIED, PackageValidationReason.UNAPPROVED_PERMISSIONS, "review invalid or expired"

        # 6. Check reviewed permissions match manifest permissions
        reviewed_keys = {p.permission_key() for p in self.review.reviewed_permissions}
        manifest_keys = {p.permission_key() for p in manifest.declared_permissions}
        if manifest_keys != reviewed_keys:
            return PackageValidationOutcome.DENIED, PackageValidationReason.PERMISSION_WIDENING, "manifest requests unapproved permissions"

        # 7. Check rollback metadata if replacement
        if is_replacement and self.rollback_metadata is None:
            return PackageValidationOutcome.DENIED, PackageValidationReason.MISSING_ROLLBACK_METADATA, "rollback metadata required for replacement"

        return PackageValidationOutcome.VALID, PackageValidationReason.OK, "install plan approved"

    def __repr__(self) -> str:
        return "SkillInstallPlan()"
