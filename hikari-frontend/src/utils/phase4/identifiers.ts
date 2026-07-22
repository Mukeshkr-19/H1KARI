/**
 * Pure Phase 4 identifier validation utilities.
 *
 * Implements strict string format rules for canonical request/challenge/handoff IDs,
 * opaque device/transfer IDs, and summary text. Performs no side effects or logging.
 */

const CANONICAL_ID_REGEX = /^[a-z0-9][a-z0-9_.-]{0,79}$/;
const OPAQUE_ID_REGEX = /^[A-Za-z0-9._:-]+$/;
const CF_CATEGORY_REGEX = /\p{Cf}/u;
const UNICODE_WHITESPACE_REGEX = /^\p{White_Space}*$/u;

/**
 * Validates a canonical request, challenge, or handoff ID.
 * Bound: 1-80 code points, matching ^[a-z0-9][a-z0-9_.-]{0,79}$
 */
export function isValidCanonicalId(id: unknown): id is string {
  if (typeof id !== "string" || id.length === 0 || id.length > 80) {
    return false;
  }
  return CANONICAL_ID_REGEX.test(id);
}

/**
 * Validates an opaque device or transfer ID.
 * Bound: 1-128 characters, matching ^[A-Za-z0-9._:-]+$
 */
export function isValidOpaqueId(id: unknown): id is string {
  if (typeof id !== "string" || id.length === 0 || id.length > 128) {
    return false;
  }
  return OPAQUE_ID_REGEX.test(id);
}

/**
 * Validates summary text.
 * Bound: 1-200 Unicode code points, non-blank, no ASCII controls or Unicode Cf characters.
 */
export function isValidSummaryText(text: unknown): text is string {
  if (typeof text !== "string") {
    return false;
  }

  // Count Unicode code points correctly using Array.from
  const codePoints = Array.from(text);
  if (codePoints.length === 0 || codePoints.length > 200) {
    return false;
  }

  // Reject blank / whitespace-only string
  if (UNICODE_WHITESPACE_REGEX.test(text)) {
    return false;
  }

  // Reject ASCII control characters (< 32, 127) and Unicode format (Cf) characters
  for (const char of codePoints) {
    const code = char.codePointAt(0);
    if (code !== undefined) {
      if (code < 32 || code === 127) {
        return false;
      }
    }
    if (CF_CATEGORY_REGEX.test(char)) {
      return false;
    }
  }

  return true;
}

export function isValidDeviceLabel(label: unknown): label is string {
  if (typeof label !== "string") {
    return false;
  }
  const codePoints = Array.from(label);
  if (codePoints.length < 1 || codePoints.length > 64) {
    return false;
  }
  for (const char of codePoints) {
    const code = char.codePointAt(0);
    if ((code !== undefined && (code < 32 || code === 127)) || CF_CATEGORY_REGEX.test(char)) {
      return false;
    }
  }
  return true;
}

/**
 * Generates a valid canonical request ID with the specified prefix.
 */
export function createCanonicalRequestId(prefix: string = "req"): string {
  const cleanPrefix = isValidCanonicalId(prefix) ? prefix : "req";
  const ts = Date.now().toString(36);
  const random = new Uint32Array(2);
  globalThis.crypto.getRandomValues(random);
  const rand = `${random[0].toString(36)}${random[1].toString(36)}`.slice(0, 12);
  const id = `${cleanPrefix}-${ts}-${rand}`;
  return isValidCanonicalId(id) ? id : `req-${ts}-${rand}`;
}
