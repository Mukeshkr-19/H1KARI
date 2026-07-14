# WP-003 Characterization Matrix

Status: complete
Baseline: `develop` at `c47208a`
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
| Doctor checks | quick-check names, clean-clone private layout, explicit full command plan/timeouts, success, missing executable, timeout, nonzero exit tail, failure aggregation, exit code | high | Resolved in `test/doctor-full-failure-contract`. Preserve deterministic command selection and aggregate all results before returning failure. | blocking regression gate |
| Brain v2 storage/retrieval | episode separation, reviewed truth, repair lifecycle, rollback, conflict redaction, guest isolation, eval 8/8, live QA | high | Maintain current gates. New architecture work must not weaken reviewed-memory authority, source links, repair history, or live-data isolation. | blocking regression gate |
| Brain v2 CLI | subprocess isolation, safe accept/no-promote, explicit promotion/repair tokens, read-only reconciliation | high | No immediate characterization gap. Preserve exact tokens, redaction, and copy-only legacy repair behavior. | blocking regression gate |
| Daemon lifecycle | one entrypoint, import-safe lazy audio initialization, one owned loop, graceful SIGINT/SIGTERM stop, timeout continuation, wake/active transitions, missing-dependency failure, speaker checks | high | Resolved in `test/daemon-lifecycle-characterization`. Keep model loading behind explicit startup and preserve fail-closed enrolled-speaker verification. | blocking regression gate |
| Voice identity | similarity direction, degenerate cosine fallback, pitch edge cases, guest/owner session context, finite/dimension validation, private enrollment round trip, malformed-profile rejection, owner-only file mode, exact model/cache wiring, offline load failure | high | Resolved in `safety/voice-identity-failure-paths`. Tests use synthetic vectors and stubbed loaders only; preserve the no-weight-download and no-live-biometric rule. | blocking regression gate |
| Voice recognition backends | no-load CLI status for installed packages, exact model ids, expected cache paths, offline readiness, component fallback order, and Google audio egress | high | Resolved in `feature/voice-backend-status`. Model weights still require provenance/checksum records before bundling or release claims. | blocking regression gate |
| Frontend API behavior | shared v1 JSON contract imported by Python and Next.js, bounded client validation, pairing version negotiation, backward-compatible omitted version, protected server authorization, source-level voice race tests, companion ordering, clean production build | high | Resolved in `feature/websocket-protocol-v1`. Incompatible field or message changes require a new protocol version. | blocking regression gate |
| Frontend accessibility | associated pairing/chat labels, named icon controls, conversation and status live regions, current-page navigation, native button choice semantics, global focus/reduced-motion styles, automated contract tests, manual keyboard/VoiceOver/zoom/failure checklist | high | Resolved in `accessibility/frontend-client-contract`. Complete and record the manual checklist on each release candidate without private runtime data. | release regression gate |

## Execution order

All identified WP-003 characterization gaps have executable or documented release
evidence. Continue with the remaining Phase 0 work packages; preserve every blocking
regression gate in this matrix.

Each item lands through its own branch. Brain v2 full gates remain mandatory for shared orchestration changes, and `main` remains unchanged until the owner explicitly authorizes a release merge.
