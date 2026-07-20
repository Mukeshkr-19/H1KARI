/** Pure one-shot scheduled-job proposal helpers. No transport or persistence. */

import {
  hasCalendarUnicodeFormatChars,
  parseCalendarInstantMicros,
} from "./calendarProposal";

export const SCHEDULE_MAX_HORIZON_SECONDS = 365 * 24 * 3600;
export const SCHEDULE_MIN_ATTEMPTS = 1;
export const SCHEDULE_MAX_ATTEMPTS = 5;
export const QUIET_HOURS_MINUTE_MAX = 1439;
export const QUIET_HOURS_TIMEZONE_MAX = 128;

const MICROS_PER_SECOND = BigInt(1_000_000);
const MAX_HORIZON_MICROS =
  BigInt(SCHEDULE_MAX_HORIZON_SECONDS) * MICROS_PER_SECOND;

export const SCHEDULE_ACTIONS = Object.freeze([
  "browser.research",
  "calendar.read",
] as const);

export type ScheduleAction = (typeof SCHEDULE_ACTIONS)[number];
export type ScheduleClock = () => bigint;

export type ScheduleProposalFields = Readonly<{
  action: ScheduleAction;
  nextRunAt: string;
  maxAttempts: string;
  quietHoursEnabled: boolean;
  quietStartMinute: string;
  quietEndMinute: string;
  quietTimezone: string;
}>;

export type ScheduleFieldName =
  | "action"
  | "nextRunAt"
  | "maxAttempts"
  | "quietHoursEnabled"
  | "quietStartMinute"
  | "quietEndMinute"
  | "quietTimezone";

export type ScheduleValidationCode =
  | "action_invalid"
  | "next_run_required"
  | "next_run_invalid_controls"
  | "next_run_missing_timezone"
  | "next_run_invalid_format"
  | "next_run_not_future"
  | "next_run_horizon_too_long"
  | "max_attempts_invalid"
  | "quiet_hours_unexpected"
  | "quiet_hours_required"
  | "quiet_hours_minute_invalid"
  | "quiet_hours_empty_window"
  | "quiet_timezone_invalid"
  | "clock_unavailable";

export type ValidatedScheduleProposal = Readonly<{
  action: ScheduleAction;
  nextRunAt: string;
  maxAttempts: number;
  quietHours?: Readonly<{
    startMinute: number;
    endMinute: number;
    timezone: string;
  }>;
}>;

export type ScheduleValidationResult =
  | Readonly<{ ok: true; fields: ValidatedScheduleProposal }>
  | Readonly<{
      ok: false;
      code: ScheduleValidationCode;
      field: ScheduleFieldName;
    }>;

export type ScheduleProposalClientState = Readonly<{
  fields: ScheduleProposalFields;
  pending: boolean;
  validationCode?: ScheduleValidationCode;
  validationField?: ScheduleFieldName;
}>;

export type ScheduleProposalClientEvent =
  | Readonly<{ type: "fields_changed"; fields: ScheduleProposalFields }>
  | Readonly<{
      type: "validation_failed";
      code: ScheduleValidationCode;
      field: ScheduleFieldName;
    }>
  | Readonly<{ type: "submit_started" }>
  | Readonly<{ type: "clear_pending" }>
  | Readonly<{ type: "clear_form" }>;

const FIELD_KEYS = new Set([
  "action",
  "nextRunAt",
  "maxAttempts",
  "quietHoursEnabled",
  "quietStartMinute",
  "quietEndMinute",
  "quietTimezone",
]);

const VALIDATION_MESSAGES: Record<ScheduleValidationCode, string> = {
  action_invalid: "Choose a supported scheduled action.",
  next_run_required: "Enter the date and time for this one-shot job.",
  next_run_invalid_controls: "The scheduled time contains invalid characters.",
  next_run_missing_timezone: "Include an explicit timezone offset or Z.",
  next_run_invalid_format: "Enter a valid ISO 8601 date and time.",
  next_run_not_future: "The scheduled time must be in the future.",
  next_run_horizon_too_long: "The scheduled time must be within 365 days.",
  max_attempts_invalid: "Maximum attempts must be an integer from 1 to 5.",
  quiet_hours_unexpected: "Clear quiet-hours values or enable quiet hours.",
  quiet_hours_required: "Complete every quiet-hours field.",
  quiet_hours_minute_invalid: "Quiet-hours minutes must be integers from 0 to 1439.",
  quiet_hours_empty_window: "Quiet-hours start and end must be different.",
  quiet_timezone_invalid: "Enter a valid IANA timezone.",
  clock_unavailable: "The scheduled time could not be validated.",
};

export function createEmptyScheduleProposalFields(): ScheduleProposalFields {
  return Object.freeze({
    action: "browser.research",
    nextRunAt: "",
    maxAttempts: "1",
    quietHoursEnabled: false,
    quietStartMinute: "",
    quietEndMinute: "",
    quietTimezone: "",
  });
}

export function createScheduleRequestId(): string | null {
  try {
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return `schedule-${Array.from(bytes, (value) =>
      value.toString(16).padStart(2, "0"),
    ).join("")}`;
  } catch {
    return null;
  }
}

export function createInitialScheduleProposalClientState(): ScheduleProposalClientState {
  return Object.freeze({
    fields: createEmptyScheduleProposalFields(),
    pending: false,
  });
}

export function mapScheduleValidationMessage(code: unknown): string {
  if (
    typeof code === "string" &&
    Object.prototype.hasOwnProperty.call(VALIDATION_MESSAGES, code)
  ) {
    return VALIDATION_MESSAGES[code as ScheduleValidationCode];
  }
  return "The scheduled-job proposal could not be validated.";
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasOnlyKnownFields(value: Record<string, unknown>): boolean {
  return Object.keys(value).every((key) => FIELD_KEYS.has(key));
}

function hasAsciiControls(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 32 || code === 127) {
      return true;
    }
  }
  return false;
}

function hasUnsafeStructuralText(value: string): boolean {
  return hasAsciiControls(value) || hasCalendarUnicodeFormatChars(value);
}

function hasExplicitTimezone(value: string): boolean {
  return /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
}

function parseBoundedInteger(
  value: string,
  minimum: number,
  maximum: number,
): number | null {
  if (!/^(?:0|[1-9]\d*)$/.test(value)) {
    return null;
  }
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= minimum && parsed <= maximum
    ? parsed
    : null;
}

export function isValidScheduleTimezone(value: string): boolean {
  if (
    value.length < 1 ||
    value.length > QUIET_HOURS_TIMEZONE_MAX ||
    hasUnsafeStructuralText(value) ||
    !/^(?:UTC|[A-Za-z0-9_+.-]+(?:\/[A-Za-z0-9_+.-]+)+)$/.test(value)
  ) {
    return false;
  }
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value }).format(0);
    return true;
  } catch {
    return false;
  }
}

function failure(
  code: ScheduleValidationCode,
  field: ScheduleFieldName,
): ScheduleValidationResult {
  return Object.freeze({ ok: false, code, field });
}

export function validateScheduleProposalFields(
  input: unknown,
  clock: ScheduleClock,
): ScheduleValidationResult {
  if (!isPlainObject(input) || !hasOnlyKnownFields(input)) {
    return failure("action_invalid", "action");
  }
  if (
    typeof input.action !== "string" ||
    typeof input.nextRunAt !== "string" ||
    typeof input.maxAttempts !== "string" ||
    typeof input.quietHoursEnabled !== "boolean" ||
    typeof input.quietStartMinute !== "string" ||
    typeof input.quietEndMinute !== "string" ||
    typeof input.quietTimezone !== "string"
  ) {
    return failure("action_invalid", "action");
  }
  if (!SCHEDULE_ACTIONS.includes(input.action as ScheduleAction)) {
    return failure("action_invalid", "action");
  }

  if (input.nextRunAt.length < 1) {
    return failure("next_run_required", "nextRunAt");
  }
  if (hasUnsafeStructuralText(input.nextRunAt)) {
    return failure("next_run_invalid_controls", "nextRunAt");
  }
  if (!hasExplicitTimezone(input.nextRunAt)) {
    return failure("next_run_missing_timezone", "nextRunAt");
  }
  const nextRunMicros = parseCalendarInstantMicros(input.nextRunAt);
  if (nextRunMicros === null) {
    return failure("next_run_invalid_format", "nextRunAt");
  }

  let nowMicros: bigint;
  try {
    nowMicros = clock();
  } catch {
    return failure("clock_unavailable", "nextRunAt");
  }
  if (typeof nowMicros !== "bigint") {
    return failure("clock_unavailable", "nextRunAt");
  }
  if (nextRunMicros <= nowMicros) {
    return failure("next_run_not_future", "nextRunAt");
  }
  if (nextRunMicros - nowMicros > MAX_HORIZON_MICROS) {
    return failure("next_run_horizon_too_long", "nextRunAt");
  }

  const maxAttempts = parseBoundedInteger(
    input.maxAttempts,
    SCHEDULE_MIN_ATTEMPTS,
    SCHEDULE_MAX_ATTEMPTS,
  );
  if (maxAttempts === null) {
    return failure("max_attempts_invalid", "maxAttempts");
  }

  let quietHours: ValidatedScheduleProposal["quietHours"];
  if (!input.quietHoursEnabled) {
    if (
      input.quietStartMinute.length > 0 ||
      input.quietEndMinute.length > 0 ||
      input.quietTimezone.length > 0
    ) {
      return failure("quiet_hours_unexpected", "quietHoursEnabled");
    }
  } else {
    if (
      input.quietStartMinute.length < 1 ||
      input.quietEndMinute.length < 1 ||
      input.quietTimezone.length < 1
    ) {
      return failure("quiet_hours_required", "quietStartMinute");
    }
    const startMinute = parseBoundedInteger(
      input.quietStartMinute,
      0,
      QUIET_HOURS_MINUTE_MAX,
    );
    const endMinute = parseBoundedInteger(
      input.quietEndMinute,
      0,
      QUIET_HOURS_MINUTE_MAX,
    );
    if (startMinute === null) {
      return failure("quiet_hours_minute_invalid", "quietStartMinute");
    }
    if (endMinute === null) {
      return failure("quiet_hours_minute_invalid", "quietEndMinute");
    }
    if (startMinute === endMinute) {
      return failure("quiet_hours_empty_window", "quietEndMinute");
    }
    if (!isValidScheduleTimezone(input.quietTimezone)) {
      return failure("quiet_timezone_invalid", "quietTimezone");
    }
    quietHours = Object.freeze({
      startMinute,
      endMinute,
      timezone: input.quietTimezone,
    });
  }

  const fields: ValidatedScheduleProposal = Object.freeze({
    action: input.action as ScheduleAction,
    nextRunAt: input.nextRunAt,
    maxAttempts,
    ...(quietHours ? { quietHours } : {}),
  });
  return Object.freeze({ ok: true, fields });
}

function freezeState(
  state: ScheduleProposalClientState,
): ScheduleProposalClientState {
  return Object.freeze({
    ...state,
    fields: Object.freeze({ ...state.fields }),
  });
}

export function reduceScheduleProposalClientState(
  state: ScheduleProposalClientState,
  event: ScheduleProposalClientEvent,
): ScheduleProposalClientState {
  switch (event.type) {
    case "fields_changed":
      return freezeState({
        ...state,
        fields: event.fields,
        validationCode: undefined,
        validationField: undefined,
      });
    case "validation_failed":
      return freezeState({
        ...state,
        pending: false,
        validationCode: event.code,
        validationField: event.field,
      });
    case "submit_started":
      return state.pending
        ? state
        : freezeState({
            ...state,
            pending: true,
            validationCode: undefined,
            validationField: undefined,
          });
    case "clear_pending":
      return freezeState({
        ...state,
        pending: false,
        validationCode: undefined,
        validationField: undefined,
      });
    case "clear_form":
      return createInitialScheduleProposalClientState();
    default:
      return state;
  }
}
