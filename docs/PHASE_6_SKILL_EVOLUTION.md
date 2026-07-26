# Phase 6 Reviewed Skill Evolution & Signed Package Specification

This document details H1KARI's Phase 6 reviewed skill evolution architecture (`core/phase6_ecosystem/skill_package.py` and `core/phase6_ecosystem/skill_review.py`).

---

## 1. Package Format & Layout

Skill packages are declared via immutable `SkillPackageManifest` records containing:
- `package_id`: Opaque identifier matching `^[a-z0-9][a-z0-9_.-]{0,79}$`
- `version`: Explicit semantic version (`MAJOR.MINOR.PATCH`)
- `publisher_id`: Verified publisher identifier
- `declared_permissions`: Explicit tuple of `SkillPermissionDeclaration` requirements
- `files`: Sorted tuple of `SkillFileDigest` records describing exact relative paths, sizes, and SHA-256 digests
- `dependencies`: Explicit list of declared dependencies

### File & Path Boundaries
- Maximum files per package: 100
- Maximum total package size: 10 MB
- Maximum single file size: 2 MB
- Maximum path depth: 10 levels
- Path Traversal: `..` components strictly prohibited
- Absolute Paths: Leading `/` or drive letters (`C:`) prohibited
- Symlinks: Prohibited
- Binary Content: Executable extensions (`.pyc`, `.so`, `.dll`, `.exe`, `.bin`, `.dylib`, `.wasm`) denied by default
- Binary Content: executable magic/NUL payloads are denied even when disguised
  with a text extension.
- Case Collisions: Lowercase path collisions rejected
- Dependencies: declared dependencies remain default-denied until a separate
  reviewed dependency resolver exists.

---

## 2. Canonicalization, Digests & Signature Boundary

- **Canonical Manifest Serialization**: `manifest.canonical_json()` generates deterministic JSON with key sorting and stripped whitespace.
- **Canonical Digest**: `manifest.canonical_digest()` computes `SHA-256(canonical_json())`.
- **Signature Verification**: Signature verification is delegated to caller-supplied verifiers (`signature_verifier(manifest_bytes, signature_evidence)`). No homegrown cryptography or private signing keys are embedded in this package.
- Publisher IDs on the manifest, signature evidence, trust record, and review
  must match exactly.

---

## 3. Review Lifecycle State Machine

```
              ┌───────────┐
              │ PROPOSED  │  <-- (Teach Me proposals stop here)
              └─────┬─────┘
                    │ validate_package()
                    ▼
              ┌───────────┐
              │ VALIDATED │
              └─────┬─────┘
                    │ submit_for_review()
                    ▼
          ┌───────────────────┐
          │ AWAITING_REVIEW   │
          └─────────┬─────────┘
        approve_review() │ reject_review()
        (Owner-only)     │
            ┌────────────┴────────────┐
            ▼                         ▼
      ┌───────────┐             ┌───────────┐
      │ APPROVED  │             │ REJECTED  │
      └─────┬─────┘             └───────────┘
            │ create_install_plan()
            ▼
      ┌───────────────┐
      │ INSTALL_READY │
      └─────┬─────────┘
            │ record_installation()
            ▼
 ┌─────────────────────┐       ┌───────────┐
 │ INSTALLED_RECORDED  │ ────► │  REVOKED  │
 └─────────────────────┘       └───────────┘
```

### Authorization Rules
- **Teach Me Boundary**: Phase 5 Teach Me produces at most `PROPOSED` state.
- **Assistant Self-Approval Denied**: Non-owner contexts (such as `Actor.SYSTEM` or `Actor.GUEST`) cannot approve reviews.
- Owner identity is matched to the review record; review use is one-time for an
  install-ready transition.
- **Permission Widening Detection**: `detect_permission_widening(older, newer)` identifies newly requested permissions; widened packages require explicit owner re-review.
- **Rejection Protection**: Resubmitting an identical package digest after rejection fails closed unless a new candidate is generated.

---

## 4. Rollback & Revocation

- **Rollback Requirement**: Replacing an existing installed package requires explicit `SkillRollbackMetadata` specifying previous version, digest, and backup reference.
- Replacement status is an explicit caller input and cannot be inferred from
  whether rollback metadata happened to be supplied.
- **Revocation**: `SkillRevocation` can target publishers, packages, or specific versions, immediately blocking installation or execution readiness.

---

## 5. No-Install Boundary & Mira Integration Plan

- **Inert Contract**: This package provides pure state evaluation only. It performs zero filesystem mutations, dynamic module imports, network calls, or code execution.
- **Mira Integration**: Mira will wire the `SkillInstallPlan` into the production runtime daemon and sandbox executor when deploying Phase 6 capabilities.
