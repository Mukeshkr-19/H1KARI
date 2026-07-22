"use client";

/**
 * Pure Phase 4 Camera Capture Panel component.
 *
 * Implements accessible, explicit, user-activated camera capture controls, visible activity indicators,
 * polite status updates, safe error alerts, and direct canvas frame encoding. Contains zero network,
 * storage, telemetry, file fallback, or focus-stealing side effects.
 */

import { useCallback, useReducer, useEffect, useRef } from "react";
import {
  createInitialCameraCaptureState,
  isCameraCaptureActive,
  isCameraCapturePending,
  MAX_FRAME_BYTES,
  MAX_FRAME_DIMENSION,
  reduceCameraCapture,
  stopStreamTracks,
  type CameraCaptureErrorCode,
  type CameraCaptureStatus,
} from "../utils/phase4/cameraCapture";

export interface CameraCapturePanelProps {
  readonly onFrameCaptured?: (frame: Blob) => void;
  readonly headingRef?: React.RefObject<HTMLHeadingElement | null>;
  readonly errorRef?: React.RefObject<HTMLDivElement | null>;
  readonly disabled?: boolean;
}

function formatStatusText(status: CameraCaptureStatus): string {
  switch (status) {
    case "idle":
      return "Camera inactive.";
    case "requesting":
      return "Requesting camera permission...";
    case "active":
      return "Camera active.";
    case "capturing":
      return "Capturing image frame...";
    case "captured":
      return "Image frame captured.";
    case "stopping":
      return "Stopping camera...";
    case "stopped":
      return "Camera stopped.";
    case "failed":
      return "Camera capture error.";
    default:
      return "Camera status updated.";
  }
}

function formatErrorMessage(code: CameraCaptureErrorCode | null): string {
  switch (code) {
    case "permission_denied":
      return "Camera permission was denied. Please check site settings.";
    case "camera_unavailable":
      return "Camera device is unavailable.";
    case "capture_failed":
      return "Failed to capture frame from camera.";
    case "image_too_large":
      return "Captured frame exceeds size limit (1 MiB).";
    case "dimensions_exceeded":
      return "Captured frame exceeds dimension limit (4096x4096).";
    default:
      return "Camera error occurred.";
  }
}

export function CameraCapturePanel({
  onFrameCaptured,
  headingRef,
  errorRef,
  disabled = false,
}: CameraCapturePanelProps) {
  const [state, dispatch] = useReducer(reduceCameraCapture, undefined, createInitialCameraCaptureState);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const tokenRef = useRef<number>(0);
  const mountedRef = useRef(true);
  const currentStreamRef = useRef<MediaStream | null>(null);

  currentStreamRef.current = state.streamRef;

  const handleStartCamera = useCallback(() => {
    if (disabled || isCameraCapturePending(state.status) || state.status === "active") {
      return;
    }
    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices ||
      typeof navigator.mediaDevices.getUserMedia !== "function"
    ) {
      const newToken = tokenRef.current + 1;
      tokenRef.current = newToken;
      dispatch({ type: "START_REQUESTED", token: newToken });
      dispatch({ type: "CAMERA_UNAVAILABLE", token: newToken });
      return;
    }
    const newToken = tokenRef.current + 1;
    tokenRef.current = newToken;
    stopStreamTracks(currentStreamRef.current);
    dispatch({ type: "START_REQUESTED", token: newToken });

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then((stream) => {
        if (!mountedRef.current || tokenRef.current !== newToken) {
          stopStreamTracks(stream);
          return;
        }
        dispatch({ type: "PERMISSION_GRANTED", token: newToken, stream });
      })
      .catch((err) => {
        if (!mountedRef.current || tokenRef.current !== newToken) {
          return;
        }
        const isPermission =
          err && (err.name === "NotAllowedError" || err.name === "PermissionDeniedError");
        if (isPermission) {
          dispatch({ type: "PERMISSION_DENIED", token: newToken });
        } else {
          dispatch({ type: "CAMERA_UNAVAILABLE", token: newToken });
        }
      });
  }, [disabled, state.status]);

  const handleCaptureImage = useCallback(() => {
    if (state.status !== "active" || !state.streamRef || !videoRef.current) {
      return;
    }
    const video = videoRef.current;
    const curToken = state.token;
    const width = video.videoWidth;
    const height = video.videoHeight;

    if (!width || !height) {
      dispatch({ type: "CAPTURE_FAILED", token: curToken, errorCode: "capture_failed" });
      return;
    }

    if (width > MAX_FRAME_DIMENSION || height > MAX_FRAME_DIMENSION) {
      dispatch({ type: "CAPTURE_FAILED", token: curToken, errorCode: "dimensions_exceeded" });
      return;
    }

    dispatch({ type: "CAPTURE_REQUESTED" });

    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      dispatch({ type: "CAPTURE_FAILED", token: curToken, errorCode: "capture_failed" });
      return;
    }

    ctx.drawImage(video, 0, 0, width, height);

    canvas.toBlob(
      (blob) => {
        if (!mountedRef.current || tokenRef.current !== curToken) {
          return;
        }
        if (!blob || blob.size === 0) {
          dispatch({ type: "CAPTURE_FAILED", token: curToken, errorCode: "capture_failed" });
          return;
        }
        if (blob.size > MAX_FRAME_BYTES) {
          dispatch({ type: "CAPTURE_FAILED", token: curToken, errorCode: "image_too_large" });
          return;
        }
        dispatch({ type: "FRAME_CAPTURED", token: curToken, frame: blob });
        if (onFrameCaptured) {
          onFrameCaptured(blob);
        }
        dispatch({ type: "CLEAR_FRAME" });
      },
      "image/jpeg",
      0.85,
    );
  }, [state.status, state.streamRef, state.token, onFrameCaptured]);

  const handleStopCamera = useCallback(() => {
    tokenRef.current += 1;
    dispatch({ type: "STOP_REQUESTED" });
  }, []);

  useEffect(() => {
    if (state.status === "active" && state.streamRef && videoRef.current) {
      videoRef.current.srcObject = state.streamRef;
    }
  }, [state.status, state.streamRef]);

  useEffect(() => () => {
    mountedRef.current = false;
    tokenRef.current += 1;
    stopStreamTracks(currentStreamRef.current);
  }, []);

  const canStart =
    state.status === "idle" ||
    state.status === "stopped" ||
    state.status === "failed" ||
    state.status === "captured";

  const isActive = isCameraCaptureActive(state.status);
  const canCapture = state.status === "active";

  return (
    <section
      aria-labelledby="camera-capture-heading"
      className="p-4 rounded-lg border border-gray-700 bg-gray-900 text-gray-100 max-w-lg"
    >
      <h2
        id="camera-capture-heading"
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        Camera Capture
      </h2>

      {/* Obvious Camera Active indicator (text + badge, not color alone) */}
      {isActive && (
        <div className="flex items-center space-x-2 text-sm text-green-400 font-semibold mb-3">
          <span className="inline-block w-2.5 h-2.5 bg-green-500 rounded-full animate-pulse" aria-hidden="true" />
          <span>Camera active</span>
        </div>
      )}

      {/* Live Video Preview element */}
      {isActive && (
        <div className="mb-4 rounded-md overflow-hidden border border-gray-700 bg-black aspect-video flex items-center justify-center">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            aria-label="Live camera preview"
            className="w-full h-full object-cover"
          />
        </div>
      )}

      {/* Control Buttons */}
      <div className="flex space-x-3 mb-3">
        {canStart && (
          <button
            type="button"
            onClick={handleStartCamera}
            disabled={disabled}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-blue-400"
          >
            Start camera
          </button>
        )}

        {(isActive || state.status === "requesting") && (
          <>
            {isActive && (
              <button
                type="button"
                onClick={handleCaptureImage}
                disabled={!canCapture}
                className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-purple-400 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Capture image
              </button>
            )}
            <button
              type="button"
              onClick={handleStopCamera}
              className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-red-400"
            >
              {state.status === "requesting" ? "Cancel camera request" : "Stop camera"}
            </button>
          </>
        )}
      </div>

      {/* Live Region for polite status updates only (no data URLs, filenames, or exception text) */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="text-sm text-gray-300 mt-2"
      >
        {formatStatusText(state.status)}
      </div>

      {/* Safe local error alert summary */}
      {state.status === "failed" && (
        <div
          ref={errorRef}
          role="alert"
          tabIndex={-1}
          className="mt-3 p-3 bg-red-900/50 border border-red-500 rounded text-sm text-red-200 focus:outline-none focus:ring-2 focus:ring-red-400"
        >
          {formatErrorMessage(state.errorCode)}
        </div>
      )}
    </section>
  );
}
