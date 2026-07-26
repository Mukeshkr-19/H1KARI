# Phase 6 Live Adapters

## Purpose

Phase 6 live adapters are **isolated, optional, disabled-by-default** wrappers
around the inert contracts in `core.phase6_ecosystem` and `core.phase6_agent`.
They provide a safe boundary for real (but still injected) transport and storage
adapters without enabling any live capability by default.

This document describes the five adapter families, their authority boundaries,
and the required injection points before they can be enabled.

## Scope

- `core.phase6_adapters.home_assistant` — Home Assistant transport adapter
- `core.phase6_adapters.encrypted_sync` — Encrypted sync storage adapter
- `core.phase6_adapters.remote_worker` — Remote worker coordinator
- `core.phase6_adapters.skill_staging` — Reviewed skill staging
- `core.phase6_adapters.measured_routing` — Measured model routing

## Non-Goals

- Not a replacement for `core/action_policy.py` or action audit.
- Not an enabled-by-default feature.
- Not a real network/storage/model implementation.
- No autonomous action execution.
- No second runtime or memory authority.

## Default-Deny Behavior

Constructing any adapter without an explicit configuration returns a disabled
adapter:

```python
from core.phase6_adapters import HomeAssistantAdapter

ha = HomeAssistantAdapter()
assert ha.state == "disabled"
```

Every adapter requires:

1. An explicit config object.
2. Injected clock and ID factory.
3. Injected transport/storage/crypto/signature/observation adapters as appropriate.
4. Caller-supplied audit/authorization hooks where applicable.

No real side effect occurs when an adapter is disabled.

## Adapter APIs and State Machines

### 1. Home Assistant Adapter

**Contracts**: `HomeAssistantAdapterConfig`, `HomeAssistantAdapter`,
`HomeAssistantAdapterOutcome`, `HomeAssistantAdapterReason`

State machine:

```text
DISABLED
  -> ENABLED (after valid config)
  -> prepare(entity, service, actor, nonce)
       -> ALLOW (read-only)
       -> REQUIRE_CONFIRMATION (state-changing or sensitive)
       -> DENY (policy/URL/actor failure)
  -> confirm_and_execute(proposal, confirmation, actor)
       -> audit -> one injected transport call -> observation
```

Safety rules:

- Only exact base URL from config is permitted.
- HTTPS/WSS by default; loopback HTTP only with an explicit flag.
- Wildcards, userinfo, fragments, and query strings are rejected.
- Base URL path traversal is rejected.
- Confirmation must match proposal ID, nonce, actor, and expiry.
- Replay of consumed confirmations is rejected.
- The adapter invokes transport once, audits authorization first, and bounds
  returned evidence bytes. A production transport must independently enforce
  its deadline, redirect/DNS pinning, and idempotency contract.
- Credentials never appear in repr, logs, or audit events.

### 2. Encrypted Sync Adapter

**Contracts**: `EncryptedSyncAdapterConfig`, `EncryptedSyncAdapter`,
`EncryptedSyncTransactionProposal`

Safety rules:

- Operates on opaque ciphertext descriptors only.
- Injected verified crypto provider; no plaintext inspection.
- Device trust and revocation checks before planning.
- Nonce replay is blocked.
- Hard bounds on object count, object size, total bytes, and version.
- Atomic staging/commit/rollback via injected storage interface; only the exact
  proposal staged by the adapter can be committed or rolled back.
- Content-addressed commit and rollback digests.
- Concurrent-update conflicts are surfaced, never silently resolved.

### 3. Remote Worker Coordinator

**Contracts**: `RemoteWorkerCoordinatorConfig`, `RemoteWorkerCoordinator`

Safety rules:

- Envelope validation compares signed input with trusted caller-supplied worker,
  task, capability, and target scope and fails closed on expiry, revocation,
  replay, mismatch, or bad signature.
- Durable nonce store is injected.
- Bounded concurrent jobs, response count, response size, elapsed time, and retries.
- Quarantine and revocation gates. A production worker transport still needs a
  cancellation acknowledgement contract before activation.
- Remote results are evidence only; they cannot mark tasks successful or execute local actions.
- Remote results remain evidence only. A local authorizer is required before
  any separate local action may be proposed.

### 4. Skill Staging Adapter

**Contracts**: `SkillStagingAdapterConfig`, `SkillStagingAdapter`,
`SkillStagingProposal`

Safety rules:

- Injected archive reader and signature verifier.
- Rejects traversal, absolute paths, NUL bytes, executable magic, archive bombs,
  and any archive entry not byte-identical to the reviewed candidate.
- Enforces file count, file size, total size, and nesting depth limits.
- Rechecks manifest, digests, signature, publisher trust, and review identity.
- Produces a content-addressed install proposal and an optional rollback proposal;
  it never installs either proposal.
- No production skill mutation.
- No auto-install or assistant self-approval.

Known limitation: the injected `ArchiveReaderInterface` only returns path/content
mappings, so hardlink/device-node detection requires an extended reader contract.

### 5. Measured Routing Adapter

**Contracts**: `MeasuredRoutingAdapterConfig`, `MeasuredRoutingAdapter`,
`RoutingAdapterResult`, `CanaryProposal`

Safety rules:

- Provider-neutral benchmark observation ingestion is injected.
- Enforces privacy class, capabilities, safety, memory, latency, and cost.
- Advisory recommendation only.
- Canary and rollback proposals help prevent route flapping.
- Never edits or invokes `core/router.py`.
- Remote candidates never win when scenario privacy is `LOCAL_ONLY`.

## Authority and Privacy Boundaries

- All adapters are disabled unless explicitly configured.
- All external behavior is injected; no hardcoded transport, storage, or model calls.
- Reprs are content-free.
- Reason codes are fixed and non-attributable.
- No private Brain state, runtime secrets, credentials, or personal data is accessed.

## Verification

Run the focused adapter tests:

```bash
.venv/bin/python -m pytest tests/test_phase6_adapter_layer.py -q
```

Compile check:

```bash
.venv/bin/python -m compileall -q core/phase6_adapters
```

Diff check:

```bash
git diff --check -- core/phase6_adapters tests/test_phase6_adapter_layer.py docs/PHASE_6_LIVE_ADAPTERS.md
```

## Known Limitations

- The adapters are contract/test scaffolding, not live integrations.
- Home Assistant transport interface must be supplied by Mira-owned wiring.
- Encrypted sync storage, crypto, and device-trust adapters must be supplied.
- Remote worker signature verifier and durable nonce store must be supplied.
- Skill staging archive reader must be supplied. Its current path/content-only
  interface cannot prove entry types, so production archive installation remains
  disabled until link/device metadata is added and checked.
- Measured routing benchmark source and canary confirmation UI must be supplied.

## Mira-Owned Wiring

- Wire `HomeAssistantAdapter` into the action policy flow with a real HTTPS/WSS transport.
- Wire `EncryptedSyncAdapter` to the encrypted storage backend and device-trust registry.
- Wire `RemoteWorkerCoordinator` to the durable nonce store and local authorizer.
- Wire `SkillStagingAdapter` to the archive reader and skill-install executor.
- Wire `MeasuredRoutingAdapter` to the benchmark ingestion service.
- Add owner-configuration tests with real endpoints before enabling any adapter.

## Files

| File | Purpose |
|------|---------|
| `core/phase6_adapters/__init__.py` | Public exports |
| `core/phase6_adapters/contracts.py` | Shared adapter contracts |
| `core/phase6_adapters/home_assistant.py` | Home Assistant adapter |
| `core/phase6_adapters/encrypted_sync.py` | Encrypted sync adapter |
| `core/phase6_adapters/remote_worker.py` | Remote worker coordinator |
| `core/phase6_adapters/skill_staging.py` | Skill staging adapter |
| `core/phase6_adapters/measured_routing.py` | Measured routing adapter |
| `tests/test_phase6_adapter_layer.py` | Adversarial tests |
| `docs/PHASE_6_LIVE_ADAPTERS.md` | This document |
