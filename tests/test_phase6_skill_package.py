"""Synthetic test suite for signed skill packages (Phase 6 Part A)."""

import hashlib
import pytest

from core.phase6_ecosystem.skill_package import (
    PackageValidationOutcome,
    PackageValidationReason,
    PublisherTrust,
    SignatureEvidence,
    SkillFileDigest,
    SkillInstallPlan,
    SkillPackageCandidate,
    SkillPackageManifest,
    SkillPermissionDeclaration,
    SkillRevocation,
    SkillRollbackMetadata,
    SkillReview,
    detect_permission_widening,
)


def _make_sample_manifest(
    package_id: str = "pkg_sample_01",
    version: str = "1.0.0",
    files=None,
    permissions=None,
) -> SkillPackageManifest:
    if files is None:
        files = (
            SkillFileDigest("README.md", hashlib.sha256(b"hello").hexdigest(), 5),
            SkillFileDigest("skill.py", hashlib.sha256(b"print(1)").hexdigest(), 8),
        )
    if permissions is None:
        permissions = (
            SkillPermissionDeclaration("skill", "skill.execute", "skill", "timer"),
        )
    return SkillPackageManifest(
        package_id=package_id,
        name="Sample Skill",
        version=version,
        publisher_id="publisher_acme",
        description="A sample skill package for testing",
        declared_permissions=tuple(permissions),
        files=tuple(files),
        dependencies=(),
        created_at=1000.0,
    )


def test_canonical_digest_stability_and_file_reordering():
    f1 = SkillFileDigest("b_skill.py", hashlib.sha256(b"code_b").hexdigest(), 6)
    f2 = SkillFileDigest("a_readme.md", hashlib.sha256(b"code_a").hexdigest(), 6)

    m1 = SkillPackageManifest(
        package_id="pkg_test",
        name="Test",
        version="1.0.0",
        publisher_id="pub_test",
        description="Test",
        declared_permissions=(),
        files=(f1, f2),  # Unsorted order
        dependencies=(),
        created_at=1000.0,
    )

    m2 = SkillPackageManifest(
        package_id="pkg_test",
        name="Test",
        version="1.0.0",
        publisher_id="pub_test",
        description="Test",
        declared_permissions=(),
        files=(f2, f1),  # Different order
        dependencies=(),
        created_at=1000.0,
    )

    assert m1.files == m2.files  # Automatically sorted by relative_path
    assert m1.canonical_json() == m2.canonical_json()
    assert m1.canonical_digest() == m2.canonical_digest()


def test_changed_file_byte_changes_digest():
    m1 = _make_sample_manifest()

    f_changed = (
        SkillFileDigest("README.md", hashlib.sha256(b"hello_world").hexdigest(), 11),
        SkillFileDigest("skill.py", hashlib.sha256(b"print(1)").hexdigest(), 8),
    )
    m2 = _make_sample_manifest(files=f_changed)

    assert m1.canonical_digest() != m2.canonical_digest()


def test_path_traversal_and_absolute_path_rejection():
    # Traversal '..'
    with pytest.raises(ValueError, match="path traversal"):
        SkillPackageManifest(
            package_id="pkg_test",
            name="Test",
            version="1.0.0",
            publisher_id="pub_test",
            description="Test",
            declared_permissions=(),
            files=(SkillFileDigest("../secret.txt", hashlib.sha256(b"x").hexdigest(), 1),),
            dependencies=(),
            created_at=1000.0,
        )

    # Absolute path '/'
    with pytest.raises(ValueError, match="absolute paths"):
        SkillPackageManifest(
            package_id="pkg_test",
            name="Test",
            version="1.0.0",
            publisher_id="pub_test",
            description="Test",
            declared_permissions=(),
            files=(SkillFileDigest("/etc/passwd", hashlib.sha256(b"x").hexdigest(), 1),),
            dependencies=(),
            created_at=1000.0,
        )


def test_symlink_and_nul_byte_rejection():
    with pytest.raises(ValueError, match="symlinks prohibited"):
        SkillFileDigest("symlink.py", hashlib.sha256(b"x").hexdigest(), 1, is_symlink=True)

    with pytest.raises(ValueError, match="NUL bytes prohibited"):
        SkillPackageManifest(
            package_id="pkg_test",
            name="Test",
            version="1.0.0",
            publisher_id="pub_test",
            description="Test",
            declared_permissions=(),
            files=(SkillFileDigest("bad\x00file.py", hashlib.sha256(b"x").hexdigest(), 1),),
            dependencies=(),
            created_at=1000.0,
        )


def test_executable_binary_extension_rejection():
    with pytest.raises(ValueError, match="executable/binary extension prohibited"):
        SkillPackageManifest(
            package_id="pkg_test",
            name="Test",
            version="1.0.0",
            publisher_id="pub_test",
            description="Test",
            declared_permissions=(),
            files=(SkillFileDigest("compiled.pyc", hashlib.sha256(b"x").hexdigest(), 1),),
            dependencies=(),
            created_at=1000.0,
        )


def test_candidate_validation_missing_and_extra_files():
    manifest = _make_sample_manifest()

    # Valid candidate
    contents = {
        "README.md": b"hello",
        "skill.py": b"print(1)",
    }
    candidate = SkillPackageCandidate(manifest, contents)
    outcome, reason, _ = candidate.validate_candidate()
    assert outcome == PackageValidationOutcome.VALID
    assert reason == PackageValidationReason.OK

    # Missing file
    missing_candidate = SkillPackageCandidate(manifest, {"README.md": b"hello"})
    m_out, m_reason, _ = missing_candidate.validate_candidate()
    assert m_out == PackageValidationOutcome.INVALID
    assert m_reason == PackageValidationReason.MISSING_FILE


def test_permission_widening_detection():
    m_old = _make_sample_manifest(permissions=[
        SkillPermissionDeclaration("skill", "skill.execute", "skill", "timer"),
    ])
    m_new = _make_sample_manifest(permissions=[
        SkillPermissionDeclaration("skill", "skill.execute", "skill", "timer"),
        SkillPermissionDeclaration("mcp", "mcp.execute", "mcp_server", "fs_server"),
    ])

    is_widened, widened_keys = detect_permission_widening(m_old, m_new)
    assert is_widened is True
    assert "mcp:mcp.execute:mcp_server:fs_server" in widened_keys


def test_install_plan_validation_full_flow():
    manifest = _make_sample_manifest()
    contents = {"README.md": b"hello", "skill.py": b"print(1)"}
    candidate = SkillPackageCandidate(manifest, contents)

    trust = PublisherTrust("publisher_acme", "trusted", ("key_001",), ())
    sig = SignatureEvidence("publisher_acme", "key_001", "ab" * 64)

    review = SkillReview(
        review_id="rev_001",
        package_id=manifest.package_id,
        package_digest=manifest.canonical_digest(),
        version=manifest.version,
        publisher_id=manifest.publisher_id,
        reviewed_permissions=manifest.declared_permissions,
        reviewer_actor_id="owner_1",
        reviewer_role="owner",
        outcome="approved",
        reviewed_at=1000.0,
    )

    def dummy_verifier(data: bytes, sig_ev: SignatureEvidence) -> bool:
        return sig_ev.signature_hex == "ab" * 64

    plan = SkillInstallPlan("plan_001", candidate, review, sig, trust)
    outcome, reason, _ = plan.validate_plan(dummy_verifier, (), is_replacement=False, now=1050.0)
    assert outcome == PackageValidationOutcome.VALID
    assert reason == PackageValidationReason.OK


def test_install_plan_revoked_publisher_rejection():
    manifest = _make_sample_manifest()
    candidate = SkillPackageCandidate(manifest, {"README.md": b"hello", "skill.py": b"print(1)"})
    trust = PublisherTrust("publisher_acme", "trusted", ("key_001",), ())
    sig = SignatureEvidence("publisher_acme", "key_001", "ab" * 64)
    review = SkillReview("rev_1", manifest.package_id, manifest.canonical_digest(), manifest.version, manifest.publisher_id, manifest.declared_permissions, "owner_1", "owner", "approved", 1000.0)

    revocation = SkillRevocation("rev_pub_1", "publisher", "publisher_acme", "malicious", 1020.0)

    plan = SkillInstallPlan("plan_001", candidate, review, sig, trust)
    outcome, reason, _ = plan.validate_plan(lambda d, s: True, [revocation], is_replacement=False, now=1050.0)

    assert outcome == PackageValidationOutcome.DENIED
    assert reason == PackageValidationReason.REVOKED_PUBLISHER


def test_content_free_repr_skill_package():
    manifest = _make_sample_manifest()
    sig = SignatureEvidence("pub_1", "key_1", "ab" * 64)
    trust = PublisherTrust("pub_1", "trusted", ("key_1",), ())
    review = SkillReview("rev_1", "pkg_1", "a" * 64, "1.0.0", "pub_1", (), "owner", "owner", "approved", 1000.0)

    assert repr(manifest) == "SkillPackageManifest()"
    assert repr(sig) == "SignatureEvidence()"
    assert repr(trust) == "PublisherTrust()"
    assert repr(review) == "SkillReview()"


def test_install_plan_binds_publisher_identity() -> None:
    manifest = _make_sample_manifest()
    candidate = SkillPackageCandidate(manifest, {"README.md": b"hello", "skill.py": b"print(1)"})
    review = SkillReview(
        "rev_1", manifest.package_id, manifest.canonical_digest(), manifest.version,
        manifest.publisher_id, manifest.declared_permissions, "owner_1", "owner",
        "approved", 1000.0,
    )
    plan = SkillInstallPlan(
        "plan_1", candidate, review,
        SignatureEvidence("other_publisher", "key_001", "ab" * 64),
        PublisherTrust("other_publisher", "trusted", ("key_001",), ()),
    )
    outcome, reason, _ = plan.validate_plan(lambda *_: True, (), False, 1001.0)
    assert outcome is PackageValidationOutcome.DENIED
    assert reason is PackageValidationReason.UNTRUSTED_PUBLISHER


def test_candidate_deep_freezes_file_mapping() -> None:
    manifest = _make_sample_manifest()
    contents = {"README.md": b"hello", "skill.py": b"print(1)"}
    candidate = SkillPackageCandidate(manifest, contents)
    contents["README.md"] = b"changed"
    assert candidate.file_contents["README.md"] == b"hello"


def test_binary_payload_denied_even_with_text_extension() -> None:
    payload = b"\x7fELF" + b"x" * 4
    file = SkillFileDigest("skill.py", hashlib.sha256(payload).hexdigest(), len(payload))
    manifest = _make_sample_manifest(files=(file,))
    candidate = SkillPackageCandidate(manifest, {"skill.py": payload})
    outcome, reason, _ = candidate.validate_candidate()
    assert outcome is PackageValidationOutcome.DENIED
    assert reason is PackageValidationReason.BINARY_CONTENT_DENIED


def test_dependencies_are_default_denied_at_install_boundary() -> None:
    base = _make_sample_manifest()
    manifest = SkillPackageManifest(
        base.package_id, base.name, base.version, base.publisher_id, base.description,
        base.declared_permissions, base.files, ("dependency_one",), base.created_at,
    )
    candidate = SkillPackageCandidate(manifest, {"README.md": b"hello", "skill.py": b"print(1)"})
    review = SkillReview(
        "rev_dep", manifest.package_id, manifest.canonical_digest(), manifest.version,
        manifest.publisher_id, manifest.declared_permissions, "owner_1", "owner",
        "approved", 1000.0,
    )
    plan = SkillInstallPlan(
        "plan_dep", candidate, review,
        SignatureEvidence(manifest.publisher_id, "key_001", "ab" * 64),
        PublisherTrust(manifest.publisher_id, "trusted", ("key_001",), ()),
    )
    outcome, reason, _ = plan.validate_plan(lambda *_: True, (), False, 1001.0)
    assert outcome is PackageValidationOutcome.DENIED
    assert reason is PackageValidationReason.UNAPPROVED_DEPENDENCIES
