/** Pure Phase 3 browser-research proposal helpers. No transport, storage, or timers. */

export const RESEARCH_QUERY_MAX = 2000;
export const RESEARCH_DOMAIN_MAX = 253;
export const RESEARCH_DOMAINS_MAX = 16;
export const RESEARCH_MAX_RESULTS_MIN = 1;
export const RESEARCH_MAX_RESULTS_MAX = 20;
export const RESEARCH_MAX_RESULTS_DEFAULT = 10;

/** Exact correlation ID shape: 1–80 chars, no normalization. */
export const RESEARCH_REQUEST_ID_PATTERN = /^[a-z0-9][a-z0-9_.-]{0,79}$/;

/** Unicode Format (Cf) characters — reject; never strip or rewrite. */
const UNICODE_FORMAT_PATTERN = /\p{Cf}/u;

/**
 * Python ``str.strip()`` blank parity for research queries.
 * Unicode White_Space includes U+0085 NEXT LINE, which JS ``trim()`` does not.
 */
const PYTHON_BLANK_QUERY_PATTERN = /^[\p{White_Space}]*$/u;

export type ResearchFields = Readonly<{
  query: string;
  domainsText: string;
  maxResults: string;
}>;

export type ResearchFieldName = "query" | "domainsText" | "maxResults";

export type ResearchValidationCode =
  | "query_required"
  | "query_blank"
  | "query_too_long"
  | "query_invalid_controls"
  | "domains_too_many"
  | "domain_too_long"
  | "domain_invalid_controls"
  | "domains_duplicate"
  | "max_results_invalid"
  | "max_results_out_of_range";

export type ValidatedResearchFields = Readonly<{
  query: string;
  domains?: ReadonlyArray<string>;
  maxResults: number;
}>;

export type ResearchValidationResult =
  | Readonly<{ ok: true; fields: ValidatedResearchFields }>
  | Readonly<{
      ok: false;
      code: ResearchValidationCode;
      field: ResearchFieldName;
    }>;

const RESEARCH_FIELD_KEYS = new Set(["query", "domainsText", "maxResults"]);

const VALIDATION_MESSAGES: Record<ResearchValidationCode, string> = {
  query_required: "Enter a research query.",
  query_blank: "Enter a research query.",
  query_too_long: "Query must be 2,000 characters or fewer.",
  query_invalid_controls: "Query contains characters that are not allowed.",
  domains_too_many: "Enter at most 16 allowed domains.",
  domain_too_long: "Each domain must be 253 characters or fewer.",
  domain_invalid_controls: "A domain contains characters that are not allowed.",
  domains_duplicate: "Remove duplicate domain entries.",
  max_results_invalid: "Maximum results must be a whole number.",
  max_results_out_of_range: "Maximum results must be between 1 and 20.",
};

export function createEmptyResearchFields(): ResearchFields {
  return Object.freeze({
    query: "",
    domainsText: "",
    maxResults: String(RESEARCH_MAX_RESULTS_DEFAULT),
  });
}

export function researchCodePointLength(value: string): number {
  return [...value].length;
}

export function isBlankResearchQuery(value: string): boolean {
  return PYTHON_BLANK_QUERY_PATTERN.test(value);
}

export function isValidResearchRequestId(value: unknown): value is string {
  return typeof value === "string" && RESEARCH_REQUEST_ID_PATTERN.test(value);
}

export function createResearchRequestId(): string {
  if (
    typeof globalThis.crypto !== "undefined" &&
    typeof globalThis.crypto.randomUUID === "function"
  ) {
    return `research-${globalThis.crypto.randomUUID()}`;
  }
  return `research-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 12)}`;
}

export function researchResponseMatchesRequest(
  activeRequestId: string | null,
  responseRequestId: unknown,
): boolean {
  return (
    activeRequestId !== null &&
    isValidResearchRequestId(activeRequestId) &&
    responseRequestId === activeRequestId
  );
}

export function hasResearchUnicodeFormatChars(value: string): boolean {
  return UNICODE_FORMAT_PATTERN.test(value);
}

function hasDisallowedAsciiControls(value: string): boolean {
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (code === 0 || (code > 0 && code < 32) || code === 127) {
      return true;
    }
  }
  return false;
}

export function parseResearchDomainsText(domainsText: string): ReadonlyArray<string> {
  const domains: string[] = [];
  for (const line of domainsText.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.length > 0) {
      domains.push(trimmed);
    }
  }
  return Object.freeze(domains);
}

export function mapResearchValidationMessage(code: unknown): string {
  if (
    typeof code === "string" &&
    Object.prototype.hasOwnProperty.call(VALIDATION_MESSAGES, code)
  ) {
    return VALIDATION_MESSAGES[code as ResearchValidationCode];
  }
  return "The research request could not be validated.";
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

function validateDomainLine(
  domain: string,
): ResearchValidationCode | null {
  if (researchCodePointLength(domain) > RESEARCH_DOMAIN_MAX) {
    return "domain_too_long";
  }
  if (
    hasDisallowedAsciiControls(domain) ||
    hasResearchUnicodeFormatChars(domain)
  ) {
    return "domain_invalid_controls";
  }
  return null;
}

export function validateResearchFields(
  input: unknown,
): ResearchValidationResult {
  if (!isPlainObject(input) || !hasOnlyKeys(input, RESEARCH_FIELD_KEYS)) {
    return Object.freeze({
      ok: false,
      code: "query_required" as const,
      field: "query" as const,
    });
  }
  if (
    typeof input.query !== "string" ||
    typeof input.domainsText !== "string" ||
    typeof input.maxResults !== "string"
  ) {
    return Object.freeze({
      ok: false,
      code: "query_required" as const,
      field: "query" as const,
    });
  }

  if (input.query.length === 0) {
    return Object.freeze({
      ok: false,
      code: "query_required" as const,
      field: "query" as const,
    });
  }
  if (isBlankResearchQuery(input.query)) {
    return Object.freeze({
      ok: false,
      code: "query_blank" as const,
      field: "query" as const,
    });
  }
  if (researchCodePointLength(input.query) > RESEARCH_QUERY_MAX) {
    return Object.freeze({
      ok: false,
      code: "query_too_long" as const,
      field: "query" as const,
    });
  }
  if (
    hasDisallowedAsciiControls(input.query) ||
    hasResearchUnicodeFormatChars(input.query)
  ) {
    return Object.freeze({
      ok: false,
      code: "query_invalid_controls" as const,
      field: "query" as const,
    });
  }

  const domains = parseResearchDomainsText(input.domainsText);
  if (domains.length > RESEARCH_DOMAINS_MAX) {
    return Object.freeze({
      ok: false,
      code: "domains_too_many" as const,
      field: "domainsText" as const,
    });
  }
  const seen = new Set<string>();
  for (const domain of domains) {
    const domainError = validateDomainLine(domain);
    if (domainError) {
      return Object.freeze({
        ok: false,
        code: domainError,
        field: "domainsText" as const,
      });
    }
    const key = domain.toLowerCase();
    if (seen.has(key)) {
      return Object.freeze({
        ok: false,
        code: "domains_duplicate" as const,
        field: "domainsText" as const,
      });
    }
    seen.add(key);
  }

  const maxResultsText = input.maxResults.trim();
  const parsedMax =
    maxResultsText.length === 0
      ? RESEARCH_MAX_RESULTS_DEFAULT
      : Number(maxResultsText);
  if (
    !Number.isInteger(parsedMax) ||
    String(parsedMax) !== maxResultsText.replace(/^\+/, "")
  ) {
    return Object.freeze({
      ok: false,
      code: "max_results_invalid" as const,
      field: "maxResults" as const,
    });
  }
  if (
    parsedMax < RESEARCH_MAX_RESULTS_MIN ||
    parsedMax > RESEARCH_MAX_RESULTS_MAX
  ) {
    return Object.freeze({
      ok: false,
      code: "max_results_out_of_range" as const,
      field: "maxResults" as const,
    });
  }

  const fields: {
    query: string;
    domains?: ReadonlyArray<string>;
    maxResults: number;
  } = {
    query: input.query,
    maxResults: parsedMax,
  };
  if (domains.length > 0) {
    fields.domains = domains;
  }
  return Object.freeze({
    ok: true,
    fields: Object.freeze(fields),
  });
}
