"use client";

/**
 * Pure Phase 4 Vision Analysis Panel component.
 *
 * Implements accessible capability selection, explicit action triggers, polite live status updates,
 * safe error alerts, and text-only observation rendering. Contains zero network, transport,
 * storage, camera, binary reading, or logging side effects.
 */

import { useState, useCallback } from "react";
import {
  CONFIDENCE_UNCERTAINTY_THRESHOLD_MILLI,
  isVisionAnalysisPending,
  type VisionAnalysisErrorCode,
  type VisionAnalysisState,
  type VisionCapability,
} from "../utils/phase4/visionAnalysis";

export interface VisionAnalysisPanelProps {
  readonly state: VisionAnalysisState;
  readonly onCapabilityChange?: (capability: VisionCapability) => void;
  readonly onStartAnalysis?: (capability: VisionCapability) => void;
  readonly onCancelAnalysis?: () => void;
  readonly headingRef?: React.RefObject<HTMLHeadingElement | null>;
}

function formatStatusText(state: VisionAnalysisState): string {
  if (state.cancelPending) {
    return "Cancellation requested...";
  }
  switch (state.status) {
    case "idle":
      return "Vision analysis idle.";
    case "preparing":
      return "Preparing vision analysis...";
    case "awaiting_image":
      return "Awaiting image frame...";
    case "analyzing":
      return "Analyzing image...";
    case "completed":
      return "Vision analysis complete.";
    case "cancelled":
      return "Vision analysis cancelled.";
    case "expired":
      return "Vision analysis request expired.";
    case "failed":
      return "Vision analysis failed.";
    default:
      return "Vision analysis status updated.";
  }
}

function formatErrorMessage(code: VisionAnalysisErrorCode | null): string {
  switch (code) {
    case "unavailable":
      return "Vision analysis service unavailable. Please try again later.";
    case "invalid_request":
      return "Invalid vision analysis request parameters.";
    case "analysis_not_found":
      return "Vision analysis task not found.";
    case "handoff_not_accepted":
      return "The associated handoff has not been accepted.";
    case "transfer_mismatch":
      return "The selected image does not match this analysis request.";
    case "analysis_expired":
      return "Vision analysis session expired.";
    case "analysis_cancelled":
      return "Vision analysis was cancelled.";
    case "capability_unavailable":
      return "Requested vision capability is unsupported.";
    case "analysis_failed":
      return "Vision analysis processing failed.";
    default:
      return "Vision analysis failed. Please try again.";
  }
}

export function VisionAnalysisPanel({
  state,
  onCapabilityChange,
  onStartAnalysis,
  onCancelAnalysis,
  headingRef,
}: VisionAnalysisPanelProps) {
  const [selectedCapability, setSelectedCapability] = useState<VisionCapability>(
    state.capability || "ocr",
  );
  const isPending = isVisionAnalysisPending(state.status);

  const handleCapabilityChange = useCallback(
    (cap: VisionCapability) => {
      setSelectedCapability(cap);
      if (onCapabilityChange) {
        onCapabilityChange(cap);
      }
    },
    [onCapabilityChange],
  );

  const handleStart = useCallback(() => {
    if (onStartAnalysis && !isPending) {
      onStartAnalysis(selectedCapability);
    }
  }, [onStartAnalysis, isPending, selectedCapability]);

  const handleCancel = useCallback(() => {
    if (onCancelAnalysis && isPending && !state.cancelPending) {
      onCancelAnalysis();
    }
  }, [onCancelAnalysis, isPending, state.cancelPending]);

  return (
    <section
      aria-labelledby="vision-analysis-heading"
      className="p-4 rounded-lg border border-gray-700 bg-gray-900 text-gray-100 max-w-lg"
    >
      <h2
        id="vision-analysis-heading"
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        Vision Analysis
      </h2>

      <fieldset className="mb-4 border-none p-0 m-0">
        <legend className="text-sm font-medium text-gray-200 mb-2">Analysis Mode</legend>
        <div className="flex space-x-4">
          <label className="flex items-center space-x-2 text-sm text-gray-300 cursor-pointer">
            <input
              type="radio"
              name="vision-capability"
              value="ocr"
              checked={selectedCapability === "ocr"}
              onChange={() => handleCapabilityChange("ocr")}
              disabled={isPending}
              className="text-blue-600 focus:ring-blue-500 bg-gray-800 border-gray-600"
            />
            <span>OCR (Text Extraction)</span>
          </label>
          <label className="flex items-center space-x-2 text-sm text-gray-500 cursor-not-allowed">
            <input
              type="radio"
              name="vision-capability"
              value="describe"
              checked={selectedCapability === "describe"}
              onChange={() => handleCapabilityChange("describe")}
              disabled
              aria-describedby="vision-description-unavailable"
              className="text-blue-600 focus:ring-blue-500 bg-gray-800 border-gray-600"
            />
            <span>Describe Image</span>
          </label>
        </div>
        <p id="vision-description-unavailable" className="mt-2 text-xs text-gray-400">
          Image description is unavailable until a reviewed local engine is configured.
        </p>
      </fieldset>

      <div className="flex space-x-3 mb-3">
        <button
          type="button"
          onClick={handleStart}
          disabled={isPending || state.status === "completed"}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Start Analysis
        </button>

        {isPending && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={state.cancelPending}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-gray-400 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {state.cancelPending ? "Cancelling..." : "Cancel Analysis"}
          </button>
        )}
      </div>

      {/* Render Bounded Observations */}
      {state.status === "completed" && state.observations.length > 0 && (
        <div className="mt-4 space-y-3">
          <h3 className="text-sm font-semibold text-gray-200">
            Observations ({state.observations.length})
          </h3>
          {state.observations.map((obs, idx) => {
            const hasConfidence =
              obs.confidenceMilli !== null && obs.confidenceMilli !== undefined;
            const confPercent = hasConfidence
              ? Math.round(obs.confidenceMilli! / 10)
              : null;
            const isUncertain =
              !hasConfidence ||
              obs.confidenceMilli! < CONFIDENCE_UNCERTAINTY_THRESHOLD_MILLI;

            return (
              <div
                key={idx}
                className="p-3 bg-gray-800 border border-gray-700 rounded text-sm text-gray-100"
              >
                <div className="flex justify-between items-center mb-1 text-xs text-gray-400">
                  <span className="uppercase font-medium">{obs.kind}</span>
                  <span>
                    {hasConfidence
                      ? `Confidence: ${confPercent}%`
                      : "Confidence unavailable"}
                    {isUncertain && (
                      <span className="ml-2 text-yellow-400 font-semibold">(Uncertain)</span>
                    )}
                  </span>
                </div>
                {/* Bounded text output as plain text only (never HTML) */}
                <p className="whitespace-pre-wrap break-words text-gray-100">{obs.text}</p>
              </div>
            );
          })}
        </div>
      )}

      {/* Live status region for polite status updates only (observation text is NEVER in status) */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="text-sm text-gray-300 mt-2"
      >
        {formatStatusText(state)}
      </div>

      {/* Safe local error summary */}
      {state.status === "failed" && (
        <div
          role="alert"
          className="mt-3 p-3 bg-red-900/50 border border-red-500 rounded text-sm text-red-200"
        >
          {formatErrorMessage(state.errorCode)}
        </div>
      )}
    </section>
  );
}
