/**
 * Pure Phase 4 Camera Capture frontend primitives and state reducer.
 *
 * Implements bounded, explicit, cancellable camera stream lifecycle, deterministic track cleanup,
 * token-guarded async permission handling, safe error mapping, and zero external side effects.
 */

export type CameraCaptureStatus =
  | "idle"
  | "requesting"
  | "active"
  | "capturing"
  | "captured"
  | "stopping"
  | "stopped"
  | "failed";

export type CameraCaptureErrorCode =
  | "camera_unavailable"
  | "permission_denied"
  | "capture_failed"
  | "image_too_large"
  | "dimensions_exceeded";

const CAMERA_ERROR_CODES = new Set<CameraCaptureErrorCode>([
  "camera_unavailable",
  "permission_denied",
  "capture_failed",
  "image_too_large",
  "dimensions_exceeded",
]);

export const MAX_FRAME_BYTES = 1048576; // 1 MiB
export const MAX_FRAME_DIMENSION = 4096;

export interface CameraCaptureState {
  readonly status: CameraCaptureStatus;
  readonly token: number;
  readonly streamRef: MediaStream | null;
  readonly capturedFrame: Blob | null;
  readonly errorCode: CameraCaptureErrorCode | null;
}

export type CameraCaptureAction =
  | { type: "START_REQUESTED"; token: number }
  | { type: "PERMISSION_GRANTED"; token: number; stream: MediaStream }
  | { type: "PERMISSION_DENIED"; token: number }
  | { type: "CAMERA_UNAVAILABLE"; token: number }
  | { type: "CAPTURE_REQUESTED" }
  | { type: "FRAME_CAPTURED"; token: number; frame: Blob }
  | { type: "CAPTURE_FAILED"; token: number; errorCode: CameraCaptureErrorCode }
  | { type: "STOP_REQUESTED" }
  | { type: "STOP_CONFIRMED" }
  | { type: "CLEAR_FRAME" }
  | { type: "RESET" };

export function isCameraCaptureErrorCode(code: unknown): code is CameraCaptureErrorCode {
  return typeof code === "string" && CAMERA_ERROR_CODES.has(code as CameraCaptureErrorCode);
}

export function stopStreamTracks(stream: MediaStream | null): void {
  if (!stream) return;
  try {
    if (typeof stream.getTracks === "function") {
      const tracks = stream.getTracks();
      if (Array.isArray(tracks)) {
        for (const track of tracks) {
          if (track && typeof track.stop === "function") {
            track.stop();
          }
        }
      }
    }
  } catch {
    // Ignore track cleanup errors
  }
}

export function isCameraCapturePending(status: CameraCaptureStatus): boolean {
  return status === "requesting" || status === "capturing" || status === "stopping";
}

export function isCameraCaptureActive(status: CameraCaptureStatus): boolean {
  return status === "active" || status === "capturing";
}

export function createInitialCameraCaptureState(): CameraCaptureState {
  return Object.freeze({
    status: "idle",
    token: 0,
    streamRef: null,
    capturedFrame: null,
    errorCode: null,
  });
}

export function validateCapturedFrame(blob: unknown): Blob | null {
  if (!blob || typeof blob !== "object") return null;
  const b = blob as Blob;
  if (typeof b.size !== "number" || b.size <= 0 || b.size > MAX_FRAME_BYTES) {
    return null;
  }
  const type = (b.type || "").toLowerCase();
  if (type !== "image/jpeg" && type !== "image/png") {
    return null;
  }
  return b;
}

export function reduceCameraCapture(
  state: CameraCaptureState,
  action: CameraCaptureAction,
): CameraCaptureState {
  if (action.type === "RESET") {
    return Object.freeze({
      status: "idle",
      token: state.token + 1,
      streamRef: null,
      capturedFrame: null,
      errorCode: null,
    });
  }

  switch (action.type) {
    case "START_REQUESTED": {
      if (
        state.status === "requesting" ||
        state.status === "active" ||
        state.status === "capturing" ||
        state.status === "stopping"
      ) {
        return state;
      }
      return Object.freeze({
        status: "requesting",
        token: action.token,
        streamRef: null,
        capturedFrame: null,
        errorCode: null,
      });
    }

    case "PERMISSION_GRANTED": {
      if (action.token !== state.token || state.status !== "requesting") {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "active",
        streamRef: action.stream,
        errorCode: null,
      });
    }

    case "PERMISSION_DENIED": {
      if (action.token !== state.token || state.status !== "requesting") {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "failed",
        streamRef: null,
        errorCode: "permission_denied",
      });
    }

    case "CAMERA_UNAVAILABLE": {
      if (action.token !== state.token || state.status !== "requesting") {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "failed",
        streamRef: null,
        errorCode: "camera_unavailable",
      });
    }

    case "CAPTURE_REQUESTED": {
      if (state.status !== "active") {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "capturing",
      });
    }

    case "FRAME_CAPTURED": {
      if (action.token !== state.token || state.status !== "capturing") {
        return state;
      }
      const validBlob = validateCapturedFrame(action.frame);
      if (!validBlob) {
        return Object.freeze({
          ...state,
          status: "failed",
          streamRef: null,
          capturedFrame: null,
          errorCode: action.frame && action.frame.size > MAX_FRAME_BYTES ? "image_too_large" : "capture_failed",
        });
      }
      return Object.freeze({
        ...state,
        status: "captured",
        streamRef: null,
        capturedFrame: validBlob,
        errorCode: null,
      });
    }

    case "CAPTURE_FAILED": {
      if (action.token !== state.token) {
        return state;
      }
      const err = isCameraCaptureErrorCode(action.errorCode) ? action.errorCode : "capture_failed";
      return Object.freeze({
        ...state,
        status: "failed",
        streamRef: null,
        capturedFrame: null,
        errorCode: err,
      });
    }

    case "STOP_REQUESTED":
    case "STOP_CONFIRMED": {
      if (state.status === "stopped" || state.status === "idle") {
        return state;
      }
      return Object.freeze({
        ...state,
        status: "stopped",
        streamRef: null,
      });
    }

    case "CLEAR_FRAME": {
      return Object.freeze({
        ...state,
        capturedFrame: null,
      });
    }

    default:
      return state;
  }
}
