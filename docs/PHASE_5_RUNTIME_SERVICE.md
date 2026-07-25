# Phase 5 Persistent Session Management & Production Runtime Coordinator

This document describes the design, architecture, persistent SQLite session store, and application-facing runtime coordinator for Phase 5 session management (`core/phase5/session_store.py` and `core/phase5/runtime_service.py`).

---

## 1. Architecture & Module Composition

`Phase5RuntimeService` serves as the single application-facing Phase 5 runtime coordinator. It composes the pure policy, session lifecycle, child mode, policy service, and runtime guard components without duplicating their internal decision authority.

```
                      ┌───────────────────────────────┐
                      │    Phase5RuntimeService       │
                      └───────────────┬───────────────┘
                                      │
     ┌──────────────────┬─────────────┼───────────────┬──────────────────┐
     ▼                  ▼             ▼               ▼                  ▼
┌──────────┐     ┌────────────┐ ┌──────────┐ ┌─────────────────┐ ┌─────────────┐
│  Session │     │   Policy   │ │ Runtime  │ │Session Lifecycle│ │ Child Mode  │
│  Store   │     │  Service   │ │  Guard   │ │   Evaluation    │ │   Guard     │
└──────────┘     └────────────┘ └──────────┘ └─────────────────┘ └─────────────┘
```

---

## 2. Session Store (`core/phase5/session_store.py`)

The `Phase5SessionStore` manages `AccessSession` snapshots in a local, bounded SQLite database.

### Database Schema
```sql
CREATE TABLE IF NOT EXISTS phase5_sessions (
    session_id TEXT PRIMARY KEY,
    session_type TEXT NOT NULL,
    owner_actor_id TEXT NOT NULL,
    session_actor_id TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    capabilities TEXT NOT NULL,
    authority_source TEXT NOT NULL,
    evidence_digest TEXT,
    grant_id TEXT,
    revoked_at REAL,
    locked_at REAL,
    closed_at REAL,
    revision INTEGER NOT NULL DEFAULT 1,
    serialized_snapshot TEXT NOT NULL
);
```

### Key Properties
- **Permissions**: Directory permissions `0o700`, database file permissions `0o600`.
- **Optimistic Concurrency**: Stored rows track a `revision` counter (starting at 1). Updates compare expected revision counters (`StaleRevisionError` on mismatch).
- **Immutability Protection**: Session identity, authority source, evidence digest, and bound grant cannot be modified during updates.
- **Capability Expansion Guard**: Updates verify that updated capabilities are a subset of existing capabilities (broadening capabilities fails closed).
- **Privacy Hashing**: Raw activation evidence is never stored; only its SHA-256 hex digest (`evidence_digest`) is persisted. Reload/save cycles preserve that digest instead of hashing it again.
- **Deterministic Ordering**: Sessions are indexed and queried by `created_at DESC, session_id ASC`.

---

## 3. Runtime Coordinator (`core/phase5/runtime_service.py`)

`Phase5RuntimeService` provides the public API for activating, authorizing, transitioning, listing, and revoking Phase 5 access sessions.

### Public API Operations
1. `activate_session(actor_context, activation_request) -> SessionDecision`
   - Requires a transport-derived `OWNER` whose actor id exactly matches the requested owner.
   - Evaluates activation requests via `session_lifecycle.evaluate_activation_request`.
   - Persists successful sessions atomically in `Phase5SessionStore`.
2. `authorize(request) -> Phase5RuntimeDecision`
   - Loads the caller's active session if missing from request.
   - Evaluates request through `Phase5RuntimeGuard.authorize` **exactly once** using a single injected timestamp.
3. `transition_session(session_id, to_state, actor_id, authority) -> SessionDecision`
   - Applies state transition rules atomically using optimistic concurrency.
4. `revoke_helper_access(grant_id, owner_actor_id) -> bool`
   - Owner-scoped operation: the supplied owner must own the grant before it is revoked, then all dependent sessions are marked `REVOKED`.
5. `expire_due_sessions() -> int`
   - Expire sessions whose `expires_at <= now`.

---

## 4. Safety Invariants

- **Default Deny**: Unrecognized inputs or invalid authority sources fail closed.
- **Content-Free Reprs**: `__repr__` output across all contracts emits no sensitive evidence, actor IDs, or tokens.
- **Zero Raw Evidence Persistence**: Only SHA-256 digests of activation evidence are stored.
- **Immediate Grant Revocation**: Revoking a helper grant immediately invalidates all active sessions bound to that grant.
- **No Implicit Renewal or Delegation**: Helper grants and child sessions cannot be renewed or delegated.

---

## 5. Verification

### Test Commands
```bash
.venv/bin/python -m pytest \
  tests/test_phase5_session_store.py \
  tests/test_phase5_runtime_service.py -q

.venv/bin/python -m compileall -q \
  core/phase5/session_store.py \
  core/phase5/runtime_service.py

git diff --check -- \
  core/phase5/session_store.py \
  core/phase5/runtime_service.py \
  tests/test_phase5_session_store.py \
  tests/test_phase5_runtime_service.py \
  docs/PHASE_5_RUNTIME_SERVICE.md
```

---

## 6. Known Limitations & Mira Integration Instructions

- **Caller-Supplied Authentication**: `Phase5RuntimeService` does not perform network user authentication; callers must supply authenticated `ActorContext` instances derived from transport/session layer.
- **Transport status**: The WebSocket server composes this service lazily and
  currently exposes the owner control plane. A separate authenticated device
  binding is still required before child/helper devices can act as their own
  runtime identities over WebSocket; client-supplied roles remain forbidden.
