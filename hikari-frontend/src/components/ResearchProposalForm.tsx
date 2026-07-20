"use client";

import { useEffect, useId, useRef, type Ref } from "react";
import {
  RESEARCH_MAX_RESULTS_DEFAULT,
  RESEARCH_MAX_RESULTS_MAX,
  RESEARCH_MAX_RESULTS_MIN,
  mapResearchValidationMessage,
  type ResearchFieldName,
  type ResearchFields,
  type ResearchValidationCode,
} from "@/utils/productivity/researchProposal";

export type ResearchProposalFormProps = Readonly<{
  fields: ResearchFields;
  pending: boolean;
  disabled?: boolean;
  validationCode?: ResearchValidationCode;
  validationField?: ResearchFieldName;
  onChange: (fields: ResearchFields) => void;
  onSubmit: () => void;
  onReset: () => void;
  headingRef?: Ref<HTMLHeadingElement>;
}>;

export function ResearchProposalForm({
  fields,
  pending,
  disabled = false,
  validationCode,
  validationField,
  onChange,
  onSubmit,
  onReset,
  headingRef,
}: ResearchProposalFormProps) {
  const instanceId = useId();
  const headingId = `${instanceId}-heading`;
  const queryId = `${instanceId}-query`;
  const domainsId = `${instanceId}-domains`;
  const maxResultsId = `${instanceId}-max-results`;
  const validationMessageId = `${instanceId}-validation-message`;
  const queryRef = useRef<HTMLTextAreaElement>(null);
  const domainsRef = useRef<HTMLTextAreaElement>(null);
  const maxResultsRef = useRef<HTMLInputElement>(null);

  const locked = pending || disabled;
  const validationMessage = validationCode
    ? mapResearchValidationMessage(validationCode)
    : "";
  const activeField =
    validationField === "query" ||
    validationField === "domainsText" ||
    validationField === "maxResults"
      ? validationField
      : undefined;

  useEffect(() => {
    if (!validationCode || !activeField) {
      return;
    }
    if (activeField === "query") {
      queryRef.current?.focus();
      return;
    }
    if (activeField === "domainsText") {
      domainsRef.current?.focus();
      return;
    }
    maxResultsRef.current?.focus();
  }, [validationCode, activeField]);

  const submitDisabled =
    locked || fields.query.length < 1;

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
        Browser research proposal
      </h2>
      <p className="mt-1 text-sm text-gray-400">
        Review a bounded research request before any browser access. Nothing is
        searched from this form.
      </p>

      <div className="mt-4 space-y-4">
        <div>
          <label
            htmlFor={queryId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Query
          </label>
          <textarea
            id={queryId}
            ref={queryRef}
            rows={4}
            autoComplete="off"
            spellCheck={false}
            value={fields.query}
            disabled={locked}
            aria-invalid={activeField === "query"}
            aria-describedby={
              activeField === "query" ? validationMessageId : undefined
            }
            onChange={(event) =>
              onChange(
                Object.freeze({
                  ...fields,
                  query: event.target.value,
                }),
              )
            }
            className="w-full resize-y rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <div>
          <label
            htmlFor={domainsId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Allowed domains (optional, one per line)
          </label>
          <textarea
            id={domainsId}
            ref={domainsRef}
            rows={4}
            autoComplete="off"
            spellCheck={false}
            value={fields.domainsText}
            disabled={locked}
            aria-invalid={activeField === "domainsText"}
            aria-describedby={
              activeField === "domainsText" ? validationMessageId : undefined
            }
            onChange={(event) =>
              onChange(
                Object.freeze({
                  ...fields,
                  domainsText: event.target.value,
                }),
              )
            }
            className="w-full resize-y rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 font-mono text-sm text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <div>
          <label
            htmlFor={maxResultsId}
            className="mb-2 block text-sm font-medium text-gray-200"
          >
            Maximum results
          </label>
          <input
            id={maxResultsId}
            ref={maxResultsRef}
            type="number"
            inputMode="numeric"
            min={RESEARCH_MAX_RESULTS_MIN}
            max={RESEARCH_MAX_RESULTS_MAX}
            autoComplete="off"
            value={fields.maxResults}
            disabled={locked}
            aria-invalid={activeField === "maxResults"}
            aria-describedby={
              activeField === "maxResults" ? validationMessageId : undefined
            }
            onChange={(event) =>
              onChange(
                Object.freeze({
                  ...fields,
                  maxResults: event.target.value,
                }),
              )
            }
            className="w-full max-w-xs rounded-lg border border-gray-700 bg-[#0f0f1a] px-3 py-2 text-white placeholder-gray-600 focus:border-purple-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
          <p className="mt-1 text-xs text-gray-500">
            Default {RESEARCH_MAX_RESULTS_DEFAULT}. Allowed range{" "}
            {RESEARCH_MAX_RESULTS_MIN}–{RESEARCH_MAX_RESULTS_MAX}.
          </p>
        </div>
      </div>

      {validationMessage ? (
        <div
          className="mt-4 rounded-lg border border-red-500/40 bg-red-950/20 p-3"
          role="alert"
        >
          <h3 className="text-sm font-semibold text-red-200">Check this field</h3>
          <p id={validationMessageId} className="mt-1 text-sm text-red-100">
            {validationMessage}
          </p>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={onSubmit}
          disabled={submitDisabled}
          className="rounded-lg bg-purple-600 px-4 py-2.5 font-semibold text-white hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Prepare research
        </button>
        <button
          type="button"
          onClick={onReset}
          disabled={pending}
          className="rounded-lg border border-gray-600 px-4 py-2.5 font-semibold text-gray-100 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Reset
        </button>
      </div>
    </section>
  );
}
