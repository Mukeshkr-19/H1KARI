/** Pure Phase 3 calendar proposal helpers. No transport, storage, or timers. */

export const CALENDAR_NAME_MAX = 200;
export const CALENDAR_TITLE_MAX = 500;
export const CALENDAR_LOCATION_MAX = 500;
export const CALENDAR_NOTES_MAX = 4000;
export const CALENDAR_MAX_RANGE_SECONDS = 31 * 24 * 3600;

/**
 * ISO 8601 local date-time with required explicit offset or Z.
 * Fractional seconds accept 1–6 digits (Python microsecond parity).
 * Component validity is checked separately — never trust Date normalization alone.
 */
export const CALENDAR_DATETIME_WITH_OFFSET_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?(Z|[+-]\d{2}:\d{2})$/;

/** Unicode Format (Cf) characters — reject; never strip or rewrite. */
const UNICODE_FORMAT_PATTERN = /\p{Cf}/u;

/**
 * Python ``str.strip()`` blank parity for calendar titles.
 * Unicode White_Space includes U+0085 NEXT LINE, which JS ``trim()`` does not.
 */
const PYTHON_BLANK_TITLE_PATTERN = /^[\p{White_Space}]*$/u;

const MICROS_PER_SECOND = BigInt(1_000_000);
const MICROS_PER_MINUTE = BigInt(60) * MICROS_PER_SECOND;
const MICROS_PER_HOUR = BigInt(60) * MICROS_PER_MINUTE;
const MICROS_PER_DAY = BigInt(24) * MICROS_PER_HOUR;
const MAX_RANGE_MICROS = BigInt(CALENDAR_MAX_RANGE_SECONDS) * MICROS_PER_SECOND;

const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31] as const;

export type CalendarFormMode = "read" | "draft";

export type CalendarReadFields = Readonly<{
  start: string;
  end: string;
  calendarName: string;
}>;

export type CalendarDraftFields = Readonly<{
  title: string;
  start: string;
  end: string;
  calendarName: string;
  location: string;
  notes: string;
}>;

export type CalendarReadFieldName = "start" | "end" | "calendarName";
export type CalendarDraftFieldName =
  | "title"
  | "start"
  | "end"
  | "calendarName"
  | "location"
  | "notes";
export type CalendarFieldName = CalendarReadFieldName | CalendarDraftFieldName;

export type CalendarReadValidationCode =
  | "start_required"
  | "start_invalid_format"
  | "start_missing_timezone"
  | "start_invalid_controls"
  | "end_required"
  | "end_invalid_format"
  | "end_missing_timezone"
  | "end_invalid_controls"
  | "end_before_start"
  | "range_too_long"
  | "calendar_name_too_long"
  | "calendar_name_invalid_controls";

export type CalendarDraftValidationCode =
  | "title_required"
  | "title_too_long"
  | "title_invalid_controls"
  | "start_required"
  | "start_invalid_format"
  | "start_missing_timezone"
  | "start_invalid_controls"
  | "end_required"
  | "end_invalid_format"
  | "end_missing_timezone"
  | "end_invalid_controls"
  | "end_before_start"
  | "range_too_long"
  | "calendar_name_required"
  | "calendar_name_too_long"
  | "calendar_name_invalid_controls"
  | "location_too_long"
  | "location_invalid_controls"
  | "notes_too_long"
  | "notes_invalid_controls";

export type CalendarValidationCode =
  | CalendarReadValidationCode
  | CalendarDraftValidationCode;

export type CalendarReadValidationResult =
  | Readonly<{ ok: true; fields: ValidatedCalendarReadFields }>
  | Readonly<{
      ok: false;
      code: CalendarReadValidationCode;
      field: CalendarReadFieldName;
    }>;

export type CalendarDraftValidationResult =
  | Readonly<{ ok: true; fields: ValidatedCalendarDraftFields }>
  | Readonly<{
      ok: false;
      code: CalendarDraftValidationCode;
      field: CalendarDraftFieldName;
    }>;

export type ValidatedCalendarReadFields = Readonly<{
  start: string;
  end: string;
  calendarName?: string;
}>;

export type ValidatedCalendarDraftFields = Readonly<{
  title: string;
  start: string;
  end: string;
  calendarName: string;
  location?: string;
  notes?: string;
}>;

export type CalendarProposalClientState = Readonly<{
  mode: CalendarFormMode;
  readFields: CalendarReadFields;
  draftFields: CalendarDraftFields;
  pending: boolean;
  validationCode?: CalendarValidationCode;
  validationField?: CalendarFieldName;
}>;

export type CalendarProposalClientEvent =
  | Readonly<{ type: "mode_changed"; mode: CalendarFormMode }>
  | Readonly<{ type: "read_fields_changed"; fields: CalendarReadFields }>
  | Readonly<{ type: "draft_fields_changed"; fields: CalendarDraftFields }>
  | Readonly<{
      type: "read_validation_failed";
      code: CalendarReadValidationCode;
      field: CalendarReadFieldName;
    }>
  | Readonly<{
      type: "draft_validation_failed";
      code: CalendarDraftValidationCode;
      field: CalendarDraftFieldName;
    }>
  | Readonly<{ type: "submit_started"; mode: CalendarFormMode }>
  | Readonly<{ type: "submit_blocked_duplicate" }>
  | Readonly<{ type: "clear_form" }>
  | Readonly<{ type: "clear_pending" }>;

const READ_FIELD_KEYS = new Set(["start", "end", "calendarName"]);
const DRAFT_FIELD_KEYS = new Set([
  "title",
  "start",
  "end",
  "calendarName",
  "location",
  "notes",
]);

const READ_VALIDATION_MESSAGES: Record<CalendarReadValidationCode, string> = {
  start_required: "Enter a start date and time.",
  start_invalid_format: "Enter the start as YYYY-MM-DDTHH:mm:ss±HH:MM or Z.",
  start_missing_timezone: "Include an explicit timezone offset or Z on the start.",
  start_invalid_controls: "Start contains characters that are not allowed.",
  end_required: "Enter an end date and time.",
  end_invalid_format: "Enter the end as YYYY-MM-DDTHH:mm:ss±HH:MM or Z.",
  end_missing_timezone: "Include an explicit timezone offset or Z on the end.",
  end_invalid_controls: "End contains characters that are not allowed.",
  end_before_start: "End must be after start.",
  range_too_long: "The range must be 31 days or fewer.",
  calendar_name_too_long: "Calendar name must be 200 characters or fewer.",
  calendar_name_invalid_controls:
    "Calendar name contains characters that are not allowed.",
};

const DRAFT_VALIDATION_MESSAGES: Record<CalendarDraftValidationCode, string> = {
  title_required: "Enter an event title.",
  title_too_long: "Title must be 500 characters or fewer.",
  title_invalid_controls: "Title contains characters that are not allowed.",
  start_required: "Enter a start date and time.",
  start_invalid_format: "Enter the start as YYYY-MM-DDTHH:mm:ss±HH:MM or Z.",
  start_missing_timezone: "Include an explicit timezone offset or Z on the start.",
  start_invalid_controls: "Start contains characters that are not allowed.",
  end_required: "Enter an end date and time.",
  end_invalid_format: "Enter the end as YYYY-MM-DDTHH:mm:ss±HH:MM or Z.",
  end_missing_timezone: "Include an explicit timezone offset or Z on the end.",
  end_invalid_controls: "End contains characters that are not allowed.",
  end_before_start: "End must be after start.",
  range_too_long: "The event must be 31 days or fewer.",
  calendar_name_required: "Enter a calendar name.",
  calendar_name_too_long: "Calendar name must be 200 characters or fewer.",
  calendar_name_invalid_controls:
    "Calendar name contains characters that are not allowed.",
  location_too_long: "Location must be 500 characters or fewer.",
  location_invalid_controls: "Location contains characters that are not allowed.",
  notes_too_long: "Notes must be 4,000 characters or fewer.",
  notes_invalid_controls: "Notes contains characters that are not allowed.",
};

export function createEmptyCalendarReadFields(): CalendarReadFields {
  return Object.freeze({
    start: "",
    end: "",
    calendarName: "",
  });
}

export function createEmptyCalendarDraftFields(): CalendarDraftFields {
  return Object.freeze({
    title: "",
    start: "",
    end: "",
    calendarName: "",
    location: "",
    notes: "",
  });
}

export function createInitialCalendarProposalClientState(): CalendarProposalClientState {
  return Object.freeze({
    mode: "read" as const,
    readFields: createEmptyCalendarReadFields(),
    draftFields: createEmptyCalendarDraftFields(),
    pending: false,
  });
}

export function hasCalendarUnicodeFormatChars(value: string): boolean {
  return UNICODE_FORMAT_PATTERN.test(value);
}

/** Python ``len(str)`` parity: count Unicode code points, not UTF-16 units. */
export function calendarCodePointLength(value: string): number {
  return Array.from(value).length;
}

/**
 * True when ``title`` is empty after Python-style whitespace stripping.
 * Does not mutate or rewrite the original string.
 */
export function isBlankCalendarTitle(value: string): boolean {
  return PYTHON_BLANK_TITLE_PATTERN.test(value);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasOnlyKeys(
  record: Record<string, unknown>,
  allowed: ReadonlySet<string>,
): boolean {
  for (const key of Object.keys(record)) {
    if (!allowed.has(key)) {
      return false;
    }
  }
  return true;
}

function hasDisallowedAsciiControls(
  value: string,
  allowNewlineTab: boolean,
): boolean {
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (allowNewlineTab && (code === 9 || code === 10)) {
      continue;
    }
    if (code === 0 || (code > 0 && code < 32) || code === 127) {
      return true;
    }
  }
  return false;
}

function hasExplicitTimezone(value: string): boolean {
  return /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
}

function isLeapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function daysInMonth(year: number, month: number): number {
  if (month === 2) {
    return isLeapYear(year) ? 29 : 28;
  }
  return DAYS_IN_MONTH[month - 1] ?? 0;
}

/** Days since Unix epoch (1970-01-01), proleptic Gregorian. */
function daysFromUnixEpoch(year: number, month: number, day: number): bigint {
  let y = year;
  let m = month;
  if (m <= 2) {
    y -= 1;
    m += 9;
  } else {
    m -= 3;
  }
  const era = Math.floor(y / 400);
  const yoe = y - era * 400;
  const doy = Math.floor((153 * m + 2) / 5) + day - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return BigInt(era * 146097 + doe - 719468);
}

function padFractionToMicros(fraction: string | undefined): number {
  if (!fraction) {
    return 0;
  }
  return Number.parseInt(fraction.padEnd(6, "0"), 10);
}

/**
 * Parse a calendar instant to UTC microseconds since epoch.
 * Returns null for malformed or calendar-impossible values.
 * Does not use Date normalization APIs.
 */
export function parseCalendarInstantMicros(value: string): bigint | null {
  const match = CALENDAR_DATETIME_WITH_OFFSET_PATTERN.exec(value);
  if (!match) {
    return null;
  }
  const year = Number.parseInt(match[1], 10);
  const month = Number.parseInt(match[2], 10);
  const day = Number.parseInt(match[3], 10);
  const hour = Number.parseInt(match[4], 10);
  const minute = Number.parseInt(match[5], 10);
  const second = match[6] === undefined ? 0 : Number.parseInt(match[6], 10);
  const micros = padFractionToMicros(match[7]);
  const zone = match[8];

  // Python datetime years are 1..9999; reject year 0000.
  if (year < 1 || year > 9999) {
    return null;
  }
  if (month < 1 || month > 12) {
    return null;
  }
  if (day < 1 || day > daysInMonth(year, month)) {
    return null;
  }
  if (hour < 0 || hour > 23) {
    return null;
  }
  if (minute < 0 || minute > 59) {
    return null;
  }
  if (second < 0 || second > 59) {
    return null;
  }
  if (micros < 0 || micros > 999_999) {
    return null;
  }

  let offsetMicros = BigInt(0);
  if (zone !== "Z") {
    const sign = zone[0] === "-" ? BigInt(-1) : BigInt(1);
    const offsetHour = Number.parseInt(zone.slice(1, 3), 10);
    const offsetMinute = Number.parseInt(zone.slice(4, 6), 10);
    if (offsetHour < 0 || offsetHour > 23 || offsetMinute < 0 || offsetMinute > 59) {
      return null;
    }
    offsetMicros =
      sign *
      (BigInt(offsetHour) * MICROS_PER_HOUR +
        BigInt(offsetMinute) * MICROS_PER_MINUTE);
  }

  const localMicros =
    daysFromUnixEpoch(year, month, day) * MICROS_PER_DAY +
    BigInt(hour) * MICROS_PER_HOUR +
    BigInt(minute) * MICROS_PER_MINUTE +
    BigInt(second) * MICROS_PER_SECOND +
    BigInt(micros);

  return localMicros - offsetMicros;
}

type DateRangeFailure = Readonly<{
  code: "end_before_start" | "range_too_long";
  field: "end";
}>;

function validateDateRange(
  startValue: string,
  endValue: string,
): DateRangeFailure | null {
  const startInstant = parseCalendarInstantMicros(startValue);
  const endInstant = parseCalendarInstantMicros(endValue);
  if (startInstant === null || endInstant === null) {
    return null;
  }
  if (endInstant <= startInstant) {
    return Object.freeze({ code: "end_before_start", field: "end" });
  }
  if (endInstant - startInstant > MAX_RANGE_MICROS) {
    return Object.freeze({ code: "range_too_long", field: "end" });
  }
  return null;
}

function validateDateTimeField(
  value: unknown,
  field: "start" | "end",
): CalendarReadValidationCode | null {
  if (typeof value !== "string" || value.length < 1) {
    return field === "start" ? "start_required" : "end_required";
  }
  if (
    hasCalendarUnicodeFormatChars(value) ||
    hasDisallowedAsciiControls(value, false)
  ) {
    return field === "start" ? "start_invalid_controls" : "end_invalid_controls";
  }
  if (!hasExplicitTimezone(value)) {
    return field === "start" ? "start_missing_timezone" : "end_missing_timezone";
  }
  if (parseCalendarInstantMicros(value) === null) {
    return field === "start" ? "start_invalid_format" : "end_invalid_format";
  }
  return null;
}

function validateOptionalTextField(
  value: unknown,
  maximum: number,
  tooLongCode: CalendarReadValidationCode | CalendarDraftValidationCode,
  controlsCode: CalendarReadValidationCode | CalendarDraftValidationCode,
  allowNewlineTab: boolean,
): CalendarReadValidationCode | CalendarDraftValidationCode | null {
  if (typeof value !== "string") {
    return controlsCode;
  }
  if (value.length < 1) {
    return null;
  }
  if (
    hasCalendarUnicodeFormatChars(value) ||
    hasDisallowedAsciiControls(value, allowNewlineTab)
  ) {
    return controlsCode;
  }
  if (calendarCodePointLength(value) > maximum) {
    return tooLongCode;
  }
  return null;
}

export function mapCalendarReadValidationMessage(code: unknown): string {
  if (
    typeof code === "string" &&
    Object.prototype.hasOwnProperty.call(READ_VALIDATION_MESSAGES, code)
  ) {
    return READ_VALIDATION_MESSAGES[code as CalendarReadValidationCode];
  }
  return "The calendar read request could not be validated.";
}

export function mapCalendarDraftValidationMessage(code: unknown): string {
  if (
    typeof code === "string" &&
    Object.prototype.hasOwnProperty.call(DRAFT_VALIDATION_MESSAGES, code)
  ) {
    return DRAFT_VALIDATION_MESSAGES[code as CalendarDraftValidationCode];
  }
  return "The calendar event draft could not be validated.";
}

export function mapCalendarValidationMessage(
  mode: CalendarFormMode,
  code: unknown,
): string {
  return mode === "read"
    ? mapCalendarReadValidationMessage(code)
    : mapCalendarDraftValidationMessage(code);
}

export function validateCalendarReadFields(
  input: unknown,
): CalendarReadValidationResult {
  if (!isPlainObject(input) || !hasOnlyKeys(input, READ_FIELD_KEYS)) {
    return Object.freeze({
      ok: false,
      code: "start_required" as const,
      field: "start" as const,
    });
  }

  const startError = validateDateTimeField(input.start, "start");
  if (startError) {
    return Object.freeze({
      ok: false,
      code: startError,
      field: "start" as const,
    });
  }

  const endError = validateDateTimeField(input.end, "end");
  if (endError) {
    return Object.freeze({
      ok: false,
      code: endError,
      field: "end" as const,
    });
  }

  const rangeError = validateDateRange(
    input.start as string,
    input.end as string,
  );
  if (rangeError) {
    return Object.freeze({
      ok: false,
      code: rangeError.code,
      field: rangeError.field,
    });
  }

  const calendarNameError = validateOptionalTextField(
    input.calendarName,
    CALENDAR_NAME_MAX,
    "calendar_name_too_long",
    "calendar_name_invalid_controls",
    false,
  );
  if (calendarNameError) {
    return Object.freeze({
      ok: false,
      code: calendarNameError as CalendarReadValidationCode,
      field: "calendarName" as const,
    });
  }

  const fields: {
    start: string;
    end: string;
    calendarName?: string;
  } = {
    start: input.start as string,
    end: input.end as string,
  };
  if (
    typeof input.calendarName === "string" &&
    input.calendarName.length > 0
  ) {
    fields.calendarName = input.calendarName;
  }
  return Object.freeze({
    ok: true as const,
    fields: Object.freeze(fields),
  });
}

export function validateCalendarDraftFields(
  input: unknown,
): CalendarDraftValidationResult {
  if (!isPlainObject(input) || !hasOnlyKeys(input, DRAFT_FIELD_KEYS)) {
    return Object.freeze({
      ok: false,
      code: "title_required" as const,
      field: "title" as const,
    });
  }
  if (typeof input.title !== "string") {
    return Object.freeze({
      ok: false,
      code: "title_required" as const,
      field: "title" as const,
    });
  }
  if (
    hasCalendarUnicodeFormatChars(input.title) ||
    hasDisallowedAsciiControls(input.title, false)
  ) {
    return Object.freeze({
      ok: false,
      code: "title_invalid_controls" as const,
      field: "title" as const,
    });
  }
  // Blank detection uses Python strip whitespace parity; output stays original.
  if (isBlankCalendarTitle(input.title)) {
    return Object.freeze({
      ok: false,
      code: "title_required" as const,
      field: "title" as const,
    });
  }
  if (calendarCodePointLength(input.title) > CALENDAR_TITLE_MAX) {
    return Object.freeze({
      ok: false,
      code: "title_too_long" as const,
      field: "title" as const,
    });
  }

  const startError = validateDateTimeField(input.start, "start");
  if (startError) {
    return Object.freeze({
      ok: false,
      code: startError as CalendarDraftValidationCode,
      field: "start" as const,
    });
  }

  const endError = validateDateTimeField(input.end, "end");
  if (endError) {
    return Object.freeze({
      ok: false,
      code: endError as CalendarDraftValidationCode,
      field: "end" as const,
    });
  }

  const rangeError = validateDateRange(
    input.start as string,
    input.end as string,
  );
  if (rangeError) {
    return Object.freeze({
      ok: false,
      code: rangeError.code,
      field: rangeError.field,
    });
  }

  if (typeof input.calendarName !== "string") {
    return Object.freeze({
      ok: false,
      code: "calendar_name_required" as const,
      field: "calendarName" as const,
    });
  }
  if (input.calendarName.length < 1) {
    return Object.freeze({
      ok: false,
      code: "calendar_name_required" as const,
      field: "calendarName" as const,
    });
  }
  const calendarNameError = validateOptionalTextField(
    input.calendarName,
    CALENDAR_NAME_MAX,
    "calendar_name_too_long",
    "calendar_name_invalid_controls",
    false,
  );
  if (calendarNameError) {
    return Object.freeze({
      ok: false,
      code: calendarNameError as CalendarDraftValidationCode,
      field: "calendarName" as const,
    });
  }

  const locationError = validateOptionalTextField(
    input.location,
    CALENDAR_LOCATION_MAX,
    "location_too_long",
    "location_invalid_controls",
    true,
  );
  if (locationError) {
    return Object.freeze({
      ok: false,
      code: locationError as CalendarDraftValidationCode,
      field: "location" as const,
    });
  }

  const notesError = validateOptionalTextField(
    input.notes,
    CALENDAR_NOTES_MAX,
    "notes_too_long",
    "notes_invalid_controls",
    true,
  );
  if (notesError) {
    return Object.freeze({
      ok: false,
      code: notesError as CalendarDraftValidationCode,
      field: "notes" as const,
    });
  }

  const fields: {
    title: string;
    start: string;
    end: string;
    calendarName: string;
    location?: string;
    notes?: string;
  } = {
    title: input.title,
    start: input.start as string,
    end: input.end as string,
    calendarName: input.calendarName,
  };
  if (typeof input.location === "string" && input.location.length > 0) {
    fields.location = input.location;
  }
  if (typeof input.notes === "string" && input.notes.length > 0) {
    fields.notes = input.notes;
  }
  return Object.freeze({
    ok: true as const,
    fields: Object.freeze(fields),
  });
}

function freezeClientState(
  state: CalendarProposalClientState,
): CalendarProposalClientState {
  return Object.freeze({
    ...state,
    readFields: Object.freeze({ ...state.readFields }),
    draftFields: Object.freeze({ ...state.draftFields }),
  });
}

export function reduceCalendarProposalClientState(
  state: CalendarProposalClientState,
  event: CalendarProposalClientEvent,
): CalendarProposalClientState {
  switch (event.type) {
    case "mode_changed":
      return freezeClientState({
        ...state,
        mode: event.mode,
        validationCode: undefined,
        validationField: undefined,
      });
    case "read_fields_changed":
      return freezeClientState({
        ...state,
        readFields: event.fields,
        validationCode: undefined,
        validationField: undefined,
      });
    case "draft_fields_changed":
      return freezeClientState({
        ...state,
        draftFields: event.fields,
        validationCode: undefined,
        validationField: undefined,
      });
    case "read_validation_failed":
      return freezeClientState({
        ...state,
        pending: false,
        validationCode: event.code,
        validationField: event.field,
      });
    case "draft_validation_failed":
      return freezeClientState({
        ...state,
        pending: false,
        validationCode: event.code,
        validationField: event.field,
      });
    case "submit_blocked_duplicate":
      return state;
    case "submit_started":
      if (state.pending) {
        return state;
      }
      return freezeClientState({
        ...state,
        pending: true,
        mode: event.mode,
        validationCode: undefined,
        validationField: undefined,
      });
    case "clear_pending":
      return freezeClientState({
        ...state,
        pending: false,
        validationCode: undefined,
        validationField: undefined,
      });
    case "clear_form":
      return createInitialCalendarProposalClientState();
    default:
      return state;
  }
}
