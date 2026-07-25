# Phase 5 Session Lifecycle

## Purpose

`core/phase5/session_lifecycle.py` and `core/phase5/child_mode.py` define a
pure, deterministic, runtime-neutral layer for managing access sessions.

The layer provides:

- Immutable typed contracts for sessions, activation requests, authority
  snapshots, transitions, decisions, and policy.
- Fail-closed lifecycle state management for owner, child, and trusted-helper
  sessions.
- Deterministic child-mode action classification with hard-deny categories.

It does **not** authenticate callers, read stores, contact external systems,
or execute actions.  All identity, grants, consents, timestamps, and
revocation states are caller-supplied.

## Authority Hierarchy

```
OWNER
  │
  ├── owner session (OWNER_DIRECT authority)
  │
  ├── child session (CHILD_ACTIVATION evidence supplied by owner)
  │
  └── trusted-helper session (HELPER_GRANT scoped, expiring, revocable)
```

- **Owner** sessions derive authority directly from the owner actor.
- **Child** sessions require explicit owner-controlled activation evidence.
- **Trusted-helper** sessions require a valid, scoped, non-permanent grant
  issued by the owner.

Helper and child authority can never create an owner sessions.

## Lifecycle State Diagram

```
                         +---------+
                         | INACTIVE|
                         +----+----+
                              |
            +-----------------+-----------------+
            |                                   |
            v                                   v
+--------------------------+        +-----------------------+
| PENDING_OWNER_APPROVAL   |        | ACTIVE                |
+--------------------------+        +----------+------------+
            |          |                       |
            |          |                       |
            v          v                       v
        ACTIVE      CLOSED            +-------+-------+-------+
                                      |       |       |       |
                                      v       v       v       v
                                   LOCKED  EXPIRED REVOKED  CLOSED
                                      |       |       |       |
                                      |       |       |       |
                                      v       v       v       v
                                    CLOSED CLOSED CLOSED   (idempotent)
```

- **INACTIVE**: Session has been requested but not yet activated.
- **PENDING_OWNER_APPROVAL**: Waiting for owner approval.
- **ACTIVE**: Session is live and may be used.
- **EXPIRED**: Session reached its bounded lifetime.
- **REVOKED**: Owner revoked the session immediately.
- **LOCKED**: Owner temporarily locked the session.
- **CLOSED**: Terminal, immutable state.

## Transition Rules

| From | To | Allowed | Notes |
|------|----|---------|-------|
| INACTIVE | PENDING_OWNER_APPROVAL | Yes | Initial request needs approval |
| INACTIVE | ACTIVE | Yes | Owner direct, child with evidence, helper with grant |
| PENDING_OWNER_APPROVAL | ACTIVE | Owner only | Owner approval |
| PENDING_OWNER_APPROVAL | CLOSED | Yes | Rejection/cleanup |
| ACTIVE | LOCKED | Owner only | Owner lock |
| ACTIVE | EXPIRED | Yes | Time-driven |
| ACTIVE | REVOKED | Owner only | Immediate revocation |
| ACTIVE | CLOSED | Yes | Cleanup |
| LOCKED | INACTIVE | Owner only | Release to start a new request |
| LOCKED | CLOSED | Yes | Cleanup |
| EXPIRED | CLOSED | Yes | Cleanup |
| REVOKED | CLOSED | Yes | Cleanup |
| CLOSED | CLOSED | Yes | Idempotent |

Any transition not listed above is denied with
`TRANSITION_NOT_ALLOWED`.

Locked, revoked, and expired sessions cannot reactivate to `ACTIVE`
directly; they require a new owner-authorized activation request.

## Activation Behavior

### Owner Session

- Authority source must be `OWNER_DIRECT`.
- Session actor must be the owner actor.
- Bounded by `owner_max_duration_seconds`.

### Child Session

- Authority source must be `CHILD_ACTIVATION` and include activation
  evidence.
- Requested capabilities must be within the policy's
  `child_allowed_capabilities`.
- `TRUSTED_HELPER_ACCESS` is always blocked for child sessions.

### Trusted-Helper Session

- Authority source must be `HELPER_GRANT` and include a valid grant.
- The grant must not be expired or revoked.
- Helper and owner actor IDs must match the request.
- Every authority snapshot is bound to the request's owner actor ID; mismatched
  authority fails closed.
- Session expiration must be at or before the grant expiration.
- Requested capabilities must be exactly the grant's capability (no scope
  widening).
- Delegation and silent-renewal metadata are blocked.

## Scoring / Session Scope

- Session scope is always equal to or narrower than its authority source.
- Child sessions are limited to a configured set of child-safe capabilities.
- Helper sessions are limited to the capability named in the grant.
- Owner sessions may request any capability, but individual capability
  evaluation (for example, `evaluate_phase5_request`) still applies the
  relevant approval rules.

## Child-Mode Hard Blocks

`core/phase5/child_mode.py` hard-denies the following categories:

| Category / Action | Reason |
|-------------------|--------|
| owner/private memory | `child_owner_memory_blocked` |
| financial purchase/payment | `child_purchase_blocked` |
| external calls/messages/email/posting | `child_communication_blocked` |
| dangerous/self-harm instructions | `child_dangerous_blocked` |
| weapon/hazardous-material assistance | `child_weapon_hazard_blocked` |
| audit bypass | `child_audit_bypass_blocked` |
| approval bypass | `child_approval_bypass_blocked` |
| identity/authentication bypass | `child_identity_bypass_blocked` |
| permission/grant creation | `child_grant_creation_blocked` |
| policy weakening | `child_policy_weakening_blocked` |
| unrestricted browsing/downloads | `child_browsing_download_blocked` |
| secret/credential access | `child_secret_credential_blocked` |

Safe, child-scoped categories (e.g., `educational`, `learning`, `homework`,
`guidance`, `care`, `support`) are allowed.  Ambiguous categories return
`REQUIRE_APPROVAL` (or deny), never a silent allow.

Matching scans category, action, subject, resource, and metadata using
normalized words and short phrases across case, spaces, underscores, hyphens,
and common delimiters; a safe category cannot mask a blocked action. No AI or
external classifier is used.

## Privacy Guarantees

- `__repr__` methods never reveal activation evidence, consent content,
  tokens, actor IDs, or session IDs.
- Validation errors are generic and do not echo sensitive input.
- No filesystem, database, network, subprocess, environment, wall-clock, or
  UUID access is performed by these modules.

## Limitations

- This layer is purely a contract/policy calculator.  It does not store
  sessions, enforce network-level session cookies, or bind to a transport.
- Authentication is caller-supplied; callers must verify identity before
  invoking this layer.
- Revocation is immediate from the supplied snapshot, but the caller is
  responsible for propagating the revocation to any runtime state.
- The child-mode guard is category-based; callers must still supply accurate
  action descriptors.

## Future Integration with `Phase5PolicyService`

- `Phase5PolicyService` (or a Mira-owned service) can use
  `evaluate_activation_request` to decide whether to create a runtime session.
- The service persists grants and consents; the lifecycle layer remains
  stateless and I/O-free.
- Runtime session stores can map `AccessSession.session_id` to transport
  tokens, while the lifecycle layer provides the authoritative state model and
  transition rules.

## Non-Goals

- No user authentication.
- No session persistence or transport binding.
- No external actions (filesystem, network, OS).
- No wall-clock reads or UUID generation.
- No weakening of child or helper policy.
- No permanent helper sessions.
- No silent renewal or delegation.

## Verification

```bash
.venv/bin/python -m pytest tests/test_phase5_session_lifecycle.py tests/test_phase5_child_mode.py -q
.venv/bin/python -m compileall -q core/phase5/session_lifecycle.py core/phase5/child_mode.py
git diff --check -- core/phase5/session_lifecycle.py core/phase5/child_mode.py tests/test_phase5_session_lifecycle.py tests/test_phase5_child_mode.py docs/PHASE_5_SESSION_LIFECYCLE.md
```

## Files

| File | Purpose |
|------|---------|
| `core/phase5/session_lifecycle.py` | Immutable session lifecycle contracts and evaluation |
| `core/phase5/child_mode.py` | Deterministic child-mode action guard |
| `tests/test_phase5_session_lifecycle.py` | Synthetic tests for lifecycle transitions and scope rules |
| `tests/test_phase5_child_mode.py` | Synthetic tests for child-mode hard blocks and safe categories |
| `docs/PHASE_5_SESSION_LIFECYCLE.md` | This document |
