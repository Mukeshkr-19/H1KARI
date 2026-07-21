/**
 * Pure Phase 4 visual transfer state machine and immutable reducer.
 *
 * Enforces local file validation (image/png, image/jpeg, <= 1 MiB), exact correlation,
 * zero persistence, and automatic file reference clearing on all terminal/cancel paths.
 */

import { isValidCanonicalId, isValidOpaqueId } from "./identifiers.js";

export type VisualTransferErrorCode =
  | "unavailable"
  | "unauthorized"
  | "invalid_request"
  | "transfer_not_found"
  | "transfer_expired"
  | "handoff_not_accepted"
  | "mime_unsupported"
  | "mime_mismatch"
  | "size_exceeded"
  | "dimensions_exceeded"
  | "frame_count_invalid"
  | "decompression_limit"
  | "metadata_rejected"
  | "malformed_image"
  | "rate_limited";

const VISUAL_TRANSFER_ERROR_CODES = new Set<VisualTransferErrorCode>([
  "unavailable",
  "unauthorized",
  "invalid_request",
  "transfer_not_found",
  "transfer_expired",
  "handoff_not_accepted",
  "mime_unsupported",
  "mime_mismatch",
  "size_exceeded",
  "dimensions_exceeded",
  "frame_count_invalid",
  "decompression_limit",
  "metadata_rejected",
  "malformed_image",
  "rate_limited",
]);

export type VisualTransferStatus =
  | "idle"
  | "selected"
  | "beginning"
  | "ready"
  | "transferring"
  | "validating"
  | "completed"
  | "cancelling"
  | "cancelled"
  | "failed";

export const MAX_IMAGE_BYTES = 1048576; // 1 MiB
export const ALLOWED_MIME_TYPES = Object.freeze(["image/png", "image/jpeg"]);

export interface VisualTransferState {
  readonly status: VisualTransferStatus;
  readonly requestId: string | null;
  readonly transferId: string | null;
  readonly fileRef: File | null;
  readonly fileSize: number | null;
  readonly fileType: string | null;
  readonly errorCode: VisualTransferErrorCode | "not_a_file" | null;
}

export type VisualTransferAction =
  | { type: "SELECT_FILE"; file: File }
  | { type: "BEGIN_TRANSFER"; requestId: string }
  | { type: "SET_READY"; requestId: string; transferId: string }
  | { type: "START_TRANSFERRING"; transferId: string }
  | { type: "VALIDATE"; transferId: string }
  | { type: "TRANSFER_COMPLETE"; transferId: string }
  | { type: "CANCEL" }
  | { type: "CANCEL_COMPLETE" }
  | { type: "FAIL"; errorCode?: VisualTransferErrorCode }
  | { type: "RESET" };

export function createInitialVisualTransferState(): VisualTransferState {
  return Object.freeze({
    status: "idle",
    requestId: null,
    transferId: null,
    fileRef: null,
    fileSize: null,
    fileType: null,
    errorCode: null,
  });
}

export function isVisualTransferPending(status: VisualTransferStatus): boolean {
  return (
    status === "beginning" ||
    status === "ready" ||
    status === "transferring" ||
    status === "validating" ||
    status === "cancelling"
  );
}

export function isVisualTransferTerminal(status: VisualTransferStatus): boolean {
  return (
    status === "completed" ||
    status === "cancelled" ||
    status === "failed"
  );
}

/**
 * Pure local file validator. Checks type and size bounds without reading file body.
 */
export function validateImageFile(file: unknown): {
  valid: boolean;
  errorCode: VisualTransferErrorCode | "not_a_file" | null;
} {
  if (!(file instanceof File)) {
    return { valid: false, errorCode: "not_a_file" };
  }
  if (!ALLOWED_MIME_TYPES.includes(file.type)) {
    return { valid: false, errorCode: "mime_unsupported" };
  }
  if (file.size <= 0 || file.size > MAX_IMAGE_BYTES) {
    return { valid: false, errorCode: "size_exceeded" };
  }
  return { valid: true, errorCode: null };
}

export function reduceVisualTransfer(
  state: VisualTransferState,
  action: VisualTransferAction
): VisualTransferState {
  switch (action.type) {
    case "SELECT_FILE": {
      if (isVisualTransferPending(state.status)) {
        return state; // Locked during transfer
      }
      const val = validateImageFile(action.file);
      if (!val.valid) {
        return Object.freeze({
          status: "failed",
          requestId: null,
          transferId: null,
          fileRef: null,
          fileSize: null,
          fileType: null,
          errorCode: val.errorCode,
        });
      }
      return Object.freeze({
        status: "selected",
        requestId: null,
        transferId: null,
        fileRef: action.file,
        fileSize: action.file.size,
        fileType: action.file.type,
        errorCode: null,
      });
    }

    case "BEGIN_TRANSFER": {
      if (state.status !== "selected" || !state.fileRef) {
        return state;
      }
      if (!isValidCanonicalId(action.requestId)) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "beginning",
        requestId: action.requestId,
        transferId: null,
      });
    }

    case "SET_READY": {
      if (
        state.status !== "beginning" ||
        action.requestId !== state.requestId ||
        !isValidOpaqueId(action.transferId)
      ) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "ready",
        transferId: action.transferId,
      });
    }

    case "START_TRANSFERRING": {
      if (
        (state.status !== "ready" && state.status !== "beginning") ||
        action.transferId !== state.transferId
      ) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "transferring",
      });
    }

    case "VALIDATE": {
      if (state.status !== "transferring" || action.transferId !== state.transferId) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "validating",
      });
    }

    case "TRANSFER_COMPLETE": {
      if (
        (state.status !== "transferring" && state.status !== "validating") ||
        action.transferId !== state.transferId
      ) {
        return state;
      }
      // MUST CLEAR fileRef on complete!
      return Object.freeze({
        ...state,
        status: "completed",
        fileRef: null,
      });
    }

    case "CANCEL": {
      if (
        state.status === "idle" ||
        isVisualTransferTerminal(state.status) ||
        state.status === "cancelling"
      ) {
        return state;
      }
      // MUST CLEAR fileRef on cancel!
      return Object.freeze({
        ...state,
        status: "cancelling",
        fileRef: null,
      });
    }

    case "CANCEL_COMPLETE": {
      if (state.status !== "cancelling") {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "cancelled",
        fileRef: null,
      });
    }

    case "FAIL": {
      if (isVisualTransferTerminal(state.status)) {
        return state;
      }
      const err =
        action.errorCode && VISUAL_TRANSFER_ERROR_CODES.has(action.errorCode)
          ? action.errorCode
          : "unavailable";
      // MUST CLEAR fileRef on fail!
      return Object.freeze({
        ...state,
        status: "failed",
        fileRef: null,
        errorCode: err,
      });
    }

    case "RESET": {
      return createInitialVisualTransferState();
    }

    default:
      return state;
  }
}
