"use client";

/**
 * Pure Phase 4 Device Pairing Panel component.
 *
 * Implements accessible challenge-code input, focusable heading, polite live status
 * region, safe error alerts, and pending locks. Contains zero network, transport,
 * storage, logging, or secret-mirroring side effects.
 */

import { useState, useCallback } from "react";
import {
  isPairingPending,
  isPairingTerminal,
  type PairingState,
} from "../utils/phase4/pairing";

const PAIRING_CODE_PATTERN = /^[0-9A-F]{6,10}$/;

export interface Phase4PairingPanelProps {
  readonly state: PairingState;
  readonly onStartPairing?: (deviceLabel?: string) => void;
  readonly onConfirm?: (code: string) => void;
  readonly onCancel?: () => void;
  readonly headingRef?: React.RefObject<HTMLHeadingElement | null>;
}

function formatStatusText(state: PairingState): string {
  switch (state.status) {
    case "idle":
      return "Pairing idle. Ready to start.";
    case "preparing":
      return "Preparing pairing request...";
    case "challenge":
      return "Challenge received. Enter challenge code to confirm.";
    case "confirming":
      return "Confirming pairing request...";
    case "paired":
      return "Device paired successfully.";
    case "cancelling":
      return "Cancelling pairing...";
    case "cancelled":
      return "Pairing cancelled.";
    case "revoked":
      return "Pairing revoked.";
    case "expired":
      return "Pairing request expired.";
    case "failed":
      return "Pairing failed.";
    default:
      return "Pairing status updated.";
  }
}

export function Phase4PairingPanel({
  state,
  onStartPairing,
  onConfirm,
  onCancel,
  headingRef,
}: Phase4PairingPanelProps) {
  const [code, setCode] = useState("");
  const isPending = isPairingPending(state.status);
  const isTerminal = isPairingTerminal(state.status);

  const handleStart = useCallback(() => {
    if (onStartPairing && state.status === "idle") {
      onStartPairing();
    }
  }, [onStartPairing, state.status]);

  const handleConfirm = useCallback(() => {
    const uppercaseCode = code.toUpperCase();
    if (PAIRING_CODE_PATTERN.test(uppercaseCode) && onConfirm && state.status === "challenge") {
      onConfirm(uppercaseCode);
    }
  }, [code, onConfirm, state.status]);

  const handleCancel = useCallback(() => {
    if (onCancel) {
      onCancel();
    }
  }, [onCancel]);

  return (
    <section
      aria-labelledby="phase4-pairing-heading"
      className="p-4 rounded-lg border border-gray-700 bg-gray-900 text-gray-100 max-w-md"
    >
      <h2
        id="phase4-pairing-heading"
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        Device Pairing
      </h2>

      {state.deviceLabel && (
        <p className="text-sm text-gray-300 mb-3" id="pairing-device-label-desc">
          Device: <span className="font-medium text-white">{state.deviceLabel}</span>
        </p>
      )}

      {state.status === "idle" && (
        <button
          type="button"
          onClick={handleStart}
          className="mb-4 px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-purple-400"
        >
          Start Device Pairing
        </button>
      )}

      <div className="mb-4">
        <label
          htmlFor="pairing-challenge-code-input"
          className="block text-sm font-medium text-gray-200 mb-1"
        >
          Challenge Code
        </label>
        <input
          id="pairing-challenge-code-input"
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          disabled={isPending || isTerminal || state.status !== "challenge"}
          maxLength={10}
          pattern="[0-9A-F]{6,10}"
          autoComplete="one-time-code"
          aria-describedby={state.deviceLabel ? "pairing-code-hint pairing-device-label-desc" : "pairing-code-hint"}
          className="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 font-mono tracking-wider uppercase"
          placeholder="ENTER CODE"
        />
        <p id="pairing-code-hint" className="text-xs text-gray-400 mt-1">
          Enter the 6–10 character uppercase code shown by HIKARI.
        </p>
      </div>

      <div className="flex space-x-3 mb-3">
        <button
          type="button"
          onClick={handleConfirm}
          disabled={isPending || isTerminal || state.status !== "challenge" || !PAIRING_CODE_PATTERN.test(code.toUpperCase())}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Confirm
        </button>
        <button
          type="button"
          onClick={handleCancel}
          disabled={isTerminal || state.status === "cancelling" || !state.challengeId}
          className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-gray-400 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Cancel
        </button>
      </div>

      {/* Live region for polite status updates without code mirroring */}
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
          Pairing failed. Please verify the code and try again.
        </div>
      )}
    </section>
  );
}
