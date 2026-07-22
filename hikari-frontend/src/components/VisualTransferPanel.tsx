"use client";

/**
 * Pure Phase 4 Visual Transfer Panel component.
 *
 * Implements native file input (image/png, image/jpeg, <= 1 MiB), explicit transfer
 * trigger, privacy-preserving live status regions (no filename mirroring), safe local
 * validation alerts, and zero automatic upload/preview side effects.
 */

import { useCallback } from "react";
import {
  isVisualTransferPending,
  isVisualTransferTerminal,
  type VisualTransferState,
} from "../utils/phase4/visualTransfer";

export interface VisualTransferPanelProps {
  readonly state: VisualTransferState;
  readonly onSelectFile?: (file: File) => void;
  readonly onBeginTransfer?: (file: Blob) => void;
  readonly onCancel?: () => void;
  readonly headingRef?: React.RefObject<HTMLHeadingElement | null>;
}

function formatStatusText(state: VisualTransferState): string {
  switch (state.status) {
    case "idle":
      return "Visual transfer idle. Select an image file.";
    case "selected":
      return `Image file selected. Size: ${state.fileSize ?? 0} bytes. Format: ${
        state.fileType ?? "image"
      }.`;
    case "beginning":
      return "Beginning visual transfer...";
    case "ready":
      return "Visual transfer ready.";
    case "transferring":
      return "Transferring visual payload...";
    case "validating":
      return "Validating visual payload...";
    case "completed":
      return "Visual transfer completed successfully.";
    case "cancelling":
      return "Cancelling visual transfer...";
    case "cancelled":
      return "Visual transfer cancelled.";
    case "failed":
      return "Visual transfer failed.";
    default:
      return "Visual transfer status updated.";
  }
}

export function VisualTransferPanel({
  state,
  onSelectFile,
  onBeginTransfer,
  onCancel,
  headingRef,
}: VisualTransferPanelProps) {
  const isPending = isVisualTransferPending(state.status);
  const isTerminal = isVisualTransferTerminal(state.status);
  const isSelected = state.status === "selected" && Boolean(state.fileRef);

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (files && files.length === 1 && onSelectFile) {
        onSelectFile(files[0]);
      }
    },
    [onSelectFile]
  );

  const handleBegin = useCallback(() => {
    if (state.fileRef && onBeginTransfer && isSelected) {
      onBeginTransfer(state.fileRef);
    }
  }, [state.fileRef, onBeginTransfer, isSelected]);

  const handleCancel = useCallback(() => {
    if (onCancel) {
      onCancel();
    }
  }, [onCancel]);

  return (
    <section
      aria-labelledby="phase4-visual-transfer-heading"
      className="p-4 rounded-lg border border-gray-700 bg-gray-900 text-gray-100 max-w-md"
    >
      <h2
        id="phase4-visual-transfer-heading"
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        Visual Transfer
      </h2>

      <div className="mb-4">
        <label
          htmlFor="visual-transfer-file-input"
          className="block text-sm font-medium text-gray-200 mb-1"
        >
          Select Image File (PNG or JPEG, Max 1 MiB)
        </label>
        <input
          id="visual-transfer-file-input"
          type="file"
          accept="image/png,image/jpeg"
          onChange={handleFileChange}
          disabled={isPending || isTerminal}
          aria-describedby="visual-transfer-file-hint"
          className="w-full text-sm text-gray-300 file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:text-sm file:font-semibold file:bg-blue-600 file:text-white hover:file:bg-blue-500 focus:outline-none disabled:opacity-50"
        />
        <p id="visual-transfer-file-hint" className="text-xs text-gray-400 mt-1">
          Accepted formats: PNG (.png) or JPEG (.jpg, .jpeg), maximum 1,048,576 bytes.
        </p>
      </div>

      {state.fileSize !== null && (
        <div className="mb-4 p-2.5 bg-gray-800 rounded border border-gray-700 text-xs text-gray-300 space-y-1">
          <div>File Size: <span className="text-white font-mono">{state.fileSize} bytes</span></div>
          <div>MIME Type: <span className="text-white font-mono">{state.fileType}</span></div>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex space-x-3 mb-3">
        <button
          type="button"
          onClick={handleBegin}
          disabled={!isSelected || isPending || !state.fileRef}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-blue-400 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Begin Transfer
        </button>
        <button
          type="button"
          onClick={handleCancel}
          disabled={
            isTerminal ||
            state.status === "cancelling" ||
            state.status === "idle" ||
            (isPending && !state.transferId)
          }
          className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white font-medium rounded focus:outline-none focus:ring-2 focus:ring-gray-400 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Cancel
        </button>
      </div>

      {/* Polite status region - Contains byte counts and state ONLY, never filename */}
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
          {state.errorCode === "mime_unsupported" || state.errorCode === "mime_mismatch"
            ? "Validation error: Only PNG and JPEG images are allowed."
            : state.errorCode === "size_exceeded"
            ? "Validation error: Image file must be non-empty and 1 MiB or less."
            : "Visual transfer failed. Please choose another image file."}
        </div>
      )}
    </section>
  );
}
