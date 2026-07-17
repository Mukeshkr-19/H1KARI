# Phase 1 Completion Record

Status: complete for source integration after the gates below passed

Verified: 2026-07-17

Release baseline: `main` and `origin/main` at `f4d5009` before this closure

## Scope boundary

This record closes the Phase 1 safe companion kernel. It does not begin Phase 2,
enable quarantined side-effecting tools, select a project license, authorize a
binary or commercial distribution, or approve access to private owner data for
release testing.

## Phase 1 work packages

| Package | Completion evidence |
|---|---|
| WP-101 typed domain contracts | `core/action_policy.py` defines bounded actors, scopes, risks, outcomes, and immutable policy decisions with default-deny tests. |
| WP-102 persistent task ledger | `core/tasks/` provides scoped SQLite persistence, guarded transitions, interruption, retry, cancellation, verification, and deterministic connection cleanup. |
| WP-103 central policy and audit path | `core/policy_service.py`, `core/action_audit.py`, and focused policy tests enforce authorization before protected work and retain content-free audit references. |
| WP-104 actor-aware approval grants | `core/grants.py` binds one-use grants to actor, session, action, resource, destination, task, and expiry under atomic consumption. |
| WP-105 Brain v2 service boundary | `core/brain_service.py` and `core/brain_v2/` preserve owner/guest isolation, reviewed-memory authority, provenance, and synthetic evaluation coverage. |
| WP-106 provider-neutral routing | `core/router.py` accepts a bounded provider allowlist and authorizes every attempted document destination immediately before egress. |
| WP-107 selected-document flow | The CLI and WebSocket paths prepare, explicitly confirm, read one approved UTF-8 text file of at most 100 KB, explain it, reconnect, cancel, and ask bounded follow-ups without rereading the file. |
| WP-108 accessible client flow | The frontend exposes labeled controls, live status and error regions, keyboard focus after pairing, bounded generic server-error handling, and the documented manual accessibility checklist. |

## Audit closure

- SQLite-backed grant, audit, task, and Brain v2 stores close every connection
  deterministically while retaining transaction commit and rollback behavior.
- The WebSocket server uses the supported `websockets.asyncio` API, preserves its
  public HTTP helpers, and closes the server cleanly.
- The standalone privacy scan is executable and release-branch pushes run CI.
- Setup guidance uses the runtime-loaded local environment filename, discovers
  Python 3.12 without a machine-specific framework path, documents PortAudio,
  and matches the tracked repository layout.
- Adapted-source provenance and the upstream license restriction are recorded and
  regression-tested. Commercial distribution remains blocked without permission
  or a clean-room replacement of the affected files.
- The web client surfaces generic protocol errors and moves focus to the dashboard
  heading after the pairing view is replaced.
- Test-session runtime directories are removed at process exit, and project-owned
  pytest parameter data is compatible with the upcoming pytest 10 behavior.

## Verification record

The integrated source tree passed:

- focused integration tests: 130 passed, 5 subtests passed
- complete Python suite: 1,214 passed, 5 subtests passed
- HIKARI doctor with no failing checks; warnings were limited to the dirty closure
  branch, the existing non-symlink Brain path, and an episode database not yet
  created before first use
- Brain v2 synthetic evaluation: 8/8 passed
- read-only voice status without model loading or enrollment-content access
- direct public-source privacy scan
- shared protocol and adapted-source attribution tests
- Python dependency compatibility with `pip check`
- exact frontend third-party input check
- frontend lint, type checking, and production build
- frontend advisory audit: 0 vulnerabilities
- production-browser pairing focus and generic server-error behavior
- `git diff --check`, public-source metadata review, and attribution scans

Private live-memory QA was intentionally not run. The synthetic evaluation is the
release evidence for this source closure and does not inspect owner conversations,
memories, voice samples, or enrollment content.

## Known boundaries and recovery

- A completed document explanation and status reconnect from the durable task
  ledger. The WebSocket provider snapshot is process-local; after a server restart,
  prepare a new document request before asking another follow-up.
- Voice models remain opt-in runtime downloads and were not loaded by these gates.
- Dependency refresh suggestions without a demonstrated vulnerability or supported-
  platform failure remain maintenance work, not Phase 1 release blockers.
- The project license remains an explicit owner decision. Third-party terms still
  apply, including the recorded restriction on adapted source.
- Prebuilt frontend or native artifacts remain blocked until their exact binary
  notice and relinking obligations are reviewed.

Rollback is a source rollback to the pre-closure `f4d5009` baseline. Runtime data
and private repositories are outside this source change and must not be deleted or
rewritten as part of rollback.

## Exit criteria

- Fresh setup no longer depends on a private machine-specific Python path.
- One explicitly selected document can be explained through one-use read and
  provider grants with content-free audit records.
- Durable task state supports provider fallback, cancellation, and client reconnect.
- Legacy side-effecting tools remain quarantined from the Phase 1 runtime.
- Privacy, protocol, dependency, provenance, backend, and frontend gates are green.
- Phase 2 has not started.
