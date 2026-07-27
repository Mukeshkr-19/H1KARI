# Time Sense Runtime

## Bridge

`TimeSenseRuntimeBridge` returns WAIT | RESPOND | CHECK_IN | SUMMARIZE | SUPPRESS
from caller-supplied observations. It never speaks, schedules, retries, cancels,
or mutates tasks. No transcript storage.

## Observation coordinator

`TimeSenseObservationCoordinator` polls injected read-only sources:

- conversation session
- scheduled jobs
- task ledger progress
- streaming voice speech activity

Static adapters under `core.time_sense.adapters` exist for tests and Mira wiring.
Observations are freshness-checked and hard-bounded. Stuck detection requires
repeated evidence and cooldown via existing stuck detectors/notifiers.

## Daemon exposure

`get_timing_advisory_snapshot()` / `get_timing_coordinator_snapshot()` expose
content-free advisories only. This slice does not auto-speak.


## Observation coordinator bounds

`TimeSenseObservationCoordinator` iterates observation sources with a hard
cap and stops without materializing unbounded results. Future and stale
observations are classified separately. Configuration values receive type,
finite, and hard-bound checks. Task/session identifiers are sanitized.
The coordinator remains advisory-only and never retains transcript or private
task content.
