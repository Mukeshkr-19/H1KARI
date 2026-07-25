/**
 * Immutable Phase 5 UI state reducer.
 * No localStorage. Sensitive proposal content clears on close/revoke/logout/dispose.
 */

import {
  PHASE5_SAFE_ERROR_MESSAGES,
  type Phase5ErrorCode,
  type Phase5HelperGrant,
  type Phase5ServerMessage,
} from "./phase5Protocol";

export type Phase5UiStatus =
  | "unavailable"
  | "idle"
  | "activating"
  | "active"
  | "approval_required"
  | "proposal_ready"
  | "denied"
  | "expired"
  | "revoked"
  | "locked"
  | "closed"
  | "error";

export type Phase5State = Readonly<{
  status: Phase5UiStatus;
  requestId: string | null;
  pendingRequestId: string | null;
  sessionId: string | null;
  sessionType: string | null;
  sessionState: string | null;
  expiresAt: number | null;
  capabilities: readonly string[];
  capability: string | null;
  proposalSummary: string | null;
  proposalItems: readonly string[];
  approvalRequired: boolean;
  uncertaintyDisclosed: boolean;
  emergencyLimitation: string | null;
  helperGrants: readonly Phase5HelperGrant[];
  errorCode: Phase5ErrorCode | null;
  errorMessage: string | null;
  submitLocked: boolean;
}>;

export const INITIAL_PHASE5_STATE: Phase5State = Object.freeze({
  status: "idle",
  requestId: null,
  pendingRequestId: null,
  sessionId: null,
  sessionType: null,
  sessionState: null,
  expiresAt: null,
  capabilities: Object.freeze([]),
  capability: null,
  proposalSummary: null,
  proposalItems: Object.freeze([]),
  approvalRequired: false,
  uncertaintyDisclosed: false,
  emergencyLimitation: null,
  helperGrants: Object.freeze([]),
  errorCode: null,
  errorMessage: null,
  submitLocked: false,
});

function clearSensitive(state: Phase5State): Phase5State {
  return Object.freeze({
    ...state,
    proposalSummary: null,
    proposalItems: Object.freeze([]),
    emergencyLimitation: null,
    helperGrants: Object.freeze([]),
    pendingRequestId: null,
    capability: null,
    approvalRequired: false,
    uncertaintyDisclosed: false,
  });
}

export type Phase5Action =
  | { type: "phase5/begin_request"; requestId: string; status?: Phase5UiStatus }
  | { type: "phase5/apply_server"; message: Phase5ServerMessage }
  | { type: "phase5/clear_sensitive" }
  | { type: "phase5/reset" }
  | { type: "phase5/unlock_submit" };

export function reducePhase5State(state: Phase5State, action: Phase5Action): Phase5State {
  switch (action.type) {
    case "phase5/reset":
      return INITIAL_PHASE5_STATE;
    case "phase5/clear_sensitive":
      return clearSensitive(state);
    case "phase5/unlock_submit":
      return Object.freeze({ ...state, submitLocked: false });
    case "phase5/begin_request": {
      if (state.submitLocked && state.requestId === action.requestId) {
        return state;
      }
      return Object.freeze({
        ...state,
        requestId: action.requestId,
        status: action.status ?? "activating",
        submitLocked: true,
        errorCode: null,
        errorMessage: null,
      });
    }
    case "phase5/apply_server": {
      const message = action.message;
      if (state.requestId && message.request_id !== state.requestId) {
        // Exact correlation: ignore stale responses except helper grant lists
        // that may arrive for a list request id already tracked.
        if (message.type !== "phase5_helper_grants") {
          return state;
        }
      }
      if (message.type === "phase5_session_update") {
        let status: Phase5UiStatus = "active";
        if (message.state === "expired") status = "expired";
        else if (message.state === "revoked") status = "revoked";
        else if (message.state === "locked") status = "locked";
        else if (message.state === "closed") status = "closed";
        else if (message.state === "active") status = "active";
        else status = "activating";
        const next = Object.freeze({
          ...state,
          status,
          requestId: message.request_id,
          sessionId: message.session_id,
          sessionType: message.session_type,
          sessionState: message.state,
          expiresAt: message.expires_at,
          capabilities: message.capabilities,
          submitLocked: false,
          errorCode: null,
          errorMessage: null,
        });
        if (status === "revoked" || status === "closed" || status === "expired") {
          return clearSensitive(next);
        }
        return next;
      }
      if (message.type === "phase5_approval_required") {
        return Object.freeze({
          ...state,
          status: "approval_required",
          requestId: message.request_id,
          pendingRequestId: message.pending_request_id,
          capability: message.capability,
          approvalRequired: true,
          submitLocked: false,
          errorCode: null,
          errorMessage: null,
        });
      }
      if (message.type === "phase5_capability_proposal") {
        return Object.freeze({
          ...state,
          status: "proposal_ready",
          requestId: message.request_id,
          capability: message.capability,
          proposalSummary: message.summary,
          proposalItems: message.items,
          approvalRequired: message.approval_required,
          uncertaintyDisclosed: Boolean(message.uncertainty_disclosed),
          emergencyLimitation: message.emergency_limitation ?? null,
          submitLocked: false,
          errorCode: null,
          errorMessage: null,
        });
      }
      if (message.type === "phase5_helper_grants") {
        return Object.freeze({
          ...state,
          requestId: message.request_id,
          helperGrants: message.grants,
          submitLocked: false,
          errorCode: null,
          errorMessage: null,
        });
      }
      if (message.type === "phase5_error") {
        let status: Phase5UiStatus = "error";
        if (message.code === "denied") status = "denied";
        else if (message.code === "expired") status = "expired";
        else if (message.code === "revoked") status = "revoked";
        else if (message.code === "locked") status = "locked";
        else if (message.code === "closed") status = "closed";
        else if (message.code === "unavailable") status = "unavailable";
        else if (message.code === "unauthorized") status = "denied";
        return Object.freeze({
          ...state,
          status,
          requestId: message.request_id,
          errorCode: message.code,
          errorMessage: PHASE5_SAFE_ERROR_MESSAGES[message.code],
          submitLocked: false,
        });
      }
      return state;
    }
    default:
      return state;
  }
}
