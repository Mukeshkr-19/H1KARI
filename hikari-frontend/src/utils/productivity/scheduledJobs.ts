/** Pure Phase 3 scheduled-job view models. No transport, storage, timers, or effects. */

import { boundPreviewLabel } from "./actionPreview";
import { isValidProposalId } from "./actionLifecycle";

export const isValidJobId = isValidProposalId;

export const SCHEDULED_JOB_OWNERSHIP_LABEL = "Current session";
export const SCHEDULED_JOB_ATTEMPT_MAX = 100;
export const SCHEDULED_JOB_LIST_MAX = 64;

export type ScheduledJobState =
  | "scheduled"
  | "paused"
  | "running"
  | "interrupted"
  | "completed"
  | "failed"
  | "cancelled";

export type ScheduledJobControl = "pause" | "resume" | "cancel";

export type ScheduledJobView = Readonly<{
  jobId: string;
  actionLabel: string;
  state: ScheduledJobState;
  nextRunLabel: string;
  ownershipLabel: typeof SCHEDULED_JOB_OWNERSHIP_LABEL;
  quietHoursLabel?: string;
  attemptCount: number;
  maxAttempts: number;
  pendingControl: ScheduledJobControl | null;
}>;

export type ScheduledJobErrorCode =
  | "control_failed"
  | "job_not_found"
  | "unavailable";

const SCHEDULED_JOB_ERROR_MESSAGES: Record<ScheduledJobErrorCode, string> = {
  control_failed: "The job control could not be completed.",
  job_not_found: "That job is no longer available.",
  unavailable: "Scheduled jobs are temporarily unavailable.",
};

export const GENERIC_SCHEDULED_JOB_ERROR_MESSAGE =
  "The scheduled job action could not be completed.";

const JOB_STATES = new Set<string>([
  "scheduled",
  "paused",
  "running",
  "interrupted",
  "completed",
  "failed",
  "cancelled",
]);

const JOB_CONTROLS = new Set<string>(["pause", "resume", "cancel"]);

const TERMINAL_STATES = new Set<ScheduledJobState>([
  "completed",
  "failed",
  "cancelled",
]);

const JOB_KEYS = new Set([
  "jobId",
  "actionLabel",
  "state",
  "nextRunLabel",
  "quietHoursLabel",
  "attemptCount",
  "maxAttempts",
  "pendingControl",
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

function isScheduledJobState(value: unknown): value is ScheduledJobState {
  return typeof value === "string" && JOB_STATES.has(value);
}

function isScheduledJobControl(value: unknown): value is ScheduledJobControl {
  return typeof value === "string" && JOB_CONTROLS.has(value);
}

function isBoundedAttempt(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= SCHEDULED_JOB_ATTEMPT_MAX
  );
}

export function isTerminalScheduledJobState(state: ScheduledJobState): boolean {
  return TERMINAL_STATES.has(state);
}

export function availableScheduledJobControls(
  state: ScheduledJobState,
): ReadonlyArray<ScheduledJobControl> {
  switch (state) {
    case "scheduled":
      return Object.freeze(["pause", "cancel"]);
    case "paused":
      return Object.freeze(["resume", "cancel"]);
    case "running":
      return Object.freeze(["cancel"]);
    case "interrupted":
      return Object.freeze(["resume", "cancel"]);
    default:
      return Object.freeze([]);
  }
}

export function isScheduledJobControlAvailable(
  state: ScheduledJobState,
  control: ScheduledJobControl,
): boolean {
  return availableScheduledJobControls(state).includes(control);
}

export function isScheduledJobErrorCode(
  value: unknown,
): value is ScheduledJobErrorCode {
  return (
    typeof value === "string" &&
    Object.prototype.hasOwnProperty.call(SCHEDULED_JOB_ERROR_MESSAGES, value)
  );
}

export function mapScheduledJobErrorMessage(code: unknown): string {
  if (isScheduledJobErrorCode(code)) {
    return SCHEDULED_JOB_ERROR_MESSAGES[code];
  }
  return GENERIC_SCHEDULED_JOB_ERROR_MESSAGE;
}

export function parseScheduledJobView(input: unknown): ScheduledJobView | null {
  if (!isPlainObject(input) || !hasOnlyKeys(input, JOB_KEYS)) {
    return null;
  }
  if (!isValidJobId(input.jobId)) {
    return null;
  }
  if (typeof input.actionLabel !== "string") {
    return null;
  }
  if (!isScheduledJobState(input.state)) {
    return null;
  }
  if (typeof input.nextRunLabel !== "string") {
    return null;
  }
  if (
    input.quietHoursLabel !== undefined &&
    typeof input.quietHoursLabel !== "string"
  ) {
    return null;
  }
  if (!isBoundedAttempt(input.attemptCount) || !isBoundedAttempt(input.maxAttempts)) {
    return null;
  }
  if (input.maxAttempts < 1 || input.attemptCount > input.maxAttempts) {
    return null;
  }
  if (
    input.pendingControl !== undefined &&
    input.pendingControl !== null &&
    !isScheduledJobControl(input.pendingControl)
  ) {
    return null;
  }
  if (
    input.pendingControl !== undefined &&
    input.pendingControl !== null &&
    !isScheduledJobControlAvailable(input.state, input.pendingControl)
  ) {
    return null;
  }

  const action = boundPreviewLabel(input.actionLabel);
  const nextRun = boundPreviewLabel(input.nextRunLabel);
  const view: {
    jobId: string;
    actionLabel: string;
    state: ScheduledJobState;
    nextRunLabel: string;
    ownershipLabel: typeof SCHEDULED_JOB_OWNERSHIP_LABEL;
    quietHoursLabel?: string;
    attemptCount: number;
    maxAttempts: number;
    pendingControl: ScheduledJobControl | null;
  } = {
    jobId: input.jobId,
    actionLabel: action.text,
    state: input.state,
    nextRunLabel: nextRun.text,
    ownershipLabel: SCHEDULED_JOB_OWNERSHIP_LABEL,
    attemptCount: input.attemptCount,
    maxAttempts: input.maxAttempts,
    pendingControl:
      input.pendingControl === undefined ? null : input.pendingControl,
  };

  if (typeof input.quietHoursLabel === "string") {
    view.quietHoursLabel = boundPreviewLabel(input.quietHoursLabel).text;
  }

  return Object.freeze(view);
}

export function parseScheduledJobList(
  input: unknown,
): ReadonlyArray<ScheduledJobView> | null {
  if (!Array.isArray(input) || input.length > SCHEDULED_JOB_LIST_MAX) {
    return null;
  }
  const jobs: ScheduledJobView[] = [];
  const seen = new Set<string>();
  for (const item of input) {
    const job = parseScheduledJobView(item);
    if (!job) {
      return null;
    }
    if (seen.has(job.jobId)) {
      return null;
    }
    seen.add(job.jobId);
    jobs.push(job);
  }
  return Object.freeze(jobs);
}

export function setScheduledJobPendingControl(
  job: ScheduledJobView,
  jobId: string,
  control: ScheduledJobControl,
): ScheduledJobView | null {
  if (job.jobId !== jobId) {
    return null;
  }
  if (!isScheduledJobControlAvailable(job.state, control)) {
    return null;
  }
  if (job.pendingControl !== null) {
    return null;
  }
  return Object.freeze({
    ...job,
    pendingControl: control,
  });
}

export function clearScheduledJobPendingControl(
  job: ScheduledJobView,
  jobId: string,
): ScheduledJobView | null {
  if (job.jobId !== jobId) {
    return null;
  }
  if (job.pendingControl === null) {
    return job;
  }
  return Object.freeze({
    ...job,
    pendingControl: null,
  });
}

export function updateScheduledJobState(
  job: ScheduledJobView,
  jobId: string,
  state: ScheduledJobState,
): ScheduledJobView | null {
  if (job.jobId !== jobId) {
    return null;
  }
  if (!isScheduledJobState(state)) {
    return null;
  }
  return Object.freeze({
    ...job,
    state,
    pendingControl: null,
  });
}

export function replaceScheduledJobInList(
  jobs: ReadonlyArray<ScheduledJobView>,
  next: ScheduledJobView,
): ReadonlyArray<ScheduledJobView> | null {
  const index = jobs.findIndex((job) => job.jobId === next.jobId);
  if (index < 0) {
    return null;
  }
  const copy = jobs.slice();
  copy[index] = next;
  return Object.freeze(copy);
}
