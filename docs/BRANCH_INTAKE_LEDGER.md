# Develop Branch Intake Ledger

Status: active  
Baseline: `develop` at `2616e24`  
Baseline source: `main` at `6b481b7` plus the canonical plan merge  
Updated: 2026-07-13

## Purpose

This ledger controls the review of pre-existing branches before any change enters `develop`. Historical branches are not merged wholesale. Each change is reviewed, tested, and either re-applied through a fresh issue branch or explicitly deferred.

## Baseline verification

| Gate | Result |
|---|---|
| Python tests | 854 passed; 6 warnings |
| Doctor | OK with one warning: frontend dependencies are not installed in the develop worktree |
| Brain v2 evaluation | 8/8 passed |
| Brain live QA | all scenarios passed |
| Privacy scan | passed |
| Git state | `develop` clean and tracking `origin/develop` |
| Public `main` | unchanged at `6b481b7` |

The warnings are known dependency/deprecation notices plus an unwritable pytest cache in the restricted worktree. They did not change test results.

## Status legend

- `ready`: small, understood, test-backed change suitable for a fresh intake branch
- `revise`: useful fix, but the existing patch needs adjustment or additional tests
- `characterize`: behavior must be captured before the change is accepted
- `defer`: depends on a planned architecture boundary that does not exist yet
- `superseded`: later code already contains the intended behavior
- `reject`: change should not enter `develop`
- `integrated`: accepted through a fresh intake branch and verified on `develop`

## Intake decisions

| Historical branch | Scope | Initial decision | Reason and required evidence |
|---|---|---|---|
| `fix/calculator-eval-security` | replace arithmetic `eval` with bounded AST evaluation | ready | Small standard-library fix with malicious-input and complexity checks. Re-run calculator and full tests after intake. |
| `fix/research-url-encoding` | encode search query before URL construction | ready | Small boundary fix with a focused regression test. Confirm all research URL construction callers. |
| `fix/server-pairing-safety` | escape pairing values and add response headers | revise | Correct XSS direction and focused tests. Add no-store/referrer/frame protections or record why they are not applicable before intake. |
| `fix/auth-and-safety-hardening` | reject path-like memory names and align auth trust thresholds | revise | Path-like name rejection is sound. Threshold change is a separate behavior decision and should not be bundled without characterization. |
| `fix/mac-control-safety-gates` | require confirmation for destructive Mac actions | revise | Important containment, but confirmation strings are not a substitute for the planned central policy. Review every caller, eliminate broad exception handling, and add a compatibility test. |
| `fix/security-policy-path-whitelist` | enforce allowed paths in `SecurityPolicy` | defer | `SecurityPolicy` currently has no production callers. Merging would create a false safety claim. Implement as part of the real central action path. |
| `fix/orchestrator-singleton-lock` | protect singleton initialization | ready | Small concurrency change with a focused multithreaded regression test. Verify no alternate singleton path exists. |
| `fix/system-agent-music-flow` | remove unreachable clipboard restore block | ready | Deletion-only runtime cleanup with a focused behavior test. Review interaction with exception-observability changes. |
| `fix/exception-observability` | replace broad exception handlers across runtime paths | revise | Direction is useful, but it spans nine production files. Review each exception boundary and avoid converting recoverable optional-feature failures into crashes. |
| `fix/runtime-guard-cleanups` | guard optional collaborators and runtime stubs | revise | Test-backed but touches unrelated optional systems. Split into coherent issue-family intakes. |
| `fix/reminders-and-speech-runtime` | scheduler and speech runtime corrections | characterize | Side-effect and persistence behavior needs focused scheduling/voice characterization before intake. |
| `fix/runtime-small-bugs` | browser, build executor, and menubar fixes | revise | Three unrelated subsystems should be separated into independent intake branches. |
| `fix/neural-graph-algorithms` | graph algorithm corrections | characterize | Substantial algorithm change. Run focused graph tests, inspect complexity and empty/cyclic cases, then full memory gates. |
| `fix/voice-verification-math` | speaker verification scoring math | characterize | Identity-sensitive calculation. Requires threshold/edge-case review and focused voice regression tests before acceptance. |
| `fix/brain-v2-fixture-leakage` | remove hardcoded clean-room fixtures | revise | Privacy intent is valid, but later Brain and profile work may overlap. Re-diff against current `develop` and keep only non-superseded changes. |
| `fix/hikari-daemon-runtime` | large daemon loop rewrite | characterize | Large deletion/rewrite with no dedicated test file in the branch. Capture startup, shutdown, speaker-lock, timeout, and error behavior first. |
| `fix/router-provider-config-sync` | add one provider and synchronize fallback configuration | defer | Provider-specific configuration should follow the provider-neutral capability/router contract and a secrets/data-boundary review. |
| `fix/frontend-pwa-icons` | point manifest entries to an existing image | ready | Minimal asset-reference fix. Verify manifest validity, frontend lint/build, and the installed-app icon behavior. |

## Intake order

### Wave 1 - Small security and input-boundary fixes

1. Calculator evaluation
2. Research URL encoding
3. Pairing-page output hardening
4. Auth and memory-name validation, split by concern

### Wave 2 - Concurrency and contained runtime fixes

1. Orchestrator singleton lock
2. System music-flow deletion
3. Frontend PWA icon reference
4. Split runtime guard and small-bug changes
5. Exception observability, reviewed file by file

### Wave 3 - Side effects, voice, memory, and daemon behavior

1. Mac destructive-action containment
2. Scheduler/reminder and speech behavior
3. Voice verification math
4. Neural graph algorithms
5. Brain fixture cleanup
6. Daemon characterization and minimal root-cause fix

### Architecture-bound work

- Real path scopes land with the central action-policy path, not as an unused policy class.
- Provider additions land through provider-neutral contracts.
- Large daemon or orchestrator restructuring waits for characterization coverage.

## Per-branch intake procedure

1. Create a new branch from current `develop`.
2. Re-implement or cherry-pick only the reviewed coherent change.
3. Inspect all callers and sibling paths.
4. Run the smallest focused check that proves the root-cause fix.
5. Run `git diff --check` and the privacy/attribution scan.
6. Run the full test suite for security, identity, memory, orchestrator, or shared-runtime changes.
7. Commit and push the focused branch.
8. Merge into `develop`, rerun relevant integration gates, and push `develop`.
9. Mark the row `integrated` with the accepted commit.
10. Do not modify or delete the historical branch unless separately requested.

## Completion condition

Phase A branch intake completes when every listed branch is `integrated`, `superseded`, `defer`, or `reject` with evidence; `develop` passes the full integration gates; and `main` remains unchanged.
