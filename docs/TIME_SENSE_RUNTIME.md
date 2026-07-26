# Time Sense Runtime Foundations

Pure, injected-clock Time Sense foundations for conversation timing, job
observations, stuck-notification backoff, and Mira adapter protocols.

Package: `core/time_sense/`

This document covers the **runtime policy** surface added alongside existing
phrase interpretation, stuck detection, and background awareness. Time Sense
never performs corrective actions, delivery, scheduling, or store I/O.

## Conversation timing observations

`ConversationTimingObservation` captures:

- pause age
- last user speech / last assistant response ages
- active vs sleeping
- quiet hours
- recent dismissal
- child / privacy suppression
- active speech flags

## Conversational timing policy

`evaluate_conversation_timing` returns one of:

- `WAIT`
- `RESPOND`
- `CHECK_IN`
- `SUMMARIZE`
- `SUPPRESS`

Decisions use supplied evidence only. Quiet hours, child mode, privacy
suppression, sleeping state, active speech, and recent dismissal all force
`SUPPRESS` with content-free reason codes.

## Task / background-job observations

`JobTimingObservation` carries job ID only plus:

- state, age, heartbeat age
- bounded completion estimate range
- retry / attempt count
- cancellation / resolution evidence codes

No raw private payloads.

## Stuck-task notification tracker

`StuckNotificationTracker` applies:

- minimum age
- missed-heartbeat threshold
- consecutive failure threshold
- exponential notification backoff
- deduplication
- resolved / cancelled terminal states

The tracker **never** retries, cancels, or repairs work.

## Adapter protocols (Mira-owned)

Defined in `core/time_sense/adapters.py`:

- `ScheduledJobObservationSource` — scheduled jobs → `JobTimingObservation`
- `ConversationSessionObservationSource` — sessions → timing + quiet hours
- `StreamingVoiceObservationSource` — streaming voice → speech activity ages
- `TaskProgressObservationSource` — existing stuck-detection observations

Adapters describe supply contracts only; no DB or runtime I/O in this workstream.

## Mira integration points (not wired here)

- Conversation session runtime projects pause/speech ages into
  `ConversationTimingObservation`.
- Job scheduler emits `JobTimingObservation` heartbeats without payloads.
- Streaming voice turn machine feeds `StreamingVoiceObservationSource`.
- Notification delivery remains Mira-owned; Time Sense only advises.
