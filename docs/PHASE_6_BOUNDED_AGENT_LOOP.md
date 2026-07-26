# Phase 6 Bounded Agent Loop Foundations

## Status

This document describes the **foundations** package in `core/phase6_agent/`.
The inert Phase 6 facade is attached to the live orchestrator through
`core/phase6_runtime.py`, but bounded execution remains disabled by default.
There is no WebSocket, frontend, voice, Brain, or executor route to this kernel.

## Purpose

Provide a deterministic, injected execution kernel:

```text
reason → authorize → act → observe → correct
```

inside HIKARI’s existing policy, audit, task, and tool boundaries.

## Public API

| Symbol | Role |
|--------|------|
| `LoopRequest` / `LoopBudget` | Caller-supplied goal, exact tool allowlist, budgets |
| `ProposedAction` | Planner output (zero authority) |
| `AuthorizationDecision` / `ApprovalReference` | Injected policy + scoped approval |
| `AuthorizedAction` | Single-attempt allow after authorize+audit |
| `ActionObservation` | Structured observation only |
| `BoundedAgentLoop` | Deterministic loop runner |
| `RemoteWorkerAuthorityEnvelope` | Non-delegable remote authority claim |
| `validate_remote_envelope` / `accept_remote_result` | Fail-closed remote validation |

## State machine

`CREATED → PLANNING → AUTHORIZING → (WAITING_FOR_APPROVAL) → ACTING → OBSERVING → (CORRECTING → PLANNING…) → SUCCEEDED | DENIED | FAILED | CANCELLED | EXHAUSTED`

Cancellation is checked before and after every injected boundary. Time comes
only from an injected monotonic clock.

## Authority rules

- Planner/model/system/remote proposals grant **zero** authority.
- Every action is authorized immediately before execution.
- `DENY` stops without calling the executor.
- `REQUIRE_APPROVAL` requires an exact, scoped, unexpired, unused approval.
- Policy is rechecked after approval and must explicitly return `ALLOW`; a
  repeated `REQUIRE_APPROVAL` never becomes execution authority.
- Audit failure fails closed for executable actions.
- No wildcard or prefix tool/target authority.

## Budgets

Caller budgets are clamped below hard maximums for planning attempts, total
steps, tool actions, corrections, consecutive failures, observation length,
event history, elapsed monotonic duration, and remote responses.

## Remote workers

Optional remote-worker contracts exist without networking:

- verified worker identity is caller-supplied
- envelopes carry exact task, capability, targets, expiry, nonce, and limits
- expired/revoked/replayed/mismatched/unsigned envelopes fail closed
- remote results are untrusted evidence only
- remote results cannot mark tasks successful or execute local actions

## Explicit non-goals / limitations

- No live integration with orchestrator/server/protocol/frontend/voice/Brain
- No filesystem, network, subprocess, database, Git, or model calls in-package
- No chain-of-thought persistence or exposure
- No second autonomous orchestrator
- No custom cryptography (signature verification is injected)
- Correction cannot broaden scope, change actor/session, add undeclared tools,
  increase budgets, or retry non-retryable failures

## Runtime integration boundary

`HIKARI_Orchestrator.phase6` exposes the facade. Constructing it has no external
side effects. An embedding runtime must explicitly enable bounded execution and
inject planner, policy authorizer, content-free auditor, executor, clock, ID
factory, approval resolver, and cancellation adapters. No permissive defaults
exist.
