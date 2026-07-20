/** Pure Phase 3 proposal lifecycle reducer. No transport, storage, timers, or effects. */

import {
  boundPreviewLabel,
  boundPreviewValue,
  isProductivityPreviewErrorCode,
  type ProductivityPreviewErrorCode,
} from "./actionPreview";

/** Exact correlation ID shape: 1–80 chars, no normalization. */
export const PROPOSAL_ID_PATTERN = /^[a-z0-9][a-z0-9_.-]{0,79}$/;

/** Conservative cap on destination and payload preview rows. */
export const PREVIEW_ENTRY_MAX = 32;

export type ProposalLifecycleStatus =
  | "idle"
  | "preview"
  | "confirming"
  | "approved"
  | "executing"
  | "completed"
  | "failed"
  | "cancelling"
  | "cancelled";

export type ProposalLifecycleEntry = Readonly<{
  label: string;
  value: string;
  truncated?: boolean;
}>;

export type ProposalLifecycleSnapshot = Readonly<{
  proposalId: string;
  heading: string;
  actionLabel: string;
  riskLabel: string;
  targets: ReadonlyArray<ProposalLifecycleEntry>;
  payload: ReadonlyArray<ProposalLifecycleEntry>;
  expirationLabel?: string;
}>;

export type ProposalLifecycleInput = {
  proposalId: string;
  heading: string;
  actionLabel: string;
  riskLabel: string;
  targets: ReadonlyArray<{
    label: string;
    value: string;
    truncated?: boolean;
  }>;
  payload: ReadonlyArray<{
    label: string;
    value: string;
    truncated?: boolean;
  }>;
  expirationLabel?: string;
};

export type ProposalLifecycleState =
  | {
      status: "idle";
      proposalId: null;
      proposal: null;
      error: null;
    }
  | {
      status: Exclude<ProposalLifecycleStatus, "idle" | "failed">;
      proposalId: string;
      proposal: ProposalLifecycleSnapshot;
      error: null;
    }
  | {
      status: "failed";
      proposalId: string;
      proposal: ProposalLifecycleSnapshot;
      error: ProductivityPreviewErrorCode;
    };

export type ProposalLifecycleEvent =
  | { type: "preview"; proposal: ProposalLifecycleInput }
  | { type: "confirm"; proposalId: string }
  | { type: "approve"; proposalId: string }
  | { type: "execute"; proposalId: string }
  | { type: "complete"; proposalId: string }
  | { type: "fail"; proposalId: string; error?: unknown }
  | { type: "cancel"; proposalId: string }
  | { type: "cancelled"; proposalId: string };

const TERMINAL_STATUSES = new Set<ProposalLifecycleStatus>([
  "completed",
  "failed",
  "cancelled",
]);

const ACTIVE_CANCEL_STATUSES = new Set<ProposalLifecycleStatus>([
  "preview",
  "confirming",
  "approved",
  "executing",
]);

const FAILABLE_STATUSES = new Set<ProposalLifecycleStatus>([
  "preview",
  "confirming",
  "approved",
  "executing",
  "cancelling",
]);

export function createInitialProposalLifecycleState(): ProposalLifecycleState {
  return {
    status: "idle",
    proposalId: null,
    proposal: null,
    error: null,
  };
}

export function isValidProposalId(raw: unknown): raw is string {
  return typeof raw === "string" && PROPOSAL_ID_PATTERN.test(raw);
}

export function isTerminalProposalLifecycleStatus(
  status: ProposalLifecycleStatus,
): boolean {
  return TERMINAL_STATUSES.has(status);
}

function freezeEntry(entry: unknown): ProposalLifecycleEntry | null {
  if (entry === null || typeof entry !== "object" || Array.isArray(entry)) {
    return null;
  }
  const record = entry as Record<string, unknown>;
  if (typeof record.label !== "string" || typeof record.value !== "string") {
    return null;
  }
  const label = boundPreviewLabel(record.label);
  const value = boundPreviewValue(record.value);
  const truncated =
    record.truncated === true || label.truncated || value.truncated;
  const frozen: {
    label: string;
    value: string;
    truncated?: boolean;
  } = {
    label: label.text,
    value: value.text,
  };
  if (truncated) {
    frozen.truncated = true;
  }
  return Object.freeze(frozen);
}

function freezeEntries(
  entries: unknown,
): ReadonlyArray<ProposalLifecycleEntry> | null {
  if (!Array.isArray(entries)) {
    return null;
  }
  if (entries.length > PREVIEW_ENTRY_MAX) {
    return null;
  }
  const frozen: ProposalLifecycleEntry[] = [];
  for (const entry of entries) {
    const next = freezeEntry(entry);
    if (!next) {
      return null;
    }
    frozen.push(next);
  }
  return Object.freeze(frozen);
}

export function freezeProposalSnapshot(
  input: unknown,
): ProposalLifecycleSnapshot | null {
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    return null;
  }
  const record = input as Record<string, unknown>;
  if (!isValidProposalId(record.proposalId)) {
    return null;
  }
  if (typeof record.heading !== "string") {
    return null;
  }
  if (typeof record.actionLabel !== "string") {
    return null;
  }
  if (typeof record.riskLabel !== "string") {
    return null;
  }
  if (
    record.expirationLabel !== undefined &&
    typeof record.expirationLabel !== "string"
  ) {
    return null;
  }

  const targets = freezeEntries(record.targets);
  const payload = freezeEntries(record.payload);
  if (!targets || !payload) {
    return null;
  }

  const heading = boundPreviewLabel(record.heading);
  const actionLabel = boundPreviewLabel(record.actionLabel);
  const riskLabel = boundPreviewLabel(record.riskLabel);

  const snapshot: {
    proposalId: string;
    heading: string;
    actionLabel: string;
    riskLabel: string;
    targets: ReadonlyArray<ProposalLifecycleEntry>;
    payload: ReadonlyArray<ProposalLifecycleEntry>;
    expirationLabel?: string;
  } = {
    proposalId: record.proposalId,
    heading: heading.text,
    actionLabel: actionLabel.text,
    riskLabel: riskLabel.text,
    targets,
    payload,
  };

  if (typeof record.expirationLabel === "string") {
    snapshot.expirationLabel = boundPreviewLabel(record.expirationLabel).text;
  }

  return Object.freeze(snapshot);
}

export function resolveLifecycleErrorCode(
  value: unknown,
): ProductivityPreviewErrorCode {
  if (isProductivityPreviewErrorCode(value)) {
    return value;
  }
  return "unavailable";
}

function matchesProposal(
  state: ProposalLifecycleState,
  proposalId: unknown,
): boolean {
  if (state.status === "idle" || state.proposalId === null) {
    return false;
  }
  return typeof proposalId === "string" && proposalId === state.proposalId;
}

function withStatus(
  state: Exclude<ProposalLifecycleState, { status: "idle" }>,
  status: Exclude<ProposalLifecycleStatus, "idle" | "failed">,
): ProposalLifecycleState {
  return {
    status,
    proposalId: state.proposalId,
    proposal: state.proposal,
    error: null,
  };
}

export function reduceProposalLifecycle(
  state: ProposalLifecycleState,
  event: ProposalLifecycleEvent,
): ProposalLifecycleState {
  switch (event.type) {
    case "preview": {
      if (state.status !== "idle" && !isTerminalProposalLifecycleStatus(state.status)) {
        return state;
      }
      const proposal = freezeProposalSnapshot(event.proposal);
      if (!proposal) {
        return state;
      }
      return {
        status: "preview",
        proposalId: proposal.proposalId,
        proposal,
        error: null,
      };
    }
    case "confirm": {
      if (state.status !== "preview" || !matchesProposal(state, event.proposalId)) {
        return state;
      }
      return withStatus(state, "confirming");
    }
    case "approve": {
      if (state.status !== "confirming" || !matchesProposal(state, event.proposalId)) {
        return state;
      }
      return withStatus(state, "approved");
    }
    case "execute": {
      if (state.status !== "approved" || !matchesProposal(state, event.proposalId)) {
        return state;
      }
      return withStatus(state, "executing");
    }
    case "complete": {
      if (state.status !== "executing" || !matchesProposal(state, event.proposalId)) {
        return state;
      }
      return withStatus(state, "completed");
    }
    case "fail": {
      if (
        state.status === "idle" ||
        !FAILABLE_STATUSES.has(state.status) ||
        !matchesProposal(state, event.proposalId)
      ) {
        return state;
      }
      return {
        status: "failed",
        proposalId: state.proposalId,
        proposal: state.proposal,
        error: resolveLifecycleErrorCode(event.error),
      };
    }
    case "cancel": {
      if (
        state.status === "idle" ||
        !ACTIVE_CANCEL_STATUSES.has(state.status) ||
        !matchesProposal(state, event.proposalId)
      ) {
        return state;
      }
      return withStatus(state, "cancelling");
    }
    case "cancelled": {
      if (state.status !== "cancelling" || !matchesProposal(state, event.proposalId)) {
        return state;
      }
      return withStatus(state, "cancelled");
    }
    default:
      return state;
  }
}
