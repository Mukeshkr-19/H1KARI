# Phase 5 Runtime Integration

## Purpose

`core/phase5/runtime_guard.py` provides the production-facing runtime
authorization layer for Phase 5. It composes the existing pure policy
(`core/phase5/policy.py`), session lifecycle (`core/phase5/session_lifecycle.py`),
and child-mode guard (`core/phase5/child_mode.py`) into a single fail-closed
call that authorizes runtime requests without executing any action.

The guard is stateless and deterministic: all identity, session snapshots,
grants, consents, and timestamps are caller-supplied.

## Authority Hierarchy

```
OWNER
 ├── Owner request without session (actor is OWNER)
 └── Owner request with an matching OWNER session

CHILD
 └── Child request requires a matching CHILD session with active,
     unexpired state and owner-controlled activation evidence.

TRUSTED HELPER
 └── Trusted-helper request requires a matching TRUSTED_HELPER session
     backed by a current, unexpired, non-revoked, scoped grant.

GUEST / SYSTEM
 └── Denied; a session cannot be created for these actors.
```

## Contracts

| Contract | Purpose |
|----------|---------|
| `Phase5RuntimeContext` | Transport-derived identity wrapper (core `ActorContext` + source) |
| `Phase5RuntimeRequest` | Bounded request: capability, action, resource, subject, metadata, user-initiated flag, optional session, injected time |
| `Phase5RuntimeDecision` | Final decision with policy decision, child reason, audit id, and approval flag |
| `RuntimeDecisionReason` | Stable machine-readable reasons for every runtime outcome |
| `Phase5RuntimeGuard` | Stateless guard that runs the pipeline |

## Decision Pipeline

The guard evaluates requests in this fail-closed order:

1. **Validate structural input and injected time.**
   Malformed requests or non-finite timestamps return `INVALID_INPUT`.

2. **Validate transport-derived actor context.**
   Invalid core `ActorContext` returns `INVALID_ACTOR`.

3. **Require a session for child and trusted-helper actors.**
   Non-owner actors without a matching session return `SESSION_REQUIRED`.

4. **Verify session state is active and unexpired.**
   Expired, revoked, locked, or closed sessions return the corresponding
   runtime reason (`SESSION_EXPIRED`, `SESSION_REVOKED`, `SESSION_LOCKED`,
   `SESSION_CLOSED`).

5. **Verify session actor and owner bindings.**
   The core actor id must match the session's `session_actor_id`
   (or `owner_actor_id` for owner sessions). Mismatches return
   `SESSION_ACTOR_MISMATCH`.

6. **Check capability is inside the session scope.**
   If the requested capability is not listed in `AccessSession.capabilities`,
   the guard returns `CAPABILITY_NOT_IN_SESSION`.

   The guard also re-applies session policy at use time. It rejects snapshots
   whose authority source does not match the session type, whose lifetime
   exceeds the configured maximum, whose evaluation time predates creation,
   or whose child capability set exceeds the child allowlist.

7. **For child sessions, run `classify_child_action`.**
   - Hard-denied categories stop immediately with `CHILD_HARD_DENY`.
   - Ambiguous categories require owner approval with
     `CHILD_AMBIGUOUS_REQUIRES_APPROVAL`.
   - Safe categories continue to the Phase 5 policy service; classification
     alone never grants authority.

8. **For trusted-helper sessions, validate the current grant.**
   The guard fetches the stored grant by id and checks:
   - correct helper and owner actor ids
   - not revoked
   - not expired
   - capability and scope match the request
   Failures return the matching `HELPER_GRANT_*` or `HELPER_SCOPE_MISMATCH`
   reason.

9. **Convert to `Phase5AuthorizationRequest` and call `Phase5PolicyService.authorize`.**
   This produces a single audit record and a policy decision.

10. **Preserve the policy decision and approval requirement.**
    The runtime guard never converts a denial into an allow and never
    silently elevates `REQUIRE_APPROVAL` into `ALLOW`.

## Session Requirements

- **Owner:** may operate without a session. If a session is supplied, it must
  be an `OWNER` session whose `owner_actor_id` matches the actor id.
- **Child:** must provide a `CHILD` session. The actor id must match the
  session's `session_actor_id`. The session must be active and unexpired.
- **Trusted Helper:** must provide a `TRUSTED_HELPER` session. The actor id
  must match the session's `session_actor_id`. The underlying grant is
  re-validated on every request.

## Child-Mode Hard Blocks

For child sessions, the runtime guard evaluates `classify_child_action`. The
following categories are hard-denied:

- owner/private memory
- financial purchase/payment
- external calls/messages/email/posting
- dangerous/self-harm/weapon/hazardous instructions
- credential/secret access
- unrestricted downloads/browsing
- grant creation
- policy weakening
- identity, audit, or approval bypass

Safe, child-scoped categories (e.g., `educational`, `learning`, `homework`,
`guidance`, `care`, `support`) continue to capability policy. Low-risk child
requests may be allowed; Guide My Hands and Care still require approval.
Ambiguous categories require owner approval.

## Trusted-Helper Scope Enforcement

The runtime guard enforces the helper contract:

- No session without a valid grant.
- No permanent sessions (session lifetime is bounded).
- No silent renewal (renewal metadata is blocked by policy).
- No delegation (delegation metadata is blocked by policy).
- No scope expansion (requests must fit the grant's capability, data subject,
  resource pattern, and allowed actions).
- No unrelated owner memory (subject and scope must match).
- Revocation and expiration take effect immediately from the stored grant.

## Approval and Audit Handling

- Every non-structural decision records exactly one audit row.
- Structural failures (`INVALID_INPUT`, `INVALID_ACTOR`) cannot be audited
  because the request or actor is malformed.
- Audit resource references are content-free SHA-256 digests produced by
  `opaque_resource_reference`.
- The runtime guard never stores raw private-memory content, resource
  content, or action content in the decision or audit objects.
- Runtime policy evaluation uses the request's validated injected timestamp.
  Audit identifiers and recording timestamps remain owned by the append-only
  audit store and are never rewritten after insertion.

## Injected Dependencies

The guard accepts:

- `Phase5PolicyService` (with injected `clock` and optional entity `id_factory`)
- `SessionPolicy` (optional; defaults to `default_session_policy()`)

No wall-clock reads, UUID generation, or I/O beyond the injected stores is
performed by `runtime_guard.py`.

## Privacy Properties

- `__repr__` methods never expose actor ids, session ids, request ids,
  activation evidence, grant content, or resource content.
- Validation errors are generic and do not echo sensitive input.
- Audit records contain only capability, outcome, reason, and an opaque
  resource digest.

## Known Limitations

- The guard is stateless; callers must supply current session snapshots and
  injected time.
- The guard performs no authentication. Caller must verify identity before
  invoking the guard.
- Session- and child-classification denials are audited at the runtime layer
  before reaching the policy service. Their audit reason matches the final
  runtime decision and exactly one row is recorded.
- Child-mode classification is category/descriptor based; callers must supply
  accurate action descriptors.

## Future Mira-Owned Orchestrator Wiring

- The orchestrator can create `Phase5RuntimeRequest` objects from transport
  messages and pass them to `Phase5RuntimeGuard.authorize`.
- Session activation can call `evaluate_activation_request` from
  `core/phase5/session_lifecycle` before the runtime request is formed.
- Runtime session stores can persist `AccessSession` snapshots and pass them
  back into the guard.
- The policy service can be wrapped to expose helper-grant lookup for other
  runtime components.

## Verification

Run the focused test suite:

```bash
.venv/bin/python -m pytest tests/test_phase5_policy.py tests/test_phase5_runtime_guard.py -q
```

Compile check:

```bash
.venv/bin/python -m compileall -q core/phase5/policy.py core/phase5/runtime_guard.py
```

Diff check (allowlist only):

```bash
git diff --check -- core/phase5/policy.py core/phase5/runtime_guard.py tests/test_phase5_policy.py tests/test_phase5_runtime_guard.py docs/PHASE_5_RUNTIME_INTEGRATION.md
```

## Files

| File | Purpose |
|------|---------|
| `core/phase5/runtime_guard.py` | Runtime guard contracts and pipeline |
| `core/phase5/policy.py` | Hardened policy service with explicit evaluation time and append-only audit use |
| `tests/test_phase5_runtime_guard.py` | Runtime guard tests |
| `tests/test_phase5_policy.py` | Policy service regression and hardening tests |
| `docs/PHASE_5_RUNTIME_INTEGRATION.md` | This document |
