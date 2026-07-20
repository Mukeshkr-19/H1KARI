/** Pure Phase 3 scheduled-jobs protocol parser and encoders. No transport or effects. */

import {
  SCHEDULED_JOB_LIST_MAX,
  isScheduledJobErrorCode,
  isValidJobId,
  parseScheduledJobList,
  parseScheduledJobView,
  type ScheduledJobErrorCode,
  type ScheduledJobView,
} from "./scheduledJobs";
import { isValidProposalId } from "./actionLifecycle";
import {
  parseProductivityServerMessage,
  type ProductivityCalendarResultEvent,
  type ProductivityResearchResultItem,
} from "./productivityProtocol";
import {
  validateScheduleProposalFields,
  type ScheduleClock,
  type ScheduleProposalFields,
} from "./scheduleProposal";

export type ScheduledJobsListMessage = Readonly<{
  type: "scheduled_jobs";
  jobs: ReadonlyArray<ScheduledJobView>;
}>;

export type ScheduledJobUpdateMessage = Readonly<{
  type: "scheduled_job_update";
  job: ScheduledJobView;
  request_id?: string;
}>;

export type ScheduledJobErrorMessage = Readonly<{
  type: "scheduled_job_error";
  job_id: string;
  code: ScheduledJobErrorCode;
  request_id?: string;
}>;

export type ScheduledJobResearchResultMessage = Readonly<{
  type: "scheduled_job_research_result";
  job_id: string;
  items: ReadonlyArray<ProductivityResearchResultItem>;
}>;

export type ScheduledJobCalendarResultMessage = Readonly<{
  type: "scheduled_job_calendar_result";
  job_id: string;
  events: ReadonlyArray<ProductivityCalendarResultEvent>;
}>;

export type ScheduledJobsServerMessage =
  | ScheduledJobsListMessage
  | ScheduledJobUpdateMessage
  | ScheduledJobErrorMessage
  | ScheduledJobResearchResultMessage
  | ScheduledJobCalendarResultMessage;

export type ScheduledJobCreateRequest = Readonly<{
  type: "scheduled_job_create";
  request_id: string;
  proposal_id: string;
  next_run_at: string;
  max_attempts: number;
  quiet_hours?: Readonly<{
    start_minute: number;
    end_minute: number;
    timezone: string;
  }>;
}>;

export type ScheduledJobsListRequest = Readonly<{
  type: "scheduled_jobs_list";
}>;

export type ScheduledJobPauseRequest = Readonly<{
  type: "scheduled_job_pause";
  job_id: string;
}>;

export type ScheduledJobResumeRequest = Readonly<{
  type: "scheduled_job_resume";
  job_id: string;
}>;

export type ScheduledJobCancelRequest = Readonly<{
  type: "scheduled_job_cancel";
  job_id: string;
}>;

const LIST_KEYS = new Set(["type", "jobs"]);
const UPDATE_KEYS = new Set(["type", "job"]);
UPDATE_KEYS.add("request_id");
const ERROR_KEYS = new Set(["type", "job_id", "code"]);
ERROR_KEYS.add("request_id");
const RESULT_KEYS = new Set(["type", "job_id", "items", "events"]);
const WIRE_JOB_KEYS = new Set([
  "job_id",
  "action",
  "state",
  "next_run_at",
  "quiet_hours_label",
  "attempt_count",
  "max_attempts",
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

function formatNextRunAt(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  if (typeof value === "string") {
    return value;
  }
  return null;
}

function translateWireJob(input: unknown): unknown {
  if (!isPlainObject(input) || !hasOnlyKeys(input, WIRE_JOB_KEYS)) {
    return null;
  }
  const nextRunLabel = formatNextRunAt(input.next_run_at);
  if (nextRunLabel === null) {
    return null;
  }
  if (typeof input.action !== "string") {
    return null;
  }
  const camel: {
    jobId: unknown;
    actionLabel: string;
    state: unknown;
    nextRunLabel: string;
    quietHoursLabel?: unknown;
    attemptCount: unknown;
    maxAttempts: unknown;
    pendingControl: null;
  } = {
    jobId: input.job_id,
    actionLabel: input.action,
    state: input.state,
    nextRunLabel,
    attemptCount: input.attempt_count,
    maxAttempts: input.max_attempts,
    pendingControl: null,
  };
  if (input.quiet_hours_label !== undefined) {
    camel.quietHoursLabel = input.quiet_hours_label;
  }
  return camel;
}

function parseWireJob(input: unknown): ScheduledJobView | null {
  return parseScheduledJobView(translateWireJob(input));
}

function parseWireJobList(
  input: unknown,
): ReadonlyArray<ScheduledJobView> | null {
  if (!Array.isArray(input) || input.length > SCHEDULED_JOB_LIST_MAX) {
    return null;
  }
  const translated: unknown[] = [];
  for (const item of input) {
    const camel = translateWireJob(item);
    if (!camel) {
      return null;
    }
    translated.push(camel);
  }
  return parseScheduledJobList(translated);
}

function parseJobsList(
  record: Record<string, unknown>,
): ScheduledJobsListMessage | null {
  if (!hasOnlyKeys(record, LIST_KEYS)) {
    return null;
  }
  const jobs = parseWireJobList(record.jobs);
  if (!jobs) {
    return null;
  }
  return Object.freeze({
    type: "scheduled_jobs" as const,
    jobs,
  });
}

function parseJobUpdate(
  record: Record<string, unknown>,
): ScheduledJobUpdateMessage | null {
  if (!hasOnlyKeys(record, UPDATE_KEYS)) {
    return null;
  }
  const job = parseWireJob(record.job);
  if (!job) {
    return null;
  }
  if (record.request_id !== undefined && !isValidProposalId(record.request_id)) {
    return null;
  }
  return Object.freeze({
    type: "scheduled_job_update" as const,
    job,
    ...(record.request_id === undefined ? {} : { request_id: record.request_id }),
  });
}

function parseJobError(
  record: Record<string, unknown>,
): ScheduledJobErrorMessage | null {
  if (!hasOnlyKeys(record, ERROR_KEYS)) {
    return null;
  }
  if (!isValidJobId(record.job_id) || !isScheduledJobErrorCode(record.code)) {
    return null;
  }
  if (record.request_id !== undefined && !isValidProposalId(record.request_id)) {
    return null;
  }
  return Object.freeze({
    type: "scheduled_job_error" as const,
    job_id: record.job_id,
    code: record.code,
    ...(record.request_id === undefined ? {} : { request_id: record.request_id }),
  });
}

function parseScheduledResult(
  record: Record<string, unknown>,
): ScheduledJobResearchResultMessage | ScheduledJobCalendarResultMessage | null {
  if (!hasOnlyKeys(record, RESULT_KEYS) || !isValidJobId(record.job_id)) {
    return null;
  }
  const isResearch = record.type === "scheduled_job_research_result";
  const isCalendar = record.type === "scheduled_job_calendar_result";
  if (!isResearch && !isCalendar) {
    return null;
  }
  if ((isResearch && record.events !== undefined) || (isCalendar && record.items !== undefined)) {
    return null;
  }
  const parsed = parseProductivityServerMessage({
    type: isResearch
      ? "productivity_research_result"
      : "productivity_calendar_result",
    proposal_id: record.job_id,
    ...(isResearch ? { items: record.items } : { events: record.events }),
  });
  if (!parsed) {
    return null;
  }
  return isResearch && parsed.type === "productivity_research_result"
    ? Object.freeze({
        type: "scheduled_job_research_result" as const,
        job_id: record.job_id,
        items: parsed.items,
      })
    : isCalendar && parsed.type === "productivity_calendar_result"
      ? Object.freeze({
          type: "scheduled_job_calendar_result" as const,
          job_id: record.job_id,
          events: parsed.events,
        })
      : null;
}

export function parseScheduledJobsServerMessage(
  input: unknown,
): ScheduledJobsServerMessage | null {
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
    case "scheduled_jobs":
      return parseJobsList(value);
    case "scheduled_job_update":
      return parseJobUpdate(value);
    case "scheduled_job_error":
      return parseJobError(value);
    case "scheduled_job_research_result":
    case "scheduled_job_calendar_result":
      return parseScheduledResult(value);
    default:
      return null;
  }
}

export function encodeScheduledJobCreate(
  requestId: unknown,
  proposalId: unknown,
  fields: ScheduleProposalFields,
  clock: ScheduleClock,
): ScheduledJobCreateRequest | null {
  if (!isValidProposalId(requestId) || !isValidProposalId(proposalId)) {
    return null;
  }
  const validated = validateScheduleProposalFields(fields, clock);
  if (!validated.ok) {
    return null;
  }
  const result: {
    type: "scheduled_job_create";
    request_id: string;
    proposal_id: string;
    next_run_at: string;
    max_attempts: number;
    quiet_hours?: {
      start_minute: number;
      end_minute: number;
      timezone: string;
    };
  } = {
    type: "scheduled_job_create",
    request_id: requestId,
    proposal_id: proposalId,
    next_run_at: validated.fields.nextRunAt,
    max_attempts: validated.fields.maxAttempts,
  };
  if (validated.fields.quietHours) {
    result.quiet_hours = {
      start_minute: validated.fields.quietHours.startMinute,
      end_minute: validated.fields.quietHours.endMinute,
      timezone: validated.fields.quietHours.timezone,
    };
  }
  return Object.freeze(result);
}

export function encodeScheduledJobsList(): ScheduledJobsListRequest {
  return Object.freeze({
    type: "scheduled_jobs_list" as const,
  });
}

export function encodeScheduledJobPause(
  jobId: unknown,
): ScheduledJobPauseRequest | null {
  if (!isValidJobId(jobId)) {
    return null;
  }
  return Object.freeze({
    type: "scheduled_job_pause" as const,
    job_id: jobId,
  });
}

export function encodeScheduledJobResume(
  jobId: unknown,
): ScheduledJobResumeRequest | null {
  if (!isValidJobId(jobId)) {
    return null;
  }
  return Object.freeze({
    type: "scheduled_job_resume" as const,
    job_id: jobId,
  });
}

export function encodeScheduledJobCancel(
  jobId: unknown,
): ScheduledJobCancelRequest | null {
  if (!isValidJobId(jobId)) {
    return null;
  }
  return Object.freeze({
    type: "scheduled_job_cancel" as const,
    job_id: jobId,
  });
}
