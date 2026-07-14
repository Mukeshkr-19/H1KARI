# Phase 0 Completion Record

Status: complete on `develop` after closure integration

Verified: 2026-07-14

Stable baseline held: `main` and `origin/main` at `6b481b7`

## Scope boundary

This record closes Phase A and Phase 0 only. It does not begin the Phase 1
companion kernel, migrate existing action callers to a new authorization system,
select a project license, authorize a binary release, or merge `develop` into
`main`.

## Phase A

`docs/BRANCH_INTAKE_LEDGER.md` records the baseline, every historical branch
disposition, focused intake evidence, and the frozen `main` commit. All candidates
have a terminal disposition and accepted behavior entered through reviewed,
test-backed changes rather than wholesale historical merges.

## Phase 0 work packages

| Package | Completion evidence |
|---|---|
| WP-001 inventory and remediation | `docs/PROVENANCE_INVENTORY.md`, `docs/MODEL_PROVENANCE.md`, `docs/PROVIDER_PROVENANCE.md`, `THIRD_PARTY_NOTICES.md`; exact supported locks; reviewed model revisions; conflicting provider config and unshippable wake-word prototype removed |
| WP-002 governance and public security | `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and `GOVERNANCE.md`; project license remains an explicit owner decision |
| WP-003 characterization | `docs/CHARACTERIZATION_MATRIX.md` records complete CLI, server, doctor, Brain v2, daemon, voice identity, frontend API, and accessibility coverage |
| WP-004 continuous checks | `.github/workflows/ci.yml`, full Python tests, privacy/secret scan, exact Python and npm locks, frontend audit/lint/build, generated third-party input, and protocol schema checks |
| Protocol compatibility | `docs/PROTOCOL_V1.md` and `protocol/hikari-v1.json`, enforced by Python and frontend contract tests |
| WP-005 naming and compatibility | `docs/RUNTIME_PATH_COMPATIBILITY.md`; canonical public checkout identity and legacy launcher compatibility are regression-tested |
| WP-006 private runtime home | `docs/RUNTIME_HOME.md`; read-only plan, explicit initialization, backup, migration dry run, safe rollback, permissions, symlink refusal, and no-download disclosure are tested |
| WP-007 threat and action policy | `docs/THREAT_MODEL.md` and `core/action_policy.py`; deterministic policy skeleton and explicit caller-migration boundary are tested without claiming Phase 1 enforcement |

## Remediation closure

- Every supported Python import is declared; the supported macOS arm64/Python
  3.12 runtime and development graphs are exact.
- Voice models have reviewed identities. Runtime downloads use the reviewed
  revisions where the loader supports revision selection, and no weights are
  tracked or bundled.
- Hosted-provider and external-service egress, retention evidence, credentials,
  failure behavior, and disable paths are recorded. OpenWeather transport is
  HTTPS and credential-bearing failures are redacted.
- Frontend license families and native binary obligations are recorded. Source
  publication is allowed by this gate; a prebuilt artifact remains blocked until
  its exact notices and LGPL obligations are reviewed.
- All shipped assets have recorded hashes and origins; unused or unknown-origin
  template assets were removed.
- No project license was added. Repository ownership and third-party licenses do
  not silently grant a H1KARI redistribution license.
- Private runtime data, credentials, biometrics, and live owner content remain
  outside the public checkout. Tests and QA use synthetic identities and isolated
  databases.

## Verification record

The closure candidate source tree passed:

- `git diff --check`
- 80 focused closure tests
- full Python suite: 1,046 passed, 5 subtests passed, 4 known deprecation warnings
- public privacy and secret scan
- HIKARI doctor with no failing checks
- Brain v2 synthetic evaluation: 8/8 passed
- isolated full-orchestrator Brain live QA: all scenarios passed
- fresh source-only text initialization, followed by safe rollback of all seven
  created paths; no sibling private repository was required
- read-only voice status without model loading or enrollment-content access
- Python dependency compatibility: `pip check` clean
- exact frontend third-party input check
- frontend lint: no warnings or errors
- frontend production build: successful
- frontend advisory audit: 0 vulnerabilities
- reviewed resolution of `rumps==0.4.0` to exact PyObjC Cocoa dependencies used
  in both supported platform locks

The same gates are rerun on `develop` after the closure merge. Phase 0 is not
complete if that integrated tree differs, a required gate fails, `main` moves, the
worktree limit is exceeded, or private/helper state appears in tracked content.

## Exit criteria

- Fresh initialization works without a required sibling private repository.
- Current supported behavior is green across unit, integration, Brain, doctor,
  privacy, protocol, dependency, and frontend gates.
- Every shipped dependency, runtime-downloaded model, external service, and asset
  has a known provenance source and a fail-closed distribution disposition.
- The final H1KARI license remains unselected pending explicit owner approval.
- `main` remains unchanged and Phase 1 has not started.
