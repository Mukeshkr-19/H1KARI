/** Pure Phase 3 reminder proposal helpers. No transport, storage, or timers. */

import {
  calendarCodePointLength,
  hasCalendarUnicodeFormatChars,
  isBlankCalendarTitle,
  parseCalendarInstantMicros,
} from "./calendarProposal";

export const REMINDER_TITLE_MAX = 500;
export const REMINDER_NOTES_MAX = 4000;
export const REMINDER_LIST_NAME_MAX = 200;
export const REMINDER_MAX_HORIZON_SECONDS = 366 * 24 * 3600;

const MICROS_PER_MILLISECOND = BigInt(1_000);
const MICROS_PER_SECOND = BigInt(1_000_000);
const MAX_HORIZON_MICROS = BigInt(REMINDER_MAX_HORIZON_SECONDS) * MICROS_PER_SECOND;

export function hasReminderUnicodeFormatChars(value: string): boolean {
  return hasCalendarUnicodeFormatChars(value);
}

export type ReminderFields = Readonly<{
  title: string;
  remindAt: string;
  notes: string;
  listName: string;
}>;

export type ReminderFieldName = "title" | "remindAt" | "notes" | "listName";

export type ReminderValidationCode =
  | "title_required"
  | "title_too_long"
  | "title_invalid_controls"
  | "remind_at_required"
  | "remind_at_invalid_format"
  | "remind_at_missing_timezone"
  | "remind_at_invalid_controls"
  | "remind_at_before_now"
  | "remind_at_horizon_too_long"
  | "notes_too_long"
  | "notes_invalid_controls"
  | "list_name_too_long"
  | "list_name_invalid_controls";

export type ValidatedReminderFields = Readonly<{
  title: string;
  remindAt: string;
  notes?: string;
  listName?: string;
}>;

export type ReminderValidationResult =
  | Readonly<{ ok: true; fields: ValidatedReminderFields }>
  | Readonly<{
      ok: false;
      code: ReminderValidationCode;
      field: ReminderFieldName;
    }>;

export type ReminderProposalClientState = Readonly<{
  fields: ReminderFields;
  pending: boolean;
  validationCode?: ReminderValidationCode;
  validationField?: ReminderFieldName;
}>;

export type ReminderProposalClientEvent =
  | Readonly<{ type: "fields_changed"; fields: ReminderFields }>
  | Readonly<{
      type: "validation_failed";
      code: ReminderValidationCode;
      field: ReminderFieldName;
    }>
  | Readonly<{ type: "submit_started" }>
  | Readonly<{ type: "submit_blocked_duplicate" }>
  | Readonly<{ type: "clear_pending" }>
  | Readonly<{ type: "clear_form" }>;

const REMINDER_FIELD_KEYS = new Set(["title", "remindAt", "notes", "listName"]);

const VALIDATION_MESSAGES: Record<ReminderValidationCode, string> = {
  title_required: "Enter a reminder title.",
  title_too_long: "Title must be 500 characters or fewer.",
  title_invalid_controls: "Title contains characters that are not allowed.",
  remind_at_required: "Enter a remind-at date and time.",
  remind_at_invalid_format:
    "Enter the remind-at time as YYYY-MM-DDTHH:mm:ss±HH:MM or Z.",
  remind_at_missing_timezone:
    "Include an explicit timezone offset or Z on the remind-at time.",
  remind_at_invalid_controls:
    "Remind-at time contains characters that are not allowed.",
  remind_at_before_now: "Remind-at time must be in the future.",
  remind_at_horizon_too_long: "Remind-at time must be within 366 days.",
  notes_too_long: "Notes must be 4,000 characters or fewer.",
  notes_invalid_controls: "Notes contain characters that are not allowed.",
  list_name_too_long: "List name must be 200 characters or fewer.",
  list_name_invalid_controls:
    "List name contains characters that are not allowed.",
};

export type ReminderValidationOptions = Readonly<{
  nowMicros?: bigint;
}>;

export function createEmptyReminderFields(): ReminderFields {
  return Object.freeze({
    title: "",
    remindAt: "",
    notes: "",
    listName: "",
  });
}

export function createInitialReminderProposalClientState(): ReminderProposalClientState {
  return Object.freeze({
    fields: createEmptyReminderFields(),
    pending: false,
  });
}

export function reminderCodePointLength(value: string): number {
  return calendarCodePointLength(value);
}

export function isBlankReminderTitle(value: string): boolean {
  return isBlankCalendarTitle(value);
}

/** Current UTC epoch microseconds for client-side remind-at bounds. */
export function reminderNowMicros(): bigint {
  return BigInt(Date.now()) * MICROS_PER_MILLISECOND;
}

export function mapReminderValidationMessage(code: unknown): string {
  if (
    typeof code === "string" &&
    Object.prototype.hasOwnProperty.call(VALIDATION_MESSAGES, code)
  ) {
    return VALIDATION_MESSAGES[code as ReminderValidationCode];
  }
  return "The reminder request could not be validated.";
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

function validateOptionalTextField(
  value: unknown,
  maximum: number,
  tooLongCode: ReminderValidationCode,
  controlsCode: ReminderValidationCode,
  allowNewlineTab: boolean,
): ReminderValidationCode | null {
  if (typeof value !== "string") {
    return controlsCode;
  }
  if (value.length < 1) {
    return null;
  }
  if (
    hasReminderUnicodeFormatChars(value) ||
    hasDisallowedAsciiControls(value, allowNewlineTab)
  ) {
    return controlsCode;
  }
  if (reminderCodePointLength(value) > maximum) {
    return tooLongCode;
  }
  return null;
}

function validateRemindAtField(
  value: unknown,
  nowMicros: bigint,
): ReminderValidationCode | null {
  if (typeof value !== "string" || value.length < 1) {
    return "remind_at_required";
  }
  if (
    hasReminderUnicodeFormatChars(value) ||
    hasDisallowedAsciiControls(value, false)
  ) {
    return "remind_at_invalid_controls";
  }
  if (!hasExplicitTimezone(value)) {
    return "remind_at_missing_timezone";
  }
  const instant = parseCalendarInstantMicros(value);
  if (instant === null) {
    return "remind_at_invalid_format";
  }
  if (instant <= nowMicros) {
    return "remind_at_before_now";
  }
  if (instant - nowMicros > MAX_HORIZON_MICROS) {
    return "remind_at_horizon_too_long";
  }
  return null;
}

export function validateReminderFields(
  input: unknown,
  options: ReminderValidationOptions = {},
): ReminderValidationResult {
  if (!isPlainObject(input) || !hasOnlyKeys(input, REMINDER_FIELD_KEYS)) {
    return Object.freeze({
      ok: false,
      code: "title_required" as const,
      field: "title" as const,
    });
  }
  if (
    typeof input.title !== "string" ||
    typeof input.remindAt !== "string" ||
    typeof input.notes !== "string" ||
    typeof input.listName !== "string"
  ) {
    return Object.freeze({
      ok: false,
      code: "title_required" as const,
      field: "title" as const,
    });
  }

  if (
    hasReminderUnicodeFormatChars(input.title) ||
    hasDisallowedAsciiControls(input.title, false)
  ) {
    return Object.freeze({
      ok: false,
      code: "title_invalid_controls" as const,
      field: "title" as const,
    });
  }
  if (isBlankReminderTitle(input.title)) {
    return Object.freeze({
      ok: false,
      code: "title_required" as const,
      field: "title" as const,
    });
  }
  if (reminderCodePointLength(input.title) > REMINDER_TITLE_MAX) {
    return Object.freeze({
      ok: false,
      code: "title_too_long" as const,
      field: "title" as const,
    });
  }

  const nowMicros = options.nowMicros ?? reminderNowMicros();
  const remindAtError = validateRemindAtField(input.remindAt, nowMicros);
  if (remindAtError) {
    return Object.freeze({
      ok: false,
      code: remindAtError,
      field: "remindAt" as const,
    });
  }

  const notesError = validateOptionalTextField(
    input.notes,
    REMINDER_NOTES_MAX,
    "notes_too_long",
    "notes_invalid_controls",
    true,
  );
  if (notesError) {
    return Object.freeze({
      ok: false,
      code: notesError,
      field: "notes" as const,
    });
  }

  const listNameError = validateOptionalTextField(
    input.listName,
    REMINDER_LIST_NAME_MAX,
    "list_name_too_long",
    "list_name_invalid_controls",
    false,
  );
  if (listNameError) {
    return Object.freeze({
      ok: false,
      code: listNameError,
      field: "listName" as const,
    });
  }

  const fields: {
    title: string;
    remindAt: string;
    notes?: string;
    listName?: string;
  } = {
    title: input.title,
    remindAt: input.remindAt,
  };
  if (input.notes.length > 0) {
    fields.notes = input.notes;
  }
  if (input.listName.length > 0) {
    fields.listName = input.listName;
  }
  return Object.freeze({
    ok: true,
    fields: Object.freeze(fields),
  });
}

function freezeClientState(
  state: ReminderProposalClientState,
): ReminderProposalClientState {
  return Object.freeze({
    ...state,
    fields: Object.freeze({ ...state.fields }),
  });
}

export function reduceReminderProposalClientState(
  state: ReminderProposalClientState,
  event: ReminderProposalClientEvent,
): ReminderProposalClientState {
  switch (event.type) {
    case "fields_changed":
      return freezeClientState({
        ...state,
        fields: event.fields,
        validationCode: undefined,
        validationField: undefined,
      });
    case "validation_failed":
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
      return createInitialReminderProposalClientState();
    default:
      return state;
  }
}
