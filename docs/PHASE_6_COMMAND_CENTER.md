# Phase 6 Command Center Transport & UI Architecture

This document specifies the transport frames, frontend state reducers, and accessible UI components for H1KARI's Phase 6 Command Center (`core/phase6_transport.py`, `hikari-frontend/src/utils/phase6/`, and `hikari-frontend/src/components/phase6/`).

---

## 1. Transport & Protocol Architecture

All Phase 6 command-center WebSocket frames use `protocol_version: 1` and strict request correlation via canonical `request_id` identifiers (`^[a-z0-9][a-z0-9_.-]{0,79}$`).

### Server → Client Messages (`PHASE6_SERVER_MESSAGE_TYPES`)
1. `phase6_integration_status`: Ecosystem integration status (10 states: `unavailable`, `disabled`, `configuring`, `ready`, `degraded`, `approval_required`, `active`, `cancelling`, `failed`, `revoked`).
2. `phase6_agent_run_update`: Bounded agent runs (states: `preview`, `waiting_for_approval`, `running`, `observing`, `correcting`, `succeeded`, `denied`, `failed`, `cancelled`, `exhausted`). Step/action/budget counts only; no hidden chain-of-thought or raw parameters.
3. `phase6_time_sense_update`: Task/job age, heartbeat status, stuck reason, next allowed check-in, suppression state, background status.
4. `phase6_repo_intel_update`: Bounded query hits with path, line, symbol, score, and provenance. No whole-file dumps.
5. `phase6_skill_evolution_update`: Skill package evolution status and permissions. Excludes auto-installation controls.
6. `phase6_home_assistant_proposal`: Action proposals with entity, domain, service, risk badge, effect summary, nonce, and TTL.
7. `phase6_encrypted_sync_update`: Status and conflict counts. Exposes `exposes_plaintext: false` (zero object contents or private filenames).
8. `phase6_remote_worker_update`: Remote job telemetry. Exposes `verified_local_authority: false` (remote output never local authority).
9. `phase6_model_eval_update`: Evaluated model candidate scores, latency, privacy class (`local_only`, `gateway_ok`, `remote_ok`), and rejection reason.
10. `phase6_error`: Fixed safe error codes (`invalid_request`, `unauthorized`, `unavailable`, `denied`, `expired`, `revoked`, `locked`, `closed`, `approval_required`, `not_found`, `stale_request`, `duplicate_request`, `internal_error`).

---

## 2. Frontend State Reducer (`hikari-frontend/src/utils/phase6/phase6State.ts`)

- **Immutable State**: State updates produce frozen state snapshots (`reducePhase6State`).
- **Submit Locking**: `begin_request` sets `submitLocked: true` to prevent duplicate submissions.
- **Correlated Updates**: Every incoming server message, including errors, must
  match a currently active `state.requestId`; stale and unsolicited responses
  are ignored.
- **Sensitive Clearing**: Sensitive proposals (Home Assistant nonces, repo hits, skill evolution details) clear on disconnect, close, revoke, logout, or explicit reset.
- **Zero Local Caching**: `localStorage`, `sessionStorage`, `IndexedDB`, and service worker caches are prohibited.

---

## 3. Accessible UI Components (`hikari-frontend/src/components/phase6/`)

- `IntegrationStatusPanel`: Text + SVG icon indicators for all 10 integration status states.
- `AgentRunPanel`: Step budget progress bar (`role="progressbar"`) with safe summaries.
- `TimeSensePanel`: Task age and heartbeat telemetry layout.
- `RepoIntelPanel`: Tabular result hits (`<table>`, `<caption>`, `<th>`, `<td>`).
- `SkillEvolutionPanel`: Declared permissions list with explicit policy notice.
- `HomeAssistantPanel`: 2-phase confirmation dialog explaining WHAT, TARGET, EFFECT, and EXPIRY, with accessible ~44px buttons.
- `EncryptedSyncPanel`: Sync status and conflict counts without plaintext exposure.
- `RemoteWorkerPanel`: Worker job status with local authority disclaimers.
- `ModelEvalPanel`: Measured scores, latency, capabilities, and rejection reasons.
- `Phase6CommandCenter`: Main accessible container with landmark navigation, polite screen reader live regions, high contrast, reduced motion, and safe unavailable fallback UI.

---

## 4. Mira Integration Instructions

1. Register Phase 6 WebSocket message handlers in the server dispatch loop:
   ```python
   from core.phase6_transport import parse_phase6_client_frame, build_error_frame
   ```
2. Import `Phase6CommandCenter` in frontend application views:
   ```tsx
   import { Phase6CommandCenter } from "./components/phase6/Phase6CommandCenter";
   ```
3. Pass WebSocket frame dispatcher callback to `onSendClientFrame`.
