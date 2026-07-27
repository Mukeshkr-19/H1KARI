# Phase 6 Protocol Specification

This document describes H1KARI's Phase 6 WebSocket transport protocol specifications, client request schemas, server response frames, error handling, and in-process registration.

---

## 1. Registration Architecture

Phase 1–4 message types are defined in `hikari-v1.json`. Phase 5 and Phase 6 message types are registered in-process when `core/protocol.py` is loaded:

```python
from core.phase5.transport import register_phase5_protocol
from core.phase6_transport import register_phase6_protocol

register_phase5_protocol(CLIENT_MESSAGES, SERVER_MESSAGES)
register_phase6_protocol(CLIENT_MESSAGES, SERVER_MESSAGES)
```

This preserves `hikari-v1.json` as the Phase 1–4 protocol baseline while extending `CLIENT_MESSAGES` and `SERVER_MESSAGES` in memory for `validate_client_message` and `validate_server_message`.

---

## 2. Canonical Bounds & Data Standards

1. **Protocol Version**: Fixed at `protocol_version: 1`.
2. **Canonical Identifiers**: Must match regex `^[a-z0-9][a-z0-9_.-]{0,79}$` (1 to 80 lowercase alphanumeric characters, dots, dashes, underscores).
3. **Safe Text**: Text fields enforce length limits (64, 128, or 512 chars) and forbid control characters (ASCII < 32 or 127) and Unicode format/directional marks (`\u200e`, `\u200f`, `\u202a`–`\u202e`, etc.).
4. **Finite Numbers**: Numeric fields require finite numbers (`math.isfinite(val)`). `NaN` and `Infinity` are strictly rejected.
5. **Boolean Strictness**: Boolean fields reject integer coercions (e.g. `0` or `1` passed as bool).
6. **No Client Identity Assertions**: Client frames cannot contain `actor_id`, `owner_id`, `role`, `authority`, `approved`, `audited`, or `paired`. Unknown fields are rejected.

---

## 3. Client Request Frames

### 1. `phase6_integration_list_request`
- `request_id`: Canonical ID
- `protocol_version`: `1`

### 2. `phase6_home_assistant_prepare_request`
- `request_id`: Canonical ID
- `protocol_version`: `1`
- `entity_id`: Safe text <= 128 (wildcards `*` prohibited)
- `domain`: Safe text <= 128 (wildcards `*` prohibited)
- `service`: Safe text <= 128 (wildcards `*` prohibited)
- `risk`: Enum (`"low"`, `"medium"`, `"high"`, `"critical"`)
- `effect_summary`: Safe text <= 512

### 3. `phase6_home_assistant_confirm_request`
- `request_id`: Canonical ID
- `protocol_version`: `1`
- `proposal_id`: Canonical ID
- `nonce`: Safe text <= 80

### 4. `phase6_proposal_cancel_request`
- `request_id`: Canonical ID
- `protocol_version`: `1`
- `proposal_id`: Canonical ID

### 5. `phase6_agent_run_request`
- `request_id`: Canonical ID
- `protocol_version`: `1`
- `action`: Enum (`"preview"`, `"start"`, `"confirm"`, `"cancel"`, `"status"`)
- `run_id`: Canonical ID
- Optional `nonce`: Safe text <= 80
- Optional `budget_limit`: Integer >= 0
- Optional `task_summary`: Safe text <= 512

### 6. `phase6_snapshot_refresh_request`
- `request_id`: Canonical ID
- `protocol_version`: `1`
- `target`: Enum (`"all"`, `"time_sense"`, `"repo_intel"`, `"integrations"`)

---

## 4. Server Telemetry Frames

1. `phase6_integration_status`: Status for 5 capabilities (`home_assistant`, `encrypted_sync`, `remote_workers`, `skill_evolution`, `model_evaluation`).
2. `phase6_agent_run_update`: Step/action/budget telemetry for bounded runs.
3. `phase6_time_sense_update`: Read-only Time Sense task age and heartbeat telemetry.
4. `phase6_repo_intel_update`: Read-only repository query hits.
5. `phase6_skill_evolution_update`: Skill package review state (`allows_auto_install: false`).
6. `phase6_home_assistant_proposal`: Action proposal with single-use `nonce` and `expires_at` TTL.
7. `phase6_encrypted_sync_update`: Sync status (`exposes_plaintext: false`).
8. `phase6_remote_worker_update`: Worker job status (`verified_local_authority: false`).
9. `phase6_model_eval_update`: Evaluated model scores and latency.
10. `phase6_error`: Fixed safe error code and message.

---

## 5. Safe Error Codes (`Phase6ErrorCode`)

- `invalid_request`: Payload schema validation error or wildcard violation.
- `unauthorized`: Transport connection is not a paired owner.
- `unavailable`: Feature or adapter is disabled or unconfigured.
- `denied`: Action policy or authorization audit failed.
- `expired`: Proposal TTL expired before confirmation.
- `revoked`: Resource or session was revoked.
- `locked`: Session or submit is locked.
- `closed`: Connection closed or proposal cancelled.
- `stale_request`: Proposal ID or nonce mismatch or already consumed.
- `duplicate_request`: `request_id` was already processed.
- `internal_error`: Unhandled server exception (details masked).
