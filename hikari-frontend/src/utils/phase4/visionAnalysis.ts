/**
 * Pure Phase 4 Vision Analysis frontend primitives and state reducer.
 *
 * Implements bounded immutable vision analysis state management, deterministic validation,
 * exact request/analysis correlation, safe error mapping, and zero external side effects.
 */

export type VisionAnalysisStatus =
  | "idle"
  | "preparing"
  | "awaiting_image"
  | "analyzing"
  | "completed"
  | "cancelled"
  | "expired"
  | "failed";

export type VisionCapability = "ocr" | "describe";

export type ObservationKind = "text" | "description";

export type VisionAnalysisErrorCode =
  | "unavailable"
  | "invalid_request"
  | "analysis_not_found"
  | "handoff_not_accepted"
  | "transfer_mismatch"
  | "analysis_expired"
  | "analysis_cancelled"
  | "capability_unavailable"
  | "analysis_failed";

const CANONICAL_ID_PATTERN = /^[a-z0-9][a-z0-9_.-]{0,79}$/;
const FORBIDDEN_ASCII_CONTROLS = /[\x00-\x08\x0B-\x1F\x7F]/;
const UNICODE_CF_CATEGORY = /\p{Cf}/u;

const VISION_ANALYSIS_ERROR_CODES = new Set<VisionAnalysisErrorCode>([
  "unavailable",
  "invalid_request",
  "analysis_not_found",
  "handoff_not_accepted",
  "transfer_mismatch",
  "analysis_expired",
  "analysis_cancelled",
  "capability_unavailable",
  "analysis_failed",
]);

export const CONFIDENCE_UNCERTAINTY_THRESHOLD_MILLI = 700;
export const MAX_OBSERVATIONS = 16;
export const MAX_TEXT_CODE_POINTS = 2000;

export interface VisionObservation {
  readonly kind: ObservationKind;
  readonly text: string;
  readonly confidenceMilli: number | null;
}

export interface VisionAnalysisState {
  readonly status: VisionAnalysisStatus;
  readonly capability: VisionCapability;
  readonly requestId: string | null;
  readonly analysisId: string | null;
  readonly transferId: string | null;
  readonly handoffId: string | null;
  readonly observations: readonly VisionObservation[];
  readonly errorCode: VisionAnalysisErrorCode | null;
  readonly cancelPending: boolean;
}

export type VisionAnalysisAction =
  | {
      type: "PREPARE_REQUESTED";
      requestId: string;
      capability: VisionCapability;
      transferId?: string;
      handoffId?: string;
    }
  | {
      type: "READY_RECEIVED";
      requestId: string;
      analysisId: string;
    }
  | {
      type: "IMAGE_ATTACHED";
      requestId: string;
      analysisId: string;
      transferId?: string;
    }
  | {
      type: "ANALYSIS_STARTED";
      requestId: string;
      analysisId: string;
    }
  | {
      type: "OBSERVATION_RECEIVED";
      requestId: string;
      analysisId: string;
      observations: readonly VisionObservation[];
    }
  | {
      type: "CANCEL_REQUESTED";
    }
  | {
      type: "CANCEL_CONFIRMED";
      requestId?: string;
      analysisId?: string;
    }
  | {
      type: "EXPIRED";
      requestId?: string;
      analysisId?: string;
    }
  | {
      type: "SAFE_ERROR";
      requestId?: string;
      analysisId?: string;
      errorCode?: VisionAnalysisErrorCode;
    }
  | {
      type: "RESET";
    };

export function isValidCanonicalId(id: unknown): id is string {
  return typeof id === "string" && CANONICAL_ID_PATTERN.test(id);
}

export function isValidObservationText(
  text: unknown,
  kind: ObservationKind = "text",
): text is string {
  if (typeof text !== "string") return false;
  if (FORBIDDEN_ASCII_CONTROLS.test(text)) return false;
  if (UNICODE_CF_CATEGORY.test(text)) return false;
  if (kind === "description" && text.trim().length === 0) return false;
  if (kind === "description" && /[\n\t]/.test(text)) return false;
  const codePointCount = [...text].length;
  return codePointCount >= 1 && codePointCount <= MAX_TEXT_CODE_POINTS;
}

export function isVisionAnalysisErrorCode(code: unknown): code is VisionAnalysisErrorCode {
  return typeof code === "string" && VISION_ANALYSIS_ERROR_CODES.has(code as VisionAnalysisErrorCode);
}

export function validateObservation(obs: unknown): VisionObservation | null {
  if (!obs || typeof obs !== "object" || Array.isArray(obs)) {
    return null;
  }
  const keys = Object.keys(obs);
  if (keys.length < 2 || keys.length > 3) {
    return null;
  }
  if (!keys.includes("kind") || !keys.includes("text")) {
    return null;
  }
  const obj = obs as Record<string, unknown>;

  if (obj.kind !== "text" && obj.kind !== "description") {
    return null;
  }

  if (!isValidObservationText(obj.text, obj.kind)) {
    return null;
  }

  let conf: number | null = null;
  if (obj.confidenceMilli !== null && obj.confidenceMilli !== undefined) {
    if (
      typeof obj.confidenceMilli !== "number" ||
      !Number.isInteger(obj.confidenceMilli) ||
      obj.confidenceMilli < 0 ||
      obj.confidenceMilli > 1000
    ) {
      return null;
    }
    conf = obj.confidenceMilli;
  }

  return Object.freeze({
    kind: obj.kind,
    text: obj.text,
    confidenceMilli: conf,
  });
}

export function validateObservations(
  observations: unknown,
): readonly VisionObservation[] | null {
  if (!Array.isArray(observations) || observations.length === 0 || observations.length > MAX_OBSERVATIONS) {
    return null;
  }
  const validated: VisionObservation[] = [];
  for (const item of observations) {
    const validItem = validateObservation(item);
    if (!validItem) {
      return null;
    }
    validated.push(validItem);
  }
  return Object.freeze(validated);
}

export function isVisionAnalysisPending(status: VisionAnalysisStatus): boolean {
  return status === "preparing" || status === "awaiting_image" || status === "analyzing";
}

export function isVisionAnalysisTerminal(status: VisionAnalysisStatus): boolean {
  return (
    status === "completed" ||
    status === "cancelled" ||
    status === "expired" ||
    status === "failed"
  );
}

export function createInitialVisionAnalysisState(): VisionAnalysisState {
  return Object.freeze({
    status: "idle",
    capability: "ocr",
    requestId: null,
    analysisId: null,
    transferId: null,
    handoffId: null,
    observations: Object.freeze([]),
    errorCode: null,
    cancelPending: false,
  });
}

export function reduceVisionAnalysis(
  state: VisionAnalysisState,
  action: VisionAnalysisAction,
): VisionAnalysisState {
  if (action.type === "RESET") {
    return createInitialVisionAnalysisState();
  }

  if (isVisionAnalysisTerminal(state.status)) {
    return state;
  }

  switch (action.type) {
    case "PREPARE_REQUESTED": {
      if (state.status !== "idle") {
        return state;
      }
      if (!isValidCanonicalId(action.requestId)) {
        return state;
      }
      if (action.transferId !== undefined && !isValidCanonicalId(action.transferId)) {
        return state;
      }
      if (action.handoffId !== undefined && !isValidCanonicalId(action.handoffId)) {
        return state;
      }
      if (action.capability !== "ocr" && action.capability !== "describe") {
        return state;
      }
      return Object.freeze({
        status: "preparing",
        capability: action.capability,
        requestId: action.requestId,
        analysisId: null,
        transferId: action.transferId ?? null,
        handoffId: action.handoffId ?? null,
        observations: Object.freeze([]),
        errorCode: null,
        cancelPending: false,
      });
    }

    case "READY_RECEIVED": {
      if (state.status !== "preparing") {
        return state;
      }
      if (action.requestId !== state.requestId || !isValidCanonicalId(action.analysisId)) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "awaiting_image",
        analysisId: action.analysisId,
      });
    }

    case "IMAGE_ATTACHED": {
      if (!isVisionAnalysisPending(state.status)) {
        return state;
      }
      if (action.requestId !== state.requestId) {
        return state;
      }
      if (state.analysisId && action.analysisId !== state.analysisId) {
        return state;
      }
      if (!isValidCanonicalId(action.analysisId)) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "analyzing",
        analysisId: action.analysisId,
        transferId: action.transferId ?? state.transferId,
      });
    }

    case "ANALYSIS_STARTED": {
      if (!isVisionAnalysisPending(state.status)) {
        return state;
      }
      if (action.requestId !== state.requestId || action.analysisId !== state.analysisId) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "analyzing",
      });
    }

    case "OBSERVATION_RECEIVED": {
      if (!isVisionAnalysisPending(state.status)) {
        return state;
      }
      if (action.requestId !== state.requestId || action.analysisId !== state.analysisId) {
        return state;
      }
      const validatedObs = validateObservations(action.observations);
      if (!validatedObs) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "completed",
        observations: validatedObs,
        cancelPending: false,
      });
    }

    case "CANCEL_REQUESTED": {
      if (!isVisionAnalysisPending(state.status) || state.cancelPending) {
        return state;
      }
      return Object.freeze({
        ...state,
        cancelPending: true,
      });
    }

    case "CANCEL_CONFIRMED": {
      if (!isVisionAnalysisPending(state.status) && !state.cancelPending) {
        return state;
      }
      if (action.requestId && action.requestId !== state.requestId) {
        return state;
      }
      if (action.analysisId && state.analysisId && action.analysisId !== state.analysisId) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "cancelled",
        cancelPending: false,
        observations: Object.freeze([]),
      });
    }

    case "EXPIRED": {
      if (!isVisionAnalysisPending(state.status)) {
        return state;
      }
      if (action.requestId && action.requestId !== state.requestId) {
        return state;
      }
      if (action.analysisId && state.analysisId && action.analysisId !== state.analysisId) {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "expired",
        cancelPending: false,
        observations: Object.freeze([]),
      });
    }

    case "SAFE_ERROR": {
      if (!isVisionAnalysisPending(state.status)) {
        return state;
      }
      if (action.requestId && action.requestId !== state.requestId) {
        return state;
      }
      if (action.analysisId && state.analysisId && action.analysisId !== state.analysisId) {
        return state;
      }
      const err = isVisionAnalysisErrorCode(action.errorCode)
        ? action.errorCode
        : "unavailable";
      return Object.freeze({
        ...state,
        status: "failed",
        errorCode: err,
        cancelPending: false,
        observations: Object.freeze([]),
      });
    }

    default:
      return state;
  }
}
