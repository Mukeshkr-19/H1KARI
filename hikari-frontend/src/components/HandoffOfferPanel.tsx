"use client";

/**
 * Pure Phase 4 Handoff Offer Panel component.
 *
 * Displays task handoff offers with frozen preview content, explicit user acknowledgment,
 * exact accessible descriptions, polite status regions, and no authority language.
 */

import { useCallback } from "react";
import {
  isHandoffPending,
  isHandoffTerminal,
  type HandoffState,
} from "../utils/phase4/handoff";

export interface HandoffOfferPanelProps {
  readonly state: HandoffState;
  readonly onAccept?: () => void;
  readonly onReject?: () => void;
  readonly onCancel?: () => void;
  readonly onToggleAcknowledge?: (checked: boolean) => void;
}

function formatStatusText(state: HandoffState): string {
  switch (state.status) {
    case "idle":
      return "Handoff idle.";
    case "offered":
      return "Task handoff offer received. Review details and acknowledge to accept.";
    case "accepting":
      return "Accepting task handoff...";
    case "accepted":
      return "Task handoff accepted successfully.";
    case "rejecting":
      return "Rejecting task handoff...";
    case "rejected":
      return "Task handoff rejected.";
    case "cancelling":
      return "Cancelling handoff request...";
    case "cancelled":
      return "Task handoff cancelled.";
    case "expired":
      return "Task handoff offer expired.";
    case "failed":
      return "Task handoff failed.";
    default:
      return "Handoff status updated.";
  }
}

export function HandoffOfferPanel({
  state,
  onAccept,
  onReject,
  onCancel,
  onToggleAcknowledge,
}: HandoffOfferPanelProps) {
  const isPending = isHandoffPending(state.status);
  const isTerminal = isHandoffTerminal(state.status);
  const isOffered = state.status === "offered";

  const handleAccept = useCallback(() => {
    if (isOffered && state.acknowledged && onAccept) {
      onAccept();
    }
  }, [isOffered, state.acknowledged, onAccept]);

  const handleReject = useCallback(() => {
    if (isOffered && onReject) {
      onReject();
    }
  }, [isOffered, onReject]);

  const handleCancel = useCallback(() => {
    if (onCancel) {
      onCancel();
    }
  }, [onCancel]);

  const handleAckChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (onToggleAcknowledge && isOffered && !isPending) {
        onToggleAcknowledge(e.target.checked);
      }
    },
    [onToggleAcknowledge, isOffered, isPending]
  );

  return (
    <section
      aria-labelledby="phase4-handoff-heading"
      className="p-4 rounded-lg border border-gray-700 bg-gray-900 text-gray-100 max-w-lg"
    >
      <h2
        id="phase4-handoff-heading"
        tabIndex={-1}
        className="text-lg font-semibold mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        Task Handoff Offer
      </h2>

      {state.preview ? (
        <div
          id="handoff-preview-details"
          className="mb-4 p-3 bg-gray-800 rounded border border-gray-700 text-sm space-y-2"
        >
          <div>
            <span className="text-gray-400 font-medium">Task Summary:</span>
            <p className="text-white font-medium mt-0.5">{state.preview.summary}</p>
          </div>
          <div className="text-xs text-gray-400">Task ID: {state.preview.taskId}</div>
        </div>
      ) : (
        <p className="text-sm text-gray-400 mb-4">No active task offer available.</p>
      )}

      {/* Explicit acknowledgment checkbox */}
      <div className="flex items-start space-x-2 mb-4">
        <input
          type="checkbox"
          id="handoff-acknowledge-checkbox"
          checked={state.acknowledged}
          onChange={handleAckChange}
          disabled={!isOffered || isPending}
          aria-describedby="handoff-ack-desc"
          className="mt-1 h-4 w-4 rounded bg-gray-800 border-gray-600 text-blue-600 focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
        />
        <label
          htmlFor="handoff-acknowledge-checkbox"
          id="handoff-ack-desc"
          className="text-sm text-gray-200 cursor-pointer select-none"
        >
          I acknowledge and confirm taking over this active task on this device.
        </label>
      </div>

      {/* Action buttons */}
      <div className="flex space-x-3 mb-3">
        <button
          type="button"
          onClick={handleAccept}
          disabled={!isOffered || isPending || !state.acknowledged}
          aria-describedby={state.preview ? "handoff-preview-details" : undefined}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Accept Handoff
        </button>
        <button
          type="button"
          onClick={handleReject}
          disabled={!isOffered || isPending}
          className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-gray-400 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Reject Handoff
        </button>
        <button
          type="button"
          onClick={handleCancel}
          disabled={isTerminal || state.status === "cancelling" || state.status === "idle"}
          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 font-medium rounded border border-gray-600 focus:outline-none focus:ring-2 focus:ring-gray-400 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Cancel
        </button>
      </div>

      {/* Polite status region */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="text-sm text-gray-300 mt-2"
      >
        {formatStatusText(state)}
      </div>

      {/* Safe local error alert */}
      {state.status === "failed" && (
        <div
          role="alert"
          className="mt-3 p-2 bg-red-900/50 border border-red-500 rounded text-sm text-red-200"
        >
          Task handoff failed. Please request a new offer from the primary device.
        </div>
      )}
    </section>
  );
}
