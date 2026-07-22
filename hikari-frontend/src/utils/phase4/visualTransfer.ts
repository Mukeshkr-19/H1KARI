/**
 * Pure Phase 4 visual transfer state machine and immutable reducer.
 *
 * Enforces local file validation (image/png, image/jpeg, <= 1 MiB), exact correlation,
 * zero persistence, and automatic file reference clearing on all terminal/cancel paths.
 */

import { isValidCanonicalId, isValidOpaqueId } from "./identifiers";

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

export function isVisualTransferErrorCode(code: unknown): code is VisualTransferErrorCode {
  return typeof code === "string" && VISUAL_TRANSFER_ERROR_CODES.has(code as VisualTransferErrorCode);
}

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
  readonly fileRef: Blob | null;
  readonly fileSize: number | null;
  readonly fileType: string | null;
  readonly errorCode: VisualTransferErrorCode | "not_a_file" | null;
}

export type VisualTransferAction =
  | { type: "SELECT_FILE"; file: Blob }
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
  if (!(file instanceof Blob)) {
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

export function inspectImageDimensions(
  buffer: ArrayBuffer,
  mimeType: string,
): Readonly<{ width: number; height: number }> | null {
  const bytes = new Uint8Array(buffer);
  if (mimeType === "image/png") {
    if (
      bytes.length < 24 ||
      bytes[0] !== 0x89 || bytes[1] !== 0x50 || bytes[2] !== 0x4e || bytes[3] !== 0x47 ||
      bytes[4] !== 0x0d || bytes[5] !== 0x0a || bytes[6] !== 0x1a || bytes[7] !== 0x0a
    ) {
      return null;
    }
    const view = new DataView(buffer);
    const width = view.getUint32(16, false);
    const height = view.getUint32(20, false);
    return width >= 1 && width <= 4096 && height >= 1 && height <= 4096
      ? Object.freeze({ width, height })
      : null;
  }
  if (mimeType !== "image/jpeg" || bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) {
    return null;
  }
  const sofMarkers = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
  let offset = 2;
  while (offset + 8 < bytes.length) {
    if (bytes[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    while (offset < bytes.length && bytes[offset] === 0xff) offset += 1;
    if (offset >= bytes.length) return null;
    const marker = bytes[offset++];
    if (marker === 0xd9 || marker === 0xda) return null;
    if (offset + 1 >= bytes.length) return null;
    const segmentLength = (bytes[offset] << 8) | bytes[offset + 1];
    if (segmentLength < 2 || offset + segmentLength > bytes.length) return null;
    if (sofMarkers.has(marker)) {
      if (segmentLength < 7) return null;
      const height = (bytes[offset + 3] << 8) | bytes[offset + 4];
      const width = (bytes[offset + 5] << 8) | bytes[offset + 6];
      return width >= 1 && width <= 4096 && height >= 1 && height <= 4096
        ? Object.freeze({ width, height })
        : null;
    }
    offset += segmentLength;
  }
  return null;
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
