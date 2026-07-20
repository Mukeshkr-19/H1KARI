"use client";

import { useId, type Ref } from "react";
import {
  boundPreviewLabel,
  boundPreviewValue,
  mapPreviewErrorMessage,
  type PreviewEntry,
  type ProductivityPreviewErrorCode,
} from "@/utils/productivity/actionPreview";

export type {
  PreviewEntry,
  ProductivityPreviewErrorCode,
} from "@/utils/productivity/actionPreview";

export {
  PREVIEW_LABEL_MAX,
  PREVIEW_VALUE_MAX,
  GENERIC_PREVIEW_ERROR_MESSAGE,
  sanitizePreviewText,
  boundPreviewLabel,
  boundPreviewValue,
  mapPreviewErrorMessage,
} from "@/utils/productivity/actionPreview";

export type ProductivityActionPreviewModel = {
  proposalId: string;
  heading: string;
  actionLabel: string;
  riskLabel: string;
  targets: readonly PreviewEntry[];
  payload: readonly PreviewEntry[];
  expirationLabel?: string;
};

export type ProductivityActionPreviewProps = {
  proposal: ProductivityActionPreviewModel;
  pending: boolean;
  confirmDisabled?: boolean;
  cancelDisabled?: boolean;
  error?: ProductivityPreviewErrorCode;
  liveStatus?: string;
  onConfirm: () => void;
  onCancel: () => void;
  headingRef?: Ref<HTMLHeadingElement>;
};

function PreviewEntryList({
  title,
  entries,
  listId,
}: {
  title: string;
  entries: readonly PreviewEntry[];
  listId: string;
}) {
  return (
    <div className="mt-4">
      <h4 id={listId} className="text-sm font-semibold text-gray-200">
        {title}
      </h4>
      <dl className="mt-2 space-y-2" aria-labelledby={listId}>
        {entries.map((entry, index) => {
          const label = boundPreviewLabel(entry.label);
          const value = boundPreviewValue(entry.value);
          const shortened = Boolean(entry.truncated) || label.truncated || value.truncated;
          return (
            <div key={`${listId}-${index}`} className="rounded-lg bg-[#0f0f1a]/60 px-3 py-2">
              <dt className="text-xs font-medium uppercase tracking-wide text-gray-400">
                {label.text || "Field"}
              </dt>
              <dd className="mt-1 whitespace-pre-wrap break-words text-sm text-gray-100">
                {value.text}
              </dd>
              {shortened ? (
                <p className="mt-1 text-xs text-amber-200/90">Preview shortened</p>
              ) : null}
            </div>
          );
        })}
      </dl>
    </div>
  );
}

export function ProductivityActionPreview({
  proposal,
  pending,
  confirmDisabled = pending,
  cancelDisabled = pending,
  error,
  liveStatus,
  onConfirm,
  onCancel,
  headingRef,
}: ProductivityActionPreviewProps) {
  const instanceId = useId();
  const headingId = `${instanceId}-heading`;
  const destinationId = `${instanceId}-destination`;
  const payloadId = `${instanceId}-payload`;

  const heading = boundPreviewLabel(proposal.heading);
  const actionLabel = boundPreviewLabel(proposal.actionLabel);
  const riskLabel = boundPreviewLabel(proposal.riskLabel);
  const expiration = proposal.expirationLabel
    ? boundPreviewLabel(proposal.expirationLabel)
    : null;
  const errorMessage = error ? mapPreviewErrorMessage(error) : "";
  const statusText =
    liveStatus || (pending ? "Waiting for confirmation…" : "");

  return (
    <section
      className="rounded-xl border border-yellow-500/40 bg-yellow-950/20 p-4"
      aria-labelledby={headingId}
    >
      <h3
        id={headingId}
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold text-yellow-200"
      >
        {heading.text || "Review proposed action"}
      </h3>

      <p className="mt-2 text-sm text-gray-200">
        <span className="font-medium text-gray-100">{actionLabel.text || "Action"}</span>
        <span className="text-gray-500"> · </span>
        <span>{riskLabel.text || "Risk unknown"}</span>
      </p>

      <PreviewEntryList
        title="Destination"
        entries={proposal.targets}
        listId={destinationId}
      />

      <PreviewEntryList
        title="Payload preview"
        entries={proposal.payload}
        listId={payloadId}
      />

      {expiration ? (
        <p className="mt-3 text-sm text-gray-300">{expiration.text}</p>
      ) : null}

      {statusText ? (
        <p className="mt-3 text-sm text-amber-100" role="status" aria-live="polite">
          {statusText}
        </p>
      ) : null}

      {errorMessage ? (
        <p className="mt-3 text-sm text-red-200" role="alert">
          {errorMessage}
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={onConfirm}
          disabled={confirmDisabled}
          className="rounded-lg bg-green-600 px-4 py-2 font-semibold text-white hover:bg-green-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Confirm
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={cancelDisabled}
          className="rounded-lg border border-gray-600 px-4 py-2 font-semibold text-gray-100 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </section>
  );
}
