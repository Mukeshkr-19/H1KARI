import assert from "node:assert/strict";
import test from "node:test";

import {
  createEmptyScheduleProposalFields,
  createInitialScheduleProposalClientState,
  isValidScheduleTimezone,
  mapScheduleValidationMessage,
  reduceScheduleProposalClientState,
  validateScheduleProposalFields,
  type ScheduleProposalFields,
} from "./scheduleProposal";

const EPOCH_CLOCK = () => BigInt(0);

function fields(
  overrides: Partial<ScheduleProposalFields> = {},
): ScheduleProposalFields {
  return Object.freeze({
    ...createEmptyScheduleProposalFields(),
    nextRunAt: "1970-01-02T00:00:00.000001Z",
    ...overrides,
  });
}

test("validates both supported one-shot actions and freezes output", () => {
  for (const action of ["browser.research", "calendar.read"] as const) {
    const result = validateScheduleProposalFields(fields({ action }), EPOCH_CLOCK);
    assert.equal(result.ok, true);
    if (result.ok) {
      assert.equal(result.fields.action, action);
      assert.equal(result.fields.maxAttempts, 1);
      assert.equal(result.fields.quietHours, undefined);
      assert.equal(Object.isFrozen(result), true);
      assert.equal(Object.isFrozen(result.fields), true);
    }
  }
});

test("rejects unknown actions, unknown fields, and malformed field types", () => {
  assert.deepEqual(
    validateScheduleProposalFields(
      { ...fields(), action: "email.draft" },
      EPOCH_CLOCK,
    ),
    { ok: false, code: "action_invalid", field: "action" },
  );
  assert.equal(
    validateScheduleProposalFields({ ...fields(), extra: true }, EPOCH_CLOCK).ok,
    false,
  );
  assert.equal(
    validateScheduleProposalFields({ ...fields(), maxAttempts: 1 }, EPOCH_CLOCK)
      .ok,
    false,
  );
});

test("requires an explicit timezone and rejects impossible instants", () => {
  assert.deepEqual(
    validateScheduleProposalFields(
      fields({ nextRunAt: "1970-01-02T00:00:00" }),
      EPOCH_CLOCK,
    ),
    { ok: false, code: "next_run_missing_timezone", field: "nextRunAt" },
  );
  assert.deepEqual(
    validateScheduleProposalFields(
      fields({ nextRunAt: "1970-02-30T00:00:00Z" }),
      EPOCH_CLOCK,
    ),
    { ok: false, code: "next_run_invalid_format", field: "nextRunAt" },
  );
});

test("preserves microsecond precision at the strict future boundary", () => {
  const now = BigInt(86_400_000_000);
  assert.equal(
    validateScheduleProposalFields(
      fields({ nextRunAt: "1970-01-02T00:00:00.000001Z" }),
      () => now,
    ).ok,
    true,
  );
  assert.deepEqual(
    validateScheduleProposalFields(
      fields({ nextRunAt: "1970-01-02T00:00:00Z" }),
      () => now,
    ),
    { ok: false, code: "next_run_not_future", field: "nextRunAt" },
  );
});

test("accepts the exact 365-day horizon and rejects one microsecond beyond", () => {
  assert.equal(
    validateScheduleProposalFields(
      fields({ nextRunAt: "1971-01-01T00:00:00Z" }),
      EPOCH_CLOCK,
    ).ok,
    true,
  );
  assert.deepEqual(
    validateScheduleProposalFields(
      fields({ nextRunAt: "1971-01-01T00:00:00.000001Z" }),
      EPOCH_CLOCK,
    ),
    {
      ok: false,
      code: "next_run_horizon_too_long",
      field: "nextRunAt",
    },
  );
});

test("calls the injected clock once and fails safely for invalid clocks", () => {
  let calls = 0;
  const result = validateScheduleProposalFields(fields(), () => {
    calls += 1;
    return BigInt(0);
  });
  assert.equal(result.ok, true);
  assert.equal(calls, 1);
  assert.deepEqual(
    validateScheduleProposalFields(fields(), () => {
      throw new Error("sensitive clock detail");
    }),
    { ok: false, code: "clock_unavailable", field: "nextRunAt" },
  );
});

test("enforces integer maximum attempts from one through five", () => {
  for (const maxAttempts of ["1", "2", "3", "4", "5"]) {
    assert.equal(
      validateScheduleProposalFields(fields({ maxAttempts }), EPOCH_CLOCK).ok,
      true,
    );
  }
  for (const maxAttempts of ["0", "6", "01", "1.5", "-1", " 1"] as const) {
    assert.deepEqual(
      validateScheduleProposalFields(fields({ maxAttempts }), EPOCH_CLOCK),
      { ok: false, code: "max_attempts_invalid", field: "maxAttempts" },
    );
  }
});

test("requires disabled quiet-hours fields to remain empty", () => {
  assert.deepEqual(
    validateScheduleProposalFields(
      fields({ quietTimezone: "UTC" }),
      EPOCH_CLOCK,
    ),
    {
      ok: false,
      code: "quiet_hours_unexpected",
      field: "quietHoursEnabled",
    },
  );
});

test("validates and deeply freezes an enabled cross-midnight quiet window", () => {
  const result = validateScheduleProposalFields(
    fields({
      quietHoursEnabled: true,
      quietStartMinute: "1320",
      quietEndMinute: "420",
      quietTimezone: "America/New_York",
    }),
    EPOCH_CLOCK,
  );
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.deepEqual(result.fields.quietHours, {
      startMinute: 1320,
      endMinute: 420,
      timezone: "America/New_York",
    });
    assert.equal(Object.isFrozen(result.fields.quietHours), true);
  }
});

test("rejects missing, unbounded, and empty enabled quiet windows", () => {
  assert.equal(
    validateScheduleProposalFields(
      fields({ quietHoursEnabled: true }),
      EPOCH_CLOCK,
    ).ok,
    false,
  );
  assert.deepEqual(
    validateScheduleProposalFields(
      fields({
        quietHoursEnabled: true,
        quietStartMinute: "1440",
        quietEndMinute: "0",
        quietTimezone: "UTC",
      }),
      EPOCH_CLOCK,
    ),
    {
      ok: false,
      code: "quiet_hours_minute_invalid",
      field: "quietStartMinute",
    },
  );
  assert.deepEqual(
    validateScheduleProposalFields(
      fields({
        quietHoursEnabled: true,
        quietStartMinute: "60",
        quietEndMinute: "60",
        quietTimezone: "UTC",
      }),
      EPOCH_CLOCK,
    ),
    {
      ok: false,
      code: "quiet_hours_empty_window",
      field: "quietEndMinute",
    },
  );
});

test("validates IANA timezones and rejects controls without rewriting", () => {
  assert.equal(isValidScheduleTimezone("UTC"), true);
  assert.equal(isValidScheduleTimezone("America/New_York"), true);
  assert.equal(isValidScheduleTimezone("Not/A_Real_Zone"), false);
  assert.equal(isValidScheduleTimezone("America/\u200bNew_York"), false);
  assert.equal(isValidScheduleTimezone(" America/New_York"), false);
});

test("rejects control and Unicode format characters in structural fields", () => {
  assert.deepEqual(
    validateScheduleProposalFields(
      fields({ nextRunAt: "1970-01-02T00:00:00Z\n" }),
      EPOCH_CLOCK,
    ),
    {
      ok: false,
      code: "next_run_invalid_controls",
      field: "nextRunAt",
    },
  );
  assert.equal(
    validateScheduleProposalFields(
      fields({ nextRunAt: "1970-01-02T00:00:00\u200bZ" }),
      EPOCH_CLOCK,
    ).ok,
    false,
  );
});

test("maps only fixed validation messages", () => {
  assert.equal(
    mapScheduleValidationMessage("max_attempts_invalid"),
    "Maximum attempts must be an integer from 1 to 5.",
  );
  assert.equal(
    mapScheduleValidationMessage("provider exception: secret"),
    "The scheduled-job proposal could not be validated.",
  );
});

test("reducer blocks duplicate submit and clears state deterministically", () => {
  const initial = createInitialScheduleProposalClientState();
  const started = reduceScheduleProposalClientState(initial, {
    type: "submit_started",
  });
  assert.equal(started.pending, true);
  assert.equal(
    reduceScheduleProposalClientState(started, { type: "submit_started" }),
    started,
  );
  const cleared = reduceScheduleProposalClientState(started, {
    type: "clear_form",
  });
  assert.deepEqual(cleared, createInitialScheduleProposalClientState());
});
