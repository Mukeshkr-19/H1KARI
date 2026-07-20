# Phase 3 Completion Record

Status: complete for source integration after the gates below passed

Verified: 2026-07-20

## Scope boundary

Phase 3 adds transparent productivity proposals, bounded approvals, action
adapters, and one-shot scheduled reads. It does not begin Phase 4 vision or
mobile capture, enable arbitrary third-party tools, create a portable identity,
or claim that platform providers and browser services are available offline.

## Completed capabilities

| Capability | Integrated behavior |
|---|---|
| Preview and approval | Research, email draft, calendar read/draft, and reminder requests produce immutable destination-and-payload previews. The server creates proposal and approval identifiers, derives actor/session context from the transport, and consumes approvals atomically before execution. |
| Approval scopes | Once, session, fixed duration, and precise persistent scopes remain bound to the exact actor, action, targets, and snapshot. Persistent grants can be rebound only to the same stable local installation after an exact snapshot match. |
| External actions | Browser research and calendar reads return bounded result messages. Email creates a visible unsent Mail draft. Calendar and reminder writes target the exact confirmed destination. Failures return fixed local codes without provider output or exception text. |
| Scheduled reads | A frozen research or calendar-read preview can become a one-shot job. Jobs are visible, pausable, resumable, cancellable, quiet-hours aware, bounded to five attempts, audited, and recovered safely after interruption. Write actions cannot be scheduled. |
| Ownership and privacy | Paired loopback owner sessions map to a private stable installation scope for scheduled jobs; guests remain isolated. Job and approval databases use private local runtime paths and restrictive permissions. Content is retained only in the purpose-specific private stores required for pending execution. |
| Meaningful-change delivery | Scheduled read results use bounded job-correlated messages and durable fingerprints. Fingerprints are recorded only after positive transport acceptance, and compare-and-swap transitions prevent concurrent duplicate execution. |
| Third-party policy boundary | MCP and skill invocation is deny-all by default. Enabling it requires an exact permission manifest and an exact callable binding; every request is evaluated immediately before invocation. No third-party tool is enabled in the default product. |
| Accessibility | Productivity previews, scope selection, forms, results, job controls, status announcements, and errors use labelled native controls, keyboard operation, bounded live regions, and focused validation targets. |

## Security and failure behavior

- Client messages cannot supply actor IDs, session IDs, approval IDs, job IDs
  for creation, provider details, or executable payloads.
- Prepared inputs are actor/session scoped, bounded, frozen, and cleared after
  failure, cancellation, terminal completion, expiry, or disconnect as required.
- Scheduled execution accepts only exact retained read-input types. An envelope
  is persisted before the job is activated, and failed creation is compensated
  with exact revision checks.
- A runner claims work with compare-and-swap transitions. Unexpected adapter
  returns, exceptions, audit failures, stale revisions, and replay attempts fail
  closed.
- Startup recovery moves crash-left running work through audited interrupted and
  scheduled states. If recovery cannot be audited, the job is not left active.
- No proposal content, targets, provider output, actor/session identifiers,
  approval identifiers, or exception details are written to public logs.

## Verification record

The integrated source tree passed the complete Python test suite, the complete
Phase 3 suite, Python bytecode compilation, dependency compatibility, doctor,
voice-status, privacy and public-artifact scans, protocol validation, provenance
and third-party checks, frontend unit tests, lint, production build, dependency
audit, and Git whitespace/hygiene checks.

All adapter tests use controlled fakes or bounded test doubles. The release gate
does not perform live email, calendar, reminder, browser, MCP, skill, microphone,
or provider actions. Availability and permissions for platform applications and
external services remain machine-specific acceptance checks.

## Exit criteria

- Every enabled external action shows a frozen destination-and-payload preview
  before approval.
- Approval consumption precedes execution and cannot be replayed.
- Scheduled jobs are durable across reconnects on one local installation,
  visible, pausable, resumable, cancellable, quiet-hours aware, and audited.
- Scheduled execution is limited to read actions with bounded result delivery.
- Third-party tool invocation is disabled unless an exact declared permission
  and binding are installed; undeclared access is denied.
- Errors, public artifacts, documentation, and protocol messages contain no
  private runtime content or implementation provenance residue.

## Known boundaries and rollback

- Scheduled jobs are one-shot. Recurring schedules require a future reviewed
  contract rather than an implicit repeat.
- Stable scheduled ownership is local-installation scoped, not a cross-device
  account identity.
- Result delivery is retry-safe and job-correlated but does not claim universal
  exactly-once presentation after every possible client crash.
- MCP and skill execution remains disabled in the default product.
- Source rollback is a revert of the Phase 3 integration commit. Private runtime
  databases are outside the repository and must not be deleted automatically.
