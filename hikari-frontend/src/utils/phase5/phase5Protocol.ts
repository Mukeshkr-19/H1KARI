/**
 * Strict Phase 5 protocol parser and encoders.
 * Pure: no storage, transport, or logging side effects.
 */

import { isValidCanonicalId } from "../phase4/identifiers";

export const PHASE5_SERVER_MESSAGE_TYPES = Object.freeze([
  "phase5_session_update",
  "phase5_capability_proposal",
  "phase5_approval_required",
  "phase5_helper_grants",
  "phase5_error",
] as const);

export type Phase5ServerMessageType = (typeof PHASE5_SERVER_MESSAGE_TYPES)[number];

export const PHASE5_ERROR_CODES = Object.freeze([
  "invalid_request",
  "unauthorized",
  "unavailable",
  "denied",
  "expired",
  "revoked",
  "locked",
  "closed",
  "approval_required",
  "not_found",
  "stale_request",
  "duplicate_request",
  "internal_error",
] as const);

export type Phase5ErrorCode = (typeof PHASE5_ERROR_CODES)[number];

export const PHASE5_SAFE_ERROR_MESSAGES: Readonly<Record<Phase5ErrorCode, string>> = Object.freeze({
  invalid_request: "The Phase 5 request was invalid.",
  unauthorized: "Phase 5 requires a paired owner connection.",
  unavailable: "Phase 5 is temporarily unavailable.",
  denied: "The Phase 5 request was denied.",
  expired: "The Phase 5 session expired.",
  revoked: "The Phase 5 session was revoked.",
  locked: "The Phase 5 session is locked.",
  closed: "The Phase 5 session is closed.",
  approval_required: "Owner approval is required.",
  not_found: "The Phase 5 resource was not found.",
  stale_request: "That approval request is no longer valid.",
  duplicate_request: "That request was already submitted.",
  internal_error: "A Phase 5 error occurred.",
});

const SESSION_STATES = new Set([
  "inactive",
  "pending_owner_approval",
  "active",
  "expired",
  "revoked",
  "locked",
  "closed",
]);

const CAPABILITIES = new Set([
  "teach_me",
  "guide_my_hands",
  "care",
  "child_mode",
  "trusted_helper_access",
]);

function hasOnlyKeys(obj: Record<string, unknown>, allowed: Set<string>): boolean {
  for (const key of Object.keys(obj)) {
    if (!allowed.has(key)) return false;
  }
  return true;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isBoundedText(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= max;
}

export type Phase5SessionUpdate = Readonly<{
  type: "phase5_session_update";
  request_id: string;
  protocol_version: 1;
  session_id: string;
  session_type: "owner" | "child" | "trusted_helper";
  state: string;
  expires_at: number;
  capabilities: readonly string[];
}>;

export type Phase5CapabilityProposal = Readonly<{
  type: "phase5_capability_proposal";
  request_id: string;
  protocol_version: 1;
  capability: "teach_me" | "guide_my_hands" | "care";
  outcome: "allow" | "require_approval";
  approval_required: boolean;
  summary: string;
  items: readonly string[];
  installs_skills: false;
  camera_accessed: false;
  contact_made: false;
  uncertainty_disclosed?: boolean;
  emergency_limitation?: string;
}>;

export type Phase5ApprovalRequired = Readonly<{
  type: "phase5_approval_required";
  request_id: string;
  protocol_version: 1;
  pending_request_id: string;
  capability: string;
  reason_code: Phase5ErrorCode;
  summary?: string;
}>;

export type Phase5HelperGrant = Readonly<{
  grant_id: string;
  helper_actor_id: string;
  capability: string;
  expires_at: number;
  revoked: boolean;
  data_subject?: string;
}>;

export type Phase5HelperGrants = Readonly<{
  type: "phase5_helper_grants";
  request_id: string;
  protocol_version: 1;
  grants: readonly Phase5HelperGrant[];
}>;

export type Phase5ErrorMessage = Readonly<{
  type: "phase5_error";
  request_id: string;
  protocol_version: 1;
  code: Phase5ErrorCode;
}>;

export type Phase5ServerMessage =
  | Phase5SessionUpdate
  | Phase5CapabilityProposal
  | Phase5ApprovalRequired
  | Phase5HelperGrants
  | Phase5ErrorMessage;

function parseSessionUpdate(obj: Record<string, unknown>): Phase5SessionUpdate | null {
  const allowed = new Set([
    "type",
    "request_id",
    "protocol_version",
    "session_id",
    "session_type",
    "state",
    "expires_at",
    "capabilities",
  ]);
  if (!hasOnlyKeys(obj, allowed)) return null;
  if (obj.protocol_version !== 1) return null;
  if (!isValidCanonicalId(obj.request_id) || !isValidCanonicalId(obj.session_id)) return null;
  if (typeof obj.session_type !== "string" || !["owner", "child", "trusted_helper"].includes(obj.session_type)) {
    return null;
  }
  if (typeof obj.state !== "string" || !SESSION_STATES.has(obj.state)) return null;
  if (!isFiniteNumber(obj.expires_at)) return null;
  if (!Array.isArray(obj.capabilities) || obj.capabilities.length > 8) return null;
  if (!obj.capabilities.every((c) => typeof c === "string" && CAPABILITIES.has(c))) return null;
  return Object.freeze({
    type: "phase5_session_update",
    request_id: obj.request_id,
    protocol_version: 1,
    session_id: obj.session_id,
    session_type: obj.session_type as Phase5SessionUpdate["session_type"],
    state: obj.state,
    expires_at: obj.expires_at,
    capabilities: Object.freeze([...obj.capabilities]),
  });
}

function parseProposal(obj: Record<string, unknown>): Phase5CapabilityProposal | null {
  const allowed = new Set([
    "type",
    "request_id",
    "protocol_version",
    "capability",
    "outcome",
    "approval_required",
    "summary",
    "items",
    "installs_skills",
    "camera_accessed",
    "contact_made",
    "uncertainty_disclosed",
    "emergency_limitation",
  ]);
  if (!hasOnlyKeys(obj, allowed)) return null;
  if (obj.protocol_version !== 1 || !isValidCanonicalId(obj.request_id)) return null;
  if (obj.capability !== "teach_me" && obj.capability !== "guide_my_hands" && obj.capability !== "care") {
    return null;
  }
  if (obj.outcome !== "allow" && obj.outcome !== "require_approval") return null;
  if (typeof obj.approval_required !== "boolean") return null;
  if (!isBoundedText(obj.summary, 512)) return null;
  if (!Array.isArray(obj.items) || obj.items.length > 32) return null;
  if (!obj.items.every((item) => isBoundedText(item, 512))) return null;
  if (obj.installs_skills !== false || obj.camera_accessed !== false || obj.contact_made !== false) {
    return null;
  }
  const message: Phase5CapabilityProposal = {
    type: "phase5_capability_proposal",
    request_id: obj.request_id,
    protocol_version: 1,
    capability: obj.capability,
    outcome: obj.outcome,
    approval_required: obj.approval_required,
    summary: obj.summary,
    items: Object.freeze([...obj.items]) as readonly string[],
    installs_skills: false,
    camera_accessed: false,
    contact_made: false,
  };
  if ("uncertainty_disclosed" in obj) {
    if (typeof obj.uncertainty_disclosed !== "boolean") return null;
    (message as { uncertainty_disclosed?: boolean }).uncertainty_disclosed = obj.uncertainty_disclosed;
  }
  if ("emergency_limitation" in obj) {
    if (typeof obj.emergency_limitation !== "string" || obj.emergency_limitation.length > 512) return null;
    (message as { emergency_limitation?: string }).emergency_limitation = obj.emergency_limitation;
  }
  return Object.freeze(message);
}

function parseApproval(obj: Record<string, unknown>): Phase5ApprovalRequired | null {
  const allowed = new Set([
    "type",
    "request_id",
    "protocol_version",
    "pending_request_id",
    "capability",
    "reason_code",
    "summary",
  ]);
  if (!hasOnlyKeys(obj, allowed)) return null;
  if (obj.protocol_version !== 1) return null;
  if (!isValidCanonicalId(obj.request_id) || !isValidCanonicalId(obj.pending_request_id)) return null;
  if (typeof obj.capability !== "string" || !CAPABILITIES.has(obj.capability)) return null;
  if (typeof obj.reason_code !== "string" || !(PHASE5_ERROR_CODES as readonly string[]).includes(obj.reason_code)) {
    return null;
  }
  const message: Phase5ApprovalRequired = {
    type: "phase5_approval_required",
    request_id: obj.request_id,
    protocol_version: 1,
    pending_request_id: obj.pending_request_id,
    capability: obj.capability,
    reason_code: obj.reason_code as Phase5ErrorCode,
  };
  if ("summary" in obj) {
    if (typeof obj.summary !== "string" || obj.summary.length > 512) return null;
    (message as { summary?: string }).summary = obj.summary;
  }
  return Object.freeze(message);
}

function parseHelperGrants(obj: Record<string, unknown>): Phase5HelperGrants | null {
  const allowed = new Set(["type", "request_id", "protocol_version", "grants"]);
  if (!hasOnlyKeys(obj, allowed)) return null;
  if (obj.protocol_version !== 1 || !isValidCanonicalId(obj.request_id)) return null;
  if (!Array.isArray(obj.grants) || obj.grants.length > 50) return null;
  const grants: Phase5HelperGrant[] = [];
  for (const raw of obj.grants) {
    if (!raw || typeof raw !== "object") return null;
    const g = raw as Record<string, unknown>;
    const gAllowed = new Set([
      "grant_id",
      "helper_actor_id",
      "capability",
      "expires_at",
      "revoked",
      "data_subject",
    ]);
    if (!hasOnlyKeys(g, gAllowed)) return null;
    if (!isValidCanonicalId(g.grant_id)) return null;
    if (typeof g.helper_actor_id !== "string" || g.helper_actor_id.length > 128) return null;
    if (typeof g.capability !== "string" || !CAPABILITIES.has(g.capability)) return null;
    if (!isFiniteNumber(g.expires_at) || typeof g.revoked !== "boolean") return null;
    const grant: Phase5HelperGrant = {
      grant_id: g.grant_id,
      helper_actor_id: g.helper_actor_id,
      capability: g.capability,
      expires_at: g.expires_at,
      revoked: g.revoked,
    };
    if ("data_subject" in g) {
      if (typeof g.data_subject !== "string" || g.data_subject.length > 128) return null;
      (grant as { data_subject?: string }).data_subject = g.data_subject;
    }
    grants.push(Object.freeze(grant));
  }
  return Object.freeze({
    type: "phase5_helper_grants",
    request_id: obj.request_id,
    protocol_version: 1 as const,
    grants: Object.freeze(grants),
  });
}

function parseError(obj: Record<string, unknown>): Phase5ErrorMessage | null {
  const allowed = new Set(["type", "request_id", "protocol_version", "code"]);
  if (!hasOnlyKeys(obj, allowed)) return null;
  if (obj.protocol_version !== 1 || !isValidCanonicalId(obj.request_id)) return null;
  if (typeof obj.code !== "string" || !(PHASE5_ERROR_CODES as readonly string[]).includes(obj.code)) {
    return null;
  }
  return Object.freeze({
    type: "phase5_error",
    request_id: obj.request_id,
    protocol_version: 1,
    code: obj.code as Phase5ErrorCode,
  });
}

export function parsePhase5ServerMessage(raw: unknown): Phase5ServerMessage | null {
  let value: unknown = raw;
  if (typeof raw === "string") {
    try {
      value = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== "object") return null;
  const obj = value as Record<string, unknown>;
  switch (obj.type) {
    case "phase5_session_update":
      return parseSessionUpdate(obj);
    case "phase5_capability_proposal":
      return parseProposal(obj);
    case "phase5_approval_required":
      return parseApproval(obj);
    case "phase5_helper_grants":
      return parseHelperGrants(obj);
    case "phase5_error":
      return parseError(obj);
    default:
      return null;
  }
}

export function encodePhase5ClientMessage(
  type: string,
  fields: Record<string, unknown>,
): string {
  return JSON.stringify({ type, protocol_version: 1, ...fields });
}

export function phase5ErrorMessage(code: Phase5ErrorCode): string {
  return PHASE5_SAFE_ERROR_MESSAGES[code];
}
