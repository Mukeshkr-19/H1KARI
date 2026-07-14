# WP-003 Characterization Matrix

Status: active
Baseline: `develop` at `c80166f`
Reviewed: 2026-07-13

## Purpose

This matrix records which current behaviors are protected before architectural migration and which gaps require focused branches. A test count alone is not coverage: each row names the release behavior, evidence, missing boundary, and acceptance condition.

## Coverage matrix

| Surface | Current evidence | Confidence | Gap and acceptance condition | Priority |
|---|---|---|---|---|
| CLI argument safety | Brain v2 promotion/repair flag tests, task CLI tests, CLI safety intent tests, `hikari.py --help` in doctor | medium | Runtime modes are ordered `if` statements rather than an exclusive contract. Add subprocess characterization proving conflicting `--text`, `--server`, `--daemon`, and `--tray` modes fail without starting a side effect. | high |
| CLI read-only operations | Brain v2 eval/status/repair tests, memory status tests, task-list tests | high | Preserve read-only isolation and redacted output while parser changes land. No immediate gap beyond the runtime-mode contract. | medium |
| Server pairing authorization | pairing-page escaping and headers; voice/typed message unit flows | low | Pairing is not an authorization boundary: unpaired clients can send message, voice, status, identify, ping, and preference events. The welcome payload and HTTP status expose the pairing code. Require per-connection pairing before protected events, never disclose the code remotely, and test disconnect cleanup. | critical |
| Server HTTP surfaces | `/qr` escaping and `/connect` hardening headers | low | `/api/status` exposes pairing code and device details without authentication; QR behavior can expose a connection secret. Make public status minimal and non-secret, then characterize all three endpoints. | critical |
| Server protocol errors | invalid JSON returns a stable error; voice lifecycle failures redact one internal marker | medium | Unknown message types and unexpected exceptions lack a stable, non-sensitive contract. Add bounded error schemas after pairing enforcement. | high |
| Doctor quick checks | formatting, quick-check names, clean-clone private-layout behavior, live command gate | high | Full-doctor failure aggregation and exit-code behavior are not isolated from expensive subprocesses. Characterize command selection, timeout, and failure reporting. | medium |
| Brain v2 storage/retrieval | episode separation, reviewed truth, repair lifecycle, rollback, conflict redaction, guest isolation, eval 8/8, live QA | high | Maintain current gates. New architecture work must not weaken reviewed-memory authority, source links, repair history, or live-data isolation. | blocking regression gate |
| Brain v2 CLI | subprocess isolation, safe accept/no-promote, explicit promotion/repair tokens, read-only reconciliation | high | No immediate characterization gap. Preserve exact tokens, redaction, and copy-only legacy repair behavior. | blocking regression gate |
| Daemon structure | one entrypoint, one owned loop, wake/active speaker checks, fail-closed verification errors | medium | Tests inspect the AST but do not execute startup, stop, timeout, state transition, or dependency-failure paths. Introduce narrow seams and behavioral tests without redesigning the daemon. | high |
| Voice identity math | similarity direction, degenerate cosine fallback, pitch edge cases, guest/owner session context | medium | Model-load failure, cache location, enrollment persistence, malformed profile handling, and no-download status are not characterized. Tests must not download weights or touch live biometric data. | high |
| Voice recognition backends | import checks and locked clean environment | low | Backend choice, model id, first-use download, cache, offline behavior, and Google audio egress are implicit. Add a read-only model/backend status contract before voice expansion. | high |
| Frontend API behavior | source-level voice race tests, companion state machine, server voice event ordering, clean production build | medium | The frontend assumes pairing is sufficient but the server does not enforce it; protocol shapes are duplicated rather than generated/shared. First bind behavior to server authorization, then define a versioned schema boundary. | high |
| Frontend accessibility | some semantic labels and keyboard paths in source | low | No automated accessibility audit or representative manual checklist exists for the claimed client flow. Add after the protected protocol is stable. | medium |

## Execution order

1. Enforce server pairing authorization and remove remote secret disclosure.
2. Characterize mutually exclusive CLI runtime modes.
3. Add behavioral daemon lifecycle seams and tests.
4. Add read-only voice backend/model/cache status without downloading weights.
5. Characterize full-doctor subprocess failures and timeouts.
6. Define a versioned server/frontend message schema and add accessibility checks.

Each item lands through its own branch. Brain v2 full gates remain mandatory for shared orchestration changes, and `main` remains unchanged until the owner explicitly authorizes a release merge.
