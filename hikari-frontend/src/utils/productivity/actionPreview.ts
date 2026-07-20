/** Pure preview text helpers. Untrusted proposal text must be sanitized and bounded. */

export const PREVIEW_LABEL_MAX = 120;
export const PREVIEW_VALUE_MAX = 2000;

export type PreviewEntry = {
  label: string;
  value: string;
  truncated?: boolean;
};

export type ProductivityPreviewErrorCode =
  | "confirm_failed"
  | "cancel_failed"
  | "proposal_expired"
  | "proposal_invalid"
  | "unavailable";

export const GENERIC_PREVIEW_ERROR_MESSAGE =
  "The action could not be completed.";

const PREVIEW_ERROR_MESSAGES: Record<ProductivityPreviewErrorCode, string> = {
  confirm_failed: "The action could not be confirmed.",
  cancel_failed: "The action could not be cancelled.",
  proposal_expired: "This proposal has expired. Request a new preview.",
  proposal_invalid: "This proposal is no longer valid.",
  unavailable: "The action is temporarily unavailable.",
};

/** ASCII controls except TAB and LF, plus Unicode bidi/format controls. */
const UNSAFE_CONTROL_PATTERN =
  /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u061C\u200E-\u200F\u202A-\u202E\u2066-\u2069\uFEFF]/g;

export function sanitizePreviewText(raw: string): string {
  return raw.replace(UNSAFE_CONTROL_PATTERN, "");
}

export function boundPreviewLabel(raw: string): { text: string; truncated: boolean } {
  const cleaned = sanitizePreviewText(raw).trim();
  if (cleaned.length <= PREVIEW_LABEL_MAX) {
    return { text: cleaned, truncated: false };
  }
  return {
    text: `${cleaned.slice(0, PREVIEW_LABEL_MAX)}…`,
    truncated: true,
  };
}

export function boundPreviewValue(raw: string): { text: string; truncated: boolean } {
  const cleaned = sanitizePreviewText(raw);
  if (cleaned.length <= PREVIEW_VALUE_MAX) {
    return { text: cleaned, truncated: false };
  }
  return {
    text: `${cleaned.slice(0, PREVIEW_VALUE_MAX)}…`,
    truncated: true,
  };
}

export function isProductivityPreviewErrorCode(
  value: unknown,
): value is ProductivityPreviewErrorCode {
  return (
    typeof value === "string" &&
    Object.prototype.hasOwnProperty.call(PREVIEW_ERROR_MESSAGES, value)
  );
}

export function mapPreviewErrorMessage(code: unknown): string {
  if (isProductivityPreviewErrorCode(code)) {
    return PREVIEW_ERROR_MESSAGES[code];
  }
  return GENERIC_PREVIEW_ERROR_MESSAGE;
}
