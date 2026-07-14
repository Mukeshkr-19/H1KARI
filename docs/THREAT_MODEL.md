# HIKARI Threat Model

Status: WP-007 public threat model and central policy skeleton

## Scope

This model covers HIKARI-owned code, private runtime state, local model caches,
microphone and speaker identity data, provider requests, browser and macOS control,
task scheduling, memory writes, and connected clients. It does not claim that all
existing action callers already use the central policy skeleton.

## Assets

- owner identity, voice enrollment, memories, tasks, preferences, and local files
- credentials and provider configuration
- microphone audio, screen/window captures, clipboard content, and app state
- integrity and availability of the Mac, HIKARI runtime home, and private backups
- audit evidence describing who requested an action and why it was allowed

## Actors and trust boundaries

| Actor or boundary | Trust position | Required treatment |
|---|---|---|
| owner | authenticated local principal | may read private state; side effects require explicit intent and verified grants |
| guest | untrusted for owner-private data | public/session reads only; no side effects in the skeleton |
| autonomous/system trigger | no implicit human authority | deny until a bounded, revocable grant exists |
| paired web/client device | authenticated transport is not owner identity | preserve actor and scope through every request |
| hosted provider or external service | off-device processor | disclose egress, minimize data, enforce timeout/disable path |
| model/tool output | untrusted instructions | never converts text into authority or confirmation |
| repository vs `HIKARI_HOME` | public code/private state boundary | private data never falls back into the checkout |

## Primary threats

- prompt or content injection causing an unintended tool invocation
- guest or paired-client privilege escalation into owner memory or system actions
- confused-deputy behavior where a model, scheduler, or background process is treated as the owner
- confirmation replay, spoofing, or applying one grant to a different action/target
- destructive, privileged, or irreversible action without a dedicated policy and recovery path
- private content leaking through logs, tests, screenshots, provider requests, Git, or generated artifacts
- path traversal, symlink following, or unsafe migration/rollback crossing the runtime-home boundary
- unbounded subprocess, network, model-download, or automation resource use
- dependency, model, prompt, or binary provenance gaps weakening release integrity

## Security invariants

1. Unknown and autonomous actors have no implicit action authority.
2. Guest context cannot read owner-private/system data or create side effects.
3. Model output, tool output, and remembered text are data, never approval.
4. A side effect requires explicit user intent plus a verified, action-bound grant.
5. Destructive and privileged actions remain denied until an action-specific policy, recovery path, and tests exist.
6. Denial and confirmation requirements occur before importing or invoking an action implementation.
7. Specific existing safeguards—Brain repair tokens, scheduler confirmation, pairing authorization, and the osascript kill switch—remain in force during migration.
8. Private runtime state, credentials, biometrics, and backups never enter the public repository.

## Central skeleton

`core/action_policy.py` defines the initial actor, data-scope, risk, and decision
types. `evaluate_action()` is pure and deterministic. It allows bounded reads,
requires confirmation for owner-initiated reversible/network/OS side effects, and
denies guest side effects plus destructive, privileged, unknown, and autonomous
requests.

`confirmation_granted=True` represents a future verified grant, not a raw user
string or model assertion. Callers must not set it until action id, target, actor,
scope, expiry, and replay behavior are bound by the later grant contract.

## Current gaps and migration order

The repository still contains direct AppleScript, `open`, clipboard, screenshot,
power, application, browser, file, smart-home, and scheduler callers. WP-007 does
not reroute them silently.

Caller migration order:

1. inventory each caller's actor, target, data scope, side effect, timeout, and existing confirmation
2. preserve its current kill switch and regression tests
3. add an action-specific adapter that asks the central policy before importing/executing the caller
4. add denial, confirmation, guest, timeout, and rollback tests
5. remove the older local gate only after behavior parity and integration verification

Until a caller completes those steps, its existing guard is authoritative and its
central-policy coverage remains an explicit release gap.
