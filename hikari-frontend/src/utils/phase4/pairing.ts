/**
 * Pure Phase 4 pairing state machine and immutable reducer.
 *
 * Enforces correlation of request and challenge IDs, pending state locking,
 * duplicate suppression, and content-free privacy invariants.
 */

import {
  isValidCanonicalId,
  isValidDeviceLabel,
  isValidOpaqueId,
} from "./identifiers.js";

export type PairingErrorCode =
  | "unavailable"
  | "unauthorized"
  | "invalid_request"
  | "challenge_invalid"
  | "challenge_expired"
  | "pairing_locked"
  | "rate_limited"
  | "device_not_found";

const PAIRING_ERROR_CODES = new Set<PairingErrorCode>([
  "unavailable",
  "unauthorized",
  "invalid_request",
  "challenge_invalid",
  "challenge_expired",
  "pairing_locked",
  "rate_limited",
  "device_not_found",
]);

export type PairingStatus =
  | "idle"
  | "preparing"
  | "challenge"
  | "confirming"
  | "paired"
  | "cancelling"
  | "cancelled"
  | "revoked"
  | "expired"
  | "failed";

export interface PairingState {
  readonly status: PairingStatus;
  readonly requestId: string | null;
  readonly challengeId: string | null;
  readonly deviceLabel: string | null;
  readonly deviceId: string | null;
  readonly errorCode: PairingErrorCode | null;
}

export type PairingAction =
  | { type: "START_PREPARING"; requestId: string; deviceLabel?: string }
  | { type: "RECEIVE_CHALLENGE"; requestId: string; challengeId: string }
  | { type: "SUBMIT_CONFIRM"; challengeId: string }
  | { type: "CONFIRM_SUCCESS"; challengeId: string; deviceId: string }
  | { type: "CANCEL" }
  | { type: "CANCEL_COMPLETE" }
  | { type: "EXPIRE" }
  | { type: "REVOKE" }
  | { type: "FAIL"; errorCode?: PairingErrorCode }
  | { type: "RESET" };

export function createInitialPairingState(): PairingState {
  return Object.freeze({
    status: "idle",
    requestId: null,
    challengeId: null,
    deviceLabel: null,
    deviceId: null,
    errorCode: null,
  });
}

export function isPairingPending(status: PairingStatus): boolean {
  return (
    status === "preparing" ||
    status === "challenge" ||
    status === "confirming" ||
    status === "cancelling"
  );
}

export function isPairingTerminal(status: PairingStatus): boolean {
  return (
    status === "paired" ||
    status === "cancelled" ||
    status === "revoked" ||
    status === "expired" ||
    status === "failed"
  );
}

export function reducePairing(
  state: PairingState,
  action: PairingAction
): PairingState {
  switch (action.type) {
    case "START_PREPARING": {
      if (isPairingPending(state.status) || state.status === "paired") {
        return state; // Duplicate / pending lock
      }
      if (!isValidCanonicalId(action.requestId)) {
        return state;
      }
      if (action.deviceLabel !== undefined && !isValidDeviceLabel(action.deviceLabel)) {
        return state;
      }
      const label = action.deviceLabel ?? null;
      return Object.freeze({
        status: "preparing",
        requestId: action.requestId,
        challengeId: null,
        deviceLabel: label,
        deviceId: null,
        errorCode: null,
      });
    }

    case "RECEIVE_CHALLENGE": {
      if (state.status !== "preparing") {
        return state;
      }
      // Exact correlation check: must match active requestId
      if (action.requestId !== state.requestId) {
        return state; // Stale request ID ignored
      }
      if (!isValidCanonicalId(action.challengeId)) {
        return state;
      }
      return Object.freeze({
        status: "challenge",
        requestId: state.requestId,
        challengeId: action.challengeId,
        deviceLabel: state.deviceLabel,
        deviceId: null,
        errorCode: null,
      });
    }

    case "SUBMIT_CONFIRM": {
      if (state.status !== "challenge") {
        return state;
      }
      // Exact correlation check: must match active challengeId
      if (action.challengeId !== state.challengeId) {
        return state; // Stale challenge ID ignored
      }
      return Object.freeze({
        ...state,
        status: "confirming",
      });
    }

    case "CONFIRM_SUCCESS": {
      if (state.status !== "confirming") {
        return state;
      }
      // Exact correlation check: must match active challengeId
      if (action.challengeId !== state.challengeId) {
        return state; // Stale challenge ID ignored
      }
      if (!isValidOpaqueId(action.deviceId)) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "paired",
        deviceId: action.deviceId,
      });
    }

    case "CANCEL": {
      if (
        state.status === "idle" ||
        isPairingTerminal(state.status) ||
        state.status === "cancelling"
      ) {
        return state; // Duplicate / terminal cancel ignored
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
      if (isPairingTerminal(state.status)) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "expired",
      });
    }

    case "REVOKE": {
      if (state.status !== "paired") {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "revoked",
      });
    }

    case "FAIL": {
      if (isPairingTerminal(state.status)) {
        return state;
      }
      const err =
        action.errorCode && PAIRING_ERROR_CODES.has(action.errorCode)
          ? action.errorCode
          : "unavailable";
      return Object.freeze({
        ...state,
        status: "failed",
        errorCode: err,
      });
    }

    case "RESET": {
      return createInitialPairingState();
    }

    default:
      return state;
  }
}
