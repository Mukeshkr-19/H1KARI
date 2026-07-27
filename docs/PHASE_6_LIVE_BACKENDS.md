# Phase 6 Live Backends

## Purpose

`core.phase6_live` contains real, optional, disabled-by-default backend
implementations for the injected interfaces in `core.phase6_adapters`.  They
provide durable storage, network transport, and archive inspection without
enabling any live capability by default.

## Scope

- `core.phase6_live.home_assistant` — HTTPS Home Assistant transport
- `core.phase6_live.encrypted_sync` — SQLite-backed sync registries and storage
- `core.phase6_live.remote_worker` — SQLite-backed remote worker state
- `core.phase6_live.skill_staging` — safe archive metadata reader
- `core.phase6_live.measured_routing` — SQLite-backed observation source

## Non-Goals

- Not enabled by default.
- Not a replacement for action policy, audit, or orchestration.
- Not a second runtime, Brain, or task ledger.
- Not a model runner or remote worker network transport.

## Default-Deny Behavior

Every backend requires explicit construction with a configuration object or
`db_path`.  Constructing without arguments leaves the backend disabled and
performing no I/O.

```python
from core.phase6_live import LiveHomeAssistantTransport

# Disabled — no network side effects.
ha = LiveHomeAssistantTransport()
```

## Backend APIs

### Home Assistant HTTP Transport

```python
from core.phase6_live import LiveHomeAssistantTransport

transport = LiveHomeAssistantTransport(
    config=LiveHomeAssistantTransportConfig(
        base_url="https://hass.local:8123",
        credential_provider=lambda: get_bearer_token(),
        allow_loopback_http=False,
        connect_timeout_seconds=10.0,
        max_response_bytes=1_048_576,
        connection_factory=my_factory,  # optional, for synthetic tests
    )
)
```

Safety:
- HTTPS/WSS only; loopback HTTP only when explicitly allowed.
- Exact configured scheme, host and port enforced.
- Redirects denied.
- DNS rebinding mitigated by resolving once and connecting by IP with SNI.
- Already-expired requests are rejected before DNS or credential access; the
  injected monotonic deadline is rechecked after resolution and before connect.
- Bounded response size and elapsed time.
- TLS verification is always enabled; no trust-all context.
- Credentials supplied per call; no persistence in repr/logs/evidence.
- Optional injected `HTTPConnectionFactory(ip, port, use_tls, timeout)` for
  synthetic tests; the default path uses `http.client.HTTPSConnection` with the
  system SSL context.

### Encrypted Sync Durable Backends

```python
from core.phase6_live import (
    SqliteNonceRegistry,
    SqliteDeviceRegistry,
    SqliteTransactionRegistry,
    SqliteEncryptedSyncStorage,
)

nonce = SqliteNonceRegistry(Path("/tmp/hikari/sync_nonce.db"))
device = SqliteDeviceRegistry(Path("/tmp/hikari/sync_device.db"))
tx = SqliteTransactionRegistry(Path("/tmp/hikari/sync_tx.db"))
storage = SqliteEncryptedSyncStorage(Path("/tmp/hikari/sync_storage.db"))
```

Lifecycle:

```text
PLANNED -> STAGED -> COMMITTED
             ↘ ROLLED_BACK
```

- Durable nonce replay registry across restarts.
- Device revocation rechecked before stage and before commit.
- Atomic stage/commit/rollback with SQLite transactions.
- Ciphertext digests stored in plan metadata; actual ciphertext must be stored
  by a separate, caller-supplied object store (contract limitation).

### Remote Worker Durable State

```python
from core.phase6_live import SqliteRemoteWorkerState

state = SqliteRemoteWorkerState(Path("/tmp/hikari/rw_state.db"))
```

Stores:
- durable nonce consumption
- worker trust/revocation (explicit enrollment; re-enrollment requires the
  dedicated `reenroll_worker` path)
- worker quarantine keyed by worker ID
- job/result correlation
- cancellation acknowledgements (insert-once with CAS state transition)

No remote network transport is implemented here. Durable expiry requires a
valid caller-supplied epoch deadline; no monotonic clock value is persisted as
security expiry. Legacy schemas containing `expires_at_mono` fail closed and
must be migrated through a separately reviewed private-data procedure.

### Safe Archive Metadata Reader

```python
from core.phase6_adapters.skill_staging import SkillStagingAdapterConfig
from core.phase6_live import LiveArchiveEntryReader

reader = LiveArchiveEntryReader(SkillStagingAdapterConfig())
entries = reader.read_entries(archive_bytes)
```

Uses only `zipfile` and `tarfile`.  Reads metadata only; never extracts to
disk.  Rejects symlinks, hardlinks, devices, traversal, absolute paths, NUL
bytes, Unicode confusables, case collisions, and excessive nesting.

Two-pass design:

1. **Metadata pass** — enumerates entry headers, validates paths and collisions,
   checks file count cap (`max_files + 1`), per-entry size, aggregate size and
   compression ratio bounds. Rejects encrypted or unsupported ZIP compression.
2. **Content pass** — streams only accepted regular files in bounded chunks,
   enforces aggregate byte budget, checks cancellation at each chunk.

Tar compression safety: because tar members do not expose compressed size, the
reader bounds aggregate decompressed output and applies a conservative aggregate
expansion ratio against the archive input size.

### Measured Routing Observation Source

```python
from core.phase6_live import SqliteMeasuredRoutingSource

source = SqliteMeasuredRoutingSource(Path("/tmp/hikari/routing.db"))
```

- Persists benchmark measurements with bounded per-candidate and total counts.
- Provides latest measurement per candidate.
- Persists canary confirmation and rollback evidence (insert-once, CAS state
  transitions).
- Advisory only; never edits `core/router.py`.

**Interface blocker:** the upstream `BenchmarkObservationInterface` does not
include a `scenario_id` parameter, so `SqliteMeasuredRoutingSource` requires a
non-empty `scenario_id` and fails closed when one is not supplied. Callers must
use the backend extension method or update the adapter contract to carry scenario
correlation safely.

## Authority and Privacy Boundaries

- All backends are disabled unless explicitly configured.
- All external behavior is injected; no hardcoded credentials or endpoints.
- Reprs are content-free.
- SQLite files are created with restrictive owner-only permissions.
- No private Brain state, runtime secrets, or personal data is accessed.

## Verification

Compile:

```bash
.venv/bin/python -m compileall -q core/phase6_live
```

Run focused tests:

```bash
.venv/bin/python -m pytest tests/test_phase6_live_*.py -q
```

Diff check:

```bash
git diff --check -- core/phase6_live tests/test_phase6_live_*.py docs/PHASE_6_LIVE_BACKENDS.md docs/PHASE_6_PLATFORM_EVALUATION.md
```

## Known Limitations

- `SqliteEncryptedSyncStorage` stores plan metadata and transaction state.  It
  also provides implementation-specific `store_ciphertext(digest, ciphertext)`
  and `load_ciphertext(digest)` methods because the injected
  `EncryptedSyncStorageInterface` contract does not yet include ciphertext
  storage; callers that know they have the SQLite backend may use these
  methods, or store ciphertext separately keyed by digest.
- Home Assistant transport uses `http.client` with IP resolution + SNI for DNS
  rebinding mitigation but still relies on the configured base URL matching the
  actual service certificate.  It accepts an injected monotonic clock via
  `LiveHomeAssistantTransportConfig.clock` for deterministic deadline tests.
- The remote worker backend provides durable state only; network transport is
  out of scope for this workstream. Jobs persist `expires_at_epoch` only and
  reject invalid, expired, or legacy monotonic-expiry schemas.
- `LiveArchiveEntryReader` does not parse nested archives or opaque formats.
- ZIP metadata enumeration is still driven by `zipfile.ZipFile.infolist()`, which
  reads the central directory before the `max_files + 1` cap is enforced.  A
  fully streaming ZIP reader would need a lower-level parser.
- The measured routing backend rejects `record_measurement(..., scenario_id="")`
  because the upstream `BenchmarkObservationInterface` lacks scenario
  correlation.  Callers that need scenario-bound storage must call the backend
  extension directly or extend the adapter contract.

## Mira-Owned Wiring

- Wire `LiveHomeAssistantTransport` into the action policy flow with a real
  credential provider and owner configuration.
- Wire `SqliteEncryptedSyncStorage` and registries to the encrypted sync
  adapter with a caller-supplied ciphertext object store.
- Wire `SqliteRemoteWorkerState` into the remote worker coordinator.
- Wire `LiveArchiveEntryReader` into the skill staging adapter.
- Wire `SqliteMeasuredRoutingSource` into the measured routing adapter and the
  benchmark ingestion service.
- Add owner-configuration tests with real endpoints before enabling any backend.

## Files

| File | Purpose |
|------|---------|
| `core/phase6_live/__init__.py` | Public exports |
| `core/phase6_live/base.py` | Shared base utilities |
| `core/phase6_live/home_assistant.py` | HA HTTP transport backend |
| `core/phase6_live/encrypted_sync.py` | Encrypted sync durable backends |
| `core/phase6_live/remote_worker.py` | Remote worker durable state |
| `core/phase6_live/skill_staging.py` | Safe archive metadata reader |
| `core/phase6_live/measured_routing.py` | Measured routing observation source |
| `tests/test_phase6_live_*.py` | Backend tests |
| `docs/PHASE_6_LIVE_BACKENDS.md` | This document |
| `docs/PHASE_6_PLATFORM_EVALUATION.md` | Platform/provider evaluation matrix |


## Exact-cap routing retention

`SqliteMeasuredRoutingSource` prunes only when counts strictly exceed configured
caps (`count > cap`) and removes exactly `count - cap` oldest rows/scenarios.
An exact-cap store remains at the configured maximum. Retention-age pruning is
independent. Cap postconditions are checked inside the same write transaction
before commit.

## Remote result byte API

Caller-supplied `max_result_bytes` values above the backend hard maximum are
rejected as invalid configuration (not clamped). The hard maximum cannot be
enlarged by callers. Accepted content types are only `text/plain` and
`application/json`.

## Home Assistant request isolation

Deadline clock validation is request-local: a completed request cannot poison a
later request that uses a fresh synthetic clock origin. DNS results are
validated with `ipaddress`. Caller `service_data.entity_id` cannot override the
authorized entity; conflicts fail closed.
