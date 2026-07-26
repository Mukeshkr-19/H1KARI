# Time Sense Runtime

## Runtime bridge

`core.time_sense.runtime_bridge.TimeSenseRuntimeBridge` consumes caller-supplied
`ConversationTimingObservation` values and returns advisory
`WAIT | RESPOND | CHECK_IN | SUMMARIZE | SUPPRESS` decisions.

It never schedules, retries, cancels, delivers, or speaks. It never stores
transcript text. Tracked sessions and observation age are hard-bounded.

## Suppression

Proactive output is suppressed during sleep, quiet hours, child mode, privacy
suppression, active user speech, active assistant speech, and recent dismissal.

## Daemon exposure

`services.hikari_daemon.get_timing_advisory_snapshot()` exposes advisories only.
This slice must not begin unsolicited speaking from Time Sense advice.

## Mira-owned next steps

- Wire conversation session + job adapters to supply observations
- Optional UI surfacing of advisories without auto-speak
- Notification delivery remains Mira-owned (stuck notify tracker is advisory)
