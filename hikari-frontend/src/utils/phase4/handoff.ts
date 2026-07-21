/**
 * Pure Phase 4 handoff state machine and immutable reducer.
 *
 * Enforces frozen preview copies of task ID and summary, explicit user acknowledgment,
 * correlation of handoff IDs, and terminal state protection.
 */

import {
  isValidCanonicalId,
  isValidOpaqueId,
  isValidSummaryText,
} from "./identifiers.js";

export type HandoffStatus =
  | "idle"
  | "offered"
  | "accepting"
  | "accepted"
  | "rejecting"
  | "rejected"
  | "cancelling"
  | "cancelled"
  | "expired"
  | "failed";

export interface HandoffPreview {
  readonly taskId: string;
  readonly summary: string;
}

export type HandoffErrorCode =
  | "unavailable"
  | "unauthorized"
  | "invalid_request"
  | "task_not_found"
  | "handoff_not_found"
  | "handoff_expired"
  | "handoff_conflict"
  | "policy_denied";

const HANDOFF_ERROR_CODES = new Set<HandoffErrorCode>([
  "unavailable",
  "unauthorized",
  "invalid_request",
  "task_not_found",
  "handoff_not_found",
  "handoff_expired",
  "handoff_conflict",
  "policy_denied",
]);

export interface HandoffState {
  readonly status: HandoffStatus;
  readonly handoffId: string | null;
  readonly preview: HandoffPreview | null;
  readonly acknowledged: boolean;
  readonly errorCode: HandoffErrorCode | null;
}

export type HandoffAction =
  | {
      type: "RECEIVE_OFFER";
      handoffId: string;
      taskId: string;
      summary: string;
    }
  | { type: "TOGGLE_ACKNOWLEDGE"; acknowledged?: boolean }
  | { type: "ACCEPT"; handoffId: string }
  | { type: "ACCEPT_COMPLETE"; handoffId: string }
  | { type: "REJECT"; handoffId: string }
  | { type: "REJECT_COMPLETE"; handoffId: string }
  | { type: "CANCEL" }
  | { type: "CANCEL_COMPLETE" }
  | { type: "EXPIRE" }
  | { type: "FAIL"; errorCode?: HandoffErrorCode }
  | { type: "RESET" };

export function createInitialHandoffState(): HandoffState {
  return Object.freeze({
    status: "idle",
    handoffId: null,
    preview: null,
    acknowledged: false,
    errorCode: null,
  });
}

export function isHandoffPending(status: HandoffStatus): boolean {
  return (
    status === "accepting" ||
    status === "rejecting" ||
    status === "cancelling"
  );
}

export function isHandoffTerminal(status: HandoffStatus): boolean {
  return (
    status === "accepted" ||
    status === "rejected" ||
    status === "cancelled" ||
    status === "expired" ||
    status === "failed"
  );
}

export function reduceHandoff(
  state: HandoffState,
  action: HandoffAction
): HandoffState {
  switch (action.type) {
    case "RECEIVE_OFFER": {
      if (isHandoffPending(state.status) || state.status === "offered") {
        return state; // Duplicate / pending lock
      }
      if (
        !isValidCanonicalId(action.handoffId) ||
        !isValidOpaqueId(action.taskId) ||
        !isValidSummaryText(action.summary)
      ) {
        return state; // Malformed payload rejected
      }

      // Create an explicitly frozen preview copy at offer time
      const frozenPreview: HandoffPreview = Object.freeze({
        taskId: action.taskId,
        summary: action.summary,
      });

      return Object.freeze({
        status: "offered",
        handoffId: action.handoffId,
        preview: frozenPreview,
        acknowledged: false,
        errorCode: null,
      });
    }

    case "TOGGLE_ACKNOWLEDGE": {
      if (state.status !== "offered") {
        return state;
      }
      const nextAck =
        typeof action.acknowledged === "boolean"
          ? action.acknowledged
          : !state.acknowledged;
      return Object.freeze({
        ...state,
        acknowledged: nextAck,
      });
    }

    case "ACCEPT": {
      if (state.status !== "offered") {
        return state; // Cannot accept unless offered
      }
      if (action.handoffId !== state.handoffId) {
        return state; // Stale handoff ID ignored
      }
      if (!state.acknowledged) {
        return state; // Requires explicit acknowledgment
      }
      return Object.freeze({
        ...state,
        status: "accepting",
      });
    }

    case "ACCEPT_COMPLETE": {
      if (state.status !== "accepting") {
        return state;
      }
      if (action.handoffId !== state.handoffId) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "accepted",
      });
    }

    case "REJECT": {
      if (state.status !== "offered") {
        return state;
      }
      if (action.handoffId !== state.handoffId) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "rejecting",
      });
    }

    case "REJECT_COMPLETE": {
      if (state.status !== "rejecting") {
        return state;
      }
      if (action.handoffId !== state.handoffId) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "rejected",
      });
    }

    case "CANCEL": {
      if (
        state.status === "idle" ||
        isHandoffTerminal(state.status) ||
        state.status === "cancelling"
      ) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "cancelling",
      });
    }

    case "CANCEL_COMPLETE": {
      if (state.status !== "cancelling") {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "cancelled",
      });
    }

    case "EXPIRE": {
      if (isHandoffTerminal(state.status)) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "expired",
      });
    }

    case "FAIL": {
      if (isHandoffTerminal(state.status)) {
        return state;
      }
      const err =
        action.errorCode && HANDOFF_ERROR_CODES.has(action.errorCode)
          ? action.errorCode
          : "unavailable";
      return Object.freeze({
        ...state,
        status: "failed",
        errorCode: err,
      });
    }

    case "RESET": {
      return createInitialHandoffState();
    }

    default:
      return state;
  }
}
