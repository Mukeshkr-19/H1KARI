/**
 * Pure Phase 4 protocol parser and encoders.
 *
 * Implements strict, fail-closed parsing for Phase 4 server messages:
 * - pairing_challenge
 * - pairing_confirmed
 * - pairing_update
 * - pairing_error
 * - handoff_offer
 * - handoff_update
 * - handoff_error
 * - visual_transfer_ready
 * - visual_transfer_update
 * - visual_transfer_complete
 * - visual_transfer_error
 *
 * Enforces exact field contracts, unknown field rejection, and safe code validation.
 * Performs zero side effects, storage, transport, or logging.
 */

import {
  isValidCanonicalId,
  isValidOpaqueId,
  isValidSummaryText,
} from "./identifiers";
import { isPairingErrorCode } from "./pairing";
import { isHandoffErrorCode } from "./handoff";
import { isVisualTransferErrorCode } from "./visualTransfer";
export const PHASE4_SERVER_MESSAGE_TYPES = Object.freeze([
  "pairing_challenge",
  "pairing_confirmed",
  "pairing_update",
  "pairing_error",
  "handoff_offer",
  "handoff_update",
  "handoff_error",
  "visual_transfer_ready",
  "visual_transfer_update",
  "visual_transfer_complete",
  "visual_transfer_error",
] as const);

export type Phase4ServerMessageType = (typeof PHASE4_SERVER_MESSAGE_TYPES)[number];

export type PairingChallengeMessage = Readonly<{
  type: "pairing_challenge";
  request_id: string;
  challenge_id: string;
  expires_at: number;
}>;

export type PairingConfirmedMessage = Readonly<{
  type: "pairing_confirmed";
  request_id: string;
  device_id: string;
  expires_at: number;
  protocol_version: 1;
}>;

export type PairingUpdateMessage = Readonly<{
  type: "pairing_update";
  request_id: string;
  status: "cancelled" | "revoked" | "expired";
  challenge_id?: string;
  device_id?: string;
}>;

export type PairingErrorMessage = Readonly<{
  type: "pairing_error";
  request_id: string;
  code: string;
}>;

export type HandoffOfferMessage = Readonly<{
  type: "handoff_offer";
  request_id: string;
  handoff_id: string;
  task_id: string;
  summary: string;
  expires_at: number;
}>;

export type HandoffUpdateMessage = Readonly<{
  type: "handoff_update";
  request_id: string;
  handoff_id: string;
  status: "offered" | "accepted" | "rejected" | "cancelled" | "expired";
}>;

export type HandoffErrorMessage = Readonly<{
  type: "handoff_error";
  request_id: string;
  handoff_id?: string;
  code: string;
}>;

export type VisualTransferReadyMessage = Readonly<{
  type: "visual_transfer_ready";
  request_id: string;
  transfer_id: string;
  expires_at: number;
}>;

export type VisualTransferUpdateMessage = Readonly<{
  type: "visual_transfer_update";
  request_id: string;
  transfer_id: string;
  status: "pending" | "receiving" | "validating" | "completed" | "cancelled" | "failed";
  bytes_received: number;
}>;

export type VisualTransferCompleteMessage = Readonly<{
  type: "visual_transfer_complete";
  request_id: string;
  transfer_id: string;
  content_hash: string;
}>;

export type VisualTransferErrorMessage = Readonly<{
  type: "visual_transfer_error";
  request_id: string;
  transfer_id?: string;
  code: string;
}>;

export type Phase4ServerMessage =
  | PairingChallengeMessage
  | PairingConfirmedMessage
  | PairingUpdateMessage
  | PairingErrorMessage
  | HandoffOfferMessage
  | HandoffUpdateMessage
  | HandoffErrorMessage
  | VisualTransferReadyMessage
  | VisualTransferUpdateMessage
  | VisualTransferCompleteMessage
  | VisualTransferErrorMessage;

function hasOnlyKeys(obj: Record<string, unknown>, allowedKeys: Set<string>): boolean {
  for (const key of Object.keys(obj)) {
    if (!allowedKeys.has(key)) {
      return false;
    }
  }
  return true;
}

const VALID_PAIRING_STATUSES = new Set<string>([
  "cancelled",
  "revoked",
  "expired",
]);

const VALID_HANDOFF_STATUSES = new Set<string>([
  "offered",
  "accepted",
  "rejected",
  "cancelled",
  "expired",
]);

const VALID_VISUAL_TRANSFER_STATUSES = new Set<string>([
  "pending",
  "receiving",
  "validating",
  "completed",
  "cancelled",
  "failed",
]);

/**
 * Strictly parses a raw Phase 4 server message string or object.
 * Returns an immutable Phase4ServerMessage if valid, or null if malformed.
 */
export function parsePhase4ServerMessage(raw: unknown): Phase4ServerMessage | null {
  let obj: unknown = raw;
  if (typeof raw === "string") {
    try {
      obj = JSON.parse(raw);
    } catch {
      return null;
    }
  }

  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
    return null;
  }

  const rec = obj as Record<string, unknown>;
  const msgType = rec.type;
  if (typeof msgType !== "string") {
    return null;
  }

  switch (msgType) {
    case "pairing_challenge": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "challenge_id", "expires_at"]))) {
        return null;
      }
      if (!isValidCanonicalId(rec.request_id) || !isValidCanonicalId(rec.challenge_id)) {
        return null;
      }
      if (typeof rec.expires_at !== "number" || !Number.isFinite(rec.expires_at) || rec.expires_at <= 0) {
        return null;
      }
      return Object.freeze({
        type: "pairing_challenge",
        request_id: rec.request_id,
        challenge_id: rec.challenge_id,
        expires_at: rec.expires_at,
      });
    }

    case "pairing_confirmed": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "device_id", "expires_at", "protocol_version"]))) {
        return null;
      }
      if (
        !isValidCanonicalId(rec.request_id) ||
        !isValidOpaqueId(rec.device_id) ||
        typeof rec.expires_at !== "number" ||
        !Number.isFinite(rec.expires_at) ||
        rec.protocol_version !== 1
      ) {
        return null;
      }
      return Object.freeze({
        type: "pairing_confirmed",
        request_id: rec.request_id,
        device_id: rec.device_id,
        expires_at: rec.expires_at,
        protocol_version: 1,
      });
    }

    case "pairing_update": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "status", "challenge_id", "device_id"]))) {
        return null;
      }
      if (!isValidCanonicalId(rec.request_id) || typeof rec.status !== "string" || !VALID_PAIRING_STATUSES.has(rec.status)) {
        return null;
      }
      const challengeId = rec.challenge_id;
      const deviceId = rec.device_id;
      if (rec.status === "revoked") {
        if (!isValidOpaqueId(deviceId) || challengeId !== undefined) return null;
      } else if (!isValidCanonicalId(challengeId) || deviceId !== undefined) {
        return null;
      }
      return Object.freeze({
        type: "pairing_update",
        request_id: rec.request_id,
        status: rec.status as "cancelled" | "revoked" | "expired",
        ...(challengeId !== undefined ? { challenge_id: challengeId } : {}),
        ...(deviceId !== undefined ? { device_id: deviceId } : {}),
      });
    }

    case "pairing_error": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "code"]))) {
        return null;
      }
      if (!isValidCanonicalId(rec.request_id) || !isPairingErrorCode(rec.code)) {
        return null;
      }
      return Object.freeze({
        type: "pairing_error",
        request_id: rec.request_id,
        code: rec.code,
      });
    }

    case "handoff_offer": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "handoff_id", "task_id", "summary", "expires_at"]))) {
        return null;
      }
      if (
        !isValidCanonicalId(rec.request_id) ||
        !isValidCanonicalId(rec.handoff_id) ||
        !isValidOpaqueId(rec.task_id) ||
        !isValidSummaryText(rec.summary)
      ) {
        return null;
      }
      if (typeof rec.expires_at !== "number" || !Number.isFinite(rec.expires_at) || rec.expires_at <= 0) {
        return null;
      }
      return Object.freeze({
        type: "handoff_offer",
        request_id: rec.request_id,
        handoff_id: rec.handoff_id,
        task_id: rec.task_id,
        summary: rec.summary,
        expires_at: rec.expires_at,
      });
    }

    case "handoff_update": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "handoff_id", "status"]))) {
        return null;
      }
      if (
        !isValidCanonicalId(rec.request_id) ||
        !isValidCanonicalId(rec.handoff_id) ||
        typeof rec.status !== "string" ||
        !VALID_HANDOFF_STATUSES.has(rec.status)
      ) {
        return null;
      }
      return Object.freeze({
        type: "handoff_update",
        request_id: rec.request_id,
        handoff_id: rec.handoff_id,
        status: rec.status as "offered" | "accepted" | "rejected" | "cancelled" | "expired",
      });
    }

    case "handoff_error": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "handoff_id", "code"]))) {
        return null;
      }
      if (
        !isValidCanonicalId(rec.request_id) ||
        !isHandoffErrorCode(rec.code)
      ) {
        return null;
      }
      if (rec.handoff_id !== undefined && !isValidCanonicalId(rec.handoff_id)) {
        return null;
      }
      return Object.freeze({
        type: "handoff_error",
        request_id: rec.request_id,
        ...(rec.handoff_id !== undefined ? { handoff_id: rec.handoff_id } : {}),
        code: rec.code,
      });
    }

    case "visual_transfer_ready": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "transfer_id", "expires_at"]))) {
        return null;
      }
      if (!isValidCanonicalId(rec.request_id) || !isValidOpaqueId(rec.transfer_id) || typeof rec.expires_at !== "number" || !Number.isFinite(rec.expires_at)) {
        return null;
      }
      return Object.freeze({
        type: "visual_transfer_ready",
        request_id: rec.request_id,
        transfer_id: rec.transfer_id,
        expires_at: rec.expires_at,
      });
    }

    case "visual_transfer_update": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "transfer_id", "status", "bytes_received"]))) {
        return null;
      }
      if (
        !isValidCanonicalId(rec.request_id) ||
        !isValidOpaqueId(rec.transfer_id) ||
        typeof rec.status !== "string" ||
        !VALID_VISUAL_TRANSFER_STATUSES.has(rec.status) ||
        !Number.isInteger(rec.bytes_received) ||
        (rec.bytes_received as number) < 0 ||
        (rec.bytes_received as number) > 1048576
      ) {
        return null;
      }
      return Object.freeze({
        type: "visual_transfer_update",
        request_id: rec.request_id,
        transfer_id: rec.transfer_id,
        status: rec.status as "pending" | "receiving" | "validating" | "completed" | "cancelled" | "failed",
        bytes_received: rec.bytes_received as number,
      });
    }

    case "visual_transfer_complete": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "transfer_id", "content_hash"]))) {
        return null;
      }
      if (!isValidCanonicalId(rec.request_id) || !isValidOpaqueId(rec.transfer_id)) {
        return null;
      }
      if (typeof rec.content_hash !== "string" || !/^sha256\.[0-9a-f]{64}$/.test(rec.content_hash)) {
        return null;
      }
      return Object.freeze({
        type: "visual_transfer_complete",
        request_id: rec.request_id,
        transfer_id: rec.transfer_id,
        content_hash: rec.content_hash,
      });
    }

    case "visual_transfer_error": {
      if (!hasOnlyKeys(rec, new Set(["type", "request_id", "transfer_id", "code"]))) {
        return null;
      }
      if (
        !isValidCanonicalId(rec.request_id) ||
        !isVisualTransferErrorCode(rec.code)
      ) {
        return null;
      }
      if (rec.transfer_id !== undefined && !isValidOpaqueId(rec.transfer_id)) {
        return null;
      }
      return Object.freeze({
        type: "visual_transfer_error",
        request_id: rec.request_id,
        ...(rec.transfer_id !== undefined ? { transfer_id: rec.transfer_id } : {}),
        code: rec.code,
      });
    }

    default:
      return null;
  }
}

// --------------------------------------------------------------------------
// Encoders for Outbound Phase 4 Client Messages
// --------------------------------------------------------------------------

export function encodePairingPrepare(requestId: string) {
  if (!isValidCanonicalId(requestId)) return null;
  return {
    type: "pairing_prepare",
    request_id: requestId,
  };
}

export function encodePairingConfirm(requestId: string, challengeId: string, code: string) {
  if (!isValidCanonicalId(requestId) || !isValidCanonicalId(challengeId)) return null;
  if (typeof code !== "string" || !/^[0-9A-F]{6}$/.test(code)) return null;
  return {
    type: "pairing_confirm",
    request_id: requestId,
    challenge_id: challengeId,
    code: code.trim(),
  };
}

export function encodePairingCancel(requestId: string, challengeId: string) {
  if (!isValidCanonicalId(requestId) || !isValidCanonicalId(challengeId)) return null;
  return {
    type: "pairing_cancel",
    request_id: requestId,
    challenge_id: challengeId,
  };
}

export function encodeHandoffAccept(requestId: string, handoffId: string) {
  if (!isValidCanonicalId(requestId) || !isValidCanonicalId(handoffId)) return null;
  return {
    type: "handoff_accept",
    request_id: requestId,
    handoff_id: handoffId,
    acknowledged: true,
  };
}

export function encodeHandoffReject(requestId: string, handoffId: string) {
  if (!isValidCanonicalId(requestId) || !isValidCanonicalId(handoffId)) return null;
  return {
    type: "handoff_reject",
    request_id: requestId,
    handoff_id: handoffId,
  };
}

export function encodeHandoffCancel(requestId: string, handoffId: string) {
  if (!isValidCanonicalId(requestId) || !isValidCanonicalId(handoffId)) return null;
  return {
    type: "handoff_cancel",
    request_id: requestId,
    handoff_id: handoffId,
  };
}

export function encodeVisualTransferBegin(
  requestId: string,
  handoffId: string,
  mimeType: string,
  fileSize: number,
  width: number,
  height: number,
) {
  if (!isValidCanonicalId(requestId) || !isValidCanonicalId(handoffId)) return null;
  if (mimeType !== "image/png" && mimeType !== "image/jpeg") return null;
  if (typeof fileSize !== "number" || fileSize <= 0 || fileSize > 1048576) return null;
  if (!Number.isInteger(width) || width < 1 || width > 4096) return null;
  if (!Number.isInteger(height) || height < 1 || height > 4096) return null;
  return {
    type: "visual_transfer_begin",
    request_id: requestId,
    handoff_id: handoffId,
    mime_type: mimeType,
    size_bytes: fileSize,
    width,
    height,
    frame_count: 1,
  };
}

export function encodeVisualTransferCancel(requestId: string, transferId: string) {
  if (!isValidCanonicalId(requestId) || !isValidOpaqueId(transferId)) return null;
  return {
    type: "visual_transfer_cancel",
    request_id: requestId,
    transfer_id: transferId,
  };
}
