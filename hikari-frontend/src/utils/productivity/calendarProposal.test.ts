import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  CALENDAR_NAME_MAX,
  CALENDAR_NOTES_MAX,
  CALENDAR_TITLE_MAX,
  calendarCodePointLength,
  createEmptyCalendarDraftFields,
  createEmptyCalendarReadFields,
  createInitialCalendarProposalClientState,
  hasCalendarUnicodeFormatChars,
  isBlankCalendarTitle,
  mapCalendarDraftValidationMessage,
  mapCalendarReadValidationMessage,
  parseCalendarInstantMicros,
  reduceCalendarProposalClientState,
  validateCalendarDraftFields,
  validateCalendarReadFields,
} from "./calendarProposal";

const START = "2026-07-18T09:00:00-04:00";
const END = "2026-07-18T10:00:00-04:00";

function readSample(overrides: Record<string, unknown> = {}) {
  return {
    start: START,
    end: END,
    calendarName: "",
    ...overrides,
  };
}

function draftSample(overrides: Record<string, unknown> = {}) {
  return {
    title: "Planning sync",
    start: START,
    end: END,
    calendarName: "Work",
    location: "",
    notes: "",
    ...overrides,
  };
}

describe("calendarProposal", () => {
  it("validates and freezes bounded calendar read fields", () => {
    const result = validateCalendarReadFields(
      readSample({ calendarName: "Work" }),
    );
    assert.equal(result.ok, true);
    if (!result.ok) {
      return;
    }
    assert.equal(result.fields.start, START);
    assert.equal(result.fields.end, END);
    assert.equal(result.fields.calendarName, "Work");
    assert.throws(() => {
      (result.fields as { start: string }).start = "nope";
    }, TypeError);
  });

  it("omits empty optional calendar name without rewriting dates", () => {
    const result = validateCalendarReadFields(readSample());
    assert.equal(result.ok, true);
    if (!result.ok) {
      return;
    }
    assert.equal("calendarName" in result.fields, false);
    assert.equal(result.fields.start, START);
    assert.equal(result.fields.end, END);
  });

  it("rejects naive datetimes missing explicit timezone offsets", () => {
    const start = validateCalendarReadFields(
      readSample({ start: "2026-07-18T09:00", end: END }),
    );
    assert.equal(start.ok, false);
    if (!start.ok) {
      assert.equal(start.code, "start_missing_timezone");
      assert.equal(start.field, "start");
    }
    const end = validateCalendarReadFields(
      readSample({ start: START, end: "2026-07-18T10:00" }),
    );
    assert.equal(end.ok, false);
    if (!end.ok) {
      assert.equal(end.code, "end_missing_timezone");
      assert.equal(end.field, "end");
    }
  });

  it("accepts Zulu and offset datetimes without inventing timezones", () => {
    const zulu = validateCalendarReadFields(
      readSample({
        start: "2026-07-18T13:00:00Z",
        end: "2026-07-18T14:00:00Z",
      }),
    );
    assert.equal(zulu.ok, true);
    if (zulu.ok) {
      assert.equal(zulu.fields.start, "2026-07-18T13:00:00Z");
      assert.equal(zulu.fields.end, "2026-07-18T14:00:00Z");
    }
  });

  it("rejects impossible calendar dates without Date.parse normalization", () => {
    const cases = [
      "2026-02-30T09:00:00Z",
      "2026-04-31T09:00:00Z",
      "2026-07-18T24:00:00Z",
      "2025-02-29T09:00:00Z",
      "2026-07-18T09:00:00+25:00",
      "2026-07-18T09:00:00+12:60",
      "2026-13-01T09:00:00Z",
      "2026-07-18T09:60:00Z",
    ] as const;
    for (const impossible of cases) {
      assert.equal(
        parseCalendarInstantMicros(impossible),
        null,
        impossible,
      );
      assert.equal(
        validateCalendarReadFields(
          readSample({ start: impossible, end: "2026-07-18T10:00:00Z" }),
        ).ok,
        false,
        impossible,
      );
    }
    assert.notEqual(parseCalendarInstantMicros("2024-02-29T09:00:00Z"), null);
    assert.equal(
      validateCalendarReadFields(
        readSample({
          start: "2024-02-29T09:00:00Z",
          end: "2024-02-29T10:00:00Z",
        }),
      ).ok,
      true,
    );
  });

  it("rejects year 0000 and accepts Python datetime year bounds", () => {
    assert.equal(parseCalendarInstantMicros("0000-01-01T00:00:00Z"), null);
    assert.equal(
      validateCalendarReadFields(
        readSample({
          start: "0000-01-01T00:00:00Z",
          end: "0000-01-01T01:00:00Z",
        }),
      ).ok,
      false,
    );
    assert.notEqual(parseCalendarInstantMicros("0001-01-01T00:00:00Z"), null);
    assert.equal(
      validateCalendarReadFields(
        readSample({
          start: "0001-01-01T00:00:00Z",
          end: "0001-01-01T01:00:00Z",
        }),
      ).ok,
      true,
    );
    assert.notEqual(
      parseCalendarInstantMicros("9999-12-31T23:59:59.999999Z"),
      null,
    );
    assert.equal(
      validateCalendarReadFields(
        readSample({
          start: "9999-12-31T22:00:00Z",
          end: "9999-12-31T23:59:59.999999Z",
        }),
      ).ok,
      true,
    );
  });

  it("orders by microsecond precision and accepts 1–6 fractional digits", () => {
    const earlier = parseCalendarInstantMicros("2026-07-18T09:00:00.000001Z");
    const later = parseCalendarInstantMicros("2026-07-18T09:00:00.000002Z");
    assert.ok(earlier !== null && later !== null);
    assert.equal(later! - earlier!, BigInt(1));

    const msOnly = parseCalendarInstantMicros("2026-07-18T09:00:00.001Z");
    const micros = parseCalendarInstantMicros("2026-07-18T09:00:00.001000Z");
    assert.equal(msOnly, micros);

    const equalMs = validateCalendarReadFields(
      readSample({
        start: "2026-07-18T09:00:00.001Z",
        end: "2026-07-18T09:00:00.001000Z",
      }),
    );
    assert.equal(equalMs.ok, false);
    if (!equalMs.ok) {
      assert.equal(equalMs.code, "end_before_start");
    }

    const microOrder = validateCalendarReadFields(
      readSample({
        start: "2026-07-18T09:00:00.000001Z",
        end: "2026-07-18T09:00:00.000002Z",
      }),
    );
    assert.equal(microOrder.ok, true);

    assert.equal(
      validateCalendarReadFields(
        readSample({
          start: "2026-07-18T09:00:00.123456Z",
          end: "2026-07-18T09:00:01.000000Z",
        }),
      ).ok,
      true,
    );
    assert.equal(
      parseCalendarInstantMicros("2026-07-18T09:00:00.1234567Z"),
      null,
    );
  });

  it("compares offset-aware ordering without inventing local timezones", () => {
    const left = parseCalendarInstantMicros("2026-07-18T12:00:00-04:00");
    const right = parseCalendarInstantMicros("2026-07-18T16:00:00Z");
    assert.equal(left, right);
    const after = validateCalendarReadFields(
      readSample({
        start: "2026-07-18T12:00:00-04:00",
        end: "2026-07-18T16:00:01Z",
      }),
    );
    assert.equal(after.ok, true);
    const reversed = validateCalendarReadFields(
      readSample({
        start: "2026-07-18T16:00:01Z",
        end: "2026-07-18T12:00:00-04:00",
      }),
    );
    assert.equal(reversed.ok, false);
    if (!reversed.ok) {
      assert.equal(reversed.code, "end_before_start");
    }
  });

  it("rejects invalid ordering and excessive ranges", () => {
    const reversed = validateCalendarReadFields(
      readSample({ start: END, end: START }),
    );
    assert.equal(reversed.ok, false);
    if (!reversed.ok) {
      assert.equal(reversed.code, "end_before_start");
      assert.equal(reversed.field, "end");
    }
    const equal = validateCalendarReadFields(
      readSample({ start: START, end: START }),
    );
    assert.equal(equal.ok, false);
    if (!equal.ok) {
      assert.equal(equal.code, "end_before_start");
    }
    const tooLong = validateCalendarReadFields(
      readSample({
        start: "2026-07-01T00:00:00Z",
        end: "2026-08-02T00:00:01Z",
      }),
    );
    assert.equal(tooLong.ok, false);
    if (!tooLong.ok) {
      assert.equal(tooLong.code, "range_too_long");
      assert.equal(tooLong.field, "end");
    }
    assert.equal(
      validateCalendarReadFields(
        readSample({
          start: "2026-07-01T00:00:00Z",
          end: "2026-08-01T00:00:00Z",
        }),
      ).ok,
      true,
    );
  });

  it("rejects malformed dates bounds controls and Unicode Cf without rewriting", () => {
    assert.equal(
      validateCalendarReadFields(readSample({ start: "not-a-date", end: END })).ok,
      false,
    );
    assert.equal(
      validateCalendarReadFields(
        readSample({ calendarName: "N".repeat(CALENDAR_NAME_MAX + 1) }),
      ).ok,
      false,
    );
    const cf = "\u200B";
    assert.equal(hasCalendarUnicodeFormatChars(`a${cf}b`), true);
    const cfName = validateCalendarReadFields(
      readSample({ calendarName: `Work${cf}` }),
    );
    assert.equal(cfName.ok, false);
    if (!cfName.ok) {
      assert.equal(cfName.code, "calendar_name_invalid_controls");
    }
    const controls = validateCalendarReadFields(
      readSample({ start: `${START.slice(0, 10)}\u0007${START.slice(10)}`, end: END }),
    );
    assert.equal(controls.ok, false);
    if (!controls.ok) {
      assert.equal(controls.code, "start_invalid_controls");
    }
  });

  it("uses Unicode code-point length for text bounds including emoji", () => {
    const emoji = "😀";
    assert.equal(emoji.length, 2);
    assert.equal(calendarCodePointLength(emoji), 1);
    const exact = emoji.repeat(CALENDAR_NAME_MAX);
    assert.equal(calendarCodePointLength(exact), CALENDAR_NAME_MAX);
    assert.equal(exact.length, CALENDAR_NAME_MAX * 2);
    assert.equal(
      validateCalendarReadFields(readSample({ calendarName: exact })).ok,
      true,
    );
    const over = emoji.repeat(CALENDAR_NAME_MAX + 1);
    const rejected = validateCalendarReadFields(
      readSample({ calendarName: over }),
    );
    assert.equal(rejected.ok, false);
    if (!rejected.ok) {
      assert.equal(rejected.code, "calendar_name_too_long");
    }
  });

  it("preserves surrounding title whitespace and rejects overlong untrimmed titles", () => {
    const padded = "  Planning sync  ";
    const preserved = validateCalendarDraftFields(draftSample({ title: padded }));
    assert.equal(preserved.ok, true);
    if (preserved.ok) {
      assert.equal(preserved.fields.title, padded);
    }

    const overWithSpaces = ` ${"T".repeat(CALENDAR_TITLE_MAX)} `;
    assert.equal(calendarCodePointLength(overWithSpaces), CALENDAR_TITLE_MAX + 2);
    const rejected = validateCalendarDraftFields(
      draftSample({ title: overWithSpaces }),
    );
    assert.equal(rejected.ok, false);
    if (!rejected.ok) {
      assert.equal(rejected.code, "title_too_long");
    }

    const exact = "T".repeat(CALENDAR_TITLE_MAX);
    assert.equal(
      validateCalendarDraftFields(draftSample({ title: exact })).ok,
      true,
    );
  });

  it("rejects U+0085-only titles as blank under Python strip parity", () => {
    const nextLine = "\u0085";
    assert.equal(" \t\n".trim().length, 0);
    assert.notEqual(nextLine.trim().length, 0);
    assert.equal(isBlankCalendarTitle(nextLine), true);
    assert.equal(isBlankCalendarTitle(nextLine.repeat(3)), true);
    assert.equal(isBlankCalendarTitle(""), true);
    assert.equal(isBlankCalendarTitle("   "), true);

    const blankNel = validateCalendarDraftFields(
      draftSample({ title: nextLine }),
    );
    assert.equal(blankNel.ok, false);
    if (!blankNel.ok) {
      assert.equal(blankNel.code, "title_required");
      assert.equal(blankNel.field, "title");
    }

    const mixedNelSpaces = validateCalendarDraftFields(
      draftSample({ title: ` \u0085 ` }),
    );
    assert.equal(mixedNelSpaces.ok, false);
    if (!mixedNelSpaces.ok) {
      assert.equal(mixedNelSpaces.code, "title_required");
    }

    const preserved = validateCalendarDraftFields(
      draftSample({ title: "  Keep me  " }),
    );
    assert.equal(preserved.ok, true);
    if (preserved.ok) {
      assert.equal(preserved.fields.title, "  Keep me  ");
    }
    assert.equal(isBlankCalendarTitle("  Keep me  "), false);
  });

  it("validates draft fields with required calendar name and optional location notes", () => {
    const result = validateCalendarDraftFields(
      draftSample({
        location: "Room 3",
        notes: "Bring notes\nand agenda",
      }),
    );
    assert.equal(result.ok, true);
    if (!result.ok) {
      return;
    }
    assert.equal(result.fields.title, "Planning sync");
    assert.equal(result.fields.calendarName, "Work");
    assert.equal(result.fields.location, "Room 3");
    assert.equal(result.fields.notes, "Bring notes\nand agenda");
    const emptyOptional = validateCalendarDraftFields(draftSample());
    assert.equal(emptyOptional.ok, true);
    if (emptyOptional.ok) {
      assert.equal(emptyOptional.fields.calendarName, "Work");
      assert.equal("location" in emptyOptional.fields, false);
      assert.equal("notes" in emptyOptional.fields, false);
    }
  });

  it("rejects missing oversized and control-bearing draft calendar names", () => {
    const missing = validateCalendarDraftFields(draftSample({ calendarName: "" }));
    assert.equal(missing.ok, false);
    if (!missing.ok) {
      assert.equal(missing.code, "calendar_name_required");
      assert.equal(missing.field, "calendarName");
    }
    const tooLong = validateCalendarDraftFields(
      draftSample({ calendarName: "N".repeat(CALENDAR_NAME_MAX + 1) }),
    );
    assert.equal(tooLong.ok, false);
    if (!tooLong.ok) {
      assert.equal(tooLong.code, "calendar_name_too_long");
      assert.equal(tooLong.field, "calendarName");
    }
    const cf = "\u200B";
    const cfName = validateCalendarDraftFields(
      draftSample({ calendarName: `Work${cf}` }),
    );
    assert.equal(cfName.ok, false);
    if (!cfName.ok) {
      assert.equal(cfName.code, "calendar_name_invalid_controls");
      assert.equal(cfName.field, "calendarName");
    }
  });

  it("rejects empty oversized and control-bearing draft text", () => {
    assert.equal(validateCalendarDraftFields(draftSample({ title: "   " })).ok, false);
    const tooLong = validateCalendarDraftFields(
      draftSample({ title: "T".repeat(CALENDAR_TITLE_MAX + 1) }),
    );
    assert.equal(tooLong.ok, false);
    if (!tooLong.ok) {
      assert.equal(tooLong.code, "title_too_long");
    }
    const notes = validateCalendarDraftFields(
      draftSample({ notes: "N".repeat(CALENDAR_NOTES_MAX + 1) }),
    );
    assert.equal(notes.ok, false);
    if (!notes.ok) {
      assert.equal(notes.code, "notes_too_long");
      assert.equal(notes.field, "notes");
    }
    const cfTitle = validateCalendarDraftFields(
      draftSample({ title: "Plan\u202Ening" }),
    );
    assert.equal(cfTitle.ok, false);
    if (!cfTitle.ok) {
      assert.equal(cfTitle.code, "title_invalid_controls");
    }
  });

  it("maps field-specific validation messages", () => {
    assert.match(mapCalendarReadValidationMessage("range_too_long"), /31 days/);
    assert.match(mapCalendarDraftValidationMessage("notes_too_long"), /4,000/);
    assert.match(mapCalendarDraftValidationMessage("calendar_name_required"), /calendar name/i);
    assert.equal(
      mapCalendarReadValidationMessage("provider_timeout").includes("provider"),
      false,
    );
  });

  it("reducer blocks duplicate submit and clears pending explicitly", () => {
    let state = reduceCalendarProposalClientState(
      createInitialCalendarProposalClientState(),
      { type: "submit_started", mode: "read" },
    );
    assert.equal(state.pending, true);
    const duplicate = reduceCalendarProposalClientState(state, {
      type: "submit_blocked_duplicate",
    });
    assert.equal(duplicate, state);
    const secondStart = reduceCalendarProposalClientState(state, {
      type: "submit_started",
      mode: "draft",
    });
    assert.equal(secondStart, state);
    state = reduceCalendarProposalClientState(state, { type: "clear_pending" });
    assert.equal(state.pending, false);
    state = reduceCalendarProposalClientState(state, { type: "clear_form" });
    assert.deepEqual(state.readFields, createEmptyCalendarReadFields());
    assert.deepEqual(state.draftFields, createEmptyCalendarDraftFields());
    assert.equal(state.pending, false);
  });

  it("rejects unknown fields and non-string values", () => {
    assert.equal(validateCalendarReadFields(readSample({ extra: true })).ok, false);
    assert.equal(validateCalendarDraftFields(draftSample({ title: 1 })).ok, false);
    assert.equal(validateCalendarReadFields(null).ok, false);
    assert.equal(validateCalendarDraftFields([]).ok, false);
  });
});
