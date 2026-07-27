/**
 * Strict Phase 6 Command-Center protocol parser, types, and encoders.
 * Pure: no storage, transport, or logging side effects.
 */

import { isValidCanonicalId } from "../phase4/identifiers";

export const PHASE6_SERVER_MESSAGE_TYPES = Object.freeze([
  "phase6_integration_status",
  "phase6_agent_run_update",
  "phase6_time_sense_update",
  "phase6_repo_intel_update",
  "phase6_skill_evolution_update",
  "phase6_home_assistant_proposal",
  "phase6_encrypted_sync_update",
  "phase6_remote_worker_update",
  "phase6_model_eval_update",
  "phase6_error",
] as const);

export type Phase6ServerMessageType = (typeof PHASE6_SERVER_MESSAGE_TYPES)[number];

export const PHASE6_ERROR_CODES = Object.freeze([
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

export type Phase6ErrorCode = (typeof PHASE6_ERROR_CODES)[number];

export const PHASE6_SAFE_ERROR_MESSAGES: Readonly<Record<Phase6ErrorCode, string>> = Object.freeze({
  invalid_request: "The Phase 6 request was invalid.",
  unauthorized: "Phase 6 requires a paired owner connection.",
  unavailable: "Phase 6 features are temporarily unavailable.",
  denied: "The Phase 6 request was denied.",
  expired: "The Phase 6 request or proposal expired.",
  revoked: "The Phase 6 resource was revoked.",
  locked: "The Phase 6 session is locked.",
  closed: "The Phase 6 session is closed.",
  approval_required: "Owner approval is required.",
  not_found: "The Phase 6 resource was not found.",
  stale_request: "That approval request is no longer valid.",
  duplicate_request: "That request was already submitted.",
  internal_error: "A Phase 6 transport error occurred.",
});

const UNICODE_FORMAT_REGEX = /[\u200e\u200f\u202a-\u202e\u2060-\u2069\ufeff]/;

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function generatePhase6RequestId(): string {
  const timePart = Date.now().toString(36);
  const randPart = Math.random().toString(36).substring(2, 8);
  return `req_${timePart}_${randPart}`;
}

function isNonNegativeInt(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isSafeText(value: unknown, maxLen = 1024): value is string {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLen) {
    return false;
  }
  if (UNICODE_FORMAT_REGEX.test(value)) {
    return false;
  }
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    if ((code < 32 && code !== 9 && code !== 10 && code !== 13) || code === 127) {
      return false;
    }
  }
  return true;
}

function hasOnlyKeys(obj: Record<string, unknown>, allowed: Set<string>): boolean {
  for (const key of Object.keys(obj)) {
    if (!allowed.has(key)) return false;
  }
  return true;
}

export type Phase6IntegrationStatus = Readonly<{
  type: "phase6_integration_status";
  request_id: string;
  protocol_version: 1;
  integration_id: string;
  name: string;
  status:
    | "unavailable"
    | "disabled"
    | "configuring"
    | "ready"
    | "degraded"
    | "approval_required"
    | "active"
    | "cancelling"
    | "failed"
    | "revoked";
  details_summary?: string;
}>;

export type Phase6AgentRunUpdate = Readonly<{
  type: "phase6_agent_run_update";
  request_id: string;
  protocol_version: 1;
  run_id: string;
  state:
    | "preview"
    | "waiting_for_approval"
    | "running"
    | "observing"
    | "correcting"
    | "succeeded"
    | "denied"
    | "failed"
    | "cancelled"
    | "exhausted";
  step_count: number;
  action_count: number;
  budget_limit: number;
  safe_summary: string;
}>;

export type Phase6TimeSenseUpdate = Readonly<{
  type: "phase6_time_sense_update";
  request_id: string;
  protocol_version: 1;
  task_age_seconds: number;
  heartbeat_status: string;
  stuck_reason?: string;
  next_allowed_checkin: number;
  suppression_state: string;
  background_status: string;
}>;

export type Phase6RepoHit = Readonly<{
  path: string;
  line: number;
  symbol?: string;
  score: number;
  provenance: string;
}>;

export type Phase6RepoIntelUpdate = Readonly<{
  type: "phase6_repo_intel_update";
  request_id: string;
  protocol_version: 1;
  scan_state: string;
  query_summary: string;
  hit_count: number;
  results: readonly Phase6RepoHit[];
}>;

export type Phase6SkillEvolutionUpdate = Readonly<{
  type: "phase6_skill_evolution_update";
  request_id: string;
  protocol_version: 1;
  package_id: string;
  version: string;
  state: "proposal" | "validation" | "review" | "rejection" | "revocation" | "install_plan_ready";
  permissions_summary: readonly string[];
  rollback_ready: boolean;
  allows_auto_install: false;
}>;

export type Phase6HomeAssistantProposal = Readonly<{
  type: "phase6_home_assistant_proposal";
  request_id: string;
  protocol_version: 1;
  proposal_id: string;
  entity_id: string;
  domain: string;
  service: string;
  risk: "low" | "medium" | "high" | "critical";
  effect_summary: string;
  expires_at: number;
  nonce: string;
}>;

export type Phase6EncryptedSyncUpdate = Readonly<{
  type: "phase6_encrypted_sync_update";
  request_id: string;
  protocol_version: 1;
  enabled: boolean;
  configured: boolean;
  status: string;
  conflict_count: number;
  exposes_plaintext: false;
}>;

export type Phase6RemoteWorkerUpdate = Readonly<{
  type: "phase6_remote_worker_update";
  request_id: string;
  protocol_version: 1;
  job_id: string;
  worker_id: string;
  state: string;
  has_evidence: boolean;
  quarantined: boolean;
  verified_local_authority: false;
}>;

export type Phase6ModelEvalUpdate = Readonly<{
  type: "phase6_model_eval_update";
  request_id: string;
  protocol_version: 1;
  candidate_id: string;
  privacy_class: "local_only" | "gateway_ok" | "remote_ok";
  capabilities: readonly string[];
  quality_score: number;
  safety_score: number;
  latency_ms: number;
  recommendation: string;
  rejection_reason?: string;
}>;

export type Phase6ErrorMessage = Readonly<{
  type: "phase6_error";
  request_id: string;
  protocol_version: 1;
  code: Phase6ErrorCode;
  message: string;
}>;

export type Phase6ServerMessage =
  | Phase6IntegrationStatus
  | Phase6AgentRunUpdate
  | Phase6TimeSenseUpdate
  | Phase6RepoIntelUpdate
  | Phase6SkillEvolutionUpdate
  | Phase6HomeAssistantProposal
  | Phase6EncryptedSyncUpdate
  | Phase6RemoteWorkerUpdate
  | Phase6ModelEvalUpdate
  | Phase6ErrorMessage;

/**
 * Strict parser for Phase 6 WebSocket server messages.
 * Fails closed on unknown fields, malformed types, or invalid boundaries.
 */
export function parsePhase6ServerMessage(input: unknown): Phase6ServerMessage | null {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    return null;
  }
  const obj = input as Record<string, unknown>;

  if (
    typeof obj.type !== "string" ||
    !PHASE6_SERVER_MESSAGE_TYPES.includes(obj.type as Phase6ServerMessageType) ||
    !isValidCanonicalId(obj.request_id) ||
    obj.protocol_version !== 1
  ) {
    return null;
  }

  const type = obj.type as Phase6ServerMessageType;

  if (type === "phase6_error") {
    if (!hasOnlyKeys(obj, new Set(["type", "request_id", "protocol_version", "code", "message"]))) return null;
    if (typeof obj.code !== "string" || !PHASE6_ERROR_CODES.includes(obj.code as Phase6ErrorCode)) return null;
    return Object.freeze({
      type: "phase6_error",
      request_id: obj.request_id as string,
      protocol_version: 1,
      code: obj.code as Phase6ErrorCode,
      message: PHASE6_SAFE_ERROR_MESSAGES[obj.code as Phase6ErrorCode],
    });
  }

  if (type === "phase6_integration_status") {
    if (!hasOnlyKeys(obj, new Set(["type", "request_id", "protocol_version", "integration_id", "name", "status", "details_summary"]))) return null;
    if (!isValidCanonicalId(obj.integration_id) || !isSafeText(obj.name, 128)) return null;
    const validStatuses = new Set(["unavailable", "disabled", "configuring", "ready", "degraded", "approval_required", "active", "cancelling", "failed", "revoked"]);
    if (typeof obj.status !== "string" || !validStatuses.has(obj.status)) return null;
    if (obj.details_summary !== undefined && !isSafeText(obj.details_summary, 512)) return null;

    return Object.freeze({
      type: "phase6_integration_status",
      request_id: obj.request_id as string,
      protocol_version: 1,
      integration_id: obj.integration_id as string,
      name: obj.name as string,
      status: obj.status as Phase6IntegrationStatus["status"],
      details_summary: obj.details_summary as string | undefined,
    });
  }

  if (type === "phase6_agent_run_update") {
    if (!hasOnlyKeys(obj, new Set(["type", "request_id", "protocol_version", "run_id", "state", "step_count", "action_count", "budget_limit", "safe_summary"]))) return null;
    if (!isValidCanonicalId(obj.run_id)) return null;
    const validStates = new Set(["preview", "waiting_for_approval", "running", "observing", "correcting", "succeeded", "denied", "failed", "cancelled", "exhausted"]);
    if (typeof obj.state !== "string" || !validStates.has(obj.state)) return null;
    if (!isNonNegativeInt(obj.step_count) || !isNonNegativeInt(obj.action_count) || !isNonNegativeInt(obj.budget_limit)) return null;
    if (!isSafeText(obj.safe_summary, 512)) return null;

    return Object.freeze({
      type: "phase6_agent_run_update",
      request_id: obj.request_id as string,
      protocol_version: 1,
      run_id: obj.run_id as string,
      state: obj.state as Phase6AgentRunUpdate["state"],
      step_count: obj.step_count as number,
      action_count: obj.action_count as number,
      budget_limit: obj.budget_limit as number,
      safe_summary: obj.safe_summary as string,
    });
  }

  if (type === "phase6_home_assistant_proposal") {
    if (!hasOnlyKeys(obj, new Set(["type", "request_id", "protocol_version", "proposal_id", "entity_id", "domain", "service", "risk", "effect_summary", "expires_at", "nonce"]))) return null;
    if (!isValidCanonicalId(obj.proposal_id)) return null;
    if (!isSafeText(obj.entity_id, 128) || obj.entity_id.includes("*")) return null;
    if (!isSafeText(obj.domain, 128) || obj.domain.includes("*")) return null;
    if (!isSafeText(obj.service, 128) || obj.service.includes("*")) return null;
    if (!new Set(["low", "medium", "high", "critical"]).has(obj.risk as string)) return null;
    if (!isSafeText(obj.effect_summary, 512) || !isFiniteNumber(obj.expires_at) || !isSafeText(obj.nonce, 80)) return null;

    return Object.freeze({
      type: "phase6_home_assistant_proposal",
      request_id: obj.request_id as string,
      protocol_version: 1,
      proposal_id: obj.proposal_id as string,
      entity_id: obj.entity_id as string,
      domain: obj.domain as string,
      service: obj.service as string,
      risk: obj.risk as Phase6HomeAssistantProposal["risk"],
      effect_summary: obj.effect_summary as string,
      expires_at: obj.expires_at as number,
      nonce: obj.nonce as string,
    });
  }

  if (type === "phase6_skill_evolution_update") {
    if (!hasOnlyKeys(obj, new Set(["type", "request_id", "protocol_version", "package_id", "version", "state", "permissions_summary", "rollback_ready", "allows_auto_install"]))) return null;
    if (!isValidCanonicalId(obj.package_id) || !isSafeText(obj.version, 64)) return null;
    const validStates = new Set(["proposal", "validation", "review", "rejection", "revocation", "install_plan_ready"]);
    if (typeof obj.state !== "string" || !validStates.has(obj.state)) return null;
    if (!Array.isArray(obj.permissions_summary) || obj.permissions_summary.length > 64) return null;
    if (typeof obj.rollback_ready !== "boolean" || obj.allows_auto_install !== false) return null;

    const cleanPerms: string[] = [];
    for (const p of obj.permissions_summary) {
      if (!isSafeText(p, 128)) return null;
      cleanPerms.push(p);
    }

    return Object.freeze({
      type: "phase6_skill_evolution_update",
      request_id: obj.request_id as string,
      protocol_version: 1,
      package_id: obj.package_id as string,
      version: obj.version as string,
      state: obj.state as Phase6SkillEvolutionUpdate["state"],
      permissions_summary: Object.freeze(cleanPerms),
      rollback_ready: obj.rollback_ready,
      allows_auto_install: false,
    });
  }

  if (type === "phase6_model_eval_update") {
    if (!hasOnlyKeys(obj, new Set(["type", "request_id", "protocol_version", "candidate_id", "privacy_class", "capabilities", "quality_score", "safety_score", "latency_ms", "recommendation", "rejection_reason"]))) return null;
    if (!isValidCanonicalId(obj.candidate_id)) return null;
    if (!new Set(["local_only", "gateway_ok", "remote_ok"]).has(obj.privacy_class as string)) return null;
    if (!isFiniteNumber(obj.quality_score) || obj.quality_score < 0 || obj.quality_score > 1) return null;
    if (!isFiniteNumber(obj.safety_score) || obj.safety_score < 0 || obj.safety_score > 1) return null;
    if (!isFiniteNumber(obj.latency_ms) || obj.latency_ms < 0) return null;
    if (!isSafeText(obj.recommendation, 128)) return null;
    if (obj.rejection_reason !== undefined && !isSafeText(obj.rejection_reason, 128)) return null;
    if (!Array.isArray(obj.capabilities) || obj.capabilities.length > 64) return null;
    const cleanCapabilities: string[] = [];
    for (const capability of obj.capabilities) {
      if (!isSafeText(capability, 64)) return null;
      cleanCapabilities.push(capability);
    }

    return Object.freeze({
      type: "phase6_model_eval_update",
      request_id: obj.request_id as string,
      protocol_version: 1,
      candidate_id: obj.candidate_id as string,
      privacy_class: obj.privacy_class as Phase6ModelEvalUpdate["privacy_class"],
      capabilities: Object.freeze(cleanCapabilities),
      quality_score: obj.quality_score as number,
      safety_score: obj.safety_score as number,
      latency_ms: obj.latency_ms as number,
      recommendation: obj.recommendation as string,
      rejection_reason: obj.rejection_reason as string | undefined,
    });
  }

  if (type === "phase6_encrypted_sync_update") {
    if (!hasOnlyKeys(obj, new Set(["type", "request_id", "protocol_version", "enabled", "configured", "status", "conflict_count", "exposes_plaintext"]))) return null;
    if (typeof obj.enabled !== "boolean" || typeof obj.configured !== "boolean" || obj.exposes_plaintext !== false) return null;
    if (!isSafeText(obj.status, 64) || !isNonNegativeInt(obj.conflict_count)) return null;
    return Object.freeze({
      type: "phase6_encrypted_sync_update",
      request_id: obj.request_id as string,
      protocol_version: 1,
      enabled: obj.enabled,
      configured: obj.configured,
      status: obj.status as string,
      conflict_count: obj.conflict_count as number,
      exposes_plaintext: false,
    });
  }

  if (type === "phase6_remote_worker_update") {
    if (!hasOnlyKeys(obj, new Set(["type", "request_id", "protocol_version", "job_id", "worker_id", "state", "has_evidence", "quarantined", "verified_local_authority"]))) return null;
    if (!isValidCanonicalId(obj.job_id) || !isValidCanonicalId(obj.worker_id)) return null;
    if (!isSafeText(obj.state, 64) || typeof obj.has_evidence !== "boolean" || typeof obj.quarantined !== "boolean" || obj.verified_local_authority !== false) return null;
    return Object.freeze({
      type: "phase6_remote_worker_update",
      request_id: obj.request_id as string,
      protocol_version: 1,
      job_id: obj.job_id as string,
      worker_id: obj.worker_id as string,
      state: obj.state as string,
      has_evidence: obj.has_evidence,
      quarantined: obj.quarantined,
      verified_local_authority: false,
    });
  }

  if (type === "phase6_time_sense_update") {
    if (!hasOnlyKeys(obj, new Set(["type", "request_id", "protocol_version", "task_age_seconds", "heartbeat_status", "stuck_reason", "next_allowed_checkin", "suppression_state", "background_status"]))) return null;
    if (!isFiniteNumber(obj.task_age_seconds) || obj.task_age_seconds < 0) return null;
    if (!isSafeText(obj.heartbeat_status, 64) || !isSafeText(obj.suppression_state, 64) || !isSafeText(obj.background_status, 64)) return null;
    if (obj.stuck_reason !== undefined && !isSafeText(obj.stuck_reason, 512)) return null;
    if (!isFiniteNumber(obj.next_allowed_checkin)) return null;

    return Object.freeze({
      type: "phase6_time_sense_update",
      request_id: obj.request_id as string,
      protocol_version: 1,
      task_age_seconds: obj.task_age_seconds as number,
      heartbeat_status: obj.heartbeat_status as string,
      stuck_reason: obj.stuck_reason as string | undefined,
      next_allowed_checkin: obj.next_allowed_checkin as number,
      suppression_state: obj.suppression_state as string,
      background_status: obj.background_status as string,
    });
  }

  if (type === "phase6_repo_intel_update") {
    if (!hasOnlyKeys(obj, new Set(["type", "request_id", "protocol_version", "scan_state", "query_summary", "hit_count", "results"]))) return null;
    if (!isSafeText(obj.scan_state, 64) || !isSafeText(obj.query_summary, 128) || !isNonNegativeInt(obj.hit_count)) return null;
    if (!Array.isArray(obj.results) || obj.results.length > 64 || obj.hit_count !== obj.results.length) return null;

    const cleanResults: Phase6RepoHit[] = [];
    for (const r of obj.results) {
      if (typeof r !== "object" || r === null) return null;
      const rObj = r as Record<string, unknown>;
      if (!hasOnlyKeys(rObj, new Set(["path", "line", "symbol", "score", "provenance"]))) return null;
      if (!isSafeText(rObj.path, 256) || !isNonNegativeInt(rObj.line) || !isFiniteNumber(rObj.score) || !isSafeText(rObj.provenance, 128)) return null;
      if ((rObj.score as number) < 0 || (rObj.score as number) > 1) return null;
      if (rObj.symbol !== undefined && !isSafeText(rObj.symbol, 128)) return null;
      cleanResults.push(Object.freeze({
        path: rObj.path as string,
        line: rObj.line as number,
        symbol: rObj.symbol as string | undefined,
        score: rObj.score as number,
        provenance: rObj.provenance as string,
      }));
    }

    return Object.freeze({
      type: "phase6_repo_intel_update",
      request_id: obj.request_id as string,
      protocol_version: 1,
      scan_state: obj.scan_state as string,
      query_summary: obj.query_summary as string,
      hit_count: obj.hit_count as number,
      results: Object.freeze(cleanResults),
    });
  }

  return null;
}

// Client frame builders

export function buildIntegrationListRequest(requestId: string) {
  if (!isValidCanonicalId(requestId)) {
    throw new Error("Invalid request_id for integration list request");
  }
  return Object.freeze({
    type: "phase6_integration_list_request",
    request_id: requestId,
    protocol_version: 1,
  });
}

export function buildHomeAssistantPrepareRequest(
  requestId: string,
  entityId: string,
  domain: string,
  service: string,
  risk: "low" | "medium" | "high" | "critical",
  effectSummary: string,
) {
  if (
    !isValidCanonicalId(requestId) ||
    !isSafeText(entityId, 128) ||
    !isSafeText(domain, 128) ||
    !isSafeText(service, 128) ||
    !isSafeText(effectSummary, 512) ||
    entityId.includes("*") ||
    domain.includes("*") ||
    service.includes("*")
  ) {
    throw new Error("Invalid parameters for Home Assistant prepare request");
  }
  return Object.freeze({
    type: "phase6_home_assistant_prepare_request",
    request_id: requestId,
    protocol_version: 1,
    entity_id: entityId,
    domain,
    service,
    risk,
    effect_summary: effectSummary,
  });
}

export function buildHomeAssistantConfirmRequest(requestId: string, proposalId: string, nonce: string) {
  if (!isValidCanonicalId(requestId) || !isValidCanonicalId(proposalId) || !isSafeText(nonce, 80)) {
    throw new Error("Invalid parameters for Home Assistant confirmation request");
  }
  return Object.freeze({
    type: "phase6_home_assistant_confirm_request",
    request_id: requestId,
    protocol_version: 1,
    proposal_id: proposalId,
    nonce,
  });
}

export function buildProposalCancelRequest(requestId: string, proposalId: string) {
  if (!isValidCanonicalId(requestId) || !isValidCanonicalId(proposalId)) {
    throw new Error("Invalid parameters for proposal cancel request");
  }
  return Object.freeze({
    type: "phase6_proposal_cancel_request",
    request_id: requestId,
    protocol_version: 1,
    proposal_id: proposalId,
  });
}

export function buildAgentRunRequest(
  requestId: string,
  runId: string,
  action: "preview" | "start" | "confirm" | "cancel" | "status",
  options?: { nonce?: string; budgetLimit?: number; taskSummary?: string },
) {
  if (!isValidCanonicalId(requestId) || !isValidCanonicalId(runId)) {
    throw new Error("Invalid parameters for agent run request");
  }
  const payload: Record<string, unknown> = {
    type: "phase6_agent_run_request",
    request_id: requestId,
    protocol_version: 1,
    action,
    run_id: runId,
  };
  if (options?.nonce) {
    if (!isSafeText(options.nonce, 80)) throw new Error("Invalid nonce");
    payload.nonce = options.nonce;
  }
  if (options?.budgetLimit !== undefined) {
    if (!isNonNegativeInt(options.budgetLimit)) throw new Error("Invalid budget_limit");
    payload.budget_limit = options.budgetLimit;
  }
  if (options?.taskSummary) {
    if (!isSafeText(options.taskSummary, 512)) throw new Error("Invalid task_summary");
    payload.task_summary = options.taskSummary;
  }
  return Object.freeze(payload);
}

export function buildSnapshotRefreshRequest(requestId: string, target: "all" | "time_sense" | "repo_intel" | "integrations") {
  if (!isValidCanonicalId(requestId)) {
    throw new Error("Invalid request_id for snapshot refresh");
  }
  return Object.freeze({
    type: "phase6_snapshot_refresh_request",
    request_id: requestId,
    protocol_version: 1,
    target,
  });
}
