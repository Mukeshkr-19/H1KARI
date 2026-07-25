# Time Sense

Time Sense provides deterministic, caller-driven primitives for conversational
time interpretation, stuck-task assessment, and bounded background-work
awareness. It does not poll task stores, read the wall clock, schedule jobs,
send notifications, or claim work is happening without supplied evidence.

## Contracts

- `interpret_time_phrase` resolves supported phrases against an explicit,
  timezone-aware reference time and flags ambiguity instead of guessing.
- `assess_stuck` evaluates caller-supplied progress evidence. Waiting for owner
  approval or user input is kept distinct from a technical failure.
- `build_awareness` creates capped snapshots of active, completed, blocked,
  retrying, stuck, delayed, and quiet-hours-suppressed work.
- `recommend_notification` returns advice only. It never performs delivery.

All public values are immutable and bounded. Evidence and provenance are
content-free codes. Derived payload hints are omitted unless the caller opts in
explicitly. Summaries that say work is active or completed are discarded unless
the supplied observations support that claim.

## Integration boundary

The task ledger, job scheduler, delivery lifecycle, and UI remain authoritative
for their own state. An integration adapter may translate their already-loaded
records into Time Sense contracts and pass an injected timestamp. Time Sense
must not open their databases or mutate their records directly.

This package is not yet wired into the runtime conversation path. That wiring
should be a separate reviewed change after these contracts remain stable.
