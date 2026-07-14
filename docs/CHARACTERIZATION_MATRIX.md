# WP-003 Characterization Matrix

Status: active
Baseline: `develop` at `7599647`
Reviewed: 2026-07-13

## Purpose

This matrix records which current behaviors are protected before architectural migration and which gaps require focused branches. A test count alone is not coverage: each row names the release behavior, evidence, missing boundary, and acceptance condition.

## Coverage matrix

| Surface | Current evidence | Confidence | Gap and acceptance condition | Priority |
|---|---|---|---|---|
| CLI argument safety | Brain v2 promotion/repair flag tests, task CLI tests, CLI safety intent tests, `hikari.py --help` in doctor, subprocess coverage for every conflicting runtime-mode pair and the `--bg` alias | high | Resolved in `safety/cli-runtime-mode-exclusivity`. Preserve parser-level rejection before macOS UI imports or runtime startup. | blocking regression gate |
| CLI read-only operations | Brain v2 eval/status/repair tests, memory status tests, task-list tests, no-load voice backend/model/cache status | high | Preserve read-only isolation and redacted output while parser changes land. | blocking regression gate |
| Server pairing authorization | per-connection random-code pairing, bounded failure lockout, protected-event rejection, paired-only broadcasts, disconnect cleanup | high | Resolved in `security/server-pairing-authorization`. Preserve the rule that only ping and pair are accepted before authorization. | blocking regression gate |
| Server HTTP surfaces | `/qr` and `/connect` hardening headers; `/qr` omits the secret; `/api/status` exposes only running state and client count | high | Resolved in `security/server-pairing-authorization`. Keep pairing secrets and device details local-only. | blocking regression gate |
| Server protocol errors | invalid JSON, unknown message types, unpaired access, pairing lockout, and unexpected request failures have stable non-sensitive responses | high | Resolved in `security/server-pairing-authorization`. Preserve generic remote errors while retaining local diagnostics. | blocking regression gate |
| Doctor quick checks | formatting, quick-check names, clean-clone private-layout behavior, live command gate | high | Full-doctor failure aggregation and exit-code behavior are not isolated from expensive subprocesses. Characterize command selection, timeout, and failure reporting. | medium |
| Brain v2 storage/retrieval | episode separation, reviewed truth, repair lifecycle, rollback, conflict redaction, guest isolation, eval 8/8, live QA | high | Maintain current gates. New architecture work must not weaken reviewed-memory authority, source links, repair history, or live-data isolation. | blocking regression gate |
| Brain v2 CLI | subprocess isolation, safe accept/no-promote, explicit promotion/repair tokens, read-only reconciliation | high | No immediate characterization gap. Preserve exact tokens, redaction, and copy-only legacy repair behavior. | blocking regression gate |
| Daemon lifecycle | one entrypoint, import-safe lazy audio initialization, one owned loop, graceful SIGINT/SIGTERM stop, timeout continuation, wake/active transitions, missing-dependency failure, speaker checks | high | Resolved in `test/daemon-lifecycle-characterization`. Keep model loading behind explicit startup and preserve fail-closed enrolled-speaker verification. | blocking regression gate |
| Voice identity math | similarity direction, degenerate cosine fallback, pitch edge cases, guest/owner session context; status checks cache/enrollment-file presence without reading content | medium | Model-load failure, enrollment persistence, and malformed profile handling still need isolated characterization. Tests must not download weights or read live biometric data. | high |
| Voice recognition backends | no-load CLI status for installed packages, exact model ids, expected cache paths, offline readiness, component fallback order, and Google audio egress | high | Resolved in `feature/voice-backend-status`. Model weights still require provenance/checksum records before bundling or release claims. | blocking regression gate |
| Frontend API behavior | source-level voice race tests, companion state machine, server voice event ordering, clean production build | medium | The frontend assumes pairing is sufficient but the server does not enforce it; protocol shapes are duplicated rather than generated/shared. First bind behavior to server authorization, then define a versioned schema boundary. | high |
| Frontend accessibility | some semantic labels and keyboard paths in source | low | No automated accessibility audit or representative manual checklist exists for the claimed client flow. Add after the protected protocol is stable. | medium |

## Execution order

1. Characterize voice identity load/enrollment failure paths without model downloads.
2. Characterize full-doctor subprocess failures and timeouts.
3. Define a versioned server/frontend message schema and add accessibility checks.

Each item lands through its own branch. Brain v2 full gates remain mandatory for shared orchestration changes, and `main` remains unchanged until the owner explicitly authorizes a release merge.
