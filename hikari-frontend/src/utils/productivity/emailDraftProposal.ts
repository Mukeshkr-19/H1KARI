/** Pure Phase 3 email-draft proposal helpers. No transport, storage, or timers. */

import {
  isProductivityPreviewErrorCode,
  type ProductivityPreviewErrorCode,
} from "./actionPreview";

export const EMAIL_DRAFT_RECIPIENT_MAX = 320;
export const EMAIL_DRAFT_SUBJECT_MAX = 998;
export const EMAIL_DRAFT_BODY_MAX = 20000;

/** Exact correlation ID shape: 1–80 chars, no normalization. */
export const EMAIL_DRAFT_REQUEST_ID_PATTERN =
  /^[a-z0-9][a-z0-9_.-]{0,79}$/;

/** Unicode Format (Cf) characters — reject; never strip or rewrite. */
const UNICODE_FORMAT_PATTERN = /\p{Cf}/u;

export type EmailDraftFields = Readonly<{
  recipient: string;
  subject: string;
  body: string;
}>;

export type EmailDraftFieldName = "recipient" | "subject" | "body";

export type EmailDraftValidationCode =
  | "recipient_required"
  | "recipient_too_long"
  | "recipient_invalid_format"
  | "recipient_invalid_controls"
  | "subject_too_long"
  | "subject_invalid_controls"
  | "body_too_long"
  | "body_invalid_controls";

export type EmailDraftValidationResult =
  | Readonly<{ ok: true; fields: EmailDraftFields }>
  | Readonly<{
      ok: false;
      code: EmailDraftValidationCode;
      field: EmailDraftFieldName;
    }>;

export type EmailDraftClientState = Readonly<{
  fields: EmailDraftFields;
  pending: boolean;
  requestId: string | null;
  validationCode?: EmailDraftValidationCode;
  validationField?: EmailDraftFieldName;
  prepareError?: ProductivityPreviewErrorCode;
}>;

export type EmailDraftClientEvent =
  | Readonly<{ type: "fields_changed"; fields: EmailDraftFields }>
  | Readonly<{
      type: "validation_failed";
      code: EmailDraftValidationCode;
      field: EmailDraftFieldName;
    }>
  | Readonly<{
      type: "submit_started";
      requestId: string;
      fields: EmailDraftFields;
    }>
  | Readonly<{ type: "submit_blocked_duplicate" }>
  | Readonly<{ type: "prepare_unavailable" }>
  | Readonly<{ type: "matched_confirmation"; requestId: string }>
  | Readonly<{
      type: "matched_error";
      requestId: string;
      code: ProductivityPreviewErrorCode;
    }>
  | Readonly<{ type: "protocol_rejection" }>
  | Readonly<{ type: "clear_form" }>
  | Readonly<{ type: "clear_pending" }>;

const EMAIL_DRAFT_FIELD_KEYS = new Set(["recipient", "subject", "body"]);

const EMAIL_RECIPIENT_PATTERN =
  /^[a-zA-Z0-9._%+-]{1,64}@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$/;

const VALIDATION_MESSAGES: Record<EmailDraftValidationCode, string> = {
  recipient_required: "Enter a recipient.",
  recipient_too_long: "Recipient must be 320 characters or fewer.",
  recipient_invalid_format: "Enter one complete email address.",
  recipient_invalid_controls:
    "Recipient contains characters that are not allowed.",
  subject_too_long: "Subject must be 998 characters or fewer.",
  subject_invalid_controls: "Subject contains characters that are not allowed.",
  body_too_long: "Body must be 20,000 characters or fewer.",
  body_invalid_controls: "Body contains characters that are not allowed.",
};

export function createEmptyEmailDraftFields(): EmailDraftFields {
  return Object.freeze({
    recipient: "",
    subject: "",
    body: "",
  });
}

export function createInitialEmailDraftClientState(): EmailDraftClientState {
  return Object.freeze({
    fields: createEmptyEmailDraftFields(),
    pending: false,
    requestId: null,
  });
}

export function isValidEmailDraftRequestId(value: unknown): value is string {
  return (
    typeof value === "string" && EMAIL_DRAFT_REQUEST_ID_PATTERN.test(value)
  );
}

export function createEmailDraftRequestId(): string {
  if (
    typeof globalThis.crypto !== "undefined" &&
    typeof globalThis.crypto.randomUUID === "function"
  ) {
    return `email-${globalThis.crypto.randomUUID()}`;
  }
  return `email-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 12)}`;
}

export function hasEmailDraftUnicodeFormatChars(value: string): boolean {
  return UNICODE_FORMAT_PATTERN.test(value);
}

export function emailDraftResponseMatchesRequest(
  activeRequestId: string | null,
  responseRequestId: unknown,
): boolean {
  return (
    activeRequestId !== null &&
    isValidEmailDraftRequestId(activeRequestId) &&
    responseRequestId === activeRequestId
  );
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

export function mapEmailDraftValidationMessage(code: unknown): string {
  if (
    typeof code === "string" &&
    Object.prototype.hasOwnProperty.call(VALIDATION_MESSAGES, code)
  ) {
    return VALIDATION_MESSAGES[code as EmailDraftValidationCode];
  }
  return "The email draft could not be validated.";
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

export function validateEmailDraftFields(
  input: unknown,
): EmailDraftValidationResult {
  if (!isPlainObject(input) || !hasOnlyKeys(input, EMAIL_DRAFT_FIELD_KEYS)) {
    return Object.freeze({
      ok: false,
      code: "recipient_required" as const,
      field: "recipient" as const,
    });
  }
  if (
    typeof input.recipient !== "string" ||
    typeof input.subject !== "string" ||
    typeof input.body !== "string"
  ) {
    return Object.freeze({
      ok: false,
      code: "recipient_required" as const,
      field: "recipient" as const,
    });
  }

  if (
    hasEmailDraftUnicodeFormatChars(input.recipient) ||
    hasDisallowedAsciiControls(input.recipient, false)
  ) {
    return Object.freeze({
      ok: false,
      code: "recipient_invalid_controls" as const,
      field: "recipient" as const,
    });
  }
  const recipient = input.recipient.trim();
  if (recipient.length < 1) {
    return Object.freeze({
      ok: false,
      code: "recipient_required" as const,
      field: "recipient" as const,
    });
  }
  if (recipient.length > EMAIL_DRAFT_RECIPIENT_MAX) {
    return Object.freeze({
      ok: false,
      code: "recipient_too_long" as const,
      field: "recipient" as const,
    });
  }
  if (!EMAIL_RECIPIENT_PATTERN.test(recipient)) {
    return Object.freeze({
      ok: false,
      code: "recipient_invalid_format" as const,
      field: "recipient" as const,
    });
  }

  if (
    hasEmailDraftUnicodeFormatChars(input.subject) ||
    hasDisallowedAsciiControls(input.subject, false)
  ) {
    return Object.freeze({
      ok: false,
      code: "subject_invalid_controls" as const,
      field: "subject" as const,
    });
  }
  if (input.subject.length > EMAIL_DRAFT_SUBJECT_MAX) {
    return Object.freeze({
      ok: false,
      code: "subject_too_long" as const,
      field: "subject" as const,
    });
  }

  if (
    hasEmailDraftUnicodeFormatChars(input.body) ||
    hasDisallowedAsciiControls(input.body, true)
  ) {
    return Object.freeze({
      ok: false,
      code: "body_invalid_controls" as const,
      field: "body" as const,
    });
  }
  if (input.body.length > EMAIL_DRAFT_BODY_MAX) {
    return Object.freeze({
      ok: false,
      code: "body_too_long" as const,
      field: "body" as const,
    });
  }

  return Object.freeze({
    ok: true as const,
    fields: Object.freeze({
      recipient,
      subject: input.subject,
      body: input.body,
    }),
  });
}

function freezeClientState(
  state: EmailDraftClientState,
): EmailDraftClientState {
  return Object.freeze({ ...state, fields: Object.freeze({ ...state.fields }) });
}

export function reduceEmailDraftClientState(
  state: EmailDraftClientState,
  event: EmailDraftClientEvent,
): EmailDraftClientState {
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
        requestId: null,
        validationCode: event.code,
        validationField: event.field,
        prepareError: undefined,
      });
    case "submit_blocked_duplicate":
      return state;
    case "submit_started": {
      if (state.pending) {
        return state;
      }
      if (!isValidEmailDraftRequestId(event.requestId)) {
        return freezeClientState({
          ...state,
          prepareError: "unavailable",
          validationCode: undefined,
          validationField: undefined,
        });
      }
      return freezeClientState({
        fields: event.fields,
        pending: true,
        requestId: event.requestId,
        validationCode: undefined,
        validationField: undefined,
        prepareError: undefined,
      });
    }
    case "prepare_unavailable":
      return freezeClientState({
        ...state,
        pending: false,
        requestId: null,
        prepareError: "unavailable",
        validationCode: undefined,
        validationField: undefined,
      });
    case "matched_confirmation": {
      if (!emailDraftResponseMatchesRequest(state.requestId, event.requestId)) {
        return state;
      }
      return freezeClientState({
        ...state,
        pending: false,
        requestId: null,
        prepareError: undefined,
        validationCode: undefined,
        validationField: undefined,
      });
    }
    case "matched_error": {
      if (!emailDraftResponseMatchesRequest(state.requestId, event.requestId)) {
        return state;
      }
      if (!isProductivityPreviewErrorCode(event.code)) {
        return state;
      }
      return freezeClientState({
        ...state,
        pending: false,
        requestId: null,
        prepareError: event.code,
        validationCode: undefined,
        validationField: undefined,
      });
    }
    case "protocol_rejection": {
      if (!state.pending) {
        return state;
      }
      return freezeClientState({
        ...state,
        pending: false,
        requestId: null,
        prepareError: "unavailable",
        validationCode: undefined,
        validationField: undefined,
      });
    }
    case "clear_pending":
      return freezeClientState({
        ...state,
        pending: false,
        requestId: null,
        prepareError: undefined,
        validationCode: undefined,
        validationField: undefined,
      });
    case "clear_form":
      return createInitialEmailDraftClientState();
    default:
      return state;
  }
}
