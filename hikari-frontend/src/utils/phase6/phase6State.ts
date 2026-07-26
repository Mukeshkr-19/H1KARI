/**
 * Immutable Phase 6 UI state reducer.
 * Pure: no storage, transport, or side effects.
 * Sensitive data clears on close/revoke/logout/reset.
 */

import {
  PHASE6_SAFE_ERROR_MESSAGES,
  type Phase6AgentRunUpdate,
  type Phase6EncryptedSyncUpdate,
  type Phase6ErrorCode,
  type Phase6HomeAssistantProposal,
  type Phase6IntegrationStatus,
  type Phase6ModelEvalUpdate,
  type Phase6RemoteWorkerUpdate,
  type Phase6RepoIntelUpdate,
  type Phase6ServerMessage,
  type Phase6SkillEvolutionUpdate,
  type Phase6TimeSenseUpdate,
} from "./phase6Protocol";
import { isValidCanonicalId } from "../phase4/identifiers";

export type Phase6UiStatus =
  | "unavailable"
  | "idle"
  | "loading"
  | "ready"
  | "approval_required"
  | "error"
  | "closed"
  | "revoked";

export type Phase6State = Readonly<{
  status: Phase6UiStatus;
  requestId: string | null;
  pendingRequestId: string | null;
  submitLocked: boolean;
  errorCode: Phase6ErrorCode | null;
  errorMessage: string | null;
  integrations: readonly Phase6IntegrationStatus[];
  agentRun: Phase6AgentRunUpdate | null;
  timeSense: Phase6TimeSenseUpdate | null;
  repoIntel: Phase6RepoIntelUpdate | null;
  skillEvolution: Phase6SkillEvolutionUpdate | null;
  haProposal: Phase6HomeAssistantProposal | null;
  confirmedNonce: string | null;
  encryptedSync: Phase6EncryptedSyncUpdate | null;
  remoteWorker: Phase6RemoteWorkerUpdate | null;
  modelEval: Phase6ModelEvalUpdate | null;
}>;

export const INITIAL_PHASE6_STATE: Phase6State = Object.freeze({
  status: "idle",
  requestId: null,
  pendingRequestId: null,
  submitLocked: false,
  errorCode: null,
  errorMessage: null,
  integrations: Object.freeze([]),
  agentRun: null,
  timeSense: null,
  repoIntel: null,
  skillEvolution: null,
  haProposal: null,
  confirmedNonce: null,
  encryptedSync: null,
  remoteWorker: null,
  modelEval: null,
});

export function clearSensitivePhase6Data(state: Phase6State): Phase6State {
  return Object.freeze({
    ...state,
    haProposal: null,
    confirmedNonce: null,
    pendingRequestId: null,
    repoIntel: null,
    modelEval: null,
    skillEvolution: null,
  });
}

export type Phase6Action =
  | { type: "phase6/begin_request"; requestId: string; status?: Phase6UiStatus }
  | { type: "phase6/apply_server"; message: Phase6ServerMessage }
  | { type: "phase6/confirm_home_assistant"; proposalId: string; nonce: string }
  | { type: "phase6/clear_sensitive" }
  | { type: "phase6/reset" }
  | { type: "phase6/unlock_submit" }
  | { type: "phase6/dismiss_error" };

export function reducePhase6State(state: Phase6State, action: Phase6Action): Phase6State {
  switch (action.type) {
    case "phase6/reset":
      return INITIAL_PHASE6_STATE;

    case "phase6/clear_sensitive":
      return clearSensitivePhase6Data(state);

    case "phase6/unlock_submit":
      return Object.freeze({ ...state, submitLocked: false });

    case "phase6/dismiss_error":
      return Object.freeze({ ...state, errorCode: null, errorMessage: null, submitLocked: false });

    case "phase6/begin_request": {
      if (!isValidCanonicalId(action.requestId)) {
        return state;
      }
      if (state.submitLocked && state.requestId === action.requestId) {
        return state;
      }
      return Object.freeze({
        ...state,
        requestId: action.requestId,
        status: action.status ?? "loading",
        submitLocked: true,
        errorCode: null,
        errorMessage: null,
      });
    }

    case "phase6/confirm_home_assistant": {
      if (!state.haProposal || state.haProposal.proposal_id !== action.proposalId || state.haProposal.nonce !== action.nonce) {
        return state;
      }
      return Object.freeze({
        ...state,
        confirmedNonce: action.nonce,
        status: "loading",
        submitLocked: true,
      });
    }

    case "phase6/apply_server": {
      const msg = action.message;

      // Never accept unsolicited or stale messages, including errors.
      if (state.requestId === null || msg.request_id !== state.requestId) {
        return state;
      }

      if (msg.type === "phase6_error") {
        const isRevocation = msg.code === "revoked" || msg.code === "closed";
        const next = Object.freeze({
          ...state,
          status: isRevocation ? msg.code : "error",
          errorCode: msg.code,
          errorMessage: PHASE6_SAFE_ERROR_MESSAGES[msg.code] ?? "Phase 6 transport error",
          submitLocked: false,
        });
        if (isRevocation) {
          return clearSensitivePhase6Data(next);
        }
        return next;
      }

      switch (msg.type) {
        case "phase6_integration_status": {
          const existing = state.integrations.filter((i) => i.integration_id !== msg.integration_id);
          return Object.freeze({
            ...state,
            status: "ready",
            integrations: Object.freeze([...existing, msg]),
            submitLocked: false,
          });
        }

        case "phase6_agent_run_update": {
          return Object.freeze({
            ...state,
            status: msg.state === "waiting_for_approval" ? "approval_required" : "ready",
            agentRun: msg,
            submitLocked: false,
          });
        }

        case "phase6_time_sense_update": {
          return Object.freeze({
            ...state,
            status: "ready",
            timeSense: msg,
            submitLocked: false,
          });
        }

        case "phase6_repo_intel_update": {
          return Object.freeze({
            ...state,
            status: "ready",
            repoIntel: msg,
            submitLocked: false,
          });
        }

        case "phase6_skill_evolution_update": {
          return Object.freeze({
            ...state,
            status: "ready",
            skillEvolution: msg,
            submitLocked: false,
          });
        }

        case "phase6_home_assistant_proposal": {
          return Object.freeze({
            ...state,
            status: "approval_required",
            haProposal: msg,
            pendingRequestId: msg.request_id,
            submitLocked: false,
          });
        }

        case "phase6_encrypted_sync_update": {
          return Object.freeze({
            ...state,
            status: "ready",
            encryptedSync: msg,
            submitLocked: false,
          });
        }

        case "phase6_remote_worker_update": {
          return Object.freeze({
            ...state,
            status: "ready",
            remoteWorker: msg,
            submitLocked: false,
          });
        }

        case "phase6_model_eval_update": {
          return Object.freeze({
            ...state,
            status: "ready",
            modelEval: msg,
            submitLocked: false,
          });
        }

        default:
          return state;
      }
    }

    default:
      return state;
  }
}
