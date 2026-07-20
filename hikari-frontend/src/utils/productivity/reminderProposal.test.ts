import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  REMINDER_LIST_NAME_MAX,
  REMINDER_MAX_HORIZON_SECONDS,
  REMINDER_NOTES_MAX,
  REMINDER_TITLE_MAX,
  createEmptyReminderFields,
  createInitialReminderProposalClientState,
  hasReminderUnicodeFormatChars,
  isBlankReminderTitle,
  mapReminderValidationMessage,
  reduceReminderProposalClientState,
  reminderCodePointLength,
  reminderNowMicros,
  validateReminderFields,
} from "./reminderProposal";
import { parseCalendarInstantMicros } from "./calendarProposal";

const NOW = parseCalendarInstantMicros("2026-07-20T12:00:00Z")!;
const NOW_EPOCH_MS = Number(NOW / BigInt(1_000));
const FUTURE = "2026-07-21T09:00:00-04:00";
const HORIZON_EDGE = "2027-07-21T12:00:00Z";
const HORIZON_TOO_LATE = "2027-07-21T12:00:01Z";

function sample(overrides: Record<string, unknown> = {}) {
  return {
    title: "Pick up package",
    remindAt: FUTURE,
    notes: "",
    listName: "",
    ...overrides,
  };
}

function withMockedDateNow(epochMs: number, run: () => void): void {
  const original = Date.now;
  Date.now = () => epochMs;
  try {
    run();
  } finally {
    Date.now = original;
  }
}

describe("reminderProposal", () => {
  it("converts epoch milliseconds to microseconds exactly", () => {
    const epochMs = 1_700_000_000_123;
    withMockedDateNow(epochMs, () => {
      assert.equal(reminderNowMicros(), BigInt(epochMs) * 1_000n);
      assert.equal(reminderNowMicros(), 1_700_000_000_123_000n);
    });
  });

  it("accepts reminders one second after the production clock and rejects equal or past times", () => {
    withMockedDateNow(NOW_EPOCH_MS, () => {
      const oneSecondLater = validateReminderFields(
        sample({ remindAt: "2026-07-20T12:00:01Z" }),
      );
      assert.equal(oneSecondLater.ok, true);

      const equal = validateReminderFields(
        sample({ remindAt: "2026-07-20T12:00:00Z" }),
      );
      assert.equal(equal.ok, false);
      if (!equal.ok) {
        assert.equal(equal.code, "remind_at_before_now");
      }

      const past = validateReminderFields(
        sample({ remindAt: "2026-07-19T12:00:00Z" }),
      );
      assert.equal(past.ok, false);
      if (!past.ok) {
        assert.equal(past.code, "remind_at_before_now");
      }
    });
  });

  it("validates and freezes bounded reminder fields", () => {
    const result = validateReminderFields(
      sample({
        notes: "Bring ID\nand receipt",
        listName: "Errands",
      }),
      { nowMicros: NOW },
    );
    assert.equal(result.ok, true);
    if (!result.ok) {
      return;
    }
    assert.equal(result.fields.title, "Pick up package");
    assert.equal(result.fields.remindAt, FUTURE);
    assert.equal(result.fields.notes, "Bring ID\nand receipt");
    assert.equal(result.fields.listName, "Errands");
    assert.throws(() => {
      (result.fields as { title: string }).title = "nope";
    }, TypeError);
  });

  it("omits empty optional notes and list name without rewriting text", () => {
    const result = validateReminderFields(sample(), { nowMicros: NOW });
    assert.equal(result.ok, true);
    if (!result.ok) {
      return;
    }
    assert.equal("notes" in result.fields, false);
    assert.equal("listName" in result.fields, false);
    const preserved = validateReminderFields(
      sample({ title: "  Keep spacing  " }),
      { nowMicros: NOW },
    );
    assert.equal(preserved.ok, true);
    if (preserved.ok) {
      assert.equal(preserved.fields.title, "  Keep spacing  ");
    }
  });

  it("rejects whitespace-only titles under Python strip parity", () => {
    assert.equal(
      validateReminderFields(sample({ title: "   " }), { nowMicros: NOW }).ok,
      false,
    );
    const nextLine = "\u0085";
    assert.equal(isBlankReminderTitle(nextLine), true);
    const nel = validateReminderFields(sample({ title: nextLine }), {
      nowMicros: NOW,
    });
    assert.equal(nel.ok, false);
    if (!nel.ok) {
      assert.equal(nel.code, "title_required");
      assert.equal(nel.field, "title");
    }
    const controls = validateReminderFields(sample({ title: " \t\n " }), {
      nowMicros: NOW,
    });
    assert.equal(controls.ok, false);
    if (!controls.ok) {
      assert.equal(controls.code, "title_invalid_controls");
    }
  });

  it("rejects oversized and control-bearing title without truncation", () => {
    const tooLong = validateReminderFields(
      sample({ title: "T".repeat(REMINDER_TITLE_MAX + 1) }),
      { nowMicros: NOW },
    );
    assert.equal(tooLong.ok, false);
    if (!tooLong.ok) {
      assert.equal(tooLong.code, "title_too_long");
    }
    const controls = validateReminderFields(
      sample({ title: "bad\u0000title" }),
      { nowMicros: NOW },
    );
    assert.equal(controls.ok, false);
    if (!controls.ok) {
      assert.equal(controls.code, "title_invalid_controls");
    }
    const cf = validateReminderFields(
      sample({ title: "bad\u200btitle" }),
      { nowMicros: NOW },
    );
    assert.equal(cf.ok, false);
    if (!cf.ok) {
      assert.equal(cf.code, "title_invalid_controls");
    }
  });

  it("uses Unicode code-point length for text bounds including emoji", () => {
    const emojiTitle = "📌".repeat(REMINDER_TITLE_MAX);
    assert.equal(reminderCodePointLength(emojiTitle), REMINDER_TITLE_MAX);
    assert.equal(
      validateReminderFields(sample({ title: emojiTitle }), { nowMicros: NOW })
        .ok,
      true,
    );
    assert.equal(
      validateReminderFields(sample({ title: `${emojiTitle}📌` }), {
        nowMicros: NOW,
      }).ok,
      false,
    );
  });

  it("rejects naive datetimes missing explicit timezone offsets", () => {
    const result = validateReminderFields(
      sample({ remindAt: "2026-07-21T09:00" }),
      { nowMicros: NOW },
    );
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.equal(result.code, "remind_at_missing_timezone");
      assert.equal(result.field, "remindAt");
    }
  });

  it("accepts Zulu and offset datetimes without inventing timezones", () => {
    const zulu = validateReminderFields(
      sample({ remindAt: "2026-07-21T13:00:00Z" }),
      { nowMicros: NOW },
    );
    assert.equal(zulu.ok, true);
    if (zulu.ok) {
      assert.equal(zulu.fields.remindAt, "2026-07-21T13:00:00Z");
    }
  });

  it("rejects impossible dates and year 0000 without Date.parse normalization", () => {
    const cases = [
      "2026-02-30T09:00:00Z",
      "2026-04-31T09:00:00Z",
      "2026-07-20T24:00:00Z",
      "2025-02-29T09:00:00Z",
      "0000-01-01T00:00:00Z",
    ] as const;
    for (const impossible of cases) {
      assert.equal(parseCalendarInstantMicros(impossible), null, impossible);
      const result = validateReminderFields(sample({ remindAt: impossible }), {
        nowMicros: NOW,
      });
      assert.equal(result.ok, false, impossible);
      if (!result.ok) {
        assert.equal(result.field, "remindAt");
      }
    }
    assert.notEqual(parseCalendarInstantMicros("0001-01-01T00:00:00Z"), null);
  });

  it("orders by microsecond precision and accepts 1–6 fractional digits", () => {
    const earlier = parseCalendarInstantMicros("2026-07-21T09:00:00.000001Z");
    const later = parseCalendarInstantMicros("2026-07-21T09:00:00.000002Z");
    assert.ok(earlier !== null && later !== null);
    assert.equal(later! - earlier!, BigInt(1));

    const msOnly = validateReminderFields(
      sample({ remindAt: "2026-07-21T09:00:00.001Z" }),
      { nowMicros: NOW },
    );
    const micros = validateReminderFields(
      sample({ remindAt: "2026-07-21T09:00:00.001000Z" }),
      { nowMicros: NOW },
    );
    assert.equal(msOnly.ok, true);
    assert.equal(micros.ok, true);
  });

  it("requires remind-at strictly after injected now", () => {
    const equal = validateReminderFields(
      sample({ remindAt: "2026-07-20T12:00:00Z" }),
      { nowMicros: NOW },
    );
    assert.equal(equal.ok, false);
    if (!equal.ok) {
      assert.equal(equal.code, "remind_at_before_now");
    }
    const past = validateReminderFields(
      sample({ remindAt: "2026-07-19T12:00:00Z" }),
      { nowMicros: NOW },
    );
    assert.equal(past.ok, false);
    if (!past.ok) {
      assert.equal(past.code, "remind_at_before_now");
    }
    const future = validateReminderFields(
      sample({ remindAt: "2026-07-20T12:00:00.000001Z" }),
      { nowMicros: NOW },
    );
    assert.equal(future.ok, true);
  });

  it("enforces a maximum 366-day horizon", () => {
    assert.equal(REMINDER_MAX_HORIZON_SECONDS, 366 * 24 * 3600);
    const edge = validateReminderFields(sample({ remindAt: HORIZON_EDGE }), {
      nowMicros: NOW,
    });
    assert.equal(edge.ok, true);
    const tooLate = validateReminderFields(
      sample({ remindAt: HORIZON_TOO_LATE }),
      { nowMicros: NOW },
    );
    assert.equal(tooLate.ok, false);
    if (!tooLate.ok) {
      assert.equal(tooLate.code, "remind_at_horizon_too_long");
      assert.equal(tooLate.field, "remindAt");
    }
  });

  it("allows newline and tab only in notes and rejects oversized notes", () => {
    const ok = validateReminderFields(
      sample({ notes: "Line one\n\tLine two" }),
      { nowMicros: NOW },
    );
    assert.equal(ok.ok, true);
    const tooLong = validateReminderFields(
      sample({ notes: "N".repeat(REMINDER_NOTES_MAX + 1) }),
      { nowMicros: NOW },
    );
    assert.equal(tooLong.ok, false);
    if (!tooLong.ok) {
      assert.equal(tooLong.code, "notes_too_long");
      assert.equal(tooLong.field, "notes");
    }
    const controls = validateReminderFields(
      sample({ notes: "bad\u0000notes" }),
      { nowMicros: NOW },
    );
    assert.equal(controls.ok, false);
    if (!controls.ok) {
      assert.equal(controls.code, "notes_invalid_controls");
    }
  });

  it("rejects oversized and control-bearing list names", () => {
    const tooLong = validateReminderFields(
      sample({ listName: "L".repeat(REMINDER_LIST_NAME_MAX + 1) }),
      { nowMicros: NOW },
    );
    assert.equal(tooLong.ok, false);
    if (!tooLong.ok) {
      assert.equal(tooLong.code, "list_name_too_long");
      assert.equal(tooLong.field, "listName");
    }
    const controls = validateReminderFields(
      sample({ listName: "bad\u0000list" }),
      { nowMicros: NOW },
    );
    assert.equal(controls.ok, false);
    if (!controls.ok) {
      assert.equal(controls.code, "list_name_invalid_controls");
    }
  });

  it("rejects unknown fields and non-string values", () => {
    assert.equal(validateReminderFields({ extra: true }).ok, false);
    assert.equal(
      validateReminderFields(sample({ title: 1 }), { nowMicros: NOW }).ok,
      false,
    );
    assert.equal(validateReminderFields(null).ok, false);
    assert.equal(validateReminderFields([]).ok, false);
  });

  it("maps field-specific validation messages", () => {
    assert.match(mapReminderValidationMessage("remind_at_horizon_too_long"), /366 days/);
    assert.match(mapReminderValidationMessage("notes_too_long"), /4,000/);
    assert.equal(
      mapReminderValidationMessage("provider_timeout").includes("provider"),
      false,
    );
  });

  it("detects Unicode format characters without rewriting", () => {
    assert.equal(hasReminderUnicodeFormatChars("plain"), false);
    assert.equal(hasReminderUnicodeFormatChars("bad\u200btext"), true);
    assert.equal(isBlankReminderTitle("   "), true);
  });

  it("creates empty fields and initial client state", () => {
    const fields = createEmptyReminderFields();
    assert.equal(fields.title, "");
    assert.equal(fields.remindAt, "");
    const state = createInitialReminderProposalClientState();
    assert.equal(state.pending, false);
    assert.deepEqual(state.fields, fields);
  });

  it("reducer blocks duplicate submit and clears pending explicitly", () => {
    let state = reduceReminderProposalClientState(
      createInitialReminderProposalClientState(),
      { type: "submit_started" },
    );
    assert.equal(state.pending, true);
    const duplicate = reduceReminderProposalClientState(state, {
      type: "submit_blocked_duplicate",
    });
    assert.equal(duplicate, state);
    const secondStart = reduceReminderProposalClientState(state, {
      type: "submit_started",
    });
    assert.equal(secondStart, state);
    state = reduceReminderProposalClientState(state, { type: "clear_pending" });
    assert.equal(state.pending, false);
    state = reduceReminderProposalClientState(state, { type: "clear_form" });
    assert.deepEqual(state.fields, createEmptyReminderFields());
    assert.equal(state.pending, false);
  });
});
