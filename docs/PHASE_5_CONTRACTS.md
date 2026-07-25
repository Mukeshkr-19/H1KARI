# Phase 5 Safety and Authority Contracts

## Goals

Phase 5 defines a fail-closed policy foundation for four new capabilities:

- **Teach Me** — Owner instruction produces only a proposed lesson or skill-evolution candidate; never silently modifies production skills; never treats assistant-authored content as owner authority; child/helper teaching is approval-gated; reviewed skill evolution remains a later integration.
- **Guide My Hands** — Guidance and caller-supplied observation only; no physical action in this layer; consequential step requires approval; uncertainty is explicit; camera/vision consent remains separate; never claims completion without supplied evidence.
- **Care** — Supportive assistance only; no medical diagnosis or treatment authority; high-risk/emergency cases cannot follow normal autonomous flow; sensitive health scope is explicit; never claims an emergency contact was contacted; never fabricates professional advice.
- **Child Mode** — Least privilege; cannot access owner memories; cannot expose secrets; blocks purchases and account changes; blocks unrestricted external communication; blocks dangerous instructions; cannot disable audit or approval; cannot grant trusted-helper access.
- **Trusted Helper Access** — Explicit owner-created grant; narrow capabilities; narrow data/subject scope; expiration required; revocation supported; no delegation; no scope expansion; no unrelated owner memory; no silent renewal.

This package defines authority, consent, approval, expiration, revocation, scope, and audited decisions. It performs no capability action and no authentication. The policy service persists only grants, consents, and audit records in local SQLite stores.

## Actors

| Actor | Description | Base Authority |
|-------|-------------|----------------|
| `OWNER` | Authenticated local principal | Full authority within policy bounds |
| `CHILD` | Least-privilege household member | Cannot weaken policy; blocked from purchases, external comms, owner memory, dangerous actions, audit/approval bypass, helper grants |
| `TRUSTED_HELPER` | Explicit owner-created grant holder | Only capabilities explicitly granted; scoped; expiring; revocable; non-delegable |
| `GUEST` | Untrusted for owner-private data | Public/session reads only; no side effects |
| `SYSTEM` | Autonomous trigger | No implicit authority; denied until bounded grant exists |

## Capabilities

| Capability | Risk Level | Requires Approval | Description |
|------------|------------|-------------------|-------------|
| `teach_me` | LOW | No | Propose lessons/skill candidates only |
| `guide_my_hands` | MEDIUM | Yes (consequential steps) | Guidance with explicit uncertainty; no physical action |
| `care` | HIGH | Yes (emergency/high-risk) | Supportive assistance; no diagnosis; no false contact claims |
| `child_mode` | LOW | No | Least-privilege mode with hard blocks |
| `trusted_helper_access` | MEDIUM | Yes | Explicit owner grant with scope/expiry/revocation |

## Authority Matrix

| Actor \ Capability | teach_me | guide_my_hands | care | child_mode | trusted_helper_access |
|--------------------|----------|----------------|------|------------|----------------------|
| OWNER | ALLOW | REQUIRE_APPROVAL | REQUIRE_APPROVAL | ALLOW | REQUIRE_APPROVAL |
| CHILD | ALLOW (child-scoped) | REQUIRE_APPROVAL | REQUIRE_APPROVAL | ALLOW | DENY |
| TRUSTED_HELPER | GRANT_REQUIRED | GRANT_REQUIRED | GRANT_REQUIRED | DENY | DENY |
| GUEST | DENY | DENY | DENY | DENY | DENY |
| SYSTEM | DENY | DENY | DENY | DENY | DENY |

**Notes:**
- `CHILD` capabilities are limited to child-scoped subjects and actions.  Access to owner/private memory, purchases, external communication, dangerous instructions, and policy weakening remains hard-denied.
- `CHILD` *Teach Me* is allowed for child-scoped, safe educational requests.
- `CHILD` *Guide My Hands* and `CHILD` *Care* require explicit owner approval before producing executable or consequential output.

**Legend:**
- `ALLOW` — Permitted without additional approval
- `REQUIRE_APPROVAL` — Owner must explicitly approve via consent/grant flow
- `GRANT_REQUIRED` — Must have valid, non-expired, non-revoked `CapabilityGrant`
- `DENY` — Hard block

## Consent and Approval

### Owner Consent (`ConsentRecord`)
- Explicit owner authorization for a capability
- Scoped by `ScopeConstraint` (capability, data subject, resource pattern, allowed actions)
- Optional expiration; revocable at any time
- Used for high-risk capabilities (`guide_my_hands`, `care`, `trusted_helper_access`)

### Trusted Helper Grant (`CapabilityGrant`)
- Owner-issued, scoped, expiring grant for a specific helper
- **Expiration required** — no permanent grants
- **Revocation supported** — immediate invalidation
- **No delegation** — helper cannot delegate grant
- **No scope expansion** — helper cannot request broader scope
- **No unrelated memory** — helper cannot access owner memory outside scope
- **No silent renewal** — grant renewal requires explicit owner action

### Approval Flow
1. Owner requests capability requiring approval
2. Policy returns `REQUIRE_APPROVAL` with explicit reason
3. Owner provides explicit consent via `ConsentRecord` or issues `CapabilityGrant` for helper
4. A subsequent matching owner request with valid consent may return `ALLOW`; a helper request requires a matching valid grant
5. Expired or revoked consent/grant returns `EXPIRED` or `REVOKED`

## Child Mode Invariants

Child mode enforces **least privilege** with hard blocks that cannot be overridden:

| Blocked Action | Reason Code |
|----------------|-------------|
| Access owner memories | `CHILD_OWNER_MEMORY_BLOCKED` |
| Make purchases | `CHILD_PURCHASE_BLOCKED` |
| Unrestricted external communication | `CHILD_COMMUNICATION_BLOCKED` |
| Dangerous instructions | `CHILD_DANGEROUS_BLOCKED` |
| Disable audit | `CHILD_AUDIT_BYPASS_BLOCKED` |
| Disable approval | `CHILD_AUDIT_BYPASS_BLOCKED` |
| Grant trusted-helper access | `CHILD_HELPER_GRANT_BLOCKED` |
| Weaken policy | `CHILD_CANNOT_WEAKEN` |

Child can only operate within explicitly allowed child-scoped data and actions.

## Trusted Helper Lifecycle

```
Owner creates grant
       │
       ▼
┌──────────────────┐
│ CapabilityGrant  │
│ - grant_id       │
│ - helper_actor_id│
│ - owner_actor_id │
│ - capability     │
│ - scope          │
│ - issued_at      │
│ - expires_at     │◄── REQUIRED (no permanent grants)
│ - revoked=false  │
└──────────────────┘
       │
       ▼
Helper uses grant (validated on each request)
       │
       ├──► Valid grant → ALLOW
       │
       ├──► Expired → EXPIRED (HELPER_GRANT_EXPIRED)
       │
       ├──► Revoked → REVOKED (HELPER_GRANT_REVOKED)
       │
       ├──► Scope mismatch → DENY (HELPER_SCOPE_MISMATCH)
       │
       ├──► Delegation attempt → DENY (HELPER_DELEGATION_BLOCKED)
       │
       ├──► Scope expansion → DENY (HELPER_SCOPE_EXPANSION_BLOCKED)
       │
       ├──► Unrelated memory → DENY (HELPER_UNRELATED_MEMORY_BLOCKED)
       │
       └──► Silent renewal → DENY (HELPER_SILENT_RENEWAL_BLOCKED)
```

## Teach Me Review Boundary

- **Proposal only** — Owner instruction produces a proposed lesson or skill-evolution candidate
- **No direct skill installation** — `TEACH_ME_NO_DIRECT_INSTALL` blocks `install`, `deploy`, `activate`, `publish` actions
- **Assistant authority denied** — Assistant-authored content lacks owner authority (`TEACH_ME_ASSISTANT_AUTHORITY_DENIED`)
- **Reviewed skill evolution** — Remains a later integration; not part of Phase 5

## Guide My Hands Action Boundary

- **Guidance only** — No physical action in this layer
- **Consequential step requires approval** — `execute`, `perform`, `apply`, `confirm` actions require `REQUIRE_APPROVAL`
- **Uncertainty is explicit** — `GUIDE_HANDS_UNCERTAINTY` reason when metadata indicates uncertainty
- **No false completion** — `GUIDE_HANDS_NO_FALSE_COMPLETION` blocks completion claims without evidence
- **Camera/vision consent separate** — Not handled by this capability

## Care Safety Boundary

- **No diagnosis** — `CARE_NO_DIAGNOSIS` blocks `diagnose`, `prescribe`, `treat`, `medical` actions
- **Emergency handling** — Care remains approval-gated after the hard safety checks; a valid scoped consent is required for `ALLOW`
- **No false contact claims** — `CARE_NO_FALSE_CONTACT` blocks claims of contacting emergency services without evidence
- **No professional advice fabrication** — Supportive assistance only

## Privacy and Audit

### Content-Free Decisions
- All `Phase5Decision` objects have privacy-safe `__repr__` (no actor IDs, session IDs, request IDs, timestamps)
- `CapabilityGrant`, `ConsentRecord`, `ScopeConstraint` also have content-free reprs
- Audit records use opaque resource references (SHA-256 digests) never raw paths/content

### Audit Integration
- Every Phase 5 decision is recorded via `ActionAuditStore`
- Audit action format: `phase5.{capability}` (e.g., `phase5.teach_me`)
- Audit reason: explicit `DecisionReason` code
- No private data in audit records

### Data Isolation
- Phase 5 databases (`grants.db`, `consents.db`) use `0o700` directory / `0o600` file permissions
- No private data outside explicit scope
- No network/camera/microphone/OS capability action in this package; filesystem writes are limited to the local policy and audit stores supplied by the caller

## Integration Requirements

### Core Policy Integration
- Uses `core.action_policy.ActorContext` for audit compatibility
- Uses `core.grants.GrantStore` pattern for grant management
- Uses `core.action_audit.ActionAuditStore` for audit trail
- Uses `core.policy_service.PolicyService` pattern for authorization

### Authentication Boundary
- **Authentication is caller-supplied** — Phase 5 performs no authentication
- Actor identity (`Phase5ActorContext`) must be derived by caller from transport/session
- This package only evaluates authorization given an authenticated actor

### Time Dependency
- All time-dependent evaluation uses **injected time** (`now` parameter or service `clock`)
- No direct wall-clock reads occur in the Phase 5 package
- Enables deterministic testing and replay

### Fail-Closed Guarantees
1. Unknown capability → `DENY` (`UNKNOWN_CAPABILITY`)
2. Unknown/autonomous actor → `DENY` (`UNKNOWN_ACTOR`)
3. Guest actor → `DENY` (`GUEST_DENIED`)
4. Child mode blocks → `DENY` (specific reason codes)
5. Helper without grant → `DENY` (`HELPER_NO_GRANT`)
6. Expired grant → `EXPIRED` (`HELPER_GRANT_EXPIRED`)
7. Revoked grant → `REVOKED` (`HELPER_GRANT_REVOKED`)
8. Scope mismatch → `DENY` (`HELPER_SCOPE_MISMATCH`)
9. Delegation attempt → `DENY` (`HELPER_DELEGATION_BLOCKED`)
10. Scope expansion → `DENY` (`HELPER_SCOPE_EXPANSION_BLOCKED`)
11. Unrelated memory → `DENY` (`HELPER_UNRELATED_MEMORY_BLOCKED`)
12. Silent renewal → `DENY` (`HELPER_SILENT_RENEWAL_BLOCKED`)
13. Approval bypass → `DENY` (`APPROVAL_BYPASS_BLOCKED`)
14. Audit bypass → `DENY` (`AUDIT_BYPASS_BLOCKED`)
15. Brain write → `DENY` (`BRAIN_WRITE_DENIED`)
16. Invalid input/time → `DENY` (`INVALID_INPUT`, `INVALID_TIME`)

## Non-Goals

- **No authentication** — Caller must supply authenticated actor
- **No external actions** — No filesystem, network, camera, microphone, OS calls
- **No Brain v2 mutation** — Teach Me produces proposals only; no direct skill installation
- **No skill execution** — Skill evolution remains a later integration
- **No vision/camera handling** — Guide My Hands camera consent is separate
- **No medical authority** — Care is supportive assistance only
- **No child mode configuration** — Policy is fixed; no runtime weakening
- **No grant delegation** — Helper grants are non-delegable by design
- **No permanent grants** — Expiration is required
- **No silent renewal** — Explicit owner action required for renewal

## Files

| File | Purpose |
|------|---------|
| `core/phase5/contracts.py` | Pure immutable contracts and evaluation function |
| `core/phase5/policy.py` | Stateful service with grant/consent stores and audit |
| `core/phase5/__init__.py` | Package exports |
| `tests/test_phase5_contracts.py` | Contract validation and evaluation tests |
| `tests/test_phase5_policy.py` | Service integration tests |
| `docs/PHASE_5_CONTRACTS.md` | This document |

## Verification

Run tests:
```bash
.venv/bin/python -m pytest tests/test_phase5_contracts.py tests/test_phase5_policy.py -q
```

Compile check:
```bash
.venv/bin/python -m compileall -q core/phase5
```

Git diff check:
```bash
git diff --check -- core/phase5 tests/test_phase5_contracts.py tests/test_phase5_policy.py docs/PHASE_5_CONTRACTS.md
```
