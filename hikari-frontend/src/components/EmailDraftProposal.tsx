"use client";

import { useEffect, useId, useRef, type Ref } from "react";
import {
  EMAIL_DRAFT_BODY_MAX,
  EMAIL_DRAFT_RECIPIENT_MAX,
  EMAIL_DRAFT_SUBJECT_MAX,
  mapEmailDraftValidationMessage,
  type EmailDraftFieldName,
  type EmailDraftFields,
  type EmailDraftValidationCode,
} from "@/utils/productivity/emailDraftProposal";
import {
  mapPreviewErrorMessage,
  type ProductivityPreviewErrorCode,
} from "@/utils/productivity/actionPreview";

export type EmailDraftProposalProps = {
  fields: EmailDraftFields;
  pending: boolean;
  disabled?: boolean;
  validationCode?: EmailDraftValidationCode;
  validationField?: EmailDraftFieldName;
  prepareError?: ProductivityPreviewErrorCode;
  onChange: (fields: EmailDraftFields) => void;
  onSubmit: () => void;
  onReset: () => void;
  headingRef?: Ref<HTMLHeadingElement>;
};

export function EmailDraftProposal({
  fields,
  pending,
  disabled = false,
  validationCode,
  validationField,
  prepareError,
  onChange,
  onSubmit,
  onReset,
  headingRef,
}: EmailDraftProposalProps) {
  const instanceId = useId();
  const headingId = `${instanceId}-heading`;
  const recipientId = `${instanceId}-recipient`;
  const subjectId = `${instanceId}-subject`;
  const bodyId = `${instanceId}-body`;
  const validationMessageId = `${instanceId}-validation-message`;
  const prepareErrorId = `${instanceId}-prepare-error`;
  const prepareErrorMessageId = `${instanceId}-prepare-error-message`;
  const recipientRef = useRef<HTMLInputElement>(null);
  const subjectRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const prepareErrorHeadingRef = useRef<HTMLHeadingElement>(null);

  const locked = pending || disabled;
  const validationMessage = validationCode
    ? mapEmailDraftValidationMessage(validationCode)
    : "";
  const prepareErrorMessage = prepareError
    ? mapPreviewErrorMessage(prepareError)
    : "";

  useEffect(() => {
    if (!validationCode || !validationField) {
      return;
    }
    if (validationField === "recipient") {
      recipientRef.current?.focus();
      return;
    }
    if (validationField === "subject") {
      subjectRef.current?.focus();
      return;
    }
    bodyRef.current?.focus();
  }, [validationCode, validationField]);

  useEffect(() => {
    if (prepareError) {
      prepareErrorHeadingRef.current?.focus();
    }
  }, [prepareError]);

  return (
    <section
      className="rounded-xl border border-gray-800 bg-[#1a1a2e] p-4"
      aria-labelledby={headingId}
    >
      <h2
        id={headingId}
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold text-gray-100"
      >
        Draft an email
      </h2>
      <p className="mt-1 text-sm text-gray-400">
        Review a proposal before any email is sent. Nothing is sent from this form.
      </p>

      <div className="mt-4 space-y-4">
        <div>
          <label
            htmlFor={recipientId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Recipient
          </label>
          <input
            id={recipientId}
            ref={recipientRef}
            type="email"
            autoComplete="off"
            spellCheck={false}
            value={fields.recipient}
            disabled={locked}
            maxLength={EMAIL_DRAFT_RECIPIENT_MAX + 1}
            aria-invalid={validationField === "recipient"}
            aria-describedby={
              validationField === "recipient" ? validationMessageId : undefined
            }
            onChange={(event) =>
              onChange(
                Object.freeze({
                  ...fields,
                  recipient: event.target.value,
                }),
              )
            }
            className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <div>
          <label
            htmlFor={subjectId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Subject
          </label>
          <input
            id={subjectId}
            ref={subjectRef}
            type="text"
            autoComplete="off"
            value={fields.subject}
            disabled={locked}
            maxLength={EMAIL_DRAFT_SUBJECT_MAX + 1}
            aria-invalid={validationField === "subject"}
            aria-describedby={
              validationField === "subject" ? validationMessageId : undefined
            }
            onChange={(event) =>
              onChange(
                Object.freeze({
                  ...fields,
                  subject: event.target.value,
                }),
              )
            }
            className="w-full rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <div>
          <label
            htmlFor={bodyId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Body
          </label>
          <textarea
            id={bodyId}
            ref={bodyRef}
            rows={8}
            autoComplete="off"
            value={fields.body}
            disabled={locked}
            maxLength={EMAIL_DRAFT_BODY_MAX + 1}
            aria-invalid={validationField === "body"}
            aria-describedby={
              validationField === "body" ? validationMessageId : undefined
            }
            onChange={(event) =>
              onChange(
                Object.freeze({
                  ...fields,
                  body: event.target.value,
                }),
              )
            }
            className="w-full resize-y rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>
      </div>

      {validationMessage ? (
        <div className="mt-4 rounded-lg border border-red-500/40 bg-red-950/20 p-3" role="alert">
          <h3 className="text-sm font-semibold text-red-200">Check this field</h3>
          <p id={validationMessageId} className="mt-1 text-sm text-red-100">
            {validationMessage}
          </p>
        </div>
      ) : null}

      {prepareErrorMessage ? (
        <div className="mt-4 rounded-lg border border-red-500/40 bg-red-950/20 p-3" role="alert">
          <h3
            id={prepareErrorId}
            ref={prepareErrorHeadingRef}
            tabIndex={-1}
            className="text-sm font-semibold text-red-200"
          >
            Draft request error
          </h3>
          <p id={prepareErrorMessageId} className="mt-1 text-sm text-red-100">
            {prepareErrorMessage}
          </p>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={onSubmit}
          disabled={locked || !fields.recipient.trim()}
          className="rounded-lg bg-purple-600 px-4 py-2.5 font-semibold text-white hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Review email draft
        </button>
        <button
          type="button"
          onClick={onReset}
          disabled={pending}
          className="rounded-lg border border-gray-600 px-4 py-2.5 font-semibold text-gray-100 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Clear draft
        </button>
      </div>
    </section>
  );
}
