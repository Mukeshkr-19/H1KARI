/** Pure Phase 3 productivity protocol parser and encoders. No transport or effects. */

import {
  boundPreviewLabel,
  boundPreviewValue,
  isProductivityPreviewErrorCode,
  type ProductivityPreviewErrorCode,
} from "./actionPreview";
import {
  PREVIEW_ENTRY_MAX,
  isValidProposalId,
  type ProposalLifecycleStatus,
} from "./actionLifecycle";
import {
  approvalDurationSeconds,
  isApprovalScopeConfirmReady,
  parseAllowedApprovalScopes,
  type ApprovalDurationSeconds,
  type ApprovalScopeKind,
  type ApprovalScopeState,
} from "./approvalScopes";
import {
  isValidEmailDraftRequestId,
  validateEmailDraftFields,
  type EmailDraftFields,
} from "./emailDraftProposal";
import {
  validateCalendarDraftFields,
  validateCalendarReadFields,
  type CalendarDraftFields,
  type CalendarReadFields,
} from "./calendarProposal";
import {
  isValidResearchRequestId,
  RESEARCH_MAX_RESULTS_DEFAULT,
  validateResearchFields,
} from "./researchProposal";
import {
  validateReminderFields,
} from "./reminderProposal";

export const PRODUCTIVITY_ACTIONS = [
  "browser.research",
  "email.draft",
  "calendar.read",
  "calendar.draft",
  "reminder.create",
  "scheduled_job.manage",
  "skill.execute",
  "mcp.execute",
] as const;

export type ProductivityAction = (typeof PRODUCTIVITY_ACTIONS)[number];

export type ProductivityUpdateStatus = Exclude<ProposalLifecycleStatus, "idle">;

export type ProductivityProtocolEntry = Readonly<{
  label: string;
  value: string;
  truncated?: boolean;
}>;

export type ProductivityConfirmationRequired = Readonly<{
  type: "productivity_confirmation_required";
  proposal_id: string;
  action: ProductivityAction;
  heading: string;
  risk_label: string;
  targets: ReadonlyArray<ProductivityProtocolEntry>;
  payload: ReadonlyArray<ProductivityProtocolEntry>;
  expires_at: number;
  allowed_scopes: ReadonlyArray<ApprovalScopeKind>;
  request_id?: string;
}>;

export type ProductivityUpdate = Readonly<{
  type: "productivity_update";
  proposal_id: string;
  status: ProductivityUpdateStatus;
}>;

export type ProductivityErrorMessage = Readonly<{
  type: "productivity_error";
  proposal_id: string;
  code: ProductivityPreviewErrorCode;
  request_id?: string;
}>;

export type ProductivityResearchResultItem = Readonly<{
  title: string;
  url: string;
  domain: string;
  snippet?: string;
}>;

export type ProductivityResearchResult = Readonly<{
  type: "productivity_research_result";
  proposal_id: string;
  items: ReadonlyArray<ProductivityResearchResultItem>;
}>;

export type ProductivityCalendarResultEvent = Readonly<{
  title: string;
  start: string;
  end: string;
  calendar: string;
  location?: string;
}>;

export type ProductivityCalendarResult = Readonly<{
  type: "productivity_calendar_result";
  proposal_id: string;
  events: ReadonlyArray<ProductivityCalendarResultEvent>;
}>;

export type ProductivityServerMessage =
  | ProductivityConfirmationRequired
  | ProductivityUpdate
  | ProductivityErrorMessage
  | ProductivityResearchResult
  | ProductivityCalendarResult;

export type ProductivityConfirmRequest =
  | Readonly<{
      type: "productivity_confirm";
      proposal_id: string;
      scope: "once" | "session";
    }>
  | Readonly<{
      type: "productivity_confirm";
      proposal_id: string;
      scope: "duration";
      duration_seconds: ApprovalDurationSeconds;
    }>
  | Readonly<{
      type: "productivity_confirm";
      proposal_id: string;
      scope: "precise_persistent";
      acknowledged: true;
    }>;

export type ProductivityCancelRequest = Readonly<{
  type: "productivity_cancel";
  proposal_id: string;
}>;

export type ProductivityStatusRequest = Readonly<{
  type: "productivity_status";
  proposal_id: string;
}>;

export type ProductivityEmailDraftPrepareRequest = Readonly<{
  type: "productivity_email_draft_prepare";
  request_id: string;
  recipient: string;
  subject: string;
  body: string;
}>;

export type ProductivityCalendarReadPrepareRequest = Readonly<{
  type: "productivity_calendar_read_prepare";
  request_id: string;
  start: string;
  end: string;
  calendar_name?: string;
}>;

export type ProductivityCalendarDraftPrepareRequest = Readonly<{
  type: "productivity_calendar_draft_prepare";
  request_id: string;
  title: string;
  start: string;
  end: string;
  calendar_name: string;
  location?: string;
  notes?: string;
}>;

export type ProductivityResearchPrepareRequest = Readonly<{
  type: "productivity_research_prepare";
  request_id: string;
  query: string;
  domains?: ReadonlyArray<string>;
  max_results?: number;
}>;

export type ProductivityReminderPrepareRequest = Readonly<{
  type: "productivity_reminder_prepare";
  request_id: string;
  title: string;
  remind_at: string;
  notes?: string;
  list_name?: string;
}>;

const PRODUCTIVITY_ACTION_SET = new Set<string>(PRODUCTIVITY_ACTIONS);

const UPDATE_STATUSES = new Set<string>([
  "preview",
  "confirming",
  "approved",
  "executing",
  "completed",
  "failed",
  "cancelling",
  "cancelled",
]);

const CONFIRMATION_KEYS = new Set([
  "type",
  "proposal_id",
  "action",
  "heading",
  "risk_label",
  "targets",
  "payload",
  "expires_at",
  "allowed_scopes",
  "request_id",
]);

const UPDATE_KEYS = new Set(["type", "proposal_id", "status"]);
const ERROR_KEYS = new Set(["type", "proposal_id", "code", "request_id"]);
const RESEARCH_RESULT_KEYS = new Set(["type", "proposal_id", "items"]);
const RESEARCH_ITEM_KEYS = new Set(["title", "url", "domain", "snippet"]);
const CALENDAR_RESULT_KEYS = new Set(["type", "proposal_id", "events"]);
const CALENDAR_EVENT_KEYS = new Set([
  "title",
  "start",
  "end",
  "calendar",
  "location",
]);
const ENTRY_KEYS = new Set(["label", "value", "truncated"]);
const EMAIL_DRAFT_PREPARE_KEYS = new Set([
  "type",
  "request_id",
  "recipient",
  "subject",
  "body",
]);
const CALENDAR_READ_PREPARE_KEYS = new Set([
  "type",
  "request_id",
  "start",
  "end",
  "calendar_name",
]);
const CALENDAR_DRAFT_PREPARE_KEYS = new Set([
  "type",
  "request_id",
  "title",
  "start",
  "end",
  "calendar_name",
  "location",
  "notes",
]);
const RESEARCH_PREPARE_KEYS = new Set([
  "type",
  "request_id",
  "query",
  "domains",
  "max_results",
]);
const REMINDER_PREPARE_KEYS = new Set([
  "type",
  "request_id",
  "title",
  "remind_at",
  "notes",
  "list_name",
]);

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

function isProductivityAction(value: unknown): value is ProductivityAction {
  return typeof value === "string" && PRODUCTIVITY_ACTION_SET.has(value);
}

function isUpdateStatus(value: unknown): value is ProductivityUpdateStatus {
  return typeof value === "string" && UPDATE_STATUSES.has(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseEntry(value: unknown): ProductivityProtocolEntry | null {
  if (!isPlainObject(value) || !hasOnlyKeys(value, ENTRY_KEYS)) {
    return null;
  }
  if (typeof value.label !== "string" || typeof value.value !== "string") {
    return null;
  }
  if (value.truncated !== undefined && typeof value.truncated !== "boolean") {
    return null;
  }
  const label = boundPreviewLabel(value.label);
  const boundValue = boundPreviewValue(value.value);
  const truncated =
    value.truncated === true || label.truncated || boundValue.truncated;
  const entry: {
    label: string;
    value: string;
    truncated?: boolean;
  } = {
    label: label.text,
    value: boundValue.text,
  };
  if (truncated) {
    entry.truncated = true;
  }
  return Object.freeze(entry);
}

function parseEntries(
  value: unknown,
): ReadonlyArray<ProductivityProtocolEntry> | null {
  if (!Array.isArray(value) || value.length > PREVIEW_ENTRY_MAX) {
    return null;
  }
  const entries: ProductivityProtocolEntry[] = [];
  for (const item of value) {
    const entry = parseEntry(item);
    if (!entry) {
      return null;
    }
    entries.push(entry);
  }
  return Object.freeze(entries);
}

function parseOptionalRequestId(
  record: Record<string, unknown>,
): string | undefined | null {
  if (record.request_id === undefined) {
    return undefined;
  }
  if (!isValidEmailDraftRequestId(record.request_id)) {
    return null;
  }
  return record.request_id;
}

function parseConfirmationRequired(
  record: Record<string, unknown>,
): ProductivityConfirmationRequired | null {
  if (!hasOnlyKeys(record, CONFIRMATION_KEYS)) {
    return null;
  }
  if (
    !isValidProposalId(record.proposal_id) ||
    !isProductivityAction(record.action) ||
    typeof record.heading !== "string" ||
    typeof record.risk_label !== "string" ||
    !isFiniteNumber(record.expires_at)
  ) {
    return null;
  }
  const request_id = parseOptionalRequestId(record);
  if (request_id === null) {
    return null;
  }
  const targets = parseEntries(record.targets);
  const payload = parseEntries(record.payload);
  const allowed_scopes = parseAllowedApprovalScopes(record.allowed_scopes);
  if (!targets || !payload || !allowed_scopes) {
    return null;
  }
  const heading = boundPreviewLabel(record.heading);
  const risk = boundPreviewLabel(record.risk_label);
  const parsed: {
    type: "productivity_confirmation_required";
    proposal_id: string;
    action: ProductivityAction;
    heading: string;
    risk_label: string;
    targets: ReadonlyArray<ProductivityProtocolEntry>;
    payload: ReadonlyArray<ProductivityProtocolEntry>;
    expires_at: number;
    allowed_scopes: ReadonlyArray<ApprovalScopeKind>;
    request_id?: string;
  } = {
    type: "productivity_confirmation_required" as const,
    proposal_id: record.proposal_id,
    action: record.action,
    heading: heading.text,
    risk_label: risk.text,
    targets,
    payload,
    expires_at: record.expires_at,
    allowed_scopes,
  };
  if (request_id !== undefined) {
    parsed.request_id = request_id;
  }
  return Object.freeze(parsed);
}

function parseUpdate(
  record: Record<string, unknown>,
): ProductivityUpdate | null {
  if (!hasOnlyKeys(record, UPDATE_KEYS)) {
    return null;
  }
  if (!isValidProposalId(record.proposal_id) || !isUpdateStatus(record.status)) {
    return null;
  }
  return Object.freeze({
    type: "productivity_update" as const,
    proposal_id: record.proposal_id,
    status: record.status,
  });
}

function parseError(
  record: Record<string, unknown>,
): ProductivityErrorMessage | null {
  if (!hasOnlyKeys(record, ERROR_KEYS)) {
    return null;
  }
  if (
    !isValidProposalId(record.proposal_id) ||
    !isProductivityPreviewErrorCode(record.code)
  ) {
    return null;
  }
  const request_id = parseOptionalRequestId(record);
  if (request_id === null) {
    return null;
  }
  const parsed: {
    type: "productivity_error";
    proposal_id: string;
    code: ProductivityPreviewErrorCode;
    request_id?: string;
  } = {
    type: "productivity_error" as const,
    proposal_id: record.proposal_id,
    code: record.code,
  };
  if (request_id !== undefined) {
    parsed.request_id = request_id;
  }
  return Object.freeze(parsed);
}

const RESEARCH_URL_RE =
  /^https:\/\/[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+(?::443)?(?:\/[\x21-\x7E]*)?$/;
const RESEARCH_DOMAIN_RE =
  /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$/;
const CALENDAR_INSTANT_RE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$/;

function hasDisallowedControls(
  value: string,
  allowNewlineTab: boolean,
): boolean {
  for (const char of value) {
    if (allowNewlineTab && (char === "\n" || char === "\t")) {
      continue;
    }
    const code = char.charCodeAt(0);
    if (code < 32 || code === 127) {
      return true;
    }
    if (/^\p{Cf}$/u.test(char)) {
      return true;
    }
  }
  return false;
}

function parseResearchItem(
  value: unknown,
): ProductivityResearchResultItem | null {
  if (!isPlainObject(value) || !hasOnlyKeys(value, RESEARCH_ITEM_KEYS)) {
    return null;
  }
  if (
    typeof value.title !== "string" ||
    value.title.trim().length < 1 ||
    value.title.length > 500 ||
    hasDisallowedControls(value.title, false)
  ) {
    return null;
  }
  if (
    typeof value.url !== "string" ||
    value.url.length < 1 ||
    value.url.length > 2048 ||
    !RESEARCH_URL_RE.test(value.url) ||
    hasDisallowedControls(value.url, false)
  ) {
    return null;
  }
  if (
    typeof value.domain !== "string" ||
    value.domain.length < 1 ||
    value.domain.length > 253 ||
    !RESEARCH_DOMAIN_RE.test(value.domain) ||
    hasDisallowedControls(value.domain, false)
  ) {
    return null;
  }
  try {
    const host = new URL(value.url).hostname.toLowerCase();
    if (host !== value.domain) {
      return null;
    }
  } catch {
    return null;
  }
  const item: {
    title: string;
    url: string;
    domain: string;
    snippet?: string;
  } = {
    title: value.title,
    url: value.url,
    domain: value.domain,
  };
  if (value.snippet !== undefined) {
    if (
      typeof value.snippet !== "string" ||
      value.snippet.trim().length < 1 ||
      value.snippet.length > 2000 ||
      hasDisallowedControls(value.snippet, true)
    ) {
      return null;
    }
    item.snippet = value.snippet;
  }
  return Object.freeze(item);
}

function parseResearchResult(
  record: Record<string, unknown>,
): ProductivityResearchResult | null {
  if (!hasOnlyKeys(record, RESEARCH_RESULT_KEYS)) {
    return null;
  }
  if (!isValidProposalId(record.proposal_id) || !Array.isArray(record.items)) {
    return null;
  }
  if (record.items.length > 20) {
    return null;
  }
  const items: ProductivityResearchResultItem[] = [];
  for (const entry of record.items) {
    const parsed = parseResearchItem(entry);
    if (!parsed) {
      return null;
    }
    items.push(parsed);
  }
  return Object.freeze({
    type: "productivity_research_result" as const,
    proposal_id: record.proposal_id,
    items: Object.freeze(items),
  });
}

function parseCalendarEvent(
  value: unknown,
): ProductivityCalendarResultEvent | null {
  if (!isPlainObject(value) || !hasOnlyKeys(value, CALENDAR_EVENT_KEYS)) {
    return null;
  }
  if (
    typeof value.title !== "string" ||
    value.title.trim().length < 1 ||
    value.title.length > 500 ||
    hasDisallowedControls(value.title, false)
  ) {
    return null;
  }
  if (
    typeof value.start !== "string" ||
    !CALENDAR_INSTANT_RE.test(value.start) ||
    hasDisallowedControls(value.start, false)
  ) {
    return null;
  }
  if (
    typeof value.end !== "string" ||
    !CALENDAR_INSTANT_RE.test(value.end) ||
    hasDisallowedControls(value.end, false)
  ) {
    return null;
  }
  if (
    typeof value.calendar !== "string" ||
    value.calendar.trim().length < 1 ||
    value.calendar.length > 200 ||
    hasDisallowedControls(value.calendar, false)
  ) {
    return null;
  }
  const event: {
    title: string;
    start: string;
    end: string;
    calendar: string;
    location?: string;
  } = {
    title: value.title,
    start: value.start,
    end: value.end,
    calendar: value.calendar,
  };
  if (value.location !== undefined) {
    if (
      typeof value.location !== "string" ||
      value.location.length < 1 ||
      value.location.length > 500 ||
      hasDisallowedControls(value.location, true)
    ) {
      return null;
    }
    event.location = value.location;
  }
  return Object.freeze(event);
}

function parseCalendarResult(
  record: Record<string, unknown>,
): ProductivityCalendarResult | null {
  if (!hasOnlyKeys(record, CALENDAR_RESULT_KEYS)) {
    return null;
  }
  if (!isValidProposalId(record.proposal_id) || !Array.isArray(record.events)) {
    return null;
  }
  if (record.events.length > 100) {
    return null;
  }
  const events: ProductivityCalendarResultEvent[] = [];
  for (const entry of record.events) {
    const parsed = parseCalendarEvent(entry);
    if (!parsed) {
      return null;
    }
    events.push(parsed);
  }
  return Object.freeze({
    type: "productivity_calendar_result" as const,
    proposal_id: record.proposal_id,
    events: Object.freeze(events),
  });
}

export function parseProductivityServerMessage(
  input: unknown,
): ProductivityServerMessage | null {
  let value: unknown = input;
  if (typeof input === "string") {
    try {
      value = JSON.parse(input);
    } catch {
      return null;
    }
  }
  if (!isPlainObject(value) || typeof value.type !== "string") {
    return null;
  }
  switch (value.type) {
    case "productivity_confirmation_required":
      return parseConfirmationRequired(value);
    case "productivity_update":
      return parseUpdate(value);
    case "productivity_error":
      return parseError(value);
    case "productivity_research_result":
      return parseResearchResult(value);
    case "productivity_calendar_result":
      return parseCalendarResult(value);
    default:
      return null;
  }
}

export function encodeProductivityConfirm(
  proposalId: unknown,
  scopeState: ApprovalScopeState,
): ProductivityConfirmRequest | null {
  if (!isValidProposalId(proposalId)) {
    return null;
  }
  if (!isApprovalScopeConfirmReady(scopeState)) {
    return null;
  }
  switch (scopeState.scope) {
    case "once":
    case "session":
      return Object.freeze({
        type: "productivity_confirm" as const,
        proposal_id: proposalId,
        scope: scopeState.scope,
      });
    case "duration": {
      if (!scopeState.duration) {
        return null;
      }
      return Object.freeze({
        type: "productivity_confirm" as const,
        proposal_id: proposalId,
        scope: "duration" as const,
        duration_seconds: approvalDurationSeconds(scopeState.duration),
      });
    }
    case "precise_persistent":
      return Object.freeze({
        type: "productivity_confirm" as const,
        proposal_id: proposalId,
        scope: "precise_persistent" as const,
        acknowledged: true as const,
      });
    default:
      return null;
  }
}

export function encodeProductivityCancel(
  proposalId: unknown,
): ProductivityCancelRequest | null {
  if (!isValidProposalId(proposalId)) {
    return null;
  }
  return Object.freeze({
    type: "productivity_cancel" as const,
    proposal_id: proposalId,
  });
}

export function encodeProductivityStatus(
  proposalId: unknown,
): ProductivityStatusRequest | null {
  if (!isValidProposalId(proposalId)) {
    return null;
  }
  return Object.freeze({
    type: "productivity_status" as const,
    proposal_id: proposalId,
  });
}

export function encodeProductivityEmailDraftPrepare(
  input: unknown,
): ProductivityEmailDraftPrepareRequest | null {
  if (!isPlainObject(input) || !hasOnlyKeys(input, EMAIL_DRAFT_PREPARE_KEYS)) {
    return null;
  }
  if (input.type !== "productivity_email_draft_prepare") {
    return null;
  }
  if (!isValidEmailDraftRequestId(input.request_id)) {
    return null;
  }
  const validated = validateEmailDraftFields({
    recipient: input.recipient,
    subject: input.subject,
    body: input.body,
  });
  if (!validated.ok) {
    return null;
  }
  return Object.freeze({
    type: "productivity_email_draft_prepare" as const,
    request_id: input.request_id,
    recipient: validated.fields.recipient,
    subject: validated.fields.subject,
    body: validated.fields.body,
  });
}

export function encodeProductivityCalendarReadPrepare(
  input: unknown,
): ProductivityCalendarReadPrepareRequest | null {
  if (
    !isPlainObject(input) ||
    !hasOnlyKeys(input, CALENDAR_READ_PREPARE_KEYS)
  ) {
    return null;
  }
  if (input.type !== "productivity_calendar_read_prepare") {
    return null;
  }
  if (!isValidEmailDraftRequestId(input.request_id)) {
    return null;
  }
  const validated = validateCalendarReadFields({
    start: input.start,
    end: input.end,
    calendarName: input.calendar_name ?? "",
  });
  if (!validated.ok) {
    return null;
  }
  const encoded: {
    type: "productivity_calendar_read_prepare";
    request_id: string;
    start: string;
    end: string;
    calendar_name?: string;
  } = {
    type: "productivity_calendar_read_prepare" as const,
    request_id: input.request_id,
    start: validated.fields.start,
    end: validated.fields.end,
  };
  if (validated.fields.calendarName !== undefined) {
    encoded.calendar_name = validated.fields.calendarName;
  }
  return Object.freeze(encoded);
}

export function encodeProductivityCalendarDraftPrepare(
  input: unknown,
): ProductivityCalendarDraftPrepareRequest | null {
  if (
    !isPlainObject(input) ||
    !hasOnlyKeys(input, CALENDAR_DRAFT_PREPARE_KEYS)
  ) {
    return null;
  }
  if (input.type !== "productivity_calendar_draft_prepare") {
    return null;
  }
  if (!isValidEmailDraftRequestId(input.request_id)) {
    return null;
  }
  const validated = validateCalendarDraftFields({
    title: input.title,
    start: input.start,
    end: input.end,
    calendarName: input.calendar_name ?? "",
    location: input.location ?? "",
    notes: input.notes ?? "",
  });
  if (!validated.ok) {
    return null;
  }
  const encoded: {
    type: "productivity_calendar_draft_prepare";
    request_id: string;
    title: string;
    start: string;
    end: string;
    calendar_name: string;
    location?: string;
    notes?: string;
  } = {
    type: "productivity_calendar_draft_prepare" as const,
    request_id: input.request_id,
    title: validated.fields.title,
    start: validated.fields.start,
    end: validated.fields.end,
    calendar_name: validated.fields.calendarName,
  };
  if (validated.fields.location !== undefined) {
    encoded.location = validated.fields.location;
  }
  if (validated.fields.notes !== undefined) {
    encoded.notes = validated.fields.notes;
  }
  return Object.freeze(encoded);
}

export function encodeProductivityResearchPrepare(
  input: unknown,
): ProductivityResearchPrepareRequest | null {
  if (!isPlainObject(input) || !hasOnlyKeys(input, RESEARCH_PREPARE_KEYS)) {
    return null;
  }
  if (input.type !== "productivity_research_prepare") {
    return null;
  }
  if (!isValidResearchRequestId(input.request_id)) {
    return null;
  }
  const domains =
    input.domains === undefined
      ? ""
      : Array.isArray(input.domains)
        ? input.domains.join("\n")
        : null;
  if (domains === null) {
    return null;
  }
  const maxResults =
    input.max_results === undefined
      ? String(RESEARCH_MAX_RESULTS_DEFAULT)
      : typeof input.max_results === "number"
        ? String(input.max_results)
        : null;
  if (maxResults === null) {
    return null;
  }
  const validated = validateResearchFields({
    query: input.query,
    domainsText: domains,
    maxResults,
  });
  if (!validated.ok) {
    return null;
  }
  const encoded: {
    type: "productivity_research_prepare";
    request_id: string;
    query: string;
    domains?: ReadonlyArray<string>;
    max_results?: number;
  } = {
    type: "productivity_research_prepare" as const,
    request_id: input.request_id,
    query: validated.fields.query,
  };
  if (validated.fields.domains !== undefined) {
    encoded.domains = validated.fields.domains;
  }
  if (validated.fields.maxResults !== RESEARCH_MAX_RESULTS_DEFAULT) {
    encoded.max_results = validated.fields.maxResults;
  }
  return Object.freeze(encoded);
}

export function encodeProductivityReminderPrepare(
  input: unknown,
): ProductivityReminderPrepareRequest | null {
  if (!isPlainObject(input) || !hasOnlyKeys(input, REMINDER_PREPARE_KEYS)) {
    return null;
  }
  if (input.type !== "productivity_reminder_prepare") {
    return null;
  }
  if (!isValidEmailDraftRequestId(input.request_id)) {
    return null;
  }
  if (typeof input.title !== "string" || typeof input.remind_at !== "string") {
    return null;
  }
  if (input.notes !== undefined && typeof input.notes !== "string") {
    return null;
  }
  if (input.list_name !== undefined && typeof input.list_name !== "string") {
    return null;
  }
  const validated = validateReminderFields({
    title: input.title,
    remindAt: input.remind_at,
    notes: input.notes ?? "",
    listName: input.list_name ?? "",
  });
  if (!validated.ok) {
    return null;
  }
  const encoded: {
    type: "productivity_reminder_prepare";
    request_id: string;
    title: string;
    remind_at: string;
    notes?: string;
    list_name?: string;
  } = {
    type: "productivity_reminder_prepare" as const,
    request_id: input.request_id,
    title: validated.fields.title,
    remind_at: validated.fields.remindAt,
  };
  if (validated.fields.notes !== undefined) {
    encoded.notes = validated.fields.notes;
  }
  if (validated.fields.listName !== undefined) {
    encoded.list_name = validated.fields.listName;
  }
  return Object.freeze(encoded);
}

export type { EmailDraftFields, CalendarReadFields, CalendarDraftFields };
