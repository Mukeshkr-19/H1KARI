# Phase 6 Ecosystem Contracts & Authority Specification

This document specifies the authority boundaries, conflict models, and scoring policies for H1KARI's Phase 6 ecosystem adapters (`core/phase6_ecosystem/home_assistant.py`, `core/phase6_ecosystem/encrypted_sync.py`, and `core/phase6_ecosystem/model_evaluation.py`).

---

## 1. Home Assistant Authority Model

### Explicit Boundaries
- **No Wildcards**: Domain, entity, and service references must be exact (`*` characters are strictly rejected).
- **Read vs. Execute Segregation**: Read-only operations (`get_state`, `listen_event`) and state-changing operations (`turn_on`, `lock`, `unlock`) are governed independently.
- **Two-Phase Action Boundary**: State-changing and sensitive actions follow a strict 2-phase boundary:
  1. `prepare_action(...)` -> returns `REQUIRE_CONFIRMATION` and an immutable `HomeAssistantActionProposal` with nonce and TTL.
  2. `authorize_execution(...)` -> requires explicit `HomeAssistantConfirmation` from an authenticated `OWNER` context matching `proposal_id` and `nonce`.
- Confirmations also exact-match the authenticated actor, are timestamp-bounded,
  and are consumed once. Direct/unprepared proposals fail closed.
- Service domains must match entity domains. Payload-bearing service calls are
  currently denied because reviewed per-service field schemas are not yet implemented.
- **Sensitive Entity Protection**: Locks, alarm control panels, doors, cameras, covers, and valves default to `DENY` for non-owner actors (`GUEST`, `SYSTEM`, `UNKNOWN`).
- **No Network Transport**: `HomeAssistantContractEvaluator` performs zero HTTP, MQTT, or socket communication.

---

## 2. Encrypted Sync Conflict & Privacy Model

### Zero Plaintext & Opaque Objects
- `EncryptedObjectDescriptor` tracks opaque `ciphertext_digest` (SHA-256) and sizes. Plaintext is never stored, inspected, or transmitted.
- Encryption providers must be explicitly verified (`is_verified == True`). Unverified providers fail closed (`UNVERIFIED_PROVIDER`).
- Every descriptor is passed through the injected provider verifier; provider
  exceptions fail closed.
- Device trust is verified via explicit `DeviceTrustRecord`. Device names confer zero owner authority.
- The trusted device ID must exactly match the remote manifest device ID.

### Conflict Resolution Strategy
- **Fail-Closed Conflicts**: Synchronizations never silently overwrite via last-write-wins.
- **Concurrent Modification**: Equal versions with differing ciphertext digests produce `SyncConflict(reason="concurrent_update")`.
- **Brain Authority Preservation**: Local vs. remote `authority_state` mismatches (`accepted` vs. `pending` vs. `rejected`) trigger explicit conflicts (`authority_mismatch`).
- **Deletions**: Object deletions use explicit tombstones (`is_tombstone=True`).

---

## 3. Measured Model Routing Evaluation

### Egress & Privacy Policy
Candidates are classified into three strict privacy levels:
1. `LOCAL_ONLY` (on-device models)
2. `GATEWAY_OK` (local network gateways like OmniRoute / 9Router)
3. `REMOTE_OK` (remote cloud providers)

If an `EvaluationScenario` specifies `max_privacy_class = LOCAL_ONLY`, any candidate requiring `GATEWAY_OK` or `REMOTE_OK` is immediately rejected with `PRIVACY_EGRESS_FORBIDDEN`.

### Multi-Criteria Scoring Formula
For eligible candidates passing all hard capability, safety, quality, latency, and cost thresholds:

$$\text{Base Score} = (\text{Quality} \times 0.5) + (\text{Safety} \times 0.3) + (\text{Reliability} \times 0.2)$$

$$\text{Final Score} = \min(1, \text{Base Score} + \text{Local Bonus}) \quad (\text{if } \text{prefer\_local} \text{ and candidate is local})$$

Ties are broken deterministically by alphabetical `candidate_id` order.
Memory limits, measurement identity, and future/stale measurement timestamps
are hard eligibility gates.

---

## 4. Explicit Non-Goals & Limitations

- **No Live Adaptors**: These contracts do not perform live Home Assistant REST/WebSocket calls, sync network transfers, or LLM inference.
- **No Direct Router Mutation**: `ModelRoutingEvaluator` does not mutate `core/router.py`.

---

## 5. Integration Test Requirements

Before deploying Phase 6 adapters to production:
1. Wire `HomeAssistantContractEvaluator` with actual Home Assistant WebSocket client in daemon services.
2. Implement E2E end-to-end encrypted storage sync adapters for device backup.
3. Hook `ModelRoutingEvaluator` into `core/router.py` request dispatch.
